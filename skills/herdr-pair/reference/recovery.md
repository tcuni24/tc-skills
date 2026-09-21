# Recovery and context hygiene

Read this file for uncertain delivery, executor freshness failure, budget compaction, phase
rollover, or a planner that did not resume. The command implementation and installed Herdr CLI
are authoritative; never edit state.json manually.

## Dispatch failure paths A–F

- A: send-round persists uncertain pending_dispatch before invoking Herdr. Only agent_prompted
  consumes a round. agent_not_found or failure to start Herdr clears pending; stalled, timeout,
  malformed response, interrupted process and other unknown outcomes retain it. Inspect the
  recorded target pane and round_id, then explicitly resolve-pending --outcome delivered or
  not-delivered. Do not resend before resolving. Delivery recovery retains the original contract
  and pre-dispatch snapshot.
- B: only a complete `[轮次] round_id=...` line is rewritten; examples inside the body remain.
- C: init never resets existing rounds/jobs and has no --reset.
- D: jobs.tsv is regenerated from state.json. TSV-only rows are not state.
- E: rollover --reason new requires a different non-placeholder session id and no active round.
  In-place --reason compact permits the same id and advances compaction_epoch. When an active
  round exists and a compact is queued, recovery consumes the queue without advancing phase or
  discarding the round, contract, receipts or round count. Once the five-round phase is complete,
  normal rollover advances phase and resets its count.
- F: send-round freshens the executor by default before pending exists. It requires idle/done,
  sends the kind-specific fresh command, then verifies the visible screen. fresh_failed consumes
  nothing. Inspect agent get and agent read together. An unknown kind/command is a configuration
  gap, not proof that the executor is unavailable.

Before resolving a v2 pending delivery, its persisted contract must still match the recorded hash.
A missing/corrupt contract leaves pending intact. A legacy v1 dispatch resolves as revision 0,
unconfirmed_protocol; bind a complete contract with adopt-contract once. Repeat adoption is rejected.

## Planner session and usage

The Claude SessionStart hook calls note-session for startup/resume/compact/clear, even with no pair
state. It stores planner-session.json at the cwd's state root, separately from state.json. With
missing required identity/cwd/source it stays silent. A missing transcript path may use the derived
path; missing or mismatched sessionId, unreadable data or non-Claude kind yields unknown usage.
The hook never invokes Herdr, starts sessions or reads credentials. When HERDR_PANE_ID is available,
an executor's hook cannot overwrite the recorded planner session or consume its compact queue.
Without pane identity, two Claude panes sharing one cwd cannot be distinguished reliably.

An installed hook matcher must include all four sources, not just compact/clear.
On this machine the existing matcher already does; installing this script elsewhere does not
install a hook automatically. The supplied hook uses the default cwd/XDG state selection; custom
--state-dir pairings are not automatically restored by that default hook.

context-usage reads the last non-synthetic assistant usage:
input_tokens + cache_creation_input_tokens + cache_read_input_tokens. It also reports the first
message's timestamp and whether session age exceeds PAIRCTL_SESSION_STALE_HOURS (default 12).
The budget defaults to 150000; a command override, PAIRCTL_CONTEXT_BUDGET, or init --context-budget
can configure it. Unknown measurements are not zero and must never be treated as headroom.

### Evidence for transcript_path

