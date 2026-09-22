#!/usr/bin/env python3
"""herdr plugin hook: deliver the armed resume prompt once the planner is idle.

Event-driven counterpart of `pairctl watch-compact-continue` (issue #10). herdr
invokes this on pane.agent_status_changed with HERDR_PLUGIN_EVENT and
HERDR_PLUGIN_EVENT_JSON in the environment. The hook resolves the pane against
the pairctl pane index ($XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json) and runs

    python3 <pairctl> resume-deliver --pane <pane_id> --via plugin \
        --cwd <index cwd> --state-dir <index state_dir>

exactly once when this pane is the recorded planner of a live pair state whose
compaction epoch has advanced. Contractual rules:

* only pane.agent_status_changed; agent_status must be idle or done;
* stale entries are dropped (PAIRCTL_SESSION_STALE_HOURS, default 12 h);
* candidates = fresh planner entries whose resume_pending names this pane,
  is pending/uncertain, and has compaction_epoch > epoch_at_arm;
* newest recorded_at wins; a newest stamp shared by two state dirs is ambiguous:
  log a line containing "ambiguous_pane" and do not deliver;
* zero hit, stale, mismatch or any check failure: exit 0 with empty stdout and
  stderr.

The hook never imports pairctl, never writes state.json, and never calls herdr.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_STALE_HOURS = 12.0
DEFAULT_STATE_HOME = "~/.local/state"
EVENT_NAME = "pane.agent_status_changed"
FRESH_OK_STATUS = {"idle", "done"}
# Terminal for the record, or already claimed by another wake-up source: only
# pending/uncertain may be delivered by this hook.
DELIVERABLE_STATUS = {"pending", "uncertain"}


def index_path(pane_id: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / "panes" / f"{pane_id}.json"


def log_path() -> Path:
    plugin_state = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if plugin_state:
        return Path(plugin_state).expanduser() / "hook.log"
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / "plugin-hook.log"


def append_log(line: str) -> None:
    """Best-effort plugin log: logging must never change the hook's exit path."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def parse_stamp(value: object) -> dt.datetime | None:
    try:
        stamp = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp


def load_entries(pane_id: str) -> list[dict]:
    try:
        raw = json.loads(index_path(pane_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []  # zero hit: this pane was never recorded
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def load_state(state_dir: str) -> dict:
    try:
        raw = json.loads((Path(state_dir).expanduser() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # unreadable state: this candidate simply does not exist
    return raw if isinstance(raw, dict) else {}


def stale_hours() -> float:
    try:
        return float(os.environ.get("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS))
    except ValueError:
        return DEFAULT_STALE_HOURS


def select_candidate(pane_id: str) -> dict | None:
    """Newest fresh planner entry whose pair state is armed for this pane.

    Returns None on zero hit, stale entries, failed checks, or an ambiguous newest
    stamp shared by two state dirs (which is logged, never delivered).
    """
    limit = dt.timedelta(hours=stale_hours())
    now = dt.datetime.now(dt.timezone.utc)
    candidates: list[tuple[dict, dt.datetime]] = []
    for entry in load_entries(pane_id):
        if entry.get("role") != "planner":
            continue  # executor-role hits are silent (issue #12 owns that path)
        stamp = parse_stamp(entry.get("recorded_at"))
        if stamp is None or now - stamp > limit:
            continue  # stale or unparsable: treated as nonexistent
        state = load_state(str(entry.get("state_dir") or ""))
        record = state.get("resume_pending")
        if not isinstance(record, dict) or not record:
            continue
        if str(record.get("planner_pane") or "") != pane_id:
            continue
        if str(record.get("status") or "") not in DELIVERABLE_STATUS:
            continue
        try:
            advanced = int(state.get("compaction_epoch", 0)) > int(record.get("epoch_at_arm", 0))
        except (TypeError, ValueError):
            continue
        if not advanced:
            continue
        candidates.append((entry, stamp))
    if not candidates:
        return None
    newest = max(stamp for _, stamp in candidates)
    top = [entry for entry, stamp in candidates if stamp == newest]
    state_dirs = sorted({str(entry.get("state_dir") or "") for entry in top})
    if len(state_dirs) > 1:
        append_log(
            f"ambiguous_pane pane={pane_id} recorded_at={newest.isoformat()} "
            f"state_dirs={','.join(state_dirs)}: no resume-deliver"
        )
        return None
    return top[0]


def main() -> int:
    if os.environ.get("HERDR_PLUGIN_EVENT", "") != EVENT_NAME:
        return 0
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "") or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    pane_id = str(payload.get("pane_id") or "")
    if not pane_id:
        return 0
    if str(payload.get("agent_status") or "") not in FRESH_OK_STATUS:
        return 0
    entry = select_candidate(pane_id)
    if entry is None:
        return 0
    pairctl = os.environ.get("PAIRCTL") or str(
        Path(__file__).resolve().parent.parent / "scripts" / "pairctl.py"
    )
    argv = [
        sys.executable, pairctl, "resume-deliver",
        "--pane", pane_id, "--via", "plugin",
        "--cwd", str(entry.get("cwd") or ""),
        "--state-dir", str(entry.get("state_dir") or ""),
    ]
    # Capture the child's output: the hook stays silent whatever resume-deliver
    # reports, and its own exit code is always 0 (contract: checks failing or
    # hitting zero records exit quietly).
    try:
        subprocess.run(argv, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
