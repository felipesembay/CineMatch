"""Reranker supervisionado item-item (LogisticRegression, SGD, LightGBM) com rótulo fraco.

Protocolo (ADR-017):
1. **Candidatos:** união do top-`depth` do TF-IDF e de uma representação densa por consulta.
2. **Features por par (consulta, candidato):** cossenos, ranks, Jaccard de gêneros e
   keywords, pessoas em comum, tipo, idioma, distância de ano, votos/popularidade do
   candidato. **Nenhuma feature de título** (evita atalho lexical para franquias).
3. **Rótulo fraco:** candidato pertence à mesma coleção TMDB da consulta. NÃO é relevância
   para usuários; o modelo aprende a "detectar franquia" a partir de metadados.
4. **Validação:** GroupKFold por coleção. Predições out-of-fold para consultas de coleções;
   para as demais consultas, modelo treinado em todas as coleções (sem rótulo nelas, logo
   sem vazamento; só alimentam gênero/cobertura).
"""

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import sparse
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from cinematch.similarity.data import SimilarityData
from cinematch.similarity.methods import K, _mask, _top, cosine_scorer

DEFAULT_DEPTH = 50


@dataclass
class Candidates:
    query: np.ndarray  # índice da consulta (ordenado, contíguo por consulta)
    cand: np.ndarray
    X: np.ndarray  # float32 (pares, features); nan = ausente
    features: list[str]
    y: np.ndarray  # 1 = mesma coleção
    meta: dict[str, Any] = field(default_factory=dict)

    def subset(self, mask: np.ndarray) -> "Candidates":
        return Candidates(
            self.query[mask], self.cand[mask], self.X[mask], self.features, self.y[mask], self.meta
        )


def _pair_counts(m: sparse.csr_matrix, q: np.ndarray) -> np.ndarray:
    return np.asarray((m[q] @ m.T).toarray(), dtype=np.float32)


def _jaccard(inter: np.ndarray, sizes: np.ndarray, q: np.ndarray) -> np.ndarray:
    union = sizes[q][:, None] + sizes[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def build_candidates(
    data: SimilarityData,
    dense_rep: str,
    queries: np.ndarray | None = None,
    depth: int = DEFAULT_DEPTH,
    chunk: int = 256,
) -> Candidates:
    queries = np.sort(np.arange(data.n) if queries is None else np.asarray(queries))
    reps = ["tfidf", *data.dense]  # todas as representações viram feature de cosseno
    scorers = {r: cosine_scorer(data, r) for r in reps}
    g_size = np.asarray(data.genres.sum(axis=1)).ravel()
    k_size = np.asarray(data.keywords.sum(axis=1)).ravel()
    features = [f"cos_{r}" for r in reps] + [
        "rank_tfidf",
        f"rank_{dense_rep}",
        "genre_jaccard",
        "keyword_jaccard",
        "shared_directors",
        "shared_cast",
        "same_type",
        "same_language",
        "year_diff",
        "cand_log_votes",
        "cand_log_popularity",
    ]
    parts_q, parts_c, parts_x = [], [], []
    for s in range(0, len(queries), chunk):
        q = queries[s : s + chunk]
        rows = np.arange(len(q))[:, None]
        cos = {r: _mask(f(q), q) for r, f in scorers.items()}
        ranks = {}
        member = np.zeros((len(q), data.n), dtype=bool)
        for r in ("tfidf", dense_rep):
            order = _top(cos[r], depth)
            rank = np.full((len(q), data.n), depth, dtype=np.float32)  # depth = "fora"
            rank[rows, order] = np.arange(depth, dtype=np.float32)
            ranks[r] = rank
            member[rows, order] = True
        r_idx, c_idx = np.nonzero(member)
        g = _jaccard(_pair_counts(data.genres, q), g_size, q)
        kw = _jaccard(_pair_counts(data.keywords, q), k_size, q)
        dirs = _pair_counts(data.directors, q)
        cast = _pair_counts(data.cast, q)
        qa, ca = q[r_idx], c_idx
        cols = [cos[r][r_idx, c_idx] for r in reps] + [
            ranks["tfidf"][r_idx, c_idx],
            ranks[dense_rep][r_idx, c_idx],
            g[r_idx, c_idx],
            kw[r_idx, c_idx],
            dirs[r_idx, c_idx],
            cast[r_idx, c_idx],
            (data.item_type[qa] == data.item_type[ca]).astype(np.float32),
            (data.language[qa] == data.language[ca]).astype(np.float32),
            np.abs(data.year[qa] - data.year[ca]),
            data.log_votes[ca],
            data.log_popularity[ca],
        ]
        parts_q.append(qa)
        parts_c.append(ca)
        parts_x.append(np.column_stack(cols).astype(np.float32))
    query = np.concatenate(parts_q)
    cand = np.concatenate(parts_c)
    coll = data.collection
    y = ((coll[query] >= 0) & (coll[query] == coll[cand])).astype(np.int8)
    # Teto de recall: fração dos outros membros da coleção presentes nos candidatos.
    cq = np.intersect1d(data.collection_queries(), queries)
    found = np.bincount(query[y == 1], minlength=data.n)[cq]
    total = np.array([(coll == coll[i]).sum() - 1 for i in cq])
    meta = {
        "depth": depth,
        "dense_rep": dense_rep,
        "pairs": len(query),
        "positives": int(y.sum()),
        "candidate_recall_ceiling": round(float((found / total).mean()), 4) if cq.size else None,
    }
    return Candidates(query, cand, np.concatenate(parts_x), features, y, meta)


# --- Modelos --------------------------------------------------------------------------


def _linear(estimator: Any) -> Any:
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), estimator)


