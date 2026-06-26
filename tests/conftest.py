"""Pytest configuration: ensure the workspace root is on sys.path.

The tool's entry point (find_expired_symbols.py) runs from the workspace root,
so the workspace root is on sys.path at runtime. Pytest also discovers it as
the rootdir; this file just makes the dependency explicit.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
