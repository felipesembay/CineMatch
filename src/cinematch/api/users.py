"""Contas, sessão, preferências, favoritos, avaliações, eventos e recomendação personalizada."""

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine

from cinematch.api.catalog import ItemSummary, Recommendation, RecommendationList, _summary
from cinematch.config import Settings, get_settings
from cinematch.db import get_engine
from cinematch.logconfig import request_id_ctx
from cinematch.serving import catalog as cat
from cinematch.serving.neighbors import active_version
from cinematch.users import repository as repo
from cinematch.users.google import GoogleAuthError, GoogleClient, new_pkce
from cinematch.users.recommend import personalized
from cinematch.users.security import AttemptLimiter, hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()

EngineDep = Annotated[Engine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")
login_limiter = AttemptLimiter(max_attempts=10, window_s=900)


# --- modelos ----------------------------------------------------------------------------


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL.match(v):
            raise ValueError("e-mail inválido")
        return v


class Registration(Credentials):
    name: str | None = Field(default=None, max_length=80)


class User(BaseModel):
    id: int
    email: str
    name: str
    onboarded: bool


class Preferences(BaseModel):
    liked: list[int] = Field(default_factory=list, max_length=50)
    disliked: list[int] = Field(default_factory=list, max_length=50)
    genres: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=30)


class FavoriteIds(BaseModel):
    ids: list[int] = Field(max_length=500)


class RatingIn(BaseModel):
    item_id: int
    value: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


class RatingOut(BaseModel):
    item: ItemSummary
    value: int
    comment: str | None
    updated_at: datetime


class Ok(BaseModel):
    ok: bool = True


EventType = Literal["impression", "click", "favorite_add", "favorite_remove", "rating", "search"]
Surface = Literal["home_row", "similar", "feed", "search", "detail", "my_list"]


class EventIn(BaseModel):
    event_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    type: EventType
    surface: Surface
    item_id: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0, le=1000)
    request_id: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=200)
    query: str | None = Field(default=None, max_length=100)
    occurred_at: datetime


class EventBatch(BaseModel):
    events: list[EventIn] = Field(max_length=100)


class EventAck(BaseModel):
    ok: bool = True
    accepted: int
    duplicates: int


# --- sessão -----------------------------------------------------------------------------


def optional_user(
    request: Request, engine: EngineDep, settings: SettingsDep
) -> dict[str, Any] | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token or len(token) > 128:
        return None
    with engine.connect() as conn:
        return repo.user_for_token(conn, token)


def current_user(user: Annotated[dict | None, Depends(optional_user)]) -> dict[str, Any]:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "faça login para continuar")
    return user


UserDep = Annotated[dict[str, Any], Depends(current_user)]


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/auth/register", response_model=User, status_code=201, tags=["auth"])
def register(
    body: Registration, response: Response, engine: EngineDep, settings: SettingsDep
) -> dict[str, Any]:
    pw_hash = hash_password(body.password)
    try:
        with engine.begin() as conn:
            user = repo.create_user(conn, body.email, pw_hash, body.name)
            token = repo.create_session(conn, user["id"], settings.session_ttl_days)
    except repo.EmailTaken:
        raise HTTPException(status.HTTP_409_CONFLICT, "e-mail já cadastrado") from None
    _set_cookie(response, settings, token)
    logger.info("user_registered", extra={"user_id": user["id"]})
    return user


