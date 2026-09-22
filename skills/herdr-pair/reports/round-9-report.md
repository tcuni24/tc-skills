# Round 9 报告 — p01-r001（issue #9：resume_pending 的 mechanism 判定 + 记录驱动低频 watcher）

## 1. 轮次标识

| 项 | 值 |
|---|---|
| round_id | `p01-r001` |
| revision | 1 |
| contract_hash | `53bff438a50f0556b4343ad951fdb64b284a2452bfcb322859a92ec99985b4a4` |
| contract_path | `/home/tcuni-claw/.local/state/herdr-pair/f279893fb325ba82b31d/contracts/p01-r001.rev1.contract` |
| executor | `w2X:p1` |
| work_status（报告落盘时） | `running` |
| acceptance | `python3 -m pytest -q` → **exit 0，136 passed**（附录 B） |
| check-round | 共 4 次（基线、红测试前、实现前、报告前），全部 `allowed=true`、`reason=ok`、hash 一致；`check-before-red-tests.json`、`check-before-report.json` 存于 `.round9-tmp/` |

协议命令一律 `python3 /home/tcuni-claw/.claude/skills/herdr-pair/scripts/pairctl.py <sub> --cwd /public/scripts/tc-skills`（未传 `--state-dir`）；ack-round accept/start 已在基线阶段完成（见上一轮对话记录）。被改实现是仓库内 `skills/herdr-pair/scripts/pairctl.py`，协议脚本未动。

## 2. 前 / 后状态

### 2.1 之前（HEAD `77ee9281b54c23911edf39abfb1e8a5afac82f30`，本轮未改前）

- 写入文件字节：`pairctl.py` 109758、`test_pairctl.py` 110541、`test_context_edges.py` 9292。
- 未跟踪（**本轮之前已存在**，非本轮产物，内容未被本轮触碰）：`.handoff/`、`.round9-tmp/`、`docs/specs/issue-1-herdr-pair.md`、`docs/tickets/`、`herdr-api-080.json`。
- 行为基线（本轮要移除的旧实现）：
  1. `arm_resume_record` 硬编码 `mechanism="watcher"`，且每次整条替换记录（无同 epoch requeue 概念）；
  2. `PAIRCTL_CONTINUE_AFTER_COMPACT=0` 时**仍写** `resume_pending`（旧语义：flag 只影响派生）；
  3. `spawn_compact_continue_watcher` 给子进程写 `PAIRCTL_CONTINUE_AFTER_COMPACT=0`（用户开关被复用为防递归），无 `PAIRCTL_INTERNAL_WATCHER`；
  4. `compact-self` 从不派生 watcher；
  5. `watch-compact-continue` 是 min-delay / idle / timeout 轮询（`PAIRCTL_CONTINUE_MIN_DELAY_S`/`_IDLE_S`/`_TIMEOUT_S`），与记录、deadline 无关，也不经 `resume-deliver` 认领。

### 2.2 红测试（新测试 + 按新语义适配的既有测试先落盘，实现未动）

`python3 -m pytest -q` → **exit 1**：`9 failed, 128 passed, 68 subtests passed in 81.93s`。
（9 failed = 8 个用例失败 + 1 个失败子测试 `SUBFAILED`；`test_mechanism_plugin_only_when_linked_enabled_and_claude` 以子测试形式在第 1 个 case 即红。测试项总数 136 = 8 failed + 128 passed。）
六个契约必测全部为红。失败原文全文见 **附录 A**（文件 `.round9-tmp/red-pytest.txt`，16723 B）。

### 2.3 之后（实现落盘后）

- 字节：`pairctl.py` 115027、`test_pairctl.py` 123698、`test_context_edges.py` 9713。
- `python3 -m pytest -q`（经 `slot cpu`）→ **exit 0**：`136 passed, 69 subtests passed in 39.11s`（附录 B）。
- `git status --short` / `git rev-parse HEAD` / `git diff --stat` 原文见附录 C；仅 scope 内 3 个文件被修改，HEAD 未变（未 commit），`docs/`、`claude_session_start_hook.py`、`.handoff/` 零改动。
- 验收后 `pgrep -af watch-compact-continue` 只匹配到执行 pgrep 的 shell 自身；`ls skills/herdr-pair/tests | grep -c '^tmp'` = 0（无残留临时目录/进程）。

## 3. 改动明细

### 3.1 `skills/herdr-pair/scripts/pairctl.py`

