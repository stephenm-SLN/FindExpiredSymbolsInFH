"""Tests for the Pydantic models at the JSON boundary.

Validation error wording must match the CLI's argparse strings so both
fronts speak the same dialect.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fh_symbol_check.api.models import ScanRequest


# ---------------------------------------------------------------------------
# Filter combination validation
# ---------------------------------------------------------------------------


def test_scan_request_empty_body_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ScanRequest()
    msg = "\n".join(err["msg"] for err in exc.value.errors())
    assert "specify --hostname, --exchange-name, --symbol, or --all" in msg


def test_scan_request_all_with_hostname_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ScanRequest(all=True, hostname="TA-TKY-A-41")
    msg = "\n".join(err["msg"] for err in exc.value.errors())
    assert "--all cannot be combined with --hostname or --exchange-name" in msg


def test_scan_request_all_with_exchange_name_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ScanRequest(all=True, exchange_name="HUOBI")
    msg = "\n".join(err["msg"] for err in exc.value.errors())
    assert "--all cannot be combined with --hostname or --exchange-name" in msg


@pytest.mark.parametrize(
    "kwargs",
    [
        {"all": True},
        {"hostname": "TA-TKY-A-41"},
        {"exchange_name": "HUOBI"},
        {"symbol": ["BTC/USDT-PERP"]},
        {"symbol": ["BTC/USDT-PERP", "ETH/USDT-PERP"]},
        {"hostname": "x", "exchange_name": "y"},
    ],
)
def test_scan_request_accepts_valid_filter_sets(kwargs: dict) -> None:
    ScanRequest(**kwargs)  # must not raise


# ---------------------------------------------------------------------------
# to_filters — round-trip to the pipeline dataclass
# ---------------------------------------------------------------------------


def test_to_filters_preserves_field_semantics() -> None:
    req = ScanRequest(
        hostname="TA-TKY-A-41",
        exchange_name="HUOBI",
        symbol=["BTC/USDT-PERP"],
        source="rp",
        concurrency=8,
    )
    filters = req.to_filters()
    assert filters.hostname == "TA-TKY-A-41"
    assert filters.exchange_name == "HUOBI"
    assert filters.all_producers is False
    assert filters.symbols == ("BTC/USDT-PERP",)
    assert filters.source == "rp"
    assert filters.concurrency == 8


def test_to_filters_maps_all_to_all_producers() -> None:
    filters = ScanRequest(all=True).to_filters()
    assert filters.all_producers is True
    assert filters.symbols == ()


def test_to_filters_symbol_none_becomes_empty_tuple() -> None:
    # Client didn't send `symbol` at all — Pydantic default is None,
    # which we normalise to () so pipeline can treat "no filter" uniformly.
    filters = ScanRequest(hostname="x", symbol=None).to_filters()
    assert filters.symbols == ()


# ---------------------------------------------------------------------------
# Extras policy
# ---------------------------------------------------------------------------


def test_scan_request_rejects_unknown_fields() -> None:
    # extra="forbid" — API surface is closed, catches typos early.
    with pytest.raises(ValidationError):
        ScanRequest(all=True, misspelt="oops")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# concurrency bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, 33])
def test_scan_request_concurrency_out_of_range(bad: int) -> None:
    with pytest.raises(ValidationError):
        ScanRequest(all=True, concurrency=bad)


@pytest.mark.parametrize("good", [1, 4, 32])
def test_scan_request_concurrency_in_range(good: int) -> None:
    ScanRequest(all=True, concurrency=good)
