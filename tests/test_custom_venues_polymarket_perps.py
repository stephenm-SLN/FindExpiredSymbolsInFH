from __future__ import annotations

import json
from io import BytesIO

import pytest

from fh_symbol_check.custom_venues import polymarket_perps as pp_module
from fh_symbol_check.custom_venues.polymarket_perps import (
    PolymarketPerpsFetchError,
    _parse_instruments,
    fetch_symbols,
)


def _fixture() -> list[dict]:
    """A trimmed sample of the real /v1/info/instruments response."""
    return [
        {
            "instrument_id": 2,
            "instrument_type": "perpetual",
            "category": "commodity",
            "symbol": "GOLD-USD",
            "base_asset": "GOLD",
            "quote_asset": "pUSD",
        },
        {
            "instrument_id": 3,
            "instrument_type": "perpetual",
            "category": "commodity",
            "symbol": "WTIOIL-USD",
            "base_asset": "WTIOIL",
            "quote_asset": "pUSD",
        },
        {
            "instrument_id": 5,
            "instrument_type": "perpetual",
            "category": "commodity",
            "symbol": "SILVER-USD",
            "base_asset": "SILVER",
            "quote_asset": "pUSD",
        },
        {
            "instrument_id": 6,
            "instrument_type": "perpetual",
            "category": "crypto",
            "symbol": "BTC-USD",
            "base_asset": "BTC",
            "quote_asset": "pUSD",
        },
    ]


def test_parse_extracts_symbol_field() -> None:
    out = _parse_instruments(_fixture())
    assert out["GOLD-USD"] is True
    assert out["SILVER-USD"] is True
    assert out["WTIOIL-USD"] is True
    assert out["BTC-USD"] is True


def test_parse_uppercases_keys() -> None:
    out = _parse_instruments([{"symbol": "btc-usd"}])
    assert out == {"BTC-USD": True}


def test_parse_top_level_must_be_list() -> None:
    with pytest.raises(
        PolymarketPerpsFetchError, match="unexpected Polymarket Perps response shape"
    ):
        _parse_instruments({"instruments": _fixture()})


def test_parse_skips_garbage_entries() -> None:
    out = _parse_instruments(
        [
            {"symbol": "BTC-USD"},
            "not a dict",
            {"no_symbol_field": "x"},
            {"symbol": 42},  # non-str symbol
        ]
    )
    assert out == {"BTC-USD": True}


def test_parse_empty_list_is_empty_dict() -> None:
    assert _parse_instruments([]) == {}


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._buf = BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def test_fetch_symbols_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(_fixture()).encode("utf-8")

    def fake_urlopen(req, timeout):
        assert req.full_url == pp_module.POLYMARKETPERPS_INSTRUMENTS_URL
        return _FakeResponse(body)

    monkeypatch.setattr(pp_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["GOLD-USD"] is True
    assert out["WTIOIL-USD"] is True


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(pp_module, "urlopen", fake_urlopen)
    with pytest.raises(PolymarketPerpsFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(pp_module, "urlopen", fake_urlopen)
    with pytest.raises(
        PolymarketPerpsFetchError, match="failed to fetch Polymarket Perps instruments"
    ):
        fetch_symbols()
