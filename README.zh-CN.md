# tc-skills

[English](README.md) | [简体中文](README.zh-CN.md)

自定义 skills 仓库，组织方式与 [antfu/skills](https://github.com/antfu/skills) 保持一致：

- 每个 skill 一个文件夹，位于 `skills/` 下
- 每个 skill 提供 `SKILL.md`
- 可选子目录：`scripts/`、`references/`、`assets/`、`agents/`

## 安装

使用 [skills CLI](https://github.com/vercel-labs/skills) 从本仓库安装：

```bash
# 交互式安装 — 选择 skill 和目标 agent
npx skills@latest add tcuni24/tc-skills

# 安装指定 skill
npx skills@latest add tcuni24/tc-skills --skill gitee-pr

# 为所有 agent 全局安装全部 skill，无交互
npx skills@latest add tcuni24/tc-skills --all -g
```

常用参数：`-s/--skill <name>`（指定 skill，`*` 为全部）、`-a/--agent <name>`（目标 agent，如 `claude-code`，`*` 为全部）、`-g`（安装到全局 `~/<agent>/skills/` 而非项目目录）、`-y`（跳过交互）。

## Skills

| Skill | 说明 |
| --- | --- |
| `customer-delivery` | 从分析结果构建客户交付包（Excel + 离线 HTML 报告 + plot/），剥离内部路径/列名，按 tc-design-md 风格渲染，并做泄漏审计。 |
| `customer-report-simplify` | 简化面向客户的交付报告：诊断报告为何让非专业读者难以消化，围绕客户的问题重组内容，并用脚本审计结果。 |
| `gitee-pr` | 在当前仓库自动 commit/push 并创建 Gitee PR。 |
| `herdr-pair` | 用两个 Herdr agent 分工协作完成任务——一方规划/审核，另一方执行；覆盖角色契约、可验证的轮次拆分与自包含交接。 |
| `html-pdf-print` | 优化 HTML 报告经浏览器打印为 PDF 的分页：防止内容截断、保留可读分页、定位页脚，并以真实浏览器输出验证。 |
| `nextflow-workflow-skills` | 编写或重构 Nextflow（DSL2 / 25.10+）流水线：process、channel、类型化参数、模块化 subworkflow、生产级配置与 executor profile。 |

## 目录结构

```text
skills/
  gitee-pr/
    SKILL.md
    scripts/
    agents/
```
