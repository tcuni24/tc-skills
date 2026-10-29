---
name: brainstorming-lite
description: 做产品/技术/工作流决策前，发散备选方案、比较取舍、给出推荐。
disable-model-invocation: true
metadata:
  version: "0.1.0"
  derived-from: "obra/superpowers skills/brainstorming (MIT), lightweight rewrite without the workflow chain"
---

在用户确认决定之前，只讨论，不写代码、不改文件。

1. **定框**：一两句话复述目标和约束。能从项目里查到的事实自己查；只把会改变结论的决策性问题交给用户，一次问最关键的一个，并附上你的推荐答案。
2. **发散**：列出真实可选的方案，其中至少一个非显而易见（反向做、重新定义问题、维持现状）。简单问题直接给建议，跳过此步。
3. **收敛**：只挑 2–3 个对这个决定影响最大的视角比较。对推荐方案做一次 pre-mortem，对最强的落选方案做一次 steelman。
4. **推荐**：给出明确选择，说明什么条件变了推荐也会变。高风险时，先给成本最低、可撤回的验证步骤。

用户确认后结束，并输出决策摘要：

- 决定：
- 理由：
- 放弃的方案及原因：
- 待验证的假设：
- 下一步：
