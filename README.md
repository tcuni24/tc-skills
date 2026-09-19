# tc-skills

<p align="center">
  <b>A curated collection of production-grade agent skills for modern AI coding workflows.</b>
</p>

<p align="center">
  <a href="https://github.com/vercel-labs/skills"><img src="https://img.shields.io/badge/standard-skills.json-blue" alt="Skills Standard"></a>
  <a href="https://github.com/tcuni24/tc-skills"><img src="https://img.shields.io/badge/skills-6%20available-green" alt="Skills Count"></a>
  <a href="#supported-agents"><img src="https://img.shields.io/badge/agents-Claude%20%7C%20Cursor%20%7C%20Codex%20%7C%20Pi-orange" alt="Supported Agents"></a>
</p>

<p align="center">
  <a href="README.md">English</a> • <a href="README.zh-CN.md">简体中文</a>
</p>

---

`tc-skills` is a repository of reusable [Agent Skills](https://github.com/vercel-labs/skills) designed for real-world engineering, bioinformatics pipelines, multi-agent orchestration, and customer deliverable generation. Compatible with [Claude Code](https://docs.anthropic.com/claude-code), [Cursor](https://cursor.sh), [Windsurf](https://codeium.com/windsurf), [Cline](https://github.com/cline/cline), [Pi](https://github.com/mariozechner/pi), and other AI coding assistants.

## 📦 Skills Catalog

The skills are organized into focused operational domains:

### 📊 Deliverables & Reporting

| Skill | Description | Typical Triggers |
| --- | --- | --- |
| [`customer-delivery`](skills/customer-delivery/SKILL.md) | Package raw analysis results into clean customer deliverables (Excel workbook + standalone HTML report + plot assets), sanitize internal paths/columns, audit for information leaks, and verify Simplified Chinese typography. | *"生成交付结果"*, *"整理客户交付目录"*, *"做交付报告"* |
| [`customer-report-simplify`](skills/customer-report-simplify/SKILL.md) | Diagnose why technical reports overwhelm non-expert clients, restructure narratives around customer business questions, and run automated audits on readability. | *"报告太复杂"*, *"客户看不懂"*, *"重写成简报"* |
| [`html-pdf-print`](skills/html-pdf-print/SKILL.md) | Optimize HTML reports for browser print-to-PDF: prevent clipped graphs/tables, avoid orphan lines, enforce accurate pagination, and position bottom footers. | *"报告打印被切断"*, *"页脚不在页底"*, *"PDF 分页优化"* |

### 🤖 Multi-Agent & Workflow

| Skill | Description | Typical Triggers |
| --- | --- | --- |
| [`herdr-pair`](skills/herdr-pair/SKILL.md) | Coordinate dual-agent tasks (e.g. Planner/Reviewer + Executor) in Herdr with strict role contracts, verifiable execution rounds, artifact-based handoffs, and concurrency safety. | *"你负责规划让X执行"*, *"协同两个agent"*, *"派给同项目Agent"* |
| [`gitee-pr`](skills/gitee-pr/SKILL.md) | Automate Git commit/push and create Gitee pull requests with team conventions enforcement (branch naming, commit prefixes, clean working trees). | *"创建 Gitee PR"*, *"提交工单 PR"* |

### 🧬 Bioinformatics & Pipelines

| Skill | Description | Typical Triggers |
| --- | --- | --- |
| [`nextflow-workflow-skills`](skills/nextflow-workflow-skills/SKILL.md) | Best-practice authoring and refactoring for Nextflow (DSL2 / 25.10+): typed parameters, modular subworkflows, channel idioms, production-ready configs, and executor profiles. | *"编写 Nextflow 流水线"*, *"重构 nf 脚本"*, *"samplesheet 解析"* |

---

## 🚀 Installation

Install skills into your project or global agent environment using the [`skills` CLI](https://github.com/vercel-labs/skills):

### Quick Start

```bash
# Interactive mode — select skills and target agent(s)
npx skills@latest add tcuni24/tc-skills

# Install a specific skill to current project
npx skills@latest add tcuni24/tc-skills --skill customer-delivery

# Install all skills globally for all detected agents (no prompts)
npx skills@latest add tcuni24/tc-skills --all -g -y
```

### Useful CLI Flags

- `-s, --skill <name>` — Install a specific skill (use `*` for all).
- `-a, --agent <name>` — Specify the target agent (e.g., `claude-code`, `cursor`, `windsurf`, `cline`, or `*` for all).
- `-g, --global` — Install globally (`~/<agent>/skills/`) rather than the local project directory.
- `-y, --yes` — Non-interactive execution, accepting defaults automatically.

---

## 🖥️ Supported Agents

This repository follows the open Agent Skill specification and works seamlessly with:

- **Claude Code** (`~/.claude/skills/` or `.claude/skills/`)
- **Cursor** (`.cursor/skills/`)
- **Windsurf** (`~/.codeium/windsurf/skills/`)
- **Cline / Roo Code** (`~/.cline/skills/`)
- **Pi Coding Agent** (`~/.pi/agent/skills/`)
- Any agent supporting standard `SKILL.md` instruction files

---

## 📁 Repository Layout

The structure follows the [antfu/skills](https://github.com/antfu/skills) pattern:

```text
tc-skills/
├── skills/
│   ├── customer-delivery/
│   │   ├── SKILL.md            # Skill instructions, triggers, and execution protocol
│   │   ├── references/         # Deep reference documents (specs, styles, templates)
│   │   ├── scripts/            # Standalone automation tools (Python / Shell)
│   │   └── tests/              # Regression and unit tests
│   ├── herdr-pair/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── tests/
│   └── ...
├── README.md                   # English documentation
└── README.zh-CN.md             # Chinese documentation
```

### Skill Structure Conventions

Each skill in `skills/<name>/` is a self-contained, modular package:
- `SKILL.md` — Mandatory entrypoint containing YAML frontmatter (`name`, `description`) and comprehensive operational guidance.
- `scripts/` — Deterministic helper scripts invoked directly by agents or CI.
- `references/` — Detailed background, domain models, or edge-case handling rules loaded on-demand.
- `tests/` — Automated test suite verifying the skill scripts and contracts.

---

## 🤝 Contributing

1. Fork this repository.
2. Create a new skill directory under `skills/<skill-name>/` following the layout above.
3. Ensure your `SKILL.md` includes clear `name` and `description` frontmatter metadata.
4. Add automated tests under `skills/<skill-name>/tests/` if your skill includes scripts.
5. Update `README.md` and `README.zh-CN.md` with the new skill's summary.
6. Submit a Pull Request!

---

## 📄 License

Distributed under the [MIT License](LICENSE).

