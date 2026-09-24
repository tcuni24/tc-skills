"""pairctl subprocess runner and failure notification for the plugin hooks.

Shared by `on_planner_status.py` (issue #11) and `on_pane_exited.py`
(issue #12). This module never imports pairctl.py: it spawns the script as a
child process, so a pairctl import error cannot take the hook down with it.
The only host call here is `herdr notification show` announcing that pairctl
failed, once per failure episode.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

NOTIFIED_MARKER = "pairctl-failed-notified"
FAILURE_TITLE = "herdr-pair pairctl failed"
DEFAULT_STATE_HOME = "~/.local/state"


def log_path() -> Path:
    plugin_state = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if plugin_state:
        return Path(plugin_state).expanduser() / "hook.log"
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / "plugin-hook.log"


def marker_path() -> Path:
    plugin_state = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if plugin_state:
        return Path(plugin_state).expanduser() / NOTIFIED_MARKER
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / NOTIFIED_MARKER


def append_log(line: str) -> None:
    """Best-effort plugin log: logging must never change the caller's exit path."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def run_pairctl(pairctl_path: str, argv: list[str]) -> tuple[bool, str]:
    """Run pairctl argv; (True, stdout) only when stdout is parseable JSON.

    Failure (issue #11 as ruled by the planner) is exactly two classes: the
    pairctl file does not exist, or the child's stdout is not JSON. A non-zero
    exit code on its own is *not* a failure - `lock_timeout` and `planner_busy`
    both answer a JSON object with exit 2, and the issue #10 silence contract
    makes those completed answers the hook must swallow. Spawning, timing out,
    or dying before printing anything leaves no JSON on stdout, so they fall
    into the same not-JSON class. detail is safe to log and to show in the
    failure notification. The pairctl child gets a bounded lock wait so a busy
    state lock surfaces as an answer instead of hanging the hook.
    """
    path = Path(pairctl_path)
    if not path.is_file():
        return False, f"pairctl not found: {pairctl_path}"
    env = os.environ.copy()
    env.setdefault("PAIRCTL_LOCK_WAIT_S", "2")
    cmd = [sys.executable, pairctl_path, *argv]
    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True, check=False, timeout=30, env=env
        )
    except subprocess.TimeoutExpired:
        return False, "pairctl stdout was not JSON (timed out)"
    except OSError as exc:
        return False, f"pairctl stdout was not JSON (spawn failed: {exc})"
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else ""
        suffix = f": {tail}" if tail else ""
        return False, f"pairctl stdout was not JSON (exit {proc.returncode}){suffix}"[:300]
    clear_failure_notice()
    return True, json.dumps(payload, sort_keys=True)


def clear_failure_notice() -> None:
    """End the current failure episode once pairctl answers again.

    Without this the marker outlives the failure that wrote it, and every later
    failure (a different cause included) is only logged, never shown.
    """
    marker = marker_path()
    try:
        marker.unlink()
    except OSError:
        return  # no marker (the usual case) or not removable: nothing to announce
    append_log("pairctl_recovered: failure notice re-armed")


def notify_pairctl_failed(pane: str, pairctl_path: str, detail: str) -> None:
    """Log the failure and show one host notification per failure episode (best effort).

    The marker file gates the host call: the first failure logs and notifies,
    later failures only log until run_pairctl sees pairctl answer again and
    clears the marker. The marker is written after the attempt so a crash
    mid-notify leaves the next run a chance to retry. Never raises.
    """
    line = f"pairctl_failed pane={pane} pairctl={pairctl_path} detail={detail}"
    try:
        append_log(line)
    except OSError:
        pass
    marker = marker_path()
    try:
        if marker.is_file():
            return
        # Same resolution as action.py: PAIRCTL_HERDR, then HERDR_BIN_PATH
        # (plugin processes lack herdr on PATH), then the literal (issue #16).
        herdr = (
            os.environ.get("PAIRCTL_HERDR")
            or os.environ.get("HERDR_BIN_PATH")
            or "herdr"
        )
        subprocess.run(
            [herdr, "notification", "show", FAILURE_TITLE, "--body", line],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1\n", encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired):
        pass
