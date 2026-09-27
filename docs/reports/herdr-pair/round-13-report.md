# Round p01-r005 report — section 6 plugin actions (issue #7)

- **round_id**: `p01-r005` / **revision**: `1`
- **contract_hash**: `c6e5cf781c8395e13826fb5b43b89d5f6de3f062cc0e7bf0f71ffe6066a32d4a`
- **contract_path**: `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p01-r005.rev1.contract`
- **executor**: `w2X:p1`, cwd `/public/scripts/tc-skills`, state `default`
- **acceptance**: `python3 -m pytest -q` → **exit 0, `169 passed, 69 subtests passed`**
- HEAD (unchanged, nothing committed): `d08871de2bb3406d9e2b08c7a727161b4279b00d`

## Protocol

1. Preflight: `slot audit` + `slot status` captured to `.round13-tmp/slot-audit.log` and
   `.round13-tmp/slot-status.log` **before** any heavy command. Audit found one process
   bypassing slot (`bowtie2-build-l`, PID 2915535, 15.5 G RSS); per the global rule it was
   **not terminated**, only recorded here as a resource conflict to be aware of.
2. `check-round --cwd /public/scripts/tc-skills --round-id p01-r005 --pane w2X:p1 --revision 1`
   before acceptance → **exit 2**, `reason=not_accepted`; round_id, revision, executor,
   acceptance, scope and full `contract_text`/`path`/`hash` matched the dispatch.
3. `ack-round --action accept` then `--action start` with the returned scope + hash → both
   `receipt_recorded` (work_status accepted → running).
4. A `check-round` returning `allowed=true, reason=ok` was run immediately before **every**
   file write in this round (red test write, action.py create + 1 fix, pairctl.py function +
   subparser, manifest rewrite, two test edits, the lint-assertion fixes, and this report).
5. No `herdr plugin link/unlink/enable/disable`, no commit/push/PR/issue change, no popup,
   no `agent.view.set`, no sidebar. 只读输入 `herdr-api-080.json` and `docs/**` untouched;
   `claude_session_start_hook.py`, `on_planner_status.py`, `on_pane_exited.py`, `notify.py`,
   `.handoff/`, `.round9-tmp/`…`.round12-tmp/` untouched.

## git status --short

Before (captured at preflight, after `.round13-tmp/` was created for the slot logs):

```
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round12-tmp/
?? .round13-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/reports/
```

After:

```
 M skills/herdr-pair/herdr-plugin.toml
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round12-tmp/
?? .round13-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/hooks/action.py
?? skills/herdr-pair/reports/
```

(The report itself, `skills/herdr-pair/reports/round-13-report.md`, lands inside the
already-untracked `skills/herdr-pair/reports/`.)

## git diff --stat

```
 skills/herdr-pair/herdr-plugin.toml     |  40 +++++-
 skills/herdr-pair/scripts/pairctl.py    |  41 ++++++
 skills/herdr-pair/tests/test_pairctl.py | 241 +++++++++++++++++++++++++++++++-
 3 files changed, 320 insertions(+), 2 deletions(-)
```

Plus new file `skills/herdr-pair/hooks/action.py` (15 674 bytes, untracked).

## pytest — red run (before implementation)

Command: `python3 -m pytest -q` (raw output in `.round13-tmp/pytest-red.log`), **exit 1**:

```
......................................................FFF.......................FF... [ 50%]
........................................F...........F........... [ 88%]
...........F........                                                   [100%]
=================================== FAILURES ====================================
__________ PairctlTest.test_action_output_includes_resolution_source ___________
...
>       self.assertEqual(explicit.returncode, 0, explicit.stderr + explicit.stdout)
E       AssertionError: 2 != 0 : python3: can't open file '/public/scripts/tc-skills/skills/herdr-pair/hooks/action.py': [Errno 2] No such file or directory
...
=========================== short test summary info ============================
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_action_output_includes_resolution_source
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_action_resolution_priority_explicit_wins
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_action_resolution_uses_custom_state_dir_from_index
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_dispatch_prepared_sends_registered_handoff
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_dispatch_prepared_without_registration_reports_nothing_prepared
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_plugin_manifest_lists_section6_actions
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_resume_now_returns_reason_without_sending
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_write_action_rejected_when_focus_is_executor
8 failed, 161 passed, 69 subtests passed in 55.61s
```

All seven contract test names were red (plus the extra nothing_prepared case); the 161
pre-existing tests stayed green, so the red baseline is caused only by the missing work.

## pytest — green run (acceptance)

- Direct: `python3 -m pytest -q` → **exit 0**, `.round13-tmp/pytest-green.log`:

```
..................................................................................... [ 50%]
................................................................ [ 88%]
....................                                                   [100%]
169 passed, 69 subtests passed in 54.76s
```

- Compliance re-run through the slot queue: `slot cpu -- python3 -m pytest -q` →
  **exit 0**, `.round13-tmp/pytest-slot.log`:

```
..................................................................................... [ 50%]
................................................................ [ 88%]
....................                                                   [100%]
169 passed, 69 subtests passed in 54.78s
```

## What was implemented

