"""Fase 4: contas, sessão, preferências, favoritos, avaliações, eventos e /recommendations/me."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from cinematch.api import users as users_api
from cinematch.api.main import create_app
from cinematch.catalog.retention import TOMBSTONE_TITLE, expire_tmdb_items
from cinematch.config import Settings
from cinematch.db import get_engine
from cinematch.serving.neighbors import write_neighbor_set
from cinematch.users.security import AttemptLimiter
from tests.integration.test_api_catalog import _store
from tests.integration.test_api_catalog import ids as ids

PASSWORD = "senha-segura-123"


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    users_api.login_limiter = AttemptLimiter(max_attempts=3, window_s=900)
    app = create_app(Settings(log_json=False))
    app.dependency_overrides[get_engine] = lambda: engine
    with TestClient(app) as c:
        yield c


def _register(c: TestClient, email: str = "ana@example.com") -> dict[str, Any]:
    r = c.post("/auth/register", json={"email": email, "password": PASSWORD, "name": "Ana"})
    assert r.status_code == 201, r.text
    return r.json()


def test_register_login_logout_and_session_cookie(client: TestClient, engine: Engine) -> None:
    user = _register(client)
    assert user == {"id": user["id"], "email": "ana@example.com", "name": "Ana", "onboarded": False}
    cookie = client.cookies.get("cm_session")
    assert cookie and len(cookie) > 30
    with engine.connect() as conn:  # banco guarda só o hash do token e da senha
        stored = conn.execute(text("SELECT token_hash FROM user_sessions")).scalar()
        pw = conn.execute(text("SELECT password_hash FROM users")).scalar()
    assert stored != cookie and len(stored) == 64 and pw.startswith("$argon2id$")
    assert client.get("/auth/me").json()["email"] == "ana@example.com"

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401
    assert (
        client.post(
            "/auth/register", json={"email": "ANA@example.com", "password": PASSWORD}
        ).status_code
        == 409
    )
    r = client.post("/auth/login", json={"email": "Ana@Example.com", "password": PASSWORD})
    assert r.status_code == 200 and client.get("/auth/me").status_code == 200


def test_login_errors_and_rate_limit(client: TestClient) -> None:
    _register(client)
    client.cookies.clear()
    bad = {"email": "ana@example.com", "password": "errada-errada"}
    assert client.post("/auth/login", json=bad).status_code == 401
    missing = client.post("/auth/login", json={"email": "x@example.com", "password": PASSWORD})
    assert (
        missing.status_code == 401
        and missing.json()
        == client.post("/auth/login", json={"email": "x@example.com", "password": PASSWORD}).json()
    )
    for _ in range(2):
        client.post("/auth/login", json=bad)
    assert client.post("/auth/login", json=bad).status_code == 429
    assert (
        client.post("/auth/register", json={"email": "invalido", "password": PASSWORD}).status_code
        == 422
    )
    assert (
        client.post(
            "/auth/register", json={"email": "b@example.com", "password": "curta"}
        ).status_code
        == 422
    )


def test_protected_routes_require_session(client: TestClient) -> None:
    for method, path in [
        ("GET", "/users/me/favorites"),
        ("GET", "/recommendations/me"),
        ("POST", "/ratings"),
        ("GET", "/users/me/preferences"),
    ]:
        assert client.request(method, path, json={}).status_code in (401, 422)
    assert client.get("/users/me/favorites").status_code == 401


def test_cross_origin_writes_blocked(client: TestClient) -> None:
    r = client.post(
        "/auth/register",
        json={"email": "c@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    ok = client.post(
        "/auth/register",
        json={"email": "c@example.com", "password": PASSWORD},
        headers={"Origin": "http://localhost:5173"},
    )
    assert ok.status_code == 201


def test_preferences_favorites_ratings(client: TestClient, ids: dict[str, int]) -> None:
    _register(client)
    a, b, g = ids["Alpha Heist"], ids["Beta Drama"], ids["Gamma Action"]
    prefs = {"liked": [a], "disliked": [b], "genres": ["Action"]}
    assert client.put("/users/me/preferences", json=prefs).json() == prefs
    assert client.get("/auth/me").json()["onboarded"] is True
    bad = client.put("/users/me/preferences", json={"liked": [a], "disliked": [a]})
    assert bad.status_code == 422
    assert client.put("/users/me/preferences", json={"liked": [999999]}).status_code == 422

    assert client.put(f"/users/me/favorites/{g}").json() == {"ok": True}
    assert client.put(f"/users/me/favorites/{g}").status_code == 200  # idempotente
    favs = client.get("/users/me/favorites").json()
    assert [f["title"] for f in favs] == ["Gamma Action"] and favs[0]["poster_url"] is None
    client.delete(f"/users/me/favorites/{g}")
    assert client.get("/users/me/favorites").json() == []

    r = client.post("/ratings", json={"item_id": a, "value": 5, "comment": "  Ótimo!  "})
    assert r.json() == {"ok": True}
    client.post("/ratings", json={"item_id": a, "value": 4, "comment": "Bom"})  # upsert
    ratings = client.get("/users/me/ratings").json()
    assert len(ratings) == 1 and ratings[0]["value"] == 4 and ratings[0]["comment"] == "Bom"
    assert ratings[0]["item"]["title"] == "Alpha Heist"
    assert client.post("/ratings", json={"item_id": a, "value": 6}).status_code == 422
    assert (
        client.post("/ratings", json={"item_id": a, "value": 3, "comment": "x" * 1001}).status_code
        == 422
    )
    assert client.delete(f"/ratings/{a}").status_code == 200
    assert client.delete(f"/ratings/{a}").status_code == 404


def _event(**kw: Any) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "type": "impression",
        "surface": "feed",
        "item_id": 1,
        "position": 0,
        "request_id": "r1",
        "model_version": "v1",
        "occurred_at": datetime.now(UTC).isoformat(),
        **kw,
    }


def test_events_idempotent_and_validated(client: TestClient, engine: Engine) -> None:
    e1, e2 = _event(), _event(type="click")
    anon = client.post("/events", json={"events": [e1, e2]}).json()
    assert anon == {"ok": True, "accepted": 2, "duplicates": 0}
    user = _register(client)
    again = client.post("/events", json={"events": [e1, _event()]}).json()
    assert again == {"ok": True, "accepted": 1, "duplicates": 1}
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT user_id FROM events ORDER BY received_at")).all()
    assert [r.user_id for r in rows] == [None, None, user["id"]]
    future = _event(occurred_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    assert client.post("/events", json={"events": [future]}).status_code == 422
    assert client.post("/events", json={"events": [_event(type="hack")]}).status_code == 422
    too_many = {"events": [_event() for _ in range(101)]}
    assert client.post("/events", json=too_many).status_code == 422


def test_recommendations_profile_and_fallback(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    _register(client)
    cold = client.get("/recommendations/me").json()
    assert cold["fallback"] is True and cold["items"]  # sem sementes: bayes

    order = {
        "Alpha Heist": ["Alpha Heist 2", "Alpha Heist 3", "Alpha Heist 4", "Gamma Action"],
        "Beta Drama": ["Lonely 100% _Odd_", "Gamma Action"],
        "Gamma Action": ["Alpha Heist", "Beta Drama"],
    }
    _store(engine, ids, "v-prof", order)
    client.put(
        "/users/me/preferences",
        json={"liked": [ids["Alpha Heist"]], "disliked": [ids["Beta Drama"]], "genres": []},
    )
    rec = client.get("/recommendations/me", params={"k": 5}).json()
    assert rec["fallback"] is False and rec["model_version"] == "profile_rocchio_v1|v-prof"
    titles = [i["title"] for i in rec["items"]]
    # vistos excluídos; no máx. 2 por coleção; Gamma: 0.7 (Alpha) - 0.5*0.9 (Beta) > 0
    assert titles == ["Alpha Heist 2", "Alpha Heist 3", "Gamma Action"]
    assert rec["items"][0]["reason"] == "Porque você curtiu Alpha Heist"
    assert rec["items"][0]["score"] == 1.0
    r1, r2 = (client.get("/recommendations/me", params={"k": 5}).json() for _ in range(2))
    assert {**r1, "request_id": ""} == {**r2, "request_id": ""}  # determinístico


def test_delete_account_cascades(client: TestClient, engine: Engine, ids: dict[str, int]) -> None:
    _register(client)
    client.put(f"/users/me/favorites/{ids['Alpha Heist']}")
    client.post("/events", json={"events": [_event()]})
    assert client.delete("/users/me").json() == {"ok": True}
    assert client.get("/auth/me").status_code == 401
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM user_favorites), "
                "(SELECT count(*) FROM user_sessions), (SELECT count(*) FROM events), "
                "(SELECT count(user_id) FROM events)"
            )
        ).one()
    assert tuple(counts) == (0, 0, 0, 1, 0)


def test_expire_tombstones_items_with_user_data(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    _register(client)
    fav, plain = ids["Alpha Heist"], ids["Beta Drama"]
    client.put(f"/users/me/favorites/{fav}")
    write_neighbor_set(
        engine,
        model_version="v-t",
        method="t",
        item_ids=[fav, plain],
        neighbors=np.array([[1], [0]]),
        scores=np.ones((2, 1)),
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE item_external_ids SET last_synced_at = now() - interval "
                "'200 days' WHERE item_id = ANY(:i)"
            ),
            {"i": [fav, plain]},
        )
    res = expire_tmdb_items(engine, apply=True)
    assert (res.tombstoned_items, res.deleted_items) == (1, 1)
    favs = client.get("/users/me/favorites").json()
    assert [f["title"] for f in favs] == [TOMBSTONE_TITLE] and favs[0]["genres"] == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM item_neighbors")).scalar() == 0
    again = expire_tmdb_items(engine, apply=True)
    assert again.tombstoned_items == 0  # idempotente
