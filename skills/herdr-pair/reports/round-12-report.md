# Round 12 report — executor status absorption (herdr-pair issue #12)

- Round: `p01-r004`, revision `1`
- Contract hash: `ed0df2909bfc64b864f469ce673a2c9771949cb5346b09ebe9e9e1d22dc92ec3`
- Executor pane: `w2X:p1` → report to planner pane `w2X:p2`
- Protocol: every file write was preceded by
  `pairctl check-round --revision 1 --round-id p01-r004 --pane w2X:p1 --cwd /public/scripts/tc-skills`
  → `allowed=true` (contract hash matched each time).
- Heavy work: all pytest runs and preflights went through `slot cpu`;
  `slot audit` + `slot status` logs: `.round12-tmp/slot-audit.log`,
  `.round12-tmp/slot-status.log`, `.round12-tmp/slot-preflight-green.log`.
- Stop-after-write honored: no commit, push, PR, issue change; no
  `herdr plugin link/unlink/enable/disable`.

## Git

- HEAD (unchanged): `d1b73f9b5a99f07d13f1f813cc66190c4f26e107`
- Before (round start): `git diff --stat` empty — no tracked file modified;
  `git status --short` listed only pre-existing untracked entries
  (`.handoff/`, `.round9-tmp/`–`.round11-tmp/`, `docs/specs/issue-1-herdr-pair.md`,
  `docs/tickets/`, `herdr-api-080.json`). The baseline snapshot was captured at
  round start but not persisted to a file; the empty `git diff --stat` and the
  clean tracked tree are the authoritative part of that record.
- After (this report, before it exists):

```text
 M skills/herdr-pair/herdr-plugin.toml
 M skills/herdr-pair/hooks/notify.py
 M skills/herdr-pair/hooks/on_planner_status.py
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round12-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/hooks/on_pane_exited.py
?? skills/herdr-pair/reports/
```

- After `git diff --stat`:

```text
 skills/herdr-pair/herdr-plugin.toml          |  13 +-
 skills/herdr-pair/hooks/notify.py            |  11 +-
 skills/herdr-pair/hooks/on_planner_status.py |  88 ++++++-
 skills/herdr-pair/scripts/pairctl.py         | 363 +++++++++++++++++++-
 skills/herdr-pair/tests/test_pairctl.py      | 479 ++++++++++++++++++++++++++-
 5 files changed, 937 insertions(+), 17 deletions(-)
```

- Nothing committed this round (all changes are working-tree only).

## What changed

| File | Change |
|---|---|
| `skills/herdr-pair/scripts/pairctl.py` | New `executor-event` subcommand (`cmd_executor_event`) with the agreed gate order; issue-#12 constants and notice helpers (`executor_notice_min_s` / `executor_notice_hold` / `round_report_hash` / `executor_notice_entry` / `issue_executor_notice` / `flush_executor_notices`); `record_notice(..., sound=)`; `deferred_notices` in `load()`; `compact_continue_prompt` "Deferred executor notices:" section; `cmd_resume_deliver` clears `deferred_notices` on `delivered`; `cmd_send_round` `executor_pane_gone` gate right after `load_ready`; `round_sent` payload now carries `report`. |
| `skills/herdr-pair/hooks/on_planner_status.py` | Executor routing: fresh executor index entry + live delivered round for this pane → `executor-event --pane … --status <agent_status>` (never `resume-deliver`); planner path unchanged behind `FRESH_OK_STATUS`. |
| `skills/herdr-pair/hooks/on_pane_exited.py` | **New.** `pane.exited` hook: fresh index entries → one `executor-event --status exited` per distinct (cwd, state_dir); zero/stale → silent exit 0, empty stdout/stderr; pairctl failure → one-time notify + exit 1. |
| `skills/herdr-pair/hooks/notify.py` | Docstring now covers both consuming hooks. |
| `skills/herdr-pair/herdr-plugin.toml` | Second `[[events]] on = "pane.exited"` → `hooks/on_pane_exited.py`; header comment updated. |
| `skills/herdr-pair/tests/test_pairctl.py` | Red tests (8 required contract tests + 2 hook-routing tests) and manifest test asserting exactly 2 events. |

## Red → green

Command for both acceptance runs (same cwd = repo root, same collection):
`slot cpu -- python3 -m pytest -q`

- Red: exit **1** — `11 failed, 150 passed, 69 subtests passed in 52.00s`
  (`.round12-tmp/pytest-red.txt`). The 11 failures are exactly the 8 required
  contract tests + 2 hook-routing tests + the manifest test.
- Green: exit **0** — `161 passed, 69 subtests passed in 52.39s`
  (`.round12-tmp/pytest-green.txt`).

Iterations in between (each green run also via `slot cpu`):

