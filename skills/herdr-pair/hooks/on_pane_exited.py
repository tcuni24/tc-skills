#!/usr/bin/env python3
"""herdr plugin hook: forward a pane exit to pairctl (issue #12).

herdr invokes this on pane.exited with HERDR_PLUGIN_EVENT and
HERDR_PLUGIN_EVENT_JSON in the environment. The hook resolves the exited pane
against the pairctl pane index ($XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json)
and runs, once per distinct (cwd, state_dir) of every fresh entry:

    python3 <pairctl> executor-event --pane <pane_id> --status exited \
        --cwd <index cwd> --state-dir <index state_dir>

pairctl owns the whole decision: an executor pane records
executor_pane_gone_at and short-reports the planner, a planner pane expires the
owed resume and notifies the human, anything unmatched answers "ignored". The
hook itself never reads or writes state.json and never imports pairctl. Its
only herdr call is the one-time failure notification in notify.py.

Contractual rules:

* only pane.exited; stale entries are dropped (PAIRCTL_SESSION_STALE_HOURS,
  default 12 h);
* zero hit or only stale entries: exit 0 with empty stdout and stderr;
* a selected entry whose pairctl cannot answer (missing file, or stdout that is
  not JSON): log a "pairctl_failed" line, show the "herdr-pair pairctl failed"
  notification once (marker file), exit 1. A non-zero exit code on its own is
  not a failure: executor-event answers JSON for every gate it rejects, and
  those stay silent exit 0.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

# Same directory as this hook: log paths, the pairctl runner, and the one-time
# failure notification live in notify.py so neither module imports the other's.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import append_log, notify_pairctl_failed, recorded_machine, run_pairctl  # noqa: E402

DEFAULT_STALE_HOURS = 12.0
DEFAULT_STATE_HOME = "~/.local/state"
EVENT_NAME = "pane.exited"


def index_path(pane_id: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / "panes" / f"{pane_id}.json"


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


def stale_hours() -> float:
    try:
        return float(os.environ.get("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS))
    except ValueError:
        return DEFAULT_STALE_HOURS


def fresh_targets(pane_id: str) -> list[tuple[str, str]]:
    """Distinct (cwd, state_dir) pairs of the fresh index entries for this pane.

    One executor-event per pair: the same state dir reached by two entries
    (planner and executor roles, or a refreshed stamp) is one state to tell,
    while two different state dirs each need their own view of the exit.
    """
    limit = dt.timedelta(hours=stale_hours())
    now = dt.datetime.now(dt.timezone.utc)
    seen: set[tuple[str, str]] = set()
    targets: list[tuple[str, str]] = []
    for entry in load_entries(pane_id):
        stamp = parse_stamp(entry.get("recorded_at"))
        if stamp is None or now - stamp > limit:
            continue  # stale or unparsable: treated as nonexistent
        cwd = str(entry.get("cwd") or "")
        state_dir = str(entry.get("state_dir") or "")
        if not cwd or not state_dir:
            continue  # an entry that cannot name a state says nothing
        if (cwd, state_dir) in seen:
            continue
        seen.add((cwd, state_dir))
        targets.append((cwd, state_dir))
    return targets


def main() -> int:
    if os.environ.get("HERDR_PLUGIN_EVENT", "") != EVENT_NAME:
        return 0
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "") or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    # herdr 0.8.0 wraps the event fields in an envelope {"event", "data"}: read
    # pane_id from `data` when it is a dict, else the flat payload.
    data = payload.get("data")
    if isinstance(data, dict):
        payload = data
    pane_id = str(payload.get("pane_id") or "")
    if not pane_id:
        return 0
    targets = fresh_targets(pane_id)
    if not targets:
        return 0  # zero hit or only stale entries: completely silent
    pairctl = os.environ.get("PAIRCTL") or str(
        Path(__file__).resolve().parent.parent / "scripts" / "pairctl.py"
    )
    for cwd, state_dir in targets:
        argv = [
            "executor-event",
            "--pane", pane_id, "--status", "exited",
            "--cwd", cwd, "--state-dir", state_dir,
        ]
        machine = recorded_machine(state_dir)
        if machine:
            argv.extend(("--machine", machine))
        # Capture the child's output: the hook stays silent whatever
        # executor-event answers. Only a pairctl that cannot answer (missing
        # file, non-JSON stdout) logs pairctl_failed, notifies once, exits 1.
        ok, detail = run_pairctl(pairctl, argv)
        if not ok:
            notify_pairctl_failed(pane_id, pairctl, detail, machine)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
