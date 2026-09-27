"""Packaging and executable handoff examples for the short skill core."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[2] / "skills" / "herdr-pair"
PAIRCTL = SKILL / "scripts" / "pairctl.py"


class SkillLayoutTest(unittest.TestCase):
    def test_short_core_and_reference_files(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(text), 15000)
        for keyword in ("context-usage", "/tmp", "check-round", "ack-round", "finish-round --report"):
            self.assertIn(keyword, text)
        for name in ("handoff", "verification", "recovery", "executor-resolution", "common-requests"):
            relative = f"references/{name}.md"
            self.assertTrue((SKILL / relative).is_file(), relative)
            self.assertIn(relative, text)

    def test_core_handoff_template_can_start_a_round(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        template = re.search(r"```text\n(\[轮次\].*?)\n```", text, re.S)
        self.assertIsNotNone(template)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            cwd = root / "project"
            cwd.mkdir()
            handoff = cwd / "handoff.md"
            handoff.write_text(template.group(1), encoding="utf-8")
            env = dict(os.environ, PAIRCTL_AUTO_COMPACT="0", PAIRCTL_CONTINUE_AFTER_COMPACT="0")
            common = ["--cwd", str(cwd), "--state-dir", str(root / "state")]
            for command in (
                ["init", "--no-context-check"],
                ["start-round", "--file", str(handoff), "--executor", "fake:p2",
                 "--scope", "template", "--acceptance", "example accepted"],
            ):
                result = subprocess.run(
                    ["python3", str(PAIRCTL), *command, *common],
                    text=True, capture_output=True, env=env, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            started = json.loads(result.stdout)
            self.assertEqual(started["status"], "round_started")
            state = json.loads((root / "state" / "state.json").read_text())
            self.assertEqual(state["rounds"][0]["report"], str(cwd / "reports" / "round-report.md"))

    def test_herdr_binary_resolution_lives_in_one_module(self):
        """A6: --herdr / PAIRCTL_HERDR / HERDR_BIN_PATH is resolved exactly once."""
        resolver = SKILL / "herdr_bin.py"
        self.assertTrue(resolver.is_file(), resolver)
        self.assertIn('os.environ.get("HERDR_BIN_PATH")', resolver.read_text(encoding="utf-8"))
        for consumer in (
            SKILL / "hooks" / "action.py",
            SKILL / "hooks" / "notify.py",
            SKILL / "scripts" / "pairctl" / "herdr.py",
        ):
            self.assertIn("resolve_herdr", consumer.read_text(encoding="utf-8"), consumer)
        offenders = sorted(
            str(path.relative_to(SKILL))
            for path in SKILL.rglob("*.py")
            if "tests" not in path.parts
            and path != resolver
            and 'os.environ.get("HERDR_BIN_PATH")' in path.read_text(encoding="utf-8")
        )
        self.assertEqual(offenders, [], "herdr resolution duplicated outside herdr_bin.py")
