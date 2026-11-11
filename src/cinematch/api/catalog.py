"""Endpoints de catálogo e similaridade (contrato consumido pelo frontend).

Rotas usam `/movies` para filmes e séries (filtro `type`), conforme spec §10.
"""

import logging
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cinematch.config import Settings, get_settings
from cinematch.db import get_engine
from cinematch.logconfig import request_id_ctx
from cinematch.serving import catalog as cat
from cinematch.serving.neighbors import active_version, read_neighbors

logger = logging.getLogger(__name__)
router = APIRouter(tags=["catalog"])

ItemType = Literal["movie", "tv"]
MAX_NEIGHBORS = 50


class ItemSummary(BaseModel):
    id: int
    type: ItemType
    title: str
    year: int | None
    poster_url: str | None
    backdrop_url: str | None
    genres: list[str]
    vote_average: float | None
    vote_count: int


class ExternalLinks(BaseModel):
    tmdb_url: str | None
    imdb_url: str | None
    tvmaze_url: str | None


class ItemDetail(ItemSummary):
    overview: str | None
    runtime_minutes: int | None
    release_date: date | None
    status: str | None
    original_language: str | None
    directors: list[str]
    cast: list[str]
    keywords: list[str]
    external: ExternalLinks


class ItemPage(BaseModel):
    items: list[ItemSummary]
    page: int
    page_size: int
    total: int


class Recommendation(ItemSummary):
    score: float
    reason: str | None


class RecommendationList(BaseModel):
    request_id: str
    model_version: str
    fallback: bool
    items: list[Recommendation]


class Genre(BaseModel):
    id: int
    name: str
    items: int = Field(description="itens do catálogo com o gênero")


def _image(settings: Settings, path: str | None, size: str) -> str | None:
    return f"{settings.tmdb_image_base_url}/{size}{path}" if path else None


def _summary(row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["item_type"],
        "title": row["title"],
        "year": row["release_year"],
        "poster_url": _image(settings, row["poster_path"], settings.poster_size),
        "backdrop_url": _image(settings, row["backdrop_path"], settings.backdrop_size),
        "genres": list(row["genres"]),
        "vote_average": row["vote_average"],
        "vote_count": row["vote_count"],
    }


