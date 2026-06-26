from __future__ import annotations

import json
from io import BytesIO

import pytest

from fh_symbol_check.custom_venues import nado as nado_module
from fh_symbol_check.custom_venues.nado import (
    NadoFetchError,
    _parse_symbols,
    fetch_symbols,
)


def _fixture() -> dict:
    """A shrunk-down sample of the real /v2/symbols response."""
    return {
        "BTC-PERP": {
            "type": "perp",
            "product_id": 2,
            "symbol": "BTC-PERP",
            "trading_status": "live",
            "isolated_only": False,
            "market_hours": None,
        },
        "ETH-PERP": {
            "type": "perp",
            "product_id": 4,
            "symbol": "ETH-PERP",
            "trading_status": "live",
        },
        "OLD-PERP": {
            "type": "perp",
            "product_id": 999,
            "symbol": "OLD-PERP",
            "trading_status": "not_tradable",
        },
        "ROFL-PERP": {
            "type": "perp",
            "product_id": 998,
            "symbol": "ROFL-PERP",
            "trading_status": "reduce_only",
        },
        "wTSLAx": {
            "type": "spot",
            "product_id": 50,
            "symbol": "wTSLAx",
            "trading_status": "live",
        },
    }


def test_parse_live_symbols_are_active() -> None:
    out = _parse_symbols(_fixture())
    assert out["BTC-PERP"] is True
    assert out["ETH-PERP"] is True
    assert out["WTSLAX"] is True  # mixed-case input upper-cased


def test_parse_non_live_symbols_are_inactive() -> None:
    out = _parse_symbols(_fixture())
    assert out["OLD-PERP"] is False  # not_tradable
    assert out["ROFL-PERP"] is False  # reduce_only


def test_parse_returns_uppercased_keys() -> None:
    out = _parse_symbols({"abc": {"trading_status": "live"}})
    assert "ABC" in out
    assert out["ABC"] is True


def test_parse_ignores_garbage_entries() -> None:
    out = _parse_symbols(
        {
            "BTC-PERP": {"trading_status": "live"},
            "junk": "not a dict",
            42: {"trading_status": "live"},  # non-str key
        }
    )
    assert set(out) == {"BTC-PERP"}


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(NadoFetchError, match="unexpected Nado response shape"):
        _parse_symbols([{"BTC-PERP": {"trading_status": "live"}}])


def test_parse_missing_trading_status_is_inactive() -> None:
    """Defensive: a Nado entry without trading_status should be treated as
    inactive (we can't prove it's live, so we err on the side of caution)."""
    out = _parse_symbols({"NEW-PERP": {"type": "perp", "product_id": 1000}})
    assert out["NEW-PERP"] is False


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
        assert req.full_url == nado_module.NADO_SYMBOLS_URL
        return _FakeResponse(body)

    monkeypatch.setattr(nado_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["BTC-PERP"] is True
    assert out["OLD-PERP"] is False


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(nado_module, "urlopen", fake_urlopen)
    with pytest.raises(NadoFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(nado_module, "urlopen", fake_urlopen)
    with pytest.raises(NadoFetchError, match="failed to fetch Nado symbols"):
        fetch_symbols()
