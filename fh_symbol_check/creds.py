"""Load DB credentials from a YAML file structured as a map of named sections.

Expected YAML layout:

    crypto_db:
      host: 10.50.12.8
      database: crypto_db
      user: <user>
      password: <secret>

Credentials are never logged. DBCreds.__repr__ masks the password.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class CredsError(Exception):
    """Raised for any failure loading or validating the creds file."""


@dataclass(frozen=True)
class DBCreds:
    host: str
    user: str
    password: str
    database: str

    def __repr__(self) -> str:
        return (
            f"DBCreds(host={self.host!r}, user={self.user!r}, "
            f"password='***', database={self.database!r})"
        )


_REQUIRED_KEYS = ("host", "user", "password", "database")


def load_creds(path: Path, section: str = "crypto_db") -> DBCreds:
    """Load DB credentials from ``path`` under top-level key ``section``."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError as e:
        raise CredsError(f"creds file not found: {path}") from e
    except OSError as e:
        raise CredsError(f"failed to read creds file: {path}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise CredsError(f"creds file is not valid YAML: {path}") from e

    if not isinstance(data, dict):
        raise CredsError(f"creds file must be a YAML mapping at the top level: {path}")

    if section not in data:
        raise CredsError(f"section {section!r} not found in {path}")

    entry = data[section]
    if not isinstance(entry, dict):
        raise CredsError(f"section {section!r} must be a mapping in {path}")

    missing = [k for k in _REQUIRED_KEYS if k not in entry]
    if missing:
        raise CredsError(
            f"section {section!r} is missing required keys: {', '.join(missing)}"
        )

    return DBCreds(
        host=str(entry["host"]),
        user=str(entry["user"]),
        password=str(entry["password"]),
        database=str(entry["database"]),
    )
