"""Snapshots Gold: gate de qualidade, idempotência, as_of, manifesto e integridade."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import Engine, update

pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

from cinematch.data.gold import SnapshotRefused, create_snapshot
from cinematch.data.schema import catalog_items, item_external_ids
from cinematch.features.embeddings import compute_embeddings
from cinematch.quality.checks import run_checks
from tests.integration.test_features import FakeEncoder
from tests.integration.tmdb_fake import FakeTmdb


def _passing_quality_run(engine: Engine) -> None:
    from sqlalchemy import insert

    from cinematch.data.schema import data_quality_runs

    with engine.begin() as conn:
        conn.execute(insert(data_quality_runs).values(status="pass", summary={}, results=[]))


def _path(manifest: dict[str, Any]) -> Path:
    return Path(unquote(urlparse(manifest["uri"]).path))


def test_refused_without_quality_run(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    ingest(item_types=["movie"], max_items_per_type=2)
    with pytest.raises(SnapshotRefused, match="quality"):
        create_snapshot(engine, tmp_path)
    assert create_snapshot(engine, tmp_path, allow_failing_quality=True)["reused"] is False


def test_snapshot_contents_manifest_and_idempotence(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    ingest(item_types=["movie", "tv"], max_items_per_type=3)
    compute_embeddings(engine, FakeEncoder())
    assert run_checks(engine, None).status == "fail"  # factories sem elenco
    with pytest.raises(SnapshotRefused):
        create_snapshot(engine, tmp_path)
    _passing_quality_run(engine)

    m1 = create_snapshot(engine, tmp_path)
    out = _path(m1)
    items = pq.read_table(out / "items.parquet")
    emb = pq.read_table(out / "embeddings.parquet")
    assert items.num_rows == 6 and emb.num_rows == 6
    assert items.column("item_id").to_pylist() == sorted(items.column("item_id").to_pylist())
    assert m1["rows_by_type"] == {"movie": 3, "tv": 3}
    assert m1["embedding_dimensions"] == 8
    assert m1["item_features_point_in_time"] is False
    assert m1["alembic_revision"]
    for fname, meta in m1["files"].items():
        assert hashlib.sha256((out / fname).read_bytes()).hexdigest() == meta["sha256"]
    assert json.loads((out / "manifest.json").read_text())["snapshot_id"] == m1["snapshot_id"]

    m2 = create_snapshot(engine, tmp_path)  # mesmo conteúdo
    assert m2["reused"] is True and m2["snapshot_id"] == m1["snapshot_id"]

    with engine.begin() as conn:
        conn.execute(update(catalog_items).where(catalog_items.c.id == 1).values(title="Changed"))
    m3 = create_snapshot(engine, tmp_path)
    assert m3["reused"] is False and m3["content_hash"] != m1["content_hash"]


def test_as_of_excludes_later_items(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    ingest(item_types=["movie"], max_items_per_type=3)
    _passing_quality_run(engine)
    old = datetime.now(UTC) - timedelta(days=10)
    with engine.begin() as conn:
        conn.execute(
            update(item_external_ids)
            .where(item_external_ids.c.source == "tmdb", item_external_ids.c.external_id == "100")
            .values(first_seen_at=old - timedelta(days=1))
        )
    m = create_snapshot(engine, tmp_path, as_of=old)
    assert m["rows_by_type"] == {"movie": 1}
