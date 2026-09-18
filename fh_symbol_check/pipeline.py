"""Shared orchestration entry for both the CLI and the API service.

Both :func:`fh_symbol_check.cli.main` and the API's scan worker call
:func:`run_scan` — same code path, same results, so any bug we fix once
benefits both fronts. Kept intentionally free of argparse / HTTP concerns.

Callers own:

- loading creds (``DBCreds``) and the exchange map (``dict[str, str]``)
- log-level setup
- rendering / exit codes / HTTP status translation

:func:`run_scan` owns:

- filter validation (mirroring the CLI's argparse checks)
- DB fetch from ``fh_config`` and/or ``repeater_feeds`` per ``ScanFilters.source``
- exchange-name mapping + symbol translation (via :mod:`.validator`)
- ``--symbol`` filter (OR semantics; case-sensitive; both sides matched)
- ccxt / custom-venue classification
- progress reporting to an optional callback (used by the API for polling)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

from .creds import DBCreds
from .db import fetch_feed_handlers, fetch_repeaters
from .models import FeedHandlerRow, SymbolResult
from .validator import build_tasks, classify_symbols, filter_by_symbol

logger = logging.getLogger(__name__)


SourceSelector = Literal["fh", "rp", "both"]
ScanPhase = Literal[
    "querying_db",
    "building_tasks",
    "classifying",
    "done",
]


class ScanFiltersError(ValueError):
    """Raised by :meth:`ScanFilters.validate` for invalid filter combinations.

    Message text matches the CLI's ``argparse.error()`` strings so callers
    that surface it to end users (CLI, API 422 responses) all show the
    same wording.
    """


@dataclass(frozen=True)
class ScanFilters:
    """Filter set for a single scan; mirrors the CLI's argparse args.

    Field names differ slightly to avoid shadowing Python builtins:

    - ``all_producers`` <-> CLI ``--all``
    - ``symbols`` (tuple) <-> CLI ``--symbol V1 V2 ...``

    All other fields keep the CLI's names verbatim.
    """

    hostname: str | None = None
    exchange_name: str | None = None
    all_producers: bool = False
    symbols: tuple[str, ...] = ()
    source: SourceSelector = "both"
    concurrency: int = 4

    def validate(self) -> None:
        """Raise :class:`ScanFiltersError` if the combination is invalid."""
        if self.all_producers and (self.hostname or self.exchange_name):
            raise ScanFiltersError(
                "--all cannot be combined with --hostname or --exchange-name"
            )
        if not (
            self.all_producers
            or self.hostname
            or self.exchange_name
            or self.symbols
        ):
            raise ScanFiltersError(
                "specify --hostname, --exchange-name, --symbol, or --all"
            )


@dataclass(frozen=True)
class ScanProgress:
    """Snapshot of scan progress delivered to :func:`run_scan`'s callback.

    ``total_producers`` is the number of distinct ccxt_id groups the
    classifier will process — one HTTP round-trip per group, so this is
    the meaningful unit for progress reporting. ``completed_producers``
    counts how many groups have finished so far. Both are 0 outside the
    ``classifying`` phase.

    ``rows_scanned`` is the count of DB rows fetched (fh + repeater
    combined) once the ``building_tasks`` phase starts; it's included
    so the UI can render an "N producers loaded" line before ccxt work
    begins.
    """

    phase: ScanPhase
    total_producers: int = 0
    completed_producers: int = 0
    rows_scanned: int = 0


ProgressCallback = Callable[[ScanProgress], None]


def run_scan(
    filters: ScanFilters,
    creds: DBCreds,
    exchange_map: dict[str, str],
    *,
    on_progress: ProgressCallback | None = None,
) -> list[SymbolResult]:
    """Load rows, build tasks, classify, return combined results.

    Consolidates the DB→classify middle of the CLI's ``main()`` so the
    API worker can reuse it. Callers are responsible for their own log
    setup, rendering, and exit-code / HTTP-status translation.

    Errors surface as:

    - :class:`ScanFiltersError` — invalid filter combination
    - :class:`fh_symbol_check.db.DBError` — MySQL fetch failed
    - :class:`fh_symbol_check.exchange_map.ExchangeMapError` — only if the
      caller passes a bad map; ``load_exchange_map`` is not invoked here.
    """
    filters.validate()

    _emit(on_progress, ScanProgress(phase="querying_db"))
    rows = _fetch_rows(filters, creds)

    _emit(
        on_progress,
        ScanProgress(phase="building_tasks", rows_scanned=len(rows)),
    )
    tasks, early_errors = build_tasks(rows, exchange_map)

    if filters.symbols:
        before_tasks = len(tasks)
        before_errors = len(early_errors)
        tasks, early_errors = filter_by_symbol(
            tasks, early_errors, filters.symbols
        )
        logger.info(
            "--symbol %s: matched %d/%d task(s) and %d/%d "
            "unmappable-exchange error(s)",
            list(filters.symbols),
            len(tasks),
            before_tasks,
            len(early_errors),
            before_errors,
        )
        if not tasks and not early_errors:
            logger.warning(
                "--symbol %s: 0 occurrences found in the scanned producer set",
                list(filters.symbols),
            )

    total = len({t.ccxt_id for t in tasks})
    _emit(
        on_progress,
        ScanProgress(
            phase="classifying",
            total_producers=total,
            rows_scanned=len(rows),
        ),
    )

    completed_box = _Counter()

    def _tick(_ccxt_id: str) -> None:
        completed_box.value += 1
        _emit(
            on_progress,
            ScanProgress(
                phase="classifying",
                total_producers=total,
                completed_producers=completed_box.value,
                rows_scanned=len(rows),
            ),
        )

    live_results = classify_symbols(
        tasks,
        concurrency=filters.concurrency,
        on_group_done=_tick,
    )
    results = early_errors + live_results

    _emit(
        on_progress,
        ScanProgress(
            phase="done",
            total_producers=total,
            completed_producers=completed_box.value,
            rows_scanned=len(rows),
        ),
    )
    return results


def describe_filters(filters: ScanFilters) -> str:
    """Human-readable filter summary used in log lines.

    Kept here (rather than in :mod:`cli`) so the API can log the same
    string when a scan starts.
    """
    parts: list[str] = []
    if filters.hostname:
        parts.append(f"hostname LIKE %{filters.hostname}%")
    if filters.exchange_name:
        parts.append(f"exchange_name={filters.exchange_name!r}")
    if filters.symbols:
        parts.append(f"symbols={list(filters.symbols)!r}")
    if filters.source != "both":
        parts.append(f"source={filters.source!r}")
    return ", ".join(parts) if parts else "ALL (no filters)"


def _fetch_rows(filters: ScanFilters, creds: DBCreds) -> list[FeedHandlerRow]:
    rows: list[FeedHandlerRow] = []
    scan_fh = filters.source in ("fh", "both")
    scan_rp = filters.source in ("rp", "both")

    if scan_fh:
        fh_rows = fetch_feed_handlers(
            creds,
            hostname_pattern=filters.hostname,
            exchange_name=filters.exchange_name,
        )
        if not fh_rows:
            logger.warning(
                "no fh_config rows matched filters (%s)",
                describe_filters(filters),
            )
        rows.extend(fh_rows)

    if scan_rp:
        rp_rows = fetch_repeaters(
            creds,
            hostname_pattern=filters.hostname,
            exchange_name=filters.exchange_name,
        )
        if not rp_rows:
            logger.warning(
                "no repeater_feeds rows matched filters (%s)",
                describe_filters(filters),
            )
        rows.extend(rp_rows)

    return rows


def _emit(cb: ProgressCallback | None, progress: ScanProgress) -> None:
    if cb is None:
        return
    try:
        cb(progress)
    except Exception:
        # A misbehaving progress callback must not fail the scan. This is
        # the API's problem to log on its side; the pipeline just carries
        # on producing results.
        logger.exception("progress callback raised; ignoring")


@dataclass
class _Counter:
    """Tiny mutable box so the nested `_tick` closure can bump a shared count
    without needing `nonlocal` in a way mypy flags in strict mode."""

    value: int = 0


__all__ = [
    "ProgressCallback",
    "ScanFilters",
    "ScanFiltersError",
    "ScanPhase",
    "ScanProgress",
    "SourceSelector",
    "describe_filters",
    "run_scan",
]
