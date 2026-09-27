"""pairctl rounds: The round state machine: ids, contracts, commit, rollover and the round commands."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .constants import (
    DEFAULT_STALE_HOURS,
    DIR_MODE,
    FILE_MODE,
    FRESH_GUIDANCE,
    MAX_ARG_BYTES,
    ROLLOVER_EXIT,
    ROLLOVER_GUIDANCE,
    ROUND_HEADER,
    TERMINAL_JOBS,
)
from .context import (
    auto_compact_enabled,
    budget_for,
    compact_pending,
    compact_pending_payload,
    read_context_usage,
    write_checkpoint,
)
from .handoff import handoff_lint, parse_report_path, snapshot_round
from .herdr import freshen_executor, herdr_bin, herdr_error_code, is_agent_prompted
from .notice import validate_new_session_id
from .state import (
    atomic_text,
    canonical_cwd,
    chmod_private,
    clip,
    env_float,
    file_sha256,
    load,
    load_ready,
    locked,
    now,
    output,
    parse_json_payload,
    paths,
    pending_payload,
    persist,
    record_pane,
    write_ledger,
)


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
    from .dispatch import queue_planner_compact  # local import: breaks the module cycle
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
    from .dispatch import spawn_compact_continue_watcher  # local import: breaks the module cycle
    payload = rollover_payload(pp, data, reason, args)
    persist(pp, data)
    payload["continue_after_compact"] = spawn_compact_continue_watcher(
        args, pp, data, payload.get("planner_compact")
        if isinstance(payload.get("planner_compact"), dict) else None,
    )
    output(payload)
    return ROLLOVER_EXIT


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
    from .dispatch import (  # local import: breaks the module cycle
        cancel_resume_record,
        queue_budget_compact,
        spawn_compact_continue_watcher,
    )
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


def cmd_finish_round(args: argparse.Namespace) -> int:
    from .dispatch import (  # local import: breaks the module cycle
        queue_planner_compact,
        spawn_compact_continue_watcher,
    )
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


def cmd_rollover(args: argparse.Namespace) -> int:
    from .dispatch import cancel_resume_record  # local import: breaks the module cycle
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