@router.post("/auth/login", response_model=User, tags=["auth"])
def login(
    body: Credentials,
    request: Request,
    response: Response,
    engine: EngineDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    key = f"{request.client.host if request.client else '-'}|{body.email.lower()}"
    if not login_limiter.allow(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "muitas tentativas; aguarde")
    with engine.begin() as conn:
        row = repo.user_for_login(conn, body.email)
        if not verify_password(row.password_hash if row else None, body.password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "e-mail ou senha inválidos")
        token = repo.create_session(conn, row.id, settings.session_ttl_days)
        user = repo._user_dict(row)
    login_limiter.reset(key)
    _set_cookie(response, settings, token)
    return user


@router.post("/auth/logout", response_model=Ok, tags=["auth"])
def logout(request: Request, response: Response, engine: EngineDep, settings: SettingsDep) -> Ok:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        with engine.begin() as conn:
            repo.delete_session(conn, token)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return Ok()


@router.get("/auth/me", response_model=User, tags=["auth"])
def me(user: UserDep) -> dict[str, Any]:
    return user


@router.delete("/users/me", response_model=Ok, tags=["users"])
def delete_me(user: UserDep, response: Response, engine: EngineDep, settings: SettingsDep) -> Ok:
    """Exclusão de conta (LGPD): remove conta e interações; eventos ficam sem vínculo."""
    with engine.begin() as conn:
        repo.delete_user(conn, user["id"])
    response.delete_cookie(settings.session_cookie_name, path="/")
    logger.info("user_deleted", extra={"user_id": user["id"]})
    return Ok()


# --- login com Google -------------------------------------------------------------------

OAUTH_COOKIE = "cm_oauth_state"


@lru_cache(maxsize=1)
def _google_client(client_id: str, client_secret: str) -> GoogleClient:
    return GoogleClient(client_id, client_secret)


def get_google_client(settings: SettingsDep) -> GoogleClient | None:
    if not (settings.google_client_id and settings.google_client_secret):
        return None
    return _google_client(
        settings.google_client_id, settings.google_client_secret.get_secret_value()
    )


GoogleDep = Annotated[GoogleClient | None, Depends(get_google_client)]


class Providers(BaseModel):
    password: bool = True
    google: bool


def _public_origin(request: Request, settings: Settings) -> str:
    """Origem que o navegador usa (o proxy do Vite/nginx preserva o Host). Só origens da
    lista permitida: o redirect_uri também precisa estar cadastrado no Google."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    origin = f"{proto}://{host}"
    if origin not in {o.rstrip("/") for o in settings.cors_origins}:
        raise HTTPException(400, "origem não permitida para login social")
    return origin


def _safe_next(path: str | None) -> str:
    """Só caminhos relativos da própria aplicação (evita open redirect)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path[:200]


@router.get("/auth/providers", response_model=Providers, tags=["auth"])
def providers(google: GoogleDep) -> Providers:
    return Providers(google=google is not None)


@router.get("/auth/google/login", status_code=302, tags=["auth"])
def google_login(
    request: Request,
    engine: EngineDep,
    settings: SettingsDep,
    google: GoogleDep,
    next: str | None = None,
) -> RedirectResponse:
    if google is None:
        raise HTTPException(404, "login com Google não configurado")
    origin = _public_origin(request, settings)
    redirect_uri = f"{origin}{settings.public_api_prefix}/auth/google/callback"
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = new_pkce()
    with engine.begin() as conn:
        repo.save_oauth_state(
            conn, "google", state, nonce, verifier, redirect_uri, _safe_next(next)
        )
    response = RedirectResponse(
        google.authorization_url(redirect_uri, state, nonce, challenge), status_code=302
    )
    response.set_cookie(
        OAUTH_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/google/callback", status_code=302, tags=["auth"])
def google_callback(
    request: Request,
    engine: EngineDep,
    settings: SettingsDep,
    google: GoogleDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if google is None:
        raise HTTPException(404, "login com Google não configurado")
    origin = _public_origin(request, settings)

    def fail(reason: str) -> RedirectResponse:
        logger.warning("google_login_failed", extra={"reason": reason})
        resp = RedirectResponse(f"{origin}/login?error=google", status_code=302)
        resp.delete_cookie(OAUTH_COOKIE, path="/")
        return resp

    cookie_state = request.cookies.get(OAUTH_COOKIE) or ""
    if error:
        return fail(f"google: {error[:40]}")
    if not code or not state or not secrets.compare_digest(state, cookie_state):
        return fail("state ausente ou diferente do cookie")
    with engine.begin() as conn:
        saved = repo.pop_oauth_state(conn, "google", state)
    if saved is None:
        return fail("state expirado ou já usado")
    try:
        id_token = google.exchange_code(code, saved.redirect_uri, saved.code_verifier)
        claims = google.verify_id_token(id_token, saved.nonce)
    except GoogleAuthError as exc:
        return fail(str(exc))
    except httpx.HTTPError as exc:
        return fail(f"rede: {type(exc).__name__}")
    with engine.begin() as conn:
        user, outcome = repo.user_for_identity(
            conn, "google", claims.subject, claims.email, claims.name
        )
        token = repo.create_session(conn, user["id"], settings.session_ttl_days)
    logger.info("google_login", extra={"user_id": user["id"], "outcome": outcome})
    dest = saved.next_path if user["onboarded"] else "/onboarding"
    response = RedirectResponse(f"{origin}{dest}", status_code=302)
    _set_cookie(response, settings, token)
    response.delete_cookie(OAUTH_COOKIE, path="/")
    return response


# --- preferências e favoritos -----------------------------------------------------------


def _known(conn: Any, ids: list[int]) -> list[int]:
    known = repo.existing_items(conn, ids)
    missing = [i for i in ids if i not in known]
    if missing:
        raise HTTPException(422, f"itens inexistentes: {missing}")
    return list(dict.fromkeys(ids))


@router.get("/users/me/preferences", response_model=Preferences, tags=["users"])
def get_prefs(user: UserDep, engine: EngineDep) -> dict[str, Any]:
    with engine.connect() as conn:
        return repo.get_preferences(conn, user["id"])


@router.put("/users/me/preferences", response_model=Preferences, tags=["users"])
def put_prefs(body: Preferences, user: UserDep, engine: EngineDep) -> dict[str, Any]:
    if set(body.liked) & set(body.disliked):
        raise HTTPException(422, "um item não pode estar em liked e disliked")
    with engine.begin() as conn:
        liked, disliked = _known(conn, body.liked), _known(conn, body.disliked)
        repo.replace_preferences(conn, user["id"], liked, disliked, body.genres)
        return repo.get_preferences(conn, user["id"])


def _items(conn: Any, ids: list[int], settings: Settings) -> list[dict[str, Any]]:
    rows = cat.summaries_by_ids(conn, ids)
    return [_summary(rows[i], settings) for i in ids if i in rows]


@router.get("/users/me/favorites", response_model=list[ItemSummary], tags=["users"])
def get_favorites(user: UserDep, engine: EngineDep, settings: SettingsDep) -> list[dict]:
    with engine.connect() as conn:
        return _items(conn, repo.favorite_ids(conn, user["id"]), settings)


@router.put("/users/me/favorites", response_model=list[ItemSummary], tags=["users"])
def put_favorites(
    body: FavoriteIds, user: UserDep, engine: EngineDep, settings: SettingsDep
) -> list[dict]:
    with engine.begin() as conn:
        repo.replace_favorites(conn, user["id"], _known(conn, body.ids))
        return _items(conn, repo.favorite_ids(conn, user["id"]), settings)


@router.put("/users/me/favorites/{item_id}", response_model=Ok, tags=["users"])
def add_favorite(item_id: int, user: UserDep, engine: EngineDep) -> Ok:
    with engine.begin() as conn:
        _known(conn, [item_id])
        repo.add_favorite(conn, user["id"], item_id)
    return Ok()


@router.delete("/users/me/favorites/{item_id}", response_model=Ok, tags=["users"])
def remove_favorite(item_id: int, user: UserDep, engine: EngineDep) -> Ok:
    with engine.begin() as conn:
        repo.remove_favorite(conn, user["id"], item_id)
    return Ok()


# --- avaliações --------------------------------------------------------------------------


@router.post("/ratings", response_model=Ok, tags=["ratings"])
def rate(body: RatingIn, user: UserDep, engine: EngineDep) -> Ok:
    with engine.begin() as conn:
        _known(conn, [body.item_id])
        repo.upsert_rating(conn, user["id"], body.item_id, body.value, body.comment)
    return Ok()


@router.delete("/ratings/{item_id}", response_model=Ok, tags=["ratings"])
def unrate(item_id: int, user: UserDep, engine: EngineDep) -> Ok:
    with engine.begin() as conn:
        if not repo.delete_rating(conn, user["id"], item_id):
            raise HTTPException(404, "avaliação não encontrada")
    return Ok()


@router.get("/users/me/ratings", response_model=list[RatingOut], tags=["ratings"])
def my_ratings(user: UserDep, engine: EngineDep, settings: SettingsDep) -> list[dict]:
    with engine.connect() as conn:
        ratings = repo.list_ratings(conn, user["id"])
        rows = cat.summaries_by_ids(conn, [r["item_id"] for r in ratings])
    return [
        {
            "item": _summary(rows[r["item_id"]], settings),
            "value": r["value"],
            "comment": r["comment"],
            "updated_at": r["updated_at"],
        }
        for r in ratings
        if r["item_id"] in rows
    ]


# --- eventos -----------------------------------------------------------------------------


@router.post("/events", response_model=EventAck, tags=["events"])
def post_events(
    body: EventBatch,
    engine: EngineDep,
    user: Annotated[dict | None, Depends(optional_user)],
) -> EventAck:
    """Eventos validados e idempotentes (`event_id`). Usuário vem da sessão, nunca do corpo."""
    limit = datetime.now(UTC) + timedelta(minutes=10)
    if any(e.occurred_at > limit for e in body.events):
        raise HTTPException(422, "occurred_at no futuro")
    batch = [e.model_dump() for e in body.events]
    with engine.begin() as conn:
        accepted = repo.insert_events(conn, user["id"] if user else None, batch)
    return EventAck(accepted=accepted, duplicates=len(batch) - accepted)


# --- recomendação personalizada --------------------------------------------------------


@router.get("/recommendations/me", response_model=RecommendationList, tags=["recommendations"])
def recommendations_me(
    user: UserDep,
    engine: EngineDep,
    settings: SettingsDep,
    k: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RecommendationList:
    with engine.connect() as conn:
        prefs = repo.get_preferences(conn, user["id"])
        result = personalized(
            conn,
            user["id"],
            active_version(conn),
            repo.seen_item_ids(conn, user["id"]),
            prefs["genres"],
            k,
        )
    logger.info(
        "recommendations_served",
        extra={
            "user_id": user["id"],
            "model_version": result["model_version"],
            "fallback": result["fallback"],
            "n": len(result["items"]),
        },
    )
    return RecommendationList(
        request_id=request_id_ctx.get() or "",
        model_version=result["model_version"],
        fallback=result["fallback"],
        items=[
            Recommendation(**_summary(row, settings), score=score, reason=reason)
            for row, score, reason in result["items"]
        ],
    )
