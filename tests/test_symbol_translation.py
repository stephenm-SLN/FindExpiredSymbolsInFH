from __future__ import annotations

import logging

import pytest

from fh_symbol_check.symbol_translation import (
    TRANSLATORS,
    _translate_arcus,
    _translate_injective,
    _translate_ondoperps,
    _translate_perp_by_quote,
    _translate_perp_strip_quote,
    _translate_perp_suffix,
    _translate_perp_suffix_inverse,
    _translate_polymarketperps,
    _translate_rhlighter,
    translate,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1000BONK/USDC-PERP", "1000BONK/USDC:USDC"),
        ("BTC/USDC-PERP", "BTC/USDC:USDC"),
        ("BTC/USDT-PERP", "BTC/USDT:USDT"),
        ("BTC/USDT", "BTC/USDT"),
        ("WEIRD-PERP", "WEIRD-PERP"),
        ("", ""),
    ],
)
def test_translate_perp_suffix(raw: str, expected: str) -> None:
    assert _translate_perp_suffix(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC/USD-PERP", "BTC/USD:BTC"),
        ("ETH/USD-PERP", "ETH/USD:ETH"),
        ("LINK/USD-PERP", "LINK/USD:LINK"),
        ("BTC/USD", "BTC/USD"),
        ("WEIRD-PERP", "WEIRD-PERP"),
        ("", ""),
    ],
)
def test_translate_perp_suffix_inverse(raw: str, expected: str) -> None:
    assert _translate_perp_suffix_inverse(raw) == expected


def test_translate_dispatches_linear_perp_for_linear_exchanges() -> None:
    assert translate("WOO", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("WOODEX", "1000BONK/USDC-PERP") == "1000BONK/USDC:USDC"
    assert translate("HUOBIDM", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("KUCOINDM", "ETH/USDT-PERP") == "ETH/USDT:USDT"
    assert translate("BINANCEDM", "SOL/USDT-PERP") == "SOL/USDT:USDT"
    assert translate("GATEIODM", "XRP/USDT-PERP") == "XRP/USDT:USDT"
    assert translate("PHEMEXDMT", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("CBITL", "BTC/USDC-PERP") == "BTC/USDC:USDC"
    # WHITEBITDM and CRYPTOCOMDM and KRAKENDM are linear-only; verify
    # the linear translator gives sensible output for each
    assert translate("WHITEBITDM", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("CRYPTOCOMDM", "BTC/USD-PERP") == "BTC/USD:USD"
    assert translate("KRAKENDM", "BTC/USD-PERP") == "BTC/USD:USD"


def test_translate_dispatches_inverse_perp_for_inverse_exchanges() -> None:
    assert translate("BINANCEDMCOIN", "BTC/USD-PERP") == "BTC/USD:BTC"
    assert translate("HUOBICOINSWAP", "BTC/USD-PERP") == "BTC/USD:BTC"
    assert translate("PHEMEXDMCOIN", "ETH/USD-PERP") == "ETH/USD:ETH"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC/USD-PERP", "BTC-PERP"),
        ("ETH/USD-PERP", "ETH-PERP"),
        ("BNB/USD-PERP", "BNB-PERP"),
        ("SOL/USD-PERP", "SOL-PERP"),
        ("1000PEPE/USDC-PERP", "1000PEPE-PERP"),
        ("BTC/USD", "BTC/USD"),
        ("WEIRD-PERP", "WEIRD-PERP"),
        ("", ""),
    ],
)
def test_translate_perp_strip_quote(raw: str, expected: str) -> None:
    assert _translate_perp_strip_quote(raw) == expected


def test_translate_dispatches_strip_quote_for_nado() -> None:
    assert translate("NADO", "BTC/USD-PERP") == "BTC-PERP"
    assert translate("NADO", "ETH/USD-PERP") == "ETH-PERP"
    assert translate("nado", "SOL/USD-PERP") == "SOL-PERP"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("GOLD/USDC-PERP", "GOLD-USD"),
        ("SILVER/USDC-PERP", "SILVER-USD"),
        ("BTC/USDC-PERP", "BTC-USD"),
        ("ETH/USDC-PERP", "ETH-USD"),
        ("SOL/USDC-PERP", "SOL-USD"),
        ("SP500/USDC-PERP", "SP500-USD"),
        ("NAS100/USDC-PERP", "NAS100-USD"),
        ("SPCX/USDC-PERP", "SPCX-USD"),
        ("WTI/USDC-PERP", "WTIOIL-USD"),  # FH says "WTI", venue says "WTIOIL"
        ("BTC/USDC", "BTC/USDC"),  # no -PERP suffix -> identity
        ("WEIRD-PERP", "WEIRD-PERP"),  # no slash -> identity
        ("", ""),
    ],
)
def test_translate_polymarketperps(raw: str, expected: str) -> None:
    assert _translate_polymarketperps(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC/USDC-PERP", "BTC/USDC PERP"),
        ("ETH/USDT-PERP", "ETH/USDT PERP"),
        ("INJ/USDC-PERP", "INJ/USDC PERP"),
        ("INJ/USDT", "INJ/USDT"),
        ("WETH/USDC", "WETH/USDC"),
        ("WEIRD-PERP", "WEIRD PERP"),
        ("", ""),
    ],
)
def test_translate_injective(raw: str, expected: str) -> None:
    assert _translate_injective(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC/USDG-PERP", "BTC"),
        ("BTC/USDT-PERP", "BTC"),
        ("ETH/USDG-PERP", "ETH"),
        ("BTC-PERP", "BTC"),
        ("META/USDG", "META/USDG"),
        ("ETH/USDG", "ETH/USDG"),
        ("", ""),
    ],
)
def test_translate_rhlighter(raw: str, expected: str) -> None:
    assert _translate_rhlighter(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTC/USD-PERP", "BTC-USD"),
        ("ETH/USD-PERP", "ETH-USD"),
        ("AAPL/USD-PERP", "AAPL-USD"),
        ("BTC/USD", "BTC-USD"),
        ("BTC-USD", "BTC-USD"),
        ("WEIRD-PERP", "WEIRD"),
        ("", ""),
    ],
)
def test_translate_arcus(raw: str, expected: str) -> None:
    assert _translate_arcus(raw) == expected


