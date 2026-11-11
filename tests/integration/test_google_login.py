"""Login com Google: state/PKCE/nonce, validação do ID token e vínculo de contas."""

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from cinematch.api import users as users_api
from cinematch.api.main import create_app
from cinematch.config import Settings
from cinematch.db import get_engine
from cinematch.users import google as g

CLIENT_ID = "test-client.apps.googleusercontent.com"
ORIGIN = "http://localhost:5173"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key())), "kid": "k1"}


def _id_token(expected_nonce: str, **over: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-123",
        "email": "ana@gmail.com",
        "email_verified": True,
        "name": "Ana G",
        "iat": now,
        "exp": now + 600,
        "nonce": expected_nonce,
        **over,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    g._jwks.keys, g._jwks.fetched_at = {}, 0.0
    settings = Settings(
        log_json=False,
        google_client_id=CLIENT_ID,
        google_client_secret="s3cret",
        cors_origins=[ORIGIN],
    )
    app = create_app(settings)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[users_api.get_settings] = lambda: settings
    app.dependency_overrides[users_api.get_google_client] = lambda: g.GoogleClient(
        CLIENT_ID, "s3cret", httpx.Client()
    )
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        yield c


def _start(c: TestClient, next_path: str = "/my-list") -> dict[str, str]:
    r = c.get("/auth/google/login", params={"next": next_path})
    assert r.status_code == 302
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == f"{ORIGIN}/api/auth/google/callback"
    assert c.cookies.get("cm_oauth_state") == q["state"]
    return q


def _mock_google(router: respx.Router, id_token: str, status: int = 200) -> respx.Route:
    router.get(g.JWKS_URL).respond(json={"keys": [_JWK]})
    return router.post(g.TOKEN_URL).respond(status, json={"id_token": id_token})


def test_full_flow_creates_user_and_session(client: TestClient, engine: Engine) -> None:
    q = _start(client)
    with respx.mock(assert_all_called=True) as router:
        token_route = _mock_google(router, _id_token(q["nonce"]))
        r = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
    assert r.status_code == 302 and r.headers["location"] == f"{ORIGIN}/onboarding"
    sent = parse_qs(token_route.calls.last.request.content.decode())
    assert sent["code_verifier"][0] and sent["client_secret"] == ["s3cret"]
    me = client.get("/auth/me").json()
    assert me["email"] == "ana@gmail.com" and me["name"] == "Ana G"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT password_hash FROM users")).scalar() is None
        assert conn.execute(text("SELECT count(*) FROM oauth_states")).scalar() == 0
    # mesmo `sub` de novo: mesma conta; já com onboarding, volta para `next`
    client.put("/users/me/preferences", json={"liked": [], "disliked": [], "genres": []})
    q2 = _start(client)
    with respx.mock(assert_all_called=False) as router:
        _mock_google(router, _id_token(q2["nonce"]))
        r2 = client.get("/auth/google/callback", params={"code": "x", "state": q2["state"]})
    assert r2.headers["location"] == f"{ORIGIN}/my-list"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "outro-app"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 3600},
        {"email_verified": False},
        {"nonce": "outro-nonce"},
    ],
)
def test_invalid_id_token_rejected(client: TestClient, engine: Engine, over: dict) -> None:
    q = _start(client)
    with respx.mock(assert_all_called=False) as router:
        _mock_google(router, _id_token(q["nonce"], **over))
        r = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
    assert r.headers["location"] == f"{ORIGIN}/login?error=google"
    assert client.get("/auth/me").status_code == 401
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 0


def test_state_must_match_cookie_and_is_single_use(client: TestClient) -> None:
    q = _start(client)
    r = client.get("/auth/google/callback", params={"code": "abc", "state": "forjado"})
    assert r.headers["location"].endswith("/login?error=google")
    client.cookies.set("cm_oauth_state", q["state"])
    with respx.mock(assert_all_called=False) as router:
        _mock_google(router, _id_token(q["nonce"]))
        ok = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
        assert ok.headers["location"].endswith("/onboarding")
        client.cookies.set("cm_oauth_state", q["state"])
        replay = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
    assert replay.headers["location"].endswith("/login?error=google")


def test_google_error_and_token_endpoint_failure(client: TestClient) -> None:
    q = _start(client)
    denied = client.get(
        "/auth/google/callback", params={"error": "access_denied", "state": q["state"]}
    )
    assert denied.headers["location"].endswith("/login?error=google")
    q = _start(client)
    with respx.mock(assert_all_called=False) as router:
        _mock_google(router, "", status=400)
        r = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
    assert r.headers["location"].endswith("/login?error=google")


def test_links_existing_password_account_and_revokes_password(
    client: TestClient, engine: Engine
) -> None:
    client.post("/auth/register", json={"email": "Ana@Gmail.com", "password": "senha-123456"})
    client.put("/users/me/preferences", json={"liked": [], "disliked": [], "genres": []})
    old_session = client.cookies.get("cm_session")
    q = _start(client)
    with respx.mock(assert_all_called=False) as router:
        _mock_google(router, _id_token(q["nonce"]))
        r = client.get("/auth/google/callback", params={"code": "abc", "state": q["state"]})
    assert r.headers["location"] == f"{ORIGIN}/my-list"  # conta existente (já onboarded)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT count(*), max(password_hash) FROM users")).one()
        sessions = conn.execute(text("SELECT count(*) FROM user_sessions")).scalar()
    assert tuple(row) == (1, None) and sessions == 1  # senha anulada, sessão antiga revogada
    client.cookies.clear()
    client.cookies.set("cm_session", old_session)
    assert client.get("/auth/me").status_code == 401
    pw = client.post("/auth/login", json={"email": "ana@gmail.com", "password": "senha-123456"})
    assert pw.status_code == 401


def test_open_redirect_and_origin_guards(client: TestClient) -> None:
    for bad in ["https://evil.example", "//evil.example", "/\\evil.example"]:
        q = _start(client, next_path=bad)
        with respx.mock(assert_all_called=False) as router:
            _mock_google(router, _id_token(q["nonce"]))
            client.put("/users/me/preferences", json={"liked": [], "disliked": [], "genres": []})
            r = client.get("/auth/google/callback", params={"code": "a", "state": q["state"]})
        assert r.headers["location"] in (f"{ORIGIN}/", f"{ORIGIN}/onboarding")
    r = client.get("/auth/google/login", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_providers_and_disabled(client: TestClient, engine: Engine) -> None:
    assert client.get("/auth/providers").json() == {"password": True, "google": True}
    app = create_app(Settings(log_json=False))
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[users_api.get_google_client] = lambda: None
    with TestClient(app) as c:
        assert c.get("/auth/providers").json() == {"password": True, "google": False}
        assert c.get("/auth/google/login", follow_redirects=False).status_code == 404