1. **新增 `probe_resume_mechanism(args, kind)`**：调 `herdr plugin list --json`；信封判定 = `result.plugins` 为数组 且 `result.type == "plugin_list"`，条目认 `plugin_id == "tc.herdr-pair"` 且 `enabled` 为 JSON `true`（字符串形式会做 JSON 解析）；同时要求 planner 的 `agent get` 结果为 `claude`（见假设 6.1）。任何失败——命令非 0、`error` 信封、缺 `plugins`、非数组、未启用、非 claude——一律 `watcher`，**压缩排队不受影响**（探测整体 best-effort，永不 raise 到排队路径）。
2. **`queue_planner_compact`**：
   - 在 compact 提示词 `run_herdr` **之前**计算 `auto_continue = continue_after_compact_enabled()`、同 epoch requeue 判定与探测，保证探测不改变“最后一次 herdr 调用是 /compact 提示”的既有外部观测（`herdr_log()["argv"][4]` 断言不受影响）；
   - `auto_continue=False`（含测试夹具默认 `=0`）时**不写** `resume_pending`，compact 照常排队；
   - 同 epoch requeue 不重新探测，沿用旧 `mechanism`；
   - 返回值新增 `resume_armed`（是否发生了新记录写入），供 spawn 门控。
3. **`arm_resume_record(..., mechanism)`**：
   - 同 `epoch_at_arm` 再入队：只把 `deadline` 推到 `now + PAIRCTL_RESUME_DEADLINE_S`（`env_float`，默认 `DEFAULT_RESUME_DEADLINE_S = 600.0`，变量名不变），`armed_at`/`mechanism`/`status`/`attempts`/`last_error` 全部保持；
   - epoch 不同或无记录：整条替换为新 pending 记录，`mechanism` 取探测值（非法值兜底 `watcher`）。
4. **`spawn_compact_continue_watcher`**：
   - 首条门控：父进程 `PAIRCTL_INTERNAL_WATCHER=1` → 直接不派生（`reason: internal watcher parent`）；
   - 其后既有门控：flag 关闭 → `disabled`；compact 未排队 → `compact not queued`；
   - 新增门控：`planner_compact.resume_armed` 为假（requeue / 未写记录）→ 不派生；
   - 存活 pid → 沿用原逻辑返回已有 pid，不派生第二个；
   - 子进程 env = 父进程拷贝 + `PAIRCTL_INTERNAL_WATCHER=1`，**删除** `PAIRCTL_CONTINUE_AFTER_COMPACT="0"` 覆写。
5. **`cmd_watch_compact_continue` 重写**为读记录的低频循环：
   - `PAIRCTL_CONTINUE_POLL_S` 默认 **15**，下限 0.05；**不再读取** `PAIRCTL_CONTINUE_MIN_DELAY_S` / `PAIRCTL_CONTINUE_IDLE_S` / `PAIRCTL_CONTINUE_TIMEOUT_S`（全仓 grep 确认实现中无残留）；
   - 每轮在锁内重读 `resume_pending`：`delivered`/`cancelled`/`expired` → 退出（exit 0，`continue_skipped`/`record_<status>`）；`pending`/`uncertain` 且当前时间 ≥ `deadline` → 以子进程执行一次 `resume-deliver --pane <记录里的 planner_pane> --via watcher`（透传 `--cwd`/`--state-dir`/`--herdr`）；未到 deadline → 睡一轮；`claimed` → 只等待；
   - 子进程返回 `resume_delivered` → 输出 `{"status": "continue_prompted", "pane": ...}`，exit 0；`resume_expired` → `continue_skipped`，exit 0；`epoch_not_advanced`/`planner_busy`/`uncertain`/响应不可解析 → 睡一轮重试，**不计失败次数**；无记录 → `continue_skipped/no_record`，exit 0（契约未规定该情形，见 5.降级说明）；
   - prompt 文本只来自既有 `compact_continue_prompt`，函数未改动。
6. **`cmd_compact_self`**：锁外调用既有 spawn（结果并入输出），输出新增 `continue_after_compact` 键。
7. `init` / `send-round` 预算压缩 / `finish-round` / `emit_rollover_block` 的既有 spawn 调用点**未改签名**，由第 4 点的新门控自动生效（“新写入记录 + flag 开启 + 非 INTERNAL 父进程”三者同时满足才派生）。

### 3.2 `skills/herdr-pair/tests/test_pairctl.py`

