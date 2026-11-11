"""Acesso ao banco (SQLAlchemy 2)."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from cinematch.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout_s},
    )


def check_database(engine: Engine) -> None:
    """Executa `SELECT 1`; propaga exceção se o banco estiver indisponível."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
