"""Robinhood Lighter custom symbol checker (RHLIGHTER).

Robinhood Chain runs its own Lighter deployment. It shares the API
shape with mainnet Lighter (``/api/v1/orderBookDetails``) but **not**
the market universe, quote currency, or hostname. ccxt's ``lighter``
id points at ``mainnet.zklighter.elliot.ai`` (USDC, 235 perps). This
checker hits ``https://api.rh.lighter.xyz`` (USDG, ~57 perps + spot).
Mapping ``RHLIGHTER`` to ccxt ``lighter`` would classify against the
wrong book — 14 RH-only markets and 192 mainnet-only ones.

Venue tickers:

    perp  -> bare base (``BTC``, ``ETH``, ``AAPL``)
    spot  -> ``<BASE>/USDG`` (``META/USDG``, ``ETH/USDG``)

The FH stores ``<BASE>/<QUOTE>-PERP`` / ``<BASE>-PERP``; the
translator strips that down to the bare base. Spot is identity.

Status mapping (venue ``status`` field):

    ``active``                  -> LISTED
    ``inactive``                -> INACTIVE
    symbol absent from payload  -> DELISTED

Docs: https://apidocs.rh.lighter.xyz/reference/orderbookdetails
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

RHLIGHTER_DETAILS_URL = "https://api.rh.lighter.xyz/api/v1/orderBookDetails"
RHLIGHTER_FETCH_TIMEOUT_SEC = 15
USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"

_LIVE_STATUSES = frozenset({"active"})
_INACTIVE_STATUSES = frozenset({"inactive"})


class RHLighterFetchError(Exception):
    """Raised on any failure to fetch or parse the RH Lighter payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch RH Lighter's perp + spot books and return
    ``{SYMBOL_UPPER: is_live}``."""
    logger.info("fetching RHLIGHTER markets from %s", RHLIGHTER_DETAILS_URL)
    req = Request(
        RHLIGHTER_DETAILS_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=RHLIGHTER_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise RHLighterFetchError(
            f"failed to fetch RHLIGHTER markets from {RHLIGHTER_DETAILS_URL}: {e}"
        ) from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RHLighterFetchError(
            f"RHLIGHTER response is not valid JSON: {e}"
        ) from e

    return _parse_order_books(payload)


def _parse_order_books(payload: object) -> dict[str, bool]:
    """Parse an ``/api/v1/orderBookDetails`` payload.

    Pure function so tests can exercise parsing without network I/O.
    Perps live in ``order_book_details``; spot in
    ``spot_order_book_details``. Entries without a usable symbol or
    with an unrecognised status are skipped (absent → DELISTED).
    """
    if not isinstance(payload, dict):
        raise RHLighterFetchError(
            f"unexpected RHLIGHTER response shape: {type(payload).__name__}"
        )

    result: dict[str, bool] = {}
    for key in ("order_book_details", "spot_order_book_details"):
        books = payload.get(key)
        if books is None:
            continue
        if not isinstance(books, list):
            raise RHLighterFetchError(
                f"unexpected RHLIGHTER {key} field: {type(books).__name__}"
            )
        for entry in books:
            parsed = _parse_one(entry)
            if parsed is None:
                continue
            symbol, is_live = parsed
            if is_live or symbol not in result:
                result[symbol] = is_live
    return result


def _parse_one(entry: object) -> tuple[str, bool] | None:
    if not isinstance(entry, dict):
        return None
    symbol = entry.get("symbol")
    if not isinstance(symbol, str):
        return None
    key = symbol.strip().upper()
    if not key:
        return None
    status = entry.get("status")
    if status in _LIVE_STATUSES:
        return key, True
    if status in _INACTIVE_STATUSES:
        return key, False
    return None
