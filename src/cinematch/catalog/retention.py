"""Expiração de conteúdo TMDB (termos §1.C: cache máximo de 6 meses).

Remove itens cujo ID primário TMDB não é sincronizado há mais de `older_than_days` dias
(ex.: o TMDB passou a devolver 404 no refresh). A remoção cascateia para IDs externos,
gêneros, keywords, créditos e embeddings; pessoas e keywords órfãs também são removidas.

Atenção: quando existirem dados de usuários (Fase 4) referenciando itens, a expiração deve
virar "tombstone" (apagar só o conteúdo TMDB e manter o ID interno), não DELETE.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

TMDB_MAX_AGE_DAYS = 180

_CANDIDATES = text("""
SELECT c.id, c.item_type, c.title, e.external_id, e.last_synced_at
FROM catalog_items c
JOIN item_external_ids e ON e.item_id = c.id AND e.source = 'tmdb' AND e.match_method IS NULL
WHERE e.last_synced_at < :cutoff
ORDER BY e.last_synced_at, c.id
""")


@dataclass
class ExpireResult:
    cutoff: str
    candidates: int
    deleted_items: int = 0
    deleted_people: int = 0
    deleted_keywords: int = 0
    applied: bool = False
    sample: list[dict] = field(default_factory=list)


def expire_tmdb_items(
    engine: Engine,
    older_than_days: int = TMDB_MAX_AGE_DAYS,
    apply: bool = False,
    now: datetime | None = None,
) -> ExpireResult:
    if not 0 < older_than_days <= TMDB_MAX_AGE_DAYS:
        raise ValueError(f"older_than_days deve estar em 1..{TMDB_MAX_AGE_DAYS}")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    with engine.begin() as conn:
        rows = conn.execute(_CANDIDATES, {"cutoff": cutoff}).all()
        result = ExpireResult(
            cutoff=cutoff.isoformat(),
            candidates=len(rows),
            sample=[
                {
                    "item_id": r.id,
                    "type": r.item_type,
                    "title": r.title,
                    "tmdb_id": r.external_id,
                    "last_synced_at": r.last_synced_at.isoformat(),
                }
                for r in rows[:10]
            ],
        )
        if not apply or not rows:
            return result
        ids = [r.id for r in rows]
        result.deleted_items = conn.execute(
            text("DELETE FROM catalog_items WHERE id = ANY(:ids)"), {"ids": ids}
        ).rowcount
        result.deleted_people = conn.execute(
            text(
                "DELETE FROM people p WHERE NOT EXISTS "
                "(SELECT 1 FROM item_credits r WHERE r.person_id = p.id)"
            )
        ).rowcount
        result.deleted_keywords = conn.execute(
            text(
                "DELETE FROM keywords k WHERE NOT EXISTS "
                "(SELECT 1 FROM item_keywords ik WHERE ik.keyword_id = k.id)"
            )
        ).rowcount
        result.applied = True
    logger.warning("tmdb_items_expired", extra=result.__dict__ | {"sample": None})
    return result
