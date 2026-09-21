# herdr-pair：规划者上下文预算、恢复契约与短核心协议

关联 issue：<https://github.com/tcuni24/tc-skills/issues/2>

来源：[HYF 会话成本审计](../../analysis/herdr-pair-hyf-20260921/REPORT.md)（§2、§6、§9）。
关联：issue #1 的工单 04（冻结与漂移）、08（验证预算）——本 spec 的快照与验收条目为其前置最小版，命名与清单格式按 04 的 `review_epoch` 设计，04 直接扩展而不重做。

状态：决策已定（下表），待实施。

## Problem Statement

审计确认：规划者每次超过 5 分钟的等待都会让下一次调用按缓存创建费率重写整段上下文（49 次 >5 分钟间隔中 46 次整段重建，≤5 分钟的 666 次中 0 次）。配对工作流中"等执行器 >5 分钟"是结构性的，因此 skill 唯一能控制的变量是**上下文长度**。本次会话从 540k tokens 历史开始配对、五轮后才压缩，第一阶段估算费用约 69% 来自开局已携带的历史；同样的调用序列若压在 150k 内，估算约为原来的四分之一。

配套缺口：检查点只有轮次表与后台作业，`finish-round` 的 `--artifacts/--notes` 全空，范围修订与用户决策丢在聊天里，压缩后无法机械恢复；验收对比旧 HEAD 而非轮前工作区；工单 fence 自相矛盾、`/tmp` 禁令缺失未被拦截；SKILL.md 989 行 / 57 KB，压缩后宿主只回灌前 20k 字符，hook 注入上限 16k 字符，恢复上下文里进的是截断版。

## Decisions

| # | 决策 | 定为 |
|---|---|---|
| D1 | `init` 前上下文过大的处理 | `/compact`（保留摘要），阈值 **150,000 tokens**；或会话首条消息距今 **>12h** 也触发 |
| D2 | 派发后等待期压缩 | `send-round` 进入 active 后自动检查用量，**超阈值自动排队 `compact-self`**；用量 unknown 不自动 |
| D3 | 用量来源 | 新增 `context-usage`：Claude 规划者读 hook 记录的 transcript JSONL 最后一条 assistant usage；非 Claude 或无法定位 → `unknown` |
| D4 | 检查点 schema | 见"Checkpoint"节 |
| D5 | 检查点超 16k 注入 | hook 注入头部 + Resume 步骤 + 文件路径，正文要求规划者读文件 |
| D6 | 轮前基线快照 | 本次做最小版：`send-round`/`start-round` 复制 fence 内文件 + sha256 清单；新增 `diff-round` |
| D7 | 派发前 lint | 三项 fail-closed：fence 集合交叉、工单含 `/tmp` 字面量、untracked 冲突；不做行数上限 |
| D8 | 拆短核心 | **本次一起做**，核心 `SKILL.md` **< 15,000 字符** |
| D9 | 五轮规则 | 保留为后备，不动 |
| D10 | 流程 | 先发 issue，分支实施 |
| D11 | 缓存 TTL | 不在范围；核心里一句"宿主是否支持 1h TTL 待核实" |
| D12 | `finish-round` fail-closed | `--status accepted` 时 `--artifacts`、`--notes` 为空拒绝；新增 `--report <path>` 且路径必须存在 |
| D13 | 参考材料位置 | `skills/herdr-pair/reference/*.md` |

## Solution

### A. `context-usage`

```
python3 pairctl.py context-usage [--cwd] [--budget N]
```

输出 JSON：`{"status": "ok"|"unknown", "context_tokens", "budget", "over_budget", "session_started_at", "session_age_hours", "stale", "source", "measured_at", "reason"}`。

- `context_tokens` = transcript 最后一条非 synthetic assistant 消息的 `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`（与审计 `audit.py` 同口径）。
- `budget` 默认 150,000，可由 `--budget`、`PAIRCTL_CONTEXT_BUDGET`、`init --context-budget` 覆盖；`stale` = 首条消息距今 >12h（`PAIRCTL_SESSION_STALE_HOURS`）。
- transcript 定位：state 中 `planner_session.transcript_path`（hook 写入）；没有则按 `~/.claude/projects/<escaped-cwd>/<session_id>.jsonl` 推导，标 `source: derived`；仍找不到 → `unknown`，`reason` 说明。**不猜数字。**
- transcript 必须包含与记录一致的 `sessionId`，否则 `unknown`。

### B. hook 记录会话

