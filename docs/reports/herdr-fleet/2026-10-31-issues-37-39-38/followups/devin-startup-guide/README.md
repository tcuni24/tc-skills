# devin-startup-guide 任务证据与审阅要点

本目录在任务根（fleet worktree 外层），不在仓库内；仓库改动只有 `skills/herdr-fleet/SKILL.md` 与 `skills/herdr-fleet/references/workflow.md`。

## 只读输入

- 主报告（只读 A 段「使用中发现的 skill / 集成问题 → A」与其列出的启动证据）：
  `/home/glx/scripts/tc-skills/docs/reports/herdr-fleet/2026-10-31-issues-37-39-38/README.md`
- 证据文件（同目录，全部已读）：
  - `stale-round-38-trust-screen.txt`：信任选择页实录（`Do you trust the authors of this directory?` / `1 Yes, trust` / `2 No, exit` / `↑↓ select · ↵ confirm · esc cancel`）。
  - `report-block-37-startup-status.json`：超时后 status，`agent=idle`、`phase=idle-empty`，agent_output 含 `✓ Trust ~/scripts/tc-skills-fleet/worktrees/report-block-37?` 与 `Devin CLI v3000.11.234` 启动页。
  - `startup-ls.json`：同上任务的 ls 行（idle / idle-empty / no-commits）。
  - `startup-doctor.txt`：`herdr 0.9.1`、插件 enabled、`devin 集成 current (v2)`、doctor 通过。
  - `pr-state-39-submit.txt`：`fleet send` 开始确认失败，`error.code=timeout`（`timed out waiting for agent status`）。
  - `pr-state-39-recovery.txt` / `stale-round-38-submit.txt` / `recovery-send.txt`：恢复 `fleet send` 成功（exit=0），prior session 为 `herdr:devin` 会话 id。
- 本机命令核对（只读 --help / list）：
  - `herdr agent get <target>`：默认 JSON 输出，无 `--json`；实测 `herdr agent list` 中 devin agent 含 `interactive_ready`、`agent_session`、`agent_status`、`state_change_seq` 字段。
  - `herdr agent read <target> --source recent-unwrapped --lines N`：即 `fleet peek` 底层命令（fleet.py `agent_output`）。
  - `herdr agent send-keys <TARGET> <KEY>...`：发送按键（esc 为规范 Escape 名）；具体选项键名未从本机 help 确认，文档按页面提示作答并保留人工确认退路。
  - `herdr agent prompt ... --wait --until working --until blocked --timeout <ms>`：`fleet send` 底层提交命令（fleet.py `submit`），超时/`agent_prompt_stalled` 时回看 `state_change_seq`。
- 用户补充确认：三个新任务（含本任务）在未提交 prompt 情况下复现同一信任选择页；确认目录为用户授权的任务目录后处理选择，`agent_session` 出现且正式输入页可见，首次 `fleet send` 成功。

## 文档落点与边界

- `references/workflow.md` 新增「任务 agent 启动与提交恢复」一节：Devin 信任选择页识别/处理流程 + 开始确认失败后的投递不确定恢复顺序；SKILL.md §2 加一行指针。
- 版本边界：仅 herdr 0.9.1 + devin 集成 v2（Devin CLI v3000.11.234）实测；其他版本/agent 种类只作排查线索；非 devin 不加无依据门槛。
- 事实切分：信任选择页误报 ready（idle + interactive_ready=true、agent_session 缺失）与「开始确认超时」分别记录；前两次超时的完整输入处理链未捕获，不把超时当刻拼成"正处于信任页 / prompt 被其消耗"的因果链。
- 未宣称项：不宣称 herdr/devin readiness 检测已修复（本节是操作规避）；不臆测超时 prompt 被信任页消耗；不归因为额度；`fleet doctor` 通过不证明 prompt 执行。
- 恢复规则：保留现场 → `state_change_seq` vs `TASK.md submit_seq`、`fleet peek` 屏幕、任务文件/提交是否已开始执行，三项只作排查辅助；即使三项缺失证据同时成立（seq 未推进 + 无执行痕迹 + 无产物），prompt 仍可能已接收/排队，不能据此确认未投递。无明确"原请求未被接受或不会再执行"证据时持续标记投递不确定、不得重发；不凭本轮单次成功恢复外推通用判定。
- 未改 `fleet.py`、tests（并行 B 任务）、主报告、外部 Herdr/Devin 安装文件；未操作其他 pane。

## 验证命令（在任务仓库内执行）

- `git diff --check` → exit 0
- `git status --short` → 仅 `M skills/herdr-fleet/SKILL.md`、`M skills/herdr-fleet/references/workflow.md`
- `git diff --stat` → 2 files, +47/-0
- 文档检查：SKILL.md 引用的节名「任务 agent 启动与提交恢复」与 workflow.md 新节标题逐字一致；workflow.md 内「安全规则」锚点为本文件既有节；代码块内命令均经本机 `--help`/fleet.py 核对。
