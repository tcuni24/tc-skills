#!/usr/bin/env python3
"""Persistent round and background-job state for herdr-pair.

Thin entry point: the implementation lives in `pairctl/` next to this file
(see `pairctl/__init__.py` for the layout). `PAIRCTL` and every hook keep
pointing at this path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pairctl.cli import main  # noqa: E402  (scripts/ added to sys.path above)

if __name__ == "__main__":
    raise SystemExit(main())
