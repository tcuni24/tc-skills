#!/usr/bin/env python3
"""Single source of truth for locating the `herdr` executable.

Resolution order, first hit wins:

1. an explicit `--herdr` argument (pairctl only);
2. `PAIRCTL_HERDR` — the tests and pairctl's own child processes set it;
3. `HERDR_BIN_PATH` — injected by the host into plugin subprocesses, whose
   `PATH` has no `herdr` on it (issue #16);
4. the literal `herdr`.

`hooks/action.py`, `hooks/notify.py` and pairctl all import this module, so the
order is defined once. A script in `hooks/` or `scripts/` reaches it by putting
the skill root on `sys.path` first:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from herdr_bin import resolve_herdr

Both `hooks/` and `scripts/` sit one level below the skill root, so the same
line works from either place, and it never depends on the current directory.
"""

from __future__ import annotations

import os

DEFAULT_HERDR = "herdr"


def resolve_herdr(cli_value: str | None = None) -> str:
    """Return the herdr executable to run; never raises on missing configuration."""
    value = (
        cli_value
        or os.environ.get("PAIRCTL_HERDR")
        or os.environ.get("HERDR_BIN_PATH")
        or DEFAULT_HERDR
    )
    return value
