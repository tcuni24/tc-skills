#!/usr/bin/env python3
"""Claude Code SessionStart hook for herdr-pair.

Registered in ~/.claude/settings.json under SessionStart. Claude Code runs it after
/compact and /clear (and on startup/resume). It reads the hook payload from stdin,
asks pairctl whether a pairing exists for the session's cwd, and:

- records the pending rollover (`pairctl rollover`) when the five-round limit had been
  reached and the context is now fresh;
- injects the pairing checkpoint into the new context as `additionalContext`.

It never blocks the session: every failure path exits 0 with no output. It only
talks to pairctl; it does not call herdr, does not switch sessions, and does not
read credentials.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys

PAIRCTL = Path(__file__).resolve().with_name("pairctl.py")
FRESH_SOURCES = {"compact", "clear"}
STARTUP_SOURCES = {"startup", "resume"}
STALE_AFTER_HOURS = 12
MAX_CONTEXT_CHARS = 16000


def run_pairctl(cwd: str, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env.setdefault("PAIRCTL_HERDR", "herdr")
    try:
        proc = subprocess.run(
            [sys.executable, str(PAIRCTL), *args, "--cwd", cwd],
            capture_output=True, text=True, check=False, timeout=30, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def parse_json(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def recent(updated_at: str) -> bool:
    try:
        stamp = dt.datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - stamp < dt.timedelta(hours=STALE_AFTER_HOURS)


def emit(context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context[:MAX_CONTEXT_CHARS],
        }
    }, ensure_ascii=False))


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = str(payload.get("cwd") or os.getcwd())
    source = str(payload.get("source") or "")
    session_id = str(payload.get("session_id") or "")

    code, out, _err = run_pairctl(cwd, "status")
    status = parse_json(out)
    if not status or code not in (0, 20, 2):
        return 0  # no pairing state for this cwd, or pairctl unavailable
    if status.get("status") == "PENDING_DISPATCH_UNRESOLVED":
        if source in FRESH_SOURCES or recent(str(status.get("updated_at") or "")):
            emit(
                "herdr-pair: a dispatch is still unresolved for this working directory. "
                + str(status.get("guidance") or "")
            )
        return 0

    rollover_required = bool(status.get("rollover_required"))
    fresh_now = source in FRESH_SOURCES
    touched_recently = recent(str(status.get("updated_at") or ""))
    if not rollover_required and not fresh_now:
        return 0
    if not touched_recently and not (rollover_required and fresh_now):
        # A pairing left behind days ago must not resurface on an unrelated /clear or start.
        # The one exception is a pending rollover right after the compaction it asked for.
        return 0

    lines = ["herdr-pair SessionStart hook."]
    if rollover_required:
        recorded = str(status.get("session_id") or "")
        reason = "compact" if source == "compact" or not session_id or session_id == recorded else "new"
        rcode, rout, rerr = run_pairctl(
            cwd, "rollover", "--reason", reason, "--new-session-id", session_id or recorded,
        )
        result = parse_json(rout)
        if rcode == 0 and result.get("status") == "rollover_recorded":
            lines.append(
                f"Rollover recorded automatically after `{source or 'unknown'}`: phase is now "
                f"{result.get('phase')} (reason={reason}, session_id={result.get('session_id')}, "
                f"compaction_epoch={result.get('compaction_epoch')}). Nonterminal background "
                f"jobs carried: {result.get('carried_nonterminal_jobs')}."
            )
        else:
            lines.append(
                "Rollover was required but could not be recorded automatically: "
                f"{(rerr or rout).strip()[:500]}. Run `python3 {PAIRCTL} rollover` yourself "
                "after checking `status`."
            )
    else:
        lines.append(
            f"Context was refreshed by `{source}` mid-phase; the pairing continues in phase "
            f"{status.get('phase')} with {status.get('phase_round_count')} rounds used."
        )
    checkpoint = str(status.get("checkpoint") or "")
    if checkpoint and Path(checkpoint).is_file():
        try:
            text = Path(checkpoint).read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text:
            lines += ["", f"Checkpoint `{checkpoint}` (read it before doing anything else):", "", text]
    lines += [
        "",
        f"Then run `python3 {PAIRCTL} status` and continue with the herdr-pair skill. "
        "Re-resolve the executor pane with `herdr agent list` before sending the next round.",
    ]
    emit("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - a hook must never break session start
        raise SystemExit(0)
