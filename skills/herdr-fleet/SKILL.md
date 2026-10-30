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
fleet new <slug> [-r <键>[,<键>]] [--fix] [--base <键>=<ref>] --prompt '<自包含的任务说明>'
```

`--prompt` 必须写成自包含的任务说明，任务 agent 看不到主 agent 的上下文：

- 目标与背景，用户原话的关键句尽量照抄
- 项目 AGENTS.md 有分级或流程约定时，写明按哪一级执行和一句理由
- 修改范围和不要碰的地方
- 验收条件：要跑的测试命令、断言，UI 改动还要截图
- 交付方式：例如"提 PR 后回报 PR 链接；不要合并"
- 依赖：例如"后端接口字段以 `xxx` 为准"

`fleet` 会在 prompt 前自动加上 worktree 路径、分支、base 和"不要改主 checkout"，不必重复。开完后执行 `fleet ls`，确认 tab 和 agent 都在。

## 3. 看情况

- `fleet ls`：查看各任务的 agent 状态（idle/working/blocked/done）、未提交文件数、ahead/behind、合并状态。
- `fleet peek <slug> [--lines N]`：查看任务 agent 的最近输出。
- agent 状态为 `blocked`（等待确认或提问）时，先 peek 弄清它在问什么。属于用户的决策就转给用户，主 agent 不代为做决定。
- `fleet send <slug> '<补充说明>'`：转达用户的补充或修正。用户中途追加的、与现有任务无关的需求，应该另开一个任务，不要塞进正在进行的任务。
- 汇报给用户时，每个任务一行：slug、状态、PR 或阻塞点、下一步。

主 agent 不替任务 agent 跑测试，也不进入其 worktree 修改文件。核对时只读：例如 `git -C worktrees/<slug>/<dir> diff --stat`，或者看 PR。

## 4. 收尾

- 用户确认 PR 已合并或任务已放弃之后，执行 `fleet done <slug>`。它会删除 worktree、删除已合并的分支、关闭 tab。
- `done` 拒绝时，不要随手加参数：
  - 报 dirty：先 peek 或询问任务 agent 这些改动是什么，确认可以丢弃后才加 `--discard`。
  - 报 unmerged：确认是放弃这个任务后才加 `--force`，分支会保留。
- 定期执行 `fleet gc` 查看遗留的 worktree，只出报告。`fleet gc --apply` 要在用户确认报告之后才能执行。
- tab 被误关或 herdr 重启后，用 `fleet open <slug>` 重建 tab 和 agent，worktree 不受影响。
