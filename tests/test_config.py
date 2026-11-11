import pytest

from cinematch.config import Settings


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CINEMATCH_ENV", "staging")
    monkeypatch.setenv("CINEMATCH_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", "postgresql+psycopg://u:secret@db:5432/x")
    s = Settings(_env_file=None)
    assert s.env == "staging"
    assert s.log_level == "DEBUG"
    assert s.database_url.get_secret_value().endswith("@db:5432/x")


def test_database_url_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CINEMATCH_DATABASE_URL", "postgresql+psycopg://u:supersecret@db/x")
    s = Settings(_env_file=None)
    assert "supersecret" not in repr(s)


def test_invalid_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CINEMATCH_ENV", "prod-typo")
    with pytest.raises(ValueError):
        Settings(_env_file=None)