`claude_session_start_hook.py` 在**所有** source（startup/resume/compact/clear）上，无论是否已有配对状态，都调用
`pairctl note-session --session-id <id> --transcript-path <path> --kind claude --source <source>`。
pairctl 把它写到 `<state-root>/planner-session.json`（cwd 级别，独立于配对 state，因为配对可能在会话开始很久之后才 `init`）；`init` 和 `context-usage` 读取它。hook 保持"任何失败 exit 0 无输出"。

### C. `init` 预检

- `init` 新增 `--goal '<一句话目标>'`（可选，写入 state，进检查点与 compact 焦点指令）与 `--context-budget N`、`--no-context-check`。
- `init` 成功写入 state 后运行 `context-usage`：`over_budget` 或 `stale` 为 true → 写检查点（reason `init_context_budget`）、排队 `compact-self`、spawn continue watcher，输出 `status: CONTEXT_COMPACT_QUEUED`，exit 0。
- 已排队且未消费（同一 `compaction_epoch`）时，`send-round`/`start-round` 以 exit 20 重报排队命令（复用现有机制），不派发。
- `unknown` → `init` 正常完成，输出 `context_usage.status: unknown`，skill 规则要求规划者按 D9 五轮后备。

### D. 派发后自动压缩

- `send-round` 得到 `agent_prompted` 并把轮次记为 active **之后**（pending 未解决时绝不触发），运行 `context-usage`；`status: ok` 且 `over_budget` → 写检查点（reason `post_dispatch_budget`）、排队 `compact-self`、spawn watcher；输出附 `planner_compact`。
- compact 焦点指令必须点名：检查点路径、当前 round_id/revision、工单路径、执行器 pane、"执行器回报可能已到达，恢复后先读 `status` 与报告文件"。
- `PAIRCTL_AUTO_COMPACT=0` 或 `init --no-auto-compact` 同时关闭 C、D 与原五轮压缩。

### E. 检查点 schema（D4）

`write_checkpoint` 新增：

- 头部：`Goal`（`init --goal`）、`Context usage`（最后一次 `context-usage` 读数或 unknown）、`Budget`。
- 每轮一行增加列：`Revision`、`Contract`（工单路径）、`Report`（`finish-round --report`）、`Snapshot`（清单路径）、`Artifacts`、`Notes`。
- `## Decisions`：`pairctl note --text '<决策/澄清/范围修订>'` 追加的带时间戳条目（存 state `notes[]`）。
- `## Resume` 增加："先读每个 active 轮的 Report 路径是否已出现"。
- hook 注入内容 = 头部 + `## Resume` + `Full checkpoint: <path>`；正文不注入（D5）。测试断言注入体 < 16,000 字符且含路径。

### F. `finish-round` fail-closed（D12）

`--status accepted` 且 `--artifacts` 或 `--notes` 为空白 → exit 2，`reason: missing_acceptance_evidence`，状态不变。`--report <path>` 新增；给出时路径必须存在，否则 exit 2 `reason: report_missing`。其他 status 不受限。

### G. 轮前快照与 `diff-round`（D6）

- `send-round`/`start-round` 从工单文件解析 `[可以改]`、`[只读输入]`、`[可以新建]` 行（逗号/中文逗号分隔，相对 cwd），对**存在的**文件复制到 `<state>/snapshots/<round_id>-r<revision>/`，写 `manifest.json`：`[{path, sha256, size, mode, exists}]`，含 `[可以新建]` 中尚不存在的路径（`exists: false`）。目录按文件递归；单文件 >50 MB 只记 hash 不复制并标注。
- 轮次记录 `snapshot: {manifest, files, skipped}`；解析不到任何路径 → `snapshot: null` 并在输出 `warnings` 中说明，不阻塞。
- `diff-round --round-id <id> [--revision N]`：对比当前工作区与清单，输出 `{changed, added, removed, unchanged}`；有差异 exit 1，无差异 exit 0。不依赖 git。
- 清单字段与 04 的 `review_epoch` 保持可扩展（04 增加 symlink 目标、权限变化即可）。

### H. 派发前 lint（D7）

`send-round`/`start-round` 在写 pending 之前对工单文件检查，任一失败 exit 2、`reason: handoff_lint`、`findings: [...]`，不消费轮次：

