"""Recomendação personalizada "Achamos que você vai gostar" (`/recommendations/me`).

**Rocchio no espaço de escores** sobre o conjunto de vizinhos ativo (ADR-019):

    score(c) = Σ_s w_s · sim(s, c)

- Sementes positivas: curtidos no onboarding (w = 1), favoritos (w = 1), notas 4-5
  (w = 0,5 / 1). Negativas: não curtidos (w = -gamma, gamma = 0,5) e notas 1-2 (w = -1 / -0,5).
- `sim(s, c)` vem dos top-50 vizinhos pré-computados de cada semente (mesma `model_version`
  servida pelo `/similar`). Como o cosseno é linear no vetor de consulta, a soma ponderada dos
  escores equivale ao Rocchio sobre os vetores, truncado aos top-50 de cada semente.
- No benchmark offline (ADR-017), Rocchio de perfil superou seed única e MaxSim.
- Exclui itens já vistos (curtidos, favoritos, avaliados); diversidade: no máximo
  `max_per_collection` por coleção. Sem sementes ou sem candidatos: fallback bayesiano nos
  gêneros preferidos.
"""

from datetime import date
from typing import Any

from sqlalchemy import Connection, text

from cinematch.serving import catalog as cat

GAMMA = 0.5
POOL = 200
PROFILE_VERSION = "profile_rocchio_v1"

_SCORED_SQL = text(f"""
WITH seeds AS (
  SELECT item_id, CASE signal WHEN 'like' THEN 1.0 ELSE -{GAMMA} END AS w
  FROM user_item_feedback WHERE user_id = :u
  UNION ALL SELECT item_id, 1.0 FROM user_favorites WHERE user_id = :u
  UNION ALL SELECT item_id, (value - 3) / 2.0 FROM user_ratings WHERE user_id = :u AND value <> 3
),
w AS (SELECT item_id, sum(w) AS w FROM seeds GROUP BY item_id HAVING sum(w) <> 0)
SELECT n.neighbor_id,
       sum(w.w * n.score) AS score,
       (array_agg(n.item_id ORDER BY w.w * n.score DESC, n.item_id))[1] AS top_seed
FROM w JOIN item_neighbors n ON n.item_id = w.item_id AND n.model_version = :mv
GROUP BY n.neighbor_id
HAVING sum(w.w * n.score) > 0
ORDER BY score DESC, n.neighbor_id
LIMIT :pool
""")  # noqa: S608 - GAMMA é constante do módulo


def personalized(
    conn: Connection,
    user_id: int,
    neighbor_version: str | None,
    seen: set[int],
    genres: list[str],
    k: int,
    max_per_collection: int = 2,
    today: date | None = None,
) -> dict[str, Any]:
    picked: list[tuple[dict[str, Any], float, str | None]] = []
    if neighbor_version:
        rows = conn.execute(_SCORED_SQL, {"u": user_id, "mv": neighbor_version, "pool": POOL})
        scored = [
            (r.neighbor_id, float(r.score), r.top_seed) for r in rows if r.neighbor_id not in seen
        ]
        summaries = cat.summaries_by_ids(
            conn, [c for c, _, _ in scored] + list({s for _, _, s in scored})
        )
        per_coll: dict[int, int] = {}
        for cand, score, seed in scored:
            row = summaries.get(cand)
            if row is None:
                continue
            coll = row["collection_id"]
            if coll is not None:
                if per_coll.get(coll, 0) >= max_per_collection:
                    continue
                per_coll[coll] = per_coll.get(coll, 0) + 1
            seed_title = summaries.get(seed, {}).get("title")
            picked.append((row, score, f"Porque você curtiu {seed_title}" if seed_title else None))
            if len(picked) == k:
                break
    if picked:
        top = picked[0][1] or 1.0
        return {
            "model_version": f"{PROFILE_VERSION}|{neighbor_version}",
            "fallback": False,
            "items": [(row, round(score / top, 6), reason) for row, score, reason in picked],
        }
    return {
        "model_version": f"fallback:{cat.BAYES_VERSION}",
        "fallback": True,
        "items": _fallback(conn, seen, genres, k, today),
    }


def _fallback(
    conn: Connection, seen: set[int], genres: list[str], k: int, today: date | None
) -> list[tuple[dict[str, Any], float, str | None]]:
    out: list[tuple[dict[str, Any], float, str | None]] = []
    for genre in [*genres[:3], None]:
        query = cat.ListQuery(genre=genre, sort="top_rated", page_size=k + len(seen), today=today)
        rows, _ = cat.list_items(conn, query)
        for row in rows:
            if row["id"] in seen or any(r["id"] == row["id"] for r, _, _ in out):
                continue
            reason = f"Popular em {genre}" if genre else "Mais bem avaliados"
            out.append((row, 0.0, reason))
            if len(out) == k:
                return out
    return out
