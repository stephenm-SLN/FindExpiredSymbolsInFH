"""Ondo Perps custom symbol checker.

Ondo Perps is a tokenized-equity / crypto perpetuals venue
(``https://app.ondoperps.xyz``). It is **not** supported by ``ccxt``
(checked against 4.5.60), so we hit its public REST API:

    GET https://api.ondoperps.xyz/v1/markets

Venue tickers are ``<BASE>-<QUOTE>.P`` (USD-quoted, e.g. ``NVDA-USD.P``,
``US100-USD.P``). The FH stores ``<BASE>/<QUOTE>-PERP``;
``_translate_ondoperps`` converts the slash/PERP form.

The ``/v1/markets`` payload has no active/inactive flag — every
``tradingPairs`` entry is presumed live, so this venue only ever
surfaces ``LISTED`` or ``DELISTED`` (no ``INACTIVE``).

Docs: https://docs.ondoperps.xyz/api-reference/markets/get-markets
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

ONDOPERPS_MARKETS_URL = "https://api.ondoperps.xyz/v1/markets"
ONDOPERPS_FETCH_TIMEOUT_SEC = 15
USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"


class OndoPerpsFetchError(Exception):
    """Raised on any failure to fetch or parse the Ondo Perps markets payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch Ondo Perps markets and return ``{TICKER_UPPER: True}``."""
    logger.info("fetching ONDOPERPS markets from %s", ONDOPERPS_MARKETS_URL)
    req = Request(
        ONDOPERPS_MARKETS_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=ONDOPERPS_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise OndoPerpsFetchError(
            f"failed to fetch ONDOPERPS markets from {ONDOPERPS_MARKETS_URL}: {e}"
        ) from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OndoPerpsFetchError(
            f"ONDOPERPS response is not valid JSON: {e}"
        ) from e

    return _parse_markets(payload)


def _parse_markets(payload: object) -> dict[str, bool]:
    """Parse a ``GET /v1/markets`` payload into ``{TICKER_UPPER: True}``.

    Pure function so tests can exercise parsing without network I/O.
    Key is ``market`` (``NVDA-USD.P``). Spot pairs in ``result.spot`` are
    ignored — FH ``ONDOPERPS`` rows are all ``-PERP``.
    """
    if not isinstance(payload, dict):
        raise OndoPerpsFetchError(
            f"unexpected ONDOPERPS response shape: {type(payload).__name__}"
        )
    if payload.get("success") is False:
        err = payload.get("error") or payload.get("error_code") or "success=false"
        raise OndoPerpsFetchError(f"ONDOPERPS API error: {err}")

    result = payload.get("result")
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise OndoPerpsFetchError(
            f"unexpected ONDOPERPS result field: {type(result).__name__}"
        )
    perps = result.get("perps")
    if perps is None:
        return {}
    if not isinstance(perps, dict):
        raise OndoPerpsFetchError(
            f"unexpected ONDOPERPS perps field: {type(perps).__name__}"
        )
    pairs = perps.get("tradingPairs")
    if pairs is None:
        return {}
    if not isinstance(pairs, list):
        raise OndoPerpsFetchError(
            f"unexpected ONDOPERPS tradingPairs field: {type(pairs).__name__}"
        )

    out: dict[str, bool] = {}
    for entry in pairs:
        if not isinstance(entry, dict):
            continue
        name = entry.get("market")
        if not isinstance(name, str):
            continue
        key = name.strip().upper()
        if not key:
            continue
        out[key] = True
    return out
