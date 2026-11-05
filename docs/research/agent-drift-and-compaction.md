# Agent 任务偏移与上下文压缩：同类做法调研

- 调研日期：2026-11-05
- 调研方式：逐页打开下列 8 个 URL 并摘录；文中每条结论都注明来源 URL。页面内容以当日抓取为准，个别产品已改版，命令名以页面现文为主。

## 背景

rex-pair（本仓库 `skills/rex-pair`）有两个具体问题：

1. Executor 完成后 Planner 收不到通知，工单模板默认也不要求回报（issue #50）。
2. 任务在多轮工单传递和上下文压缩后容易偏移：口径被改写、范围被扩张，或者提前宣布完成。

本文调研同类工具和编排方式如何做到 (1) 任务不偏移、(2) 长上下文压缩，作为给 rex-pair 设计 `plan.md` 与 `notify` 的依据。

## 各来源要点

### Anthropic：Effective harnesses for long-running agents

<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>

- 首次会话由 initializer agent 建立 `init.sh`、`claude-progress.txt` 和初始 git commit，让后续会话能快速了解现状。
- 功能清单存为 JSON（页面明确说模型相对不容易误改 JSON），coding agent 只允许修改 `passes` 字段，且明确禁止删除或改写测试。
- 每个会话只做一个功能（incremental progress），结束后提交 git 并写进度摘要，保证环境干净、可回退。
- 未经端到端测试不得把功能标为通过；页面把“没有真正做端到端验证就宣布完成”列为需要专门提示的失败模式。
- 页面总结的失败模式有两类：试图一次做完（导致半成品和未记录状态）；后续会话看到已有进展就宣布整个项目完成。

### Anthropic：Effective context engineering for AI agents

<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>

- 长程任务有三种手段：compaction、结构化笔记（structured note-taking）、子 agent 架构。
- compaction：接近窗口上限时摘要并重启上下文。调参建议先最大化召回、再提高精度；清掉旧的工具调用与结果是最轻的压缩方式。Claude Code 会保留架构决策、未解决 bug 和实现细节，并保留最近访问的 5 个文件。
- 结构化笔记：把笔记持久化到上下文之外，之后再读回；页面以 Claude Code 的 to-do list 和 `NOTES.md` 为例，强调上下文重置后靠自己的笔记恢复。
- 子 agent：各自使用干净的上下文窗口，只向主 agent 返回约 1,000–2,000 token 的浓缩摘要。
- 三者适用场景不同：compaction 适合大量来回对话；笔记适合有清晰里程碑的迭代开发；子 agent 适合需要并行探索的复杂研究。

### Manus：Context Engineering for AI Agents

<https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus>

- 把文件系统当作“终极上下文”：容量大、持久、可由 agent 自己读写，是结构化的外部记忆。
- 压缩策略必须可恢复：网页内容可以从上下文里删掉，但保留 URL；文档内容可以省略，但保留沙箱中的路径。
- 用 todo.md 反复复述目标：不断重写 todo 列表，把全局计划推到上下文末尾，以对抗 lost-in-the-middle 和目标偏移。
- 保留出错的过程：不要清理失败轨迹，模型看到失败动作和报错后才会修正判断。
- 另有一条相关经验：避免让上下文充满雷同的动作-观察对，否则模型会机械模仿（“Don't get few-shotted”）。

### Amp：Handoff (No More Compaction)

<https://ampcode.com/news/handoff>

- 压缩是有损的：上下文窗口被摘要替换，摘要是否包含该包含的内容取决于 agent。
- 压缩会鼓励长而漫游的线程，最后变成“摘要叠摘要”。
- Amp 改为 Handoff：指定新线程的目标，从当前线程中抽取与下一步相关的上下文，并附相关文件列表；生成的 prompt 先作为草稿供人编辑。
- 目标是鼓励聚焦的线程，而不是靠反复压缩维持一条长线程。

### OpenHands：Context Condenser

<https://docs.openhands.dev/sdk/guides/context-condenser>

- 默认实现 `LLMSummarizingCondenser`：对话超过 `max_context_length` 个事件时触发。
- 触发后始终保留开头 `keep_first` 个事件（系统提示、初始用户消息），最近消息保持原样，较早内容换成 LLM 生成的摘要。
- 页面给出的收益是降低每轮 API 成本、保持长会话响应时间稳定，并声称软件工程任务上效果相当或更好。

### Kiro：Specs

<https://kiro.dev/docs/specs/>

