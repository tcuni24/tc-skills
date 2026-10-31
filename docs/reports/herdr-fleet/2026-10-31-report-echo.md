# 2026-10-31 #48 回显误判修复

历史验收针对 Devin 输入回显中的 FLEET-REPORT 模板被误认的问题。PR #49 的已验收 HEAD 为 `bd34c82`，随后经授权 squash 合并为 `c3787f5`；先前 OPEN 和保留现场是合并前状态。历史记录称任务 worktree、本地分支、tab 已清理，远端分支保留。

最终报告相关测试命令为 `python -m pytest tests/herdr-fleet/test_fleet.py -k "report_blocks or zero_lines" -q`：42 passed、113 deselected，执行者记录 exit 0；先前全套 178 passed 发生在最后新增一项边界测试之前，不能称作最终完整收集数。真实快照重放中 3 份 initial 返回 null、3 份 done 与历史回报一致；候选脚本的真实只读 status 也留有记录。复现需对应历史 HEAD 与原始快照；完整命令与日志位于本机归档 `2026-10-31-report-echo-48/`，见[索引](README.md)。

来源判断是 Devin 渲染形态的保守启发式，并非通用角色识别。窗口顶部来源不明的缩进块或回显后紧贴同样深缩进的输出可能返回 null；扩大 `--lines` 或用 `fleet peek` 人工查看，其他 agent 的回显形态未验证。收到回报不等于验收通过。
