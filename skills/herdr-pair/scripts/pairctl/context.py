"""pairctl context: Context usage, the budget, checkpoints and the compaction instructions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
from typing import Any

from .constants import DEFAULT_CONTEXT_BUDGET, DEFAULT_STALE_HOURS, PAIRCTL_SCRIPT, TERMINAL_JOBS
from .state import (
    atomic_text,
    canonical_cwd,
    env_flag,
    env_float,
    load,
    load_ready,
    locked,
    now,
    output,
    parse_stamp,
    paths,
    persist,
    record_pane,
    table_cell,
    unknown_usage,
)


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


def auto_compact_enabled(data: dict[str, Any]) -> bool:
    if os.environ.get("PAIRCTL_AUTO_COMPACT", "").strip() in {"0", "off", "false", "no"}:
        return False
    return bool(data.get("auto_compact", True))


def continue_after_compact_enabled() -> bool:
    return env_flag("PAIRCTL_CONTINUE_AFTER_COMPACT", True)


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


def cmd_checkpoint(args: argparse.Namespace) -> int:
    pp = paths(args)
    cwd = canonical_cwd(args.cwd)
    with locked(pp["root"], pp["lock"]):
        data = load_ready(pp, cwd)
        write_checkpoint(pp["checkpoint"], data, args.reason)
        persist(pp, data)
    output({"status": "checkpoint_written", "checkpoint": str(pp["checkpoint"])})
    return 0