1. `3 failed, 106 passed` — (a) manifest test used the bare name
   `PANE_EXITED_HOOK` (class attributes are not visible to bare names →
   `NameError`; fixed to `self.PANE_EXITED_HOOK`); (b) planner-exit notice used
   a second-precision dedupe key, so two back-to-back exits in the same second
   deduped to zero notifications (fixed to a microsecond-precision per-call
   key); (c) `round_sent` payload lacked `report` (KeyError; fixed by adding
   `"report": parse_report_path(text, cwd)`, consistent with the round record).
2. `1 failed, 108 passed` — idle test asserted `len(prompts) == 2` after its own
   `clear_calls()`, contradicting its own comment ("exactly one more push") and
   issue-7-revised line 149 (`idle` … 有变化推送一次). This was a test-authoring
   off-by-one (the cumulative count belongs to `state["notices"]`, which the
   test correctly expects to be 2); assertion corrected to 1.
3. Green from repo root: `161 passed, 69 subtests passed` (an earlier green run
   from `skills/herdr-pair` covered only 110 tests; rerun from repo root to be
   collection-identical with the red run).

## Assumptions (as implemented)

1. Planner `exited` branch requires no active round — only
   `status == "exited"` and `pane == planner_pane`; it expires
   `resume_pending` (`status=expired`, `last_error=planner_gone`) and always
   notifies, with no hold and no min-interval gate (each exit is a distinct
   event). Executor-side statuses require an active round with
   `dispatch_status == delivered` and pane ∈ {executor, planner_pane}.
2. `executor-event` uses `load()`, never `load_ready()` — a pending dispatch
   must not turn a status edge into `PendingDispatchError`.
3. `deferred_notices` entries are full dicts
   `{kind, title, prompt, body, sound, dedupe_key, round_id, revision, status,
   report_hash, reason, at}`; check order is dedupe → hold → min-interval, and
   the deferred twin of the current key is dropped before flushing the rest
   (oldest first) on the about-to-push path only.
4. `resume-deliver` clears `deferred_notices` only in the `delivered` branch.
5. The `send-round` `executor_pane_gone` gate runs immediately after
   `load_ready` (before rollover/compact/active-round checks), raises
   `ValueError` → exit 2, stderr `PAIRCTL_ERROR: executor_pane_gone: …`;
   a refusal consumes no round and leaves `pending_dispatch` untouched.
6. `on_pane_exited` calls pairctl once per distinct (cwd, state_dir) among
   fresh index entries; zero hits or only-stale entries exit 0 with empty
   stdout/stderr and make zero pairctl/herdr calls.
7. `on_planner_status` reads `state.json` to confirm a live round
   (`status==active`, `executor==pane`, `dispatch_status==delivered`) before
   calling `executor-event`; an executor-role hit without one falls through to
   the planner path, which filters it out — zero pairctl calls.
8. Notice sounds: `done → done`, `blocked/exited → request`, `idle → none`
   (`--sound` omitted); the recorded notice keeps its exact 7 fields
   (`sound` exists only in the herdr argv). Dedupe key:
   `executor:<round_id>:<revision>:<status>:<report_hash>`.
9. `working`/`unknown` are ignored before any mutation; every `ignored` path
   returns JSON exit 0 and never persists (state bytes unchanged).
10. Ambiguous newest-stamp executor entries across two state dirs log
    `ambiguous_pane` and forward nothing (mirrors the planner selector).

## Deferred / not done

- Commit, push, PR, issue edits, and `herdr plugin link/unlink/enable/disable`:
  forbidden by this round's contract — done state must stop at writing.
- Live end-to-end verification (issue-7-revised §验收: real `pane.exited` /
  `agent_status_changed` through a linked herdr plugin) cannot run without
  `herdr plugin link`; deferred to the planner's acceptance step.
- No tests skipped; no work items dropped.

## Raw pytest output

### Red — exit 1 (`.round12-tmp/pytest-red.txt`)

