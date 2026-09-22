#!/usr/bin/env python3
"""herdr plugin hook: deliver the armed resume prompt once the planner is idle.

Event-driven counterpart of `pairctl watch-compact-continue` (issue #10). herdr
invokes this on pane.agent_status_changed with HERDR_PLUGIN_EVENT and
HERDR_PLUGIN_EVENT_JSON in the environment. The hook resolves the pane against
the pairctl pane index ($XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json) and runs

    python3 <pairctl> resume-deliver --pane <pane_id> --via plugin \
        --cwd <index cwd> --state-dir <index state_dir>

exactly once when this pane is the recorded planner of a live pair state whose
compaction epoch has advanced. An executor-pane edge instead runs

    python3 <pairctl> executor-event --pane <pane_id> --status <agent_status> \
        --cwd <index cwd> --state-dir <index state_dir>

and never reaches resume-deliver (issue #12); pairctl owns every further
decision there (dedupe, hold, interval, ignore). Contractual rules:

* only pane.agent_status_changed; agent_status must be idle or done;
* executor routing fires only for a fresh executor entry whose state holds a
  live delivered round executed by this pane; everything else falls through to
  the planner checks below, so an executor-role hit with no live round makes
  no pairctl call at all;
* stale entries are dropped (PAIRCTL_SESSION_STALE_HOURS, default 12 h);
* candidates = fresh planner entries whose resume_pending names this pane,
  is pending/uncertain, and has compaction_epoch > epoch_at_arm;
* newest recorded_at wins; a newest stamp shared by two state dirs is ambiguous:
  log a line containing "ambiguous_pane" and do not deliver;
* zero hit, stale, mismatch or any check failure: exit 0 with empty stdout and
  stderr;
* a selected candidate whose pairctl cannot answer (missing file, or stdout
  that is not JSON): log a "pairctl_failed" line, show the
  "herdr-pair pairctl failed" notification once (marker file), exit 1.
  A non-zero exit code on its own is not a failure: lock_timeout and
  planner_busy answer JSON with exit 2, and those stay silent exit 0.

The hook never imports pairctl and never writes state.json. Its only herdr
call is that failure notification (`notification show`); it never runs
`herdr agent prompt`.
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
from notify import append_log, notify_pairctl_failed, run_pairctl  # noqa: E402

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


# Statuses this hook forwards for an executor pane (issue #12); anything else
# falls through to the planner path, which filters it out silently.
EXECUTOR_OK_STATUS = {"done", "blocked", "idle", "working", "unknown"}


def select_executor_entry(pane_id: str) -> dict | None:
    """Newest fresh executor entry whose pair state is live on this pane.

    Returns None on zero hit, stale entries, or a state without an active
    delivered round executed by this pane - the hook never forwards a status
    pairctl would only have to ignore, so a stale executor-role hit stays
    completely silent. An ambiguous newest stamp shared by two state dirs is
    logged and not forwarded, mirroring select_candidate.
    """
    limit = dt.timedelta(hours=stale_hours())
    now = dt.datetime.now(dt.timezone.utc)
    candidates: list[tuple[dict, dt.datetime]] = []
    for entry in load_entries(pane_id):
        if entry.get("role") != "executor":
            continue
        stamp = parse_stamp(entry.get("recorded_at"))
        if stamp is None or now - stamp > limit:
            continue
        state = load_state(str(entry.get("state_dir") or ""))
        live = any(
            str(r.get("status") or "") == "active"
            and str(r.get("executor") or "") == pane_id
            and str(r.get("dispatch_status") or "") == "delivered"
            for r in state.get("rounds") or []
            if isinstance(r, dict)
        )
        if not live:
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
            f"state_dirs={','.join(state_dirs)}: no executor-event"
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
    # herdr 0.8.0 wraps the event fields in an envelope {"event", "data"}: read
    # pane_id/agent_status from `data` when it is a dict, else the flat payload.
    data = payload.get("data")
    if isinstance(data, dict):
        payload = data
    pane_id = str(payload.get("pane_id") or "")
    if not pane_id:
        return 0
    agent_status = str(payload.get("agent_status") or "")
    pairctl = os.environ.get("PAIRCTL") or str(
        Path(__file__).resolve().parent.parent / "scripts" / "pairctl.py"
    )
    # Issue #12: an executor-pane edge goes to executor-event, never
    # resume-deliver. Only a fresh entry naming a live delivered round for this
    # pane counts; otherwise fall through, where the planner filters keep the
    # hook silent (an executor-role hit with no live round makes no call).
    if agent_status in EXECUTOR_OK_STATUS:
        exec_entry = select_executor_entry(pane_id)
        if exec_entry is not None:
            argv = [
                "executor-event",
                "--pane", pane_id,
                "--status", agent_status,
                "--cwd", str(exec_entry.get("cwd") or ""),
                "--state-dir", str(exec_entry.get("state_dir") or ""),
            ]
            ok, detail = run_pairctl(pairctl, argv)
            if not ok:
                notify_pairctl_failed(pane_id, pairctl, detail)
                return 1
            return 0
    if agent_status not in FRESH_OK_STATUS:
        return 0
    entry = select_candidate(pane_id)
    if entry is None:
        return 0
    argv = [
        "resume-deliver",
        "--pane", pane_id, "--via", "plugin",
        "--cwd", str(entry.get("cwd") or ""),
        "--state-dir", str(entry.get("state_dir") or ""),
    ]
    # Capture the child's output: the hook stays silent whatever resume-deliver
    # answers. Only a pairctl that cannot answer (missing file, non-JSON stdout)
    # is the one loud path: log pairctl_failed, notify once, exit 1 (issue #11).
    # A JSON answer with a non-zero exit (lock_timeout, planner_busy) is a
    # completed answer under the issue #10 silence contract.
    ok, detail = run_pairctl(pairctl, argv)
    if not ok:
        notify_pairctl_failed(pane_id, pairctl, detail)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
