# herdr-fleet 历史验收报告

这些是按批次整理的历史结论，不代表 PR 的实时状态或部署状态。完整原件含日志、快照与备份，未随仓库分发。

- [2026-10-30 Devin 性能试运行](2026-10-30-smoke.md)
- [2026-10-31 三项并行性能验收及后续收尾](2026-10-31-performance.md)
- [2026-10-31 #37/#39/#38 及后续修复](2026-10-31-fleet-issues.md)
- [2026-10-31 #48 回显误判修复](2026-10-31-report-echo.md)

本机原始归档：`/home/glx/.local/state/herdr-fleet/runs/tc-skills-e9fbca96ba83/archive-migration-20261031T233917Z/original-reports/`。这是此机器的路径，其他机器不能直接访问。可移植的定位方式：从 `${XDG_STATE_HOME:-$HOME/.local/state}/herdr-fleet/runs/<project-key>/<run-id>/` 找该项目与运行；本批 project-key 为 `tc-skills-e9fbca96ba83`，run-id 为 `archive-migration-20261031T233917Z`，原件在 `original-reports/`。共享完整证据须另行打包并检查敏感信息；仓库报告自包含，不依赖该本机路径。
