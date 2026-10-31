# config-test-isolation evidence

Base: `origin/main` = `468b1a895591441fb178b4b7ad1efd8228d7c55b`
Fix HEAD: `dc5d61ba6bd2d720dbda1c02b1ed28b8da363d04` (`fix/config-test-isolation`)
Python: `/home/glx/scripts/tc-skills-fleet/worktrees/report-block-37/.venv/bin/python` (Python 3.12.3, pytest 9.2.0, read-only)

## 1. Failure reproduction at base HEAD (real fleet worktree)

Command (cwd = `worktrees/config-test-isolation/tc-skills`, ancestors contain `/home/glx/scripts/tc-skills-fleet/.herdr-fleet.toml`):

    git rev-parse HEAD  # 468b1a895591441fb178b4b7ad1efd8228d7c55b
    <venv>/python -m pytest tests/herdr-fleet/test_fleet.py::test_config_discovery_bases_and_errors -q

Result: EXIT=1 — `fleet ls` returned 0 listing the real fleet tasks
(config-test-isolation, devin-startup-guide, ..., stale-round-38): find_root
resolved `/home/glx/scripts/tc-skills-fleet/.herdr-fleet.toml` via the script
path ancestors despite the config-free cwd.
Full log: `before-worktree-repro.log`

## 2. Full suite after fix — real fleet worktree

    <venv>/python -m pytest tests/herdr-fleet/test_fleet.py -q   # EXIT=0
    133 passed in 156.38s

Full log: `after-worktree-pytest.log` (exit code appended as last line)

## 3. Full suite after fix — config-free exact-HEAD snapshot

    mkdir -p /tmp/config-test-isolation-snapshot
    git archive dc5d61ba6bd2d720dbda1c02b1ed28b8da363d04 | tar -x -C /tmp/config-test-isolation-snapshot
    # ancestor check: no .herdr-fleet.toml in /tmp/config-test-isolation-snapshot or any parent up to /
    cd /tmp/config-test-isolation-snapshot
    <venv>/python -m pytest tests/herdr-fleet/test_fleet.py -q   # EXIT=0
    133 passed in 155.43s

Full log: `after-snapshot-pytest.log` (exit code appended as last line)
Snapshot content: all 155 tracked files `cmp`-verified identical to HEAD.

## 4. Diff hygiene

    git diff --check                      # EXIT=0 (clean)
    git diff --check origin/main...HEAD   # EXIT=0 (clean)
    git diff origin/main...HEAD --stat    # tests/herdr-fleet/test_fleet.py | 21 +- (18+/3-)
