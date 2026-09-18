"""Unit tests for the Ondo Perps custom-venue checker."""

from __future__ import annotations

import json
from io import BytesIO

import pytest

from fh_symbol_check.custom_venues import CUSTOM_VENUES
from fh_symbol_check.custom_venues import ondoperps as ondoperps_module
from fh_symbol_check.custom_venues.ondoperps import (
    ONDOPERPS_MARKETS_URL,
    OndoPerpsFetchError,
    _parse_markets,
    fetch_symbols,
)


def _fixture() -> dict:
    return {
        "success": True,
        "result": {
            "perps": {
                "tradingPairs": [
                    {"market": "NVDA-USD.P", "displayName": "NVDAUSD"},
                    {"market": "  us100-usd.p  "},
                    {"market": "WTI-USD.P"},
                    {"displayName": "NOMARKET"},  # missing market
                    {"market": ""},
                    "not a dict",
                ]
            }
        },
    }


def test_parse_extracts_market_as_live() -> None:
    out = _parse_markets(_fixture())
    assert out["NVDA-USD.P"] is True
    assert out["WTI-USD.P"] is True


def test_parse_strips_and_uppercases() -> None:
    out = _parse_markets(_fixture())
    assert out["US100-USD.P"] is True


def test_parse_skips_garbage_entries() -> None:
    out = _parse_markets(_fixture())
    assert set(out) == {"NVDA-USD.P", "US100-USD.P", "WTI-USD.P"}


def test_parse_missing_result_is_empty() -> None:
    assert _parse_markets({"success": True}) == {}


def test_parse_missing_trading_pairs_is_empty() -> None:
    assert _parse_markets({"success": True, "result": {"perps": {}}}) == {}


def test_parse_success_false_raises() -> None:
    with pytest.raises(OndoPerpsFetchError, match="ONDOPERPS API error: boom"):
        _parse_markets({"success": False, "error": "boom"})


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(OndoPerpsFetchError, match="unexpected ONDOPERPS response shape"):
        _parse_markets([{"market": "NVDA-USD.P"}])


def test_parse_trading_pairs_must_be_list() -> None:
    with pytest.raises(
        OndoPerpsFetchError, match="unexpected ONDOPERPS tradingPairs field"
    ):
        _parse_markets(
            {"success": True, "result": {"perps": {"tradingPairs": {"market": "X"}}}}
        )


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
        assert req.full_url == ONDOPERPS_MARKETS_URL
        return _FakeResponse(json.dumps(_fixture()).encode("utf-8"))

    monkeypatch.setattr(ondoperps_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["NVDA-USD.P"] is True
    assert out["US100-USD.P"] is True


def test_fetch_symbols_sends_ua_and_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[dict] = []

    def fake_urlopen(req, timeout):
        headers.append(dict(req.header_items()))
        return _FakeResponse(b'{"success": true, "result": {"perps": {"tradingPairs": []}}}')

    monkeypatch.setattr(ondoperps_module, "urlopen", fake_urlopen)
    fetch_symbols()
    first = {k.lower(): v for k, v in headers[0].items()}
    assert first["user-agent"] == ondoperps_module.USER_AGENT
    assert first["accept"] == "application/json"


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(ondoperps_module, "urlopen", fake_urlopen)
    with pytest.raises(OndoPerpsFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_includes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(ondoperps_module, "urlopen", fake_urlopen)
    with pytest.raises(
        OndoPerpsFetchError,
        match="failed to fetch ONDOPERPS markets from https://api.ondoperps.xyz",
    ):
        fetch_symbols()


def test_registered_in_custom_venues() -> None:
    assert CUSTOM_VENUES["ONDOPERPS"] is ondoperps_module.fetch_symbols
