# Round 10 报告 — p01-r002（issue #10：per-pane 状态索引 + herdr 插件清单与 resume 钩子）

## 1. 轮次标识

| 项 | 值 |
|---|---|
| round_id | `p01-r002` |
| revision | 1 |
| contract_hash | `031f6f72e5c05764fdabe9a7699e1c24a287f47c1fd5838222b80ea0096d833b` |
| contract_path | `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p01-r002.rev1.contract` |
| executor | `w2X:p1` |
| work_status（报告落盘时） | `running` |
| acceptance | `python3 -m pytest -q` → **exit 0，`142 passed, 69 subtests passed`**（附录 B） |
| check-round | 协议要求的每个写入点前均执行，全部 `allowed=true`、`reason=ok`、revision=1、executor=`w2X:p1`、hash 一致；报告写入前一次存档于 `.round10-tmp/check-before-report.json` |

协议命令一律 `python3 /home/tcuni-claw/.claude/skills/herdr-pair/scripts/pairctl.py <sub> --cwd /public/scripts/tc-skills --round-id p01-r002 --revision 1 --pane w2X:p1`（未传 `--state-dir`）。接受前 `check-round` 预期 exit 2（`not_accepted`），已验证；随后 `ack-round --action accept`、`ack-round --action start` 依次完成（scope/hash 取自 check-round 返回值原文）。被改实现是仓库内 `skills/herdr-pair/scripts/pairctl.py`，协议脚本未动。契约全文（含冲突裁定、索引/钩子/测试细则）已与 `.handoff/issue10-pane-index-r1.md` 派发内容逐字核对一致。

## 2. Preflight（slot）

按环境规则，重活前执行 `slot audit` + `slot status` 并落盘 `.round10-tmp/slot-audit.txt`（2026-09-22T12:49:05+08:00）：

- `slot audit`：**没发现绕过 slot 的重进程。**
- `slot status`：heavy.slice 内存 37.5G/76.0G 硬顶、进程 30；io/cpu/gpu/nf 四池全部为 `finished` 历史作业，无运行中任务（全文 82 行见上述文件）。
- 本轮 pytest 单次 40–56 秒、内存占用小，未走 slot（未达 >1 分钟 / >2G / 重 NFS IO 门槛）；中间文件全部在本轮 fence 内的 `.round10-tmp/`，测试临时目录由各用例自己的 `TemporaryDirectory` 承担，且 `invoke`/`popen` 把 `XDG_STATE_HOME` 钉在用例临时目录内，未写用户真实 state 目录。

## 3. 前 / 后状态

### 3.1 之前（HEAD `09911ed9afdb9c664883bd00bd8b828f701aa36e`，本轮未改前）

- 写入文件字节：`pairctl.py` 115027、`test_pairctl.py` 123698（与 round 9 报告的"之后"一致，HEAD 未动）。
- `git status --short`（原文见 `.round10-tmp/git-baseline.txt`）：

  ```
  ?? .handoff/
  ?? .round10-tmp/
  ?? .round9-tmp/
  ?? docs/specs/issue-1-herdr-pair.md
  ?? docs/tickets/
  ?? herdr-api-080.json
  ?? skills/herdr-pair/reports/
  ```

- `git diff --stat` 为空（无已跟踪文件被修改）；HEAD = `09911ed9afdb9c664883bd00bd8b828f701aa36e`。
- 行为基线：pairctl **没有**任何 per-pane 索引概念；仓库里没有 `herdr-plugin.toml`、没有 `hooks/` 目录。

### 3.2 红测试（六个契约必测 + 测试夹具隔离先落盘，实现未动）

`python3 -m pytest -q` → **exit 1**：`7 failed, 135 passed, 69 subtests passed in 39.40s`
（7 failed = 六个契约必测全红 + 1 个**既有**测试 `test_fifth_finish_spawns_continue_watcher_when_enabled` 抢跑失败 `'claimed' != 'delivered'`，后证实为基线即存在的偶发竞态，见 §5.1。）
失败原文全文见附录 A（存档 `.round10-tmp/red-pytest.txt`，245 行）。

