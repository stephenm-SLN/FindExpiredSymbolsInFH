from __future__ import annotations

from pathlib import Path

import pytest

from fh_symbol_check.exchange_map import ExchangeMapError, load_exchange_map, resolve


def test_load_uppercases_keys(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("huobi: htx\nWOODEX: woo\n")
    m = load_exchange_map(p)
    assert m == {"HUOBI": "htx", "WOODEX": "woo"}


def test_resolve_case_insensitive() -> None:
    m = {"HUOBI": "htx"}
    assert resolve(m, "HUOBI") == "htx"
    assert resolve(m, "huobi") == "htx"
    assert resolve(m, "Huobi") == "htx"


def test_resolve_unknown_returns_none() -> None:
    assert resolve({"HUOBI": "htx"}, "BINANCE") is None


def test_empty_yaml(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load_exchange_map(p) == {}


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ExchangeMapError, match="not found"):
        load_exchange_map(tmp_path / "nope.yaml")


def test_non_mapping_yaml(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n")
    with pytest.raises(ExchangeMapError, match="must be a YAML mapping"):
        load_exchange_map(p)


def test_non_string_values_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("HUOBI: 123\n")
    with pytest.raises(ExchangeMapError, match="string -> string"):
        load_exchange_map(p)