The [official SessionStart input reference](https://code.claude.com/docs/en/hooks#sessionstart-input)
includes transcript_path. Local Claude 2.1.278's installed hook documentation and changelog also
describe the field. These establish documented support, not a captured live payload.
Local project directory names show separators and underscores normalized to hyphens, with historical
dot-path evidence as well. These observations do not prove the full escaping algorithm; derived
lookup still requires matching sessionId. Synthetic transcript tests cover both lookup paths.

## When automatic compaction queues

1. init writes pair state, then checks usage unless --no-context-check. Over-budget or stale
   sessions write reason init_context_budget, queue compact-self and spawn the continuation
   watcher; success returns CONTEXT_COMPACT_QUEUED with exit 0.
2. send-round checks usage only after agent_prompted and active state have been persisted.
   Over-budget known usage writes reason post_dispatch_budget, queues compaction and returns
   planner_compact. An unresolved pending dispatch, unknown usage or stale age alone does not
   trigger this post-dispatch path.
3. The existing fallback remains: checkpoint at three rounds, finish the fifth and return
   SESSION_ROLLOVER_REQUIRED / exit 20 before another round. Do not send a sixth by hand.

init --no-auto-compact or PAIRCTL_AUTO_COMPACT=0 disables all three automatic queues.
The five-round gate remains a phase boundary even when its automatic compact command is disabled.
compact-self explicitly requests compaction; --mode clear explicitly requests a fresh session.

An unconsumed queue is tied to compaction_epoch. send-round/start-round re-report it with exit 20
without sending another compact or consuming a round. Read planner_compact.queued and its reason:
a failed queue attempt is not proof that compaction happened.

### Compact commands and focus

Kind mappings: Claude/Pi/Kimi/Codex/OpenCode use /compact; Cursor uses /summarize; Droid uses
/compress. Claude/Pi accept focus instructions. The focus names the checkpoint, goal, current
round/revision, contract, executor pane and report path. It explicitly warns that executor reports
may arrive while compaction is in progress.

Measured historically on this box (Herdr 0.8.0, 2026-09-07), a slash command sent to a working Claude
pane queued until the current turn ended. Pairctl queues the command; it does not itself switch
sessions or prove compaction completed. Finish the current turn to let the TUI process it.

### Restore an active round

First run status, read the full checkpoint, and check whether each active Report path exists.
If its report arrived during compaction, inspect that file and the artifacts before dispatching
anything new. A report callback may be queued or absent; the on-disk report is the durable input.
Executor acceptance/start/write-check receipts remain valid for the same round and revision.

Claude's SessionStart hook records a completed requested refresh. Other planners run
rollover --reason compact --new-session-id <recorded-id> after their context refresh. Active budget
recovery preserves phase/count; a finished phase can advance. A new-session rollover remains
blocked while a round is active. If recovery fails, retain the queue and inspect status.

The checkpoint carries Goal, Context usage, Budget, each round's Revision/Contract/Report/Snapshot/
Artifacts/Notes, timestamped Decisions and nonterminal jobs. Record user decisions with
note --text instead of relying on chat memory. Hook injection is limited to the header, Resume
steps and Full checkpoint path; open that file to recover tables and decisions.

### Continuation watcher

Do not enqueue a normal follow-up behind /compact: historically it could run before the summary
existed. Pairctl instead spawns watch-compact-continue. It waits for planner idle (defaults:
minimum delay 20s, consecutive idle 8s, poll 1s, timeout 600s), then prompts the planner to recover
and continue. This is a routing heuristic, not proof of completed compaction.
PAIRCTL_CONTINUE_AFTER_COMPACT=0 disables this watcher only.

If the planner does not continue, inspect compact-continue.log and compact-continue.pid next to
state.json and compare agent status with the visible screen. The timing knobs are
PAIRCTL_CONTINUE_MIN_DELAY_S, PAIRCTL_CONTINUE_IDLE_S, PAIRCTL_CONTINUE_POLL_S and
PAIRCTL_CONTINUE_TIMEOUT_S. Inspect before retrying; do not create competing watchers.

## Fresh executor

Historical markers verified here: Pi /new -> “New session started”; Claude /clear -> “Claude Code v”;
Kimi /clear -> “Started a new session”. Other kinds may require --fresh-command and --fresh-marker.
Read [executor-resolution.md](executor-resolution.md) before declaring an executor unavailable.
Use --no-fresh only to deliberately retain context after ruling out an active/stale write prompt;
record the reason. Every handoff is self-contained even when keeping context.

## Snapshot and lint recovery

send-round/start-round parse comma-separated [可以改], [只读输入], [可以新建] path lines relative to cwd.
Before execution they copy existing fence files and write snapshots/<round>-r<revision>/manifest.json.
Directories are traversed; missing new paths are recorded exists:false. Files above 50 MB have hashes
but no copy, with an explicit skipped annotation. For overwritten large files arrange an allowed
backup separately. No parsed paths gives snapshot:null and warnings, not an invented baseline.

diff-round compares the current workspace with that revision's manifest without Git. Differences
exit 1, no differences exit 0; operational failures exit 2. Check changed/added/removed content and
also inspect status/inventories outside the fence: this is not a full workspace freeze gate.
Permissions/symlink drift enforcement belongs to the later review_epoch work.

handoff_lint runs before freshness, pending and round consumption. Resolve fence_overlap,
tmp_path, untracked_conflict and env_block_missing findings by editing the contract. An intentional
exception uses --skip-lint '<reason>', stored on the round (and pending if delivery is uncertain).

## Sending safely

The file-plus-pointer pattern avoids both shell expansion and the single-argument size ceiling;
read [handoff.md](handoff.md#the-prompt-is-one-argv-argument--131071-bytes-hard) for large messages.
Pairctl supplies the handoff via a subprocess argv list. For short manual callbacks use proper
single quoting; double-quoted Markdown backticks, dollar signs and command substitutions can alter
the message before Herdr sees it. Read stderr as well as JSON.

agent_prompted proves delivery to a pane, not intended identity or completed work. Verify the returned
pane/tab against the resolved seat. A recycled/missing pane needs re-listing, never a guessed
same-type replacement. --wait does not track turns: a previous turn can satisfy it, so idle after
--wait is not task acceptance. Require a persisted report and artifact verification.

## Audit the governing spec

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
