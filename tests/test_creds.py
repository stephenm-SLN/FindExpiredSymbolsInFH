from __future__ import annotations

import logging
from pathlib import Path

import pytest

from fh_symbol_check.creds import CredsError, DBCreds, load_creds


_SECRET = "p@ssw0rd-CANARY-9f8a2c"


def _write_creds(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".DBCreds.yaml"
    p.write_text(body)
    return p


def test_load_creds_happy_path(tmp_path: Path) -> None:
    p = _write_creds(
        tmp_path,
        f"""
crypto_db:
  host: 10.50.12.8
  database: crypto_db
  user: alice
  password: {_SECRET!r}
""",
    )
    creds = load_creds(p, section="crypto_db")
    assert creds.host == "10.50.12.8"
    assert creds.database == "crypto_db"
    assert creds.user == "alice"
    assert creds.password == _SECRET


def test_repr_masks_password(tmp_path: Path) -> None:
    p = _write_creds(
        tmp_path,
        f"""
crypto_db:
  host: h
  database: d
  user: u
  password: {_SECRET!r}
""",
    )
    creds = load_creds(p)
    r = repr(creds)
    assert "***" in r
    assert _SECRET not in r


def test_password_not_in_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = _write_creds(
        tmp_path,
        f"""
crypto_db:
  host: h
  database: d
  user: u
  password: {_SECRET!r}
""",
    )
    with caplog.at_level(logging.DEBUG):
        creds = load_creds(p)
        logging.getLogger("test").debug("creds=%r", creds)
    assert _SECRET not in caplog.text


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CredsError, match="not found"):
        load_creds(tmp_path / "does-not-exist.yaml")


def test_missing_section(tmp_path: Path) -> None:
    p = _write_creds(tmp_path, "other_section:\n  host: h\n  database: d\n  user: u\n  password: s\n")
    with pytest.raises(CredsError, match="section 'crypto_db' not found"):
        load_creds(p)


def test_missing_required_key(tmp_path: Path) -> None:
    p = _write_creds(tmp_path, "crypto_db:\n  host: h\n  database: d\n  user: u\n")
    with pytest.raises(CredsError, match="missing required keys"):
        load_creds(p)


def test_malformed_yaml(tmp_path: Path) -> None:
    p = _write_creds(tmp_path, "crypto_db: : : :\n  not valid\n")
    with pytest.raises(CredsError, match="not valid YAML"):
        load_creds(p)


def test_non_mapping_top_level(tmp_path: Path) -> None:
    p = _write_creds(tmp_path, "- a\n- b\n")
    with pytest.raises(CredsError, match="must be a YAML mapping"):
        load_creds(p)


def test_dbcreds_frozen() -> None:
    c = DBCreds(host="h", user="u", password="p", database="d")
    with pytest.raises(Exception):
        c.host = "other"  # type: ignore[misc]
