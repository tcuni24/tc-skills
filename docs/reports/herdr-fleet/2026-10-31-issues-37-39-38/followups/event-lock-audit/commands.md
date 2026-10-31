# 命令与退出码记录

## 环境

- OS: Linux 6.14.0-36-generic
- Python: `/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python` → Python 3.12.3（只读复用任务 #37 的 venv）
- 被测脚本（只读）：`/home/glx/scripts/tc-skills-fleet/worktrees/event-lock-audit/tc-skills/skills/herdr-fleet/scripts/fleet.py`
- tc-skills HEAD: `98831bf4613b7110ff876e5e5f36148bb55d0781`
- 实验脚本：`/home/glx/scripts/tc-skills-fleet/worktrees/event-lock-audit/evidence/experiment.py`，sha256 `29eaa8d96f0dadcce6e84814d1f9e283c2545d3315d1a9a680774e58e46a22fb`

## 运行

```bash
cd /home/glx/scripts/tc-skills-fleet/worktrees/event-lock-audit/evidence
/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python experiment.py 2>&1 | tee run-output.txt
echo "SCRIPT_EXIT=${PIPESTATUS[0]}"
```

- 输出：见 `run-output.txt`（15/15 PASS；/proc/locks pending 证据、fake-herdr 调用日志、四个进程 exit=0）
- **SCRIPT_EXIT=0**
- 复跑：`... python experiment.py | tee run-output-2.txt` → 同样 15/15 PASS，**SCRIPT_EXIT=0**（`run-output-2.txt`，workdir `/tmp/fleet-lock-audit-rh6odx5k`）
- 临时现场保留：`/tmp/fleet-lock-audit-8ywg9x3i` 与 `/tmp/fleet-lock-audit-rh6odx5k`（root/state/fake-herdr/log）

## 代码事实核对（只读）

```bash
# 全局锁内唯一 IPC 调用扫描：对 fleet.py 全部 `with fleet_state(` 块查
# herdr/herdr_result/subprocess.run/run/git/fetch/repo_info/pr_info 调用
# 结果：仅命中 419-436: agent = herdr('agent', 'get', a.pane)['agent']
```

## 仓库清洁复核

```bash
cd /home/glx/scripts/tc-skills-fleet/worktrees/event-lock-audit/tc-skills
git status --porcelain | wc -l   # → 0（实验前后一致）
git rev-parse HEAD               # → 98831bf4613b7110ff876e5e5f36148bb55d0781
```

无提交、无 push、无 PR/issue 操作；`evidence/` 位于任务根（worktree 外层），不在 tc-skills 仓库内。
