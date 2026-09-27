"""pairctl notice: Executor notices: dedup, rate limiting, deferred delivery and dispatch staleness."""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import time
from typing import Any

from .constants import (
    DEFAULT_DISPATCH_STALE_S,
    DEFAULT_EXECUTOR_NOTICE_MIN_S,
    EXECUTOR_NOTICE_NEXT,
    EXECUTOR_NOTICE_SOUNDS,
    HOST_FAILURE_REASONS,
    RESUME_CLAIMABLE,
    STALE_DISPATCH_TITLE,
)
from .herdr import is_agent_prompted, run_herdr
from .state import file_sha256, now, persist


def record_notice(
    args: argparse.Namespace,
    pp: dict[str, Path],
    data: dict[str, Any],
    *,
    title: str,
    body: str,
    dedupe_key: str = "",
    sound: str = "",
) -> bool:
    """Show one host notification and record its outcome in state (issue #11).

    Every `herdr notification show` goes through here: call herdr first, then
    append {at,title,body,reason,shown,dedupe_key} to state["notices"] and
    persist. Host suppression reasons (rate_limited, busy, no_foreground_client,
    disabled) and herdr failures record shown=false but still record; a
    non-empty dedupe_key that already exists skips the herdr call and the
    second record entirely. Returns True exactly when a NEW notice was
    appended - the caller asks "was anything notified", not "did the host
    toast appear", so a rate-limited delivery still counts. `sound` (issue #12)
    is passed to the host as `--sound <value>` only when set, so the recorded
    notice keeps its exact seven-field shape whatever sound it played.
    """
    notices = data.setdefault("notices", [])
    if dedupe_key and any(
        str(entry.get("dedupe_key") or "") == dedupe_key for entry in notices
    ):
        return False  # already notified once: no second call, no second record
    reason = "manual"
    shown = True
    try:
        code, out, err, payload = run_herdr(
            args,
            ["notification", "show", title, "--body", body]
            + (["--sound", sound] if sound else []),
        )
    except ValueError:
        reason, shown = "herdr_failed", False  # herdr missing or timed out
    else:
        result = payload.get("result") if isinstance(payload, dict) else None
        failed = (
            code != 0
            or not isinstance(result, dict)
            or (isinstance(payload, dict) and payload.get("error"))
        )
        if failed:
            reason, shown = "herdr_failed", False
        else:
            reason = str(result.get("reason") or "manual")
            shown = bool(result.get("shown", True))
    if reason in HOST_FAILURE_REASONS:
        shown = False  # host suppressed the toast; the record still must exist
    notices.append({
        "at": now(),
        "title": title,
        "body": body,
        "reason": reason,
        "shown": shown,
        "dedupe_key": dedupe_key,
    })
    persist(pp, data)
    return True  # a new notice exists, whatever the host did with it


def executor_notice_min_s() -> float:
    """PAIRCTL_EXECUTOR_NOTICE_MIN_S seconds (issue #12); unset/invalid falls back to 60."""
    raw = (os.environ.get("PAIRCTL_EXECUTOR_NOTICE_MIN_S") or "").strip()
    if not raw:
        return DEFAULT_EXECUTOR_NOTICE_MIN_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_EXECUTOR_NOTICE_MIN_S
    # `value >= 0` also rejects NaN, which would otherwise never read as elapsed.
    return value if value >= 0 else DEFAULT_EXECUTOR_NOTICE_MIN_S


def executor_notice_hold(data: dict[str, Any]) -> str:
    """Why executor notices must wait ("" = they may go out now) (issue #12).

    A queued compaction, or a resume still owed to the planner, means the next
    prompt would land behind `/compact` - before the summary exists - or before
    the pairing resumed at all. Those notices queue in deferred_notices and ride
    the resume prompt instead of racing it.
    """
    if data.get("compact_queued"):
        return "compact_queued"
    record = data.get("resume_pending")
    if isinstance(record, dict) and str(record.get("status") or "") in RESUME_CLAIMABLE:
        return "resume_pending"
    return ""


def round_report_hash(round_item: dict[str, Any], cwd: str) -> tuple[bool, str]:
    """(report file exists, sha256 hexdigest of its bytes) for this round (issue #12).

    No report recorded, an unresolvable path or a missing file all answer
    (False, "") - the empty string is the hash of "there is no file".
    """
    report = str(round_item.get("report") or "")
    if not report:
        return False, ""
    path = Path(report).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    if not path.is_file():
        return False, ""
    return True, file_sha256(path)


def executor_notice_entry(
    pp: dict[str, Path],
    data: dict[str, Any],
    round_item: dict[str, Any],
    status: str,
    *,
    report_hash: str,
    reason: str,
) -> dict[str, Any]:
    """One executor short report + host notification, deferrable as a unit (issue #12).

    The first prompt line is the fixed contract string
    `herdr-pair executor <status> round <round_id> revision <revision>`; the
    notification title is `herdr-pair executor <status>`; the dedupe key is
    `executor:<round_id>:<revision>:<status>:<report_hash>`.
    """
    round_id = str(round_item.get("round_id") or "")
    revision = int(round_item.get("current_revision", 1) or 1)
    head = f"herdr-pair executor {status} round {round_id} revision {revision}"
    report = str(round_item.get("report") or "")
    prompt = (
        f"{head}\n"
        f"Working directory: {data['cwd']}\n"
        f"State directory: {pp['root']}\n"
        f"Executor pane: {round_item.get('executor') or 'unknown'}\n"
        f"Report: {report or 'not written yet'}\n"
        f"Next:{EXECUTOR_NOTICE_NEXT.get(status, '')}"
    )
    body = (
        f"{head}; executor pane {round_item.get('executor') or 'unknown'}; report "
        f"{report or 'not written yet'}; state_dir={pp['root']}. Verify by artifact: "
        "a notification is not evidence."
    )
    return {
        "kind": "executor",
        "title": f"herdr-pair executor {status}",
        "prompt": prompt,
        "body": body,
        "sound": EXECUTOR_NOTICE_SOUNDS.get(status, ""),
        "dedupe_key": f"executor:{round_id}:{revision}:{status}:{report_hash}",
        "round_id": round_id,
        "revision": revision,
        "status": status,
        "report_hash": report_hash,
        "reason": reason,
        "at": now(),
    }


