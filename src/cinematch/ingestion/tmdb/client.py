"""Cliente da API TMDB v3 (autenticação Bearer — ADR D-101)."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from cinematch.config import TmdbSettings
from cinematch.ingestion.http import AuthError, RateLimiter, RetryingClient, RetryPolicy

ItemType = Literal["movie", "tv"]
ITEM_TYPES: tuple[ItemType, ...] = ("movie", "tv")

# TMDB limita /discover a 500 páginas.
MAX_DISCOVER_PAGES = 500
DETAIL_APPEND = "keywords,credits,external_ids"


@dataclass(frozen=True)
class DiscoverPage:
    item_type: ItemType
    page: int
    total_pages: int
    ids: list[int]
    payload: dict[str, Any]
    params: dict[str, Any]


class TmdbClient:
    def __init__(self, http: RetryingClient, language: str = "en-US") -> None:
        self.http = http
        self.language = language

    @classmethod
    def from_settings(cls, settings: TmdbSettings) -> "TmdbClient":
        if settings.api_token is None or not settings.api_token.get_secret_value():
            raise AuthError("TMDB_API_TOKEN não configurado (defina no .env)")
        client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
            headers={
                "Authorization": f"Bearer {settings.api_token.get_secret_value()}",
                "Accept": "application/json",
                "User-Agent": "CineMatch/0.1 (non-commercial research project)",
            },
        )
        http = RetryingClient(
            client=client,
            rate_limiter=RateLimiter(settings.requests_per_second),
            policy=RetryPolicy(max_attempts=settings.max_attempts),
        )
        return cls(http, settings.language)

    def close(self) -> None:
        self.http.client.close()

    def discover_params(self, page: int) -> dict[str, Any]:
        return {
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "language": self.language,
            "page": page,
        }

    def discover_page(self, item_type: ItemType, page: int) -> DiscoverPage:
        params = self.discover_params(page)
        payload = self.http.get_json(f"/discover/{item_type}", params=params)
        ids = [int(r["id"]) for r in payload.get("results", []) if "id" in r]
        total = min(int(payload.get("total_pages") or 0), MAX_DISCOVER_PAGES)
        return DiscoverPage(item_type, page, total, ids, payload, params)

    def iter_discover(self, item_type: ItemType, start_page: int = 1) -> Iterator[DiscoverPage]:
        page = start_page
        while True:
            result = self.discover_page(item_type, page)
            yield result
            if page >= result.total_pages or not result.ids:
                return
            page += 1

    def details_params(self) -> dict[str, Any]:
        return {"language": self.language, "append_to_response": DETAIL_APPEND}

    def details(self, item_type: ItemType, tmdb_id: int) -> dict[str, Any]:
        return self.http.get_json(f"/{item_type}/{tmdb_id}", params=self.details_params())
