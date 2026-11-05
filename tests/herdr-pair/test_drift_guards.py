#!/usr/bin/env python3
"""Drift guards: anchor lint rules, project_goal storage and checkpoint goals.

R1/R2/R6 from docs/reviews/2026-11-05-drift-postmortem.md: every handoff must
name the overall goal anchor and the deliverable this round serves, the state
keeps one read-only project goal next to the phase goal, and the checkpoint
shows both.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import support
from support import SCRIPT

sys.path.insert(0, str(Path(SCRIPT).resolve().parent))

ANCHOR = "总目标锚点"
DELIVERABLE = "本轮服务的交付物/缺口"
VALID_ANCHOR = "plan.md#goal"
VALID_DELIVERABLE = "交付物 1/3：pairctl lint 新规则"


class DriftGuardTest(support.PairctlCase):
    def raw_handoff(self, name: str, body: str) -> Path:
        """Write a handoff verbatim: write_handoff would inject the anchor fields."""
        path = self.cwd / name
        path.write_text(body, encoding="utf-8")
        return path

    def contract_body(self, anchor: str | None, deliverable: str | None, goal: str = "实现防偏移") -> str:
        lines = ["[轮次] round_id=<unique-id>", "[版本] revision=1"]
        if goal:
            lines.append(f"[目标] {goal}")
        if anchor is not None:
            lines.append(f"[{ANCHOR}] {anchor}".rstrip())
        if deliverable is not None:
            lines.append(f"[{DELIVERABLE}] {deliverable}".rstrip())
        lines.append("[环境] local lightweight test only")
        return "\n".join(lines) + "\n"

    def dispatch(self, command: str, handoff: Path, **kwargs: object):
        if command == "start-round":
            args = ("start-round", "--file", str(handoff), "--executor", "w1:p2",
                    "--scope", "x", "--acceptance", "y")
        else:
            args = ("send-round", "--target", "w1:p2", "--file", str(handoff),
                    "--herdr", str(self.herdr), "--executor", "w1:p2",
                    "--scope", "x", "--acceptance", "y")
        return self.invoke(*args, **kwargs)  # type: ignore[arg-type]

    def test_a_missing_anchor_fields_reject_before_consuming_a_round(self) -> None:
        for command in ("start-round", "send-round"):
            with self.subTest(command=command):
                handoff = self.raw_handoff(
                    f"missing-{command}.md", self.contract_body(None, None)
                )
                proc = self.dispatch(command, handoff)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertEqual(payload["status"], "rejected")
                self.assertEqual(payload["reason"], "handoff_lint")
                rules = [item["rule"] for item in payload["findings"]]
                self.assertIn("goal_anchor_missing", rules)
                self.assertIn("round_deliverable_missing", rules)
                state = self.read_state()
                self.assertEqual(state["phase_round_count"], 0)
                self.assertEqual(state["rounds"], [])
                self.assertIsNone(state["pending_dispatch"])
                self.assertEqual(self.herdr_calls(), [])

    def test_b_placeholder_values_are_named_rules(self) -> None:
        placeholders = ("<总目标>", "TBD", "待定", "N/A", "")
        for value in placeholders:
            with self.subTest(field=ANCHOR, value=value):
                handoff = self.raw_handoff(
                    "anchor-placeholder.md",
                    self.contract_body(value, VALID_DELIVERABLE),
                )
                proc = self.dispatch("start-round", handoff)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                rules = [item["rule"] for item in json.loads(proc.stdout)["findings"]]
                self.assertIn("goal_anchor_placeholder", rules)
                self.assertNotIn("goal_anchor_missing", rules)
                self.assertNotIn("round_deliverable_placeholder", rules)
            with self.subTest(field=DELIVERABLE, value=value):
                handoff = self.raw_handoff(
                    "deliverable-placeholder.md",
                    self.contract_body(VALID_ANCHOR, value),
                )
                proc = self.dispatch("start-round", handoff)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                rules = [item["rule"] for item in json.loads(proc.stdout)["findings"]]
                self.assertIn("round_deliverable_placeholder", rules)
                self.assertNotIn("round_deliverable_missing", rules)
                self.assertNotIn("goal_anchor_placeholder", rules)

    def test_c_valid_anchors_pass_and_skip_lint_bypasses(self) -> None:
        handoff = self.raw_handoff(
            "valid.md", self.contract_body(VALID_ANCHOR, VALID_DELIVERABLE)
        )
        started = self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(started["status"], "round_started")
        self.assertEqual(self.read_state()["rounds"][0]["deliverable"], VALID_DELIVERABLE)

        self.state = self.root / "skip-state"
        self.invoke_ok("init", "--no-context-check")
        skipped = self.invoke_ok(
            "start-round", "--file", str(self.raw_handoff("skip.md", "# Legacy\n[环境] local\n")),
            "--executor", "w1:p2", "--scope", "x", "--acceptance", "y",
            "--skip-lint", "legacy fixture",
        )
        self.assertEqual(skipped["status"], "round_started")

    def test_d_project_goal_is_locked_and_set_goal_records_history(self) -> None:
        self.invoke_ok("init", "--goal", "goal A", "--no-context-check")
        self.assertEqual(self.read_state()["project_goal"], "goal A")
        locked = self.invoke("init", "--goal", "goal B", "--no-context-check")
        self.assertEqual(locked.returncode, 2, locked.stdout + locked.stderr)
        payload = json.loads(locked.stdout)
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "project_goal_locked")
        self.assertEqual(self.read_state()["project_goal"], "goal A")
        again = self.invoke("init", "--goal", "goal A", "--no-context-check")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

        changed = self.invoke_ok(
            "set-goal", "--goal", "goal B", "--reason", "用户调整目标",
        )
        self.assertEqual(changed["status"], "goal_set")
        state = self.read_state()
        self.assertEqual(state["project_goal"], "goal B")
        self.assertEqual(state["goal"], "goal B")
        history = state["goal_history"]
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["old"], "goal A")
        self.assertEqual(entry["new"], "goal B")
        self.assertEqual(entry["reason"], "用户调整目标")
        self.assertTrue(entry["created_at"])
        checkpoint = (self.state / "CHECKPOINT.md").read_text(encoding="utf-8")
        self.assertIn("- Project goal: `goal B`", checkpoint)

    def test_e_legacy_state_adopts_goal_as_project_goal(self) -> None:
        state = self.read_state()
        state["goal"] = "legacy goal"
        state.pop("project_goal", None)
        self.write_state(state)
        self.invoke_ok("note", "--text", "probe")
        self.assertEqual(self.read_state()["project_goal"], "legacy goal")

    def test_f_round_records_the_goal_anchor_and_deliverable(self) -> None:
        handoff = self.raw_handoff(
            "record.md",
            self.contract_body(VALID_ANCHOR, "交付物 2/3：checkpoint 展示", goal="本轮目标"),
        )
        started = self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "x", "--acceptance", "y",
        )
        record = self.read_state()["rounds"][0]
        self.assertEqual(record["round_id"], started["round_id"])
        self.assertEqual(record["goal"], "本轮目标")
        self.assertEqual(record["goal_anchor"], VALID_ANCHOR)
        self.assertEqual(record["deliverable"], "交付物 2/3：checkpoint 展示")

    def test_g_checkpoint_hook_and_prompts_carry_both_goals(self) -> None:
        xdg = self.root / "xdg-drift"
        env = {"XDG_STATE_HOME": str(xdg)}
        self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--session-id", "session-a",
            "--goal", "ship the drift guards", "--no-context-check",
            use_state_dir=False, extra_env=env,
        )
        handoff = self.raw_handoff(
            "checkpoint-round.md",
            self.contract_body(VALID_ANCHOR, "交付物 3/3：文档", goal="写文档"),
        )
        self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "docs", "--acceptance", "doc exists",
            use_state_dir=False, extra_env=env,
        )
        self.invoke_ok("checkpoint", "--reason", "drift test", use_state_dir=False, extra_env=env)
        state_path = next(Path(xdg).rglob("state.json"))
        text = (state_path.parent / "CHECKPOINT.md").read_text(encoding="utf-8")
        self.assertIn("- Project goal: `ship the drift guards`", text)
        self.assertLess(
            text.index("- Project goal:"), text.index("## Current phase rounds")
        )
        row = next(line for line in text.splitlines() if line.startswith("| p01-r001 |"))
        self.assertIn("写文档", row)
        self.assertIn("交付物 3/3：文档", row)

        hook = self.run_hook(
            {"session_id": "session-a", "source": "compact", "cwd": str(self.cwd)}, xdg
        )
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- Project goal: `ship the drift guards`", context)

        from pairctl.context import compact_instructions
        from pairctl.dispatch import compact_continue_prompt

        data = json.loads(state_path.read_text(encoding="utf-8"))
        pp = {"checkpoint": state_path.parent / "CHECKPOINT.md", "root": state_path.parent}
        self.assertIn(
            "Project goal (do not drift from it): ship the drift guards",
            compact_instructions(pp, data, "pi"),
        )
        self.assertIn("Project goal: ship the drift guards", compact_continue_prompt(pp, data))

    def test_h_compact_instructions_use_the_active_round_goal(self) -> None:
        from pairctl.context import compact_instructions

        xdg = self.root / "xdg-round-goal"
        env = {"XDG_STATE_HOME": str(xdg)}
        self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--session-id", "session-a",
            "--goal", "ship the drift guards", "--no-context-check",
            use_state_dir=False, extra_env=env,
        )
        handoff = self.raw_handoff(
            "round-goal.md",
            self.contract_body(VALID_ANCHOR, VALID_DELIVERABLE, goal="写第 2 章"),
        )
        self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "docs", "--acceptance", "chapter exists",
            use_state_dir=False, extra_env=env,
        )
        state_path = next(Path(xdg).rglob("state.json"))
        pp = {"checkpoint": state_path.parent / "CHECKPOINT.md", "root": state_path.parent}
        data = json.loads(state_path.read_text(encoding="utf-8"))

        text = compact_instructions(pp, data, "pi")
        self.assertIn("Round goal: 写第 2 章", text)
        self.assertNotIn("Round goal: ship the drift guards", text)

        # Several active rounds: one "round_id: goal" entry each, joined by "; ".
        two = json.loads(json.dumps(data))
        second = dict(two["rounds"][0])
        second["round_id"] = "p01-r002"
        second["goal"] = "补附录"
        two["rounds"] = [two["rounds"][0], second]
        self.assertIn(
            "Round goal: p01-r001: 写第 2 章; p01-r002: 补附录",
            compact_instructions(pp, two, "pi"),
        )

        # No active round, and an active round without a parsed goal, both show "none".
        idle = json.loads(json.dumps(data))
        idle["rounds"] = []
        self.assertIn("Round goal: none", compact_instructions(pp, idle, "pi"))
        blank = json.loads(json.dumps(data))
        blank["rounds"][0]["goal"] = ""
        self.assertIn("Round goal: none", compact_instructions(pp, blank, "pi"))
