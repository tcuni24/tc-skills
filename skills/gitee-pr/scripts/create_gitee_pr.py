#!/usr/bin/env python3
"""Create a Gitee pull request from the current git repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import PurePosixPath
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BRANCH_NAME_PATTERN = re.compile(r"^[^/\s]+/[a-z0-9]+-[a-z0-9-]+$")
COMMIT_SUBJECT_PATTERN = re.compile(
    r"^(feat|fix|docs|refactor|style|test|chore)(?:\([^)]+\))?!?:[ \t]+\S.*$"
)
CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")
CONFIG_EXTENSIONS = {".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".lock"}
REQUIRED_BODY_HEADINGS = [
    "## 改动了什么？",
    "## 为什么改动？",
    "## 测试结果？",
    "## 注意事项",
]


def fail(message: str, exit_code: int = 1) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        joined = " ".join(cmd)
        stderr = result.stderr.strip() or result.stdout.strip()
        fail(f"Command failed: {joined}\n{stderr}")
    return result


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def ensure_git_repo() -> None:
    probe = git("rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        fail("Current directory is not a git repository.")


def current_branch() -> str:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        fail("Detached HEAD is not supported. Checkout a branch first.")
    return branch


def working_tree_dirty() -> bool:
    return bool(git("status", "--porcelain").stdout.strip())


def auto_commit(commit_message: str) -> None:
    git("add", "-A")
    commit_result = git("commit", "-m", commit_message, check=False)
    if commit_result.returncode != 0:
        stderr = commit_result.stderr.strip() or commit_result.stdout.strip()
        fail(f"Auto commit failed.\n{stderr}")


def parse_gitee_repo_from_remote(remote_url: str) -> Optional[str]:
    patterns = [
        r"^git@gitee\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@gitee\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^https?://(?:[^@/]+@)?gitee\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, remote_url.strip())
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def resolve_repo(remote: str, explicit_repo: Optional[str]) -> str:
    if explicit_repo:
        return explicit_repo
    env_repo = os.environ.get("GITEE_REPO")
    if env_repo:
        return env_repo
    remote_url = git("config", "--get", f"remote.{remote}.url").stdout.strip()
    parsed = parse_gitee_repo_from_remote(remote_url)
    if parsed:
        return parsed
    fail(
        "Cannot infer Gitee repo from remote URL. "
        "Pass --repo owner/repo or set GITEE_REPO."
    )
    return ""


def resolve_base_branch(remote: str, explicit_base: Optional[str]) -> str:
    if explicit_base:
        return explicit_base
    default_head = git("symbolic-ref", f"refs/remotes/{remote}/HEAD", check=False)
    if default_head.returncode == 0:
        value = default_head.stdout.strip()
        if value:
            return value.split("/")[-1]
    for candidate in ("main", "master"):
        probe = git("show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{candidate}", check=False)
        if probe.returncode == 0:
            return candidate
    fail("Cannot detect base branch. Pass --base explicitly.")
    return ""


def range_ref(remote: str, base: str, head: str) -> str:
    return f"{remote}/{base}..{head}"


def diff_range_ref(remote: str, base: str, head: str) -> str:
    return f"{remote}/{base}...{head}"


def validate_branch_name(branch: str) -> None:
    if BRANCH_NAME_PATTERN.match(branch):
        return
    fail(
        "Invalid branch name. Expected format 'member/verb-description', "
        f"for example 'zhangsan/fix-bam-filter'. Received: '{branch}'"
    )


def collect_commit_subjects(remote: str, base: str, head: str) -> list[str]:
    result = git("log", "--pretty=%s", range_ref(remote, base, head), check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        fail(
            "Unable to read commit range for PR validation. "
            f"Range: {range_ref(remote, base, head)}\n{stderr}"
        )
    commits = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not commits:
        fail(
            "No new commits detected against base branch. "
            f"Range '{range_ref(remote, base, head)}' is empty."
        )
    return commits


def validate_commit_subjects(commits: list[str]) -> None:
    invalid = [message for message in commits if not COMMIT_SUBJECT_PATTERN.match(message)]
    if not invalid:
        return
    formatted = "\n".join(f"- {message}" for message in invalid)
    fail(
        "Commit message prefix validation failed. "
        "Use '<type>(<scope>)!: <description>' or '<type>: <description>' "
        "with allowed types: feat|fix|docs|refactor|style|test|chore.\n"
        f"{formatted}"
    )


def list_changed_files(remote: str, base: str, head: str) -> list[str]:
    result = git("diff", "--name-only", diff_range_ref(remote, base, head), check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        fail(
            "Unable to list changed files for single-file rule check. "
            f"Range: {diff_range_ref(remote, base, head)}\n{stderr}"
        )
    return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]


def is_non_core_file(path: str) -> bool:
    normalized = str(PurePosixPath(path)).lower()
    file_name = PurePosixPath(path).name.lower()
    extension = PurePosixPath(path).suffix.lower()

    if normalized.startswith("docs/"):
        return True
    if normalized.startswith(".github/"):
        return True
    if normalized.startswith("config/") or normalized.startswith("configs/"):
        return True
    if file_name.startswith("readme"):
        return True
    if extension == ".md":
        return True
    if extension in CONFIG_EXTENSIONS:
        return True
    return False


def warn_single_file_principle(remote: str, base: str, head: str) -> None:
    changed_files = list_changed_files(remote, base, head)
    core_files = [path for path in changed_files if not is_non_core_file(path)]
    if len(core_files) <= 1:
        return
    print(
        "[WARN] Single-file principle warning: multiple core files changed "
        f"({len(core_files)} files).",
        file=sys.stderr,
    )
    for path in core_files:
        print(f"[WARN] core file: {path}", file=sys.stderr)


def generate_title(explicit_title: Optional[str], commits: list[str], head: str) -> str:
    if explicit_title:
        return explicit_title
    if commits:
        return commits[0]
    latest = git("log", "-1", "--pretty=%s", check=False).stdout.strip()
    if latest:
        return latest
    return f"Update {head}"


def generate_body(explicit_body: Optional[str], commits: list[str]) -> str:
    if explicit_body:
        return explicit_body
    commit_lines = [f"- {message}" for message in commits[:20]]
    lines = [
        "## 改动了什么？",
        *commit_lines,
        "",
        "## 为什么改动？",
        "- 请补充本次改动的业务背景和目标。",
        "",
        "## 测试结果？",
        "- 已检查提交前工作区为干净状态。",
        "- 请补充本地测试命令与结果。",
        "",
        "## 注意事项",
        "- 无",
    ]
    return "\n".join(lines)


def validate_pr_language(title: str, body: str) -> None:
    if not CHINESE_TEXT_PATTERN.search(title):
        fail("PR title must include Chinese text. Please provide a Chinese title.")
    if not CHINESE_TEXT_PATTERN.search(body):
        fail("PR body must include Chinese text. Please provide a Chinese PR description.")


def validate_pr_body_format(body: str) -> None:
    lines = [line.rstrip() for line in body.splitlines()]
    heading_indexes: dict[str, int] = {}
    for heading in REQUIRED_BODY_HEADINGS:
        for index, line in enumerate(lines):
            if line.strip() == heading:
                heading_indexes[heading] = index
                break

    missing = [heading for heading in REQUIRED_BODY_HEADINGS if heading not in heading_indexes]
    if missing:
        formatted = ", ".join(missing)
        fail(f"PR body is missing required sections: {formatted}")

    indexes = [heading_indexes[heading] for heading in REQUIRED_BODY_HEADINGS]
    if indexes != sorted(indexes):
        fail("PR body sections must follow this order: 改动了什么 -> 为什么改动 -> 测试结果 -> 注意事项")

    for i, heading in enumerate(REQUIRED_BODY_HEADINGS):
        start = heading_indexes[heading] + 1
        end = len(lines)
        if i + 1 < len(REQUIRED_BODY_HEADINGS):
            end = heading_indexes[REQUIRED_BODY_HEADINGS[i + 1]]
        section_lines = [line.strip() for line in lines[start:end] if line.strip()]
        if not section_lines:
            fail(f"PR body section '{heading}' cannot be empty.")


def push_branch(remote: str, head: str) -> None:
    push = git("push", "-u", remote, head, check=False)
    if push.returncode != 0:
        stderr = push.stderr.strip() or push.stdout.strip()
        fail(f"Failed to push branch '{head}' to remote '{remote}'.\n{stderr}")


def create_pull_request(
    api_host: str,
    repo: str,
    token: str,
    title: str,
    head: str,
    base: str,
    body: str,
) -> dict:
    owner, name = repo.split("/", 1)
    endpoint = f"https://{api_host}/api/v5/repos/{owner}/{name}/pulls"
    payload = {
        "access_token": token,
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    data = urlencode(payload).encode("utf-8")
    request = Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        fail(f"Gitee API returned HTTP {err.code}: {raw}")
    except URLError as err:
        fail(f"Failed to reach Gitee API: {err.reason}")
    return {}


def validate_repo(repo: str) -> None:
    if repo.count("/") != 1:
        fail("Invalid --repo value. Use owner/repo format.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto commit, push branch, and create Gitee pull request.",
    )
    parser.add_argument("--remote", default="origin", help="Git remote name. Default: origin")
    parser.add_argument("--repo", help="Gitee repo in owner/repo format")
    parser.add_argument("--base", help="Target base branch")
    parser.add_argument("--head", help="Source branch (default: current branch)")
    parser.add_argument("--title", help="Pull request title")
    parser.add_argument("--body", help="Pull request body")
    parser.add_argument("--token", help="Gitee access token (default: $GITEE_TOKEN)")
    parser.add_argument("--api-host", default="gitee.com", help="Gitee host. Default: gitee.com")
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="Commit uncommitted changes automatically before push",
    )
    parser.add_argument(
        "--commit-message",
        default="chore: prepare changes for gitee pr",
        help="Commit message used with --auto-commit",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip git push step",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved values and skip API create",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_git_repo()

    head = args.head or current_branch()
    base = resolve_base_branch(args.remote, args.base)
    if head == base:
        fail("Head branch and base branch are the same. Choose a different --base or --head.")

    if working_tree_dirty():
        if args.auto_commit:
            auto_commit(args.commit_message)
        else:
            fail("Working tree has uncommitted changes. Use --auto-commit or commit manually.")
    if working_tree_dirty():
        fail("Working tree must be clean before creating PR.")

    validate_branch_name(head)

    repo = resolve_repo(args.remote, args.repo)
    validate_repo(repo)
    commits = collect_commit_subjects(args.remote, base, head)
    validate_commit_subjects(commits)
    warn_single_file_principle(args.remote, base, head)

    if not args.no_push:
        push_branch(args.remote, head)

    title = generate_title(args.title, commits, head)
    body = generate_body(args.body, commits)
    validate_pr_language(title, body)
    validate_pr_body_format(body)
    token = args.token or os.environ.get("GITEE_TOKEN", "")

    print(f"[INFO] repo={repo}")
    print(f"[INFO] remote={args.remote}")
    print(f"[INFO] head={head}")
    print(f"[INFO] base={base}")
    print(f"[INFO] commit_count={len(commits)}")
    print(f"[INFO] title={title}")

    if args.dry_run:
        print("[INFO] dry-run enabled; skipping PR creation")
        print("----- PR Body Preview -----")
        print(body)
        return

    if not token:
        fail("Missing token. Set $GITEE_TOKEN or pass --token.")

    result = create_pull_request(args.api_host, repo, token, title, head, base, body)
    pr_url = result.get("html_url")
    pr_number = result.get("number")
    if pr_url:
        print(f"[OK] PR created: {pr_url}")
    else:
        print("[WARN] PR created but URL not found in response.")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if pr_number is not None:
        print(f"[OK] PR number: {pr_number}")


if __name__ == "__main__":
    main()
