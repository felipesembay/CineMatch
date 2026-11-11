"""Conjuntos de vizinhos pré-computados: gravação (COPY), ativação e leitura.

`model_version` identifica o conjunto de forma determinística (método + versões dos
artefatos de entrada + parâmetros). Regravar a mesma versão é no-op (idempotente).
Ativar troca o conjunto servido em uma transação (índice único parcial garante 1 ativo
por `slot`), o que permite rollback instantâneo para a versão anterior.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, Engine, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import item_neighbors, neighbor_sets

if TYPE_CHECKING:  # numpy só no extra `ml`; a imagem da API apenas lê vizinhos
    import numpy as np

logger = logging.getLogger(__name__)

SLOT_SIMILAR = "similar"


def write_neighbor_set(
    engine: Engine,
    *,
    model_version: str,
    method: str,
    item_ids: Sequence[int],
    neighbors: "np.ndarray",
    scores: "np.ndarray",
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    slot: str = SLOT_SIMILAR,
    activate: bool = False,
) -> dict[str, Any]:
    """`neighbors[i]` = índices (em `item_ids`) dos vizinhos do item `item_ids[i]`; -1 = vazio."""
    import numpy as np

    ids = np.asarray(item_ids, dtype=np.int64)
    if neighbors.shape != scores.shape or neighbors.shape[0] != ids.size:
        raise ValueError("neighbors/scores desalinhados com item_ids")
    with engine.begin() as conn:
        exists = conn.execute(
            select(neighbor_sets.c.model_version).where(
                neighbor_sets.c.model_version == model_version
            )
        ).scalar()
        if exists:
            logger.info("neighbor_set_exists", extra={"model_version": model_version})
            rows = 0
        else:
            conn.execute(
                pg_insert(neighbor_sets).values(
                    model_version=model_version,
                    slot=slot,
                    method=method,
                    k=int(neighbors.shape[1]),
                    n_items=int(ids.size),
                    params=params or {},
                    metrics=metrics or {},
                )
            )
            rows = _copy_rows(conn, model_version, ids, neighbors, scores)
    if activate:
        activate_set(engine, model_version)
    logger.info(
        "neighbor_set_written",
        extra={"model_version": model_version, "rows": rows, "activated": activate},
    )
    return {
        "model_version": model_version,
        "rows": rows,
        "reused": bool(exists),
        "active": activate,
    }


def _copy_rows(
    conn: Connection,
    version: str,
    ids: "np.ndarray",
    neighbors: "np.ndarray",
    scores: "np.ndarray",
) -> int:
    rows = 0
    raw = conn.connection.driver_connection  # psycopg 3
    with (
        raw.cursor() as cur,
        cur.copy(
            "COPY item_neighbors (model_version, item_id, rank, neighbor_id, score) FROM STDIN"
        ) as copy,
    ):
        for i, item_id in enumerate(ids.tolist()):
            rank = 0
            for j, score in zip(neighbors[i].tolist(), scores[i].tolist(), strict=True):
                if j < 0 or ids[j] == item_id:
                    continue
                copy.write_row((version, item_id, rank, int(ids[j]), float(score)))
                rank += 1
                rows += 1
    return rows


def activate_set(engine: Engine, model_version: str) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            select(neighbor_sets.c.slot).where(neighbor_sets.c.model_version == model_version)
        ).first()
        if row is None:
            raise LookupError(f"conjunto de vizinhos inexistente: {model_version}")
        conn.execute(
            update(neighbor_sets)
            .where(neighbor_sets.c.slot == row.slot, neighbor_sets.c.is_active)
            .values(is_active=False)
        )
        conn.execute(
            update(neighbor_sets)
            .where(neighbor_sets.c.model_version == model_version)
            .values(is_active=True)
        )
    logger.info("neighbor_set_activated", extra={"model_version": model_version})


def active_version(conn: Connection, slot: str = SLOT_SIMILAR) -> str | None:
    return conn.execute(
        select(neighbor_sets.c.model_version).where(
            neighbor_sets.c.slot == slot, neighbor_sets.c.is_active
        )
    ).scalar()


def read_neighbors(
    conn: Connection, model_version: str, item_id: int, limit: int
) -> list[tuple[int, float]]:
    rows = conn.execute(
        select(item_neighbors.c.neighbor_id, item_neighbors.c.score)
        .where(
            item_neighbors.c.model_version == model_version,
            item_neighbors.c.item_id == item_id,
        )
        .order_by(item_neighbors.c.rank)
        .limit(limit)
    ).all()
    return [(int(n), float(s)) for n, s in rows]


def list_sets(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT model_version, slot, method, k, n_items, is_active, created_at, metrics "
                "FROM neighbor_sets ORDER BY created_at DESC"
            )
        )
        return [dict(r._mapping) for r in rows]
