"""Métodos não supervisionados: cosseno (TF-IDF / Sentence Transformers), RRF e Rocchio.

Um *scorer* recebe índices de consulta `q` e devolve a matriz de escores `(len(q), n)`.
`rank_all` aplica o scorer em blocos, exclui o próprio item e devolve o top-k
(ordenação estável: empates desfeitos pelo índice, logo pelo `item_id`).

Rocchio aparece em duas formas:
- **PRF** (pseudo-relevance feedback), item -> item: q' = alpha*q + beta*média(top-m de q).
- **Perfil** (vários itens curtidos, ex.: onboarding da Fase 4):
  p = beta*média(positivos) - gamma*média(negativos). Sem negativos, gamma não atua.
"""

from collections.abc import Callable, Sequence

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

from cinematch.evaluation.content_sanity import _bootstrap_ci, sanity_metrics
from cinematch.similarity.data import SimilarityData

K = 10
Scorer = Callable[[np.ndarray], np.ndarray]


def _matrix(data: SimilarityData, rep: str) -> np.ndarray | sparse.csr_matrix:
    if rep == "tfidf":
        if data.tfidf is None:
            raise KeyError("TF-IDF não carregado")
        return data.tfidf
    return data.dense[rep]


def _scores(x: np.ndarray | sparse.csr_matrix, q_rows, xt) -> np.ndarray:
    out = q_rows @ xt
    return np.asarray(out.toarray() if sparse.issparse(out) else out, dtype=np.float32)


def cosine_scorer(data: SimilarityData, rep: str) -> Scorer:
    x = _matrix(data, rep)
    xt = x.T.tocsc() if sparse.issparse(x) else x.T
    return lambda q: _scores(x, x[q], xt)


def _mask(scores: np.ndarray, q: np.ndarray) -> np.ndarray:
    scores[np.arange(len(q)), q] = -np.inf
    return scores


def _top(scores: np.ndarray, k: int) -> np.ndarray:
    return np.argsort(-scores, axis=1, kind="stable")[:, :k]


def rank_all(
    scorer: Scorer, n: int, k: int = K, queries: np.ndarray | None = None, chunk: int = 512
) -> np.ndarray:
    queries = np.arange(n) if queries is None else np.asarray(queries)
    out = np.empty((len(queries), k), dtype=np.int64)
    for s in range(0, len(queries), chunk):
        q = queries[s : s + chunk]
        out[s : s + chunk] = _top(_mask(scorer(q), q), k)
    return out


def rrf_scorer(scorers: Sequence[Scorer], n: int, depth: int = 100, c: int = 60) -> Scorer:
    """Reciprocal Rank Fusion: Σ 1/(c + rank) sobre o top-`depth` de cada scorer."""
    weights = (1.0 / (c + np.arange(1, depth + 1))).astype(np.float32)

    def score(q: np.ndarray) -> np.ndarray:
        fused = np.zeros((len(q), n), dtype=np.float32)
        rows = np.arange(len(q))[:, None]
        for s in scorers:
            fused[rows, _top(_mask(s(q), q), depth)] += weights
        return fused

    return score


def rocchio_prf_scorer(
    data: SimilarityData, rep: str, alpha: float = 1.0, beta: float = 0.75, m: int = 5
) -> Scorer:
    x = _matrix(data, rep)
    xt = x.T.tocsc() if sparse.issparse(x) else x.T
    base = cosine_scorer(data, rep)

    def score(q: np.ndarray) -> np.ndarray:
        fb = _top(_mask(base(q), q), m)
        if sparse.issparse(x):
            avg = sparse.csr_matrix(
                (np.full(fb.size, 1.0 / m), (np.repeat(np.arange(len(q)), m), fb.ravel())),
                shape=(len(q), data.n),
            )
            query = normalize(alpha * x[q] + beta * (avg @ x))
        else:
            query = normalize(alpha * x[q] + beta * x[fb].mean(axis=1))
        return _scores(x, query, xt)

    return score


def rocchio_profile_scores(
    x: np.ndarray | sparse.csr_matrix,
    positives: Sequence[int],
    negatives: Sequence[int] = (),
    beta: float = 1.0,
    gamma: float = 0.25,
) -> np.ndarray:
    """Escores para um perfil de itens curtidos/não curtidos. Seeds e negativos ficam -inf."""
    pos = np.asarray(positives, dtype=np.int64)
    if pos.size == 0:
        raise ValueError("Rocchio precisa de ao menos 1 item positivo")
    neg = np.asarray(negatives, dtype=np.int64)
    profile = beta * np.asarray(x[pos].mean(axis=0)).ravel()
    if neg.size:
        profile = profile - gamma * np.asarray(x[neg].mean(axis=0)).ravel()
    norm = np.linalg.norm(profile)
    profile = profile / (norm or 1.0)
    scores = np.asarray(x @ profile, dtype=np.float32).ravel()
    scores[np.concatenate([pos, neg])] = -np.inf
    return scores


