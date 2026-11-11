"""Normalização de payloads de detalhe TMDB para o modelo interno do catálogo.

Notas:
- `vote_average`/`vote_count` são agregados externos do TMDB, não avaliações de usuários CineMatch.
- Strings vazias viram `None`; datas inválidas/parciais viram `None`.
"""

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cinematch.ingestion.tmdb.client import ItemType

SOURCE = "tmdb"


class NormalizationError(ValueError):
    pass


class GenreRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    external_id: str
    name: str


# Minimização: elenco principal + equipe-chave (features de conteúdo), sem gênero/foto.
MAX_CAST = 15
KEY_CREW_JOBS = frozenset(
    {
        "Director",
        "Screenplay",
        "Writer",
        "Story",
        "Novel",
        "Original Music Composer",
        "Director of Photography",
    }
)


class KeywordRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    external_id: str
    name: str


class CreditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    credit_id: str
    person_external_id: str
    person_name: str
    known_for_department: str | None
    credit_type: Literal["cast", "crew", "creator"]
    character: str | None = None
    department: str | None = None
    job: str | None = None
    ordering: int | None = None


class CrosswalkId(BaseModel):
    """ID em outra fonte, declarado pelo próprio TMDB (`external_ids`)."""

    model_config = ConfigDict(frozen=True)
    source: Literal["imdb", "tvdb"]
    external_type: str
    external_id: str


class CatalogRecord(BaseModel):
    """Item de catálogo pronto para upsert."""

    model_config = ConfigDict(frozen=True)

    source: str = SOURCE
    item_type: ItemType
    external_id: str
    title: str = Field(min_length=1)
    original_title: str | None
    overview: str | None
    release_date: date | None
    release_year: int | None
    original_language: str | None
    runtime_minutes: int | None
    status: str | None
    poster_path: str | None = None
    backdrop_path: str | None = None
    collection_id: int | None = None
    collection_name: str | None = None
    popularity: float | None
    vote_average: float | None
    vote_count: int | None
    genres: tuple[GenreRecord, ...] = ()
    crosswalk: tuple[CrosswalkId, ...] = ()
    keywords: tuple[KeywordRecord, ...] = ()
    credits: tuple[CreditRecord, ...] = ()

    @field_validator("runtime_minutes")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        return v if v is None or v > 0 else None

    def content_hash(self) -> str:
        """Hash estável dos campos de conteúdo (detecta mudança real entre coletas)."""
        # crosswalk fica fora: é vínculo entre fontes, não conteúdo do item.
        data = self.model_dump(
            mode="json", exclude={"source", "item_type", "external_id", "crosswalk"}
        )
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _image_path(value: Any) -> str | None:
    """Aceita só caminhos relativos do TMDB (`/abc.jpg`); evita URL arbitrária no catálogo."""
    value = _clean_str(value)
    if value and re.fullmatch(r"/[A-Za-z0-9_\-]+\.(?:jpg|jpeg|png|svg)", value):
        return value
    return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _num(value: Any, cast: type[float] | type[int]) -> Any:
    if value is None or isinstance(value, bool):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


_IMDB_RE = re.compile(r"^tt\d{7,10}$")


def _crosswalk(item_type: ItemType, payload: dict[str, Any]) -> tuple[CrosswalkId, ...]:
    ext = payload.get("external_ids") or {}
    imdb = _clean_str(ext.get("imdb_id") or payload.get("imdb_id"))
    out: list[CrosswalkId] = []
    if imdb and _IMDB_RE.match(imdb):
        out.append(CrosswalkId(source="imdb", external_type="title", external_id=imdb))
    tvdb = _num(ext.get("tvdb_id"), int)
    if item_type == "tv" and tvdb and tvdb > 0:
        out.append(CrosswalkId(source="tvdb", external_type="series", external_id=str(tvdb)))
    return tuple(out)


def _keywords(item_type: ItemType, payload: dict[str, Any]) -> tuple[KeywordRecord, ...]:
    block = payload.get("keywords") or {}
    raw = block.get("keywords" if item_type == "movie" else "results") or []
    seen: dict[str, KeywordRecord] = {}
    for k in raw:
        if isinstance(k, dict) and "id" in k and (name := _clean_str(k.get("name"))):
            seen.setdefault(str(k["id"]), KeywordRecord(external_id=str(k["id"]), name=name))
    return tuple(seen.values())


