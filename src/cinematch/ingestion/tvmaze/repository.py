"""Persistência TVmaze e resolução de entidades com o catálogo.

Regra de resolução (spec §3: não assumir equivalência de IDs entre fontes):
- Um show TVmaze é ligado a uma série do catálogo **somente** se o IMDb ID e/ou o TheTVDB ID
  do show coincidirem com IDs que o TMDB declarou para aquela série (crosswalk em
  `item_external_ids`, `match_method='tmdb_external_ids'`).
- Se os IDs apontarem para itens diferentes, ou se dois shows apontarem para o mesmo item,
  o caso é **ambíguo** e não é ligado (contado nas métricas).
- Nenhuma correspondência por título/ano.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import catalog_items, item_external_ids, tvmaze_shows
from cinematch.ingestion.tvmaze.normalize import SOURCE, TvmazeShowRecord

LINK_TYPE = "show"


@dataclass(frozen=True)
class Provenance:
    run_id: str | None
    raw_uri: str | None
    synced_at: datetime


def upsert_shows(
    conn: Connection, records: Sequence[TvmazeShowRecord], prov: Provenance
) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    if not records:
        return counts
    # Deduplica dentro do lote (último vence) para o ON CONFLICT não colidir consigo mesmo.
    by_id = {r.tvmaze_id: r for r in records}
    existing = dict(
        conn.execute(
            select(tvmaze_shows.c.tvmaze_id, tvmaze_shows.c.content_hash).where(
                tvmaze_shows.c.tvmaze_id.in_(by_id)
            )
        ).all()
    )
    rows: list[dict[str, Any]] = []
    for tvmaze_id, rec in by_id.items():
        h = rec.content_hash()
        if tvmaze_id not in existing:
            counts["inserted"] += 1
        elif existing[tvmaze_id] != h:
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
        row = rec.model_dump()
        row["genres"] = list(rec.genres)
        row.update(
            content_hash=h,
            last_synced_at=prov.synced_at,
            last_run_id=prov.run_id,
            raw_uri=prov.raw_uri,
        )
        rows.append(row)

    stmt = pg_insert(tvmaze_shows).values(rows)
    updatable = [
        c.name for c in tvmaze_shows.columns if c.name not in {"tvmaze_id", "first_seen_at"}
    ]
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[tvmaze_shows.c.tvmaze_id],
            set_={name: stmt.excluded[name] for name in updatable},
        )
    )
    return counts


def _candidates(conn: Connection) -> list[tuple[int, int, str]]:
    """(tvmaze_id, item_id, método) para cada coincidência exata de ID externo."""
    ext = item_external_ids.c
    out: list[tuple[int, int, str]] = []
    for column, source, ext_type, method in (
        (tvmaze_shows.c.imdb_id, "imdb", "title", "imdb_id"),
        (tvmaze_shows.c.thetvdb_id, "tvdb", "series", "thetvdb_id"),
    ):
        rows = conn.execute(
            select(tvmaze_shows.c.tvmaze_id, ext.item_id)
            .join(
                item_external_ids,
                and_(
                    ext.source == source, ext.external_type == ext_type, ext.external_id == column
                ),
            )
            .join(
                catalog_items,
                and_(catalog_items.c.id == ext.item_id, catalog_items.c.item_type == "tv"),
            )
        )
        out.extend((tvmaze_id, item_id, method) for tvmaze_id, item_id in rows)
    return out


def link_shows(conn: Connection, prov: Provenance) -> dict[str, int]:
    """Recalcula todos os vínculos tvmaze→catálogo. Idempotente."""
    by_show: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for tvmaze_id, item_id, method in _candidates(conn):
        by_show[tvmaze_id][item_id].add(method)

    ambiguous_shows = {s for s, items in by_show.items() if len(items) > 1}
    by_item: dict[int, set[int]] = defaultdict(set)
    for s, items in by_show.items():
        if s not in ambiguous_shows:
            by_item[next(iter(items))].add(s)
    ambiguous_items = {i for i, shows in by_item.items() if len(shows) > 1}

    links: dict[int, tuple[int, str]] = {}
    for item_id, shows in by_item.items():
        if item_id in ambiguous_items:
            continue
        (tvmaze_id,) = shows
        methods = by_show[tvmaze_id][item_id]
        links[tvmaze_id] = (item_id, "+".join(sorted(methods)))

    ext = item_external_ids.c
    # Remove vínculos que deixaram de ser válidos.
    current = select(ext.external_id).where(ext.source == SOURCE, ext.external_type == LINK_TYPE)
    stale = [e for e in conn.execute(current).scalars() if int(e) not in links]
    if stale:
        conn.execute(
            delete(item_external_ids).where(
                ext.source == SOURCE, ext.external_type == LINK_TYPE, ext.external_id.in_(stale)
            )
        )

    for tvmaze_id, (item_id, method) in links.items():
        stmt = pg_insert(item_external_ids).values(
            item_id=item_id,
            source=SOURCE,
            external_type=LINK_TYPE,
            external_id=str(tvmaze_id),
            last_synced_at=prov.synced_at,
            last_run_id=prov.run_id,
            raw_uri=prov.raw_uri,
            match_method=method,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[ext.source, ext.external_type, ext.external_id],
                set_={
                    "item_id": stmt.excluded.item_id,
                    "match_method": stmt.excluded.match_method,
                    "last_synced_at": stmt.excluded.last_synced_at,
                    "last_run_id": stmt.excluded.last_run_id,
                },
            )
        )

    methods = [m for _, m in links.values()]
    return {
        "linked": len(links),
        "linked_imdb_only": methods.count("imdb_id"),
        "linked_thetvdb_only": methods.count("thetvdb_id"),
        "linked_both": methods.count("imdb_id+thetvdb_id"),
        "ambiguous_shows": len(ambiguous_shows),
        "ambiguous_items": len(ambiguous_items),
        "unlinked_stale_removed": len(stale),
    }