### 3.3 之后（实现落盘后）

- 字节：`pairctl.py` 117721（+56 行）、`test_pairctl.py` 140864（+362/-1 行）、`herdr-plugin.toml` 540、`hooks/on_planner_status.py` 6797。
- 中间一轮（附录 C.2）：`2 failed, 140 passed`（exit 1）——一个是我在红阶段写错的测试夹具调用（缺 `use_state_dir=False`，见 §5.2），一个是 §5.1 记录的偶发 tearDown 竞态；两者修正后复跑。
- 验收运行 `python3 -m pytest -q` → **exit 0**：`142 passed, 69 subtests passed in 56.10s`（附录 B；142 = 基线 136 + 新增 6）。
- `git status --short` / `git rev-parse HEAD` / `git diff --stat` 原文见附录 C.3：仅 scope 内 2 个已跟踪文件被修改 + 2 个 scope 内新文件（`herdr-plugin.toml`、`hooks/`）转为未跟踪待提交；HEAD 未变（未 commit）；`docs/agents/`、`claude_session_start_hook.py`、`.handoff/`、`.round9-tmp/` 零改动。
- 验收后残留检查：`ls skills/herdr-pair/tests | grep -c '^tmp'` = **0**；`pgrep -f watch-compact-continue` = **无**（green-1 偶发 tearDown 失败遗留的 `tests/tmpe4mu5t78/` 已确认为本轮测试产物后清除）。

## 4. 改动明细

### 4.1 `skills/herdr-pair/scripts/pairctl.py`（+56/-0）

1. **新增 `pane_index_path(pane_id)`**：返回 `$XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json`（`XDG_STATE_HOME` 缺省 `~/.local/state`，与既有 `default_root` 同源）。
2. **新增 `record_pane(pane_id, state_dir, cwd, role)`**：
   - 窗格 id 为空 → 不写；
   - 读入既有 JSON 数组（缺失/损坏 → 视为空数组重建）；
   - 按 `(state_dir, role)` 去重：命中则删除旧元素、把新元素追加到数组末尾（**更新即移位，不产生重复**）；
   - 元素字段恰好 `state_dir, cwd, role, recorded_at`，`recorded_at` 用既有 `now()`（UTC、秒精度 ISO）；
   - 经既有 `atomic_text`（mkstemp + fsync + `os.replace`）落盘；写失败 `OSError` 吞掉——索引是 best-effort 缓存，`state.json` 才是唯一事实源，命令本体不因索引不可写而失败。
3. **五个写入点**（均在各自命令的 state 锁内、`persist` 之后；`state_dir` 一律取 `str(pp["root"])`，即解析后的绝对路径）：
   - `cmd_init`：`--planner-pane`，role=`planner`；
   - `cmd_note_session`：`--pane`（仅成功记录 session 的路径），role=`planner`；
   - `cmd_compact_self`：实际使用的规划者窗格（`--planner-pane` 优先，否则状态里的 `planner_pane`，此时二者已被上方赋值统一），role=`planner`；
   - `cmd_send_round`：`--target`，role=`executor`（派发成功、`round_sent` 路径上）；
   - `cmd_rollover`：状态里的 `planner_pane`，role=`planner`。
4. 超龄语义不在写侧实现（写侧只做"同键刷新"）：`recorded_at` 早于 now − `DEFAULT_STALE_HOURS`（12h，`PAIRCTL_SESSION_STALE_HOURS` 可调）视为不存在，由钩子在读侧执行（§4.3）。

### 4.2 `skills/herdr-pair/herdr-plugin.toml`（新建）

```toml
id = "tc.herdr-pair"
name = "herdr-pair resume hook"
version = "0.1.0"
min_herdr_version = "0.8.0"
platforms = ["linux", "macos"]

[[events]]
on = "pane.agent_status_changed"
command = ["python3", "hooks/on_planner_status.py"]
```

