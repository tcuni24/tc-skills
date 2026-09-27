---
status: proposed
date: 2026-09-27
---

# Pi planner compaction bridge

Pi's `session_before_compact` has `preparation`, `branchEntries`, `reason`, `willRetry`, and `signal`; it is cancellable and is not evidence of completed compaction. `session_compact` carries the persisted `compactionEntry`, `fromExtension`, `reason`, and `willRetry`; the source appends the compaction entry before awaiting extension handlers. `session_compact_failed` carries `reason`, `errorMessage?`, `aborted`, `willRetry`, and `fromExtension` and does not advance the pair epoch. Manual and automatic compaction follow these rules. Source: pi-mono `packages/coding-agent/src/core/extensions/types.ts` and `core/agent-session.ts` at 2b0a123de98318c2ff8069661721ce0c3794c34e.

Install the bridge explicitly:

```sh
mkdir -p ~/.pi/agent/extensions
ln -s /absolute/path/to/tc-skills/skills/herdr-pair/pi/herdr-pair.ts ~/.pi/agent/extensions/herdr-pair.ts
```

Restart or reload Pi after installing. The probe requires this exact symlink target and an enabled `tc.herdr-pair` Herdr plugin. The optional `PAIRCTL_PI_EXTENSION_PATH` overrides the standard install path for testing or a custom Pi agent home. Claude's probe and SessionStart hook are unchanged. Uninstalled Pi uses the watcher, with the existing manual rollover instruction.

For a completed non-retrying compact, the extension passes cwd, `HERDR_PANE_ID`, and Pi's current session ID to a Python adapter. It checks the planner pane, session ID, and queued compact or required rollover; calls `pairctl rollover --reason compact` once; and attaches the existing CHECKPOINT header and Resume section to the next `before_agent_start` turn. The Herdr status plugin then sees the advanced epoch and can deliver the pending continuation. On an exception or missing pane, nothing advances. The deadline watcher remains armed, but an unsuccessful bridge requires manual rollover after verifying compaction.

**Real Pi + Herdr acceptance gate:** capture timestamps for the successful Pi `session_compact` callback, `pairctl` epoch persistence, Herdr's first idle/done edge, and the delivered continuation. Verify the epoch is persisted before the first eligible idle edge, exactly one continuation is sent, the CHECKPOINT content reaches the next model turn, and failure/abort/retry events send none. The Pi source proves callback placement relative to its own `compaction_end`; it cannot prove Herdr's pane status event ordering. Until this gate passes, leave this ADR proposed and do not claim Pi end-to-end validation.
