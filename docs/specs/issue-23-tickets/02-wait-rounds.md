## Parent

#23

## What to build

`fleet wait` 只认本次提交 prompt 之后的完成，并能一次等多个任务；`new` / `send` 返回时任务 agent 已确认开始工作（spec D8、Solution B）。主 agent 不再需要"先 status 确认 working 再 wait"。

- `new`、`open`（启动 agent 并提交 prompt 时）与 `send` 在提交前读取并记录状态序号作为本轮基线，提交后在命令内部等待 agent 进入 working 或 blocked（短超时）。提交前 agent 已 blocked 时报错并提示先 peek。
- 快速完成：任务可能在确认返回前就 working → idle，确认也可能看不到短暂的 working。确认超时后先查 `agent get`，已出现序号晚于基线的 idle/done/blocked 即视为已开始并已结束，确认成功；仍无变化才报错，且不回滚已建的 worktree。实施时核实 `--until working` 是否会错过短暂 working，结论写进 PR。
- `wait <slug>...` 对每个任务只接受序号晚于最近一次提交的 idle/done/blocked；`--all`（默认）等全部，`--any` 等第一个；多任务时逐行输出 `slug phase state`。返回码沿用：全部 idle/done 为 0，任一 blocked 为 3，超时为 4，其他错误为 1。
- 序号来源：优先 herdr `agent get` 的 `state_change_seq`；实施时核实其实际行为，不可用则由 fleet 自记，结论写进 PR。
- SKILL.md 删去手工确认步骤，并新增规则：通知不可用时（本工单阶段即始终），主 agent 结束本轮前必须 `fleet wait` 到任务结束或阻塞，不得声称会自动通知。

## Acceptance criteria

- [ ] `new` 提交 prompt 后调用了开始确认，并记录提交序号；确认失败时报错、worktree 与 tab 保留
- [ ] 提交前 agent 已 blocked：`send` 报错且提示 peek，不发送文本
- [ ] agent 在提交前就是 idle、提交后尚未完成时，`wait` 不立即返回
- [ ] 快速完成：fake herdr 让提交后立即 working → idle，且 `prompt --wait` 看不到 working（确认超时）时，`new`/`send` 不报错、基线为提交前序号，随后 `wait` 立即返回 0；提交前的旧 idle 仍不计入
- [ ] 序号已晚于提交的终态立即计入；`wait a b` 等全部，`--any` 在第一个终态返回
- [ ] 返回码 0/3/4/1 各有用例覆盖，多任务混合 blocked 时为 3
- [ ] 单个 slug、不带新参数的旧用法输出与返回码保持兼容
- [ ] SKILL.md 与 workflow.md 更新：去掉手工确认步骤，加入"结束本轮前必须 wait"的规则（不能以"说明不会自动通知"代替）
- [ ] fake herdr 扩展 `agent get`（可配 `agent_status`、`state_change_seq`）与 `agent prompt --wait` 响应；herdr-fleet 现有测试全部通过

## Blocked by

None (can start immediately)
