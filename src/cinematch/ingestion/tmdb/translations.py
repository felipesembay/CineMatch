"""Coleta avulsa de traduções pt-BR para itens já no catálogo (`ingest translations`).

Coletas novas já trazem `translations` no `append_to_response` dos detalhes; este comando
cobre os itens coletados antes disso. Uma requisição por item (`/{tipo}/{id}/translations`).

- Só itens sem linha em `item_translations` para o idioma: retomável (rodar de novo continua).
  Itens sem tradução gravam `source_language = 'none'` para não serem buscados de novo.
- Respostas brutas vão para o Bronze (`entity=<tipo>_translations`), com proveniência.
- Usa o advisory lock da fonte TMDB: não roda junto com uma coleta.
"""

import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from cinematch.ingestion import repository as repo
from cinematch.ingestion.http import NotFoundError
from cinematch.ingestion.raw_store import TMDB_LICENSE_NOTES, RawRecord, RawStore
from cinematch.ingestion.tmdb.client import TmdbClient
from cinematch.ingestion.tmdb.normalize import DISPLAY_LANGUAGE, SOURCE, pick_translation

logger = logging.getLogger(__name__)

BATCH = 100

_MISSING_SQL = text("""
SELECT ci.id, ci.item_type, e.external_id
FROM catalog_items ci
JOIN item_external_ids e
  ON e.item_id = ci.id AND e.source = 'tmdb' AND e.match_method IS NULL
     AND e.external_type = ci.item_type
WHERE ci.content_hash <> 'tombstone'
  AND NOT EXISTS (SELECT 1 FROM item_translations t
                  WHERE t.item_id = ci.id AND t.language = :lang)
ORDER BY ci.popularity DESC NULLS LAST, ci.id
LIMIT :limit""")


def missing_items(engine: Engine, limit: int | None = None) -> list[tuple[int, str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(_MISSING_SQL, {"lang": DISPLAY_LANGUAGE, "limit": limit})
        return [(r.id, r.item_type, r.external_id) for r in rows]


def backfill_translations(
    engine: Engine, client: TmdbClient, store: RawStore, max_items: int | None = None
) -> dict[str, Any]:
    stats: dict[str, Any] = defaultdict(int)
    run_id = f"translations-{uuid4().hex[:12]}"
    started = time.monotonic()
    with engine.connect() as lock_conn:
        if not repo.try_lock_source(lock_conn, SOURCE):
            raise repo.ConcurrentRunError(f"ingestão '{SOURCE}' em andamento; tente depois")
        try:
            todo = missing_items(engine, max_items)
            stats["pending"] = len(todo)
            for start in range(0, len(todo), BATCH):
                _process_batch(engine, client, store, run_id, todo[start : start + BATCH], stats)
                logger.info(
                    "translations_progress",
                    extra={"done": min(start + BATCH, len(todo)), "of": len(todo)},
                )
        finally:
            repo.unlock_source(lock_conn, SOURCE)
            lock_conn.commit()
    stats["run_id"] = run_id
    stats["duration_s"] = round(time.monotonic() - started, 1)
    logger.info("translations_done", extra=dict(stats))
    return dict(stats)


def _process_batch(
    engine: Engine,
    client: TmdbClient,
    store: RawStore,
    run_id: str,
    batch: list[tuple[int, str, str]],
    stats: dict[str, Any],
) -> None:
    fetched: dict[str, list[tuple[int, RawRecord]]] = defaultdict(list)
    for item_id, item_type, external_id in batch:
        endpoint = f"/{item_type}/{external_id}/translations"
        try:
            payload = client.translations(item_type, int(external_id))  # type: ignore[arg-type]
        except NotFoundError:
            stats["not_found"] += 1
            continue
        fetched[item_type].append(
            (
                item_id,
                RawRecord(
                    source=SOURCE,
                    entity=f"{item_type}_translations",
                    external_id=external_id,
                    endpoint=endpoint,
                    request_params={},
                    status_code=200,
                    fetched_at=datetime.now(UTC),
                    run_id=run_id,
                    license_notes=TMDB_LICENSE_NOTES,
                    payload=payload,
                ),
            )
        )
    with engine.begin() as conn:
        for item_type, pairs in fetched.items():
            uri = store.write_batch([rec for _, rec in pairs])
            for item_id, rec in pairs:
                tr = pick_translation(item_type, rec.payload)  # type: ignore[arg-type]
                repo.upsert_translation(conn, item_id, tr, uri)
                stats[f"source_{tr.source_language}"] += 1
                stats["titles_translated"] += tr.title is not None
                stats["overviews_translated"] += tr.overview is not None
                stats["written"] += 1
