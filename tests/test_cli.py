import pytest

from cinematch import cli


def test_large_run_requires_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["ingest", "tmdb", "--types", "movie", "tv", "--max-items", "300"])
    assert code == 2
    assert "--yes" in capsys.readouterr().err


def test_missing_token_reports_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TMDB_API_TOKEN", "")
    code = cli.main(["ingest", "tmdb", "--max-items", "1"])
    assert code == 2
    assert "TMDB_API_TOKEN" in capsys.readouterr().err


def test_purge_retention_capped_at_180_days() -> None:
    with pytest.raises(SystemExit):
        cli.main(["ingest", "purge-bronze", "--older-than-days", "365"])


def test_purge_bronze(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CINEMATCH_BRONZE_DIR", str(tmp_path))
    cli.get_settings.cache_clear()
    try:
        assert cli.main(["ingest", "purge-bronze", "--older-than-days", "0"]) == 0
    finally:
        cli.get_settings.cache_clear()
