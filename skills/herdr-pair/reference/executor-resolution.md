# Executor resolution

```bash
printf '%s\n' "$HERDR_WORKSPACE_ID" "$HERDR_TAB_ID" "$HERDR_PANE_ID"
herdr agent list
```

`agent list` returns JSON: `agent`, `agent_status`, `cwd`, `foreground_cwd`, `pane_id`,
`tab_id`, `workspace_id`, `terminal_title`. Exclude your own `$HERDR_PANE_ID`.

**Write down your own `$HERDR_PANE_ID` now — it is the return address.** The executor has no way
to find you: nothing in its context names the pane that prompted it, and `herdr agent prompt`
carries no reply channel. Omit it and the executor finishes, reports into its own pane, and you
wait forever on a task that is already done. Every handoff you send must carry it (§3).

**Every agent type in the list is eligible.** Filter on `agent` only when the user named a type
("让 opencode 执行"). When they did not, select on *position and availability* — `cwd`, then
`agent_status` — and never on brand preference. You do not get to decide another agent type is
unworthy; only its track record on this task does (§7).

`agent_status` is a **routing hint, not an availability verdict**. A visible model/quota banner is
also not a verdict. In particular, Antigravity/Agy's `AI: Out of credits` means the supplemental
quota is exhausted; subscription capacity may still be available. Never replace, reject or declare
an executor unavailable from that banner. A definitive prompt execution failure, an explicit report
from the executor, or the user's statement can establish unavailability; decorative or summary UI
text cannot. If `agent_status` says `idle` while the visible screen says `generating`/`running`, the
state is **unknown**: do not clear, resend, declare completion, or switch executors.

Prefer, in order: same tab → same workspace + same `cwd` → same `cwd` anywhere. Use a level only
when it yields **exactly one** match; never fall through past an ambiguous level. Always address
the resolved `pane_id`, never the agent name — names repeat across panes, `pane_id` does not.
`pane_id` uniqueness is per Herdr server: another machine may also have `w1:p1`. Selecting a
machine in the TUI does not retarget pairctl or other CLI calls; they still talk to the local
server. For a saved SSH machine, discover panes with `herdr --machine <label-or-id> agent list`
(global prefix; label or profile id) and bind the pair with `pairctl init --machine <label-or-id>`.
pairctl records that selector and prefixes every later herdr agent/pane call for the pair,
including resume and hooks. Omit `--machine` for the local server.

If the user named a type and several panes match, ask once, listing each candidate's `pane_id`,
`tab_id`, `cwd` and `terminal_title`. If the named type is not live anywhere, say so and offer the
candidates that are — do not silently substitute.

Resolve how broadly the user's executor choice applies:

- “让 Pi 执行这一轮/这个修改” names the writer for that round only.
- “整个任务只能由 Pi 修改” makes Pi the exclusive writer until the user changes it.
- “按任务难度派给不同执行者” authorizes the Planner to choose a different writer per round.

Do not treat a round-level choice as a project-wide lock, and do not treat general delegation as
permission to replace an explicitly exclusive writer. Record the chosen writer in each `round_id`.
A specialist may diagnose or verify read-only without taking the writer seat.

**Hard rule — one active writer per working directory.** Before sending, list all live agents whose
`cwd` is your working directory. Pick one writer and park every other agent that has or may still
have a write-capable prompt (§6). A read-only Verifier may share the directory only during a frozen
review epoch, never while the writer is active. `idle` is not proof that an old prompt cannot resume.
