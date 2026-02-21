---
name: gitee-pr
description: Automate Gitee pull request creation from local Git changes, including optional auto-commit and branch push. Use when the user asks to create/submit a Gitee PR, open a PR from current branch to a target base branch, or complete a "commit + push + PR" workflow on gitee.com repositories.
---

# Gitee PR

## Overview

Create a Gitee pull request from the current repository with one command.
Use `scripts/create_gitee_pr.py` to auto-commit (optional), push branch, and call Gitee API.

## Required Inputs

- Provide Gitee token by env var `GITEE_TOKEN` or CLI argument `--token`.
- Ensure Git remote URL points to `gitee.com` or pass `--repo owner/repo`.
- Run inside a Git repository.

## Workflow

1. Inspect current branch and working tree.
2. If working tree is dirty, run with `--auto-commit --commit-message "<msg>"`.
3. Resolve base branch:
   - Prefer explicit `--base`.
   - Otherwise use remote default branch.
4. Push branch to remote unless `--no-push` is set.
5. Create Gitee PR via API and return PR URL.

## Commands

```bash
# Recommended: auto-commit + push + create PR
export GITEE_TOKEN="<token>"
python3 scripts/create_gitee_pr.py \
  --auto-commit \
  --commit-message "feat: add xxx" \
  --base master \
  --title "feat: add xxx" \
  --body "Summary of this change"
```

```bash
# Dry-run: validate params and inspect generated title/body without API call
python3 scripts/create_gitee_pr.py --base master --dry-run
```

```bash
# Skip push (assume branch already pushed)
python3 scripts/create_gitee_pr.py --base master --no-push
```

## Output Expectations

- Print PR URL when successful.
- Print explicit error with actionable next step when failed (missing token, dirty tree without `--auto-commit`, missing base branch, API error).

## Script

- Main script: `scripts/create_gitee_pr.py`
- Run `python3 scripts/create_gitee_pr.py --help` for full options.
