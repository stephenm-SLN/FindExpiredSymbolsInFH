"""Arcus custom symbol checker.

Arcus is a DEX built by dYdX Labs (tokenized equities + crypto perps).
It is **not** supported by ``ccxt`` (checked against 4.5.60), so we hit
its public REST API:

    GET https://api.arcus.xyz/v1/markets

Venue tickers are ``<BASE>-<QUOTE>`` (currently all ``PERPETUAL``,
USD-quoted, e.g. ``BTC-USD``, ``AAPL-USD``). The FH stores
``<BASE>/<QUOTE>-PERP``; ``_translate_arcus`` converts the slash/PERP
form.

Status mapping (venue ``status`` field; docs: OFFLINE markets are
returned for visibility but should not be quoted or traded):

    ``ONLINE``                  -> LISTED
    ``OFFLINE``                 -> INACTIVE
    symbol absent from payload  -> DELISTED

Docs: https://docs.arcus.xyz/api-reference/public/get-markets
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ARCUS_MARKETS_URL = "https://api.arcus.xyz/v1/markets"
ARCUS_FETCH_TIMEOUT_SEC = 15
USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"

_LIVE_STATUSES = frozenset({"ONLINE"})
_INACTIVE_STATUSES = frozenset({"OFFLINE"})


class ArcusFetchError(Exception):
    """Raised on any failure to fetch or parse the Arcus markets payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch Arcus markets and return ``{TICKER_UPPER: is_live}``."""
    logger.info("fetching ARCUS markets from %s", ARCUS_MARKETS_URL)
    req = Request(
        ARCUS_MARKETS_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=ARCUS_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise ArcusFetchError(
            f"failed to fetch ARCUS markets from {ARCUS_MARKETS_URL}: {e}"
        ) from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ArcusFetchError(f"ARCUS response is not valid JSON: {e}") from e

    return _parse_markets(payload)


def _parse_markets(payload: object) -> dict[str, bool]:
    """Parse a ``GET /v1/markets`` payload into ``{TICKER_UPPER: is_live}``.

    Pure function so tests can exercise parsing without network I/O.
    Key is ``marketDisplayName`` (``BTC-USD``). Unrecognised / missing
    statuses are skipped so those symbols stay absent and classify as
    DELISTED.
    """
    if not isinstance(payload, dict):
        raise ArcusFetchError(
            f"unexpected ARCUS response shape: {type(payload).__name__}"
        )
    markets = payload.get("markets")
    if markets is None:
        return {}
    if not isinstance(markets, list):
        raise ArcusFetchError(
            f"unexpected ARCUS markets field: {type(markets).__name__}"
        )

    result: dict[str, bool] = {}
    for entry in markets:
        if not isinstance(entry, dict):
            continue
        name = entry.get("marketDisplayName")
        if not isinstance(name, str):
            continue
        key = name.strip().upper()
        if not key:
            continue
        status = entry.get("status")
        if status in _LIVE_STATUSES:
            result[key] = True
        elif status in _INACTIVE_STATUSES:
            result[key] = False
    return result
