---
status: accepted
date: 2026-09-23
---

# herdr-pair 现阶段以 Claude 为首选规划者，插件续跑只对 Claude 生效

herdr-pair 的角色本来不限 agent 类型，但规划者的自动化能力目前只接通了 Claude。所以决定：现阶段推荐 Claude 当规划者；插件续跑（`resume_pending.mechanism = plugin`）只在规划者是 Claude 时启用，其他规划者一律走兜底 watcher。这是接入范围上的取舍：别的 agent 并不一定缺相应能力，只是 skill 还没接。

## 为什么是 Claude

规划者有三项自动化，全部依赖 Claude 专属的接入点（`skills/herdr-pair/scripts/pairctl.py`、`scripts/claude_session_start_hook.py`）：

1. **压缩后自动 rollover，并推进 `compaction_epoch`。** Claude 压缩完会以 `source=compact` 重新触发 SessionStart，钩子借此自动调用 `pairctl rollover --reason compact`。其他规划者只能在压缩指令里被要求自己手动运行 rollover（见 `compact_instructions`）。
2. **向新会话注入恢复上下文。** 同一个 SessionStart 钩子会把检查点的头部、恢复步骤和文件路径注入压缩后的新会话。其他规划者要靠自己读检查点文件。
3. **上下文用量与自动压缩。** `context-usage` 只能读 Claude 的 transcript，非 Claude 规划者一律返回 `unknown`，因此 `send-round` 之后不会自动排队压缩（issue #2 D2/D3）。

插件续跑依赖第 1 项。钩子要在压缩后首次变成空闲时发恢复 prompt，判断条件是当前 `compaction_epoch` 大于排队时记下的值（issue #7 §2–3）。`/compact` 排队后，当前回合一结束窗格会先变一次空闲，那时压缩还没开始。如果 `compaction_epoch` 不会自动前进，钩子就分不清这两次空闲，插件路径只能退化成计时器，而那正是 watcher 已经在做的事。所以非 Claude 规划者直接定为 `watcher`（`probe_resume_mechanism`），这是设计本意，不算降级。

## 放弃的方案

**同时接入 pi 和 opencode。** 两者其实都有"压缩完成"事件：pi 0.87.1 的扩展事件 `session_compact`（另有 `session_before_compact`、`session_compact_failed`），opencode v2.0.14 的插件事件 `session.compacted`（另有压缩前钩子 `experimental.session.compacting`）。暂不接入的原因：

- 每个 agent 都要单独写并维护一份扩展或插件，还要覆盖安装检测和测试。
- 以下两点都没有真机验证过：事件是否在窗格变成空闲**之前**触发（这是 `compaction_epoch` 判断能成立的前提）；能否像 Claude 那样把恢复上下文注入新会话。
- Claude 路径已经端到端验证过（issue #15），目前的使用场景只用它就够了。

## 影响

- 非 Claude 规划者的配对照样可用：续跑由 watcher 在截止时间投递；执行者回报、插件动作和状态看板不区分 agent 类型。
- 执行者不受这个决策影响，可以是任何 agent。

## 何时重新评估

满足以下条件时，重新打开这个决策：

- 要长期用 pi 或 opencode 当规划者，或者 watcher 的延迟已经造成实际问题；并且
- 真机验证过对应事件先于空闲触发，并确认了恢复上下文的注入方式。

届时的改动范围：给 pi 写一个扩展、给 opencode 写一个插件，在收到压缩完成事件时调用 `pairctl rollover --reason compact`（窗格 id 取自 `HERDR_PANE_ID`）；把 `probe_resume_mechanism` 的 `kind != "claude"` 改成白名单，并加上"对应钩子已安装"的检测；按需让 `context-usage` 支持对应 agent 的会话格式。然后把本 ADR 标记为 superseded，并写一份新的 ADR。
