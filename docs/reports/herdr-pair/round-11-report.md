# Round 11 报告 — p01-r003（issue #11：`notices` 通知落账 + `wake` 唤醒点 + 锁超时 + 钩子失败路径）

## 1. 轮次标识

| 项 | 值 |
|---|---|
| round_id | `p01-r003` |
| revision | 1 |
| contract_hash | `6c6df044a2e94950c51f8d30f43b36d42353781a782161a48d3dd0ed33136f23` |
| contract_path | `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p01-r003.rev1.contract` |
| executor | `w2X:p1` |
| work_status（报告落盘时） | `running` |
| acceptance | `python3 -m pytest -q` → **exit 0，`151 passed, 69 subtests passed`**（附录 B） |
| check-round | 协议要求的每个写入点前均执行，全部 `allowed=true`、`reason=ok`、`work_status=running`、revision=1；共 65 份存档（`check-before-red-tests`、`check-before-report`、`check-01` … `check-63-final`），最近一次 `.round11-tmp/check-63-final.json` |

协议命令一律 `python3 /home/tcuni-claw/.claude/skills/herdr-pair/scripts/pairctl.py <sub> --cwd /public/scripts/tc-skills --round-id p01-r003 --revision 1 --pane w2X:p1`（未传 `--state-dir`）。接受前 `check-round` 预期 exit 2（`not_accepted`），已验证；随后 `ack-round --action accept`、`ack-round --action start` 依次完成（scope/hash 取自 check-round 返回值原文）。被改实现是仓库内 `skills/herdr-pair/scripts/pairctl.py`，协议脚本未动。两轮写入（首轮 + 规划者复核后的修订轮）合计 **65 份** check JSON 存档（`.round11-tmp/check-before-red-tests.json`、`check-01` … `check-63-final`、`check-before-report`），逐次核对 `allowed/reason/work_status` 均为 `true/ok/running`。

**修订轮（规划者复核未验收后，仍在 p01-r003 revision 1 内）**：钩子失败判据收紧为契约原文的两类（pairctl 文件不存在、stdout 非 JSON），非 0 退出码本身不再算失败；`record_notice` 返回值改为"是否新追加了一条 notice"。其余实现保留，详见 §5.4。

## 2. Preflight（slot）

按环境规则，重活前执行 `slot audit` + `slot status` 并落盘 `.round11-tmp/slot-audit.log`、`.round11-tmp/slot-status.log`：

- `slot audit`：**没发现绕过 slot 的重进程。**
- `slot status`：heavy.slice 资源用量见 `slot-status.log` 首段。
- 本轮 pytest 单次 41–50 秒、内存占用小；**验收与红/绿运行均按惯例走 `slot cpu -- python3 -m pytest -q > … 2>&1`**（输出重定向 + `PYTEST_EXIT=$code` 追加到存档文件）。中间文件全部在本轮 fence 内的 `.round11-tmp/`，测试临时目录由各用例自己的 `TemporaryDirectory` 承担，且 `invoke`/`popen` 把 `XDG_STATE_HOME` 钉在用例临时目录内，未写用户真实 state 目录。未在 `/tmp` 下创建任何中间文件。

## 3. 前 / 后状态

### 3.1 之前（HEAD `b2e9115fcf826257e65de975df5c578ebe4bf5b2`，本轮未改前）

- `git status --short`（原文见 `.round11-tmp/git-baseline.txt`）：全部为未跟踪项（`.handoff/`、`.round9-tmp/`、`.round10-tmp/`、`.round11-tmp/`、`docs/specs/issue-1-herdr-pair.md`、`docs/tickets/`、`herdr-api-080.json`、`skills/herdr-pair/reports/`），**无已跟踪文件被修改**。
- `git diff --stat` 为空；HEAD = `b2e9115fcf826257e65de975df5c578ebe4bf5b2`。
- 之前字节：`pairctl.py` 117721、`test_pairctl.py` 140864、`hooks/on_planner_status.py` 6797（与 round-10 报告"之后"一致）；仓库内**没有** `hooks/notify.py`，pairctl **没有** `notices`/`wake`/锁超时概念。

### 3.2 红测试（六个契约必测 + FAKE_HERDR 扩展先落盘，实现未动）

`slot cpu -- python3 -m pytest -q` → **exit 1**：`5 failed, 143 passed, 69 subtests passed in 46.19s`
（5 failed = 契约必测中 5 个直接依赖新实现的用例；第 6 个 `test_no_pane_match_stays_silent` 在基线上即为绿——它是"零命中必须静默"的回归护栏，其 `PAIRCTL` 指向不存在文件的设定要等钩子失败路径落地才有区分度，红阶段通过即证明护栏成立。既有 143 个用例零回归。）
失败原文全文见附录 A（存档 `.round11-tmp/red-pytest.txt`，133 行）。

### 3.3 之后（实现落盘后）

- 字节（首轮结束时）：`pairctl.py` 128808、`test_pairctl.py` 154431、`hooks/on_planner_status.py` 6731、新建 `hooks/notify.py` 4123。
- 中间一轮（附录 B 绿-1）：`1 failed, 147 passed, 69 subtests passed`（exit 1）——是我红测试自身的时序错误（§5.1），修正后复跑全绿。
- 首轮验收（附录 B 绿-2）：`148 passed, 69 subtests passed in 50.59s`（exit 0）——但规划者复核判定未验收，钩子失败判据与 `record_notice` 返回值两处需修订（§5.4），遂进入修订轮。