- **FAKE_HERDR**：新增 `plugin list` 且带 `--json` 分支（放在 `notification show` 之后、mode 分支之前，不会落入 agent prompt 成功分支）：读 `<exe路径>.plugins.json`；文件缺失 → `{"result": {"plugins": [], "type": "plugin_list"}}` exit 0；内容是 plugins 数组，原样装入信封；内容非法 JSON → `error` 信封 exit 1（覆盖“列表命令非 0”）。
- **夹具**：`invoke()` 默认 `PAIRCTL_CONTINUE_AFTER_COMPACT=0` **保持不变**；新增 `popen()`（与 invoke 同环境，供并发重叠测试）；`start_finish(n, extra_env=None)` 透传 env；新增 `RECORD_ENV = {PAIRCTL_CONTINUE_AFTER_COMPACT: 1, PAIRCTL_INTERNAL_WATCHER: 1}`（记录需要、手动 watch/resume-deliver 需要成为唯一唤醒源的用例用它抑制派生）与 `auto_continue_prompts()`。
- **tearDown**：读 `compact-continue.pid`，校验 `/proc/<pid>/cmdline` 含 `watch-compact-continue` 后发 `SIGTERM` 并轮询确认退出（上限 5s），再清理临时目录——任何用例拉起的 watcher 都在此回收。
- **六个契约必测（先红后绿）**：
  - `test_mechanism_plugin_only_when_linked_enabled_and_claude`：6 个子 case（claude+enabled→plugin；disabled→watcher；他人插件→watcher；文件缺失→watcher；坏 JSON 非 0→watcher；kind=pi+enabled→watcher），每步 rollover 推进纪元以强制“新写入”。
  - `test_continue_disabled_skips_record_and_watcher`：flag=0 时排队成功、无 `resume_pending`、无 pid 文件、`continue_after_compact.reason == "disabled"`、全程无 `plugin list` 调用。
  - `test_same_epoch_requeue_updates_deadline_only`：armed_at/mechanism/status/attempts/last_error 全等，deadline 改为 ≈ now+30（`requeue_at+28 ≤ deadline ≤ now+31`），且不派生、无 pid 文件。
  - `test_watcher_delivers_once_at_deadline_and_records_mechanism`：`compact-self`（flag=1、`DEADLINE_S=0`）真实派生；纪元前进前 0.5s 内零 auto-continue prompt；`rollover --reason compact` 后 5s 内恰好 1 条，再等 0.3s 仍恰好 1 条；记录 `delivered`、`mechanism=watcher`；不手动调 watch。
  - `test_concurrent_resume_deliver_single_prompt`：纪元前进后两个 `resume-deliver` 进程（watcher/plugin 两个 via）重叠，恰好 1 个 exit 0 `resume_delivered`、1 个 exit 2 `rejected/not_claimable`，假 herdr 恰好 1 条 agent prompt，记录 `delivered`。
  - `test_watcher_child_env_uses_internal_marker`：读子进程 `/proc/<pid>/environ`（带 exec 竞态重试，上限 5s）：含 `PAIRCTL_INTERNAL_WATCHER=1`，不含 `PAIRCTL_CONTINUE_AFTER_COMPACT=0`。
- **按新语义适配的既有用例（外部断言保留，前置条件化）**：
  - `test_watch_compact_continue_prompts_planner_after_idle`：用 `RECORD_ENV + DEADLINE_S=0` 走 5 轮（记录落盘、不派生），先 `rollover --reason compact` 再 watch；`continue_prompted`、pane、4 条 prompt 文本断言原样保留。
  - `test_fifth_finish_spawns_continue_watcher_when_enabled`：真实派生（不带 INTERNAL）；派生后纪元前进前 0.5s 内断言零 prompt；`rollover --reason compact`（deadline=0 已到）后 5s 内恰好 1 条，并追加断言记录 `delivered`；去掉已废弃的 MIN_DELAY/IDLE 环境变量。
  - `test_resume_prompt_is_identical_to_legacy_watcher`：`DEADLINE_S=0` + 先 rollover 再手动 watch（watch 经 resume-deliver 认领，记录变 `delivered`）；把记录改回 `pending` 后手动 `resume-deliver`，两条 prompt 文本逐字相等的断言保留，且追加断言 watch 路径确实走到了 `delivered`。
  - `test_active_state_and_goal_are_durable_before_compact_request`（`test_context_edges.py`）：`send-round` 带 `CONTINUE=1/INTERNAL=1/DEADLINE_S=0`；先 `rollover --reason compact`（保留 `phase_advanced=False` 与 `active_rounds` 断言）再 watch；prompt 含 `compact_queued`、报告路径、`--state-dir <state>`、`--cwd <cwd>` 的四个断言原样保留；不再依赖任何 600s 睡眠。
  - 记录敏感的既有用例显式补 `RECORD_ENV`（契约要求“=1 才有记录”）：`arm_and_advance_epoch`、full-record/requeue 用例、`rollover_with_active_round`、`rollover_after_fifth_finish`、`resume_deliver_rejections`、`adopt_contract_cancels`。**没有任何用例被删除，也没有把“flag=0 也写记录”改回来。**

