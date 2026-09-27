# Round 15 report — real Herdr 0.8.0 acceptance + plugin README

- round_id: `p02-r007`
- revision: `1`
- contract hash: `d09a1c443fad75549863b0f881c1744e09236f105e66e78420a5dd13b728060f`
- contract: `~/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p02-r007.rev1.contract`
- executor pane: `w2X:p3`
- herdr version: `0.8.0` (`herdr server` pid 124179, socket `~/.config/herdr/herdr.sock`)
- drill cwd: `/public/scripts/tc-skills/.round15-tmp/scratch`
- drill state: `/public/scripts/tc-skills/.round15-tmp/state` (never the repo-root default state)
- drill panes (all opened by `herdr pane split` + `herdr agent start`, all closed at the end):
  `w2X:p5` drill-planner (cursor), `w2X:p6` drill-executor (cursor), `w2X:p7` exited-edge executor (cursor), `w2X:p8` post-unlink executor (cursor)

## Verdict (up front)

The pairing state machine and the read-only board work on a real herdr 0.8.0 —
compaction→resume, executor three-state notices, action resolution, popup entry,
sidebar persistence/replay, and post-unlink CLI flow were all exercised end to
end. **Four real defects were found**, two of them ship-blocking; all are listed
in *Findings* with reproduction evidence. Per this round's fence none of them
was fixed in the repo; each has a documented workaround used during the drill.

## git state

