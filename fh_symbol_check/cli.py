"""CLI entry point for find_expired_symbols.py.

Exit codes:
    0   success; no invalid symbols (or --no-fail-on-invalid)
    1   success; >=1 DELISTED/INACTIVE and --fail-on-invalid (default)
    2   operational failure (creds, DB, args, exchange map, I/O, unhandled)
    130 KeyboardInterrupt
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TextIO

from .creds import CredsError, load_creds
from .db import DBError, fetch_feed_handlers
from .exchange_map import ExchangeMapError, load_exchange_map
from .logging_config import setup_logging
from .reporter import keep_fhs_with_errors, render, summary
from .validator import build_tasks, classify_symbols

logger = logging.getLogger("fh_symbol_check")

EXIT_OK = 0
EXIT_INVALID_FOUND = 1
EXIT_OPERATIONAL_FAILURE = 2
EXIT_INTERRUPT = 130


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="find_expired_symbols.py",
        description=(
            "Pull symbols configured in the Feed Handlers and report any that "
            "are no longer valid (delisted/inactive) on their exchange. Filter "
            "by --hostname and/or --exchange-name, or pass --all to scan every "
            "feed handler."
        ),
    )
    p.add_argument(
        "--hostname",
        default=None,
        help="Hostname substring for the SQL LIKE filter (e.g. TA-TKY-A-41).",
    )
    p.add_argument(
        "--exchange-name",
        default=None,
        help=(
            "Exchange name from fh_config to filter by (e.g. HUOBI). "
            "Case-insensitive exact match."
        ),
    )
    p.add_argument(
        "--all",
        action="store_true",
        help=(
            "Scan every feed handler row in fh_config (no filters). "
            "Mutually exclusive with --hostname/--exchange-name."
        ),
    )
    p.add_argument(
        "--output",
        choices=["text", "json", "csv"],
        default="text",
        help="Report format (default: text).",
    )
    p.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Write report to this path instead of stdout.",
    )
    p.add_argument(
        "--show-listed",
        action="store_true",
        help="Include LISTED rows in text output (default: omit).",
    )
    p.add_argument(
        "--errors-only",
        action="store_true",
        help=(
            "Filter the report (text/JSON/CSV) to feed handlers that have at "
            "least one ERROR row. Does not affect the summary log line or the "
            "exit code, which still reflect the full run."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max parallel exchanges (default: 4).",
    )

    invalid_group = p.add_mutually_exclusive_group()
    invalid_group.add_argument(
        "--fail-on-invalid",
        dest="fail_on_invalid",
        action="store_true",
        default=True,
        help="Exit 1 when any DELISTED/INACTIVE found (default).",
    )
    invalid_group.add_argument(
        "--no-fail-on-invalid",
        dest="fail_on_invalid",
        action="store_false",
        help="Always exit 0 unless an operational error occurs.",
    )

    p.add_argument(
        "--creds-file",
        type=Path,
        default=Path(".DBCreds.yaml"),
        help="Credentials YAML (default: ./.DBCreds.yaml).",
    )
    p.add_argument(
        "--creds-section",
        default="crypto_db",
        help="Section in the creds YAML (default: crypto_db).",
    )
    p.add_argument(
        "--exchange-map",
        type=Path,
        default=Path("exchange_mapping.yaml"),
        help="Exchange-name -> ccxt-id map (default: ./exchange_mapping.yaml).",
    )

    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Shortcut for --log-level DEBUG.",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.all and (args.hostname or args.exchange_name):
        parser.error("--all cannot be combined with --hostname or --exchange-name")
    if not args.all and not args.hostname and not args.exchange_name:
        parser.error("specify --hostname, --exchange-name, or --all")

    level = "DEBUG" if args.verbose else args.log_level
    setup_logging(level)

    filter_desc = _describe_filters(args.hostname, args.exchange_name)
    logger.info("filter: %s", filter_desc)

    try:
        creds = load_creds(args.creds_file, section=args.creds_section)
    except CredsError as e:
        logger.error("creds error: %s", e)
        return EXIT_OPERATIONAL_FAILURE

    try:
        exchange_map = load_exchange_map(args.exchange_map)
    except ExchangeMapError as e:
        logger.error("exchange map error: %s", e)
        return EXIT_OPERATIONAL_FAILURE

    try:
        rows = fetch_feed_handlers(
            creds,
            hostname_pattern=args.hostname,
            exchange_name=args.exchange_name,
        )
    except DBError as e:
        logger.error("DB error: %s", e)
        return EXIT_OPERATIONAL_FAILURE

    if not rows:
        logger.warning("no fh_config rows matched filters (%s)", filter_desc)

    tasks, early_errors = build_tasks(rows, exchange_map)
    results = early_errors + classify_symbols(tasks, concurrency=args.concurrency)

    counts = summary(results)
    logger.info(
        "summary: LISTED=%d INACTIVE=%d DELISTED=%d ERROR=%d",
        counts["LISTED"],
        counts["INACTIVE"],
        counts["DELISTED"],
        counts["ERROR"],
    )

    rendered = keep_fhs_with_errors(results) if args.errors_only else results
    if args.errors_only:
        logger.info(
            "--errors-only: rendering %d row(s) from feed handlers with ERROR status",
            len(rendered),
        )

    try:
        with _open_output(args.output_file) as stream:
            render(rendered, args.output, stream, show_listed=args.show_listed)
    except OSError as e:
        logger.error("failed to write report: %s", e)
        return EXIT_OPERATIONAL_FAILURE

    invalid = counts["DELISTED"] + counts["INACTIVE"]
    if args.fail_on_invalid and invalid > 0:
        return EXIT_INVALID_FOUND
    return EXIT_OK


def _describe_filters(hostname: str | None, exchange_name: str | None) -> str:
    parts: list[str] = []
    if hostname:
        parts.append(f"hostname LIKE %{hostname}%")
    if exchange_name:
        parts.append(f"exchange_name={exchange_name!r}")
    return ", ".join(parts) if parts else "ALL (no filters)"


def _open_output(path: Path | None) -> "_OutputCtx":
    if path is None:
        return _OutputCtx(sys.stdout, close=False)
    fh = open(path, "w", encoding="utf-8")
    return _OutputCtx(fh, close=True)


class _OutputCtx:
    """Tiny context manager that closes the file iff we opened it."""

    def __init__(self, stream: TextIO, *, close: bool) -> None:
        self._stream = stream
        self._close = close

    def __enter__(self) -> TextIO:
        return self._stream

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._close:
            self._stream.close()
