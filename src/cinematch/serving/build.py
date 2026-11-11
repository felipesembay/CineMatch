"""Jobs batch que geram conjuntos de vizinhos (requer o extra `ml`).

- `tfidf`: baseline (ADR-016). Cosseno exato sobre o artefato TF-IDF mais recente.
- `rerank-logreg`: challenger (ADR-017). Reranker LogisticRegression treinado com o rótulo
  fraco "mesma coleção" sobre candidatos TF-IDF + embedding; servido com o modelo treinado
  em todas as coleções. Métricas registradas são as out-of-fold (GroupKFold).
"""

import hashlib
import json
import logging
import time
from typing import Any

import numpy as np
from sqlalchemy import Engine, text

from cinematch.serving.neighbors import write_neighbor_set

logger = logging.getLogger(__name__)

DEFAULT_K = 50
METHODS = ("tfidf", "rerank-logreg")


def _existing_ids(engine: Engine) -> set[int]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT id FROM catalog_items")).scalars())


def build_tfidf(engine: Engine, k: int = DEFAULT_K, chunk: int = 1024) -> dict[str, Any]:
    from cinematch.features.tfidf import load_latest_tfidf

    model = load_latest_tfidf(engine)
    alive = _existing_ids(engine)
    keep = np.array([i in alive for i in model.item_ids.tolist()])  # itens expirados saem
    ids = model.item_ids[keep]
    matrix = model.matrix[keep]
    mt = matrix.T.tocsc()
    n = ids.size
    if n < 2:
        raise ValueError("catálogo com menos de 2 itens")
    k = min(k, n - 1)
    idx = np.empty((n, k), dtype=np.int64)
    val = np.empty((n, k), dtype=np.float64)
    started = time.perf_counter()
    for s in range(0, n, chunk):
        sims = (matrix[s : s + chunk] @ mt).toarray()
        rows = np.arange(sims.shape[0])
        sims[rows, rows + s] = -np.inf
        top = np.argsort(-sims, axis=1, kind="stable")[:, :k]
        idx[s : s + chunk] = top
        val[s : s + chunk] = np.take_along_axis(sims, top, axis=1)
    m = model.manifest
    version = f"similar:tfidf|{m['version']}|c-{m['corpus_hash'][:12]}|n-{n}|k{k}"
    return {
        "model_version": version,
        "method": "tfidf",
        "item_ids": ids,
        "neighbors": idx,
        "scores": val,
        "params": {"k": k, "tfidf_version": m["version"], "corpus_hash": m["corpus_hash"]},
        "metrics": {"build_seconds": round(time.perf_counter() - started, 2)},
    }


def build_rerank_logreg(
    engine: Engine, k: int = DEFAULT_K, dense: str = "emb_v2", depth: int = 50
) -> dict[str, Any]:
    from cinematch.features.tfidf import load_latest_tfidf
    from cinematch.similarity import methods as m
    from cinematch.similarity import rerank as r
    from cinematch.similarity.data import load_similarity_data, stored_model_versions

    started = time.perf_counter()
    data = load_similarity_data(engine, stored_model_versions(engine), load_latest_tfidf(engine))
    if dense not in data.dense:
        raise LookupError(f"embedding '{dense}' ausente; disponíveis: {sorted(data.dense)}")
    cands = r.build_candidates(data, dense, depth=depth)
    res = r.cross_validate_reranker("logreg", cands, data, k=m.K)
    oof = m.neighbor_metrics(res.neighbors, data, m.K)
    idx, val = r.topk_pairs_with_scores(
        cands, r.score_pairs(res.model, "logreg", cands.X), data.n, k
    )
    params = {
        "k": k,
        "dense": dense,
        "depth": depth,
        "features": cands.features,
        "versions": data.versions,
        "data_fingerprint": data.fingerprint,
    }
    phash = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    return {
        "model_version": f"similar:rerank_logreg|data-{data.fingerprint}|p-{phash}|k{k}",
        "method": "rerank-logreg",
        "item_ids": data.item_ids,
        "neighbors": idx,
        "scores": val,
        "params": params,
        "metrics": {
            **{key: v for key, v in oof.items() if isinstance(v, int | float)},
            "evaluation": "out-of-fold GroupKFold(collection), rótulo fraco",
            "candidate_recall_ceiling": cands.meta["candidate_recall_ceiling"],
            "build_seconds": round(time.perf_counter() - started, 2),
        },
        "model": res.model,
        "importance": res.importance,
    }


def build_and_store(
    engine: Engine,
    method: str,
    k: int = DEFAULT_K,
    activate: bool = False,
    log_mlflow: bool = False,
) -> dict[str, Any]:
    if method == "tfidf":
        built = build_tfidf(engine, k)
    elif method == "rerank-logreg":
        built = build_rerank_logreg(engine, k)
    else:
        raise ValueError(f"método desconhecido: {method} (use {', '.join(METHODS)})")
    result = write_neighbor_set(
        engine,
        model_version=built["model_version"],
        method=built["method"],
        item_ids=built["item_ids"],
        neighbors=built["neighbors"],
        scores=built["scores"],
        params=built["params"],
        metrics=built["metrics"],
        activate=activate,
    )
    if log_mlflow and not result["reused"]:
        _log_mlflow(built)
    return {**result, "metrics": built["metrics"]}


def _log_mlflow(built: dict[str, Any]) -> None:
    from cinematch import tracking

    tracking.setup("serving-similar")
    versions = built["params"].get("versions") or {"tfidf": built["params"]["tfidf_version"]}
    label = "weak:tmdb_collection" if built["method"] != "tfidf" else "none"
    with tracking.method_run(
        built["model_version"],
        data_fingerprint=built["params"].get("data_fingerprint", "n/a"),
        versions=versions,
        params={k: v for k, v in built["params"].items() if k != "versions"},
        label_kind=label,
        tags={"neighbor_set": built["model_version"], "stage": "serving_candidate"},
    ):
        tracking.log_results(
            built["metrics"],
            artifacts={"importance.json": built["importance"]} if "importance" in built else None,
            model=built.get("model"),
        )
