"""Recovery boundaries exercised through public commands and the fake Herdr."""

import datetime as dt
import json
from pathlib import Path
import subprocess

import support


class ContextEdgesTest(support.PairctlCase):

    def test_unknown_pane_preserves_planner_session(self):
        for has_state, recorded_pane in ((True, None), (True, ""), (True, "w1:p1"), (False, "w1:p1")):
            with self.subTest(has_state=has_state, recorded_pane=recorded_pane):
                self.state = self.root / f"state-{has_state}-{recorded_pane}"
                if has_state:
                    self.invoke_ok("init", "--planner-pane", "w1:p1", "--no-context-check")
                if recorded_pane is not None:
                    self.invoke_ok("note-session", "--session-id", "planner", "--kind", "claude",
                                "--source", "startup", "--pane", "w1:p1")
                    if not recorded_pane:
                        record = self.state / "planner-session.json"
                        data = json.loads(record.read_text())
                        data["pane"] = ""  # A legacy record written before pane tracking.
                        record.write_text(json.dumps(data))
                files = [self.state / name for name in ("state.json", "planner-session.json")]
                before = [path.read_bytes() if path.exists() else None for path in files]
                result = self.invoke_ok("note-session", "--session-id", "unrelated", "--kind", "claude",
                                     "--source", "startup", "--pane", "")
                self.assertEqual(result, {"status": "session_ignored", "reason": "pane_unknown"})
                self.assertEqual(before, [path.read_bytes() if path.exists() else None for path in files])

    def test_unknown_pane_can_record_before_init(self):
        self.state = self.root / "before-init"
        for session_id in ("first", "resumed"):
            result = self.invoke_ok("note-session", "--session-id", session_id, "--kind", "claude",
                                 "--source", "startup", "--pane", "")
            self.assertEqual(result["status"], "session_noted")
            record = json.loads((self.state / "planner-session.json").read_text())
            self.assertEqual(record["session_id"], session_id)
            self.assertEqual(record["pane"], "")
        self.assertFalse((self.state / "state.json").exists())

    def test_large_checkpoint_keeps_resume_and_full_path_without_body(self):
        xdg = self.root / "xdg"
        env = {"XDG_STATE_HOME": str(xdg)}
        initialized = self.invoke_ok(
            "init", "--goal", "long-goal-" * 6000, "--session-id", "planner",
            "--no-context-check", use_state_dir=False, extra_env=env,
        )
        self.invoke_ok("note", "--text", "DECISION_BODY_NOT_INJECTED" * 1000,
                    use_state_dir=False, extra_env=env)
        hook = self.run_hook({"cwd": str(self.cwd), "session_id": "planner", "source": "compact"}, xdg)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(context), 16000)
        self.assertIn("## Resume", context)
        self.assertIn("Report path", context)
        self.assertIn("Full checkpoint: " + str(Path(initialized["state_root"]) / "CHECKPOINT.md"), context)
        self.assertNotIn("DECISION_BODY_NOT_INJECTED", context)

    def test_executor_or_unknown_pane_hook_does_not_record_or_rollover_planner(self):
        xdg = self.root / "xdg"
        env = {"XDG_STATE_HOME": str(xdg)}
        initialized = self.invoke_ok("init", "--planner-pane", "w1:p1", "--session-id", "planner",
                                  use_state_dir=False, extra_env=env)
        self.invoke_ok("note-session", "--session-id", "planner", "--kind", "claude",
                    "--source", "startup", "--pane", "w1:p1", use_state_dir=False, extra_env=env)
        self.invoke_ok("compact-self", use_state_dir=False, extra_env=env)
        root = Path(initialized["state_root"])
        before = [(root / filename).read_bytes() for filename in ("state.json", "planner-session.json")]
        for pane in ("w1:p2", ""):
            with self.subTest(pane=pane):
                hook = self.run_hook({"cwd": str(self.cwd), "session_id": "unrelated", "source": "compact"},
                                  xdg, pane=pane)
                self.assertEqual(hook.returncode, 0, hook.stderr)
                self.assertEqual(hook.stdout, "")
                self.assertEqual(before, [(root / filename).read_bytes() for filename in ("state.json", "planner-session.json")])

    def test_nested_unicode_untracked_and_root_fence_fail_before_send(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        nested = self.cwd / "nested"
        nested.mkdir()
        (nested / "未跟踪 file.txt").write_text("preserve me\n", encoding="utf-8")
        self.cwd = nested
        self.state = self.root / "nested-state"
        self.invoke_ok("init", "--no-context-check")
        for body, rule in (
            ("[可以改] 未跟踪 file.txt\n[不许动] 任何未跟踪文件\n", "untracked_conflict"),
            ("[可以改] .\n[不许动] guarded/file.txt\n", "fence_overlap"),
            ("[可以改] src/../guarded\n[可以新建] guarded/new.txt\n", "fence_overlap"),
        ):
            handoff = self.write_handoff("contract.md", body)
            before = (self.state / "state.json").read_bytes()
            rejected = self.invoke("send-round", "--target", "w1:p2", "--file", str(handoff))
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn(rule, [finding["rule"] for finding in json.loads(rejected.stdout)["findings"]])
            self.assertEqual((self.state / "state.json").read_bytes(), before)
            self.assertEqual(self.herdr_calls(), [])

    def test_active_state_and_goal_are_durable_before_compact_request(self):
        self.invoke_ok("init", "--goal", "Preserve the active task", "--no-context-check")
        self.set_kind("claude")
        transcript = self.root / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "type": "assistant", "sessionId": "session-a",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "message": {"role": "assistant", "usage": {
                "input_tokens": 151000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            }},
        }) + "\n", encoding="utf-8")
        self.invoke_ok("note-session", "--session-id", "session-a", "--transcript-path", str(transcript),
                    "--kind", "claude", "--source", "startup", "--pane", "w1:p1")
        source = self.cwd / "input.txt"
        source.write_text("before dispatch\n", encoding="utf-8")
        handoff = self.write_handoff("active.md", "[可以改] input.txt\n[可以新建] report.md\n[报告] report.md\n")
        sent = self.invoke_ok(
            "send-round", "--target", "w1:p2", "--file", str(handoff), "--no-fresh",
            extra_env={
                # Record write needs auto-continue; INTERNAL keeps this test's manual
                # watch the only wake-up source; deadline 0 => due immediately.
                "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
                "PAIRCTL_INTERNAL_WATCHER": "1",
                "PAIRCTL_RESUME_DEADLINE_S": "0",
            },
        )
        self.assertTrue(sent["planner_compact"]["queued"])
        manifest = Path(sent["snapshot"]["manifest"])
        self.assertEqual((manifest.parent / "files" / "input.txt").read_text(), "before dispatch\n")
        self.assertEqual(self.invoke("diff-round", "--round-id", sent["round_id"]).returncode, 0)
        source.write_text("after dispatch\n", encoding="utf-8")
        diff = self.invoke("diff-round", "--round-id", sent["round_id"])
        self.assertEqual(diff.returncode, 1)
        self.assertIn("input.txt", json.loads(diff.stdout)["changed"])
        during_compact = json.loads((self.root / "fake-herdr.state-during-send.json").read_text())
        self.assertIsNone(during_compact["pending_dispatch"])
        self.assertEqual(during_compact["rounds"][0]["status"], "active")
        self.assertIn("Preserve the active task", self.herdr_log()["argv"][4])
        self.assertIn(sent["round_id"], self.herdr_log()["argv"][4])

        # A planner without hook recovery must receive the report and the exact
        # state selection, even though the five-round boundary is not reached.
        report = self.cwd / "report.md"
        report.write_text("executor finished during compaction\n", encoding="utf-8")
        # Issue #9: the record-driven watch delivers after deadline 0 plus an epoch
        # advance, instead of sleeping out the 600 s default deadline.
        recovered = self.invoke_ok("rollover", "--reason", "compact", "--new-session-id", "session-a")
        self.assertFalse(recovered["phase_advanced"])
        watched = self.invoke_ok("watch-compact-continue", extra_env={
            "PAIRCTL_CONTINUE_POLL_S": "0.05",
        })
        self.assertEqual(watched["status"], "continue_prompted")
        prompt = self.herdr_log()["argv"][4]
        self.assertIn("compact_queued", prompt)
        self.assertIn(str(report), prompt)
        self.assertIn("--state-dir " + str(self.state), prompt)
        self.assertIn("--cwd " + str(self.cwd), prompt)
        self.assertEqual(self.invoke_ok("status")["active_rounds"], [sent["round_id"]])
