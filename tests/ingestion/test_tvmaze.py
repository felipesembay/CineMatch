from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from cinematch.config import TvmazeSettings
from cinematch.ingestion.tmdb.normalize import normalize
from cinematch.ingestion.tvmaze.client import USER_AGENT, TvmazeClient
from cinematch.ingestion.tvmaze.normalize import NormalizationError, normalize_show, strip_html
from tests.ingestion.factories import movie_payload, tv_payload, tvmaze_show

BASE = "https://api.tvmaze.com"


def test_normalize_show_fields() -> None:
    rec = normalize_show(tvmaze_show(1))
    assert rec.tvmaze_id == 1
    assert rec.genres == ("Drama", "Thriller")
    assert rec.premiered == date(2013, 6, 24)
    assert rec.network == "CBS"
    assert rec.country_code == "US"
    assert rec.imdb_id == "tt1553656"
    assert rec.thetvdb_id == "264492"
    assert rec.summary == "Under the Dome is the story & more. Second."
    assert rec.source_updated_at == datetime.fromtimestamp(1704794122, tz=UTC)


def test_normalize_show_web_channel_and_missing_values() -> None:
    rec = normalize_show(
        tvmaze_show(
            2,
            network=None,
            webChannel={"name": "Netflix", "country": None},
            externals={"imdb": "not-an-id", "thetvdb": None, "tvrage": None},
            runtime=None,
            premiered="",
            summary=None,
            rating={"average": None},
        )
    )
    assert rec.web_channel == "Netflix"
    assert rec.network is None and rec.country_code is None
    assert rec.imdb_id is None  # formato inválido descartado
    assert rec.thetvdb_id is None
    assert rec.runtime_minutes is None and rec.premiered is None
    assert rec.summary is None and rec.rating_average is None


def test_normalize_show_requires_id_and_name() -> None:
    with pytest.raises(NormalizationError):
        normalize_show(tvmaze_show(3, name=""))
    with pytest.raises(NormalizationError):
        normalize_show(tvmaze_show(0))


def test_strip_html() -> None:
    assert strip_html("<p>a<br>b &lt;c&gt;</p>") == "a b <c>"
    assert strip_html("   ") is None
    assert strip_html(None) is None


def test_content_hash_ignores_nothing_but_id() -> None:
    a = normalize_show(tvmaze_show(4)).content_hash()
    assert a == normalize_show(tvmaze_show(4)).content_hash()
    assert a != normalize_show(tvmaze_show(4, weight=1)).content_hash()


@respx.mock
def test_index_page_and_end_of_list() -> None:
    route = respx.get(f"{BASE}/shows").mock(
        side_effect=lambda r: (
            httpx.Response(200, json=[tvmaze_show(1)])
            if r.url.params["page"] == "0"
            else httpx.Response(404)
        )
    )
    client = TvmazeClient.from_settings(TvmazeSettings(_env_file=None, requests_per_second=2))
    assert [s["id"] for s in client.index_page(0) or []] == [1]
    assert client.index_page(1) is None
    assert route.calls[0].request.headers["User-Agent"] == USER_AGENT


def test_index_page_rejects_negative() -> None:
    client = TvmazeClient.from_settings(TvmazeSettings(_env_file=None))
    with pytest.raises(ValueError):
        client.index_page(-1)


# ------------------------------------------------------------ crosswalk TMDB


def test_tmdb_tv_crosswalk() -> None:
    rec = normalize("tv", tv_payload(1, external_ids={"imdb_id": "tt0944947", "tvdb_id": 121361}))
    assert {(c.source, c.external_type, c.external_id) for c in rec.crosswalk} == {
        ("imdb", "title", "tt0944947"),
        ("tvdb", "series", "121361"),
    }


def test_tmdb_movie_crosswalk_has_no_tvdb() -> None:
    rec = normalize("movie", movie_payload(2, external_ids={"imdb_id": "tt0137523", "tvdb_id": 9}))
    assert [(c.source, c.external_id) for c in rec.crosswalk] == [("imdb", "tt0137523")]


def test_tmdb_crosswalk_ignores_invalid_ids_and_hash() -> None:
    rec = normalize("tv", tv_payload(3, external_ids={"imdb_id": "", "tvdb_id": 0}))
    assert rec.crosswalk == ()
    other = normalize("tv", tv_payload(3, external_ids={"imdb_id": "tt0000001"}))
    assert rec.content_hash() == other.content_hash()  # crosswalk fora do hash
