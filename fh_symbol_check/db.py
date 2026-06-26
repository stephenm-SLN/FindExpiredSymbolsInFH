"""Fetch feed-handler rows from crypto_db.fh_config via parameterised SQL."""

from __future__ import annotations

import logging

from mysql_select_query import MySQLQueryClient

from .creds import DBCreds
from .models import FeedHandlerRow

logger = logging.getLogger(__name__)


class DBError(Exception):
    """Raised on any failure to query the Feed Handler DB."""


_SELECT_BASE = (
    "SELECT service_id, fh_name, hostname, exchange_name, cover_names "
    "FROM crypto_db.fh_config"
)


def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]:
    """Run the SELECT with optional filters; return parsed rows.

    Filters:
        hostname_pattern: substring matched via ``hostname LIKE %<value>%``
        exchange_name:    case-insensitive exact match on ``exchange_name``

    If both are ``None`` the query scans every row in ``fh_config``.
    """
    client = MySQLQueryClient(
        host=creds.host,
        user=creds.user,
        password=creds.password,
        database=creds.database,
    )

    conditions: list[str] = []
    params: list[str] = []
    if hostname_pattern:
        conditions.append("hostname LIKE %s")
        params.append(f"%{hostname_pattern}%")
    if exchange_name:
        conditions.append("UPPER(exchange_name) = UPPER(%s)")
        params.append(exchange_name)

    sql = _SELECT_BASE
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    logger.debug("running fh_config query with %d filter(s)", len(conditions))

    try:
        raw_rows = client.fetch_query_results(
            sql, params=tuple(params) if params else None
        )
    except Exception as e:  # pymysql raises a variety of types
        raise DBError(f"failed to query fh_config: {e}") from e

    rows: list[FeedHandlerRow] = []
    for raw in raw_rows:
        service_id, fh_name, hostname, exchange_name, cover_names = raw
        symbols = _parse_cover_names(cover_names)
        if not symbols:
            logger.debug(
                "row service_id=%s has empty cover_names; including with 0 symbols",
                service_id,
            )
        rows.append(
            FeedHandlerRow(
                service_id=int(service_id),
                fh_name=str(fh_name),
                hostname=str(hostname),
                exchange_name=str(exchange_name),
                symbols=symbols,
            )
        )
    logger.info("fetched %d feed-handler row(s) from fh_config", len(rows))
    return rows


def _parse_cover_names(value: object) -> tuple[str, ...]:
    """Split the comma-separated cover_names column into a tuple of symbols.

    Strips whitespace and drops empty entries.
    """
    if not value:
        return ()
    parts = [p.strip() for p in str(value).split(",")]
    return tuple(p for p in parts if p)
