"""Checks de qualidade de dados do catálogo e da camada Bronze.

Status por check: `pass`, `warn`, `fail` ou `info` (informativo, não afeta o resultado).
O resultado agregado (pior status, exceto `info`) é gravado em `data_quality_runs`, o que
permite comparar com a execução anterior (queda de contagem, mudança de schema da fonte).
"""

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, Engine, desc, insert, select, text

from cinematch.data.schema import data_quality_runs
from cinematch.ingestion.raw_store import read_records

logger = logging.getLogger(__name__)

Status = Literal["pass", "warn", "fail", "info"]
_RANK = {"pass": 0, "info": 0, "warn": 1, "fail": 2}

TMDB_MAX_AGE_DAYS = 180
TMDB_REFRESH_WARN_DAYS = 150
MAX_COUNT_DROP = 0.05

# (campo, aviso abaixo de, falha abaixo de) — frações de cobertura por tipo de item.
COVERAGE_THRESHOLDS: dict[str, tuple[float, float]] = {
    "overview": (0.95, 0.85),
    "release_year": (0.98, 0.90),
    "runtime": (0.90, 0.75),
    "genres": (0.97, 0.90),
    "keywords": (0.70, 0.40),
    "cast_members": (0.80, 0.50),
    "imdb_id": (0.90, 0.75),
}

# Chaves de topo do payload das quais a normalização depende.
REQUIRED_KEYS: dict[str, set[str]] = {
    "tmdb/movie": {"id", "title", "overview", "release_date", "genres", "runtime"},
    "tmdb/tv": {"id", "name", "overview", "first_air_date", "genres", "episode_run_time"},
}
SCHEMA_SAMPLE_FILES = 3


@dataclass
class CheckResult:
    name: str
    status: Status
    value: Any = None
    threshold: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _status(value: float, warn_below: float, fail_below: float) -> Status:
    if value < fail_below:
        return "fail"
    if value < warn_below:
        return "warn"
    return "pass"


# ------------------------------------------------------------ checks de banco


def check_not_empty(conn: Connection) -> list[CheckResult]:
    rows = dict(
        conn.execute(text("SELECT item_type, count(*) FROM catalog_items GROUP BY 1")).all()
    )
    total = sum(rows.values())
    return [CheckResult("catalog_not_empty", "pass" if total else "fail", total, "> 0", rows)]


def check_keys(conn: Connection) -> list[CheckResult]:
    no_primary = conn.execute(
        text("""
        SELECT count(*) FROM catalog_items c WHERE NOT EXISTS (
          SELECT 1 FROM item_external_ids e WHERE e.item_id = c.id AND e.match_method IS NULL)
    """)
    ).scalar_one()
    blank_title = conn.execute(
        text("SELECT count(*) FROM catalog_items WHERE btrim(title) = ''")
    ).scalar_one()
    no_provenance = conn.execute(
        text("""
        SELECT count(*) FROM item_external_ids
        WHERE match_method IS NULL AND (raw_uri IS NULL OR last_run_id IS NULL)
    """)
    ).scalar_one()
    return [
        CheckResult(
            "items_have_primary_source_id", "fail" if no_primary else "pass", no_primary, "= 0"
        ),
        CheckResult("title_not_blank", "fail" if blank_title else "pass", blank_title, "= 0"),
        CheckResult(
            "primary_ids_have_provenance", "fail" if no_provenance else "pass", no_provenance, "= 0"
        ),
    ]


_COVERAGE_SQL = text("""
SELECT c.item_type,
  count(*) AS n,
  count(c.overview) AS overview,
  count(c.release_year) AS release_year,
  count(c.runtime_minutes) AS runtime,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_genres g WHERE g.item_id = c.id)) AS genres,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_keywords k WHERE k.item_id = c.id))
    AS keywords,
  count(*) FILTER (WHERE EXISTS (
    SELECT 1 FROM item_credits r WHERE r.item_id = c.id AND r.credit_type = 'cast'))
    AS cast_members,
  count(*) FILTER (WHERE EXISTS (
    SELECT 1 FROM item_external_ids e WHERE e.item_id = c.id AND e.source = 'imdb')) AS imdb_id
FROM catalog_items c GROUP BY c.item_type ORDER BY c.item_type
""")


def check_coverage(conn: Connection) -> list[CheckResult]:
    out: list[CheckResult] = []
    for row in conn.execute(_COVERAGE_SQL).mappings():
        n = row["n"]
        for fld, (warn_below, fail_below) in COVERAGE_THRESHOLDS.items():
            frac = row[fld] / n if n else 0.0
            out.append(
                CheckResult(
                    f"coverage.{row['item_type']}.{fld}",
                    _status(frac, warn_below, fail_below),
                    round(frac, 4),
                    f"warn < {warn_below}, fail < {fail_below}",
                    {"present": row[fld], "total": n},
                )
            )
    return out


