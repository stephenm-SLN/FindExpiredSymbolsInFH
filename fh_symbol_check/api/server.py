"""FastAPI application factory.

Kept as a pure factory (``build_app`` returns a fresh ``FastAPI`` instance)
so tests can spin up isolated apps with different creds / exchange maps /
TTLs and the ``main`` entry point can inject config from CLI flags.

Wires:

- routes from :mod:`.routes` (JSON API surface)
- HTML views from :mod:`.views` (Checkpoint 3 — HTMX + Jinja)
- static asset mount at ``/static``
- a background TTL sweeper on the :class:`JobStore`
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..creds import DBCreds
from .jobs import JobStore
from .routes import api as api_router
from .views import views as views_router
from .workers import ScanWorkers

logger = logging.getLogger(__name__)


# The sweeper interval — small enough that a 1h TTL is honoured within
# ~a minute of expiry. Only relevant for jobs the user never bothered
# to fetch after completion.
_SWEEP_INTERVAL_SECONDS = 60.0


def build_app(
    *,
    creds: DBCreds,
    exchange_map: dict[str, str],
    job_ttl_seconds: float = 3600.0,
    max_concurrent_scans: int = 2,
    sweep_interval_seconds: float = _SWEEP_INTERVAL_SECONDS,
    static_dir: Path | None = None,
    templates_dir: Path | None = None,
) -> FastAPI:
    """Construct the FastAPI app.

    Every dependency is passed in explicitly — no globals — so tests can
    build the app against stubbed creds/maps and check the wiring
    end-to-end without touching the network or the DB.

    ``static_dir`` / ``templates_dir`` default to the packaged assets under
    ``fh_symbol_check/api/{static,templates}/``. Overriding lets a
    development workflow point at the source tree without reinstalling.
    """
    store = JobStore(job_ttl_seconds=job_ttl_seconds)
    workers = ScanWorkers(
        store=store,
        creds=creds,
        exchange_map=exchange_map,
        max_concurrent_scans=max_concurrent_scans,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
        sweep_task = asyncio.create_task(
            _sweep_loop(store, sweep_interval_seconds)
        )
        logger.info(
            "API started (job TTL %.0fs, max concurrent scans %d, sweep every %.0fs)",
            job_ttl_seconds,
            max_concurrent_scans,
            sweep_interval_seconds,
        )
        try:
            yield
        finally:
            sweep_task.cancel()
            try:
                await sweep_task
            except (asyncio.CancelledError, Exception):
                pass
            workers.shutdown(wait=False)
            logger.info("API stopped")

    app = FastAPI(
        title="find-expired-symbols API",
        description=(
            "Report Feed Handler / repeater symbols that are no longer "
            "listed on their exchange. Same scanning capability as the "
            "`find-expired-symbols` CLI, via async-poll HTTP."
        ),
        version=_read_pkg_version(),
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.workers = workers
    app.state.job_ttl_seconds = job_ttl_seconds

    _static = static_dir or Path(str(_pkg_files("fh_symbol_check").joinpath("api/static")))
    _templates = templates_dir or Path(
        str(_pkg_files("fh_symbol_check").joinpath("api/templates"))
    )
    app.state.templates_dir = _templates
    app.state.static_dir = _static

    # Only mount the static dir if it exists on disk — Checkpoint 3 adds
    # the actual assets; a fresh checkout that hasn't been reinstalled
    # would otherwise 500 on `/static/htmx.min.js`.
    if _static.exists():
        app.mount("/static", StaticFiles(directory=str(_static)), name="static")
    else:
        logger.warning(
            "static dir %s missing; /static/* will 404 until assets land",
            _static,
        )

    app.include_router(api_router)
    app.include_router(views_router)
    return app


def _read_pkg_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("find-expired-symbols")
    except PackageNotFoundError:
        return "0.0.0+unknown"


async def _sweep_loop(store: JobStore, interval_seconds: float) -> None:
    """Periodic TTL sweep. Cancelled cleanly by the lifespan on shutdown."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            store.sweep()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("sweep failed; continuing")


__all__ = ["build_app"]