只有一个 `[[events]]`，无 `actions`/`panes`/`startup`；按契约**未**执行任何 `herdr plugin link/enable`。

### 4.3 `skills/herdr-pair/hooks/on_planner_status.py`（新建，6797 B）

- 入口：读 `HERDR_PLUGIN_EVENT`、`HERDR_PLUGIN_EVENT_JSON`；事件名非 `pane.agent_status_changed` → 静默 exit 0。
- JSON 认 `pane_id`（缺 → 静默）、`workspace_id`（忽略）、`agent_status`（非 `idle`/`done` → 静默）。
- 候选筛选（全部满足才入选）：索引里 `role == "planner"`（executor 命中 → 静默 exit 0，属 #12）→ `recorded_at` 未超龄（`PAIRCTL_SESSION_STALE_HOURS`，默认 12h；不可解析也视为过期）→ 载入 `state_dir/state.json` → `resume_pending` 存在且 `planner_pane == pane_id` → `status ∈ {pending, uncertain}`（`delivered/cancelled/expired/claimed` 不选）→ `compaction_epoch > epoch_at_arm`。
- 选取与歧义：候选按解析后的 `recorded_at` 取最新；**最新时间戳若被两个不同 `state_dir` 共享 → 向插件日志追加一行含 `ambiguous_pane` 的文字，不调用 `resume-deliver`，exit 0**；唯一最新者胜出（与契约"过期丢掉、余者按 recorded_at 取最近、仍剩两个不同 state_dir 则放弃"一致）。
- 交付：命中后**恰好一次**执行
  `python3 <pairctl> resume-deliver --pane <pane_id> --via plugin --cwd <索引 cwd> --state-dir <索引 state_dir>`，
  其中 `<pairctl>` = 环境变量 `PAIRCTL`，否则钩子文件旁的 `../scripts/pairctl.py`；子进程输出全部捕获丢弃（钩子 stdout/stderr 保持为空），子进程失败也 exit 0。
- 钩子**不** import pairctl、**不**写 `state.json`、**不**调用 herdr（子进程只有 pairctl 一条命令）。
- 日志：`HERDR_PLUGIN_STATE_DIR/hook.log`，否则 `$XDG_STATE_HOME/herdr-pair/plugin-hook.log`；仅歧义时写，日志失败不改出口。
- 零命中 / 过期 / 任一核对失败 / JSON 解析失败 → exit 0，stdout 与 stderr 都空。

### 4.4 `skills/herdr-pair/tests/test_pairctl.py`（+362/-1）

1. **夹具隔离**：`setUp` 建 `self.state_home = root/"xdg-state"`；`invoke()`/`popen()` 设 `XDG_STATE_HOME` 指向它（契约要求：测试不得写用户真实 state 目录）；`import tomllib`、常量 `PLUGIN_HOOK`。
2. **辅助件**：`pane_index_path`/`read_pane_index`/`write_pane_index`（直接操纵索引制造过期、歧义、错角色场景）、`fresh_stamp`（UTC 秒级 ISO）、`write_pairctl_stub`（记录 `resume-deliver` 精确 argv 后 `os.execv` 真 pairctl，端到端走完交付）、`stub_calls`、`run_resume_hook`（注入 `HERDR_PLUGIN_EVENT(_JSON)`、`PAIRCTL`、`HERDR_PLUGIN_STATE_DIR` 等，`subprocess.run` 捕获输出）。
3. **六个契约必测**（名称逐字）：
   - `test_pane_index_five_write_points_include_custom_state_dir`：五个写入点、字段集合、同键刷新移位、自定义 `--state-dir` 独立成条、空 id 不写。
   - `test_pane_index_entry_expires_after_stale_hours`：13h 过期静默；`PAIRCTL_SESSION_STALE_HOURS=1` 下 3h 也过期；`note-session` 重新写入后同一条恢复可交付（断言 stub argv 与 `delivered`）。
   - `test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch`：恰好一次、argv 逐字匹配 `["resume-deliver","--pane","w1:p1","--via","plugin","--cwd",…,"--state-dir",…]`、恰好一条 auto-continue prompt、`delivered`；下一事件边沿不再调用。
   - `test_resume_hook_silent_on_miss_and_mismatch`：零命中 / 错事件名 / busy 状态 / 无记录 / epoch 未前进 / planner_pane 不匹配 / executor 角色 —— 全部 exit 0 且 stdout、stderr 为空、stub 零调用、状态未被认领、日志文件不产生。
   - `test_resume_hook_ambiguous_pane_logs_and_does_not_deliver`：同 `recorded_at` 双 `state_dir` → 不交付、日志含 `ambiguous_pane`、状态保持 `pending`；`recorded_at` 不同 → 最新者唯一胜出、按其 `state_dir/cwd` 交付。
   - `test_plugin_manifest_declares_resume_hook_only`：id/name/version/`min_herdr_version`/`platforms`、唯一事件、command 恰好一个部件指向钩子且解析到真实文件、无 `actions`/`panes`/`startup`。
