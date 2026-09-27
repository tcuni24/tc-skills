# Issue 2 implementation acceptance

Branch: `feat/herdr-pair-context-budget`; baseline: `f5e8126`.
Formal specification: [issue-2-herdr-pair-context-budget.md](../../docs/specs/issue-2-herdr-pair-context-budget.md).
All changes remain uncommitted. Pre-existing `AGENTS.md`, `docs/` and the HYF audit directory were preserved.

## Verification

- Current executable evidence: [post-review-validation.log](post-review-validation.log): **66 tests and 4 subtests passed**, exit 0, 30.98 s.
- The existing 49 tests remain in the suite; their fixtures now include the mandatory environment block and acceptance evidence.
- Skill validation passed; all local Markdown links and anchors resolve.
- The core is **13,825 characters**, below 15,000, with identical front matter.
- `git diff --check` passed.
- Tests use public CLI calls, fake Herdr, synthetic transcripts and task-local temporary directories. No real messages were sent to Herdr panes.
- Full suite runtime was measured below one minute. Slot audit found no bypassing heavy process; its global audit-log path was read-only, and its output was retained locally. No heavy job bypassed slot.

## Acceptance ledger

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Correct three-field usage, budget/age and unknown cases | PASS | Transcript, mismatched session, non-Claude, derived path and invalid latest usage CLI tests |
| 2 | SessionStart records session before init, handles missing fields | PASS | All four hook sources tested with stdin payloads; malformed identity fields leave record unchanged |
| 3 | Init budget/stale compaction, dispatch gate and bypass/unknown behavior | PASS | Budget precedence, queued init, stale-only init, dispatch exit 20 and no-check paths |
| 4 | Post-dispatch compaction only after confirmed delivery | PASS | Pending/disabled paths; fake Herdr observes durable active state before compact; focus includes goal and current round |
| 5 | Accepted finish needs evidence, report must exist | PASS | Missing evidence/report refusal preserves state; accepted checkpoint includes report/artifacts/notes |
| 6 | Pre-round snapshot and Git-independent diff | PASS | Existing-file send-round backup; changed/added/removed/missing paths; hash-only large file; manifest filename collision |
| 7 | Four lint categories and recorded skip | PASS | Four failing subtests and passing contract; no pending/round consumption; nested Unicode untracked paths and directory-prefix collisions |
| 8 | Checkpoint schema and bounded hook injection | PASS | Goal/usage/decisions/round columns; oversized header keeps Resume and full checkpoint path without table/decision body |
| 9 | Short core and five references | PASS | Packaging tests, runnable core handoff template, front matter and local-link verification |
| 10 | Original and added test suite green | PASS | Current full-suite log: 66 tests + 4 subtests |

## Integration decisions

- `[报告] relative/path.md` records the expected report before dispatch so recovery can read a report that arrived during compaction.
- Mid-round compact recovery advances compaction_epoch and consumes the queue while preserving phase, round count, contract and receipts. Completed phase rollover retains the five-round rule.
- The watcher names active reports, handles pending budget compaction and preserves the exact cwd/state selection in its commands.
- Fence lint treats the working-directory root and equivalent normalized paths as overlapping; backups live under snapshot `files/` so a source named manifest.json cannot collide with metadata.
- The example handoff uses disjoint fences and positively names task-local scratch or /project/tmp, avoiding the forbidden literal while keeping the environment rule.

## Assumption checks and limits

1. Official [Claude SessionStart documentation](https://code.claude.com/docs/en/hooks#sessionstart-input), local Claude 2.1.278 hook docs, and the local changelog support transcript_path. This is documented support; a real runtime payload was not captured. Local hook registration already matches startup/resume/compact/clear, so no global configuration changed.
2. Local project-directory names show separators/underscores normalized to hyphens, with historical dot-path evidence. The full encoding algorithm was not independently proven; the derived lookup requires a matching sessionId and otherwise reports unknown.
3. Fake Herdr verifies active accepted/running round + queued compact + report file on disk + hook recovery. A watcher continuation without hook recovery is also tested. Real TUI callback timing remains outside the specified test boundary.
4. Pane isolation uses HERDR_PANE_ID when available. Without it, two Claude panes sharing one cwd cannot be distinguished reliably. The supplied hook uses default cwd/XDG state; custom state-dir recovery uses explicit commands.
5. GitHub API access failed at task start. The local formal spec governed implementation; no issue or remote repository was modified.

## Attribution and review

Astra worker implemented scripts and A-H tests. Root integrated the short skill/references, additional recovery/packaging tests and final fixes. A fresh reviewer assessed the first frozen snapshot; after the watcher follow-up, another fresh reviewer assessed the final snapshot.

Final independent review: **PASS (final), no findings**. See [final-review.md](final-review.md) and [snapshot.json](snapshot.json).

```json
{
  "scope_id": "issue-2-herdr-pair-context-budget",
  "snapshot_id": "7074ed9bd2ab06e423e7c24df5c8b1f2140cac73163edb2d0251875b05e9ae98",
  "code_review": "NOT_REQUESTED",
  "validation": "PASS",
  "final_acceptance": "PASS",
  "review_kind": "final",
  "review_verdict": "PASS",
  "isolation_requirement": "ordinary",
  "evidence_refs": [
    "post-review-validation.log",
    "snapshot.json",
    "final-review.md",
    "root-edges.log"
  ],
  "contract_error": null
}
```
