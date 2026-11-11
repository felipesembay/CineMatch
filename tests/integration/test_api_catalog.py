"""Contrato da API de catálogo/similaridade, determinismo, diversidade e fallback."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from cinematch.api.main import create_app
from cinematch.config import Settings
from cinematch.db import get_engine
from cinematch.ingestion import repository as repo
from cinematch.ingestion.tmdb.normalize import normalize
from cinematch.serving.neighbors import activate_set, list_sets, write_neighbor_set

TODAY = date.today()


def _movie(tmdb_id: int, title: str, **kw: Any) -> dict[str, Any]:
    payload = {
        "id": tmdb_id,
        "title": title,
        "original_title": title,
        "overview": kw.get("overview", f"Sinopse de {title}."),
        "release_date": kw.get("release_date", "2010-05-01"),
        "original_language": "en",
        "runtime": 100,
        "status": "Released",
        "popularity": kw.get("popularity", 10.0),
        "vote_average": kw.get("vote_average", 7.0),
        "vote_count": kw.get("vote_count", 100),
        "poster_path": kw.get("poster_path", f"/p{tmdb_id}.jpg"),
        "backdrop_path": f"/b{tmdb_id}.jpg",
        "genres": [{"id": g, "name": n} for g, n in kw.get("genres", [(28, "Action")])],
        "belongs_to_collection": kw.get("collection"),
        "external_ids": {"imdb_id": f"tt{tmdb_id:07d}"},
        "keywords": {"keywords": []},
        "credits": {
            "cast": [
                {
                    "id": 900 + i,
                    "name": n,
                    "order": i,
                    "character": "x",
                    "credit_id": f"c{tmdb_id}-{i}",
                }
                for i, n in enumerate(kw.get("cast", []))
            ],
            "crew": [
                {
                    "id": 800,
                    "name": d,
                    "job": "Director",
                    "department": "Directing",
                    "credit_id": f"d{tmdb_id}",
                }
                for d in kw.get("directors", [])
            ],
        },
    }
    return payload


CATALOG = [
    _movie(
        1,
        "Alpha Heist",
        popularity=50,
        vote_average=8.0,
        vote_count=5000,
        collection={"id": 77, "name": "Alpha Collection"},
        directors=["Ann Lee"],
    ),
    _movie(
        2,
        "Alpha Heist 2",
        popularity=40,
        collection={"id": 77, "name": "Alpha Collection"},
        directors=["Ann Lee"],
    ),
    _movie(3, "Alpha Heist 3", popularity=30, collection={"id": 77, "name": "Alpha Collection"}),
    _movie(4, "Alpha Heist 4", popularity=20, collection={"id": 77, "name": "Alpha Collection"}),
    _movie(
        5,
        "Beta Drama",
        popularity=5,
        genres=[(18, "Drama")],
        cast=["Bo Kim"],
        vote_average=9.9,
        vote_count=3,
    ),
    _movie(6, "Gamma Action", popularity=60, cast=["Bo Kim"], poster_path="https://evil/x.jpg"),
    _movie(7, "Future Film", release_date=(TODAY + timedelta(days=30)).isoformat(), popularity=99),
    _movie(8, "Lonely 100% _Odd_", genres=[(18, "Drama")], popularity=1),
]


@pytest.fixture
def ids(engine: Engine) -> dict[str, int]:
    prov = repo.Provenance(run_id=None, raw_uri="file:///test", synced_at=datetime.now(UTC))
    out = {}
    with engine.begin() as conn:
        for payload in CATALOG:
            rec = normalize("movie", payload)
            repo.upsert_item(conn, rec, prov)
    with engine.connect() as conn:
        from sqlalchemy import text

        out = dict(conn.execute(text("SELECT title, id FROM catalog_items")).all())
    return out


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app(Settings(log_json=False))
    app.dependency_overrides[get_engine] = lambda: engine
    with TestClient(app) as c:
        yield c


def _store(
    engine: Engine,
    ids: dict[str, int],
    version: str,
    order: dict[str, list[str]],
    activate: bool = True,
) -> dict[str, Any]:
    item_ids = sorted(ids.values())
    pos = {i: n for n, i in enumerate(item_ids)}
    k = max(len(v) for v in order.values())
    nb = np.full((len(item_ids), k), -1, dtype=np.int64)
    sc = np.zeros((len(item_ids), k))
    for title, neigh in order.items():
        row = pos[ids[title]]
        for j, t in enumerate(neigh):
            nb[row, j] = pos[ids[t]]
            sc[row, j] = 1.0 - j * 0.1
    return write_neighbor_set(
        engine,
        model_version=version,
        method="test",
        item_ids=item_ids,
        neighbors=nb,
        scores=sc,
        activate=activate,
    )


def test_list_contract_filters_and_unreleased(client: TestClient, ids: dict[str, int]) -> None:
    r = client.get("/movies", params={"page_size": 50})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "page", "page_size", "total"}
    titles = [i["title"] for i in body["items"]]
    assert "Future Film" not in titles and body["total"] == 7
    assert titles[0] == "Gamma Action"  # sort=popular
    item = body["items"][0]
    assert set(item) == {
        "id",
        "type",
        "title",
        "year",
        "poster_url",
        "backdrop_url",
        "genres",
        "vote_average",
        "vote_count",
    }
    assert item["poster_url"] is None  # caminho não-TMDB descartado na normalização
    alpha = next(i for i in body["items"] if i["title"] == "Alpha Heist")
    assert alpha["poster_url"].endswith("/w342/p1.jpg")

    with_future = client.get("/movies", params={"include_unreleased": True}).json()
    assert with_future["total"] == 8
    drama = client.get("/movies", params={"genre": "drama"}).json()
    assert {i["title"] for i in drama["items"]} == {"Beta Drama", "Lonely 100% _Odd_"}


def test_search_relevance_and_like_escaping(client: TestClient, ids: dict[str, int]) -> None:
    r = client.get("/movies", params={"q": "alpha heist"}).json()
    assert r["items"][0]["title"] == "Alpha Heist"  # exato primeiro
    assert client.get("/movies", params={"q": "100%"}).json()["total"] == 1
    assert client.get("/movies", params={"q": "_"}).json()["total"] == 1  # '_' literal


def test_top_rated_uses_bayesian_average(client: TestClient, ids: dict[str, int]) -> None:
    top = client.get("/movies", params={"sort": "top_rated"}).json()["items"]
    # 9.9 com 3 votos não vence 8.0 com 5000 votos
    assert top[0]["title"] == "Alpha Heist"
    assert [i["title"] for i in top].index("Beta Drama") > 0


def test_pagination_and_validation(client: TestClient, ids: dict[str, int]) -> None:
    p2 = client.get("/movies", params={"page": 2, "page_size": 3}).json()
    assert p2["page"] == 2 and len(p2["items"]) == 3 and p2["total"] == 7
    assert client.get("/movies", params={"page": 99}).json() == {
        "items": [],
        "page": 99,
        "page_size": 20,
        "total": 7,
    }
    assert client.get("/movies", params={"page_size": 51}).status_code == 422
    assert client.get("/movies", params={"type": "anime"}).status_code == 422


def test_detail_contract(client: TestClient, ids: dict[str, int]) -> None:
    d = client.get(f"/movies/{ids['Alpha Heist']}").json()
    assert d["directors"] == ["Ann Lee"]
    assert d["external"]["imdb_url"] == "https://www.imdb.com/title/tt0000001/"
    assert d["external"]["tmdb_url"] == "https://www.themoviedb.org/movie/1"
    assert d["release_date"] == "2010-05-01" and d["runtime_minutes"] == 100
    assert client.get("/movies/999999").status_code == 404


def test_similar_diversity_reasons_and_determinism(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    order = {
        "Alpha Heist": [
            "Alpha Heist 2",
            "Alpha Heist 3",
            "Alpha Heist 4",
            "Gamma Action",
            "Beta Drama",
        ]
    }
    _store(engine, ids, "v-test-1", order)
    url = f"/movies/{ids['Alpha Heist']}/similar"
    r = client.get(url, params={"max_per_collection": 2, "k": 4}, headers={"X-Request-ID": "abc"})
    body = r.json()
    assert r.status_code == 200 and body["request_id"] == "abc"
    assert body["model_version"] == "v-test-1" and body["fallback"] is False
    titles = [i["title"] for i in body["items"]]
    assert titles == ["Alpha Heist 2", "Alpha Heist 3", "Gamma Action", "Beta Drama"]
    assert body["items"][0]["reason"] == "Da coleção Alpha Collection"
    assert body["items"][2]["reason"] == "Também Action"
    assert body["items"][3]["reason"] == "Tema parecido"
    assert (
        client.get(url, params={"max_per_collection": 2, "k": 4}).json()["items"] == (body["items"])
    )


def test_similar_fallback_when_no_neighbors(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    body = client.get(f"/movies/{ids['Beta Drama']}/similar").json()  # sem conjunto ativo
    assert body["fallback"] is True and body["model_version"].startswith("fallback:bayes")
    titles = [i["title"] for i in body["items"]]
    assert titles == ["Lonely 100% _Odd_"]  # mesmo gênero, lançado, sem o próprio item
    assert body["items"][0]["reason"] == "Popular em Drama"
    _store(engine, ids, "v-test-2", {"Alpha Heist": ["Alpha Heist 2"]})
    assert client.get(f"/movies/{ids['Beta Drama']}/similar").json()["fallback"] is True
    assert client.get("/movies/999999/similar").status_code == 404


def test_neighbor_sets_idempotent_and_activation_swap(
    engine: Engine, ids: dict[str, int], client: TestClient
) -> None:
    order = {"Alpha Heist": ["Gamma Action"]}
    first = _store(engine, ids, "v-a", order)
    assert first["rows"] == 1 and not first["reused"]
    assert _store(engine, ids, "v-a", order)["reused"] is True
    _store(engine, ids, "v-b", {"Alpha Heist": ["Beta Drama"]}, activate=False)
    url = f"/movies/{ids['Alpha Heist']}/similar"
    assert client.get(url).json()["model_version"] == "v-a"
    activate_set(engine, "v-b")
    assert client.get(url).json()["items"][0]["title"] == "Beta Drama"
    assert [s["model_version"] for s in list_sets(engine) if s["is_active"]] == ["v-b"]
    with pytest.raises(LookupError):
        activate_set(engine, "nope")


def test_genres_and_cors(client: TestClient, ids: dict[str, int]) -> None:
    genres = client.get("/genres").json()
    assert [(g["name"], g["items"]) for g in genres] == [("Action", 6), ("Drama", 2)]
    r = client.options(
        "/movies",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.options(
        "/movies",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers
