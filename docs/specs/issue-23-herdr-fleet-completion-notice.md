# herdr-fleet：主 agent 唤醒、按轮次等待与任务回报块

关联 issue：<https://github.com/tcuni24/tc-skills/issues/23>

状态：spec 已按 to-spec 流程合成；测试接缝与工单拆分已经用户确认。下文所述命令、插件与钩子均为拟实现能力，不代表已存在。

## Problem Statement

用户在 herdr 里让主 agent（控制 tab）用 herdr-fleet 开多个并行任务。真实一次使用中（deliver 项目的 `perf-usetable-fix`、`perf-admin-list-n1` 两个任务），主 agent 确认两个任务 agent 进入 working 后就结束了本轮，既没有执行 `fleet wait`，也没有任何机制在任务完成时叫醒它；两个任务早已 idle 并提了 PR，主 agent 直到用户追问才去取结果。

根因有四处：

1. **只有拉取，没有推送。** fleet 提供 `ls/status/peek/wait`，全部需要主 agent 主动调用；任务 agent 的结果只留在各自的终端里。主 agent 一结束本轮，没有任何东西能把它叫醒。herdr-fleet 也不走原生 subagent，宿主侧不会产生完成通知。
2. **SKILL 没有把这个限制写成规则。** SKILL 只说"开完后执行 `fleet ls`"，没有要求主 agent 在结束本轮前二选一：要么阻塞等待，要么确认有通知机制。于是主 agent 可能对用户说"完成后会通知你"，而实际上不会。
3. **`fleet wait` 需要手工规避竞态。** agent 在提交 prompt 之前已经是 idle，`wait` 会立即返回；现在要求主 agent 先 `status` 确认 working 再 `wait`，且只能一次等一个任务。
4. **任务回报没有固定格式。** 主 agent 只能翻终端尾部拼凑结论，证据边界容易丢。例如 `perf-usetable-fix` 的 hook 当前没有业务调用方、截图只是空列表，这类"不能据此认定已验收"的信息只有读完终端才看得出来。

## Decisions

| # | 决策 | 定为 |
|---|---|---|
| D1 | 唤醒机制 | herdr 插件 `tc.herdr-fleet` 订阅 `pane.agent_status_changed` 与 `pane.exited`；不做常驻 watcher 进程。herdr-pair 已验证执行者状态事件不区分 agent 类型（ADR 0001「影响」一节） |
| D2 | 逻辑归属 | 插件钩子是薄壳，只解析事件并调用 `fleet event`；判定、去重、延后、投递全部在 fleet 命令里，状态以 fleet 的记录为准 |
| D3 | 投递时机 | 主 agent 空闲时立即 `herdr agent prompt`；主 agent working 或 blocked 时进收件箱，等主 agent 自己的空闲边沿到来时合并为一条投递。不往正在运行的主 agent 里插话 |
| D4 | 通知内容 | 只当触发器：首行固定标明"herdr-fleet 自动通知，非用户消息"，列出 slug、agent 状态、phase、PR（如已知），并要求用 `fleet status` 核查。通知本身不是证据 |
| D5 | 去重 | 一个任务每一轮最多通知一次。轮次由 `new`/`send` 提交成功时开始（基线为提交前读取的状态序号），也可由序号晚于上次已通知终态的 working 边沿开始（用户直接在任务 pane 里输入的情况）；只有序号晚于本轮基线的终态（idle/done/blocked/exited）才通知，不要求事先观察到 working 边沿。提交前的旧终态、迟到或重复的事件不通知 |
| D6 | 状态存放 | `TASK.md` 记录 `coordinator_pane`；pane 索引与收件箱放在 `$XDG_STATE_HOME/herdr-fleet/`（默认 `~/.local/state/herdr-fleet/`），不放进任务目录（`done` 预检会拒绝任务目录里多出的文件） |
| D7 | 无插件时的规则 | `fleet doctor` 未通过时，主 agent 必须在同一轮内 `fleet wait` 直到任务结束或阻塞，不能以"告诉用户不会自动通知"代替等待；不得声称会自动通知。`fleet inbox` 只作兜底查看，不替代等待 |
| D8 | wait 语义 | 只接受本次提交 prompt 之后发生的终态（序号晚于提交前读取的基线）；`new`/`send` 在命令内部确认 agent 已开始工作，确认期间已出现晚于基线的终态（快速完成）同样视为确认成功；`wait` 支持多个 slug 与 `--any`/`--all` |
| D9 | 回报块 | 任务 agent 结束时输出固定的 `FLEET-REPORT` 块；`fleet status` 摘取最近一块；SKILL 汇报模板加"证据边界"一列 |
| D10 | 实施基线 | 实施分支基于已合并 PR #22（ls 表格）之后的 `main`；工单 1、2 可并行，3 → 4 → 5 串行 |

