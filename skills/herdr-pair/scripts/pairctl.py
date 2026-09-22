#!/usr/bin/env python3
"""Persistent round and background-job state for herdr-pair."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator


class PendingDispatchError(ValueError):
    def __init__(
        self, pending: dict[str, Any], notices: list[dict[str, Any]] | None = None,
        board: dict[str, Any] | None = None,
    ) -> None:
        self.pending = pending
        # status carries the notices array even on the pending-error path (issue #11).
        self.notices = notices
        # status also carries the six popup-board fields on that path (issue #14).
        self.board = board
        super().__init__("unresolved pending_dispatch")


class LockTimeoutError(Exception):
    """PAIRCTL_LOCK_WAIT_S elapsed before the state lock was acquired (issue #11).

    Deliberately not a ValueError: main() must answer with the exact
    lock_timeout payload on stdout and an empty stderr, not PAIRCTL_ERROR.
    """


VERSION = 2
ROLLOVER_EXIT = 20
MAX_ARG_BYTES = 131071
FILE_MODE = 0o600
DIR_MODE = 0o700
TERMINAL_JOBS = {"succeeded", "failed", "cancelled"}
ROUND_STATES = {"accepted", "blocked", "failed", "cancelled"}
JOB_STATES = {"submitted", "running", "succeeded", "failed", "cancelled", "unknown"}
ROUND_HEADER = re.compile(r"^\[轮次\][ \t]+round_id=\S+[ \t]*\r?$", re.MULTILINE)
PAIRCTL_SCRIPT = str(Path(__file__).resolve())
PENDING_GUIDANCE = (
    "A previous send-round left an unresolved dispatch. Inspect the recorded "
    "target pane and round_id and confirm whether that pane received the handoff. "
    "pairctl will not resend. Resolve it explicitly with `resolve-pending "
    "--outcome delivered` or `resolve-pending --outcome not-delivered` only after "
    "that inspection. Do not edit state.json by hand."
)
ROLLOVER_GUIDANCE = (
    "Phase limit reached. If planner_compact.queued is true, pairctl already queued the "
    "planner pane's own compaction command via herdr; it executes when the current turn "
    "ends. After the planner pane is idle again, pairctl prompts it to rollover and continue "
    "the pairing so the user does not have to send a resume message. Disable with "
    "PAIRCTL_CONTINUE_AFTER_COMPACT=0. A Claude Code planner also records the rollover from "
    "its SessionStart hook. If compact was not queued, send it with "
    f"`python3 {PAIRCTL_SCRIPT} compact-self` (cursor `/summarize`; claude/pi `/compact`; "
    "droid `/compress`). pairctl never switches sessions, never starts a Droid session, never "
    "calls the Factory Sessions API, and never reads credentials."
)
FRESH_GUIDANCE = (
    "The executor pane was not cleared, so the handoff was not sent and no round was "
    "consumed. Inspect both `herdr agent get` and `herdr agent read`; agent_status is a "
    "routing hint, not proof that a turn completed or that the agent is unavailable. UI quota "
    "banners (including `AI: Out of credits`) are informational and must not be interpreted "
    "as proof that subscription capacity is exhausted. If the visible screen disagrees with "
    "agent_status, treat the state as unknown and do not clear or resend. Pass --fresh-command "
    "'/…' and --fresh-marker '…' for an agent kind pairctl does not know, or --no-fresh only "
    "when deliberately retaining the existing context. An unknown fresh command is a dispatch "
    "configuration problem, not evidence that the executor is unusable."
)
# Slash command that starts a fresh session (empties the context) per herdr agent kind.
# Verified on this box 2026-09-07: pi /new, claude /clear, kimi /clear (all via
# `herdr agent prompt`). codex/opencode/droid entries are documented, not yet observed.
FRESH_COMMANDS = {
    "pi": "/new",
    "claude": "/clear",
    "kimi": "/clear",
    "droid": "/clear",
    "codex": "/new",
    "opencode": "/new",
}
# Text that appears on the visible screen once the fresh command took effect.
FRESH_MARKERS = {
    "pi": ("New session started",),
    "claude": ("Claude Code v",),
    "kimi": ("Started a new session",),
}
# Slash command that compacts the current session in place, per agent kind.
# Cursor's analogue of Claude/pi `/compact` is `/summarize` (user-stated 2026-09-07;
# finish-round previously failed closed with "no compact command known for agent kind 'cursor'").
COMPACT_COMMANDS = {
    "claude": "/compact",
    "pi": "/compact",
    "kimi": "/compact",
    "codex": "/compact",
    "opencode": "/compact",
    "droid": "/compress",
    "cursor": "/summarize",
}
# Kinds whose compact command takes free-text focus instructions after the command.
COMPACT_ACCEPTS_INSTRUCTIONS = {"claude", "pi"}
FRESH_OK_STATUS = {"idle", "done"}
FRESH_MAX_LINES = 12
HERDR_CALL_TIMEOUT = 30.0
DEFAULT_CONTEXT_BUDGET = 150000
DEFAULT_STALE_HOURS = 12.0
# A resume_pending record left in any of these states may still be claimed by
# resume-deliver; delivered/expired/cancelled are terminal for that record.
RESUME_CLAIMABLE = {"pending", "uncertain", "claimed"}
DEFAULT_RESUME_DEADLINE_S = 600.0
SNAPSHOT_COPY_LIMIT = 50 * 1024 * 1024
FENCE_RE = re.compile(
    r"^\s*\[(可以改|只读输入|可以新建|不许动|环境)\]\s*(.*?)\s*$", re.MULTILINE
)
TMP_PATH_RE = re.compile(r"(^|[^\w])/tmp(/|\b)")
# Issue #11: notification notices, pending-dispatch staleness, bounded lock wait.
HOST_FAILURE_REASONS = {"rate_limited", "busy", "no_foreground_client", "disabled"}
DEFAULT_DISPATCH_STALE_S = 900.0
STALE_DISPATCH_TITLE = "herdr-pair dispatch stale"
# Written verbatim (no trailing newline, no sort_keys) on a bounded lock timeout.
LOCK_TIMEOUT_PAYLOAD = '{"status":"rejected","reason":"lock_timeout"}'
# Issue #12: executor status reports become a short report to the planner plus a
# host notification, deduplicated per round / revision / status / report hash and
# rate-limited to one push per PAIRCTL_EXECUTOR_NOTICE_MIN_S seconds per round.
DEFAULT_EXECUTOR_NOTICE_MIN_S = 60.0
# `herdr notification show --sound` per status; idle deliberately stays silent.
EXECUTOR_NOTICE_SOUNDS = {"done": "done", "blocked": "request", "exited": "request"}
# What the planner should do next, appended to the short report per status.
EXECUTOR_NOTICE_NEXT = {
    "done": " Verify the artifacts before accepting: a notification is not evidence.",
    "idle": " Check whether the Report file changed: a notification is not evidence.",
    "blocked": " Unblock it or re-cut the round: a notification is not evidence.",
    "exited": (
        " Re-resolve the executor before the next dispatch: send-round to this pane "
        "fails with executor_pane_gone."
    ),
}


class FreshError(ValueError):
    """The executor pane could not be verified as freshly cleared."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_cwd(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def default_root(cwd: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    key = hashlib.sha256(cwd.encode()).hexdigest()[:20]
    return base / "herdr-pair" / key


def pane_index_path(pane_id: str) -> Path:
    """Per-pane index file: $XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json (issue #10).

    Shared by every pair state directory, so it lives outside any single state root
    and is never protected by a state lock.
    """
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return base / "herdr-pair" / "panes" / f"{pane_id}.json"


def paths(args: argparse.Namespace) -> dict[str, Path]:
    cwd = canonical_cwd(args.cwd)
    root = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_root(cwd)
    return {
        "root": root,
        "state": root / "state.json",
        "lock": root / "state.lock",
        "ledger": root / "jobs.tsv",
        "checkpoint": root / "CHECKPOINT.md",
        "contracts": root / "contracts",
        "planner_session": root / "planner-session.json",
        "snapshots": root / "snapshots",
    }


def chmod_private(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def lock_wait_s() -> float | None:
    """PAIRCTL_LOCK_WAIT_S seconds (issue #11): bound on acquiring the state lock.

    Unset, unparsable or negative keeps the historical blocking lock; a valid
    value lets a hook event be abandoned on contention and retried by the next
    wake-up source instead of hanging behind another writer.
    """
    raw = (os.environ.get("PAIRCTL_LOCK_WAIT_S") or "").strip()
    if not raw:
        return None
    try:
        wait = float(raw)
    except ValueError:
        return None
    return wait if wait >= 0 else None


@contextlib.contextmanager
def locked(root: Path, lock_path: Path, create: bool = False) -> Iterator[None]:
    if create:
        root.mkdir(parents=True, exist_ok=True)
        chmod_private(root, DIR_MODE)
    if not root.is_dir():
        raise ValueError("pair state is not initialized")
    with lock_path.open("a+", encoding="utf-8") as handle:
        chmod_private(lock_path, FILE_MODE)
        wait = lock_wait_s()
        if wait is None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"state lock not acquired within {wait}s"
                        )
                    time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chmod_private(path.parent, DIR_MODE)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        chmod_private(Path(tmp_name), FILE_MODE)
        os.replace(tmp_name, path)
        chmod_private(path, FILE_MODE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def record_pane(pane_id: str, state_dir: str, cwd: str, role: str) -> None:
    """Index which pair state directory a pane belongs to (issue #10).

    The file is a JSON array of {state_dir, cwd, role, recorded_at} elements keyed
    by state_dir + role: rewriting one refreshes recorded_at in place and moves the
    element to the end of the array instead of appending a duplicate. An empty
    pane id writes nothing. The index only feeds the plugin resume hook, so
    state.json stays the single source of truth: a corrupt file restarts the
    array and an unwritable location never fails the command it decorates.
    """
    if not pane_id:
        return
    path = pane_index_path(pane_id)
    raw: Any = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None  # missing or corrupt: rebuild from this write
    entries: list[dict[str, str]] = (
        [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    )
    key = (state_dir, role)
    entries = [
        e for e in entries
        if (str(e.get("state_dir") or ""), str(e.get("role") or "")) != key
    ]
    entries.append({
        "state_dir": state_dir, "cwd": cwd, "role": role, "recorded_at": now(),
    })
    try:
        atomic_text(path, json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass  # best-effort cache; the state machine does not depend on it


def load(path: Path, cwd: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("pair state is not initialized; run init")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("cwd") != cwd:
        raise ValueError(f"state cwd mismatch: {data.get('cwd')!r} != {cwd!r}")
    # Issue #11: states written before notices existed gain the array on load.
    data.setdefault("notices", [])
    # Issue #12: executor notices withheld during compaction queue up here.
    data.setdefault("deferred_notices", [])
    ver = data.get("version")
    if ver == 1:
        data["version"] = 2
        if data.get("pending_dispatch"):
            data["pending_dispatch"]["legacy_unconfirmed_protocol"] = True
        for r in data.get("rounds", []):
            if r.get("status") == "active":
                r["work_status"] = "unconfirmed_protocol"
                r["dispatch_status"] = "delivered"
                r["current_revision"] = 0
                r.setdefault("revisions", {})
                r.setdefault("receipts", [])
            else:
                r["work_status"] = r.get("status")
                r["dispatch_status"] = "delivered"
                r["current_revision"] = 0
                r.setdefault("revisions", {})
                r.setdefault("receipts", [])
        save(path, data)
    elif ver == 2:
        pass
    else:
        raise ValueError(f"unsupported state version: {ver!r}")
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    atomic_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def persist(pp: dict[str, Path], data: dict[str, Any]) -> None:
    save(pp["state"], data)
    write_ledger(pp["ledger"], data)


def load_ready(pp: dict[str, Path], cwd: str) -> dict[str, Any]:
    data = load(pp["state"], cwd)
    # jobs.tsv is a derived view of state.json, never a second source of truth.
    write_ledger(pp["ledger"], data)
    pending = data.get("pending_dispatch")
    if pending:
        raise PendingDispatchError(pending)
    return data


def pending_payload(pending: dict[str, Any]) -> dict[str, Any]:
    round_id = str(pending.get("round_id") or "")
    target = str(pending.get("target") or "")
    return {
        "status": "PENDING_DISPATCH_UNRESOLVED",
        "round_consumed": False,
        "resent": False,
        "counted": False,
        "round_id": round_id,
        "target": target,
        "session_switched": False,
        "guidance": (
            f"{PENDING_GUIDANCE} target={target} round_id={round_id}."
        ),
    }


def board_fields(data: dict[str, Any]) -> dict[str, Any]:
    """The six fields the pair.status popup board renders (issue #14).

    Both `pairctl status` exits — the normal answer and the PENDING_DISPATCH_
    UNRESOLVED rejection — carry this exact set, computed once from the state
    already loaded under the lock. `pending_dispatch_age_s` stays null unless a
    pending record with a parsable created_at exists; `resume_pending` is null
    when no record was ever armed.
    """
    age: int | None = None
    pending = data.get("pending_dispatch")
    if isinstance(pending, dict):
        stamp = parse_stamp(pending.get("created_at"))
        if stamp is not None:
            age = max(0, int(time.time() - stamp.timestamp()))
    return {
        "goal": str(data.get("goal") or ""),
        "phase": data.get("phase"),
        "rounds": [
            {
                "round_id": str(item.get("round_id") or ""),
                "status": str(item.get("status") or ""),
                "executor": str(item.get("executor") or ""),
            }
            for item in (data.get("rounds") or [])
            if isinstance(item, dict)
        ],
        "pending_dispatch_age_s": age,
        "resume_pending": data.get("resume_pending"),
        "notices": data.get("notices", []),
    }


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


def output(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def tsv(value: Any) -> str:
    return str(value or "").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def write_ledger(path: Path, data: dict[str, Any]) -> None:
    fields = [
        "round_id", "phase", "queue", "job_id", "label", "submitter",
        "command", "log", "expected_artifacts", "completion_assertion",
        "state", "cancel_retry_owner", "created_at", "updated_at",
    ]
    lines = ["\t".join(fields)]
    for job in data["jobs"]:
        lines.append("\t".join(tsv(job.get(field)) for field in fields))
    atomic_text(path, "\n".join(lines) + "\n")


def table_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def budget_for(args: argparse.Namespace, data: dict[str, Any] | None = None) -> int:
    explicit = getattr(args, "budget", None)
    if explicit is None:
        explicit = getattr(args, "context_budget", None)
    if explicit is not None:
        if explicit <= 0:
            raise ValueError("--budget must be positive")
        return explicit
    raw = (os.environ.get("PAIRCTL_CONTEXT_BUDGET") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("PAIRCTL_CONTEXT_BUDGET must be an integer") from exc
        if value <= 0:
            raise ValueError("PAIRCTL_CONTEXT_BUDGET must be positive")
        return value
    if data and data.get("context_budget"):
        return int(data["context_budget"])
    return DEFAULT_CONTEXT_BUDGET


def parse_stamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def unknown_usage(budget: int, reason: str, source: str = "unknown") -> dict[str, Any]:
    return {
        "status": "unknown", "context_tokens": None, "budget": budget,
        "over_budget": False, "session_started_at": None, "session_age_hours": None,
        "stale": False, "source": source, "measured_at": now(), "reason": reason,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derive_transcript(cwd: str, session_id: str) -> Path:
    """Locate a transcript when the hook did not provide an explicit path.

    Checked on 2026-09-21 against the first cwd-bearing record of all 97
    top-level JSONL transcripts in 31 local Claude project directories: zero
    mismatches, including underscores, Chinese characters and dots. Another
    10 directories had no JSONL samples; spaces are covered by a synthetic test.
    This is an observed fallback convention, not a Claude API guarantee.
    """
    escaped = re.sub(r"[^A-Za-z0-9-]", "-", cwd)
    return Path.home() / ".claude" / "projects" / escaped / f"{session_id}.jsonl"


def read_context_usage(
    pp: dict[str, Path], cwd: str, budget: int, stale_hours: float,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not pp["planner_session"].is_file():
        return unknown_usage(budget, "planner session record missing")
    try:
        session = json.loads(pp["planner_session"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return unknown_usage(budget, "planner session record unreadable")
    if session.get("cwd") != cwd:
        return unknown_usage(budget, "planner session cwd mismatch")
    if session.get("kind") != "claude":
        return unknown_usage(budget, "planner kind is not claude")
    planner_pane = str((data or {}).get("planner_pane") or "")
    recorded_pane = str(session.get("pane") or "")
    if planner_pane and recorded_pane and recorded_pane != planner_pane:
        return unknown_usage(budget, "planner pane does not match session record")
    session_id = str(session.get("session_id") or "")
    if not session_id:
        return unknown_usage(budget, "planner session id missing")
    recorded = str(session.get("transcript_path") or "")
    transcript = Path(recorded).expanduser() if recorded else derive_transcript(cwd, session_id)
    source = "recorded" if recorded else "derived"
    if not transcript.is_file():
        return unknown_usage(budget, "transcript missing", source)
    first: dt.datetime | None = None
    last_usage: dict[str, Any] | None = None
    saw_session = False
    try:
        with transcript.open(encoding="utf-8") as transcript_handle:
            lines = transcript_handle
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                entry_session = str(entry.get("sessionId") or entry.get("session_id") or "")
                if entry_session == session_id:
                    saw_session = True
                    stamp = parse_stamp(entry.get("timestamp") or entry.get("created_at"))
                    if stamp is not None and (first is None or stamp < first):
                        first = stamp
                message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
                synthetic = bool(
                    entry.get("isSynthetic") or entry.get("synthetic")
                    or message.get("isSynthetic") or message.get("synthetic")
                    or message.get("model") == "<synthetic>"
                )
                is_assistant = message.get("role") == "assistant" or entry.get("type") == "assistant"
                if entry_session != session_id or synthetic or not is_assistant:
                    continue
                usage = message.get("usage")
                fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                if isinstance(usage, dict) and all(
                    isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)
                    for key in fields
                ):
                    last_usage = usage
                else:
                    last_usage = None
    except (OSError, UnicodeDecodeError):
        return unknown_usage(budget, "transcript unreadable", source)
    if not saw_session:
        return unknown_usage(budget, "transcript sessionId mismatch", source)
    if last_usage is None:
        return unknown_usage(budget, "assistant usage missing", source)
    tokens = sum(int(last_usage[key]) for key in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"
    ))
    age = None
    stale = False
    if first is not None:
        age = max(0.0, (dt.datetime.now(dt.timezone.utc) - first).total_seconds() / 3600)
        stale = age > stale_hours
    return {
        "status": "ok", "context_tokens": tokens, "budget": budget,
        "over_budget": tokens > budget,
        "session_started_at": first.isoformat() if first else None,
        "session_age_hours": round(age, 3) if age is not None else None,
        "stale": stale, "source": source, "measured_at": now(), "reason": "",
    }


def parse_handoff_fences(text: str) -> dict[str, list[str]]:
    result = {key: [] for key in ("可以改", "只读输入", "可以新建", "不许动", "环境")}
    for match in FENCE_RE.finditer(text):
        value = match.group(2).strip().rstrip("；;")
        if match.group(1) == "环境":
            result["环境"].append(value)
        else:
            result[match.group(1)].extend(
                part.strip().rstrip("；;") for part in re.split(r"[,，]", value) if part.strip()
            )
    return result


def normalized_fence_path(value: str, cwd: str | None = None) -> str:
    value = value.strip().replace("\\", "/")
    path = Path(value).expanduser()
    if path.is_absolute() and cwd:
        try:
            value = path.resolve().relative_to(Path(cwd).resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()
    else:
        value = os.path.normpath(value).replace("\\", "/")
    return value.rstrip("/") or "/"


def paths_intersect(left: str, right: str) -> bool:
    a, b = normalized_fence_path(left), normalized_fence_path(right)
    if a == "." or b == ".":
        other = b if a == "." else a
        return not Path(other).is_absolute() and other != ".." and not other.startswith("../")
    return bool(a and b and (a == b or a.startswith(b + "/") or b.startswith(a + "/")))


def handoff_lint(cwd: str, text: str) -> list[dict[str, Any]]:
    fences = parse_handoff_fences(text)
    findings: list[dict[str, Any]] = []
    groups = ("可以改", "可以新建", "不许动")
    for index, left in enumerate(groups):
        for right in groups[index + 1:]:
            for a in fences[left]:
                for b in fences[right]:
                    normalized_a = normalized_fence_path(a, cwd)
                    normalized_b = normalized_fence_path(b, cwd)
                    if paths_intersect(normalized_a, normalized_b):
                        findings.append({"rule": "fence_overlap", "left": a, "right": b})
    if TMP_PATH_RE.search(text):
        findings.append({"rule": "tmp_path"})
    if not fences["环境"]:
        findings.append({"rule": "env_block_missing"})
    forbidden_text = " ".join(fences["不许动"]).lower()
    if "未跟踪" in forbidden_text or "untracked" in forbidden_text:
        try:
            top = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            proc = subprocess.run(
                ["git", "-C", top.stdout.strip(), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                capture_output=True, text=True, check=False, timeout=10,
            ) if top.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc and proc.returncode == 0:
            untracked = []
            repo_root = Path(top.stdout.strip()).resolve()
            cwd_path = Path(cwd).resolve()
            for line in proc.stdout.split("\0"):
                if line.startswith("?? "):
                    absolute = repo_root / line[3:]
                    try:
                        untracked.append(absolute.resolve().relative_to(cwd_path).as_posix())
                    except ValueError:
                        continue
            for allowed in fences["可以改"] + fences["可以新建"]:
                allowed = normalized_fence_path(allowed, cwd)
                matches = [path for path in untracked if paths_intersect(allowed, path)]
                if matches:
                    findings.append({
                        "rule": "untracked_conflict", "path": allowed, "untracked": matches
                    })
    return findings


def parse_report_path(text: str, cwd: str) -> str:
    match = re.search(r"^\s*(?:\[报告\]|\[report\]|report\s*:)\s*(\S.*?)\s*$", text, re.I | re.M)
    if not match:
        return ""
    value = match.group(1).strip().strip("`").rstrip("；;")
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else Path(cwd) / path)


def snapshot_round(
    pp: dict[str, Path], cwd: str, round_id: str, revision: int, text: str
) -> tuple[dict[str, Any] | None, list[str]]:
    fences = parse_handoff_fences(text)
    requested = fences["可以改"] + fences["只读输入"] + fences["可以新建"]
    warnings: list[str] = []
    if not requested:
        return None, ["handoff has no snapshot fence paths"]
    root = pp["snapshots"] / f"{round_id}-r{revision}"
    root.mkdir(parents=True, exist_ok=True)
    chmod_private(root, DIR_MODE)
    records: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    cwd_path = Path(cwd)
    for raw in requested:
        rel = normalized_fence_path(raw, cwd)
        candidate = (cwd_path / rel).resolve()
        try:
            relative = candidate.relative_to(cwd_path)
        except ValueError:
            warnings.append(f"snapshot path outside cwd skipped: {raw}")
            continue
        if candidate.is_dir():
            files = sorted(path for path in candidate.rglob("*") if path.is_file())
            if not files:
                records.setdefault(rel, {"path": rel, "sha256": "", "size": 0, "mode": 0, "exists": True, "directory": True})
        elif candidate.exists():
            files = [candidate]
        else:
            records.setdefault(rel, {"path": rel, "sha256": "", "size": 0, "mode": 0, "exists": False})
            continue
        for source in files:
            file_rel = source.relative_to(cwd_path).as_posix()
            content_hash = file_sha256(source)
            size = source.stat().st_size
            record = {
                "path": file_rel, "sha256": content_hash, "size": size,
                "mode": source.stat().st_mode & 0o7777, "exists": True,
            }
            records[file_rel] = record
            if size > SNAPSHOT_COPY_LIMIT:
                record["copied"] = False
                skipped.append({"path": file_rel, "reason": "size_exceeds_copy_limit", "size": size})
            else:
                destination = root / "files" / file_rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                record["copied"] = True
    manifest = root / "manifest.json"
    atomic_text(manifest, json.dumps(list(records.values()), indent=2, sort_keys=True) + "\n")
    return {
        "manifest": str(manifest), "files": sorted(records), "skipped": skipped,
        "roots": [normalized_fence_path(value, cwd) for value in requested],
    }, warnings


def write_checkpoint(path: Path, data: dict[str, Any], reason: str) -> None:
    current = [
        r for r in data["rounds"]
        if r["phase"] == data["phase"] or r.get("status") == "active"
    ]
    active_jobs = [j for j in data["jobs"] if j["state"] not in TERMINAL_JOBS]
    usage = data.get("last_context_usage") or {}
    tokens = usage.get("context_tokens")
    lines = [
        "# Herdr Pair Checkpoint",
        "",
        f"- Generated: `{now()}`",
        f"- Reason: `{reason}`",
        f"- Working directory: `{data['cwd']}`",
        f"- Phase: `{data['phase']}`",
        f"- Rounds in phase: `{data['phase_round_count']}`",
        f"- Rollover required: `{str(data['rollover_required']).lower()}`",
        f"- Session ID: `{data.get('session_id') or 'unknown'}`",
        f"- Goal: `{table_cell(data.get('goal')) or 'unspecified'}`",
        f"- Context usage: `{tokens if tokens is not None else 'unknown'}`",
        f"- Budget: `{usage.get('budget', data.get('context_budget', DEFAULT_CONTEXT_BUDGET))}`",
        "",
        "## Current phase rounds",
        "",
        "| Round | Revision | Executor | Status | Contract | Report | Snapshot | Scope | Acceptance | Artifacts | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in current:
        lines.append(
            "| {round_id} | {current_revision} | {executor} | {status} | {contract} | {report} | {snapshot} | {scope} | {acceptance} | {artifacts} | {notes} |".format(
                **{key: table_cell(item.get(key)) for key in (
                    "round_id", "current_revision", "executor", "status", "scope", "acceptance",
                    "artifacts", "notes", "report",
                )},
                contract=table_cell(((item.get("revisions") or {}).get(str(item.get("current_revision", 1))) or {}).get("contract_path")),
                snapshot=table_cell((item.get("snapshot") or {}).get("manifest")),
            )
        )
    if not current:
        lines.append("| none |  |  |  |  |  |  |  |  |  |  |")
    lines += ["", "## Decisions", ""]
    notes = data.get("notes") or []
    if notes:
        lines.extend(f"- `{item.get('created_at')}` {item.get('text')}" for item in notes)
    else:
        lines.append("None.")
    lines += ["", "## Nonterminal background jobs", ""]
    if active_jobs:
        lines += ["```json", json.dumps(active_jobs, indent=2, ensure_ascii=False), "```"]
    else:
        lines.append("None.")
    compact_queued = data.get("compact_queued") or {}
    lines += [
        "",
        "## Resume",
        "",
        "1. Read this checkpoint and `jobs.tsv`.",
        "2. Before finishing a round, check whether every active round's Report path now exists.",
        "3. Verify every nonterminal job from its scheduler and log.",
        "4. Planner compaction: "
        + (
            f"`{compact_queued.get('command')}` was queued on planner pane "
            f"`{compact_queued.get('pane')}` at `{compact_queued.get('queued_at')}`; it runs "
            "when that turn ends."
            if compact_queued.get("phase") == data["phase"]
            else f"not queued yet; run `python3 {PAIRCTL_SCRIPT} compact-self` or ask the user "
            "to run that kind's compact command (cursor `/summarize`) or clear."
        ),
        "5. Record the rollover once the context is fresh: a Claude Code planner does this from "
        f"its SessionStart hook; otherwise run `python3 {PAIRCTL_SCRIPT} rollover --reason "
        "compact --new-session-id <id>` (use `--reason new` when the session id changed).",
        "6. Do not cancel, retry, or replace jobs without the recorded owner's authorization.",
        "",
    ]
    atomic_text(path, "\n".join(lines))
    data["last_checkpoint"] = {
        "path": str(path),
        "reason": reason,
        "created_at": now(),
    }


def find_round(data: dict[str, Any], round_id: str) -> dict[str, Any]:
    found = [item for item in data["rounds"] if item["round_id"] == round_id]
    if len(found) != 1:
        raise ValueError(f"unknown round_id: {round_id}")
    return found[0]


def active_round_ids(data: dict[str, Any]) -> list[str]:
    return [r["round_id"] for r in data["rounds"] if r["status"] == "active"]


def allocate_round_id(data: dict[str, Any]) -> str:
    return f"p{data['phase']:02d}-r{data['round_seq'] + 1:03d}"


def inject_round_id(text: str, round_id: str) -> str:
    header = f"[轮次] round_id={round_id}"
    match = ROUND_HEADER.search(text)
    if match:
        return text[:match.start()] + header + text[match.end():]
    return header + "\n" + text


def save_contract(
    pp: dict[str, Path], round_id: str, revision: int, text: str
) -> tuple[str, str]:
    contracts_dir = pp["contracts"]
    contracts_dir.mkdir(parents=True, exist_ok=True)
    chmod_private(contracts_dir, DIR_MODE)
    contract_file = contracts_dir / f"{round_id}.rev{revision}.contract"
    content_bytes = text.encode("utf-8")
    contract_hash = hashlib.sha256(content_bytes).hexdigest()
    atomic_text(contract_file, text)
    chmod_private(contract_file, FILE_MODE)
    return str(contract_file), contract_hash


def verify_round_contract(
    round_data: dict[str, Any], revision: int | None = None
) -> tuple[bool, str]:
    if revision is None:
        revision = round_data.get("current_revision", 1)
    payload, reason = verified_contract_payload(round_data, revision)
    return payload is not None, reason


def verified_contract_payload(
    round_data: dict[str, Any], revision: int
) -> tuple[dict[str, Any] | None, str]:
    revisions = round_data.get("revisions") or {}
    rev_info = revisions.get(str(revision))
    if not rev_info:
        return None, "contract_missing"
    path_str = str(rev_info.get("contract_path") or "")
    expected_hash = str(rev_info.get("contract_hash") or "")
    if not path_str or not Path(path_str).is_file():
        return None, "contract_missing"
    try:
        content_bytes = Path(path_str).read_bytes()
        contract_text = content_bytes.decode("utf-8")
    except OSError:
        return None, "contract_missing"
    except UnicodeDecodeError:
        return None, "contract_corrupted"
    if hashlib.sha256(content_bytes).hexdigest() != expected_hash:
        return None, "contract_corrupted"
    return {
        "revision": revision,
        "executor": round_data.get("executor") or "",
        "scope": rev_info.get("scope") or round_data.get("scope") or "",
        "acceptance": rev_info.get("acceptance") or round_data.get("acceptance") or "",
        "contract_hash": expected_hash,
        "contract_path": path_str,
        "contract_text": contract_text,
    }, "valid"


def commit_round(
    data: dict[str, Any],
    *,
    round_id: str,
    executor: str,
    scope: str,
    acceptance: str,
    fresh: dict[str, Any] | None = None,
    revision: int = 1,
    contract_path: str = "",
    contract_hash: str = "",
    dispatch_status: str = "delivered",
    work_status: str = "pending_acceptance",
    snapshot: dict[str, Any] | None = None,
    skip_lint: str = "",
    report: str = "",
) -> None:
    expected = allocate_round_id(data)
    if round_id != expected:
        raise ValueError(f"round_id mismatch: {round_id} != {expected}")
    data["round_seq"] += 1
    data["phase_round_count"] += 1
    rev_info = {
        "revision": revision,
        "scope": scope,
        "acceptance": acceptance,
        "contract_path": contract_path,
        "contract_hash": contract_hash,
        "created_at": now(),
    }
    data["rounds"].append({
        "round_id": round_id,
        "phase": data["phase"],
        "executor": executor,
        "scope": scope,
        "acceptance": acceptance,
        "status": "active",
        "dispatch_status": dispatch_status,
        "work_status": work_status,
        "current_revision": revision,
        "revisions": {str(revision): rev_info},
        "receipts": [],
        "started_at": now(),
        "finished_at": "",
        "artifacts": "",
        "notes": "",
        "report": report,
        "snapshot": snapshot,
        "skip_lint": skip_lint,
        "fresh": fresh,
    })
    if data["phase_round_count"] >= 5:
        data["rollover_required"] = True


def after_round_checkpoint(pp: dict[str, Path], data: dict[str, Any]) -> None:
    if data["phase_round_count"] == 3:
        write_checkpoint(pp["checkpoint"], data, "automatic three-round checkpoint")


def rollover_payload(
    pp: dict[str, Path], data: dict[str, Any], reason: str, args: argparse.Namespace
) -> dict[str, Any]:
    planner_compact = queue_planner_compact(args, pp, data)
    write_checkpoint(pp["checkpoint"], data, reason)
    return {
        "status": "SESSION_ROLLOVER_REQUIRED",
        "trigger": "SESSION_ROLLOVER_REQUIRED",
        "checkpoint": str(pp["checkpoint"]),
        "session_switched": False,
        "planner_compact": planner_compact,
        "guidance": ROLLOVER_GUIDANCE,
        "next": ROLLOVER_GUIDANCE,
    }


def emit_rollover_block(
    pp: dict[str, Path], data: dict[str, Any], reason: str, args: argparse.Namespace
) -> int:
    payload = rollover_payload(pp, data, reason, args)
    persist(pp, data)
    payload["continue_after_compact"] = spawn_compact_continue_watcher(
        args, pp, data, payload.get("planner_compact")
        if isinstance(payload.get("planner_compact"), dict) else None,
    )
    output(payload)
    return ROLLOVER_EXIT


def herdr_bin(args: argparse.Namespace) -> str:
    return getattr(args, "herdr", None) or os.environ.get("PAIRCTL_HERDR") or "herdr"


def run_herdr(
    args: argparse.Namespace, tail: list[str], timeout: float = HERDR_CALL_TIMEOUT
) -> tuple[int, str, str, Any]:
    """Run one herdr subcommand with an argv list (no shell). Never raises on herdr errors."""
    argv = [herdr_bin(args), *tail]
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, shell=False, timeout=timeout,
        )
    except OSError as exc:
        raise ValueError(f"cannot run herdr ({argv[0]}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"herdr timed out after {timeout}s: {' '.join(tail[:3])}") from exc
    return proc.returncode, proc.stdout, proc.stderr, parse_json_payload(proc.stdout)


def agent_info(args: argparse.Namespace, target: str) -> dict[str, Any]:
    code, out, err, payload = run_herdr(args, ["agent", "get", target])
    result = payload.get("result") if isinstance(payload, dict) else None
    agent = result.get("agent") if isinstance(result, dict) else None
    if code != 0 or not isinstance(agent, dict) or (isinstance(payload, dict) and payload.get("error")):
        raise ValueError(
            f"herdr agent get {target} failed: {herdr_error_code(payload) or clip(err or out, 300)}"
        )
    return agent


def agent_screen(args: argparse.Namespace, target: str, lines: int) -> str:
    """Visible screen text of the target pane. `herdr agent read` prints raw text, not JSON."""
    code, out, err, _ = run_herdr(
        args, ["agent", "read", target, "--source", "visible", "--lines", str(lines)],
    )
    if code != 0:
        raise ValueError(f"herdr agent read {target} failed: {clip(err or out, 300)}")
    return out


def verify_fresh(screen: str, markers: tuple[str, ...]) -> str:
    """Return how the screen proved fresh ('marker' or 'heuristic'), or '' if it did not."""
    if markers:
        return "marker" if any(m in screen for m in markers) else ""
    nonblank = [line for line in screen.splitlines() if line.strip()]
    return "heuristic" if len(nonblank) <= FRESH_MAX_LINES else ""


def freshen_executor(args: argparse.Namespace, target: str) -> dict[str, Any]:
    """Send the executor's fresh-session command and verify on screen that it took effect.

    Fails closed: any doubt raises FreshError and the handoff is not sent.
    """
    info = agent_info(args, target)
    kind = str(info.get("agent") or "")
    status = str(info.get("agent_status") or "")
    if status not in FRESH_OK_STATUS:
        raise FreshError(
            f"refusing to clear executor {target} ({kind}): agent_status={status!r}; "
            "only idle/done panes are cleared"
        )
    command = args.fresh_command or FRESH_COMMANDS.get(kind)
    if not command:
        raise FreshError(f"no fresh-session command known for agent kind {kind!r}")
    code, out, err, payload = run_herdr(args, ["agent", "prompt", target, command])
    if code != 0 or not is_agent_prompted(payload):
        raise FreshError(
            f"{command} was not accepted by {target}: "
            f"{herdr_error_code(payload) or 'unknown_response'} {clip(err or out, 300)}"
        )
    markers = tuple(args.fresh_marker or ()) + FRESH_MARKERS.get(kind, ())
    deadline = time.monotonic() + args.fresh_timeout
    started = time.monotonic()
    screen = ""
    while True:
        try:
            screen = agent_screen(args, target, args.fresh_lines)
        except ValueError:
            screen = ""
        verified = verify_fresh(screen, markers)
        if verified:
            return {
                "kind": kind,
                "command": command,
                "verified": verified,
                "markers": list(markers),
                "elapsed_s": round(time.monotonic() - started, 2),
                "at": now(),
            }
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    tail = "\n".join(line for line in screen.splitlines() if line.strip())[-600:]
    raise FreshError(
        f"{command} was sent to {target} ({kind}) but the screen did not confirm a fresh "
        f"session within {args.fresh_timeout}s (markers={list(markers)!r}); screen tail: {tail!r}"
    )


def auto_compact_enabled(data: dict[str, Any]) -> bool:
    if os.environ.get("PAIRCTL_AUTO_COMPACT", "").strip() in {"0", "off", "false", "no"}:
        return False
    return bool(data.get("auto_compact", True))


def env_flag(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "off", "false", "no"}


def env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def continue_after_compact_enabled() -> bool:
    return env_flag("PAIRCTL_CONTINUE_AFTER_COMPACT", True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def compact_continue_prompt(pp: dict[str, Path], data: dict[str, Any]) -> str:
    session = str(data.get("session_id") or "").strip() or "<session id from status>"
    selection = shlex.join(["--cwd", data["cwd"], "--state-dir", str(pp["root"])])
    reports = "; ".join(
        f"{item['round_id']} revision {item.get('current_revision', 1)}: "
        f"{item.get('report') or 'read the contract for the report path'}"
        for item in data.get("rounds", []) if item.get("status") == "active"
    ) or "no active rounds recorded"
    # Issue #12: executor notices withheld while compaction was queued are listed
    # here by title, so the planner learns what happened during the blackout. The
    # resume-deliver command clears deferred_notices once this prompt is sent.
    deferred = [item for item in (data.get("deferred_notices") or []) if isinstance(item, dict)]
    withheld = ""
    if deferred:
        withheld = (
            "\nDeferred executor notices:\n"
            + "\n".join(
                f"- {item.get('title') or 'herdr-pair executor notice'}" for item in deferred
            )
            + "\n"
        )
    return (
        "herdr-pair auto-continue after compaction. Do not wait for the user to say 继续.\n"
        f"Working directory: {data['cwd']}\n"
        f"Checkpoint: {pp['checkpoint']}\n"
        f"PAIRCTL={shlex.quote(PAIRCTL_SCRIPT)}\n"
        f"1. python3 \"$PAIRCTL\" status {selection}\n"
        f"2. Read any active Report files that have arrived: {reports}.\n"
        "3. If status still has compact_queued or rollover_required (or status is "
        "SESSION_ROLLOVER_REQUIRED), record the completed compaction:\n"
        f"   python3 \"$PAIRCTL\" rollover --reason compact --new-session-id {shlex.quote(session)} {selection}\n"
        "4. Read the checkpoint. Re-resolve the executor with herdr agent list "
        "(one writer per cwd).\n"
        "5. Continue the pairing immediately: independently verify any outstanding "
        "executor report, or dispatch the next prepared handoff. Do not ask the user "
        "to confirm."
        + withheld
    )


def spawn_compact_continue_watcher(
    args: argparse.Namespace,
    pp: dict[str, Path],
    data: dict[str, Any],
    planner_compact: dict[str, Any] | None,
) -> dict[str, Any]:
    """Detached waiter: after compact settles, prompt the planner to resume.

    Must not be queued through herdr behind `/compact`/`/summarize`: a follow-up
    prompt in that queue was observed to run before the summary existed.

    Only spawns for a freshly armed record (issue #9): the parent marker
    PAIRCTL_INTERNAL_WATCHER=1 suppresses spawning (we *are* an internal watcher),
    and the child itself runs with that marker instead of the user-facing
    PAIRCTL_CONTINUE_AFTER_COMPACT switch.
    """
    if os.environ.get("PAIRCTL_INTERNAL_WATCHER", "") == "1":
        return {"spawned": False, "reason": "internal watcher parent"}
    if not continue_after_compact_enabled():
        return {"spawned": False, "reason": "disabled"}
    if not planner_compact or not planner_compact.get("queued"):
        return {"spawned": False, "reason": "compact not queued"}
    if not planner_compact.get("resume_armed"):
        return {"spawned": False, "reason": "resume record not newly armed"}
    pid_path = pp["root"] / "compact-continue.pid"
    try:
        existing = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        existing = 0
    if existing and pid_alive(existing):
        return {"spawned": False, "reason": "watcher already running", "pid": existing}
    log = pp["root"] / "compact-continue.log"
    cmd = [sys.executable, PAIRCTL_SCRIPT, "watch-compact-continue", "--cwd", data["cwd"]]
    state_dir = getattr(args, "state_dir", None)
    if state_dir:
        # The child cwd is the state root: a relative --state-dir would point
        # inside itself. Always pass the resolved absolute root (issue #16).
        cmd += ["--state-dir", str(pp["root"])]
    herdr = herdr_bin(args)
    cmd += ["--herdr", herdr]
    # Child env = parent copy + the internal marker. Never rewrite the user's
    # PAIRCTL_CONTINUE_AFTER_COMPACT here (issue #9): that flag is not recursion control.
    env = os.environ.copy()
    env["PAIRCTL_HERDR"] = herdr
    env["PAIRCTL_INTERNAL_WATCHER"] = "1"
    try:
        handle = log.open("ab")
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(pp["root"]),
            env=env,
        )
        handle.close()
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        os.chmod(pid_path, FILE_MODE)
    except OSError as exc:
        return {"spawned": False, "reason": str(exc)}
    return {"spawned": True, "pid": proc.pid, "log": str(log)}


def spawn_resume_deliver(
    args: argparse.Namespace, cwd: str, pane: str, via: str = "watcher"
) -> dict[str, Any]:
    """Run `resume-deliver --via <via>` in a child pairctl; {} when unparsable.

    Shared by the watch loop and `wake --source watcher` (issue #11): the
    subprocess owns the claim state machine, so two wake-up sources racing
    here can never both prompt the planner.
    """
    cmd = [
        sys.executable, PAIRCTL_SCRIPT, "resume-deliver",
        "--pane", pane, "--via", via, "--cwd", cwd,
    ]
    state_dir = getattr(args, "state_dir", None)
    if state_dir:
        # Same rule as the watcher spawn: hand the child the resolved absolute
        # state root, never the caller's relative path (issue #16).
        cmd += ["--state-dir", str(paths(args)["root"])]
    cmd += ["--herdr", herdr_bin(args)]
    env = os.environ.copy()
    env["PAIRCTL_HERDR"] = herdr_bin(args)
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False, env=env)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def wake_check(
    args: argparse.Namespace, source: str, *, quiet: bool = False
) -> dict[str, Any]:
    """One wake-up point (issue #11): pending-dispatch staleness for every source,
    the due resume-deliver only for the watcher source.

    Uses load(), not load_ready(): a stale pending is notified here and the
    caller keeps running - resolve-pending / status still own the refusal path,
    and plain commands never gain a resume-deliver side effect.
    """
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    result: dict[str, Any] = {
        "status": "wake_checked",
        "source": source,
        "dispatch_stale_notified": False,
        "notices": [],
        "resume": None,
        "pane": "",
    }
    due_pane = ""
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        # jobs.tsv is a derived view of state.json, never a second source of truth.
        write_ledger(pp["ledger"], data)
        result["dispatch_stale_notified"] = check_dispatch_stale(args, pp, data)
        result["notices"] = data.get("notices", [])
        record = data.get("resume_pending")
        if (
            source == "watcher"
            and isinstance(record, dict)
            and record
            and str(record.get("status") or "") in {"pending", "uncertain"}
            and str(record.get("planner_pane") or "")
            and resume_due(record)
        ):
            due_pane = str(record.get("planner_pane") or "")
    if due_pane:
        # Released the lock first: the child claims through its own locked run.
        result["pane"] = due_pane
        payload = spawn_resume_deliver(args, cwd, due_pane, via="watcher")
        result["resume"] = payload.get("status")
    if not quiet:
        output(result)
    return result


def cmd_wake(args: argparse.Namespace) -> int:
    """`pairctl wake --source ...` (issue #11): one wake-up check, exit 0.

    A bounded lock (PAIRCTL_LOCK_WAIT_S) surfaces as the exact lock_timeout
    payload with exit 2 via main(); everything checkable here is best-effort.
    """
    wake_check(args, args.source, quiet=False)
    return 0


def cmd_watch_compact_continue(args: argparse.Namespace) -> int:
    """Low-frequency record-driven loop: deliver the armed resume exactly once (issue #9).

    Each tick re-reads resume_pending under the lock: terminal records exit, a
    pending/uncertain record past its deadline claims one delivery through
    `resume-deliver --via watcher`, and epoch_not_advanced / planner_busy / claimed
    simply wait for the next tick without counting failures. Delivery success still
    reports status=continue_prompted with exit 0, and the prompt text comes only
    from the existing compact_continue_prompt.

    Issue #11: every round additionally runs `wake --source watcher` - the
    pending-dispatch staleness notification plus the same due resume-deliver -
    on top of the original record-driven judgment below.
    """
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    poll = max(env_float("PAIRCTL_CONTINUE_POLL_S", 15.0), 0.05)
    while True:
        # Wake-up point first (issue #11). A bounded lock abandons only this round.
        try:
            wake = wake_check(args, "watcher", quiet=True)
        except LockTimeoutError:
            time.sleep(poll)
            continue
        if wake.get("resume") == "resume_delivered":
            output({"status": "continue_prompted", "pane": wake.get("pane")})
            return 0
        if wake.get("resume") == "resume_expired":
            output({
                "status": "continue_skipped", "reason": "resume_expired",
                "pane": wake.get("pane"),
            })
            return 0
        attempted = wake.get("resume") is not None
        with locked(pp["root"], pp["lock"]):
            data = load(pp["state"], cwd)
            write_ledger(pp["ledger"], data)
            raw = data.get("resume_pending")
            record = dict(raw) if isinstance(raw, dict) else {}
            pane = str(record.get("planner_pane") or "")
        if not record:
            output({"status": "continue_skipped", "reason": "no_record"})
            return 0
        status = str(record.get("status") or "")
        if status in {"delivered", "cancelled", "expired"}:
            output({"status": "continue_skipped", "reason": f"record_{status}", "pane": pane})
            return 0
        if status not in {"pending", "uncertain"}:
            # claimed by another wake-up source: only wait for that delivery.
            time.sleep(poll)
            continue
        if attempted:
            # wake above already spent this round's claim attempt; wait for the next tick.
            time.sleep(poll)
            continue
        try:
            due = dt.datetime.fromisoformat(str(record.get("deadline") or "")).timestamp() <= time.time()
        except ValueError:
            due = True  # an unparsable deadline must never block delivery forever
        if not due:
            time.sleep(poll)
            continue
        if not pane:
            output({"status": "continue_skipped", "reason": "planner_pane_unknown"})
            return 0
        # One claim per tick; the subprocess owns the state machine, so a concurrent
        # wake-up source (plugin, a second watcher) cannot double-send.
        payload = spawn_resume_deliver(args, cwd, pane, via="watcher")
        result = payload.get("status")
        if result == "resume_delivered":
            output({"status": "continue_prompted", "pane": pane})
            return 0
        if result == "resume_expired":
            output({"status": "continue_skipped", "reason": "resume_expired", "pane": pane})
            return 0
        # epoch_not_advanced / planner_busy / uncertain / unparsable response:
        # no failure counting here — the loop just waits for the next low-frequency tick.
        time.sleep(poll)


def compact_instructions(pp: dict[str, Path], data: dict[str, Any], kind: str) -> str:
    rollover = (
        "The SessionStart hook records the pairctl rollover automatically."
        if kind == "claude"
        else f"then run python3 {PAIRCTL_SCRIPT} rollover --reason compact --new-session-id <your session id>"
    )
    active = [r for r in data.get("rounds", []) if r.get("status") == "active"]
    if active:
        item = active[0]
        revision = item.get("current_revision", 1)
        contract = ((item.get("revisions") or {}).get(str(revision)) or {}).get("contract_path", "")
        focus = (
            f" Active round_id {item.get('round_id')} revision {revision}, contract {contract}, "
            f"executor pane {item.get('executor')}, Report {item.get('report') or 'unspecified'}."
        )
    else:
        focus = " No active round."
    return (
        "herdr-pair context checkpoint. Keep verbatim: checkpoint file "
        f"{pp['checkpoint']}. Goal: {data.get('goal') or 'unspecified'}. Preserve "
        f"planner pane {data.get('planner_pane') or 'unknown'}, working "
        f"directory {data['cwd']}.{focus} Keep every round_id with status, all nonterminal "
        "background jobs, open blockers, and the user's original task statement. The executor "
        "report may already have arrived; after compaction first read pairctl status and the "
        f"Report file, then read the checkpoint. {rollover}"
    )


def probe_resume_mechanism(args: argparse.Namespace, kind: str) -> str:
    """Decide `mechanism` for a fresh resume_pending write (issue #9).

    'plugin' only when `herdr plugin list --json` returns a well-formed plugin_list
    envelope carrying an enabled tc.herdr-pair entry AND the planner's `agent get`
    said claude (its kind, fetched by the caller). Any other outcome — non-zero
    exit, bad envelope, missing plugins, disabled plugin, non-Claude planner —
    means 'watcher'. Probing is best-effort and never fails the compact queue.
    """
    if kind != "claude":
        return "watcher"
    try:
        code, _out, _err, payload = run_herdr(args, ["plugin", "list", "--json"])
    except ValueError:
        return "watcher"
    if code != 0 or not isinstance(payload, dict) or payload.get("error"):
        return "watcher"
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("type") != "plugin_list":
        return "watcher"
    plugins = result.get("plugins")
    if not isinstance(plugins, list):
        return "watcher"
    for entry in plugins:
        if not isinstance(entry, dict) or entry.get("plugin_id") != "tc.herdr-pair":
            continue
        enabled = entry.get("enabled")
        if isinstance(enabled, str):
            try:
                enabled = json.loads(enabled)
            except ValueError:
                enabled = None
        return "plugin" if enabled is True else "watcher"
    return "watcher"


def queue_planner_compact(
    args: argparse.Namespace,
    pp: dict[str, Path],
    data: dict[str, Any],
    *,
    mode: str = "compact",
    force: bool = False,
) -> dict[str, Any]:
    """Queue the planner pane's own compact/clear command through herdr.

    The command lands in the planner's input queue and executes when its current turn
    ends (observed on Claude Code 2026-09-07). Never raises: the result says whether it
    was queued and why not. Mutates data['compact_queued'] on success.
    """
    pane = str(data.get("planner_pane") or "")
    already = data.get("compact_queued") or {}
    epoch = int(data.get("compaction_epoch", 0))
    if not force and already and already.get("compaction_epoch", epoch) == epoch:
        return {"queued": False, "reason": "already queued for this compaction epoch", "previous": already}
    if not pane:
        return {"queued": False, "reason": "planner_pane unknown; run init --planner-pane"}
    if not force and not auto_compact_enabled(data):
        return {
            "queued": False,
            "reason": "auto compact disabled (init --no-auto-compact or PAIRCTL_AUTO_COMPACT=0)",
        }
    try:
        info = agent_info(args, pane)
    except ValueError as exc:
        return {"queued": False, "reason": f"cannot inspect planner pane {pane}: {exc}"}
    kind = str(info.get("agent") or "")
    table = FRESH_COMMANDS if mode == "clear" else COMPACT_COMMANDS
    command = table.get(kind)
    if not command:
        return {"queued": False, "reason": f"no {mode} command known for agent kind {kind!r}", "pane": pane}
    text = command
    if mode == "compact" and kind in COMPACT_ACCEPTS_INSTRUCTIONS:
        text = f"{command} {compact_instructions(pp, data, kind)}"
    # issue #9: the mechanism is decided only where a fresh record would be written
    # with auto-continue on, and it must be probed before the compact prompt so that
    # prompt stays pairctl's last Herdr call in this queue operation.
    auto_continue = continue_after_compact_enabled()
    existing = data.get("resume_pending")
    same_epoch_requeue = False
    if isinstance(existing, dict) and existing:
        try:
            same_epoch_requeue = int(existing.get("epoch_at_arm", -1)) == int(epoch)
        except (TypeError, ValueError):
            same_epoch_requeue = False
    newly_armed = auto_continue and not same_epoch_requeue
    mechanism = probe_resume_mechanism(args, kind) if newly_armed else ""
    try:
        code, out, err, payload = run_herdr(args, ["agent", "prompt", pane, text])
    except ValueError as exc:
        return {"queued": False, "reason": str(exc), "pane": pane}
    if code != 0 or not is_agent_prompted(payload):
        return {
            "queued": False,
            "reason": herdr_error_code(payload) or "unknown_response",
            "pane": pane,
            "herdr_exit": code,
            "herdr_stdout": clip(out, 500),
            "herdr_stderr": clip(err, 500),
        }
    record = {
        "phase": data["phase"],
        "compaction_epoch": epoch,
        "pane": pane,
        "kind": kind,
        "mode": mode,
        "command": command,
        "queued_at": now(),
    }
    data["compact_queued"] = record
    # PAIRCTL_CONTINUE_AFTER_COMPACT=0 disables auto-continue: the compact still
    # queues, but no resume_pending record is written (issue #9).
    if auto_continue:
        arm_resume_record(pp, data, pane, epoch, mechanism)
    return {
        "queued": True,
        **record,
        "resume_armed": newly_armed,
        "note": "queued in the planner pane; it executes when the current turn ends",
    }


def arm_resume_record(
    pp: dict[str, Path], data: dict[str, Any], pane: str, epoch: int, mechanism: str = "watcher",
) -> dict[str, Any]:
    """Arm the resume owed to the planner once the compaction epoch advances.

    Written exactly where compact_queued is set, so every arm path (finish-round,
    emit_rollover_block, compact-self, budget compaction) gets one record.

    Same epoch_at_arm requeue (issue #9): keep the record — armed_at, mechanism,
    status, attempts, last_error all stay — and push only the deadline out to now +
    PAIRCTL_RESUME_DEADLINE_S. A different epoch, or no record at all, replaces it
    with a fresh pending record carrying the probed mechanism.
    """
    deadline_s = env_float("PAIRCTL_RESUME_DEADLINE_S", DEFAULT_RESUME_DEADLINE_S)
    stamp = now()
    deadline = (dt.datetime.fromisoformat(stamp) + dt.timedelta(seconds=deadline_s)).isoformat()
    existing = data.get("resume_pending")
    if isinstance(existing, dict) and existing:
        try:
            same_epoch = int(existing.get("epoch_at_arm", -1)) == int(epoch)
        except (TypeError, ValueError):
            same_epoch = False
        if same_epoch:
            # The deadline is recorded here but not consumed by pairctl itself: the
            # low-frequency wake-up watcher reads it to decide when a still-pending
            # resume is due for claim-delivery.
            existing["deadline"] = deadline
            return existing
    record = {
        "armed_at": stamp,
        "epoch_at_arm": int(epoch),
        "planner_pane": pane,
        "state_dir": str(pp["root"]),
        "mechanism": mechanism if mechanism in {"plugin", "watcher"} else "watcher",
        "deadline": deadline,
        "status": "pending",
        "attempts": 0,
        "last_error": "",
    }
    data["resume_pending"] = record
    return record


def resume_claim_gate(data: dict[str, Any], record: dict[str, Any]) -> str:
    """'' when a resume record may be claimed, else the rejection reason.

    Shared by cancel_resume_record (planner-side progress) and cmd_resume_deliver
    (wake-up source): both need the compaction epoch to have advanced past arming
    and a non-terminal status. Epoch is checked first, matching resume-deliver's
    documented rejection order (epoch_not_advanced before not_claimable).
    """
    if int(data.get("compaction_epoch", 0)) <= int(record.get("epoch_at_arm", 0)):
        return "epoch_not_advanced"
    if str(record.get("status") or "") not in RESUME_CLAIMABLE:
        return "not_claimable"
    return ""


def cancel_resume_record(data: dict[str, Any], reason: str) -> bool:
    """Planner-side progress (a round, an adopted contract, a session rollover) made the
    armed resume unnecessary. Fires only while the shared claim gate passes, so records
    in a terminal state are never overwritten."""
    record = data.get("resume_pending")
    if not isinstance(record, dict) or not record:
        return False
    if resume_claim_gate(data, record):
        return False
    record["status"] = "cancelled"
    record["cancel_reason"] = reason
    record["cancelled_at"] = now()
    return True


def compact_pending(data: dict[str, Any]) -> bool:
    queued = data.get("compact_queued") or {}
    return bool(queued and int(queued.get("compaction_epoch", data.get("compaction_epoch", 0))) == int(data.get("compaction_epoch", 0)))


def compact_pending_payload(pp: dict[str, Path], data: dict[str, Any]) -> dict[str, Any]:
    queued = data.get("compact_queued") or {}
    return {
        "status": "CONTEXT_COMPACT_QUEUED", "trigger": "CONTEXT_COMPACT_QUEUED",
        "checkpoint": str(pp["checkpoint"]), "planner_compact": {"queued": False, "previous": queued},
        "compaction_epoch": data.get("compaction_epoch", 0),
        "guidance": "Planner compaction is queued and must be consumed before dispatching another round.",
    }


def parse_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def is_agent_prompted(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    if payload.get("type") == "agent_prompted":
        return True
    result = payload.get("result")
    if isinstance(result, dict) and not result.get("error"):
        return result.get("type") == "agent_prompted"
    return False


def herdr_error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "")
    result = payload.get("result")
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "")
        if result.get("type") == "agent_prompt_stalled":
            return "agent_prompt_stalled"
    if payload.get("type") == "agent_prompt_stalled":
        return "agent_prompt_stalled"
    return ""


def clip(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def queue_budget_compact(
    args: argparse.Namespace, pp: dict[str, Path], data: dict[str, Any], reason: str,
) -> dict[str, Any]:
    """Persist recoverable state before requesting the planner's context refresh."""
    write_checkpoint(pp["checkpoint"], data, reason)
    persist(pp, data)
    result = queue_planner_compact(args, pp, data)
    if result.get("queued"):
        write_checkpoint(pp["checkpoint"], data, reason)
    return result


def cmd_init(args: argparse.Namespace) -> int:
    if args.context_budget is not None and args.context_budget <= 0:
        raise ValueError("--context-budget must be positive")
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"], create=True):
        if pp["state"].exists():
            data = load_ready(pp, cwd)
            # Existing rounds and jobs are never discarded. There is no --reset.
            if args.planner_pane:
                data["planner_pane"] = args.planner_pane
            if args.session_id:
                data["session_id"] = args.session_id
            if args.no_auto_compact:
                data["auto_compact"] = False
            if args.goal:
                data["goal"] = args.goal
            if args.context_budget is not None:
                data["context_budget"] = args.context_budget
        else:
            data = {
                "version": VERSION,
                "cwd": cwd,
                "planner_pane": args.planner_pane or "",
                "session_id": args.session_id or "",
                "auto_compact": not args.no_auto_compact,
                "goal": args.goal or "",
                "context_budget": args.context_budget or budget_for(args),
                "last_context_usage": None,
                "notes": [],
                "phase": 1,
                "round_seq": 0,
                "phase_round_count": 0,
                "rollover_required": False,
                "compaction_epoch": 0,
                "compact_queued": None,
                "resume_pending": None,
                "rounds": [],
                "jobs": [],
                "pending_dispatch": None,
                "notices": [],
                "created_at": now(),
            }
        persist(pp, data)
        usage = read_context_usage(
            pp, cwd, budget_for(args, data), env_float("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS), data
        )
        data["last_context_usage"] = usage
        planner_compact = None
        if (
            not args.no_context_check and auto_compact_enabled(data)
            and (usage.get("over_budget") or usage.get("stale"))
        ):
            planner_compact = queue_budget_compact(args, pp, data, "init_context_budget")
        persist(pp, data)
        # Pane index, write point 1: init's --planner-pane.
        record_pane(args.planner_pane or "", str(pp["root"]), cwd, "planner")
    continue_after = spawn_compact_continue_watcher(args, pp, data, planner_compact) if planner_compact else None
    payload = {
        "status": "CONTEXT_COMPACT_QUEUED" if planner_compact and planner_compact.get("queued") else "initialized",
        "state": str(pp["state"]),
        "ledger": str(pp["ledger"]),
        "state_root": str(pp["root"]),
        "context_usage": usage,
    }
    if planner_compact:
        payload.update({"planner_compact": planner_compact, "checkpoint": str(pp["checkpoint"]), "continue_after_compact": continue_after})
    output(payload)
    return 0


def cmd_note_session(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    record = {
        "cwd": cwd, "session_id": args.session_id, "transcript_path": args.transcript_path or "",
        "kind": args.kind, "source": args.source, "pane": args.pane or "", "recorded_at": now(),
    }
    with locked(pp["root"], pp["lock"], create=True):
        if pp["state"].is_file():
            data = load(pp["state"], cwd)
            planner_pane = str(data.get("planner_pane") or "")
            if planner_pane and not args.pane:
                output({"status": "session_ignored", "reason": "pane_unknown"})
                return 0
            if planner_pane and args.pane != planner_pane:
                output({"status": "session_ignored", "reason": "non_planner_pane"})
                return 0
        if not args.pane and pp["planner_session"].is_file():
            previous = json.loads(pp["planner_session"].read_text(encoding="utf-8"))
            if previous.get("pane"):
                output({"status": "session_ignored", "reason": "pane_unknown"})
                return 0
        atomic_text(pp["planner_session"], json.dumps(record, indent=2, sort_keys=True) + "\n")
        # Pane index, write point 2: note-session's --pane.
        record_pane(args.pane or "", str(pp["root"]), cwd, "planner")
    output({"status": "session_noted", "planner_session": str(pp["planner_session"])})
    return 0


def cmd_context_usage(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    data = None
    if pp["state"].is_file():
        with locked(pp["root"], pp["lock"]):
            data = load(pp["state"], cwd)
    usage = read_context_usage(
        pp, cwd, budget_for(args, data), env_float("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS), data
    )
    if data is not None:
        with locked(pp["root"], pp["lock"]):
            current = load(pp["state"], cwd)
            current["last_context_usage"] = usage
            persist(pp, current)
    output(usage)
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        data.setdefault("notes", []).append({"created_at": now(), "text": args.text})
        write_checkpoint(pp["checkpoint"], data, "decision noted")
        persist(pp, data)
    output({"status": "note_added", "checkpoint": str(pp["checkpoint"])})
    return 0


def cmd_start_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    handoff = Path(args.file).expanduser()
    if not handoff.is_file():
        raise ValueError(f"contract file not found: {handoff}")
    try:
        source_text = handoff.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"contract file is not valid UTF-8: {handoff}") from exc
    if not source_text.strip():
        raise ValueError("contract file is empty")
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        if data["rollover_required"]:
            return emit_rollover_block(pp, data, "round limit reached", args)
        if compact_pending(data):
            output(compact_pending_payload(pp, data))
            return ROLLOVER_EXIT
        active = active_round_ids(data)
        if active:
            raise ValueError(f"active round already exists: {active[0]}")
        findings = [] if args.skip_lint else handoff_lint(cwd, source_text)
        if findings:
            output({"status": "rejected", "reason": "handoff_lint", "findings": findings})
            return 2
        round_id = allocate_round_id(data)
        contract_text = inject_round_id(source_text, round_id)
        snapshot, warnings = snapshot_round(pp, cwd, round_id, 1, source_text)
        contract_path, contract_hash = save_contract(pp, round_id, 1, contract_text)
        commit_round(
            data,
            round_id=round_id,
            executor=args.executor,
            scope=args.scope,
            acceptance=args.acceptance,
            revision=1,
            contract_path=contract_path,
            contract_hash=contract_hash,
            dispatch_status="delivered",
            work_status="pending_acceptance",
            snapshot=snapshot,
            skip_lint=args.skip_lint or "",
            report=parse_report_path(source_text, cwd),
        )
        after_round_checkpoint(pp, data)
        persist(pp, data)
    payload = {
        "status": "round_started",
        "round_id": round_id,
        "revision": 1,
        "dispatch_status": "delivered",
        "work_status": "pending_acceptance",
        "contract_hash": contract_hash,
        "contract_path": contract_path,
        "snapshot": snapshot,
        "warnings": warnings,
    }
    if data["rollover_required"]:
        payload["after_round"] = "SESSION_ROLLOVER_REQUIRED"
    output(payload)
    return 0


def cmd_send_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    handoff = Path(args.file).expanduser()
    if not handoff.is_file():
        raise ValueError(f"handoff file not found: {handoff}")
    text = handoff.read_text(encoding="utf-8")
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        # Issue #12: a pane that exited during a round must not be freshened or
        # prompted into a dead target. Checked before any dispatch bookkeeping so
        # a refusal consumes no round and leaves pending_dispatch untouched.
        target = str(args.target or "").strip()
        for item in data.get("rounds", []):
            if (
                str(item.get("executor") or "").strip() == target
                and item.get("executor_pane_gone_at")
            ):
                raise ValueError(
                    "executor_pane_gone: "
                    f"pane {target} exited at {item['executor_pane_gone_at']} "
                    f"during round {item.get('round_id')}; re-dispatch to a live pane"
                )
        if data["rollover_required"]:
            return emit_rollover_block(pp, data, "round limit reached", args)
        if compact_pending(data):
            output(compact_pending_payload(pp, data))
            return ROLLOVER_EXIT
        active = active_round_ids(data)
        if active:
            raise ValueError(f"active round already exists: {active[0]}")
        findings = [] if args.skip_lint else handoff_lint(cwd, text)
        if findings:
            output({"status": "rejected", "reason": "handoff_lint", "findings": findings})
            return 2
        round_id = allocate_round_id(data)
        message = inject_round_id(text, round_id)
        encoded = message.encode("utf-8")
        if len(encoded) > MAX_ARG_BYTES:
            raise ValueError(
                f"handoff is {len(encoded)} bytes; argv limit is {MAX_ARG_BYTES}. "
                "Send a file pointer instead of inlining the payload."
            )
        fresh: dict[str, Any] | None = None
        if args.fresh:
            # Clear the executor's context before the handoff lands. This happens before
            # pending_dispatch exists, so a failure here consumes nothing and resends nothing.
            try:
                fresh = freshen_executor(args, args.target)
            except ValueError as exc:
                output({
                    "status": "fresh_failed",
                    "round_consumed": False,
                    "round_id": round_id,
                    "target": args.target,
                    "error": str(exc),
                    "guidance": FRESH_GUIDANCE,
                })
                return 2
        snapshot, warnings = snapshot_round(pp, cwd, round_id, 1, text)
        contract_path, contract_hash = save_contract(pp, round_id, 1, message)
        argv = [herdr_bin(args), "agent", "prompt", args.target, message]
        data["pending_dispatch"] = {
            "status": "uncertain",
            "counted": False,
            "round_id": round_id,
            "target": args.target,
            "executor": args.executor or args.target,
            "scope": args.scope,
            "acceptance": args.acceptance,
            "handoff": str(handoff),
            "revision": 1,
            "contract_path": contract_path,
            "contract_hash": contract_hash,
            "fresh": fresh,
            "snapshot": snapshot,
            "skip_lint": args.skip_lint or "",
            "report": parse_report_path(text, cwd),
            "phase_round_count": data["phase_round_count"],
            "created_at": now(),
        }
        persist(pp, data)
        try:
            proc = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=args.send_timeout,
            )
        except OSError as exc:
            data["pending_dispatch"] = None
            persist(pp, data)
            output({
                "status": "send_failed",
                "round_consumed": False,
                "round_id": round_id,
                "error": str(exc),
            })
            return 2
        except subprocess.TimeoutExpired as exc:
            result = pending_payload(data["pending_dispatch"])
            result.update({
                "error": "herdr_timeout",
                "send_timeout": args.send_timeout,
                "herdr_stdout": clip(exc.stdout or ""),
                "herdr_stderr": clip(exc.stderr or ""),
            })
            output(result)
            return 2
        payload = parse_json_payload(proc.stdout)
        prompted = proc.returncode == 0 and is_agent_prompted(payload)
        if not prompted:
            code = herdr_error_code(payload)
            # agent_not_found is a definitive pre-delivery failure. A stalled,
            # malformed or unknown response is ambiguous and must not be resent.
            if code == "agent_not_found":
                data["pending_dispatch"] = None
                persist(pp, data)
                output({
                    "status": "send_failed",
                    "round_consumed": False,
                    "round_id": round_id,
                    "herdr_error": code,
                    "herdr_exit": proc.returncode,
                    "herdr_stdout": clip(proc.stdout),
                    "herdr_stderr": clip(proc.stderr),
                })
            else:
                result = pending_payload(data["pending_dispatch"])
                result.update({
                    "herdr_error": code or "unknown_response",
                    "herdr_exit": proc.returncode,
                    "herdr_stdout": clip(proc.stdout),
                    "herdr_stderr": clip(proc.stderr),
                })
                output(result)
            return 2
        data["pending_dispatch"] = None
        commit_round(
            data,
            round_id=round_id,
            executor=args.executor or args.target,
            scope=args.scope,
            acceptance=args.acceptance,
            fresh=fresh,
            revision=1,
            contract_path=contract_path,
            contract_hash=contract_hash,
            dispatch_status="delivered",
            work_status="pending_acceptance",
            snapshot=snapshot,
            skip_lint=args.skip_lint or "",
            report=parse_report_path(text, cwd),
        )
        # The planner dispatched a new round itself: the armed resume is obsolete.
        cancel_resume_record(data, "send_round")
        after_round_checkpoint(pp, data)
        planner_compact = None
        # A delivered round must be durable before any further Herdr call can fail.
        persist(pp, data)
        usage = read_context_usage(
            pp, cwd, budget_for(args, data), env_float("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS), data
        )
        data["last_context_usage"] = usage
        if usage.get("status") == "ok" and usage.get("over_budget") and auto_compact_enabled(data):
            planner_compact = queue_budget_compact(args, pp, data, "post_dispatch_budget")
        persist(pp, data)
        # Pane index, write point 3: send-round's --target, bound as the executor.
        record_pane(args.target, str(pp["root"]), cwd, "executor")
    continue_after = spawn_compact_continue_watcher(args, pp, data, planner_compact) if planner_compact else None
    result = {
        "status": "round_sent",
        "round_id": round_id,
        "round_consumed": True,
        "target": args.target,
        "agent_prompted": True,
        "fresh": fresh,
        "revision": 1,
        "dispatch_status": "delivered",
        "work_status": "pending_acceptance",
        "contract_hash": contract_hash,
        "contract_path": contract_path,
        "report": parse_report_path(text, cwd),
        "snapshot": snapshot,
        "warnings": warnings,
        "context_usage": usage,
    }
    if planner_compact:
        result["planner_compact"] = planner_compact
        result["continue_after_compact"] = continue_after
    if data["phase_round_count"] == 3:
        result["checkpoint"] = str(pp["checkpoint"])
        result["checkpoint_reason"] = "automatic three-round checkpoint"
    if data["rollover_required"]:
        result["after_round"] = "SESSION_ROLLOVER_REQUIRED"
    output(result)
    return 0


def cmd_prepare_round(args: argparse.Namespace) -> int:
    """Register a finished handoff for the `pair.dispatch-prepared` action (issue #7).

    Only the file path is recorded, in state.json's `prepared_round`: nothing is
    sent, linted, freshened or dispatched here. That keeps free-text dispatch out
    of the plugin (the action may send exactly this one file) and leaves send-round
    as the single dispatch path with its fence lint, target checks and pending
    bookkeeping unchanged. Registering again replaces the previous file; the
    registration is never consumed implicitly.
    """
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    handoff = Path(args.file).expanduser()
    if not handoff.is_file():
        raise ValueError(f"handoff file not found: {handoff}")
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        write_ledger(pp["ledger"], data)
        record = {"handoff": str(handoff), "registered_at": now()}
        data["prepared_round"] = record
        persist(pp, data)
    output({
        "status": "round_prepared",
        "handoff": record["handoff"],
        "registered_at": record["registered_at"],
        "sent": False,
        "guidance": (
            "Handoff registered only; pair.dispatch-prepared sends exactly this "
            "file through send-round while the planner pane holds the focus."
        ),
    })
    return 0


def cmd_ack_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        round_item = None
        for r in data["rounds"]:
            if r.get("round_id") == args.round_id:
                round_item = r
                break
        if not round_item:
            output({"status": "rejected", "reason": "round_not_found", "round_id": args.round_id})
            return 2
        if round_item.get("status") != "active":
            output({"status": "rejected", "reason": "round_terminal", "round_id": args.round_id})
            return 2

        if round_item.get("work_status") == "unconfirmed_protocol":
            output({"status": "rejected", "reason": "unconfirmed_protocol", "round_id": args.round_id})
            return 2

        # Check pane
        if (args.pane or "").strip() != (round_item.get("executor") or "").strip():
            output({"status": "rejected", "reason": "pane_mismatch", "pane": args.pane})
            return 2

        # Check revision
        current_rev = round_item.get("current_revision", 1)
        if args.revision != current_rev:
            output({"status": "rejected", "reason": "revision_mismatch", "revision": args.revision, "current_revision": current_rev})
            return 2

        # Check scope
        expected_scope = (round_item.get("scope") or "").strip()
        if (args.scope or "").strip() != expected_scope:
            output({"status": "rejected", "reason": "scope_mismatch", "scope": args.scope, "expected_scope": expected_scope})
            return 2

        # Check contract validity on disk
        is_valid, contract_err = verify_round_contract(round_item, args.revision)
        if not is_valid:
            output({"status": "rejected", "reason": contract_err, "round_id": args.round_id})
            return 2

        # Check hash against expected hash
        rev_info = (round_item.get("revisions") or {}).get(str(args.revision)) or {}
        expected_hash = rev_info.get("contract_hash") or ""
        if (args.contract_hash or "").strip() != expected_hash:
            output({"status": "rejected", "reason": "hash_mismatch", "contract_hash": args.contract_hash, "expected_hash": expected_hash})
            return 2

        current_work = round_item.get("work_status") or "pending_acceptance"

        # Action: accept
        if args.action == "accept":
            receipt_entry = {
                "action": "accept",
                "revision": args.revision,
                "pane": args.pane,
                "scope": args.scope,
                "contract_hash": args.contract_hash,
                "at": now(),
            }
            if current_work == "pending_acceptance":
                round_item["work_status"] = "accepted"
                round_item.setdefault("receipts", []).append(receipt_entry)
                persist(pp, data)
                output({
                    "status": "receipt_recorded",
                    "action": "accept",
                    "round_id": args.round_id,
                    "revision": args.revision,
                    "work_status": "accepted",
                })
                return 0
            elif current_work in ("accepted", "running"):
                output({
                    "status": "receipt_recorded",
                    "action": "accept",
                    "round_id": args.round_id,
                    "revision": args.revision,
                    "work_status": current_work,
                    "idempotent": True,
                })
                return 0
            else:
                output({"status": "rejected", "reason": f"invalid_state_{current_work}", "round_id": args.round_id})
                return 2

        # Action: start
        elif args.action == "start":
            if current_work == "pending_acceptance":
                output({"status": "rejected", "reason": "not_accepted", "round_id": args.round_id})
                return 2
            receipt_entry = {
                "action": "start",
                "revision": args.revision,
                "pane": args.pane,
                "scope": args.scope,
                "contract_hash": args.contract_hash,
                "at": now(),
            }
            if current_work == "accepted":
                round_item["work_status"] = "running"
                round_item.setdefault("receipts", []).append(receipt_entry)
                persist(pp, data)
                output({
                    "status": "receipt_recorded",
                    "action": "start",
                    "round_id": args.round_id,
                    "revision": args.revision,
                    "work_status": "running",
                })
                return 0
            elif current_work == "running":
                output({
                    "status": "receipt_recorded",
                    "action": "start",
                    "round_id": args.round_id,
                    "revision": args.revision,
                    "work_status": "running",
                    "idempotent": True,
                })
                return 0
            else:
                output({"status": "rejected", "reason": f"invalid_state_{current_work}", "round_id": args.round_id})
                return 2
        else:
            output({"status": "rejected", "reason": "invalid_action", "action": args.action})
            return 2


def cmd_check_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        round_item = None
        for r in data["rounds"]:
            if r.get("round_id") == args.round_id:
                round_item = r
                break
        if not round_item:
            output({"allowed": False, "reason": "round_not_found", "round_id": args.round_id})
            return 2
        if round_item.get("status") != "active":
            output({"allowed": False, "reason": "round_terminal", "round_id": args.round_id})
            return 2

        current_rev = round_item.get("current_revision", 1)
        current_work = round_item.get("work_status") or "pending_acceptance"

        if current_work == "unconfirmed_protocol":
            output({"allowed": False, "reason": "unconfirmed_protocol", "round_id": args.round_id, "work_status": current_work})
            return 2

        # Check pane
        if (args.pane or "").strip() != (round_item.get("executor") or "").strip():
            output({"allowed": False, "reason": "pane_mismatch", "pane": args.pane, "round_id": args.round_id})
            return 2

        # Omitting the revision is a read-only discovery query. It still verifies
        # the assigned pane and authoritative snapshot, but can never authorize work.
        query_only = args.revision is None
        claimed_revision = current_rev if query_only else args.revision

        # Check revision
        if claimed_revision < current_rev:
            output({"allowed": False, "reason": "superseded_revision", "revision": claimed_revision, "current_revision": current_rev})
            return 2
        elif claimed_revision > current_rev:
            output({"allowed": False, "reason": "unknown_future_revision", "revision": claimed_revision, "current_revision": current_rev})
            return 2

        contract, contract_err = verified_contract_payload(round_item, claimed_revision)
        if contract is None:
            output({"allowed": False, "reason": contract_err, "round_id": args.round_id, "revision": claimed_revision})
            return 2

        base = {
            "allowed": False,
            "round_id": args.round_id,
            "current_revision": current_rev,
            "work_status": current_work,
            **contract,
        }
        if query_only:
            output({**base, "reason": "missing_revision_query_only"})
            return 2

        # Check work status
        if current_work == "pending_acceptance":
            output({**base, "reason": "not_accepted"})
            return 2
        elif current_work == "accepted":
            output({**base, "reason": "not_running"})
            return 2
        elif current_work == "unconfirmed_protocol":
            output({"allowed": False, "reason": "unconfirmed_protocol", "round_id": args.round_id, "work_status": current_work})
            return 2
        elif current_work == "running":
            receipts = round_item.get("receipts") or []
            has_start = any(
                rc.get("action") == "start" and rc.get("revision") == args.revision
                for rc in receipts
            )
            if not has_start:
                output({"allowed": False, "reason": "not_running", "round_id": args.round_id, "work_status": current_work})
                return 2

            output({
                **contract,
                "allowed": True,
                "reason": "ok",
                "round_id": args.round_id,
                "revision": args.revision,
                "work_status": "running",
            })
            return 0
        else:
            output({"allowed": False, "reason": f"invalid_state_{current_work}", "round_id": args.round_id})
            return 2


def cmd_adopt_contract(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    handoff = Path(args.file).expanduser()
    if not handoff.is_file():
        output({"status": "rejected", "reason": "contract_file_not_found", "file": str(handoff)})
        return 2
    contract_text = handoff.read_text(encoding="utf-8")
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        round_item = None
        for r in data["rounds"]:
            if r.get("round_id") == args.round_id:
                round_item = r
                break
        if not round_item:
            output({"status": "rejected", "reason": "round_not_found", "round_id": args.round_id})
            return 2
        if round_item.get("status") != "active":
            output({"status": "rejected", "reason": "round_terminal", "round_id": args.round_id})
            return 2
        if (
            round_item.get("work_status") != "unconfirmed_protocol"
            or round_item.get("current_revision") != 0
        ):
            output({"status": "rejected", "reason": "already_bound", "work_status": round_item.get("work_status")})
            return 2

        scope = args.scope or round_item.get("scope") or ""
        acceptance = args.acceptance or round_item.get("acceptance") or ""

        # Save authoritative contract as revision 1
        contract_path, contract_hash = save_contract(pp, args.round_id, 1, contract_text)

        round_item["current_revision"] = 1
        round_item["scope"] = scope
        round_item["acceptance"] = acceptance
        round_item["work_status"] = "pending_acceptance"
        round_item["dispatch_status"] = "delivered"
        round_item.setdefault("revisions", {})["1"] = {
            "revision": 1,
            "scope": scope,
            "acceptance": acceptance,
            "contract_hash": contract_hash,
            "contract_path": contract_path,
            "created_at": now(),
        }
        # Binding a contract is planner-side progress: the armed resume is obsolete.
        cancel_resume_record(data, "adopt_contract")
        persist(pp, data)

    output({
        "status": "contract_adopted",
        "round_id": args.round_id,
        "revision": 1,
        "work_status": "pending_acceptance",
        "contract_hash": contract_hash,
        "contract_path": contract_path,
        "scope": scope,
    })
    return 0


def cmd_resolve_pending(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        # Repair the derived ledger, but intentionally bypass load_ready because
        # this command exists to resolve pending_dispatch.
        write_ledger(pp["ledger"], data)
        pending = data.get("pending_dispatch")
        if not pending:
            raise ValueError("no pending_dispatch to resolve")
        round_id = str(pending.get("round_id") or "")
        target = str(pending.get("target") or "")
        if args.outcome == "delivered":
            active = active_round_ids(data)
            if active:
                raise ValueError(f"active round already exists: {active[0]}")
            legacy_pending = bool(pending.get("legacy_unconfirmed_protocol")) or (
                "revision" not in pending
                and "contract_path" not in pending
                and "contract_hash" not in pending
            )
            revision = 0 if legacy_pending else int(pending.get("revision") or 1)
            contract_path = str(pending.get("contract_path") or "")
            contract_hash = str(pending.get("contract_hash") or "")
            pending_contract = {
                "executor": pending.get("executor") or target,
                "scope": pending.get("scope") or "",
                "acceptance": pending.get("acceptance") or "",
                "revisions": {str(revision): {
                    "contract_path": contract_path,
                    "contract_hash": contract_hash,
                    "scope": pending.get("scope") or "",
                    "acceptance": pending.get("acceptance") or "",
                }},
            }
            verified, contract_err = verified_contract_payload(pending_contract, revision)
            if not legacy_pending and verified is None:
                output({
                    "status": "rejected",
                    "reason": contract_err,
                    "round_id": round_id,
                    "target": target,
                    "round_consumed": False,
                })
                return 2
            commit_round(
                data,
                round_id=round_id,
                executor=str(pending.get("executor") or target),
                scope=str(pending.get("scope") or ""),
                acceptance=str(pending.get("acceptance") or ""),
                fresh=pending.get("fresh"),
                revision=revision,
                contract_path=contract_path,
                contract_hash=contract_hash,
                dispatch_status="delivered",
                work_status="unconfirmed_protocol" if legacy_pending else "pending_acceptance",
                snapshot=pending.get("snapshot"),
                skip_lint=str(pending.get("skip_lint") or ""),
                report=str(pending.get("report") or ""),
            )
            data["pending_dispatch"] = None
            after_round_checkpoint(pp, data)
            persist(pp, data)
            output({
                "status": "pending_resolved_delivered",
                "round_id": round_id,
                "target": target,
                "round_consumed": True,
                "revision": revision,
                "dispatch_status": "delivered",
                "work_status": "unconfirmed_protocol" if legacy_pending else "pending_acceptance",
                "contract_hash": contract_hash,
            })
            return 0
        data["pending_dispatch"] = None
        persist(pp, data)
        output({
            "status": "pending_resolved_not_delivered",
            "round_id": round_id,
            "target": target,
            "round_consumed": False,
        })
        return 0


def cmd_finish_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        item = find_round(data, args.round_id)
        if item["status"] != "active":
            raise ValueError(f"round is not active: {args.round_id} ({item['status']})")
        if args.report:
            report_path = Path(args.report).expanduser()
            if not report_path.is_absolute():
                report_path = Path(cwd) / report_path
            if not report_path.exists():
                output({"status": "rejected", "reason": "report_missing", "report": str(report_path)})
                return 2
        else:
            report_path = Path(str(item.get("report") or "")) if item.get("report") else None
        if args.status == "accepted" and (not args.artifacts.strip() or not args.notes.strip()):
            output({
                "status": "rejected", "reason": "missing_acceptance_evidence",
                "missing": [name for name, value in (("artifacts", args.artifacts), ("notes", args.notes)) if not value.strip()],
            })
            return 2
        item.update({
            "status": args.status,
            "work_status": "finished",
            "finished_at": now(),
            "artifacts": args.artifacts,
            "notes": args.notes,
            "report": str(report_path) if report_path else str(item.get("report") or ""),
        })
        if data["phase_round_count"] >= 5:
            data["rollover_required"] = True
        planner_compact = None
        if data["rollover_required"]:
            # The phase is over and no round is active: queue the planner's own compaction
            # now, so it runs as soon as the planner's current turn ends.
            planner_compact = queue_planner_compact(args, pp, data)
        reason = "round completed"
        if data["rollover_required"]:
            reason = "five-round limit reached"
        write_checkpoint(pp["checkpoint"], data, reason)
        persist(pp, data)
    if data["rollover_required"]:
        continue_after = spawn_compact_continue_watcher(args, pp, data, planner_compact)
        output({
            "status": "round_finished",
            "round_id": args.round_id,
            "trigger": "SESSION_ROLLOVER_REQUIRED",
            "checkpoint": str(pp["checkpoint"]),
            "session_switched": False,
            "planner_compact": planner_compact,
            "continue_after_compact": continue_after,
            "guidance": ROLLOVER_GUIDANCE,
            "next": ROLLOVER_GUIDANCE,
        })
        return ROLLOVER_EXIT
    output({"status": "round_finished", "round_id": args.round_id})
    return 0


def cmd_diff_round(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        item = find_round(data, args.round_id)
        revision = args.revision or int(item.get("current_revision", 1))
        if revision != int(item.get("current_revision", 1)):
            rev_snapshot = ((item.get("revisions") or {}).get(str(revision)) or {}).get("snapshot")
            snapshot = rev_snapshot
        else:
            snapshot = item.get("snapshot")
        if not snapshot or not Path(str(snapshot.get("manifest") or "")).is_file():
            raise ValueError(f"snapshot missing for {args.round_id} revision {revision}")
        manifest = json.loads(Path(snapshot["manifest"]).read_text(encoding="utf-8"))
    original = {record["path"]: record for record in manifest}
    current: dict[str, dict[str, Any]] = {}
    cwd_path = Path(cwd)
    for root_value in snapshot.get("roots") or list(original):
        candidate = (cwd_path / root_value).resolve()
        try:
            candidate.relative_to(cwd_path)
        except ValueError:
            continue
        paths_now = sorted(path for path in candidate.rglob("*") if path.is_file()) if candidate.is_dir() else ([candidate] if candidate.is_file() else [])
        for path in paths_now:
            rel = path.relative_to(cwd_path).as_posix()
            current[rel] = {"sha256": file_sha256(path)}
    changed = sorted(path for path in original if original[path].get("exists") and path in current and original[path].get("sha256") != current[path]["sha256"])
    removed = sorted(path for path in original if original[path].get("exists") and not original[path].get("directory") and path not in current)
    added = sorted(path for path in current if path not in original or not original[path].get("exists"))
    unchanged = sorted(path for path in original if original[path].get("exists") and path in current and original[path].get("sha256") == current[path]["sha256"])
    output({"round_id": args.round_id, "revision": revision, "changed": changed, "added": added, "removed": removed, "unchanged": unchanged})
    return 1 if changed or added or removed else 0


def cmd_job_add(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        item = find_round(data, args.round_id)
        if any(j["queue"] == args.queue and j["job_id"] == args.job_id for j in data["jobs"]):
            raise ValueError(f"job already exists: {args.queue}/{args.job_id}")
        job = {
            "round_id": args.round_id,
            "phase": item["phase"],
            "queue": args.queue,
            "job_id": args.job_id,
            "label": args.label,
            "submitter": args.submitter,
            "command": args.command,
            "log": args.log,
            "expected_artifacts": args.expected_artifacts,
            "completion_assertion": args.completion_assertion,
            "state": args.state,
            "cancel_retry_owner": args.owner,
            "created_at": now(),
            "updated_at": now(),
        }
        data["jobs"].append(job)
        if data["phase_round_count"] >= 3:
            write_checkpoint(pp["checkpoint"], data, "background job added")
        persist(pp, data)
    output({"status": "job_added", "job": f"{args.queue}/{args.job_id}", "ledger": str(pp["ledger"])})
    return 0


def cmd_job_update(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        found = [j for j in data["jobs"] if j["queue"] == args.queue and j["job_id"] == args.job_id]
        if len(found) != 1:
            raise ValueError(f"unknown job: {args.queue}/{args.job_id}")
        job = found[0]
        job["state"] = args.state
        job["updated_at"] = now()
        if args.log:
            job["log"] = args.log
        if args.notes:
            job["notes"] = args.notes
        if data["phase_round_count"] >= 3 or data["rollover_required"]:
            write_checkpoint(pp["checkpoint"], data, "background job updated")
        persist(pp, data)
    output({"status": "job_updated", "job": f"{args.queue}/{args.job_id}", "state": args.state})
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        write_checkpoint(pp["checkpoint"], data, args.reason)
        persist(pp, data)
    output({"status": "checkpoint_written", "checkpoint": str(pp["checkpoint"])})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        write_ledger(pp["ledger"], data)
        check_dispatch_stale(args, pp, data)
        if data.get("pending_dispatch"):
            raise PendingDispatchError(
                data["pending_dispatch"], data.get("notices", []), board_fields(data)
            )
    active_jobs = [j for j in data["jobs"] if j["state"] not in TERMINAL_JOBS]
    active_ids = active_round_ids(data)
    dispatch_status = "idle"
    work_status = "idle"
    current_revision = None
    contract_hash = ""
    contract_path = ""
    contract_status = "none"
    contract_valid = False
    report = ""
    if active_ids:
        active_round = find_round(data, active_ids[0])
        dispatch_status = str(active_round.get("dispatch_status") or "delivered")
        work_status = str(active_round.get("work_status") or "pending_acceptance")
        current_revision = active_round.get("current_revision", 1)
        revisions = active_round.get("revisions") or {}
        rev_info = revisions.get(str(current_revision)) or {}
        contract_hash = str(rev_info.get("contract_hash") or "")
        contract_path = str(rev_info.get("contract_path") or "")
        contract_valid, contract_status = verify_round_contract(active_round, current_revision)
        report = str(active_round.get("report") or "")

    board = board_fields(data)
    payload = {
        "status": "SESSION_ROLLOVER_REQUIRED" if data["rollover_required"] else "ok",
        "rollover_required": data["rollover_required"],
        "goal": board["goal"],
        "phase": data["phase"],
        "phase_round_count": data["phase_round_count"],
        "rounds": board["rounds"],
        "rounds_total": len(data["rounds"]),
        "pending_dispatch_age_s": board["pending_dispatch_age_s"],
        "active_rounds": active_ids,
        "active_jobs": len(active_jobs),
        "checkpoint": (data.get("last_checkpoint") or {}).get("path", ""),
        "planner_pane": data.get("planner_pane") or "",
        "session_id": data.get("session_id") or "",
        "auto_compact": auto_compact_enabled(data),
        "compact_queued": data.get("compact_queued"),
        "compaction_epoch": data.get("compaction_epoch", 0),
        "resume_pending": data.get("resume_pending"),
        "updated_at": data.get("updated_at", ""),
        "session_switched": False,
        "dispatch_status": dispatch_status,
        "work_status": work_status,
        "current_revision": current_revision,
        "contract_hash": contract_hash,
        "contract_path": contract_path,
        "contract_status": contract_status,
        "contract_valid": contract_valid,
        "report": report,
        "notices": data.get("notices", []),
    }
    if data["rollover_required"]:
        payload["guidance"] = ROLLOVER_GUIDANCE
    output(payload)
    return ROLLOVER_EXIT if data["rollover_required"] else 0


def cmd_rollover(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        active = active_round_ids(data)
        if active and args.reason != "compact":
            raise ValueError(f"cannot rollover with active round: {active[0]}")
        if not data["rollover_required"] and not compact_pending(data) and not args.force:
            raise ValueError("rollover is not required; use --force only for an intentional phase boundary")
        current = data.get("session_id") or ""
        if args.reason == "compact":
            # In-place compaction keeps the session id (Claude Code, pi). Accept the same
            # id, but still refuse placeholders; the epoch counter records the boundary.
            text = (args.new_session_id or current).strip()
            if text.startswith("<") and text.endswith(">"):
                raise ValueError(f"new-session-id is a placeholder: {text}")
            data["session_id"] = text
            data["compaction_epoch"] = int(data.get("compaction_epoch", 0)) + 1
        else:
            if not args.new_session_id:
                raise ValueError("--new-session-id is required with --reason new")
            data["session_id"] = validate_new_session_id(args.new_session_id, current)
            # A new session means the planner is already moving on; only an advanced
            # epoch makes this a real cancellation (epoch_at_arm < compaction_epoch).
            cancel_resume_record(data, "rollover_new")
        write_checkpoint(pp["checkpoint"], data, f"session rollover ({args.reason})")
        phase_advanced = bool(not active and (data["rollover_required"] or args.force))
        if phase_advanced:
            data["phase"] += 1
            data["phase_round_count"] = 0
            data["rollover_required"] = False
        data["compact_queued"] = None
        persist(pp, data)
        # Pane index, write point 4: rollover's planner_pane.
        record_pane(str(data.get("planner_pane") or ""), str(pp["root"]), cwd, "planner")
    output({
        "status": "rollover_recorded",
        "phase": data["phase"],
        "reason": args.reason,
        "session_id": data["session_id"],
        "compaction_epoch": data.get("compaction_epoch", 0),
        "session_switched": False,
        "phase_advanced": phase_advanced,
        "guidance": (
            "State advanced locally only. This command records that the planner context "
            "was already compacted or cleared; it does not start or switch a session."
        ),
        "carried_nonterminal_jobs": sum(j["state"] not in TERMINAL_JOBS for j in data["jobs"]),
    })
    return 0


def cmd_compact_self(args: argparse.Namespace) -> int:
    """Queue the planner pane's own compact command (or /clear) through herdr, on demand."""
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        write_ledger(pp["ledger"], data)
        if args.planner_pane:
            data["planner_pane"] = args.planner_pane
        result = queue_planner_compact(args, pp, data, mode=args.mode, force=True)
        if result.get("queued"):
            write_checkpoint(pp["checkpoint"], data, f"planner {args.mode} queued")
        persist(pp, data)
        # Pane index, write point 5: the planner pane compact-self actually used
        # (--planner-pane wins above, otherwise the state's planner_pane).
        record_pane(str(data.get("planner_pane") or ""), str(pp["root"]), cwd, "planner")
    result["status"] = "planner_compact_queued" if result.get("queued") else "planner_compact_not_queued"
    result["checkpoint"] = str(pp["checkpoint"])
    # Spawn point (issue #9): only fires for a newly armed record with auto-continue on.
    result["continue_after_compact"] = spawn_compact_continue_watcher(
        args, pp, data, result if result.get("queued") else None,
    )
    output(result)
    return 0 if result.get("queued") else 2


def cmd_resume_deliver(args: argparse.Namespace) -> int:
    """Claim and deliver the armed resume prompt to the planner pane, exactly once.

    The state-machine rejections (no_record, pane_mismatch, epoch_not_advanced,
    not_claimable) run on local state before any herdr call; planner_busy needs one
    `herdr agent get`. Rejections exit 2 with {"status": "rejected"} and touch
    nothing; a completed transition (delivered/uncertain/expired) exits 0.
    """
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        record = data.get("resume_pending")
        if not isinstance(record, dict) or not record:
            output({"status": "rejected", "reason": "no_record", "via": args.via})
            return 2
        if args.pane != str(record.get("planner_pane") or ""):
            output({
                "status": "rejected", "reason": "pane_mismatch", "via": args.via,
                "expected_pane": record.get("planner_pane"), "pane": args.pane,
            })
            return 2
        # State-machine gate before any herdr call: epoch must have advanced past
        # arming, and the record must still be claimable.
        gate = resume_claim_gate(data, record)
        if gate:
            output({
                "status": "rejected", "reason": gate, "via": args.via,
                "compaction_epoch": int(data.get("compaction_epoch", 0)),
                "epoch_at_arm": record.get("epoch_at_arm"),
                "record_status": record.get("status"),
            })
            return 2
        agent_status = str(agent_info(args, args.pane).get("agent_status") or "")
        if agent_status not in FRESH_OK_STATUS:
            output({
                "status": "rejected", "reason": "planner_busy", "via": args.via,
                "agent_status": agent_status,
            })
            return 2
        # Claim inside the lock and persist before prompting, so a concurrent wake-up
        # source sees the claim (state-during-send shows "claimed") instead of double-sending.
        record["status"] = "claimed"
        persist(pp, data)
        text = compact_continue_prompt(pp, data)
        delivered = False
        error = ""
        try:
            code, _, _, payload = run_herdr(args, ["agent", "prompt", args.pane, text])
            delivered = code == 0 and is_agent_prompted(payload)
            # last_error carries the herdr error code (or a fixed sentinel), per spec.
            error = herdr_error_code(payload) or "unknown_response"
        except ValueError as exc:
            # run_herdr failed before any response existed; there is no code to extract.
            error = str(exc)
        result: dict[str, Any] = {"pane": args.pane, "via": args.via}
        if delivered:
            record["status"] = "delivered"
            record["last_error"] = ""
            # The prompt body carried the deferred executor notices section, so the
            # delivered prompt consumed them; drop them from state.
            data["deferred_notices"] = []
            persist(pp, data)
            output({"status": "resume_delivered", **result, "record": record})
            return 0
        record["attempts"] = int(record.get("attempts", 0)) + 1
        record["last_error"] = error
        if int(record["attempts"]) >= 2:
            record["status"] = "expired"
            persist(pp, data)
            body = (
                "herdr-pair resume-deliver gave up: the planner compaction completed but the "
                f"resume prompt never confirmed. pane={args.pane} attempts={record['attempts']} "
                f"last_error={record['last_error']} state_dir={pp['root']}. "
                "Resume the pairing manually."
            )
            record_notice(
                args, pp, data,
                title="herdr-pair resume expired", body=body, dedupe_key="",
            )
            output({"status": "resume_expired", **result, "record": record})
            return 0
        record["status"] = "uncertain"
        persist(pp, data)
        output({"status": "resume_uncertain", **result, "record": record})
        return 0


def cmd_executor_event(args: argparse.Namespace) -> int:
    """Absorb one executor status edge: short-report, defer, or stay silent (issue #12).

    Always answers JSON on stdout: `notice_sent`, `notice_deferred`,
    `planner_exited_notified`, or `ignored` (+reason). Gates run in order, first
    hit wins: planner exit (needs no round) -> active round -> delivered dispatch
    -> pane match -> working/unknown -> exited stamp -> idle report hash ->
    dedupe -> hold -> min interval -> flush earlier deferred notices, then push
    this one. Uses load(), not load_ready(): a pending dispatch must not turn a
    status edge into a PendingDispatchError.
    """
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    status = args.status
    pane = args.pane
    with locked(pp["root"], pp["lock"]):
        data = load(pp["state"], cwd)
        if status == "exited" and pane == str(data.get("planner_pane") or ""):
            # The planner is gone: no short report can land there, so only the
            # human hears it and an owed resume dies. No hold and no interval
            # gate: a repeated exit is a repeated event, not a duplicate.
            record = data.get("resume_pending")
            if isinstance(record, dict) and record:
                record["status"] = "expired"
                record["last_error"] = "planner_gone"
            record_notice(
                args,
                pp,
                data,
                title="herdr-pair planner exited",
                body=(
                    f"planner pane {pane} exited; state_dir={pp['root']}. A queued "
                    "resume, if any, is expired, not pending."
                ),
                dedupe_key=(
                    f"planner-exited:"
                    f"{dt.datetime.now(dt.timezone.utc).isoformat()}"
                ),
            )
            output({"status": "planner_exited_notified", "pane": pane})
            return 0
        active = next(
            (r for r in data.get("rounds", []) if str(r.get("status") or "") == "active"),
            None,
        )
        if active is None:
            output({"status": "ignored", "reason": "no_active_round"})
            return 0
        if str(active.get("dispatch_status") or "") != "delivered":
            output({"status": "ignored", "reason": "dispatch_not_delivered"})
            return 0
        executor = str(active.get("executor") or "")
        planner = str(data.get("planner_pane") or "")
        if pane != executor and pane != planner:
            output({"status": "ignored", "reason": "pane_mismatch", "pane": pane})
            return 0
        if status in ("working", "unknown"):
            output({"status": "ignored", "reason": "status_ignored"})
            return 0
        if status == "exited":
            # Recorded before any notice gate: a vanished pane is a fact even
            # when its notice is held back; send-round refuses this target later.
            active["executor_pane_gone_at"] = now()
            persist(pp, data)
        exists, report_hash = round_report_hash(active, cwd)
        if status == "idle":
            if not exists:
                output({"status": "ignored", "reason": "report_missing"})
                return 0
            if report_hash == str(active.get("last_executor_report_hash") or ""):
                output({"status": "ignored", "reason": "report_unchanged"})
                return 0
        entry = executor_notice_entry(
            pp, data, active, status, report_hash=report_hash, reason="executor_event"
        )
        key = str(entry["dedupe_key"])
        if any(
            str(n.get("dedupe_key") or "") == key
            for n in data.setdefault("notices", [])
        ):
            output({"status": "ignored", "reason": "already_notified"})
            return 0
        deferred = data.setdefault("deferred_notices", [])
        already_deferred = any(
            isinstance(d, dict) and str(d.get("dedupe_key") or "") == key
            for d in deferred
        )

        def hold(reason: str) -> int:
            if already_deferred:
                output({"status": "ignored", "reason": "already_deferred"})
                return 0
            entry["reason"] = reason
            deferred.append(entry)
            persist(pp, data)
            output({"status": "notice_deferred", "reason": reason, "dedupe_key": key})
            return 0

        hold_reason = executor_notice_hold(data)
        if hold_reason:
            return hold(hold_reason)
        min_s = executor_notice_min_s()
        last = str(active.get("last_executor_notice_at") or "")
        if last and min_s > 0:
            try:
                elapsed = (
                    dt.datetime.now(dt.timezone.utc)
                    - dt.datetime.fromisoformat(last)
                ).total_seconds()
            except ValueError:
                elapsed = min_s  # unparsable stamp cannot throttle: let it through
            if elapsed < min_s:
                return hold("min_interval")
        # Nothing holds it back: drop this key's own deferred twin (a resend),
        # flush the rest oldest-first, then push the current notice.
        data["deferred_notices"] = [
            d
            for d in deferred
            if not (isinstance(d, dict) and str(d.get("dedupe_key") or "") == key)
        ]
        flush_executor_notices(args, pp, data, active)
        issue_executor_notice(args, pp, data, entry)
        active["last_executor_notice_at"] = now()
        if status == "idle" and report_hash:
            active["last_executor_report_hash"] = report_hash
        persist(pp, data)
        output({
            "status": "notice_sent",
            "dedupe_key": key,
            "round_id": entry.get("round_id"),
        })
        return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", default=os.getcwd())
    common.add_argument("--state-dir")
    common.add_argument("--herdr", help="herdr executable; PAIRCTL_HERDR otherwise, then herdr")
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("init", parents=[common])
    p.add_argument("--planner-pane")
    p.add_argument("--session-id")
    p.add_argument(
        "--no-auto-compact", action="store_true",
        help="do not queue the planner's own compact command when the five-round limit is reached",
    )
    p.add_argument("--goal", default="")
    p.add_argument("--context-budget", type=int)
    p.add_argument("--no-context-check", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("note-session", parents=[common])
    p.add_argument("--session-id", required=True)
    p.add_argument("--transcript-path", default="")
    p.add_argument("--kind", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--pane", default="")
    p.set_defaults(func=cmd_note_session)

    p = sub.add_parser("context-usage", parents=[common])
    p.add_argument("--budget", type=int)
    p.set_defaults(func=cmd_context_usage)

    p = sub.add_parser("note", parents=[common])
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("start-round", parents=[common])
    p.add_argument("--file", required=True, help="complete UTF-8 contract / handoff file")
    p.add_argument("--executor", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--acceptance", required=True)
    p.add_argument("--skip-lint", default="", metavar="REASON")
    p.set_defaults(func=cmd_start_round)

    p = sub.add_parser("send-round", parents=[common])
    p.add_argument("--target", required=True, help="executor pane_id")
    p.add_argument("--file", required=True, help="handoff file to send as the prompt")
    p.add_argument("--executor", default="", help="defaults to --target")
    p.add_argument("--scope", default="")
    p.add_argument("--acceptance", default="")
    p.add_argument("--send-timeout", type=float, default=30.0)
    p.add_argument(
        "--no-fresh", dest="fresh", action="store_false",
        help="send into the executor's existing context instead of clearing it first",
    )
    p.add_argument("--fresh-command", help="override the per-kind fresh-session slash command")
    p.add_argument(
        "--fresh-marker", action="append",
        help="extra screen text that proves the fresh command took effect (repeatable)",
    )
    p.add_argument("--fresh-timeout", type=float, default=15.0)
    p.add_argument("--fresh-lines", type=int, default=40)
    p.add_argument("--skip-lint", default="", metavar="REASON")
    p.set_defaults(func=cmd_send_round, fresh=True)

    p = sub.add_parser("prepare-round", parents=[common])
    p.add_argument(
        "--file", required=True,
        help="finished handoff to register for pair.dispatch-prepared; nothing is sent",
    )
    p.set_defaults(func=cmd_prepare_round)

    p = sub.add_parser("ack-round", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--revision", type=int, required=True)
    p.add_argument("--pane", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--contract-hash", required=True)
    p.add_argument("--action", required=True, choices=("accept", "start"))
    p.set_defaults(func=cmd_ack_round)

    p = sub.add_parser("check-round", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--pane", required=True)
    p.add_argument("--revision", type=int, help="authoritative revision claimed by executor")
    p.set_defaults(func=cmd_check_round)

    p = sub.add_parser("adopt-contract", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--file", required=True, help="contract / handoff file to adopt")
    p.add_argument("--scope", default="")
    p.add_argument("--acceptance", default="")
    p.set_defaults(func=cmd_adopt_contract)

    p = sub.add_parser("resolve-pending", parents=[common])
    p.add_argument("--outcome", required=True, choices=("delivered", "not-delivered"))
    p.set_defaults(func=cmd_resolve_pending)

    p = sub.add_parser("finish-round", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--status", required=True, choices=sorted(ROUND_STATES))
    p.add_argument("--artifacts", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--report", default="")
    p.set_defaults(func=cmd_finish_round)

    p = sub.add_parser("diff-round", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--revision", type=int)
    p.set_defaults(func=cmd_diff_round)

    p = sub.add_parser("job-add", parents=[common])
    p.add_argument("--round-id", required=True)
    p.add_argument("--queue", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--submitter", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--expected-artifacts", required=True)
    p.add_argument("--completion-assertion", required=True)
    p.add_argument("--owner", required=True)
    p.add_argument("--state", choices=sorted(JOB_STATES), default="submitted")
    p.set_defaults(func=cmd_job_add)

    p = sub.add_parser("job-update", parents=[common])
    p.add_argument("--queue", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--state", required=True, choices=sorted(JOB_STATES))
    p.add_argument("--log")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_job_update)

    p = sub.add_parser("checkpoint", parents=[common])
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_checkpoint)

    p = sub.add_parser("status", parents=[common])
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rollover", parents=[common])
    p.add_argument("--new-session-id", default="")
    p.add_argument(
        "--reason", choices=("new", "compact"), default="new",
        help="new: a different session id is required; compact: in-place compaction, same id allowed",
    )
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_rollover)

    p = sub.add_parser("compact-self", parents=[common])
    p.add_argument("--mode", choices=("compact", "clear"), default="compact")
    p.add_argument("--planner-pane", help="override the recorded planner pane")
    p.set_defaults(func=cmd_compact_self)

    p = sub.add_parser("watch-compact-continue", parents=[common])
    p.set_defaults(func=cmd_watch_compact_continue)

    p = sub.add_parser("resume-deliver", parents=[common])
    p.add_argument("--pane", required=True, help="planner pane that must receive the resume prompt")
    p.add_argument(
        "--via", required=True, choices=("plugin", "watcher"),
        help="wake-up source claiming this delivery (recorded in the output)",
    )
    p.set_defaults(func=cmd_resume_deliver)

    p = sub.add_parser("wake", parents=[common])
    p.add_argument(
        "--source", required=True, choices=("command", "hook", "status", "watcher"),
        help="wake-up point that ran this check (recorded in the output)",
    )
    p.set_defaults(func=cmd_wake)

    p = sub.add_parser("executor-event", parents=[common])
    p.add_argument("--pane", required=True, help="pane this status edge came from")
    p.add_argument(
        "--status", required=True,
        choices=("done", "blocked", "idle", "working", "unknown", "exited"),
        help="executor status edge (issue #12); working/unknown are always ignored",
    )
    p.set_defaults(func=cmd_executor_event)
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except LockTimeoutError:
        # Exact, stable, and newline-free: the caller's stdout may already hold a
        # partial result, so this bypasses output() (and its sort_keys formatting).
        sys.stdout.write(LOCK_TIMEOUT_PAYLOAD)
        return 2
    except PendingDispatchError as exc:
        payload = pending_payload(exc.pending)
        # The status exit also carries the popup-board fields (issue #14); the
        # notices argument stays for raises that never computed a board.
        if exc.board:
            payload.update(exc.board)
        if exc.notices is not None:
            payload["notices"] = exc.notices
        output(payload)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PAIRCTL_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
