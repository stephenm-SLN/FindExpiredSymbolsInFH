"""Fetch producer rows (feed handlers + repeaters) via parameterised SQL.

Both tables (``fh_config`` and ``repeater_feeds``) go through the same downstream
pipeline (exchange mapping, symbol translation, ccxt / custom-venue classification)
because they describe the same underlying venue subscriptions from different
producer layers. Rows are returned as ``FeedHandlerRow`` with ``source`` set to
distinguish them for the reporter.
"""

from __future__ import annotations

import logging

from mysql_select_query import MySQLQueryClient

from .creds import DBCreds
from .models import FeedHandlerRow

logger = logging.getLogger(__name__)


class DBError(Exception):
    """Raised on any failure to query the Feed Handler DB."""


_SELECT_FH = (
    "SELECT service_id, fh_name, hostname, exchange_name, cover_names "
    "FROM crypto_db.fh_config"
)

_SELECT_REPEATERS = (
    "SELECT hostname, app_name, exchange_name, instruments "
    "FROM crypto_db.repeater_feeds"
)


def fetch_feed_handlers(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]:
    """Run the ``fh_config`` SELECT with optional filters; return parsed rows.

    Filters:
        hostname_pattern: substring matched via ``hostname LIKE %<value>%``
        exchange_name:    case-insensitive exact match on ``exchange_name``

    If both are ``None`` the query scans every row in ``fh_config``.
    """
    sql, params = _compose_sql(_SELECT_FH, hostname_pattern, exchange_name)
    logger.debug("running fh_config query with %d filter(s)", len(params))

    raw_rows = _run_query(creds, sql, params, "fh_config")

    rows: list[FeedHandlerRow] = []
    for raw in raw_rows:
        service_id, fh_name, hostname, exchange_name_val, cover_names = raw
        symbols = _parse_csv_symbols(cover_names)
        if not symbols:
            logger.debug(
                "fh_config row service_id=%s has empty cover_names; including with 0 symbols",
                service_id,
            )
        rows.append(
            FeedHandlerRow(
                service_id=int(service_id),
                fh_name=str(fh_name),
                hostname=str(hostname),
                exchange_name=str(exchange_name_val),
                symbols=symbols,
                source="fh",
            )
        )
    logger.info("fetched %d feed-handler row(s) from fh_config", len(rows))
    return rows


def fetch_repeaters(
    creds: DBCreds,
    hostname_pattern: str | None = None,
    *,
    exchange_name: str | None = None,
) -> list[FeedHandlerRow]:
    """Run the ``repeater_feeds`` SELECT with optional filters; return parsed rows.

    Same filter contract as :func:`fetch_feed_handlers`. The DB columns differ
    (``app_name`` vs ``fh_name``, ``instruments`` vs ``cover_names``, no
    ``service_id``) but the parsed rows land in the same ``FeedHandlerRow`` type
    with ``source="repeater"`` and ``service_id=None`` so downstream stages don't
    need to branch.
    """
    sql, params = _compose_sql(_SELECT_REPEATERS, hostname_pattern, exchange_name)
    logger.debug("running repeater_feeds query with %d filter(s)", len(params))

    raw_rows = _run_query(creds, sql, params, "repeater_feeds")

    rows: list[FeedHandlerRow] = []
    for raw in raw_rows:
        hostname, app_name, exchange_name_val, instruments = raw
        symbols = _parse_csv_symbols(instruments)
        if not symbols:
            logger.debug(
                "repeater_feeds row app_name=%s hostname=%s has empty instruments; "
                "including with 0 symbols",
                app_name,
                hostname,
            )
        rows.append(
            FeedHandlerRow(
                service_id=None,
                fh_name=str(app_name),  # app_name stored in the fh_name slot
                hostname=str(hostname),
                exchange_name=str(exchange_name_val),
                symbols=symbols,
                source="repeater",
            )
        )
    logger.info("fetched %d repeater row(s) from repeater_feeds", len(rows))
    return rows


def _compose_sql(
    base: str,
    hostname_pattern: str | None,
    exchange_name: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Append WHERE clauses + bound params. Both filters None -> full-table scan."""
    conditions: list[str] = []
    params: list[str] = []
    if hostname_pattern:
        conditions.append("hostname LIKE %s")
        params.append(f"%{hostname_pattern}%")
    if exchange_name:
        conditions.append("UPPER(exchange_name) = UPPER(%s)")
        params.append(exchange_name)

    sql = base
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return sql, tuple(params)


def _run_query(
    creds: DBCreds,
    sql: str,
    params: tuple[str, ...],
    table_label: str,
) -> list[tuple]:
    """Open a client, run the query, wrap any pymysql exception in DBError."""
    client = MySQLQueryClient(
        host=creds.host,
        user=creds.user,
        password=creds.password,
        database=creds.database,
    )
    try:
        return client.fetch_query_results(sql, params=params if params else None)
    except Exception as e:  # pymysql raises a variety of types
        raise DBError(f"failed to query {table_label}: {e}") from e


def _parse_csv_symbols(value: object) -> tuple[str, ...]:
    """Split the comma-separated symbol column into a tuple of symbols.

    Works for both ``cover_names`` (fh_config) and ``instruments`` (repeater_feeds)
    — both are documented as comma-separated symbol lists. Strips whitespace and
    drops empty entries.
    """
    if not value:
        return ()
    parts = [p.strip() for p in str(value).split(",")]
    return tuple(p for p in parts if p)
