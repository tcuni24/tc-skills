# herdr-pair 插件

把 pairctl 的配对状态机接进 Herdr：事件钩子自动续接压缩后的规划者、转发执行者状态边沿，五个动作在插件面板里可用，`pair-status` 弹窗展示同一次 `pairctl status` 的看板字段。

## 安装

```bash
herdr plugin link <仓库根>/skills/herdr-pair --enabled
```

`link` 即安装本地插件；`--enabled` 同时启用。之后用 `herdr plugin enable tc.herdr-pair` / `herdr plugin disable tc.herdr-pair` 管理开关，`herdr plugin unlink tc.herdr-pair` 卸载。

注意：herdr 0.8.0 的清单校验要求动作 id 不含点号（`invalid_plugin_action_id`）。如果 `link` 报这个错，说明 manifest 里的 `[[actions]]` id 仍是 `pair.*` 形式，需要先把点号改成连字符再链接。

## pairctl 路径

所有钩子与动作脚本都不 import pairctl，而是以子进程方式调用它。解析顺序：

1. 环境变量 `PAIRCTL`（指向 pairctl.py 的绝对路径，钩子与手动调用都认它）；
2. 缺省回落到 `<插件根>/scripts/pairctl.py`（相对钩子文件位置推导）。

清单（herdr-plugin.toml）里今天没有 `pairctl_path` 键 —— 想换 pairctl 就设 `PAIRCTL` 环境变量，不要为此去改 toml。

## 真机触发验证

装完插件后不要直接信任路径，先做一次端到端触发：

1. 在配对 state 上跑一次真实压缩：`pairctl compact-self`（规划者是 claude 时发 `/compact`，cursor 时发 `/summarize`）。
2. `herdr plugin log list` 查看这次触发的钩子/动作调用记录（事件、exit code、stdout/stderr）。
3. 确认 `resume_pending.status` 变成 `delivered`，且恢复 prompt 恰好到达规划者一次。

机制选择：只有插件已启用 **且** 规划者 kind 为 `claude` 时 `resume_pending.mechanism` 才是 `plugin`；其余一律 `watcher` —— 非 claude 规划者保持 watcher 投递，属设计而非降级。