## Solution

### A. 任务回报块（D9）

`fleet new` 附加给任务 agent 的固定说明里，新增结束时的回报要求：在最终回复里输出一块以 `FLEET-REPORT` 开头、以 `END FLEET-REPORT` 结尾的文本，字段固定：

- 结果：完成 / 部分完成 / 阻塞
- PR：链接或"无"
- 测试：每条命令及退出码
- 证据边界：哪些已真实验证，哪些只是合成数据、mock、空页面或未覆盖
- 需要决策：需要用户决定的事项，或"无"

`fleet status` 在 agent 最近输出中查找最后一个完整的回报块，原样展示在"回报"一节，JSON 输出新增 `report` 字段（未找到时为 `null`，文本显示"未找到回报块"）。`send` 转达的补充说明不改变这条要求。

### B. 按轮次等待（D8）

- `new` 与 `send` 在提交 prompt 前读取并记录状态序号作为本轮基线，提交后在命令内部等待 agent 进入 working 或 blocked（短超时）。任务可能在确认返回前就完成 working → idle，且确认可能看不到短暂的 working：确认超时后先查 `agent get`，若已出现序号晚于基线的 idle/done/blocked，视为已开始并已结束，确认成功。仍无变化时才报错并保留任务，不回滚已建的 worktree（`new` 的回滚只针对 worktree、tab、agent 创建失败）。
- `wait <slug>...`：对每个任务，只接受序号晚于最近一次提交的 idle/done/blocked；已经满足的立即计入。`--all`（默认）等全部任务，`--any` 等第一个。返回码沿用现有约定：全部 idle/done 为 0，任一 blocked 为 3，超时为 4，其他错误为 1；多任务时逐行输出 `slug phase state`。
- SKILL 删去"先用 `fleet status` 确认 working 再 wait"的手工步骤。

### C. 完成事件与收件箱（D2、D3、D5、D6）

- `new`/`open` 把当前 `HERDR_PANE_ID` 记为 `coordinator_pane`（写入 `TASK.md`），并在 pane 索引中登记：任务 pane → (项目根, slug, role=task)，主 agent pane → (项目根, role=coordinator)。`done` 清理对应索引；`fleet bind` 把当前项目全部活跃任务的 `coordinator_pane` 改为当前 pane（主 agent 换了 tab 或重启后使用）。
- `fleet event --pane <id> --status <状态>`：
  - 本轮开始：`new`/`send` 提交成功时已记录本轮基线（B 节），不依赖插件观察到 working 边沿。任务 pane 的 working 边沿只在其序号晚于上次已通知终态时开始新一轮（用户直接在任务 pane 输入的情况），迟到的旧 working 不开新轮。
  - 任务 pane 的终态边沿（idle/done/blocked/exited）：以 `agent get` 核对当前状态与序号，序号晚于本轮基线且本轮未通知时，生成一条事件进收件箱，并尝试投递。working 事件缺失、idle 先于 working 到达时同样通知一次；提交前的旧终态与重复事件不通知。
  - 主 agent pane 的 idle/done 边沿：把收件箱里属于该 pane 的待投递事件合并成一条投递。
  - 不认识的 pane：静默退出 0。
- 投递：主 agent 当前为 idle/done 时 `herdr agent prompt`，以 `agent_prompted` 为成功；主 agent working/blocked、pane 不存在或投递未确认时保留为待投递。pane 不存在时用 `herdr notification show` 提醒一次。
- `fleet inbox`：列出待投递事件；`--all` 包含已投递历史；`--json` 输出结构化数据。在主 agent 里运行 `inbox` 不改变投递状态。
- 收件箱写入需加锁，两个钩子并发触发时不丢事件、不重复投递。

