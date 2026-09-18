"""HTTP routes for the JSON API.

HTML views (the HTMX-driven UI) live in :mod:`.views` and are wired in on
top of these. The split lets the API and UI evolve independently and lets
machine consumers pin against just the JSON surface.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from .jobs import Job, JobStore
from .models import (
    HealthResponse,
    ProgressOut,
    ScanJobDetail,
    ScanJobList,
    ScanJobSummary,
    ScanRequest,
    SymbolResultOut,
)
from .workers import ScanWorkers

logger = logging.getLogger(__name__)

api = APIRouter(tags=["scans"])


def _package_version() -> str:
    try:
        return version("find-expired-symbols")
    except PackageNotFoundError:
        return "0.0.0+unknown"


# ---------------------------------------------------------------------------
# Adapters between the domain Job and the API's JobSummary/JobDetail models
# ---------------------------------------------------------------------------


def _job_to_summary(job: Job) -> ScanJobSummary:
    return ScanJobSummary(
        id=job.id,
        state=job.state,  # type: ignore[arg-type]
        filters=job.filters,
        progress=ProgressOut.from_progress(job.progress),
        created_at=job.created_at,
        completed_at=job.completed_at,
        error=job.error,
    )


def _job_to_detail(job: Job) -> ScanJobDetail:
    result: list[SymbolResultOut] | None
    if job.state == "done" and job.result_json is not None:
        result = [SymbolResultOut(**row) for row in job.result_json]
    else:
        result = None
    return ScanJobDetail(
        id=job.id,
        state=job.state,  # type: ignore[arg-type]
        filters=job.filters,
        progress=ProgressOut.from_progress(job.progress),
        created_at=job.created_at,
        completed_at=job.completed_at,
        error=job.error,
        result=result,
    )


def _get_store(request: Request) -> JobStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=500, detail="job store not configured on app.state.store"
        )
    return store


def _get_workers(request: Request) -> ScanWorkers:
    workers = getattr(request.app.state, "workers", None)
    if workers is None:
        raise HTTPException(
            status_code=500,
            detail="workers not configured on app.state.workers",
        )
    return workers


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@api.get("/health", response_model=HealthResponse, tags=["meta"])
def health(request: Request) -> HealthResponse:
    store = _get_store(request)
    return HealthResponse(
        status="ok",
        version=_package_version(),
        active_scans=store.active_count(),
        recent_scans=store.recent_count(),
    )


@api.post(
    "/scans",
    response_model=ScanJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_scan(
    body: ScanRequest, request: Request, response: Response
) -> ScanJobSummary:
    """Submit a scan. Returns 202 + a job summary. Poll ``GET /scans/{id}``
    for status; result rows appear once ``state == "done"``.
    """
    workers = _get_workers(request)
    filters = body.to_filters()
    # Pydantic already validated on the wire, but the pipeline's own
    # validator is the source of truth — call through both so a future
    # divergence between the two surfaces the same error message twice
    # rather than shipping a broken scan.
    filters.validate()

    dump: dict[str, Any] = body.model_dump(mode="json", by_alias=True)
    job = workers.submit(filters, dump)
    response.headers["Location"] = f"/scans/{job.id}"
    return _job_to_summary(job)


@api.get("/scans", response_model=ScanJobList)
def list_scans(request: Request) -> ScanJobList:
    store = _get_store(request)
    jobs = store.list_all()
    return ScanJobList(jobs=[_job_to_summary(j) for j in jobs])


@api.get(
    "/scans/{job_id}",
    response_model=ScanJobDetail,
    responses={404: {"description": "Job not found or evicted after TTL"}},
)
def get_scan(job_id: str, request: Request) -> ScanJobDetail:
    store = _get_store(request)
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return _job_to_detail(job)


__all__ = ["api"]
