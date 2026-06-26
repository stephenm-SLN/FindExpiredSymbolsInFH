"""Per-FH-exchange-name translators from internal FH symbol format to ccxt format.

Translators are keyed by FH ``exchange_name`` (uppercase) — not by ccxt id —
because multiple FH names can resolve to the same ccxt id while needing
different conventions (e.g. ``HUOBI``/``HUOBIDM``/``HUOBICOINSWAP`` all resolve
to ``htx`` but use spot, linear-perp and inverse-perp formats respectively).

Exchange names without an entry fall through to identity; the validator calls
translate() once per symbol.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

Translator = Callable[[str], str]


def _translate_perp_suffix(s: str) -> str:
    """FH internal "<BASE>/<QUOTE>-PERP" -> ccxt linear perp "<BASE>/<QUOTE>:<QUOTE>".

    Examples:
        "1000BONK/USDC-PERP" -> "1000BONK/USDC:USDC"   (WOO / WOODEX)
        "BTC/USDT-PERP"      -> "BTC/USDT:USDT"        (HUOBIDM)

    Non-perp symbols (no -PERP suffix) are returned unchanged.
    """
    suffix = "-PERP"
    if not s.endswith(suffix):
        return s
    base_quote = s[: -len(suffix)]
    if "/" not in base_quote:
        return s
    quote = base_quote.split("/", 1)[1]
    return f"{base_quote}:{quote}"


def _translate_perp_suffix_inverse(s: str) -> str:
    """FH internal "<BASE>/<QUOTE>-PERP" -> ccxt inverse perp "<BASE>/<QUOTE>:<BASE>".

    Inverse perps (coin-margined, e.g. Binance COIN-M, HTX coin-swap) settle in
    the base currency, so ccxt uses "<BASE>/<QUOTE>:<BASE>" rather than ":<QUOTE>".

    Examples:
        "BTC/USD-PERP" -> "BTC/USD:BTC"   (BINANCEDMCOIN / HUOBICOINSWAP)
        "ETH/USD-PERP" -> "ETH/USD:ETH"

    Non-perp symbols (no -PERP suffix) are returned unchanged.
    """
    suffix = "-PERP"
    if not s.endswith(suffix):
        return s
    base_quote = s[: -len(suffix)]
    if "/" not in base_quote:
        return s
    base = base_quote.split("/", 1)[0]
    return f"{base_quote}:{base}"


def _translate_perp_by_quote(s: str) -> str:
    """FH internal "<BASE>/<QUOTE>-PERP" -> linear OR inverse ccxt key based on quote.

    Some venues (e.g. Bybit) host both linear and inverse perps under a single FH
    exchange_name (BYBITDM). The convention used downstream:

        quote == "USD"   -> inverse  "<BASE>/<QUOTE>:<BASE>"
        quote != "USD"   -> linear   "<BASE>/<QUOTE>:<QUOTE>"   (USDT, USDC, USDe, …)

    Examples:
        "BTC/USDT-PERP" -> "BTC/USDT:USDT"   (Bybit linear)
        "BTC/USDC-PERP" -> "BTC/USDC:USDC"
        "BTC/USD-PERP"  -> "BTC/USD:BTC"     (Bybit inverse)

    Non-perp symbols (no -PERP suffix) are returned unchanged.
    """
    suffix = "-PERP"
    if not s.endswith(suffix):
        return s
    base_quote = s[: -len(suffix)]
    if "/" not in base_quote:
        return s
    base, quote = base_quote.split("/", 1)
    if quote == "USD":
        return f"{base_quote}:{base}"
    return f"{base_quote}:{quote}"


def _translate_perp_strip_quote(s: str) -> str:
    """FH internal "<BASE>/<QUOTE>-PERP" -> "<BASE>-PERP".

    Some venues (e.g. Nado) name their perps without quoting the collateral:
    Nado returns "BTC-PERP" / "ETH-PERP" from /v2/symbols, while the FH stores
    "BTC/USD-PERP" / "ETH/USD-PERP". This strips the "/<QUOTE>" portion.

    Examples:
        "BTC/USD-PERP" -> "BTC-PERP"      (NADO)
        "ETH/USD-PERP" -> "ETH-PERP"

    Non-perp symbols (no -PERP suffix) are returned unchanged.
    """
    suffix = "-PERP"
    if not s.endswith(suffix):
        return s
    base_quote = s[: -len(suffix)]
    if "/" not in base_quote:
        return s
    base = base_quote.split("/", 1)[0]
    return f"{base}{suffix}"


# Per-FH-base name remap for Polymarket Perps. The venue uses a slightly
# different name for one commodity (FH says "WTI" while the API says "WTIOIL").
# Add more entries if/when other FH↔Polymarket name discrepancies surface.
_POLYMARKETPERPS_BASE_REMAP: dict[str, str] = {
    "WTI": "WTIOIL",
}


def _translate_polymarketperps(s: str) -> str:
    """FH internal "<BASE>/USDC-PERP" -> Polymarket Perps "<BASE>-USD".

    Polymarket Perps names its instruments as ``<BASE>-USD`` (e.g. ``BTC-USD``,
    ``GOLD-USD``, ``WTIOIL-USD``) — collateral is pUSD on Polygon but the
    symbol drops the chain prefix. Quote-stripping + ``-PERP``→``-USD`` and a
    small per-base remap is enough to bridge the two namespaces.

    Examples:
        "GOLD/USDC-PERP"   -> "GOLD-USD"
        "SILVER/USDC-PERP" -> "SILVER-USD"
        "WTI/USDC-PERP"    -> "WTIOIL-USD"   (via WTI→WTIOIL remap)
        "BTC/USDC-PERP"    -> "BTC-USD"

    Non-perp symbols (no -PERP suffix) are returned unchanged.
    """
    suffix = "-PERP"
    if not s.endswith(suffix):
        return s
    base_quote = s[: -len(suffix)]
    if "/" not in base_quote:
        return s
    base = base_quote.split("/", 1)[0]
    base = _POLYMARKETPERPS_BASE_REMAP.get(base, base)
    return f"{base}-USD"


TRANSLATORS: dict[str, Translator] = {
    "BINANCEDM": _translate_perp_suffix,
    "BINANCEDMCOIN": _translate_perp_suffix_inverse,
    "BITGETDM": _translate_perp_by_quote,
    "BYBITDM": _translate_perp_by_quote,
    "CBITL": _translate_perp_suffix,
    # CRYPTOCOMDM hosts USD-quoted linear perps that settle in USD (e.g.
    # BTC/USD:USD on cryptocom). The linear suffix translator picks this up
    # via "<BASE>/USD-PERP" -> "<BASE>/USD:USD". 0 inverse contracts at
    # check time, so by-quote (which would route USD to inverse) is wrong.
    "CRYPTOCOMDM": _translate_perp_suffix,
    "GATEIODM": _translate_perp_suffix,
    "HUOBICOINSWAP": _translate_perp_suffix_inverse,
    "HUOBIDM": _translate_perp_suffix,
    # KRAKENDM (krakenfutures) has 318 linear vs 14 inverse perps, both
    # USD-quoted. Defaulting to linear here since that's the bulk of the
    # universe; the 14 inverse markets will surface as DELISTED until/if
    # we learn FH stores them under a distinguishable cover_names format.
    "KRAKENDM": _translate_perp_suffix,
    "KUCOINDM": _translate_perp_suffix,
    "NADO": _translate_perp_strip_quote,
    "PHEMEXDMCOIN": _translate_perp_suffix_inverse,
    "PHEMEXDMT": _translate_perp_suffix,
    "POLYMARKETPERPS": _translate_polymarketperps,
    "WHITEBITDM": _translate_perp_suffix,
    "WOO": _translate_perp_suffix,
    "WOODEX": _translate_perp_suffix,
}


def translate(exchange_name: str, internal_symbol: str) -> str:
    """Translate one symbol. Missing translators fall through to identity.

    Lookup is case-insensitive on ``exchange_name``.
    """
    key = exchange_name.upper()
    fn = TRANSLATORS.get(key)
    if fn is None:
        logger.debug(
            "no symbol translator registered for exchange_name=%r; using identity",
            exchange_name,
        )
        return internal_symbol
    return fn(internal_symbol)
