Base directory for this skill: /home/hyf/.claude/skills/herdr-pair

# Herdr Pair: Planner + Executor (+ optional Verifier)

At minimum two live Herdr agents share one task. **Planner** owns scope, sequencing, acceptance and
the final report to the user. **Executor** owns edits and command runs. An optional **Verifier**
performs independent read-only checks against a frozen artifact state. The user sees one deliverable.

**Roles are positions, not agent types.** Either seat can be held by any agent on the roster —
claude, opencode, codex, droid, cursor, hermes, omp, kimi, pi, grok, and whatever gets installed
next. Nothing in this skill depends on who is which. Settle your seat first (next section), then
follow that seat's sections.

This is not `herdr-handoff`. That skill relays a message and stops. This one keeps a loop
running and makes whoever plans accountable for work they did not type.

## Which seat are you in

| You are… | when | you follow |
|---|---|---|
| **Planner** | the user is talking to you about the task, and asks you to farm out the doing | §0–§8 |
| **Executor** | another agent sent you a scoped task and expects a report back | “If you are the executor” |
| **Verifier** | the Planner explicitly asks for an independent read-only check of a completed round | “Stable review epochs” + §5 |

If the user's phrasing leaves it open, you are the Planner — the agent holding the user's thread
owns the deliverable. Never assume the seat from agent type; a `hermes` can plan for a `claude`
just as easily as the reverse.

The seats may be filled by the same agent type. Two `claude` panes, or two `opencode` panes, pair
fine — resolution is by `pane_id` (§1), which is unique regardless of type.

A Verifier is optional and does not turn one round into parallel writing. It may inspect only after
the Executor has stopped writing and the Planner has frozen a review epoch. If the Executor writes
again, the Verifier's unfinished or completed conclusion is stale and must be discarded.

## The one rule that matters

> **The Planner is accountable for every line the Executor writes. If the Planner cannot vouch
> for it, it does not ship.**

Everything below exists to make that possible.

## When NOT to pair

Pairing costs a full round-trip per step plus your review time. Skip it and just do the work when:

- the task is mostly **judgment or authoring** (specs, decisions, reviews) — the executor
  becomes a slow typist for your own reasoning;
- the task is **short** (under ~15 minutes solo);
- the task is **deciding** something — what the metric means, which design wins, how a normative
  sentence should read. Delegate a decision and you get a fluent paraphrase of a choice nobody made;
- **you have already delegated this once and got nothing back.** Do not re-delegate a failed
  handoff. Take it back into your own pane.

Pair when the work is **wide and mechanical**: many files, repetitive edits, long test/lint
cycles, pipeline babysitting, migrations.

**Precision work is pairable — paraphrase is the enemy, not precision.** Implementing against a
frozen spec (exact metric definitions, gate IDs, invariants) pairs fine *provided* you fence the
spec file as untouchable and require quoting over restating: "照抄 §5.2 的措辞", "不许改动
coverage 那句", "拿不准就停下来问我，不要自己换个说法". What breaks these tasks is an executor
rewording a definition in good faith — not the difficulty of the definition.

## 0. Preflight

```bash
test "${HERDR_ENV:-}" = 1
herdr --skill
herdr agent prompt --help
```

Not inside Herdr → say so and stop. The installed binary is authoritative over this file.

### pairctl is the dispatch path

This skill ships [`scripts/pairctl.py`](scripts/pairctl.py) as a support script. Agent-skill
layouts may include a `scripts/` directory; that is the supported way to carry this helper.
**Do not enable a global Factory or Cursor hook** to run it, and do not edit
`~/.factory/hooks.json` / `~/.factory/settings.json` for pairing. Invoke the script from this
skill at each dispatch. The one hook this skill does own is the Claude Code `SessionStart`
hook in `~/.claude/settings.json` that runs
[`scripts/claude_session_start_hook.py`](scripts/claude_session_start_hook.py) after
`/compact` or `/clear`; it only calls pairctl (see "Context hygiene is automated" below).

