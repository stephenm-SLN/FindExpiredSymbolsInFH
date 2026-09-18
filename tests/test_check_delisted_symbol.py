"""Contract tests for the top-level ``check_delisted_symbol`` helper.

The module is imported by ``fh_symbol_check.validator`` for its
``load_exchange_markets_safe`` and ``classify`` primitives, so any
behaviour change here ripples through the whole pipeline.

Currently covers only what's non-obvious about the module:

- ``load_exchange_markets_safe`` sets a browser-style User-Agent on the
  ccxt exchange before calling ``load_markets``, so corporate
  SSL-inspection proxies don't reject requests as "non-browser". See the
  module docstring in ``check_delisted_symbol._BROWSER_UA`` for the
  motivation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import ccxt
import pytest

import check_delisted_symbol as cds


class _FakeExchange:
    """Stand-in for a ccxt exchange instance.

    Mirrors just enough of the real API for
    ``load_exchange_markets_safe`` to run without any network I/O:
    ``headers`` is a mutable dict, ``load_markets`` is a
    ``MagicMock`` returning an empty dict by default.
    """

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers if headers is not None else {}
        self.load_markets = MagicMock(return_value={})


@pytest.fixture
def fake_ccxt(monkeypatch: pytest.MonkeyPatch) -> type[_FakeExchange]:
    """Register ``ccxt.fake`` so ``getattr(ccxt, "fake")()`` works, and
    add ``"fake"`` to ``ccxt.exchanges`` so the guard in
    ``load_exchange_markets_safe`` passes."""
    monkeypatch.setattr(ccxt, "fake", _FakeExchange, raising=False)
    monkeypatch.setattr(ccxt, "exchanges", list(ccxt.exchanges) + ["fake"])
    return _FakeExchange


def test_load_exchange_markets_safe_sets_browser_user_agent(
    fake_ccxt: type[_FakeExchange],
) -> None:
    exchange, markets = cds.load_exchange_markets_safe("fake")
    assert isinstance(exchange, _FakeExchange)
    ua = exchange.headers["User-Agent"]
    # Must look like a real browser \u2014 the whole point is to sidestep
    # corp proxy policies that reject `python-requests/*`.
    assert ua.startswith("Mozilla/5.0")
    assert "Chrome/" in ua
    # And the request must actually have been sent (i.e. the UA is set
    # *before* load_markets(), not after).
    exchange.load_markets.assert_called_once()
    assert markets == {}


def test_load_exchange_markets_safe_handles_none_headers(
    fake_ccxt: type[_FakeExchange], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some ccxt exchange classes initialise ``headers`` to ``None``.
    The helper must cope."""

    class _NullHeadersExchange(_FakeExchange):
        def __init__(self) -> None:  # noqa: D401 - not a docstring context
            super().__init__(headers=None)

    monkeypatch.setattr(ccxt, "fake", _NullHeadersExchange, raising=False)

    exchange, _ = cds.load_exchange_markets_safe("fake")
    assert exchange.headers is not None
    assert exchange.headers["User-Agent"].startswith("Mozilla/5.0")


def test_load_exchange_markets_safe_rejects_unknown_exchange() -> None:
    with pytest.raises(cds.MarketLoadError) as exc_info:
        cds.load_exchange_markets_safe("definitely-not-an-exchange")
    assert "not a supported exchange" in str(exc_info.value)


def test_load_exchange_markets_safe_wraps_ccxt_errors(
    fake_ccxt: type[_FakeExchange], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any ccxt.NetworkError or ExchangeError becomes MarketLoadError,
    preserving the original message for the caller."""

    class _RaisingExchange(_FakeExchange):
        def __init__(self) -> None:
            super().__init__()
            self.load_markets = MagicMock(  # type: ignore[method-assign]
                side_effect=ccxt.NetworkError("upbit GET https://api.upbit.com/…")
            )

    monkeypatch.setattr(ccxt, "fake", _RaisingExchange, raising=False)

    with pytest.raises(cds.MarketLoadError) as exc_info:
        cds.load_exchange_markets_safe("fake")
    assert "upbit GET" in str(exc_info.value)
