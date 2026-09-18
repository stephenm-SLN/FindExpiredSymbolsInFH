"""Injective (Helix) custom symbol checker.

Injective is a Cosmos-SDK L1 with an on-chain orderbook (spot +
perpetuals + dated futures). It is **not** supported by ``ccxt``
(checked against 4.5.60), so we hit the public chain LCD REST API
directly. Helix is the UI; the market list lives on-chain.

    GET {LCD}/injective/exchange/v1beta1/spot/markets?status={status}
    GET {LCD}/injective/exchange/v1beta1/derivative/markets?status={status}

LCD defaults to ``Active`` only. We also fetch ``Paused`` and
``Expired`` so a known-but-untradeable market surfaces as INACTIVE
instead of DELISTED. ``Demolished`` is *not* fetched: those markets
are gone, so they stay absent from the universe and classify as
DELISTED.

Status mapping:

    ``Active``                              -> LISTED
    ``Paused`` / ``Expired``                -> INACTIVE
    symbol absent (incl. ``Demolished``)    -> DELISTED

Ticker format on the venue is ``INJ/USDT`` (spot) and
``BTC/USDC PERP`` (perps — space, not hyphen). The FH stores
``<BASE>/<QUOTE>-PERP``; ``_translate_injective`` converts the hyphen
form. Spot symbols pass through as identity.

Docs: https://docs.injective.network/infra/public-endpoints
"""

from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

LCD_BASE = "https://sentry.lcd.injective.network"
_SPOT_PATH = "/injective/exchange/v1beta1/spot/markets"
_DERIV_PATH = "/injective/exchange/v1beta1/derivative/markets"
# Demolished is intentionally omitted — see module docstring.
_FETCH_STATUSES: tuple[str, ...] = ("Active", "Paused", "Expired")
_LIVE_STATUSES = frozenset({"Active"})
_INACTIVE_STATUSES = frozenset({"Paused", "Expired"})
_FETCH_TIMEOUT_SEC = 15

USER_AGENT = "FindExpiredSymbolsInFH/1.0 (symbol-validation)"


class InjectiveFetchError(Exception):
    """Raised on any failure to fetch or parse an Injective markets payload."""


def fetch_symbols() -> dict[str, bool]:
    """Fetch Injective spot + derivative markets and return
    ``{TICKER_UPPER: is_live}``.

    Six LCD calls (spot × 3 statuses + derivative × 3 statuses). Any
    single failure raises so we don't silently classify against a
    partial universe.
    """
    result: dict[str, bool] = {}
    for path, kind in ((_SPOT_PATH, "spot"), (_DERIV_PATH, "derivative")):
        for status in _FETCH_STATUSES:
            payload = _fetch(path, status)
            for ticker, is_live in _parse_markets(kind, payload).items():
                if is_live or ticker not in result:
                    result[ticker] = is_live
    logger.info(
        "fetched %d Injective symbols (spot + derivative, statuses=%s)",
        len(result),
        ",".join(_FETCH_STATUSES),
    )
    return result


def _fetch(path: str, status: str) -> object:
    url = f"{LCD_BASE}{path}?status={status}"
    logger.info("fetching Injective %s markets from %s", status, url)
    req = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except (URLError, TimeoutError) as e:
        raise InjectiveFetchError(
            f"failed to fetch Injective {status} markets from {url}: {e}"
        ) from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise InjectiveFetchError(
            f"Injective {status} response from {url} is not valid JSON: {e}"
        ) from e


def _parse_markets(kind: str, payload: object) -> dict[str, bool]:
    """Parse one LCD ``/markets`` payload into ``{TICKER_UPPER: is_live}``.

    Pure function so tests can exercise parsing without network I/O.
    Spot entries are market dicts; derivative entries wrap the market
    under ``{"market": {...}}``. Tickers are stripped (LCD has at least
    one trailing-space ticker) and uppercased. ``Demolished`` /
    unknown / missing statuses are skipped so those symbols stay
    absent and classify as DELISTED.
    """
    if not isinstance(payload, dict):
        raise InjectiveFetchError(
            f"unexpected Injective {kind} response shape: {type(payload).__name__}"
        )
    markets = payload.get("markets")
    if markets is None:
        return {}
    if not isinstance(markets, list):
        raise InjectiveFetchError(
            f"unexpected Injective {kind} markets field: {type(markets).__name__}"
        )

    result: dict[str, bool] = {}
    for entry in markets:
        market = _unwrap_market(entry)
        if market is None:
            continue
        ticker = market.get("ticker")
        if not isinstance(ticker, str):
            continue
        key = ticker.strip().upper()
        if not key:
            continue
        status = market.get("status")
        if status in _LIVE_STATUSES:
            result[key] = True
        elif status in _INACTIVE_STATUSES:
            result[key] = False
        # Demolished / Unspecified / missing → omit (DELISTED).
    return result


def _unwrap_market(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    nested = entry.get("market")
    if isinstance(nested, dict):
        return nested
    return entry
