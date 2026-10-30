# herdr-fleet 自动通知

需要 herdr 0.9.1 或更新版本，以及 Python 3.11 或更新版本。在 herdr 中安装并启用插件（将路径替换为实际 skill 目录）：

```sh
herdr plugin link <skill 目录> --enabled
```

插件 `tc.herdr-fleet` 与 `tc.herdr-pair` 独立，可以同时启用。卸载本地链接：

```sh
herdr plugin unlink tc.herdr-fleet
```

在项目根运行 `python3 <skill 目录>/scripts/fleet.py doctor` 检查版本、插件、主 agent pane 和待投递事件。检查不通过时，主 agent 必须在同一轮内用 `fleet wait` 等待任务结束或阻塞。换控制 pane 后运行 `fleet bind`；用 `fleet inbox` 查看待投递事件。

通知未到时，用 `herdr plugin log list` 检查钩子记录，并用 `fleet status <slug>` 核查任务。插件卸载后其他 fleet 命令仍可使用。

## 真机验证清单

- 开任务后结束主 agent 本轮，确认任务完成时自动唤醒并用 `fleet status` 核查。
- 主 agent 忙时确认通知延后；验证任务阻塞和多个任务完成时合并投递。

以上真机端到端验证由后续工单 #28 执行；自动化测试使用 fake herdr，不修改真实插件注册表。
