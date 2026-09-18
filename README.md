# tc-skills

Custom skills repository, organized in the same style as [antfu/skills](https://github.com/antfu/skills):

- one folder per skill under `skills/`
- each skill provides a `SKILL.md`
- optional subfolders like `scripts/`, `references/`, `assets/`, `agents/`

## Installation

Install skills from this repo with the [skills CLI](https://github.com/vercel-labs/skills):

```bash
# Interactive install — pick skills and target agents
npx skills@latest add tcuni24/tc-skills

# Install a specific skill
npx skills@latest add tcuni24/tc-skills --skill gitee-pr

# Install all skills globally for all agents, no prompts
npx skills@latest add tcuni24/tc-skills --all -g
```

Useful flags: `-s/--skill <name>` (specific skill, `*` for all), `-a/--agent <name>` (target agent, e.g. `claude-code`, `*` for all), `-g` (global `~/<agent>/skills/` instead of the project directory), `-y` (skip prompts).

## Skills

| Skill | Description |
| --- | --- |
| `gitee-pr` | Auto commit/push and create Gitee pull requests from the current repository. |
| `customer-report-simplify` | Simplify customer-facing delivery reports: diagnose why they overwhelm non-experts, restructure around the customer's questions, and audit the result with a script. |
| `customer-delivery` | Build the customer delivery package (Excel + offline HTML report + plot/) from analysis results, strip internal paths/columns, render in the tc-design-md style, and audit for leaks. |

## Layout

```text
skills/
  gitee-pr/
    SKILL.md
    scripts/
    agents/
```
