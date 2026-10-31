# 报告C诊断：`task_event` 在 `fleet_state` 全局锁内调用 `herdr agent get`

- 代码 base：`98831bf4613b7110ff876e5e5f36148bb55d0781`（#40+#41+#42 组合，本任务 worktree HEAD，与 TASK.md base 一致）
- 只读代码与已有测试；证据脚本/输出全部在 `evidence/`（任务根，仓库外）；仓库 tracked/untracked 均无改动。
- 范围：仅此一项诊断；未改生产代码、未实测真实 herdr IPC。

## 代码事实（tc-skills @ 98831bf，skills/herdr-fleet/scripts/fleet.py）

1. **全局锁**：`fleet_state(write)`（L222-238）对 `$XDG_STATE_HOME/herdr-fleet/lock` 做 `fcntl.flock(LOCK_EX)`。读写路径都用排他锁（读路径 L355 `inbox`、L660 `event` 预检也是 `LOCK_EX`），是**所有任务/coordinator 共享的单锁**。
2. **锁序**：`event`（L659-672）= `fleet_state()`读(释放) → `task_lock(slug)` → `task_event` → `fleet_state(write=True)` → **`herdr('agent','get', a.pane)`（L436）**。`task_lock` 只串行同任务操作；跨任务串行点只有这一个全局锁。`task_event` 唯一调用点是 L670（总在 `task_lock` 内）。
3. **无超时**：`herdr()`（L63-65）→ `run()`（L52-56）→ `subprocess.run` 无 `timeout`。锁内 `agent get` 在真实 IPC 卡顿/挂起时**无界持有全局锁**。对比：deliver 路径的 `herdr_result`（L478-487）有 `timeout=5`。
4. **唯一性**：程序扫描确认 L436 是全部 `fleet_state` 块中唯一的 IPC/子进程调用。同块内另有 `load()`(L424)/`save()`(L456,L473) 的 TASK.md 文件 IO（本地磁盘 IO，有界但与 IPC 同锁）。
5. **既有锁纪律**：deliver 路径（L593-656）把 `agent get`/`agent prompt`/`notification show` 放在 `delivery_lock`+按序 `task_lock` 内、**`fleet_state` 外**（L604-606 注释明确"the cross-task state lock is still never held while calling herdr on this path"）；`submit`（L685-717）同样在 `task_lock` 内做 `agent get`(L688)/`agent prompt`(L698)，`fleet_state` 只做存取。`task_event` 是唯一违反该纪律的点。

## 可复现实验（fake-herdr + 隔离临时 state）

脚本 `experiment.py`（自带 fake-herdr，逐字复制自 tests/herdr-fleet/test_fleet.py @ 98831bf）：

- 合成 `FLEET_ROOT`（一个 `web` repo、两个任务 task-a/task-b）、`XDG_STATE_HOME`（panes.json 登记 pa/pb/coordinator）、`FLEET_HERDR`=fake。**不执行 `fleet new`，无真实 git/herdr/pane 操作。**
- A = `fleet event --pane pa --status done`，env 只对 A 设 `BARRIER`/`BARRIER_CALL='agent get'`：A 在 `fleet_state(write=True)` 内调用 fake `agent get pa` 时被 barrier 有界暂停（fake 内部 10s 上限）。
- 同步原语全部是条件等待（barrier 文件、`/proc/locks` pending 项、进程退出），无时序猜测。
- A 挂起期间启动三个旁观者：B = 另一任务 `fleet event --pane pb --status done`；C = 未登记 pane `fleet event --pane pz --status done`；I = 只读 `fleet inbox --json`。

### 结果（run-output.txt，15/15 PASS，exit 0）

- barrier 持有期间 `/proc/locks`：A(pid 781166) 持有 lock inode `38424837` 的 FLOCK WRITE；B/C/I 三者均为同一 inode 上的 pending（`->`）WRITE 请求，wchan 均为 `locks_lock_inode_wait`。**内核级证据：三者排在同一个全局锁上。**
- fake 调用日志：barrier 持有期间只有 `["agent","get","pa"]`；B 的 `agent get pb` **始终未出现**（B 连自己的 IPC 都没到，卡在 `fleet_state` 入口）。
- C（完全不存在的 pane，纯无操作）与 I（纯读）同样排队——证明影响不限于"另一任务写路径"：**任何 event/inbox 读都等待**。
- 释放 barrier 后：A/B/C/I 全部 exit 0；B 完成 `agent get pb`；inbox.json 含 task-a/task-b 两条 `done`/`pending`（coordinator=fake working → 无 `agent prompt`）；两 TASK.md `event_round={baseline:10,notified:true,last_terminal_seq:12}`。**干净结束，无残留。**

结论（fake-herdr 可重现影响）：一个任务的 `agent get` 在全局锁内停顿期间，**所有其他任务的 event（含无关 pane 的 no-op）与 inbox 读写整体排队**，直到 IPC 返回；真实 IPC 无超时，故真实环境下停顿时长=IPC 时长，可无限。

## 未实测边界

- 真实 herdr `agent get` 的 IPC 延迟/挂起概率未测；fake-herdr 只证明锁序与排队关系，不证明真实卡顿时长。
- 未测 `deliver`/`send`/`bind`/`done`/`doctor` 并发路径在此窗口的排队（代码事实显示它们同样取全局锁，推断同样等待）。
- flock 公平性/FIFO 未测；`->` pending 展示依赖 Linux `/proc/locks`。

## 最窄修复建议（不实施）

把 L436 的 `agent get` 移出 `fleet_state(write=True)`、保留在调用方已持有的 `task_lock(slug)` 内：

1. `event()`/`task_event` 先用短 `fleet_state()` 读解析 pane→entry；
2. `task_lock` 内、全局锁外执行 `agent get`（与 `submit` L688 同模式，同任务 send/event 仍串行，seq 快照语义不变）；
3. 再进 `fleet_state(write=True)`，按 `bind` L318-321 既有模式复核 `panes[a.pane]` 仍绑定同一 slug（防 `open`/`register_task` 重绑窗口），然后走原逻辑。
4. 可选加固：给 `herdr()` 子进程加超时（对齐 `herdr_result` 的 5s），防止任何残余锁内/锁外 IPC 无限挂起。

锁序要求：`fleet_state` 保持最内层且内部不得调用 herdr/git/gh（与 L604-606 注释一致）；`task_lock`/`delivery_lock` 永远在外层。重校验：跑通现有 event 轮次/dedup/supersede 测试（同任务串行性由 `task_lock` 保持）；新增回归测试——barrier 挂 `agent get` 时并发第二任务 event 应能在 fake 日志中出现其 `agent get`（不再等全局锁），以及 pane 在读写锁间隙重绑时不产生错 slug 事件；exited 路径不发起 `agent get` 保持不变。

## 文件

- `experiment.py` — 证据脚本（可重复运行，exit 0=全部断言成立）
- `run-output.txt` — 完整输出（15/15 PASS）
- `commands.md` — 运行命令/环境/退出码
- `herdr.log` / `inbox.json` / `panes.json` / `TASK-a.md.final` / `TASK-b.md.final` — 终态归档
