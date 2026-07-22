from __future__ import annotations

import pytest

import fh_symbol_check.validator as validator_module
from fh_symbol_check.models import FeedHandlerRow
from fh_symbol_check.models import ResolvedTask, SymbolResult
from fh_symbol_check.validator import build_tasks, classify_symbols, filter_by_symbol


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


# ---------------------------------------------------------------------------
# --symbol filter (filter_by_symbol)
# ---------------------------------------------------------------------------


def _task(original: str, *, ccxt_symbol: str | None = None, ccxt_id: str = "binance") -> ResolvedTask:
    return ResolvedTask(
        service_id=4001,
        fh_name="fh_test_4001",
        hostname="TA-TKY-A-41_LOCAL",
        exchange_name="BINANCE",
        ccxt_id=ccxt_id,
        original_symbol=original,
        ccxt_symbol=ccxt_symbol if ccxt_symbol is not None else original,
    )


def _err(original: str, *, ccxt_symbol: str = "") -> SymbolResult:
    return SymbolResult(
        service_id=4001,
        fh_name="fh_unknown_4001",
        hostname="TA-TKY-A-41_LOCAL",
        exchange_name="UNKNOWNEX",
        ccxt_id="",
        original_symbol=original,
        ccxt_symbol=ccxt_symbol,
        status="ERROR",
        detail="unknown exchange_name='UNKNOWNEX'; add it to exchange_mapping.yaml",
    )


def test_filter_by_symbol_matches_original_side() -> None:
    tasks = [
        _task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT"),
        _task("BTC/USDT-PERP", ccxt_symbol="BTC/USDT:USDT"),
    ]
    matched_tasks, matched_errors = filter_by_symbol(tasks, [], ["IP/USDT-PERP"])
    assert [t.original_symbol for t in matched_tasks] == ["IP/USDT-PERP"]
    assert matched_errors == []


def test_filter_by_symbol_matches_ccxt_side() -> None:
    """User passes the translated venue form; we should still match the row."""
    tasks = [
        _task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT"),
        _task("BTC/USDT-PERP", ccxt_symbol="BTC/USDT:USDT"),
    ]
    matched_tasks, _ = filter_by_symbol(tasks, [], ["IP/USDT:USDT"])
    assert [t.original_symbol for t in matched_tasks] == ["IP/USDT-PERP"]


