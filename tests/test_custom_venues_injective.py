"""Unit tests for the Injective custom-venue checker."""

from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from fh_symbol_check.custom_venues import CUSTOM_VENUES
from fh_symbol_check.custom_venues import injective as injective_module
from fh_symbol_check.custom_venues.injective import (
    LCD_BASE,
    InjectiveFetchError,
    _parse_markets,
    fetch_symbols,
)


def _spot_fixture() -> dict:
    return {
        "markets": [
            {"ticker": "INJ/USDT", "status": "Active"},
            {"ticker": "WETH/USDC", "status": "Paused"},
            {"ticker": "ARB/USDT", "status": "Demolished"},
            {"ticker": "APP/INJ ", "status": "Active"},  # trailing space
            {"ticker": "GHOST/USDT"},  # missing status
            "not a dict",
        ]
    }


def _deriv_fixture() -> dict:
    return {
        "markets": [
            {"market": {"ticker": "BTC/USDC PERP", "status": "Active"}},
            {"market": {"ticker": "XAU/USDT PERP", "status": "Paused"}},
            {"market": {"ticker": "WTIV5/USDT-22SEP25", "status": "Expired"}},
            {"market": {"ticker": "TRIA/USDC PERP", "status": "Demolished"}},
            {"ticker": "ETH/USDC PERP", "status": "Active"},  # unwrapped also ok
            {"market": "not a dict"},
        ]
    }


def test_parse_spot_active_is_live() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert out["INJ/USDT"] is True


def test_parse_spot_paused_is_inactive() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert out["WETH/USDC"] is False


def test_parse_spot_demolished_is_absent() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert "ARB/USDT" not in out


def test_parse_spot_strips_and_uppercases_ticker() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert "APP/INJ" in out
    assert out["APP/INJ"] is True


def test_parse_spot_missing_status_is_absent() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert "GHOST/USDT" not in out


def test_parse_deriv_active_paused_expired() -> None:
    out = _parse_markets("derivative", _deriv_fixture())
    assert out["BTC/USDC PERP"] is True
    assert out["XAU/USDT PERP"] is False
    assert out["WTIV5/USDT-22SEP25"] is False
    assert out["ETH/USDC PERP"] is True


def test_parse_deriv_demolished_is_absent() -> None:
    out = _parse_markets("derivative", _deriv_fixture())
    assert "TRIA/USDC PERP" not in out


def test_parse_ignores_garbage_entries() -> None:
    out = _parse_markets("spot", _spot_fixture())
    assert "INJ/USDT" in out


def test_parse_empty_markets_is_empty() -> None:
    assert _parse_markets("spot", {"markets": []}) == {}
    assert _parse_markets("spot", {}) == {}


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(InjectiveFetchError, match="unexpected Injective spot response shape"):
        _parse_markets("spot", [{"ticker": "INJ/USDT"}])


def test_parse_markets_field_must_be_list() -> None:
    with pytest.raises(InjectiveFetchError, match="unexpected Injective derivative markets field"):
        _parse_markets("derivative", {"markets": {"ticker": "BTC/USDC PERP"}})


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._buf = BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def test_fetch_symbols_hits_spot_and_deriv_for_each_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_urlopen(req, timeout):
        seen.append(req.full_url)
        parsed = urlparse(req.full_url)
        status = parse_qs(parsed.query).get("status", [""])[0]
        if parsed.path.endswith("/spot/markets"):
            body = {"markets": [{"ticker": f"SPOT/{status}", "status": status}]}
        else:
            body = {
                "markets": [
                    {"market": {"ticker": f"PERP/{status} PERP", "status": status}}
                ]
            }
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(injective_module, "urlopen", fake_urlopen)
    out = fetch_symbols()

    assert len(seen) == 6
    assert all(u.startswith(LCD_BASE) for u in seen)
    assert sum("/spot/markets" in u for u in seen) == 3
    assert sum("/derivative/markets" in u for u in seen) == 3
    for status in ("Active", "Paused", "Expired"):
        assert any(f"status={status}" in u for u in seen)
    assert not any("Demolished" in u for u in seen)

    assert out["SPOT/ACTIVE"] is True
    assert out["SPOT/PAUSED"] is False
    assert out["SPOT/EXPIRED"] is False
    assert out["PERP/ACTIVE PERP"] is True
    assert out["PERP/PAUSED PERP"] is False


def test_fetch_symbols_active_wins_over_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the same ticker appears as both Active and Paused (shouldn't, but
    LCD is not a lock), keep LISTED."""

    def fake_urlopen(req, timeout):
        parsed = urlparse(req.full_url)
        status = parse_qs(parsed.query).get("status", [""])[0]
        body = {"markets": [{"ticker": "INJ/USDT", "status": status}]}
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(injective_module, "urlopen", fake_urlopen)
    out = fetch_symbols()
    assert out["INJ/USDT"] is True


def test_fetch_symbols_sends_ua_and_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers: list[dict] = []

    def fake_urlopen(req, timeout):
        headers.append(dict(req.header_items()))
        return _FakeResponse(b'{"markets":[]}')

    monkeypatch.setattr(injective_module, "urlopen", fake_urlopen)
    fetch_symbols()
    assert headers
    first = {k.lower(): v for k, v in headers[0].items()}
    assert first["user-agent"] == injective_module.USER_AGENT
    assert first["accept"] == "application/json"


def test_fetch_symbols_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        return _FakeResponse(b"<<<not json>>>")

    monkeypatch.setattr(injective_module, "urlopen", fake_urlopen)
    with pytest.raises(InjectiveFetchError, match="not valid JSON"):
        fetch_symbols()


def test_fetch_symbols_network_error_includes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib.error import URLError

    def fake_urlopen(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(injective_module, "urlopen", fake_urlopen)
    with pytest.raises(
        InjectiveFetchError,
        match=r"failed to fetch Injective Active markets from https://",
    ):
        fetch_symbols()


def test_registered_in_custom_venues() -> None:
    assert CUSTOM_VENUES["INJECTIVE"] is injective_module.fetch_symbols
