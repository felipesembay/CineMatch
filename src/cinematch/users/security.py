"""Senhas (Argon2id), tokens de sessão e limitador simples de tentativas de login."""

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # parâmetros padrão do argon2-cffi (RFC 9106, perfil de baixa memória)
# Verificação contra um hash fixo quando o e-mail não existe: tempo de resposta parecido
# para usuário existente e inexistente (dificulta enumeração por tempo).
_DUMMY_HASH = _hasher.hash("cinematch-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AttemptLimiter:
    """Janela deslizante em memória (por processo). Suficiente para 1 réplica da API; com
    várias réplicas, mover para Redis/Postgres."""

    def __init__(self, max_attempts: int = 10, window_s: float = 900.0) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