EngineDep = Annotated[Engine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/genres", response_model=list[Genre])
def genres(engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return cat.list_genres(conn)


@router.get("/movies", response_model=ItemPage)
def list_movies(
    engine: EngineDep,
    settings: SettingsDep,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    type: Annotated[ItemType | None, Query()] = None,
    genre: Annotated[str | None, Query(max_length=64)] = None,
    year_from: Annotated[int | None, Query(ge=1870, le=2100)] = None,
    year_to: Annotated[int | None, Query(ge=1870, le=2100)] = None,
    sort: cat.Sort | None = None,
    page: Annotated[int, Query(ge=1, le=500)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    include_unreleased: bool = False,
) -> ItemPage:
    query = cat.ListQuery(
        q=q,
        item_type=type,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        sort=sort or ("relevance" if q else "popular"),
        page=page,
        page_size=page_size,
        include_unreleased=include_unreleased,
    )
    with engine.connect() as conn:
        rows, total = cat.list_items(conn, query)
    return ItemPage(
        items=[ItemSummary(**_summary(r, settings)) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/movies/{item_id}", response_model=ItemDetail, responses={404: {}})
def get_movie(item_id: int, engine: EngineDep, settings: SettingsDep) -> ItemDetail:
    with engine.connect() as conn:
        row = cat.item_detail(conn, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="item não encontrado")
    kind = "movie" if row["item_type"] == "movie" else "tv"
    return ItemDetail(
        **_summary(row, settings),
        overview=row["overview"],
        runtime_minutes=row["runtime_minutes"],
        release_date=row["release_date"],
        status=row["status"],
        original_language=row["original_language"],
        directors=row["directors"],
        cast=row["cast"][:15],
        keywords=row["keywords"],
        external=ExternalLinks(
            tmdb_url=f"https://www.themoviedb.org/{kind}/{row['tmdb_id']}"
            if row["tmdb_id"]
            else None,
            imdb_url=f"https://www.imdb.com/title/{row['imdb_id']}/" if row["imdb_id"] else None,
            tvmaze_url=row["tvmaze_url"],
        ),
    )


def _reason(
    query: dict[str, Any], cand: dict[str, Any], people: dict[int, dict[str, list[str]]]
) -> str | None:
    """Explicação por sobreposição de metadados (não descreve o interior do modelo)."""
    if query["collection_id"] and cand["collection_id"] == query["collection_id"]:
        return (
            f"Da coleção {cand['collection_name']}"
            if cand["collection_name"]
            else ("Mesma coleção")
        )
    q_people, c_people = people.get(query["id"], {}), people.get(cand["id"], {})
    shared = [p for p in c_people.get("directors", []) if p in q_people.get("directors", [])]
    if shared:
        return f"{'Direção' if query['item_type'] == 'movie' else 'Criação'} de {shared[0]}"
    shared = [p for p in c_people.get("cast", []) if p in q_people.get("cast", [])]
    if shared:
        return f"Com {shared[0]}"
    genres = [g for g in cand["genres"] if g in query["genres"]]
    if len(genres) >= 2:
        return f"Também {genres[0]} e {genres[1]}"
    if genres:
        return f"Também {genres[0]}"
    return "Tema parecido"  # similaridade vem do texto (sinopse/keywords)


@router.get("/movies/{item_id}/similar", response_model=RecommendationList, responses={404: {}})
def similar(
    item_id: int,
    engine: EngineDep,
    settings: SettingsDep,
    k: Annotated[int, Query(ge=1, le=MAX_NEIGHBORS)] = 12,
    max_per_collection: Annotated[int, Query(ge=1, le=MAX_NEIGHBORS)] = 3,
) -> RecommendationList:
    """Itens similares por conteúdo (conjunto de vizinhos ativo). Diversidade: no máximo
    `max_per_collection` itens da mesma coleção. Sem vizinhos: fallback por popularidade."""
    with engine.connect() as conn:
        item_type = cat.item_exists(conn, item_id)
        if item_type is None:
            raise HTTPException(status_code=404, detail="item não encontrado")
        version = active_version(conn)
        pairs = read_neighbors(conn, version, item_id, MAX_NEIGHBORS) if version else []
        fallback = not pairs
        if fallback:
            rows = cat.popular_fallback(conn, item_id, item_type, k)
            scored = [(r, None) for r in rows]
            version = f"fallback:{cat.BAYES_VERSION}"
            query_row = cat.summaries_by_ids(conn, [item_id])[item_id]
        else:
            summaries = cat.summaries_by_ids(conn, [item_id, *[n for n, _ in pairs]])
            query_row = summaries[item_id]
            per_coll: dict[int, int] = {}
            scored = []
            for neighbor_id, score in pairs:
                row = summaries.get(neighbor_id)
                if row is None:
                    continue
                coll = row["collection_id"]
                if coll is not None:
                    if per_coll.get(coll, 0) >= max_per_collection:
                        continue
                    per_coll[coll] = per_coll.get(coll, 0) + 1
                scored.append((row, score))
                if len(scored) == k:
                    break
        people = cat.item_people(conn, [item_id, *[r["id"] for r, _ in scored]])

    items = []
    for pos, (row, score) in enumerate(scored):
        reason = (
            f"Popular em {row['genres'][0]}"
            if fallback and row["genres"]
            else (None if fallback else _reason(query_row, row, people))
        )
        items.append(
            Recommendation(
                **_summary(row, settings),
                score=round(score if score is not None else 1.0 - pos / max(len(scored), 1), 6),
                reason=reason,
            )
        )
    request_id = request_id_ctx.get() or ""
    logger.info(
        "similar_served",
        extra={"item_id": item_id, "model_version": version, "fallback": fallback, "n": len(items)},
    )
    return RecommendationList(
        request_id=request_id, model_version=version, fallback=fallback, items=items
    )