def check_tmdb_freshness(conn: Connection, now: datetime) -> list[CheckResult]:
    """Termos TMDB §1.C: nada obtido do TMDB pode ficar em cache por mais de 6 meses."""
    row = conn.execute(
        text("""
        SELECT count(*) FILTER (WHERE last_synced_at < :expired) AS expired,
               count(*) FILTER (WHERE last_synced_at < :warn) AS due,
               min(last_synced_at) AS oldest
        FROM item_external_ids WHERE source = 'tmdb'
        """),
        {
            "expired": now - timedelta(days=TMDB_MAX_AGE_DAYS),
            "warn": now - timedelta(days=TMDB_REFRESH_WARN_DAYS),
        },
    ).one()
    status: Status = "fail" if row.expired else ("warn" if row.due else "pass")
    return [
        CheckResult(
            "tmdb_sync_age",
            status,
            row.expired,
            f"0 itens > {TMDB_MAX_AGE_DAYS} d; aviso > {TMDB_REFRESH_WARN_DAYS} d",
            {"due_for_refresh": row.due, "oldest_sync": str(row.oldest)},
        )
    ]


def check_count_drop(conn: Connection, current_total: int) -> list[CheckResult]:
    prev = conn.execute(
        select(data_quality_runs.c.summary).order_by(desc(data_quality_runs.c.id)).limit(1)
    ).scalar()
    if not prev or "catalog_items" not in prev:
        return [
            CheckResult(
                "catalog_count_drop",
                "pass",
                None,
                f"queda <= {MAX_COUNT_DROP:.0%}",
                {"previous": None},
            )
        ]
    previous = int(prev["catalog_items"])
    drop = (previous - current_total) / previous if previous else 0.0
    return [
        CheckResult(
            "catalog_count_drop",
            "fail" if drop > MAX_COUNT_DROP else "pass",
            round(drop, 4),
            f"queda <= {MAX_COUNT_DROP:.0%}",
            {"previous": previous, "current": current_total},
        )
    ]


