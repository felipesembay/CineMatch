"""Pipeline de ingestão TMDB: descoberta → extração em lotes → Bronze → upsert.

Garantias:
- Uma execução por fonte por vez (advisory lock no PostgreSQL).
- Checkpoint: páginas de descoberta em `ingestion_runs.state`; status por item em
  `ingestion_run_items`. `resume_run_id` continua de onde parou sem refazer itens `done`.
- Bronze é append-only; upsert + marcação do item ocorrem na mesma transação.
"""

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine

from cinematch.ingestion import repository as repo
from cinematch.ingestion.http import AuthError, HttpError, NotFoundError
from cinematch.ingestion.raw_store import TMDB_LICENSE_NOTES, RawRecord, RawStore
from cinematch.ingestion.tmdb.client import ITEM_TYPES, ItemType, TmdbClient
from cinematch.ingestion.tmdb.normalize import SOURCE, NormalizationError, normalize

logger = logging.getLogger(__name__)

MAX_ITEM_ATTEMPTS = 3


class IngestionError(RuntimeError):
    pass


@dataclass
class RunSummary:
    run_id: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class TmdbIngestion:
    engine: Engine
    client: TmdbClient
    store: RawStore
    batch_size: int = 50
    now: Callable[[], datetime] = _utcnow
    _metrics: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # ------------------------------------------------------------ entrada

    def run(
        self,
        item_types: Sequence[ItemType] = ITEM_TYPES,
        max_items_per_type: int = 50,
        resume_run_id: str | None = None,
        skip_synced_within_days: int | None = None,
    ) -> RunSummary:
        if max_items_per_type < 1:
            raise ValueError("max_items_per_type deve ser >= 1")
        invalid = set(item_types) - set(ITEM_TYPES)
        if invalid:
            raise ValueError(f"tipos inválidos: {sorted(invalid)}")

        started = time.perf_counter()
        with self.engine.connect() as lock_conn:
            if not repo.try_lock_source(lock_conn, SOURCE):
                raise repo.ConcurrentRunError(f"ingestão '{SOURCE}' já em andamento")
            try:
                run_id, params, state = self._start_or_resume(
                    resume_run_id, list(item_types), max_items_per_type, skip_synced_within_days
                )
                try:
                    # Runs de refresh não têm descoberta: os itens vêm do próprio catálogo.
                    discover_types = [] if params.get("mode") == "refresh" else params["item_types"]
                    for item_type in discover_types:
                        self._discover(
                            run_id,
                            item_type,
                            params["max_items_per_type"],
                            state,
                            params.get("skip_synced_within_days"),
                        )
                    self._extract(run_id)
                except BaseException as exc:
                    self._finish(run_id, started, error=exc)
                    raise
                return self._finish(run_id, started)
            finally:
                repo.unlock_source(lock_conn, SOURCE)
                lock_conn.commit()

    def refresh(self, older_than_days: int, max_items: int | None = None) -> RunSummary:
        """Rebusca detalhes de itens sincronizados há mais de `older_than_days` dias.

        Termos TMDB §1.C: nada obtido do TMDB pode ficar em cache por mais de 6 meses.
        Itens que o TMDB não devolve mais (404) ficam `not_found` e são candidatos a expirar
        (`cinematch catalog expire`). Retomável com `run(resume_run_id=...)`.
        """
        if older_than_days < 0:
            raise ValueError("older_than_days deve ser >= 0")
        if max_items is not None and max_items < 1:
            raise ValueError("max_items deve ser >= 1")
        started = time.perf_counter()
        with self.engine.connect() as lock_conn:
            if not repo.try_lock_source(lock_conn, SOURCE):
                raise repo.ConcurrentRunError(f"ingestão '{SOURCE}' já em andamento")
            try:
                run_id = str(uuid.uuid4())
                cutoff = self.now() - timedelta(days=older_than_days)
                with self.engine.begin() as conn:
                    params = {
                        "mode": "refresh",
                        "older_than_days": older_than_days,
                        "max_items": max_items,
                        "cutoff": cutoff.isoformat(),
                        "language": self.client.language,
                    }
                    repo.create_run(conn, run_id, SOURCE, params)
                    stale = repo.stale_items(conn, SOURCE, cutoff, max_items)
                    for item_type in ITEM_TYPES:
                        ids = [i for t, i in stale if t == item_type]
                        repo.add_run_items(conn, run_id, item_type, ids)
                self._metrics["selected_for_refresh"] = len(stale)
                logger.info(
                    "refresh_started", extra={"run_id": run_id, **params, "selected": len(stale)}
                )
                try:
                    self._extract(run_id)
                except BaseException as exc:
                    self._finish(run_id, started, error=exc)
                    raise
                return self._finish(run_id, started)
            finally:
                repo.unlock_source(lock_conn, SOURCE)
                lock_conn.commit()

    # ------------------------------------------------------------ etapas

    def _start_or_resume(
        self,
        resume_run_id: str | None,
        item_types: list[str],
        max_items: int,
        skip_days: int | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        with self.engine.begin() as conn:
            if resume_run_id is None:
                run_id = str(uuid.uuid4())
                params = {
                    "item_types": item_types,
                    "max_items_per_type": max_items,
                    "skip_synced_within_days": skip_days,
                    "language": self.client.language,
                }
                repo.create_run(conn, run_id, SOURCE, params)
                logger.info("ingestion_started", extra={"run_id": run_id, **params})
                return run_id, params, {}

            run = repo.get_run(conn, resume_run_id)
            if run is None or run["source"] != SOURCE:
                raise IngestionError(f"run {resume_run_id} não encontrado para '{SOURCE}'")
            if run["status"] == "succeeded":
                raise IngestionError(f"run {resume_run_id} já concluído com sucesso")
            requeued = repo.requeue_failed(conn, resume_run_id, MAX_ITEM_ATTEMPTS)
            repo.update_run(conn, resume_run_id, status="running", error=None, finished_at=None)
            # Métricas acumulam entre execuções do mesmo run.
            for k, v in (run["metrics"] or {}).items():
                if isinstance(v, int) and k != "duration_s":
                    self._metrics[k] = v
            logger.info("ingestion_resumed", extra={"run_id": resume_run_id, "requeued": requeued})
            return resume_run_id, run["params"], dict(run["state"] or {})

    def _discover(
        self,
        run_id: str,
        item_type: ItemType,
        max_items: int,
        state: dict[str, Any],
        skip_days: int | None = None,
    ) -> None:
        type_state = state.setdefault(item_type, {"last_page": 0, "done": False})
        if type_state["done"]:
            return
        with self.engine.connect() as conn:
            discovered = repo.count_run_items(conn, run_id, item_type)

        page = type_state["last_page"] + 1
        while discovered < max_items:
            result = self.client.discover_page(item_type, page)
            fetched_at = self.now()
            uri = self.store.write_batch(
                [
                    RawRecord(
                        source=SOURCE,
                        entity=f"discover_{item_type}",
                        external_id=None,
                        endpoint=f"/discover/{item_type}",
                        request_params=result.params,
                        status_code=200,
                        fetched_at=fetched_at,
                        run_id=run_id,
                        license_notes=TMDB_LICENSE_NOTES,
                        payload=result.payload,
                    )
                ]
            )
            remaining = max_items - discovered
            ids = [str(i) for i in dict.fromkeys(result.ids)]
            if skip_days is not None:
                # Com skip, `max_items` conta só itens novos/desatualizados.
                with self.engine.connect() as conn:
                    fresh = repo.recently_synced(
                        conn, SOURCE, item_type, ids, self.now() - timedelta(days=skip_days)
                    )
                self._metrics["skipped_fresh"] += sum(1 for i in ids if i in fresh)
                ids = [i for i in ids if i not in fresh]
            ids = ids[:remaining]
            exhausted = page >= result.total_pages or not result.ids
            with self.engine.begin() as conn:
                added = repo.add_run_items(conn, run_id, item_type, ids)
                discovered += added
                type_state["last_page"] = page
                type_state["done"] = exhausted or discovered >= max_items
                repo.update_run(conn, run_id, state=state)
            self._metrics["discovered"] += added
            self._metrics["discover_pages"] += 1
            logger.info(
                "discover_page",
                extra={
                    "run_id": run_id,
                    "item_type": item_type,
                    "page": page,
                    "added": added,
                    "discovered": discovered,
                    "raw_uri": uri,
                },
            )
            if exhausted:
                break
            page += 1
        type_state["done"] = True
        with self.engine.begin() as conn:
            repo.update_run(conn, run_id, state=state)

    def _extract(self, run_id: str) -> None:
        while True:
            with self.engine.connect() as conn:
                batch = repo.pending_items(conn, run_id, self.batch_size)
            if not batch:
                return
            self._process_batch(run_id, batch)

    def _process_batch(self, run_id: str, batch: list[tuple[str, str]]) -> None:
        raws: dict[str, list[RawRecord]] = defaultdict(list)
        normalized: dict[str, list[Any]] = defaultdict(list)
        outcomes: list[tuple[str, str, repo.ItemStatus, str | None]] = []

        for item_type, external_id in batch:
            try:
                payload = self.client.details(item_type, int(external_id))  # type: ignore[arg-type]
                rec = normalize(item_type, payload)  # type: ignore[arg-type]
            except AuthError:
                raise
            except NotFoundError:
                outcomes.append((item_type, external_id, "not_found", "HTTP 404"))
                continue
            except (HttpError, NormalizationError) as exc:
                outcomes.append((item_type, external_id, "failed", str(exc)[:500]))
                logger.warning(
                    "item_failed",
                    extra={
                        "run_id": run_id,
                        "item_type": item_type,
                        "external_id": external_id,
                        "error": str(exc)[:200],
                    },
                )
                continue
            raws[item_type].append(
                RawRecord(
                    source=SOURCE,
                    entity=item_type,
                    external_id=external_id,
                    endpoint=f"/{item_type}/{external_id}",
                    request_params=self.client.details_params(),
                    status_code=200,
                    fetched_at=self.now(),
                    run_id=run_id,
                    license_notes=TMDB_LICENSE_NOTES,
                    payload=payload,
                )
            )
            normalized[item_type].append(rec)
            outcomes.append((item_type, external_id, "done", None))

        # Bronze primeiro: se o processo cair antes do commit, o item segue `pending`
        # e é refeito no resume (Bronze aceita duplicatas; o upsert é idempotente).
        uris = {t: self.store.write_batch(recs) for t, recs in raws.items()}

        with self.engine.begin() as conn:
            for item_type, records in normalized.items():
                prov = repo.Provenance(run_id=run_id, raw_uri=uris[item_type], synced_at=self.now())
                for outcome, n in repo.upsert_items(conn, records, prov).items():
                    self._metrics[outcome] += n
            for item_type, external_id, status, error in outcomes:
                repo.mark_item(conn, run_id, item_type, external_id, status, error)
                self._metrics["fetched" if status == "done" else status] += 1
            repo.update_run(conn, run_id, metrics=self._snapshot_metrics())
        logger.info(
            "batch_done",
            extra={
                "run_id": run_id,
                "size": len(batch),
                "done": sum(1 for o in outcomes if o[2] == "done"),
            },
        )

    # ------------------------------------------------------------ fechamento

    def _snapshot_metrics(self, duration_s: float | None = None) -> dict[str, Any]:
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
        metrics = self._snapshot_metrics(time.perf_counter() - started)
        with self.engine.begin() as conn:
            counts = repo.count_by_status(conn, run_id)
            if error is not None:
                status = "failed"
            elif counts.get("failed", 0) > 0:
                status = "partial"
            else:
                status = "succeeded"
            metrics["items_by_status"] = counts
            repo.update_run(
                conn,
                run_id,
                status=status,
                metrics=metrics,
                finished_at=self.now(),
                error=f"{type(error).__name__}: {str(error)[:500]}" if error else None,
            )
        log = logger.error if error else logger.info
        log("ingestion_finished", extra={"run_id": run_id, "status": status, **metrics})
        return RunSummary(run_id=run_id, status=status, metrics=metrics)