4. **附带修正（非契约测试）**：`test_fifth_finish_spawns_continue_watcher_when_enabled` 的既有竞态断言改为带截止的轮询（§5.1）。

## 5. 红→绿期间发现并处理的问题（如实披露）

### 5.1 既有偶发失败：`test_fifth_finish_spawns_continue_watcher_when_enabled`（`'claimed' != 'delivered'`）

- 机理：`cmd_resume_deliver` 的顺序是 **认领（写 `claimed`）→ herdr prompt → 写 `delivered`**；该测试看到 prompt 落账后立即读状态，恰落在 prompt 与 `delivered` 两次写盘之间。
- 归因实验：`git stash` 掉本轮测试改动、在**基线文件**上单跑该用例 5 次 → **4 过 1 挂**，证明竞态基线即存在（带本轮夹具改动单跑 3 次 → 1 过 2 挂，样本小，且同仓的 `test_watcher_delivers_once_at_deadline_and_records_mechanism` 本就用 `sleep(0.3)` 规避同一竞态）。
- 处置：把该断言改为最多 5 秒、轮询至 `delivered` 的写法（断言强度不变：最终必须 `delivered`），与同仓既有做法一致。此为验收必须全绿下的最小测试侧修复，文件在 scope 内。

### 5.2 我的红测试自身的夹具错误

`invoke()` 默认在参数末尾追加 `--state-dir self.state`，argparse 取最后一个 → 红阶段两处 `init --state-dir <自定义>` 实际被覆盖回默认状态目录，绿阶段表现为"自定义目录没成为第二条"。按仓内既有惯例改用 `use_state_dir=False`（该参数本就是为此存在，仓内已有 16 处同样用法）。属测试作者错误，非实现问题，已修正并复跑全绿。

### 5.3 一次未复现的 tearDown 竞态（中间轮）

中间轮 `test_fresh_unknown_kind_needs_override_and_heuristic_verifies` 在 tearDown 清理时报 `OSError: [Errno 39] Directory not empty: .../state/contracts`（正文断言全过）。`contracts` 唯一写者是同步的 `save_contract`，该测试内 pairctl 全部经 `invoke` 同步等待，怀疑为与既有 watcher/清理交错的偶发竞态。**最终验收轮未复现**，全绿；其遗留临时目录 `tests/tmpe4mu5t78/` 已确认为本轮测试产物后删除。未对相关既有代码做防御性改动（无稳定复现，避免盲改），如实记录在此。

## 6. 推迟 / 跳过 / 降级

