"""Shared dataclasses and types for the fh_symbol_check package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SymbolStatus = Literal["LISTED", "INACTIVE", "DELISTED", "ERROR"]


@dataclass(frozen=True)
class FeedHandlerRow:
    """A single row from crypto_db.fh_config after parsing cover_names."""

    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str  # raw DB value, e.g. "HUOBI"
    symbols: tuple[str, ...]  # parsed from cover_names CSV


@dataclass(frozen=True)
class ResolvedTask:
    """A (row, symbol) pair after exchange-name mapping and symbol translation."""

    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str  # raw DB value, e.g. "HUOBI"
    ccxt_id: str  # mapped value, e.g. "htx"
    original_symbol: str  # raw cover_names value, e.g. "1000BONK/USDC-PERP"
    ccxt_symbol: str  # translated value, e.g. "1000BONK/USDC:USDC"


@dataclass(frozen=True)
class SymbolResult:
    """Outcome of validating one (exchange, symbol) pair against ccxt."""

    service_id: int
    fh_name: str
    hostname: str
    exchange_name: str
    ccxt_id: str
    original_symbol: str
    ccxt_symbol: str
    status: SymbolStatus
    detail: str = ""
