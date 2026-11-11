"""Normalização de shows TVmaze (CC BY-SA 4.0).

`rating_average` é agregado do TVmaze, não avaliação de usuário CineMatch.
`summary` vem em HTML: guardamos texto puro.
"""

import hashlib
import html
import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SOURCE = "tvmaze"
_IMDB_RE = re.compile(r"^tt\d{7,10}$")


class NormalizationError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "li"}:
            self.parts.append(" ")


def strip_html(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()
    return text or None


class TvmazeShowRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tvmaze_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    show_type: str | None
    language: str | None
    genres: tuple[str, ...]
    status: str | None
    runtime_minutes: int | None
    average_runtime_minutes: int | None
    premiered: date | None
    ended: date | None
    network: str | None
    web_channel: str | None
    country_code: str | None
    rating_average: float | None
    weight: int | None
    summary: str | None
    imdb_id: str | None
    thetvdb_id: str | None
    tvrage_id: str | None
    url: str | None
    source_updated_at: datetime | None

    def content_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"tvmaze_id"})
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()


def _str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def normalize_show(payload: dict[str, Any]) -> TvmazeShowRecord:
    externals = payload.get("externals") or {}
    network = payload.get("network") or {}
    web = payload.get("webChannel") or {}
    country = (network.get("country") or web.get("country") or {}) if (network or web) else {}
    imdb = _str(externals.get("imdb"))
    updated = _int(payload.get("updated"))
    try:
        return TvmazeShowRecord(
            tvmaze_id=payload.get("id"),
            name=_str(payload.get("name")) or "",
            show_type=_str(payload.get("type")),
            language=_str(payload.get("language")),
            genres=tuple(g for g in payload.get("genres") or [] if isinstance(g, str) and g),
            status=_str(payload.get("status")),
            runtime_minutes=_int(payload.get("runtime")),
            average_runtime_minutes=_int(payload.get("averageRuntime")),
            premiered=_date(payload.get("premiered")),
            ended=_date(payload.get("ended")),
            network=_str(network.get("name")),
            web_channel=_str(web.get("name")),
            country_code=_str(country.get("code")),
            rating_average=_float((payload.get("rating") or {}).get("average")),
            weight=_int(payload.get("weight")),
            summary=strip_html(payload.get("summary")),
            imdb_id=imdb if imdb and _IMDB_RE.match(imdb) else None,
            thetvdb_id=str(v) if (v := _int(externals.get("thetvdb"))) else None,
            tvrage_id=str(v) if (v := _int(externals.get("tvrage"))) else None,
            url=_str(payload.get("url")),
            source_updated_at=datetime.fromtimestamp(updated, tz=UTC) if updated else None,
        )
    except ValidationError as exc:
        msg = exc.errors()[0]["msg"]
        raise NormalizationError(f"tvmaze/{payload.get('id')}: {msg}") from exc
