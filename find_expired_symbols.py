#!/usr/bin/env python3
"""Find delisted symbols in Feed Handler config for a given host.

See requirements.md / design.md in the workspace root for full details.
"""

from __future__ import annotations

import logging
import sys

from fh_symbol_check.cli import EXIT_INTERRUPT, EXIT_OPERATIONAL_FAILURE, main


def _run() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPT
    except Exception:
        logging.getLogger("fh_symbol_check").exception("unhandled exception")
        return EXIT_OPERATIONAL_FAILURE


if __name__ == "__main__":
    sys.exit(_run())