### 3.4 修订轮（同一 p01-r003 revision 1 内，其余实现保留）

- 红（只加 3 个新测试，实现未动）：`slot cpu -- python3 -m pytest -q` → **exit 1**：`3 failed, 148 passed, 69 subtests passed in 47.41s`（存档 `.round11-tmp/red2-pytest.txt`，93 行）。三个失败恰为新增的三条：`test_hook_silent_when_pairctl_reports_lock_timeout`（钩子 exit 1——`run_pairctl` 把 exit 2 判成失败）、`test_hook_silent_when_pairctl_prints_json_and_exits_nonzero`（同因）、`test_rate_limited_stale_dispatch_still_reports_notified`（`dispatch_stale_notified` 为 False——`record_notice` 返回了 `shown`）。既有 148 个用例零回归。
- 修订实现：`hooks/notify.py` 的 `run_pairctl` 判据改为"文件存在 + stdout 是 JSON"（非 0 退出码不再算失败）；`pairctl.py` 的 `record_notice` 改为"新追加一条 notice 即返回 True"；钩子 docstring/注释同步。每个写入点前照旧 check-round（`check-34` … `check-42`）。
- **验收（最终）**：`slot cpu -- python3 -m pytest -q` → **exit 0**：`151 passed, 69 subtests passed in 47.58s`（附录 B 绿-4；151 = 首轮 148 + 修订轮新增 3）。绿-3 与绿-4 之间只改了钩子内的注释/docstring，两次均 exit 0（`green-pytest-3.txt` / `green-pytest-4.txt`）。
- 最终字节：`pairctl.py` 128920、`test_pairctl.py` 159893、`hooks/on_planner_status.py` 6997、`hooks/notify.py` 4428。
- `git status --short` / `git rev-parse HEAD` / `git diff --stat` 原文见附录 C.3：scope 内 3 个已跟踪文件被修改 + 1 个 scope 内新文件 `skills/herdr-pair/hooks/notify.py` 转为未跟踪待提交；HEAD 未变（未 commit）；`docs/`、`herdr-plugin.toml`、`claude_session_start_hook.py`、`.handoff/`、`.round9-tmp/`、`.round10-tmp/` 零改动。
- 验收后残留检查（`.round11-tmp/git-after.txt`）：`skills/herdr-pair/tests/tmp*` **无匹配**（`no matches found`）；`pgrep -af watch-compact-continue`（排除自身 shell 后）**无匹配**，无真实残留 watcher 进程。

## 4. 改动明细

### 4.1 `skills/herdr-pair/scripts/pairctl.py`（`+311/-24`，修订后）

1. **异常与常量**：
   - `PendingDispatchError.__init__` 增加可选 `notices` 属性（默认 `None`）：`status` 在 pending 路径也要带上整个 `notices` 数组；
   - 新增 `class LockTimeoutError(Exception)`——**刻意不是 `ValueError` 的子类**，否则会被 `main()` 的 `PAIRCTL_ERROR` 分支接走写 stderr，而契约要求 stdout 精确、stderr 为空；
   - `HOST_FAILURE_REASONS = {"rate_limited","busy","no_foreground_client","disabled"}`、`DEFAULT_DISPATCH_STALE_S = 900.0`、`STALE_DISPATCH_TITLE = "herdr-pair dispatch stale"`、`LOCK_TIMEOUT_PAYLOAD = '{"status":"rejected","reason":"lock_timeout"}'`（字面量即精确契约串，**绕过 `output()` 与 `sort_keys`**）。
2. **锁等待**：`lock_wait_s()` 解析 `PAIRCTL_LOCK_WAIT_S`——未设或非法/负数返回 `None`（即维持原阻塞语义，字节级不变）；`locked()` 改为 `LOCK_NB` 轮询重试，截止时间到仍拿不到锁 → `raise LockTimeoutError`。超时路径**不写 state、不发通知**（state.json 逐字节不变，由测试断言）。
3. **`load()`**：读入即 `data.setdefault("notices", [])`——旧状态文件在载入侧获得数组，`init` 新状态也写入 `"notices": []`。
4. **通知辅助（所有 `herdr notification show` 必经）**：
   - `record_notice(args, pp, data, *, title, body, dedupe_key="")`：非空 `dedupe_key` 已存在 → **既不调 herdr 也不写第二条记录**，返回 `False`；否则先 `run_herdr(["notification","show",title,"--body",body])`，再把 `{at,title,body,reason,shown,dedupe_key}` 追加进 `state["notices"]` 并 `persist`，**返回 `True`——语义是"新追加了一条 notice"，不是"宿主把它弹出来了"**（修订轮改动：原先返回 `shown`，导致 `rate_limited` 时通知已落账、`check_dispatch_stale` 却拿到 `False`，`wake` 的 `dispatch_stale_notified` 报假）。
   - `run_herdr` 抛 `ValueError`（herdr 缺失/超时）→ `reason="herdr_failed"`、`shown=False`；返回码非 0、`error` 非空、非 JSON、或 `result` 非对象 → 同样 `herdr_failed`/`False`；宿主 `result.reason ∈ HOST_FAILURE_REASONS` → 记录保留但 `shown=False`；否则沿用宿主 `reason`/`shown`（测试夹具未设 `FAKE_HERDR_NOTIFY_REASON` 时为 `manual`/`true`）。
   - `dispatch_stale_threshold_s()`：`PAIRCTL_DISPATCH_STALE_S`，非法或负数回落 900；`check_dispatch_stale(args, pp, data)`：无 pending → 不通知；`created_at` 不可解析 → 静默；`age > threshold` 才算 stale（严格大于）；`dedupe_key = f"dispatch-stale:{round_id}:{created_at}"`，title `herdr-pair dispatch stale`。naive 时间戳按 UTC 解释。
