"""pairctl: herdr-pair's round and background-job state machine.

Split out of the former single-file `scripts/pairctl.py`, which is now a thin
entry point. Modules are layered so imports only point downwards:

    constants -> state -> herdr -> notice -> handoff -> context
              -> rounds -> dispatch -> executor -> commands -> cli

`herdr_bin.py` at the skill root stays the single source of truth for locating
the herdr executable; the skill root is put on `sys.path` here so every module
below can `from herdr_bin import resolve_herdr`. A few genuine cycles (rounds
calling into dispatch for resume/watcher work) are broken with function-local
imports, each marked with a comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `herdr_bin.py` lives at the skill root, two levels above `scripts/pairctl/`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
