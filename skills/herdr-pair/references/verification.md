# Verification

## Stable review epochs

An independent review is valid only against a frozen artifact state:

1. Park the writer and confirm its report names the `round_id`.
2. Capture `HEAD` plus `git status --short` in Git, or the scoped inventory digest in a non-Git
   directory. Call this `review_epoch`.
3. Send the Verifier the `round_id`, `review_epoch`, read-only fence and acceptance ledger.
4. The Verifier checks the epoch before and after review. Any drift means `STALE_REVIEW`, not pass
   or fail.
5. If the writer changes anything, invalidate all earlier review conclusions and create a new epoch.

Never ask a Verifier to “keep reviewing” while the Executor fixes files. Pause it first, then restart
from the new epoch. A conclusion against an unknown mixture of versions does not count.

## 5. Verify by artifact, never by status

This is the step that gets skipped, and skipping it is how a pairing session produces nothing while
reporting success.

Check the **repository**, not the agent:

```bash
git status --short
git rev-parse HEAD
python3 "$PAIRCTL" diff-round --round-id <id>  # exit 1 = differences to inspect
git diff --stat
git diff --cached --stat
git diff --check
```

Use the round's pre-dispatch snapshot as the working-tree baseline; an old HEAD does not capture
pre-existing uncommitted edits or untracked files. Inspect changed content against the saved files,
and check status/inventories for out-of-fence changes: diff-round is scoped to the declared fence.
Large files are hashed without copies, so arrange a separate allowed backup before overwriting them.

Then **read the diff line by line.** Not skim — read. You are about to sign it.

Reject and re-drive when the diff contains:

- **any number, threshold, weight or ID you did not supply** — executors invent plausible values;
- **identifiers that do not exist** — `rg` every file path, symbol and gate ID it introduced;
- claims that contradict your spec (swapped gate IDs, wrong module landing, wrong formula);
- files outside the scope fence;
- a commit, push, or PR you did not authorize.

> **Observed, this repo, 2026-08-24:** an executor reported success while `HEAD` had not moved from
> `311b093` — zero files changed, zero commits. A later round wrote confidently wrong content into
> two docs: the authoritative module was named as `alignment/off_target_metrics.py` when the spec
> said `alignment/off_target.py`, and two gate IDs (G6/G7) were swapped. Both read fluently. Both
> would have shipped without a line-by-line read.

### No git? Verify the artifacts directly

The header says *artifact*, not *commit*, on purpose. In an unversioned directory the principle is
unchanged and only the commands move:

```bash
ls -la --time-style=full-iso <every file the fence allowed>   # mtime + size is your diff
sha256sum <output artifacts>                                  # compare to the reported digests
wc -l <tables>                                                # row counts you predicted
diff <(…) <(…)                                                # against the .bak the round was told to keep
```

Read the **content** of what changed, not just that it changed — the same line-by-line duty as a diff.
And be sharper about scope than you would be with git: there is no `git status` to reveal a stray write
outside the fence, so diff the before/after inventory and inspect anything whose path or metadata
changed. For large generated datasets, hash final artifacts and control files rather than blindly
hashing every multi-gigabyte intermediate.

### An explanation is a claim — check its precondition

The §5 list catches invented *values*. It does not catch an invented *reason*, which is the more
durable failure: a wrong number gets disputed, a wrong explanation gets adopted and quoted onward.

Executors reach for the nearest legitimating clause in the code or spec — a documented escape hatch,
a "known limitation", a tolerance. The clause usually is real. What goes unchecked is whether its
**precondition holds on this data**. So check that separately, against the artifact:

- Quote the clause's condition verbatim from the source, then evaluate it yourself over **every**
  affected record — not a sample, not the three the executor listed.
- **Bulk-parse the enumerated lines and let them outvote the summary line.** A report that lists N
  anomalies individually is self-checking: the detail rows are evidence, the one-line roll-up on top
  is narration. When they disagree, the rows win.
- **Cross-check the executor's numbers against *each other*, not only against your own recomputation.**
  Internal contradiction is the cheapest defect to find — it needs no fixture, no rerun, no baseline —
  and it is conclusive: at least one number is wrong no matter who produced it. Look hardest at pairs
  where one is a *residual* (deficit, unsatisfied count, remaining budget, leftover) and the other is
  an independent recount of the same thing. A residual of **0** sitting next to a recount reporting a
  shortfall is the canonical shape, and it means the accounting is broken, not that one view is
  "stricter".
