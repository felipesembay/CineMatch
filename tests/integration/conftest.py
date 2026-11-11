"""Fixtures de integração. Exigem CINEMATCH_TEST_DATABASE_URL apontando para um banco
cujo nome termina em `_test` (o schema é recriado do zero)."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text

from tests.integration.tmdb_fake import fake, ingest  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[2]
TEST_URL = os.environ.get("CINEMATCH_TEST_DATABASE_URL")


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    if not TEST_URL:
        pytest.skip("defina CINEMATCH_TEST_DATABASE_URL para testes de integração")
    database = make_url(TEST_URL).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"banco de teste deve terminar em '_test' (recebido: {database!r})")
    return TEST_URL


@pytest.fixture(scope="session")
def migrated_engine(db_url: str) -> Iterator[Engine]:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE item_genres, genres, item_keywords, keywords, item_credits, people, "
                "data_quality_runs, item_embeddings, neighbor_sets, item_neighbors, users, events, "
                "feature_artifacts, training_datasets, "
                "item_external_ids, "
                "catalog_items, tvmaze_shows, "
                "ingestion_run_items, ingestion_runs RESTART IDENTITY CASCADE"
            )
        )
    return migrated_engine
