"""Vizinhos por similaridade de conteúdo (checagem qualitativa; serving é Fase 3)."""

from sqlalchemy import Connection, text


def embedding_neighbors(
    conn: Connection, item_id: int, model_version: str, k: int = 10
) -> list[tuple[int, float]]:
    """Top-k por cosseno (`<=>` do pgvector) dentro da mesma `model_version`. Busca exata."""
    rows = conn.execute(
        text("""
        SELECT e.item_id, 1 - (e.embedding <=> q.embedding) AS score
        FROM item_embeddings e,
             (SELECT embedding FROM item_embeddings
              WHERE item_id = :item_id AND model_version = :mv) q
        WHERE e.model_version = :mv AND e.item_id <> :item_id
        ORDER BY e.embedding <=> q.embedding, e.item_id
        LIMIT :k
        """),
        {"item_id": item_id, "mv": model_version, "k": k},
    ).all()
    if not rows:
        exists = conn.execute(
            text("SELECT 1 FROM item_embeddings WHERE item_id = :i AND model_version = :mv"),
            {"i": item_id, "mv": model_version},
        ).first()
        if exists is None:
            raise KeyError(f"item {item_id} sem embedding para {model_version}")
    return [(int(i), float(s)) for i, s in rows]


def latest_model_version(conn: Connection) -> str | None:
    return conn.execute(
        text("SELECT model_version FROM item_embeddings ORDER BY created_at DESC LIMIT 1")
    ).scalar()
