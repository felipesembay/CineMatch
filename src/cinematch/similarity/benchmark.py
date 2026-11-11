"""Roda todos os métodos no MESMO conjunto de itens e, opcionalmente, registra no MLflow."""

import logging
import time
from typing import Any

import numpy as np

from cinematch.similarity import methods as m
from cinematch.similarity import rerank as r
from cinematch.similarity.data import SimilarityData

logger = logging.getLogger(__name__)


def unsupervised_scorers(data: SimilarityData, dense_rep: str) -> dict[str, m.Scorer]:
    scorers: dict[str, m.Scorer] = {}
    if data.tfidf is not None:
        scorers["tfidf:cosine"] = m.cosine_scorer(data, "tfidf")
    for rep in data.dense:
        scorers[f"{rep}:cosine"] = m.cosine_scorer(data, rep)
    if data.tfidf is not None:
        scorers[f"rrf(tfidf+{dense_rep})"] = m.rrf_scorer(
            [scorers["tfidf:cosine"], scorers[f"{dense_rep}:cosine"]], data.n
        )
        scorers["tfidf:rocchio_prf"] = m.rocchio_prf_scorer(data, "tfidf")
    scorers[f"{dense_rep}:rocchio_prf"] = m.rocchio_prf_scorer(data, dense_rep)
    return scorers


def run_benchmark(
    data: SimilarityData,
    dense_rep: str = "emb_v2",
    k: int = m.K,
    depth: int = r.DEFAULT_DEPTH,
    supervised: tuple[str, ...] = r.SUPERVISED,
    n_splits: int = 5,
    log_mlflow: bool = False,
    experiment: str = "similarity-item2item",
) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    neighbors: dict[str, np.ndarray] = {}
    details: dict[str, dict[str, Any]] = {}

    for name, scorer in unsupervised_scorers(data, dense_rep).items():
        t0 = time.perf_counter()
        nb = m.rank_all(scorer, data.n, k)
        neighbors[name] = nb
        results[name] = {
            **m.neighbor_metrics(nb, data, k),
            "seconds": round(time.perf_counter() - t0, 2),
        }
        logger.info("benchmark_method", extra={"method": name})

    cands = None
    if supervised:
        cands = r.build_candidates(data, dense_rep, depth=depth)
        for name in supervised:
            res = r.cross_validate_reranker(name, cands, data, k=k, n_splits=n_splits)
            key = f"rerank:{name}"
            neighbors[key] = res.neighbors
            results[key] = {
                **m.neighbor_metrics(res.neighbors, data, k),
                "seconds": res.fit_seconds,
            }
            details[key] = {
                "importance": res.importance,
                "fold_recall": res.fold_scores,
                "candidates": cands.meta,
                "model": res.model,
            }
            logger.info("benchmark_method", extra={"method": key})

    multiseed = {}
    tasks = m.multiseed_tasks(data, n_seeds=2)
    for rep in [*(["tfidf"] if data.tfidf is not None else []), dense_rep]:
        multiseed.update(m.evaluate_multiseed(m.profile_strategies(data, rep), tasks, k))

    out = {
        "k": k,
        "items": data.n,
        "fingerprint": data.fingerprint,
        "versions": data.versions,
        "item_to_item": results,
        "multiseed": multiseed,
        "rerank_details": {
            n: {kk: v for kk, v in d.items() if kk != "model"} for n, d in details.items()
        },
        "neighbors": neighbors,
    }
    if log_mlflow:
        log_benchmark(out, details, experiment, dense_rep, depth, n_splits)
    return out


def log_benchmark(
    out: dict[str, Any],
    details: dict[str, dict[str, Any]],
    experiment: str,
    dense_rep: str,
    depth: int,
    n_splits: int,
) -> None:
    from cinematch import tracking

    tracking.setup(experiment)
    common = {"data_fingerprint": out["fingerprint"], "versions": out["versions"]}
    for name, metrics in out["item_to_item"].items():
        params: dict[str, Any] = {"k": out["k"], "task": "item_to_item"}
        if name.startswith("rerank:"):
            params.update(
                depth=depth, n_splits=n_splits, dense_rep=dense_rep, cv="GroupKFold(collection)"
            )
        with tracking.method_run(name, params=params, **common):
            d = details.get(name, {})
            tracking.log_results(
                {
                    **metrics,
                    **(
                        {"candidate_recall_ceiling": d["candidates"]["candidate_recall_ceiling"]}
                        if d
                        else {}
                    ),
                },
                artifacts={"importance.json": d["importance"], "fold_recall.json": d["fold_recall"]}
                if d
                else None,
                model=d.get("model"),
            )
    for name, metrics in out["multiseed"].items():
        with tracking.method_run(
            f"profile:{name}", params={"k": out["k"], "task": "multiseed", "n_seeds": 2}, **common
        ):
            tracking.log_results(metrics)
