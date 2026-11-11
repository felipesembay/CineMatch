from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from cinematch.ingestion.raw_store import (
    TMDB_LICENSE_NOTES,
    LocalRawStore,
    RawRecord,
    purge_older_than,
    read_records,
)


def _rec(run_id: str = "r1", ts: datetime | None = None, ext: str = "1") -> RawRecord:
    return RawRecord(
        source="tmdb",
        entity="movie",
        external_id=ext,
        endpoint=f"/movie/{ext}",
        request_params={"language": "en-US"},
        status_code=200,
        fetched_at=ts or datetime(2026, 11, 10, 12, tzinfo=UTC),
        run_id=run_id,
        license_notes=TMDB_LICENSE_NOTES,
        payload={"id": int(ext), "title": "T"},
    )


def test_write_batch_layout_and_provenance(tmp_path: Path) -> None:
    uri = LocalRawStore(tmp_path).write_batch([_rec(ext="1"), _rec(ext="2")])
    path = Path(unquote(urlparse(uri).path))
    rel = path.relative_to(tmp_path)
    assert rel.parts[:4] == ("source=tmdb", "entity=movie", "dt=2026-11-10", "run_id=r1")
    assert path.name.endswith(".jsonl.gz")

    rows = list(read_records(path))
    assert [r["external_id"] for r in rows] == ["1", "2"]
    for r in rows:
        assert r["source"] == "tmdb"
        assert r["run_id"] == "r1"
        assert r["fetched_at"].startswith("2026-11-10T12:00:00")
        assert "TMDB" in r["license_notes"]
        assert r["payload"]["title"] == "T"
    assert not list(tmp_path.rglob("*.tmp"))


def test_batches_never_overwrite(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    assert store.write_batch([_rec()]) != store.write_batch([_rec()])
    assert len(list(tmp_path.rglob("*.jsonl.gz"))) == 2


def test_mixed_batch_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalRawStore(tmp_path).write_batch([_rec(run_id="a"), _rec(run_id="b")])
    with pytest.raises(ValueError):
        LocalRawStore(tmp_path).write_batch([])


def test_purge_removes_only_old_partitions(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    store.write_batch([_rec(ts=datetime(2026, 1, 1, tzinfo=UTC))])
    store.write_batch([_rec(ts=datetime(2026, 11, 1, tzinfo=UTC))])
    removed = purge_older_than(tmp_path, 180, now=datetime(2026, 11, 10, tzinfo=UTC))
    assert [p.name for p in removed] == ["dt=2026-01-01"]
    remaining = [p.name for p in tmp_path.glob("source=*/entity=*/dt=*")]
    assert remaining == ["dt=2026-11-01"]


def test_relative_root_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    uri = LocalRawStore(Path("data/bronze")).write_batch([_rec()])
    path = Path(unquote(urlparse(uri).path))
    assert path.is_absolute()
    assert path.is_relative_to(tmp_path / "data" / "bronze")