## 4. 命令 / 退出码 / 原始输出（索引）

| # | 命令 | 退出码 | 原始输出位置 |
|---|---|---|---|
| 1 | `slot audit`（红测试前、实现后各一次） | 0 | `.round9-tmp/.round9-slot-audit.log`（附录 D） |
| 2 | `slot status`（同上两次） | 0 | `.round9-tmp/.round9-slot-status.log`（附录 D 尾部） |
| 3 | `check-round --round-id p01-r001 --revision 1 --pane w2X:p1` ×4 | 0（`allowed=true`） | `.round9-tmp/check-before-red-tests.json`、`check-before-report.json`；其余两次输出见会话记录，字段一致 |
| 4 | 红测试：`python3 -m pytest -q > .round9-tmp/red-pytest.txt`（实现前，直接执行） | 1 | **附录 A 全文** |
| 5 | 实现后验收：`slot cpu -- python3 -m pytest -q > .round9-tmp/green-pytest-1.txt` | 0 | **附录 B 全文** |
| 6 | `python3 -m py_compile pairctl.py test_pairctl.py test_context_edges.py` | 0 | `COMPILE_OK` |
| 7 | `grep PAIRCTL_CONTINUE_MIN_DELAY_S\|_IDLE_S\|_TIMEOUT_S\|continue_timeout pairctl.py` | 1（无匹配） | `no legacy watch vars in pairctl.py` |
| 8 | `git status --short` / `git rev-parse HEAD` / `git diff --stat` | 0 | **附录 C** |

超 1 分钟的 pytest 经 `slot cpu` 提交（验收 39.11s wall / 红测试 81.93s 因旧 watch 20s min-delay 属实现前状态）；`slot audit`/`slot status` 均在重活前执行并落盘。

## 5. 推迟 / 跳过 / 降级

- **按冻结推迟到 #10 及以后**：插件清单的写入侧、`claude_session_start_hook.py` 钩子改动（钩子当前不读 `resume_pending`，本轮未动且被禁改）、窗格索引、窗格退出取消。本轮只判 `mechanism` 字段，**没有实现 plugin 侧的投递消费端**（投递仍统一走 `resume-deliver`，`--via` 取值已含 plugin/watcher）。
- **watch 输出面收窄（契约要求）**：成功仍为 `status=continue_prompted`/exit 0；不再产出 `continue_timeout`/`continue_failed`，也不再携带 `herdr_exit` 字段；idle/min-delay 判定路径整体移除。
- **降级说明**：`watch-compact-continue` 在“无记录”这一契约未规定的情形下，选 `{"status":"continue_skipped","reason":"no_record"}` + exit 0；deadline 不可解析时视为“已到期”（宁可早投递也不永久卡住，实际写入路径均产生合法 ISO deadline）。
- **红测试用直接执行**（当时未预估到 81.93s > 1min）；其后所有 pytest 均改经 `slot cpu`。红测试的 slot 规避属于流程瑕疵，已如实记录，无资源争用（`slot audit` 两轮均 exit 0、无告警）。
- 未删除任何既有用例；未放宽任何外部断言；未改 `invoke()` 的 flag=0 默认。

## 6. 假设