5. **`wake` 子命令**：`wake_check(args, source)` 在 state 锁内 `load()` → `check_dispatch_stale` → 若 `source == "watcher"` 且 resume 记录到期则锁内**锁外**执行 `spawn_resume_deliver(args, cwd, pane, "watcher")`（释放锁后再 spawn，避免子进程再抢锁死等）；**永不抛 `PendingDispatchError`**，正常输出 `{"status":"wake_checked","source",...,"dispatch_stale_notified","notices","resume","pane"}`，退出 0（锁超时除外）。`--source` 取值 `command|hook|status|watcher`（argparse `choices`）。
6. **`status`**：锁内改为 `load()` + `write_ledger` + `check_dispatch_stale` + **若仍有 `pending_dispatch` 则 `raise PendingDispatchError(pending, data["notices"])`**；正常 payload 增加 `"notices"`。`main()` 的 PendingDispatch 分支把 `exc.notices`（非 `None` 时）并进 payload，因此 pending 路径与正常路径都带整个数组。
7. **`watch-compact-continue`**：每轮先跑 `wake_check(args, "watcher", quiet=True)`（捕获 `LockTimeoutError` → 记一轮、sleep 继续，不打断 watcher）；`wake` 返回 `resume == "resume_delivered"` → 输出 `continue_prompted` 并以 `wake["pane"]` 收尾；`resume_expired` → `continue_skipped`；`attempted` 判定（`wake.get("resume") is not None`）前置于原有 due/pane 判断，命中即不再自行 spawn；原 spawn 语句重构为共用的 `spawn_resume_deliver`。`watch` 各轮同样跑 resume-deliver 到期判定；`status` 与 `wake --source command|hook|status` **只做 dispatch-stale 检查**，不做 resume-deliver。
8. **`resume-deliver` 过期路径**：原本直连 `run_herdr(notification show)` 的两处调用改为 `record_notice(..., title="herdr-pair resume expired", dedupe_key="")`——过期通知同样落账（`dedupe_key=""` 表示每次过期都可再记，符合契约"过期那次通知也走这个函数"）。
9. **`main()`**：`except LockTimeoutError` 分支**先于** `PendingDispatchError` 之前注册，`sys.stdout.write(LOCK_TIMEOUT_PAYLOAD)` + `return 2`（无换行、无 stderr）。

### 4.2 `skills/herdr-pair/hooks/notify.py`（新建，修订后 4428 B）

- `log_path()` / `marker_path()`：`HERDR_PLUGIN_STATE_DIR` 存在 → `<dir>/hook.log`、`<dir>/pairctl-failed-notified`；否则 `$XDG_STATE_HOME`（缺省 `~/.local/state`）→ `herdr-pair/plugin-hook.log`、`herdr-pair/pairctl-failed-notified`。与钩子原日志路径逐字一致。
- `append_log()`：best-effort（`OSError` 吞掉），日志失败不改退出路径。
- `run_pairctl(pairctl_path, argv) -> (ok, detail)` —— **修订轮按契约原文收紧判据**：
  - **失败恰好两类**：① `is_file()` 不存在 → `(False, "pairctl not found: …")`；② 子进程 stdout 不是可解析 JSON → `(False, "pairctl stdout was not JSON (exit N)[: stderr 尾行]")`（截断 300 字符）。`TimeoutExpired` / spawn `OSError` 之所以也算失败，是因为这两种情况下拿不到 JSON stdout——归到同一"非 JSON"类，detail 里注明原因。
  - **非 0 退出码本身不是失败**：`returncode` 只用于 detail 文案，不参与判定。`lock_timeout`（exit 2、stdout 为精确 JSON）、`planner_busy`（exit 2、JSON 对象）都走 `(True, …)`，钩子随后 exit 0 静默——这是 #10 静默契约。
  - 不再要求"JSON 必须是对象"以外的额外条件（`json.loads` 成功即 `True`）。
  - `env.setdefault("PAIRCTL_LOCK_WAIT_S","2")` 给子进程一个**有界**锁等待：忙锁时 pairctl 自己以 lock_timeout JSON 回答，钩子据此静默，而不是挂死。
  - **不 import pairctl**（按契约以子进程方式运行）。
- `notify_pairctl_failed(pane, path, detail)`：写一行 `pairctl_failed pane=… pairctl=… detail=…` 日志 → marker 已存在则**到此为止**（不再碰 herdr）→ 否则 `PAIRCTL_HERDR`（缺省 `herdr`）`notification show "herdr-pair pairctl failed" --body <同一行>`，`timeout=30`，**best-effort（`OSError`/超时吞掉，绝不抛出）**，尝试后写 marker（写在尝试之后：中途崩溃下一轮仍有机会重试一次）。

### 4.3 `skills/herdr-pair/hooks/on_planner_status.py`（`+24/-30`，修订后 6997 B）

