"""Render SymbolResult lists as text, JSON, or CSV."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Literal, TextIO

from .models import SourceKind, SymbolResult, SymbolStatus

Format = Literal["text", "json", "csv"]


@dataclass(frozen=True)
class FeedHandlerSummary:
    """Per-producer aggregate counts for the summary table.

    Used for BOTH feed handlers (``source="fh"``) and repeaters
    (``source="repeater"``). ``fh_name`` stores whichever identifier the
    source uses — the DB ``fh_name`` for FHs, the DB ``app_name`` for
    repeaters. Buckets are mutually exclusive:
    ``active + inactive + delisted + error == total``.
    """

    fh_name: str
    hostname: str
    exchange_name: str
    active: int  # LISTED
    inactive: int
    delisted: int
    error: int
    total_dead: int  # inactive + delisted
    total: int


@dataclass(frozen=True)
class ExchangeSummary:
    """Per-(exchange, source) aggregate counts for the ``--exchange-grouping``
    summary table.

    Each row collapses every producer targeting the same
    (``exchange_name``, ``source``) pair into one line. ``feed_handlers`` is
    the count of distinct (hostname, fh_name) pairs contributing to this
    exchange/source. Buckets are mutually exclusive:
    ``active + inactive + delisted + error == total``.
    """

    exchange_name: str
    source: SourceKind  # "fh" or "repeater"
    feed_handlers: int
    active: int  # LISTED
    inactive: int
    delisted: int
    error: int
    total_dead: int  # inactive + delisted
    total: int

_CSV_FIELDS = [
    "source",
    "service_id",
    "fh_name",
    "hostname",
    "exchange_name",
    "ccxt_id",
    "original_symbol",
    "ccxt_symbol",
    "status",
    "detail",
]


def keep_fhs_with_errors(results: list[SymbolResult]) -> list[SymbolResult]:
    """Return the subset of ``results`` belonging to producers that have at
    least one ``ERROR`` row. All rows of an offending producer are retained
    (not just its ERROR rows) so the surviving summaries remain complete.

    Producer identity is ``(source, hostname, fh_name, exchange_name)`` so
    a repeater and a feed handler with the same name aren't confused.
    """
    offending: set[tuple[SourceKind, str, str, str]] = {
        (r.source, r.hostname, r.fh_name, r.exchange_name)
        for r in results
        if r.status == "ERROR"
    }
    if not offending:
        return []
    return [
        r
        for r in results
        if (r.source, r.hostname, r.fh_name, r.exchange_name) in offending
    ]


def summary(results: list[SymbolResult]) -> dict[SymbolStatus, int]:
    """Count results by status. Returns all four statuses even if zero."""
    counter: Counter[str] = Counter(r.status for r in results)
    return {
        "LISTED": counter.get("LISTED", 0),
        "INACTIVE": counter.get("INACTIVE", 0),
        "DELISTED": counter.get("DELISTED", 0),
        "ERROR": counter.get("ERROR", 0),
    }


def summary_by_fh(results: list[SymbolResult]) -> list[FeedHandlerSummary]:
    """Aggregate feed-handler results into one row per producer.

    Only rows with ``source="fh"`` contribute. Sorted by (hostname, fh_name,
    exchange_name) for stable output.
    """
    return _summary_by_producer(results, source="fh")


def summary_by_repeater(results: list[SymbolResult]) -> list[FeedHandlerSummary]:
    """Aggregate repeater results into one row per producer.

    Only rows with ``source="repeater"`` contribute. Same shape as
    :func:`summary_by_fh` — ``fh_name`` slot holds the repeater ``app_name``.
    """
    return _summary_by_producer(results, source="repeater")


def _summary_by_producer(
    results: list[SymbolResult], *, source: SourceKind
) -> list[FeedHandlerSummary]:
    grouped: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for r in results:
        if r.source != source:
            continue
        key = (r.hostname, r.fh_name, r.exchange_name)
        grouped[key][r.status] += 1

    rows: list[FeedHandlerSummary] = []
    for (hostname, fh_name, exchange_name), c in sorted(grouped.items()):
        listed = c["LISTED"]
        inactive = c["INACTIVE"]
        delisted = c["DELISTED"]
        error = c["ERROR"]
        rows.append(
            FeedHandlerSummary(
                fh_name=fh_name,
                hostname=hostname,
                exchange_name=exchange_name,
                active=listed,
                inactive=inactive,
                delisted=delisted,
                error=error,
                total_dead=inactive + delisted,
                total=listed + inactive + delisted + error,
            )
        )
    return rows


def summary_by_exchange(results: list[SymbolResult]) -> list[ExchangeSummary]:
    """Aggregate results into one row per (``exchange_name``, ``source``).

    Sorted by (exchange_name, source) for stable output. ``feed_handlers``
    counts distinct ``(hostname, fh_name)`` pairs contributing to each
    (exchange, source) bucket.
    """
    counts: dict[tuple[str, SourceKind], Counter[str]] = defaultdict(Counter)
    fh_keys: dict[tuple[str, SourceKind], set[tuple[str, str]]] = defaultdict(set)
    for r in results:
        key = (r.exchange_name, r.source)
        counts[key][r.status] += 1
        fh_keys[key].add((r.hostname, r.fh_name))

    rows: list[ExchangeSummary] = []
    for (exchange_name, source) in sorted(counts):
        c = counts[(exchange_name, source)]
        listed = c["LISTED"]
        inactive = c["INACTIVE"]
        delisted = c["DELISTED"]
        error = c["ERROR"]
        rows.append(
            ExchangeSummary(
                exchange_name=exchange_name,
                source=source,
                feed_handlers=len(fh_keys[(exchange_name, source)]),
                active=listed,
                inactive=inactive,
                delisted=delisted,
                error=error,
                total_dead=inactive + delisted,
                total=listed + inactive + delisted + error,
            )
        )
    return rows


def render(
    results: list[SymbolResult],
    fmt: Format,
    stream: TextIO,
    *,
    show_listed: bool = False,
    exchange_grouping: bool = False,
    suppress_details: bool = False,
) -> None:
    if fmt == "json":
        _render_json(results, stream)
    elif fmt == "csv":
        _render_csv(results, stream)
    elif fmt == "text":
        _render_text(
            results,
            stream,
            show_listed=show_listed,
            exchange_grouping=exchange_grouping,
            suppress_details=suppress_details,
        )
    else:
        raise ValueError(f"unknown format: {fmt!r}")


def _render_json(results: list[SymbolResult], stream: TextIO) -> None:
    json.dump([asdict(r) for r in results], stream, indent=2, sort_keys=False)
    stream.write("\n")


def _render_csv(results: list[SymbolResult], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in results:
        writer.writerow(asdict(r))


_SOURCE_TITLES: dict[SourceKind, str] = {
    "fh": "Feed handlers",
    "repeater": "Repeaters",
}

_SOURCE_NAME_HEADER: dict[SourceKind, str] = {
    "fh": "fh_name",
    "repeater": "app_name",
}

_SOURCE_SUMMARY_TITLE: dict[SourceKind, str] = {
    "fh": "Summary by feed handler",
    "repeater": "Summary by repeater",
}


def _render_text(
    results: list[SymbolResult],
    stream: TextIO,
    *,
    show_listed: bool,
    exchange_grouping: bool,
    suppress_details: bool,
) -> None:
    if not suppress_details:
        _render_detail_sections(results, stream, show_listed=show_listed)

    if exchange_grouping:
        _render_exchange_summary_table(summary_by_exchange(results), stream)
    else:
        # Two separate summary tables — one per source. Empty tables are elided
        # by _render_producer_summary_table so single-source runs render exactly
        # one table.
        _render_producer_summary_table(
            summary_by_fh(results), stream, source="fh"
        )
        _render_producer_summary_table(
            summary_by_repeater(results), stream, source="repeater"
        )

    counts = summary(results)
    stream.write(
        "\nSummary: "
        f"LISTED={counts['LISTED']} "
        f"INACTIVE={counts['INACTIVE']} "
        f"DELISTED={counts['DELISTED']} "
        f"ERROR={counts['ERROR']}\n"
    )


def _render_detail_sections(
    results: list[SymbolResult],
    stream: TextIO,
    *,
    show_listed: bool,
) -> None:
    """Emit per-producer detail rows, split into labelled sections per source.

    Sections are only emitted when they contain at least one printable row
    (INACTIVE/DELISTED/ERROR, plus LISTED when ``show_listed`` is set).
    """
    printable_statuses = {"INACTIVE", "DELISTED", "ERROR"}
    if show_listed:
        printable_statuses.add("LISTED")

    any_printed = False
    for source in ("fh", "repeater"):
        source_results = [r for r in results if r.source == source]
        if not source_results:
            continue

        grouped: dict[tuple[str, str, str], list[SymbolResult]] = defaultdict(list)
        for r in source_results:
            grouped[(r.hostname, r.fh_name, r.exchange_name)].append(r)

        printable_groups: list[
            tuple[tuple[str, str, str], list[SymbolResult]]
        ] = []
        for key in sorted(grouped):
            rows = [r for r in grouped[key] if r.status in printable_statuses]
            if rows:
                printable_groups.append((key, rows))
        if not printable_groups:
            continue

        stream.write(f"\n--- {_SOURCE_TITLES[source]} ---\n")  # type: ignore[index]
        for (hostname, producer_name, exchange_name), rows in printable_groups:
            stream.write(f"\n[{hostname}] {producer_name} ({exchange_name})\n")
            for r in rows:
                line = f"  {r.status:<9} {r.original_symbol}"
                if r.ccxt_symbol and r.ccxt_symbol != r.original_symbol:
                    line += f"  (ccxt={r.ccxt_symbol})"
                if r.detail:
                    line += f"  -- {r.detail}"
                stream.write(line + "\n")
        any_printed = True

    if not any_printed:
        stream.write("\nNo invalid symbols found.\n")


def _render_psql_table(
    *,
    title: str,
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    totals_row: tuple[str, ...],
    text_col_idx: tuple[int, ...],
    stream: TextIO,
) -> None:
    """Render a psql-style box-drawn table with a separated totals row.

    text_col_idx: indices of columns to left-align (everything else is
    right-aligned as a numeric column).
    """
    if not rows:
        return

    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows + [totals_row]))
        for i in range(len(headers))
    ]

    def hline() -> str:
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt(row: tuple[str, ...]) -> str:
        parts = []
        for i, cell in enumerate(row):
            padded = (
                cell.ljust(widths[i])
                if i in text_col_idx
                else cell.rjust(widths[i])
            )
            parts.append(f" {padded} ")
        return "|" + "|".join(parts) + "|"

    stream.write(f"\n{title}:\n")
    stream.write(hline() + "\n")
    stream.write(fmt(headers) + "\n")
    stream.write(hline() + "\n")
    for row in rows:
        stream.write(fmt(row) + "\n")
    stream.write(hline() + "\n")
    stream.write(fmt(totals_row) + "\n")
    stream.write(hline() + "\n")


def _render_producer_summary_table(
    summaries: list[FeedHandlerSummary],
    stream: TextIO,
    *,
    source: SourceKind,
) -> None:
    """Render one per-producer summary table, labelled per source.

    Empty ``summaries`` -> nothing rendered (handled inside ``_render_psql_table``
    but short-circuited here to skip the totals-row construction too).
    """
    if not summaries:
        return

    name_header = _SOURCE_NAME_HEADER[source]
    title = _SOURCE_SUMMARY_TITLE[source]

    headers = (
        name_header,
        "hostname",
        "exchange_name",
        "active",
        "inactive",
        "delisted",
        "error",
        "total dead",
        "total",
    )
    rows: list[tuple[str, ...]] = [
        (
            s.fh_name,
            s.hostname,
            s.exchange_name,
            str(s.active),
            str(s.inactive),
            str(s.delisted),
            str(s.error),
            str(s.total_dead),
            str(s.total),
        )
        for s in summaries
    ]
    totals_row: tuple[str, ...] = (
        "TOTAL",
        "",
        "",
        str(sum(s.active for s in summaries)),
        str(sum(s.inactive for s in summaries)),
        str(sum(s.delisted for s in summaries)),
        str(sum(s.error for s in summaries)),
        str(sum(s.total_dead for s in summaries)),
        str(sum(s.total for s in summaries)),
    )
    _render_psql_table(
        title=title,
        headers=headers,
        rows=rows,
        totals_row=totals_row,
        text_col_idx=(0, 1, 2),
        stream=stream,
    )


def _render_exchange_summary_table(
    summaries: list[ExchangeSummary], stream: TextIO
) -> None:
    if not summaries:
        return

    headers = (
        "exchange_name",
        "source",
        "feed_handlers",
        "active",
        "inactive",
        "delisted",
        "error",
        "total dead",
        "total",
    )
    rows: list[tuple[str, ...]] = [
        (
            s.exchange_name,
            s.source,
            str(s.feed_handlers),
            str(s.active),
            str(s.inactive),
            str(s.delisted),
            str(s.error),
            str(s.total_dead),
            str(s.total),
        )
        for s in summaries
    ]
    totals_row: tuple[str, ...] = (
        "TOTAL",
        "",
        str(sum(s.feed_handlers for s in summaries)),
        str(sum(s.active for s in summaries)),
        str(sum(s.inactive for s in summaries)),
        str(sum(s.delisted for s in summaries)),
        str(sum(s.error for s in summaries)),
        str(sum(s.total_dead for s in summaries)),
        str(sum(s.total for s in summaries)),
    )
    _render_psql_table(
        title="Summary by exchange",
        headers=headers,
        rows=rows,
        totals_row=totals_row,
        text_col_idx=(0, 1),
        stream=stream,
    )
