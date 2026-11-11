"""Persistência da ingestão: runs, checkpoints por item e upsert idempotente do catálogo.

Idempotência: a identidade do item é (source, external_type, external_id) em
`item_external_ids` (UNIQUE). Reprocessar o mesmo payload não cria linhas e não altera
`updated_at` (comparação por `content_hash`).
"""

import logging
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection, and_, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import (
    catalog_items,
    genres,
    ingestion_run_items,
    ingestion_runs,
    item_credits,
    item_external_ids,
    item_genres,
    item_keywords,
    keywords,
    people,
)
from cinematch.ingestion.tmdb.normalize import CatalogRecord

logger = logging.getLogger(__name__)

UpsertOutcome = Literal["inserted", "updated", "unchanged"]
ItemStatus = Literal["pending", "done", "failed", "not_found"]


class ConcurrentRunError(RuntimeError):
    """Já existe uma ingestão em andamento para a mesma fonte."""


# ---------------------------------------------------------------- locks


def _lock_key(source: str) -> int:
    # Chave estável de 32 bits (pg_advisory_lock aceita bigint).
    return zlib.crc32(f"cinematch:ingest:{source}".encode())


def try_lock_source(conn: Connection, source: str) -> bool:
    return bool(
        conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(source)}).scalar()
    )


def unlock_source(conn: Connection, source: str) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(source)})


# ---------------------------------------------------------------- runs


def create_run(conn: Connection, run_id: str, source: str, params: dict[str, Any]) -> None:
    conn.execute(
        insert(ingestion_runs).values(
            run_id=run_id, source=source, status="running", params=params, state={}, metrics={}
        )
    )


def get_run(conn: Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(ingestion_runs).where(ingestion_runs.c.run_id == run_id)).first()
    return dict(row._mapping) if row else None


def latest_runs(conn: Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(ingestion_runs).order_by(ingestion_runs.c.started_at.desc()).limit(limit)
    )
    return [dict(r._mapping) for r in rows]


def update_run(conn: Connection, run_id: str, **values: Any) -> None:
    conn.execute(update(ingestion_runs).where(ingestion_runs.c.run_id == run_id).values(**values))


# ---------------------------------------------------------------- run items (checkpoint)


def add_run_items(conn: Connection, run_id: str, external_type: str, ids: Iterable[str]) -> int:
    rows = [{"run_id": run_id, "external_type": external_type, "external_id": i} for i in ids]
    if not rows:
        return 0
    # RETURNING conta só as linhas realmente inseridas (rowcount do driver não é confiável
    # para INSERT multi-VALUES com ON CONFLICT).
    stmt = (
        pg_insert(ingestion_run_items)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(ingestion_run_items.c.external_id)
    )
    return len(conn.execute(stmt).all())


def recently_synced(
    conn: Connection, source: str, external_type: str, ids: Sequence[str], since: datetime
) -> set[str]:
    """IDs já sincronizados a partir de `since` (para pular na coleta incremental)."""
    if not ids:
        return set()
    ext = item_external_ids.c
    rows = conn.execute(
        select(ext.external_id).where(
            ext.source == source,
            ext.external_type == external_type,
            ext.external_id.in_(list(ids)),
            ext.last_synced_at >= since,
        )
    )
    return set(rows.scalars())


