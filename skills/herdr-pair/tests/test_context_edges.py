"""Recovery boundaries exercised through public commands and the fake Herdr."""

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import unittest

import test_pairctl as support


class ContextEdgesTest(unittest.TestCase):
    def setUp(self):
        self.h = support.PairctlTest()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_large_checkpoint_keeps_resume_and_full_path_without_body(self):
        h = self.h
        xdg = h.root / "xdg"
        env = {"XDG_STATE_HOME": str(xdg)}
        initialized = h.invoke_ok(
            "init", "--goal", "long-goal-" * 6000, "--session-id", "planner",
            "--no-context-check", use_state_dir=False, extra_env=env,
        )
        h.invoke_ok("note", "--text", "DECISION_BODY_NOT_INJECTED" * 1000,
                    use_state_dir=False, extra_env=env)
        hook = h.run_hook({"cwd": str(h.cwd), "session_id": "planner", "source": "compact"}, xdg)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(context), 16000)
        self.assertIn("## Resume", context)
        self.assertIn("Report path", context)
        self.assertIn("Full checkpoint: " + str(Path(initialized["state_root"]) / "CHECKPOINT.md"), context)
        self.assertNotIn("DECISION_BODY_NOT_INJECTED", context)

    def test_executor_hook_does_not_record_or_rollover_planner(self):
        h = self.h
        xdg = h.root / "xdg"
        env = {"XDG_STATE_HOME": str(xdg)}
        initialized = h.invoke_ok("init", "--planner-pane", "w1:p1", "--session-id", "planner",
                                  use_state_dir=False, extra_env=env)
        h.invoke_ok("note-session", "--session-id", "planner", "--kind", "claude",
                    "--source", "startup", "--pane", "w1:p1", use_state_dir=False, extra_env=env)
        h.invoke_ok("compact-self", use_state_dir=False, extra_env=env)
        root = Path(initialized["state_root"])
        before = [(root / filename).read_bytes() for filename in ("state.json", "planner-session.json")]
        hook = subprocess.run(
            ["python3", str(support.HOOK)],
            input=json.dumps({"cwd": str(h.cwd), "session_id": "executor", "source": "compact"}),
            text=True, capture_output=True, check=False,
            env={**os.environ, **env, "HERDR_PANE_ID": "w1:p2", "PAIRCTL_HERDR": str(h.herdr)},
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(before, [(root / filename).read_bytes() for filename in ("state.json", "planner-session.json")])

    def test_nested_unicode_untracked_and_root_fence_fail_before_send(self):
        h = self.h
        subprocess.run(["git", "init", "-q", str(h.cwd)], check=True)
        nested = h.cwd / "nested"
        nested.mkdir()
        (nested / "未跟踪 file.txt").write_text("preserve me\n", encoding="utf-8")
        h.cwd = nested
        h.state = h.root / "nested-state"
        h.invoke_ok("init", "--no-context-check")
        for body, rule in (
            ("[可以改] 未跟踪 file.txt\n[不许动] 任何未跟踪文件\n", "untracked_conflict"),
            ("[可以改] .\n[不许动] guarded/file.txt\n", "fence_overlap"),
            ("[可以改] src/../guarded\n[可以新建] guarded/new.txt\n", "fence_overlap"),
        ):
            handoff = h.write_handoff("contract.md", body)
            before = (h.state / "state.json").read_bytes()
            rejected = h.invoke("send-round", "--target", "w1:p2", "--file", str(handoff))
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn(rule, [finding["rule"] for finding in json.loads(rejected.stdout)["findings"]])
            self.assertEqual((h.state / "state.json").read_bytes(), before)
            self.assertEqual(h.herdr_calls(), [])

    def test_active_state_and_goal_are_durable_before_compact_request(self):
        h = self.h
        h.invoke_ok("init", "--goal", "Preserve the active task", "--no-context-check")
        h.set_kind("claude")
        transcript = h.root / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant", "sessionId": "session-a",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "message": {"role": "assistant", "usage": {
                "input_tokens": 151000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }},
        }) + "\n", encoding="utf-8")
        h.invoke_ok("note-session", "--session-id", "session-a", "--transcript-path", str(transcript),
                    "--kind", "claude", "--source", "startup")
        source = h.cwd / "input.txt"
        source.write_text("before dispatch\n", encoding="utf-8")
        handoff = h.write_handoff("active.md", "[可以改] input.txt\n[可以新建] report.md\n[报告] report.md\n")
        sent = h.invoke_ok("send-round", "--target", "w1:p2", "--file", str(handoff), "--no-fresh")
        self.assertTrue(sent["planner_compact"]["queued"])
        manifest = Path(sent["snapshot"]["manifest"])
        self.assertEqual((manifest.parent / "files" / "input.txt").read_text(), "before dispatch\n")
        self.assertEqual(h.invoke("diff-round", "--round-id", sent["round_id"]).returncode, 0)
        source.write_text("after dispatch\n", encoding="utf-8")
        diff = h.invoke("diff-round", "--round-id", sent["round_id"])
        self.assertEqual(diff.returncode, 1)
        self.assertIn("input.txt", json.loads(diff.stdout)["changed"])
        during_compact = json.loads((h.root / "fake-herdr.state-during-send.json").read_text())
        self.assertIsNone(during_compact["pending_dispatch"])
        self.assertEqual(during_compact["rounds"][0]["status"], "active")
        self.assertIn("Preserve the active task", h.herdr_log()["argv"][4])
        self.assertIn(sent["round_id"], h.herdr_log()["argv"][4])

        # A planner without hook recovery must receive the report and the exact
        # state selection, even though the five-round boundary is not reached.
        report = h.cwd / "report.md"
        report.write_text("executor finished during compaction\n", encoding="utf-8")
        watched = h.invoke_ok("watch-compact-continue", extra_env={
            "PAIRCTL_CONTINUE_MIN_DELAY_S": "0", "PAIRCTL_CONTINUE_IDLE_S": "0",
            "PAIRCTL_CONTINUE_POLL_S": "0.05", "PAIRCTL_CONTINUE_TIMEOUT_S": "1",
        })
        self.assertEqual(watched["status"], "continue_prompted")
        prompt = h.herdr_log()["argv"][4]
        self.assertIn("compact_queued", prompt)
        self.assertIn(str(report), prompt)
        self.assertIn("--state-dir " + str(h.state), prompt)
        self.assertIn("--cwd " + str(h.cwd), prompt)
        recovered = h.invoke_ok("rollover", "--reason", "compact", "--new-session-id", "session-a")
        self.assertFalse(recovered["phase_advanced"])
        self.assertEqual(h.invoke_ok("status")["active_rounds"], [sent["round_id"]])
