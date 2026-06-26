from __future__ import annotations

import csv
import io
import json

from fh_symbol_check.models import SymbolResult
from fh_symbol_check.reporter import (
    FeedHandlerSummary,
    keep_fhs_with_errors,
    render,
    summary,
    summary_by_fh,
)


def _r(
    status: str,
    *,
    symbol: str = "BTC/USDT",
    ccxt_symbol: str | None = None,
    detail: str = "",
    exchange_name: str = "HUOBI",
    ccxt_id: str = "htx",
    fh_name: str = "fh_huobi_4002",
    hostname: str = "TA-TKY-A-41_LOCAL",
    service_id: int = 4002,
) -> SymbolResult:
    return SymbolResult(
        service_id=service_id,
        fh_name=fh_name,
        hostname=hostname,
        exchange_name=exchange_name,
        ccxt_id=ccxt_id,
        original_symbol=symbol,
        ccxt_symbol=ccxt_symbol if ccxt_symbol is not None else symbol,
        status=status,  # type: ignore[arg-type]
        detail=detail,
    )


def test_summary_counts_all_statuses() -> None:
    results = [_r("LISTED"), _r("LISTED"), _r("INACTIVE"), _r("DELISTED"), _r("ERROR")]
    assert summary(results) == {"LISTED": 2, "INACTIVE": 1, "DELISTED": 1, "ERROR": 1}


def test_summary_includes_zeros_when_no_results() -> None:
    assert summary([]) == {"LISTED": 0, "INACTIVE": 0, "DELISTED": 0, "ERROR": 0}


def test_render_json_is_valid() -> None:
    results = [_r("LISTED"), _r("DELISTED", symbol="DEAD/USDT")]
    out = io.StringIO()
    render(results, "json", out)
    data = json.loads(out.getvalue())
    assert isinstance(data, list) and len(data) == 2
    assert data[0]["status"] == "LISTED"
    assert data[1]["original_symbol"] == "DEAD/USDT"


def test_render_csv_has_expected_header() -> None:
    results = [_r("DELISTED", symbol="DEAD/USDT", detail="not found")]
    out = io.StringIO()
    render(results, "csv", out)
    reader = csv.DictReader(io.StringIO(out.getvalue()))
    header = reader.fieldnames
    assert header == [
        "service_id",
        "fh_name",
        "hostname",
        "exchange_name",
        "ccxt_id",
        "original_symbol",
        "ccxt_symbol",
        "status",
        "detail",
    ]
    row = next(reader)
    assert row["original_symbol"] == "DEAD/USDT"
    assert row["status"] == "DELISTED"


def test_render_text_omits_listed_by_default_and_includes_invalid() -> None:
    results = [
        _r("LISTED"),
        _r("DELISTED", symbol="DEAD/USDT"),
        _r("INACTIVE", symbol="ZOMBIE/USDT"),
    ]
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()
    assert "BTC/USDT" not in text  # LISTED hidden
    assert "DEAD/USDT" in text
    assert "ZOMBIE/USDT" in text
    assert "Summary: LISTED=1 INACTIVE=1 DELISTED=1 ERROR=0" in text


def test_render_text_show_listed_includes_listed() -> None:
    results = [_r("LISTED")]
    out = io.StringIO()
    render(results, "text", out, show_listed=True)
    assert "BTC/USDT" in out.getvalue()


def test_render_text_no_invalid_says_so() -> None:
    results = [_r("LISTED"), _r("LISTED")]
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()
    assert "No invalid symbols found" in text
    assert "Summary: LISTED=2 INACTIVE=0 DELISTED=0 ERROR=0" in text


def test_render_text_shows_ccxt_symbol_when_different() -> None:
    results = [
        _r("LISTED", symbol="BTC/USDC-PERP", ccxt_symbol="BTC/USDC:USDC"),
        _r("DELISTED", symbol="DEAD/USDC-PERP", ccxt_symbol="DEAD/USDC:USDC"),
    ]
    out = io.StringIO()
    render(results, "text", out, show_listed=True)
    text = out.getvalue()
    assert "DEAD/USDC-PERP" in text
    assert "ccxt=DEAD/USDC:USDC" in text


