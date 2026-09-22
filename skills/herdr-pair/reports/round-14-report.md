# Round p02-r006 (revision 1) — Issue #14: pair.status popup 看板 + 侧边栏重放

## 契约核对

- round_id: `p02-r006`, revision: `1`, executor pane: `w2X:p3`
- contract_hash: `5516b74b6f3d26878dbfd41a4229c1aa49c16d3a2a611b80c86f756f7b597d57`
- contract_path: `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p02-r006.rev1.contract`
- 契约正文与收到的任务书逐字一致（round_id 以首行 `p02-r006` 为准；协议操作段中"预期 p02-r001"为模板残留，按首行执行）。
- 协议流水：`check-round --revision 1` → exit 2 `reason=not_accepted`（预期）；`ack-round --action accept` → `work_status=accepted`；`ack-round --action start` → `work_status=running`；写报告前 `check-round` → `allowed=true`（`.round14-tmp/check-final.json`）。
- 全程 `--cwd /public/scripts/tc-skills`、不传 `--state-dir`、未设 `XDG_STATE_HOME`（默认 `~/.local/state`，与上一轮一致）。

## 实现

### `skills/herdr-pair/scripts/pairctl.py`（+48）

- 新增 `board_fields(data)`（紧挨 `pending_payload`）：从同一份已加载 state 计算六个看板字段——`goal`（缺省 `""`）、`phase`、`rounds`（投影为 `{round_id,status,executor}`）、`pending_dispatch_age_s`（无未决派发或 `created_at` 不可解析时为 null，否则 `max(0, int(now-created_at))`）、`resume_pending`（无记录为 null）、`notices`（含 `shown=false` 行）。
- `cmd_status` exit-0 payload 增补 `goal`/`rounds`/`pending_dispatch_age_s`（`phase`/`resume_pending`/`notices` 原有字段复用 board 值）；exit-2 未决派发出口经 `PendingDispatchError` 新增的 `board` 参数携带同一组字段，main() 里 `payload.update(exc.board)` 后照常叠加 `notices`——不再为看板二次读 state.json。
- `PendingDispatchError.__init__` 加可选 `board`，`load_ready` 等其他 raise 点不传、输出不变。

### `skills/herdr-pair/hooks/action.py`（+118）

- `pair.status` 改走 `run_pair_status`：先 emit 合并后的 status JSON（保持 stdout 单行干净），当 payload 含 `phase`（含 exit-2 未决派发）再依次
  1. `herdr plugin pane open --plugin tc.herdr-pair --entrypoint pair-status --placement popup`（`open_status_popup`，best-effort，超时/OSError 静默，不改变动作出口码）；
  2. `sidebar_view_params` 计算视图参数并 `write_sidebar_view` 落盘到 `$HERDR_PLUGIN_STATE_DIR/sidebar-view.json`（临时文件 + `os.replace`；env 缺失或写失败静默）。
- 视图参数：`source=tc.herdr-pair`、`label=herdr-pair`、`filter={op:"in",field:"pane_id",values:[planner_pane, executor]}`；executor 取活跃轮次，无活跃轮次取 `rounds` 末条非空 executor，仍无则仅规划者窗格；与 planner 重复时去重。`planner_pane` 优先 payload、回退 `load_state`（未决派发 payload 无该字段时仍工作）。
- 非 `phase` 的 stdout（lock_timeout 等 rejected/非 JSON）不开窗格、不写文件；动作本身不调用 `agent.view.set`、不进写类焦点门、不写 state.json。

### 新文件

- `skills/herdr-pair/hooks/status_pane.py`：`sys.path` 注入 hooks 目录后 `import action` 复用同一解析优先级（显式参数 > `PAIRCTL_*` env > 窗格索引 > 焦点 cwd > 工作区 cwd）与 `run_pairctl`；单次 `pairctl status` 的 stdout JSON（含 exit 2 未决派发）渲染为可读文本；每条 notice 打印 `title` 与 `shown=<true|false>`（附 `reason`），`shown=false` 的限流通知在看板可见。不 import pairctl、不写任何状态。
- `skills/herdr-pair/hooks/on_startup.py`：只重放——`$HERDR_PLUGIN_STATE_DIR/sidebar-view.json` 不存在/不可解析/非对象则 exit 0；否则向 `$HERDR_SOCKET_PATH`（AF_UNIX SOCK_STREAM）`sendall` 一行 `{"id":"herdr-pair-startup-view","method":"agent.view.set","params":<文件对象>}` + `\n`，exit 0。socket 缺失或写失败同样 exit 0，不触碰配对状态。

### `skills/herdr-pair/herdr-plugin.toml`（+14/-4）

- 新增 `[[panes]] pair-status`（`hooks/status_pane.py`，`placement="popup"`）与 `[[startup]]`（`hooks/on_startup.py`）；五个 `[[actions]]`、两个 `[[events]]`、`min_herdr_version="0.8.0"` 未动；头部注释里"无 popup 看板与侧边栏"的过时描述已更正。

### `skills/herdr-pair/tests/test_pairctl.py`（+221）

新增五个测试（全部先红后绿）：