| 项 | 处置 |
|---|---|
| 执行者窗格的 resume 命中（`role=executor`）如何行动 | **按契约跳过**：本轮 executor 命中一律静默 exit 0，属 issue #12 |
| `herdr plugin link/enable/disable/unlink` | **按契约不执行**（清单仅落盘，未接入本机插件注册表） |
| 索引跨 state_dir 并发写的锁 | **降级**：单文件内原子替换（`atomic_text`）保证不损坏；两个进程同时写同一窗格文件时可能丢失一次"刷新"（保留旧时间戳），不影响正确性——最坏退化为钩子少触发一次，下个写入点即恢复 |
| §5.3 tearDown 偶发 | **推迟观察**：未复现、无稳定根因，未改；若再次出现应单独开票 |
| commit / push / PR / issue 变更 | **按契约不做**（停在工作区状态） |

## 7. 假设

1. **歧义判定**：契约"按 recorded_at 取最近一条；若这样仍有两个不同 state_dir"实现为——候选中取最新 `recorded_at`，若该最新时间戳被 ≥2 个不同 `state_dir` 共享（并列无法排序）→ 歧义；存在唯一最新 → 该条胜出。此解读同时满足契约两句原文与自带测试（同戳 → 歧义；异戳 → 新者胜）。
2. 时间比较用解析后的 `datetime`（naive 视作 UTC），不依赖字符串字典序；`now()` 与测试 `fresh_stamp` 同为 UTC 秒级 ISO。
3. 清单 `command` 用相对清单目录的 `hooks/on_planner_status.py`；钩子对工作目录无依赖（pairctl 路径按钩子文件自身位置或 `PAIRCTL` 解析，索引/日志按环境变量解析）。
4. 钩子对 `resume-deliver` 的结果不做区分：任何子进程结局（含非 0、异常）钩子都 exit 0 且无输出——契约的静默条款优先于向上冒泡。
5. `send-round` 仅在派发成功（`round_sent`）路径写 executor 索引条目；失败/uncertain 派发不写（契约未要求，避免把未真正到达的窗格记成 executor）。
6. `note-session` 仅在成功记录 session 的路径写索引（`session_ignored` 早退不写）。
7. 超龄窗口读侧生效（钩子筛选时丢弃），写侧不做物理删除——`note-session` 等同键重写即可让条目"复活"，与契约例示一致。
8. 索引文件权限沿用 `atomic_text` 的私有模式（目录 0700、文件 0600），与 state 目录一致。

## 附录 A — 红测试失败原文（`python3 -m pytest -q` → exit 1，逐字全文；同文存档 `.round10-tmp/red-pytest.txt`，245 行）

七个失败的归因：6 个 = 实现尚未落盘（索引/钩子/清单缺失）；1 个 = §5.1 记录的既有竞态（`test_fifth_finish_spawns_continue_watcher_when_enabled`）。

