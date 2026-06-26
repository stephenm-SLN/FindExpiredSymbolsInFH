"""Render SymbolResult lists as text, JSON, or CSV."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Literal, TextIO

from .models import SymbolResult, SymbolStatus

Format = Literal["text", "json", "csv"]


@dataclass(frozen=True)
class FeedHandlerSummary:
    """Per-feed-handler aggregate counts for the summary table.

    Buckets are mutually exclusive: active + inactive + delisted + error == total.
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

_CSV_FIELDS = [
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
    """Return the subset of ``results`` belonging to feed handlers that have at
    least one ``ERROR`` row. All rows of an offending FH are retained (not just
    its ERROR rows) so the surviving FH summaries remain complete.
    """
    offending: set[tuple[str, str, str]] = {
        (r.hostname, r.fh_name, r.exchange_name)
        for r in results
        if r.status == "ERROR"
    }
    if not offending:
        return []
    return [
        r
        for r in results
        if (r.hostname, r.fh_name, r.exchange_name) in offending
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
    """Aggregate results into one row per feed handler.

    Sorted by (hostname, fh_name, exchange_name) for stable output.
    """
    grouped: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for r in results:
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


def render(
    results: list[SymbolResult],
    fmt: Format,
    stream: TextIO,
    *,
    show_listed: bool = False,
) -> None:
    if fmt == "json":
        _render_json(results, stream)
    elif fmt == "csv":
        _render_csv(results, stream)
    elif fmt == "text":
        _render_text(results, stream, show_listed=show_listed)
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


def _render_text(
    results: list[SymbolResult], stream: TextIO, *, show_listed: bool
) -> None:
    grouped: dict[tuple[str, str, str], list[SymbolResult]] = defaultdict(list)
    for r in results:
        grouped[(r.hostname, r.fh_name, r.exchange_name)].append(r)

    printable_statuses = {"INACTIVE", "DELISTED", "ERROR"}
    if show_listed:
        printable_statuses.add("LISTED")

    any_printed = False
    for key in sorted(grouped):
        hostname, fh_name, exchange_name = key
        rows = [r for r in grouped[key] if r.status in printable_statuses]
        if not rows:
            continue
        any_printed = True
        stream.write(f"\n[{hostname}] {fh_name} ({exchange_name})\n")
        for r in rows:
            line = f"  {r.status:<9} {r.original_symbol}"
            if r.ccxt_symbol and r.ccxt_symbol != r.original_symbol:
                line += f"  (ccxt={r.ccxt_symbol})"
            if r.detail:
                line += f"  -- {r.detail}"
            stream.write(line + "\n")

    if not any_printed:
        stream.write("\nNo invalid symbols found.\n")

    _render_fh_summary_table(summary_by_fh(results), stream)

    counts = summary(results)
    stream.write(
        "\nSummary: "
        f"LISTED={counts['LISTED']} "
        f"INACTIVE={counts['INACTIVE']} "
        f"DELISTED={counts['DELISTED']} "
        f"ERROR={counts['ERROR']}\n"
    )


def _render_fh_summary_table(
    summaries: list[FeedHandlerSummary], stream: TextIO
) -> None:
    if not summaries:
        return

    headers = (
        "fh_name",
        "hostname",
        "exchange_name",
        "active",
        "inactive",
        "delisted",
        "error",
        "total dead",
        "total",
    )
    text_col_idx = (0, 1, 2)

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

    stream.write("\nSummary by feed handler:\n")
    stream.write(hline() + "\n")
    stream.write(fmt(headers) + "\n")
    stream.write(hline() + "\n")
    for row in rows:
        stream.write(fmt(row) + "\n")
    stream.write(hline() + "\n")
    stream.write(fmt(totals_row) + "\n")
    stream.write(hline() + "\n")
