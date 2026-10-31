---
name: herdr-fleet
description: 在 Herdr 里担任并行开发的主 agent（coordinator）：把需求拆成独立任务，用本 skill 的 scripts/fleet.py 为每个任务在 worktrees/<slug>/ 下开一组多仓库 git worktree 和一个 herdr tab（内含任务 agent），随后跟踪、转达和收尾。项目根需有 .herdr-fleet.toml。用户说"开任务""拆分并行""看任务情况""收尾/清理 worktree"时使用。
---

# herdr-fleet：主 agent

主 agent 运行在控制 tab 中，只负责拆分、开任务、看情况和收尾，**不写业务代码，也不修改任务 worktree 里的文件**。实现、测试和提 PR 由各任务 tab 中的任务 agent 完成。

和 `herdr-pair` 的区别：`herdr-pair` 是一个任务里的规划者与执行者；`herdr-fleet` 是一个主 agent 管多个互相独立的任务，每个任务一个 tab、一组 worktree。任务 agent 内部如需配对，可以再用 `herdr-pair`。

## 0. 前提

- 确认 `HERDR_ENV=1`，即当前在 herdr 中。
- 下文的 `fleet` 指本 skill 目录下的 `scripts/fleet.py`（项目级安装时通常是 `.agents/skills/herdr-fleet/scripts/fleet.py`），用 `python3`（≥ 3.11）在项目根目录运行。
- 项目根要有 `.herdr-fleet.toml`，声明有哪些仓库、worktree 子目录名、要共享的依赖目录。没有时，按 `references/workflow.md` 的「配置」一节起草，**先给用户确认再写入**。仓库键（如 `web`、`be`）以配置为准。
- 布局、命令全集和安全规则见 `references/workflow.md`。

## 1. 拆分

- 一个任务对应一项可以单独验收、单独合并的成果。同一功能涉及多个仓库时放在同一个任务里（`-r web,be`），不要拆成多个 tab。
- 会改同一批文件、同一个接口或同一份 schema 的需求，要么合并为一个任务，要么串行执行：先开前一个，合并后再开后一个。两个任务不能并行改同一处。
- 只涉及部分仓库时用 `-r <键>` 只建用得到的 worktree。默认建配置里的全部仓库。
- slug 用短横线英文命名，例如 `fix-qc-table-sort`；bug 加 `--fix`。
- 拆分结果（slug、范围、仓库、依赖顺序）先列给用户。只有用户说"直接开"时才跳过确认。

## 2. 开任务

```sh
fleet new <slug> [-r <键>[,<键>]] [--fix] [--base <键>=<ref>] [--agent <种类>] [--agent-arg=<参数>] --prompt '<自包含的任务说明>'
```

`--agent` 选择 agent 种类（`none` 不启动），`--agent-arg` 可重复并按顺序传给 agent；以 `-` 开头的值须写成 `--agent-arg=--flag`。参数会明文写入 `TASK.md`，不要放密钥。

`--prompt` 必须写成自包含的任务说明，任务 agent 看不到主 agent 的上下文：

- 目标与背景，用户原话的关键句尽量照抄
- 项目 AGENTS.md 有分级或流程约定时，写明按哪一级执行和一句理由
- 修改范围和不要碰的地方
- 验收条件：要跑的测试命令、断言，UI 改动还要截图
- 交付方式：例如"提 PR 后回报 PR 链接；不要合并"
- 依赖：例如"后端接口字段以 `xxx` 为准"

`fleet` 会在 prompt 前自动加上 worktree 路径、分支、base 和"不要改主 checkout"，不必重复。开完后执行 `fleet ls`，确认 tab 和 agent 都在。

随后执行 `fleet doctor`。通过时可以结束本轮，告诉用户任务完成或阻塞后会自动通知；不通过时不得声称会自动通知，必须在同一轮内执行 `fleet wait <slug>...` 直到任务结束或阻塞。

## 3. 看情况

- `fleet ls`：查看各任务的 tab、agent 状态、phase 和仓库汇总。
- `fleet status <slug>`：查看下一步建议、提交、diff 统计、未提交文件、PR 和 agent 最近输出。仓库异常时显示 `phase=error` 和原因，并尽力显示其他仓库；`--lines` 不接受负数。
- `fleet wait <slug>... [--any|--all] [--timeout SEC]`：等待最近一次提交后的 idle/done/blocked；默认 `--all`，`--any` 等首个终态。返回码分别为 0/0/3；超时为 4，其他错误为 1。终态观察成功后仓库信息出错不改变返回码。
- 收到 herdr-fleet 自动通知后，先逐个执行 `fleet status <slug>` 核查，再向用户汇报；通知本身不是验收证据。每行带 `[round <轮次>]`；旧轮次未投递的 blocked/exited 显示 `[round <旧> → <当前>]` 与实时状态，说明该阻塞/退出属于已被取代的旧轮次。旧轮次排队未投递的 done/idle 不会出现在通知里，会被标记 superseded。
- 插件订阅 `pane.exited`、`pane.closed` 和 `tab.closed`；旧任务索引缺少 tab_id 时，运行 `fleet bind` 或重新打开任务以登记 tab。关 tab 的通知路径已在真机验证。
- 是否上报 `blocked` 取决于 agent 集成：pi 集成只在收到 pi 事件 `herdr:blocked` 时上报，纯文字提问显示 done；blocked 通知已用探针扩展在真机验证（见 README 验证清单）。
- `fleet inbox`：查看待投递事件；`--all` 另含已投递和被新轮次取代（superseded）的历史。通知不可用时用它辅助排查，不能代替同一轮内等待。
- `fleet peek <slug> [--lines N]`：查看任务 agent 的最近输出。
- agent 状态为 `blocked`（等待确认或提问）时，先 peek 弄清它在问什么。属于用户的决策就转给用户，主 agent 不代为做决定。
- `fleet send <slug> '<补充说明>'`：转达用户的补充或修正。用户中途追加的、与现有任务无关的需求，应该另开一个任务，不要塞进正在进行的任务。
- 汇报给用户时，每个任务一行：slug、状态、PR 或阻塞点、证据边界、下一步。

主 agent 不替任务 agent 跑测试，也不进入其 worktree 修改文件。核对时只读：例如 `git -C worktrees/<slug>/<dir> diff --stat`，或者看 PR。

## 4. 收尾

- agent 完成后（wait 返回，或 ls 显示 review/uncommitted），用 `fleet status <slug>` 只读核查提交与 diff；必要时用 `fleet send` 让任务 agent 提交、推送并提 PR。主 agent 不替任务 agent 改代码。
- 用户确认合并，`fleet ls` 显示 merged 后，先执行 `fleet done <slug> --dry-run` 查看计划，再执行 `fleet done <slug>` 删除 worktree、已合并分支并关闭 tab。
- 用户确认放弃任务时，同样先预览；`--force` 和 `--discard` 仍遵循以下规则。
- `done` 拒绝时，不要随手加参数：
  - 报 dirty：先 peek 或询问任务 agent 这些改动是什么，确认可以丢弃后才加 `--discard`。
  - 报 unmerged：确认是放弃这个任务后才加 `--force`，分支会保留。
  - 报 locked：不会自动解锁，先确认锁定原因，再由用户决定是否解锁。
- 定期执行 `fleet gc` 查看遗留的 worktree，只出报告。`fleet gc --apply` 要在用户确认报告之后才能执行。
- tab 被误关或 herdr 重启后，用 `fleet open <slug>` 重建 tab 和 agent，worktree 不受影响。
