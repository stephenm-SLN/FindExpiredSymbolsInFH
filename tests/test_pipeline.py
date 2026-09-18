"""Tests for the shared orchestration entry (`fh_symbol_check.pipeline`).

These lock the CLI/API contract at the pipeline boundary:

- `ScanFilters.validate()` messages match the CLI's argparse strings
  (so both fronts surface identical error wording to end users).
- `describe_filters()` produces a stable, human-readable summary.
- `run_scan()` fetches from the right producer table(s) per `source`,
  respects `--symbol` OR-semantics, feeds classify with progress
  reporting, and surfaces DB errors unchanged.
"""

from __future__ import annotations

import pytest

import fh_symbol_check.pipeline as pipeline_module
import fh_symbol_check.validator as validator_module
from fh_symbol_check.creds import DBCreds
from fh_symbol_check.db import DBError
from fh_symbol_check.models import FeedHandlerRow
from fh_symbol_check.pipeline import (
    ScanFilters,
    ScanFiltersError,
    ScanProgress,
    describe_filters,
    run_scan,
)


def _creds() -> DBCreds:
    return DBCreds(host="h", user="u", password="s", database="crypto_db")


def _fh_row(
    exchange: str,
    symbols: tuple[str, ...],
    *,
    hostname: str = "TA-TKY-A-41_LOCAL",
    svc: int = 4002,
    source: str = "fh",
    fh_name: str | None = None,
) -> FeedHandlerRow:
    return FeedHandlerRow(
        service_id=svc if source == "fh" else None,
        fh_name=fh_name or f"fh_{exchange.lower()}_{svc}",
        hostname=hostname,
        exchange_name=exchange,
        symbols=symbols,
        source=source,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# ScanFilters.validate — message contract shared with the CLI's parser.error
# ---------------------------------------------------------------------------


def test_validate_rejects_all_combined_with_hostname() -> None:
    with pytest.raises(ScanFiltersError, match="--all cannot be combined"):
        ScanFilters(all_producers=True, hostname="TA-TKY-A-41").validate()


def test_validate_rejects_all_combined_with_exchange_name() -> None:
    with pytest.raises(ScanFiltersError, match="--all cannot be combined"):
        ScanFilters(all_producers=True, exchange_name="HUOBI").validate()


def test_validate_rejects_empty_filter_set() -> None:
    with pytest.raises(ScanFiltersError, match="specify --hostname"):
        ScanFilters().validate()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"all_producers": True},
        {"hostname": "TA-TKY-A-41"},
        {"exchange_name": "HUOBI"},
        {"symbols": ("BTC/USDT-PERP",)},
        {"hostname": "x", "exchange_name": "y"},
        {"hostname": "x", "symbols": ("A",)},
    ],
)
def test_validate_accepts_valid_filter_sets(kwargs: dict) -> None:
    ScanFilters(**kwargs).validate()  # must not raise


# ---------------------------------------------------------------------------
# describe_filters
# ---------------------------------------------------------------------------


def test_describe_filters_empty_all() -> None:
    assert describe_filters(ScanFilters(all_producers=True)) == "ALL (no filters)"


def test_describe_filters_includes_every_active_predicate() -> None:
    got = describe_filters(
        ScanFilters(
            hostname="TA-TKY-A-41",
            exchange_name="HUOBI",
            symbols=("BTC/USDT-PERP", "ETH/USDT-PERP"),
            source="rp",
        )
    )
    assert "hostname LIKE %TA-TKY-A-41%" in got
    assert "exchange_name='HUOBI'" in got
    assert "BTC/USDT-PERP" in got
    assert "source='rp'" in got


def test_describe_filters_omits_source_when_default_both() -> None:
    got = describe_filters(ScanFilters(hostname="X", source="both"))
    assert "source=" not in got


# ---------------------------------------------------------------------------
# run_scan — DB routing per `source`
# ---------------------------------------------------------------------------


def _patch_dbs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fh_rows: list[FeedHandlerRow] | Exception = None,  # type: ignore[assignment]
    rp_rows: list[FeedHandlerRow] | Exception = None,  # type: ignore[assignment]
) -> dict[str, int]:
    """Patch pipeline.fetch_feed_handlers / fetch_repeaters and record call counts."""
    calls = {"fh": 0, "rp": 0}

    def fake_fh(creds, *, hostname_pattern=None, exchange_name=None):
        calls["fh"] += 1
        if isinstance(fh_rows, Exception):
            raise fh_rows
        return list(fh_rows or [])

    def fake_rp(creds, *, hostname_pattern=None, exchange_name=None):
        calls["rp"] += 1
        if isinstance(rp_rows, Exception):
            raise rp_rows
        return list(rp_rows or [])

    monkeypatch.setattr(pipeline_module, "fetch_feed_handlers", fake_fh)
    monkeypatch.setattr(pipeline_module, "fetch_repeaters", fake_rp)
    return calls


