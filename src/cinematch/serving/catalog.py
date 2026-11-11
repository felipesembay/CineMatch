"""Consultas de catálogo para a API: listagem/busca, detalhe e ranking por popularidade.

**Média bayesiana (`BAYES_VERSION`)**, por tipo (filme/série):
    nota = (v·R + m·C) / (v + m)
- R: `vote_average` do TMDB; v: `vote_count`.
- C: média de `vote_average` dos itens do mesmo tipo com votos.
- m: percentil 75 de `vote_count` desses itens (2026-11-10: filmes 3.572, séries 497).
  A mediana (séries: 75) deixava títulos com ~400 votos no topo de "mais bem avaliados".
Itens com poucos votos são puxados para a média do tipo (899 itens têm < 10 votos).
Votos e notas são agregados externos do TMDB, não avaliações de usuários do CineMatch.

**Regra de lançamento:** "lançado" = `release_date <= hoje`. Por padrão a listagem omite
itens não lançados ou sem data (`include_unreleased=true` os inclui).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import Connection, text

BAYES_VERSION = "bayes_v1:m=p75_votes_by_type,C=mean_rating_by_type"
Sort = Literal["popular", "top_rated", "recent", "relevance"]

_STATS_CTE = """
stats AS (
  SELECT item_type,
         avg(vote_average) FILTER (WHERE vote_count > 0) AS c,
         percentile_cont(0.75) WITHIN GROUP (ORDER BY vote_count)
           FILTER (WHERE vote_count > 0) AS m
  FROM catalog_items GROUP BY item_type
)"""

_BAYES = (
    "(COALESCE(ci.vote_count, 0) * COALESCE(ci.vote_average, 0) + s.m * s.c)"
    " / NULLIF(COALESCE(ci.vote_count, 0) + s.m, 0)"
)

_GENRES = (
    "COALESCE((SELECT array_agg(g.name ORDER BY g.name) FROM item_genres ig "
    "JOIN genres g ON g.id = ig.genre_id WHERE ig.item_id = ci.id), '{}')"
)

_SUMMARY_COLS = f"""
  ci.id, ci.item_type, ci.title, ci.release_year, ci.release_date, ci.poster_path,
  ci.backdrop_path, ci.vote_average, COALESCE(ci.vote_count, 0) AS vote_count,
  ci.collection_id, ci.collection_name, ci.popularity, {_BAYES} AS bayes_rating,
  {_GENRES} AS genres"""

_ORDER = {
    "popular": "ci.popularity DESC NULLS LAST, ci.id",
    "top_rated": "bayes_rating DESC NULLS LAST, ci.id",
    "recent": "ci.release_date DESC NULLS LAST, ci.id",
    # busca: título exato > prefixo > contém; depois popularidade
    "relevance": (
        "(lower(ci.title) = lower(:q_raw)) DESC, (ci.title ILIKE :q_prefix) DESC, "
        "ci.popularity DESC NULLS LAST, ci.id"
    ),
}


@dataclass(frozen=True)
class ListQuery:
    q: str | None = None
    item_type: str | None = None
    genre: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    sort: Sort = "popular"
    page: int = 1
    page_size: int = 20
    include_unreleased: bool = False
    today: date | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_items(conn: Connection, query: ListQuery) -> tuple[list[dict[str, Any]], int]:
    where, params = ["TRUE"], {}
    if query.q:
        esc = _escape_like(query.q.strip())
        params |= {"q_like": f"%{esc}%", "q_prefix": f"{esc}%", "q_raw": query.q.strip()}
        where.append("(ci.title ILIKE :q_like OR ci.original_title ILIKE :q_like)")
    if query.item_type:
        where.append("ci.item_type = :item_type")
        params["item_type"] = query.item_type
    if query.genre:
        where.append(
            "EXISTS (SELECT 1 FROM item_genres ig JOIN genres g ON g.id = ig.genre_id "
            "WHERE ig.item_id = ci.id AND lower(g.name) = lower(:genre))"
        )
        params["genre"] = query.genre
    if query.year_from is not None:
        where.append("ci.release_year >= :year_from")
        params["year_from"] = query.year_from
    if query.year_to is not None:
        where.append("ci.release_year <= :year_to")
        params["year_to"] = query.year_to
    if not query.include_unreleased:
        where.append("ci.release_date IS NOT NULL AND ci.release_date <= :today")
        params["today"] = query.today or date.today()
    sort = query.sort if (query.sort != "relevance" or query.q) else "popular"
    # Só fragmentos constantes deste módulo entram no f-string; valores vão por bind.
    sql = f"""
WITH {_STATS_CTE}
SELECT {_SUMMARY_COLS}, count(*) OVER () AS total
FROM catalog_items ci JOIN stats s USING (item_type)
WHERE {" AND ".join(where)}
ORDER BY {_ORDER[sort]}
LIMIT :limit OFFSET :offset"""
    params |= {"limit": query.page_size, "offset": (query.page - 1) * query.page_size}
    rows = [dict(r._mapping) for r in conn.execute(text(sql), params)]
    total = rows[0]["total"] if rows else _count_only(conn, query, where, params)
    return rows, total


def _count_only(conn: Connection, query: ListQuery, where: list[str], params: dict) -> int:
    if query.page == 1:
        return 0
    sql = f"SELECT count(*) FROM catalog_items ci WHERE {' AND '.join(where)}"
    return int(conn.execute(text(sql), params).scalar_one())


def summaries_by_ids(conn: Connection, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    sql = f"""
WITH {_STATS_CTE}
SELECT {_SUMMARY_COLS}
FROM catalog_items ci JOIN stats s USING (item_type)
WHERE ci.id = ANY(:ids)"""
    return {r.id: dict(r._mapping) for r in conn.execute(text(sql), {"ids": ids})}


def item_people(conn: Connection, ids: list[int]) -> dict[int, dict[str, list[str]]]:
    """Diretores/criadores e elenco principal (ordem de crédito) por item."""
    out: dict[int, dict[str, list[str]]] = {i: {"directors": [], "cast": []} for i in ids}
    rows = conn.execute(
        text("""
