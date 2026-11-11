"""Embeddings (encoder falso, determinístico) e TF-IDF contra Postgres real."""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import Engine, func, select, text, update

from cinematch.data.schema import catalog_items, feature_artifacts, item_embeddings
from cinematch.features.embeddings import compute_embeddings, model_version
from cinematch.features.neighbors import embedding_neighbors
from cinematch.features.text import load_item_documents
from tests.integration.tmdb_fake import FakeTmdb


class FakeEncoder:
    model_id = "fake/encoder@000000000000"
    dimensions = 8

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray:
        self.calls += len(texts)
        out = []
        for t in texts:
            seed = int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
            v = np.random.default_rng(seed).normal(size=self.dimensions)
            out.append(v / np.linalg.norm(v))
        return np.asarray(out, dtype=np.float32)


def test_embeddings_incremental_and_versioned(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    ingest(item_types=["movie"], max_items_per_type=5)
    enc = FakeEncoder()
    first = compute_embeddings(engine, enc, batch_size=2)
    assert (first.items, first.encoded, first.unchanged) == (5, 5, 0)
    assert first.model_version == "fake/encoder@000000000000|emb_text_v1"

    second = compute_embeddings(engine, enc, batch_size=2)
    assert (second.encoded, second.unchanged) == (0, 5)
    assert enc.calls == 5

    with engine.begin() as conn:
        conn.execute(update(catalog_items).where(catalog_items.c.id == 1).values(overview="new"))
    third = compute_embeddings(engine, enc)
    assert third.encoded == 1

    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(item_embeddings)).scalar_one()
        dims = conn.execute(text("SELECT DISTINCT vector_dims(embedding) FROM item_embeddings"))
        assert dims.scalars().all() == [8]
    assert n == 5


def test_embedding_neighbors_exclude_self_and_sorted(
    engine: Engine, fake: FakeTmdb, ingest: Any
) -> None:
    ingest(item_types=["movie"], max_items_per_type=6)
    enc = FakeEncoder()
    compute_embeddings(engine, enc)
    with engine.connect() as conn:
        pairs = embedding_neighbors(conn, 1, model_version(enc), k=3)
        with pytest.raises(KeyError):
            embedding_neighbors(conn, 999, model_version(enc))
    assert len(pairs) == 3
    assert 1 not in [i for i, _ in pairs]
    scores = [s for _, s in pairs]
    assert scores == sorted(scores, reverse=True)


def test_tvmaze_summary_used_as_overview_fallback(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO catalog_items (id, item_type, title, content_hash)
              VALUES (1, 'tv', 'S', 'h');
            INSERT INTO tvmaze_shows (tvmaze_id, name, summary, content_hash, last_synced_at)
              VALUES (77, 'S', 'From TVmaze.', 'h', now());
            INSERT INTO item_external_ids (item_id, source, external_type, external_id,
                                           last_synced_at, match_method)
              VALUES (1, 'tvmaze', 'show', '77', now(), 'imdb_id');
        """)
        )
        (doc,) = load_item_documents(conn)
    assert doc.overview == "From TVmaze."
    assert doc.overview_source == "tvmaze"


def test_tfidf_deterministic_and_registered(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    from cinematch.features.tfidf import build_tfidf, load_latest_tfidf

    ingest(item_types=["movie", "tv"], max_items_per_type=4)
    m1 = build_tfidf(engine, tmp_path)
    m2 = build_tfidf(engine, tmp_path)
    assert m1["corpus_hash"] == m2["corpus_hash"]
    assert m1["n_items"] == 8
    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(feature_artifacts)).scalar_one()
    assert n == 1  # mesmo corpus + versão = mesmo artefato

    model = load_latest_tfidf(engine)
    pairs = model.neighbors(1, k=3)
    assert len(pairs) == 3 and 1 not in [i for i, _ in pairs]
    assert pairs == model.neighbors(1, k=3)


def test_quality_check_flags_stale_embeddings(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    from cinematch.quality.checks import run_checks

    ingest(item_types=["movie"], max_items_per_type=3)
    compute_embeddings(engine, FakeEncoder())

    def emb_check() -> Any:
        report = run_checks(engine, None, save=False)
        return next(r for r in report.results if r.name.startswith("embeddings["))

    assert emb_check().status == "pass"
    with engine.begin() as conn:
        conn.execute(update(catalog_items).where(catalog_items.c.id == 1).values(overview="x"))
    check = emb_check()
    assert check.status == "warn"
    assert check.details["stale"] == 1
