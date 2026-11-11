"""TVmaze ponta a ponta + resolução de entidades + backfill de crosswalk (Postgres real)."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select

from cinematch.data.schema import item_external_ids, tvmaze_shows
from cinematch.ingestion import repository as runs
from cinematch.ingestion.http import RateLimiter, RetryingClient, RetryPolicy
from cinematch.ingestion.pipeline import TmdbIngestion
from cinematch.ingestion.raw_store import LocalRawStore
from cinematch.ingestion.tmdb.backfill import reprocess_tmdb_bronze
from cinematch.ingestion.tmdb.client import TmdbClient
from cinematch.ingestion.tvmaze.client import TvmazeClient
from cinematch.ingestion.tvmaze.pipeline import TvmazeIngestion, link_catalog
from tests.ingestion.factories import discover_payload, tv_payload, tvmaze_show

TVMAZE = "https://api.tvmaze.com"
TMDB = "https://api.themoviedb.org/3"


def _http(base: str) -> RetryingClient:
    return RetryingClient(
        client=httpx.Client(base_url=base),
        rate_limiter=RateLimiter(10_000),
        policy=RetryPolicy(max_attempts=3, base_backoff_s=0, jitter_s=0),
        sleep=lambda _s: None,
    )


class FakeTvmaze:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: list[int] = []
        self.fail_page: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        self.calls.append(page)
        if page == self.fail_page:
            self.fail_page = None
            return httpx.Response(400)  # não retentável: aborta o run
        if page >= len(self.pages):
            return httpx.Response(404)
        return httpx.Response(200, json=self.pages[page])


# TMDB: séries 10 (casa por IMDb+TVDB), 11 (só IMDb), 12 (só TVDB), 13 (sem IDs).
TMDB_SERIES = {
    10: {"imdb_id": "tt0000010", "tvdb_id": 1010},
    11: {"imdb_id": "tt0000011"},
    12: {"tvdb_id": 1012},
    13: {},
}


@pytest.fixture
def mocked(tmp_path: Path) -> Iterator[tuple[respx.MockRouter, FakeTvmaze]]:
    shows = [
        tvmaze_show(1, externals={"imdb": "tt0000010", "thetvdb": 1010, "tvrage": None}),
        tvmaze_show(2, externals={"imdb": "tt0000011", "thetvdb": None, "tvrage": None}),
        tvmaze_show(3, externals={"imdb": None, "thetvdb": 1012, "tvrage": None}),
        tvmaze_show(4, externals={"imdb": "tt9999999", "thetvdb": None, "tvrage": None}),
    ]
    fake = FakeTvmaze([shows[:2], shows[2:]])
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TVMAZE}/shows").mock(side_effect=fake.handler)
        router.get(f"{TMDB}/discover/tv").respond(
            200, json=discover_payload(list(TMDB_SERIES), page=1, total_pages=1)
        )
        router.get(url__regex=rf"{TMDB}/tv/(?P<tmdb_id>\d+)(?:\?|$)").mock(
            side_effect=lambda r, tmdb_id: httpx.Response(
                200, json=tv_payload(int(tmdb_id), external_ids=TMDB_SERIES[int(tmdb_id)])
            )
        )
        yield router, fake


@pytest.fixture
def tmdb_ingest(engine: Engine, tmp_path: Path) -> Callable[[], Any]:
    return lambda: TmdbIngestion(
        engine=engine, client=TmdbClient(_http(TMDB)), store=LocalRawStore(tmp_path)
    ).run(item_types=["tv"], max_items_per_type=10)


@pytest.fixture
def tvmaze_ingest(engine: Engine, tmp_path: Path) -> Callable[..., Any]:
    return lambda **kw: TvmazeIngestion(
        engine=engine, client=TvmazeClient(_http(TVMAZE)), store=LocalRawStore(tmp_path)
    ).run(**kw)


def _links(engine: Engine) -> dict[str, str]:
    ext = item_external_ids.c
    with engine.connect() as conn:
        rows = conn.execute(
            select(ext.external_id, ext.match_method).where(ext.source == "tvmaze")
        ).all()
    return dict(rows)


def test_crawl_link_and_idempotence(
    engine: Engine, mocked: Any, tmdb_ingest: Callable, tvmaze_ingest: Callable
) -> None:
    tmdb_ingest()
    first = tvmaze_ingest()
    assert first.status == "succeeded"
    assert first.metrics["pages"] == 2
    assert first.metrics["inserted"] == 4
    assert first.metrics["link_linked"] == 3
    assert _links(engine) == {"1": "imdb_id+thetvdb_id", "2": "imdb_id", "3": "thetvdb_id"}

    second = tvmaze_ingest()
    assert second.metrics["inserted"] == 0
    assert second.metrics["unchanged"] == 4
    assert second.metrics["link_linked"] == 3
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(tvmaze_shows)).scalar_one() == 4
        tvmaze_rows = conn.execute(
            select(func.count()).where(item_external_ids.c.source == "tvmaze")
        ).scalar_one()
    assert tvmaze_rows == 3


def test_ambiguous_ids_not_linked(
    engine: Engine, mocked: Any, tmdb_ingest: Callable, tvmaze_ingest: Callable
) -> None:
    _, fake = mocked
    # IMDb aponta para a série 10, TVDB para a 12: conflito → não liga.
    fake.pages = [[tvmaze_show(1, externals={"imdb": "tt0000010", "thetvdb": 1012})]]
    tmdb_ingest()
    summary = tvmaze_ingest()
    assert summary.metrics["link_ambiguous_shows"] == 1
    assert _links(engine) == {}


def test_two_shows_same_item_not_linked(
    engine: Engine, mocked: Any, tmdb_ingest: Callable, tvmaze_ingest: Callable
) -> None:
    _, fake = mocked
    fake.pages = [
        [
            tvmaze_show(1, externals={"imdb": "tt0000011", "thetvdb": None}),
            tvmaze_show(2, externals={"imdb": "tt0000011", "thetvdb": None}),
        ]
    ]
    tmdb_ingest()
    summary = tvmaze_ingest()
    assert summary.metrics["link_ambiguous_items"] == 1
    assert _links(engine) == {}


def test_stale_link_removed(
    engine: Engine, mocked: Any, tmdb_ingest: Callable, tvmaze_ingest: Callable
) -> None:
    tmdb_ingest()
    tvmaze_ingest()
    _, fake = mocked
    fake.pages = [[tvmaze_show(2, externals={"imdb": None, "thetvdb": None})]]
    summary = tvmaze_ingest()
    assert summary.metrics["link_unlinked_stale_removed"] >= 1
    assert "2" not in _links(engine)


def test_max_pages_and_resume_after_failure(
    engine: Engine, mocked: Any, tvmaze_ingest: Callable
) -> None:
    _, fake = mocked
    fake.fail_page = 1
    with pytest.raises(Exception, match="HTTP 400"):
        tvmaze_ingest()
    with engine.connect() as conn:
        (run,) = runs.latest_runs(conn, 1)
    assert run["status"] == "failed"
    assert run["state"]["next_page"] == 1  # página 0 persistida

    fake.calls.clear()
    resumed = tvmaze_ingest(resume_run_id=run["run_id"])
    assert resumed.status == "succeeded"
    assert fake.calls == [1, 2]  # não refaz a página 0

    limited = tvmaze_ingest(max_pages=1)
    assert limited.metrics["pages"] == 1


def test_crosswalk_backfill_from_bronze(
    engine: Engine, mocked: Any, tmdb_ingest: Callable, tmp_path: Path
) -> None:
    tmdb_ingest()
    ext = item_external_ids.c
    with engine.begin() as conn:  # simula itens coletados antes do crosswalk existir
        conn.execute(item_external_ids.delete().where(ext.source.in_(["imdb", "tvdb"])))
    stats = reprocess_tmdb_bronze(engine, tmp_path)
    assert stats["crosswalk_ids"] == 4  # 10: imdb+tvdb, 11: imdb, 12: tvdb
    assert stats["items_without_crosswalk"] == 1
    assert stats["unchanged"] == 4  # conteúdo igual ao já gravado
    with engine.begin() as conn:
        assert link_catalog(conn, None, datetime.now(UTC))["linked"] == 0  # sem shows ainda
        n = conn.execute(
            select(func.count()).where(ext.match_method == "tmdb_external_ids")
        ).scalar_one()
    assert n == 4
