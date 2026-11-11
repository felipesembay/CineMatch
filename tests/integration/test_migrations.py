from alembic import command
from sqlalchemy import Engine, inspect

from tests.integration.conftest import alembic_config

EXPECTED = {
    "training_datasets",
    "item_embeddings",
    "feature_artifacts",
    "keywords",
    "item_keywords",
    "people",
    "item_credits",
    "data_quality_runs",
    "tvmaze_shows",
    "catalog_items",
    "item_external_ids",
    "genres",
    "item_genres",
    "ingestion_runs",
    "ingestion_run_items",
}


def test_upgrade_downgrade_roundtrip(db_url: str, migrated_engine: Engine) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    assert EXPECTED.isdisjoint(inspect(migrated_engine).get_table_names())
    command.upgrade(cfg, "head")
    assert set(inspect(migrated_engine).get_table_names()) >= EXPECTED


def test_schema_matches_metadata(db_url: str, migrated_engine: Engine) -> None:
    # Falha se src/cinematch/data/schema.py divergir das migrações.
    command.check(alembic_config(db_url))
