"""Packaging smoke checks.

These do NOT rebuild the wheel (that would be slow and would need setuptools
in the test environment). They verify the *runtime* contract the wheel
depends on:

  1. `fh_symbol_check.cli.run` is importable (this is the console-script
     entry point declared in pyproject.toml → project.scripts).
  2. `--help` succeeds and mentions every filter flag the operator relies on.
  3. The default `--exchange-map` value resolves to the packaged data file,
     the file exists on disk, and it is a valid YAML mapping. This is what
     makes the console script "just work" once installed on the server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fh_symbol_check import cli as cli_module
from fh_symbol_check.cli import DEFAULT_EXCHANGE_MAP, EXIT_OK, run


def test_run_is_the_console_script_entry_point() -> None:
    """`pyproject.toml` declares `find-expired-symbols = fh_symbol_check.cli:run`.
    This test locks that name in so a rename doesn't silently break installs."""
    assert callable(run)
    assert cli_module.run is run


def test_help_flag_succeeds_and_covers_every_filter_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--help` must exit 0 and document every filter flag; an operator
    scheduling this externally will use `--help` as their first source of
    truth for the CLI surface."""
    with pytest.raises(SystemExit) as exc_info:
        run.__wrapped__() if hasattr(run, "__wrapped__") else cli_module.main(["--help"])
    # argparse exits 0 on --help
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--hostname", "--exchange-name", "--symbol", "--all"):
        assert flag in out, f"--help output missing {flag!r}"
    for flag in ("--output", "--output-file", "--errors-only", "--exchange-grouping"):
        assert flag in out, f"--help output missing {flag!r}"


def test_default_exchange_map_resolves_to_bundled_file() -> None:
    """`DEFAULT_EXCHANGE_MAP` is the path pip-installed operators get for
    `--exchange-map` when they don't override it. It must:

      1. Point at a real file on disk (both in source checkouts and in
         installed wheels, since setuptools unzips by default).
      2. Live inside the installed `fh_symbol_check` package, so a
         `pip install --upgrade` swaps it out automatically.
    """
    assert isinstance(DEFAULT_EXCHANGE_MAP, Path)
    assert DEFAULT_EXCHANGE_MAP.exists(), (
        f"packaged exchange_mapping.yaml not found at {DEFAULT_EXCHANGE_MAP}"
    )
    # Must live inside the package tree (as opposed to some working-dir path)
    parts = DEFAULT_EXCHANGE_MAP.parts
    assert "fh_symbol_check" in parts, (
        f"DEFAULT_EXCHANGE_MAP {DEFAULT_EXCHANGE_MAP} is not inside the "
        "fh_symbol_check package \u2014 wheel install would break"
    )
    assert parts[-2:] == ("data", "exchange_mapping.yaml"), (
        f"unexpected location for packaged mapping: {DEFAULT_EXCHANGE_MAP}"
    )


def test_default_exchange_map_is_valid_yaml_mapping() -> None:
    """The bundled file must load cleanly as a `{str: str}` mapping \u2014
    otherwise the default install is broken out of the box."""
    data = yaml.safe_load(DEFAULT_EXCHANGE_MAP.read_text())
    assert isinstance(data, dict) and data, "bundled exchange_mapping.yaml is empty"
    for k, v in data.items():
        assert isinstance(k, str) and isinstance(v, str), (
            f"bundled exchange_mapping.yaml has non-string entry {k!r}: {v!r}"
        )


def test_exit_codes_exported() -> None:
    """Schedulers key off the documented exit codes; freezing them here
    catches accidental renames."""
    assert EXIT_OK == 0
    assert cli_module.EXIT_INVALID_FOUND == 1
    assert cli_module.EXIT_OPERATIONAL_FAILURE == 2
    assert cli_module.EXIT_INTERRUPT == 130
