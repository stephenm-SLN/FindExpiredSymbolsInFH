"""Unit tests for the Arcus custom-venue checker."""

from __future__ import annotations

import json
from io import BytesIO

import pytest

from fh_symbol_check.custom_venues import CUSTOM_VENUES
from fh_symbol_check.custom_venues import arcus as arcus_module
from fh_symbol_check.custom_venues.arcus import (
    ARCUS_MARKETS_URL,
    ArcusFetchError,
    _parse_markets,
    fetch_symbols,
)


def _fixture() -> dict:
    return {
        "markets": [
            {"marketDisplayName": "BTC-USD", "status": "ONLINE", "type": "PERPETUAL"},
            {"marketDisplayName": "F-USD", "status": "OFFLINE", "type": "PERPETUAL"},
            {"marketDisplayName": "  aapl-usd  ", "status": "ONLINE"},
            {"marketDisplayName": "GHOST-USD"},  # missing status
            {"status": "ONLINE"},  # missing name
            "not a dict",
        ]
    }


def test_parse_online_is_live() -> None:
    out = _parse_markets(_fixture())
    assert out["BTC-USD"] is True


def test_parse_offline_is_inactive() -> None:
    out = _parse_markets(_fixture())
    assert out["F-USD"] is False


def test_parse_strips_and_uppercases() -> None:
    out = _parse_markets(_fixture())
    assert out["AAPL-USD"] is True


def test_parse_missing_status_is_absent() -> None:
    out = _parse_markets(_fixture())
    assert "GHOST-USD" not in out


def test_parse_missing_markets_is_empty() -> None:
    assert _parse_markets({}) == {}


def test_parse_ignores_garbage_entries() -> None:
    out = _parse_markets(_fixture())
    assert "BTC-USD" in out


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(ArcusFetchError, match="unexpected ARCUS response shape"):
        _parse_markets([{"marketDisplayName": "BTC-USD"}])


def test_parse_markets_field_must_be_list() -> None:
    with pytest.raises(ArcusFetchError, match="unexpected ARCUS markets field"):
        _parse_markets({"markets": {"marketDisplayName": "BTC-USD"}})


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
        assert req.full_url == ARCUS_MARKETS_URL
        return _FakeResponse(json.dumps(_fixture()).encode("utf-8"))

    monkeypatch.setattr(arcus_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["BTC-USD"] is True
    assert out["F-USD"] is False


def test_fetch_symbols_sends_ua_and_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[dict] = []

    def fake_urlopen(req, timeout):
        headers.append(dict(req.header_items()))
        return _FakeResponse(b'{"markets":[]}')

    monkeypatch.setattr(arcus_module, "urlopen", fake_urlopen)
    fetch_symbols()
    first = {k.lower(): v for k, v in headers[0].items()}
    assert first["user-agent"] == arcus_module.USER_AGENT
    assert first["accept"] == "application/json"


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(arcus_module, "urlopen", fake_urlopen)
    with pytest.raises(ArcusFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_includes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(arcus_module, "urlopen", fake_urlopen)
    with pytest.raises(
        ArcusFetchError,
        match="failed to fetch ARCUS markets from https://api.arcus.xyz",
    ):
        fetch_symbols()


def test_registered_in_custom_venues() -> None:
    assert CUSTOM_VENUES["ARCUS"] is arcus_module.fetch_symbols