def test_translate_dispatches_for_arcus() -> None:
    assert translate("ARCUS", "BTC/USD-PERP") == "BTC-USD"
    assert translate("ARCUS", "AAPL/USD") == "AAPL-USD"
    assert translate("arcus", "ETH/USD-PERP") == "ETH-USD"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NVDA/USD-PERP", "NVDA-USD.P"),
        ("US100/USD-PERP", "US100-USD.P"),
        ("WTI/USD-PERP", "WTI-USD.P"),
        ("BTC/USD", "BTC-USD.P"),
        ("BTC-USD.P", "BTC-USD.P"),
        ("BTC-USD", "BTC-USD.P"),
        ("WEIRD-PERP", "WEIRD.P"),
        ("", ""),
    ],
)
def test_translate_ondoperps(raw: str, expected: str) -> None:
    assert _translate_ondoperps(raw) == expected


def test_translate_dispatches_for_ondoperps() -> None:
    assert translate("ONDOPERPS", "NVDA/USD-PERP") == "NVDA-USD.P"
    assert translate("ONDOPERPS", "US100/USD-PERP") == "US100-USD.P"
    assert translate("ondoperps", "BTC/USD-PERP") == "BTC-USD.P"


def test_translate_dispatches_for_rhlighter() -> None:
    assert translate("RHLIGHTER", "BTC/USDG-PERP") == "BTC"
    assert translate("RHLIGHTER", "BTC-PERP") == "BTC"
    assert translate("RHLIGHTER", "META/USDG") == "META/USDG"
    assert translate("rhlighter", "ETH/USDT-PERP") == "ETH"


def test_translate_dispatches_for_injective() -> None:
    assert translate("INJECTIVE", "BTC/USDC-PERP") == "BTC/USDC PERP"
    assert translate("INJECTIVE", "INJ/USDT") == "INJ/USDT"
    assert translate("injective", "ETH/USDT-PERP") == "ETH/USDT PERP"


def test_translate_dispatches_for_polymarketperps() -> None:
    assert translate("POLYMARKETPERPS", "GOLD/USDC-PERP") == "GOLD-USD"
    assert translate("POLYMARKETPERPS", "WTI/USDC-PERP") == "WTIOIL-USD"
    assert translate("polymarketperps", "BTC/USDC-PERP") == "BTC-USD"


@pytest.mark.parametrize(
    "raw, expected",
    [
        # linear (anything non-USD)
        ("BTC/USDT-PERP", "BTC/USDT:USDT"),
        ("ETH/USDT-PERP", "ETH/USDT:USDT"),
        ("BTC/USDC-PERP", "BTC/USDC:USDC"),
        ("ETH/USDe-PERP", "ETH/USDe:USDe"),
        # inverse (USD-quoted)
        ("BTC/USD-PERP", "BTC/USD:BTC"),
        ("ETH/USD-PERP", "ETH/USD:ETH"),
        ("LINK/USD-PERP", "LINK/USD:LINK"),
        # pass-through
        ("BTC/USDT", "BTC/USDT"),
        ("WEIRD-PERP", "WEIRD-PERP"),
        ("", ""),
    ],
)
def test_translate_perp_by_quote(raw: str, expected: str) -> None:
    assert _translate_perp_by_quote(raw) == expected


