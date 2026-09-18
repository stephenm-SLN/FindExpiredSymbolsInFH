"""Unit tests for the Vertex custom-venue checker.

The live archive API is gone (Vertex shut down July 2025). Runtime
fetchers raise ``VenueGone``. The ``/v2/symbols`` parser is kept for
the historical fixture and a possible successor API.
"""

from __future__ import annotations

import pytest

from fh_symbol_check.custom_venues import CUSTOM_VENUES, VenueGone
from fh_symbol_check.custom_venues.vertex import (
    _EDGES,
    VertexFetchError,
    _fetch,
    _parse_symbols,
    fetch_avavertex,
    fetch_beravertex,
    fetch_mntvertex,
    fetch_sovertex,
    fetch_vertex,
)


def _fixture() -> dict:
    """A shrunk-down sample of a ``/v2/symbols`` response."""
    return {
        "BTC-PERP": {
            "type": "perp",
            "product_id": 2,
            "symbol": "BTC-PERP",
            "trading_status": "live",
        },
        "ETH-PERP": {
            "type": "perp",
            "product_id": 4,
            "symbol": "ETH-PERP",
            "trading_status": "live",
        },
        "OLD-PERP": {
            "type": "perp",
            "product_id": 999,
            "symbol": "OLD-PERP",
            "trading_status": "not_tradable",
        },
        "RO-PERP": {
            "type": "perp",
            "product_id": 998,
            "symbol": "RO-PERP",
            "trading_status": "reduce_only",
        },
    }


# ---------------------------------------------------------------------------
# _parse_symbols
# ---------------------------------------------------------------------------


def test_parse_live_symbols_are_active() -> None:
    out = _parse_symbols("VERTEX", _fixture())
    assert out["BTC-PERP"] is True
    assert out["ETH-PERP"] is True


def test_parse_non_live_symbols_are_inactive() -> None:
    out = _parse_symbols("VERTEX", _fixture())
    assert out["OLD-PERP"] is False
    assert out["RO-PERP"] is False


def test_parse_uppercases_keys() -> None:
    out = _parse_symbols("VERTEX", {"btc-perp": {"trading_status": "live"}})
    assert "BTC-PERP" in out
    assert out["BTC-PERP"] is True


def test_parse_ignores_garbage_entries() -> None:
    out = _parse_symbols(
        "VERTEX",
        {
            "BTC-PERP": {"trading_status": "live"},
            "junk": "not a dict",
            42: {"trading_status": "live"},  # non-str key
        },
    )
    assert set(out) == {"BTC-PERP"}


def test_parse_top_level_must_be_dict() -> None:
    with pytest.raises(VertexFetchError, match="unexpected VERTEX response shape"):
        _parse_symbols("VERTEX", [{"BTC-PERP": {"trading_status": "live"}}])


def test_parse_missing_trading_status_is_inactive() -> None:
    """A Vertex entry without ``trading_status`` should be treated as
    inactive — we can't prove it's live, so err on the safe side."""
    out = _parse_symbols("VERTEX", {"NEW-PERP": {"type": "perp", "product_id": 1000}})
    assert out["NEW-PERP"] is False


def test_parse_error_message_names_the_edge() -> None:
    """When Berachain returns a JSON list instead of a dict, the error
    should say ``BERAVERTEX`` — not ``VERTEX`` — so operators know which
    edge is broken."""
    with pytest.raises(VertexFetchError, match="unexpected BERAVERTEX response shape"):
        _parse_symbols("BERAVERTEX", [])


# ---------------------------------------------------------------------------
# _EDGES + registry sanity
# ---------------------------------------------------------------------------


def test_all_five_edges_have_distinct_archive_hostnames() -> None:
    """A copy-paste bug in ``_EDGES`` would silently route two FH
    exchange_names to the same edge. Lock it down: five distinct hosts."""
    urls = list(_EDGES.values())
    hosts = [u.split("/")[2] for u in urls]
    assert len(hosts) == 5
    assert len(set(hosts)) == 5, f"duplicate edge host in _EDGES: {hosts}"


def test_edges_map_to_expected_hostnames() -> None:
    """Guard against typos in the edge subdomains. If Vertex ever
    renames one of these we want the diff to show up here explicitly."""
    assert _EDGES["VERTEX"].endswith("archive.prod.vertexprotocol.com")
    assert _EDGES["AVAVERTEX"].endswith("archive.avax-prod.vertexprotocol.com")
    assert _EDGES["BERAVERTEX"].endswith("archive.bera-prod.vertexprotocol.com")
    assert _EDGES["MNTVERTEX"].endswith("archive.mantle-prod.vertexprotocol.com")
    assert _EDGES["SOVERTEX"].endswith("archive.sonic-prod.vertexprotocol.com")


def test_all_five_wrappers_registered_in_custom_venues() -> None:
    for name, fn in (
        ("VERTEX", fetch_vertex),
        ("AVAVERTEX", fetch_avavertex),
        ("BERAVERTEX", fetch_beravertex),
        ("MNTVERTEX", fetch_mntvertex),
        ("SOVERTEX", fetch_sovertex),
    ):
        assert CUSTOM_VENUES[name] is fn


# ---------------------------------------------------------------------------
# _fetch — shutdown short-circuit (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn, edge, host",
    [
        (fetch_vertex, "VERTEX", "archive.prod.vertexprotocol.com"),
        (fetch_avavertex, "AVAVERTEX", "archive.avax-prod.vertexprotocol.com"),
        (fetch_beravertex, "BERAVERTEX", "archive.bera-prod.vertexprotocol.com"),
        (fetch_mntvertex, "MNTVERTEX", "archive.mantle-prod.vertexprotocol.com"),
        (fetch_sovertex, "SOVERTEX", "archive.sonic-prod.vertexprotocol.com"),
    ],
)
def test_fetch_raises_venue_gone_naming_edge_and_host(fn, edge: str, host: str) -> None:
    with pytest.raises(VenueGone, match=edge) as exc_info:
        fn()
    msg = str(exc_info.value)
    assert host in msg
    assert "shut down" in msg
    assert "Ink Foundation" in msg


def test_fetch_does_not_open_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regression that starts hitting the dead archive hosts would
    resurrect the TLS-EOF ERROR rows."""

    def boom(*_a, **_k):
        raise AssertionError("urlopen must not be called after Vertex shutdown")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(VenueGone):
        fetch_vertex()


def test_private_fetch_rejects_unknown_edge_key() -> None:
    """A copy-paste bug that calls ``_fetch("VRTX")`` should KeyError
    loudly rather than looking like a shutdown."""
    with pytest.raises(KeyError):
        _fetch("VRTX")
