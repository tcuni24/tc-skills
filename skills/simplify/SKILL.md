---
name: simplify
description: Manual-only cleanup pass on recently changed code. Reviews the diff from four angles (reuse, quality, efficiency, altitude), applies risk-tiered fixes, and verifies behavior is preserved. Not a bug hunt.
disable-model-invocation: true
license: MIT
metadata:
  version: "0.1.0"
  derived-from: "hermes-agent simplify-code 1.1.0 (MIT), EveryInc ce-simplify-code, fcakyon simplify, addyosmani code-simplification"
---

# Simplify

Cleanup pass on code that already works. Goal: a new teammate understands the result
faster than the original, with identical behavior. Fewer lines is NOT the goal.
Correctness review is a different pass; do not hunt for bugs here.

## 0. Parse arguments

Text after `/skill:simplify` is the request. Recognize:
- Scope: `staged`, `last` (HEAD~1..HEAD), `branch`, `base=<ref>`, a path, or a PR number.
- `focus=<angle>[,<angle>]`: run only those angles (reuse | quality | efficiency | altitude).
- `dry-run`: review and report every tier, apply nothing.
Everything else is an extra constraint passed to every reviewer.

## 1. Resolve scope

A user-named scope is authoritative; never widen it. Otherwise:
1. Base = `@{upstream}`, else `origin/HEAD`, else `main`, else `master`. Take `git diff <base>...HEAD`.
2. Add working-tree changes (`git diff HEAD`) and untracked files
   (`git ls-files --others --exclude-standard`); the pass often runs before commit.
3. No git or empty diff: use files edited in this session. Nothing found: say so and stop.
Size guard: more than ~2000 changed lines -> stop and propose splitting by directory or commit.

## 2. Preflight

Remove from scope: documentation, generated or vendored files, lockfiles and dependency
manifests, mechanical churn (pure renames, formatting). Config that carries behavior
(workflow/pipeline configs, CI, build scripts) STAYS in scope. If no substantive code
remains, report "nothing to simplify" and stop. Never skip because a change is small.

## 3. Load the project profile

Read `AGENTS.md` / `CLAUDE.md` and, if present, `.pi/simplify.md`. The profile supplies
verification commands, public contracts, extra per-angle checklist items and excluded paths.
Without a profile, discover verification commands from the repo (Makefile, pyproject.toml,
package.json, CI files) and say that you did.

## 4. Review: four angles

Prompt files: `references/reuse.md`, `references/quality.md`, `references/efficiency.md`,
`references/altitude.md` (relative to this skill's directory).

- Preferred: four FRESH-context subagents in parallel (e.g. pi-subagents). Fresh, not forked:
  reviewers must not inherit the implementation conversation. Give each the full content of
  its prompt file, the FULL diff, the absolute repo path, the profile and user constraints.
- Reviewers are read-only: they may run git, grep/rg, read files and run linters, but never edit.
- Fewer slots: run in waves. No subagents: run each angle yourself, one at a time, re-reading
  the diff for each, and declare "inline single-pass review" in the report.
- Never drop or merge an angle unless `focus=` says so.

Each finding must use this format; drop any finding without `file:line` evidence:
`file:line | angle | problem | cost | fix | confidence: high/med/low | tier: SAFE/CAREFUL/RISKY`

## 5. Triage

1. Merge findings that target the same line or mechanism.
2. Drop false positives and nits silently; report only the count.
3. Conflicts: correctness > user focus > clarity/reuse > micro-performance.
   When two fixes are mutually exclusive, take the smaller diff and note the alternative.
4. Clarity gate for every fix: would a new teammate understand the result faster? If not,
   drop it. Never inline a well-named helper to save lines, never collapse a clear
   multi-step transformation into one nested expression, never remove an explaining variable.
5. Assign the final tier (override the reviewer when needed):
   - SAFE: provably behavior-neutral and local. Unused imports/locals confirmed by a linter
     AND grep, redundant casts, branches tooling proves unreachable.
   - CAREFUL: semantics-preserving restructuring. Rename private symbols, flatten nesting,
     extract or consolidate helpers, delete commented-out code, remove private pass-throughs.
   - RISKY: anything touching a profile public contract, exported/public API, error handling,
     concurrency, I/O order, caching, defaults or thresholds; any altitude fix in shared code;
     any edit outside the diff beyond a minimal adjacent change.
6. Real correctness bugs go to a separate "Bugs found" section and are never fixed here.

## 6. Apply (skip entirely in dry-run)

- SAFE: apply as one batch, then verify.
- CAREFUL: one file at a time, verify after each file, revert that file's change on failure.
- RISKY: never apply. List each with risk, test coverage and a suggested follow-up task.
- Before removing any symbol, grep its name, including string/dynamic references, configs
  and templates. A clean dead-code report is not proof.
- Stay inside the scope plus the minimal adjacent edit a fix requires.

## 7. Verification red lines

- Run the profile's commands: lint/typecheck project-wide; tests matched to blast radius
  (shared code -> broader tests; runner cannot scope -> full suite).
- On failure, fix the simplification or revert it. Never edit assertions, delete/skip/xfail
  tests, loosen types, add ignore/noqa markers, or remove validation or boundary checks.
- No lint and no tests available: mark the run UNVERIFIED and treat every CAREFUL finding as
  report-only unless the user explicitly allows applying it.

## 8. Report

1. Scope: base ref, ranges, files; mode (parallel subagents / waves / inline).
2. Verification: commands run and results, or UNVERIFIED.
3. Applied fixes grouped by tier and angle.
4. RISKY findings and suggested follow-ups.
5. Skipped: count and main reasons.
6. Bugs found (if any).
