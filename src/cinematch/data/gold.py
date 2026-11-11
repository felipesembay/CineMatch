"""Camada Gold: snapshots imutáveis e versionados do catálogo para treino e avaliação.

Layout: <gold_dir>/<name>/snapshot_id=<id>/{items.parquet, embeddings.parquet, manifest.json}

- Linhas ordenadas por `item_id`; `content_hash` = hash do conteúdo canônico (não dos
  bytes Parquet). Mesmo conteúdo => mesmo snapshot (idempotente, nada é regravado).
- `as_of`: inclui só itens vistos pela primeira vez até `as_of`.
- **Limitação de point-in-time:** os atributos dos itens refletem o estado da última
  sincronização, não o estado em `as_of` (não há histórico SCD do catálogo). Popularidade
  e votos agregados do TMDB podem, portanto, conter informação posterior a `as_of`. O
  manifesto registra `item_features_point_in_time = false`. Datasets com interações
  (Fase 5) devem usar só features temporalmente válidas.
- Gate de qualidade: recusa snapshot se o último `quality check` falhou ou não existe.
"""

import hashlib
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Connection, Engine, desc, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch import __version__
from cinematch.data.schema import data_quality_runs, item_embeddings, training_datasets
from cinematch.features.text import EMBED_TEXT_VERSION, TFIDF_TEXT_VERSION, load_item_documents

logger = logging.getLogger(__name__)

DEFAULT_NAME = "catalog_items"


class SnapshotRefusedError(RuntimeError):
    pass


_ITEMS_SQL = text("""
SELECT c.id AS item_id, c.item_type, c.title, c.original_title, c.release_date,
       c.release_year, c.original_language, c.runtime_minutes, c.status,
       c.popularity, c.vote_average, c.vote_count, c.collection_id, c.collection_name,
       c.poster_path, c.backdrop_path,
       t.external_id AS tmdb_id, t.first_seen_at, t.last_synced_at,
       (SELECT external_id FROM item_external_ids WHERE item_id = c.id AND source = 'imdb'
        ORDER BY id LIMIT 1) AS imdb_id,
       (SELECT external_id FROM item_external_ids WHERE item_id = c.id AND source = 'tvmaze'
        ORDER BY id LIMIT 1) AS tvmaze_id
FROM catalog_items c
JOIN item_external_ids t ON t.item_id = c.id AND t.source = 'tmdb' AND t.match_method IS NULL
WHERE t.first_seen_at <= :as_of
ORDER BY c.id
""")