Every Herdr handoff in a pairing session goes through `pairctl`, not a raw `herdr agent prompt`
typed in a shell. pairctl reads the handoff file, assigns `round_id`, and replaces only a
strict `[轮次] round_id=...` header line (other `round_id=` examples in the body stay). It
execs `herdr` with a subprocess argv list (no shell, so backticks are not executed).

`send-round` is two-phase: it first records an uncertain `pending_dispatch` without increasing
`phase_round_count`, then calls herdr. On `type: agent_prompted` it becomes active and counts
the round. Failure paths:

- A: only a definitive pre-delivery failure (`agent_not_found` or failure to start herdr) clears
  pending without counting the round. `agent_prompt_stalled`, timeout, malformed output, process
  death, and other unknown outcomes retain pending because delivery is uncertain. Every later
  command fail-closes and pairctl never resends that dispatch. Inspect the target pane, then use
  `resolve-pending --outcome delivered|not-delivered`; never edit `state.json` by hand.
- B: only a full line matching `[轮次] round_id=...` is rewritten; other `round_id=` strings stay.
- C: `init` has no `--reset` and will not empty an existing state that already has rounds or jobs.
- D: a missing or stale `jobs.tsv` is rebuilt from `state.json`; extra TSV-only rows are dropped.
- E: `rollover --new-session-id` (default `--reason new`) rejects empty values, placeholders
  such as `<id>`, and a repeat of the current session id, and leaves phase/session state
  unchanged. `--reason compact` accepts the same id (in-place compaction keeps it) and bumps
  `compaction_epoch` instead.
- F: `send-round` clears the executor's context first (`--fresh`, the default) and fails closed
  with `fresh_failed` if the pane is not idle or the screen does not confirm a fresh session.
  Nothing is sent and no round is consumed in that case.

```bash
PAIRCTL=/home/tcuni-claw/.agents/skills/herdr-pair/scripts/pairctl.py
python3 "$PAIRCTL" init --planner-pane "$HERDR_PANE_ID"
python3 "$PAIRCTL" send-round --target <executor_pane_id> --file /abs/path/to/handoff.md \
  --executor <executor_pane_id> --scope '<fence>' --acceptance '<command>'
python3 "$PAIRCTL" resolve-pending --outcome delivered  # or not-delivered, after pane inspection
python3 "$PAIRCTL" job-add --round-id <id> --queue <queue> --job-id <id> --label <label> \
  --submitter <pane> --command '<exact command>' --log <log> \
  --expected-artifacts '<paths>' --completion-assertion '<test>' --owner <pane>
python3 "$PAIRCTL" job-update --queue <queue> --job-id <id> --state running
python3 "$PAIRCTL" finish-round --round-id <id> --status accepted
python3 "$PAIRCTL" status
python3 "$PAIRCTL" compact-self [--mode compact|clear]   # queue your own pane's compaction now
python3 "$PAIRCTL" rollover --reason compact --new-session-id <id>   # non-Claude planners, after compaction
```

State defaults to `$XDG_STATE_HOME/herdr-pair/<cwd-hash>` (`~/.local/state/...` when unset).
`state.json` is the source of truth; each state file is written with a lock and atomic replace
(mode `0600`). `jobs.tsv` is a derived view rewritten from `state.json` whenever state loads
successfully under the lock. Do not treat the two files as one cross-file atomic commit.
`init` never wipes rounds or jobs.

### Context hygiene is automated

Slash commands go through `herdr agent prompt` like any other text, and the receiving TUI
executes them. Measured on this box, herdr 0.8.0, 2026-09-07: pi `/new` → "New session
started", kimi `/clear` → "Started a new session", Claude Code `/clear` and `/compact` → run.
Cursor's in-place compact analogue is `/summarize` (user-stated 2026-09-07; `finish-round`
previously failed closed with `no compact command known for agent kind 'cursor'`). A command
sent to a Claude pane that is still working is **queued and runs when that turn
ends**, without cutting the turn short. pairctl uses this in two places:

