import numpy as np
import pytest

pytest.importorskip("scipy")
from scipy import sparse

from cinematch.evaluation.content_sanity import (
    _bootstrap_ci,
    sanity_metrics,
    topk_dense,
    topk_sparse,
)
from cinematch.features.text import ItemDoc


def _doc(i: int, title: str, genres: tuple[str, ...], t: str = "movie") -> ItemDoc:
    return ItemDoc(i, t, title, None, None, genres, (), (), ())


def test_topk_excludes_self_and_is_deterministic() -> None:
    m = np.eye(4, dtype=np.float32)
    m[1] = m[0]  # 0 e 1 idênticos
    nb = topk_dense(m, k=2)
    assert nb[0, 0] == 1 and nb[1, 0] == 0
    assert all(i not in row for i, row in enumerate(nb))
    np.testing.assert_array_equal(nb, topk_dense(m, k=2))
    np.testing.assert_array_equal(topk_sparse(sparse.csr_matrix(m), k=2)[:2, 0], [1, 0])


def test_sanity_metrics_values() -> None:
    docs = [
        _doc(1, "Alien", ("Horror",)),
        _doc(2, "Aliens", ("Horror", "Action")),
        _doc(3, "Romance Story", ("Romance",)),
    ]
    neighbors = np.array([[1, 2], [0, 2], [0, 1]])
    m = sanity_metrics(neighbors, docs, collections={1: 7, 2: 7}, k=2)
    assert m["collection_queries"] == 2
    assert m["collection_recall@2"] == 1.0  # 1 encontra 2 e vice-versa
    assert m["genre_overlap@2"] == pytest.approx((0.5 + 0.5 + 0.0) / 3, abs=1e-4)
    assert m["catalog_coverage@2"] == 1.0


def test_bootstrap_ci_bounds() -> None:
    lo, hi = _bootstrap_ci(np.array([0.0, 1.0] * 50))
    assert 0.35 < lo < 0.5 < hi < 0.65