1. **mechanism 的“`agent get` 返回 claude”复用 `queue_planner_compact` 既有的那次 `agent get` 结果**（变量 `kind`）：同一次排队操作内的同一判定，不额外再调一次 `agent get`；探测只新增 `plugin list --json` 一次调用。
2. 首次写入的 `deadline = armed_at + PAIRCTL_RESUME_DEADLINE_S`（沿用 issue #8 原样）；同 epoch requeue 的 `deadline = now + PAIRCTL_RESUME_DEADLINE_S`（契约字面）。“已到 deadline”按 ISO(+00:00)→timestamp 比较（`now()` 为 UTC 秒精度）。
3. 同 epoch requeue **保留** `status`/`attempts`/`last_error`，即使记录已是终态（契约字面“其余字段保持不动”）；requeue 不派生 watcher（“只在新写入记录时派生”）。
4. `emit_rollover_block` 的既有 spawn 点保留原样，但同样吃 spawn 的三重门控（新记录写入 + flag + 非 INTERNAL），等价于契约列举的派生点语义。
5. 并发“恰好一条 prompt”依赖既有锁：`cmd_resume_deliver` 的认领→提示→落盘整段在同一把状态锁内，后到者只能看到 `delivered` 从而 `not_claimable`；本轮未改该锁结构。
6. `mechanism` 只在新写入时判定：探测被“flag 开启 且 非同 epoch requeue”双重门控，因此 `PAIRCTL_CONTINUE_AFTER_COMPACT=0` 的旧用例连 `plugin list` 调用都不会出现（保住了既有 `herdr_calls()==[]` 断言）。
7. 测试的“恰好一条”窗口取 5s（契约）+ 额外 0.3s 静默复核；快用例一律 `PAIRCTL_CONTINUE_POLL_S=0.05`，无任何测试按 15s 默认间隔等待。

## 7. 数值复算命令

```bash
cd /public/scripts/tc-skills
# 复算验收数字（136 passed / exit 0）
python3 -m pytest -q
# 只复算六个契约必测
python3 -m pytest -q skills/herdr-pair/tests/test_pairctl.py \
  -k "mechanism_plugin_only or continue_disabled or same_epoch_requeue or watcher_delivers or concurrent_resume or internal_marker"
# 复算 diff / 版本 / 字节数
git status --short
git rev-parse HEAD                      # 期望 77ee9281b54c23911edf39abfb1e8a5afac82f30
git diff --stat                         # 期望 3 files changed, 450 insertions(+), 96 deletions(-)
wc -c skills/herdr-pair/scripts/pairctl.py skills/herdr-pair/tests/test_pairctl.py skills/herdr-pair/tests/test_context_edges.py
# 复算“实现中已无旧 watch 变量”
grep -n "PAIRCTL_CONTINUE_MIN_DELAY_S\|PAIRCTL_CONTINUE_IDLE_S\|PAIRCTL_CONTINUE_TIMEOUT_S" skills/herdr-pair/scripts/pairctl.py
# 复算 contract hash（协议脚本输出为准）
python3 /home/tcuni-claw/.claude/skills/herdr-pair/scripts/pairctl.py check-round \
  --round-id p01-r001 --revision 1 --pane w2X:p1 --cwd /public/scripts/tc-skills | grep -o '"contract_hash": "[^"]*"'
# 红测试无法原样复跑（实现已落盘）；原文见附录 A / .round9-tmp/red-pytest.txt
```

## 8. 后台作业

- 无常驻后台作业；两轮 pytest 均为前台阻塞执行（验收一轮经 `slot cpu` 前台提交，退出码原样返回）。
- 测试拉起的 `watch-compact-continue` 子进程全部由 `PairctlTest.tearDown` 经 `compact-continue.pid` 发 `SIGTERM` 并确认退出；验收后复查：`pgrep -af watch-compact-continue` 仅命中执行 pgrep 的 shell 自身（无实际 watcher 进程），`skills/herdr-pair/tests/` 下 `tmp*` 残留计数 0。
- 中间产物全部在本轮 fence 内：`.round9-tmp/`（slot 日志、check-round 输出、红/绿 pytest 原文）；未向 `/tmp` 写任何文件。

## 9. 写入范围声明

仅修改 scope 内 3 个文件、新建本报告：`skills/herdr-pair/scripts/pairctl.py`、`skills/herdr-pair/tests/test_pairctl.py`、`skills/herdr-pair/tests/test_context_edges.py`、`skills/herdr-pair/reports/round-9-report.md`。`docs/`、`skills/herdr-pair/scripts/claude_session_start_hook.py`、`.handoff/`、`herdr-api-080.json` 零改动；未 commit、未 push、未开 PR、未改 issue。

---

# 附录 A：红测试原始输出（实现前，`python3 -m pytest -q`，exit=1）

