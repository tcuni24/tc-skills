## Parent

#23

## What to build

herdr 插件 `tc.herdr-fleet` 把真实的 agent 状态变化接到 `fleet event`，`fleet doctor` 告诉主 agent 自动通知是否可用，SKILL 据此决定能否结束本轮（spec D1、D7、Solution D）。

- skill 目录新增插件清单，订阅 `pane.agent_status_changed` 与 `pane.exited`，`min_herdr_version` 按实际验证的版本填写。
- 钩子读取 `HERDR_PLUGIN_EVENT` / `HERDR_PLUGIN_EVENT_JSON`，兼容 `{event, data}` 包装与扁平载荷，取 `pane_id`、`agent_status`（exited 事件映射为 exited），查 pane 索引得到项目根后调用同目录 fleet 的 `event`；不认识的 pane 不调用 fleet；fleet 失败时显示一次 `notification show`。钩子不导入 fleet、不写状态。
- `fleet doctor`：检查 herdr 版本、插件已安装且启用、活跃任务的 `coordinator_pane` 存在、待投递事件数；全部满足返回 0，否则非零并逐项说明。
- README 写安装（`herdr plugin link <skill 目录> --enabled`）、卸载与 `herdr plugin log list` 排障；与 herdr-pair 插件可同时启用。
- SKILL.md「开任务」：开完后运行 `fleet doctor`；通过则可结束本轮并告诉用户会被自动通知，不通过则必须在同一轮内 `fleet wait` 到任务结束或阻塞，不得只说明不会自动通知就结束本轮（spec D7）。新增"收到自动通知后先 `fleet status` 核查再汇报"。

## Acceptance criteria

- [ ] 清单结构断言：id、两个事件订阅、命令指向钩子
- [ ] 包装载荷与扁平载荷都转成正确的 `fleet event --pane --status` 调用（以子进程运行钩子断言）
- [ ] `pane.exited` 转成 `--status exited`；不认识的 pane 不调用 fleet
- [ ] fleet 失败时钩子显示一次通知并以非零退出
- [ ] `doctor`：插件未安装、已安装未启用、`coordinator_pane` 不存在、有待投递事件各给出对应说明与非零返回码；全部满足返回 0
- [ ] 测试中不 link 本机真实插件注册表（`plugin list` 由 fake herdr 提供）
- [ ] SKILL.md 中 doctor 不通过的分支只有"同一轮内 `fleet wait`"一条，不提供"只说明不会自动通知"的选项
- [ ] README 与 SKILL.md 按上述更新；herdr-fleet 现有测试全部通过

## Blocked by

- #26
