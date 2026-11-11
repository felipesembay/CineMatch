"""Refresh de itens TMDB antigos e expiração (termos TMDB: cache <= 6 meses)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, update

from cinematch.catalog.retention import expire_tmdb_items
from cinematch.data.schema import catalog_items, item_credits, item_external_ids, people
from cinematch.ingestion.pipeline import TmdbIngestion
from cinematch.ingestion.raw_store import LocalRawStore
from tests.integration.tmdb_fake import FakeTmdb, make_client


def _age(engine: Engine, days: int, external_ids: list[str] | None = None) -> None:
    ext = item_external_ids.c
    cond = ext.source == "tmdb"
    if external_ids is not None:
        cond = cond & ext.external_id.in_(external_ids)
    with engine.begin() as conn:
        conn.execute(
            update(item_external_ids)
            .where(cond)
            .values(last_synced_at=datetime.now(UTC) - timedelta(days=days))
        )


def _count(engine: Engine, table: Any) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _refresh(engine: Engine, tmp_path: Path, **kw: Any) -> Any:
    return TmdbIngestion(
        engine=engine, client=make_client(), store=LocalRawStore(tmp_path)
    ).refresh(**kw)


def test_refresh_only_stale_items(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    ingest(item_types=["movie"], max_items_per_type=4)
    _age(engine, 160, ["100", "101"])
    fake.detail_calls.clear()
    summary = _refresh(engine, tmp_path, older_than_days=150)
    assert summary.status == "succeeded"
    assert summary.metrics["selected_for_refresh"] == 2
    assert sorted(fake.detail_calls) == [("movie", 100), ("movie", 101)]
    assert summary.metrics["unchanged"] == 2  # conteúdo igual; só a data de sync avança

    again = _refresh(engine, tmp_path, older_than_days=150)
    assert again.metrics["selected_for_refresh"] == 0


def test_refresh_respects_max_items_oldest_first(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    ingest(item_types=["movie"], max_items_per_type=3)
    _age(engine, 170, ["102"])
    _age(engine, 160, ["100", "101"])
    fake.detail_calls.clear()
    _refresh(engine, tmp_path, older_than_days=150, max_items=1)
    assert fake.detail_calls == [("movie", 102)]


def test_refresh_404_then_expire(
    engine: Engine, fake: FakeTmdb, ingest: Any, tmp_path: Path
) -> None:
    fake.overrides[("movie", 100)] = {
        "credits": {"cast": [{"id": 9, "name": "Only Here", "order": 0, "credit_id": "c9"}]}
    }
    ingest(item_types=["movie"], max_items_per_type=2)
    _age(engine, 200)
    fake.failures[("movie", 100)] = [404]  # removido do TMDB
    summary = _refresh(engine, tmp_path, older_than_days=150)
    assert summary.metrics["not_found"] == 1

    dry = expire_tmdb_items(engine, 180)
    assert dry.candidates == 1 and not dry.applied
    assert _count(engine, catalog_items) == 2  # simulação não remove

    applied = expire_tmdb_items(engine, 180, apply=True)
    assert applied.deleted_items == 1
    assert applied.deleted_people == 1  # pessoa só aparecia no item expirado
    assert _count(engine, catalog_items) == 1
    assert _count(engine, item_credits) == 0
    assert _count(engine, people) == 0


def test_expire_rejects_window_beyond_terms(engine: Engine) -> None:
    with pytest.raises(ValueError):
        expire_tmdb_items(engine, 365)