def test_filter_by_symbol_is_case_sensitive() -> None:
    """We promised case-sensitive exact matching; `IP/USDT-Perp` must NOT match `IP/USDT-PERP`."""
    tasks = [_task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT")]
    matched_tasks, _ = filter_by_symbol(tasks, [], ["IP/USDT-Perp"])
    assert matched_tasks == []


def test_filter_by_symbol_exact_only_not_substring() -> None:
    """`IP/USDT` must NOT match `1000IP/USDT-PERP` or `IP/USDT-PERP` \u2014 it's a full-string equality."""
    tasks = [
        _task("1000IP/USDT-PERP", ccxt_symbol="1000IP/USDT:USDT"),
        _task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT"),
    ]
    matched_tasks, _ = filter_by_symbol(tasks, [], ["IP/USDT"])
    assert matched_tasks == []


def test_filter_by_symbol_filters_unmappable_errors_too() -> None:
    """The early-errors list (unmappable exchange_name) is also filtered so
    `--symbol X --exchange-name UNKNOWNEX` still surfaces X's ERROR row."""
    errors = [
        _err("IP/USDT-PERP"),
        _err("BTC/USDT-PERP"),
    ]
    _, matched_errors = filter_by_symbol([], errors, ["IP/USDT-PERP"])
    assert [e.original_symbol for e in matched_errors] == ["IP/USDT-PERP"]


def test_filter_by_symbol_no_match_returns_empty() -> None:
    tasks = [_task("BTC/USDT-PERP", ccxt_symbol="BTC/USDT:USDT")]
    errors = [_err("ETH/USDT-PERP")]
    matched_tasks, matched_errors = filter_by_symbol(tasks, errors, ["DOGE/USDT-PERP"])
    assert matched_tasks == [] and matched_errors == []


def test_filter_by_symbol_does_not_mutate_inputs() -> None:
    tasks = [_task("IP/USDT-PERP"), _task("BTC/USDT-PERP")]
    errors = [_err("IP/USDT-PERP")]
    tasks_before = list(tasks)
    errors_before = list(errors)
    _ = filter_by_symbol(tasks, errors, ["IP/USDT-PERP"])
    assert tasks == tasks_before
    assert errors == errors_before


def test_filter_by_symbol_keeps_all_fhs_for_that_symbol() -> None:
    """The same symbol on multiple FHs should all survive the filter."""
    tasks = [
        ResolvedTask(
            service_id=4001, fh_name="fh_a", hostname="host-a",
            exchange_name="BINANCE", ccxt_id="binance",
            original_symbol="IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT",
        ),
        ResolvedTask(
            service_id=4002, fh_name="fh_b", hostname="host-b",
            exchange_name="BYBIT", ccxt_id="bybit",
            original_symbol="IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT",
        ),
        ResolvedTask(
            service_id=4003, fh_name="fh_c", hostname="host-c",
            exchange_name="HUOBI", ccxt_id="htx",
            original_symbol="OTHER/USDT", ccxt_symbol="OTHER/USDT",
        ),
    ]
    matched_tasks, _ = filter_by_symbol(tasks, [], ["IP/USDT-PERP"])
    assert {t.fh_name for t in matched_tasks} == {"fh_a", "fh_b"}


# ---------------------------------------------------------------------------
# --symbol filter (filter_by_symbol) — multi-value / OR semantics
# ---------------------------------------------------------------------------


def test_filter_by_symbol_multi_value_or_semantics() -> None:
    """`--symbol A B` keeps rows matching A OR B (union), not the intersection."""
    tasks = [
        _task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT"),
        _task("BTC/USDT-PERP", ccxt_symbol="BTC/USDT:USDT"),
        _task("ETH/USDT-PERP", ccxt_symbol="ETH/USDT:USDT"),
    ]
    matched_tasks, _ = filter_by_symbol(
        tasks, [], ["IP/USDT-PERP", "BTC/USDT-PERP"]
    )
    assert [t.original_symbol for t in matched_tasks] == [
        "IP/USDT-PERP",
        "BTC/USDT-PERP",
    ]


def test_filter_by_symbol_multi_value_mixes_sides() -> None:
    """A caller can mix FH-form and ccxt-form entries in the same call."""
    tasks = [
        _task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT"),
        _task("BTC/USDT-PERP", ccxt_symbol="BTC/USDT:USDT"),
        _task("ETH/USDT-PERP", ccxt_symbol="ETH/USDT:USDT"),
    ]
    matched_tasks, _ = filter_by_symbol(
        tasks, [], ["IP/USDT-PERP", "BTC/USDT:USDT"]  # FH form + ccxt form
    )
    assert [t.original_symbol for t in matched_tasks] == [
        "IP/USDT-PERP",
        "BTC/USDT-PERP",
    ]


def test_filter_by_symbol_multi_value_deduplicates() -> None:
    """Duplicate symbols in the list must not duplicate matched rows."""
    tasks = [_task("IP/USDT-PERP", ccxt_symbol="IP/USDT:USDT")]
    matched_tasks, _ = filter_by_symbol(
        tasks, [], ["IP/USDT-PERP", "IP/USDT-PERP", "IP/USDT:USDT"]
    )
    assert len(matched_tasks) == 1


def test_filter_by_symbol_empty_iterable_filters_everything_out() -> None:
    """Empty ``symbols`` matches nothing (the CLI prevents this path via
    argparse ``nargs='+'``, but the pure function must still be well-defined)."""
    tasks = [_task("IP/USDT-PERP"), _task("BTC/USDT-PERP")]
    errors = [_err("IP/USDT-PERP")]
    matched_tasks, matched_errors = filter_by_symbol(tasks, errors, [])
    assert matched_tasks == [] and matched_errors == []


# ---------------------------------------------------------------------------
# source propagation (fh vs repeater)
# ---------------------------------------------------------------------------


def _repeater_row(
    exchange_name: str, symbols: tuple[str, ...], app_name: str = "rp_binance_a"
) -> FeedHandlerRow:
    return FeedHandlerRow(
        service_id=None,
        fh_name=app_name,
        hostname="TA-TKY-B-01",
        exchange_name=exchange_name,
        symbols=symbols,
        source="repeater",
    )


def test_build_tasks_propagates_source_from_repeater_row() -> None:
    rows = [
        _row("HUOBI", ("BTC/USDT",)),  # default source="fh"
        _repeater_row("BINANCE", ("BTC/USDT",)),
    ]
    tasks, errors = build_tasks(rows, {"HUOBI": "htx", "BINANCE": "binance"})

    assert errors == []
    assert len(tasks) == 2
    by_ccxt = {t.ccxt_id: t for t in tasks}
    assert by_ccxt["htx"].source == "fh"
    assert by_ccxt["htx"].service_id == 4002
    assert by_ccxt["binance"].source == "repeater"
    assert by_ccxt["binance"].service_id is None
    assert by_ccxt["binance"].fh_name == "rp_binance_a"


def test_build_tasks_propagates_source_on_unknown_exchange_error() -> None:
    """Unmappable rows become ERROR SymbolResults; the source must be preserved
    so the reporter can render them in the correct section."""
    rows = [_repeater_row("MYSTERY", ("BTC/USDT",))]
    tasks, errors = build_tasks(rows, {"HUOBI": "htx"})

    assert tasks == []
    assert len(errors) == 1
    assert errors[0].status == "ERROR"
    assert errors[0].source == "repeater"
    assert errors[0].service_id is None


def test_classify_symbols_propagates_source_from_task_to_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeater-sourced ResolvedTask must yield a repeater-sourced SymbolResult
    after ccxt classification."""

    def fake_load(_ccxt_id):
        return None, {"BTC/USDT": {"active": True}}

    def fake_classify(_markets, _sym):
        return "LISTED", ""

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_load)
    monkeypatch.setattr(validator_module, "classify", fake_classify)

    tasks = [
        ResolvedTask(
            service_id=None,
            fh_name="rp_binance_a",
            hostname="TA-TKY-B-01",
            exchange_name="BINANCE",
            ccxt_id="binance",
            original_symbol="BTC/USDT",
            ccxt_symbol="BTC/USDT",
            source="repeater",
        ),
    ]
    [result] = classify_symbols(tasks, concurrency=1)
    assert result.status == "LISTED"
    assert result.source == "repeater"
    assert result.service_id is None
    assert result.fh_name == "rp_binance_a"
