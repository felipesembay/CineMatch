"""Matriz de dados alinhada por item para os experimentos de similaridade.

Todas as representações (TF-IDF, embeddings, metadados) ficam alinhadas na mesma ordem
(`item_id` crescente) e restritas aos itens presentes em todas elas. `fingerprint` identifica
o conjunto exato (itens + versões) e vai para o MLflow.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import sparse
from sklearn.preprocessing import MultiLabelBinarizer
from sqlalchemy import Engine, select, text

from cinematch.data.schema import item_embeddings
from cinematch.evaluation.content_sanity import load_collections
from cinematch.features.text import ItemDoc, load_item_documents

_ATTRS_SQL = text(
    "SELECT id, release_year, original_language, popularity, vote_count FROM catalog_items"
)


@dataclass
class SimilarityData:
    docs: list[ItemDoc]
    item_ids: np.ndarray
    item_type: np.ndarray
    collection: np.ndarray  # id da coleção TMDB ou -1
    year: np.ndarray  # float, nan = desconhecido
    language: np.ndarray
    log_votes: np.ndarray
    log_popularity: np.ndarray
    tfidf: sparse.csr_matrix | None
    dense: dict[str, np.ndarray]  # nome -> (n, d) normalizado L2
    versions: dict[str, str]  # nome da representação -> versão exata
    genres: sparse.csr_matrix = field(repr=False)
    keywords: sparse.csr_matrix = field(repr=False)
    directors: sparse.csr_matrix = field(repr=False)
    cast: sparse.csr_matrix = field(repr=False)

    @property
    def n(self) -> int:
        return len(self.docs)

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha256(json.dumps(self.versions, sort_keys=True).encode())
        h.update(self.item_ids.tobytes())
        h.update(self.collection.tobytes())
        return h.hexdigest()[:16]

    def index_of(self, item_id: int) -> int:
        pos = int(np.searchsorted(self.item_ids, item_id))
        if pos >= self.n or self.item_ids[pos] != item_id:
            raise KeyError(item_id)
        return pos

    def search(self, query: str, limit: int = 10) -> list[tuple[int, int, str, str]]:
        """(índice, item_id, tipo, título) cujo título contém `query` (sem caixa)."""
        q = query.lower()
        hits = [
            (i, d.item_id, d.item_type, d.title)
            for i, d in enumerate(self.docs)
            if q in d.title.lower()
        ]
        return hits[:limit]

    def collection_queries(self, min_members: int = 2) -> np.ndarray:
        """Índices de itens cuja coleção tem >= `min_members` membros no conjunto."""
        ids, counts = np.unique(self.collection[self.collection >= 0], return_counts=True)
        ok = set(ids[counts >= min_members].tolist())
        return np.array([i for i, c in enumerate(self.collection) if c in ok], dtype=np.int64)

    def add_dense(self, name: str, matrix: np.ndarray, version: str) -> None:
        if matrix.shape[0] != self.n:
            raise ValueError("matriz desalinhada com os itens")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self.dense[name] = (matrix / np.where(norms == 0, 1, norms)).astype(np.float32)
        self.versions[name] = version


def _binarize(values: list[tuple[str, ...]]) -> sparse.csr_matrix:
    m = MultiLabelBinarizer(sparse_output=True).fit_transform(values)
    return sparse.csr_matrix(m, dtype=np.float32)


def build_similarity_data(
    docs: list[ItemDoc],
    attrs: dict[int, dict[str, Any]],
    collections: dict[int, int],
    tfidf: tuple[sparse.csr_matrix, np.ndarray, str] | None,
    dense: dict[str, tuple[dict[int, np.ndarray], str]],
) -> SimilarityData:
    """Alinha tudo pela interseção de itens. `tfidf` = (matriz, item_ids, versão)."""
    common = {d.item_id for d in docs} & set(attrs)
    for vecs, _ in dense.values():
        common &= set(vecs)
    if tfidf is not None:
        common &= set(tfidf[1].tolist())
    docs = sorted((d for d in docs if d.item_id in common), key=lambda d: d.item_id)
    ids = np.array([d.item_id for d in docs], dtype=np.int64)

    versions: dict[str, str] = {}
    tf_matrix = None
    if tfidf is not None:
        matrix, tf_ids, ver = tfidf
        pos = {int(i): n for n, i in enumerate(tf_ids.tolist())}
        tf_matrix = sparse.csr_matrix(matrix[[pos[int(i)] for i in ids]], dtype=np.float32)
        versions["tfidf"] = ver
    data = SimilarityData(
        docs=docs,
        item_ids=ids,
        item_type=np.array([d.item_type for d in docs]),
        collection=np.array([collections.get(int(i), -1) for i in ids], dtype=np.int64),
        year=np.array([attrs[int(i)]["release_year"] or np.nan for i in ids], dtype=np.float64),
        language=np.array([attrs[int(i)]["original_language"] or "" for i in ids]),
        log_votes=np.log1p(np.array([attrs[int(i)]["vote_count"] or 0 for i in ids], float)),
        log_popularity=np.log1p(np.array([attrs[int(i)]["popularity"] or 0.0 for i in ids], float)),
        tfidf=tf_matrix,
        dense={},
        versions=versions,
        genres=_binarize([d.genres for d in docs]),
        keywords=_binarize([d.keywords for d in docs]),
        directors=_binarize([d.directors for d in docs]),
        cast=_binarize([d.cast for d in docs]),
    )
    for name, (vecs, ver) in dense.items():
        data.add_dense(name, np.stack([vecs[int(i)] for i in ids]), ver)
    return data


def load_similarity_data(
    engine: Engine,
    model_versions: dict[str, str],
    tfidf_model: Any | None = None,
) -> SimilarityData:
    """Carrega do Postgres. `model_versions`: nome -> model_version."""
    with engine.connect() as conn:
        docs = load_item_documents(conn)
        attrs = {r.id: dict(r._mapping) for r in conn.execute(_ATTRS_SQL)}
        dense: dict[str, tuple[dict[int, np.ndarray], str]] = {}
        for name, mv in model_versions.items():
            rows = conn.execute(
                select(item_embeddings.c.item_id, item_embeddings.c.embedding).where(
                    item_embeddings.c.model_version == mv
                )
            ).all()
            if not rows:
                raise LookupError(f"sem embeddings para {mv}")
            dense[name] = ({i: np.asarray(v, dtype=np.float32) for i, v in rows}, mv)
    tfidf = (
        (tfidf_model.matrix, tfidf_model.item_ids, tfidf_model.manifest["version"])
        if tfidf_model is not None
        else None
    )
    return build_similarity_data(docs, attrs, load_collections(engine), tfidf, dense)


def stored_model_versions(engine: Engine) -> dict[str, str]:
    """Embeddings gravados no pgvector: `emb_v1`/`emb_v2`... -> model_version."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT model_version, text_version FROM item_embeddings ORDER BY 1")
        ).all()
    return {"emb_" + tv.rsplit("_", 1)[-1]: mv for mv, tv in rows}
