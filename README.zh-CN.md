# tc-skills

<p align="center">
  <b>面向生产环境的 AI Agent Skills 高质量精选集合</b>
</p>

<p align="center">
  <a href="https://github.com/vercel-labs/skills"><img src="https://img.shields.io/badge/standard-skills.json-blue" alt="Skills Standard"></a>
  <a href="https://github.com/tcuni24/tc-skills"><img src="https://img.shields.io/badge/skills-6%20个可用-green" alt="Skills Count"></a>
  <a href="#支持的-agent"><img src="https://img.shields.io/badge/agents-Claude%20%7C%20Cursor%20%7C%20Codex%20%7C%20Pi-orange" alt="Supported Agents"></a>
</p>

<p align="center">
  <a href="README.md">English</a> • <a href="README.zh-CN.md">简体中文</a>
</p>

---

`tc-skills` 是一套符合开源 [Agent Skills](https://github.com/vercel-labs/skills) 规范的可复用技能库，专门针对日常工程交付、生信数据流管道、多智能体协同调度及对外报告产出场景深度打磨。兼容 [Claude Code](https://docs.anthropic.com/claude-code)、[Cursor](https://cursor.sh)、[Windsurf](https://codeium.com/windsurf)、[Cline](https://github.com/cline/cline)、[Pi](https://github.com/mariozechner/pi) 等主流智能体框架。

## 📦 技能分类索引

所有技能按业务场景划分如下：

### 📊 交付与报告输出 (Deliverables & Reporting)

| 技能名称 | 核心能力说明 | 常见唤起词 (Triggers) |
| --- | --- | --- |
| [`customer-delivery`](skills/customer-delivery/SKILL.md) | 从内部原始分析结果一键构建专业级客户交付包（独立 Excel 工作簿 + 离线自包含 HTML 报告 + 矢量/高清图表目录），剥离内部路径与列名，执行信息泄露审计并严格校验简体中文字体。 | *"生成交付结果"*, *"整理客户交付目录"*, *"做交付报告"*, *"出交付表格"* |
| [`customer-report-simplify`](skills/customer-report-simplify/SKILL.md) | 诊断技术交付报告为何让非专业读者产生认知负担，围绕客户的核心业务诉求重构行文脉络与结论层次，并配套自动化可读性审计脚本。 | *"报告太复杂"*, *"对非专业客户不友好"*, *"客户看不懂"*, *"重写成简报"* |
| [`html-pdf-print`](skills/html-pdf-print/SKILL.md) | 优化 HTML 报告经浏览器打印为 PDF 时的排版与分页：杜绝图表/文字跨页截断、保持表格表头续页、避免孤行孤字，并精准定位末页/每页页脚。 | *"报告打印被切断"*, *"页脚不在页底"*, *"打印 PDF 分页错乱"* |

### 🤖 多智能体协同与研发流 (Multi-Agent & Workflow)

| 技能名称 | 核心能力说明 | 常见唤起词 (Triggers) |
| --- | --- | --- |
| [`herdr-pair`](skills/herdr-pair/SKILL.md) | 在 Herdr 环境下编排双智能体协作任务（如 Planner/Reviewer 规划把关 + Executor 实施编码），包含严格角色契约、可验证的执行轮次切分、自包含产物交接及防并发冲突锁。 | *"你负责规划让X执行"*, *"协同两个agent"*, *"派给同项目Agent"*, *"你把关让它做"* |
| [`gitee-pr`](skills/gitee-pr/SKILL.md) | 自动化本地代码提交、分支推送并一键创建 Gitee Pull Request，强制执行团队规范（分支命名检测、规范 commit 前缀、工作区防脏校验）。 | *"创建 Gitee PR"*, *"提交工单 PR"*, *"提交 PR 到 Gitee"* |

### 🧬 生信分析与流程管道 (Bioinformatics & Pipelines)

| 技能名称 | 核心能力说明 | 常见唤起词 (Triggers) |
| --- | --- | --- |
| [`nextflow-workflow-skills`](skills/nextflow-workflow-skills/SKILL.md) | Nextflow（DSL2 / 25.10+）工程化编写与重构：强类型参数解析、Samplesheet 驱动、模块化 Subworkflow 拆分、生产级 Nextflow 配置模式与执行器 profile 优化。 | *"编写 Nextflow 流水线"*, *"重构 nf 脚本"*, *"DSL2 模块化封装"* |

---

## 🚀 安装指南

推荐使用官方 [`skills` CLI](https://github.com/vercel-labs/skills) 安装技能到本地项目或全局 Agent 目录：

### 快速安装

```bash
# 交互式选择 — 自主勾选需要的 skill 与目标 agent
npx skills@latest add tcuni24/tc-skills

# 安装单个指定 skill 到当前项目
npx skills@latest add tcuni24/tc-skills --skill customer-delivery

# 一键将本仓库所有 skills 全局安装到所有检测到的 agent（无提示）
npx skills@latest add tcuni24/tc-skills --all -g -y
```

### 常用参数说明

- `-s, --skill <name>` — 安装指定名称的 skill（支持 `*` 通配全部）。
- `-a, --agent <name>` — 明确安装目标 Agent（例如 `claude-code`、`cursor`、`windsurf`、`cline`，默认交互选择或通配 `*`）。
- `-g, --global` — 全局安装模式（存入 `~/<agent>/skills/`，对所有工程生效，无需逐个仓库配置）。
- `-y, --yes` — 跳过所有交互确认，自动接受默认选项。

---

## 🖥️ 支持的 Agent 环境

本仓库严格遵循开放式 Agent Skill 标准，原生适配支持如下开发工具与智能体：

- **Claude Code**（`~/.claude/skills/` 或项目根目录 `.claude/skills/`）
- **Cursor**（项目根目录 `.cursor/skills/`）
- **Windsurf**（`~/.codeium/windsurf/skills/`）
- **Cline / Roo Code**（`~/.cline/skills/`）
- **Pi Coding Agent**（`~/.pi/agent/skills/`）
- 任何支持基于 `SKILL.md` 指令触发的 AI 编程助手

---

## 📁 目录规范与设计结构

本仓库组织方式与 [antfu/skills](https://github.com/antfu/skills) 一脉相承：

```text
tc-skills/
├── skills/
│   ├── customer-delivery/
│   │   ├── SKILL.md            # 核心指导文件：触发时机、操作规范与执行契约
│   │   ├── references/         # 深度参考文档（规约、配色规范、样例等）
│   │   ├── scripts/            # 可独立执行的自动化辅助工具（Python / Shell）
│   │   └── tests/              # 自动化回归测试与断言用例
│   ├── herdr-pair/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── tests/
│   └── ...
├── README.md                   # 英文说明文档
└── README.zh-CN.md             # 中文说明文档
```

### 模块设计约定

位于 `skills/<name>/` 下的每个技能均为自包含模块：
- `SKILL.md`：核心入口，开头包含 YAML frontmatter（`name`、`description`），正文明确操作协议、检查清单与异常防护。
- `scripts/`：具备确定性逻辑的 CLI 脚本，供智能体在受控环境下直接调用。
- `references/`：按需加载的长篇技术标准、样式规约或领域模型定义，避免污染上下文主窗口。
- `tests/`：保障技能脚本鲁棒性的单元与回归测试套件。

---

## 🤝 参与贡献

1. Fork 本仓库。
2. 在 `skills/<skill-name>/` 下创建新的技能目录，遵循上述规范。
3. 编写 `SKILL.md` 并包含准确的 frontmatter（`name` 与 `description`）。
4. 若包含辅助脚本，请在 `tests/` 下补充自动化测试用例。
5. 更新 `README.md` 与 `README.zh-CN.md` 中的技能表格与触发词。
6. 提交 Pull Request！

---

## 📄 开源许可

本项目基于 [MIT License](LICENSE) 协议开源。