def _patch_ccxt(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_loader(ccxt_id: str):
        markets = {
            "htx": {"BTC/USDT": {"active": True}, "ETH/USDT": {"active": False}},
            "woo": {"BTC/USDC:USDC": {"active": True}},
        }
        return object(), markets[ccxt_id]

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_loader)


def test_run_scan_source_fh_calls_only_fh_table(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_dbs(
        monkeypatch,
        fh_rows=[_fh_row("HUOBI", ("BTC/USDT",), source="fh")],
        rp_rows=[_fh_row("HUOBI", ("XRP/USDT",), source="repeater")],
    )
    _patch_ccxt(monkeypatch)

    results = run_scan(
        ScanFilters(all_producers=True, source="fh"),
        _creds(),
        {"HUOBI": "htx"},
    )

    assert calls == {"fh": 1, "rp": 0}
    assert {r.original_symbol for r in results} == {"BTC/USDT"}
    assert all(r.source == "fh" for r in results)


def test_run_scan_source_rp_calls_only_repeater_table(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_dbs(
        monkeypatch,
        fh_rows=[_fh_row("HUOBI", ("BTC/USDT",), source="fh")],
        rp_rows=[_fh_row("HUOBI", ("XRP/USDT",), source="repeater")],
    )
    _patch_ccxt(monkeypatch)

    results = run_scan(
        ScanFilters(all_producers=True, source="rp"),
        _creds(),
        {"HUOBI": "htx"},
    )

    assert calls == {"fh": 0, "rp": 1}
    assert {r.original_symbol for r in results} == {"XRP/USDT"}
    assert all(r.source == "repeater" for r in results)


def test_run_scan_source_both_concatenates_both_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_dbs(
        monkeypatch,
        fh_rows=[_fh_row("HUOBI", ("BTC/USDT",), source="fh")],
        rp_rows=[_fh_row("HUOBI", ("ETH/USDT",), source="repeater")],
    )
    _patch_ccxt(monkeypatch)

    results = run_scan(
        ScanFilters(all_producers=True, source="both"),
        _creds(),
        {"HUOBI": "htx"},
    )

    assert calls == {"fh": 1, "rp": 1}
    sources = {(r.original_symbol, r.source) for r in results}
    assert sources == {("BTC/USDT", "fh"), ("ETH/USDT", "repeater")}


# ---------------------------------------------------------------------------
# run_scan — DB errors surface unchanged
# ---------------------------------------------------------------------------


def test_run_scan_propagates_db_error_from_fh(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dbs(monkeypatch, fh_rows=DBError("connection refused"))
    with pytest.raises(DBError, match="connection refused"):
        run_scan(
            ScanFilters(all_producers=True, source="fh"),
            _creds(),
            {"HUOBI": "htx"},
        )


def test_run_scan_propagates_db_error_from_repeaters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[],
        rp_rows=DBError("access denied"),
    )
    with pytest.raises(DBError, match="access denied"):
        run_scan(
            ScanFilters(all_producers=True, source="rp"),
            _creds(),
            {"HUOBI": "htx"},
        )


# ---------------------------------------------------------------------------
# run_scan — --symbol OR-semantics + classification wiring
# ---------------------------------------------------------------------------


def test_run_scan_applies_symbol_filter_or_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[
            _fh_row(
                "HUOBI",
                ("BTC/USDT", "ETH/USDT", "DELISTED/USDT"),
                source="fh",
            ),
        ],
        rp_rows=[],
    )
    _patch_ccxt(monkeypatch)

    results = run_scan(
        ScanFilters(symbols=("BTC/USDT", "ETH/USDT")),
        _creds(),
        {"HUOBI": "htx"},
    )

    matched = {(r.original_symbol, r.status) for r in results}
    assert matched == {("BTC/USDT", "LISTED"), ("ETH/USDT", "INACTIVE")}


def test_run_scan_unknown_exchange_becomes_error_and_still_flows_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[_fh_row("UNKNOWN_EX", ("BTC/USDT",), source="fh")],
        rp_rows=[],
    )
    _patch_ccxt(monkeypatch)

    results = run_scan(
        ScanFilters(all_producers=True, source="fh"),
        _creds(),
        {"HUOBI": "htx"},  # UNKNOWN_EX intentionally missing
    )
    assert len(results) == 1
    assert results[0].status == "ERROR"
    assert "unknown exchange_name" in results[0].detail


