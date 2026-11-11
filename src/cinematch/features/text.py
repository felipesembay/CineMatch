"""Documentos de texto por item para features de conteúdo.

As versões de texto entram na versão dos artefatos: mudar um template exige nova versão.
Fallback de sinopse: para séries sem `overview` do TMDB, usa o `summary` do TVmaze quando o
vínculo foi verificado por ID (ver ADR-011). A origem fica registrada em `overview_source`.
"""

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import Connection, text

EMBED_TEXT_VERSION = "emb_text_v1"
TFIDF_TEXT_VERSION = "tfidf_text_v1"
MAX_KEYWORDS = 15
MAX_CAST_TOKENS = 5


@dataclass(frozen=True)
class ItemDoc:
    item_id: int
    item_type: str
    title: str
    overview: str | None
    overview_source: str | None
    genres: tuple[str, ...]
    keywords: tuple[str, ...]
    directors: tuple[str, ...]
    cast: tuple[str, ...]


_DOCS_SQL = text(
    """
WITH tvmaze AS (
  SELECT e.item_id, s.summary
  FROM item_external_ids e
  JOIN tvmaze_shows s ON s.tvmaze_id = e.external_id::int
  WHERE e.source = 'tvmaze' AND e.external_type = 'show'
)
SELECT c.id, c.item_type, c.title,
  COALESCE(c.overview, tv.summary) AS overview,
  CASE WHEN c.overview IS NOT NULL THEN 'tmdb'
       WHEN tv.summary IS NOT NULL THEN 'tvmaze' END AS overview_source,
  COALESCE((SELECT array_agg(g.name ORDER BY g.name) FROM item_genres ig
            JOIN genres g ON g.id = ig.genre_id WHERE ig.item_id = c.id), '{}') AS genres,
  COALESCE((SELECT array_agg(k.name ORDER BY k.name) FROM item_keywords ik
            JOIN keywords k ON k.id = ik.keyword_id WHERE ik.item_id = c.id), '{}') AS keywords,
  COALESCE((SELECT array_agg(p.name ORDER BY p.name) FROM item_credits r
            JOIN people p ON p.id = r.person_id
            WHERE r.item_id = c.id AND (r.job = 'Director' OR r.credit_type = 'creator')),
           '{}') AS directors,
  COALESCE((SELECT array_agg(p.name ORDER BY r.ordering) FROM item_credits r
            JOIN people p ON p.id = r.person_id
            WHERE r.item_id = c.id AND r.credit_type = 'cast' AND r.ordering < :max_cast),
           '{}') AS cast_names
FROM catalog_items c
LEFT JOIN tvmaze tv ON tv.item_id = c.id
ORDER BY c.id
"""
)


def load_item_documents(conn: Connection) -> list[ItemDoc]:
    rows = conn.execute(_DOCS_SQL, {"max_cast": MAX_CAST_TOKENS})
    return [
        ItemDoc(
            item_id=r.id,
            item_type=r.item_type,
            title=r.title,
            overview=r.overview,
            overview_source=r.overview_source,
            genres=tuple(r.genres),
            keywords=tuple(r.keywords)[:MAX_KEYWORDS],
            directors=tuple(dict.fromkeys(r.directors)),
            cast=tuple(r.cast_names),
        )
        for r in rows
    ]


def embedding_text_v1(doc: ItemDoc) -> str:
    """`emb_text_v1`: título + gêneros + keywords + sinopse."""
    parts = [doc.title + "."]
    if doc.genres:
        parts.append("Genres: " + ", ".join(doc.genres) + ".")
    if doc.keywords:
        parts.append("Keywords: " + ", ".join(doc.keywords) + ".")
    if doc.overview:
        parts.append(doc.overview)
    return " ".join(parts)


def embedding_text_v2(doc: ItemDoc) -> str:
    """`emb_text_v2`: sem o título (hipótese: o título domina o vetor em v1)."""
    parts = []
    if doc.genres:
        parts.append("Genres: " + ", ".join(doc.genres) + ".")
    if doc.keywords:
        parts.append("Keywords: " + ", ".join(doc.keywords) + ".")
    parts.append(doc.overview or doc.title)  # sem sinopse, o título é o único conteúdo
    return " ".join(parts)


EMBED_TEXT_BUILDERS = {"emb_text_v1": embedding_text_v1, "emb_text_v2": embedding_text_v2}
embedding_text = embedding_text_v1  # compatibilidade: template padrão


def build_embedding_text(doc: ItemDoc, text_version: str = EMBED_TEXT_VERSION) -> str:
    try:
        return EMBED_TEXT_BUILDERS[text_version](doc)
    except KeyError:
        raise ValueError(f"text_version desconhecida: {text_version}") from None


def _token(prefix: str, value: str) -> str:
    slug = re.sub(r"\W+", "_", value.lower(), flags=re.UNICODE).strip("_")
    return f"{prefix}_{slug}" if slug else ""


def tfidf_text(doc: ItemDoc) -> str:
    """Bag-of-words para TF-IDF (`tfidf_text_v1`).

    Gêneros, keywords e pessoas viram tokens únicos prefixados (ex.: `kw_time_travel`,
    `person_christopher_nolan`) para não se misturarem às palavras da sinopse.
    """
    tokens = [_token("genre", g) for g in doc.genres]
    tokens += [_token("kw", k) for k in doc.keywords]
    tokens += [_token("person", p) for p in doc.directors + doc.cast]
    return " ".join([doc.title, doc.overview or "", *filter(None, tokens)])


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