SELECT r.item_id, p.name,
       CASE WHEN r.credit_type = 'cast' THEN 'cast' ELSE 'directors' END AS role
FROM item_credits r JOIN people p ON p.id = r.person_id
WHERE r.item_id = ANY(:ids)
  AND (r.job = 'Director' OR r.credit_type = 'creator'
       OR (r.credit_type = 'cast' AND r.ordering < :max_cast))
ORDER BY r.item_id, role, r.ordering NULLS LAST, p.name"""),
        {"ids": ids, "max_cast": 15},
    )
    for item_id, name, role in rows:
        if name not in out[item_id][role]:
            out[item_id][role].append(name)
    return out


def item_detail(conn: Connection, item_id: int) -> dict[str, Any] | None:
    summary = summaries_by_ids(conn, [item_id]).get(item_id)
    if summary is None:
        return None
    row = conn.execute(
        text("""
SELECT ci.overview, ci.runtime_minutes, ci.status, ci.original_language,
       (SELECT s.summary FROM item_external_ids e JOIN tvmaze_shows s
          ON s.tvmaze_id = e.external_id::int
        WHERE e.item_id = ci.id AND e.source = 'tvmaze' LIMIT 1) AS tvmaze_summary,
       (SELECT s.url FROM item_external_ids e JOIN tvmaze_shows s
          ON s.tvmaze_id = e.external_id::int
        WHERE e.item_id = ci.id AND e.source = 'tvmaze' LIMIT 1) AS tvmaze_url,
       (SELECT external_id FROM item_external_ids
        WHERE item_id = ci.id AND source = 'tmdb' AND match_method IS NULL LIMIT 1) AS tmdb_id,
       (SELECT external_id FROM item_external_ids
        WHERE item_id = ci.id AND source = 'imdb' ORDER BY id LIMIT 1) AS imdb_id,
       COALESCE((SELECT array_agg(k.name ORDER BY k.name) FROM item_keywords ik
                 JOIN keywords k ON k.id = ik.keyword_id WHERE ik.item_id = ci.id), '{}')
         AS keywords
FROM catalog_items ci WHERE ci.id = :id"""),
        {"id": item_id},
    ).one()
    people = item_people(conn, [item_id])[item_id]
    return {
        **summary,
        "overview": row.overview or row.tvmaze_summary,
        "overview_source": "tmdb" if row.overview else ("tvmaze" if row.tvmaze_summary else None),
        "runtime_minutes": row.runtime_minutes,
        "status": row.status,
        "original_language": row.original_language,
        "keywords": list(row.keywords),
        "tmdb_id": row.tmdb_id,
        "imdb_id": row.imdb_id,
        "tvmaze_url": row.tvmaze_url,
        **people,
    }


def item_exists(conn: Connection, item_id: int) -> str | None:
    """Tipo do item ou None."""
    return conn.execute(
        text("SELECT item_type FROM catalog_items WHERE id = :id"), {"id": item_id}
    ).scalar()


def popular_fallback(
    conn: Connection, item_id: int, item_type: str, k: int, today: date | None = None
) -> list[dict[str, Any]]:
    """Fallback do `/similar`: mais bem avaliados (bayes) do mesmo tipo que compartilham
    gênero com o item; se não houver gênero, os mais bem avaliados do tipo."""
    sql = f"""
WITH {_STATS_CTE},
item_g AS (SELECT genre_id FROM item_genres WHERE item_id = :id)
SELECT {_SUMMARY_COLS}
FROM catalog_items ci JOIN stats s USING (item_type)
WHERE ci.item_type = :item_type AND ci.id <> :id
  AND ci.release_date IS NOT NULL AND ci.release_date <= :today
  AND (NOT EXISTS (SELECT 1 FROM item_g)
       OR EXISTS (SELECT 1 FROM item_genres ig
                  WHERE ig.item_id = ci.id AND ig.genre_id IN (SELECT genre_id FROM item_g)))
ORDER BY bayes_rating DESC NULLS LAST, ci.id
LIMIT :k"""
    params = {"id": item_id, "item_type": item_type, "k": k, "today": today or date.today()}
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def list_genres(conn: Connection) -> list[dict[str, Any]]:
    """Gêneros com ao menos um item, por nome (TMDB tem listas distintas p/ filme e série)."""
    rows = conn.execute(
        text("""
SELECT min(g.id) AS id, g.name, count(DISTINCT ig.item_id) AS items
FROM genres g JOIN item_genres ig ON ig.genre_id = g.id
GROUP BY g.name ORDER BY g.name""")
    )
    return [dict(r._mapping) for r in rows]