def test_translate_dispatches_for_bybitdm() -> None:
    """BYBITDM hosts both linear (USDT/USDC) and inverse (USD) perps under a
    single FH exchange_name, so its translator must branch on the quote."""
    assert translate("BYBITDM", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("BYBITDM", "BTC/USDC-PERP") == "BTC/USDC:USDC"
    assert translate("BYBITDM", "BTC/USD-PERP") == "BTC/USD:BTC"
    assert translate("bybitdm", "ETH/USDT-PERP") == "ETH/USDT:USDT"


def test_translate_dispatches_for_bitgetdm() -> None:
    """BITGETDM has the same triple-quote shape as BYBITDM (USDT, USDC, and
    inverse USD all under one ccxt id)."""
    assert translate("BITGETDM", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("BITGETDM", "BTC/USDC-PERP") == "BTC/USDC:USDC"
    assert translate("BITGETDM", "BTC/USD-PERP") == "BTC/USD:BTC"


def test_translate_dispatches_for_okex() -> None:
    """OKEX carries both spot AND perps under one FH exchange_name.
    Spot symbols must pass through unchanged; perps split by quote."""
    # Perps (both flavours live on ccxt-okx)
    assert translate("OKEX", "ETH/USDT-PERP") == "ETH/USDT:USDT"  # linear
    assert translate("OKEX", "BTC/USD-PERP") == "BTC/USD:BTC"     # inverse
    assert translate("OKEX", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    # Spot passthrough
    assert translate("OKEX", "BTC/USDT") == "BTC/USDT"
    assert translate("OKEX", "ETH/USDT") == "ETH/USDT"


def test_translate_is_case_insensitive_on_exchange_name() -> None:
    assert translate("woo", "BTC/USDT-PERP") == "BTC/USDT:USDT"
    assert translate("BinanceDMCoin", "BTC/USD-PERP") == "BTC/USD:BTC"


def test_translate_spot_exchanges_are_identity() -> None:
    """Spot exchanges (BINANCE, HUOBI, KUCOIN, GATEIO, PHEMEX, ...) have no
    translator registered and must pass symbols through unchanged."""
    assert translate("BINANCE", "BTC/USDT") == "BTC/USDT"
    assert translate("HUOBI", "BTC/USDT") == "BTC/USDT"
    assert translate("KUCOIN", "BTC/USDT") == "BTC/USDT"
    assert translate("GATEIO", "BTC/USDT") == "BTC/USDT"
    assert translate("PHEMEX", "BTC/USDT") == "BTC/USDT"


def test_translate_unregistered_exchange_is_identity() -> None:
    assert translate("MADEUPEX", "ABC/DEF") == "ABC/DEF"
    assert translate("MADEUPEX", "GHI/JKL-PERP") == "GHI/JKL-PERP"


def test_translate_unregistered_exchange_logs_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Absence of a translator is the norm (every spot exchange), so the
    'using identity' message lives at DEBUG to avoid spamming default logs."""
    with caplog.at_level(logging.DEBUG, logger="fh_symbol_check.symbol_translation"):
        translate("BINANCE", "BTC/USDT")
    debug_lines = [
        r
        for r in caplog.records
        if "BINANCE" in r.message and r.levelno == logging.DEBUG
    ]
    assert debug_lines, "expected a DEBUG line for unregistered exchange_name"


def test_translate_perp_translator_unchanged_when_no_perp_suffix() -> None:
    """WOO translator must not change non-perp symbols (e.g. if WOO ever ships
    a spot-style symbol through this code path)."""
    assert translate("WOO", "BTC/USDT") == "BTC/USDT"


def test_registry_contains_expected_exchange_names() -> None:
    expected_linear = {
        "WOO",
        "WOODEX",
        "HUOBIDM",
        "KUCOINDM",
        "BINANCEDM",
        "GATEIODM",
        "PHEMEXDMT",
        "CBITL",
        "WHITEBITDM",
        "CRYPTOCOMDM",
        "KRAKENDM",
    }
    expected_inverse = {
        "BINANCEDMCOIN",
        "HUOBICOINSWAP",
        "PHEMEXDMCOIN",
    }
    expected_strip_quote = {"NADO"}
    expected_polymarketperps = {"POLYMARKETPERPS"}
    expected_injective = {"INJECTIVE"}
    expected_rhlighter = {"RHLIGHTER"}
    expected_arcus = {"ARCUS"}
    expected_ondoperps = {"ONDOPERPS"}
    expected_by_quote = {"BYBITDM", "BITGETDM", "OKEX"}
    for name in expected_linear:
        assert TRANSLATORS[name] is _translate_perp_suffix, name
    for name in expected_inverse:
        assert TRANSLATORS[name] is _translate_perp_suffix_inverse, name
    for name in expected_strip_quote:
        assert TRANSLATORS[name] is _translate_perp_strip_quote, name
    for name in expected_polymarketperps:
        assert TRANSLATORS[name] is _translate_polymarketperps, name
    for name in expected_injective:
        assert TRANSLATORS[name] is _translate_injective, name
    for name in expected_rhlighter:
        assert TRANSLATORS[name] is _translate_rhlighter, name
    for name in expected_arcus:
        assert TRANSLATORS[name] is _translate_arcus, name
    for name in expected_ondoperps:
        assert TRANSLATORS[name] is _translate_ondoperps, name
    for name in expected_by_quote:
        assert TRANSLATORS[name] is _translate_perp_by_quote, name
