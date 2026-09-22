#!/usr/bin/env python3
"""Startup hook for herdr-pair (issue #14): replay the saved sidebar view.

hooks/action.py's pair.status persists the agent.view.set params it computed
to $HERDR_PLUGIN_STATE_DIR/sidebar-view.json. This hook only replays that file:
it never recomputes the query, never touches pair state, and never calls
pairctl. When the file or the socket is missing, or the write fails, it exits
0 — a plugin that cannot reach the host must stay invisible, not noisy.

The frame is newline JSON on $HERDR_SOCKET_PATH:
{"id": "<non-empty>", "method": "agent.view.set", "params": <file contents>}
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

REQUEST_ID = "herdr-pair-startup-view"
SOCKET_TIMEOUT_S = 5.0


def main() -> int:
    state_dir = (os.environ.get("HERDR_PLUGIN_STATE_DIR") or "").strip()
    sock_path = (os.environ.get("HERDR_SOCKET_PATH") or "").strip()
    if not state_dir or not sock_path:
        return 0
    try:
        params = json.loads(
            (Path(state_dir).expanduser() / "sidebar-view.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return 0
    if not isinstance(params, dict):
        return 0
    frame = (
        json.dumps(
            {"id": REQUEST_ID, "method": "agent.view.set", "params": params},
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(sock_path)
            sock.sendall(frame.encode("utf-8"))
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
