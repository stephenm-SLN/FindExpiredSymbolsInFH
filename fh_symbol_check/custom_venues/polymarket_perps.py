"""Polymarket Perps custom symbol checker.

Polymarket Perps is the new (April 2026) perpetual-futures product on
Polymarket. It's not in ccxt; we hit its public ``/v1/info/instruments``
endpoint to learn the universe of listed instruments.

The endpoint returns a JSON list of instrument objects with a ``symbol``
field (e.g. ``"BTC-USD"``, ``"GOLD-USD"``, ``"WTIOIL-USD"``). There is no
explicit ``active`` flag — every instrument returned is presumed live, so
this venue only ever surfaces ``LISTED`` or ``DELISTED`` (no ``INACTIVE``).

URL: https://api.perpetuals.polymarket.com/v1/info/instruments
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

POLYMARKETPERPS_INSTRUMENTS_URL = (
    "https://api.perpetuals.polymarket.com/v1/info/instruments"
)
POLYMARKETPERPS_FETCH_TIMEOUT_SEC = 15
USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"


class PolymarketPerpsFetchError(Exception):
    """Raised on any failure to fetch or parse the Polymarket Perps payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch Polymarket Perps' instrument list and return ``{SYMBOL_UPPER: True}``."""
    logger.info(
        "fetching Polymarket Perps instruments from %s",
        POLYMARKETPERPS_INSTRUMENTS_URL,
    )
    req = Request(
        POLYMARKETPERPS_INSTRUMENTS_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=POLYMARKETPERPS_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise PolymarketPerpsFetchError(
            f"failed to fetch Polymarket Perps instruments: {e}"
        ) from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PolymarketPerpsFetchError(
            f"Polymarket Perps response is not valid JSON: {e}"
        ) from e

    return _parse_instruments(payload)


def _parse_instruments(payload: object) -> dict[str, bool]:
    """Parse a ``/v1/info/instruments`` payload into ``{SYMBOL_UPPER: True}``.

    Pure function so tests can exercise the parsing without network I/O.
    """
    if not isinstance(payload, list):
        raise PolymarketPerpsFetchError(
            f"unexpected Polymarket Perps response shape: {type(payload).__name__}"
        )
    result: dict[str, bool] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        if not isinstance(symbol, str):
            continue
        result[symbol.upper()] = True
    return result
