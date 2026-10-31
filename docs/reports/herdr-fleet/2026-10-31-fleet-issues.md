# 2026-10-31 #37、#39、#38 和后续修复

历史上 #37 回报摘取、#39 PR 查询三态、#38 旧轮次通知分别由 PR #40/#41/#42 交付；后续 #43 提供 Devin 启动恢复指引，#44 修复测试配置隔离，#45 缩小 event 查询锁范围。历史记录称六项均合并，功能验收基线为 `12783ac14b4e23ce65dd5ac890acee4b894097b2`，不是实时 PR 状态。

在无真实 fleet 配置祖先的精确提交快照，三项最初独立测试分别 105、110、100 passed；组合冲突解决后 133 passed。最终 main 基线在真实 fleet worktree 执行：

```sh
/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python -m pytest tests/herdr-fleet/test_fleet.py tests/herdr-fleet/test_event_lock.py -q
```

历史结果为 139 passed、exit 0；这是 fake-herdr 测试，不是 Herdr 端到端并发或真实 IPC 时长验证。复现须使用对应历史提交、Python 环境和隔离配置；上述绝对路径只描述当时环境。

Devin 信任页可能被误报 ready；prompt 超时去向未捕获，不能凭缺少执行痕迹断定未投递。测试曾受脚本路径祖先的真实 `.herdr-fleet.toml` 影响，#44 针对该情况隔离。锁实验只证明合成 barrier 下的等待关系，#45 的 pane 重绑测试也为模拟。历史原始报告、命令和日志位于本机归档 `2026-10-31-issues-37-39-38/`，见[索引](README.md)。
