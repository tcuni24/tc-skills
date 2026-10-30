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

## 真机验证清单

在 herdr 0.9.1 上使用 pi 完成以下验证；0.9.5 未验证。验收后插件已 unlink，pi 集成保留安装。自动化测试使用 fake herdr，不修改真实插件注册表。

- 通过：安装 pi 集成后 `herdr integration status` 显示 current，`fleet doctor` 返回 0；`fleet send` 开始确认成功。
- 通过：主 agent 结束本轮后，任务完成时被自动通知唤醒，先用 `fleet status` 核查再汇报。
- 通过：主 agent 忙于 sleep 150 时两个任务先后完成，空闲后合并为一条通知，包含 `live-b done review -` 和 `live-c done review -`。
- 通过：herdr 接受清单及相对路径钩子，`herdr plugin log list` 显示 `pane.agent_status_changed` 钩子 succeeded、退出码 0。
- 已知限制：关闭任务 tab 不触发 `pane.exited`，插件日志没有该事件，不能依靠关 tab 获得退出通知。pane 内进程退出（如 shell exit）时探测到 `pane.exited`；杀掉任务 agent 进程时通知状态为 done。
- 未验证：blocked 通知。pi 提问时 herdr 状态为 done，未触发 blocked。
- 已知限制：任务回报块常不在最近 12 行中，`fleet status` 可能显示未找到回报块；可增大 `--lines`（属 #24 范围）。
