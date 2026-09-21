"""Packaging and executable handoff examples for the short skill core."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
PAIRCTL = SKILL / "scripts" / "pairctl.py"


class SkillLayoutTest(unittest.TestCase):
    def test_short_core_and_reference_files(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(text), 15000)
        for keyword in ("context-usage", "/tmp", "check-round", "ack-round", "finish-round --report"):
            self.assertIn(keyword, text)
        for name in ("handoff", "verification", "recovery", "executor-resolution", "common-requests"):
            relative = f"reference/{name}.md"
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
