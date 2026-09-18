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
from typing import Callable, Iterable

from check_delisted_symbol import (
    MarketLoadError,
    classify,
    load_exchange_markets_safe,
)

from .custom_venues import (
    CUSTOM_VENUE_PREFIX,
    VenueGone,
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
                        source=row.source,
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
                        source=row.source,
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
                    source=row.source,
                )
            )

    return tasks, errors


def filter_by_symbol(
    tasks: list[ResolvedTask],
    errors: list[SymbolResult],
    symbols: Iterable[str],
) -> tuple[list[ResolvedTask], list[SymbolResult]]:
    """Keep only tasks/errors whose ``original_symbol`` OR ``ccxt_symbol``
    equals **any** value in ``symbols``.

    Match is **case-sensitive** and exact (full string equality). Both sides
    are checked so the caller can pass either the FH-internal form
    (``IP/USDT-PERP``) or the translated venue form (``IP/USDT:USDT``) and
    get the same row back. Multiple symbols use OR semantics — a row is
    kept if it matches any of the provided values.

    Returns a new (tasks, errors) pair; the inputs are not mutated. An
    empty ``symbols`` iterable filters everything out.
    """
    wanted = set(symbols)
    matched_tasks = [
        t for t in tasks
        if t.original_symbol in wanted or t.ccxt_symbol in wanted
    ]
    matched_errors = [
        e for e in errors
        if e.original_symbol in wanted or e.ccxt_symbol in wanted
    ]
    return matched_tasks, matched_errors


def classify_symbols(
    tasks: Iterable[ResolvedTask],
    *,
    concurrency: int = 4,
    on_group_done: Callable[[str], None] | None = None,
) -> list[SymbolResult]:
    """Validate each task against ccxt; one load_markets() call per ccxt_id.

    ``on_group_done``, if provided, is invoked with the ccxt_id string
    after each per-exchange group finishes (whether successful or all
    ERROR). It fires from the coordinating thread as futures complete,
    so callers get natural throttling — one callback per ``load_markets``
    round-trip, in the order groups finish. Used by the API service to
    push progress updates into its in-memory JobStore. Exceptions raised
    by the callback are logged and swallowed so a broken observer can't
    break the scan.
    """
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
            if on_group_done is not None:
                try:
                    on_group_done(futures[fut])
                except Exception:
                    logger.exception(
                        "on_group_done callback raised for %s; ignoring",
                        futures[fut],
                    )

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
        raw = str(e)
        hint = _rewrite_proxy_block(ccxt_id, raw)
        if hint is not None:
            logger.warning("ccxt load_markets failed for %s: %s", ccxt_id, hint)
            logger.debug("full underlying error for %s: %s", ccxt_id, raw)
            detail_text = hint
        else:
            logger.warning("ccxt load_markets failed for %s: %s", ccxt_id, raw)
            detail_text = raw
        return [_error_result(t, f"load_markets failed: {detail_text}") for t in group]
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
                source=t.source,
            )
        )
    return out


