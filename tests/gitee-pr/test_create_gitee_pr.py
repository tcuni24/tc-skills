#!/usr/bin/env python3
"""Regression and unit tests for Gitee PR script validation rules."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add scripts directory to sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "gitee-pr" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import create_gitee_pr as gitee_pr  # noqa: E402

from create_gitee_pr import (  # noqa: E402
    BRANCH_NAME_PATTERN,
    CHINESE_TEXT_PATTERN,
    COMMIT_SUBJECT_PATTERN,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_NETWORK_TIMEOUT_S,
    HTTP_TIMEOUT_ENV_VAR,
    NETWORK_TIMEOUT_ENV_VAR,
    REQUIRED_BODY_HEADINGS,
    git,
    http_timeout_seconds,
    network_timeout_seconds,
    validate_commit_subjects,
)


class GiteePrCommitValidationTests(unittest.TestCase):
    """Tests for Conventional Commit subject regex and validator."""

    def test_standard_prefixes_accepted(self):
        valid_subjects = [
            "feat: add export button",
            "fix: resolve null pointer exception",
            "docs: update installation instructions",
            "refactor: simplify database query",
            "style: adjust indentation",
            "test: add unit test for parser",
            "chore: bump dependency version",
        ]
        for subject in valid_subjects:
            with self.subTest(subject=subject):
                self.assertIsNotNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should accept standard commit: {subject!r}",
                )

    def test_scope_syntax_accepted(self):
        valid_scoped = [
            "feat(customer-delivery): add simplified chinese font audit",
            "fix(parser): handle empty input gracefully",
            "docs(readme): add installation flags",
            "refactor(core/engine): split execution pipeline",
            "test(api): add mock endpoint tests",
            "chore(deps): update urllib3 to 2.2.1",
        ]
        for subject in valid_scoped:
            with self.subTest(subject=subject):
                self.assertIsNotNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should accept scoped commit: {subject!r}",
                )

    def test_breaking_change_marker_accepted(self):
        valid_breaking = [
            "feat!: drop python 3.8 support",
            "fix(auth)!: require token in all requests",
            "refactor(api)!: rename endpoint to v2",
        ]
        for subject in valid_breaking:
            with self.subTest(subject=subject):
                self.assertIsNotNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should accept breaking change commit: {subject!r}",
                )

    def test_single_character_description_accepted(self):
        # Must not reject single-character or single-cjk-character descriptions
        valid_short = [
            "fix: 修",
            "fix: a",
            "chore: 1",
            "feat(ui): 新",
            "fix!: 补",
        ]
        for subject in valid_short:
            with self.subTest(subject=subject):
                self.assertIsNotNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should accept single-char description: {subject!r}",
                )

    def test_multiple_spaces_accepted(self):
        self.assertIsNotNone(COMMIT_SUBJECT_PATTERN.match("feat:   spaced message"))
        self.assertIsNotNone(COMMIT_SUBJECT_PATTERN.match("feat:\tspaced message"))

    def test_empty_or_whitespace_only_description_rejected(self):
        invalid_empty = [
            "feat:",
            "feat: ",
            "feat:    ",
            "feat:\t",
            "feat(scope):",
            "feat(scope): ",
            "feat(scope):   ",
            "feat!:",
            "feat!: ",
        ]
        for subject in invalid_empty:
            with self.subTest(subject=subject):
                self.assertIsNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should reject empty or whitespace description: {subject!r}",
                )

    def test_missing_space_after_colon_rejected(self):
        invalid_no_space = [
            "feat:add feature",
            "fix:修",
            "feat(scope):message",
            "feat!:breaking",
        ]
        for subject in invalid_no_space:
            with self.subTest(subject=subject):
                self.assertIsNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should reject commit missing space after colon: {subject!r}",
                )

    def test_invalid_type_rejected(self):
        invalid_types = [
            "perf: optimize query",
            "build: update maven",
            "ci: add github action",
            "wip: working on it",
            "random commit message",
            "fixup! fix something",
        ]
        for subject in invalid_types:
            with self.subTest(subject=subject):
                self.assertIsNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should reject unsupported type: {subject!r}",
                )

    def test_malformed_scope_rejected(self):
        invalid_scopes = [
            "feat(): empty scope",
            "feat(unclosed: missing paren",
            "feat scope): missing open paren",
        ]
        for subject in invalid_scopes:
            with self.subTest(subject=subject):
                self.assertIsNone(
                    COMMIT_SUBJECT_PATTERN.match(subject),
                    f"Should reject malformed scope: {subject!r}",
                )

    def test_validate_commit_subjects_function(self):
        # Valid list should not raise
        validate_commit_subjects([
            "feat(scope): valid one",
            "fix: 修",
            "docs: update docs",
        ])

        # Invalid list should raise SystemExit
        with self.assertRaises(SystemExit) as ctx:
            validate_commit_subjects([
                "feat: valid",
                "invalid commit message",
            ])
        self.assertEqual(ctx.exception.code, 1)


class GiteePrBranchAndBodyTests(unittest.TestCase):
    """Tests for branch format, Chinese text check, and PR body headings."""

    def test_branch_name_pattern(self):
        valid_branches = [
            "zhangsan/add-qc-script",
            "lisi/fix-bam-filter",
            "wangwu/update-readme",
            "user123/verb-descriptive-name-2",
        ]
        for branch in valid_branches:
            with self.subTest(branch=branch):
                self.assertIsNotNone(BRANCH_NAME_PATTERN.match(branch))

        invalid_branches = [
            "main",
            "dev",
            "feature-branch",
            "user/singleword",
            "user/UPPER-CASE",
            "/invalid-prefix",
        ]
        for branch in invalid_branches:
            with self.subTest(branch=branch):
                self.assertIsNone(BRANCH_NAME_PATTERN.match(branch))

    def test_chinese_text_pattern(self):
        self.assertIsNotNone(CHINESE_TEXT_PATTERN.search("修复过滤逻辑"))
        self.assertIsNotNone(CHINESE_TEXT_PATTERN.search("fix: 修复 bug"))
        self.assertIsNone(CHINESE_TEXT_PATTERN.search("fix: English only message"))

    def test_required_body_headings(self):
        expected_headings = [
            "## 改动了什么？",
            "## 为什么改动？",
            "## 测试结果？",
            "## 注意事项",
        ]
        self.assertEqual(REQUIRED_BODY_HEADINGS, expected_headings)


class GiteePrSubprocessGuardTests(unittest.TestCase):
    """Regression tests for A4: non-interactive guard and timeouts."""

    def _spy_run(self, returncode: int = 0):
        fake = subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout="", stderr="")
        return mock.patch.object(gitee_pr.subprocess, "run", return_value=fake)

    def test_git_calls_disable_terminal_prompt_and_stdin(self):
        with self._spy_run() as spy:
            git("status", "--porcelain")
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_run_does_not_prompt_or_inherit_stdin(self):
        with self._spy_run() as spy:
            gitee_pr.run(["git", "rev-parse", "HEAD"])
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_explicit_env_is_preserved_with_prompt_guard(self):
        with self._spy_run() as spy:
            gitee_pr.run(["git", "status"], env={"MY_FLAG": "1"})
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["MY_FLAG"], "1")

    def test_explicit_env_cannot_reenable_the_prompt_guard(self):
        """A caller must not be able to switch the non-interactive guard back off."""
        with self._spy_run() as spy:
            gitee_pr.run(["git", "status"], env={"GIT_TERMINAL_PROMPT": "1"})
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_environment_inherits_the_prompt_guard_over_the_ambient_value(self):
        with mock.patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "1"}):
            with self._spy_run() as spy:
                gitee_pr.run(["git", "status"])
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_explicit_input_is_still_forwarded(self):
        with self._spy_run() as spy:
            gitee_pr.run(["git", "hash-object", "--stdin"], input="data")
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["input"], "data")
        self.assertNotIn("stdin", kwargs)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_real_subprocess_gets_prompt_guard_and_closed_stdin(self):
        probe = (
            "import os, sys;"
            "print(os.environ.get('GIT_TERMINAL_PROMPT', '<unset>'));"
            "print(sys.stdin.read())"
        )
        result = gitee_pr.run([sys.executable, "-c", probe], check=False)
        stdout_lines = result.stdout.splitlines()
        self.assertEqual(stdout_lines[0], "0")
        self.assertEqual(stdout_lines[1], "")

    def test_real_subprocess_pipes_explicit_input(self):
        probe = "import sys; print(sys.stdin.read())"
        result = gitee_pr.run([sys.executable, "-c", probe], input="piped-payload")
        self.assertEqual(result.stdout.strip(), "piped-payload")

    def test_network_git_command_gets_timeout(self):
        with self._spy_run() as spy:
            git("push", "-u", "origin", "zhangsan/fix-thing")
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["timeout"], DEFAULT_NETWORK_TIMEOUT_S)

    def test_local_git_command_has_no_timeout(self):
        for args in (("status", "--porcelain"), ("rev-parse", "--abbrev-ref", "HEAD")):
            with self.subTest(args=args):
                with self._spy_run() as spy:
                    git(*args)
                _, kwargs = spy.call_args
                self.assertIsNone(kwargs["timeout"])

    def test_ls_remote_and_fetch_are_network_commands(self):
        for args in (("ls-remote", "origin"), ("fetch", "origin")):
            with self.subTest(args=args):
                with self._spy_run() as spy:
                    git(*args)
                _, kwargs = spy.call_args
                self.assertEqual(kwargs["timeout"], DEFAULT_NETWORK_TIMEOUT_S)

    def test_network_timeout_override_from_env(self):
        with mock.patch.dict(os.environ, {NETWORK_TIMEOUT_ENV_VAR: "7"}):
            self.assertEqual(network_timeout_seconds(), 7.0)
            with self._spy_run() as spy:
                git("push", "origin", "head")
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["timeout"], 7.0)

    def test_http_timeout_override_from_env(self):
        with mock.patch.dict(os.environ, {HTTP_TIMEOUT_ENV_VAR: "5"}):
            self.assertEqual(http_timeout_seconds(), 5.0)

    def test_invalid_timeout_env_value_fails_clearly(self):
        with mock.patch.dict(os.environ, {NETWORK_TIMEOUT_ENV_VAR: "not-a-number"}):
            with self.assertRaises(SystemExit) as ctx:
                network_timeout_seconds()
        self.assertEqual(ctx.exception.code, 1)

    def test_git_timeout_surfaces_as_clear_error(self):
        expired = subprocess.TimeoutExpired(cmd=["git", "push"], timeout=DEFAULT_NETWORK_TIMEOUT_S)
        with mock.patch.object(gitee_pr.subprocess, "run", side_effect=expired):
            with self.assertRaises(SystemExit) as ctx:
                git("push", "-u", "origin", "head")
        self.assertEqual(ctx.exception.code, 1)


class GiteePrHttpTimeoutTests(unittest.TestCase):
    """Regression tests for A4: urlopen timeout."""

    def test_urlopen_receives_timeout(self):
        response = mock.MagicMock()
        response.read.return_value = b'{"html_url": "https://gitee.com/o/r/pulls/1"}'
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(gitee_pr, "urlopen", return_value=response) as spy:
            result = gitee_pr.create_pull_request(
                "gitee.com", "owner/repo", "token", "feat: 标题", "head", "main", "body"
            )
        _, kwargs = spy.call_args
        self.assertEqual(kwargs["timeout"], DEFAULT_HTTP_TIMEOUT_S)
        self.assertEqual(result["html_url"], "https://gitee.com/o/r/pulls/1")

    def test_urlopen_timeout_raises_clear_error(self):
        with mock.patch.object(gitee_pr, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(SystemExit) as ctx:
                gitee_pr.create_pull_request(
                    "gitee.com", "owner/repo", "token", "feat: 标题", "head", "main", "body"
                )
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