### D. 插件薄壳与 doctor（D1、D7）

- 插件清单 `tc.herdr-fleet` 放在 skill 目录，`min_herdr_version` 按实际验证版本填写；订阅 `pane.agent_status_changed` 与 `pane.exited`。
- 钩子读取 `HERDR_PLUGIN_EVENT` 与 `HERDR_PLUGIN_EVENT_JSON`，兼容 `{event, data}` 外层包装与扁平载荷，取 `pane_id`、`agent_status`，查 pane 索引得到项目根后调用同目录的 fleet `event`。不认识的 pane 不调用 fleet。钩子失败时显示一次 `notification show`。
- `fleet doctor`：检查 herdr 版本、插件已安装且启用、活跃任务的 `coordinator_pane` 仍存在、待投递事件数量；全部满足返回 0，否则返回非零并逐项说明。
- README 写安装：`herdr plugin link <skill 目录> --enabled`，以及卸载与 `herdr plugin log list` 排障。
- SKILL「开任务」一节：开完后运行 `fleet doctor`；通过则可以结束本轮，并告诉用户完成后会被自动通知；不通过则必须在同一轮内 `fleet wait` 到任务结束或阻塞，不得只告诉用户不会自动通知就结束本轮。收到自动通知后先 `fleet status` 核查，再向用户汇报。

## User Stories

1. 作为主 agent，我希望任务 agent 完成时自动在我的 pane 里收到一条消息，以便结束本轮后仍能接着核查和汇报。
2. 作为主 agent，我希望任务 agent 进入 blocked 时同样被通知，以便及时把属于用户的决策转给用户。
3. 作为主 agent，我希望任务 tab 被关闭或 agent 退出时被通知，以便发现任务意外中断。
4. 作为主 agent，我希望自己正在工作时不被插话，通知等我空闲时再来，以便当前操作不被打断。
5. 作为主 agent，我希望多个任务接连完成时只收到一条合并消息，以便不被连续的消息刷屏。
6. 作为主 agent，我希望同一次完成只通知一次，以便不会对同一结果重复核查。
7. 作为主 agent，我希望通知明确标明不是用户消息，以便我不把它当成用户的新指令。
8. 作为主 agent，我希望通知只给出 slug、状态、phase 和 PR，并提示用 `fleet status` 核查，以便我按证据而不是按通知下结论。
9. 作为主 agent，我希望通知未能投递时留在收件箱里，以便之后用 `fleet inbox` 找回。
10. 作为主 agent，我希望我的 pane 不存在时用户能看到 herdr 通知，以便有人知道结果没送达。
11. 作为主 agent，我希望换了控制 tab 后用 `fleet bind` 把任务改绑到新 pane，以便通知送到正确的地方。
12. 作为主 agent，我希望运行 `fleet doctor` 就知道自动通知是否可用，以便决定能否结束本轮。
13. 作为主 agent，我希望 doctor 不通过时 SKILL 明确要求我在同一轮内等待，以便不会再出现"开完就走、没人取结果"。
14. 作为用户，我希望主 agent 只在通知确实可用时才说"完成后会通知你"，以便我对进度的判断不被误导。
15. 作为用户，我希望用一条 `herdr plugin link` 命令就能装上通知插件，并能随时卸载，以便试用成本低。
16. 作为用户，我希望插件卸载后 fleet 其余功能照常可用，以便插件不成为硬依赖。
17. 作为用户，我希望在 `herdr plugin log list` 里看到钩子调用记录，以便通知没来时能排障。
18. 作为主 agent，我希望 `fleet new` 返回时任务 agent 已确认开始工作，以便不必再手工 `status` 确认。
19. 作为主 agent，我希望 `fleet send` 返回时任务 agent 已确认收到并开始处理，以便知道补充说明已生效。
20. 作为主 agent，我希望 `fleet wait` 只认本次提交之后的完成，以便不会因为提交前的 idle 而立即返回。
21. 作为主 agent，我希望一条 `fleet wait a b` 等多个任务，以便在同一轮里阻塞到所有任务结束。
22. 作为主 agent，我希望 `fleet wait --any` 在第一个任务结束时返回，以便边等边处理先完成的任务。
23. 作为主 agent，我希望 wait 的返回码区分完成、阻塞和超时，以便脚本化地决定下一步。
24. 作为主 agent，我希望任务 agent 结束时输出固定格式的回报块，以便不用翻终端拼凑结论。
25. 作为主 agent，我希望 `fleet status` 直接展示最近的回报块，以便一条命令拿到 PR、测试和证据边界。
26. 作为主 agent，我希望回报块必须写证据边界，以便区分真实验证和 mock、空页面、未覆盖的部分。
27. 作为主 agent，我希望回报块列出需要用户决定的事项，以便及时转给用户。
28. 作为主 agent，我希望找不到回报块时 `fleet status` 明确说"未找到回报块"，以便知道要去 peek 或追问。
29. 作为用户，我希望主 agent 的汇报表格包含证据边界一列，以便一眼看出哪些结论还不能作为验收依据。
30. 作为任务 agent，我希望回报要求写在 fleet 自动附加的说明里，以便不依赖主 agent 每次在 prompt 里重复。
31. 作为维护者，我希望判定、去重和投递都在 fleet 命令里，钩子只是薄壳，以便用命令行黑盒测试覆盖几乎全部逻辑。
32. 作为维护者，我希望并发钩子不会丢事件或重复投递，以便多个任务同时完成时行为可靠。
33. 作为维护者，我希望 pane 索引和收件箱不写进任务目录，以便 `fleet done` 的预检不受影响。
34. 作为维护者，我希望 `fleet done` 清理该任务的索引和收件箱记录，以便状态目录不累积垃圾。
35. 作为维护者，我希望不认识的 pane 事件静默忽略，以便插件不干扰 herdr-pair 或其他 tab。
36. 作为维护者，我希望旧任务（`TASK.md` 没有 `coordinator_pane`）继续可用，只是不产生通知，以便升级不破坏已有任务。
37. 作为主 agent，我希望任务在开始确认返回前就完成（working → idle）时，`new`/`send` 不误报超时、`wait` 立即返回、通知照常且只发一次，以便很快完成的小任务不被当成失败或被漏掉。

