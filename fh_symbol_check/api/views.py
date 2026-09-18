"""HTML views for the HTMX-driven UI.

Three endpoints:

- ``GET /`` — home page: filter form + list of recent in-memory jobs.
- ``POST /`` — form target. Parses form-encoded body into a ``ScanRequest``,
  submits it, and returns the new scan card as an HTML fragment for
  HTMX's ``hx-swap="afterbegin"`` to insert at the top of the list.
- ``GET /scans/{job_id}/partial`` — the HTMX polling target. Returns the
  same scan-card fragment; polling naturally stops when the fragment
  swapped in no longer carries the ``hx-trigger`` attribute (i.e. once
  the state becomes ``done`` or ``failed``).

Templates read from ``app.state.templates_dir`` — set to the packaged
``fh_symbol_check/api/templates`` by default; ``build_app`` accepts an
override so a dev workflow can point at the source tree without a
reinstall.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .jobs import Job, JobStore
from .models import ScanRequest
from .workers import ScanWorkers

logger = logging.getLogger(__name__)

views = APIRouter(tags=["ui"])


def _get_templates(request: Request) -> Jinja2Templates:
    """Fetch (or lazy-build) the Jinja env stored on app.state.

    Kept out of ``build_app`` so tests can point at custom template
    directories without rebuilding the whole app.
    """
    templates: Jinja2Templates | None = getattr(request.app.state, "templates", None)
    if templates is not None:
        return templates
    templates_dir = getattr(request.app.state, "templates_dir", None)
    if templates_dir is None:
        raise HTTPException(
            status_code=500,
            detail="templates_dir not configured on app.state.templates_dir",
        )
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.filters["filter_desc"] = _filter_desc_for_job
    request.app.state.templates = templates
    return templates


def _get_store(request: Request) -> JobStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="job store missing")
    return store


def _get_workers(request: Request) -> ScanWorkers:
    workers = getattr(request.app.state, "workers", None)
    if workers is None:
        raise HTTPException(status_code=500, detail="workers missing")
    return workers


def _job_ttl_seconds(request: Request) -> float:
    return getattr(request.app.state, "job_ttl_seconds", 3600.0)


def _package_version() -> str:
    try:
        return version("find-expired-symbols")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _filter_desc_for_job(job: Job) -> str:
    """Compact ``fh_name LIKE %...% & exchange=X & symbols=[...]`` string for
    the scan-card header. Reads the request dump that was stashed on the
    job at submission time so it survives even without the ScanFilters
    dataclass around."""
    filters = job.filters or {}
    parts: list[str] = []
    if filters.get("hostname"):
        parts.append(f"hostname LIKE %{filters['hostname']}%")
    if filters.get("exchange_name"):
        parts.append(f"exchange={filters['exchange_name']}")
    symbols = filters.get("symbol") or []
    if symbols:
        parts.append(f"symbols={list(symbols)}")
    if filters.get("all"):
        parts.append("all")
    source = filters.get("source") or "both"
    if source != "both":
        parts.append(f"source={source}")
    return " · ".join(parts) if parts else "no filters"


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def _scan_card_context(job: Job) -> dict[str, Any]:
    rows = job.result_json or []
    return {
        "job": job,
        "rows": rows,
        "status_counts": _status_counts(rows),
    }


# ---------------------------------------------------------------------------
# GET /  — home page
# ---------------------------------------------------------------------------


@views.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    templates = _get_templates(request)
    store = _get_store(request)
    recent_jobs = store.list_all()
    # Enrich each job with the status_counts / rows the card expects. The
    # template `{% include "scan_card.html" %}` inside `home.html` reads
    # `rows` and `status_counts` from the enclosing context, so we pass a
    # nested loop-friendly shape.
    context = {
        "recent_jobs": recent_jobs,
        "rows": [],  # overridden inside the loop by the card
        "status_counts": {},
        "version": _package_version(),
        "job_ttl_minutes": _job_ttl_seconds(request) / 60.0,
    }
    return templates.TemplateResponse(request, "home.html", context)


# ---------------------------------------------------------------------------
# POST / — form target
# ---------------------------------------------------------------------------


@views.post("/", response_class=HTMLResponse)
def submit_form(
    request: Request,
    hostname: str = Form(default=""),
    exchange_name: str = Form(default=""),
    symbol: str = Form(default=""),
    source: str = Form(default="both"),
    concurrency: int = Form(default=4),
    all: str = Form(default=""),
    show_listed: str = Form(default=""),
    errors_only: str = Form(default=""),
) -> HTMLResponse:
    templates = _get_templates(request)
    store = _get_store(request)
    workers = _get_workers(request)

    # Normalise form fields into ScanRequest kwargs. Empty text inputs
    # become None; unchecked boxes stay False; the symbol string is
    # space-split.
    symbols = symbol.split() if symbol.strip() else None
    try:
        scan_req = ScanRequest(
            hostname=hostname.strip() or None,
            exchange_name=exchange_name.strip() or None,
            symbol=symbols,
            source=source,  # type: ignore[arg-type]
            concurrency=concurrency,
            all=_is_truthy(all),
            show_listed=_is_truthy(show_listed),
            errors_only=_is_truthy(errors_only),
        )
    except ValidationError as e:
        # Re-render the form with an error banner. The empty <div
        # id="scans-list"> that HTMX targets stays untouched thanks to
        # the 422 status — HTMX inserts nothing on non-200 responses by
        # default. But it does surface the response body via the
        # "htmx:responseError" event; simpler to render a proper HTML
        # partial with the message and set 200 so it lands in-place.
        first = e.errors()[0]["msg"] if e.errors() else "invalid form input"
        return templates.TemplateResponse(
            request,
            "form_error.html",
            {"message": first},
            status_code=200,
        )

    job = workers.submit(scan_req.to_filters(), scan_req.model_dump(mode="json", by_alias=True))
    # Refresh the reference so the initial progress snapshot lands in
    # the card (the worker fires "querying_db" almost immediately, but
    # the create() progress is already what we want to show).
    fresh = store.get(job.id) or job
    return templates.TemplateResponse(
        request, "scan_card.html", _scan_card_context(fresh)
    )


# ---------------------------------------------------------------------------
# GET /scans/{id}/partial — HTMX polling target
# ---------------------------------------------------------------------------


@views.get("/scans/{job_id}/partial", response_class=HTMLResponse)
def scan_card_partial(job_id: str, request: Request) -> HTMLResponse:
    templates = _get_templates(request)
    store = _get_store(request)
    job = store.get(job_id)
    if job is None:
        # The card was TTL-swept during polling; return an empty fragment
        # so HTMX replaces it with nothing, stopping the poll.
        return HTMLResponse(content="", status_code=200)
    return templates.TemplateResponse(
        request, "scan_card.html", _scan_card_context(job)
    )


def _is_truthy(v: str) -> bool:
    """HTML checkboxes send their `value` attribute (typically "1" or "on")
    when checked, and don't send anything at all when unchecked. FastAPI's
    Form(default="") gives us "" for unchecked."""
    return v.strip().lower() in ("1", "on", "true", "yes")


__all__ = ["views"]