```text
..............................................F...............F..F................F.. [ 62%]
.......................F....F............FF........            [100%]
=================================== FAILURES ===================================
_ ContextEdgesTest.test_active_state_and_goal_are_durable_before_compact_request _

self = <test_context_edges.ContextEdgesTest testMethod=test_active_state_and_goal_are_durable_before_compact_request>

    def test_active_state_and_goal_are_durable_before_compact_request(self):
        h = self.h
        h.invoke_ok("init", "--goal", "Preserve the active task", "--no-context-check")
        h.set_kind("claude")
        transcript = h.root / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant", "sessionId": "session-a",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "message": {"role": "assistant", "usage": {
                "input_tokens": 151000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }},
        }) + "\n", encoding="utf-8")
        h.invoke_ok("note-session", "--session-id", "session-a", "--transcript-path", str(transcript),
                    "--kind", "claude", "--source", "startup", "--pane", "w1:p1")
        source = h.cwd / "input.txt"
        source.write_text("before dispatch\n", encoding="utf-8")
        handoff = h.write_handoff("active.md", "[可以改] input.txt\n[可以新建] report.md\n[报告] report.md\n")
        sent = h.invoke_ok(
            "send-round", "--target", "w1:p2", "--file", str(handoff), "--no-fresh",
            extra_env={
                # Record write needs auto-continue; INTERNAL keeps this test's manual
                # watch the only wake-up source; deadline 0 => due immediately.
                "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
                "PAIRCTL_INTERNAL_WATCHER": "1",
                "PAIRCTL_RESUME_DEADLINE_S": "0",
            },
        )
        self.assertTrue(sent["planner_compact"]["queued"])
        manifest = Path(sent["snapshot"]["manifest"])
        self.assertEqual((manifest.parent / "files" / "input.txt").read_text(), "before dispatch\n")
        self.assertEqual(h.invoke("diff-round", "--round-id", sent["round_id"]).returncode, 0)
        source.write_text("after dispatch\n", encoding="utf-8")
        diff = h.invoke("diff-round", "--round-id", sent["round_id"])
        self.assertEqual(diff.returncode, 1)
        self.assertIn("input.txt", json.loads(diff.stdout)["changed"])
        during_compact = json.loads((h.root / "fake-herdr.state-during-send.json").read_text())
        self.assertIsNone(during_compact["pending_dispatch"])
        self.assertEqual(during_compact["rounds"][0]["status"], "active")
>       self.assertIn("Preserve the active task", h.herdr_log()["argv"][4])
                                                  ^^^^^^^^^^^^^^^^^^^^^^^^
E       IndexError: list index out of range

skills/herdr-pair/tests/test_context_edges.py:152: IndexError
___________ PairctlTest.test_concurrent_resume_deliver_single_prompt ___________

self = <test_pairctl.PairctlTest testMethod=test_concurrent_resume_deliver_single_prompt>

    def test_concurrent_resume_deliver_single_prompt(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(queued["queued"], queued)
        # The test drives delivery manually: pairctl must not spawn a watcher too.
>       self.assertFalse(queued["continue_after_compact"]["spawned"], queued)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       KeyError: 'continue_after_compact'

skills/herdr-pair/tests/test_pairctl.py:2559: KeyError
_________ PairctlTest.test_continue_disabled_skips_record_and_watcher __________

self = <test_pairctl.PairctlTest testMethod=test_continue_disabled_skips_record_and_watcher>

    def test_continue_disabled_skips_record_and_watcher(self) -> None:
        # invoke() defaults PAIRCTL_CONTINUE_AFTER_COMPACT=0: compact still queues,
        # but no record is written, no watcher spawns, and no probe runs.
        self.clear_calls()
        queued = self.invoke_ok("compact-self")
        self.assertTrue(queued["queued"], queued)
>       self.assertFalse(queued["continue_after_compact"]["spawned"], queued)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       KeyError: 'continue_after_compact'

skills/herdr-pair/tests/test_pairctl.py:2481: KeyError
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
>       self.assertEqual(len(continue_prompts), 1, self.herdr_calls())
E       AssertionError: 0 != 1 : [['agent', 'get', 'w1:p1'], ['agent', 'prompt', 'w1:p1', '/summarize'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1'], ['agent', 'get', 'w1:p1']]

skills/herdr-pair/tests/test_pairctl.py:918: AssertionError
_ PairctlTest.test_mechanism_plugin_only_when_linked_enabled_and_claude (kind='claude', content=[{'plugin_id': 'tc.herdr-pair', 'enabled': True}], expected='plugin') _

self = <test_pairctl.PairctlTest testMethod=test_mechanism_plugin_only_when_linked_enabled_and_claude>

    def test_mechanism_plugin_only_when_linked_enabled_and_claude(self) -> None:
        # issue #9: mechanism is decided only on a new resume_pending write.
        plugins = Path(str(self.herdr) + ".plugins.json")
        linked = [{"plugin_id": "tc.herdr-pair", "enabled": True}]
        cases = (
            # (planner kind, plugins.json content, expected mechanism)
            ("claude", linked, "plugin"),
            ("claude", [{"plugin_id": "tc.herdr-pair", "enabled": False}], "watcher"),
            ("claude", [{"plugin_id": "someone.else", "enabled": True}], "watcher"),
            ("claude", None, "watcher"),                     # file missing -> empty list
            ("claude", "{not json at all", "watcher"),       # list exits non-zero
            ("pi", linked, "watcher"),                       # not Claude -> watcher
        )
        for index, (kind, content, expected) in enumerate(cases):
            with self.subTest(kind=kind, content=content, expected=expected):
                self.set_kind(kind)
                if content is None:
                    plugins.unlink(missing_ok=True)
                elif isinstance(content, str):
                    plugins.write_text(content, encoding="utf-8")
                else:
                    plugins.write_text(json.dumps(content), encoding="utf-8")
                if index:
                    # A different epoch => the next arm is a fresh write and re-probes.
                    self.invoke_ok("rollover", "--reason", "compact")
                queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
                self.assertTrue(queued["queued"], queued)
                record = self.read_state()["resume_pending"]
>               self.assertEqual(record["mechanism"], expected)
E               AssertionError: 'watcher' != 'plugin'
E               - watcher
E               + plugin

skills/herdr-pair/tests/test_pairctl.py:2473: AssertionError
________ PairctlTest.test_resume_prompt_is_identical_to_legacy_watcher _________

self = <test_pairctl.PairctlTest testMethod=test_resume_prompt_is_identical_to_legacy_watcher>

    def test_resume_prompt_is_identical_to_legacy_watcher(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            **self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "0",
        })
        self.assertTrue(queued["queued"], queued)
        # Issue #9: the watch loop delivers only after the epoch advanced.
        self.invoke_ok("rollover", "--reason", "compact")
        self.set_status("idle")
        self.clear_calls()
        watched = self.invoke_ok(
            "watch-compact-continue",
            extra_env={"PAIRCTL_CONTINUE_POLL_S": "0.05"},
        )
        self.assertEqual(watched["status"], "continue_prompted")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        legacy_text = prompts[0][3]
    
        # The watch path claims through resume-deliver; rewind the record to pending so
        # the manual deliver below exercises the same prompt text.
        state_path = self.state / "state.json"
        doc = json.loads(state_path.read_text(encoding="utf-8"))
>       self.assertEqual(doc["resume_pending"]["status"], "delivered")
E       AssertionError: 'pending' != 'delivered'
E       - pending
E       + delivered

skills/herdr-pair/tests/test_pairctl.py:2434: AssertionError
__________ PairctlTest.test_same_epoch_requeue_updates_deadline_only ___________

self = <test_pairctl.PairctlTest testMethod=test_same_epoch_requeue_updates_deadline_only>

    def test_same_epoch_requeue_updates_deadline_only(self) -> None:
        first = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(first["queued"], first)
        record = self.read_state()["resume_pending"]
        armed_at = record["armed_at"]
        mechanism = record["mechanism"]
        first_deadline = record["deadline"]
        # Leftover delivery state must survive a same-epoch requeue untouched.
        state_path = self.state / "state.json"
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        doc["resume_pending"]["attempts"] = 1
        doc["resume_pending"]["last_error"] = "prior failure"
        state_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    
        requeue_at = time.time()
        requeued = self.invoke_ok(
            "compact-self",
            extra_env={**self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "30"},
        )
        self.assertTrue(requeued["queued"], requeued)
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["armed_at"], armed_at)
        self.assertEqual(record["mechanism"], mechanism)
        self.assertEqual(record["status"], "pending")
>       self.assertEqual(record["attempts"], 1)
E       AssertionError: 0 != 1

skills/herdr-pair/tests/test_pairctl.py:2514: AssertionError
___________ PairctlTest.test_watcher_child_env_uses_internal_marker ____________

self = <test_pairctl.PairctlTest testMethod=test_watcher_child_env_uses_internal_marker>

    def test_watcher_child_env_uses_internal_marker(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
            "PAIRCTL_RESUME_DEADLINE_S": "0",
            "PAIRCTL_CONTINUE_POLL_S": "0.05",
        })
        self.assertTrue(queued["queued"], queued)
>       self.assertTrue(queued["continue_after_compact"]["spawned"], queued)
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       KeyError: 'continue_after_compact'

skills/herdr-pair/tests/test_pairctl.py:2591: KeyError
___ PairctlTest.test_watcher_delivers_once_at_deadline_and_records_mechanism ___

self = <test_pairctl.PairctlTest testMethod=test_watcher_delivers_once_at_deadline_and_records_mechanism>

    def test_watcher_delivers_once_at_deadline_and_records_mechanism(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
            "PAIRCTL_RESUME_DEADLINE_S": "0",
            "PAIRCTL_CONTINUE_POLL_S": "0.05",
        })
        self.assertTrue(queued["queued"], queued)
>       self.assertTrue(queued["continue_after_compact"]["spawned"], queued)
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       KeyError: 'continue_after_compact'

skills/herdr-pair/tests/test_pairctl.py:2531: KeyError
=========================== short test summary info ============================
FAILED skills/herdr-pair/tests/test_context_edges.py::ContextEdgesTest::test_active_state_and_goal_are_durable_before_compact_request
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_concurrent_resume_deliver_single_prompt
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_continue_disabled_skips_record_and_watcher
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_fifth_finish_spawns_continue_watcher_when_enabled
SUBFAILED(kind='claude', content=[{'plugin_id': 'tc.herdr-pair', 'enabled': True}], expected='plugin') skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_mechanism_plugin_only_when_linked_enabled_and_claude
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_resume_prompt_is_identical_to_legacy_watcher
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_same_epoch_requeue_updates_deadline_only
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_watcher_child_env_uses_internal_marker
FAILED skills/herdr-pair/tests/test_pairctl.py::PairctlTest::test_watcher_delivers_once_at_deadline_and_records_mechanism
9 failed, 128 passed, 68 subtests passed in 81.93s (0:01:21)
exit=1
```