- `test_status_payload_includes_board_fields`：exit 0 六字段齐、`rounds[0]` 三元组正确；`set_pending_dispatch(120)` 后 exit 2 六字段仍在，`pending_dispatch_age_s` 与 `created_at` 折算一致（±30s 容差）。
- `test_status_pane_prints_rate_limited_notice`：注入 `shown=false reason=rate_limited` 的 notice，看板 stdout 含 title 与字面 `shown=false`。
- `test_pair_status_opens_popup_from_executor_focus`：执行者 `w1:p2` 焦点下 exit 0，假 herdr 恰好只收到上述 `plugin pane open`（无 `agent.view.set`——本就不走 CLI），state.json 字节不变。
- `test_startup_replays_saved_sidebar_view`：`pair.status` 生成 `sidebar-view.json` 后，本地监听 unix socket 收到唯一一帧 `agent.view.set`（id 非空、source/label/filter.values 含 `w1:p1`+`w1:p2`）；删文件后重跑零写入。
- `test_plugin_manifest_lists_popup_pane_and_startup`：`pair-status`/`popup`/`hooks/on_startup.py` 断言 + 五动作两事件 `min_herdr_version` 回归。

既有测试改动两处（均属本轮范围必然调整）：

1. 新增 helper 原名 `run_hook` 与第 993 行 SessionStart 钩子 helper 同名覆盖，导致 6 处 TypeError——改名 `run_plugin_hook`。
2. `test_plugin_manifest_declares_resume_hook_only` 原先断言 manifest 不含 `panes`/`startup`——该断言是 issue #14 之前的白名单，本轮按规格新增两个表，改为断言 actions 首项为 `pair.status`，详细结构断言移交新测试。

## 验证

- 红：`pytest -k`（5 新测试）→ `5 failed, 2 passed`，确认实现缺失。
- 绿（中途）：同上 `7 passed`。
- 全量：`slot cpu -- python3 -m pytest -q` → **exit 0**，`174 passed, 69 subtests passed in 69.63s`（`.round14-tmp/pytest-full2.out`，exit 记录 `.round14-tmp/pytest-exit2.txt`）。中途一次全量曾 8 failed（上述 run_hook 覆盖 + 旧 manifest 白名单），修复后归零。
- 环境预检：`slot audit` 无绕过进程，`slot status` 已留档（内存 36.8G/76G，各池空闲）；pytest 全程走 `slot cpu`。
- API 事实核对（`herdr-api-080.json`）：`agent.view.set` params `{source,label,filter,sort}`，`in` 过滤形状 `{op,field,values}` 与写入一致；`plugin.pane.open` 收 `plugin_id/entrypoint/placement`，`PluginPanePlacement` 枚举含 `popup`。

## git 状态

- HEAD: `32a04084ec5bdf92c69ace65f3fd0fe9f548d765`（未 commit/push，按 fence 停止）
- 前（开工时）：四个被改文件均为 HEAD 干净态；未跟踪集合 = 现集合减去 `.round14-tmp/`、`hooks/on_startup.py`、`hooks/status_pane.py`、`reports/round-14-report.md`（本轮未新增其他路径）。
- 后 `git status --short`：

```
 M skills/herdr-pair/herdr-plugin.toml
 M skills/herdr-pair/hooks/action.py
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/ .round9..14-tmp/ docs/specs/ docs/tickets/ herdr-api-080.json
?? skills/herdr-pair/hooks/on_startup.py
?? skills/herdr-pair/hooks/status_pane.py
?? skills/herdr-pair/reports/   （含前轮报告与本报告）
```

- `git diff --stat`：`4 files changed, 391 insertions(+), 10 deletions(-)`（pairctl +48、action.py +118、toml +14/-4、tests +221）。

## 推迟 / 跳过 / 降级

- `status_pane.py`/`on_startup.py` 对解析失败、pairctl 非 JSON、socket 不可达一律打印一行可读信息后 exit 0——弹窗/启动钩子属便利面，按"找不到就静默"处理，未做任何重试或告警通道（规格未要求）。
- `open_status_popup` 不校验 herdr 返回值：popup 打开失败只影响当次展示，动作 JSON 已先落 stdout。
- 侧边栏 `values` 对 executor==planner 去重；`HERDR_PLUGIN_STATE_DIR` 未导出时不写文件（无插件实例目录可写）。
- 真机验收（herdr plugin link/enable、真实 socket 回放）按 fence 明确不做；测试全程假 herdr + 本地 socket。

## 假设

- 协议段"预期 p02-r001"为模板残留，以契约首行 `round_id=p02-r006` 为准（check-round/ack-round 均按 p02-r006 通过）。
- `pairctl status` exit-0 的 `pending_dispatch_age_s` 恒为 null（有未决派发必走 exit-2 出口），符合"没有未决派发时为 JSON null"。
- `rounds` 投影严格取三键；规格"元素含 round_id、status、executor"按最小集合实现，看板与 values 计算均只需这三键。
- `sidebar-view.json` 写入用原子替换；`on_startup.py` 只 sendall 不读回执（契约只要求写一行 + exit 0）。
