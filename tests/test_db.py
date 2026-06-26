from __future__ import annotations

import pytest

import fh_symbol_check.db as db_module
from fh_symbol_check.creds import DBCreds
from fh_symbol_check.db import DBError, fetch_feed_handlers


class _FakeClient:
    """Records construction args + query/params; returns canned rows."""

    last_init: dict | None = None
    last_query: str | None = None
    last_params: object = None
    rows_to_return: list[list] = []
    raise_on_query: Exception | None = None

    def __init__(self, host: str, user: str, password: str, database: str) -> None:
        type(self).last_init = {
            "host": host,
            "user": user,
            "password": password,
            "database": database,
        }

    def fetch_query_results(self, query, params=None, return_header=False):
        type(self).last_query = query
        type(self).last_params = params
        if type(self).raise_on_query is not None:
            raise type(self).raise_on_query
        return list(type(self).rows_to_return)


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    _FakeClient.last_init = None
    _FakeClient.last_query = None
    _FakeClient.last_params = None
    _FakeClient.rows_to_return = []
    _FakeClient.raise_on_query = None


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    monkeypatch.setattr(db_module, "MySQLQueryClient", _FakeClient)
    return _FakeClient


def _creds() -> DBCreds:
    return DBCreds(host="h", user="u", password="s", database="crypto_db")


def test_passes_creds_to_client(patched_client: type[_FakeClient]) -> None:
    patched_client.rows_to_return = []
    fetch_feed_handlers(_creds(), "TA-TKY-A-41")
    assert patched_client.last_init == {
        "host": "h",
        "user": "u",
        "password": "s",
        "database": "crypto_db",
    }


def test_uses_parameterised_sql_and_bound_pattern(
    patched_client: type[_FakeClient],
) -> None:
    fetch_feed_handlers(_creds(), "TA-TKY-A-41")

    assert patched_client.last_query is not None
    assert "%s" in patched_client.last_query
    assert "hostname LIKE" in patched_client.last_query
    assert "TA-TKY-A-41" not in patched_client.last_query
    assert patched_client.last_params == ("%TA-TKY-A-41%",)


def test_filter_by_exchange_name_only(patched_client: type[_FakeClient]) -> None:
    fetch_feed_handlers(_creds(), exchange_name="HUOBI")

    assert patched_client.last_query is not None
    sql = patched_client.last_query
    assert "UPPER(exchange_name) = UPPER(%s)" in sql
    assert "hostname LIKE" not in sql
    assert "HUOBI" not in sql  # bound, not formatted
    assert patched_client.last_params == ("HUOBI",)


def test_filter_by_hostname_and_exchange_name(
    patched_client: type[_FakeClient],
) -> None:
    fetch_feed_handlers(_creds(), "TA-TKY-A-41", exchange_name="WOODEX")

    sql = patched_client.last_query
    assert sql is not None
    assert "hostname LIKE %s" in sql
    assert "UPPER(exchange_name) = UPPER(%s)" in sql
    assert " AND " in sql
    assert patched_client.last_params == ("%TA-TKY-A-41%", "WOODEX")


def test_no_filters_scans_all(patched_client: type[_FakeClient]) -> None:
    fetch_feed_handlers(_creds())

    sql = patched_client.last_query
    assert sql is not None
    assert "WHERE" not in sql
    assert patched_client.last_params is None


def test_parses_cover_names_into_symbols(patched_client: type[_FakeClient]) -> None:
    patched_client.rows_to_return = [
        [4002, "fh_huobi_4002", "TA-TKY-A-41_LOCAL", "HUOBI", "BTC/USDT, ETH/USDT,SOL/USDT"]
    ]
    rows = fetch_feed_handlers(_creds(), "TA-TKY-A-41")
    assert len(rows) == 1
    r = rows[0]
    assert r.service_id == 4002
    assert r.fh_name == "fh_huobi_4002"
    assert r.hostname == "TA-TKY-A-41_LOCAL"
    assert r.exchange_name == "HUOBI"
    assert r.symbols == ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def test_empty_cover_names_yields_empty_symbols(
    patched_client: type[_FakeClient],
) -> None:
    patched_client.rows_to_return = [
        [1, "fh_x", "TA-TKY-A-41", "HUOBI", ""],
        [2, "fh_y", "TA-TKY-A-41", "HUOBI", None],
    ]
    rows = fetch_feed_handlers(_creds(), "TA-TKY-A-41")
    assert rows[0].symbols == ()
    assert rows[1].symbols == ()


def test_db_errors_wrapped(patched_client: type[_FakeClient]) -> None:
    patched_client.raise_on_query = RuntimeError("connection refused")
    with pytest.raises(DBError, match="failed to query fh_config"):
        fetch_feed_handlers(_creds(), "TA-TKY-A-41")
