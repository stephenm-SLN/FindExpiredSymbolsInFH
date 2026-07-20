#!/usr/bin/env python3
"""Find delisted symbols in Feed Handler config.

This top-level script exists so the tool can still be launched from a
source checkout via ``pixi run python find_expired_symbols.py ...``. The
same entry point is exposed as the installed console script
``find-expired-symbols`` (see ``pyproject.toml``). Both paths call
:func:`fh_symbol_check.cli.run`.

See ``requirements.md`` / ``design.md`` in the workspace root for details.
"""

from __future__ import annotations

import sys

from fh_symbol_check.cli import run

if __name__ == "__main__":
    sys.exit(run())
