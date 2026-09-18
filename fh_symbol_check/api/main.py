"""Console script entry point for the API service.

Wraps :func:`fh_symbol_check.api.server.build_app` with the same argparse
conventions the CLI uses — creds file, exchange map, log level — plus
service-specific flags for bind address / port / concurrency / TTL.

Registered in ``pyproject.toml`` as::

    [project.scripts]
    find-expired-symbols-service = "fh_symbol_check.api.main:run"
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from ..cli import (
    DEFAULT_EXCHANGE_MAP,
    EXIT_OK,
    EXIT_OPERATIONAL_FAILURE,
    _install_system_trust_store,
)
from ..creds import CredsError, load_creds
from ..exchange_map import ExchangeMapError, load_exchange_map
from ..logging_config import setup_logging
from .server import build_app

logger = logging.getLogger("fh_symbol_check.api")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="find-expired-symbols-service",
        description=(
            "Long-running HTTP service that runs the same symbol-validation "
            "scans as the `find-expired-symbols` CLI. Async-poll REST + "
            "HTMX browser UI. See `/docs` on the running service for the "
            "OpenAPI reference."
        ),
    )
    p.add_argument(
        "--bind",
        default="127.0.0.1",
        help=(
            "Interface to bind (default: 127.0.0.1 — loopback only). "
            "Use 0.0.0.0 to expose on all interfaces; only do that when "
            "the box is behind a corp perimeter / VPN, since the service "
            "does no auth of its own."
        ),
    )
    p.add_argument("--port", type=int, default=8000, help="TCP port (default: 8000).")
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
            "`fh_symbol_check/data/exchange_mapping.yaml`)."
        ),
    )
    p.add_argument(
        "--job-ttl-seconds",
        type=float,
        default=3600.0,
        help=(
            "Retention window for completed jobs (default: 3600 = 1h). "
            "Older completed jobs are dropped by a background sweeper."
        ),
    )
    p.add_argument(
        "--max-concurrent-scans",
        type=int,
        default=2,
        help=(
            "Hard ceiling on parallel in-flight scans (default: 2). "
            "Excess submissions queue behind them."
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

    setup_logging("DEBUG" if args.verbose else args.log_level)

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

    app = build_app(
        creds=creds,
        exchange_map=exchange_map,
        job_ttl_seconds=args.job_ttl_seconds,
        max_concurrent_scans=args.max_concurrent_scans,
    )

    logger.info(
        "starting API on %s:%d (loaded %d exchange mapping(s), max %d "
        "concurrent scan(s), job TTL %.0fs)",
        args.bind,
        args.port,
        len(exchange_map),
        args.max_concurrent_scans,
        args.job_ttl_seconds,
    )
    # uvicorn.run blocks until the process is signalled. It handles
    # SIGINT/SIGTERM itself (returns cleanly) so we don't need our own
    # KeyboardInterrupt guard here — but we do want SSL/proxy compatibility
    # matching the CLI, so the trust-store installer runs unconditionally
    # from run() below before we ever get here.
    uvicorn.run(
        app,
        host=args.bind,
        port=args.port,
        log_level=("debug" if args.verbose else args.log_level.lower()),
        access_log=True,
    )
    return EXIT_OK


def run() -> int:
    """Console-script entry point.

    Mirrors :func:`fh_symbol_check.cli.run` — installs the OS-native SSL
    trust store first (so any ccxt call made by workers benefits) and
    wraps :func:`main` with a documented exit contract.
    """
    _install_system_trust_store()
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_OK
    except Exception:
        logger.exception("unhandled exception")
        return EXIT_OPERATIONAL_FAILURE