- 每个 spec 生成三份文件：`requirements.md`（用户故事与验收标准）、`design.md`（架构、时序图、实现考虑）、`tasks.md`（离散可跟踪任务）。
- 流程分三阶段：需求（或 bug 分析）→ 设计 → 任务；任务可单独运行或批量运行，并按依赖关系分波并发。
- 标准 Feature Spec 在阶段之间有评审/批准关口（approval gates）；Quick Spec 是唯一不在阶段之间设关口的变体（<https://kiro.dev/docs/specs/feature-specs/>）。
- 需求变化时的迭代方式（<https://kiro.dev/docs/specs/best-practices/>）：先改 `requirements.md`，再更新 `design.md` 使其与需求一致，最后在 `tasks.md` 里点 **Sync Files** 生成对应新需求的任务；Design-First 则先改设计再回验需求。

### GitHub Spec Kit

<https://github.com/github/spec-kit>

- 采用分层产物：constitution（项目原则）、spec（做什么/为什么）、plan（技术方案）、tasks（可执行任务），并可继续 implement/converge。
- 仓库 README 当前给出的流程是：constitution 每个项目建一次，其余步骤每个功能重复；“one at a time, reviewing each result before continuing”。
- README 现用命令名为 `/speckit-constitution`、`/speckit-specify`、`/speckit-plan`、`/speckit-tasks`、`/speckit-implement`、`/speckit-converge`（早期资料常写作 `/specify`、`/plan`、`/tasks`）。

### Cline：Memory Bank

<https://docs.cline.bot/best-practices/memory-bank>

- `memory-bank/` 下按层级组织：`projectbrief.md` 是基础文档，塑造其他所有文件；`activeContext.md` 记录当前焦点、最近变更和下一步，变化最频繁；`progress.md` 记录已完成、待办、当前状态和已知问题。
- 每次记忆重置后必须读取全部 memory bank 文件才能继续；“activeContext.md changes most frequently; update it after each session”，“progress.md tracks milestones; review it when resuming work”。
- 上下文窗口将满时：先 “update memory bank” 记录现状，再开新会话，然后 “follow your custom instructions”。

## 共性模式

### 防偏移

- **目标外置并被反复复述**：Anthropic 的 feature list、Manus 的 todo.md、Cline 的 activeContext.md 都把目标变成上下文之外的持久文本，并在每个周期重新写入当前注意力范围。
- **阶段关口与独立验收**：Kiro 的 approval gate、Spec Kit 的“每步评审后再继续”、Anthropic 的“端到端测试后才标通过”，都要求在人（或独立评审者）确认后才推进。
- **一次只做一个可验收单元**：Anthropic 明确要求每个会话只处理一个功能，避免 one-shot 和半成品。
- **恢复时先读状态**：Cline 要求重置后先读 memory bank，Anthropic 要求会话开始先读进度文件和 git log，Manus 依赖文件系统里的持久状态。

### 压缩

- **保留目标、决策和未解决问题，丢弃冗余工具输出**（Anthropic compaction）。
- **压缩必须可恢复**：保留路径或 URL，而不是只留摘要（Manus）。
- **优先开新线程并抽取“下一步需要什么”**，避免摘要叠摘要（Amp Handoff）。
- **外置记忆优于纯摘要**：笔记、文件、进度日志在上下文之外持久存在，需要时再读回（Anthropic 结构化笔记、Manus 文件系统、Cline Memory Bank）。
- **摘要之外仍保留开头锚点与最近事件**（OpenHands 保留 `keep_first` 与最近消息）。

## 对 rex-pair 的取舍

采纳：

- **`plan.md` 对应 spec 分层与 Goal 锁定**：Kiro 的三件套与 Spec Kit 的 constitution/spec/plan/tasks 表明，把“要做什么”和“怎么做”分成稳定的上层文件，能减少每轮工单各自发挥。rex-pair 用一份 `plan.md` 承载 Goal、Non-goals、Decided、Acceptance，各轮 contract 只描述本轮增量（<https://kiro.dev/docs/specs/>、<https://github.com/github/spec-kit>）。
- **Rounds 与 Next step 对应 todo 复述和 progress 文件**：Manus 的 todo.md 复述、Cline 的 progress.md/activeContext.md 更新、Anthropic 的进度日志，说明“每轮把当前轮次、状态、下一步写回文件”比依赖对话记忆可靠。rex-pair 要求 Planner 在每次用户决策后和每轮验收后更新 Rounds 与 Next step（<https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus>、<https://docs.cline.bot/best-practices/memory-bank>、<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>）。
- **以落盘文件为准，不以压缩摘要为准**：Amp 指出摘要有损且会摘要叠摘要，Manus 要求压缩可恢复，Cline 在重置后强制读文件。因此 rex-pair 规定压缩或换会话后先重读 `plan.md` 和最新 STATUS/report（<https://ampcode.com/news/handoff>、<https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus>、<https://docs.cline.bot/best-practices/memory-bank>）。
- **Decided 只能由用户修改**：Anthropic 记录了两类失败——提前宣布完成、以及会话间状态丢失后自行猜测；Kiro 和 Spec Kit 把需求变更放在显式评审关口。rex-pair 因此要求 Goal 和 Decided 只随用户决定变更，并保留“旧 → 新”记录和方案版本号，Executor 发现冲突时停下报告（<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>、<https://kiro.dev/docs/specs/best-practices/>）。
- **验收按内容而非通知**：Anthropic 强调端到端测试后才算完成，Kiro 有批准关口，Spec Kit 要求逐步评审。rex-pair 的 notify 只是唤醒信号，`ready` 不等于 `accepted`，Planner 仍须独立验收（<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>、<https://kiro.dev/docs/specs/>、<https://github.com/github/spec-kit>）。