- **Executor, every round.** `send-round` first runs `herdr agent get` on the target, refuses
  unless it is `idle`/`done`, sends the kind's fresh-session command (pi `/new`; claude, kimi,
  droid `/clear`; codex, opencode `/new`), then polls `herdr agent read --source visible` until
  the screen shows the kind's marker. Only then does the handoff go out, and the round record
  carries `fresh: {kind, command, verified}`. `agent_prompted` on the slash command is *not*
  taken as proof; the screen is. This is why every handoff must be self-contained (§3): the
  executor keeps nothing from the previous round, by design. For a kind pairctl does not know,
  pass `--fresh-command '/…'` plus `--fresh-marker '<screen text>'`; `--no-fresh` sends into the
  existing context deliberately (say why in the round notes).
- **Planner, at the phase boundary.** The `finish-round` that completes round 5 writes the
  checkpoint and then sends your own pane the kind's compact command (claude/pi get
  `/compact <focus instructions naming the checkpoint>`; cursor `/summarize`; droid
  `/compress`; kimi/codex/opencode `/compact`). Because it is
  queued behind your current turn, finish what you were going to say to the user first; the
  compaction happens after. `exit 20` on any later `send-round`/`start-round` re-reports the
  queued command instead of re-sending it. Disable with `init --no-auto-compact` or
  `PAIRCTL_AUTO_COMPACT=0`; trigger by hand with `compact-self` (`--mode clear` sends the
  fresh-session command instead).

After the compaction the phase must be recorded. A **Claude Code planner** gets this for free:
the `SessionStart` hook (`matcher: compact|clear`) runs `pairctl status`, records
`rollover --reason compact|new`, and injects `CHECKPOINT.md` into the fresh context as
`additionalContext`; on `startup`/`resume` it does the same only when a rollover is pending and
the state was touched within 12 hours. Any **other planner** runs
`python3 "$PAIRCTL" rollover --reason compact --new-session-id <id>` itself after its
compaction (the compact-command instruction text says so). A message queued *behind* the compact
command was observed to run before the summary existed, so pairctl never queues a follow-up
through herdr. Instead it spawns `watch-compact-continue`: a detached waiter that polls until
the planner pane has been idle (default 8s, and at least 20s after compact was queued), then
`herdr agent prompt`s the planner to rollover and keep pairing. The user does not have to send
a resume message. Disable with `PAIRCTL_CONTINUE_AFTER_COMPACT=0`.

**exit 20** still means `SESSION_ROLLOVER_REQUIRED`: after five completed rounds the next
`send-round` is hard-blocked and a checkpoint is written. The payload's `planner_compact` field
says whether the compaction was queued (`queued: true`, or the reason it was not). pairctl never
switches sessions, never starts a Droid session, never calls the Factory Sessions API, and never
reads or prints credentials. Nonterminal job rows from `state.json` are copied into the
checkpoint and kept across rollover.

## 1. Resolve the executor

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

## 2. Cut the work into rounds

One handoff = one round = one diff you can read end-to-end and sign. Do this split before
writing any prompt; it decides how many prompts there are.

A round is correctly sized when all of these hold:

- **one fence** — the files it may touch fit on one line (§3), and no later round reopens them;
- **one acceptance command**, one literal expected result;
- **standalone verifiable** — you can sign its diff without knowing what round N+1 does;
- **no carried-forward values** — every input is in the prompt or already in the repo. If round
  N+1 needs a number round N produced, *you* verify it (§5) and *you* put it in the next prompt.
  A value that travels between rounds unread is how a fabricated number gets laundered past §5.