- 顶部 `sys.path.insert(0, 本目录)` 后 `from notify import append_log, notify_pairctl_failed, run_pairctl`；**删除本文件的 `log_path`/`append_log` 与 `import subprocess`**（日志语义原样搬进 `notify.py`，`ambiguous_pane` 分支继续调 `append_log`）。
- `main()` 命中候选后改为 `ok, detail = run_pairctl(pairctl, argv)`（`argv` 不再含解释器，由 `run_pairctl` 拼 `sys.executable`）；`not ok` → `notify_pairctl_failed(...)` + **`return 1`**；`ok` → `return 0`。零命中/错事件/忙/无记录/epoch 未进/不匹配等静默路径原样 exit 0、stdout/stderr 为空。
- docstring 与实现处注释（修订轮同步改写）：响亮路径 = "pairctl 无法回答（文件不存在、stdout 非 JSON）→ 记 `pairctl_failed`、一次性通知、exit 1"；并明确"**非 0 退出码本身不是失败：lock_timeout 与 planner_busy 都以 exit 2 回答 JSON，这些一律静默 exit 0**"；"钩子从不 import pairctl、从不写 state.json；唯一的 herdr 调用是那次失败通知 `notification show`，从不执行 `herdr agent prompt`"。
- 钩子仍未持有任何 state 写句柄：失败用例断言 `resume_pending.status` 仍为 `pending`（状态零改动）。

### 4.4 `skills/herdr-pair/tests/test_pairctl.py`（`+408/-2`，修订后 159893 B）

1. **FAKE_HERDR 扩展**：`FAKE_HERDR_NOTIFY_REASON` 环境变量 → `result.reason` 取该值，且值属四个宿主失败原因时 `result.shown=false`；未设 → 维持 `shown:true` / `reason:manual`（既有断言不受影响）。
2. **辅助件**：`notification_calls()`（过滤 `["notification","show"]` 的 herdr 调用）、`set_pending_dispatch(age_s, round_id="p01-r007")`（直接把 `pending_dispatch` 写进 state.json，返回 `created_at`——`send-round`/`start-round` 在各测试路径上都会清掉自己的 pending，staleness 场景只能手工种）。
3. **六个契约必测**（名称逐字）：
   - `test_notice_recorded_when_notification_is_rate_limited`：两次失败 prompt 使记录过期，过期通知经辅助函数落账——`FAKE_HERDR_NOTIFY_REASON=rate_limited` 下 `notices[0]` 字段集合恰为 `{at,title,body,reason,shown,dedupe_key}`、`reason=="rate_limited"`、`shown is False`。
   - `test_missing_pairctl_logs_and_notifies_once`：`PAIRCTL` 指向不存在文件 → exit 1、日志含 `pairctl_failed`、恰好一条 `herdr-pair pairctl failed` 通知、marker 落盘；第二次事件只记日志不再通知；非 JSON pairctl 输出同类失败、仍不再通知；状态零改动。
   - `test_no_pane_match_stays_silent`：零命中 → exit 0、stdout/stderr 空、**日志文件与 marker 都不产生**（即便 `PAIRCTL` 指向不存在文件）。
   - `test_lock_timeout_abandons_event_and_later_wake_retries`：外部进程持 `state.lock` + `PAIRCTL_LOCK_WAIT_S=1` → `wake` 退出 2、stdout **精确等于** `{"status":"rejected","reason":"lock_timeout"}`、stderr 空、`state.json` 逐字节不变、`notices==[]`、零 herdr 调用；锁释放后同一命令重试 exit 0、`status=="wake_checked"`、`dispatch_stale_notified` 为假。
   - `test_stale_dispatch_notifies_once`：1000 s 前的 pending → `wake --source command` 恰一条 `herdr-pair dispatch stale`、字段集合与 `dedupe_key=dispatch-stale:p01-r007:<created_at>` 精确断言；第二次 wake（hook 源）静默；`status` 在 pending 路径返回 2 且 payload 带同一 `notices`；pending 清除后正常 `status` 仍带同一数组；herdr 被换成不存在的二进制 → `wake` 仍 exit 0、记录 `reason=="herdr_failed"` / `shown is False`。
   - `test_dispatch_stale_threshold_is_configurable`：`PAIRCTL_DISPATCH_STALE_S=300` 下 500 s 判 stale（通知 1 次）；非法阈值回落 900 → 500 s 静默；负值同样回落；`age == threshold` 不算 stale、`age > threshold` 才算；无 pending 不通知。
4. **修订轮新增的三个测试**（规划者复核后补，红→绿见 §3.4）：
   - `test_hook_silent_when_pairctl_reports_lock_timeout`：已选中候选（`arm_and_advance_epoch` + idle）+ 外部进程持 `state.lock` + `PAIRCTL_LOCK_WAIT_S=0.2` → **钩子 exit 0、stdout 与 stderr 均空、`hook.log` 无 `pairctl_failed`（文件不产生）、无 `pairctl-failed-notified` marker、零 `notification show`、`state.json` 逐字节不变、`resume_pending.status` 仍为 `pending`**；finally 杀掉持锁进程后，下一次钩子事件正常 `resume-deliver` → `status == "delivered"`。
   - `test_hook_silent_when_pairctl_prints_json_and_exits_nonzero`：假 pairctl 打印 JSON 对象并 `sys.exit(2)`，`reason` 遍历 `planner_busy` 与 `lock_timeout` 两种 → 钩子 exit 0、stdout/stderr 空、无 `pairctl_failed` 日志、无 marker、零通知、状态未被认领。
   - `test_rate_limited_stale_dispatch_still_reports_notified`：stale pending + `FAKE_HERDR_NOTIFY_REASON=rate_limited` → `wake` 的 `dispatch_stale_notified` 为 **True**（新契约：只要新落一条 notice 就算）、`notices[0]` 为 `rate_limited`/`shown is False`；第二次 wake 因 dedupe key 已记录而 `dispatch_stale_notified` 为 False、`notices` 仍 1 条。
   - 契约第 3 条（文件不存在 / stdout 非 JSON 仍 exit 1 + 日志 + 只通知一次）由**既有** `test_missing_pairctl_logs_and_notifies_once` 覆盖，修订轮零改动、持续绿（红-2 与绿-4 均通过）。
