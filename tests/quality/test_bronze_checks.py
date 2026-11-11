from datetime import UTC, datetime
from pathlib import Path

from cinematch.ingestion.raw_store import LocalRawStore, RawRecord
from cinematch.quality.checks import CheckResult, check_bronze, overall
from tests.ingestion.factories import movie_payload

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def _write(root: Path, payload: dict, ts: datetime = NOW) -> None:
    LocalRawStore(root).write_batch(
        [RawRecord("tmdb", "movie", str(payload["id"]), "/movie/x", {}, 200, ts, "r", "n", payload)]
    )


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


def test_schema_ok_then_drift_warns(tmp_path: Path) -> None:
    _write(tmp_path, movie_payload(1))
    res, schema = check_bronze(tmp_path, None, NOW)
    assert _by_name(res)["bronze_schema.tmdb/movie"].status == "pass"

    _write(tmp_path, movie_payload(2, new_field=1))
    res, _ = check_bronze(tmp_path, schema, NOW)
    drift = _by_name(res)["bronze_schema.tmdb/movie"]
    assert drift.status == "warn"
    assert drift.details["added"] == ["new_field"]


def test_missing_required_key_fails(tmp_path: Path) -> None:
    payload = movie_payload(1)
    del payload["overview"]
    _write(tmp_path, payload)
    res, _ = check_bronze(tmp_path, None, NOW)
    check = _by_name(res)["bronze_schema.tmdb/movie"]
    assert check.status == "fail"
    assert check.details["missing_required"] == ["overview"]


def test_tmdb_retention(tmp_path: Path) -> None:
    _write(tmp_path, movie_payload(1), ts=datetime(2026, 1, 1, tzinfo=UTC))
    res, _ = check_bronze(tmp_path, None, NOW)
    assert _by_name(res)["bronze_tmdb_retention"].status == "fail"


def test_overall_ignores_info() -> None:
    assert overall([CheckResult("a", "info"), CheckResult("b", "pass")]) == "pass"
    assert overall([CheckResult("a", "warn"), CheckResult("b", "pass")]) == "warn"
    assert overall([CheckResult("a", "warn"), CheckResult("b", "fail")]) == "fail"