Splitting is **sequential, not fan-out.** One executor per working directory (§1) — never cut the
work into parallel rounds and fire them at several panes sharing a cwd. If the work is genuinely
parallel, give each executor its own worktree, and still drive them one at a time: you have to
read every diff yourself, and that is the real serial resource.

Cut along **files or mechanical units**, not along phases of thought. "Rewrite the header block in
these 12 docs" is a round. "Define the metric, then apply it" is not two rounds — the first half is
judgment work that should not be delegated at all (§When NOT to pair).

One round means you do not need this loop — send, verify, close out.

### Round and session budget

Do not let a pair silently become a day-long orchestration. `pairctl.py` enforces this:

- Give every handoff a unique `round_id` via `send-round` and record its Executor, scope and
  acceptance command.
- After 3 rounds, pairctl writes a short checkpoint with accepted artifacts, open blockers and
  active background jobs.
- At 5 rounds, stop. The `finish-round` of round 5 exits 20 with `SESSION_ROLLOVER_REQUIRED`,
  writes the checkpoint, and queues your own pane's compact command (§0, "Context hygiene is
  automated"). Finish your report to the user in that turn; the compaction runs after it. Either
  close the phase, take the work back, or tell the user why another phase is worth pairing. Do
  not simply send round 6 by hand.
- The checkpoint, not chat history, is the new source of context after the boundary. In Claude
  Code the `SessionStart` hook records the rollover and injects the checkpoint; in any other
  planner run `pairctl.py rollover --reason compact --new-session-id <id>` first thing after
  the compaction. If the compaction was not queued (`planner_compact.queued: false`), run
  `pairctl.py compact-self` or ask the user to run that kind's compact command (cursor `/summarize`).
- The executor's context is cleared before every round by `send-round`. Never rely on it
  remembering round N when you write round N+1; the handoff carries everything (§3).
- Do not busy-poll. Ask for an ETA once, check once after that ETA, send one stop/report request if
  overdue, then take the work back. Repeated `agent get`/`agent read` loops are not progress.

### Audit the governing spec — it is yours, not the executor's

The spec is fenced **read-only** for the executor (§3), but that fence is not an excuse to leave it
unread. Cutting rounds means reading it end-to-end, and **specs contain defects**: an acceptance
item that is literally unsatisfiable, a prohibition whose object drifted between sections, a
cross-reference to a file that already exists. If the executor hits one of these, it will do the
worst possible thing — quietly satisfy the letter, and break the behavior the same spec requires.

When you find a defect:

- **do not have the executor fix it**, and do not fix it silently yourself mid-round;
- name it to the user as a *spec* defect, not an implementation one, with the conflicting passages
  quoted and the section that carries scope authority identified;
- get the correction authorized, land it as its own dated annotation, and keep the acceptance item
  **mechanically checkable** after the rewrite. A back-check you have to interpret is not a
  back-check.

> **Observed, this repo, 2026-08-24:** a spec's §11 back-check demanded a file not exist. The same
> prohibition appeared twice earlier qualified as "no **weight constants** in that file" — §11 had
> dropped the qualifier, flipping the object from the constants to the file. The file had been
> introduced a week before the spec and held the truncation bound that spec's own §5.4 depended on.
> Read literally, passing the check would have broken the deliverable.

> **Observed, pi-grok-theme, 2026-08-25:** a frozen measurement formula — `rate = output_tokens ÷
> (end.timestamp − start.timestamp)` from event payloads — was literally unsatisfiable: every
> provider sets the message timestamp once at stream creation and both events carry the same
> object, so the duration was identically zero and the spec's own `≥ 0.5s` display threshold could
> never fire. All twelve unit tests passed because they injected synthetic timestamps. Type
> definitions proved the field *existed*; nothing proved it *carried the quantity*. Before freezing
> any measurement formula into a spec, walk the real data path and confirm the quantity exists and
> varies there — a green suite built on injected fixtures launders an unsatisfiable formula all
> the way to release.

#

[... skill content truncated for compaction; use Read on the skill path if you need the full text]