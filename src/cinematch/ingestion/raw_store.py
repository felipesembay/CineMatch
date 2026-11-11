"""Camada Bronze: respostas brutas em JSONL.gz com proveniência (ADR D-102).

Layout de chaves compatível com S3/MinIO (backend futuro):
    source=<s>/entity=<e>/dt=YYYY-MM-DD/run_id=<id>/part-<n>.jsonl.gz

Cada linha é um envelope:
    {source, entity, external_id, endpoint, request_params, status_code,
     fetched_at, run_id, license_notes, payload}
"""

import gzip
import json
import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)

TMDB_LICENSE_NOTES = (
    "TMDB API Terms of Use (rev. 2023-10-20): non-commercial license; attribution required; "
    "cache max 6 months (s1.C); ML/AI use restricted (s1.A/s1.C) - see docs/DATA_SOURCES.md"
)


@dataclass(frozen=True)
class RawRecord:
    source: str
    entity: str
    external_id: str | None
    endpoint: str
    request_params: dict[str, Any]
    status_code: int
    fetched_at: datetime
    run_id: str
    license_notes: str
    payload: Any

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "entity": self.entity,
                "external_id": self.external_id,
                "endpoint": self.endpoint,
                "request_params": self.request_params,
                "status_code": self.status_code,
                "fetched_at": self.fetched_at.isoformat(),
                "run_id": self.run_id,
                "license_notes": self.license_notes,
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


class RawStore(Protocol):
    def write_batch(self, records: list[RawRecord]) -> str: ...


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        # Absoluto: URIs file:// exigem caminho absoluto e não dependem do cwd.
        self.root = Path(root).resolve()

    def _partition(self, rec: RawRecord) -> Path:
        dt = rec.fetched_at.astimezone(UTC).date().isoformat()
        return (
            self.root
            / f"source={rec.source}"
            / f"entity={rec.entity}"
            / f"dt={dt}"
            / f"run_id={rec.run_id}"
        )

    def write_batch(self, records: list[RawRecord]) -> str:
        """Grava um lote (mesmo source/entity/run) atomicamente; retorna URI do arquivo."""
        if not records:
            raise ValueError("lote vazio")
        first = records[0]
        if any(
            (r.source, r.entity, r.run_id) != (first.source, first.entity, first.run_id)
            for r in records
        ):
            raise ValueError("lote deve ter mesmo source/entity/run_id")
        directory = self._partition(first)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = first.fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
        final = directory / f"part-{stamp}-{uuid4().hex[:8]}.jsonl.gz"
        tmp = final.with_suffix(final.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            for rec in records:
                fh.write(rec.to_json())
                fh.write("\n")
        os.replace(tmp, final)  # rename atômico no mesmo filesystem
        return final.as_uri()


def read_records(path: Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def purge_older_than(
    root: Path, days: int, now: datetime | None = None, source: str = "tmdb"
) -> list[Path]:
    """Remove partições `dt=` de `source` mais antigas que `days` (retenção TMDB: <= 180 dias)."""
    if days < 0:
        raise ValueError("days deve ser >= 0")
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).date()
    removed: list[Path] = []
    for part in sorted(root.glob(f"source={source}/entity=*/dt=*")):
        try:
            dt = datetime.strptime(part.name.removeprefix("dt="), "%Y-%m-%d").date()
        except ValueError:
            logger.warning("bronze_partition_invalid", extra={"path": str(part)})
            continue
        if dt < cutoff:
            shutil.rmtree(part)
            removed.append(part)
    logger.info("bronze_purged", extra={"partitions": len(removed), "cutoff": cutoff.isoformat()})
    return removed