# ---------------------------------------------------------------------------
# run_scan — progress callback
# ---------------------------------------------------------------------------


def test_run_scan_progress_reports_phases_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[
            _fh_row("HUOBI", ("BTC/USDT", "ETH/USDT"), source="fh"),
            _fh_row("WOODEX", ("BTC/USDC-PERP",), source="fh", svc=4003),
        ],
        rp_rows=[],
    )
    _patch_ccxt(monkeypatch)

    seen: list[ScanProgress] = []
    run_scan(
        ScanFilters(all_producers=True, source="fh"),
        _creds(),
        {"HUOBI": "htx", "WOODEX": "woo"},
        on_progress=seen.append,
    )

    phases = [p.phase for p in seen]
    assert phases[0] == "querying_db"
    assert "building_tasks" in phases
    # At least one classifying tick + a final done.
    assert phases.count("classifying") >= 2
    assert phases[-1] == "done"

    # Final done reports both groups (htx + woo) completed.
    final = seen[-1]
    assert final.total_producers == 2
    assert final.completed_producers == 2
    assert final.rows_scanned == 2


def test_run_scan_progress_completed_bumps_once_per_ccxt_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[
            _fh_row("HUOBI", ("BTC/USDT", "ETH/USDT", "XRP/USDT"), source="fh"),
        ],
        rp_rows=[],
    )
    _patch_ccxt(monkeypatch)

    ticks = 0

    def observer(p: ScanProgress) -> None:
        nonlocal ticks
        if p.phase == "classifying" and p.completed_producers > 0:
            ticks += 1

    run_scan(
        ScanFilters(all_producers=True, source="fh"),
        _creds(),
        {"HUOBI": "htx"},
        on_progress=observer,
    )
    # One ccxt_id group ("htx") -> exactly one completion tick during classifying,
    # not one per symbol.
    assert ticks == 1


def test_run_scan_progress_callback_exception_does_not_break_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dbs(
        monkeypatch,
        fh_rows=[_fh_row("HUOBI", ("BTC/USDT",), source="fh")],
        rp_rows=[],
    )
    _patch_ccxt(monkeypatch)

    def bad_observer(_p: ScanProgress) -> None:
        raise RuntimeError("boom")

    # Must not raise — the pipeline swallows callback errors so a broken
    # observer can't take out the scan.
    results = run_scan(
        ScanFilters(all_producers=True, source="fh"),
        _creds(),
        {"HUOBI": "htx"},
        on_progress=bad_observer,
    )
    assert [r.status for r in results] == ["LISTED"]


# ---------------------------------------------------------------------------
# classify_symbols on_group_done — direct check of the new callback
# ---------------------------------------------------------------------------


def test_classify_symbols_on_group_done_called_once_per_ccxt_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fh_symbol_check.validator import build_tasks, classify_symbols

    def fake_loader(ccxt_id: str):
        return object(), {"BTC/USDT": {"active": True}, "BTC/USDC:USDC": {"active": True}}

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_loader)

    rows = [
        _fh_row("HUOBI", ("BTC/USDT",), source="fh"),
        _fh_row("HUOBI", ("BTC/USDT",), source="fh", svc=4003, fh_name="fh_htx_dup"),
        _fh_row("WOODEX", ("BTC/USDC-PERP",), source="fh", svc=4004),
    ]
    tasks, _ = build_tasks(rows, {"HUOBI": "htx", "WOODEX": "woo"})

    seen: list[str] = []
    classify_symbols(tasks, concurrency=1, on_group_done=seen.append)
    # Two distinct ccxt groups -> exactly two callbacks.
    assert sorted(seen) == ["htx", "woo"]


def test_classify_symbols_on_group_done_exception_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fh_symbol_check.validator import build_tasks, classify_symbols

    def fake_loader(ccxt_id: str):
        return object(), {"BTC/USDT": {"active": True}}

    monkeypatch.setattr(validator_module, "load_exchange_markets_safe", fake_loader)

    rows = [_fh_row("HUOBI", ("BTC/USDT",), source="fh")]
    tasks, _ = build_tasks(rows, {"HUOBI": "htx"})

    def bad_cb(_ccxt_id: str) -> None:
        raise RuntimeError("observer down")

    # Must not raise; the classify step returns the LISTED row despite the
    # callback blowing up.
    results = classify_symbols(tasks, concurrency=1, on_group_done=bad_cb)
    assert [r.status for r in results] == ["LISTED"]
