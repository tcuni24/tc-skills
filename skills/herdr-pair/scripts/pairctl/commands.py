"""pairctl commands: Session, status and background-job commands."""

from __future__ import annotations

import argparse

from .constants import DEFAULT_STALE_HOURS, ROLLOVER_EXIT, ROLLOVER_GUIDANCE, TERMINAL_JOBS, VERSION
from .context import auto_compact_enabled, budget_for, read_context_usage, write_checkpoint
from .dispatch import queue_budget_compact, queue_planner_compact, spawn_compact_continue_watcher
from .notice import check_dispatch_stale
from .rounds import active_round_ids, find_round, verify_round_contract
from .state import (
    PendingDispatchError,
    board_fields,
    canonical_cwd,
    env_float,
    load,
    load_ready,
    locked,
    now,
    output,
    paths,
    persist,
    record_pane,
    write_ledger,
)


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