```text
................................................................................FFFFF [ 52%]
FF.........................F....FFF............................. [ 92%]
............                                                           [100%]
=================================== FAILURES ===================================
_________ PairctlTest.test_executor_done_and_blocked_prompt_and_notice _________

self = <test_pairctl.PairctlTest testMethod=test_executor_done_and_blocked_prompt_and_notice>

    def test_executor_done_and_blocked_prompt_and_notice(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()
    
        done = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
>       self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)

skills/herdr-pair/tests/test_pairctl.py:3448: AssertionError
______ PairctlTest.test_executor_event_dedupes_and_respects_min_interval _______

self = <test_pairctl.PairctlTest testMethod=test_executor_event_dedupes_and_respects_min_interval>

    def test_executor_event_dedupes_and_respects_min_interval(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()
    
        first = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
>       self.assertEqual(json.loads(first.stdout)["status"], "notice_sent")
                         ^^^^^^^^^^^^^^^^^^^^^^^^

skills/herdr-pair/tests/test_pairctl.py:3571: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/project/software/miniforge3/lib/python3.13/json/__init__.py:352: in loads
    return _default_decoder.decode(s)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/json/decoder.py:345: in decode
    obj, end = self.raw_decode(s, idx=_w(s, 0).end())
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <json.decoder.JSONDecoder object at 0x77f55f0e7a10>, s = '', idx = 0

    def raw_decode(self, s, idx=0):
        """Decode a JSON document from ``s`` (a ``str`` beginning with
        a JSON document) and return a 2-tuple of the Python
        representation and the index in ``s`` where the document ended.
    
        This can be used to decode a JSON document from a string that may
        have extraneous data at the end.
    
        """
        try:
            obj, end = self.scan_once(s, idx)
        except StopIteration as err:
>           raise JSONDecodeError("Expecting value", s, err.value) from None
E           json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

/project/software/miniforge3/lib/python3.13/json/decoder.py:363: JSONDecodeError
_________ PairctlTest.test_executor_event_ignores_working_and_unknown __________

self = <test_pairctl.PairctlTest testMethod=test_executor_event_ignores_working_and_unknown>

    def test_executor_event_ignores_working_and_unknown(self) -> None:
        self.send_round(1)
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
        for status in ("working", "unknown"):
            proc = self.executor_event(status, extra_env=self.ZERO_INTERVAL)
>           self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
E           AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                             {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E           pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)

skills/herdr-pair/tests/test_pairctl.py:3558: AssertionError
________ PairctlTest.test_executor_event_silent_without_delivered_round ________

self = <test_pairctl.PairctlTest testMethod=test_executor_event_silent_without_delivered_round>

    def test_executor_event_silent_without_delivered_round(self) -> None:
        def assert_ignored(proc: subprocess.CompletedProcess[str], why: str) -> None:
            self.assertEqual(proc.returncode, 0, f"{why}: {proc.stderr}{proc.stdout}")
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "ignored", f"{why}: {payload}")
            self.assertEqual(self.herdr_calls(), [], why)
    
        # no active round at all
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
>       assert_ignored(self.executor_event("done"), "no active round")

skills/herdr-pair/tests/test_pairctl.py:3767: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
skills/herdr-pair/tests/test_pairctl.py:3759: in assert_ignored
    self.assertEqual(proc.returncode, 0, f"{why}: {proc.stderr}{proc.stdout}")
E   AssertionError: 2 != 0 : no active round: usage: pairctl.py [-h]
E                     {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E   pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)
________ PairctlTest.test_executor_idle_pushes_only_when_report_changes ________

self = <test_pairctl.PairctlTest testMethod=test_executor_idle_pushes_only_when_report_changes>

    def test_executor_idle_pushes_only_when_report_changes(self) -> None:
        sent = self.send_round(1, body=(
            "[轮次] round_id=<unique-id>\n"
            "[报告] reports/executor-report.md\n"
            "produce that report file\n"
        ))
        round_id = sent["round_id"]
>       report = Path(sent["report"])
                      ^^^^^^^^^^^^^^
E       KeyError: 'report'

skills/herdr-pair/tests/test_pairctl.py:3497: KeyError
_______ PairctlTest.test_executor_notices_deferred_while_compact_queued ________

self = <test_pairctl.PairctlTest testMethod=test_executor_notices_deferred_while_compact_queued>

    def test_executor_notices_deferred_while_compact_queued(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        # Arming the resume writes compact_queued as well: both are hold reasons.
        armed = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(armed["queued"], armed)
        self.clear_calls()
    
        held = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
>       self.assertEqual(held.returncode, 0, held.stderr + held.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)

skills/herdr-pair/tests/test_pairctl.py:3654: AssertionError
_ PairctlTest.test_executor_pane_exit_records_time_and_blocks_next_fresh_send __

self = <test_pairctl.PairctlTest testMethod=test_executor_pane_exit_records_time_and_blocks_next_fresh_send>

    def test_executor_pane_exit_records_time_and_blocks_next_fresh_send(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()
    
        gone = self.executor_event("exited", extra_env=self.ZERO_INTERVAL)
>       self.assertEqual(gone.returncode, 0, gone.stderr + gone.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)

skills/herdr-pair/tests/test_pairctl.py:3685: AssertionError
_______ PairctlTest.test_pane_exited_hook_routes_through_executor_event ________

self = <test_pairctl.PairctlTest testMethod=test_pane_exited_hook_routes_through_executor_event>

    def test_pane_exited_hook_routes_through_executor_event(self) -> None:
        self.send_round(1)
        self.clear_calls()
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-exited"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}
    
        hook = self.run_exited_hook(pane_id="w1:p2", extra_env=env)
>       self.assertEqual(hook.returncode, 0, hook.stderr)
E       AssertionError: 2 != 0 : python3: can't open file '/public/scripts/tc-skills/skills/herdr-pair/hooks/on_pane_exited.py': [Errno 2] No such file or directory

skills/herdr-pair/tests/test_pairctl.py:3799: AssertionError
______________ PairctlTest.test_planner_pane_exit_expires_resume _______________

self = <test_pairctl.PairctlTest testMethod=test_planner_pane_exit_expires_resume>

    def test_planner_pane_exit_expires_resume(self) -> None:
        self.arm_and_advance_epoch()
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")
        self.clear_calls()
    
        gone = self.executor_event("exited", pane="w1:p1")
>       self.assertEqual(gone.returncode, 0, gone.stderr + gone.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver,wake} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'executor-event' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver, wake)

skills/herdr-pair/tests/test_pairctl.py:3729: AssertionError
____ PairctlTest.test_planner_status_hook_routes_executor_to_executor_event ____

self = <test_pairctl.PairctlTest testMethod=test_planner_status_hook_routes_executor_to_executor_event>

    def test_planner_status_hook_routes_executor_to_executor_event(self) -> None:
        self.send_round(1)
        self.clear_calls()
        stub = self.write_pairctl_stub()
        env = {"PAIRCTL": str(stub), "PAIRCTL_EXECUTOR_NOTICE_MIN_S": "0"}
    
        hook = self.run_resume_hook(pane_id="w1:p2", agent_status="done", extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        calls = self.stub_calls()
>       self.assertEqual(calls, [[
            "executor-event", "--pane", "w1:p2", "--status", "done",
            "--cwd", str(self.cwd.resolve()),
            "--state-dir", str(self.state.resolve()),
        ]], calls)
E       AssertionError: Lists differ: [] != [['executor-event', '--pane', 'w1:p2', '--[180 chars]te']]
E       
E       Second list contains 1 additional elements.
E       First extra element 0:
E       ['executor-event', '--pane', 'w1:p2', '--status', 'done', '--cwd', '/public/scripts/tc-skills/skills/herdr-pair/tests/tmptci0mg_m/project', '--state-dir', '/public/scripts/tc-skills/skills/herdr-pair/tests/tmptci0mg_m/state']
E       
E       - []
E       + [['executor-event',
E       +   '--pane',
E       +   'w1:p2',
E       +   '--status',
E       +   'done',
E       +   '--cwd',
E       +   '/public/scripts/tc-skills/skills/herdr-pair/tests/tmptci0mg_m/project',
E       +   '--state-dir',
E       +   '/public/scripts/tc-skills/skills/herdr-pair/tests/tmptci0mg_m/state']] : []

skills/herdr-pair/tests/test_pairctl.py:3849: AssertionError
__________ PairctlTest.test_plugin_manifest_declares_resume_hook_only __________

self = <test_pairctl.PairctlTest testMethod=test_plugin_manifest_declares_resume_hook_only>

    def test_plugin_manifest_declares_resume_hook_only(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "herdr-plugin.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("id"), "tc.herdr-pair")
        self.assertTrue(str(manifest.get("name") or "").strip())
        self.assertTrue(str(manifest.get("version") or "").strip())
        self.assertEqual(manifest.get("min_herdr_version"), "0.8.0")
        self.assertEqual(manifest.get("platforms"), ["linux", "macos"])
        events = manifest.get("events")
        self.assertIsInstance(events, list)
>       self.assertEqual(len(events), 2, events)
E       AssertionError: 1 != 2 : [{'on': 'pane.agent_status_changed', 'command': ['python3', 'hooks/on_planner_status.py']}]

skills/herdr-pair/tests/test_pairctl.py:2971: AssertionError
=========================== short test summary info ============================
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_done_and_blocked_prompt_and_notice
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_event_dedupes_and_respects_min_interval
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_event_ignores_working_and_unknown
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_event_silent_without_delivered_round
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_idle_pushes_only_when_report_changes
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_notices_deferred_while_compact_queued
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_executor_pane_exit_records_time_and_blocks_next_fresh_send
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_pane_exited_hook_routes_through_executor_event
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_planner_pane_exit_expires_resume
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_planner_status_hook_routes_executor_to_executor_event
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_plugin_manifest_declares_resume_hook_only
11 failed, 150 passed, 69 subtests passed in 52.00s
```

### Green — exit 0 (`.round12-tmp/pytest-green.txt`)

```text
..................................................................................... [ 52%]
................................................................ [ 92%]
............                                                           [100%]
161 passed, 69 subtests passed in 52.39s
```
