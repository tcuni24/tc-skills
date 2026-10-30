# herdr-fleet 自动通知

需要 herdr 0.9.1 或更新版本，以及 Python 3.11 或更新版本。在 herdr 中安装并启用插件（将路径替换为实际 skill 目录）：

```sh
herdr plugin link <skill 目录> --enabled
```

插件 `tc.herdr-fleet` 与 `tc.herdr-pair` 独立，可以同时启用。卸载本地链接：

```sh
herdr plugin unlink tc.herdr-fleet
```

任务 agent 需要对应的 herdr 集成。例如 pi 任务须先运行 `herdr integration install pi`，并用 `herdr integration status` 检查；安装仅对之后新启动的 agent 生效。未安装时 pi 可能始终显示 idle，开始确认、wait 和自动通知均不可用。

在项目根运行 `python3 <skill 目录>/scripts/fleet.py doctor` 检查版本、插件、任务 agent 集成、主 agent pane 和待投递事件。检查不通过时，主 agent 必须在同一轮内用 `fleet wait` 等待任务结束或阻塞。换控制 pane 后运行 `fleet bind`；用 `fleet inbox` 查看待投递事件。

通知未到时，用 `herdr plugin log list` 检查钩子记录，并用 `fleet status <slug>` 核查任务。插件卸载后其他 fleet 命令仍可使用。

## ask_user 扩展（pi）

`extensions/ask-user.ts` 是一个 pi 扩展，注册 `ask_user` 工具和 `/ask-user` 命令。工具在等待用户输入期间经 pi 事件总线发出 `herdr:blocked { active: true, label: <问题> }`，等待结束（回答、取消、中止或出错）后发出一次 `{ active: false }`。herdr 的 pi 集成（`herdr-agent-state.ts`）按 active 计数上报 pane 状态：等待期间显示 blocked（label 作为阻塞原因），恢复后回到 working/idle。扩展与集成的契约是配平计数——只在确实等待过的调用上发 `{ active: false }`，不会提前释放其他 blocked 所有者。未安装集成时事件无人消费，工具退化为普通提问。

参数：

- `question`（必填）：问题文本。
- `options`（可选，非空数组）：给出时弹出选择列表；省略则为自由文本输入。
- `placeholder`（可选）：自由文本输入的占位提示，有 options 时忽略。
- `timeout`（可选，毫秒）：对话框超时自动取消，结果记为 cancelled。

结果：`structuredContent`/`details.status` 为 `answered`（附 `answer`）、`cancelled`（Esc、空白输入、超时或中止）、`unavailable`（print/json 等无 UI 模式，isError）、`invalid`/`error`（isError）。select 和 input 对话框在 TUI 与 RPC 模式都可用（RPC 经 extension UI 协议转发）。

`/ask-user <问题> [| 选项1 | 选项2...]` 走同一条等待路径，供人工或验收脚本触发；无参数时报用法。

安装（任选其一）：

```sh
pi -e <skill 目录>/extensions/ask-user.ts            # 本次会话生效
cp <skill 目录>/extensions/ask-user.ts ~/.pi/agent/extensions/   # 用户级常驻
```

任务 agent 由 fleet 启动 pi 时逐任务加载（参数经 `herdr agent start` 传给 pi）：

```sh
fleet new <slug> --agent pi --agent-arg=-e --agent-arg=<skill 目录绝对路径>/extensions/ask-user.ts --prompt '...'
```

验收：`python3 tests/herdr-fleet/ask_user_live_check.py` 在 pty 中启动真实 pi（关闭扩展发现，`-e` 显式加载本扩展与已安装的 herdr 集成），HERDR_* 指向脚本自建 unix socket，键入 `/ask-user` 后断言 `pane.report_agent` 依次出现 idle → blocked（message 为问题文本）→ idle。`--plain` 则完全不加载集成、不开 socket，验证 `/ask-user` 在无事件消费者时仍是普通提问。

真实 fleet 验收（已实测，pi 1.4.0 / herdr 0.9.1 / 插件 tc.herdr-fleet，probe `ask-user-live`）：按上例 `fleet new` 创建 pi 任务并登记 `coordinator_pane`。模型路径：任务 pi 的真实模型实际调用 `ask_user(验收选项, [alpha, beta])` → pane blocked → blocked 通知进入 coordinator 收件箱并 delivered → 真实按键选 alpha → 工具返回 `User selected: alpha` → 恢复。命令路径：向 pane 键入 `/ask-user … | a | b` 回车 → blocked → Enter 回答恢复；Escape 取消 → `User cancelled without answering` 恢复。原始证据（agent get/inbox/plugin log/终端文本记录/pi session 转录）为本机 gitignored 产物，留存于 `tests/herdr-fleet/tmp-live/fleet-acceptance/`。已知限制：blocked 的任务 pane 上 `fleet status --lines 70` 读取输出返回 `agent_not_idle`（#36）；改用 `herdr agent read <pane> --source visible` 查看（`--source visible` 不可省略）。

## 真机验证清单

在 herdr 0.9.1 上使用 pi 完成以下验证；0.9.5 未验证。验收后插件已 unlink，pi 集成保留安装。自动化测试使用 fake herdr，不修改真实插件注册表。

- 通过：安装 pi 集成后 `herdr integration status` 显示 current，`fleet doctor` 返回 0；`fleet send` 开始确认成功。
- 通过：主 agent 结束本轮后，任务完成时被自动通知唤醒，先用 `fleet status` 核查再汇报。
- 通过：主 agent 忙于 sleep 150 时两个任务先后完成，空闲后合并为一条通知，包含 `live-b done review -` 和 `live-c done review -`。
- 通过：herdr 接受清单及相对路径钩子，`herdr plugin log list` 显示 `pane.agent_status_changed` 钩子 succeeded、退出码 0。
- 通过：关闭任务 tab 触发 `tab.closed`，钩子按索引中的 tab_id 找到任务 pane，事件以 `exited` 状态投递给主 agent（`herdr plugin log list` 中 `tab.closed` 钩子 succeeded、退出码 0）。插件订阅 `pane.exited`、`pane.closed` 和 `tab.closed`；杀掉任务 agent 进程时通知状态为 done。旧索引没有 tab_id，需运行 `fleet bind` 或重新打开任务以登记 tab。
- 通过：blocked 通知。用只含 `/probe-block`、`/probe-unblock` 两个命令的临时 pi 扩展（`pi -e`，经 pi 事件总线发出 `herdr:blocked`，由 herdr 自带的 pi 集成上报，未写 socket）：`fleet send <slug> '/probe-block'` 后 herdr 显示 blocked，主 agent 收到 `<slug> blocked blocked -`；`/probe-unblock` 后状态回到 idle。pi 纯文字提问不会发 `herdr:blocked`，仍显示 done。
- 通过：ask-user-live probe（#34 交付验收）。真实模型调用 `ask_user` 与 `/ask-user` 命令路径均使 pane 上报 blocked 且通知进 coordinator 收件箱；按键回答返回所选值、Esc 取消返回 cancelled，状态恢复。无集成时工具降级为普通提问。
- 已知限制：任务回报块常不在最近 12 行中，`fleet status` 可能显示未找到回报块；可增大 `--lines`（属 #24 范围）。blocked 的任务 pane 上 `fleet status --lines 70` 读输出返回 `agent_not_idle`（#36），用 `fleet status --lines 0` 加 `herdr agent read <pane> --source visible` 代替。
