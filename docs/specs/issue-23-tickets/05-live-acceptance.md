## Parent

#23

## What to build

在用户真实的 herdr 上验收自动通知链路，并把结论写进 herdr-fleet README 的验证清单（spec Testing Decisions 接缝 C）。需要在用户的 herdr 里 link 并启用插件，故为人工工单。

## Acceptance criteria

- [ ] `herdr plugin link <skill 目录> --enabled` 后 `fleet doctor` 返回 0
- [ ] 主 agent 用 pi，开一个只做小事的任务后结束本轮；任务完成时主 agent 被自动通知唤醒，并按 SKILL 先 `fleet status` 核查
- [ ] 主 agent 正在工作时任务完成：通知延后到主 agent 空闲后才投递，未插话
- [ ] 任务 agent 进入 blocked 时收到通知
- [ ] 两个任务接连完成时合并为一条通知
- [ ] 关闭任务 tab 时收到 exited 通知
- [ ] `herdr plugin log list` 能看到对应钩子调用；记录实际 herdr 版本与 `min_herdr_version` 是否需要调整
- [ ] 结论（含失败项与证据位置）写入 README 验证清单；验收后按用户意愿保留或 unlink 插件

## Blocked by

- #27
