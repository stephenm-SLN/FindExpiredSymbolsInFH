from __future__ import annotations

import pytest

import fh_symbol_check.validator as validator_module
from fh_symbol_check.models import FeedHandlerRow
from fh_symbol_check.validator import build_tasks, classify_symbols


def _row(exchange_name: str, symbols: tuple[str, ...], svc: int = 4002) -> FeedHandlerRow:
    return FeedHandlerRow(
        service_id=svc,
        fh_name=f"fh_{exchange_name.lower()}_{svc}",
        hostname="TA-TKY-A-41_LOCAL",
        exchange_name=exchange_name,
        symbols=symbols,
    )


def test_build_tasks_maps_exchange_and_translates_symbols() -> None:
    rows = [
        _row("HUOBI", ("BTC/USDT", "ETH/USDT")),
        _row("WOODEX", ("BTC/USDC-PERP",), svc=4003),
    ]
    tasks, errors = build_tasks(rows, {"HUOBI": "htx", "WOODEX": "woo"})

    assert errors == []
    assert {(t.ccxt_id, t.original_symbol, t.ccxt_symbol) for t in tasks} == {
        ("htx", "BTC/USDT", "BTC/USDT"),
        ("htx", "ETH/USDT", "ETH/USDT"),
        ("woo", "BTC/USDC-PERP", "BTC/USDC:USDC"),
    }


def test_build_tasks_unknown_exchange_produces_error_per_symbol() -> None:
    rows = [_row("BINANCE", ("BTC/USDT", "ETH/USDT"))]
    tasks, errors = build_tasks(rows, {"HUOBI": "htx"})

    assert tasks == []
    assert len(errors) == 2
    for e in errors:
        assert e.status == "ERROR"
        assert "unknown exchange_name" in e.detail


def test_classify_symbols_groups_by_exchange_and_calls_load_markets_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    fake_markets = {
        "htx": {"BTC/USDT": {"active": True}, "ETH/USDT": {"active": False}},
        "woo": {"BTC/USDC:USDC": {"active": True}},
    }

    def fake_loader(ccxt_id: str):
        calls.append(ccxt_id)
        return object(), fake_markets[ccxt_id]

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_loader)

    rows = [
        _row("HUOBI", ("BTC/USDT", "ETH/USDT", "DELISTED/USDT")),
        _row("HUOBI", ("XRP/USDT",), svc=4004),
        _row("WOODEX", ("BTC/USDC-PERP",), svc=4003),
    ]
    tasks, errors = build_tasks(rows, {"HUOBI": "htx", "WOODEX": "woo"})
    assert errors == []

    results = classify_symbols(tasks, concurrency=1)

    assert sorted(calls) == ["htx", "woo"]
    assert calls.count("htx") == 1

    statuses = {(r.original_symbol, r.status) for r in results}
    assert statuses == {
        ("BTC/USDT", "LISTED"),
        ("ETH/USDT", "INACTIVE"),
        ("DELISTED/USDT", "DELISTED"),
        ("XRP/USDT", "DELISTED"),
        ("BTC/USDC-PERP", "LISTED"),
    }


def test_classify_symbols_market_load_error_yields_error_rows_for_that_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from check_delisted_symbol import MarketLoadError

    def fake_loader(ccxt_id: str):
        if ccxt_id == "htx":
            raise MarketLoadError("network down")
        return object(), {"BTC/USDC:USDC": {"active": True}}

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_loader)

    rows = [
        _row("HUOBI", ("BTC/USDT", "ETH/USDT")),
        _row("WOODEX", ("BTC/USDC-PERP",), svc=4003),
    ]
    tasks, _ = build_tasks(rows, {"HUOBI": "htx", "WOODEX": "woo"})

    results = classify_symbols(tasks, concurrency=2)

    by_ccxt = {(r.ccxt_id, r.original_symbol): r for r in results}
    assert by_ccxt[("htx", "BTC/USDT")].status == "ERROR"
    assert "load_markets failed" in by_ccxt[("htx", "BTC/USDT")].detail
    assert by_ccxt[("htx", "ETH/USDT")].status == "ERROR"
    assert by_ccxt[("woo", "BTC/USDC-PERP")].status == "LISTED"


def test_classify_symbols_empty_input_returns_empty() -> None:
    assert classify_symbols([]) == []


# ---------------------------------------------------------------------------
# Custom venue dispatch
# ---------------------------------------------------------------------------


def test_build_tasks_routes_custom_venue_with_sentinel_ccxt_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NADO is a registered custom venue (ccxt has no `nado`), so build_tasks
    must tag those rows with a `custom:NADO` sentinel ccxt_id and NOT consult
    the exchange_map for them."""
    rows = [_row("NADO", ("BTC-PERP", "ETH-PERP"))]
    tasks, errors = build_tasks(rows, exchange_map={})

    assert errors == []
    assert {t.ccxt_id for t in tasks} == {"custom:NADO"}
    assert {t.original_symbol for t in tasks} == {"BTC-PERP", "ETH-PERP"}


def test_classify_custom_venue_listed_inactive_delisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fh_symbol_check import validator as validator_module

    fake_universe = {
        "BTC-PERP": True,   # live
        "OLD-PERP": False,  # not_tradable / reduce_only / ...
    }

    monkeypatch.setattr(
        validator_module,
        "get_checker",
        lambda cid: (lambda: fake_universe),
    )

    rows = [_row("NADO", ("BTC-PERP", "OLD-PERP", "DELETED/USDT"))]
    tasks, _ = build_tasks(rows, exchange_map={})
    results = classify_symbols(tasks, concurrency=1)

    by_sym = {r.original_symbol: r for r in results}
    assert by_sym["BTC-PERP"].status == "LISTED"
    assert by_sym["OLD-PERP"].status == "INACTIVE"
    assert by_sym["DELETED/USDT"].status == "DELISTED"
    # ccxt_id propagates the sentinel so consumers can tell where it came from
    assert by_sym["BTC-PERP"].ccxt_id == "custom:NADO"


def test_classify_custom_venue_fetch_failure_emits_error_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fh_symbol_check import validator as validator_module

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(validator_module, "get_checker", lambda cid: boom)

    rows = [_row("NADO", ("BTC-PERP", "ETH-PERP"))]
    tasks, _ = build_tasks(rows, exchange_map={})
    results = classify_symbols(tasks, concurrency=1)

    assert {r.status for r in results} == {"ERROR"}
    assert all("custom venue fetch failed" in r.detail for r in results)


def test_classify_custom_venue_case_insensitive_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FH might store `btc-perp` (lower) while Nado returns `BTC-PERP`."""
    from fh_symbol_check import validator as validator_module

    fake_universe = {"BTC-PERP": True}
    monkeypatch.setattr(
        validator_module, "get_checker", lambda cid: (lambda: fake_universe)
    )

    rows = [_row("NADO", ("btc-perp",))]
    tasks, _ = build_tasks(rows, exchange_map={})
    results = classify_symbols(tasks, concurrency=1)

    assert results[0].status == "LISTED"
