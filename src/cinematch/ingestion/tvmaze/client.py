"""Cliente da API pública do TVmaze (sem autenticação).

Índice de shows: `/shows?page=N`, até 250 shows por página, paginado por ID
(página 0 = IDs 0 a 249). Fim da lista = HTTP 404.
"""

from typing import Any

import httpx

from cinematch.config import TvmazeSettings
from cinematch.ingestion.http import NotFoundError, RateLimiter, RetryingClient, RetryPolicy

USER_AGENT = "CineMatch/0.1 (non-commercial research project)"


class TvmazeClient:
    def __init__(self, http: RetryingClient) -> None:
        self.http = http

    @classmethod
    def from_settings(cls, settings: TvmazeSettings) -> "TvmazeClient":
        client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        return cls(
            RetryingClient(
                client=client,
                rate_limiter=RateLimiter(settings.requests_per_second),
                policy=RetryPolicy(max_attempts=settings.max_attempts),
            )
        )

    def close(self) -> None:
        self.http.client.close()

    def index_page(self, page: int) -> list[dict[str, Any]] | None:
        """Shows da página; `None` quando a lista acabou (404)."""
        if page < 0:
            raise ValueError("page deve ser >= 0")
        try:
            payload = self.http.get_json("/shows", params={"page": page})
        except NotFoundError:
            return None
        if not isinstance(payload, list):
            raise ValueError(f"resposta inesperada do índice (página {page})")
        return payload
