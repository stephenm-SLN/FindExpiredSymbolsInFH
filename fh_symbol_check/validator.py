"""Build classification tasks from DB rows and run them against ccxt.

build_tasks() is a pure function (no I/O): exchange-name mapping + per-exchange
symbol translation. Rows whose exchange_name is unknown become ERROR rows.

classify_symbols() groups tasks by ccxt_id so each exchange's load_markets() is
called at most once, with bounded concurrency across exchanges.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from check_delisted_symbol import (
    MarketLoadError,
    classify,
    load_exchange_markets_safe,
)

from .custom_venues import (
    CUSTOM_VENUE_PREFIX,
    custom_id_for,
    get_checker,
    is_custom_venue,
)
from .exchange_map import resolve
from .models import FeedHandlerRow, ResolvedTask, SymbolResult
from .symbol_translation import translate

logger = logging.getLogger(__name__)


def build_tasks(
    rows: Iterable[FeedHandlerRow],
    exchange_map: dict[str, str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Map exchange_name -> ccxt_id and translate symbols.

    Returns (tasks_ready_for_ccxt, error_results_for_unmappable_rows).
    """
    tasks: list[ResolvedTask] = []
    errors: list[SymbolResult] = []

    for row in rows:
        if is_custom_venue(row.exchange_name):
            custom_id = custom_id_for(row.exchange_name)
            for sym in row.symbols:
                tasks.append(
                    ResolvedTask(
                        service_id=row.service_id,
                        fh_name=row.fh_name,
                        hostname=row.hostname,
                        exchange_name=row.exchange_name,
                        ccxt_id=custom_id,
                        original_symbol=sym,
                        ccxt_symbol=translate(row.exchange_name, sym),
                    )
                )
            continue

        ccxt_id = resolve(exchange_map, row.exchange_name)
        if ccxt_id is None:
            detail = f"unknown exchange_name={row.exchange_name!r}; add it to exchange_mapping.yaml"
            for sym in row.symbols:
                errors.append(
                    SymbolResult(
                        service_id=row.service_id,
                        fh_name=row.fh_name,
                        hostname=row.hostname,
                        exchange_name=row.exchange_name,
                        ccxt_id="",
                        original_symbol=sym,
                        ccxt_symbol="",
                        status="ERROR",
                        detail=detail,
                    )
                )
            continue

        for sym in row.symbols:
            tasks.append(
                ResolvedTask(
                    service_id=row.service_id,
                    fh_name=row.fh_name,
                    hostname=row.hostname,
                    exchange_name=row.exchange_name,
                    ccxt_id=ccxt_id,
                    original_symbol=sym,
                    ccxt_symbol=translate(row.exchange_name, sym),
                )
            )

    return tasks, errors


def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
) -> list[SymbolResult]:
    """Validate each task against ccxt; one load_markets() call per ccxt_id."""
    by_ccxt: dict[str, list[ResolvedTask]] = defaultdict(list)
    for t in tasks:
        by_ccxt[t.ccxt_id].append(t)

    if not by_ccxt:
        return []

    results: list[SymbolResult] = []
    max_workers = max(1, min(concurrency, len(by_ccxt)))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_classify_one_exchange, ccxt_id, group): ccxt_id
            for ccxt_id, group in by_ccxt.items()
        }
        for fut in as_completed(futures):
            results.extend(fut.result())

    return results


def _classify_one_exchange(
    ccxt_id: str, group: list[ResolvedTask]
) -> list[SymbolResult]:
    """Classify every task in the group against its venue.

    Routes custom (non-ccxt) venues through :func:`_classify_custom_venue`;
    everything else goes through ccxt's :func:`load_exchange_markets_safe`.
    """
    if ccxt_id.startswith(CUSTOM_VENUE_PREFIX):
        return _classify_custom_venue(ccxt_id, group)

    try:
        _exchange, markets = load_exchange_markets_safe(ccxt_id)
    except MarketLoadError as e:
        logger.warning("ccxt load_markets failed for %s: %s", ccxt_id, e)
        return [_error_result(t, f"load_markets failed: {e}") for t in group]
    except Exception as e:  # safety net for any unexpected ccxt failure
        logger.exception("unexpected error loading markets for %s", ccxt_id)
        return [_error_result(t, f"unexpected ccxt error: {e}") for t in group]

    logger.info(
        "loaded markets for ccxt_id=%s (%d markets) — classifying %d task(s)",
        ccxt_id,
        len(markets),
        len(group),
    )

    out: list[SymbolResult] = []
    for t in group:
        status, detail = classify(markets, t.ccxt_symbol)
        out.append(
            SymbolResult(
                service_id=t.service_id,
                fh_name=t.fh_name,
                hostname=t.hostname,
                exchange_name=t.exchange_name,
                ccxt_id=t.ccxt_id,
                original_symbol=t.original_symbol,
                ccxt_symbol=t.ccxt_symbol,
                status=status,  # type: ignore[arg-type]
                detail=detail,
            )
        )
    return out


def _classify_custom_venue(
    custom_id: str, group: list[ResolvedTask]
) -> list[SymbolResult]:
    """Classify tasks against a custom (non-ccxt) venue's symbol universe.

    Calls the registered checker once for the whole group; missing symbols
    are DELISTED, present-but-not-live symbols are INACTIVE, and a single
    fetch failure emits ERROR for every task in the group.
    """
    try:
        checker = get_checker(custom_id)
    except KeyError:
        logger.error("no custom-venue checker registered for %s", custom_id)
        return [
            _error_result(t, f"no custom-venue checker registered for {custom_id}")
            for t in group
        ]

    try:
        symbol_status = checker()
    except Exception as e:
        logger.warning("custom venue fetch failed for %s: %s", custom_id, e)
        return [_error_result(t, f"custom venue fetch failed: {e}") for t in group]

    logger.info(
        "fetched %d symbols for %s — classifying %d task(s)",
        len(symbol_status),
        custom_id,
        len(group),
    )

    out: list[SymbolResult] = []
    for t in group:
        key = t.ccxt_symbol.upper()
        if key in symbol_status:
            if symbol_status[key]:
                status, detail = "LISTED", ""
            else:
                status, detail = "INACTIVE", "trading_status != live"
        else:
            status, detail = "DELISTED", f"not found in {custom_id} symbols"
        out.append(
            SymbolResult(
                service_id=t.service_id,
                fh_name=t.fh_name,
                hostname=t.hostname,
                exchange_name=t.exchange_name,
                ccxt_id=t.ccxt_id,
                original_symbol=t.original_symbol,
                ccxt_symbol=t.ccxt_symbol,
                status=status,  # type: ignore[arg-type]
                detail=detail,
            )
        )
    return out


def _error_result(t: ResolvedTask, detail: str) -> SymbolResult:
    return SymbolResult(
        service_id=t.service_id,
        fh_name=t.fh_name,
        hostname=t.hostname,
        exchange_name=t.exchange_name,
        ccxt_id=t.ccxt_id,
        original_symbol=t.original_symbol,
        ccxt_symbol=t.ccxt_symbol,
        status="ERROR",
        detail=detail,
    )
