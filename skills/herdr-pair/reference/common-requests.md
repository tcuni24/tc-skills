# Common requests

| User wording | Behavior |
|---|---|
| “你负责规划和审核，让 <任意 agent> 执行” | Full loop: §1–§8, Planner signs the result |
| “任务很大，你拆一下再派” | §2 切轮次，一轮一个 handoff；串行驱动，不要同 cwd 并发派发 |
| “协同两个 agent”, no type named | Planner seat is yours; pick the executor by `cwd` + availability, not by type |
| “按不同难度派给不同执行者” | Choose the best writer per round, record it in `round_id`, and preserve one active writer per cwd |
| “让 A 执行、B 独立验收” | A writes that round; freeze a review epoch before B reads; any later write invalidates B's conclusion |
| “这是长时间 slot/后台任务” | Add a background-job ledger row and carry nonterminal rows through every checkpoint/session |
| Pairing reaches round 5 | `finish-round` exits 20, writes the checkpoint, and queues your own compact command (cursor `/summarize`; claude/pi `/compact …`; droid `/compress`); finish your report, let it run. pairctl then waits for the planner pane to go idle and prompts it to rollover + continue. Claude Code also records rollover from SessionStart; budget compaction may occur earlier while a round remains active. Disable auto-continue with `PAIRCTL_CONTINUE_AFTER_COMPACT=0` |
| “每轮都要我手动去 worker 那边 /clear” | Not any more: `send-round` clears the executor (`/new` / `/clear` per kind) and verifies the screen before sending. `fresh_failed` = nothing sent; read the error |
| “/compact 之后它没接着干” / Cursor `/summarize` / “为什么又停下了” | pairctl now waits for idle after compact and prompts the planner to continue; you should not have to ping it. If it still stops, check `compact-continue.log` next to the pairing state and `PAIRCTL_CONTINUE_AFTER_COMPACT` |
| Named type is not live anywhere | Say so, offer live candidates, do not silently substitute |
| “让它把每次的执行结果的结论返回给你” | Put your `$HERDR_PANE_ID` in the prompt (§1); enforce the §3 report contract; verify per §5 before accepting |
| “它跑完了，为什么没发回给你” | You omitted the return address (§1). The work is likely done — go read the repo, then re-send the report contract with the pane id |
| “这个数对不上” | Ask for the script and command first (§7). Fixture mismatch before fabrication |
| “全文发不出去 / argument list too long” | [handoff.md](handoff.md) 上限 131,071 字节（单个 argv 参数）。落盘 + 指路，不要想办法把全文塞进 prompt |
| “这个目录不是 git 仓库，怎么验” | [verification.md](verification.md)：先用 `diff-round` 对比轮前快照，再读内容、复算 hash/行数；大文件另留允许的备份 |
| “它给的理由听着挺合理” | [verification.md](verification.md)「解释也是断言」：先把免责条款的**前提条件**在全量数据上验一遍，再看它给的几个数彼此矛不矛盾 |
| 送出去返回了 `agent_prompted`，但对方像是没收到完整内容 | [recovery.md](recovery.md)：用了 `"…"` 包正文，Markdown 反引号被 shell 命令替换吃掉。看 stderr 有没有 `no such file or directory`；立即「上一条作废」+ 落盘指路重发 |
| “我在工单里明明写了不要 X，它还是 X 了” | [verification.md](verification.md)「先查你警告过的那条」。指令不等于保证，且证据往往就在回执自己贴的 diff 里 |
| “质量太低就停下来告诉我” | §7 kill-switch is armed; report the signal and evidence, do not push through |
| “把这个结论同步给 X” | Wrong skill — use `herdr-handoff` |
| Executor already failed this task once | Do not re-delegate. Do it in your own pane and say so |

| `init` 返回 `CONTEXT_COMPACT_QUEUED` | 结束当前 turn 让 compact 执行；恢复后先读 status、完整 checkpoint 和 active 报告。未消费队列前派发 exit 20 |
| `context-usage` 返回 unknown | 不猜用量，不自动按预算压缩；保留三轮检查点、五轮后备规则 |
| `handoff_lint` | 按 findings 修正路径交叉、临时路径字面量、untracked 冲突或环境行；刻意例外用 `--skip-lint '<原因>'` 留痕 |
| `missing_acceptance_evidence` / `report_missing` | 补齐非空 artifacts/notes 和已经落盘的报告；accepted 前原状态不变 |