def _credits(item_type: ItemType, payload: dict[str, Any]) -> tuple[CreditRecord, ...]:
    block = payload.get("credits") or {}
    out: dict[str, CreditRecord] = {}

    def person(c: dict[str, Any]) -> tuple[str, str, str | None] | None:
        name = _clean_str(c.get("name"))
        if "id" not in c or not name or not _clean_str(c.get("credit_id")):
            return None
        return str(c["id"]), name, _clean_str(c.get("known_for_department"))

    for c in block.get("cast") or []:
        order = _num(c.get("order"), int) if isinstance(c, dict) else None
        if order is None or order >= MAX_CAST or not (p := person(c)):
            continue
        out[c["credit_id"]] = CreditRecord(
            credit_id=c["credit_id"],
            person_external_id=p[0],
            person_name=p[1],
            known_for_department=p[2],
            credit_type="cast",
            character=_clean_str(c.get("character")),
            ordering=order,
        )
    for c in block.get("crew") or []:
        if not isinstance(c, dict) or c.get("job") not in KEY_CREW_JOBS or not (p := person(c)):
            continue
        out[c["credit_id"]] = CreditRecord(
            credit_id=c["credit_id"],
            person_external_id=p[0],
            person_name=p[1],
            known_for_department=p[2],
            credit_type="crew",
            department=_clean_str(c.get("department")),
            job=c["job"],
        )
    if item_type == "tv":
        for c in payload.get("created_by") or []:
            if isinstance(c, dict) and (p := person(c)):
                out[c["credit_id"]] = CreditRecord(
                    credit_id=c["credit_id"],
                    person_external_id=p[0],
                    person_name=p[1],
                    known_for_department=p[2],
                    credit_type="creator",
                    job="Creator",
                )
    return tuple(sorted(out.values(), key=lambda r: r.credit_id))


def normalize(item_type: ItemType, payload: dict[str, Any]) -> CatalogRecord:
    if "id" not in payload:
        raise NormalizationError("payload sem 'id'")

    if item_type == "movie":
        title = _clean_str(payload.get("title"))
        original_title = _clean_str(payload.get("original_title"))
        released = _parse_date(payload.get("release_date"))
        runtime = _num(payload.get("runtime"), int)
    else:
        title = _clean_str(payload.get("name"))
        original_title = _clean_str(payload.get("original_name"))
        released = _parse_date(payload.get("first_air_date"))
        # `episode_run_time` vem vazio em muitas séries; heurística: duração do último
        # episódio exibido (aproximação, não média da série).
        run_times = payload.get("episode_run_time") or []
        runtime = _num(run_times[0], int) if run_times else None
        if runtime is None:
            last_episode = payload.get("last_episode_to_air") or {}
            runtime = _num(last_episode.get("runtime"), int)

    crosswalk = _crosswalk(item_type, payload)
    collection = payload.get("belongs_to_collection") or {}
    if not isinstance(collection, dict):
        collection = {}
    genres = tuple(
        GenreRecord(external_id=str(g["id"]), name=g["name"].strip())
        for g in payload.get("genres") or []
        if isinstance(g, dict) and "id" in g and _clean_str(g.get("name"))
    )

    try:
        return CatalogRecord(
            item_type=item_type,
            external_id=str(payload["id"]),
            title=title or original_title or "",
            original_title=original_title,
            overview=_clean_str(payload.get("overview")),
            release_date=released,
            release_year=released.year if released else None,
            original_language=_clean_str(payload.get("original_language")),
            runtime_minutes=runtime,
            status=_clean_str(payload.get("status")),
            poster_path=_image_path(payload.get("poster_path")),
            backdrop_path=_image_path(payload.get("backdrop_path")),
            collection_id=_num(collection.get("id"), int),
            collection_name=_clean_str(collection.get("name")),
            popularity=_num(payload.get("popularity"), float),
            vote_average=_num(payload.get("vote_average"), float),
            vote_count=_num(payload.get("vote_count"), int),
            genres=genres,
            crosswalk=crosswalk,
            keywords=_keywords(item_type, payload),
            credits=_credits(item_type, payload),
        )
    except ValidationError as exc:
        msg = exc.errors()[0]["msg"]
        raise NormalizationError(f"{item_type}/{payload.get('id')}: {msg}") from exc