## Implementation Decisions

- **fleet 命令新增与修改**
  - 新增：`event`、`inbox`、`bind`、`doctor`。
  - 修改：`new`、`open`（记录 `coordinator_pane`、登记 pane 索引、确认 agent 开始工作并记录提交序号）、`send`（确认开始工作并记录提交序号）、`wait`（多 slug、`--any`/`--all`、按提交序号判定）、`status`（摘取回报块，JSON 增加 `report`）、`done`（清理索引与收件箱记录）。
  - 现有命令的返回码与 `ls --json` 已有键保持不变。
- **状态序号来源**：优先使用 herdr `agent get` 返回的 `state_change_seq`；实施时须核实它在提交 prompt、状态变化时的实际行为，若不可用则由 fleet 自记提交时间与边沿顺序，结论写进 PR。
- **任务 agent 的确认开始**：提交前读取基线序号；使用 `herdr agent prompt --wait --until working --until blocked` 加短超时；`agent_blocked`（提交前已 blocked）报错并提示先 peek。确认超时后以 `agent get` 复核，序号晚于基线的终态视为快速完成、确认成功。实施时核实 `--until working` 能否错过短暂的 working，结论写进 PR。
- **事件模型**：每条事件包含 项目根、slug、终态、产生时间、去重键、投递状态（pending / delivered）、投递尝试次数与最近错误。去重键由 slug 与本轮基线唯一确定（D5），不依赖是否观察到 working 边沿。
- **合并投递格式**：首行 `herdr-fleet 自动通知（非用户消息）：N 个任务状态变化`，随后每个任务一行 `slug agent状态 phase PR`，末行要求逐个 `fleet status <slug>` 核查。单条 prompt 不超过 herdr 的 prompt 长度限制，超出时截断任务行并注明剩余数量。
- **插件**：与 herdr-pair 插件互相独立，可同时启用；钩子不导入 fleet、不直接写状态，只调用 fleet 命令。
- **SKILL.md / workflow.md**：更新「开任务」「看情况」「收尾」三节与命令全集；新增"收到自动通知后的处理"；汇报模板加证据边界。CONTEXT.md 的 fleet task 条目补充 coordinator pane、收件箱、回报块三个术语。
- **不修改** herdr-pair 的任何文件，也不重开 ADR 0001。

