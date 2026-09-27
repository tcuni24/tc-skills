# round-16 report — herdr 0.8.0 real-machine fixes

- `round_id`: `p02-r008`
- `revision`: 1
- `executor`: `w2X:p3`
- contract: `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p02-r008.rev1.contract`
- contract hash: `2db8aa960cd9dced4b65160e307144c9cc9574350ab7b9e66e9fe061c5821526`
- protocol: `check-round --revision 1` → `not_accepted` (expected exit 2) → `ack-round --action accept` → `ack-round --action start`; every protocol write preceded by a check showing `allowed=true`.
- scope honored: only the eight fenced files were modified; new files limited to this report and `.round16-tmp/`.

## Goal

Make the repository plugin manifest link on herdr 0.8.0; make the event hooks
read the real `{event, data}` envelope; make plugin subprocesses find `herdr`
via `HERDR_BIN_PATH`; make spawned watcher/resume children receive the
resolved absolute state directory. Round-15 real-machine findings 1, 2, 3, 5.

## Changes

| file | change |
|---|---|
| `herdr-plugin.toml` | five `[[actions]]` ids now dot-free (`pair-status`, `pair-focus-planner`, `pair-focus-executor`, `pair-resume-now`, `pair-dispatch-prepared`); `command` still passes the dotted action name to `hooks/action.py`; titles/contexts/events/panes/startup untouched. |
| `hooks/on_planner_status.py` | unwrap `payload["data"]` when it is a dict; `pane_id`/`agent_status` read from it; flat payloads keep working. |
| `hooks/on_pane_exited.py` | same unwrap for `pane_id`. |
| `hooks/action.py` | `herdr_bin()` resolution: `PAIRCTL_HERDR` → `HERDR_BIN_PATH` → `"herdr"`. |
| `hooks/notify.py` | same resolution for the pairctl-failed notification call. |
| `scripts/pairctl.py` | `spawn_compact_continue_watcher` and `spawn_resume_deliver` now pass `str(pp["root"])` / `str(paths(args)["root"])` as `--state-dir`, never the caller's relative path. |
| `tests/test_pairctl.py` | +4 tests (envelope parity for both hooks incl. planner+executor edges, `HERDR_BIN_PATH` for action and for notify, absolute `--state-dir` in spawned watcher argv + clean `compact-continue.log`); 3 manifest assertions updated to hyphenated ids. |
| `README.md` | install section: dot-free ids, link the repo dir directly (workaround paragraph removed); added `HERDR_BIN_PATH` resolution note; added the `pane.exited` 0.8.0 limitation with the `pairctl executor-event --status exited` workaround. |

`pane.exited` is **not** renamed — the manifest still subscribes it; herdr
0.8.0 simply never dispatches it on real pane closure (round-15 evidence).

## Test-first evidence (red → green)

New tests committed first, run alone: **4 failed** —
envelope runs produced no pairctl call, `pair.focus-planner` never reached the
`HERDR_BIN_PATH` binary, and the spawned watcher argv contained
`--state-dir state` (relative) instead of the absolute root. After the fix all
four pass; two mid-run regressions (a duplicate `write_pairctl_stub` shadowed
the existing exec-stub; a delivered `resume_pending` is no longer deliverable
so each envelope variant re-arms) were fixed in the tests themselves.

## Acceptance

`python3 -m pytest -q` (via `slot cpu`), exit code **0**:

```
178 passed, 69 subtests passed in 69.08s (0:01:09)
```

Full log: `.round16-tmp/pytest.log`.

### plugin link / unlink (raw output)

```
$ herdr plugin link /public/scripts/tc-skills/skills/herdr-pair --enabled
{"id":"cli:plugin","result":{"plugin":{"actions":[{"command":["python3","hooks/action.py","pair.dispatch-prepared"],"contexts":["pane"],"id":"pair-dispatch-prepared","title":"Dispatch prepared handoff"},{"command":["python3","hooks/action.py","pair.focus-executor"],"contexts":["pane"],"id":"pair-focus-executor","title":"Focus executor pane"},{"command":["python3","hooks/action.py","pair.focus-planner"],"contexts":["pane"],"id":"pair-focus-planner","title":"Focus planner pane"},{"command":["python3","hooks/action.py","pair.resume-now"],"contexts":["pane"],"id":"pair-resume-now","title":"Deliver resume prompt now"},{"command":["python3","hooks/action.py","pair.status"],"contexts":["pane"],"id":"pair-status","title":"Pair status"}],"enabled":true,"events":[{"command":["python3","hooks/on_planner_status.py"],"on":"pane.agent_status_changed"},{"command":["python3","hooks/on_pane_exited.py"],"on":"pane.exited"}],"manifest_path":"/public/scripts/tc-skills/skills/herdr-pair/herdr-plugin.toml","min_herdr_version":"0.8.0","name":"herdr-pair resume hook","panes":[{"command":["python3","hooks/status_pane.py"],"id":"pair-status","placement":"popup","title":"Pair status"}],"platforms":["linux","macos"],"plugin_id":"tc.herdr-pair","plugin_root":"/public/scripts/tc-skills/skills/herdr-pair","source":{"kind":"local"},"startup":[{"command":["python3","hooks/on_startup.py"]}],"version":"0.1.0"},"type":"plugin_linked"}}
LINK_EXIT=0

$ herdr plugin unlink tc.herdr-pair
{"id":"cli:plugin","result":{"plugin_id":"tc.herdr-pair","removed":true,"type":"plugin_unlinked"}}
UNLINK_EXIT=0

$ herdr plugin list --json
{"id":"cli:plugin","result":{"plugins":[],"type":"plugin_list"}}
LIST_EXIT=0
```

