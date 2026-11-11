"""Pipeline TMDB ponta a ponta: HTTP mockado (respx) + PostgreSQL real + Bronze em tmp."""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import Engine, func, select

from cinematch.data.schema import catalog_items, genres, item_external_ids, item_genres
from cinematch.ingestion import repository as repo
from cinematch.ingestion.http import AuthError
from cinematch.ingestion.pipeline import IngestionError
from cinematch.ingestion.raw_store import read_records
from tests.integration.tmdb_fake import FakeTmdb


def _count(engine: Engine, table: Any) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _count_tmdb_ids(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).where(item_external_ids.c.source == "tmdb")
        ).scalar_one()


def test_crosswalk_written_on_ingest(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    ingest(item_types=["movie"], max_items_per_type=2)
    with engine.connect() as conn:
        rows = conn.execute(
            select(item_external_ids.c.external_id, item_external_ids.c.match_method).where(
                item_external_ids.c.source == "imdb"
            )
        ).all()
    assert sorted(rows) == [("tt0000100", "tmdb_external_ids"), ("tt0000101", "tmdb_external_ids")]


def test_two_runs_do_not_duplicate(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    first = ingest(item_types=["movie", "tv"], max_items_per_type=6)
    assert first.status == "succeeded"
    assert first.metrics["inserted"] == 6 + 5  # tv só tem 5 itens no total
    assert first.metrics["discovered"] == 11

    second = ingest(item_types=["movie", "tv"], max_items_per_type=6)
    assert second.status == "succeeded"
    assert second.metrics["inserted"] == 0
    assert second.metrics["unchanged"] == 11

    assert _count(engine, catalog_items) == 11
    assert _count_tmdb_ids(engine) == 11
    assert _count(engine, genres) == 3  # Drama, Comedy, Sci-Fi & Fantasy (Drama compartilhado)


def test_max_items_respected_across_pages(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    summary = ingest(item_types=["movie"], max_items_per_type=7)  # 2 páginas de 4
    assert summary.metrics["discovered"] == 7
    assert summary.metrics["discover_pages"] == 2
    assert _count(engine, catalog_items) == 7


def test_movie_and_tv_with_same_id_are_distinct(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    fake.ids = {"movie": [42], "tv": [42]}
    ingest(item_types=["movie", "tv"], max_items_per_type=1)
    with engine.connect() as conn:
        rows = (
            conn.execute(
                select(item_external_ids.c.external_type).where(
                    item_external_ids.c.source == "tmdb"
                )
            )
            .scalars()
            .all()
        )
    assert sorted(rows) == ["movie", "tv"]


def test_change_detected_updates_item_and_genres(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    ingest(item_types=["movie"], max_items_per_type=2)
    with engine.connect() as conn:
        before = conn.execute(select(catalog_items.c.updated_at).order_by("id")).scalars().all()

    fake.overrides[("movie", 100)] = {"overview": "new", "genres": [{"id": 27, "name": "Horror"}]}
    summary = ingest(item_types=["movie"], max_items_per_type=2)
    assert summary.metrics["updated"] == 1
    assert summary.metrics["unchanged"] == 1

    with engine.connect() as conn:
        item = conn.execute(
            select(catalog_items)
            .join(item_external_ids, item_external_ids.c.item_id == catalog_items.c.id)
            .where(item_external_ids.c.external_id == "100")
        ).one()
        after = conn.execute(select(catalog_items.c.updated_at).order_by("id")).scalars().all()
        names = (
            conn.execute(
                select(genres.c.name)
                .join(item_genres, item_genres.c.genre_id == genres.c.id)
                .where(item_genres.c.item_id == item.id)
            )
            .scalars()
            .all()
        )
    assert item.overview == "new"
    assert names == ["Horror"]
    assert after[0] > before[0]
    assert after[1] == before[1]  # item inalterado mantém updated_at


def test_provenance_recorded(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    summary = ingest(item_types=["movie"], max_items_per_type=1)
    with engine.connect() as conn:
        ext = conn.execute(
            select(item_external_ids).where(item_external_ids.c.source == "tmdb")
        ).one()
    assert ext.source == "tmdb"
    assert ext.last_run_id == summary.run_id
    assert ext.last_synced_at is not None
    path = Path(unquote(urlparse(ext.raw_uri).path))
    (raw,) = list(read_records(path))
    assert raw["external_id"] == ext.external_id
    assert raw["run_id"] == summary.run_id
    assert raw["license_notes"]


def test_transient_errors_retried(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    fake.failures[("movie", 100)] = [503, 429]
    summary = ingest(item_types=["movie"], max_items_per_type=2)
    assert summary.status == "succeeded"
    assert summary.metrics["http_retries"] == 2
    assert summary.metrics["inserted"] == 2


def test_not_found_is_not_failure(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    fake.failures[("movie", 101)] = [404]
    summary = ingest(item_types=["movie"], max_items_per_type=2)
    assert summary.status == "succeeded"
    assert summary.metrics["not_found"] == 1
    assert _count(engine, catalog_items) == 1


def test_persistent_failure_partial_then_resume(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    fake.failures[("movie", 102)] = [500, 500, 500]  # esgota as 3 tentativas
    first = ingest(item_types=["movie"], max_items_per_type=4)
    assert first.status == "partial"
    assert first.metrics["items_by_status"] == {"done": 3, "failed": 1}

    fake.detail_calls.clear()
    resumed = ingest(resume_run_id=first.run_id)
    assert resumed.run_id == first.run_id
    assert resumed.status == "succeeded"
    assert fake.detail_calls == [("movie", 102)]  # só o item que falhou
    assert _count(engine, catalog_items) == 4


def test_crash_then_resume_skips_done_items(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    fake.failures[("movie", 105)] = [401]  # segundo lote (batch_size=4) aborta o run
    with pytest.raises(AuthError):
        ingest(item_types=["movie"], max_items_per_type=8)
    with engine.connect() as conn:
        (run,) = repo.latest_runs(conn, 1)
    assert run["status"] == "failed"
    assert "AuthError" in run["error"]
    assert _count(engine, catalog_items) == 4  # 1º lote persistido

    fake.detail_calls.clear()
    resumed = ingest(resume_run_id=run["run_id"])
    assert resumed.status == "succeeded"
    assert {i for _, i in fake.detail_calls} == {104, 105, 106, 107}
    assert _count(engine, catalog_items) == 8


def test_cannot_resume_succeeded_run(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    summary = ingest(item_types=["movie"], max_items_per_type=1)
    with pytest.raises(IngestionError, match="já concluído"):
        ingest(resume_run_id=summary.run_id)


def test_concurrent_run_blocked(engine: Engine, fake: FakeTmdb, ingest: Callable) -> None:
    with engine.connect() as conn:
        assert repo.try_lock_source(conn, "tmdb")
        try:
            with pytest.raises(repo.ConcurrentRunError):
                ingest(item_types=["movie"], max_items_per_type=1)
        finally:
            repo.unlock_source(conn, "tmdb")


def test_skip_recently_synced_fetches_only_new(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    ingest(item_types=["movie"], max_items_per_type=4)  # 100..103
    fake.detail_calls.clear()
    summary = ingest(item_types=["movie"], max_items_per_type=3, skip_synced_within_days=30)
    assert summary.status == "succeeded"
    assert summary.metrics["skipped_fresh"] == 4
    assert summary.metrics["inserted"] == 3
    assert {i for _, i in fake.detail_calls} == {104, 105, 106}
    assert _count(engine, catalog_items) == 7


def test_skip_window_zero_refetches_nothing_newer(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    ingest(item_types=["movie"], max_items_per_type=2)
    fake.detail_calls.clear()
    # Janela de 0 dias: só pula o que foi sincronizado a partir de agora, ou seja, nada.
    summary = ingest(item_types=["movie"], max_items_per_type=2, skip_synced_within_days=0)
    assert summary.metrics.get("skipped_fresh", 0) == 0  # sync anterior é mais antigo que agora
    assert len(fake.detail_calls) == 2


def test_keywords_and_credits_persisted_and_replaced(
    engine: Engine, fake: FakeTmdb, ingest: Callable
) -> None:
    from cinematch.data.schema import item_credits, item_keywords, keywords, people

    credits = {
        "cast": [{"id": 5, "name": "A", "character": "X", "order": 0, "credit_id": "c1"}],
        "crew": [
            {"id": 6, "name": "D", "job": "Director", "department": "Directing", "credit_id": "c2"}
        ],
    }
    fake.overrides[("movie", 100)] = {"credits": credits}
    ingest(item_types=["movie"], max_items_per_type=2)
    assert _count(engine, item_credits) == 2
    assert _count(engine, people) == 2
    assert _count(engine, keywords) == 1  # "friendship" compartilhada pelos 2 filmes
    assert _count(engine, item_keywords) == 2

    fake.overrides[("movie", 100)] = {
        "credits": {"cast": [], "crew": credits["crew"]},
        "keywords": {"keywords": [{"id": 2, "name": "heist"}]},
    }
    summary = ingest(item_types=["movie"], max_items_per_type=2)
    assert summary.metrics["updated"] == 1
    assert _count(engine, item_credits) == 1  # elenco removido
    assert _count(engine, people) == 2  # pessoas permanecem (podem ter outros créditos)
    with engine.connect() as conn:
        names = (
            conn.execute(select(keywords.c.name).join(item_keywords).order_by(keywords.c.name))
            .scalars()
            .all()
        )
    assert names == ["friendship", "heist"]
