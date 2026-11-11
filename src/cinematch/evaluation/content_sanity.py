"""Testes de sanidade para similaridade de conteúdo SEM interações de usuários.

Não são métricas de relevância (spec §8: sem histórico, não alegar NDCG real). São proxies:

- `collection_recall@k`: para filmes de uma coleção TMDB (`belongs_to_collection`, ex.: uma
  franquia) com >= 2 membros no catálogo, fração dos outros membros recuperados no top-k
  (normalizada por min(k, membros - 1)). Rótulo fraco: franquias não são a única noção de
  similaridade, e títulos parecidos favorecem métodos lexicais.
- `genre_overlap@k`: fração de vizinhos com >= 1 gênero em comum.
- `title_token_overlap@k`: fração de vizinhos que compartilham uma palavra do título
  (>= 4 letras). Mede o "vazamento" do título; alto sem ganho de coleção é ruim.
- `catalog_coverage@k`: fração do catálogo que aparece em algum top-k (diversidade).
- `same_type@k`: fração de vizinhos do mesmo tipo (filme/série). Informativo.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np
from scipy import sparse
from sqlalchemy import Engine, select, text

from cinematch.data.schema import item_embeddings
from cinematch.features.text import ItemDoc, load_item_documents

K = 10
_STOP = {"the", "and", "with", "from", "that", "this", "part", "movie", "film", "season"}


def load_collections(engine: Engine) -> dict[int, int]:
    """item_id -> collection_id (TMDB `belongs_to_collection`, normalizado no catálogo)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, collection_id FROM catalog_items WHERE collection_id IS NOT NULL")
        ).all()
    return {item_id: coll for item_id, coll in rows}


def topk_dense(matrix: np.ndarray, k: int = K, chunk: int = 1024) -> np.ndarray:
    """Índices top-k por produto interno (vetores normalizados), excluindo o próprio item.
    Empate desfeito pelo índice (determinístico)."""
    n = matrix.shape[0]
    out = np.empty((n, k), dtype=np.int64)
    for start in range(0, n, chunk):
        sims = matrix[start : start + chunk] @ matrix.T
        rows = np.arange(sims.shape[0])
        sims[rows, rows + start] = -np.inf
        out[start : start + chunk] = np.argsort(-sims, axis=1, kind="stable")[:, :k]
    return out


def topk_sparse(matrix: sparse.csr_matrix, k: int = K, chunk: int = 1024) -> np.ndarray:
    n = matrix.shape[0]
    out = np.empty((n, k), dtype=np.int64)
    mt = matrix.T.tocsc()
    for start in range(0, n, chunk):
        sims = (matrix[start : start + chunk] @ mt).toarray()
        rows = np.arange(sims.shape[0])
        sims[rows, rows + start] = -np.inf
        out[start : start + chunk] = np.argsort(-sims, axis=1, kind="stable")[:, :k]
    return out


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"\w{4,}", title.lower()) if t not in _STOP}


def _bootstrap_ci(values: np.ndarray, seed: int = 0, n: int = 1000) -> tuple[float, float]:
    if values.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(n, values.size), replace=True).mean(axis=1)
    return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))


def sanity_metrics(
    neighbors: np.ndarray, docs: list[ItemDoc], collections: dict[int, int], k: int = K
) -> dict[str, Any]:
    ids = [d.item_id for d in docs]
    genres = [set(d.genres) for d in docs]
    titles = [_title_tokens(d.title) for d in docs]
    types = [d.item_type for d in docs]
    coll = [collections.get(i) for i in ids]
    members: dict[int, set[int]] = defaultdict(set)
    for idx, c in enumerate(coll):
        if c is not None:
            members[c].add(idx)

    g_overlap, t_overlap, same_type, c_recall = [], [], [], []
    for q, nbrs in enumerate(neighbors[:, :k]):
        g_overlap.append(np.mean([bool(genres[q] & genres[j]) for j in nbrs]))
        t_overlap.append(np.mean([bool(titles[q] & titles[j]) for j in nbrs]))
        same_type.append(np.mean([types[q] == types[j] for j in nbrs]))
        c = coll[q]
        if c is not None and len(members[c]) >= 2:
            others = members[c] - {q}
            c_recall.append(len(others & set(nbrs.tolist())) / min(k, len(others)))
    recall = np.asarray(c_recall)
    lo, hi = _bootstrap_ci(recall)
    return {
        f"collection_recall@{k}": round(float(recall.mean()), 4) if recall.size else None,
        "collection_recall_ci95": [round(lo, 4), round(hi, 4)],
        "collection_queries": int(recall.size),
        f"genre_overlap@{k}": round(float(np.mean(g_overlap)), 4),
        f"title_token_overlap@{k}": round(float(np.mean(t_overlap)), 4),
        f"same_type@{k}": round(float(np.mean(same_type)), 4),
        f"catalog_coverage@{k}": round(len(np.unique(neighbors[:, :k])) / len(ids), 4),
        "items": len(ids),
    }


def compare_methods(
    engine: Engine,
    model_versions: list[str],
    tfidf_loader: Callable[[], Any] | None = None,
    k: int = K,
) -> dict[str, Any]:
    """Avalia TF-IDF (mais recente) e cada `model_version` no MESMO conjunto de itens."""
    with engine.connect() as conn:
        docs = load_item_documents(conn)
        vectors: dict[str, dict[int, np.ndarray]] = {}
        for mv in model_versions:
            rows = conn.execute(
                select(item_embeddings.c.item_id, item_embeddings.c.embedding).where(
                    item_embeddings.c.model_version == mv
                )
            ).all()
            vectors[mv] = {i: np.asarray(v, dtype=np.float32) for i, v in rows}

    tfidf = tfidf_loader() if tfidf_loader else None
    common = {d.item_id for d in docs}
    for vec in vectors.values():
        common &= set(vec)
    if tfidf is not None:
        common &= set(tfidf.item_ids.tolist())
    docs = [d for d in docs if d.item_id in common]  # ordenados por item_id
    ids = [d.item_id for d in docs]
    collections = load_collections(engine)

    results: dict[str, Any] = {}
    if tfidf is not None:
        pos = {int(i): n for n, i in enumerate(tfidf.item_ids.tolist())}
        sub = tfidf.matrix[[pos[i] for i in ids]]
        results[f"tfidf[{tfidf.manifest['version']}]"] = sanity_metrics(
            topk_sparse(sub, k), docs, collections, k
        )
    for mv, vec in vectors.items():
        matrix = np.stack([vec[i] for i in ids])
        results[f"embedding[{mv}]"] = sanity_metrics(topk_dense(matrix, k), docs, collections, k)
    return {"k": k, "items": len(ids), "methods": results}
