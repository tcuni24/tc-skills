#!/usr/bin/env python3
"""Regression and unit tests for Gitee PR script validation rules."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Add scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from create_gitee_pr import (  # noqa: E402
    BRANCH_NAME_PATTERN,
    CHINESE_TEXT_PATTERN,
    COMMIT_SUBJECT_PATTERN,
    REQUIRED_BODY_HEADINGS,
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


if __name__ == "__main__":
    unittest.main()