def test_summary_by_fh_empty_input() -> None:
    assert summary_by_fh([]) == []


def test_summary_by_fh_aggregates_per_feed_handler() -> None:
    results = [
        # fh_huobi_4002 (HUOBI): 3 LISTED, 1 INACTIVE, 1 DELISTED, 1 ERROR
        _r("LISTED", symbol="BTC/USDT"),
        _r("LISTED", symbol="ETH/USDT"),
        _r("LISTED", symbol="SOL/USDT"),
        _r("INACTIVE", symbol="ZOMBIE/USDT"),
        _r("DELISTED", symbol="DEAD/USDT"),
        _r("ERROR", symbol="WAT/USDT", detail="network"),
        # fh_woodex_4003 (WOODEX): 2 DELISTED only
        _r(
            "DELISTED",
            symbol="AR/USDC-PERP",
            ccxt_symbol="AR/USDC:USDC",
            exchange_name="WOODEX",
            ccxt_id="woofipro",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
        _r(
            "DELISTED",
            symbol="STBL/USDC-PERP",
            ccxt_symbol="STBL/USDC:USDC",
            exchange_name="WOODEX",
            ccxt_id="woofipro",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
    ]
    rows = summary_by_fh(results)

    assert len(rows) == 2

    # Sorted by (hostname, fh_name, exchange_name) so fh_huobi_4002 comes first
    huobi = rows[0]
    assert huobi == FeedHandlerSummary(
        fh_name="fh_huobi_4002",
        hostname="TA-TKY-A-41_LOCAL",
        exchange_name="HUOBI",
        active=3,  # LISTED only
        inactive=1,
        delisted=1,
        error=1,
        total_dead=2,
        total=6,
    )

    woodex = rows[1]
    assert woodex == FeedHandlerSummary(
        fh_name="fh_woodex_4003",
        hostname="TA-TKY-A-41_LOCAL",
        exchange_name="WOODEX",
        active=0,
        inactive=0,
        delisted=2,
        error=0,
        total_dead=2,
        total=2,
    )

    # Sanity: columns sum to total for every row
    for row in rows:
        assert row.active + row.inactive + row.delisted + row.error == row.total


def test_summary_by_fh_groups_by_all_three_keys() -> None:
    """Two FHs with the same exchange_name but different fh_name must be separate rows."""
    results = [
        _r("LISTED", symbol="A/USDT", fh_name="fh_huobi_4002", service_id=4002),
        _r("DELISTED", symbol="B/USDT", fh_name="fh_huobi_4004", service_id=4004),
    ]
    rows = summary_by_fh(results)
    assert len(rows) == 2
    assert {r.fh_name for r in rows} == {"fh_huobi_4002", "fh_huobi_4004"}


def test_render_text_includes_summary_table() -> None:
    results = [
        _r("LISTED", symbol="BTC/USDT"),
        _r("DELISTED", symbol="DEAD/USDT"),
    ]
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()

    assert "Summary by feed handler:" in text
    # Header row
    for col in (
        "fh_name",
        "hostname",
        "exchange_name",
        "active",
        "inactive",
        "delisted",
        "error",
        "total dead",
        "total",
    ):
        assert col in text
    # Data row
    assert "fh_huobi_4002" in text
    assert "HUOBI" in text


def test_render_text_no_summary_table_when_empty() -> None:
    out = io.StringIO()
    render([], "text", out)
    text = out.getvalue()
    assert "Summary by feed handler:" not in text
    assert "Summary: LISTED=0" in text


def test_keep_fhs_with_errors_empty_input() -> None:
    assert keep_fhs_with_errors([]) == []


def test_keep_fhs_with_errors_no_errors_returns_empty() -> None:
    results = [
        _r("LISTED", symbol="A/USDT"),
        _r("DELISTED", symbol="B/USDT"),
        _r("INACTIVE", symbol="C/USDT"),
    ]
    assert keep_fhs_with_errors(results) == []


def test_keep_fhs_with_errors_keeps_all_rows_of_offending_fhs() -> None:
    """One FH with an ERROR row should survive completely \u2014 LISTED/DELISTED
    rows of the same FH are retained so its summary stays accurate."""
    results = [
        _r("LISTED", symbol="A/USDT"),
        _r("DELISTED", symbol="B/USDT"),
        _r("ERROR", symbol="C/USDT", detail="boom"),
    ]
    kept = keep_fhs_with_errors(results)
    assert {r.original_symbol for r in kept} == {"A/USDT", "B/USDT", "C/USDT"}


def test_keep_fhs_with_errors_filters_out_clean_fhs() -> None:
    results = [
        # fh_huobi_4002 \u2014 no errors, must be dropped
        _r("LISTED", symbol="A/USDT", fh_name="fh_huobi_4002", service_id=4002),
        _r("DELISTED", symbol="B/USDT", fh_name="fh_huobi_4002", service_id=4002),
        # fh_kucoindm_4005 \u2014 has an ERROR, must be kept entirely
        _r(
            "LISTED",
            symbol="X/USDT-PERP",
            ccxt_symbol="X/USDT:USDT",
            exchange_name="KUCOINDM",
            ccxt_id="kucoinfutures",
            fh_name="fh_kucoindm_4005",
            service_id=4005,
        ),
        _r(
            "ERROR",
            symbol="Y/USDT-PERP",
            ccxt_symbol="Y/USDT:USDT",
            exchange_name="KUCOINDM",
            ccxt_id="kucoinfutures",
            fh_name="fh_kucoindm_4005",
            service_id=4005,
            detail="load_markets failed",
        ),
    ]
    kept = keep_fhs_with_errors(results)
    assert {r.fh_name for r in kept} == {"fh_kucoindm_4005"}
    assert len(kept) == 2  # LISTED + ERROR from the offending FH only


def test_keep_fhs_with_errors_groups_by_full_key() -> None:
    """Two FHs sharing fh_name but on different hostnames must be considered
    distinct; an ERROR on one must not pull in rows from the other."""
    results = [
        _r("ERROR", symbol="A/USDT", hostname="host_a", detail="boom"),
        _r("LISTED", symbol="B/USDT", hostname="host_b"),
    ]
    kept = keep_fhs_with_errors(results)
    assert {r.hostname for r in kept} == {"host_a"}


def test_render_text_summary_table_has_total_row() -> None:
    results = [
        # fh_huobi_4002: 2 LISTED, 1 DELISTED, 1 ERROR
        _r("LISTED", symbol="A/USDT"),
        _r("LISTED", symbol="B/USDT"),
        _r("DELISTED", symbol="C/USDT"),
        _r("ERROR", symbol="D/USDT"),
        # fh_woodex_4003: 3 LISTED, 1 INACTIVE
        _r(
            "LISTED",
            symbol="X/USDC-PERP",
            exchange_name="WOODEX",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
        _r(
            "LISTED",
            symbol="Y/USDC-PERP",
            exchange_name="WOODEX",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
        _r(
            "LISTED",
            symbol="Z/USDC-PERP",
            exchange_name="WOODEX",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
        _r(
            "INACTIVE",
            symbol="W/USDC-PERP",
            exchange_name="WOODEX",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
    ]
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()

    # Identify the TOTAL row line
    total_lines = [line for line in text.splitlines() if line.startswith("| TOTAL")]
    assert len(total_lines) == 1, f"expected exactly one TOTAL row, got: {total_lines}"

    # active=5, inactive=1, delisted=1, error=1, total_dead=2, total=8
    total_line = total_lines[0]
    cells = [c.strip() for c in total_line.strip("|").split("|")]
    # text columns (fh_name, hostname, exchange_name) + 6 numeric columns
    assert cells[0] == "TOTAL"
    assert cells[1] == "" and cells[2] == ""
    assert cells[3:] == ["5", "1", "1", "1", "2", "8"]
