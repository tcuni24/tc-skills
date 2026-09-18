#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pairctl.py"
HOOK = Path(__file__).resolve().parents[1] / "scripts" / "claude_session_start_hook.py"
FAKE_HERDR = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log = Path(sys.argv[0] + ".log.json")
mode_path = Path(sys.argv[0] + ".mode")
log.write_text(json.dumps({"argv": sys.argv}, ensure_ascii=False), encoding="utf-8")
with Path(sys.argv[0] + ".calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv}, ensure_ascii=False) + "\n")
state_json = os.environ.get("PAIRCTL_STATE_JSON", "")
if state_json and Path(state_json).is_file():
    Path(sys.argv[0] + ".state-during-send.json").write_text(
        Path(state_json).read_text(encoding="utf-8"), encoding="utf-8"
    )


def setting(name, default):
    path = Path(sys.argv[0] + "." + name)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else default


sub = sys.argv[1:3]
if sub == ["agent", "get"]:
    print(json.dumps({
        "id": "cli:agent:get",
        "result": {"agent": {
            "agent": setting("kind", "pi"),
            "agent_status": setting("status", "idle"),
            "pane_id": sys.argv[3],
        }},
    }))
    raise SystemExit(0)
if sub == ["agent", "read"]:
    screen_path = Path(sys.argv[0] + ".screen")
    print(screen_path.read_text(encoding="utf-8") if screen_path.is_file() else "✓ New session started\n")
    raise SystemExit(0)
if sub == ["agent", "prompt"] and len(sys.argv) > 4 and sys.argv[4].startswith("/"):
    # Slash commands (/new, /clear, /compact) are always delivered by the fake.
    print(json.dumps({
        "id": "cli:agent:prompt",
        "result": {"type": "agent_prompted", "pane_id": sys.argv[3]},
    }))
    raise SystemExit(0)
mode = mode_path.read_text(encoding="utf-8").strip() if mode_path.is_file() else "ok"
if mode == "ok":
    print(json.dumps({
        "id": "cli:agent:prompt",
        "result": {"type": "agent_prompted", "pane_id": sys.argv[3] if len(sys.argv) > 3 else ""},
    }))
    raise SystemExit(0)
if mode == "fail":
    print(json.dumps({"error": {"code": "agent_not_found"}}))
    raise SystemExit(1)
if mode == "sleep":
    import time
    time.sleep(2)
    raise SystemExit(0)
print(json.dumps({
    "id": "cli:agent:prompt",
    "result": {"type": "agent_prompt_stalled"},
}))
raise SystemExit(0)
'''


class PairctlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.tmp.name)
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.state = self.root / "state"
        self.herdr = self.root / "fake-herdr"
        self.herdr.write_text(FAKE_HERDR, encoding="utf-8")
        os.chmod(self.herdr, 0o755)
        self.invoke_ok("init", "--planner-pane", "w1:p1", "--session-id", "session-a")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def invoke(
        self,
        *args: str,
        use_state_dir: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(SCRIPT), *args, "--cwd", str(self.cwd)]
        if use_state_dir:
            cmd += ["--state-dir", str(self.state)]
        env = os.environ.copy()
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL_AUTO_COMPACT", None)
        env["PAIRCTL_CONTINUE_AFTER_COMPACT"] = "0"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def invoke_ok(self, *args: str, **kwargs: object) -> dict:
        proc = self.invoke(*args, **kwargs)  # type: ignore[arg-type]
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return json.loads(proc.stdout)

    def set_herdr_mode(self, mode: str) -> None:
        (self.root / "fake-herdr.mode").write_text(mode + "\n", encoding="utf-8")

    def herdr_log(self) -> dict:
        return json.loads((self.root / "fake-herdr.log.json").read_text(encoding="utf-8"))

    def herdr_calls(self) -> list[list[str]]:
        path = self.root / "fake-herdr.calls.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)["argv"][1:]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def clear_calls(self) -> None:
        path = self.root / "fake-herdr.calls.jsonl"
        if path.exists():
            path.unlink()

    def set_kind(self, kind: str) -> None:
        (self.root / "fake-herdr.kind").write_text(kind + "\n", encoding="utf-8")

    def set_status(self, status: str) -> None:
        (self.root / "fake-herdr.status").write_text(status + "\n", encoding="utf-8")

    def set_screen(self, text: str) -> None:
        (self.root / "fake-herdr.screen").write_text(text, encoding="utf-8")

    def write_handoff(self, name: str, body: str) -> Path:
        path = self.cwd / name
        path.write_text(body, encoding="utf-8")
        return path

    def start_finish(self, n: int) -> tuple[str, subprocess.CompletedProcess[str]]:
        started = self.invoke_ok(
            "start-round",
            "--executor", f"w1:p{n + 1}",
            "--scope", f"file-{n}",
            "--acceptance", f"test-{n} exit 0",
        )
        proc = self.invoke(
            "finish-round", "--round-id", started["round_id"],
            "--status", "accepted", "--artifacts", f"artifact-{n}",
        )
        return started["round_id"], proc

    def send_round(self, n: int, body: str | None = None) -> dict:
        handoff = self.write_handoff(
            f"handoff-{n}.md",
            body if body is not None else (
                f"[轮次] round_id=<unique-id>\n"
                f"work {n} and `docs/example.md`\n"
                f"keep this round_id=example in the body\n"
            ),
        )
        return self.invoke_ok(
            "send-round",
            "--target", f"w1:p{n + 1}",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
            "--executor", f"w1:p{n + 1}",
            "--scope", f"file-{n}",
            "--acceptance", f"test-{n} exit 0",
        )

    def send_finish(self, n: int) -> tuple[str, subprocess.CompletedProcess[str]]:
        sent = self.send_round(n)
        proc = self.invoke(
            "finish-round", "--round-id", sent["round_id"],
            "--status", "accepted", "--artifacts", f"artifact-{n}",
        )
        return sent["round_id"], proc

    def test_round_checkpoint_and_rollover_gate(self) -> None:
        for n in range(1, 5):
            _, proc = self.start_finish(n)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        _, fifth = self.start_finish(5)
        self.assertEqual(fifth.returncode, 20)
        self.assertEqual(json.loads(fifth.stdout)["trigger"], "SESSION_ROLLOVER_REQUIRED")
        checkpoint = self.state / "CHECKPOINT.md"
        self.assertTrue(checkpoint.is_file())
        self.assertIn("Rounds in phase: `5`", checkpoint.read_text())
        blocked = self.invoke(
            "start-round", "--executor", "w1:p9", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(blocked.returncode, 20)
        rolled = self.invoke_ok("rollover", "--new-session-id", "session-b")
        self.assertEqual(rolled["phase"], 2)
        self.assertFalse(rolled["session_switched"])
        status = self.invoke_ok("status")
        self.assertEqual(status["phase_round_count"], 0)

    def test_job_ledger_and_checkpoint_carry_active_job(self) -> None:
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p2", "--scope", "pipeline",
            "--acceptance", "marker exists",
        )
        self.invoke_ok(
            "job-add",
            "--round-id", started["round_id"],
            "--queue", "io", "--job-id", "177", "--label", "gzip-gate",
            "--submitter", "w1:p2", "--command", "gzip -t inputs/*.gz",
            "--log", "/project/log/177", "--expected-artifacts", "PASS",
            "--completion-assertion", "exit=0 and PASS exists", "--owner", "w1:p1",
        )
        ledger = (self.state / "jobs.tsv").read_text()
        self.assertIn("round_id\tphase\tqueue\tjob_id", ledger)
        self.assertIn("submitter\tcommand\tlog\texpected_artifacts", ledger)
        self.assertIn("completion_assertion\tstate\tcancel_retry_owner", ledger)
        self.assertIn("\tio\t177\tgzip-gate\t", ledger)
        self.invoke_ok("checkpoint", "--reason", "test")
        checkpoint = (self.state / "CHECKPOINT.md").read_text()
        self.assertIn('"job_id": "177"', checkpoint)
        self.invoke_ok("job-update", "--queue", "io", "--job-id", "177", "--state", "succeeded")
        self.invoke_ok("checkpoint", "--reason", "terminal")
        self.assertIn("None.", (self.state / "CHECKPOINT.md").read_text())

    def test_duplicate_job_and_active_round_are_refused(self) -> None:
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p2", "--scope", "a", "--acceptance", "b",
        )
        active = self.invoke(
            "start-round", "--executor", "w1:p3", "--scope", "c", "--acceptance", "d",
        )
        self.assertEqual(active.returncode, 2)
        args = (
            "job-add", "--round-id", started["round_id"], "--queue", "gpu",
            "--job-id", "5", "--label", "pilot", "--submitter", "w1:p2",
            "--command", "run", "--log", "log", "--expected-artifacts", "out",
            "--completion-assertion", "ok", "--owner", "w1:p1",
        )
        self.invoke_ok(*args)
        duplicate = self.invoke(*args)
        self.assertEqual(duplicate.returncode, 2)

    def test_send_round_injects_id_via_argv_and_records_after_prompted(self) -> None:
        sent = self.send_round(1)
        self.assertEqual(sent["round_id"], "p01-r001")
        self.assertTrue(sent["agent_prompted"])
        argv = self.herdr_log()["argv"]
        self.assertEqual(argv[1:4], ["agent", "prompt", "w1:p2"])
        self.assertIn("round_id=p01-r001", argv[4])
        self.assertIn("`docs/example.md`", argv[4])
        self.assertIn("round_id=example", argv[4])
        self.assertNotIn("<unique-id>", argv[4])
        self.assertEqual(argv[4].count("round_id=p01-r001"), 1)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase_round_count"], 1)
        self.assertEqual(state["rounds"][0]["status"], "active")

    def test_failed_send_does_not_consume_a_round(self) -> None:
        self.set_herdr_mode("fail")
        handoff = self.write_handoff("handoff-fail.md", "[轮次] round_id=<unique-id>\nfail\n")
        proc = self.invoke(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
            "--scope", "x",
            "--acceptance", "y",
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["round_consumed"])
        self.assertEqual(payload["status"], "send_failed")
        status = self.invoke_ok("status")
        self.assertEqual(status["phase_round_count"], 0)
        self.set_herdr_mode("ok")
        sent = self.send_round(1)
        self.assertEqual(sent["round_id"], "p01-r001")

    def test_stalled_prompt_does_not_consume_a_round(self) -> None:
        self.set_herdr_mode("stalled")
        handoff = self.write_handoff("handoff-stalled.md", "no id yet")
        proc = self.invoke(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PENDING_DISPATCH_UNRESOLVED")
        self.assertFalse(payload["round_consumed"])
        status = self.invoke("status")
        self.assertEqual(status.returncode, 2)
        self.assertEqual(self.read_state()["phase_round_count"], 0)
        self.assertIsNotNone(self.read_state()["pending_dispatch"])

    def test_third_send_writes_checkpoint_fifth_blocks_next_send(self) -> None:
        for n in range(1, 3):
            _, proc = self.send_finish(n)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        third = self.send_round(3)
        self.assertEqual(third["round_id"], "p01-r003")
        self.assertIn("CHECKPOINT.md", third["checkpoint"])
        text = Path(third["checkpoint"]).read_text(encoding="utf-8")
        self.assertIn("Rounds in phase: `3`", text)
        self.assertIn("automatic three-round checkpoint", text)
        self.invoke(
            "finish-round", "--round-id", third["round_id"],
            "--status", "accepted", "--artifacts", "a3",
        )
        for n in range(4, 6):
            _, proc = self.send_finish(n)
            if n < 5:
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            else:
                self.assertEqual(proc.returncode, 20)
        log_path = self.root / "fake-herdr.log.json"
        before_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        blocked = self.invoke(
            "send-round",
            "--target", "w1:p9",
            "--file", str(self.write_handoff("blocked.md", "should not send")),
            "--herdr", str(self.herdr),
        )
        self.assertEqual(blocked.returncode, 20)
        payload = json.loads(blocked.stdout)
        self.assertEqual(payload["trigger"], "SESSION_ROLLOVER_REQUIRED")
        self.assertFalse(payload["session_switched"])
        self.assertIn("compact", payload["guidance"])
        # The fifth finish already queued the planner's compaction; the blocked send
        # neither re-queues it nor touches herdr at all.
        self.assertFalse(payload["planner_compact"]["queued"])
        self.assertIn("already queued", payload["planner_compact"]["reason"])
        after_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        self.assertEqual(before_log, after_log)
        self.assertNotIn("should not send", after_log)

    def test_nonterminal_job_survives_rollover(self) -> None:
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p2", "--scope", "pipeline",
            "--acceptance", "marker exists",
        )
        self.invoke_ok(
            "job-add",
            "--round-id", started["round_id"],
            "--queue", "io", "--job-id", "99", "--label", "long",
            "--submitter", "w1:p2", "--command", "sleep 9",
            "--log", "/project/log/99", "--expected-artifacts", "done",
            "--completion-assertion", "log has DONE", "--owner", "w1:p1",
        )
        self.invoke(
            "finish-round", "--round-id", started["round_id"],
            "--status", "accepted", "--artifacts", "queued",
        )
        for n in range(2, 6):
            self.start_finish(n)
        rolled = self.invoke_ok("rollover", "--new-session-id", "session-b")
        self.assertEqual(rolled["carried_nonterminal_jobs"], 1)
        ledger = (self.state / "jobs.tsv").read_text()
        self.assertIn("\tio\t99\tlong\t", ledger)
        self.assertIn("sleep 9", ledger)
        checkpoint = (self.state / "CHECKPOINT.md").read_text()
        self.assertIn('"job_id": "99"', checkpoint)

    def test_state_files_are_private_and_follow_xdg(self) -> None:
        for name in ("state.json", "jobs.tsv", "state.lock"):
            mode = stat.S_IMODE((self.state / name).stat().st_mode)
            self.assertEqual(mode, 0o600, name)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        xdg = self.root / "xdg-state"
        env = {"XDG_STATE_HOME": str(xdg)}
        init = self.invoke(
            "init", "--planner-pane", "w1:p1",
            use_state_dir=False,
            extra_env=env,
        )
        self.assertEqual(init.returncode, 0, init.stderr + init.stdout)
        payload = json.loads(init.stdout)
        expected_key = hashlib.sha256(str(self.cwd.resolve()).encode()).hexdigest()[:20]
        expected = xdg / "herdr-pair" / expected_key
        self.assertEqual(payload["state_root"], str(expected))
        self.assertTrue((expected / "state.json").is_file())
        self.assertEqual(stat.S_IMODE((expected / "state.json").stat().st_mode), 0o600)

    def test_handoff_without_round_id_is_prefixed(self) -> None:
        sent = self.send_round(1, body="do the work and leave `backticks` intact\n")
        message = self.herdr_log()["argv"][4]
        self.assertTrue(message.startswith("[轮次] round_id=p01-r001\n"))
        self.assertIn("`backticks`", message)
        self.assertEqual(sent["round_id"], "p01-r001")

    def read_state(self) -> dict:
        return json.loads((self.state / "state.json").read_text(encoding="utf-8"))

    def write_state(self, data: dict) -> None:
        (self.state / "state.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def test_a_pending_not_counted_during_send_and_cleared_on_clean_fail(self) -> None:
        self.set_herdr_mode("fail")
        handoff = self.write_handoff("handoff-a.md", "[轮次] round_id=<unique-id>\nfail path\n")
        proc = self.invoke(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
            "--scope", "x",
            "--acceptance", "y",
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "send_failed")
        self.assertFalse(payload["round_consumed"])
        mid = json.loads((self.root / "fake-herdr.state-during-send.json").read_text(encoding="utf-8"))
        pending = mid["pending_dispatch"]
        self.assertEqual(pending["status"], "uncertain")
        self.assertFalse(pending["counted"])
        self.assertEqual(mid["phase_round_count"], 0)
        self.assertEqual(mid["rounds"], [])
        after = self.read_state()
        self.assertIsNone(after["pending_dispatch"])
        self.assertEqual(after["phase_round_count"], 0)
        self.assertEqual(after["rounds"], [])

    def test_a_leftover_pending_fail_closed_does_not_resend(self) -> None:
        data = self.read_state()
        data["pending_dispatch"] = {
            "status": "uncertain",
            "counted": False,
            "round_id": "p01-r001",
            "target": "w1:p2",
            "executor": "w1:p2",
            "scope": "crash",
            "acceptance": "n/a",
            "handoff": "/missing.md",
            "phase_round_count": 0,
            "created_at": "2026-09-04T00:00:00+00:00",
        }
        self.write_state(data)
        log_path = self.root / "fake-herdr.log.json"
        if log_path.exists():
            log_path.unlink()
        blocked = self.invoke(
            "send-round",
            "--target", "w1:p9",
            "--file", str(self.write_handoff("pending.md", "[轮次] round_id=<unique-id>\nno\n")),
            "--herdr", str(self.herdr),
        )
        self.assertEqual(blocked.returncode, 2)
        payload = json.loads(blocked.stdout)
        self.assertEqual(payload["status"], "PENDING_DISPATCH_UNRESOLVED")
        self.assertFalse(payload["resent"])
        self.assertFalse(payload["counted"])
        self.assertFalse(payload["round_consumed"])
        self.assertIn("w1:p2", payload["guidance"])
        self.assertIn("p01-r001", payload["guidance"])
        self.assertFalse(log_path.exists())
        started = self.invoke(
            "start-round", "--executor", "w1:p3", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(started.returncode, 2)
        self.assertEqual(self.read_state()["phase_round_count"], 0)
        self.assertIsNotNone(self.read_state()["pending_dispatch"])

    def test_b_body_round_id_examples_are_not_rewritten(self) -> None:
        body = (
            "[轮次] round_id=<unique-id>\n"
            "report with round_id=<unique-id> in the body\n"
            "and also round_id=p99-r999 as an example\n"
        )
        sent = self.send_round(1, body=body)
        message = self.herdr_log()["argv"][4]
        lines = message.splitlines()
        self.assertEqual(lines[0], "[轮次] round_id=p01-r001")
        self.assertIn("round_id=<unique-id>", lines[1])
        self.assertIn("round_id=p99-r999", lines[2])
        self.assertEqual(message.count("round_id=p01-r001"), 1)
        self.assertEqual(sent["round_id"], "p01-r001")

    def test_c_init_reset_rejected_and_existing_state_not_cleared(self) -> None:
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p2", "--scope", "keep",
            "--acceptance", "keep",
        )
        self.invoke_ok(
            "job-add",
            "--round-id", started["round_id"],
            "--queue", "io", "--job-id", "7", "--label", "keep-job",
            "--submitter", "w1:p2", "--command", "true",
            "--log", "log", "--expected-artifacts", "out",
            "--completion-assertion", "ok", "--owner", "w1:p1",
        )
        help_proc = subprocess.run(
            ["python3", str(SCRIPT), "init", "--help"],
            text=True, capture_output=True, check=False,
        )
        self.assertNotIn("--reset", help_proc.stdout)
        reset = self.invoke("init", "--reset")
        self.assertEqual(reset.returncode, 2)
        self.invoke_ok("init", "--planner-pane", "w1:p8")
        data = self.read_state()
        self.assertEqual(len(data["rounds"]), 1)
        self.assertEqual(len(data["jobs"]), 1)
        self.assertEqual(data["rounds"][0]["round_id"], started["round_id"])
        self.assertEqual(data["jobs"][0]["job_id"], "7")
        self.assertEqual(data["planner_pane"], "w1:p8")

    def test_d_missing_or_stale_ledger_is_rebuilt_from_state_not_tsv(self) -> None:
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p2", "--scope", "pipeline",
            "--acceptance", "marker exists",
        )
        self.invoke_ok(
            "job-add",
            "--round-id", started["round_id"],
            "--queue", "io", "--job-id", "42", "--label", "repair",
            "--submitter", "w1:p2", "--command", "true",
            "--log", "/project/log/42", "--expected-artifacts", "ok",
            "--completion-assertion", "exit=0", "--owner", "w1:p1",
        )
        ledger = self.state / "jobs.tsv"
        ledger.unlink()
        self.invoke_ok("status")
        restored = ledger.read_text(encoding="utf-8")
        self.assertIn("\tio\t42\trepair\t", restored)
        ghost = (
            "round_id\tphase\tqueue\tjob_id\tlabel\tsubmitter\tcommand\tlog\t"
            "expected_artifacts\tcompletion_assertion\tstate\tcancel_retry_owner\t"
            "created_at\tupdated_at\n"
            "ghost\t1\tio\t999\tghost\tw1:p2\techo\tlog\tout\tok\tsubmitted\tw1:p1\tx\ty\n"
        )
        ledger.write_text(ghost, encoding="utf-8")
        before_jobs = self.read_state()["jobs"]
        self.invoke_ok("status")
        data = self.read_state()
        self.assertEqual(data["jobs"], before_jobs)
        self.assertEqual(len(data["jobs"]), 1)
        repaired = ledger.read_text(encoding="utf-8")
        self.assertNotIn("ghost", repaired)
        self.assertNotIn("\t999\t", repaired)
        self.assertIn("\tio\t42\trepair\t", repaired)

    def test_e_rollover_rejects_bad_session_ids_without_mutating_state(self) -> None:
        for n in range(1, 6):
            self.start_finish(n)
        before = self.read_state()
        for bad in ("", "<id>", "session-a", "   "):
            proc = self.invoke("rollover", "--new-session-id", bad)
            self.assertEqual(proc.returncode, 2, bad)
            self.assertIn("PAIRCTL_ERROR", proc.stderr)
        after = self.read_state()
        self.assertEqual(after["phase"], before["phase"])
        self.assertEqual(after["session_id"], "session-a")
        self.assertTrue(after["rollover_required"])
        rolled = self.invoke_ok("rollover", "--new-session-id", "session-b")
        self.assertEqual(rolled["phase"], 2)

    def test_resolve_pending_requires_explicit_delivery_outcome(self) -> None:
        self.set_herdr_mode("stalled")
        handoff = self.write_handoff("resolve.md", "ambiguous send\n")
        sent = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(handoff),
            "--herdr", str(self.herdr),
        )
        self.assertEqual(sent.returncode, 2)
        unresolved = self.read_state()["pending_dispatch"]
        self.assertEqual(unresolved["round_id"], "p01-r001")
        delivered = self.invoke_ok("resolve-pending", "--outcome", "delivered")
        self.assertTrue(delivered["round_consumed"])
        state = self.read_state()
        self.assertIsNone(state["pending_dispatch"])
        self.assertEqual(state["phase_round_count"], 1)
        self.assertEqual(state["rounds"][0]["round_id"], "p01-r001")

    def test_resolve_pending_not_delivered_keeps_round_number_free(self) -> None:
        self.set_herdr_mode("stalled")
        handoff = self.write_handoff("retry.md", "ambiguous send\n")
        sent = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(handoff),
            "--herdr", str(self.herdr),
        )
        self.assertEqual(sent.returncode, 2)
        resolved = self.invoke_ok("resolve-pending", "--outcome", "not-delivered")
        self.assertFalse(resolved["round_consumed"])
        self.set_herdr_mode("ok")
        retried = self.send_round(1)
        self.assertEqual(retried["round_id"], "p01-r001")

    def test_send_timeout_remains_pending_and_is_not_resent(self) -> None:
        self.set_herdr_mode("sleep")
        handoff = self.write_handoff("timeout.md", "slow send\n")
        timed = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(handoff),
            "--herdr", str(self.herdr), "--send-timeout", "0.05",
        )
        self.assertEqual(timed.returncode, 2)
        payload = json.loads(timed.stdout)
        self.assertEqual(payload["status"], "PENDING_DISPATCH_UNRESOLVED")
        self.assertEqual(payload["error"], "herdr_timeout")
        self.assertIsNotNone(self.read_state()["pending_dispatch"])

    # --- fresh executor context per round -------------------------------------------

    def test_fresh_clears_executor_before_handoff_and_records_it(self) -> None:
        self.clear_calls()
        sent = self.send_round(1)
        self.assertEqual(sent["fresh"]["kind"], "pi")
        self.assertEqual(sent["fresh"]["command"], "/new")
        self.assertEqual(sent["fresh"]["verified"], "marker")
        calls = self.herdr_calls()
        self.assertEqual(calls[0][:3], ["agent", "get", "w1:p2"])
        self.assertEqual(calls[1][:4], ["agent", "prompt", "w1:p2", "/new"])
        self.assertEqual(calls[2][:3], ["agent", "read", "w1:p2"])
        self.assertEqual(calls[-1][:3], ["agent", "prompt", "w1:p2"])
        self.assertIn("round_id=p01-r001", calls[-1][3])
        # The clear command is a separate argv element, never glued to the handoff text.
        self.assertFalse(calls[-1][3].startswith("/new"))
        state = self.read_state()
        self.assertEqual(state["rounds"][0]["fresh"]["command"], "/new")

    def test_fresh_uses_kind_specific_command(self) -> None:
        self.set_kind("claude")
        self.set_screen(" ▐▛███▛█   Claude Code v2.1.263\n❯ /clear\n")
        sent = self.send_round(1)
        self.assertEqual(sent["fresh"]["command"], "/clear")
        self.assertEqual(sent["fresh"]["verified"], "marker")

    def test_fresh_refuses_working_executor_without_consuming_round(self) -> None:
        self.set_status("working")
        self.clear_calls()
        handoff = self.write_handoff("busy.md", "[轮次] round_id=<unique-id>\nbusy\n")
        proc = self.invoke("send-round", "--target", "w1:p2", "--file", str(handoff))
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "fresh_failed")
        self.assertFalse(payload["round_consumed"])
        self.assertIn("working", payload["error"])
        calls = self.herdr_calls()
        self.assertEqual([c[:2] for c in calls], [["agent", "get"]])
        state = self.read_state()
        self.assertIsNone(state["pending_dispatch"])
        self.assertEqual(state["phase_round_count"], 0)
        self.set_status("idle")
        self.assertEqual(self.send_round(1)["round_id"], "p01-r001")

    def test_fresh_fails_closed_when_screen_does_not_confirm(self) -> None:
        self.set_screen("\n".join(f"old transcript line {n}" for n in range(30)) + "\n")
        self.clear_calls()
        handoff = self.write_handoff("stale.md", "[轮次] round_id=<unique-id>\nstale\n")
        proc = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(handoff), "--fresh-timeout", "0.6",
        )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "fresh_failed")
        self.assertIn("did not confirm", payload["error"])
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual([p[3] for p in prompts], ["/new"])
        self.assertEqual(self.read_state()["phase_round_count"], 0)

    def test_fresh_unknown_kind_needs_override_and_heuristic_verifies(self) -> None:
        self.set_kind("cursor")
        handoff = self.write_handoff("cursor.md", "[轮次] round_id=<unique-id>\ncursor\n")
        proc = self.invoke("send-round", "--target", "w1:p2", "--file", str(handoff))
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertIn("no fresh-session command", payload["error"])
        self.assertIn("dispatch configuration problem", payload["guidance"])
        self.assertIn("AI: Out of credits", payload["guidance"])
        self.set_screen("┌───┐\n│ > │\n└───┘\n")
        sent = self.invoke_ok(
            "send-round", "--target", "w1:p2", "--file", str(handoff),
            "--fresh-command", "/new-chat",
        )
        self.assertEqual(sent["fresh"]["command"], "/new-chat")
        self.assertEqual(sent["fresh"]["verified"], "heuristic")

    def test_no_fresh_sends_straight_into_existing_context(self) -> None:
        self.set_status("working")  # would be refused by --fresh; --no-fresh never asks
        self.clear_calls()
        handoff = self.write_handoff("raw.md", "[轮次] round_id=<unique-id>\nraw\n")
        sent = self.invoke_ok("send-round", "--target", "w1:p2", "--file", str(handoff), "--no-fresh")
        self.assertIsNone(sent["fresh"])
        self.assertEqual([c[:2] for c in self.herdr_calls()], [["agent", "prompt"]])

    # --- planner compaction at the phase boundary -------------------------------------

    def test_fifth_finish_queues_planner_compact_then_rollover_by_compact_reason(self) -> None:
        self.set_kind("claude")
        self.set_screen("Claude Code v2\n")
        for n in range(1, 5):
            _, proc = self.send_finish(n)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.clear_calls()
        _, fifth = self.send_finish(5)
        self.assertEqual(fifth.returncode, 20)
        payload = json.loads(fifth.stdout)
        compact = payload["planner_compact"]
        self.assertTrue(compact["queued"], compact)
        self.assertEqual(compact["pane"], "w1:p1")
        self.assertEqual(compact["command"], "/compact")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"]
        self.assertEqual(len(prompts), 1)
        self.assertTrue(prompts[0][3].startswith("/compact "))
        self.assertIn("CHECKPOINT.md", prompts[0][3])
        self.assertIn("SessionStart hook", prompts[0][3])
        self.assertNotIn("\n", prompts[0][3])
        state = self.read_state()
        self.assertEqual(state["compact_queued"]["phase"], 1)
        checkpoint = (self.state / "CHECKPOINT.md").read_text(encoding="utf-8")
        self.assertIn("`/compact` was queued on planner pane `w1:p1`", checkpoint)
        # A second boundary command in the same phase is not re-queued.
        self.clear_calls()
        blocked = self.invoke("start-round", "--executor", "w1:p9", "--scope", "x", "--acceptance", "y")
        self.assertEqual(blocked.returncode, 20)
        self.assertFalse(json.loads(blocked.stdout)["planner_compact"]["queued"])
        self.assertEqual(self.herdr_calls(), [])
        # Same session id is fine for an in-place compaction; the epoch records it.
        same = self.invoke("rollover", "--new-session-id", "session-a")
        self.assertEqual(same.returncode, 2)
        rolled = self.invoke_ok("rollover", "--reason", "compact", "--new-session-id", "session-a")
        self.assertEqual(rolled["phase"], 2)
        self.assertEqual(rolled["compaction_epoch"], 1)
        state = self.read_state()
        self.assertIsNone(state["compact_queued"])
        self.assertFalse(state["rollover_required"])
        self.assertEqual(self.invoke_ok("status")["phase_round_count"], 0)

    def test_planner_compact_bare_command_for_kind_without_instructions(self) -> None:
        self.set_kind("kimi")
        self.set_screen("Started a new session\n")
        for n in range(1, 6):
            self.send_finish(n)
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"]
        self.assertEqual([p[3] for p in prompts], ["/compact"])

    def test_cursor_planner_compact_queues_summarize(self) -> None:
        # Observed 2026-09-07: finish-round of a Cursor planner returned
        # "no compact command known for agent kind 'cursor'". Cursor's in-place
        # compact analogue is /summarize, not /compact.
        self.set_kind("cursor")
        for n in range(1, 5):
            _, proc = self.start_finish(n)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        _, proc = self.start_finish(5)
        self.assertEqual(proc.returncode, 20)
        payload = json.loads(proc.stdout)
        compact = payload["planner_compact"]
        self.assertTrue(compact["queued"], compact)
        self.assertEqual(compact["command"], "/summarize")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"]
        self.assertEqual([p[3] for p in prompts], ["/summarize"])

    def test_watch_compact_continue_prompts_planner_after_idle(self) -> None:
        self.set_kind("cursor")
        for n in range(1, 6):
            self.start_finish(n)
        self.clear_calls()
        self.set_status("idle")
        prompted = self.invoke_ok(
            "watch-compact-continue",
            extra_env={
                "PAIRCTL_CONTINUE_MIN_DELAY_S": "0",
                "PAIRCTL_CONTINUE_IDLE_S": "0",
                "PAIRCTL_CONTINUE_POLL_S": "0.05",
            },
        )
        self.assertEqual(prompted["status"], "continue_prompted")
        self.assertEqual(prompted["pane"], "w1:p1")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"]
        self.assertEqual(len(prompts), 1)
        self.assertIn("herdr-pair auto-continue after compaction", prompts[0][3])
        self.assertIn("Do not wait for the user", prompts[0][3])
        self.assertIn("rollover --reason compact", prompts[0][3])
        self.assertIn("CHECKPOINT.md", prompts[0][3])

    def test_fifth_finish_spawns_continue_watcher_when_enabled(self) -> None:
        self.set_kind("cursor")
        for n in range(1, 5):
            self.start_finish(n)
        started = self.invoke_ok(
            "start-round", "--executor", "w1:p6", "--scope", "s", "--acceptance", "a",
        )
        self.clear_calls()
        fifth = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted",
            extra_env={
                "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
                "PAIRCTL_CONTINUE_MIN_DELAY_S": "0",
                "PAIRCTL_CONTINUE_IDLE_S": "0",
                "PAIRCTL_CONTINUE_POLL_S": "0.05",
            },
        )
        self.assertEqual(fifth.returncode, 20)
        payload = json.loads(fifth.stdout)
        self.assertTrue(payload["planner_compact"]["queued"])
        self.assertTrue(payload["continue_after_compact"]["spawned"], payload)
        deadline = time.time() + 5
        continue_prompts = []
        while time.time() < deadline:
            continue_prompts = [
                c for c in self.herdr_calls()
                if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"
                and "auto-continue after compaction" in c[3]
            ]
            if continue_prompts:
                break
            time.sleep(0.05)
        self.assertEqual(len(continue_prompts), 1, self.herdr_calls())

    def test_auto_compact_can_be_disabled_by_env_and_init(self) -> None:
        for n in range(1, 5):
            self.start_finish(n)
        started = self.invoke_ok("start-round", "--executor", "w1:p6", "--scope", "s", "--acceptance", "a")
        self.clear_calls()
        fifth = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted",
            extra_env={"PAIRCTL_AUTO_COMPACT": "0"},
        )
        self.assertEqual(fifth.returncode, 20)
        compact = json.loads(fifth.stdout)["planner_compact"]
        self.assertFalse(compact["queued"])
        self.assertIn("disabled", compact["reason"])
        self.assertEqual(self.herdr_calls(), [])
        self.invoke_ok("init", "--no-auto-compact")
        self.assertFalse(self.read_state()["auto_compact"])
        self.assertFalse(json.loads(self.invoke("status").stdout)["auto_compact"])
        # compact-self is the explicit override and still works.
        forced = self.invoke_ok("compact-self", "--mode", "clear")
        self.assertTrue(forced["queued"])
        self.assertEqual(forced["command"], "/new")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual([p[2:4] for p in prompts], [["w1:p1", "/new"]])

    def test_compact_self_reports_unknown_planner_kind(self) -> None:
        self.set_kind("agy")
        proc = self.invoke("compact-self")
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "planner_compact_not_queued")
        self.assertIn("agy", payload["reason"])

    # --- Claude Code SessionStart hook ------------------------------------------------

    def run_hook(self, payload: dict, xdg: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(xdg)
        env["PAIRCTL_HERDR"] = str(self.herdr)
        return subprocess.run(
            ["python3", str(HOOK)], input=json.dumps(payload), text=True,
            capture_output=True, check=False, env=env,
        )

    def test_hook_records_rollover_after_compact_and_injects_checkpoint(self) -> None:
        xdg = self.root / "xdg-hook"
        env = {"XDG_STATE_HOME": str(xdg)}
        self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--session-id", "sess-1",
            use_state_dir=False, extra_env=env,
        )
        for n in range(1, 6):
            started = self.invoke_ok(
                "start-round", "--executor", "w1:p2", "--scope", "s", "--acceptance", "a",
                use_state_dir=False, extra_env=env,
            )
            self.invoke(
                "finish-round", "--round-id", started["round_id"], "--status", "accepted",
                use_state_dir=False, extra_env=env,
            )
        status = self.invoke("status", use_state_dir=False, extra_env=env)
        self.assertEqual(status.returncode, 20)
        hook = self.run_hook(
            {"session_id": "sess-1", "source": "compact", "cwd": str(self.cwd),
             "hook_event_name": "SessionStart"},
            xdg,
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        out = json.loads(hook.stdout)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Rollover recorded automatically", context)
        self.assertIn("# Herdr Pair Checkpoint", context)
        after = json.loads(self.invoke("status", use_state_dir=False, extra_env=env).stdout)
        self.assertEqual(after["phase"], 2)
        self.assertEqual(after["phase_round_count"], 0)
        self.assertEqual(after["compaction_epoch"], 1)
        # /clear yields a new session id: recorded as reason=new.
        for n in range(1, 6):
            started = self.invoke_ok(
                "start-round", "--executor", "w1:p2", "--scope", "s", "--acceptance", "a",
                use_state_dir=False, extra_env=env,
            )
            self.invoke(
                "finish-round", "--round-id", started["round_id"], "--status", "accepted",
                use_state_dir=False, extra_env=env,
            )
        hook = self.run_hook({"session_id": "sess-2", "source": "clear", "cwd": str(self.cwd)}, xdg)
        self.assertIn("reason=new", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
        after = json.loads(self.invoke("status", use_state_dir=False, extra_env=env).stdout)
        self.assertEqual(after["phase"], 3)
        self.assertEqual(after["session_id"], "sess-2")

    def test_hook_is_silent_without_pairing_state_or_on_plain_startup(self) -> None:
        xdg = self.root / "xdg-empty"
        hook = self.run_hook({"session_id": "x", "source": "compact", "cwd": str(self.cwd)}, xdg)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout.strip(), "")
        env = {"XDG_STATE_HOME": str(xdg)}
        self.invoke_ok("init", "--planner-pane", "w1:p1", use_state_dir=False, extra_env=env)
        hook = self.run_hook({"session_id": "x", "source": "startup", "cwd": str(self.cwd)}, xdg)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout.strip(), "")
        # A mid-phase compaction still gets the checkpoint context, without a rollover.
        hook = self.run_hook({"session_id": "x", "source": "compact", "cwd": str(self.cwd)}, xdg)
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("mid-phase", context)
        self.assertEqual(json.loads(self.invoke("status", use_state_dir=False, extra_env=env).stdout)["phase"], 1)
        # Garbage on stdin never breaks session start.
        env2 = os.environ.copy()
        env2["XDG_STATE_HOME"] = str(xdg)
        broken = subprocess.run(
            ["python3", str(HOOK)], input="not json", text=True, capture_output=True, check=False, env=env2,
        )
        self.assertEqual(broken.returncode, 0)


    def test_cycle1_send_round_initializes_revision_contract_and_statuses(self) -> None:
        handoff = self.write_handoff("task1.md", "# Task 1\nDo precision work\n")
        sent = self.invoke_ok(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
            "--executor", "w1:p2",
            "--scope", "src/models.py",
            "--acceptance", "pytest tests/test_models.py exit 0",
        )
        self.assertEqual(sent["round_id"], "p01-r001")
        self.assertEqual(sent["revision"], 1)
        self.assertEqual(sent["dispatch_status"], "delivered")
        self.assertEqual(sent["work_status"], "pending_acceptance")
        self.assertTrue(sent["contract_hash"])

        status = self.invoke_ok("status")
        self.assertEqual(status["active_rounds"], ["p01-r001"])
        self.assertEqual(status["dispatch_status"], "delivered")
        self.assertEqual(status["work_status"], "pending_acceptance")
        self.assertEqual(status["current_revision"], 1)
        self.assertEqual(status["contract_hash"], sent["contract_hash"])
        self.assertTrue(Path(status["contract_path"]).is_file())

        # Verify contract snapshot on disk
        contract_bytes = Path(status["contract_path"]).read_bytes()
        expected_hash = hashlib.sha256(contract_bytes).hexdigest()
        self.assertEqual(sent["contract_hash"], expected_hash)
        # Contract file mode is 0600
        self.assertEqual(stat.S_IMODE(Path(status["contract_path"]).stat().st_mode), 0o600)

    def test_cycle1_start_round_and_no_fresh_and_resolve_pending(self) -> None:
        # Test start-round
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-a",
            "--acceptance", "test-a",
        )
        self.assertEqual(started["revision"], 1)
        self.assertEqual(started["work_status"], "pending_acceptance")
        self.assertEqual(started["dispatch_status"], "delivered")
        self.assertTrue(started["contract_hash"])
        self.invoke(
            "finish-round", "--round-id", started["round_id"],
            "--status", "accepted",
        )

        # Test no-fresh send-round
        handoff = self.write_handoff("task2.md", "Round 2 no fresh\n")
        sent = self.invoke_ok(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff),
            "--herdr", str(self.herdr),
            "--no-fresh",
            "--scope", "scope-b",
            "--acceptance", "test-b",
        )
        self.assertEqual(sent["revision"], 1)
        self.assertEqual(sent["work_status"], "pending_acceptance")
        self.assertEqual(sent["dispatch_status"], "delivered")
        self.invoke(
            "finish-round", "--round-id", sent["round_id"],
            "--status", "accepted",
        )

        # Test resolve-pending delivered binds contract and statuses
        self.set_herdr_mode("stalled")
        handoff3 = self.write_handoff("task3.md", "Round 3 stalled\n")
        stalled = self.invoke(
            "send-round",
            "--target", "w1:p2",
            "--file", str(handoff3),
            "--herdr", str(self.herdr),
            "--scope", "scope-c",
            "--acceptance", "test-c",
        )
        self.assertEqual(stalled.returncode, 2)
        resolved = self.invoke_ok("resolve-pending", "--outcome", "delivered")
        self.assertEqual(resolved["round_id"], "p01-r003")
        self.assertEqual(resolved["revision"], 1)
        self.assertEqual(resolved["work_status"], "pending_acceptance")
        self.assertEqual(resolved["dispatch_status"], "delivered")
        self.assertTrue(resolved["contract_hash"])

    def test_cycle1_contract_corrupted_or_missing(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-corrupt",
            "--acceptance", "test-corrupt",
        )
        status1 = self.invoke_ok("status")
        self.assertEqual(status1["contract_status"], "valid")

        # Tamper with the contract file
        contract_file = Path(status1["contract_path"])
        contract_file.write_text("tampered content", encoding="utf-8")
        status2 = self.invoke_ok("status")
        self.assertEqual(status2["contract_status"], "contract_corrupted")

        # Delete the contract file
        contract_file.unlink()
        status3 = self.invoke_ok("status")
        self.assertEqual(status3["contract_status"], "contract_missing")

    def test_cycle2_ack_round_accept_and_start_transitions(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-ack",
            "--acceptance", "test-ack",
        )
        round_id = started["round_id"]
        rev = started["revision"]
        c_hash = started["contract_hash"]

        # 1. Direct start from pending_acceptance is forbidden
        bad_start = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "start",
        )
        self.assertEqual(bad_start.returncode, 2)
        bad_payload = json.loads(bad_start.stdout)
        self.assertEqual(bad_payload["reason"], "not_accepted")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # 2. ack-round accept succeeds -> accepted
        ack_accept = self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(ack_accept["work_status"], "accepted")
        self.assertEqual(self.invoke_ok("status")["work_status"], "accepted")

        # 3. Duplicate accept replay is idempotent
        dup_accept = self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(dup_accept["work_status"], "accepted")

        # 4. ack-round start succeeds -> running
        ack_start = self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "start",
        )
        self.assertEqual(ack_start["work_status"], "running")
        self.assertEqual(self.invoke_ok("status")["work_status"], "running")

        # 5. Duplicate start replay is idempotent
        dup_start = self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "start",
        )
        self.assertEqual(dup_start["work_status"], "running")

        # 6. Replaying accept while running is idempotent and does NOT downgrade status to accepted
        replay_accept = self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-ack",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(replay_accept["work_status"], "running")
        self.assertEqual(self.invoke_ok("status")["work_status"], "running")

    def test_cycle2_ack_round_validation_and_failures_do_not_modify_state(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-val",
            "--acceptance", "test-val",
        )
        round_id = started["round_id"]
        rev = started["revision"]
        c_hash = started["contract_hash"]

        # Pane mismatch
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p99",
            "--scope", "scope-val",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "pane_mismatch")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # Revision mismatch
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", "99",
            "--pane", "w1:p2",
            "--scope", "scope-val",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "revision_mismatch")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # Scope mismatch
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "wrong-scope",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "scope_mismatch")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # Hash mismatch
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-val",
            "--contract-hash", "deadbeef" * 8,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "hash_mismatch")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # Corrupted contract file
        c_path = Path(started["contract_path"])
        c_path.write_text("corrupted", encoding="utf-8")
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-val",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "contract_corrupted")
        self.assertEqual(self.invoke_ok("status")["work_status"], "pending_acceptance")

        # Finish round and verify terminal round cannot be acknowledged
        c_path.write_text((
            f"[轮次] round_id={round_id}\n"
            f"revision=1\n"
            f"scope=scope-val\n"
            f"acceptance=test-val\n"
        ), encoding="utf-8")
        self.invoke_ok(
            "finish-round",
            "--round-id", round_id,
            "--status", "accepted",
        )
        proc = self.invoke(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-val",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["reason"], "round_terminal")

    def test_cycle3_check_round_allows_only_when_running_and_matching(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-chk",
            "--acceptance", "test-chk",
        )
        round_id = started["round_id"]
        rev = started["revision"]
        c_hash = started["contract_hash"]

        # In pending_acceptance -> check-round is rejected with not_accepted
        chk1 = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", str(rev),
        )
        self.assertEqual(chk1.returncode, 2)
        self.assertEqual(json.loads(chk1.stdout)["reason"], "not_accepted")
        self.assertFalse(json.loads(chk1.stdout)["allowed"])

        # Accept the round -> in accepted -> check-round is rejected with not_running
        self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-chk",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        chk2 = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", str(rev),
        )
        self.assertEqual(chk2.returncode, 2)
        self.assertEqual(json.loads(chk2.stdout)["reason"], "not_running")
        self.assertFalse(json.loads(chk2.stdout)["allowed"])

        # Start execution -> in running -> check-round is allowed
        self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-chk",
            "--contract-hash", c_hash,
            "--action", "start",
        )
        chk3 = self.invoke_ok(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", str(rev),
        )
        self.assertTrue(chk3["allowed"])
        self.assertEqual(chk3["reason"], "ok")
        self.assertEqual(chk3["work_status"], "running")
        self.assertEqual(chk3["revision"], 1)

    def test_cycle3_check_round_rejects_omitted_revision_wrong_pane_and_contract_drift(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--executor", "w1:p2",
            "--scope", "scope-chk2",
            "--acceptance", "test-chk2",
        )
        round_id = started["round_id"]
        rev = started["revision"]
        c_hash = started["contract_hash"]

        self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-chk2",
            "--contract-hash", c_hash,
            "--action", "accept",
        )
        self.invoke_ok(
            "ack-round",
            "--round-id", round_id,
            "--revision", str(rev),
            "--pane", "w1:p2",
            "--scope", "scope-chk2",
            "--contract-hash", c_hash,
            "--action", "start",
        )

        # Omitted revision: returns allowed=false, reason=missing_revision_query_only
        chk_no_rev = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
        )
        self.assertEqual(chk_no_rev.returncode, 2)
        self.assertFalse(json.loads(chk_no_rev.stdout)["allowed"])
        self.assertEqual(json.loads(chk_no_rev.stdout)["reason"], "missing_revision_query_only")

        # Wrong pane
        chk_wrong_pane = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p99",
            "--revision", str(rev),
        )
        self.assertEqual(chk_wrong_pane.returncode, 2)
        self.assertFalse(json.loads(chk_wrong_pane.stdout)["allowed"])
        self.assertEqual(json.loads(chk_wrong_pane.stdout)["reason"], "pane_mismatch")

        # Superseded revision (e.g. 0)
        chk_old = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", "0",
        )
        self.assertEqual(chk_old.returncode, 2)
        self.assertFalse(json.loads(chk_old.stdout)["allowed"])
        self.assertEqual(json.loads(chk_old.stdout)["reason"], "superseded_revision")

        # Future revision (e.g. 2)
        chk_future = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", "2",
        )
        self.assertEqual(chk_future.returncode, 2)
        self.assertFalse(json.loads(chk_future.stdout)["allowed"])
        self.assertEqual(json.loads(chk_future.stdout)["reason"], "unknown_future_revision")

        # Contract drift/tamper
        Path(started["contract_path"]).write_text("drifted content", encoding="utf-8")
        chk_drift = self.invoke(
            "check-round",
            "--round-id", round_id,
            "--pane", "w1:p2",
            "--revision", str(rev),
        )
        self.assertEqual(chk_drift.returncode, 2)
        self.assertFalse(json.loads(chk_drift.stdout)["allowed"])
        self.assertEqual(json.loads(chk_drift.stdout)["reason"], "contract_corrupted")

    def test_cycle4_v1_migration_and_adopt_contract(self) -> None:
        # Create a v1 state file
        v1_state = {
            "version": 1,
            "cwd": str(self.cwd),
            "planner_pane": "w1:p1",
            "session_id": "session-v1",
            "auto_compact": True,
            "phase": 1,
            "round_seq": 3,
            "phase_round_count": 3,
            "rollover_required": False,
            "compaction_epoch": 0,
            "compact_queued": None,
            "rounds": [
                {
                    "round_id": "p01-r001",
                    "phase": 1,
                    "executor": "w1:p2",
                    "scope": "old-scope-1",
                    "acceptance": "exit 0",
                    "status": "accepted",
                    "started_at": "2026-09-18T10:00:00Z",
                    "finished_at": "2026-09-18T10:10:00Z",
                    "artifacts": "old-art-1",
                    "notes": "",
                    "fresh": None,
                },
                {
                    "round_id": "p01-r002",
                    "phase": 1,
                    "executor": "w1:p2",
                    "scope": "old-scope-2",
                    "acceptance": "exit 0",
                    "status": "active",
                    "started_at": "2026-09-18T10:15:00Z",
                    "finished_at": "",
                    "artifacts": "",
                    "notes": "",
                    "fresh": None,
                },
            ],
            "jobs": [
                {
                    "round_id": "p01-r002",
                    "phase": 1,
                    "queue": "default",
                    "job_id": "j101",
                    "label": "bg-job",
                    "submitter": "w1:p2",
                    "command": "sleep 10",
                    "log": "/tmp/log",
                    "expected_artifacts": "art",
                    "completion_assertion": "ok",
                    "state": "running",
                    "cancel_retry_owner": "w1:p1",
                    "created_at": "2026-09-18T10:16:00Z",
                    "updated_at": "2026-09-18T10:16:00Z",
                }
            ],
            "pending_dispatch": None,
            "created_at": "2026-09-18T09:00:00Z",
        }
        (self.state / "state.json").write_text(json.dumps(v1_state, indent=2), encoding="utf-8")

        # 1. Inspect status: active round is marked unconfirmed_protocol
        status = self.invoke_ok("status")
        self.assertEqual(status["active_rounds"], ["p01-r002"])
        self.assertEqual(status["work_status"], "unconfirmed_protocol")
        self.assertEqual(status["active_jobs"], 1)

        # 2. check-round is blocked with unconfirmed_protocol
        chk = self.invoke(
            "check-round",
            "--round-id", "p01-r002",
            "--pane", "w1:p2",
            "--revision", "1",
        )
        self.assertEqual(chk.returncode, 2)
        self.assertEqual(json.loads(chk.stdout)["reason"], "unconfirmed_protocol")

        # 3. adopt-contract explicitly recovers the round
        handoff = self.write_handoff("recovery.md", "Adopted contract for r2\n")
        adopted = self.invoke_ok(
            "adopt-contract",
            "--round-id", "p01-r002",
            "--file", str(handoff),
            "--scope", "adopted-scope",
            "--acceptance", "adopted-acceptance",
        )
        self.assertEqual(adopted["round_id"], "p01-r002")
        self.assertEqual(adopted["revision"], 1)
        self.assertEqual(adopted["work_status"], "pending_acceptance")
        self.assertTrue(adopted["contract_hash"])

        # 4. Normal protocol proceeds: accept -> start -> check-round allowed
        self.invoke_ok(
            "ack-round",
            "--round-id", "p01-r002",
            "--revision", "1",
            "--pane", "w1:p2",
            "--scope", "adopted-scope",
            "--contract-hash", adopted["contract_hash"],
            "--action", "accept",
        )
        self.invoke_ok(
            "ack-round",
            "--round-id", "p01-r002",
            "--revision", "1",
            "--pane", "w1:p2",
            "--scope", "adopted-scope",
            "--contract-hash", adopted["contract_hash"],
            "--action", "start",
        )
        chk_after = self.invoke_ok(
            "check-round",
            "--round-id", "p01-r002",
            "--pane", "w1:p2",
            "--revision", "1",
        )
        self.assertTrue(chk_after["allowed"])

        # 5. Verify state file version is 2 and repeated loads are idempotent
        state_data = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state_data["version"], 2)
        state_text_1 = (self.state / "state.json").read_text(encoding="utf-8")
        self.invoke_ok("status")
        state_text_2 = (self.state / "state.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(state_text_1)["version"], json.loads(state_text_2)["version"])

    def test_cycle4_unsupported_future_version_and_corrupted_file_untouched(self) -> None:
        # Unsupported future version (e.g. 99)
        v99_state = {"version": 99, "cwd": str(self.cwd)}
        (self.state / "state.json").write_text(json.dumps(v99_state), encoding="utf-8")
        res = self.invoke("status")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unsupported state version", res.stderr + res.stdout)
        # File must not be overwritten
        self.assertEqual((self.state / "state.json").read_text(encoding="utf-8"), json.dumps(v99_state))

        # Corrupted JSON
        (self.state / "state.json").write_text("invalid json {{{", encoding="utf-8")
        res2 = self.invoke("status")
        self.assertEqual(res2.returncode, 2)
        self.assertEqual((self.state / "state.json").read_text(encoding="utf-8"), "invalid json {{{")


if __name__ == "__main__":
    unittest.main()