```text
..................................................................................F.. [ 59%]
.............FF..F.......FFF.............................      [100%]
=================================== FAILURES ===================================
______ PairctlTest.test_fifth_finish_spawns_continue_watcher_when_enabled ______

self = <test_pairctl.PairctlTest testMethod=test_fifth_finish_spawns_continue_watcher_when_enabled>

    def test_fifth_finish_spawns_continue_watcher_when_enabled(self) -> None:
        self.set_kind("cursor")
        for n in range(1, 5):
            self.start_finish(n)
        started = self.invoke_ok(
            "start-round", "--file", str(self.write_handoff("fifth.md", "# Fifth\nFull contract\n")),
            "--executor", "w1:p6", "--scope", "s", "--acceptance", "a",
        )
        self.clear_calls()
        fifth = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
            extra_env={
                "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
                "PAIRCTL_CONTINUE_POLL_S": "0.05",
                "PAIRCTL_RESUME_DEADLINE_S": "0",
            },
        )
        self.assertEqual(fifth.returncode, 20)
        payload = json.loads(fifth.stdout)
        self.assertTrue(payload["planner_compact"]["queued"])
        self.assertTrue(payload["continue_after_compact"]["spawned"], payload)
        # The record is armed but the epoch has not advanced: no prompt before rollover.
        early_until = time.time() + 0.5
        while time.time() < early_until:
            self.assertEqual(self.auto_continue_prompts(), [], self.herdr_calls())
            time.sleep(0.05)
        self.invoke_ok("rollover", "--reason", "compact")
        deadline = time.time() + 5
        continue_prompts = []
        while time.time() < deadline:
            continue_prompts = self.auto_continue_prompts()
            if continue_prompts:
                break
            time.sleep(0.05)
        self.assertEqual(len(continue_prompts), 1, self.herdr_calls())
>       self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")
E       AssertionError: 'claimed' != 'delivered'
E       - claimed
E       + delivered

skills/herdr-pair/tests/test_pairctl.py:927: AssertionError
_________ PairctlTest.test_pane_index_entry_expires_after_stale_hours __________

self = <test_pairctl.PairctlTest testMethod=test_pane_index_entry_expires_after_stale_hours>

    def test_pane_index_entry_expires_after_stale_hours(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        stub = self.write_pairctl_stub()
    
        # Age the planner entry past DEFAULT_STALE_HOURS (12 h): treated as absent.
>       entries = self.read_pane_index("w1:p1")
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

skills/herdr-pair/tests/test_pairctl.py:2763: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
skills/herdr-pair/tests/test_pairctl.py:2624: in read_pane_index
    return json.loads(self.pane_index_path(pane_id).read_text(encoding="utf-8"))
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/pathlib/_local.py:546: in read_text
    return PathBase.read_text(self, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/pathlib/_abc.py:632: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/public/scripts/tc-skills/skills/herdr-pair/tests/tmp6d90memh/xdg-state/herdr-pair/panes/w1:p1.json')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

    def open(self, mode='r', buffering=-1, encoding=None,
             errors=None, newline=None):
        """
        Open the file pointed to by this path and return a file object, as
        the built-in open() function does.
        """
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
>       return io.open(self, mode, buffering, encoding, errors, newline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       FileNotFoundError: [Errno 2] No such file or directory: '/public/scripts/tc-skills/skills/herdr-pair/tests/tmp6d90memh/xdg-state/herdr-pair/panes/w1:p1.json'

/project/software/miniforge3/lib/python3.13/pathlib/_local.py:537: FileNotFoundError
____ PairctlTest.test_pane_index_five_write_points_include_custom_state_dir ____

self = <test_pairctl.PairctlTest testMethod=test_pane_index_five_write_points_include_custom_state_dir>

    def test_pane_index_five_write_points_include_custom_state_dir(self) -> None:
        # setUp already ran `init --planner-pane w1:p1 --state-dir self.state`.
>       entries = self.read_pane_index("w1:p1")
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

skills/herdr-pair/tests/test_pairctl.py:2689: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
skills/herdr-pair/tests/test_pairctl.py:2624: in read_pane_index
    return json.loads(self.pane_index_path(pane_id).read_text(encoding="utf-8"))
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/pathlib/_local.py:546: in read_text
    return PathBase.read_text(self, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/pathlib/_abc.py:632: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/public/scripts/tc-skills/skills/herdr-pair/tests/tmpqk8wbvzw/xdg-state/herdr-pair/panes/w1:p1.json')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

    def open(self, mode='r', buffering=-1, encoding=None,
             errors=None, newline=None):
        """
        Open the file pointed to by this path and return a file object, as
        the built-in open() function does.
        """
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
>       return io.open(self, mode, buffering, encoding, errors, newline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       FileNotFoundError: [Errno 2] No such file or directory: '/public/scripts/tc-skills/skills/herdr-pair/tests/tmpqk8wbvzw/xdg-state/herdr-pair/panes/w1:p1.json'

/project/software/miniforge3/lib/python3.13/pathlib/_local.py:537: FileNotFoundError
__________ PairctlTest.test_plugin_manifest_declares_resume_hook_only __________

self = <test_pairctl.PairctlTest testMethod=test_plugin_manifest_declares_resume_hook_only>

    def test_plugin_manifest_declares_resume_hook_only(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "herdr-plugin.toml"
>       manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

skills/herdr-pair/tests/test_pairctl.py:2932: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/project/software/miniforge3/lib/python3.13/pathlib/_local.py:546: in read_text
    return PathBase.read_text(self, encoding, errors, newline)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/project/software/miniforge3/lib/python3.13/pathlib/_abc.py:632: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/public/scripts/tc-skills/skills/herdr-pair/herdr-plugin.toml')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

    def open(self, mode='r', buffering=-1, encoding=None,
             errors=None, newline=None):
        """
        Open the file pointed to by this path and return a file object, as
        the built-in open() function does.
        """
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
>       return io.open(self, mode, buffering, encoding, errors, newline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       FileNotFoundError: [Errno 2] No such file or directory: '/public/scripts/tc-skills/skills/herdr-pair/herdr-plugin.toml'

/project/software/miniforge3/lib/python3.13/pathlib/_local.py:537: FileNotFoundError
____ PairctlTest.test_resume_hook_ambiguous_pane_logs_and_does_not_deliver _____

self = <test_pairctl.PairctlTest testMethod=test_resume_hook_ambiguous_pane_logs_and_does_not_deliver>

    def test_resume_hook_ambiguous_pane_logs_and_does_not_deliver(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-amb"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}
    
        other = self.root / "other-state"
        other.mkdir(parents=True, exist_ok=True)
        (other / "state.json").write_text(json.dumps({
            "resume_pending": {
                "planner_pane": "w1:p1", "status": "pending", "epoch_at_arm": 0,
            },
            "compaction_epoch": 1,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    
        shared = self.fresh_stamp()
        self.write_pane_index("w1:p1", [
            {"state_dir": str(self.state.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": shared},
            {"state_dir": str(other.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": shared},
        ])
        hook = self.run_resume_hook(extra_env=env)
>       self.assertEqual(hook.returncode, 0, hook.stderr)
E       AssertionError: 2 != 0 : python3: can't open file '/public/scripts/tc-skills/skills/herdr-pair/hooks/on_planner_status.py': [Errno 2] No such file or directory

skills/herdr-pair/tests/test_pairctl.py:2902: AssertionError
_ PairctlTest.test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch _

self = <test_pairctl.PairctlTest testMethod=test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch>

    def test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.clear_calls()
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}
    
        hook = self.run_resume_hook(extra_env=env)
>       self.assertEqual(hook.returncode, 0, hook.stderr)
E       AssertionError: 2 != 0 : python3: can't open file '/public/scripts/tc-skills/skills/herdr-pair/hooks/on_planner_status.py': [Errno 2] No such file or directory

skills/herdr-pair/tests/test_pairctl.py:2804: AssertionError
___________ PairctlTest.test_resume_hook_silent_on_miss_and_mismatch ___________

self = <test_pairctl.PairctlTest testMethod=test_resume_hook_silent_on_miss_and_mismatch>

    def test_resume_hook_silent_on_miss_and_mismatch(self) -> None:
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-silent"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}
    
        def assert_silent(hook: subprocess.CompletedProcess[str], why: str) -> None:
            self.assertEqual(hook.returncode, 0, f"{why}: {hook.stderr}")
            self.assertEqual(hook.stdout, "", why)
            self.assertEqual(hook.stderr, "", why)
            self.assertEqual(self.stub_calls(), [], why)
    
        # zero hit: this pane was never recorded
>       assert_silent(self.run_resume_hook(pane_id="wZ:zz", extra_env=env), "zero hit")

skills/herdr-pair/tests/test_pairctl.py:2839: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
skills/herdr-pair/tests/test_pairctl.py:2833: in assert_silent
    self.assertEqual(hook.returncode, 0, f"{why}: {hook.stderr}")
E   AssertionError: 2 != 0 : zero hit: python3: can't open file '/public/scripts/tc-skills/skills/herdr-pair/hooks/on_planner_status.py': [Errno 2] No such file or directory
=========================== short test summary info ============================
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_fifth_finish_spawns_continue_watcher_when_enabled
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_pane_index_entry_expires_after_stale_hours
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_pane_index_five_write_points_include_custom_state_dir
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_plugin_manifest_declares_resume_hook_only
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_resume_hook_ambiguous_pane_logs_and_does_not_deliver
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_resume_hook_silent_on_miss_and_mismatch
7 failed, 135 passed, 69 subtests passed in 39.40s
PYTEST_EXIT=1
```