def _classify_custom_venue(
    custom_id: str, group: list[ResolvedTask]
) -> list[SymbolResult]:
    """Classify tasks against a custom (non-ccxt) venue's symbol universe.

    Calls the registered checker once for the whole group; missing symbols
    are DELISTED, present-but-not-live symbols are INACTIVE, and a single
    fetch failure emits ERROR for every task in the group. A checker that
    raises :class:`VenueGone` (venue permanently shut down) emits DELISTED
    with the exception message as ``detail``.
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
    except VenueGone as e:
        logger.warning("venue gone for %s: %s", custom_id, e)
        return [
            _symbol_result(t, "DELISTED", str(e))
            for t in group
        ]
    except Exception as e:
        raw = str(e)
        hint = _rewrite_proxy_block(custom_id, raw)
        if hint is not None:
            logger.warning("custom venue fetch failed for %s: %s", custom_id, hint)
            logger.debug("full underlying error for %s: %s", custom_id, raw)
            detail_text = hint
        else:
            logger.warning("custom venue fetch failed for %s: %s", custom_id, raw)
            detail_text = raw
        return [
            _error_result(t, f"custom venue fetch failed: {detail_text}")
            for t in group
        ]

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
                source=t.source,
            )
        )
    return out


_MAX_ERROR_DETAIL_LEN = 500

# Substrings that positively identify a corporate SSL-inspection proxy block
# page in an HTTP response body. When one appears in a ccxt error message
# we replace the (mostly HTML) noise with a short, actionable hint so the
# report and logs stay readable.
#
# Tuples are (sentinel-substring, vendor-label). Sentinels are checked in
# order; the first match wins. Add new vendors here as they're encountered
# (Palo Alto uses `pan-block`, Netskope uses `netskope-block`, etc.).
_PROXY_BLOCK_SENTINELS: tuple[tuple[str, str], ...] = (
    ("wac_block.html", "Zscaler"),
)

# Error-text signatures for a TLS connection that died before the peer
# presented any certificate. A firewall filtering on the TLS SNI hostname
# drops the connection at exactly this point, so there is no block page to
# match on — only the transport-level failure. Distinct from the sentinels
# above, which require the proxy to have decrypted the request far enough
# to serve HTML back.
#
# A genuine upstream outage produces the same signature, so the hint says
# "likely" rather than asserting a block.
_TLS_TEARDOWN_SENTINELS: tuple[str, ...] = (
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
)


def _rewrite_proxy_block(label: str, err_text: str) -> str | None:
    """If `err_text` matches a known corporate-proxy block signature,
    return a short single-line replacement; otherwise return None.

    Covers two distinct failure shapes:

    * **Block page** — the proxy decrypted the request, denied it, and
      returned HTML (:data:`_PROXY_BLOCK_SENTINELS`). The HTML is dropped
      and only the transport-layer prefix ccxt attached is kept (e.g.
      ``htx GET https://api.huobi.pro/... 403 Forbidden``) so operators
      still see which host and status code triggered the block.
    * **TLS teardown** — the connection was killed mid-handshake with no
      certificate exchanged (:data:`_TLS_TEARDOWN_SENTINELS`). There is no
      block page to strip here, so the original text is kept intact; it
      carries the URL that was refused.

    `label` identifies the venue (a ccxt id or a ``custom:`` sentinel) and
    is only used as a fallback when the error carries no host context. The
    raw text stays available via ``logger.debug`` in the caller.
    """
    for sentinel, vendor in _PROXY_BLOCK_SENTINELS:
        if sentinel in err_text:
            # Everything up to the first '<' is the ccxt-provided HTTP
            # summary line; the HTML body follows it. Fall back to
            # `label` if ccxt didn't include a summary (unusual).
            head = err_text.split("<", 1)[0].strip() or label
            return (
                f"blocked by corporate proxy ({vendor} {sentinel}); {head} "
                "\u2014 contact IT to allowlist this exchange host"
            )

    for sentinel in _TLS_TEARDOWN_SENTINELS:
        if sentinel in err_text:
            return (
                "likely blocked by corporate network policy (TLS handshake "
                f"closed before certificate exchange); {err_text} "
                "\u2014 contact IT to allowlist this host"
            )

    return None


def _sanitize_error_detail(detail: str) -> str:
    """Collapse runs of whitespace (including newlines) into single spaces
    and cap at :data:`_MAX_ERROR_DETAIL_LEN` characters.

    Applied to ERROR-row `detail` only. The concrete case this guards
    against: ccxt raises `NetworkError` / `ExchangeError` with the full
    HTTP response body embedded in the exception message. When a
    corporate SSL-inspection proxy (Zscaler etc.) intercepts a request
    and returns an HTML block page, that HTML would otherwise land
    verbatim in the report. Truncation keeps the error line readable
    without hiding the failure — the log line emitted alongside
    (WARNING via `logger.warning`) still has the full text for
    operators who need it.
    """
    collapsed = " ".join(detail.split())
    if len(collapsed) > _MAX_ERROR_DETAIL_LEN:
        return collapsed[: _MAX_ERROR_DETAIL_LEN - 1] + "\u2026"
    return collapsed


def _symbol_result(t: ResolvedTask, status: str, detail: str) -> SymbolResult:
    return SymbolResult(
        service_id=t.service_id,
        fh_name=t.fh_name,
        hostname=t.hostname,
        exchange_name=t.exchange_name,
        ccxt_id=t.ccxt_id,
        original_symbol=t.original_symbol,
        ccxt_symbol=t.ccxt_symbol,
        status=status,  # type: ignore[arg-type]
        detail=_sanitize_error_detail(detail),
        source=t.source,
    )


def _error_result(t: ResolvedTask, detail: str) -> SymbolResult:
    return _symbol_result(t, "ERROR", detail)