- If the clause's condition is a threshold ("fewer than K available"), compute the distribution of the
  quantity. "Zero of 1,164 records meet the condition" is a one-command refutation.

> **Observed, this repo, 2026-08-25:** a set-cover step reported 1,164 events short of their K=4 quota
> and explained them as "随机实现" — the script's own docstring does say events with **fewer than 4
> surviving candidates** are random realizations, not defects. Parsing all 1,164 detail lines showed
> **1,164 of 1,164 had ≥ need survivors**, so the clause covered none of them; 409 had ≥4 available and
> selected **zero**. The same run also printed `unsat_slots = 0` — the solver's own deficit counter,
> which can only reach zero if every event was served in full — beside a recount of 3,140 unfilled
> slots, and the recount used the *looser* of the two criteria, so it should have found fewer shortfalls,
> never more. That pair alone was conclusive before any hypothesis about the cause. The docstring had
> also required these events be "单独列出供裁定，不自动处置"; they had been auto-disposed. Nothing here
> required rerunning anything — only reading the artifact against the clause it invoked.

**When you find this, freeze the affected section rather than fixing it.** Downstream numbers computed
from a broken accounting are neither pass nor fail — they are **UNKNOWN**, and saying so is the whole
value. Send the executor the refutation, the ruled-out hypotheses (so it does not re-walk them), and at
most one *labelled-unproven* lead; then let it locate the defect before anyone decides whether to
re-run. An executor handed a lead as if it were a conclusion will confirm it.

### Check the failure you warned about first

The hazard you spelled out in the work order is the one most likely to come back in the receipt —
an instruction is not a guarantee, and a receipt that reads "done" is written by the same agent
that missed it. So verify **your own warning** before anything else, and expect the evidence to be
sitting in the receipt already.

> **Observed, this repo, 2026-08-26:** the order said to anchor a cross-reference by section
> heading, "**不要写死行号**（追加内容本身会让行号漂移）". The executor wrote `L3006` — and its own
> receipt quoted the hunk `2350a2351,2353`, three inserted lines, which had moved the target to
> L3009. L3006 now named a *different* backlog entry. The proof of the defect was inside the
> report claiming success.

Generalize it in the handoff rather than re-warning per task: **cross-references use headings or
anchor text, never line numbers** — any line number read before an edit is stale after it, and the
edit that invalidates it is often the same one being written.


## Quality gate

Track quality across rounds. **Stop and tell the user** — do not silently absorb the work — when any
of these fires:

| Signal | Meaning |
|---|---|
| Reported edits, but no change from the round baseline | The executor is not actually executing |
| Fabricated numbers or non-existent identifiers | It is filling gaps with invention |
| Same instruction missed twice | It is not reading the prompt |
| Scope fence crossed after being told | Instructions are not binding on it |
| Explanations dissolve on inspection, twice | It is narrating over unread data, not analysing it |
| You are rewriting most of its output | Pairing now costs more than solo |

Report to the user as: what was asked, what came back, which signal fired, and the concrete
evidence (SHA, diff, quoted output). Then recommend either a stronger model/agent or taking the
task back into your own pane. **Do not keep re-prompting a failing executor** — that burns the
user's time and yours.

Finishing the work yourself is always a legitimate outcome. Say that you did.

### Irreproducible is not the same as fabricated

The fabrication row is the one you will misfire on. When a number the executor reported does not
reproduce on your machine, you have **two** hypotheses, and invention is the second one to test:

1. **different fixture** — you measured something else. Fixtures with defaulted, non-exposed
   parameters are the usual culprit: a helper that hardcodes a field and only lets the caller vary
   the row count will happily produce two honest, different hashes;
2. **invention** — the number never came from a run at all.

Distinguish them by **asking for provenance before judging**: the script, the command, the raw
output. Word the ask so that "作废" is a cheap answer — "若你也复不出来，就说这个数作废，不要
补一个解释". An executor that produces a runnable script whose output you can reproduce has given
you a fixture mismatch, not a fabrication; both numbers are real and neither belongs in the commit
until you have recomputed the right one (§3, measurement record).

The row fires when the number **cannot be traced to any run** — no script, shifting explanations,
or a value that entered code or docs unmeasured. It does not fire on a mismatch you have not yet
investigated, and it does not fire on a number that was measured, disputed, and withdrawn.