1. `fence_overlap`：`[可以改]`、`[可以新建]`、`[不许动]` 三集合两两交叉（路径前缀命中也算，如 `tests/` 与 `tests/x.py`）。
2. `tmp_path`：正文匹配 `(^|[^\w])/tmp(/|\b)`。
3. `untracked_conflict`：cwd 是 git 仓库且 `[不许动]` 含"未跟踪"/"untracked" 字样时，`[可以改]`∪`[可以新建]` 中任一路径在 `git status --porcelain` 为 `??`。
4. `[环境]` 行缺失 → `env_block_missing`（§5 指出 r006–r009 全部漏掉 `/tmp` 禁令）。

`--skip-lint '<原因>'` 可越过，原因写入轮次记录。

### I. 短核心 + reference/（D8、D13）

- `SKILL.md` 保留 front matter 与：座位判定、唯一规则、何时不配对、Preflight（含 pairctl 命令清单与 context-usage/budget 规则）、解析执行器（精简）、切轮（精简）、工单必含项与模板、发送、按产物验收（精简）、并发写者（一段）、质量门、收尾、执行器协议闭环（完整）。
- `reference/handoff.md`（§3 细节、argv 131,071 上限、报告契约、发布契约、后台作业台账、数值测量记录）、`reference/verification.md`（§5 全部）、`reference/recovery.md`（失败路径 A–F、压缩/rollover/watcher 细节、pending 恢复）、`reference/executor-resolution.md`（§1 细节、可用性语义）、`reference/common-requests.md`。核心中每节末尾给出对应 reference 链接。
- 新增测试：`len(SKILL.md.read_text()) < 15000`；核心必须包含字符串 `context-usage`、`/tmp`、`check-round`、`ack-round`、`finish-round --report`。
- `skills-lock.json` 若记录文件清单需同步。

## Acceptance criteria

- [ ] `context-usage` 在给定合成 transcript 时返回正确 `context_tokens`（= 最后一条 assistant 三项之和）、`over_budget`、`stale`；transcript 缺失、`sessionId` 不匹配、非 Claude kind 时返回 `unknown` 且不输出数字。
- [ ] hook 收到含 `transcript_path` 的 payload 时写入 `planner-session.json`，即使 cwd 尚无配对状态；payload 缺字段时不报错、不写入。
- [ ] `init` 在用量 > 150,000 或会话 >12h 时排队 compact、写检查点、exit 0 且 `status: CONTEXT_COMPACT_QUEUED`；之后的 `send-round` exit 20 重报；`--no-context-check` 或 unknown 时正常完成。
- [ ] `send-round` 在 `agent_prompted` 后用量超阈值时排队 compact，输出 `planner_compact.queued: true`；pending 未解决、用量 unknown、`PAIRCTL_AUTO_COMPACT=0` 时不排队。compact 焦点指令含检查点路径与 active round_id。
- [ ] `finish-round --status accepted` 缺 `--artifacts` 或 `--notes` → exit 2 `missing_acceptance_evidence`，状态不变；`--report` 路径不存在 → exit 2 `report_missing`；三者齐全时检查点行出现 Report/Artifacts/Notes。
- [ ] `send-round` 后 `<state>/snapshots/<round>-r1/manifest.json` 含 fence 内每个文件的 sha256；修改其中一个文件后 `diff-round` exit 1 并列出 `changed`；未改 exit 0。含未跟踪文件的 fence 同样入清单。
- [ ] 四类 lint 各有一条 fail 用例与一条 pass 用例；fail 时不产生 pending、不消费轮次；`--skip-lint` 越过并记录原因。
- [ ] 检查点包含 Goal、Context usage、Decisions（`note` 追加）、每轮 Revision/Contract/Report/Snapshot；hook 注入体 < 16,000 字符且含完整检查点路径。
- [ ] `SKILL.md` < 15,000 字符；reference/ 五个文件存在；核心含指定关键字串；`herdr --skill` 语义不变（front matter 未动）。
- [ ] 既有 49 项测试全部通过；新增测试覆盖以上条目；`python3 -m pytest skills/herdr-pair/tests -q` 绿。

## Out of scope

- 缓存 TTL 选择（宿主层）。
- 执行器侧 token 成本。
- 04 的 symlink/权限漂移、冻结拒绝逻辑；05 的解冻修复。
- 把规划者的探索性调研迁出到子代理（审计列为方向，未量化）。

## Test boundary

沿用 `tests/test_pairctl.py` 既有边界：公共 CLI + fake herdr + 临时目录小文件；transcript 用合成 JSONL；hook 用 stdin payload 直接调用。不依赖真实 Herdr 会话或 Claude 会话文件。