5. **既有测试零改动**（除红测试自身的时序修正，见 §5.1）。

## 5. 红→绿期间发现并处理的问题（如实披露）

### 5.1 我的红测试自身的时序错误（绿-1 的唯一失败）

`test_stale_dispatch_notifies_once` 里"herdr 失败仍落账"一节之前，我把 `pending_dispatch` 置为 `None`（用于验证正常 `status` 也带 `notices`），却忘了为最后一步重新种回 stale pending——`check_dispatch_stale` 无 pending 必然不通知，于是断言 `len(broken)==1` 实得 0。**契约明确"无 pending 不通知"，错在测试而非实现**：在该节前补一次 `set_pending_dispatch(age_s=1000)`（并保留清空 `notices` 的前置），单跑该用例 → `1 passed`，随后全量验收全绿。属测试作者时序错误，文件在 scope 内，已按协议在改动前执行 check-round（`.round11-tmp/check-27-test-fix1.json`）。

### 5.2 红阶段第 6 个新测试本来就是绿的

`test_no_pane_match_stays_silent` 在红运行时通过——它断言的是"零命中必须静默"，这条契约在 round-10 已实现，红阶段失败的另外 5 个才覆盖本轮新行为。绿阶段它继续通过（`.round11-tmp/green-pytest-2.txt`），作为回归护栏成立。

### 5.3 `pgrep watch-compact-continue` 的自匹配

残留检查里 `pgrep -af watch-compact-continue` 命中了执行该检查的 shell 自身（命令行文本含该关键词），并非真实残留 watcher。已在 §3.3 与附录 C.3 注明，未误杀任何进程；修订轮的残留检查改为 `grep -v "pgrep -af"` 排除自身后**无匹配**。

### 5.4 规划者复核未验收：钩子失败判据过宽 + `record_notice` 返回值语义错（修订轮的全部起因）

规划者指出两处，均属实、均已改：

1. **`run_pairctl` 把"非 0 退出码"当成失败**——但契约原文只有两类失败：pairctl 文件不存在、stdout 不是 JSON。`resume-deliver` 撞到有界锁时按设计以 **exit 2 + 精确 lock_timeout JSON** 回答，`planner_busy` 同样是 exit 2 + JSON 对象；这些是"已回答"，属 #10 静默契约，钩子必须 exit 0 且全静默。原实现会记 `pairctl_failed` 并 exit 1——**只测 `pairctl wake` 没测钩子，所以首轮全绿没盖住这条**（复核意见完全正确）。
   - 改法：判定改为"`is_file()` 存在 且 `json.loads(stdout)` 成功"；`returncode` 只进 detail 文案；`TimeoutExpired`/spawn `OSError` 归入"stdout 非 JSON"类（这两种情况本就拿不到 JSON）。
   - 顺带把 detail 措辞统一为 `pairctl stdout was not JSON (exit N)`，让日志一眼看出是哪一类失败。
2. **`record_notice` 返回 `shown`**——`rate_limited` 时通知已落账（`notices` 有记录、`shown=false`），但返回值是 `False`，`check_dispatch_stale` 于是向 `wake` 报 `dispatch_stale_notified=False`，与"新通知已记录"的事实矛盾。
   - 改法：**dedupe 命中 → `False`（没有新记录）；追加成功 → `True`（不看 `shown`）**，docstring 同步为"Returns True exactly when a NEW notice was appended"。这是本次唯一的 pairctl.py 行为改动。

修订轮严格 red-first：先落 3 个新测试（红-2 `3 failed, 148 passed`，失败者恰是这 3 个），再改 `notify.py`/`pairctl.py`/钩子注释，绿-4 `151 passed` 全绿。首轮已实现的其余部分（notices 数组、wake、锁超时、stale 通知、既有 6 测）零改动保留。

## 6. 推迟 / 跳过 / 降级