## 附录 B — pytest 退出码与原始输出

**验收运行（最终，exit 0）** — `python3 -m pytest -q`（存档 `.round10-tmp/green-pytest-2.txt`）：

```text
..................................................................................... [ 59%]
.........................................................      [100%]
142 passed, 69 subtests passed in 56.10s
PYTEST_EXIT=0
```

**中间运行（exit 1，实现已落盘、§5.2/§5.3 两个测试问题未修时）** — 尾部原文如下，全文存档 `.round10-tmp/green-pytest-1.txt`（两个 FAILED：`test_fresh_unknown_kind_needs_override_and_heuristic_verifies`，tearDown `Directory not empty: state/contracts`，§5.3；`test_pane_index_five_write_points_include_custom_state_dir`，缺 `use_state_dir=False`，§5.2）：

```text
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_fresh_unknown_kind_needs_override_and_heuristic_verifies
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_pane_index_five_write_points_include_custom_state_dir
2 failed, 140 passed, 69 subtests passed in 41.63s
PYTEST_EXIT=1
```

**红运行（exit 1）** — 见附录 A。

## 附录 C — git 原文

### C.1 之前（`.round10-tmp/git-baseline.txt`）

```
# git status --short (baseline, before this round's writes)
?? .handoff/
?? .round10-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/reports/
# git rev-parse HEAD
09911ed9afdb9c664883bd00bd8b828f701aa36e
# git diff --stat (baseline)
（空）
```

