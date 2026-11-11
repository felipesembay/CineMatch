"""Payloads sintéticos no formato da API TMDB (estrutura, não dados reais)."""

from typing import Any


def movie_payload(tmdb_id: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": tmdb_id,
        "title": f"Movie {tmdb_id}",
        "original_title": f"Original {tmdb_id}",
        "overview": f"Synopsis {tmdb_id}",
        "release_date": "2020-05-17",
        "original_language": "en",
        "runtime": 112,
        "status": "Released",
        "popularity": 12.5,
        "vote_average": 7.1,
        "vote_count": 1500,
        "genres": [{"id": 18, "name": "Drama"}, {"id": 35, "name": "Comedy"}],
        "keywords": {"keywords": [{"id": 1, "name": "friendship"}]},
        "credits": {"cast": [], "crew": []},
        "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
    }
    data.update(overrides)
    return data


def tv_payload(tmdb_id: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": tmdb_id,
        "name": f"Show {tmdb_id}",
        "original_name": f"Original Show {tmdb_id}",
        "overview": f"Show synopsis {tmdb_id}",
        "first_air_date": "2018-01-10",
        "original_language": "pt",
        "episode_run_time": [45],
        "status": "Returning Series",
        "popularity": 33.0,
        "vote_average": 8.0,
        "vote_count": 300,
        "genres": [{"id": 18, "name": "Drama"}, {"id": 10765, "name": "Sci-Fi & Fantasy"}],
    }
    data.update(overrides)
    return data


def discover_payload(ids: list[int], page: int, total_pages: int) -> dict[str, Any]:
    return {
        "page": page,
        "total_pages": total_pages,
        "total_results": total_pages * 20,
        "results": [{"id": i} for i in ids],
    }


def tvmaze_show(tvmaze_id: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": tvmaze_id,
        "url": f"https://www.tvmaze.com/shows/{tvmaze_id}/show-{tvmaze_id}",
        "name": f"TVmaze Show {tvmaze_id}",
        "type": "Scripted",
        "language": "English",
        "genres": ["Drama", "Thriller"],
        "status": "Ended",
        "runtime": 60,
        "averageRuntime": 58,
        "premiered": "2013-06-24",
        "ended": "2015-09-10",
        "officialSite": None,
        "rating": {"average": 6.5},
        "weight": 98,
        "network": {"id": 2, "name": "CBS", "country": {"code": "US"}},
        "webChannel": None,
        "externals": {"tvrage": 25988, "thetvdb": 264492, "imdb": "tt1553656"},
        "summary": "<p><b>Under the Dome</b> is the story &amp; more.</p><p>Second.</p>",
        "updated": 1704794122,
    }
    data.update(overrides)
    return data
