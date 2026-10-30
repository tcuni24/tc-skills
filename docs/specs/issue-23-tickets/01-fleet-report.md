## Parent

#23

## What to build

任务 agent 结束时输出固定格式的回报块，主 agent 用一条 `fleet status` 就能拿到 PR、测试和证据边界（spec D9、Solution A）。

- `fleet new` 附加给任务 agent 的固定说明里新增回报要求：最终回复输出以 `FLEET-REPORT` 开头、`END FLEET-REPORT` 结尾的块，字段为 结果（完成 / 部分完成 / 阻塞）、PR、测试（每条命令及退出码）、证据边界、需要决策。
- `fleet status` 在 agent 最近输出中查找最后一个完整回报块，文本输出展示在"回报"一节，JSON 新增 `report` 字段；找不到时文本显示"未找到回报块"、JSON 为 `null`。
- SKILL.md 的汇报模板加"证据边界"一列；workflow.md 记录回报块格式与 `status` 的新字段。

## Acceptance criteria

- [ ] `fleet new` 发给任务 agent 的 prompt 包含回报块要求及全部五个字段名（经 fake herdr 记录的 `agent prompt` 文本断言）
- [ ] agent 输出含一个完整回报块时，`fleet status` 文本与 `--json` 都给出该块；含多个时取最后一个
- [ ] 只有开头没有结尾的块不被当作回报块
- [ ] 无回报块时文本显示"未找到回报块"，JSON `report` 为 `null`，返回码不变
- [ ] `--lines 0` 时行为与现有约定一致，并在 workflow.md 写明此时是否仍摘取回报块
- [ ] `ls --json` 与 `status --json` 已有键不变
- [ ] SKILL.md 汇报模板含"证据边界"；workflow.md 记录回报块格式
- [ ] 测试全部经命令行子进程 + fake herdr，herdr-fleet 现有测试全部通过

## Blocked by

None (can start immediately)
