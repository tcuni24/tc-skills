---
name: herdr-pair
description: Use when the user asks to run a task with two Herdr agents splitting planning/review from execution — “你负责规划和审核，让 X 执行”, “协同两个 agent”, “把活派给 herdr 里的 X，你把关”, “让它把结论返回给你”, “你拆一下任务再派给 X”. Roles are agent-type agnostic — either side may be claude, opencode, codex, droid, cursor, hermes, omp, kimi, pi, grok or anything else on the roster. Covers role contracts, cutting the work into verifiable rounds, self-contained handoff prompts, the 131,071-byte prompt limit and “argument list too long” recovery, artifact-based verification with and without git, auditing an executor’s explanations and not just its numbers, the concurrent-writer hazard, and the quality kill-switch. Not for one-shot relays — use herdr-handoff for those.
---

# Herdr Pair: Planner + Executor (+ optional Verifier)

Planner owns scope, acceptance and the user's deliverable; Executor edits and runs commands.
Roles are seats, independent of agent type.

## Which seat are you in

The agent holding the user's task is Planner; a scoped handoff makes you Executor (follow
“If you are the executor”). A Verifier reviews read-only after writing stops and the Planner
freezes a review epoch; follow [verification.md](reference/verification.md#stable-review-epochs).
A later write invalidates that review.

## The one rule that matters

**The Planner is accountable for every line the Executor writes. If you cannot vouch for it,
it does not ship.**

## When NOT to pair

Do judgment, spec authoring, decisions, short work (under about 15 minutes), and previously failed
delegations yourself. Pair for wide implementation, repetitive edits and long verification.
Precision work is pairable with a frozen read-only spec and exact definitions in the contract.

## 0. Preflight

Run `test "${HERDR_ENV:-}" = 1`, `herdr --skill`, and `herdr agent prompt --help`.
Outside Herdr, report the missing environment. The installed CLI is authoritative.

Locate `PAIRCTL=<skill-dir>/scripts/pairctl.py`. Use it for every dispatch; it passes file contents
as argv without shell expansion. State defaults to
`$XDG_STATE_HOME/herdr-pair/<cwd-hash>` (otherwise `~/.local/state/...`).
`state.json` is authoritative; `jobs.tsv` is derived. Each handoff must give the same absolute
`PAIRCTL`, canonical `--cwd` and exact `--state-dir` or default-state selection.

```bash
python3 "$PAIRCTL" init --planner-pane "$HERDR_PANE_ID" --goal '<one-sentence goal>'
python3 "$PAIRCTL" context-usage
python3 "$PAIRCTL" note --text '<user decision, clarification or scope revision>'
python3 "$PAIRCTL" send-round --target <pane> --file /abs/handoff.md --scope '<fence>' --acceptance '<command>'
# Only to register a complete task already delivered manually:
python3 "$PAIRCTL" start-round --file /abs/contract.md --executor <pane> --scope '<fence>' --acceptance '<command>'
python3 "$PAIRCTL" diff-round --round-id <id> [--revision N]
python3 "$PAIRCTL" finish-round --report /abs/report.md --round-id <id> --status accepted --artifacts '<paths>' --notes '<verified evidence>'
python3 "$PAIRCTL" checkpoint --reason '<boundary>'
python3 "$PAIRCTL" status
```

Executor commands: `check-round`, `ack-round --action accept|start` (full protocol below).
Recovery commands: `resolve-pending --outcome delivered|not-delivered`,
`adopt-contract --round-id <id> --file <contract>`, `compact-self`,
`rollover --reason compact --new-session-id <id>`.
Job commands: `job-add` and `job-update`; copy their exact fields from
[handoff.md](reference/handoff.md#background-jobs-need-a-ledger). Use `--help` for arguments.

### Context budget and recovery

`context-usage` measures the last non-synthetic Claude assistant input plus cache creation and
cache read tokens. The SessionStart hook records the current Claude session even before init.
Missing/mismatched transcripts and non-Claude planners return `unknown`; never invent a count.
Default budget is **150,000 tokens**, configurable with `--budget`,
`PAIRCTL_CONTEXT_BUDGET` or `init --context-budget`; age threshold is **12 hours**
(`PAIRCTL_SESSION_STALE_HOURS`).

After init, over-budget or stale sessions write a checkpoint and queue planner compaction
(`CONTEXT_COMPACT_QUEUED`). `init --no-context-check` skips this precheck.
After confirmed `agent_prompted`, send-round automatically queues compaction if over budget;
unknown usage and unresolved pending dispatch never trigger it.
`init --no-auto-compact` or `PAIRCTL_AUTO_COMPACT=0` disables all automatic compaction,
including the five-round fallback. Unknown usage keeps that fallback: checkpoint at three rounds,
finish round five before continuing to another phase.

An unconsumed queued compact blocks the next dispatch with exit 20; finish the turn so it can run.
The watcher resumes the planner after idle; disable only this continuation with
`PAIRCTL_CONTINUE_AFTER_COMPACT=0`. Claude's SessionStart hook handles recovery; other planners
record it with `rollover --reason compact` after compaction.
**Read status and each active round's report file first: the executor may have completed during
compaction.** Read the full checkpoint from its path; hook injection contains only its header and
Resume steps. Use `note` for decisions rather than leaving them solely in chat.
宿主是否支持 1h TTL 待核实。

For uncertain delivery, freshness failures, compaction/rollover and watcher troubleshooting,
read [recovery.md](reference/recovery.md). Do not enable global Factory/Cursor hooks or change
their settings for this workflow.

## 1. Resolve the executor

Record your `HERDR_PANE_ID` as the return address and run `herdr agent list`; exclude yourself.
Filter by type only if the user named it. Prefer same tab, then same workspace + cwd, then same cwd
anywhere. Stop at the first ambiguous level and ask which pane; address the unique `pane_id`.
Respect whether the user's writer choice covers one round or the entire task.

Status and quota banners are routing hints. Conflicting screen/status means unknown: inspect
before clearing or resending. Re-list a missing/recycled pane; do not silently substitute.
Before resolving ambiguity, unavailability or a fresh-command override, read
[executor-resolution.md](reference/executor-resolution.md).

## 2. Cut the work into rounds

One round has one scope fence, a literal acceptance result and a diff you can sign independently.
Carry verified inputs into the next contract. Read the governing spec end-to-end before splitting;
raise conflicting requirements explicitly rather than having the executor reinterpret them.
Drive rounds sequentially; isolated worktrees are required for separate writers.
Use the five-round fallback above. Request an ETA once, check after it, then request stop/report
if overdue; stop repeated polling. For spec audits and budget recovery, read
[recovery.md](reference/recovery.md).

## 3. Write a self-contained handoff

Assume zero inherited rules, including for agents that scan only workspace instructions.
Include identity/revision, absolute cwd/state, goal, disjoint edit/create/forbidden fences,
read-only inputs, environment, stop conditions, literal acceptance, report path and return pane.
Use exact data expressions and measurement provenance where relevant. Name staging paths and
input checksums for generated deliverables; name queue/log/completion/owner for background jobs.

The lint is fail-closed: overlapping edit/create/forbidden paths (including directory prefixes),
any `/tmp` literal, untracked writes forbidden by the contract, or a missing `[环境]` line
refuse dispatch before pending or round consumption. Temporary files belong in a cwd subdirectory
or `/project/tmp`; the positive wording below also satisfies lint. Use
`--skip-lint '<reason>'` only for a deliberate recorded exception. Use comma-separated paths on
each fence line; keep prose and exceptions on other lines.

```text
[轮次] round_id=<unique-id>
[版本] revision=1
[执行者] pane=<executor_pane_id>
[状态位置] PAIRCTL=/abs/path/pairctl.py；cwd=/abs/project；state=default 或 --state-dir=/abs/state
[目标] One measurable outcome.
[工作目录] /abs/project；所有命令在这里运行。
[可以改] docs/a.md
[只读输入] inputs/source.tsv
[可以新建] tests/contracts/test_x.py, reports/round-report.md, .handoff/staging.xlsx, outputs/final.xlsx
[不许动] src/, docs/unrelated.md
[环境] 超 1 分钟 / 超 2G 内存 / 重 NFS IO 走 slot；先 slot audit + slot status 并记录日志。
中间文件仅放已确认的 cwd 临时子目录或 /project/tmp；日志与暂存路径也必须在本轮 fence 内。
[停在哪] 完成后停止写入；不要 commit / push / 开 PR。
[验收] <literal command>；期望 <literal result>。
[报告] reports/round-report.md
[后台作业] 无；或列 queue/label/command/log/artifacts/completion/cancel owner。
[协议操作] 同一 cwd/state 查询 check-round --revision 1（接受前预期 exit 2）；
核对完整 contract_text/path/hash、scope、acceptance、executor、revision；
用返回的 scope/hash 依次 ack-round --action accept、ack-round --action start；
每次写入前 check-round 必须 allowed=true。
[回报] 验收完成 -> 立即落盘完整报告 -> herdr agent prompt <planner_pane_id> '<路径 + 3–6 行摘要>'。
被卡住也要发送。报告包含 round_id/revision/hash、前后状态、改动、命令/退出码/原始输出、
所有推迟/跳过/降级项、假设、数值复算命令及后台作业。回报正文几 KB，其余指向文件。
```

Replace placeholders and fence paths with actual inputs, outputs and permitted task scratch paths.
For precise data keys, checksums, publication, report size, job ledger and measurement requirements,
read [handoff.md](reference/handoff.md) before dispatch.

## 4. Send

Use `send-round`. It lints and snapshots fence files, clears the executor by default, verifies its
fresh-session screen, then records pending before delivery. Only `agent_prompted` commits an
active round. `fresh_failed` consumes nothing; resolve the actual configuration/state problem.
`--no-fresh` deliberately retains context and needs a recorded reason.

Uncertain delivery stays pending; inspect the target before explicit `resolve-pending`.
Never blindly resend. The snapshot is the **pre-dispatch working directory**, including untracked
files, not an old HEAD. `snapshot: null` with warnings means no paths were parsed; obtain a valid
baseline before relying on it. Files above 50 MB are hashed without backup copies.
`diff-round` returns changed/added/removed/unchanged and exit 1 for differences, exit 0 otherwise;
read changed content as well. Details: [recovery.md](reference/recovery.md).

## 5. Verify by artifact, never by status

Read the on-disk report and compare the current artifacts with the round snapshot using
`diff-round`; in Git also inspect working-tree/staged changes, status and `git diff --check`.
Read every relevant diff and verify acceptance. Check scope, identifiers, supplied definitions
and the precise failure you warned about. Recompute reported numbers and evaluate explanations'
preconditions over all affected records. A callback is a notification, not evidence.
For full verification, non-Git work and review epochs, read
[verification.md](reference/verification.md) before accepting.
`finish-round --status accepted` requires nonblank artifacts and notes; any supplied
`--report` must exist. Always supply the report path at acceptance.

## 6. Concurrent writers

One writer per cwd. Park stale write-capable prompts and confirm stopping before transferring
ownership or freezing review. Preserve others' work and inspect the whole diff. Avoid shared
unnamed stashes; if needed capture a uniquely named stash's SHA and apply that exact SHA.
Use separate worktrees for separate writers. Drift invalidates review.

## 7. Quality gate — the kill-switch

Stop and report evidence when claimed edits are absent, numbers have no provenance, the same
instruction is missed twice, the fence is crossed, explanations fail twice, or you rewrite most
output. First rule out fixture mismatch before calling a number fabricated.
Recommend taking over or changing executor within the user's authorization.
Read [verification.md](reference/verification.md#quality-gate) when judging disputed evidence.

## 8. Close out

Run acceptance and diff checks, reconcile jobs, and enumerate every spec criterion as
pass/fail/not-run. Record the report, artifacts and verification notes with finish-round.
Report actual changed files, verification, outstanding items and who did which work; include a
commit SHA only if committed. Follow the user's authorization for publication.
For request routing and troubleshooting examples, read
[common-requests.md](reference/common-requests.md).


## If you are the executor

### Executor 协议执行闭环（必须严格遵守）
在无其他上下文的情况下，Executor 必须按以下 7 步完成任务，禁止跳步：
1. **检索并核对权威契约**：使用 Planner 指定的同一 `--cwd` 与 `--state-dir` 调用
   `check-round --round-id <dispatched-id> --revision <dispatched-revision> --pane "$HERDR_PANE_ID"`。
   接受前该命令预期 exit 2、`allowed=false`；命令返回已验证的完整 `contract_text`、
   `contract_path`、`contract_hash`、`scope`、`acceptance`、`executor` 和 `revision`。
   将这些值与收到的轮次、版本、身份和指令逐项核对后，才可继续。省略 `--revision` 只用于发现，
   同样 exit 2 且 reason=`missing_revision_query_only`，不能据此选择或接受不同版本。
2. **发送接受回执**（工作状态进入 `accepted`）：
   ```bash
   python3 "$PAIRCTL" ack-round --round-id <round_id> --revision 1 --pane "$HERDR_PANE_ID" \
     --scope '<fence>' --contract-hash '<contract_hash>' --action accept
   ```
3. **发送开始执行回执**（工作状态进入 `running`，禁止从 pending_acceptance 直接 start）：
   ```bash
   python3 "$PAIRCTL" ack-round --round-id <round_id> --revision 1 --pane "$HERDR_PANE_ID" \
     --scope '<fence>' --contract-hash '<contract_hash>' --action start
   ```
4. **每次新的写入步骤前进行写入资格检查**：
   ```bash
   python3 "$PAIRCTL" check-round --round-id <round_id> --revision 1 --pane "$HERDR_PANE_ID"
   ```
   强制必须传 `--revision`。若返回 `allowed: false`，立即停止写入并向 Planner 上报阻断原因。
5. **在 fence 范围内执行编辑与测试**：严格遵守 stop 条件与范围限制。
6. **落盘完整执行与验收证据**。
7. **发送短回报给 Planner**：通过 `herdr agent prompt` 回报结论与证据。

For evidence, scope, reports, assumptions, numerical provenance and stale prompts, follow
[Executor responsibilities](reference/handoff.md#executor-responsibilities). Always use the
Planner's cwd/state selection on every protocol command.
