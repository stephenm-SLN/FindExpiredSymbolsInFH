"""Load the DB exchange_name -> ccxt exchange id mapping from YAML.

Lookup is case-insensitive on the DB key so the loader uppercases all keys at
load time.
"""

from __future__ import annotations

from pathlib import Path

import yaml


class ExchangeMapError(Exception):
    """Raised for any failure loading or validating the exchange map."""


def load_exchange_map(path: Path) -> dict[str, str]:
    """Load the mapping. Keys are uppercased; values are passed through as-is."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError as e:
        raise ExchangeMapError(f"exchange map file not found: {path}") from e
    except OSError as e:
        raise ExchangeMapError(f"failed to read exchange map: {path}") from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ExchangeMapError(f"exchange map is not valid YAML: {path}") from e

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ExchangeMapError(f"exchange map must be a YAML mapping: {path}")

    mapping: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ExchangeMapError(
                f"exchange map entries must be string -> string: got {k!r} -> {v!r}"
            )
        mapping[k.upper()] = v
    return mapping


def resolve(mapping: dict[str, str], exchange_name: str) -> str | None:
    """Case-insensitive lookup. Returns the ccxt id or None if unknown."""
    return mapping.get(exchange_name.upper())
