"""Vertex Protocol custom symbol checker (5 edge deployments) — shut down.

Vertex was a decentralised perpetuals + spot exchange. It is **not**
supported by ``ccxt``. In July 2025 the team joined the Ink Foundation,
shut down all EVM edge deployments (Arbitrum / Avalanche / Berachain /
Mantle / Sonic / …), and deprecated the archive API. Phase 4 completed
mid-July 2025; back-end services were fully gone by mid-August 2025.

The former hosts (``archive.*.vertexprotocol.com``) no longer exist.
Leftover DNS still resolves and TCP can connect, then the TLS handshake
dies with ``UNEXPECTED_EOF_WHILE_READING`` — the same signature as a
corporate SNI block, which is what we originally diagnosed. Hitting
those IPs is wasted time and a misleading ERROR.

Each FH ``exchange_name`` still maps to one former edge so the report
names it:

    ``VERTEX``      -> Arbitrum     (``archive.prod.vertexprotocol.com``)
    ``AVAVERTEX``   -> Avalanche    (``archive.avax-prod.vertexprotocol.com``)
    ``BERAVERTEX``  -> Berachain    (``archive.bera-prod.vertexprotocol.com``)
    ``MNTVERTEX``   -> Mantle       (``archive.mantle-prod.vertexprotocol.com``)
    ``SOVERTEX``    -> Sonic        (``archive.sonic-prod.vertexprotocol.com``)

The checker raises :class:`VenueGone` (no network). The validator marks
every configured symbol ``DELISTED`` with the shutdown reason in
``detail``.

``_parse_symbols`` is kept so the old ``/v2/symbols`` fixture tests still
cover the parser if a successor API ever reappears.

Nado is a Vertex fork and is **still live** — do not apply this
short-circuit there.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Former archive-service base URLs, kept so the shutdown message can
# name the host that used to serve each edge. Not fetched.
_EDGES: dict[str, str] = {
    "VERTEX": "https://archive.prod.vertexprotocol.com",
    "AVAVERTEX": "https://archive.avax-prod.vertexprotocol.com",
    "BERAVERTEX": "https://archive.bera-prod.vertexprotocol.com",
    "MNTVERTEX": "https://archive.mantle-prod.vertexprotocol.com",
    "SOVERTEX": "https://archive.sonic-prod.vertexprotocol.com",
}


class VertexFetchError(Exception):
    """Raised on a malformed historical ``/v2/symbols`` payload (parser only)."""


def _fetch(edge: str) -> dict[str, bool]:
    """Record that this Vertex edge is gone. Does not touch the network.

    ``edge`` must be a key of :data:`_EDGES` (unknown keys still
    ``KeyError`` so a typo doesn't silently look like a shutdown).
    """
    if edge not in _EDGES:
        raise KeyError(edge)
    # Lazy import: VenueGone lives in the package __init__, which imports
    # this module. A top-level import would cycle.
    from . import VenueGone

    host = _EDGES[edge]
    msg = (
        f"{edge}: Vertex Protocol shut down July 2025 "
        f"(merged with Ink Foundation); archive host {host} no longer exists"
    )
    logger.warning("%s", msg)
    raise VenueGone(msg)


def _parse_symbols(edge: str, payload: object) -> dict[str, bool]:
    """Parse a historical ``/v2/symbols`` payload into ``{SYMBOL_UPPER: is_live}``.

    Unused at runtime after the shutdown. Kept so tests (and a possible
    successor API) still have a parser.
    """
    if not isinstance(payload, dict):
        raise VertexFetchError(
            f"unexpected {edge} response shape: {type(payload).__name__}"
        )
    result: dict[str, bool] = {}
    for symbol, info in payload.items():
        if not isinstance(symbol, str) or not isinstance(info, dict):
            continue
        result[symbol.upper()] = info.get("trading_status") == "live"
    return result


def fetch_vertex() -> dict[str, bool]:
    return _fetch("VERTEX")


def fetch_avavertex() -> dict[str, bool]:
    return _fetch("AVAVERTEX")


def fetch_beravertex() -> dict[str, bool]:
    return _fetch("BERAVERTEX")


def fetch_mntvertex() -> dict[str, bool]:
    return _fetch("MNTVERTEX")


def fetch_sovertex() -> dict[str, bool]:
    return _fetch("SOVERTEX")