不采纳：

- **不引入 JSON 状态文件**：Anthropic 用 JSON 存功能清单，是因为模型相对不容易误改 JSON；但 rex-pair 是人工协议，没有程序读写这些文件，Markdown 的 `plan.md` 对人更易读、易评审（<https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>）。
- **不新增自动状态机、脚本或前台轮询**：rex-pair 的 Non-goals 明确保持人工协议。`notify=on` 只发一条短唤醒，`notify=off` 的后台等待作业也只负责在终态退出，不承担调度或状态迁移（<https://ampcode.com/news/handoff> 的“人可编辑草稿”思路同样偏向保留人工介入）。
- **不照搬子 agent 架构**：Anthropic 的子 agent 适用于隔离大量搜索上下文；rex-pair 已经是 Planner/Executor 两个 pane，再拆角色会增加交接成本，本轮不引入第三角色（<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>）。
- **不做自动 compaction**：压缩由各 harness 自行决定，rex-pair 无法控制；可做的是压缩前后读写 `plan.md`、STATUS 和 report，把恢复成本降到最低（<https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>、<https://docs.openhands.dev/sdk/guides/context-condenser>）。

## 同目录多任务：隔离与识别

### 各来源要点

- **Spec Kit Quickstart**：active feature 由 `.specify/feature.json` 记录，可用 `SPECIFY_FEATURE_DIRECTORY` 环境变量覆盖；命令据此解析 feature，而不是看 Git 分支（<https://github.github.com/spec-kit/quickstart.html>）。
- **Spec Kit git 扩展**：feature 分支有两种编号方式，顺序编号 `001-feature-name` 或时间戳编号 `20260319-143022-feature-name`（<https://github.com/github/spec-kit/blob/main/extensions/git/README.md>）。
- **Spec Kit 讨论 #497**：团队多人从同一基线分支各自创建 spec 时，递增序号（001、002……）会自然冲突（<https://github.com/github/spec-kit/discussions/497>）。
- **Kiro**：spec 存放在项目仓库中，与代码一起做版本控制；每个 feature 一个子目录（如 `.kiro/specs/<feature>/`）（<https://kiro.dev/docs/specs/best-practices/>）。
- **Taskmaster**：用 tag 把任务分成多个上下文（不同分支、环境或阶段），当前 tag 存放在 `.taskmaster/state.json`，未指定时回落到 `master`；任务 ID 只在所属 tag 内唯一（<https://github.com/eyaltoledano/claude-task-master/blob/main/docs/task-structure.md>）。
- **Claude Code Agent Teams**：领取任务时用文件锁防止竞争；两个队友编辑同一个文件会互相覆盖，所以应拆分任务，让每人负责不同的文件（<https://code.claude.com/docs/en/agent-teams>）。
- **Claude Code worktrees**：并行会话使用 git worktree，每个 worktree 是独立的 checkout 和分支，避免编辑冲突（<https://code.claude.com/docs/en/common-workflows>）。
- **MCP Agent Mail**：对文件或 glob 声明咨询式的文件预留（lease），带 TTL，可设为 exclusive；pre-commit 守卫会拦截与他人 exclusive 预留冲突的提交；冲突时调整范围、等待过期或改用非独占预留（<https://github.com/Dicklesworthstone/mcp_agent_mail>）。

### 共性模式

