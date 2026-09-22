#!/usr/bin/env python3
"""Popup board for pair.status (issue #14): render one `pairctl status` answer.

herdr runs this as the `pair-status` plugin pane (placement popup). It never
imports pairctl: the selection (cwd/state-dir) resolves through the exact same
priority list as hooks/action.py — explicit args, PAIRCTL_* env, pane index,
focused pane cwd, workspace cwd — and the status read itself is a pairctl child
process. The board renders whatever that single stdout JSON carried, including
the exit-2 pending-dispatch payload; a missing or non-JSON answer degrades to
one readable line instead of a traceback. Read-only: nothing here writes
state.json, the sidebar file, or the herdr socket.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action  # noqa: E402  (same resolution + pairctl invocation as the actions)


def render(payload: dict) -> str:
    """Human-readable text for the six board fields of one status payload."""
    lines = ["herdr-pair status"]
    lines.append(f"goal: {str(payload.get('goal') or '').strip() or '(no goal)'}")
    lines.append(f"phase: {payload.get('phase')}")
    age = payload.get("pending_dispatch_age_s")
    lines.append(
        "pending_dispatch_age_s: " + ("none" if age is None else f"{age}s")
    )
    resume = payload.get("resume_pending")
    lines.append(
        "resume_pending: "
        + ("none" if resume is None else json.dumps(resume, ensure_ascii=False, sort_keys=True))
    )
    lines.append("rounds:")
    rounds = [r for r in (payload.get("rounds") or []) if isinstance(r, dict)]
    if not rounds:
        lines.append("  (none)")
    for item in rounds:
        lines.append(
            f"  {item.get('round_id') or '?'} status={item.get('status') or '?'}"
            f" executor={item.get('executor') or '-'}"
        )
    lines.append("notices:")
    notices = [n for n in (payload.get("notices") or []) if isinstance(n, dict)]
    if not notices:
        lines.append("  (none)")
    for entry in notices:
        shown = str(entry.get("shown")).lower()  # shown=false must stay visible
        reason = str(entry.get("reason") or "").strip()
        suffix = f" reason={reason}" if reason else ""
        lines.append(f"  {entry.get('title') or '(untitled)'} shown={shown}{suffix}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="herdr-pair status board pane")
    parser.add_argument("--cwd", help="explicit pair cwd (highest resolution priority)")
    parser.add_argument("--state-dir", help="explicit pair state directory")
    args = parser.parse_args()

    hit = action.resolve(args, action.load_context())
    if hit is None:
        print("herdr-pair status: no pairing resolved for this pane context")
        return 0
    _source, cwd, state_dir = hit
    _code, payload, detail = action.run_pairctl(cwd, state_dir, ["status"])
    if payload is None:
        print(f"herdr-pair status unavailable: {detail or 'pairctl failed'}")
        return 0
    print(render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
