## Parent

#23

## What to build

不依赖插件、仅用命令行即可验证的完成事件与收件箱：任务 agent 完成、阻塞或退出时，fleet 生成事件并投递到主 agent 的 pane；主 agent 忙时延后、空闲时合并投递（spec D2、D3、D4、D5、D6、Solution C）。

- `new`/`open` 在 `TASK.md` 记录 `coordinator_pane`（当前 `HERDR_PANE_ID`），并在 `$XDG_STATE_HOME/herdr-fleet/` 的 pane 索引登记任务 pane 与主 agent pane；`done` 清理该任务的索引与收件箱记录；`fleet bind` 把当前项目全部活跃任务改绑到当前 pane。
- `fleet event --pane <id> --status <状态>`：本轮由 `new`/`send` 提交成功时开始（沿用 #25 记录的提交前基线），不依赖插件观察到 working 边沿；任务 pane 的 working 边沿只在其序号晚于上次已通知终态时开始新一轮（用户直接在任务 pane 输入的情况），迟到的旧 working 不开新轮。终态边沿（idle/done/blocked/exited）以 `agent get` 核对当前状态与序号，序号晚于本轮基线且本轮未通知时生成事件并尝试投递；主 agent pane 的 idle/done 边沿合并投递其待投递事件；不认识的 pane 静默退出 0。
- 投递：主 agent 为 idle/done 时 `herdr agent prompt`，以 `agent_prompted` 为成功；working/blocked、pane 不存在或未确认时保留待投递；pane 不存在时 `herdr notification show` 提醒一次。消息首行固定为 `herdr-fleet 自动通知（非用户消息）`，每任务一行 `slug agent状态 phase PR`，末行要求逐个 `fleet status` 核查；超出 prompt 长度时截断并注明剩余数量。
- `fleet inbox` 列出待投递事件，`--all` 含历史，`--json` 结构化；运行它不改变投递状态。
- 收件箱写入加锁，并发 event 不丢不重。旧任务（无 `coordinator_pane`）不产生通知。
- workflow.md 记录新命令、状态目录与事件模型；CONTEXT.md 的 fleet task 条目补充 coordinator pane、收件箱术语。

## Acceptance criteria

- [ ] `new` 后 `TASK.md` 含 `coordinator_pane`，pane 索引含任务 pane 与主 agent pane；任务目录内不出现新文件，`done --dry-run` 预检不受影响
- [ ] working → idle：主 agent idle 时恰好一次 `agent prompt` 到 `coordinator_pane`，首行为固定文本
- [ ] 同一终态事件重复到达只投递一次；提交前（序号不晚于本轮基线）的旧终态不通知
- [ ] 快速完成：本轮 working 事件缺失，或 idle 先于 working 到达时，仍恰好通知一次；之后迟到的 working 与重复 idle 不再通知
- [ ] blocked 与 exited 同样产生事件
- [ ] 主 agent working 时事件保留为待投递；随后主 agent idle 边沿把两个任务的事件合并成一条投递
- [ ] 主 agent pane 不存在：保留待投递并只显示一次 `notification show`
- [ ] `agent prompt` 未返回 `agent_prompted`：保留待投递、记录尝试次数与错误
- [ ] 两个 `fleet event` 并发运行：事件不丢、不重复投递
- [ ] `inbox` / `--all` / `--json` 输出正确，运行后投递状态不变
- [ ] `bind` 改绑后事件投递到新 pane；`done` 后该任务的索引与收件箱记录被清理
- [ ] 不认识的 pane、旧任务无 `coordinator_pane`：退出 0、无 herdr 调用
- [ ] 测试全部经命令行子进程 + fake herdr，状态目录指向测试临时目录；现有测试全部通过

## Blocked by

- #25