def _items_table(conn: Connection, as_of: datetime) -> pa.Table:
    rows = [dict(r._mapping) for r in conn.execute(_ITEMS_SQL, {"as_of": as_of})]
    docs = {d.item_id: d for d in load_item_documents(conn)}
    for r in rows:
        d = docs[r["item_id"]]
        r.update(
            overview=d.overview,
            overview_source=d.overview_source,
            genres=list(d.genres),
            keywords=list(d.keywords),
            directors=list(d.directors),
            cast=list(d.cast),
        )
    schema = pa.schema(
        [
            ("item_id", pa.int64()),
            ("item_type", pa.string()),
            ("title", pa.string()),
            ("original_title", pa.string()),
            ("overview", pa.string()),
            ("overview_source", pa.string()),
            ("release_date", pa.date32()),
            ("release_year", pa.int32()),
            ("original_language", pa.string()),
            ("runtime_minutes", pa.int32()),
            ("status", pa.string()),
            ("popularity", pa.float64()),
            ("vote_average", pa.float64()),
            ("vote_count", pa.int64()),
            ("collection_id", pa.int64()),
            ("collection_name", pa.string()),
            ("poster_path", pa.string()),
            ("backdrop_path", pa.string()),
            ("genres", pa.list_(pa.string())),
            ("keywords", pa.list_(pa.string())),
            ("directors", pa.list_(pa.string())),
            ("cast", pa.list_(pa.string())),
            ("tmdb_id", pa.string()),
            ("imdb_id", pa.string()),
            ("tvmaze_id", pa.string()),
            ("first_seen_at", pa.timestamp("us", tz="UTC")),
            ("last_synced_at", pa.timestamp("us", tz="UTC")),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _embeddings_table(
    conn: Connection, model_version: str, item_ids: set[int]
) -> tuple[pa.Table, int]:
    # select() sobre a tabela: o tipo Vector converte o valor (text() devolveria string).
    rows = conn.execute(
        select(item_embeddings.c.item_id, item_embeddings.c.embedding)
        .where(item_embeddings.c.model_version == model_version)
        .order_by(item_embeddings.c.item_id)
    ).all()
    rows = [(i, np.asarray(v, dtype=np.float32)) for i, v in rows if i in item_ids]
    dim = int(rows[0][1].shape[0]) if rows else 0
    table = pa.table(
        {
            "item_id": pa.array([i for i, _ in rows], pa.int64()),
            "embedding": pa.array([v.tolist() for _, v in rows], pa.list_(pa.float32(), dim)),
        }
    )
    return table, dim


def _hash_table(table: pa.Table) -> str:
    """Hash do conteúdo canônico (linhas já ordenadas), independente de bytes Parquet."""
    h = hashlib.sha256()
    h.update(table.schema.to_string().encode())
    for batch in table.to_batches(max_chunksize=1000):
        for row in batch.to_pylist():
            h.update(json.dumps(row, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None  # repositório sem commits ou git ausente


def create_snapshot(
    engine: Engine,
    gold_dir: Path,
    name: str = DEFAULT_NAME,
    as_of: datetime | None = None,
    embedding_model_version: str | None = None,
    allow_failing_quality: bool = False,
) -> dict[str, Any]:
    as_of = as_of or datetime.now(UTC)
    with engine.connect() as conn:
        quality = conn.execute(
            select(data_quality_runs.c.id, data_quality_runs.c.status, data_quality_runs.c.ran_at)
            .order_by(desc(data_quality_runs.c.id))
            .limit(1)
        ).first()
        if not allow_failing_quality and (quality is None or quality.status == "fail"):
            raise SnapshotRefusedError(
                "último quality check ausente ou com falha; rode `cinematch quality check`"
            )
        items = _items_table(conn, as_of)
        if items.num_rows == 0:
            raise SnapshotRefusedError(f"nenhum item visto até {as_of.isoformat()}")
        mv = (
            embedding_model_version
            or conn.execute(
                text("SELECT model_version FROM item_embeddings ORDER BY created_at DESC LIMIT 1")
            ).scalar()
        )
        emb, dim = (
            _embeddings_table(conn, mv, set(items.column("item_id").to_pylist()))
            if mv
            else (None, 0)
        )
        alembic_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    hashes = {"items": _hash_table(items)}
    if emb is not None:
        hashes["embeddings"] = _hash_table(emb)
    content_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()

    with engine.connect() as conn:
        existing = conn.execute(
            select(training_datasets.c.manifest).where(
                training_datasets.c.name == name, training_datasets.c.content_hash == content_hash
            )
        ).scalar()
    if existing is not None:
        logger.info("gold_snapshot_unchanged", extra={"snapshot_id": existing["snapshot_id"]})
        return {**existing, "reused": True}

    snapshot_id = f"{name}_{as_of.astimezone(UTC):%Y%m%dT%H%M%SZ}_{content_hash[:8]}"
    out = Path(gold_dir).resolve() / name / f"snapshot_id={snapshot_id}"
    out.mkdir(parents=True, exist_ok=False)
    files = {"items.parquet": items}
    if emb is not None:
        files["embeddings.parquet"] = emb
    for fname, table in files.items():
        pq.write_table(table, out / fname, compression="zstd")

    counts = items.group_by("item_type").aggregate([("item_id", "count")]).to_pylist()
    manifest: dict[str, Any] = {
        "name": name,
        "snapshot_id": snapshot_id,
        "as_of": as_of.isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "content_hash": content_hash,
        "table_hashes": hashes,
        "files": {
            f: {"sha256": _file_sha256(out / f), "rows": t.num_rows} for f, t in files.items()
        },
        "rows_by_type": {c["item_type"]: c["item_id_count"] for c in counts},
        "filters": {"first_seen_at_lte": as_of.isoformat()},
        "item_features_point_in_time": False,
        "text_versions": {"embedding": EMBED_TEXT_VERSION, "tfidf": TFIDF_TEXT_VERSION},
        "embedding_model_version": mv,
        "embedding_dimensions": dim,
        "alembic_revision": alembic_rev,
        "git_commit": _git_commit(),
        "cinematch_version": __version__,
        "pyarrow_version": pa.__version__,
        "quality_run": (
            {"id": quality.id, "status": quality.status, "ran_at": quality.ran_at.isoformat()}
            if quality
            else None
        ),
        "sources": {"tmdb": "ver docs/DATA_SOURCES.md (ADR-008)", "tvmaze": "CC BY-SA 4.0"},
    }
    manifest["uri"] = out.as_uri()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    with engine.begin() as conn:
        conn.execute(
            pg_insert(training_datasets).values(
                name=name,
                snapshot_id=snapshot_id,
                as_of=as_of,
                content_hash=content_hash,
                uri=out.as_uri(),
                manifest=manifest,
            )
        )
    logger.info("gold_snapshot_created", extra={"snapshot_id": snapshot_id, "uri": out.as_uri()})
    return {**manifest, "reused": False}
