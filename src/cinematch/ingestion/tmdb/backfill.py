"""Reprocessa o Bronze TMDB: re-normaliza o registro mais recente de cada item e faz upsert.

Sem requisições à API. Usado quando a normalização evolui (ex.: crosswalk, keywords,
créditos) para aplicar a nova lógica a itens já coletados. Idempotente: itens cujo conteúdo
normalizado não mudou contam como `unchanged`.

Usa o mesmo advisory lock da ingestão TMDB: não roda em paralelo com uma coleta.
"""

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from cinematch.ingestion import repository as repo
from cinematch.ingestion.raw_store import read_records
from cinematch.ingestion.tmdb.client import ITEM_TYPES
from cinematch.ingestion.tmdb.normalize import SOURCE, NormalizationError, normalize

logger = logging.getLogger(__name__)

COMMIT_EVERY = 200


def latest_bronze_records(root: Path) -> dict[tuple[str, str], tuple[dict[str, Any], str]]:
    latest: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    for item_type in ITEM_TYPES:
        for path in sorted((root / f"source={SOURCE}" / f"entity={item_type}").rglob("*.jsonl.gz")):
            uri = path.resolve().as_uri()
            for rec in read_records(path):
                key = (item_type, str(rec["external_id"]))
                if key not in latest or rec["fetched_at"] > latest[key][0]["fetched_at"]:
                    latest[key] = (rec, uri)
    return latest


def reprocess_tmdb_bronze(engine: Engine, bronze_root: Path) -> dict[str, int]:
    stats: dict[str, int] = defaultdict(int)
    with engine.connect() as lock_conn:
        if not repo.try_lock_source(lock_conn, SOURCE):
            raise repo.ConcurrentRunError(f"ingestão '{SOURCE}' em andamento; tente depois")
        try:
            latest = latest_bronze_records(Path(bronze_root))
            stats["bronze_items"] = len(latest)
            items = list(latest.values())
            for start in range(0, len(items), COMMIT_EVERY):
                with engine.begin() as conn:
                    for raw, uri in items[start : start + COMMIT_EVERY]:
                        try:
                            rec = normalize(raw["entity"], raw["payload"])
                        except NormalizationError:
                            stats["invalid"] += 1
                            continue
                        prov = repo.Provenance(
                            run_id=raw["run_id"],
                            raw_uri=uri,
                            synced_at=datetime.fromisoformat(raw["fetched_at"]),
                        )
                        stats[repo.upsert_item(conn, rec, prov)] += 1
                        stats["crosswalk_ids"] += len(rec.crosswalk)
                        stats["items_without_crosswalk"] += 0 if rec.crosswalk else 1
                logger.info("reprocess_progress", extra={"done": start + COMMIT_EVERY})
        finally:
            repo.unlock_source(lock_conn, SOURCE)
            lock_conn.commit()
    logger.info("reprocess_done", extra=dict(stats))
    return dict(stats)