def recommend_for_profile(
    data: SimilarityData,
    rep: str,
    liked_ids: Sequence[int],
    disliked_ids: Sequence[int] = (),
    k: int = K,
    gamma: float = 0.25,
) -> list[tuple[int, str, float]]:
    """Top-k (item_id, título, escore) para itens curtidos/não curtidos (por item_id)."""
    x = _matrix(data, rep)
    scores = rocchio_profile_scores(
        x,
        [data.index_of(i) for i in liked_ids],
        [data.index_of(i) for i in disliked_ids],
        gamma=gamma,
    )
    top = np.argsort(-scores, kind="stable")[:k]
    return [(int(data.item_ids[i]), data.docs[i].title, float(scores[i])) for i in top]


# --- Métricas -------------------------------------------------------------------------


def neighbor_metrics(neighbors: np.ndarray, data: SimilarityData, k: int = K) -> dict:
    """Proxies de `content_sanity` + nDCG em rótulo fraco + concentração (hubness).

    `neighbors` precisa ter uma linha por item (na ordem de `data.docs`).
    """
    if neighbors.shape[0] != data.n:
        raise ValueError("uma linha de vizinhos por item é obrigatória")
    colls = {int(i): int(c) for i, c in zip(data.item_ids, data.collection, strict=True) if c >= 0}
    out = sanity_metrics(neighbors, data.docs, colls, k)
    queries = data.collection_queries()
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    ndcg = []
    for q in queries:
        others = int((data.collection == data.collection[q]).sum()) - 1
        rel = data.collection[neighbors[q, :k]] == data.collection[q]
        ndcg.append(float((rel * discounts).sum() / discounts[: min(k, others)].sum()))
    out[f"collection_ndcg@{k}"] = round(float(np.mean(ndcg)), 4) if ndcg else None
    indeg = np.bincount(neighbors[:, :k].ravel(), minlength=data.n)
    top1 = np.sort(indeg)[::-1][: max(1, data.n // 100)]
    out[f"hub_top1pct_share@{k}"] = round(float(top1.sum() / indeg.sum()), 4)
    return out


# --- Tarefa multi-seed (perfil) ---------------------------------------------------------


def multiseed_tasks(
    data: SimilarityData, n_seeds: int = 2, seed: int = 0
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Por coleção com >= n_seeds+1 membros: `n_seeds` membros sorteados viram o perfil e o
    restante é o alvo. Simula "usuário curtiu 2 filmes de uma franquia"."""
    rng = np.random.default_rng(seed)
    tasks = []
    for c in np.unique(data.collection[data.collection >= 0]):
        members = np.flatnonzero(data.collection == c)
        if members.size >= n_seeds + 1:
            seeds = np.sort(rng.choice(members, size=n_seeds, replace=False))
            tasks.append((seeds, np.setdiff1d(members, seeds)))
    return tasks


def profile_strategies(data: SimilarityData, rep: str) -> dict[str, Callable]:
    """Estratégias para várias seeds: só a 1ª seed, MaxSim (máx. por seed) e Rocchio."""
    x = _matrix(data, rep)
    base = cosine_scorer(data, rep)

    def single(seeds: np.ndarray) -> np.ndarray:
        s = base(seeds[:1])[0]
        s[seeds] = -np.inf
        return s

    def maxsim(seeds: np.ndarray) -> np.ndarray:
        s = base(seeds).max(axis=0)
        s[seeds] = -np.inf
        return s

    return {
        f"{rep}:single_seed": single,
        f"{rep}:maxsim": maxsim,
        f"{rep}:rocchio": lambda seeds: rocchio_profile_scores(x, seeds),
    }


def evaluate_multiseed(
    strategies: dict[str, Callable], tasks: list[tuple[np.ndarray, np.ndarray]], k: int = K
) -> dict[str, dict]:
    results = {}
    for name, fn in strategies.items():
        recall = []
        for seeds, targets in tasks:
            top = np.argsort(-fn(seeds), kind="stable")[:k]
            recall.append(len(set(top.tolist()) & set(targets.tolist())) / min(k, targets.size))
        arr = np.asarray(recall)
        lo, hi = _bootstrap_ci(arr)
        results[name] = {
            f"profile_recall@{k}": round(float(arr.mean()), 4),
            "profile_recall_ci95": [round(lo, 4), round(hi, 4)],
            "tasks": len(tasks),
        }
    return results
