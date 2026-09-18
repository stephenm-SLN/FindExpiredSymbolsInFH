"""In-memory job store for scan lifecycle tracking.

Design:

- One :class:`Job` per POST /scans; identified by a UUID4 string.
- All state lives in a single ``dict[str, Job]`` protected by a
  :class:`threading.RLock`. Scans run in a bounded ``ThreadPoolExecutor``
  (see :mod:`.workers`), so writes and reads may happen from any thread.
- Completed jobs (state ``done`` or ``failed``) are evicted once their
  ``completed_at`` is older than the configured TTL (default 1 hour).
- Server restart drops everything — deliberate; no DB in scope.

The store is a plain object (not a global) so tests can spin up
independent instances and the FastAPI app can be configured with
different TTLs per test.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from ..pipeline import ScanProgress

logger = logging.getLogger(__name__)


# JobState mirrors api.models.JobState but is declared here as a plain
# string to keep this module Pydantic-free (jobs.py is called from
# worker threads; keeping the type surface minimal avoids surprises).
JobState = str  # "queued" | "running" | "done" | "failed"


@dataclass
class Job:
    """One scan lifecycle record.

    Attributes are mutated by the worker via the store's atomic helpers;
    callers should treat instances as read-only snapshots and only touch
    them through :class:`JobStore`.
    """

    id: str
    state: JobState
    filters: dict[str, Any]
    progress: ScanProgress
    created_at: datetime
    completed_at: datetime | None = None
    result_json: list[dict[str, Any]] | None = None
    error: str | None = None


class JobStore:
    """Thread-safe map of ``job_id -> Job`` with a TTL sweep helper.

    Not a Singleton — the FastAPI app builds one at startup and passes it
    to the workers. Tests instantiate their own.
    """

    def __init__(self, *, job_ttl_seconds: float = 3600.0) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._ttl = float(job_ttl_seconds)

    # ------------------------------------------------------------------
    # Lifecycle helpers — each takes the lock, mutates atomically.
    # ------------------------------------------------------------------

    def create(self, *, filters: dict[str, Any]) -> Job:
        """Register a new job in the ``queued`` state and return it."""
        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            state="queued",
            filters=filters,
            progress=ScanProgress(phase="querying_db"),
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        """Snapshot of all live jobs, newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def mark_running(self, job_id: str) -> None:
        self._patch(job_id, state="running")

    def update_progress(self, job_id: str, progress: ScanProgress) -> None:
        """Called by the worker on every pipeline progress tick.

        Cheap and lock-guarded; safe to call from worker threads.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # Job was swept while running — race we accept: the caller
                # is a background thread and there's no user-visible URL
                # left, so drop the tick.
                return
            self._jobs[job_id] = replace(job, progress=progress)

    def mark_done(
        self, job_id: str, *, result_json: list[dict[str, Any]]
    ) -> None:
        self._patch(
            job_id,
            state="done",
            result_json=result_json,
            completed_at=datetime.now(timezone.utc),
        )

    def mark_failed(self, job_id: str, *, error: str) -> None:
        self._patch(
            job_id,
            state="failed",
            error=error,
            completed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # TTL sweep — dropped from the outside on a schedule (server.py wires
    # this into a background asyncio task).
    # ------------------------------------------------------------------

    def sweep(self, *, now: datetime | None = None) -> int:
        """Evict completed jobs whose ``completed_at`` is older than the TTL.

        Returns the number evicted. Called on a fixed interval by the
        server; also exposed directly so tests can force a sweep.
        """
        cutoff = (now or datetime.now(timezone.utc)).timestamp() - self._ttl
        evicted = 0
        with self._lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.completed_at is not None
                and job.completed_at.timestamp() < cutoff
            ]
            for job_id in stale:
                del self._jobs[job_id]
                evicted += 1
        if evicted:
            logger.info("job TTL sweep: evicted %d completed job(s)", evicted)
        return evicted

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values() if j.state in ("queued", "running")
            )

    def recent_count(self) -> int:
        with self._lock:
            return len(self._jobs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _patch(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                logger.warning("attempted to update unknown job %s", job_id)
                return
            self._jobs[job_id] = replace(job, **fields)


__all__ = ["Job", "JobState", "JobStore"]
