"""TF-IDF de conteúdo: artefato versionado em disco + manifesto em `feature_artifacts`.

Determinístico para o mesmo corpus, mesmos parâmetros e mesma versão do scikit-learn:
corpus ordenado por `item_id`; `corpus_hash` identifica exatamente os textos usados.
Layout: <artifacts_dir>/tfidf/<version>/<corpus_hash[:16]>/{model.joblib, manifest.json}
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Engine, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import feature_artifacts
from cinematch.features.text import TFIDF_TEXT_VERSION, load_item_documents, tfidf_text

logger = logging.getLogger(__name__)

KIND = "tfidf"
PARAMS: dict[str, Any] = {
    "min_df": 2,
    "max_df": 0.5,
    "ngram_range": [1, 2],
    "sublinear_tf": True,
    "stop_words": "english",
    "max_features": 50_000,
    "norm": "l2",
}


def version() -> str:
    params_hash = hashlib.sha256(json.dumps(PARAMS, sort_keys=True).encode()).hexdigest()[:8]
    return f"{TFIDF_TEXT_VERSION}|sklearn-{sklearn.__version__}|p-{params_hash}"


@dataclass
class TfidfModel:
    vectorizer: TfidfVectorizer
    matrix: sparse.csr_matrix
    item_ids: np.ndarray
    manifest: dict[str, Any]

    def neighbors(self, item_id: int, k: int = 10) -> list[tuple[int, float]]:
        idx = np.flatnonzero(self.item_ids == item_id)
        if idx.size == 0:
            raise KeyError(f"item {item_id} fora do artefato TF-IDF")
        scores = (self.matrix @ self.matrix[idx[0]].T).toarray().ravel()
        scores[idx[0]] = -1.0
        top = np.argsort(-scores, kind="stable")[:k]
        return [(int(self.item_ids[i]), float(scores[i])) for i in top]


def build_tfidf(engine: Engine, artifacts_dir: Path) -> dict[str, Any]:
    with engine.connect() as conn:
        docs = load_item_documents(conn)
    if not docs:
        raise ValueError("catálogo vazio")
    texts = [tfidf_text(d) for d in docs]
    ids = np.array([d.item_id for d in docs], dtype=np.int64)
    corpus_hash = hashlib.sha256(
        "\n".join(f"{i}\t{t}" for i, t in zip(ids.tolist(), texts, strict=True)).encode()
    ).hexdigest()

    params = {**PARAMS, "ngram_range": tuple(PARAMS["ngram_range"])}
    vectorizer = TfidfVectorizer(dtype=np.float32, **params)
    matrix = vectorizer.fit_transform(texts).tocsr()

    ver = version()
    out = Path(artifacts_dir).resolve() / KIND / ver.replace("|", "__") / corpus_hash[:16]
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "kind": KIND,
        "version": ver,
        "text_version": TFIDF_TEXT_VERSION,
        "sklearn": sklearn.__version__,
        "params": PARAMS,
        "corpus_hash": corpus_hash,
        "n_items": len(docs),
        "vocabulary_size": len(vectorizer.vocabulary_),
        "nnz": int(matrix.nnz),
    }
    joblib.dump({"vectorizer": vectorizer, "matrix": matrix, "item_ids": ids}, out / "model.joblib")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    with engine.begin() as conn:
        conn.execute(
            pg_insert(feature_artifacts)
            .values(
                kind=KIND,
                version=ver,
                uri=out.as_uri(),
                params=PARAMS,
                n_items=len(docs),
                corpus_hash=corpus_hash,
            )
            .on_conflict_do_nothing()
        )
    manifest["uri"] = out.as_uri()
    logger.info("tfidf_built", extra=manifest)
    return manifest


def load_latest_tfidf(engine: Engine) -> TfidfModel:
    from urllib.parse import unquote, urlparse

    with engine.connect() as conn:
        row = conn.execute(
            select(feature_artifacts)
            .where(feature_artifacts.c.kind == KIND)
            .order_by(desc(feature_artifacts.c.created_at), desc(feature_artifacts.c.id))
            .limit(1)
        ).first()
    if row is None:
        raise LookupError("nenhum artefato TF-IDF; rode `cinematch features tfidf`")
    path = Path(unquote(urlparse(row.uri).path))
    data = joblib.load(path / "model.joblib")  # artefato gerado localmente por este projeto
    manifest = json.loads((path / "manifest.json").read_text())
    return TfidfModel(data["vectorizer"], data["matrix"], data["item_ids"], manifest)
