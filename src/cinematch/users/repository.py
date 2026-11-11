"""Acesso a dados de usuários e interações (SQLAlchemy Core)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import (
    catalog_items,
    events,
    user_favorites,
    user_item_feedback,
    user_preferences,
    user_ratings,
    user_sessions,
    users,
)
from cinematch.users.security import new_session_token, token_hash


class EmailTaken(ValueError):  # noqa: N818 - nome de domínio, não erro genérico
    pass


def _user_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "name": row.display_name or row.email.split("@")[0],
        "onboarded": row.onboarded_at is not None,
    }


def create_user(conn: Connection, email: str, password_hash: str, name: str | None) -> dict:
    exists = conn.execute(
        select(users.c.id).where(func.lower(users.c.email) == email.lower())
    ).first()
    if exists:
        raise EmailTaken(email)
    row = conn.execute(
        insert(users)
        .values(email=email, password_hash=password_hash, display_name=name)
        .returning(users)
    ).one()
    return _user_dict(row)


def user_for_login(conn: Connection, email: str) -> Any | None:
    return conn.execute(select(users).where(func.lower(users.c.email) == email.lower())).first()


def create_session(conn: Connection, user_id: int, ttl_days: int) -> str:
    token = new_session_token()
    conn.execute(
        insert(user_sessions).values(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
        )
    )
    # Limpeza oportunista de sessões expiradas deste usuário.
    conn.execute(
        delete(user_sessions).where(
            user_sessions.c.user_id == user_id, user_sessions.c.expires_at < func.now()
        )
    )
    return token


def user_for_token(conn: Connection, token: str) -> dict[str, Any] | None:
    row = conn.execute(
        select(users)
        .join(user_sessions, user_sessions.c.user_id == users.c.id)
        .where(
            user_sessions.c.token_hash == token_hash(token),
            user_sessions.c.expires_at > func.now(),
        )
    ).first()
    return _user_dict(row) if row else None


def delete_session(conn: Connection, token: str) -> None:
    conn.execute(delete(user_sessions).where(user_sessions.c.token_hash == token_hash(token)))


def delete_user(conn: Connection, user_id: int) -> None:
    conn.execute(delete(users).where(users.c.id == user_id))  # cascata; eventos -> anônimos


def existing_items(conn: Connection, ids: Sequence[int]) -> set[int]:
    if not ids:
        return set()
    rows = conn.execute(select(catalog_items.c.id).where(catalog_items.c.id.in_(list(ids))))
    return set(rows.scalars())


# --- preferências ------------------------------------------------------------------------


def get_preferences(conn: Connection, user_id: int) -> dict[str, Any]:
    genres = conn.execute(
        select(user_preferences.c.genres).where(user_preferences.c.user_id == user_id)
    ).scalar()
    rows = conn.execute(
        select(user_item_feedback.c.item_id, user_item_feedback.c.signal)
        .where(user_item_feedback.c.user_id == user_id)
        .order_by(user_item_feedback.c.created_at, user_item_feedback.c.item_id)
    ).all()
    return {
        "liked": [i for i, s in rows if s == "like"],
        "disliked": [i for i, s in rows if s == "dislike"],
        "genres": list(genres or []),
    }


def replace_preferences(
    conn: Connection, user_id: int, liked: list[int], disliked: list[int], genres: list[str]
) -> None:
    conn.execute(
        pg_insert(user_preferences)
        .values(user_id=user_id, genres=genres)
        .on_conflict_do_update(
            index_elements=[user_preferences.c.user_id],
            set_={"genres": genres, "updated_at": func.now()},
        )
    )
    conn.execute(delete(user_item_feedback).where(user_item_feedback.c.user_id == user_id))
    rows = [{"user_id": user_id, "item_id": i, "signal": "like"} for i in liked]
    rows += [{"user_id": user_id, "item_id": i, "signal": "dislike"} for i in disliked]
    if rows:
        conn.execute(insert(user_item_feedback), rows)
    conn.execute(
        update(users)
        .where(users.c.id == user_id, users.c.onboarded_at.is_(None))
        .values(onboarded_at=func.now())
    )


# --- favoritos ---------------------------------------------------------------------------


def favorite_ids(conn: Connection, user_id: int) -> list[int]:
    return list(
        conn.execute(
            select(user_favorites.c.item_id)
            .where(user_favorites.c.user_id == user_id)
            .order_by(user_favorites.c.created_at.desc(), user_favorites.c.item_id)
        ).scalars()
    )


def add_favorite(conn: Connection, user_id: int, item_id: int) -> None:
    conn.execute(
        pg_insert(user_favorites).values(user_id=user_id, item_id=item_id).on_conflict_do_nothing()
    )


def remove_favorite(conn: Connection, user_id: int, item_id: int) -> None:
    conn.execute(
        delete(user_favorites).where(
            user_favorites.c.user_id == user_id, user_favorites.c.item_id == item_id
        )
    )


def replace_favorites(conn: Connection, user_id: int, item_ids: list[int]) -> None:
    conn.execute(delete(user_favorites).where(user_favorites.c.user_id == user_id))
    if item_ids:
        conn.execute(insert(user_favorites), [{"user_id": user_id, "item_id": i} for i in item_ids])


# --- avaliações --------------------------------------------------------------------------


def upsert_rating(
    conn: Connection, user_id: int, item_id: int, value: int, comment: str | None
) -> dict[str, Any]:
    stmt = pg_insert(user_ratings).values(
        user_id=user_id, item_id=item_id, value=value, comment=comment
    )
    row = conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[user_ratings.c.user_id, user_ratings.c.item_id],
            set_={"value": value, "comment": comment, "updated_at": func.now()},
        ).returning(user_ratings)
    ).one()
    return dict(row._mapping)


def delete_rating(conn: Connection, user_id: int, item_id: int) -> bool:
    return bool(
        conn.execute(
            delete(user_ratings).where(
                user_ratings.c.user_id == user_id, user_ratings.c.item_id == item_id
            )
        ).rowcount
    )


def list_ratings(conn: Connection, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(user_ratings)
        .where(user_ratings.c.user_id == user_id)
        .order_by(user_ratings.c.updated_at.desc(), user_ratings.c.item_id)
    )
    return [dict(r._mapping) for r in rows]


# --- eventos -----------------------------------------------------------------------------


def insert_events(conn: Connection, user_id: int | None, batch: list[dict[str, Any]]) -> int:
    """Insere eventos ignorando `event_id` repetido. Retorna quantos eram novos."""
    if not batch:
        return 0
    rows = [{**e, "user_id": user_id} for e in batch]
    result = conn.execute(
        pg_insert(events).values(rows).on_conflict_do_nothing().returning(events.c.event_id)
    )
    return len(result.all())


def seen_item_ids(conn: Connection, user_id: int) -> set[int]:
    rows = conn.execute(
        text("""
SELECT item_id FROM user_item_feedback WHERE user_id = :u
UNION SELECT item_id FROM user_favorites WHERE user_id = :u
UNION SELECT item_id FROM user_ratings WHERE user_id = :u"""),
        {"u": user_id},
    )
    return set(rows.scalars())
