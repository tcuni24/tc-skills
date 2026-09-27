"""Persistent round and background-job state for herdr-pair."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .commands import cmd_compact_self, cmd_init, cmd_job_add, cmd_job_update, cmd_status
from .constants import JOB_STATES, LOCK_TIMEOUT_PAYLOAD, ROUND_STATES
from .context import cmd_checkpoint, cmd_context_usage, cmd_note, cmd_note_session
from .dispatch import cmd_resolve_pending, cmd_resume_deliver, cmd_wake, cmd_watch_compact_continue
from .executor import cmd_ack_round, cmd_adopt_contract, cmd_check_round, cmd_executor_event
from .rounds import (
    cmd_diff_round,
    cmd_finish_round,
    cmd_prepare_round,
    cmd_rollover,
    cmd_send_round,
    cmd_start_round,
)
from .state import LockTimeoutError, PendingDispatchError, output, pending_payload


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", default=os.getcwd())
    common.add_argument("--state-dir")
    common.add_argument("--herdr", help="herdr executable; PAIRCTL_HERDR otherwise, then HERDR_BIN_PATH, then herdr")
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
