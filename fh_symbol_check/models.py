"""Shared dataclasses and types for the fh_symbol_check package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SymbolStatus = Literal["LISTED", "INACTIVE", "DELISTED", "ERROR"]

# "fh"       = row came from crypto_db.fh_config       (feed handler)
# "repeater" = row came from crypto_db.repeater_feeds  (repeater)
#
# The two sources share the same downstream pipeline (exchange mapping,
# symbol translation, ccxt / custom-venue classification) because they
# describe the same underlying venue subscriptions. `source` is retained
# on ResolvedTask and SymbolResult so the reporter can split them back
# into per-source summary tables.
SourceKind = Literal["fh", "repeater"]


@dataclass(frozen=True)
class FeedHandlerRow:
    """One producer row (from ``fh_config`` or ``repeater_feeds``) after
    parsing the CSV symbol column.

    For feed-handler rows (``source="fh"``): ``fh_name`` is the DB
    ``fh_name`` column, ``service_id`` is populated.
    For repeater rows (``source="repeater"``): ``fh_name`` holds the DB
    ``app_name`` column value, ``service_id`` is ``None``.
    """

    fh_name: str  # fh_config.fh_name  OR  repeater_feeds.app_name
    hostname: str
    exchange_name: str  # raw DB value, e.g. "HUOBI"
    symbols: tuple[str, ...]  # parsed from cover_names / instruments CSV
    source: SourceKind = "fh"
    service_id: int | None = None  # None for repeaters (no such column)


@dataclass(frozen=True)
class ResolvedTask:
    """A (row, symbol) pair after exchange-name mapping and symbol translation."""

    fh_name: str
    hostname: str
    exchange_name: str  # raw DB value, e.g. "HUOBI"
    ccxt_id: str  # mapped value, e.g. "htx"
    original_symbol: str  # raw cover_names value, e.g. "1000BONK/USDC-PERP"
    ccxt_symbol: str  # translated value, e.g. "1000BONK/USDC:USDC"
    source: SourceKind = "fh"
    service_id: int | None = None


@dataclass(frozen=True)
class SymbolResult:
    """Outcome of validating one (exchange, symbol) pair against ccxt."""

    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    status: SymbolStatus
    detail: str = ""
    source: SourceKind = "fh"
    service_id: int | None = None
