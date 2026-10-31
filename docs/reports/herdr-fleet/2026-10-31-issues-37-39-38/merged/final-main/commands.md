# 合并后 main 组合验收证据

tested base: `12783ac14b4e23ce65dd5ac890acee4b894097b2`（= 归档任务分支 base = origin/main，即 #45 合并提交）

执行者：归档任务 agent（Devin，--permission-mode dangerous）；非 Root 亲自运行。

## 环境

- 工作目录：`/home/glx/scripts/tc-skills-fleet/worktrees/fleet-closeout-report/tc-skills`（真实 fleet worktree，祖先含 `/home/glx/scripts/tc-skills-fleet/.herdr-fleet.toml`）
- Python：`/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python`（pytest 9.2.0）
- 运行前 `git status` 干净，HEAD == tested base

## 命令与退出码

```
cd /home/glx/scripts/tc-skills-fleet/worktrees/fleet-closeout-report/tc-skills
/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python -m pytest tests/herdr-fleet/test_fleet.py tests/herdr-fleet/test_event_lock.py -q
```

- 结果：**139 passed in 157.78s (0:02:37)**
- 退出码：0（`exit-code.txt`）

## 日志与文件

- `pytest.log`：上述命令的完整原始 stdout（-q 点阵输出）
- `exit-code.txt`：`EXIT=0`
- `tested-base.txt`：`12783ac14b4e23ce65dd5ac890acee4b894097b2`

## 边界

- 真实 fleet worktree 中两测试文件全量通过；B（#44）合并后，`test_config_discovery_bases_and_errors` 不再受脚本路径祖先的真实 `.herdr-fleet.toml` 影响（合并前同环境为 132 passed / 1 failed）。
- 测试使用 fake-herdr；未做真实 Herdr 端到端验证，未实测真实 IPC 时长。
- 只跑 `test_fleet.py` 与 `test_event_lock.py` 两个文件；未跑 `test_ask_user.py` 等其他套件。
