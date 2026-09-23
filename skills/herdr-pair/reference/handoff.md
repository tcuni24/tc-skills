# Handoff details

**The executor does not inherit your context.** It may not read the repo's `AGENTS.md`, and
delegated subagents routinely skip global rules files. Anything that matters must be *in the prompt*.

Because the seat is type-agnostic, you must assume **zero inherited rules**. Each agent type reads
its global rules from a different hook, and some read none at all in this position:

- the hooks differ per type (`~/.claude/CLAUDE.md`, `~/.config/opencode/AGENTS.md`,
  `~/.codex/AGENTS.md`, `~/.factory/AGENTS.md`, `~/.omp/agent/RULES.md`, `~/.hermes/SOUL.md`, …);
- **`cursor` cli scans the workspace only** — with no `AGENTS.md` in the project root it starts with
  no global rules whatsoever;
- a worktree is not the project root, so a root-level symlink may not be on its path either.

Do not probe which hook your executor honors, and do not shorten the prompt because you believe its
type "already knows". Restate the rules every time. A redundant reminder costs a few lines; a missed
one costs a wrong-place write or an unqueued heavy job.

Always include, verbatim:

- **round identity** — a unique `round_id`; reports and artifacts must repeat it;
- **working directory** (absolute) and, in a worktree, "run everything here, do not `cd` to the repo root";
- **environment rules** — the `slot` queue for anything >1 min / >2 G RAM / heavy NFS IO, the
  `slot audit` + `slot status` preflight, and task-local scratch or `/project/tmp` as the only allowed temporary locations;
- **scope fence, in three parts** — the files it may **edit**, the files/dirs it may **create**,
  and an explicit list of what it must not touch (`src/`, untracked scratch dirs, unrelated docs).
  Keep edit and create separate: a fence that only lists editable files silently forbids the new
  test file the task requires, and an executor that needs one will either stop or widen the fence
  on its own. Say where new files go, or say plainly that none are expected;
- **stop conditions** — "do not commit", "do not push", "do not open a PR", "do not merge",
  whichever apply;
- **acceptance** — the literal command and the expected result (`make ci` exit 0, coverage ≥ 84%);
- **literal data keys**, for mapping/data tasks — write the exact source expression and one real
  example (`source_key = strip_chr(chrom) + "_" + str(pos)`), not a semantic shorthand such as
  “original ID”. Name both sides' columns. If the expression is not frozen, the Planner must settle
  it before dispatch; the Executor must not infer it from whichever column happens to join;
- **input integrity**, for generated artifacts — the Planner creates a machine-generated checksum
  manifest before dispatch, lists it as a read-only input, and requires
  `sha256sum -c <manifest>`. Prefer that over copying long hashes into code or prose. If hashes
  must be inline, the Executor reads them from the handoff but never retypes them as source
  constants without a mechanical comparison;
- **publication contract**, for generated deliverables — name an exact in-scope staging path,
  validate the staged artifact, then atomically publish it to the final path. The final path must
  not appear until all data and presentation checks pass, and a repair must rebuild the staging
  file rather than overwrite a partially accepted final artifact;
- **background-job contract**, when applicable — queue, label, command, log, expected artifacts,
  completion test, and who may cancel or retry it;
- **the return address** — your `$HERDR_PANE_ID` (§1), plus the literal command to reach you;
- **report contract** — what to send back (below).

