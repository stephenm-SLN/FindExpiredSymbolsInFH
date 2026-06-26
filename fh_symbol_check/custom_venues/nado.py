"""Nado DEX custom symbol checker.

Nado is not in ccxt 4.5.60, but exposes a public ``/v2/symbols`` archive
endpoint that returns a ``{symbol: info}`` map with a ``trading_status``
field. The validator turns that into our ``LISTED`` / ``INACTIVE`` /
``DELISTED`` triple:

    trading_status == "live"                                -> LISTED
    trading_status in {post_only, reduce_only,
                       soft_reduce_only, not_tradable}      -> INACTIVE
    symbol absent from the response                         -> DELISTED

Docs: https://docs.nado.xyz/developer-resources/api/v2/symbols
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

NADO_SYMBOLS_URL = "https://archive.prod.nado.xyz/v2/symbols"
NADO_FETCH_TIMEOUT_SEC = 15
# Nado's WAF rejects the default Python-urllib UA with HTTP 403, so we send a
# descriptive one that identifies the tool. Keep it stable so the venue can
# rate-limit or contact us if needed.
USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"


class NadoFetchError(Exception):
    """Raised on any failure to fetch or parse the Nado symbols payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch the Nado symbol universe and return ``{SYMBOL_UPPER: is_live}``."""
    logger.info("fetching Nado symbols from %s", NADO_SYMBOLS_URL)
    req = Request(
        NADO_SYMBOLS_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=NADO_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise NadoFetchError(f"failed to fetch Nado symbols: {e}") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise NadoFetchError(f"Nado response is not valid JSON: {e}") from e

    return _parse_symbols(payload)


def _parse_symbols(payload: object) -> dict[str, bool]:
    """Parse the V2 ``/symbols`` payload into ``{SYMBOL_UPPER: is_live}``.

    Pure function so tests can exercise the parsing without network I/O.
    Keys we don't recognise are skipped silently (not raised) so a future
    Nado field addition doesn't crash an otherwise-healthy run.
    """
    if not isinstance(payload, dict):
        raise NadoFetchError(
            f"unexpected Nado response shape: {type(payload).__name__}"
        )
    result: dict[str, bool] = {}
    for symbol, info in payload.items():
        if not isinstance(symbol, str) or not isinstance(info, dict):
            continue
        result[symbol.upper()] = info.get("trading_status") == "live"
    return result
