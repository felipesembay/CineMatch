"""Cliente HTTP genérico com rate limit, timeout e retries com backoff exponencial.

Política de retry:
- Retentável: 429, 500, 502, 503, 504, timeouts e erros de transporte.
- Não retentável: demais 4xx (ex.: 401 credencial inválida, 404 item inexistente).
- 429 respeita `Retry-After` (segundos) quando presente, limitado a `max_backoff_s`.
"""

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """Falha HTTP definitiva (após retries ou não retentável)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(HttpError):
    """Credencial ausente ou inválida (401/403). Interrompe a execução."""


class NotFoundError(HttpError):
    """Recurso inexistente (404)."""


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    jitter_s: float = 0.5

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        """Espera antes da tentativa `attempt + 1` (attempt começa em 1)."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_backoff_s)
        delay = self.base_backoff_s * (2 ** (attempt - 1))
        return min(delay + random.uniform(0, self.jitter_s), self.max_backoff_s)  # noqa: S311


@dataclass
class RequestStats:
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0


class RateLimiter:
    """Intervalo mínimo entre requisições (cliente síncrono, um processo)."""

    def __init__(
        self,
        requests_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second deve ser > 0")
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0

    def acquire(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval


@dataclass
class RetryingClient:
    client: httpx.Client
    rate_limiter: RateLimiter
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep
    stats: RequestStats = field(default_factory=RequestStats)

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        last_error: str = ""
        for attempt in range(1, self.policy.max_attempts + 1):
            self.rate_limiter.acquire()
            self.stats.requests += 1
            retry_after: float | None = None
            try:
                resp = self.client.get(path, params=params)
            except httpx.TransportError as exc:  # inclui timeouts
                last_error = type(exc).__name__
            else:
                status = resp.status_code
                if status < 400:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise HttpError(f"resposta não-JSON em {path}", status) from exc
                if status in (401, 403):
                    raise AuthError(f"autenticação recusada ({status})", status)
                if status == 404:
                    raise NotFoundError(f"não encontrado: {path}", status)
                if status not in RETRYABLE_STATUS:
                    raise HttpError(f"HTTP {status} em {path}", status)
                if status == 429:
                    self.stats.rate_limited += 1
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                last_error = f"HTTP {status}"

            if attempt == self.policy.max_attempts:
                break
            delay = self.policy.backoff(attempt, retry_after)
            self.stats.retries += 1
            logger.warning(
                "http_retry",
                extra={"path": path, "attempt": attempt, "error": last_error, "delay_s": delay},
            )
            self.sleep(delay)

        raise HttpError(f"esgotadas {self.policy.max_attempts} tentativas em {path}: {last_error}")


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # formato HTTP-date: cai no backoff exponencial