## Testing Decisions

- 好的测试只断言外部行为：命令输出、返回码、fake herdr 收到的调用（尤其是 `agent prompt` 的目标与内容）、状态目录里的可观察结果；不断言内部函数。
- **主接缝 A：fleet 命令行 + fake herdr。** 沿用 herdr-fleet 测试里现有的 fake herdr 脚本与临时多仓库夹具，扩展：
  - `agent get` 返回可配置的 `agent_status` 与 `state_change_seq`；
  - `agent prompt` 返回可配置的 prompted / blocked / 失败，并记录目标 pane 与文本；
  - `agent read` 可返回含回报块的输出；
  - `plugin list` 返回可配置的已安装/启用状态。
  覆盖：回报块摘取与缺失；new/send 的开始确认与失败；快速完成（确认期间已 working → idle 且看不到 working）不误报超时、wait 立即返回；wait 的提交序号判定、多任务、`--any`、各返回码；event 的提交后终态通知、提交前旧终态不通知、working 事件缺失或乱序时仍只通知一次、重复事件只投一次、主 agent 忙时延后与空闲时合并投递、pane 不存在时通知并保留、并发两次 event 不丢不重；inbox 与 `--all`/`--json`；bind；done 清理；旧任务无 `coordinator_pane` 不通知；doctor 各项。
- **接缝 B：插件钩子子进程。** 以 `HERDR_PLUGIN_EVENT`/`HERDR_PLUGIN_EVENT_JSON` 环境变量运行钩子，断言包装载荷与扁平载荷都转成正确的 fleet `event` 调用，不认识的 pane 不调用 fleet，fleet 失败时显示一次通知。另加清单结构断言（事件订阅、id）。先例：herdr-pair 插件钩子与清单的测试。
- **接缝 C：真机验收（一次性，人工）。** 在用户的 herdr 上 link 并启用插件，主 agent 用 pi 开一个只做小事的任务后结束本轮，确认被自动通知唤醒；再验证主 agent 忙时延后、任务 blocked、两个任务接连完成合并为一条。结论写入 skill 的 README 验证清单。先例：herdr-pair 插件⑧（#15）。
- 不在测试中 link 本机真实的插件注册表。

## Out of Scope

- 常驻 watcher 进程或定时轮询作为通知机制。
- 主 agent 正在工作时向其插话（steer）。pi 等宿主如何处理工作中收到的输入未经验证，本 spec 一律延后到空闲投递。
- 任务 agent 自行调用通知命令汇报（容易遗漏，且 blocked 时无法执行）。
- 通知中携带回报块全文或测试结论；主 agent 必须自己 `fleet status` 核查。
- 跨机器（`herdr --machine`/`--remote`）的通知。
- herdr-pair 的任何改动，以及主 agent 上下文压缩后的恢复。
- 通知的声音、位置定制。

## Further Notes

- 触发本 spec 的真实场景：deliver 项目一次 fleet 使用中，主 agent 开出 `perf-usetable-fix`（前端 !468）与 `perf-admin-list-n1`（后端 !400）后结束本轮，直到用户追问才取回结果；前者的验证还存在"hook 无业务调用方、截图为空列表"的证据边界。
- 工单拆分（#23 的子 issue；正文存档于同目录 `issue-23-tickets/`）：
  1. #24 任务回报块 FLEET-REPORT（无前置）
  2. #25 wait 按轮次判定与开始确认（无前置）
  3. #26 完成事件与收件箱（前置 #25）
  4. #27 herdr 插件薄壳与 `fleet doctor`（前置 #26）
  5. #28 真机验收（前置 #27，`ready-for-human`）