The **repository directory** itself linked — no manifest copy needed anymore.
`plugins` is empty after unlink; the plugin is not left linked.

## git evidence

`git rev-parse HEAD`: `180dce43b8d1a942bca219c5d485d27b0dca8dad`
(between-rounds commit `180dce4` added `README.md`; round-16 HEAD unchanged — no commits made).

`git status --short` before this round: tracked tree clean; untracked entries
identical to the after-list minus `?? .round16-tmp/` (the `?? skills/herdr-pair/reports/` entry already held rounds 14–15).

`git status --short` after:

```
 M skills/herdr-pair/README.md
 M skills/herdr-pair/herdr-plugin.toml
 M skills/herdr-pair/hooks/action.py
 M skills/herdr-pair/hooks/notify.py
 M skills/herdr-pair/hooks/on_pane_exited.py
 M skills/herdr-pair/hooks/on_planner_status.py
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round12-tmp/
?? .round13-tmp/
?? .round14-tmp/
?? .round15-tmp/
?? .round16-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/reports/
```

`git diff --stat`:

```
 skills/herdr-pair/README.md                  |   6 +-
 skills/herdr-pair/herdr-plugin.toml          |  10 +-
 skills/herdr-pair/hooks/action.py            |   7 +-
 skills/herdr-pair/hooks/notify.py            |   8 +-
 skills/herdr-pair/hooks/on_pane_exited.py    |   5 +
 skills/herdr-pair/hooks/on_planner_status.py |   5 +
 skills/herdr-pair/scripts/pairctl.py         |   8 +-
 skills/herdr-pair/tests/test_pairctl.py      | 229 +++++++++++++++++++++++++--
 8 files changed, 256 insertions(+), 22 deletions(-)
```

Forbidden files: `git diff --name-only` matches nothing under `docs/`,
`reference/recovery.md`, `SKILL.md`, `.handoff/`, or `.round9-tmp/` …
`.round15-tmp/` — none changed (verified; recovery.md diff empty).

## slot preflight

```
slot audit:  PID 3247933 bowtie2-build-l 25.5G RSS 330% CPU — outside slot, not ours; left running per rule.
slot status: heavy.slice 0.2G used; all pools idle (no running jobs).
```

Full output: `.round16-tmp/slot-preflight.log`.

## Deferred / skipped / blocked / downgraded

- `pane.exited` non-delivery on 0.8.0: **kept as documented limitation** (per contract — no rename, no substitute event); README documents the `pairctl executor-event --status exited` path. Deferred to a future herdr fix.
- `pairctl.herdr_bin(args)` itself was left at `args.herdr → PAIRCTL_HERDR → "herdr"`: the contract scoped `HERDR_BIN_PATH` resolution to `action.py`/`notify.py`; pairctl-level callers pass `--herdr`/env explicitly. No functional gap observed.
- No real-machine re-drill of the resume path (the contract's acceptance is link + pytest only); envelope behavior is covered by argv-parity tests against the recorded real envelope from `.round15-tmp/probe-env/env-dump.jsonl`.
- No commit/push/PR — stopped after report write per contract.

## Assumptions

- The pre-round `git status` was reconstructed from round-15's report + the
  externally-made `180dce4` commit (this continuation inherited no earlier
  snapshot); tracked tree was clean, all eight `M` entries are this round's.
- The stub in tests `exec`s the real pairctl, so argv parity between flat and
  enveloped payloads also proves end-to-end delivery still works.
- `pair-status` pane id was already dot-free in round 14; unchanged here.

## Limitations discovered

- herdr 0.8.0 validates action ids against `invalid_plugin_action_id` for dots;
  ids and the action names passed on argv are now deliberately different.
- The hook envelope unwrap is tolerant by design: any future dict `data`
  shadows top-level keys — acceptable because 0.8.0 always sends the envelope.
