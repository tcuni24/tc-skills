"""pairctl executor: Executor-side protocol commands: accept/start acks, write checks, contract adoption, events."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from .dispatch import cancel_resume_record
from .notice import (
    executor_notice_entry,
    executor_notice_hold,
    executor_notice_min_s,
    flush_executor_notices,
    issue_executor_notice,
    record_notice,
    round_report_hash,
)
from .rounds import save_contract, verified_contract_payload, verify_round_contract
from .state import canonical_cwd, load, load_ready, locked, now, output, paths, persist


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