| 项 | 处置 |
|---|---|
| `status`/`wake` 之外的其他命令是否也带 `notices` | **按契约只做 `status`**（pending 路径与正常路径都带）；其他命令 payload 未加该字段，避免无谓扩张 |
| `wake --source watcher` 的 resume-deliver 与 watch 循环自行 spawn 的并发 | **降级为共用 `spawn_resume_deliver`**：交付状态机本身在 pairctl 子进程的 state 锁内认领，两路最多一路成功；watch 循环已把 `attempted` 判定前移，实际不再自行 spawn（原语句保留为死路径以维持既有分支形状） |
| 钩子失败通知的去重跨 state 目录 | **降级**：marker 落在 `HERDR_PLUGIN_STATE_DIR`（按插件实例），同插件实例全局一次；不同 `XDG_STATE_HOME` 视为不同插件实例各记一次——与日志路径同源，契约只要求"通知一次" |
| `PAIRCTL_LOCK_WAIT_S` 未设时的行为 | **保持原样**：阻塞等锁，字节级不变；只有显式设正数才启用超时 |
| commit / push / PR / issue 变更、`herdr plugin link/unlink/enable/disable` | **按契约不做**（停在工作区状态） |
| `docs/`、`herdr-plugin.toml`、`claude_session_start_hook.py`、`.handoff/`、`.round9-tmp/`、`.round10-tmp/` | **零改动** |

## 7. 假设

1. **`status` 的 pending 路径**：契约要求"pending 路径也带整个 `notices`"，实现为 `PendingDispatchError` 携带 `notices` 属性、`main()` 在 payload 非 `None` 时并入；其余仍抛该异常的命令（`send-round` 等）`notices` 为 `None`，payload 不加该字段——契约只点名 `status`。
2. **stale 判定用严格大于**：`age > threshold` 才通知（契约"超过阈值"），恰好等于阈值不通知；naive `created_at` 按 UTC 解释，与 `now()` 同为 UTC 秒级 ISO。
3. **`wake` 永不因 pending 中断**：与 `status` 相反，`wake` 用 `load()` 而非 `load_ready()`，所以 pending 存在时它照样检查 stale 并以 `wake_checked` 正常退出——契约明确 wake 不抛 `PendingDispatchError`。
4. **watcher 源独占 resume-deliver**：契约把"到期 resume-deliver"排在 watch 每轮与 `wake --source watcher` 上；`command|hook|status` 三个源只做 dispatch-stale 检查。`spawn_resume_deliver` 在释放锁后执行，避免父子进程互抢同一把非重入锁。
5. **锁超时的字节精确性**：契约给的是无换行的 `{"status":"rejected","reason":"lock_timeout"}`，故绕过 `output()`（会 `sort_keys`+`indent`+尾换行），用 `sys.stdout.write` 直写常量；`LockTimeoutError` 不是 `ValueError` 子类，确保不被 `PAIRCTL_ERROR` 分支污染 stderr。
6. **钩子的 herdr 唯一许可调用**：失败通知用 `PAIRCTL_HERDR`（测试夹具注入的假 herdr）或 PATH 上的 `herdr`，`timeout=30`、捕获输出、异常吞掉；钩子正文路径（零命中等）依旧完全不碰 herdr，`herdr agent prompt` 只可能发生在 pairctl 子进程内部（`resume-deliver` 自己的交付逻辑），钩子代码里没有该调用。
7. **marker 写在通知尝试之后**：中途崩溃时下一次事件仍会重试一次通知，代价是极端情况下可能重复一条——契约只要求"通常一次"，且 marker 一旦落盘即永久静默。
8. **`FAKE_HERDR_NOTIFY_REASON` 只作用于 `result`**：夹具在该变量存在时改写 `result.reason`，四个宿主失败原因额外把 `shown` 置 false；其余取值沿用夹具默认 `shown:true`，以覆盖"宿主给的 reason 原样落账"分支。

## 附录 A — 红测试失败原文（`slot cpu -- python3 -m pytest -q` → exit 1，逐字全文；同文存档 `.round11-tmp/red-pytest.txt`，133 行）

五个失败的归因：**全部为实现尚未落盘**——`wake` 子命令不存在（2 个）、`notices` 字段不存在（1 个）、锁超时 payload 未实现（1 个，`stdout==''`）、钩子失败路径未实现（1 个，`0 != 1`）。第 6 个新测试 `test_no_pane_match_stays_silent` 绿（§5.2），既有 143 个用例零回归。

