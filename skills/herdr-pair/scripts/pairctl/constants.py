"""pairctl constants: Module-level constants shared by the whole package."""

from __future__ import annotations

from pathlib import Path
import re


VERSION = 2
ROLLOVER_EXIT = 20
MAX_ARG_BYTES = 131071
FILE_MODE = 0o600
DIR_MODE = 0o700
TERMINAL_JOBS = {"succeeded", "failed", "cancelled"}
ROUND_STATES = {"accepted", "blocked", "failed", "cancelled"}
JOB_STATES = {"submitted", "running", "succeeded", "failed", "cancelled", "unknown"}
ROUND_HEADER = re.compile(r"^\[轮次\][ \t]+round_id=\S+[ \t]*\r?$", re.MULTILINE)
PAIRCTL_SCRIPT = str(Path(__file__).resolve().parents[1] / "pairctl.py")
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