- **每个任务有稳定的身份键**：Spec Kit 的 feature 目录、Kiro 的 feature 子目录、Taskmaster 的 tag 都先给任务一个唯一键，后续命令和恢复都靠这个键，而不是“最近改过的那个”。
- **单指针在并行时会互相覆盖**：`.specify/feature.json` 和 `.taskmaster/state.json` 都只保存一个“当前”值，适合单人顺序工作，多个执行者并存时就需要额外协调。
- **写入面必须隔离**：worktree 隔离 checkout，Agent Teams 拆分文件所有权，Agent Mail 用带 TTL 的独占 lease，Spec Kit/Kiro/Taskmaster 按 feature 分目录。
- **冲突要显式处理**：Agent Mail 在冲突时要求调整范围、等待过期或降级为非独占；Agent Teams 与 worktree 直接把并行工作放到不同文件或分支。

### 对 rex-pair 的取舍

采纳：

- **每个任务一个 `<task-id>` 目录**（D1）：对应 Kiro 的 per-feature 子目录和 Taskmaster 的 tag 隔离，让同一 cwd 下的多个 plan 各有稳定路径。
- **不设全局“当前任务”指针**（D2）：Spec Kit 的 `.specify/feature.json` 和 Taskmaster 的 `.taskmaster/state.json` 都只有一个当前值，多个 Planner 并存时会互相覆盖；rex-pair 改为扫描各 plan.md 的状态字段。
- **task-id 用时间戳**（D1）：Spec Kit 讨论 #497 显示团队从同一基线创建 spec 时顺序编号会冲突，git 扩展也支持时间戳编号。`YYYYMMDD-HHMM-<slug>` 让不同 pane 同时建目录时天然不相撞，建目录前仍需确认不存在。
- **plan 的 Scope 相当于没有 TTL 的人工预留**（D7）：MCP Agent Mail 的 lease 是服务端自动过期的咨询式预留；rex-pair 没有服务端，只能在派发前由 Planner 对照同一 cwd 下所有 active plan 的 Scope，重叠就排队或隔离。
- **Git 用 worktree，非 Git 用输出分目录 + 共享文件串行 + 副本比对哈希**（D7）：worktree 对应 Claude Code 的并行会话方案；非 Git 场景没有分支可隔离，只能靠“每个任务只写自己的输出目录”、共享文件声明占用并排队、必须并行修改的小文件复制后比对 SHA256 再回写，大数据文件不复制。

不采纳：

- **不引入 `.specify/feature.json` / `.taskmaster/state.json` 这类单指针文件**：它们解决的是工具链自己找 feature 的问题，但 rex-pair 由多个 Planner 并行，单指针会互相覆盖。
- **不引入服务端 lease 与 pre-commit 守卫**：rex-pair 是人工协议，没有常驻服务；TTL、自动过期和提交拦截都需要额外基础设施。
- **不自动按 Git 分支或最近修改时间识别任务**（D6）：Spec Kit 明确命令不依赖 Git 分支；rex-pair 同样要求用 pane ID 匹配 Owner 并结合 task_id 找回 plan，不按 mtime 选择。

## 参考链接

1. Anthropic, Effective harnesses for long-running agents — <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>
2. Anthropic, Effective context engineering for AI agents — <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
3. Manus, Context Engineering for AI Agents — <https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus>
4. Amp, Handoff (No More Compaction) — <https://ampcode.com/news/handoff>
5. OpenHands, Context Condenser — <https://docs.openhands.dev/sdk/guides/context-condenser>
6. Kiro, Specs — <https://kiro.dev/docs/specs/>（另见 <https://kiro.dev/docs/specs/feature-specs/>、<https://kiro.dev/docs/specs/best-practices/>）
7. GitHub Spec Kit — <https://github.com/github/spec-kit>
8. Cline, Memory Bank — <https://docs.cline.bot/best-practices/memory-bank>
9. Spec Kit, Quickstart — <https://github.github.com/spec-kit/quickstart.html>
10. Spec Kit, Git Branching Workflow Extension — <https://github.com/github/spec-kit/blob/main/extensions/git/README.md>
11. Spec Kit, Discussion #497 "What's the best way to use spec-kit in a team?" — <https://github.com/github/spec-kit/discussions/497>
12. Kiro, Specs Best practices — <https://kiro.dev/docs/specs/best-practices/>
13. Taskmaster, Task Structure — <https://github.com/eyaltoledano/claude-task-master/blob/main/docs/task-structure.md>
14. Claude Code, Agent Teams — <https://code.claude.com/docs/en/agent-teams>
15. Claude Code, Common workflows（worktrees）— <https://code.claude.com/docs/en/common-workflows>
16. MCP Agent Mail — <https://github.com/Dicklesworthstone/mcp_agent_mail>