```text
.............................................................................F....... [ 57%]
...........F.F...F...........................F................ [ 99%]
.                                                                        [100%]
=================================== FAILURES ====================================
__________ PairctlTest.test_dispatch_stale_threshold_is_configurable ___________

self = <test_pairctl.PairctlTest testMethod=test_dispatch_stale_threshold_is_configurable>

    def test_dispatch_stale_threshold_is_configurable(self) -> None:
        # PAIRCTL_DISPATCH_STALE_S shrinks the window: 500 s old counts as stale.
        self.set_pending_dispatch(age_s=500)
        self.clear_calls()
        fast = self.invoke(
            "wake", "--source", "status",
            extra_env={"PAIRCTL_DISPATCH_STALE_S": "300"},
        )
>       self.assertEqual(fast.returncode, 0, fast.stderr + fast.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'wake' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver)

skills/herdr-pair/tests/test_pairctl.py:3232: AssertionError
_____ PairctlTest.test_lock_timeout_abandons_event_and_later_wake_retries _____

self = <test_pairctl.PairctlTest testMethod=test_lock_timeout_abandons_event_and_later_wake_retries>

    def test_lock_timeout_abandons_event_and_later_wake_retries(self) -> None:
        lock = self.state / "state.lock"
        holder = subprocess.Popen(
            [
                "python3", "-c",
                "import fcntl, sys, time\n"
                "handle = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(handle, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(30)\n",
                str(lock),
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            before = (self.state / "state.json").read_text(encoding="utf-8")
            self.clear_calls()
            timed = self.invoke(
                "wake", "--source", "hook",
                extra_env={"PAIRCTL_LOCK_WAIT_S": "1"},
            )
            self.assertEqual(timed.returncode, 2, timed.stderr + timed.stdout)
>           self.assertEqual(
                timed.stdout, '{"status":"rejected","reason":"lock_timeout"}',
            )
E           AssertionError: '' != '{"status":"rejected","reason":"lock_timeout"}'
E           + {"status":"rejected","reason":"lock_timeout"}

skills/herdr-pair/tests/test_pairctl.py:3133: AssertionError
___________ PairctlTest.test_missing_pairctl_logs_and_notifies_once ____________

self = <test_pairctl.PairctlTest testMethod=test_missing_pairctl_logs_and_notifies_once>

    def test_missing_pairctl_logs_and_notifies_once(self) -> None:
        # A selected candidate whose pairctl cannot run: log pairctl_failed, show
        # the host notification exactly once (marker file), exit 1 - never silent.
        self.arm_and_advance_epoch()
        self.clear_calls()
        plugin_state = self.root / "plugin-state-fail"
        env = {
            "PAIRCTL": str(self.root / "no-such-pairctl.py"),
            "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
        }
    
        first = self.run_resume_hook(extra_env=env)
>       self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
E       AssertionError: 0 != 1 :

skills/herdr-pair/tests/test_pairctl.py:3059: AssertionError
______ PairctlTest.test_notice_recorded_when_notification_is_rate_limited ______

self = <test_pairctl.PairctlTest testMethod=test_notice_recorded_when_notification_is_rate_limited>

    def test_notice_recorded_when_notification_is_rate_limited(self) -> None:
        # Two failed prompts expire the record; the expired notification goes
        # through the notice helper, which must record the suppression itself.
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.set_herdr_mode("fail")
        self.clear_calls()
        env = {"FAKE_HERDR_NOTIFY_REASON": "rate_limited"}
    
        first = self.invoke(
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin", extra_env=env,
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(json.loads(first.stdout)["status"], "resume_uncertain")
    
        second = self.invoke(
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin", extra_env=env,
        )
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(json.loads(second.stdout)["status"], "resume_expired")
    
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair resume expired")
    
>       notices = self.read_state()["notices"]
                  ^^^^^^^^^^^^^^^^^^^^
E       KeyError: 'notices'

skills/herdr-pair/tests/test_pairctl.py:3030: KeyError
________________ PairctlTest.test_stale_dispatch_notifies_once _________________

self = <test_pairctl.PairctlTest testMethod=test_stale_dispatch_notifies_once>

    def test_stale_dispatch_notifies_once(self) -> None:
        created_at = self.set_pending_dispatch(age_s=1000)
        self.clear_calls()
    
        first = self.invoke("wake", "--source", "command")
>       self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
E       AssertionError: 2 != 0 : usage: pairctl.py [-h]
E                         {init,note-session,context-usage,note,start-round,send-round,ack-round,check-round,adopt-contract,resolve-pending,finish-round,diff-round,job-add,job-update,checkpoint,status,rollover,compact-self,watch-compact-continue,resume-deliver} ...
E       pairctl.py: error: argument subcommand: invalid choice: 'wake' (choose from init, note-session, context-usage, note, start-round, send-round, ack-round, check-round, adopt-contract, resolve-pending, finish-round, diff-round, job-add, job-update, checkpoint, status, rollover, compact-self, watch-compact-continue, resume-deliver)

skills/herdr-pair/tests/test_pairctl.py:3168: AssertionError
=========================== short test summary info ============================
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_dispatch_stale_threshold_is_configurable
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_lock_timeout_abandons_event_and_later_wake_retries
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_missing_pairctl_logs_and_notifies_once
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_notice_recorded_when_notification_is_rate_limited
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_stale_dispatch_notifies_once
5 failed, 143 passed, 69 subtests passed in 46.19s
PYTEST_EXIT=1
```

## 附录 B — pytest 退出码与原始输出

**验收运行（最终，exit 0，修订轮绿-4）** — `slot cpu -- python3 -m pytest -q`（存档 `.round11-tmp/green-pytest-4.txt`）：

```text
............................................................................. [ 56%]
.............................................................. [ 97%]
....                                                                     [100%]
151 passed, 69 subtests passed in 47.58s
PYTEST_EXIT=0
```

**修订轮绿-3（exit 0，改注释前的功能等价运行）** — 存档 `.round11-tmp/green-pytest-3.txt`：`151 passed, 69 subtests passed in 47.27s`，`PYTEST_EXIT=0`。

**报告落盘后的最终确认运行（exit 0）** — `slot cpu -- python3 -m pytest -q`（存档 `.round11-tmp/green-pytest-5.txt`，报告写完后执行，代码未再改动）：

```text
.............................................................. [ 97%]
....                                                                     [100%]
151 passed, 69 subtests passed in 53.16s
PYTEST_EXIT=0
```

**修订轮红-2（exit 1，只加 3 个新测试、实现未动）** — 存档 `.round11-tmp/red2-pytest.txt`（93 行，三个 FAILED 恰为新增的三条）：

```text
..................................................................................... [ 56%]
..........FF..............F................................... [ 97%]
....                                                                     [100%]
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_hook_silent_when_pairctl_prints_json_and_exits_nonzero
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_hook_silent_when_pairctl_reports_lock_timeout
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_rate_limited_stale_dispatch_still_reports_notified
3 failed, 148 passed, 69 subtests passed in 47.41s
PYTEST_EXIT=1
```

