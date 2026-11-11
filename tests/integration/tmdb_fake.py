"""Servidor TMDB falso (respx) e fixtures de ingestão compartilhadas pelos testes."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine

from cinematch.ingestion.http import RateLimiter, RetryingClient, RetryPolicy
from cinematch.ingestion.pipeline import TmdbIngestion
from cinematch.ingestion.raw_store import LocalRawStore
from cinematch.ingestion.tmdb.client import TmdbClient
from tests.ingestion.factories import discover_payload, movie_payload, tv_payload

BASE = "https://api.themoviedb.org/3"
PAGE_SIZE = 4


class FakeTmdb:
    """Servidor TMDB falso com páginas determinísticas e falhas injetáveis."""

    def __init__(self, movie_ids: list[int], tv_ids: list[int]) -> None:
        self.ids = {"movie": movie_ids, "tv": tv_ids}
        self.overrides: dict[tuple[str, int], dict[str, Any]] = {}
        self.failures: dict[tuple[str, int], list[int]] = {}  # status por chamada
        self.detail_calls: list[tuple[str, int]] = []

    def discover(self, request: httpx.Request, item_type: str) -> httpx.Response:
        page = int(request.url.params["page"])
        ids = self.ids[item_type]
        total = max(1, -(-len(ids) // PAGE_SIZE))
        chunk = ids[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        return httpx.Response(200, json=discover_payload(chunk, page, total))

    def detail(self, request: httpx.Request, item_type: str, tmdb_id: str) -> httpx.Response:
        key = (item_type, int(tmdb_id))
        self.detail_calls.append(key)
        queue = self.failures.get(key)
        if queue:
            status = queue.pop(0)
            return httpx.Response(status)
        factory = movie_payload if item_type == "movie" else tv_payload
        return httpx.Response(200, json=factory(int(tmdb_id), **self.overrides.get(key, {})))

    def install(self, router: respx.MockRouter) -> None:
        for t in ("movie", "tv"):
            router.get(f"{BASE}/discover/{t}").mock(side_effect=lambda r, t=t: self.discover(r, t))
        router.get(url__regex=rf"{BASE}/(?P<item_type>movie|tv)/(?P<tmdb_id>\d+)(?:\?|$)").mock(
            side_effect=self.detail
        )


@pytest.fixture
def fake() -> Iterator[FakeTmdb]:
    server = FakeTmdb(movie_ids=list(range(100, 110)), tv_ids=list(range(200, 205)))
    with respx.mock(assert_all_called=False) as router:
        server.install(router)
        yield server


def make_client() -> TmdbClient:
    http = RetryingClient(
        client=httpx.Client(base_url=BASE, headers={"Authorization": "Bearer t"}),
        rate_limiter=RateLimiter(10_000),
        policy=RetryPolicy(max_attempts=3, base_backoff_s=0, jitter_s=0),
        sleep=lambda _s: None,
    )
    return TmdbClient(http)


@pytest.fixture
def ingest(engine: Engine, tmp_path: Path) -> Callable[..., Any]:
    def _run(**kwargs: Any) -> Any:
        pipeline = TmdbIngestion(
            engine=engine, client=make_client(), store=LocalRawStore(tmp_path), batch_size=4
        )
        return pipeline.run(**kwargs)

    return _run
