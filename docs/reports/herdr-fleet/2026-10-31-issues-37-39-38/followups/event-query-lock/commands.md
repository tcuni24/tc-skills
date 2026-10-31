# 命令与退出码记录（修复 task_event 锁内 agent get）

## 环境

- OS: Linux 6.14.0-36-generic
- Python: `/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python` → Python 3.12.3, pytest 9.2.0（只读复用任务 #37 的 venv）
- 仓库 worktree: `/home/glx/scripts/tc-skills-fleet/worktrees/event-query-lock/tc-skills`，分支 `fix/event-query-lock`
- base: `468b1a895591441fb178b4b7ad1efd8228d7c55b`（origin/main，#40+#41+#42 已合并）
- 新 HEAD / PR HEAD: `102dcad6cbe0c972640b664f6af2a8b32e422985`
- PR: https://github.com/tcuni24/tc-skills/pull/45（OPEN，未合并）
- 快照: `/project/tmp/event-query-lock-102dcad6cbe0c972640b664f6af2a8b32e422985`（`git archive HEAD` 精确快照，无祖先 `.herdr-fleet.toml`）
- 文件 sha256：fleet.py `bd62700a…`，test_event_lock.py `38583c93…`（见上方命令输出）

## 改动

- `skills/herdr-fleet/scripts/fleet.py`：`task_event(a)` → `task_event(a, slug)`；`agent get` 移到 `task_lock(slug)` 内、`fleet_state` 全局锁外；写阶段重入 `fleet_state(write=True)` 后以 `task_index_matches` 复核 pane 仍绑定同一 root/slug/role，再 `load` 最新 TASK.md 走原轮次/dedup/supersede 逻辑；`exited` 仍不查询。
- `tests/herdr-fleet/test_event_lock.py`：新增，6 项，自包含 fake-herdr 与合成 FLEET_ROOT/XDG_STATE_HOME，不经 `fleet new`/git。
- `git diff --check` → **0**（无空白错误）。

## 验证运行

```bash
# 1) worktree 内新文件
cd .../event-query-lock/tc-skills
<venv>/python -m pytest tests/herdr-fleet/test_event_lock.py -v   # → 6 passed → EXIT=0
#   日志：worktree-test-event-lock.log

# 2) worktree 内既有套件（已知祖先配置用例失败）
<venv>/python -m pytest tests/herdr-fleet/test_fleet.py -q        # → 1 failed, 132 passed → EXIT=1
#   日志：worktree-test-fleet.log；唯一失败 test_config_discovery_bases_and_errors：
#   其 drop FLEET_ROOT 的 `fleet ls` 在本 worktree 沿 __file__ 向上发现祖先
#   /home/glx/scripts/tc-skills-fleet/.herdr-fleet.toml 并列出真实任务，断言 returncode 失败。
#   属 worktree 环境已知问题（B 侧已记录），与本次改动无关；在快照中通过。

# 3) 快照内两个文件全量（须全过）
cd /project/tmp/event-query-lock-<sha>
<venv>/python -m pytest tests/herdr-fleet/test_fleet.py tests/herdr-fleet/test_event_lock.py -q
#   → 139 passed → SNAP_EXIT=0（快照内无祖先配置，test_config_discovery_bases_and_errors 通过）
#   日志：snapshot-pytest.log

# 4) 回归有效性对照：旧 fleet.py（468b1a8）+ 新测试
git show 468b1a8:skills/herdr-fleet/scripts/fleet.py > skills/herdr-fleet/scripts/fleet.py
<venv>/python -m pytest tests/herdr-fleet/test_event_lock.py -q   # → 5 failed, 1 passed → OLD_EXIT=1
git checkout HEAD -- skills/herdr-fleet/scripts/fleet.py          # 恢复新 HEAD 内容，status 干净
#   日志：old-code-regression-check.log；唯一通过的为 get 失败/exited 语义测试（本就不该变）。
```

## 边界

- 未跑真实 pane/herdr IPC 实验；fake-herdr 只证明锁序与排队关系，真实卡顿时长不作结论。
- 未合并、未关闭 issue、未清理 worktree/快照；workflow.md 等现有文档未改（围栏外，由 Root 协调）。

## Doc-only sync commit (workflow.md lock descriptions)

- `git diff --check` → `DIFFCHECK=0`
- Changed files (102dcad..d803c9d): `skills/herdr-fleet/references/workflow.md` only, +2/-2 lines
  - L74: "只在读写内存数据时持有" → "只在读写状态与任务本地文件（panes/inbox、TASK.md）时持有"；"task_event 内既有在状态锁中调用 agent get 的旧模式不变" → "task_event 的 agent get 同样只在任务锁内、全局状态锁外执行"
  - L78: added task_event lock semantics: `task_lock` inside / `fleet_state` outside `agent get`; write re-entry re-checks `panes` binding `root`/`slug`/`role` then re-reads `TASK.md`; rebind/removal during query discards result; `exited` does not query
- New HEAD: `d803c9dbd8d56500266fb8ef013120a6cf2cceba` (pushed to `fix/event-query-lock`, PR #45)
- Unchanged file hashes vs 102dcad:
  - `sha256(fleet.py) = bd62700a8a73e40d63b0456bdcfcd9a3e6cb1c0b19a516e92129d2f2ea1e7161`
  - `sha256(test_event_lock.py) = 38583c93ad0d2b43eba5d3f29f6c44e72788989c93a06d8762301fef5c07ca19`
- `sha256(workflow.md @ d803c9d) = bc323c6134eab0edfdd689f55cb2837adfc8604426507dd340811af9c892e7bc`
- PR body updated (memory → state+local files wording; workflow.md sync bullet added)
- No pytest re-run (doc-only); prior snapshot evidence: 139/139 PASS at 102dcad
