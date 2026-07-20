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
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import TextIO

from .creds import CredsError, load_creds
from .db import DBError, fetch_feed_handlers
from .exchange_map import ExchangeMapError, load_exchange_map
from .logging_config import setup_logging
from .reporter import keep_fhs_with_errors, render, summary
from .validator import build_tasks, classify_symbols, filter_by_symbol

logger = logging.getLogger("fh_symbol_check")

EXIT_OK = 0
EXIT_INVALID_FOUND = 1
EXIT_OPERATIONAL_FAILURE = 2
EXIT_INTERRUPT = 130

# The packaged exchange_mapping.yaml. Ships inside the wheel; resolves to the
# in-tree copy when running from a source checkout (both cases land on the
# same on-disk file since setuptools installs unzipped by default). Users can
# still override with `--exchange-map /path/to/custom.yaml`.
DEFAULT_EXCHANGE_MAP: Path = Path(
    str(_pkg_files("fh_symbol_check").joinpath("data/exchange_mapping.yaml"))
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
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
        "--symbol",
        default=None,
        help=(
            "Find all occurrences of this exact symbol across the scanned "
            "feed handlers. Case-sensitive; matched against both the FH "
            "symbol (cover_names value) AND the translated venue symbol. "
            "Used alone, implies --all. Combines with --hostname / "
            "--exchange-name / --errors-only / --exchange-grouping. When "
            "set, LISTED rows are auto-included in text output so every "
            "occurrence is visible."
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
        "--exchange-grouping",
        action="store_true",
        help=(
            "Text output only: replace the per-feed-handler summary table "
            "with a per-exchange one. Per-symbol detail rows are suppressed "
            "on stdout (use --output-file to keep them in the written file). "
            "Has no effect on JSON/CSV output."
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
        default=DEFAULT_EXCHANGE_MAP,
        help=(
            "Exchange-name -> ccxt-id map (default: bundled "
            "`fh_symbol_check/data/exchange_mapping.yaml`, resolved via "
            "importlib.resources)."
        ),
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
    if not (args.all or args.hostname or args.exchange_name or args.symbol):
        parser.error("specify --hostname, --exchange-name, --symbol, or --all")

    level = "DEBUG" if args.verbose else args.log_level
    setup_logging(level)

    filter_desc = _describe_filters(args.hostname, args.exchange_name, args.symbol)
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

    if args.symbol:
        before_tasks = len(tasks)
        before_errors = len(early_errors)
        tasks, early_errors = filter_by_symbol(tasks, early_errors, args.symbol)
        logger.info(
            "--symbol %r: matched %d/%d task(s) and %d/%d unmappable-exchange error(s)",
            args.symbol,
            len(tasks), before_tasks,
            len(early_errors), before_errors,
        )
        if not tasks and not early_errors:
            logger.warning(
                "--symbol %r: 0 occurrences found in the scanned FH set",
                args.symbol,
            )

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

    suppress_details = args.exchange_grouping and args.output_file is None
    # --symbol turns the tool into a search; every occurrence should be
    # visible regardless of status, so auto-enable show_listed.
    effective_show_listed = args.show_listed or bool(args.symbol)

    try:
        with _open_output(args.output_file) as stream:
            render(
                rendered,
                args.output,
                stream,
                show_listed=effective_show_listed,
                exchange_grouping=args.exchange_grouping,
                suppress_details=suppress_details,
            )
    except OSError as e:
        logger.error("failed to write report: %s", e)
        return EXIT_OPERATIONAL_FAILURE

    invalid = counts["DELISTED"] + counts["INACTIVE"]
    if args.fail_on_invalid and invalid > 0:
        return EXIT_INVALID_FOUND
    return EXIT_OK


def _describe_filters(
    hostname: str | None,
    exchange_name: str | None,
    symbol: str | None = None,
) -> str:
    parts: list[str] = []
    if hostname:
        parts.append(f"hostname LIKE %{hostname}%")
    if exchange_name:
        parts.append(f"exchange_name={exchange_name!r}")
    if symbol:
        parts.append(f"symbol={symbol!r}")
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


def run() -> int:
    """Console-script entry point.

    Wraps :func:`main` with a KeyboardInterrupt handler and a catch-all
    exception guard so callers (systemd, cron, Airflow, ad-hoc SSH) always
    see a documented exit code rather than a Python traceback.
    """
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPT
    except Exception:
        logger.exception("unhandled exception")
        return EXIT_OPERATIONAL_FAILURE