- **`skills/herdr-pair/hooks/action.py`** (new): reads `HERDR_PLUGIN_CONTEXT_JSON`;
  resolution priority `explicit` → `env` → `pane_index` (fresh entry of
  `$XDG_STATE_HOME/herdr-pair/panes/<focused_pane_id>.json`, 12 h staleness,
  `PAIRCTL_SESSION_STALE_HOURS` overridable) → `focused_cwd` → `workspace_cwd`
  (latter two take pairctl's default root = sha256[:20] of the canonical cwd under
  `XDG_STATE_HOME/herdr-pair`); no hit ⇒ exit 2 `reason=unresolved`. Every stdout JSON
  carries `resolution = {source, cwd, state_dir, focused_pane}`. Write actions
  (`pair.resume-now`, `pair.dispatch-prepared`) check `focused_pane_id == planner_pane`
  **before any pairctl/herdr call** ⇒ exit 2 `focus_not_planner`. Bodies: status =
  `pairctl status` payload + resolution (state.json byte-identical afterwards);
  focus-planner/executor = `herdr agent focus <pane>` (executor path answers
  `no_executor` without an active round); resume-now = `pairctl resume-deliver --pane
  <planner_pane> --via plugin` with pairctl's `reason` passed through and exit 2;
  dispatch-prepared = registered file → `pairctl send-round --target <executor> --file …`
  (full fence lint), `nothing_prepared` when no registration. The script does **not**
  import pairctl (subprocess only) and never writes state.json.
- **`skills/herdr-pair/scripts/pairctl.py`**: new `prepare-round --file <handoff>`
  subcommand — records only `{handoff, registered_at}` in `state.json.prepared_round`,
  sends nothing, does not consume the registration implicitly.
- **`skills/herdr-pair/herdr-plugin.toml`**: exactly five `[[actions]]`
  (`pair.status`, `pair.focus-planner`, `pair.focus-executor`, `pair.resume-now`,
  `pair.dispatch-prepared`), each with non-empty `title`,
  `command = ["python3", "hooks/action.py", "<id>"]`, `contexts = ["pane"]`; the two
  existing `[[events]]` kept verbatim; no `panes`/`startup` keys.
- **`skills/herdr-pair/tests/test_pairctl.py`**: the seven contract tests (black-box
  subprocess on `hooks/action.py`, context injected via env, fake herdr argv asserted)
  plus one extra (`nothing_prepared`).

## Deferred / skipped / downgraded

1. **Popup status board and sidebar view**: not built (conflict ruling). `pair.status`
   only calls `pairctl status` and echoes it back; no popup, no `agent.view.set`.
2. **Free-text dispatch**: out of scope by ruling; `dispatch-prepared` sends only the
   `prepare-round` registration.
3. **`prepare-round` records no `--target`** (contract says it records only the file
   path), so `dispatch-prepared` derives `send-round --target` from state.json: the
   active round's executor, else the most recent round's bound executor; **a state with
   zero rounds answers `no_executor`**, so the very first dispatch still goes through the
   CLI `send-round --target` (see Assumptions).
4. **Existing test adjusted**: `test_plugin_manifest_declares_resume_hook_only` no longer
   lists `"actions"` among forbidden manifest keys (the section-6 test owns that surface
   now); its two-events assertions are untouched and still pass.
5. **Slot rule deviation, recorded**: two intermediate pytest runs took 64.68 s and
   90.69 s without going through `slot` (threshold is 1 min). Mitigated by the final
   acceptance being re-run as `slot cpu -- python3 -m pytest -q` (exit 0); memory stayed
   far below 2 G and no `/data_0` IO was involved.
6. Not done by design: no `pair.status` surfacing of `prepared_round`, no clearing of the
   registration after a dispatch, no knowledge-layer/SKILL.md edits — none are in this
   round's fence.

## Assumptions

1. `resolution.source` for the `unresolved` rejection is `""` (empty): the contract pins
   the five values for successful resolutions, and the unresolved payload still carries
   all four `resolution` fields.
2. The planner-focus gate runs **before** the per-action checks, so a write action from
   the executor focus answers `focus_not_planner` even when nothing is prepared or no
   resume record exists (matches "写类动作…exit 2，不调用 herdr 发 prompt").
3. pairctl rejections (`status == "rejected"`) are forced to exit 2 by the action;
   otherwise pairctl's exit code is forwarded (`pair.status` can legitimately answer 2
   for `PENDING_DISPATCH_UNRESOLVED` or 20 for rollover).
4. Fail-closed extras beyond the contract: unknown action id → `unknown_action`;
   unreadable `state.json` on a write path → `state_unavailable`; pairctl
   missing/timeout/non-JSON stdout → `pairctl_failed` (all exit 2, all with
   `resolution`).
5. `pane_index` hit = the **newest unexpired** entry of that pane's index file; entries
   without `cwd`/`state_dir` are ignored, mirroring the event hooks' staleness rules.
6. Focus target for `pair.focus-planner` comes from `state.json.planner_pane`; the
   executor focus target from the active round's `executor`. Both fail with an explicit
   reason (`no_planner` / `no_executor`, exit 2) rather than guessing.
7. `hooks/action.py` inherits `XDG_STATE_HOME` (the harness keeps setting it), so index
   and default-root resolution never touch the real `~/.local/state` in tests.