# 附录 B：验收原始输出（实现后，`slot cpu -- python3 -m pytest -q`，exit=0）

```text
..................................................................................... [ 62%]
...................................................            [100%]
136 passed, 69 subtests passed in 39.11s
exit=0
```

# 附录 C：git 原始输出

```text
$ git status --short
 M skills/herdr-pair/scripts/pairctl.py
 M skills/herdr-pair/tests/test_context_edges.py
 M skills/herdr-pair/tests/test_pairctl.py
?? .handoff/
?? .round9-tmp/
?? docs/specs/issue-1-herdr-pair.md
?? docs/tickets/
?? herdr-api-080.json
?? skills/herdr-pair/reports/

$ git rev-parse HEAD
77ee9281b54c23911edf39abfb1e8a5afac82f30

$ git diff --stat
 skills/herdr-pair/scripts/pairctl.py          | 212 ++++++++++++-----
 skills/herdr-pair/tests/test_context_edges.py |  20 +-
 skills/herdr-pair/tests/test_pairctl.py       | 314 +++++++++++++++++++++++---
 3 files changed, 450 insertions(+), 96 deletions(-)
```

# 附录 D：slot audit / status 原始输出（尾部）

```text
$ slot audit  # exit=0, 完整日志 .round9-tmp/.round9-slot-audit.log
PID      RSS         CPU%  COMM             状态

没发现绕过 slot 的重进程。
PID      RSS         CPU%  COMM             状态

没发现绕过 slot 的重进程。

$ slot status  # exit=0, 完整日志 .round9-tmp/.round9-slot-status.log（尾 15 行）
1
0
9
 
 
f
i
n
i
s
h
e
d
 
 
 
/
p
r
o
j
e
c
t
/
t
m
p
/
s
l
o
t
/
t
s
-
o
u
t
.
e
U
O
O
D
L
 
0
 
 
 
 
 
 
 
 
2
0
6
5
.
4
2
/
6
8
1
.
1
8
/
7
3
.
3
1
 
[
z
m
2
2
-
8
0
0
k
t
0
0
6
-
l
i
f
t
o
v
e
r
]
/
h
o
m
e
/
t
c
u
n
i
-
c
l
a
w
/
.
l
o
c
a
l
/
s
h
a
r
e
/
s
l
o
t
/
j
o
b
s
/
1
7
8
9
6
2
6
2
6
2
-
3
9
7
2
5
6
8
-
2
5
5
2
5
.
s
h
```
