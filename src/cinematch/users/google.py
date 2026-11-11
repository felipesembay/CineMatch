"""Login com Google (OAuth 2.0 Authorization Code + PKCE, OpenID Connect).

Fluxo:
1. `/auth/google/login` gera `state` (anti-CSRF, também em cookie), `nonce` (anti-replay do ID
   token) e `code_verifier` (PKCE); guarda os três no banco e redireciona ao Google.
2. `/auth/google/callback` confere `state` com o cookie, troca o `code` por tokens no endpoint
   do Google (com `client_secret` + `code_verifier`) e valida o ID token: assinatura RS256 com a
   chave pública do Google (JWKS), `iss`, `aud`, `exp`, `nonce` e `email_verified`.
3. Cria ou recupera o usuário pela identidade (`sub`) e abre a sessão normal do CineMatch.
"""

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - URL pública
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
JWKS_TTL_S = 3600


class GoogleAuthError(Exception):
    """Falha no fluxo OAuth (mensagem segura para log; nunca contém tokens)."""


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


@dataclass
class GoogleClaims:
    subject: str
    email: str
    name: str | None


class _JwksCache:
    def __init__(self) -> None:
        self.keys: dict[str, Any] = {}
        self.fetched_at = 0.0
        self.lock = threading.Lock()

    def get(self, http: httpx.Client, kid: str) -> Any:
        with self.lock:
            stale = time.monotonic() - self.fetched_at > JWKS_TTL_S
            if kid not in self.keys or stale:
                resp = http.get(JWKS_URL)
                resp.raise_for_status()
                self.keys = {k["kid"]: jwt.PyJWK(k).key for k in resp.json()["keys"]}
                self.fetched_at = time.monotonic()
            if kid not in self.keys:
                raise GoogleAuthError("chave de assinatura desconhecida")
            return self.keys[kid]


_jwks = _JwksCache()


class GoogleClient:
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self._secret = client_secret
        self.http = http or httpx.Client(timeout=10)

    def authorization_url(self, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> str:
        resp = self.http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        if resp.status_code != 200:
            raise GoogleAuthError(f"troca de código recusada (HTTP {resp.status_code})")
        token = resp.json().get("id_token")
        if not isinstance(token, str):
            raise GoogleAuthError("resposta sem id_token")
        return token

    def verify_id_token(self, id_token: str, nonce: str) -> GoogleClaims:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid", "")
            claims = jwt.decode(
                id_token,
                _jwks.get(self.http, kid),
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=ISSUERS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"id_token inválido: {type(exc).__name__}") from None
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise GoogleAuthError("nonce não confere")
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise GoogleAuthError("e-mail do Google não verificado")
        return GoogleClaims(
            subject=str(claims["sub"]), email=str(claims["email"]), name=claims.get("name")
        )