def stale_items(
    conn: Connection, source: str, synced_before: datetime, limit: int | None = None
) -> list[tuple[str, str]]:
    """(tipo, id externo) da fonte primária sincronizados antes de `synced_before`,
    do mais antigo para o mais recente."""
    ext = item_external_ids.c
    stmt = (
        select(ext.external_type, ext.external_id)
        .where(ext.source == source, ext.match_method.is_(None), ext.last_synced_at < synced_before)
        .order_by(ext.last_synced_at, ext.external_type, ext.external_id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return [(t, i) for t, i in conn.execute(stmt)]


def count_run_items(conn: Connection, run_id: str, external_type: str | None = None) -> int:
    cond = ingestion_run_items.c.run_id == run_id
    if external_type:
        cond = and_(cond, ingestion_run_items.c.external_type == external_type)
    return conn.execute(select(func.count()).where(cond)).scalar_one()


def count_by_status(conn: Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        select(ingestion_run_items.c.status, func.count())
        .where(ingestion_run_items.c.run_id == run_id)
        .group_by(ingestion_run_items.c.status)
    )
    return {status: n for status, n in rows}


def pending_items(conn: Connection, run_id: str, limit: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        select(ingestion_run_items.c.external_type, ingestion_run_items.c.external_id)
        .where(ingestion_run_items.c.run_id == run_id, ingestion_run_items.c.status == "pending")
        .order_by(ingestion_run_items.c.external_type, ingestion_run_items.c.external_id)
        .limit(limit)
    )
    return [(t, i) for t, i in rows]


def requeue_failed(conn: Connection, run_id: str, max_attempts: int) -> int:
    """Volta para `pending` itens com falha que ainda têm tentativas (usado no resume)."""
    result = conn.execute(
        update(ingestion_run_items)
        .where(
            ingestion_run_items.c.run_id == run_id,
            ingestion_run_items.c.status == "failed",
            ingestion_run_items.c.attempts < max_attempts,
        )
        .values(status="pending", updated_at=func.now())
    )
    return result.rowcount or 0


def mark_item(
    conn: Connection,
    run_id: str,
    external_type: str,
    external_id: str,
    status: ItemStatus,
    error: str | None = None,
) -> None:
    conn.execute(
        update(ingestion_run_items)
        .where(
            ingestion_run_items.c.run_id == run_id,
            ingestion_run_items.c.external_type == external_type,
            ingestion_run_items.c.external_id == external_id,
        )
        .values(
            status=status,
            attempts=ingestion_run_items.c.attempts + 1,
            last_error=error,
            updated_at=func.now(),
        )
    )


# ---------------------------------------------------------------- catálogo


@dataclass(frozen=True)
class Provenance:
    run_id: str
    raw_uri: str
    synced_at: datetime


def _item_values(rec: CatalogRecord) -> dict[str, Any]:
    return {
        "item_type": rec.item_type,
        "title": rec.title,
        "original_title": rec.original_title,
        "overview": rec.overview,
        "release_date": rec.release_date,
        "release_year": rec.release_year,
        "original_language": rec.original_language,
        "runtime_minutes": rec.runtime_minutes,
        "status": rec.status,
        "poster_path": rec.poster_path,
        "backdrop_path": rec.backdrop_path,
        "collection_id": rec.collection_id,
        "collection_name": rec.collection_name,
        "popularity": rec.popularity,
        "vote_average": rec.vote_average,
        "vote_count": rec.vote_count,
        "content_hash": rec.content_hash(),
    }


def _sync_genres(conn: Connection, item_id: int, rec: CatalogRecord) -> None:
    genre_ids: list[int] = []
    for g in rec.genres:
        stmt = (
            pg_insert(genres)
            .values(source=rec.source, external_id=g.external_id, name=g.name)
            .on_conflict_do_update(
                index_elements=[genres.c.source, genres.c.external_id],
                set_={"name": g.name},
            )
            .returning(genres.c.id)
        )
        genre_ids.append(conn.execute(stmt).scalar_one())
    conn.execute(item_genres.delete().where(item_genres.c.item_id == item_id))
    if genre_ids:
        conn.execute(
            insert(item_genres), [{"item_id": item_id, "genre_id": gid} for gid in genre_ids]
        )


CROSSWALK_METHOD = "tmdb_external_ids"


def sync_crosswalk(conn: Connection, item_id: int, rec: CatalogRecord, prov: Provenance) -> int:
    """Grava IDs IMDb/TVDB declarados pelo TMDB. Nunca reatribui um ID já ligado a outro
    item: o conflito é registrado em log e ignorado. Retorna o número de conflitos."""
    conflicts = 0
    for cw in rec.crosswalk:
        stmt = pg_insert(item_external_ids).values(
            item_id=item_id,
            source=cw.source,
            external_type=cw.external_type,
            external_id=cw.external_id,
            last_synced_at=prov.synced_at,
            last_run_id=prov.run_id,
            raw_uri=prov.raw_uri,
            match_method=CROSSWALK_METHOD,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                item_external_ids.c.source,
                item_external_ids.c.external_type,
                item_external_ids.c.external_id,
            ],
            set_={
                "last_synced_at": stmt.excluded.last_synced_at,
                "last_run_id": stmt.excluded.last_run_id,
                "raw_uri": stmt.excluded.raw_uri,
            },
            where=item_external_ids.c.item_id == stmt.excluded.item_id,
        ).returning(item_external_ids.c.id)
        if conn.execute(stmt).first() is None:
            conflicts += 1
            logger.warning(
                "crosswalk_conflict",
                extra={"item_id": item_id, "source": cw.source, "external_id": cw.external_id},
            )
    return conflicts


def _sync_keywords(conn: Connection, item_id: int, rec: CatalogRecord) -> None:
    conn.execute(item_keywords.delete().where(item_keywords.c.item_id == item_id))
    if not rec.keywords:
        return
    stmt = pg_insert(keywords).values(
        [{"source": rec.source, "external_id": k.external_id, "name": k.name} for k in rec.keywords]
    )
    ids = (
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[keywords.c.source, keywords.c.external_id],
                set_={"name": stmt.excluded.name},
            ).returning(keywords.c.id)
        )
        .scalars()
        .all()
    )
    conn.execute(insert(item_keywords), [{"item_id": item_id, "keyword_id": i} for i in ids])


