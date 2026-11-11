import numpy as np
import pytest

pytest.importorskip("sklearn")
from scipy import sparse

from cinematch.features.text import ItemDoc
from cinematch.similarity import methods as m
from cinematch.similarity import rerank as r
from cinematch.similarity.data import build_similarity_data
from cinematch.tracking import _numeric

N_COLL, PER_COLL, NOISE = 3, 4, 8


def _data(seed: int = 0):
    """3 coleções de 4 filmes + 8 itens soltos; vetores = centróide da coleção + ruído."""
    rng = np.random.default_rng(seed)
    n = N_COLL * PER_COLL + NOISE
    ids = list(range(100, 100 + n))
    coll = {ids[i]: 900 + i // PER_COLL for i in range(N_COLL * PER_COLL)}
    centers = rng.normal(size=(N_COLL + NOISE, 16))
    vecs, rows = {}, []
    for i, item in enumerate(ids):
        c = i // PER_COLL if item in coll else N_COLL + (i - N_COLL * PER_COLL)
        vecs[item] = (centers[c] + 0.3 * rng.normal(size=16)).astype(np.float32)
        row = np.zeros(30, dtype=np.float32)
        row[c] = 1.0
        row[rng.integers(N_COLL + NOISE, 30)] = 0.5
        rows.append(row / np.linalg.norm(row))
    docs = [
        ItemDoc(
            item,
            "movie",
            f"T{item}",
            "x",
            "tmdb",
            ("Drama",) if item in coll else ("Comedy",),
            (f"kw{coll.get(item, item)}",),
            (),
            (f"actor{coll.get(item, item)}",),
        )
        for item in ids
    ]
    attrs = {
        i: {"release_year": 2000, "original_language": "en", "popularity": 1.0, "vote_count": 10}
        for i in ids
    }
    tfidf = (sparse.csr_matrix(np.stack(rows)), np.array(ids), "tfidf_test")
    return build_similarity_data(docs, attrs, coll, tfidf, {"emb": (vecs, "emb_test")})


def test_data_alignment_and_fingerprint() -> None:
    d = _data()
    assert d.n == 20 and list(d.item_ids) == sorted(d.item_ids)
    assert d.collection_queries().size == N_COLL * PER_COLL
    assert np.allclose(np.linalg.norm(d.dense["emb"], axis=1), 1.0, atol=1e-5)
    assert d.fingerprint == _data().fingerprint
    assert d.index_of(105) == 5
    with pytest.raises(KeyError):
        d.index_of(1)


def test_rank_all_excludes_self_and_finds_collection() -> None:
    d = _data()
    for rep in ("tfidf", "emb"):
        nb = m.rank_all(m.cosine_scorer(d, rep), d.n, k=3)
        assert all(i not in row for i, row in enumerate(nb))
        assert m.neighbor_metrics(nb, d, k=3)["collection_recall@3"] > 0.9


def test_rrf_and_rocchio_prf_shapes() -> None:
    d = _data()
    s = m.rrf_scorer([m.cosine_scorer(d, "tfidf"), m.cosine_scorer(d, "emb")], d.n, depth=5)
    nb = m.rank_all(s, d.n, k=3)
    assert nb.shape == (20, 3)
    for rep in ("tfidf", "emb"):
        nb = m.rank_all(m.rocchio_prf_scorer(d, rep, m=2), d.n, k=3)
        assert all(i not in row for i, row in enumerate(nb))


def test_rocchio_profile_excludes_seeds_and_negatives_push_away() -> None:
    d = _data()
    x = d.dense["emb"]
    pos, neg = [0, 1], [4]
    scores = m.rocchio_profile_scores(x, pos, neg, gamma=0.5)
    assert np.isneginf(scores[[0, 1, 4]]).all()
    plain = m.rocchio_profile_scores(x, pos)
    assert scores[5] < plain[5]  # membro da coleção do negativo cai
    recs = m.recommend_for_profile(d, "emb", [100, 101], k=2)
    assert {i for i, _, _ in recs} <= {102, 103}
    with pytest.raises(ValueError):
        m.rocchio_profile_scores(x, [])


def test_multiseed_tasks_and_eval() -> None:
    d = _data()
    tasks = m.multiseed_tasks(d, n_seeds=2, seed=1)
    assert len(tasks) == N_COLL
    for seeds, targets in tasks:
        assert seeds.size == 2 and targets.size == 2 and not set(seeds) & set(targets)
    res = m.evaluate_multiseed(m.profile_strategies(d, "emb"), tasks, k=2)
    assert set(res) == {"emb:single_seed", "emb:maxsim", "emb:rocchio"}
    assert res["emb:rocchio"]["profile_recall@2"] == pytest.approx(1.0)


def test_build_candidates_labels_and_no_title_features() -> None:
    d = _data()
    c = r.build_candidates(d, "emb", depth=5)
    assert c.X.shape == (len(c.query), len(c.features))
    assert not any("title" in f for f in c.features)
    assert np.all(np.diff(c.query) >= 0)  # contíguo por consulta (LightGBM)
    assert not np.any(c.query == c.cand)
    same = d.collection[c.query] == d.collection[c.cand]
    np.testing.assert_array_equal(c.y == 1, same & (d.collection[c.query] >= 0))
    assert c.meta["candidate_recall_ceiling"] == pytest.approx(1.0)


def test_topk_from_pairs_tiebreak() -> None:
    c = r.Candidates(
        np.array([0, 0, 0, 1, 1]),
        np.array([3, 2, 1, 0, 2]),
        np.zeros((5, 1), np.float32),
        ["f"],
        np.zeros(5, np.int8),
    )
    nb = r.topk_from_pairs(c, np.array([1.0, 1.0, 0.5, 0.2, 0.9]), n=3, k=2)
    np.testing.assert_array_equal(nb[0], [2, 3])  # empate: menor índice primeiro
    np.testing.assert_array_equal(nb[1], [2, 0])
    assert (nb[2] == -1).all()  # sem candidatos
    # consulta com menos candidatos que k não invade a próxima
    idx, val = r.topk_pairs_with_scores(c, np.array([1.0, 1.0, 0.5, 0.2, 0.9]), n=3, k=4)
    np.testing.assert_array_equal(idx[1], [2, 0, -1, -1])
    assert np.isnan(val[1, 2:]).all() and val[1, 0] == 0.9


@pytest.mark.parametrize("name", r.SUPERVISED)
def test_cross_validate_reranker(name: str) -> None:
    pytest.importorskip("lightgbm")
    d = _data()
    c = r.build_candidates(d, "emb", depth=6)
    res = r.cross_validate_reranker(
        name, c, d, k=3, n_splits=3, min_child_samples=1, n_estimators=20
    )
    assert res.neighbors.shape == (20, 3) and (res.neighbors >= 0).all()
    assert len(res.fold_scores) == 3
    assert set(res.importance) == set(c.features)
    assert m.neighbor_metrics(res.neighbors, d, k=3)["collection_recall@3"] > 0.6


def test_make_model_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        r.make_model("xgb")


def test_numeric_metrics_for_mlflow() -> None:
    out = _numeric(
        {"recall@10": 0.5, "ci": [0.1, 0.9], "n": 3, "bad": float("nan"), "name": "x", "flag": True}
    )
    assert out == {"recall_at_10": 0.5, "ci_lo": 0.1, "ci_hi": 0.9, "n": 3.0}
