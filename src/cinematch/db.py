"""Acesso ao banco (SQLAlchemy 2)."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from cinematch.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle_s,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_s,
            "keepalives": 1,
            "keepalives_idle": 60,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            # rede instável até o banco remoto: aborta (e a pool reconecta) em vez de pendurar
            # o worker por ~60 s em retransmissão TCP.
            "tcp_user_timeout": 10_000,
        },
    )


@lru_cache(maxsize=8)
def autocommit(engine: Engine) -> Engine:
    """Mesma pool, sem transação explícita: só para leituras.

    Evita o `BEGIN` e o `ROLLBACK` que o psycopg envia em cada request (2 idas e voltas a menos
    com o banco remoto). Escritas continuam usando `engine.begin()` no engine normal.
    """
    return engine.execution_options(isolation_level="AUTOCOMMIT")


def check_database(engine: Engine) -> None:
    """Executa `SELECT 1`; propaga exceção se o banco estiver indisponível."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
