from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text, update

from cinematch.data.schema import item_external_ids
from cinematch.quality.checks import run_checks
from tests.integration.tmdb_fake import FakeTmdb


def _by_name(report: Any) -> dict[str, Any]:
    return {r.name: r for r in report.results}


def test_clean_catalog_passes_core_checks(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    ingest(item_types=["movie", "tv"], max_items_per_type=3)
    report = run_checks(engine, None)
    checks = _by_name(report)
    for name in (
        "catalog_not_empty",
        "items_have_primary_source_id",
        "title_not_blank",
        "primary_ids_have_provenance",
        "tmdb_sync_age",
        "catalog_count_drop",
    ):
        assert checks[name].status == "pass", name
    assert checks["coverage.movie.overview"].value == 1.0
    # Factories não têm elenco: cobertura de cast = 0 → fail esperado e detectado.
    assert checks["coverage.movie.cast_members"].status == "fail"
    assert report.summary["catalog_items"] == 6


def test_count_drop_detected(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    ingest(item_types=["movie"], max_items_per_type=10)
    run_checks(engine, None)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM catalog_items WHERE id IN "
                "(SELECT id FROM catalog_items ORDER BY id LIMIT 2)"
            )
        )
    check = _by_name(run_checks(engine, None))["catalog_count_drop"]
    assert check.status == "fail"
    assert check.details == {"previous": 10, "current": 8}


def test_stale_tmdb_sync_fails(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    ingest(item_types=["movie"], max_items_per_type=2)
    with engine.begin() as conn:
        conn.execute(
            update(item_external_ids)
            .where(item_external_ids.c.source == "tmdb")
            .values(last_synced_at=datetime.now(UTC) - timedelta(days=200))
        )
    report = run_checks(engine, None, save=False)
    assert _by_name(report)["tmdb_sync_age"].status == "fail"
    assert report.status == "fail"


def test_results_persisted(engine: Engine, fake: FakeTmdb, ingest: Any) -> None:
    ingest(item_types=["movie"], max_items_per_type=1)
    run_checks(engine, None)
    run_checks(engine, None, save=False)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM data_quality_runs")).scalar_one()
    assert n == 1