def issue_executor_notice(
    args: argparse.Namespace,
    pp: dict[str, Path],
    data: dict[str, Any],
    entry: dict[str, Any],
) -> bool:
    """Deliver one executor notice: short-report the planner, then notify the human.

    Returns True when a NEW notice record was appended (record_notice's contract).
    A dedupe key that is already recorded skips both calls: that notice went out
    earlier and the planner must never be prompted twice for the same event.
    """
    key = str(entry.get("dedupe_key") or "")
    notices = data.setdefault("notices", [])
    if key and any(str(n.get("dedupe_key") or "") == key for n in notices):
        return False
    planner = str(data.get("planner_pane") or "")
    prompt = str(entry.get("prompt") or "")
    prompted = False
    if planner and prompt:
        try:
            code, _, _, payload = run_herdr(args, ["agent", "prompt", planner, prompt])
            prompted = code == 0 and is_agent_prompted(payload)
        except ValueError:
            prompted = False  # herdr unusable: the notice record still must exist
    entry["prompted"] = prompted
    return record_notice(
        args,
        pp,
        data,
        title=str(entry.get("title") or ""),
        body=str(entry.get("body") or ""),
        dedupe_key=key,
        sound=str(entry.get("sound") or ""),
    )


def flush_executor_notices(
    args: argparse.Namespace,
    pp: dict[str, Path],
    data: dict[str, Any],
    round_item: dict[str, Any],
) -> int:
    """Emit the withheld executor notices once nothing holds them back (issue #12).

    Called only from the path that is about to push a fresh notice, so a hold or
    a dedupe can never trigger a flush by itself. Returns how many new notices
    were recorded while emptying deferred_notices.
    """
    entries = data.get("deferred_notices") or []
    if not entries:
        return 0
    data["deferred_notices"] = []
    sent = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if issue_executor_notice(args, pp, data, entry):
            sent += 1
        if str(entry.get("round_id") or "") == str(round_item.get("round_id") or ""):
            round_item["last_executor_notice_at"] = now()
            if entry.get("report_hash"):
                round_item["last_executor_report_hash"] = str(entry["report_hash"])
    return sent


def dispatch_stale_threshold_s() -> float:
    """PAIRCTL_DISPATCH_STALE_S seconds (issue #11); unset/invalid falls back to 900."""
    raw = (os.environ.get("PAIRCTL_DISPATCH_STALE_S") or "").strip()
    if not raw:
        return DEFAULT_DISPATCH_STALE_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DISPATCH_STALE_S
    return value if value >= 0 else DEFAULT_DISPATCH_STALE_S


def check_dispatch_stale(
    args: argparse.Namespace, pp: dict[str, Path], data: dict[str, Any]
) -> bool:
    """Notify once when pending_dispatch outlived the staleness threshold.

    Wake-up point check (issue #11): runs from `wake` (every source), from
    `status` after the state read, and from every `watch-compact-continue`
    round. The dedupe key keeps later wake-ups quiet. Returns True when a
    notice was newly recorded.
    """
    pending = data.get("pending_dispatch")
    if not isinstance(pending, dict) or not pending:
        return False  # nothing pending: never notify
    created_at = str(pending.get("created_at") or "")
    try:
        stamp = dt.datetime.fromisoformat(created_at)
    except ValueError:
        return False  # unparsable stamp cannot be compared: stay quiet
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    if time.time() - stamp.timestamp() <= dispatch_stale_threshold_s():
        return False  # still inside the threshold window
    round_id = str(pending.get("round_id") or "")
    target = str(pending.get("target") or "")
    body = (
        "pending_dispatch is unresolved past the staleness threshold: "
        f"round_id={round_id} target={target} created_at={created_at} "
        f"state_dir={pp['root']}. Inspect the recorded dispatch and resolve it "
        "with `resolve-pending --outcome delivered|not-delivered` only after "
        "confirming whether the pane received the handoff."
    )
    return record_notice(
        args,
        pp,
        data,
        title=STALE_DISPATCH_TITLE,
        body=body,
        dedupe_key=f"dispatch-stale:{round_id}:{created_at}",
    )


def resume_due(record: dict[str, Any]) -> bool:
    """True when a resume record's deadline passed (issue #11 watcher wake-ups)."""
    try:
        deadline = dt.datetime.fromisoformat(str(record.get("deadline") or ""))
    except ValueError:
        return True  # an unparsable deadline must never block delivery forever
    return deadline.timestamp() <= time.time()


def validate_new_session_id(value: str, current: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("new-session-id is empty")
    if text.startswith("<") and text.endswith(">"):
        raise ValueError(f"new-session-id is a placeholder: {text}")
    if current and text == current:
        raise ValueError("new-session-id must differ from the current session_id")
    return text