def check_informational(conn: Connection, today: date) -> list[CheckResult]:
    unreleased = dict(
        conn.execute(
            text(
                "SELECT item_type, count(*) FROM catalog_items WHERE release_date > :d GROUP BY 1"
            ),
            {"d": today},
        ).all()
    )
    low_votes = dict(
        conn.execute(
            text(
                "SELECT item_type, count(*) FROM catalog_items "
                "WHERE coalesce(vote_count, 0) < 10 GROUP BY 1"
            )
        ).all()
    )
    tv = conn.execute(
        text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM item_external_ids e
                 WHERE e.item_id = c.id AND e.source = 'tvmaze')) AS linked
        FROM catalog_items c WHERE c.item_type = 'tv'
    """)
    ).one()
    return [
        CheckResult("unreleased_items", "info", sum(unreleased.values()), None, unreleased),
        CheckResult("items_with_lt_10_votes", "info", sum(low_votes.values()), None, low_votes),
        CheckResult(
            "tvmaze_link_rate",
            "info",
            round(tv.linked / tv.total, 4) if tv.total else None,
            None,
            {"linked": tv.linked, "tv_items": tv.total},
        ),
    ]


def check_embeddings(conn: Connection) -> list[CheckResult]:
    """Para cada `model_version` com o template de texto atual: cobertura e vetores
    desatualizados (texto do item mudou desde o encoding)."""
    from cinematch.features.text import EMBED_TEXT_BUILDERS, load_item_documents, text_hash

    versions = conn.execute(
        text("""
        SELECT model_version, text_version, count(*) AS n, min(dimensions) AS dmin,
               max(dimensions) AS dmax
        FROM item_embeddings GROUP BY 1, 2 ORDER BY 1
    """)
    ).all()
    if not versions:
        return [CheckResult("embeddings", "info", 0, None, {"note": "nenhum embedding"})]
    docs = load_item_documents(conn)
    out: list[CheckResult] = []
    for v in versions:
        name = f"embeddings[{v.model_version}]"
        builder = EMBED_TEXT_BUILDERS.get(v.text_version)
        if builder is None:
            out.append(CheckResult(name, "info", v.n, None, {"note": "template desconhecido"}))
            continue
        current = {d.item_id: text_hash(builder(d)) for d in docs}
        stored = dict(
            conn.execute(
                text("SELECT item_id, text_hash FROM item_embeddings WHERE model_version = :mv"),
                {"mv": v.model_version},
            ).all()
        )
        missing = len(set(current) - set(stored))
        stale = sum(1 for i, h in stored.items() if current.get(i) not in (None, h))
        status: Status = "fail" if v.dmin != v.dmax else ("warn" if missing or stale else "pass")
        out.append(
            CheckResult(
                name,
                status,
                round(len(stored) / len(current), 4) if current else None,
                "100% dos itens, 0 desatualizados, dimensão única",
                {"missing": missing, "stale": stale, "dimensions": v.dmax},
            )
        )
    return out


# ------------------------------------------------------------ checks de Bronze


def _latest_files(directory: Path, n: int) -> list[Path]:
    files = [p for p in directory.rglob("*.jsonl.gz")]
    return sorted(files, key=os.path.getmtime)[-n:]


def observed_schema(bronze_root: Path) -> dict[str, list[str]]:
    """Chaves de topo observadas nos arquivos Bronze mais recentes de cada entidade."""
    out: dict[str, list[str]] = {}
    for key in REQUIRED_KEYS:
        source, entity = key.split("/")
        keys: set[str] = set()
        for path in _latest_files(
            bronze_root / f"source={source}" / f"entity={entity}", SCHEMA_SAMPLE_FILES
        ):
            for rec in read_records(path):
                if isinstance(rec.get("payload"), dict):
                    keys |= set(rec["payload"])
        if keys:
            out[key] = sorted(keys)
    return out


def check_bronze(
    bronze_root: Path, previous_schema: dict[str, list[str]] | None, now: datetime
) -> tuple[list[CheckResult], dict[str, list[str]]]:
    results: list[CheckResult] = []
    schema = observed_schema(bronze_root)
    for key, required in REQUIRED_KEYS.items():
        seen = set(schema.get(key, []))
        if not seen:
            results.append(
                CheckResult(
                    f"bronze_schema.{key}",
                    "info",
                    None,
                    None,
                    {"note": "sem arquivos para amostrar"},
                )
            )
            continue
        missing = sorted(required - seen)
        prev = set((previous_schema or {}).get(key, []))
        added, removed = (sorted(seen - prev), sorted(prev - seen)) if prev else ([], [])
        status: Status = "fail" if missing else ("warn" if added or removed else "pass")
        results.append(
            CheckResult(
                f"bronze_schema.{key}",
                status,
                len(seen),
                "chaves exigidas presentes",
                {"missing_required": missing, "added": added, "removed": removed},
            )
        )

    cutoff = (now - timedelta(days=TMDB_MAX_AGE_DAYS)).date()
    old = []
    for part in (bronze_root / "source=tmdb").glob("entity=*/dt=*"):
        try:
            if datetime.strptime(part.name[3:], "%Y-%m-%d").date() < cutoff:
                old.append(str(part.relative_to(bronze_root)))
        except ValueError:
            continue
    results.append(
        CheckResult(
            "bronze_tmdb_retention",
            "fail" if old else "pass",
            len(old),
            f"0 partições > {TMDB_MAX_AGE_DAYS} d",
            {"partitions": old[:20]},
        )
    )
    return results, schema


# ------------------------------------------------------------ execução


@dataclass
class QualityReport:
    status: Literal["pass", "warn", "fail"]
    summary: dict[str, Any]
    results: list[CheckResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "results": [asdict(r) for r in self.results],
        }


def overall(results: Iterable[CheckResult]) -> Literal["pass", "warn", "fail"]:
    worst = max((_RANK[r.status] for r in results), default=0)
    return ("pass", "warn", "fail")[worst]  # type: ignore[return-value]


def run_checks(
    engine: Engine,
    bronze_root: Path | None,
    save: bool = True,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> QualityReport:
    ts = now()
    with engine.begin() as conn:
        prev_summary = (
            conn.execute(
                select(data_quality_runs.c.summary).order_by(desc(data_quality_runs.c.id)).limit(1)
            ).scalar()
            or {}
        )
        results: list[CheckResult] = []
        results += (not_empty := check_not_empty(conn))
        total = int(not_empty[0].value or 0)
        results += check_keys(conn)
        results += check_coverage(conn)
        results += check_tmdb_freshness(conn, ts)
        results += check_count_drop(conn, total)
        results += check_informational(conn, ts.date())
        results += check_embeddings(conn)
        schema: dict[str, list[str]] = {}
        if bronze_root is not None:
            bronze_results, schema = check_bronze(
                Path(bronze_root), prev_summary.get("bronze_schema"), ts
            )
            results += bronze_results

        status = overall(results)
        counts = {s: sum(1 for r in results if r.status == s) for s in _RANK}
        summary = {"catalog_items": total, "checks": counts, "bronze_schema": schema}
        if save:
            conn.execute(
                insert(data_quality_runs).values(
                    ran_at=ts,
                    status=status,
                    summary=summary,
                    results=[asdict(r) for r in results],
                )
            )
    logger.info("quality_check_done", extra={"status": status, **counts})
    return QualityReport(status, summary, results)