def make_model(name: str, seed: int = 0, **params: Any) -> Any:
    if name == "logreg":
        return _linear(
            LogisticRegression(
                C=params.get("C", 1.0), class_weight="balanced", max_iter=2000, random_state=seed
            )
        )
    if name == "sgd":
        return _linear(
            SGDClassifier(
                loss="log_loss",
                alpha=params.get("alpha", 1e-4),
                class_weight="balanced",
                max_iter=200,
                tol=1e-4,
                random_state=seed,
            )
        )
    if name == "lightgbm":
        from lightgbm import LGBMRanker

        return LGBMRanker(
            objective="lambdarank",
            n_estimators=params.get("n_estimators", 300),
            learning_rate=params.get("learning_rate", 0.05),
            num_leaves=params.get("num_leaves", 31),
            min_child_samples=params.get("min_child_samples", 50),
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            random_state=seed,
            n_jobs=-1,
            verbose=-1,
        )
    raise ValueError(f"modelo desconhecido: {name}")


SUPERVISED = ("logreg", "sgd", "lightgbm")


def _fit(model: Any, name: str, c: Candidates) -> Any:
    if name == "lightgbm":
        _, groups = np.unique(c.query, return_counts=True)  # query ordenada => contígua
        return model.fit(c.X, c.y, group=groups)
    return model.fit(c.X, c.y)


def _score(model: Any, name: str, x: np.ndarray) -> np.ndarray:
    if name == "lightgbm":
        return model.predict(x)
    return model.decision_function(x)


def topk_pairs_with_scores(
    c: Candidates, scores: np.ndarray, n: int, k: int = K
) -> tuple[np.ndarray, np.ndarray]:
    """Top-k por consulta (índices e escores). Empate: menor índice. Sem candidato: -1/nan."""
    order = np.lexsort((c.cand, -scores, c.query))
    q_sorted = c.query[order]
    starts = np.searchsorted(q_sorted, np.arange(n), side="left")
    ends = np.searchsorted(q_sorted, np.arange(n), side="right")
    idx = np.full((n, k), -1, dtype=np.int64)
    val = np.full((n, k), np.nan, dtype=np.float64)
    for q in np.unique(c.query):
        sel = order[starts[q] : min(ends[q], starts[q] + k)]
        idx[q, : sel.size] = c.cand[sel]
        val[q, : sel.size] = scores[sel]
    return idx, val


def topk_from_pairs(c: Candidates, scores: np.ndarray, n: int, k: int = K) -> np.ndarray:
    """Top-k por consulta a partir dos escores dos pares. Empate: menor índice."""
    return topk_pairs_with_scores(c, scores, n, k)[0]


def score_pairs(model: Any, name: str, x: np.ndarray) -> np.ndarray:
    """Escore em [0, 1] (sigmoide da saída bruta) para servir/registrar."""
    return 1.0 / (1.0 + np.exp(-_score(model, name, x)))


def feature_importance(model: Any, name: str, features: list[str]) -> dict[str, float]:
    if name == "lightgbm":
        imp = model.booster_.feature_importance(importance_type="gain")
        imp = imp / (imp.sum() or 1.0)
    else:
        imp = model[-1].coef_.ravel()  # coeficientes padronizados (sinal importa)
    return {
        f: round(float(v), 4)
        for f, v in sorted(zip(features, imp, strict=True), key=lambda t: -abs(t[1]))
    }


@dataclass
class RerankResult:
    name: str
    neighbors: np.ndarray  # (n, k) — OOF para consultas de coleção
    importance: dict[str, float]
    model: Any  # treinado em todas as consultas de coleção
    fold_scores: list[float]
    fit_seconds: float


def cross_validate_reranker(
    name: str,
    cands: Candidates,
    data: SimilarityData,
    k: int = K,
    n_splits: int = 5,
    seed: int = 0,
    **params: Any,
) -> RerankResult:
    started = time.perf_counter()
    cq = data.collection_queries()
    labeled = np.isin(cands.query, cq)
    lab = cands.subset(labeled)
    groups = data.collection[lab.query]
    scores = np.zeros(len(cands.query), dtype=np.float64)
    lab_pos = np.flatnonzero(labeled)
    fold_recall = []
    for train, test in GroupKFold(n_splits=n_splits).split(lab.X, lab.y, groups):
        model = _fit(make_model(name, seed, **params), name, lab.subset(train))
        scores[lab_pos[test]] = _score(model, name, lab.X[test])
        test_c = lab.subset(test)
        nb = topk_from_pairs(test_c, scores[lab_pos[test]], data.n, k)
        qs = np.unique(test_c.query)
        hits = [
            (data.collection[nb[q]] == data.collection[q]).sum()
            / min(k, (data.collection == data.collection[q]).sum() - 1)
            for q in qs
        ]
        fold_recall.append(round(float(np.mean(hits)), 4))
    final = _fit(make_model(name, seed, **params), name, lab)
    if (~labeled).any():
        scores[~labeled] = _score(final, name, cands.X[~labeled])
    neighbors = topk_from_pairs(cands, scores, data.n, k)
    return RerankResult(
        name=name,
        neighbors=neighbors,
        importance=feature_importance(final, name, cands.features),
        model=final,
        fold_scores=fold_recall,
        fit_seconds=round(time.perf_counter() - started, 2),
    )
