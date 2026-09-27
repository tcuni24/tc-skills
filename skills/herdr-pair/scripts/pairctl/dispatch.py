"""pairctl dispatch: Resume and watcher machinery, plus the commands that own them."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any

from .constants import (
    COMPACT_ACCEPTS_INSTRUCTIONS,
    COMPACT_COMMANDS,
    DEFAULT_RESUME_DEADLINE_S,
    FILE_MODE,
    FRESH_COMMANDS,
    FRESH_OK_STATUS,
    PAIRCTL_SCRIPT,
    RESUME_CLAIMABLE,
)
from .context import (
    auto_compact_enabled,
    compact_instructions,
    continue_after_compact_enabled,
    write_checkpoint,
)
from .herdr import agent_info, herdr_bin, herdr_error_code, is_agent_prompted, run_herdr
from .notice import check_dispatch_stale, record_notice, resume_due
from .rounds import (
    active_round_ids,
    after_round_checkpoint,
    commit_round,
    verified_contract_payload,
)
from .state import (
    LockTimeoutError,
    canonical_cwd,
    clip,
    env_float,
    load,
    load_ready,
    locked,
    now,
    output,
    paths,
    persist,
    pid_alive,
    write_ledger,
)


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
