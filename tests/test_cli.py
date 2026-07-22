"""Light-touch CLI tests focused on argparse + the inline validation gates.

We deliberately do not exercise the full main() pipeline (creds/DB/ccxt) here
\u2014 those layers have their own unit tests. This file exists to lock down the
combinations the user is allowed to pass on the command line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fh_symbol_check.cli import EXIT_OPERATIONAL_FAILURE, _build_parser, main


# ---------------------------------------------------------------------------
# Parser-level: argparse stores the value the way we expect
# ---------------------------------------------------------------------------


def test_symbol_arg_parses_and_defaults_to_none() -> None:
    args = _build_parser().parse_args(["--all"])
    assert args.symbol is None


def test_symbol_arg_single_value_becomes_one_element_list() -> None:
    args = _build_parser().parse_args(["--symbol", "IP/USDT-PERP"])
    assert args.symbol == ["IP/USDT-PERP"]


def test_symbol_arg_accepts_multiple_space_separated_values() -> None:
    args = _build_parser().parse_args(
        ["--symbol", "IP/USDT-PERP", "BTC/USDT-PERP", "ETH/USDT-PERP"]
    )
    assert args.symbol == ["IP/USDT-PERP", "BTC/USDT-PERP", "ETH/USDT-PERP"]


def test_symbol_arg_stops_greedy_consumption_at_next_flag() -> None:
    """argparse's nargs='+' must stop consuming when the next flag starts."""
    args = _build_parser().parse_args(
        ["--symbol", "IP/USDT-PERP", "BTC/USDT-PERP", "--hostname", "TA"]
    )
    assert args.symbol == ["IP/USDT-PERP", "BTC/USDT-PERP"]
    assert args.hostname == "TA"


def test_symbol_arg_with_no_values_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--symbol` on its own (no values) must be rejected by argparse."""
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["--symbol", "--all"])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "expected at least one argument" in err or "--symbol" in err


def test_symbol_combines_with_hostname() -> None:
    args = _build_parser().parse_args(
        ["--symbol", "IP/USDT-PERP", "--hostname", "TA-TKY"]
    )
    assert args.symbol == ["IP/USDT-PERP"]
    assert args.hostname == "TA-TKY"


def test_symbol_combines_with_exchange_name() -> None:
    args = _build_parser().parse_args(
        ["--symbol", "IP/USDT-PERP", "--exchange-name", "BINANCE"]
    )
    assert args.symbol == ["IP/USDT-PERP"]
    assert args.exchange_name == "BINANCE"


def test_symbol_combines_with_all() -> None:
    args = _build_parser().parse_args(["--symbol", "IP/USDT-PERP", "--all"])
    assert args.symbol == ["IP/USDT-PERP"]
    assert args.all is True


# ---------------------------------------------------------------------------
# Validation gates in main():
#   1) at least one of --all / --hostname / --exchange-name / --symbol required
#   2) --all is still mutually exclusive with --hostname / --exchange-name
#
# We point --creds-file at a non-existent path; that means a "good" argv
# (passes the argparse gate) will progress past validation and return
# EXIT_OPERATIONAL_FAILURE on creds load. A "bad" argv will SystemExit(2)
# from argparse before that.
# ---------------------------------------------------------------------------


_BOGUS_CREDS = ["--creds-file", "/nonexistent/path/.DBCreds.yaml"]


def test_no_filter_at_all_is_rejected_by_argparse(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(_BOGUS_CREDS)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "specify --hostname, --exchange-name, --symbol, or --all" in err


def test_symbol_alone_passes_argparse_gate(tmp_path: Path) -> None:
    """`--symbol X` alone must NOT be rejected by the validation gate; it should
    proceed to creds loading and return EXIT_OPERATIONAL_FAILURE there."""
    rc = main(["--symbol", "IP/USDT-PERP"] + _BOGUS_CREDS)
    assert rc == EXIT_OPERATIONAL_FAILURE


def test_symbol_with_all_passes(tmp_path: Path) -> None:
    rc = main(["--symbol", "IP/USDT-PERP", "--all"] + _BOGUS_CREDS)
    assert rc == EXIT_OPERATIONAL_FAILURE


def test_symbol_with_hostname_passes(tmp_path: Path) -> None:
    rc = main(["--symbol", "IP/USDT-PERP", "--hostname", "TA"] + _BOGUS_CREDS)
    assert rc == EXIT_OPERATIONAL_FAILURE


def test_all_plus_hostname_still_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    """The pre-existing --all vs --hostname/--exchange-name exclusivity must
    survive the --symbol addition."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--all", "--hostname", "X"] + _BOGUS_CREDS)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--all cannot be combined with --hostname or --exchange-name" in err


def test_all_plus_exchange_name_still_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--all", "--exchange-name", "BINANCE"] + _BOGUS_CREDS)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--all cannot be combined with --hostname or --exchange-name" in err


# ---------------------------------------------------------------------------
# --source flag: producer-table selection
# ---------------------------------------------------------------------------


def test_source_defaults_to_both() -> None:
    """Default: no --source flag \u2192 scan both fh_config and repeater_feeds."""
    args = _build_parser().parse_args(["--all"])
    assert args.source == "both"


def test_source_accepts_fh_rp_both() -> None:
    for value in ("fh", "rp", "both"):
        args = _build_parser().parse_args(["--all", "--source", value])
        assert args.source == value


def test_source_rejects_unknown_choice(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["--all", "--source", "repeater"])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--source" in err


def test_source_combines_with_symbol_and_all() -> None:
    args = _build_parser().parse_args(
        ["--symbol", "IP/USDT-PERP", "--all", "--source", "rp"]
    )
    assert args.source == "rp"
    assert args.all is True
    assert args.symbol == ["IP/USDT-PERP"]