def _sync_credits(conn: Connection, item_id: int, rec: CatalogRecord) -> None:
    conn.execute(item_credits.delete().where(item_credits.c.item_id == item_id))
    if not rec.credits:
        return
    persons = {
        c.person_external_id: {
            "source": rec.source,
            "external_id": c.person_external_id,
            "name": c.person_name,
            "known_for_department": c.known_for_department,
        }
        for c in rec.credits
    }
    stmt = pg_insert(people).values(list(persons.values()))
    rows = conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[people.c.source, people.c.external_id],
            set_={
                "name": stmt.excluded.name,
                "known_for_department": stmt.excluded.known_for_department,
            },
        ).returning(people.c.external_id, people.c.id)
    ).all()
    person_ids = dict(rows)
    conn.execute(
        insert(item_credits),
        [
            {
                "item_id": item_id,
                "credit_id": c.credit_id,
                "person_id": person_ids[c.person_external_id],
                "credit_type": c.credit_type,
                "character": c.character,
                "department": c.department,
                "job": c.job,
                "ordering": c.ordering,
            }
            for c in rec.credits
        ],
    )


def _sync_children(conn: Connection, item_id: int, rec: CatalogRecord) -> None:
    _sync_genres(conn, item_id, rec)
    _sync_keywords(conn, item_id, rec)
    _sync_credits(conn, item_id, rec)


def upsert_item(conn: Connection, rec: CatalogRecord, prov: Provenance) -> UpsertOutcome:
    """Insere ou atualiza um item. Deve rodar dentro de uma transação."""
    ext = item_external_ids.c
    existing = conn.execute(
        select(ext.id, ext.item_id)
        .where(
            ext.source == rec.source,
            ext.external_type == rec.item_type,
            ext.external_id == rec.external_id,
        )
        .with_for_update()
    ).first()

    values = _item_values(rec)
    if existing is None:
        item_id = conn.execute(
            insert(catalog_items).values(**values).returning(catalog_items.c.id)
        ).scalar_one()
        conn.execute(
            insert(item_external_ids).values(
                item_id=item_id,
                source=rec.source,
                external_type=rec.item_type,
                external_id=rec.external_id,
                last_synced_at=prov.synced_at,
                last_run_id=prov.run_id,
                raw_uri=prov.raw_uri,
            )
        )
        _sync_children(conn, item_id, rec)
        sync_crosswalk(conn, item_id, rec, prov)
        return "inserted"

    item_id = existing.item_id
    changed = conn.execute(
        update(catalog_items)
        .where(
            catalog_items.c.id == item_id,
            catalog_items.c.content_hash.is_distinct_from(values["content_hash"]),
        )
        .values(**values, updated_at=func.now())
    ).rowcount
    conn.execute(
        update(item_external_ids)
        .where(ext.id == existing.id)
        .values(last_synced_at=prov.synced_at, last_run_id=prov.run_id, raw_uri=prov.raw_uri)
    )
    sync_crosswalk(conn, item_id, rec, prov)
    if changed:
        _sync_children(conn, item_id, rec)
        return "updated"
    return "unchanged"


def upsert_items(
    conn: Connection, records: Sequence[CatalogRecord], prov: Provenance
) -> dict[UpsertOutcome, int]:
    counts: dict[UpsertOutcome, int] = {"inserted": 0, "updated": 0, "unchanged": 0}
    for rec in records:
        counts[upsert_item(conn, rec, prov)] += 1
    return counts
