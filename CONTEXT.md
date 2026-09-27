# Context: tc-skills domain glossary

本文件是本仓库唯一的领域术语表，承载跨 skill、spec、ADR 与 review 复用的词汇。
布局由 [`docs/agents/domain.md`](docs/agents/domain.md) 规定（single-context），该文件也要求在这些文档里使用本表的词汇。
每一行都给出权威出处；术语含义以被引用文件为准，本表只是入口。定义用中文，技术标识符保持原样。

## Terms

| Term | 中文 | Definition | Authoritative source |
|---|---|---|---|
| skill | 技能 | 一份可复用的任务指令集，位于 `skills/<name>/`，以带 YAML front matter 的 `SKILL.md` 为入口。 | `AGENTS.md`；`skills/herdr-pair/SKILL.md` front matter |
| reference doc | 参考文档 | skill 的核心文件之外、按需读取的细节文档，一个 skill 可能有多个。 | `skills/herdr-pair/references/recovery.md` §Dispatch failure paths A–F；`docs/specs/issue-2-herdr-pair-context-budget.md` §I |
| Planner | 规划者 | 持有用户任务、负责范围、验收与最终交付的座位；对 Executor 写下的每一行负责。 | `skills/herdr-pair/SKILL.md` §"The one rule that matters" |
| Executor | 执行者 | 只做编辑与命令执行的座位，在契约给定的 fence 内工作并按契约回报。 | `skills/herdr-pair/references/handoff.md` §Executor responsibilities |
| Verifier | 验证者 | 可选座位；只读地独立复核某一轮，只能在写入停止、review epoch 冻结之后进行。 | `skills/herdr-pair/references/verification.md#stable-review-epochs` |
| pair | 配对 | 两个及以上 `pane_id` 不同的实时 agent 共担一个任务，角色是座位而非 agent 类型。 | `skills/herdr-pair/SKILL.md` §"Which seat are you in" |
| round (round_id / revision) | 轮次 / 版本 | 一次派发的最小工作单元，有一个 scope fence、一条字面验收结果和一个可独立签署的 diff；轮内每次契约修改递增 `revision`。 | `skills/herdr-pair/SKILL.md` §2；`skills/herdr-pair/scripts/pairctl.py`（`check-round --revision`） |
| handoff | 工单 | 派发时给出的自包含契约，假定 Executor 零继承规则，写清身份、范围 fence、只读输入、停在哪、字面验收与回报地址。 | `skills/herdr-pair/SKILL.md` §3 |
| handoff fence (`[可以改]` / `[可以新建]` / `[不许动]` / `[只读输入]` / `[报告]`) | 范围围栏 | 工单里的五条范围行：可改、可新建、禁改、只读输入、报告落盘路径；派发前 lint 用它们做 fail-closed 检查。 | `skills/herdr-pair/SKILL.md` §3；`skills/herdr-pair/scripts/pairctl.py`（`FENCE_RE`） |
| report | 回报 | 轮次结束后由 Executor 落盘并回送的证据文件，须给出命令、退出码、原始输出、未做项与假设，正文几 KB、其余指路径。 | `skills/herdr-pair/references/handoff.md` §Report contract |
| review epoch | 评审纪元 | 冻结的产物状态（Git 下为 `HEAD` 加 `git status --short`，非 Git 目录为范围内清单摘要）；发生漂移即 `STALE_REVIEW`，旧结论作废。 | `skills/herdr-pair/references/verification.md#stable-review-epochs` |
| context budget | 上下文预算 | 规划者上下文用量阈值，默认 `150000` tokens，可由 `--budget`、`PAIRCTL_CONTEXT_BUDGET` 或 `init --context-budget` 覆盖。 | `skills/herdr-pair/SKILL.md` §"Context budget and recovery" |
| context usage | 上下文用量 | `context-usage` 的读数：最后一条非 synthetic assistant 消息的 `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`；无法确定时为 `unknown`，不得当成余量。 | `docs/specs/issue-2-herdr-pair-context-budget.md` §A；`skills/herdr-pair/scripts/pairctl.py` |
| checkpoint | 检查点 | 写入 state 的可恢复快照，含 Goal、Context usage、Budget、每轮 Revision/Contract/Report/Snapshot/Artifacts/Notes、带时间戳的 Decisions 与未终态作业。 | `docs/specs/issue-2-herdr-pair-context-budget.md` §E；`skills/herdr-pair/references/recovery.md` §Restore an active round |
| rollover | 会话轮替 | 让规划者换用新会话（`--reason new` 需不同且非占位的 session id）或原地推进 `compaction_epoch`（`--reason compact`）。 | `skills/herdr-pair/references/recovery.md` §Dispatch failure paths A–F (E) |
| compaction | 压缩 | 规划者会话的上下文压缩或清除；各 kind 命令不同（`/compact`、cursor `/summarize`、droid `/compress`），pairctl 只负责排队。 | `skills/herdr-pair/references/recovery.md` §Compact commands and focus |
| compact queued (exit 20) | 压缩已排队 | 未被消费的排队压缩会拦住下一次派发：`send-round`/`start-round` 以 exit 20 重报该命令，不派发也不消费轮次。 | `docs/specs/issue-2-herdr-pair-context-budget.md` §C；`skills/herdr-pair/references/recovery.md` §When automatic compaction queues |
| resume prompt / `resume_pending` | 恢复提示 / 待恢复记录 | 状态里记录"压缩后还欠规划者一次恢复提示"的记录；终态为 `delivered`/`expired`/`cancelled`，其余状态仍可被 `resume-deliver` 领取。 | `skills/herdr-pair/scripts/pairctl.py`（`RESUME_CLAIMABLE`、`arm_resume_record`） |
| watcher vs plugin mechanism | 兜底监视器 vs 插件机制 | `resume_pending.mechanism` 的两种投递方式：只有插件已启用且规划者 kind 为 `claude` 才是 `plugin`，其余一律由 watcher 投递。 | `skills/herdr-pair/README.md` §真机触发验证；`skills/herdr-pair/scripts/pairctl.py`（`probe_resume_mechanism`） |
| executor notice | 执行者通知 | 执行者状态边沿触发的短通知与短回报，按轮次/版本/状态/报告 hash 去重并限流；压缩排队或恢复提示未送达时先入 `deferred_notices`。 | `skills/herdr-pair/scripts/pairctl.py`（`executor_notice_hold`、`flush_executor_notices`） |
| dispatch staleness | 派发滞留 | `pending_dispatch` 超过时限（`PAIRCTL_DISPATCH_STALE_S`，默认 900 秒）仍未解决时只通知一次，提醒先核实再 `resolve-pending`。 | `skills/herdr-pair/scripts/pairctl.py`（`check_dispatch_stale`）；`skills/herdr-pair/references/recovery.md` §Dispatch failure paths A–F (A) |
| snapshot / manifest | 轮前快照 / 清单 | 派发前把 fence 内存在的文件复制到 `snapshots/<round_id>-r<revision>/`，并写 `manifest.json`（`path`/`sha256`/`size`/`mode`/`exists`）；超过 50 MB 只记 hash 不复制。 | `docs/specs/issue-2-herdr-pair-context-budget.md` §G；`skills/herdr-pair/references/recovery.md` §Snapshot and lint recovery |
| pending dispatch | 待解决派发 | 投递结果不确定时保留的派发记录：只有 `agent_prompted` 消费轮次，其余未知结果保留 pending，解决前不得重发。 | `skills/herdr-pair/references/recovery.md` §Dispatch failure paths A–F (A) |
| `resolve-pending` | 解决待决派发 | 显式把 unknown 的派发判为 `--outcome delivered` 或 `not-delivered` 的命令；先核实目标是否收到再执行。 | `skills/herdr-pair/scripts/pairctl.py`（`resolve-pending`）；`skills/herdr-pair/references/recovery.md` §Dispatch failure paths A–F (A) |
| state root / `--state-dir` | 状态根 / 状态目录 | `state.json` 所在目录，默认 `$XDG_STATE_HOME/herdr-pair/<cwd-hash>`；所有方必须给出同一个绝对 `--cwd` 与 `--state-dir`。 | `skills/herdr-pair/SKILL.md` §0；`skills/herdr-pair/scripts/pairctl.py`（`default_root`） |
| pane index | 窗格索引 | 记录"某个 pane 属于哪个 state 目录与角色"的每窗格文件 `$XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json`，只服务插件续跑；`state.json` 仍是唯一事实来源。 | `skills/herdr-pair/scripts/pairctl.py`（`pane_index_path`、`record_pane`）；`skills/herdr-pair/herdr-plugin.toml` |
| quality kill-switch | 质量停手开关 | §7 质量门：出现"声称改了却无改动、数字无出处、同一指令漏两次、越 fence、解释两次站不住、要重写它大部分产出"时停手并报证据。 | `skills/herdr-pair/SKILL.md` §7；`skills/herdr-pair/references/verification.md#quality-gate` |
| delivery package (customer-delivery) | 交付包 | 交给客户的目录产物：只含一个 Excel 工作簿、一个离线 HTML 报告和一个 `plot/` 目录，内部列名、路径、文件名、日志与流程细节必须剥离。 | `skills/customer-delivery/SKILL.md` §客户交付包生成 |
| report simplification layer (customer-report-simplify) | 报告精简层 | 在不改交付原件的前提下，另开一份围绕客户问题重排的内容层：正文只回答问题，其余进折叠附录。 | `skills/customer-report-simplify/SKILL.md` §第二步；`skills/customer-report-simplify/scripts/audit_report.py` |
| print contract (html-pdf-print) | 打印契约 | 打印前先定下的目标浏览器/打印引擎、纸张、缩放，以及页脚是每页重复还是仅末页底部；验收对象是实际生成的 PDF。 | `skills/html-pdf-print/SKILL.md` §先明确打印契约 |
| stack analysis (nextflow-workflow-skills) | 流程编排 | 用 Nextflow DSL2 自建流程：`main.nf` + `workflows/` + `subworkflows/` + `modules/` 布局与 `nextflow.config` 资源分层。 | `skills/nextflow-workflow-skills/SKILL.md` §Quick start |
| PR convention (gitee-pr) | PR 规范 | 在 Gitee 提 PR 前强制执行的团队规则：分支名、commit 前缀、工作区干净、中文标题与正文模板。 | `skills/gitee-pr/SKILL.md` §Workflow |

## Related documents

- [`docs/agents/domain.md`](docs/agents/domain.md) — single-context 布局与术语表的使用规则。
- [`docs/adr/`](docs/adr/) — 架构决策；`0001-herdr-pair-claude-planner-first.md` 决定现阶段以 Claude 为首选规划者。
- [`docs/specs/`](docs/specs/) — 规格；`issue-2-herdr-pair-context-budget.md` 是上下文预算与恢复契约的权威来源。
- [`docs/reviews/`](docs/reviews/) — 仓库体检与评审记录，本术语表的来源条目为 `2026-09-27-repo-assessment.md` C4。