The canonical lint-compatible template is in [SKILL.md](../SKILL.md#3-write-a-self-contained-handoff).
Use comma-separated path-only fence lines; keep explanations on separate lines. Lint forbids any `/tmp`
literal even in a prohibition, so the template positively names allowed scratch locations.
Declare `[报告] <path>` before dispatch and include the report under the create/edit fence. The active
round stores that path so it can be read after compaction even before finish-round records evidence.

### The prompt is one argv argument — 131,071 bytes, hard

`herdr agent prompt <TARGET> <TEXT>` passes `TEXT` as a **single** command-line argument. Linux caps
one argument at `MAX_ARG_STRLEN` = 128 KiB **including the NUL**, so the largest prompt that execs is
**131,071 bytes**. Measured on this box, 2026-08-25: 131,071 delivered, 131,072 failed.

Three things about that limit that will bite you:

- **`ARG_MAX` is a red herring.** `getconf ARG_MAX` reports 2,097,152 here. It bounds the *whole*
  argv+env block, not one argument. Do not size a prompt against it. The per-argument cap is not
  raisable — no `ulimit`, no flag.
- **It counts bytes, not characters.** Chinese is 3 bytes/char in UTF-8, so the ceiling is roughly
  **43,600 汉字**. A report that "looks like 40k characters" can be over.
- **The shell rejects it, so `herdr` never runs.** You get `argument list too long: herdr` from
  zsh/bash — no JSON, no `agent_prompted`, nothing delivered, no partial send. Failure is clean, but
  it is invisible to anything that only greps the output for an error *code* (§4).

`herdr agent prompt` has **no `--file` and no stdin mode** — check `--help` on your build before
assuming otherwise. So the fix is not a bigger prompt, it is a smaller one:

> **Land the payload as a file; send a pointer.** Write the artifact into the working directory
> (`.handoff/<topic>-<date>.md` is a good convention — it survives the session and is greppable
> later), then send a prompt that gives the **absolute or repo-relative path, the instruction to
> read it in full, and a 3–6 line executive summary** so the recipient can tell whether it is urgent
> before opening it.

Pointer prompts beat inline payloads well below the kernel limit anyway. A 100 KB prompt is legal and
still a bad idea: it lands in the executor's context in one undigested block, competing with the work.
**Keep handoff prompts to a few KB.** If yours is heading past ~8 KB, that is the signal to split it
into a file plus a pointer, not the signal to check whether it still fits.

The same rule binds the **executor's report** back to you (below) — a long report is a file plus a
pointer, never a wall of text that may not exec.

> **Observed, this repo, 2026-08-25:** an executor finished a four-section completion report and tried
> to push the full text back through `herdr agent prompt`. 367 KB — nearly 3× the cap. The send died
> in the shell. It recovered correctly (wrote `logs/09DR12_completion_report.md`, sent a short summary
> naming the path), but the round-trip was lost. Nothing in this skill had warned it.

### Report contract

Demand **evidence, not adjectives**. Require:

1. the exact `round_id`, `revision`, `contract_hash`, start state and end state;
2. `git status --short` and `git rev-parse HEAD` (paste raw output);
3. `git diff --stat` for what it changed;
4. the acceptance command's **actual** output tail and exit code;
5. **everything it did not do** — not just hard failures, but **every deferral, every assertion it
   weakened or skipped, every fallback it took**. Ask for it in those words. "Anything you could
   not do" reads to an executor as "did anything crash", and a test quietly relaxed from an exact
   match to a `>= 0` is not a crash. Spell it out: "没做到的事，**含所有推迟项、绕过的断言、
   降级的做法**";
6. **assumptions it made** — every judgment call on something you did not state;
7. **a measurement record for every number it reports** (below);
8. every background job's queue, job ID, label, command, log, current state, expected artifacts,
   completion test, and whether it was cancelled or retried.

Explicitly: "不要报告『已完成』；贴出上面各项的原始输出。"

**Not every working directory is a git repo.** The Git status/HEAD/diff items assume one; plenty of pipeline, panel-design
and data directories are not versioned at all, and there the whole of §5's SHA-and-diff machinery is
unavailable. Say which regime you are in *when you write the prompt*, and in the unversioned case
demand a before/after inventory of the exact scope fence. For small scopes this is
`ls -la --time-style=full-iso`; for large data directories, write a machine-readable inventory with
relative path, type, size and nanosecond mtime, plus SHA-256 for control files and final artifacts.
Diff the inventories and reject paths outside the allowlist. Also require `wc -l` for tables and the
exit code of every command. Fix acceptance artifact paths in the prompt so you can verify them
yourself. Without version control you cannot recover from a bad overwrite, so **require `cp -p`
backups for pre-existing files that may be overwritten**, named in the report.

### Publish generated artifacts once

A generated deliverable has two states: staged and published. Do not use the final customer-facing
path as a scratch file:

1. Generate only the explicitly allowed staging artifact (`.<round_id>.partial`, or a staging
   directory on the same filesystem as the final destination).
2. Run **both** content checks and presentation/format checks against staging. For large XLSX files,
   use streaming/read-only parsing for table data and inspect workbook XML for freeze panes,
   filters, widths, macros and external links instead of loading the whole workbook in normal mode.
3. If any check fails, delete or rebuild only the staging artifact named in the fence.
4. When all checks pass, publish once with an atomic same-filesystem replace/rename. If the final
   path existed before the round, stop unless the handoff names its backup and overwrite policy.
5. Hash and inventory the published artifact; never rebuild it merely to change a timestamp.

The staging path is a created artifact and must appear under `[可以新建]`. “No temporary files”
without an allowed staging path forces the Executor either to violate the fence or to test on the
final file.

### Background jobs need a ledger

Submitting a job is not completing a round. For every `slot`, scheduler, detached or long-running
job, record this ledger before handing control back:

```text
round_id | queue | job_id | label | submitter | exact command | log |
expected artifacts | completion assertion | state | cancel/retry owner
```

The Executor may submit only the job explicitly authorized in the handoff. A successful submission
response proves only that the scheduler accepted it. The Planner verifies the job ID and log path
and records the row with `pairctl.py job-add` / `job-update`. `state.json` is the source of
truth; `jobs.tsv` is rewritten from it on every successful load and must not be treated as a
second source of truth.
On session rollover, pairctl copies all nonterminal ledger rows into the checkpoint and keeps them
in `jobs.tsv`. A new agent may inspect them, but must not cancel, retry or replace a job unless
the user or named owner authorized that action.

**Cap the report at a few KB and point at files for the rest** (§3, 131,071-byte limit). Put it in the
prompt in those words: "回报正文控制在几 KB 以内；长表格、完整日志、完成报告一律落盘，回报里给路径 +
3–6 行摘要，不要把全文塞进 prompt。"

### Numbers need a measurement record

Any value the executor supplies that will end up in a commit, a doc, or your report to the user —
a hash, a count, a distribution, a coverage figure — must arrive with **the script and the exact
command that produced it**, not just the value. Then **recompute it yourself**: your own script,
your own baseline (`git worktree add <dir> <base-sha>` when the number is a base-vs-HEAD
comparison). Do not re-run the executor's script — a shared fixture is a shared error.

Two numbers that disagree is a *fixture* question before it is an *honesty* question (§7).


## Executor responsibilities

1. **Work the fence, not the goal.** Touch only the files listed. If the task cannot be done inside
   the fence, stop and say why — do not widen it and do not "fix things while you are in there".
2. **Do not invent.** No thresholds, weights, IDs, file paths or symbols the Planner did not supply.
   If a value is missing, that is a blocker to report, not a gap to fill. Fabrication is the single
   fastest way to get the pairing killed (§7).
3. **Obey the stop conditions literally.** "Do not commit" means do not commit, even when the work
   looks finished and committing feels helpful.
4. **Report evidence, not adjectives.** Paste the raw output of `git status --short`,
   `git rev-parse HEAD`, `git diff --stat`, and the acceptance command with its exit code. Never
   answer "已完成" alone — the Planner is going to check the repository anyway, and a claim that the
   diff contradicts costs you the seat.
5. **Send the report back, do not just write it.** Finishing your turn does not notify anyone.
   Use `herdr agent prompt <planner_pane_id> '<短报告或报告路径>'` — the prompt should name the pane; if it
   does not, ask for it before you start. Pane ids are per server: a saved machine needs
   `herdr --machine <label-or-id> agent prompt`, and the TUI's selected machine does not retarget
   that call. Send when you are blocked, too, not only when you are
   done. A report that only exists in your own pane is a report nobody received.
   **Keep it under a few KB.** The prompt is one argv argument and dies above **131,071 bytes**
   (≈43,600 汉字) with `argument list too long` — from the shell, so nothing is delivered and you get
   no JSON telling you so. Long reports, full tables and logs go to a file in the working directory;
   send the path plus a 3–6 line summary. Do not discover the limit by hitting it.
6. **List what you did not do.** Every deferral, every assertion you weakened or skipped, every
   fallback you took — not only the things that failed outright. A test you relaxed to keep the
   suite green is the single most important line in your report.
7. **Surface every assumption you made.** This is the item that saves the deliverable.
8. **Every number you report needs a runnable command behind it.** If the Planner cannot reproduce
   it, it does not ship. If you cannot reproduce your own number, say it is withdrawn — do not
   supply an explanation for it.
9. **Do not explain an anomaly with a clause you have not evaluated.** Citing a docstring's escape
   hatch, a "known limitation" or a tolerance is a *measurement*, not a quotation: state the clause's
   condition, then report how many affected records actually meet it. If the answer is "I did not
   check", say the anomaly is unexplained — that is a useful report. A confident wrong reason costs
   the Planner more than an honest open question, because it gets believed and quoted onward.
10. **Sanity-check your numbers against each other before sending.** If a residual counter (deficit,
   unsatisfied, remaining) says zero while another line reports a shortfall of the same thing, do not
   ship either number with a story reconciling them — report the contradiction as the finding.
11. **A reason is a claim too — verify its precondition before you lean on it.** "File X does not
   exist, so I wrote to Y instead" needs a `find` that actually covered X, not one directory you
   guessed. A wrong reason attached to a right outcome is still damage: the reviewer reads it as
   evidence you looked, and stops looking themselves.
12. **Cross-reference by heading or anchor text, never by line number.** Any line number you read
   before an edit is stale after it — and the edit invalidating it is usually the one you are
   writing. This applies to your report as much as to the file.
13. **Environment rules bind you even if your global hook never loaded them.** If the prompt states a
   queue (`slot`), a scratch-directory rule, or a working directory, follow it as written.
14. **Repeat the `round_id` in every report and artifact ledger.** Do not report against a different
    or stale round.
15. **If you are the Verifier, enforce the review epoch.** Work read-only, check the epoch before and
    after, and report `STALE_REVIEW` if anything drifted. Never approve while the writer is active.

If a prompt arrives that is stale — it references a state that no longer holds, or repeats work
already landed — do not execute it. Say so and ask. A stale prompt executed confidently is how two
agents end up overwriting one file.
