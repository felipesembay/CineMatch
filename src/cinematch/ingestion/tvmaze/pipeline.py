"""Ingestão TVmaze: varredura do índice `/shows?page=N` → Bronze → `tvmaze_shows` → vínculos.

- Checkpoint: `ingestion_runs.state = {"next_page": n, "done": bool}`, gravado na mesma
  transação do upsert da página. `resume_run_id` continua da próxima página.
- Cada execução sem resume varre de `start_page` até 404 (ou `max_pages`). O upsert por
  `content_hash` torna a re-varredura idempotente. O índice é cacheado pelo TVmaze por 24 h.
- Ao final, recalcula os vínculos com o catálogo (ver `tvmaze.repository.link_shows`).
"""

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine

from cinematch.ingestion import repository as runs
from cinematch.ingestion.pipeline import IngestionError, RunSummary
from cinematch.ingestion.raw_store import RawRecord, RawStore
from cinematch.ingestion.tvmaze import repository as repo
from cinematch.ingestion.tvmaze.client import TvmazeClient
from cinematch.ingestion.tvmaze.normalize import SOURCE, NormalizationError, normalize_show

logger = logging.getLogger(__name__)

TVMAZE_LICENSE_NOTES = (
    "TVmaze API, CC BY-SA 4.0 (https://www.tvmaze.com/api): attribution by link-back required; "
    "derivatives under ShareAlike - see docs/DATA_SOURCES.md"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class TvmazeIngestion:
    engine: Engine
    client: TvmazeClient
    store: RawStore
    now: Callable[[], datetime] = _utcnow
    _metrics: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def run(
        self,
        max_pages: int | None = None,
        start_page: int = 0,
        resume_run_id: str | None = None,
    ) -> RunSummary:
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages deve ser >= 1")
        started = time.perf_counter()
        with self.engine.connect() as lock_conn:
            if not runs.try_lock_source(lock_conn, SOURCE):
                raise runs.ConcurrentRunError(f"ingestão '{SOURCE}' já em andamento")
            try:
                run_id, state, max_pages = self._start_or_resume(
                    resume_run_id, max_pages, start_page
                )
                try:
                    self._crawl(run_id, state, max_pages)
                    self._link(run_id)
                except BaseException as exc:
                    self._finish(run_id, started, exc)
                    raise
                return self._finish(run_id, started)
            finally:
                runs.unlock_source(lock_conn, SOURCE)
                lock_conn.commit()

    def _start_or_resume(
        self, resume_run_id: str | None, max_pages: int | None, start_page: int
    ) -> tuple[str, dict[str, Any], int | None]:
        with self.engine.begin() as conn:
            if resume_run_id is None:
                run_id = str(uuid.uuid4())
                params = {"max_pages": max_pages, "start_page": start_page}
                runs.create_run(conn, run_id, SOURCE, params)
                state = {"next_page": start_page, "done": False, "pages_done": 0}
                runs.update_run(conn, run_id, state=state)
                logger.info("tvmaze_ingestion_started", extra={"run_id": run_id, **params})
                return run_id, state, max_pages
            run = runs.get_run(conn, resume_run_id)
            if run is None or run["source"] != SOURCE:
                raise IngestionError(f"run {resume_run_id} não encontrado para '{SOURCE}'")
            if run["status"] == "succeeded":
                raise IngestionError(f"run {resume_run_id} já concluído com sucesso")
            runs.update_run(conn, resume_run_id, status="running", error=None, finished_at=None)
            for k, v in (run["metrics"] or {}).items():
                if isinstance(v, int) and not k.startswith("http_"):
                    self._metrics[k] = v
            logger.info("tvmaze_ingestion_resumed", extra={"run_id": resume_run_id})
            return resume_run_id, dict(run["state"]), run["params"].get("max_pages")

    def _crawl(self, run_id: str, state: dict[str, Any], max_pages: int | None) -> None:
        while not state["done"]:
            if max_pages is not None and state["pages_done"] >= max_pages:
                return
            page = state["next_page"]
            shows = self.client.index_page(page)
            if shows is None:
                state["done"] = True
                with self.engine.begin() as conn:
                    runs.update_run(conn, run_id, state=state)
                logger.info("tvmaze_index_end", extra={"run_id": run_id, "page": page})
                return
            self._process_page(run_id, page, shows, state)

    def _process_page(
        self, run_id: str, page: int, shows: list[dict[str, Any]], state: dict[str, Any]
    ) -> None:
        fetched_at = self.now()
        uri = self.store.write_batch(
            [
                RawRecord(
                    source=SOURCE,
                    entity="show_index",
                    external_id=str(page),
                    endpoint="/shows",
                    request_params={"page": page},
                    status_code=200,
                    fetched_at=fetched_at,
                    run_id=run_id,
                    license_notes=TVMAZE_LICENSE_NOTES,
                    payload=shows,
                )
            ]
        )
        records = []
        for payload in shows:
            try:
                records.append(normalize_show(payload))
            except NormalizationError as exc:
                self._metrics["invalid_shows"] += 1
                logger.warning("tvmaze_show_invalid", extra={"run_id": run_id, "error": str(exc)})

        with self.engine.begin() as conn:
            counts = repo.upsert_shows(
                conn, records, repo.Provenance(run_id=run_id, raw_uri=uri, synced_at=fetched_at)
            )
            for k, v in counts.items():
                self._metrics[k] += v
            self._metrics["pages"] += 1
            state["next_page"] = page + 1
            state["pages_done"] += 1
            runs.update_run(conn, run_id, state=state, metrics=self._snapshot())
        logger.info(
            "tvmaze_page_done",
            extra={"run_id": run_id, "page": page, "shows": len(shows), **counts},
        )

    def _link(self, run_id: str) -> None:
        with self.engine.begin() as conn:
            stats = link_catalog(conn, run_id, self.now())
        for k, v in stats.items():
            self._metrics[f"link_{k}"] = v

    def _snapshot(self, duration_s: float | None = None) -> dict[str, Any]:
        stats = self.client.http.stats
        data: dict[str, Any] = dict(self._metrics)
        data.update(
            http_requests=stats.requests,
            http_retries=stats.retries,
            http_rate_limited=stats.rate_limited,
        )
        if duration_s is not None:
            data["duration_s"] = round(duration_s, 2)
        return data

    def _finish(
        self, run_id: str, started: float, error: BaseException | None = None
    ) -> RunSummary:
        metrics = self._snapshot(time.perf_counter() - started)
        status = "failed" if error else "succeeded"
        with self.engine.begin() as conn:
            runs.update_run(
                conn,
                run_id,
                status=status,
                metrics=metrics,
                finished_at=self.now(),
                error=f"{type(error).__name__}: {str(error)[:500]}" if error else None,
            )
        log = logger.error if error else logger.info
        log("tvmaze_ingestion_finished", extra={"run_id": run_id, "status": status, **metrics})
        return RunSummary(run_id=run_id, status=status, metrics=metrics)


def link_catalog(conn: Connection, run_id: str | None, now: datetime) -> dict[str, int]:
    """Recalcula vínculos TVmaze→catálogo (também usado por `cinematch link tvmaze`)."""
    prov = repo.Provenance(run_id=run_id, raw_uri=None, synced_at=now)
    return repo.link_shows(conn, prov)