- `git rev-parse HEAD`: `b89f4c9ca4d928928b71d1e796213f4cf8783fef`
- `git diff --stat`: empty (working tree clean; round-15 only added untracked paths)
- `git status --short` after the round (before was identical minus this round's new files):

```
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round12-tmp/
?? .round13-tmp/
?? .round14-tmp/
?? .round15-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/README.md          <- new this round
?? skills/herdr-pair/reports/           <- round-15-report.md added here
```

`skills/herdr-pair/reference/recovery.md`: heading `### Continuation watcher`
still present at line 110; `git diff` for the file is empty (untouched).

## slot preflight

```
slot audit:  one non-slot heavy process observed: PID 3247933 bowtie2-build-l 25.4G RSS (not ours; left running)
slot status: pools idle; last jobs all finished; no queue
```

The drill itself is light (CLI calls + short waits); the only >1min command was
the pytest run, executed via `slot cpu` (below).

## Plugin lifecycle

- `herdr plugin link skills/herdr-pair --enabled` on the **repo manifest** fails:
  `{"error":{"code":"invalid_plugin_action_id","message":"invalid action id"}}` —
  see Finding 1.
- Drill therefore linked `.round15-tmp/herdr-pair-linked/` — a byte-identical copy
  of the plugin (hooks/scripts identical; `tests/`, `reports/` dropped) whose
  manifest only replaces `[[actions]]` ids `pair.*` → `pair-*`; the `command`
  argv still passes `pair.status` etc. to `action.py` unchanged. Link result:
  `plugin_linked`, `plugin_id: tc.herdr-pair`, enabled, 5 actions / 2 events /
  1 popup pane / 1 startup entry registered.
- `herdr plugin unlink tc.herdr-pair` → `plugin_unlinked removed:true`;
  probe plugin `tc.probe-env` also unlinked.
- `herdr plugin list --json` after unlink → `"plugins": []` — no enabled
  `tc.herdr-pair` remains. ✓ contract requirement satisfied.

## Record 1 — real compaction

Sequence (all on drill state):

1. `PAIRCTL_RESUME_DEADLINE_S=240 pairctl compact-self` → `/summarize` queued to
   cursor `w2X:p5`, `resume_pending` armed with **`mechanism: "watcher"`**
   (probe correctly returns watcher for a non-claude planner even with the
   plugin enabled).
2. First `/summarize` prompt never landed (cursor still initializing — prompt
   returned `agent_prompted` but no turn; see Assumptions). Re-queued
   `compact-self` at 10:26:04 → real `/summarize` executed (summary UI visible,
   context dropped to 7.7%).
3. The detached watcher spawned by step 1 died instantly —
   `compact-continue.log: PAIRCTL_ERROR: pair state is not initialized`
   (Finding 5: relative `--state-dir` propagated to the child whose cwd is the
   state root). Workaround: respawned the identical
   `watch-compact-continue` command manually with absolute paths +
   `PAIRCTL_INTERNAL_WATCHER=1`, pid written to `compact-continue.pid`.
4. Planner ran the epoch boundary itself: prompted `w2X:p5` to run
   `pairctl rollover --reason compact --new-session-id cursor-p5-epoch1` —
   pane shows the command + `rollover_recorded` JSON, `compaction_epoch: 0→1`.
5. Delivery: resume prompt **delivered exactly once** —
   `resume_pending.status: "delivered"`, `attempts: 0`,
   `compact-continue.log: {"pane":"w2X:p5","status":"continue_prompted"}`.
   Path taken: **watcher at deadline** (18:30:04+tick). The plugin path never
   claimed: every `pane.agent_status_changed` invocation exits 0 silently —
   Finding 2 (event JSON schema mismatch) makes the plugin resume path dead on
   this build regardless of mechanism. Cursor then read the skill +
   `CHECKPOINT.md` and resumed pairing — visible on the pane.
6. Resume prompt count: exactly 1 (claim state machine; second source exits
   on `record_delivered`).

## Record 2 — executor three states

Rounds: `p01-r001`/`p01-r002` on `w2X:p6` (cursor did the real protocol —
check-round, ack accept, ack start, wrote `r1-report.md`, and even
finish-round'd both rounds on its own), `p01-r003` on `w2X:p7` for the exit.

| Edge | Real edge fired | Plugin hook invoked | notice in state | planner short-report | human notification |
|---|---|---|---|---|---|
| done (r1, r2) | natural cursor turn ends (plugin-log-9/10/14-19/27-34) | yes (log ids, all exit 0) | `herdr-pair executor done` 10:34:01, 10:43:04 | prompted w2X:p5 (pane shows short-report turn) | `shown:false reason:disabled` |
| blocked (r1, r2) | `herdr pane report-agent --state blocked` (agent_status changed to blocked; logs +1 edge) | yes | `herdr-pair executor blocked` 10:35:54, 10:44:32 | same mechanism | `shown:false reason:disabled` |
| exited (r3) | `herdr pane close w2X:p7` while round active | **no `pane.exited` invocation at all** (Finding 4) | `herdr-pair executor exited` 10:47:40 | n/a (planner notified via notice) | `shown:false reason:disabled` |

`executor_pane_gone_at: 2026-09-22T10:47:40+00:00` recorded on p01-r003;
`executor` binding left as `w2X:p7` (gone stamp only, no rebind) ✓.

Degradation, stated plainly: because Finding 2 dead-ends every event hook, the
`executor-event` rows above were each also invoked **manually** with the same
argv the hook would use; those invocations produced the notices. The real-edge
→ hook-run leg is proven by plugin log ids; the hook → pairctl leg is dead in
this build.

Human notifications: `notification show` answered `disabled` for every notice
(`shown:false reason:disabled`) — the environment has notifications disabled;
this is a real host answer, recorded in `notices[]` and visible on the board.

Planner short-report: reached `w2X:p5` — after the first done notice the pane
entered a turn processing the executor-done report (`issue_executor_notice`
prompt landed, `prompted` path exercised).

## Record 3 — plugin log

`herdr plugin log list`: **39 entries** for the drill window.

- `tc.herdr-pair hooks/on_planner_status.py` on `pane.agent_status_changed`:
  30 invocations, all exit 0, all silent (Finding 2).
- `tc.herdr-pair hooks/action.py pair.status`: 2 invocations —
  - `plugin-log-35` exit 2: context resolved to another workspace's focused
    pane (`w6:p3K` / `focused_cwd`) → `pairctl_failed` + `resolution` fields —
    graceful failure path verified.
  - `plugin-log-36` exit 0: focused `w2X:p5` → `resolution.source="pane_index"`
    → full status JSON echoed (active_rounds `[p01-r003]`, six board fields
    present) → sidebar params persisted.
- `tc.herdr-pair hooks/action.py pair.focus-planner`: 1 invocation,
  `plugin-log-39` exit 2 — `focus_failed`: `[Errno 2] No such file or
  directory: 'herdr'` (Finding 3: `herdr` not on plugin PATH).
- `on_pane_exited.py`: **0 invocations** despite two real `pane close`s.
- Action ids covered: `pair-status`, `pair-focus-planner`.
- Plugin panes: `herdr plugin pane open --plugin tc.herdr-pair --entrypoint
  pair-status --placement popup` → `{"type":"ok"}` accepted; the in-action
  auto-open failed silently under Finding 3. `status_pane.py` rendered
  standalone with a plugin context env produces the real board (below).
- Sidebar/startup: `pair.status` wrote
  `~/.local/state/herdr/plugins/tc.herdr-pair/sidebar-view.json`
  (`source: tc.herdr-pair`, `label: herdr-pair`, `filter: in pane_id
  [w2X:p5, w2X:p7]`). Running `hooks/on_startup.py` with
  `HERDR_PLUGIN_STATE_DIR` + `HERDR_SOCKET_PATH` wrote one
  `agent.view.set` frame to the real socket; a probe frame with the same
  params returned
  `{"type":"agent_view","active":true,"source":"tc.herdr-pair","label":"herdr-pair"}`,
  then `agent.view.clear` → `active:false`. Newline-JSON framing confirmed.

Board render (real, from the drill state):

```
herdr-pair status
goal: round-15 real-machine drill
phase: 1
pending_dispatch_age_s: none
resume_pending: {…"mechanism":"watcher","planner_pane":"w2X:p5","status":"delivered"}
rounds:
  p01-r001 status=accepted executor=w2X:p6
  p01-r002 status=accepted executor=w2X:p6
  p01-r003 status=active executor=w2X:p7
notices:
  herdr-pair executor done shown=false reason=disabled
  herdr-pair executor blocked shown=false reason=disabled
  herdr-pair executor done shown=false reason=disabled
  herdr-pair executor blocked shown=false reason=disabled
  herdr-pair executor exited shown=false reason=disabled
```

## Record 4 — post-unlink zero regression

After `plugin unlink` (`plugin list` → `[]`):

- `send-round --target w2X:p8` → `round_sent p01-r004 delivered`.
- Executor-side protocol on drill state: `check-round` → contract hash
  `a5303af8…` / scope `post-unlink` verified → `ack-round accept` →
  `ack-round start` → both `receipt_recorded`.
- `finish-round --status accepted --report r4-report.md` → `round_finished`.
- Final rounds: r1 accepted, r2 accepted, r3 cancelled (executor exited),
  r4 accepted. Drill `state.json` is the only state touched; repo-root default
  state untouched.
- `slot cpu -- python3 -m pytest -q` at repo root: **exit 0** —
  `174 passed, 69 subtests passed in 68.03s`.

## pytest raw output

```
..................................................................................... [ 48%]
................................................................ [ 85%]
.........................                                              [100%]
174 passed, 69 subtests passed in 68.03s (0:01:08)
```

## Findings (real-machine defects, all unfixed per fence)

1. **Manifest action ids rejected.** herdr 0.8.0 validates action ids and
   rejects any containing `.`: `pair.status`, `pair.focus-planner`, … all
   `invalid_plugin_action_id`, so the repo manifest cannot link at all.
   Verified by isolation probes (`pair.status` ✗, `pair-status` ✓,
   `tc.probe-c.pair.status` ✗). Workaround used: linked a copy with hyphenated
   ids; `command` argv unchanged. Fix needed: rename ids in
   `herdr-plugin.toml` (fence-blocked this round).
2. **Event payload schema mismatch — all event hooks are dead.**
   `HERDR_PLUGIN_EVENT_JSON` is an envelope
   `{"event":"pane_agent_status_changed","data":{"pane_id":…,"agent_status":…}}`
   (verified by an env-dump probe plugin), but `on_planner_status.py` and
   `on_pane_exited.py` read `payload["pane_id"]` at top level → `""` → silent
   `return 0`. 30 real invocations, all exit 0, zero pairctl calls. This kills
   plugin-side resume delivery AND executor-event routing on 0.8.0.
   Fix needed: unwrap `payload["data"]` (and map `agent_status`) in both hooks.
3. **`herdr` not on the plugin PATH.** Plugin commands inherit an env where
   `herdr` is not resolvable (`pair.focus-planner` → `focus_failed`;
   `pair.status`'s popup open failed silently the same way). herdr does export
   `HERDR_BIN_PATH=/home/tcuni-claw/.local/bin/herdr` — the hooks ignore it.
   Fix needed: `herdr_bin()` should prefer `HERDR_BIN_PATH` before `herdr`.
4. **`pane.exited` is never delivered to plugins.** Two real `pane close`s
   produced zero `on_pane_exited.py` invocations (and zero probe-plugin dumps)
   — not even the schema-dead call shows up. Either the event isn't emitted to
   plugin subscribers or it's filtered upstream. Executor exits therefore need
   the `executor-event --status exited` CLI path (used above).
5. **Detached watcher inherits relative `--state-dir`.**
   `spawn_compact_continue_watcher` passes `args.state_dir` verbatim while the
   child's cwd is the state root → relative path resolves inside itself →
   `PAIRCTL_ERROR: pair state is not initialized`, watcher exits instantly.
   Fix needed: pass the resolved `pp["root"]`, not the raw arg.

## Deferred / skipped / degraded

- Plugin event leg (Finding 2/4): executor notices and the resume claim were
  verified via the same `pairctl` argv the hooks would run; the subscription→
  hook→pairctl chain is blocked in this build, documented per edge.
- Popup auto-open: `plugin pane open` accepted via API; the in-action open
  failed silently (Finding 3). Board content verified by running
  `status_pane.py` with plugin env.
- `pane.exited` hook leg: no invocation exists to degrade — the state-side
  record (`executor_pane_gone_at` + notice) was produced via manual
  `executor-event --status exited`.
- Human notifications are `disabled` on this host (`shown:false
  reason:disabled`); no GUI assertion possible — the notice records are the
  evidence.
- `pair-focus-planner` exercised to its real `focus_failed` answer (Finding
  3); the remaining action ids (`pair-resume-now`, `pair-dispatch-prepared`)
  were not invoked this round.
- Issue #15-level claude-planner `mechanism=plugin` path is unreachable on
  this build anyway (Finding 2); cursor+watcher path is what the spec
  prescribes for non-claude planners.

## Assumptions

- The first `compact-self` `/summarize` prompt was accepted
  (`agent_prompted:true`) but never executed — cursor was still initializing;
  re-queueing produced the real compaction. No state harm: re-queue keeps the
  same epoch record.
- Cursor has no discoverable session id via `herdr agent get`; the rollover
  used a stable label `cursor-p5-epoch1` (same-id compaction semantics).
- `report-agent` sets lifecycle authority; `release-agent` was used to hand
  detection back after the blocked-edge injection.
- Drill panes were opened on `w2X` and all closed; `w2X:p2`/`w2X:p4` were
  never prompted, focused, compacted or closed.
- `.round15-tmp/` holds: `probe-*/` manifests, `herdr-pair-linked/` (the
  linked copy), `handoff-r*.md`, `scratch/`, `state/`, `env-dump.jsonl`.
- No commits, pushes, PRs, issue edits; plugin ends **unlinked**;
  `reference/recovery.md` untouched.
