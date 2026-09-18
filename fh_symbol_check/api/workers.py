"""Bounded thread pool that runs scans against :func:`fh_symbol_check.pipeline.run_scan`.

One instance is created at server startup and lives for the lifetime of the
process. Submission is fire-and-forget from the caller's perspective — the
API returns the queued :class:`Job` immediately; state and results land in
the shared :class:`JobStore` as the scan progresses.

Bounded concurrency (``max_concurrent_scans``) is a hard ceiling on how
many scans run in parallel. Excess submissions queue behind the running
ones — their :class:`Job` stays in the ``queued`` state until a worker
picks them up.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from ..creds import DBCreds
from ..pipeline import ScanFilters, ScanProgress, describe_filters, run_scan
from .jobs import Job, JobStore

logger = logging.getLogger(__name__)


class ScanWorkers:
    """Coordinator that turns :class:`ScanFilters` submissions into
    :class:`JobStore` updates.

    Not tied to FastAPI — plain Python — so tests can drive it directly.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        creds: DBCreds,
        exchange_map: dict[str, str],
        max_concurrent_scans: int = 2,
    ) -> None:
        self._store = store
        self._creds = creds
        self._exchange_map = exchange_map
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_concurrent_scans),
            thread_name_prefix="scan-worker",
        )

    def submit(self, filters: ScanFilters, request_dump: dict[str, Any]) -> Job:
        """Create a :class:`Job`, enqueue it on the pool, return the record.

        ``request_dump`` is the JSON-safe form of the caller's original
        request (typically ``ScanRequest.model_dump(mode="json")``) — we
        stash it on the Job so the UI and API can echo it back verbatim
        without reconstructing.
        """
        # Defensive re-validate; the Pydantic layer already ran, but this
        # guards direct in-process callers (tests, future internal uses)
        # against silent bad state.
        filters.validate()

        job = self._store.create(filters=request_dump)
        logger.info(
            "scan job %s queued: %s", job.id, describe_filters(filters)
        )
        self._executor.submit(self._run_job, job.id, filters)
        return job

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop the pool. FastAPI's lifespan calls this at shutdown."""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    # ------------------------------------------------------------------
    # Internal — runs on a pool thread
    # ------------------------------------------------------------------

    def _run_job(self, job_id: str, filters: ScanFilters) -> None:
        self._store.mark_running(job_id)
        logger.info("scan job %s started", job_id)

        def _progress(p: ScanProgress) -> None:
            self._store.update_progress(job_id, p)

        try:
            results = run_scan(
                filters,
                self._creds,
                self._exchange_map,
                on_progress=_progress,
            )
        except Exception as e:  # broad on purpose: any pipeline failure is a job failure
            logger.exception("scan job %s failed", job_id)
            self._store.mark_failed(job_id, error=f"{type(e).__name__}: {e}")
            return

        result_json = [asdict(r) for r in results]
        self._store.mark_done(job_id, result_json=result_json)
        logger.info("scan job %s done (%d row(s))", job_id, len(result_json))


__all__ = ["ScanWorkers"]
