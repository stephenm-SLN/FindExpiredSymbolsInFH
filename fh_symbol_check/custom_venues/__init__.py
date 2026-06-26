"""Custom venue checkers for exchanges that ccxt does not support.

Each entry in :data:`CUSTOM_VENUES` is a callable that returns a mapping from
the venue's symbol (uppercased) -> ``is_live`` boolean. The validator routes
FH rows whose ``exchange_name`` matches a key here *before* consulting the
ccxt ``exchange_mapping.yaml``; FH rows that aren't in either fall back to
``ERROR``.

Adding a new custom venue:

1. Create ``fh_symbol_check/custom_venues/<venue>.py`` that exposes a
   ``fetch_symbols() -> dict[str, bool]`` function. The function should raise
   if anything fails (the validator catches it and emits ``ERROR`` rows for
   every task on that venue).
2. Register it in :data:`CUSTOM_VENUES` below, keyed by the uppercase FH
   ``exchange_name``.
3. Add unit tests against a fixture JSON so the module can be exercised
   without network I/O.
"""

from __future__ import annotations

from typing import Callable, Mapping

from . import nado, polymarket_perps

CustomVenueChecker = Callable[[], Mapping[str, bool]]

CUSTOM_VENUES: dict[str, CustomVenueChecker] = {
    "NADO": nado.fetch_symbols,
    "POLYMARKETPERPS": polymarket_perps.fetch_symbols,
}

CUSTOM_VENUE_PREFIX = "custom:"


def is_custom_venue(exchange_name: str) -> bool:
    return exchange_name.upper() in CUSTOM_VENUES


def custom_id_for(exchange_name: str) -> str:
    """Sentinel ``ccxt_id`` used to route a custom venue through the validator's
    per-exchange grouping (e.g. ``custom:NADO``)."""
    return f"{CUSTOM_VENUE_PREFIX}{exchange_name.upper()}"


def get_checker(custom_id: str) -> CustomVenueChecker:
    """Look up a checker by its sentinel id (``custom:NADO`` -> nado fetcher)."""
    if not custom_id.startswith(CUSTOM_VENUE_PREFIX):
        raise KeyError(custom_id)
    name = custom_id[len(CUSTOM_VENUE_PREFIX):]
    return CUSTOM_VENUES[name]
