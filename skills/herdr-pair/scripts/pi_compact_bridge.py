#!/usr/bin/env python3
"""Fail-closed Pi session_compact adapter; no action on pre/failure events."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from claude_session_start_hook import checkpoint_context

PAIRCTL = Path(__file__).with_name("pairctl.py")


def call(cwd, *args):
    try:
        run = subprocess.run([sys.executable, str(PAIRCTL), *args, "--cwd", cwd],
                             capture_output=True, text=True, timeout=30, check=False)
        return json.loads(run.stdout.strip().splitlines()[-1]) if run.returncode in (0, 2, 20) else {}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return {}


def bridge(cwd, pane, session_id, event):
    if event != "session_compact" or not pane or not session_id:
        return {"status": "ignored"}
    status = call(cwd, "status")
    if (status.get("planner_pane") != pane or
            not (status.get("compact_queued") or status.get("rollover_required")) or
            status.get("session_id") not in ("", session_id)):
        return {"status": "ignored"}
    result = call(cwd, "rollover", "--reason", "compact", "--new-session-id", session_id)
    if result.get("status") != "rollover_recorded":
        return {"status": "failed"}
    context = checkpoint_context(status.get("checkpoint") or "")
    result["context"] = ("herdr-pair Pi compact recovery. First run pairctl status and read "
                         "each active round's report; then read the full CHECKPOINT. "
                         "Re-resolve the executor pane before dispatch.\n\n" + context)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cwd", required=True)
    p.add_argument("--pane", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--event", required=True)
    a = p.parse_args()
    print(json.dumps(bridge(a.cwd, a.pane, a.session_id, a.event), ensure_ascii=False))
