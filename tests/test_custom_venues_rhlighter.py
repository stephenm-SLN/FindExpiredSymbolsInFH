"""Unit tests for the Robinhood Lighter custom-venue checker."""

from __future__ import annotations

import json
from io import BytesIO

import pytest

from fh_symbol_check.custom_venues import CUSTOM_VENUES
from fh_symbol_check.custom_venues import rhlighter as rhlighter_module
from fh_symbol_check.custom_venues.rhlighter import (
    RHLIGHTER_DETAILS_URL,
    RHLighterFetchError,
    _parse_order_books,
    fetch_symbols,
)


def _fixture() -> dict:
    return {
        "code": 200,
        "order_book_details": [
            {"symbol": "BTC", "market_type": "perp", "status": "active"},
            {"symbol": "ETH", "market_type": "perp", "status": "inactive"},
            {"symbol": "ghost", "market_type": "perp"},  # missing status
            "not a dict",
        ],
        "spot_order_book_details": [
            {"symbol": "META/USDG", "market_type": "spot", "status": "active"},
            {"symbol": "ETH/USDG", "market_type": "spot", "status": "inactive"},
            {"symbol": "  aapl/usdg  ", "market_type": "spot", "status": "active"},
        ],
    }


def test_parse_perp_active_is_live() -> None:
    out = _parse_order_books(_fixture())
    assert out["BTC"] is True


def test_parse_perp_inactive_is_not_live() -> None:
    out = _parse_order_books(_fixture())
    assert out["ETH"] is False


def test_parse_spot_active_and_inactive() -> None:
    out = _parse_order_books(_fixture())
    assert out["META/USDG"] is True
    assert out["ETH/USDG"] is False


def test_parse_strips_and_uppercases() -> None:
    out = _parse_order_books(_fixture())
    assert out["AAPL/USDG"] is True


def test_parse_missing_status_is_absent() -> None:
    out = _parse_order_books(_fixture())
    assert "GHOST" not in out


def test_parse_missing_lists_is_empty() -> None:
    assert _parse_order_books({}) == {}


def test_parse_ignores_garbage_entries() -> None:
    out = _parse_order_books(_fixture())
    assert "BTC" in out


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(RHLighterFetchError, match="unexpected RHLIGHTER response shape"):
        _parse_order_books([{"symbol": "BTC"}])


def test_parse_books_field_must_be_list() -> None:
    with pytest.raises(RHLighterFetchError, match="unexpected RHLIGHTER order_book_details field"):
        _parse_order_books({"order_book_details": {"symbol": "BTC"}})


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
    def fake_urlopen(req, timeout):
        assert req.full_url == RHLIGHTER_DETAILS_URL
        return _FakeResponse(json.dumps(_fixture()).encode("utf-8"))

    monkeypatch.setattr(rhlighter_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["BTC"] is True
    assert out["ETH"] is False
    assert out["META/USDG"] is True


def test_fetch_symbols_sends_ua_and_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[dict] = []

    def fake_urlopen(req, timeout):
        headers.append(dict(req.header_items()))
        return _FakeResponse(b'{"order_book_details":[],"spot_order_book_details":[]}')

    monkeypatch.setattr(rhlighter_module, "urlopen", fake_urlopen)
    fetch_symbols()
    first = {k.lower(): v for k, v in headers[0].items()}
    assert first["user-agent"] == rhlighter_module.USER_AGENT
    assert first["accept"] == "application/json"


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(rhlighter_module, "urlopen", fake_urlopen)
    with pytest.raises(RHLighterFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_includes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(rhlighter_module, "urlopen", fake_urlopen)
    with pytest.raises(
        RHLighterFetchError,
        match="failed to fetch RHLIGHTER markets from https://api.rh.lighter.xyz",
    ):
        fetch_symbols()


def test_registered_in_custom_venues() -> None:
    assert CUSTOM_VENUES["RHLIGHTER"] is rhlighter_module.fetch_symbols
