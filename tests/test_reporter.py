from __future__ import annotations

import csv
import io
import json

from fh_symbol_check.models import SymbolResult
from fh_symbol_check.reporter import (
    ExchangeSummary,
    FeedHandlerSummary,
    keep_fhs_with_errors,
    render,
    summary,
    summary_by_exchange,
    summary_by_fh,
    summary_by_repeater,
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
    service_id: int | None = 4002,
    source: str = "fh",
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
        source=source,  # type: ignore[arg-type]
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
        "source",
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
    assert row["source"] == "fh"


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


# ---------------------------------------------------------------------------
# summary_by_exchange + --exchange-grouping
# ---------------------------------------------------------------------------


def _make_two_exchange_dataset() -> list[SymbolResult]:
    """Two FHs on HUOBI (one on each host) + one FH on WOODEX, mixed statuses."""
    return [
        # HUOBI / TA-TKY-A-41 / fh_huobi_4002: 2 LISTED, 1 DELISTED
        _r("LISTED", symbol="A/USDT"),
        _r("LISTED", symbol="B/USDT"),
        _r("DELISTED", symbol="C/USDT"),
        # HUOBI / TA-TKY-A-95 / fh_huobi_4006: 1 LISTED, 1 INACTIVE, 1 ERROR
        _r("LISTED", symbol="D/USDT", hostname="TA-TKY-A-95_LOCAL", fh_name="fh_huobi_4006", service_id=4006),
        _r("INACTIVE", symbol="E/USDT", hostname="TA-TKY-A-95_LOCAL", fh_name="fh_huobi_4006", service_id=4006),
        _r("ERROR", symbol="F/USDT", hostname="TA-TKY-A-95_LOCAL", fh_name="fh_huobi_4006", service_id=4006, detail="boom"),
        # WOODEX / TA-TKY-A-41 / fh_woodex_4003: 1 LISTED only
        _r(
            "LISTED",
            symbol="X/USDC-PERP",
            ccxt_symbol="X/USDC:USDC",
            exchange_name="WOODEX",
            ccxt_id="woofipro",
            fh_name="fh_woodex_4003",
            service_id=4003,
        ),
    ]


def test_summary_by_exchange_aggregates_correctly() -> None:
    results = _make_two_exchange_dataset()
    by_ex = summary_by_exchange(results)
    by_name = {s.exchange_name: s for s in by_ex}

    assert set(by_name) == {"HUOBI", "WOODEX"}

    huobi = by_name["HUOBI"]
    assert huobi.feed_handlers == 2  # two distinct (hostname, fh_name) pairs
    assert (huobi.active, huobi.inactive, huobi.delisted, huobi.error) == (3, 1, 1, 1)
    assert huobi.total_dead == 2  # inactive + delisted
    assert huobi.total == 6

    woodex = by_name["WOODEX"]
    assert woodex.feed_handlers == 1
    assert (woodex.active, woodex.inactive, woodex.delisted, woodex.error) == (1, 0, 0, 0)
    assert woodex.total_dead == 0
    assert woodex.total == 1


def test_summary_by_exchange_sorted_alphabetically() -> None:
    results = _make_two_exchange_dataset()
    by_ex = summary_by_exchange(results)
    assert [s.exchange_name for s in by_ex] == sorted(s.exchange_name for s in by_ex)


def test_summary_by_exchange_empty_returns_empty() -> None:
    assert summary_by_exchange([]) == []


def test_render_text_exchange_grouping_table_replaces_fh_table() -> None:
    """With exchange_grouping=True, the per-FH table must be absent and the
    per-exchange table must be present."""
    results = _make_two_exchange_dataset()
    out = io.StringIO()
    render(results, "text", out, exchange_grouping=True)
    text = out.getvalue()

    assert "Summary by exchange:" in text
    assert "Summary by feed handler:" not in text


def test_render_text_default_grouping_keeps_fh_table() -> None:
    """Without --exchange-grouping, the original per-FH table is rendered and
    the per-exchange table is NOT."""
    results = _make_two_exchange_dataset()
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()

    assert "Summary by feed handler:" in text
    assert "Summary by exchange:" not in text


def test_render_text_exchange_grouping_total_row_sums_correctly() -> None:
    results = _make_two_exchange_dataset()
    out = io.StringIO()
    render(results, "text", out, exchange_grouping=True)
    text = out.getvalue()

    total_lines = [line for line in text.splitlines() if line.startswith("| TOTAL")]
    assert len(total_lines) == 1
    cells = [c.strip() for c in total_lines[0].strip("|").split("|")]
    # exchange_name, source, feed_handlers, active, inactive, delisted, error, total_dead, total
    # The TOTAL row leaves `source` empty (only aggregates the numeric columns).
    # HUOBI/fh: fh=2 act=3 in=1 del=1 err=1 dead=2 tot=6
    # WOODEX/fh: fh=1 act=1 in=0 del=0 err=0 dead=0 tot=1
    # TOTAL: fh=3 act=4 in=1 del=1 err=1 dead=2 tot=7
    assert cells == ["TOTAL", "", "3", "4", "1", "1", "1", "2", "7"]


def test_render_text_suppress_details_skips_per_symbol_rows() -> None:
    """suppress_details=True hides the per-FH detail block entirely (but the
    summary table and global summary line are still emitted)."""
    results = _make_two_exchange_dataset()
    out = io.StringIO()
    render(results, "text", out, exchange_grouping=True, suppress_details=True)
    text = out.getvalue()

    # The per-FH header line "[hostname] fh_name (exchange_name)" must be absent
    assert "[TA-TKY-A-41_LOCAL]" not in text
    assert "[TA-TKY-A-95_LOCAL]" not in text
    # but the per-symbol DELISTED/INACTIVE/ERROR lines should also be absent
    assert " DELISTED " not in text  # detail-row format has 2-space indent + status
    assert " INACTIVE " not in text
    # Summary table + global summary line should still be there
    assert "Summary by exchange:" in text
    assert "Summary:" in text


def test_render_text_suppress_details_false_keeps_per_symbol_rows() -> None:
    """When suppress_details=False (the default), per-symbol detail rows are
    rendered even with --exchange-grouping (this is the file-output case)."""
    results = _make_two_exchange_dataset()
    out = io.StringIO()
    render(
        results,
        "text",
        out,
        exchange_grouping=True,
        suppress_details=False,
    )
    text = out.getvalue()

    # Detail header for HUOBI/A-95 (which has the INACTIVE and ERROR rows)
    assert "[TA-TKY-A-95_LOCAL] fh_huobi_4006 (HUOBI)" in text
    # And the per-exchange summary table is still present
    assert "Summary by exchange:" in text


def test_render_json_ignores_exchange_grouping() -> None:
    """JSON output must be untouched by --exchange-grouping (text-only flag)."""
    results = _make_two_exchange_dataset()
    out_json_default = io.StringIO()
    out_json_with_flag = io.StringIO()
    render(results, "json", out_json_default)
    render(results, "json", out_json_with_flag, exchange_grouping=True, suppress_details=True)
    assert out_json_default.getvalue() == out_json_with_flag.getvalue()


def test_render_csv_ignores_exchange_grouping() -> None:
    results = _make_two_exchange_dataset()
    out_csv_default = io.StringIO()
    out_csv_with_flag = io.StringIO()
    render(results, "csv", out_csv_default)
    render(results, "csv", out_csv_with_flag, exchange_grouping=True, suppress_details=True)
    assert out_csv_default.getvalue() == out_csv_with_flag.getvalue()


def test_exchange_summary_dataclass_basic() -> None:
    """Just making sure the dataclass is importable and constructible."""
    s = ExchangeSummary(
        exchange_name="HUOBI",
        source="fh",
        feed_handlers=2,
        active=3,
        inactive=1,
        delisted=1,
        error=1,
        total_dead=2,
        total=6,
    )
    assert s.exchange_name == "HUOBI"
    assert s.source == "fh"
    assert s.feed_handlers == 2


# ---------------------------------------------------------------------------
# Source-split summaries (fh vs repeater)
# ---------------------------------------------------------------------------


def _mixed_source_dataset() -> list[SymbolResult]:
    """Small dataset covering both sources on the same exchange."""
    return [
        # FH rows on HUOBI
        _r("LISTED", exchange_name="HUOBI", ccxt_id="htx",
           fh_name="fh_huobi", source="fh"),
        _r("DELISTED", symbol="DEAD/USDT", exchange_name="HUOBI", ccxt_id="htx",
           fh_name="fh_huobi", source="fh"),
        # Repeater rows on HUOBI
        _r("LISTED", exchange_name="HUOBI", ccxt_id="htx",
           fh_name="rp_huobi", service_id=None, source="repeater"),
        _r("INACTIVE", symbol="OLD/USDT", exchange_name="HUOBI", ccxt_id="htx",
           fh_name="rp_huobi", service_id=None, source="repeater"),
        # Repeater rows on BINANCE
        _r("LISTED", exchange_name="BINANCE", ccxt_id="binance",
           fh_name="rp_binance", service_id=None, source="repeater"),
    ]


def test_summary_by_fh_filters_to_fh_source_only() -> None:
    results = _mixed_source_dataset()
    fh_summaries = summary_by_fh(results)
    assert [s.fh_name for s in fh_summaries] == ["fh_huobi"]
    assert fh_summaries[0].active == 1
    assert fh_summaries[0].delisted == 1


def test_summary_by_repeater_filters_to_repeater_source_only() -> None:
    results = _mixed_source_dataset()
    rp_summaries = summary_by_repeater(results)
    names = sorted(s.fh_name for s in rp_summaries)
    assert names == ["rp_binance", "rp_huobi"]
    huobi = next(s for s in rp_summaries if s.fh_name == "rp_huobi")
    assert huobi.active == 1
    assert huobi.inactive == 1


def test_summary_by_exchange_groups_by_exchange_and_source() -> None:
    """The exchange summary must split (exchange, source) so a repeater and an
    FH on the same exchange become two rows."""
    results = _mixed_source_dataset()
    rows = summary_by_exchange(results)
    keys = [(r.exchange_name, r.source) for r in rows]
    assert keys == [
        ("BINANCE", "repeater"),
        ("HUOBI", "fh"),
        ("HUOBI", "repeater"),
    ]
    huobi_fh = next(r for r in rows if r.exchange_name == "HUOBI" and r.source == "fh")
    huobi_rp = next(r for r in rows if r.exchange_name == "HUOBI" and r.source == "repeater")
    assert huobi_fh.feed_handlers == 1 and huobi_fh.total == 2
    assert huobi_rp.feed_handlers == 1 and huobi_rp.total == 2


def test_render_text_emits_two_labelled_detail_sections() -> None:
    """Detail blocks for fh vs repeater rows are separated by titled section
    headers so the operator can tell them apart at a glance."""
    results = _mixed_source_dataset()
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()

    fh_idx = text.find("--- Feed handlers ---")
    rp_idx = text.find("--- Repeaters ---")
    assert fh_idx >= 0, "expected the FH section header"
    assert rp_idx >= 0, "expected the repeater section header"
    assert fh_idx < rp_idx, "FH section must come before repeater section"

    fh_section = text[fh_idx:rp_idx]
    rp_section = text[rp_idx:]
    # FH section shows the FH DELISTED row, not the repeater INACTIVE row.
    assert "DELISTED  DEAD/USDT" in fh_section
    assert "OLD/USDT" not in fh_section
    # Repeater section shows the repeater INACTIVE row, not the FH DELISTED.
    assert "INACTIVE  OLD/USDT" in rp_section
    assert "DEAD/USDT" not in rp_section


def test_render_text_emits_two_summary_tables_with_correct_headers() -> None:
    results = _mixed_source_dataset()
    out = io.StringIO()
    render(results, "text", out)
    text = out.getvalue()

    fh_title_idx = text.find("Summary by feed handler:")
    rp_title_idx = text.find("Summary by repeater:")
    assert fh_title_idx >= 0
    assert rp_title_idx >= 0
    assert fh_title_idx < rp_title_idx

    # The FH table uses `fh_name` as the first column; the repeater table uses `app_name`.
    fh_block = text[fh_title_idx:rp_title_idx]
    rp_block = text[rp_title_idx:]
    assert "| fh_name" in fh_block
    assert "| app_name" in rp_block


def test_render_text_single_source_only_renders_one_summary_table() -> None:
    """A pure-fh (or pure-repeater) run must not emit an empty second table."""
    fh_only = [
        _r("LISTED", exchange_name="HUOBI", source="fh"),
        _r("DELISTED", symbol="DEAD/USDT", exchange_name="HUOBI", source="fh"),
    ]
    out = io.StringIO()
    render(fh_only, "text", out)
    text = out.getvalue()
    assert "Summary by feed handler:" in text
    assert "Summary by repeater:" not in text
    assert "--- Repeaters ---" not in text


def test_render_text_exchange_grouping_row_has_source_column() -> None:
    """With --exchange-grouping, the per-exchange table gains a `source` column
    so (exchange, source) is the group key."""
    results = _mixed_source_dataset()
    out = io.StringIO()
    render(results, "text", out, exchange_grouping=True)
    text = out.getvalue()

    header_line = next(
        line for line in text.splitlines() if line.strip().startswith("| exchange_name")
    )
    header_cells = [c.strip() for c in header_line.strip("|").split("|")]
    assert header_cells[:2] == ["exchange_name", "source"]

    # Two HUOBI rows (fh + repeater) and one BINANCE row (repeater).
    huobi_lines = [
        line for line in text.splitlines()
        if line.startswith("| HUOBI")
    ]
    assert len(huobi_lines) == 2
    assert any("| fh" in line for line in huobi_lines)
    assert any("| repeater" in line for line in huobi_lines)


def test_keep_fhs_with_errors_disambiguates_by_source() -> None:
    """A feed handler and a repeater with the same fh_name / hostname / exchange
    must not be conflated: an ERROR on one must not drag the other in."""
    results = [
        _r("LISTED", exchange_name="HUOBI", fh_name="dup", source="fh"),
        _r("LISTED", exchange_name="HUOBI", fh_name="dup",
           service_id=None, source="repeater"),
        _r("ERROR", exchange_name="HUOBI", fh_name="dup",
           service_id=None, source="repeater"),
    ]
    kept = keep_fhs_with_errors(results)
    # Only the two repeater rows should survive (the fh_name=dup FH row must not
    # be dragged in by the repeater's ERROR).
    assert len(kept) == 2
    assert all(r.source == "repeater" for r in kept)
