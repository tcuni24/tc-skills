#!/usr/bin/env python3
"""Startup hook for herdr-pair (issue #14): replay the saved sidebar view.

hooks/action.py's pair.status persists the agent.view.set params it computed
to $HERDR_PLUGIN_STATE_DIR/sidebar-view.json. This hook hands that file to
sidebar_view.replay(), which applies it only for panes that are still live and
clears a view whose panes are all gone. It never recomputes the query, never
touches pair state, and never calls pairctl. When the file, the state dir or
the socket is missing, replay() stays silent — a plugin that cannot reach the
host must stay invisible, not noisy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sidebar_view  # noqa: E402


def main() -> int:
    sidebar_view.replay()
    return 0


if __name__ == "__main__":
    sys.exit(main())