### C.2 中间轮关键输出

- 红：`7 failed, 135 passed, 69 subtests passed in 39.40s`，`PYTEST_EXIT=1`
- 绿-1：`2 failed, 140 passed, 69 subtests passed in 41.63s`，`PYTEST_EXIT=1`

### C.3 之后（`.round10-tmp/git-after.txt`）

```
=== git status --short (after) ===
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round10-tmp/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/herdr-plugin.toml
?? skills/herdr-pair/hooks/
?? skills/herdr-pair/reports/
?? skills/herdr-pair/tests/tmpe4mu5t78/          ← 中间轮 tearDown 竞态遗留，已核实为本轮测试产物并删除
                                                  （删除后复查 git status 已无此行）
=== git rev-parse HEAD ===
09911ed9afdb9c664883bd00bd8b828f701aa36e         ← 与之前一致，未 commit
=== git diff --stat ===
 skills/herdr-pair/scripts/pairctl.py    |  56 +++++
 skills/herdr-pair/tests/test_pairctl.py | 363 +++++++++++++++++++++++++++++++-
 2 files changed, 418 insertions(+), 1 deletion(-)
```

（`git diff --numstat`：`56 0 pairctl.py`、`362 1 test_pairctl.py`。）

## 附录 D — 协议与停止点

- 写入前 check-round 全部 `allowed=true`（最近一次存档 `.round10-tmp/check-before-report.json`）；接受前 exit 2 验证、contract_text/path/hash/scope/acceptance/executor/revision 核对、ack accept/start 均已完成。
- 停止点遵守：**未** commit / push / 开 PR / 改 issue；**未**执行 `herdr plugin link/unlink/enable/disable`；`docs/agents/`、`claude_session_start_hook.py`、`.handoff/`、`.round9-tmp/` 零改动；`herdr-api-080.json`、`docs/tickets/work-issue-7/issue-7-revised.md` 只读未动。