关键失败断言（原文）：两个钩子用例均 `AssertionError: 1 != 0`（钩子被误判成失败）；`test_rate_limited_…` 为 `AssertionError: False is not true : {'dispatch_stale_notified': False, 'notices': [{'…', 'reason': 'rate_limited', 'shown': False, …}]}`——**notice 已落账但返回值报 False**，正是 §5.4 的两处病灶。

**首轮绿-2（exit 0，被规划者复核否决的验收）** — 存档 `.round11-tmp/green-pytest-2.txt`：

```text
............................................................................. [ 57%]
.............................................................. [ 99%]
.                                                                        [100%]
148 passed, 69 subtests passed in 50.59s
PYTEST_EXIT=0
```

**首轮绿-1（exit 1，实现已落盘、§5.1 测试时序未修时）** — 全文存档 `.round11-tmp/green-pytest-1.txt`（唯一 FAILED：`test_stale_dispatch_notifies_once`，`AssertionError: 0 != 1 : []`，即 §5.1）：

```text
.............................................................................F....... [ 57%]
.............................................F................ [ 99%]
.                                                                        [100%]
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_stale_dispatch_notifies_once
1 failed, 147 passed, 69 subtests passed in 49.79s
PYTEST_EXIT=1
```

**首轮红运行（exit 1）** — 见附录 A。

## 附录 C — git 原文

### C.1 之前（`.round11-tmp/git-baseline.txt`）

```
=== git status --short (baseline, before this round writes) ===
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/reports/
=== git rev-parse HEAD ===
b2e9115fcf826257e65de975df5c578ebe4bf5b2
=== git diff --stat (baseline) ===
（空）
```

### C.2 中间轮关键输出

- 首轮红：`5 failed, 143 passed, 69 subtests passed in 46.19s`，`PYTEST_EXIT=1`
- 首轮绿-1：`1 failed, 147 passed, 69 subtests passed in 49.79s`，`PYTEST_EXIT=1`（§5.1）
- 首轮绿-2：`148 passed, 69 subtests passed in 50.59s`，`PYTEST_EXIT=0`（规划者复核否决，见 §5.4）
- 修订轮红-2：`3 failed, 148 passed, 69 subtests passed in 47.41s`，`PYTEST_EXIT=1`
- 修订轮绿-3：`151 passed, 69 subtests passed in 47.27s`，`PYTEST_EXIT=0`
- 修订轮绿-4（验收）：`151 passed, 69 subtests passed in 47.58s`，`PYTEST_EXIT=0`
- 报告落盘后最终确认（绿-5）：`151 passed, 69 subtests passed in 53.16s`，`PYTEST_EXIT=0`

### C.3 之后（`.round11-tmp/git-after.txt`，修订轮结束时重取）

```
== git status --short ==
 M skills/herdr-pair/hooks/on_planner_status.py
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round10-tmp/
?? .round11-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/hooks/notify.py
?? skills/herdr-pair/reports/

== git rev-parse HEAD ==
b2e9115fcf826257e65de975df5c578ebe4bf5b2          ← 与之前一致，未 commit

== git diff --stat ==
 skills/herdr-pair/hooks/on_planner_status.py |  54 ++--
 skills/herdr-pair/scripts/pairctl.py         | 335 ++++++++++++++++++++--
 skills/herdr-pair/tests/test_pairctl.py      | 410 ++++++++++++++++++++++++++-
 3 files changed, 743 insertions(+), 56 deletions(-)

== git diff --numstat ==
24	30	skills/herdr-pair/hooks/on_planner_status.py
311	24	skills/herdr-pair/scripts/pairctl.py
408	2	skills/herdr-pair/tests/test_pairctl.py

== leftover tests/tmp ==
zsh:1: no matches found: skills/herdr-pair/tests/tmp*
(none)

== pgrep watch-compact-continue（排除自身 shell） ==
(none)
```

## 附录 D — 协议与停止点

- 写入前 check-round 全部 `allowed=true` / `reason=ok` / `work_status=running` / `revision=1`（`.round11-tmp/` 下 **65 份** check JSON：`check-before-red-tests`、`check-before-report`、`check-01` … `check-63-final`；两轮红运行本身也过闸，`check-03-red-pytest.json`、`check-36-red-run.json`）；接受前 exit 2 验证、contract_text/path/hash/scope/acceptance/executor/revision 核对、ack accept/start 均已完成。
- Preflight：`.round11-tmp/slot-audit.log`（"没发现绕过 slot 的重进程。"）、`.round11-tmp/slot-status.log`；六次 pytest（红×2、绿×4）全部经 `slot cpu` 提交。
- 停止点遵守：**未** commit / push / 开 PR / 改 issue；**未**执行 `herdr plugin link/unlink/enable/disable`；`docs/`、`herdr-plugin.toml`、`claude_session_start_hook.py`、`.handoff/`、`.round9-tmp/`、`.round10-tmp/`、`herdr-api-080.json` 零改动。
- 修订轮遵守规划者指令：只改钩子失败判据与 `record_notice` 返回值两处（外加同步注释/docstring 与新增测试），**首轮已实现的其余部分零改动**。
- 中间文件仅在 `.round11-tmp/`（本轮 fence 内），未在 `/tmp` 下创建任何文件。
