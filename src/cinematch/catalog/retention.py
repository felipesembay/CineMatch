"""Expiração de conteúdo TMDB (termos §1.C: cache máximo de 6 meses).

Remove itens cujo ID primário TMDB não é sincronizado há mais de `older_than_days` dias
(ex.: o TMDB passou a devolver 404 no refresh). A remoção cascateia para IDs externos,
gêneros, keywords, créditos e embeddings; pessoas e keywords órfãs também são removidas.

Itens referenciados por dados de usuários (favoritos, avaliações, curtidas) viram
**tombstone**: o conteúdo vindo do TMDB é apagado (título, sinopse, imagens, gêneros, créditos,
keywords, embeddings, vizinhos), mas o ID interno permanece, preservando o histórico do
usuário. Tombstones têm `content_hash = 'tombstone'` e `release_date` nula (somem das
listagens padrão).
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


_USER_REFERENCED = text("""
SELECT item_id FROM user_favorites WHERE item_id = ANY(:ids)
UNION SELECT item_id FROM user_ratings WHERE item_id = ANY(:ids)
UNION SELECT item_id FROM user_item_feedback WHERE item_id = ANY(:ids)
""")

TOMBSTONE_TITLE = "Título indisponível"


def _tombstone(conn, ids: list[int]) -> int:
    for table in ("item_genres", "item_keywords", "item_credits", "item_embeddings"):
        conn.execute(text(f"DELETE FROM {table} WHERE item_id = ANY(:ids)"), {"ids": ids})  # noqa: S608
    conn.execute(
        text("DELETE FROM item_neighbors WHERE item_id = ANY(:ids) OR neighbor_id = ANY(:ids)"),
        {"ids": ids},
    )
    conn.execute(
        text("DELETE FROM item_external_ids WHERE item_id = ANY(:ids) AND source <> 'tmdb'"),
        {"ids": ids},
    )
    return conn.execute(
        text("""
UPDATE catalog_items SET title = :title, original_title = NULL, overview = NULL,
  release_date = NULL, release_year = NULL, original_language = NULL, runtime_minutes = NULL,
  status = 'Removed', poster_path = NULL, backdrop_path = NULL, collection_id = NULL,
  collection_name = NULL, popularity = NULL, vote_average = NULL, vote_count = NULL,
  content_hash = 'tombstone', updated_at = now()
WHERE id = ANY(:ids) AND content_hash <> 'tombstone'"""),
        {"ids": ids, "title": TOMBSTONE_TITLE},
    ).rowcount


@dataclass
class ExpireResult:
    cutoff: str
    candidates: int
    deleted_items: int = 0
    deleted_people: int = 0
    deleted_keywords: int = 0
    tombstoned_items: int = 0
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
        referenced = set(conn.execute(_USER_REFERENCED, {"ids": ids}).scalars())
        if referenced:
            result.tombstoned_items = _tombstone(conn, sorted(referenced))
        ids = [i for i in ids if i not in referenced]
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
