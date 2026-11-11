import httpx
import pytest
import respx
from pydantic import SecretStr

from cinematch.config import TmdbSettings
from cinematch.ingestion.http import AuthError
from cinematch.ingestion.tmdb.client import TmdbClient
from tests.ingestion.factories import discover_payload

BASE = "https://api.themoviedb.org/3"


def _settings(token: str | None = "test-token") -> TmdbSettings:
    return TmdbSettings(
        _env_file=None,
        api_token=SecretStr(token) if token is not None else None,
        requests_per_second=20,
    )


def test_missing_token_fails_fast() -> None:
    with pytest.raises(AuthError, match="TMDB_API_TOKEN"):
        TmdbClient.from_settings(_settings(None))
    with pytest.raises(AuthError):
        TmdbClient.from_settings(_settings(""))


@respx.mock
def test_bearer_header_and_token_not_in_url() -> None:
    route = respx.get(f"{BASE}/discover/movie").respond(
        200, json=discover_payload([1, 2], page=1, total_pages=1)
    )
    client = TmdbClient.from_settings(_settings())
    page = client.discover_page("movie", 1)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert "test-token" not in str(request.url)
    assert request.url.params["sort_by"] == "popularity.desc"
    assert request.url.params["include_adult"] == "false"
    assert page.ids == [1, 2]


@respx.mock
def test_iter_discover_stops_at_total_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=discover_payload([page * 10], page, total_pages=3))

    route = respx.get(f"{BASE}/discover/tv").mock(side_effect=handler)
    client = TmdbClient.from_settings(_settings())
    pages = list(client.iter_discover("tv"))
    assert [p.page for p in pages] == [1, 2, 3]
    assert route.call_count == 3


@respx.mock
def test_discover_total_pages_capped_at_500() -> None:
    respx.get(f"{BASE}/discover/movie").respond(
        200, json=discover_payload([1], page=1, total_pages=9999)
    )
    assert TmdbClient.from_settings(_settings()).discover_page("movie", 1).total_pages == 500


@respx.mock
def test_details_appends_related_resources() -> None:
    route = respx.get(f"{BASE}/movie/550").respond(200, json={"id": 550})
    TmdbClient.from_settings(_settings()).details("movie", 550)
    assert route.calls.last.request.url.params["append_to_response"] == (
        "keywords,credits,external_ids"
    )
