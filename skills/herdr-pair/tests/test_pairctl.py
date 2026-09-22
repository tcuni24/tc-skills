#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pairctl.py"
HOOK = Path(__file__).resolve().parents[1] / "scripts" / "claude_session_start_hook.py"
PLUGIN_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "on_planner_status.py"
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
if sub == ["notification", "show"]:
    # Notifications are best-effort; the default always succeeds so prompt-mode
    # failures stay isolated. FAKE_HERDR_NOTIFY_REASON (issue #11) simulates a
    # suppressed delivery: the four host failure reasons come back shown=false.
    reason = os.environ.get("FAKE_HERDR_NOTIFY_REASON", "").strip() or "manual"
    shown = reason not in (
        "rate_limited", "busy", "no_foreground_client", "disabled",
    )
    print(json.dumps({
        "id": "cli:notification:show",
        "result": {"type": "notification_show", "shown": shown, "reason": reason},
    }))
    raise SystemExit(0)
if sub == ["plugin", "list"] and "--json" in sys.argv[3:]:
    # Issue #9: the mechanism probe reads <exe>.plugins.json (content: plugins array).
    plugins_path = Path(sys.argv[0] + ".plugins.json")
    if not plugins_path.is_file():
        print(json.dumps({"id": "cli:plugin:list", "result": {"plugins": [], "type": "plugin_list"}}))
        raise SystemExit(0)
    try:
        plugins = json.loads(plugins_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": {"code": "plugin_list_failed", "message": str(exc)}}))
        raise SystemExit(1)
    print(json.dumps({"id": "cli:plugin:list", "result": {"plugins": plugins, "type": "plugin_list"}}))
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
        # Issue #10: invoke() pins XDG_STATE_HOME into the case's own temporary
        # directory so the pane index (and default state root) never touch the
        # user's real ~/.local/state.
        self.state_home = self.root / "xdg-state"
        self.herdr = self.root / "fake-herdr"
        self.herdr.write_text(FAKE_HERDR, encoding="utf-8")
        os.chmod(self.herdr, 0o755)
        self.invoke_ok("init", "--planner-pane", "w1:p1", "--session-id", "session-a")

    def tearDown(self) -> None:
        # Issue #9: any compact-continue watcher this test spawned must be stopped
        # here, via the pid file pairctl writes; never left running after the test.
        try:
            pid = int((self.state / "compact-continue.pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = 0
        if pid:
            cmdline = Path(f"/proc/{pid}/cmdline")
            try:
                owned = b"watch-compact-continue" in cmdline.read_bytes()
            except OSError:
                owned = False
            if owned:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                kill_deadline = time.time() + 5
                while time.time() < kill_deadline and pid:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    time.sleep(0.05)
        # NFS can report ENOTEMPTY for a directory whose entries were just
        # unlinked. The test body has already finished; retry only that race.
        for attempt in range(5):
            try:
                self.tmp.cleanup()
                break
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or attempt == 4:
                    raise
                time.sleep(0.05)

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
        env["XDG_STATE_HOME"] = str(self.state_home)
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

    def popen(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
        """Start pairctl without waiting, for overlap tests (same environment as invoke)."""
        cmd = ["python3", str(SCRIPT), *args, "--cwd", str(self.cwd), "--state-dir", str(self.state)]
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL_AUTO_COMPACT", None)
        env["PAIRCTL_CONTINUE_AFTER_COMPACT"] = "0"
        if extra_env:
            env.update(extra_env)
        return subprocess.Popen(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
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
        if body.strip() and "[环境]" not in body:
            body = body.rstrip("\n") + "\n[环境] local lightweight test only\n"
        path.write_text(body, encoding="utf-8")
        return path

    def start_finish(
        self, n: int, extra_env: dict[str, str] | None = None,
    ) -> tuple[str, subprocess.CompletedProcess[str]]:
        contract = self.write_handoff(
            f"start-{n}.md", f"# Round {n}\nComplete contract body {n}\n"
        )
        started = self.invoke_ok(
            "start-round",
            "--file", str(contract),
            "--executor", f"w1:p{n + 1}",
            "--scope", f"file-{n}",
            "--acceptance", f"test-{n} exit 0",
        )
        proc = self.invoke(
            "finish-round", "--round-id", started["round_id"],
            "--status", "accepted", "--artifacts", f"artifact-{n}", "--notes", f"verified-{n}",
            extra_env=extra_env,
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
            "--status", "accepted", "--artifacts", f"artifact-{n}", "--notes", f"verified-{n}",
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
            "start-round", "--file", str(self.write_handoff("blocked-start.md", "# Blocked\n")),
            "--executor", "w1:p9", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(blocked.returncode, 20)
        rolled = self.invoke_ok("rollover", "--new-session-id", "session-b")
        self.assertEqual(rolled["phase"], 2)
        self.assertFalse(rolled["session_switched"])
        status = self.invoke_ok("status")
        self.assertEqual(status["phase_round_count"], 0)

    def test_job_ledger_and_checkpoint_carry_active_job(self) -> None:
        started = self.invoke_ok(
            "start-round", "--file", str(self.write_handoff("pipeline.md", "# Pipeline\nRun it\n")),
            "--executor", "w1:p2", "--scope", "pipeline",
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
            "start-round", "--file", str(self.write_handoff("a.md", "# A\nFull contract\n")),
            "--executor", "w1:p2", "--scope", "a", "--acceptance", "b",
        )
        active = self.invoke(
            "start-round", "--file", str(self.write_handoff("c.md", "# C\nFull contract\n")),
            "--executor", "w1:p3", "--scope", "c", "--acceptance", "d",
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
            "--status", "accepted", "--artifacts", "a3", "--notes", "verified",
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
            "start-round", "--file", str(self.write_handoff("long.md", "# Long job\nFull contract\n")),
            "--executor", "w1:p2", "--scope", "pipeline",
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
            "--status", "accepted", "--artifacts", "queued", "--notes", "verified",
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
            "start-round", "--file", str(self.write_handoff("pending-start.md", "# Pending\n")),
            "--executor", "w1:p3", "--scope", "x", "--acceptance", "y",
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
            "start-round", "--file", str(self.write_handoff("keep.md", "# Keep\nFull contract\n")),
            "--executor", "w1:p2", "--scope", "keep",
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
            "start-round", "--file", str(self.write_handoff("repair.md", "# Repair\nFull contract\n")),
            "--executor", "w1:p2", "--scope", "pipeline",
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
        blocked = self.invoke(
            "start-round", "--file", str(self.write_handoff("rollover-blocked.md", "# Blocked\n")),
            "--executor", "w1:p9", "--scope", "x", "--acceptance", "y",
        )
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
            # Record write needs auto-continue on; INTERNAL keeps the test's manual
            # watch the only wake-up source. deadline 0 => already due.
            self.start_finish(n, extra_env={
                **self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "0",
            })
        # Issue #9: the record-driven watcher only delivers once the epoch advanced.
        self.invoke_ok("rollover", "--reason", "compact")
        self.clear_calls()
        self.set_status("idle")
        prompted = self.invoke_ok(
            "watch-compact-continue",
            extra_env={"PAIRCTL_CONTINUE_POLL_S": "0.05"},
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
            "start-round", "--file", str(self.write_handoff("fifth.md", "# Fifth\nFull contract\n")),
            "--executor", "w1:p6", "--scope", "s", "--acceptance", "a",
        )
        self.clear_calls()
        fifth = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
            extra_env={
                "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
                "PAIRCTL_CONTINUE_POLL_S": "0.05",
                "PAIRCTL_RESUME_DEADLINE_S": "0",
            },
        )
        self.assertEqual(fifth.returncode, 20)
        payload = json.loads(fifth.stdout)
        self.assertTrue(payload["planner_compact"]["queued"])
        self.assertTrue(payload["continue_after_compact"]["spawned"], payload)
        # The record is armed but the epoch has not advanced: no prompt before rollover.
        early_until = time.time() + 0.5
        while time.time() < early_until:
            self.assertEqual(self.auto_continue_prompts(), [], self.herdr_calls())
            time.sleep(0.05)
        self.invoke_ok("rollover", "--reason", "compact")
        deadline = time.time() + 5
        continue_prompts = []
        while time.time() < deadline:
            continue_prompts = self.auto_continue_prompts()
            if continue_prompts:
                break
            time.sleep(0.05)
        self.assertEqual(len(continue_prompts), 1, self.herdr_calls())
        # The watcher records `delivered` right after the prompt lands in herdr;
        # poll for it like test_watcher_delivers_once does instead of racing it.
        status = ""
        settle_until = time.time() + 5
        while time.time() < settle_until:
            status = self.read_state()["resume_pending"]["status"]
            if status == "delivered":
                break
            time.sleep(0.05)
        self.assertEqual(status, "delivered")

    def test_auto_compact_can_be_disabled_by_env_and_init(self) -> None:
        for n in range(1, 5):
            self.start_finish(n)
        started = self.invoke_ok(
            "start-round", "--file", str(self.write_handoff("disabled.md", "# Disabled\nFull contract\n")),
            "--executor", "w1:p6", "--scope", "s", "--acceptance", "a",
        )
        self.clear_calls()
        fifth = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
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

    def run_hook(self, payload: dict, xdg: Path, *, pane: str = "w1:p1") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("HERDR_PANE_ID", None)
        if pane:
            env["HERDR_PANE_ID"] = pane
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
                "start-round", "--file", str(self.write_handoff(f"hook-{n}.md", f"# Hook {n}\n")),
                "--executor", "w1:p2", "--scope", "s", "--acceptance", "a",
                use_state_dir=False, extra_env=env,
            )
            self.invoke(
                "finish-round", "--round-id", started["round_id"], "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
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
                "start-round", "--file", str(self.write_handoff(f"clear-{n}.md", f"# Clear {n}\n")),
                "--executor", "w1:p2", "--scope", "s", "--acceptance", "a",
                use_state_dir=False, extra_env=env,
            )
            self.invoke(
                "finish-round", "--round-id", started["round_id"], "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
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

    def test_resolve_pending_delivered_fails_closed_on_missing_or_corrupt_snapshot(self) -> None:
        for damage in ("missing", "corrupt"):
            with self.subTest(damage=damage):
                if self.read_state().get("pending_dispatch"):
                    self.invoke_ok("resolve-pending", "--outcome", "not-delivered")
                self.set_herdr_mode("stalled")
                source = self.write_handoff(
                    f"pending-{damage}.md", f"# Pending {damage}\nOriginal instructions\n"
                )
                stalled = self.invoke(
                    "send-round", "--target", "w1:p2", "--file", str(source),
                    "--herdr", str(self.herdr), "--scope", "alpha.txt", "--acceptance", "check",
                )
                self.assertEqual(stalled.returncode, 2)
                pending = self.read_state()["pending_dispatch"]
                snapshot = Path(pending["contract_path"])
                if damage == "missing":
                    snapshot.unlink()
                    source.write_text("# Changed\nMust never be adopted\n", encoding="utf-8")
                else:
                    snapshot.write_text("corrupted", encoding="utf-8")
                before = (self.state / "state.json").read_bytes()
                rejected = self.invoke("resolve-pending", "--outcome", "delivered")
                self.assertEqual(rejected.returncode, 2)
                expected_reason = "contract_missing" if damage == "missing" else "contract_corrupted"
                self.assertEqual(json.loads(rejected.stdout)["reason"], expected_reason)
                self.assertEqual((self.state / "state.json").read_bytes(), before)
                self.assertIsNotNone(self.read_state()["pending_dispatch"])

    def test_v1_pending_delivery_migrates_unconfirmed_without_recreating_contract(self) -> None:
        source = self.write_handoff("legacy.md", "# Legacy source\nMutable text\n")
        baseline = self.read_state()
        for version in (1, 2):
            with self.subTest(version=version):
                data = json.loads(json.dumps(baseline))
                data["version"] = version
                data["pending_dispatch"] = {
                    "status": "uncertain", "counted": False, "round_id": "p01-r001",
                    "target": "w1:p2", "executor": "w1:p2", "scope": "legacy.txt",
                    "acceptance": "legacy-check", "handoff": str(source),
                    "phase_round_count": 0, "created_at": "2026-09-18T00:00:00+00:00",
                }
                self.write_state(data)
                source.write_text(f"# Changed after v{version} dispatch\n", encoding="utf-8")
                resolved = self.invoke_ok("resolve-pending", "--outcome", "delivered")
                self.assertEqual(resolved["revision"], 0)
                self.assertEqual(resolved["work_status"], "unconfirmed_protocol")
                state = self.read_state()
                self.assertEqual(state["phase_round_count"], 1)
                self.assertEqual(state["rounds"][0]["current_revision"], 0)
                self.assertFalse(list((self.state / "contracts").glob("p01-r001*")))

    def test_cycle1_start_round_and_no_fresh_and_resolve_pending(self) -> None:
        # Test start-round
        started = self.invoke_ok(
            "start-round",
            "--file", str(self.write_handoff("start-a.md", "# Start A\nFull contract body\n")),
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
            "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
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
            "--status", "accepted", "--artifacts", "artifact", "--notes", "verified",
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

    def test_start_round_requires_nonempty_complete_utf8_file(self) -> None:
        absent = self.invoke(
            "start-round", "--executor", "w1:p2", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(absent.returncode, 2)
        empty = self.write_handoff("empty.md", " \n\t")
        rejected = self.invoke(
            "start-round", "--file", str(empty),
            "--executor", "w1:p2", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("contract file is empty", rejected.stderr)
        self.assertEqual(self.read_state()["rounds"], [])

    def test_cycle1_contract_corrupted_or_missing(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--file", str(self.write_handoff("corrupt.md", "# Corrupt\nFull contract body\n")),
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
            "--file", str(self.write_handoff("ack.md", "# Ack\nFull contract body\n")),
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
            "--file", str(self.write_handoff("validation.md", "# Validation\nFull contract body\n")),
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
        original_contract = c_path.read_bytes()
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
        c_path.write_bytes(original_contract)
        self.invoke_ok(
            "finish-round",
            "--round-id", round_id,
            "--status", "accepted",
            "--artifacts", "artifact", "--notes", "verified",
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
            "--file", str(self.write_handoff("check.md", "# Check\nFull contract body\n")),
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

    def test_check_round_returns_verified_contract_before_acceptance(self) -> None:
        body = "# Authoritative task\nEdit only alpha.txt\nRun exact-check\n"
        contract = self.write_handoff("authoritative.md", body)
        started = self.invoke_ok(
            "start-round", "--file", str(contract),
            "--executor", "w1:p2", "--scope", "alpha.txt",
            "--acceptance", "exact-check exits 0",
        )

        queried = self.invoke(
            "check-round", "--round-id", started["round_id"],
            "--revision", "1", "--pane", "w1:p2",
        )
        self.assertEqual(queried.returncode, 2)
        payload = json.loads(queried.stdout)
        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["reason"], "not_accepted")
        self.assertEqual(payload["revision"], 1)
        self.assertEqual(payload["executor"], "w1:p2")
        self.assertEqual(payload["scope"], "alpha.txt")
        self.assertEqual(payload["acceptance"], "exact-check exits 0")
        self.assertEqual(payload["contract_hash"], started["contract_hash"])
        self.assertEqual(payload["contract_path"], started["contract_path"])
        self.assertEqual(
            payload["contract_text"],
            f"[轮次] round_id={started['round_id']}\n" + body + "[环境] local lightweight test only\n",
        )

        query_only = self.invoke(
            "check-round", "--round-id", started["round_id"], "--pane", "w1:p2",
        )
        self.assertEqual(query_only.returncode, 2)
        query_payload = json.loads(query_only.stdout)
        self.assertEqual(query_payload["reason"], "missing_revision_query_only")
        self.assertEqual(query_payload["contract_hash"], started["contract_hash"])
        self.assertEqual(query_payload["contract_text"], payload["contract_text"])

    def test_executor_writes_real_file_only_after_query_accept_start_and_check(self) -> None:
        contract = self.write_handoff(
            "executor-flow.md", "# Executor flow\nCreate result.txt containing done\n"
        )
        started = self.invoke_ok(
            "send-round", "--target", "w1:p2", "--file", str(contract), "--no-fresh",
            "--executor", "w1:p2", "--scope", "result.txt", "--acceptance", "content is done",
        )
        result = self.cwd / "result.txt"
        query = self.invoke(
            "check-round", "--round-id", started["round_id"],
            "--revision", "1", "--pane", "w1:p2",
        )
        self.assertEqual(query.returncode, 2)
        metadata = json.loads(query.stdout)
        self.assertEqual(metadata["reason"], "not_accepted")
        self.assertFalse(result.exists())
        for action in ("accept", "start"):
            self.invoke_ok(
                "ack-round", "--round-id", started["round_id"],
                "--revision", str(metadata["revision"]), "--pane", metadata["executor"],
                "--scope", metadata["scope"], "--contract-hash", metadata["contract_hash"],
                "--action", action,
            )
        allowed = self.invoke_ok(
            "check-round", "--round-id", started["round_id"],
            "--revision", "1", "--pane", "w1:p2",
        )
        self.assertTrue(allowed["allowed"])
        result.write_text("done\n", encoding="utf-8")
        self.assertEqual(result.read_text(encoding="utf-8"), "done\n")

    def test_cycle3_check_round_rejects_omitted_revision_wrong_pane_and_contract_drift(self) -> None:
        started = self.invoke_ok(
            "start-round",
            "--file", str(self.write_handoff("check-drift.md", "# Check drift\nFull contract body\n")),
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
        self.assertNotIn("contract_text", json.loads(chk_wrong_pane.stdout))

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

    def legacy_v1_state(self) -> dict:
        """A raw v1 (pre-migration) state file: rounds p01-r001/p01-r002 (r002 active)
        plus one running background job, written straight to state.json."""
        return {
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

    def test_cycle4_v1_migration_and_adopt_contract(self) -> None:
        # Create a v1 state file
        v1_state = self.legacy_v1_state()
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

    def test_adopt_contract_rejects_already_bound_round_without_changes(self) -> None:
        original = self.write_handoff("bound.md", "# Bound contract\nOriginal instructions\n")
        started = self.invoke_ok(
            "start-round", "--file", str(original),
            "--executor", "w1:p2", "--scope", "one.txt", "--acceptance", "check-one",
        )
        replacement = self.write_handoff("replacement.md", "# Replacement\nChanged instructions\n")
        state_before = (self.state / "state.json").read_bytes()
        snapshot_before = Path(started["contract_path"]).read_bytes()
        rejected = self.invoke(
            "adopt-contract", "--round-id", started["round_id"],
            "--file", str(replacement), "--scope", "two.txt", "--acceptance", "check-two",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)["reason"], "already_bound")
        self.assertEqual((self.state / "state.json").read_bytes(), state_before)
        self.assertEqual(Path(started["contract_path"]).read_bytes(), snapshot_before)

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

    # --- issue 2: planner context budget, recovery, snapshots, and lint ------------

    def note_transcript(self, session_id: str, entries: list[dict], *, kind: str = "claude") -> Path:
        transcript = self.root / f"{session_id}.jsonl"
        transcript.write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
        )
        self.invoke_ok(
            "note-session", "--session-id", session_id, "--transcript-path", str(transcript),
            "--kind", kind, "--source", "startup", "--pane", "w1:p1",
        )
        return transcript

    def test_context_usage_last_real_assistant_budget_stale_and_unknown(self) -> None:
        old = "2026-01-01T00:00:00+00:00"
        self.note_transcript("session-a", [
            {"sessionId": "session-a", "timestamp": old, "message": {"role": "user"}},
            {"sessionId": "session-a", "timestamp": old, "message": {
                "role": "assistant", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30}
            }},
            {"sessionId": "session-a", "timestamp": old, "message": {
                "role": "assistant", "model": "<synthetic>",
                "usage": {"input_tokens": 900, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 900}
            }},
            {"sessionId": "session-a", "timestamp": old, "message": {
                "role": "assistant", "usage": {"input_tokens": 40, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 60}
            }},
        ])
        usage = self.invoke_ok("context-usage", "--budget", "100")
        self.assertEqual(usage["context_tokens"], 150)
        self.assertTrue(usage["over_budget"])
        self.assertTrue(usage["stale"])

        self.invoke_ok(
            "note-session", "--session-id", "other", "--transcript-path", str(self.root / "session-a.jsonl"),
            "--kind", "claude", "--source", "resume", "--pane", "w1:p1",
        )
        mismatch = self.invoke_ok("context-usage")
        self.assertEqual(mismatch["status"], "unknown")
        self.assertIsNone(mismatch["context_tokens"])
        self.invoke_ok(
            "note-session", "--session-id", "session-a", "--kind", "cursor", "--source", "startup", "--pane", "w1:p1",
        )
        non_claude = self.invoke_ok("context-usage")
        self.assertEqual(non_claude["status"], "unknown")
        self.assertIsNone(non_claude["context_tokens"])

    def test_budget_priority_init_queue_and_dispatch_gate(self) -> None:
        stamp = "2026-09-21T00:00:00+00:00"
        self.note_transcript("session-a", [{
            "sessionId": "session-a", "timestamp": stamp,
            "message": {"role": "assistant", "usage": {
                "input_tokens": 100, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 100,
            }},
        }])
        self.set_kind("claude")
        queued = self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--context-budget", "200",
            extra_env={"PAIRCTL_CONTEXT_BUDGET": "250"},
        )
        self.assertEqual(queued["status"], "CONTEXT_COMPACT_QUEUED")
        self.assertEqual(queued["context_usage"]["budget"], 200)
        blocked = self.invoke(
            "start-round", "--file", str(self.write_handoff("queued.md", "# queued\n")),
            "--executor", "w1:p2", "--scope", "x", "--acceptance", "y",
        )
        self.assertEqual(blocked.returncode, 20)
        self.assertEqual(json.loads(blocked.stdout)["status"], "CONTEXT_COMPACT_QUEUED")

    def test_post_dispatch_compact_active_hook_recovery_preserves_round(self) -> None:
        xdg = self.root / "xdg-active"
        env = {"XDG_STATE_HOME": str(xdg), "PAIRCTL_CONTINUE_AFTER_COMPACT": "0"}
        transcript = self.root / "active.jsonl"
        transcript.write_text(json.dumps({
            "sessionId": "active-session", "timestamp": "2026-09-21T00:00:00+00:00",
            "message": {"role": "assistant", "usage": {
                "input_tokens": 100000, "cache_creation_input_tokens": 30000,
                "cache_read_input_tokens": 30000,
            }},
        }) + "\n", encoding="utf-8")
        self.run_hook({
            "session_id": "active-session", "source": "startup", "cwd": str(self.cwd),
            "transcript_path": str(transcript),
        }, xdg)
        self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--session-id", "active-session",
            "--no-context-check", "--goal", "G" * 20000, use_state_dir=False, extra_env=env,
        )
        report = self.cwd / "reports" / "round-report.md"
        handoff = self.write_handoff(
            "active-handoff.md",
            "# Active\n[可以新建] reports/round-report.md\n[报告] reports/round-report.md\n",
        )
        sent = self.invoke_ok(
            "send-round", "--target", "w1:p2", "--file", str(handoff), "--no-fresh",
            "--executor", "w1:p2", "--scope", "reports/round-report.md", "--acceptance", "report",
            use_state_dir=False, extra_env=env,
        )
        self.assertTrue(sent["planner_compact"]["queued"])
        for action in ("accept", "start"):
            self.invoke_ok(
                "ack-round", "--round-id", sent["round_id"], "--revision", "1",
                "--pane", "w1:p2", "--scope", "reports/round-report.md",
                "--contract-hash", sent["contract_hash"], "--action", action,
                use_state_dir=False, extra_env=env,
            )
        report.parent.mkdir()
        report.write_text("done\n", encoding="utf-8")
        hook = self.run_hook({
            "session_id": "active-session", "source": "compact", "cwd": str(self.cwd),
            "transcript_path": str(transcript),
        }, xdg)
        context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Full checkpoint:", context)
        self.assertLess(len(context), 16000)
        self.assertIn("CHECKPOINT.md", context)
        status = json.loads(self.invoke("status", use_state_dir=False, extra_env=env).stdout)
        self.assertEqual(status["active_rounds"], [sent["round_id"]])
        self.assertEqual(status["phase_round_count"], 1)
        self.assertEqual(status["compaction_epoch"], 1)
        self.assertEqual(status["report"], str(report))
        checked = self.invoke_ok(
            "check-round", "--round-id", sent["round_id"], "--pane", "w1:p2", "--revision", "1",
            use_state_dir=False, extra_env=env,
        )
        self.assertTrue(checked["allowed"])

    def test_finish_evidence_report_and_checkpoint_decisions(self) -> None:
        report = self.cwd / "report.md"
        started = self.invoke_ok(
            "start-round", "--file", str(self.write_handoff(
                "evidence.md", "# Evidence\n[可以新建] report.md\n[报告] report.md\n"
            )), "--executor", "w1:p2", "--scope", "report.md", "--acceptance", "review",
        )
        before = (self.state / "state.json").read_bytes()
        missing = self.invoke("finish-round", "--round-id", started["round_id"], "--status", "accepted")
        self.assertEqual(json.loads(missing.stdout)["reason"], "missing_acceptance_evidence")
        self.assertEqual((self.state / "state.json").read_bytes(), before)
        absent = self.invoke(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted",
            "--artifacts", "a", "--notes", "n", "--report", str(report),
        )
        self.assertEqual(json.loads(absent.stdout)["reason"], "report_missing")
        report.write_text("ok\n", encoding="utf-8")
        self.invoke_ok("note", "--text", "scope confirmed")
        self.invoke_ok(
            "finish-round", "--round-id", started["round_id"], "--status", "accepted",
            "--artifacts", "artifact-a", "--notes", "verified-a", "--report", str(report),
        )
        checkpoint = (self.state / "CHECKPOINT.md").read_text(encoding="utf-8")
        for value in ("Goal", "Context usage", "Revision", "Contract", "Report", "Snapshot", "artifact-a", "verified-a", "scope confirmed"):
            self.assertIn(value, checkpoint)

    def test_snapshot_diff_tracks_changed_added_removed_and_missing(self) -> None:
        tracked = self.cwd / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        directory = self.cwd / "tree"
        directory.mkdir()
        (directory / "old.txt").write_text("old\n", encoding="utf-8")
        handoff = self.write_handoff(
            "snapshot.md",
            "# Snapshot\n[可以改] tracked.txt, tree\n[只读输入] missing-input.txt\n[可以新建] new.txt\n",
        )
        started = self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "tracked.txt", "--acceptance", "diff",
        )
        manifest = json.loads(Path(started["snapshot"]["manifest"]).read_text(encoding="utf-8"))
        by_path = {item["path"]: item for item in manifest}
        self.assertEqual(by_path["tracked.txt"]["sha256"], hashlib.sha256(b"one\n").hexdigest())
        self.assertFalse(by_path["new.txt"]["exists"])
        clean = self.invoke("diff-round", "--round-id", started["round_id"])
        self.assertEqual(clean.returncode, 0, clean.stdout)
        tracked.write_text("two\n", encoding="utf-8")
        (directory / "old.txt").unlink()
        (directory / "added.txt").write_text("new\n", encoding="utf-8")
        (self.cwd / "new.txt").write_text("created\n", encoding="utf-8")
        changed = self.invoke("diff-round", "--round-id", started["round_id"])
        self.assertEqual(changed.returncode, 1)
        payload = json.loads(changed.stdout)
        self.assertIn("tracked.txt", payload["changed"])
        self.assertIn("tree/old.txt", payload["removed"])
        self.assertIn("tree/added.txt", payload["added"])
        self.assertIn("new.txt", payload["added"])

    def test_handoff_lint_rules_and_skip_reason(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.cwd / "子 目录").mkdir()
        (self.cwd / "子 目录" / "未跟踪.txt").write_text("u\n", encoding="utf-8")
        cases = {
            "env_block_missing": "# no environment\n",
            "tmp_path": "# bad /tmp/cache\n[环境] local\n",
            "fence_overlap": "[可以改] tests\n[可以新建] tests/x.py\n[环境] local\n",
            "untracked_conflict": "[可以改] 子 目录\n[不许动] 任何未跟踪文件\n[环境] local\n",
        }
        for index, (rule, body) in enumerate(cases.items()):
            path = self.cwd / f"lint-{index}.md"
            path.write_text(body, encoding="utf-8")
            rejected = self.invoke(
                "start-round", "--file", str(path), "--executor", "w1:p2",
                "--scope", "x", "--acceptance", "y",
            )
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
            self.assertIn(rule, [item["rule"] for item in json.loads(rejected.stdout)["findings"]])
            self.assertEqual(self.read_state()["phase_round_count"], 0)
            self.assertIsNone(self.read_state()["pending_dispatch"])
        valid = self.cwd / "valid.md"
        valid.write_text("[可以改] tracked.txt\n[可以新建] output.txt\n[不许动] docs\n[环境] local\n", encoding="utf-8")
        passed = self.invoke_ok(
            "start-round", "--file", str(valid), "--executor", "w1:p2",
            "--scope", "tracked.txt", "--acceptance", "y",
        )
        self.assertEqual(passed["round_id"], "p01-r001")

        # A separate state proves --skip-lint records its explicit reason.
        second_state = self.root / "skip-state"
        init = subprocess.run(
            ["python3", str(SCRIPT), "init", "--cwd", str(self.cwd), "--state-dir", str(second_state)],
            text=True, capture_output=True, check=False, env={**os.environ, "PAIRCTL_HERDR": str(self.herdr)},
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        skipped = subprocess.run(
            ["python3", str(SCRIPT), "start-round", "--cwd", str(self.cwd), "--state-dir", str(second_state),
             "--file", str(self.cwd / "lint-0.md"), "--executor", "w1:p2", "--scope", "x",
             "--acceptance", "y", "--skip-lint", "legacy fixture"],
            text=True, capture_output=True, check=False, env={**os.environ, "PAIRCTL_HERDR": str(self.herdr)},
        )
        self.assertEqual(skipped.returncode, 0, skipped.stderr + skipped.stdout)
        state = json.loads((second_state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["rounds"][0]["skip_lint"], "legacy fixture")

    def test_hook_notes_all_sources_before_init_missing_fields_are_silent(self) -> None:
        xdg = self.root / "xdg-sources"
        transcript = self.root / "source.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        key = hashlib.sha256(str(self.cwd.resolve()).encode()).hexdigest()[:20]
        record = xdg / "herdr-pair" / key / "planner-session.json"
        for source in ("startup", "resume", "compact", "clear"):
            hook = self.run_hook({
                "session_id": f"sess-{source}", "source": source, "cwd": str(self.cwd),
                "transcript_path": str(transcript),
            }, xdg, pane="")
            self.assertEqual(hook.returncode, 0)
            self.assertEqual(hook.stdout, "")
            saved = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(saved["session_id"], f"sess-{source}")
            self.assertEqual(saved["source"], source)
        before = record.read_bytes()
        for payload in ({"source": "startup", "cwd": str(self.cwd)}, {"session_id": "x", "cwd": str(self.cwd)}, {"session_id": "x", "source": "startup"}):
            hook = self.run_hook(payload, xdg)
            self.assertEqual(hook.returncode, 0)
            self.assertEqual(hook.stdout, "")
            self.assertEqual(record.read_bytes(), before)

    def test_context_usage_derived_locator_and_invalid_latest_usage(self) -> None:
        fake_home = self.root / "home"
        base = self.cwd
        self.cwd = base / "under_score" / "中文 space.with-dot"
        self.cwd.mkdir(parents=True)
        self.state = self.root / "derived-state"
        self.invoke_ok("init", "--planner-pane", "w1:p1", "--no-context-check")
        encoded = re.sub(r"[^A-Za-z0-9-]", "-", str(base.resolve())) + "-under-score----space-with-dot"
        project = fake_home / ".claude" / "projects" / encoded
        project.mkdir(parents=True)
        transcript = project / "derived-session.jsonl"
        transcript.write_text("\n".join([
            json.dumps({"sessionId": "derived-session", "timestamp": "2026-09-21T00:00:00Z", "type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3}}}),
            json.dumps({"sessionId": "derived-session", "timestamp": "2026-09-21T00:01:00Z", "type": "assistant", "message": {"usage": {"input_tokens": 9}}}),
        ]) + "\n", encoding="utf-8")
        env = {"HOME": str(fake_home)}
        self.invoke_ok(
            "note-session", "--session-id", "derived-session", "--kind", "claude", "--source", "resume", "--pane", "w1:p1",
            extra_env=env,
        )
        invalid = self.invoke_ok("context-usage", extra_env=env)
        self.assertEqual(invalid["status"], "unknown")
        self.assertIsNone(invalid["context_tokens"])
        lines = transcript.read_text(encoding="utf-8").splitlines()
        transcript.write_text(lines[0] + "\n", encoding="utf-8")
        derived = self.invoke_ok("context-usage", extra_env=env)
        self.assertEqual(derived["status"], "ok")
        self.assertEqual(derived["source"], "derived")
        self.assertEqual(derived["context_tokens"], 6)

        explicit = self.root / "explicit-transcript.jsonl"
        entry = json.loads(lines[0])
        entry["message"]["usage"]["input_tokens"] = 10
        explicit.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        self.invoke_ok("note-session", "--session-id", "derived-session", "--kind", "claude",
                       "--source", "resume", "--pane", "w1:p1", "--transcript-path", str(explicit),
                       extra_env=env)
        recorded = self.invoke_ok("context-usage", extra_env=env)
        self.assertEqual(recorded["status"], "ok")
        self.assertEqual(recorded["source"], "recorded")
        self.assertEqual(recorded["context_tokens"], 15)

    def test_pending_unknown_and_disabled_never_auto_compact(self) -> None:
        self.note_transcript("session-a", [{
            "sessionId": "session-a", "timestamp": "2026-09-21T00:00:00Z",
            "message": {"role": "assistant", "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}},
        }])
        self.set_herdr_mode("stalled")
        self.clear_calls()
        pending = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(self.write_handoff("pending-budget.md", "# Pending\n")),
            "--no-fresh",
        )
        self.assertEqual(pending.returncode, 2)
        self.assertFalse(any(call[:3] == ["agent", "prompt", "w1:p1"] for call in self.herdr_calls()))

        self.invoke_ok("resolve-pending", "--outcome", "not-delivered")
        self.set_herdr_mode("ok")
        self.invoke_ok("init", "--no-auto-compact", "--no-context-check")
        disabled = self.send_round(1)
        self.assertNotIn("planner_compact", disabled)

    def test_stale_only_init_queues_compaction(self) -> None:
        self.note_transcript("session-a", [{
            "sessionId": "session-a", "timestamp": "2020-01-01T00:00:00Z",
            "message": {"role": "assistant", "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3}},
        }])
        queued = self.invoke_ok("init", "--planner-pane", "w1:p1", "--context-budget", "999999")
        self.assertEqual(queued["status"], "CONTEXT_COMPACT_QUEUED")
        self.assertFalse(queued["context_usage"]["over_budget"])
        self.assertTrue(queued["context_usage"]["stale"])

    def test_snapshot_large_hash_only_and_manifest_name_collision(self) -> None:
        large = self.cwd / "large.bin"
        with large.open("wb") as handle:
            handle.truncate(50 * 1024 * 1024 + 1)
        source_manifest = self.cwd / "manifest.json"
        source_manifest.write_text('{"source": true}\n', encoding="utf-8")
        handoff = self.write_handoff(
            "snapshot-large.md", "# Large\n[只读输入] large.bin, manifest.json\n"
        )
        started = self.invoke_ok(
            "start-round", "--file", str(handoff), "--executor", "w1:p2",
            "--scope", "none", "--acceptance", "snapshot",
        )
        snapshot = started["snapshot"]
        metadata = Path(snapshot["manifest"])
        records = {item["path"]: item for item in json.loads(metadata.read_text(encoding="utf-8"))}
        self.assertFalse(records["large.bin"]["copied"])
        self.assertTrue(records["large.bin"]["sha256"])
        self.assertTrue((metadata.parent / "files" / "manifest.json").is_file())
        self.assertIsInstance(json.loads(metadata.read_text(encoding="utf-8")), list)

    # --- resume_pending record (issue #8) ----------------------------------------------

    REQUIRED_RECORD_KEYS = (
        "armed_at", "epoch_at_arm", "planner_pane", "state_dir",
        "mechanism", "deadline", "status", "attempts", "last_error",
    )

    # A record write requires auto-continue on (issue #9). INTERNAL marks the parent as
    # an internal watcher so pairctl does not spawn one: the test drives watch /
    # resume-deliver manually and must stay the only wake-up source.
    RECORD_ENV = {
        "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
        "PAIRCTL_INTERNAL_WATCHER": "1",
    }

    def auto_continue_prompts(self) -> list[list[str]]:
        """argv tails of delivered auto-continue prompts on the planner pane."""
        return [
            c for c in self.herdr_calls()
            if c[:2] == ["agent", "prompt"] and c[2] == "w1:p1"
            and "auto-continue after compaction" in c[3]
        ]

    def arm_and_advance_epoch(self) -> str:
        """Arm the resume record via compact-self, then advance the epoch. Returns armed_at."""
        queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(queued["queued"], queued)
        armed_at = self.read_state()["resume_pending"]["armed_at"]
        self.invoke_ok("rollover", "--reason", "compact")
        return armed_at

    def test_resume_pending_none_after_init_and_full_record_when_armed(self) -> None:
        self.assertIsNone(self.read_state().get("resume_pending"))
        self.assertIsNone(self.invoke_ok("status")["resume_pending"])

        queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(queued["queued"], queued)
        record = self.invoke_ok("status", extra_env=self.RECORD_ENV)["resume_pending"]
        self.assertIsInstance(record, dict)
        for key in self.REQUIRED_RECORD_KEYS:
            self.assertIn(key, record)
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["attempts"], 0)
        self.assertEqual(record["last_error"], "")
        self.assertEqual(record["mechanism"], "watcher")
        self.assertEqual(record["epoch_at_arm"], 0)
        self.assertEqual(record["planner_pane"], "w1:p1")
        self.assertEqual(Path(record["state_dir"]).resolve(), self.state.resolve())
        first_armed = record["armed_at"]
        armed = dt.datetime.fromisoformat(first_armed)
        deadline = dt.datetime.fromisoformat(record["deadline"])
        self.assertEqual(deadline - armed, dt.timedelta(seconds=600))

        # Same epoch requeue (issue #9): armed_at stays, only the deadline moves.
        requeue_at = time.time()
        requeued = self.invoke_ok(
            "compact-self",
            extra_env={**self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "30"},
        )
        self.assertTrue(requeued["queued"], requeued)
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["armed_at"], first_armed)
        self.assertEqual(record["mechanism"], "watcher")
        deadline = dt.datetime.fromisoformat(record["deadline"])
        self.assertGreaterEqual(deadline.timestamp(), requeue_at + 28)
        self.assertLessEqual(deadline.timestamp(), time.time() + 31)

    def test_rollover_with_active_round_keeps_resume_record(self) -> None:
        for n in range(1, 5):
            self.start_finish(n)
        self.invoke_ok(
            "start-round",
            "--file", str(self.write_handoff("fifth.md", "# Fifth\nFull contract body 5\n")),
            "--executor", "w1:p6", "--scope", "scope-5", "--acceptance", "test-5 exit 0",
        )
        state = self.read_state()
        self.assertTrue(state["rollover_required"])
        self.assertEqual(state["phase_round_count"], 5)

        armed = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(armed["queued"], armed)
        armed_at = self.read_state()["resume_pending"]["armed_at"]

        rolled = self.invoke_ok("rollover", "--reason", "compact")
        self.assertEqual(rolled["compaction_epoch"], 1)
        # An active round keeps the rollover flag set; the record must survive either way.
        self.assertFalse(rolled["phase_advanced"], rolled)
        state = self.read_state()
        self.assertTrue(state["rollover_required"])
        self.assertEqual(state["phase"], 1)
        record = state["resume_pending"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["armed_at"], armed_at)
        self.assertEqual(record["epoch_at_arm"], 0)

    def test_rollover_after_fifth_finish_keeps_resume_record(self) -> None:
        for n in range(1, 6):
            _, fifth = self.start_finish(n, extra_env=self.RECORD_ENV)
        self.assertEqual(fifth.returncode, 20, fifth.stderr + fifth.stdout)
        payload = json.loads(fifth.stdout)
        self.assertTrue(payload["planner_compact"]["queued"], payload)
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "pending")
        armed_at = record["armed_at"]

        rolled = self.invoke_ok("rollover", "--reason", "compact")
        self.assertEqual(rolled["compaction_epoch"], 1)
        self.assertTrue(rolled["phase_advanced"], rolled)
        state = self.read_state()
        self.assertFalse(state["rollover_required"])
        self.assertEqual(state["phase"], 2)
        record = state["resume_pending"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["armed_at"], armed_at)
        self.assertEqual(record["epoch_at_arm"], 0)

    def test_resume_deliver_rejections_before_epoch_advance(self) -> None:
        self.clear_calls()
        missing = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(missing.returncode, 2, missing.stderr + missing.stdout)
        payload = json.loads(missing.stdout)
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "no_record")
        self.assertEqual(self.herdr_calls(), [])

        self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.clear_calls()
        early = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "plugin")
        self.assertEqual(early.returncode, 2, early.stderr + early.stdout)
        payload = json.loads(early.stdout)
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "epoch_not_advanced")
        self.assertEqual(self.herdr_calls(), [])

        wrong = self.invoke("resume-deliver", "--pane", "w1:p9", "--via", "plugin")
        self.assertEqual(wrong.returncode, 2, wrong.stderr + wrong.stdout)
        self.assertEqual(json.loads(wrong.stdout)["reason"], "pane_mismatch")
        self.assertEqual(self.herdr_calls(), [])

        self.invoke_ok("rollover", "--reason", "compact")
        self.set_status("running")
        self.clear_calls()
        busy = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(busy.returncode, 2, busy.stderr + busy.stdout)
        self.assertEqual(json.loads(busy.stdout)["reason"], "planner_busy")
        calls = self.herdr_calls()
        self.assertFalse([c for c in calls if c[:2] == ["agent", "prompt"]], calls)
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")

    def test_resume_deliver_prompts_once_and_claims_record(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.clear_calls()

        done = self.invoke_ok("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(done["status"], "resume_delivered")
        self.assertEqual(done["via"], "watcher")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(prompts[0][2], "w1:p1")
        self.assertIn("herdr-pair auto-continue after compaction", prompts[0][3])

        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "delivered")
        self.assertEqual(record["attempts"], 0)
        self.assertEqual(record["last_error"], "")

        # The fake snapshots state.json at every herdr call; the prompt call must see
        # the record already claimed (claim persisted before delivery).
        snapshot = json.loads(
            (self.root / "fake-herdr.state-during-send.json").read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["resume_pending"]["status"], "claimed")

        self.clear_calls()
        again = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "plugin")
        self.assertEqual(again.returncode, 2, again.stderr + again.stdout)
        self.assertEqual(json.loads(again.stdout)["reason"], "not_claimable")
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

    def test_resume_deliver_uncertain_retry_then_expired_notifies_once(self) -> None:
        self.arm_and_advance_epoch()
        self.set_herdr_mode("fail")
        self.clear_calls()

        first = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(json.loads(first.stdout)["status"], "resume_uncertain")
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["last_error"], "agent_not_found")

        self.clear_calls()
        second = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "plugin")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(json.loads(second.stdout)["status"], "resume_expired")
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "expired")
        self.assertEqual(record["attempts"], 2)
        notes = [c for c in self.herdr_calls() if c[:2] == ["notification", "show"]]
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertTrue(notes[0][2])
        self.assertEqual(notes[0][3], "--body")
        self.assertTrue(notes[0][4])

        self.clear_calls()
        third = self.invoke("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(third.returncode, 2, third.stderr + third.stdout)
        self.assertEqual(json.loads(third.stdout)["reason"], "not_claimable")
        calls = self.herdr_calls()
        self.assertFalse([c for c in calls if c[:2] == ["agent", "prompt"]], calls)
        self.assertFalse([c for c in calls if c[:2] == ["notification", "show"]], calls)

    def test_send_round_cancels_resume_record_after_epoch_advance(self) -> None:
        self.arm_and_advance_epoch()
        sent = self.send_round(1)
        self.assertEqual(sent["status"], "round_sent")
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["cancel_reason"], "send_round")
        self.assertTrue(record["cancelled_at"])

    def test_rollover_new_cancels_resume_record_after_epoch_advance(self) -> None:
        self.arm_and_advance_epoch()
        rolled = self.invoke_ok(
            "rollover", "--reason", "new", "--new-session-id", "session-b", "--force",
        )
        self.assertEqual(rolled["session_id"], "session-b")
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["cancel_reason"], "rollover_new")
        self.assertTrue(record["cancelled_at"])

    def test_adopt_contract_cancels_resume_record_after_epoch_advance(self) -> None:
        v1_state = self.legacy_v1_state()
        (self.state / "state.json").write_text(json.dumps(v1_state, indent=2), encoding="utf-8")

        queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(queued["queued"], queued)
        armed_at = self.read_state()["resume_pending"]["armed_at"]
        self.invoke_ok("rollover", "--reason", "compact")
        self.assertEqual(self.read_state()["resume_pending"]["armed_at"], armed_at)

        handoff = self.write_handoff("recovery.md", "Adopted contract for r2\n")
        adopted = self.invoke_ok(
            "adopt-contract",
            "--round-id", "p01-r002",
            "--file", str(handoff),
            "--scope", "adopted-scope",
            "--acceptance", "adopted-acceptance",
        )
        self.assertEqual(adopted["status"], "contract_adopted")
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["cancel_reason"], "adopt_contract")
        self.assertTrue(record["cancelled_at"])

    def test_resume_prompt_is_identical_to_legacy_watcher(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            **self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "0",
        })
        self.assertTrue(queued["queued"], queued)
        # Issue #9: the watch loop delivers only after the epoch advanced.
        self.invoke_ok("rollover", "--reason", "compact")
        self.set_status("idle")
        self.clear_calls()
        watched = self.invoke_ok(
            "watch-compact-continue",
            extra_env={"PAIRCTL_CONTINUE_POLL_S": "0.05"},
        )
        self.assertEqual(watched["status"], "continue_prompted")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        legacy_text = prompts[0][3]

        # The watch path claims through resume-deliver; rewind the record to pending so
        # the manual deliver below exercises the same prompt text.
        state_path = self.state / "state.json"
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["resume_pending"]["status"], "delivered")
        doc["resume_pending"]["status"] = "pending"
        state_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.clear_calls()
        delivered = self.invoke_ok("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        self.assertEqual(delivered["status"], "resume_delivered")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(prompts[0][3], legacy_text)


    def test_mechanism_plugin_only_when_linked_enabled_and_claude(self) -> None:
        # issue #9: mechanism is decided only on a new resume_pending write.
        plugins = Path(str(self.herdr) + ".plugins.json")
        linked = [{"plugin_id": "tc.herdr-pair", "enabled": True}]
        cases = (
            # (planner kind, plugins.json content, expected mechanism)
            ("claude", linked, "plugin"),
            ("claude", [{"plugin_id": "tc.herdr-pair", "enabled": False}], "watcher"),
            ("claude", [{"plugin_id": "someone.else", "enabled": True}], "watcher"),
            ("claude", None, "watcher"),                     # file missing -> empty list
            ("claude", "{not json at all", "watcher"),       # list exits non-zero
            ("pi", linked, "watcher"),                       # not Claude -> watcher
        )
        for index, (kind, content, expected) in enumerate(cases):
            with self.subTest(kind=kind, content=content, expected=expected):
                self.set_kind(kind)
                if content is None:
                    plugins.unlink(missing_ok=True)
                elif isinstance(content, str):
                    plugins.write_text(content, encoding="utf-8")
                else:
                    plugins.write_text(json.dumps(content), encoding="utf-8")
                if index:
                    # A different epoch => the next arm is a fresh write and re-probes.
                    self.invoke_ok("rollover", "--reason", "compact")
                queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
                self.assertTrue(queued["queued"], queued)
                record = self.read_state()["resume_pending"]
                self.assertEqual(record["mechanism"], expected)

    def test_continue_disabled_skips_record_and_watcher(self) -> None:
        # invoke() defaults PAIRCTL_CONTINUE_AFTER_COMPACT=0: compact still queues,
        # but no record is written, no watcher spawns, and no probe runs.
        self.clear_calls()
        queued = self.invoke_ok("compact-self")
        self.assertTrue(queued["queued"], queued)
        self.assertFalse(queued["continue_after_compact"]["spawned"], queued)
        self.assertEqual(queued["continue_after_compact"]["reason"], "disabled")
        self.assertIsNone(self.read_state().get("resume_pending"))
        self.assertFalse((self.state / "compact-continue.pid").exists())
        self.assertFalse(
            [c for c in self.herdr_calls() if c[:2] == ["plugin", "list"]],
            self.herdr_calls(),
        )

    def test_same_epoch_requeue_updates_deadline_only(self) -> None:
        first = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(first["queued"], first)
        record = self.read_state()["resume_pending"]
        armed_at = record["armed_at"]
        mechanism = record["mechanism"]
        first_deadline = record["deadline"]
        # Leftover delivery state must survive a same-epoch requeue untouched.
        state_path = self.state / "state.json"
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        doc["resume_pending"]["attempts"] = 1
        doc["resume_pending"]["last_error"] = "prior failure"
        state_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        requeue_at = time.time()
        requeued = self.invoke_ok(
            "compact-self",
            extra_env={**self.RECORD_ENV, "PAIRCTL_RESUME_DEADLINE_S": "30"},
        )
        self.assertTrue(requeued["queued"], requeued)
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["armed_at"], armed_at)
        self.assertEqual(record["mechanism"], mechanism)
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["last_error"], "prior failure")
        self.assertNotEqual(record["deadline"], first_deadline)
        deadline_ts = dt.datetime.fromisoformat(record["deadline"]).timestamp()
        self.assertGreaterEqual(deadline_ts, requeue_at + 28)
        self.assertLessEqual(deadline_ts, time.time() + 31)
        # A requeue is not a new write: no watcher is spawned for it.
        self.assertFalse(requeued["continue_after_compact"]["spawned"], requeued)
        self.assertFalse((self.state / "compact-continue.pid").exists())

    def test_watcher_delivers_once_at_deadline_and_records_mechanism(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
            "PAIRCTL_RESUME_DEADLINE_S": "0",
            "PAIRCTL_CONTINUE_POLL_S": "0.05",
        })
        self.assertTrue(queued["queued"], queued)
        self.assertTrue(queued["continue_after_compact"]["spawned"], queued)
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["mechanism"], "watcher")
        self.set_status("idle")
        # The spawned watcher must stay quiet until the epoch advances.
        quiet_until = time.time() + 0.5
        while time.time() < quiet_until:
            self.assertEqual(self.auto_continue_prompts(), [], self.herdr_calls())
            time.sleep(0.05)
        self.invoke_ok("rollover", "--reason", "compact")
        deadline = time.time() + 5
        prompts: list[list[str]] = []
        while time.time() < deadline:
            prompts = self.auto_continue_prompts()
            if prompts:
                break
            time.sleep(0.05)
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        time.sleep(0.3)
        self.assertEqual(len(self.auto_continue_prompts()), 1, self.herdr_calls())
        record = self.read_state()["resume_pending"]
        self.assertEqual(record["status"], "delivered")
        self.assertEqual(record["mechanism"], "watcher")

    def test_concurrent_resume_deliver_single_prompt(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(queued["queued"], queued)
        # The test drives delivery manually: pairctl must not spawn a watcher too.
        self.assertFalse(queued["continue_after_compact"]["spawned"], queued)
        self.invoke_ok("rollover", "--reason", "compact")
        self.set_status("idle")
        self.clear_calls()
        first = self.popen("resume-deliver", "--pane", "w1:p1", "--via", "watcher")
        second = self.popen("resume-deliver", "--pane", "w1:p1", "--via", "plugin")
        outputs = []
        for proc in (first, second):
            out, err = proc.communicate(timeout=30)
            outputs.append((proc.returncode, out, err))
        delivered = [
            o for o in outputs
            if o[0] == 0 and json.loads(o[1]).get("status") == "resume_delivered"
        ]
        rejected = [
            o for o in outputs
            if o[0] == 2 and json.loads(o[1]).get("status") == "rejected"
        ]
        self.assertEqual(len(delivered), 1, outputs)
        self.assertEqual(len(rejected), 1, outputs)
        self.assertEqual(json.loads(rejected[0][1])["reason"], "not_claimable")
        prompts = [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

    def test_watcher_child_env_uses_internal_marker(self) -> None:
        queued = self.invoke_ok("compact-self", extra_env={
            "PAIRCTL_CONTINUE_AFTER_COMPACT": "1",
            "PAIRCTL_RESUME_DEADLINE_S": "0",
            "PAIRCTL_CONTINUE_POLL_S": "0.05",
        })
        self.assertTrue(queued["queued"], queued)
        self.assertTrue(queued["continue_after_compact"]["spawned"], queued)
        pid = int((self.state / "compact-continue.pid").read_text(encoding="utf-8").strip())
        # Retry briefly: /proc/<pid>/environ shows the parent env until exec completes.
        environ = b""
        read_deadline = time.time() + 5
        while time.time() < read_deadline:
            try:
                environ = Path(f"/proc/{pid}/environ").read_bytes()
            except OSError:
                environ = b""
            if b"PAIRCTL_INTERNAL_WATCHER=1" in environ:
                break
            time.sleep(0.05)
        self.assertIn(b"PAIRCTL_INTERNAL_WATCHER=1", environ)
        self.assertNotIn(b"PAIRCTL_CONTINUE_AFTER_COMPACT=0", environ)


    # --- pane index + plugin resume hook (issue #10) ----------------------------------

    PLUGIN_STUB_NAME = "pairctl-stub.log.jsonl"

    def pane_index_path(self, pane_id: str) -> Path:
        return self.state_home / "herdr-pair" / "panes" / f"{pane_id}.json"

    def read_pane_index(self, pane_id: str) -> list[dict]:
        return json.loads(self.pane_index_path(pane_id).read_text(encoding="utf-8"))

    def write_pane_index(self, pane_id: str, entries: list[dict]) -> None:
        path = self.pane_index_path(pane_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def fresh_stamp(self, hours: float = 0.0, seconds: float = 0.0) -> str:
        stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours, seconds=seconds)
        return stamp.replace(microsecond=0).isoformat()

    def write_pairctl_stub(self) -> Path:
        """PAIRCTL target for the plugin hook: record the exact resume-deliver argv,
        then exec the real pairctl so the delivery path still runs end to end."""
        stub = self.root / "pairctl-stub.py"
        stub.write_text(
            "import json, os, sys\n"
            f"log = {str(self.root / self.PLUGIN_STUB_NAME)!r}\n"
            "with open(log, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')\n"
            f"os.execv(sys.executable, [sys.executable, {str(SCRIPT)!r}] + sys.argv[1:])\n",
            encoding="utf-8",
        )
        return stub

    def stub_calls(self) -> list[list[str]]:
        path = self.root / self.PLUGIN_STUB_NAME
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def run_resume_hook(
        self,
        *,
        pane_id: str = "w1:p1",
        agent_status: str = "idle",
        event: str = "pane.agent_status_changed",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL", None)
        env.pop("PAIRCTL_SESSION_STALE_HOURS", None)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        env["HERDR_PLUGIN_EVENT"] = event
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({
            "pane_id": pane_id,
            "workspace_id": "ws-1",
            "agent_status": agent_status,
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(PLUGIN_HOOK)],
            text=True, capture_output=True, check=False, env=env,
        )

    def test_pane_index_five_write_points_include_custom_state_dir(self) -> None:
        # setUp already ran `init --planner-pane w1:p1 --state-dir self.state`.
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(
            set(entries[0]), {"state_dir", "cwd", "role", "recorded_at"}, entries[0]
        )
        self.assertEqual(entries[0]["state_dir"], str(self.state.resolve()))
        self.assertEqual(entries[0]["cwd"], str(self.cwd.resolve()))
        self.assertEqual(entries[0]["role"], "planner")
        initial_stamp = dt.datetime.fromisoformat(entries[0]["recorded_at"])

        # write point 2: note-session --pane refreshes the same state_dir + role
        self.invoke_ok(
            "note-session", "--session-id", "sess-index", "--kind", "claude",
            "--source", "test", "--pane", "w1:p1",
        )
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(len(entries), 1, entries)  # updated in place, not appended

        # write point 4 (runs before 3: a queued compact would block send-round):
        # send-round --target binds the executor pane
        sent = self.send_round(1)
        self.assertEqual(sent["status"], "round_sent")
        executor_entries = self.read_pane_index("w1:p2")
        self.assertEqual(len(executor_entries), 1, executor_entries)
        self.assertEqual(executor_entries[0]["role"], "executor")
        self.assertEqual(executor_entries[0]["state_dir"], str(self.state.resolve()))
        self.assertEqual(executor_entries[0]["cwd"], str(self.cwd.resolve()))

        # write point 3: compact-self records the planner pane it actually used
        queued = self.invoke_ok("compact-self")
        self.assertTrue(queued["queued"], queued)
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["role"], "planner")
        self.assertGreaterEqual(
            dt.datetime.fromisoformat(entries[0]["recorded_at"]), initial_stamp
        )

        # write point 5: rollover records the state's planner_pane
        rolled = self.invoke_ok("rollover", "--reason", "compact")
        self.assertEqual(rolled["compaction_epoch"], 1)
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(len(entries), 1, entries)

        # a custom --state-dir is its own entry, keyed by state_dir + role
        custom = self.root / "custom-state"
        self.invoke_ok(
            "init", "--planner-pane", "w1:p1", "--state-dir", str(custom),
            use_state_dir=False,
        )
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(
            sorted(e["state_dir"] for e in entries),
            sorted([str(self.state.resolve()), str(custom.resolve())]),
        )
        # rewriting the default entry moves it to the end instead of adding a third
        self.invoke_ok(
            "note-session", "--session-id", "sess-index-2", "--kind", "claude",
            "--source", "test", "--pane", "w1:p1",
        )
        entries = self.read_pane_index("w1:p1")
        self.assertEqual(len(entries), 2, entries)
        self.assertEqual(entries[-1]["state_dir"], str(self.state.resolve()))
        self.assertEqual(entries[0]["state_dir"], str(custom.resolve()))

        # an empty pane id writes nothing at all
        panes_dir = self.state_home / "herdr-pair" / "panes"
        before = sorted(p.name for p in panes_dir.iterdir())
        self.invoke_ok(
            "init", "--state-dir", str(self.root / "paneless-state"),
            use_state_dir=False,
        )
        self.assertEqual(sorted(p.name for p in panes_dir.iterdir()), before)

    def test_pane_index_entry_expires_after_stale_hours(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        stub = self.write_pairctl_stub()

        # Age the planner entry past DEFAULT_STALE_HOURS (12 h): treated as absent.
        entries = self.read_pane_index("w1:p1")
        entries[0]["recorded_at"] = self.fresh_stamp(hours=13)
        self.write_pane_index("w1:p1", entries)
        hook = self.run_resume_hook(extra_env={"PAIRCTL": str(stub)})
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        self.assertEqual(self.stub_calls(), [])

        # PAIRCTL_SESSION_STALE_HOURS narrows the window: 3 h old with a 1 h limit expires.
        entries[0]["recorded_at"] = self.fresh_stamp(hours=3)
        self.write_pane_index("w1:p1", entries)
        hook = self.run_resume_hook(
            extra_env={"PAIRCTL": str(stub), "PAIRCTL_SESSION_STALE_HOURS": "1"}
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        self.assertEqual(self.stub_calls(), [])

        # A fresh rewrite (note-session) makes the same idle event deliverable again.
        self.invoke_ok(
            "note-session", "--session-id", "sess-stale", "--kind", "claude",
            "--source", "test", "--pane", "w1:p1",
        )
        hook = self.run_resume_hook(extra_env={"PAIRCTL": str(stub)})
        self.assertEqual(hook.returncode, 0, hook.stderr)
        calls = self.stub_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][0], "resume-deliver")
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

    def test_resume_hook_calls_resume_deliver_once_when_idle_after_epoch(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.clear_calls()
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}

        hook = self.run_resume_hook(extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        calls = self.stub_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0], [
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin",
            "--cwd", str(self.cwd.resolve()),
            "--state-dir", str(self.state.resolve()),
        ])
        # The stub exec'd the real pairctl: exactly one resume prompt reached herdr.
        prompts = self.auto_continue_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(prompts[0][2], "w1:p1")
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

        # The next idle edge after delivery is a no-op: still exactly one call.
        hook = self.run_resume_hook(extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        self.assertEqual(len(self.stub_calls()), 1)

    def test_resume_hook_silent_on_miss_and_mismatch(self) -> None:
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-silent"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}

        def assert_silent(hook: subprocess.CompletedProcess[str], why: str) -> None:
            self.assertEqual(hook.returncode, 0, f"{why}: {hook.stderr}")
            self.assertEqual(hook.stdout, "", why)
            self.assertEqual(hook.stderr, "", why)
            self.assertEqual(self.stub_calls(), [], why)

        # zero hit: this pane was never recorded
        assert_silent(self.run_resume_hook(pane_id="wZ:zz", extra_env=env), "zero hit")
        # wrong event name
        assert_silent(self.run_resume_hook(event="pane.exited", extra_env=env), "wrong event")
        # agent_status outside idle/done
        assert_silent(self.run_resume_hook(agent_status="working", extra_env=env), "busy")
        # index hit but no resume record at all
        assert_silent(self.run_resume_hook(extra_env=env), "no record")
        # armed record whose epoch has not advanced yet
        armed = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(armed["queued"], armed)
        assert_silent(self.run_resume_hook(extra_env=env), "epoch not advanced")

        self.invoke_ok("rollover", "--reason", "compact")
        # planner-role entry pointing at a state whose resume names another pane
        self.write_pane_index("w1:p9", [{
            "state_dir": str(self.state.resolve()),
            "cwd": str(self.cwd.resolve()),
            "role": "planner",
            "recorded_at": self.fresh_stamp(),
        }])
        assert_silent(
            self.run_resume_hook(pane_id="w1:p9", extra_env=env), "planner_pane mismatch"
        )

        # executor-role hit: the state would otherwise match this pane completely
        state = self.read_state()
        state["resume_pending"]["planner_pane"] = "w1:p2"
        self.write_state(state)
        self.write_pane_index("w1:p2", [{
            "state_dir": str(self.state.resolve()),
            "cwd": str(self.cwd.resolve()),
            "role": "executor",
            "recorded_at": self.fresh_stamp(),
        }])
        assert_silent(self.run_resume_hook(pane_id="w1:p2", extra_env=env), "executor role")
        # nothing claimed, delivered or logged along the way
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")
        self.assertFalse((plugin_state / "hook.log").exists())

    def test_resume_hook_ambiguous_pane_logs_and_does_not_deliver(self) -> None:
        self.arm_and_advance_epoch()
        self.set_status("idle")
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-amb"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}

        other = self.root / "other-state"
        other.mkdir(parents=True, exist_ok=True)
        (other / "state.json").write_text(json.dumps({
            "resume_pending": {
                "planner_pane": "w1:p1", "status": "pending", "epoch_at_arm": 0,
            },
            "compaction_epoch": 1,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        shared = self.fresh_stamp()
        self.write_pane_index("w1:p1", [
            {"state_dir": str(self.state.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": shared},
            {"state_dir": str(other.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": shared},
        ])
        hook = self.run_resume_hook(extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        self.assertEqual(self.stub_calls(), [])  # no resume-deliver
        log_text = (plugin_state / "hook.log").read_text(encoding="utf-8")
        self.assertIn("ambiguous_pane", log_text)
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")

        # When recorded_at differ the newest wins: one state_dir, no ambiguity.
        self.write_pane_index("w1:p1", [
            {"state_dir": str(other.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": self.fresh_stamp(seconds=5)},
            {"state_dir": str(self.state.resolve()), "cwd": str(self.cwd.resolve()),
             "role": "planner", "recorded_at": shared},
        ])
        hook = self.run_resume_hook(extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        calls = self.stub_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0], [
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin",
            "--cwd", str(self.cwd.resolve()),
            "--state-dir", str(self.state.resolve()),
        ])
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

    def test_plugin_manifest_declares_resume_hook_only(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "herdr-plugin.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("id"), "tc.herdr-pair")
        self.assertTrue(str(manifest.get("name") or "").strip())
        self.assertTrue(str(manifest.get("version") or "").strip())
        self.assertEqual(manifest.get("min_herdr_version"), "0.8.0")
        self.assertEqual(manifest.get("platforms"), ["linux", "macos"])
        events = manifest.get("events")
        self.assertIsInstance(events, list)
        self.assertEqual(len(events), 2, events)
        self.assertEqual(events[0].get("on"), "pane.agent_status_changed")
        command = events[0].get("command")
        self.assertIsInstance(command, list)
        self.assertTrue(command, command)
        hook_args = [part for part in command if part.endswith("on_planner_status.py")]
        self.assertEqual(len(hook_args), 1, command)
        resolved = (manifest_path.parent / hook_args[0]).resolve()
        self.assertEqual(resolved, PLUGIN_HOOK.resolve())
        self.assertTrue(resolved.is_file())
        # issue #12: the second subscription is the pane-exit hook.
        self.assertEqual(events[1].get("on"), "pane.exited")
        exited_command = events[1].get("command")
        self.assertIsInstance(exited_command, list)
        self.assertTrue(exited_command, exited_command)
        exited_args = [part for part in exited_command if part.endswith("on_pane_exited.py")]
        self.assertEqual(len(exited_args), 1, exited_command)
        exited_resolved = (manifest_path.parent / exited_args[0]).resolve()
        self.assertEqual(exited_resolved, self.PANE_EXITED_HOOK.resolve())
        self.assertTrue(exited_resolved.is_file())
        # Round p01-r005 adds [[actions]] (asserted by
        # test_plugin_manifest_lists_section6_actions); issue #14 adds the
        # pair-status popup pane and the startup replay entry (asserted by
        # test_plugin_manifest_lists_popup_pane_and_startup).
        # herdr 0.8.0 rejects action ids containing dots (issue #16): the ids
        # are hyphenated while the command still passes the dotted action name.
        self.assertEqual(
            [a.get("id") for a in manifest.get("actions") or []][0], "pair-status"
        )

    # --- issue #11: notices, wake, lock timeout, hook failure path ------------------

    def notification_calls(self) -> list[list[str]]:
        """argv tails of every `herdr notification show` this case produced."""
        return [c for c in self.herdr_calls() if c[:2] == ["notification", "show"]]

    def set_pending_dispatch(self, age_s: float, round_id: str = "p01-r007") -> str:
        """Hand-write a pending_dispatch created age_s seconds ago; returns created_at.

        send-round clears its own pending record on every tested path, so the
        staleness cases plant the record directly in state.json instead.
        """
        created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)
        ).replace(microsecond=0).isoformat()
        state = self.read_state()
        state["pending_dispatch"] = {
            "status": "uncertain",
            "counted": False,
            "round_id": round_id,
            "target": "w1:p2",
            "executor": "w1:p2",
            "scope": "stale-scope",
            "acceptance": "stale-acceptance",
            "handoff": str(self.cwd / "stale.md"),
            "phase_round_count": 0,
            "created_at": created_at,
        }
        self.write_state(state)
        return created_at

    def test_notice_recorded_when_notification_is_rate_limited(self) -> None:
        # Two failed prompts expire the record; the expired notification goes
        # through the notice helper, which must record the suppression itself.
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.set_herdr_mode("fail")
        self.clear_calls()
        env = {"FAKE_HERDR_NOTIFY_REASON": "rate_limited"}

        first = self.invoke(
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin", extra_env=env,
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(json.loads(first.stdout)["status"], "resume_uncertain")

        second = self.invoke(
            "resume-deliver", "--pane", "w1:p1", "--via", "plugin", extra_env=env,
        )
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(json.loads(second.stdout)["status"], "resume_expired")

        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair resume expired")

        notices = self.read_state()["notices"]
        self.assertEqual(len(notices), 1, notices)
        record = notices[0]
        self.assertEqual(
            set(record), {"at", "title", "body", "reason", "shown", "dedupe_key"},
        )
        self.assertEqual(record["title"], "herdr-pair resume expired")
        self.assertEqual(record["reason"], "rate_limited")
        self.assertIs(record["shown"], False)
        self.assertTrue(record["body"])
        self.assertTrue(record["at"])
        self.assertEqual(record["dedupe_key"], "")

        # status carries the whole notices array
        shown_status = self.invoke_ok("status")
        self.assertEqual(shown_status["notices"], notices)

    def test_missing_pairctl_logs_and_notifies_once(self) -> None:
        # A selected candidate whose pairctl cannot run: log pairctl_failed, show
        # the host notification exactly once (marker file), exit 1 - never silent.
        self.arm_and_advance_epoch()
        self.clear_calls()
        plugin_state = self.root / "plugin-state-fail"
        env = {
            "PAIRCTL": str(self.root / "no-such-pairctl.py"),
            "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
        }

        first = self.run_resume_hook(extra_env=env)
        self.assertEqual(first.returncode, 1, first.stderr + first.stdout)
        log_text = (plugin_state / "hook.log").read_text(encoding="utf-8")
        self.assertIn("pairctl_failed", log_text)
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair pairctl failed")
        self.assertTrue((plugin_state / "pairctl-failed-notified").is_file())
        # the pairing state itself is untouched: the hook never writes it
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")

        # The next event only logs: the marker suppresses a second notification.
        self.clear_calls()
        second = self.run_resume_hook(extra_env=env)
        self.assertEqual(second.returncode, 1, second.stderr + second.stdout)
        self.assertIn(
            "pairctl_failed",
            (plugin_state / "hook.log").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.notification_calls(), [])

        # Non-JSON pairctl output is the same failure class; still no second notice.
        junk = self.root / "junk-pairctl.py"
        junk.write_text("print('definitely not json')\n", encoding="utf-8")
        self.clear_calls()
        third = self.run_resume_hook(
            extra_env={"PAIRCTL": str(junk), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)},
        )
        self.assertEqual(third.returncode, 1, third.stderr + third.stdout)
        self.assertIn(
            "pairctl_failed",
            (plugin_state / "hook.log").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.notification_calls(), [])

    def test_no_pane_match_stays_silent(self) -> None:
        # Issue #10 silence contract: no pairing matches this pane, so the hook
        # must not run pairctl at all - even though PAIRCTL points at nothing.
        plugin_state = self.root / "plugin-state-silent"
        env = {
            "PAIRCTL": str(self.root / "no-such-pairctl.py"),
            "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
        }
        self.clear_calls()
        hook = self.run_resume_hook(pane_id="wZ:zz", extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        self.assertFalse((plugin_state / "hook.log").exists())
        self.assertFalse((plugin_state / "pairctl-failed-notified").exists())
        self.assertEqual(self.herdr_calls(), [])

    def test_hook_silent_when_pairctl_reports_lock_timeout(self) -> None:
        # Issue #10 silence contract: a bounded lock makes resume-deliver answer
        # the exact lock_timeout JSON with exit 2. JSON on stdout is a completed
        # answer, not a pairctl failure - the hook stays exit 0 and silent, and
        # the released lock lets the next event deliver for real.
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.clear_calls()
        plugin_state = self.root / "plugin-state-locktime"
        lock = self.state / "state.lock"
        holder = subprocess.Popen(
            [
                "python3", "-c",
                "import fcntl, sys, time\n"
                "handle = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(handle, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(30)\n",
                str(lock),
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            before = (self.state / "state.json").read_text(encoding="utf-8")
            hook = self.run_resume_hook(extra_env={
                "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
                "PAIRCTL_LOCK_WAIT_S": "0.2",
            })
            self.assertEqual(hook.returncode, 0, hook.stderr + hook.stdout)
            self.assertEqual(hook.stdout, "")
            self.assertEqual(hook.stderr, "")
            log = plugin_state / "hook.log"
            logged = log.read_text(encoding="utf-8") if log.exists() else ""
            self.assertNotIn("pairctl_failed", logged)
            self.assertFalse((plugin_state / "pairctl-failed-notified").exists())
            self.assertEqual(self.notification_calls(), [])
            # abandoned event: the pairing state is byte-identical
            self.assertEqual(
                (self.state / "state.json").read_text(encoding="utf-8"), before,
            )
            self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=5)
        # lock released: the next event resumes-deliver instead of staying stuck
        self.clear_calls()
        retry = self.run_resume_hook(
            extra_env={"HERDR_PLUGIN_STATE_DIR": str(plugin_state)},
        )
        self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
        self.assertEqual(self.read_state()["resume_pending"]["status"], "delivered")

    def test_hook_silent_when_pairctl_prints_json_and_exits_nonzero(self) -> None:
        # A JSON object on stdout is a completed answer whatever the exit code:
        # planner_busy and lock_timeout both exit 2, yet neither is a failure.
        self.arm_and_advance_epoch()
        self.set_status("idle")
        self.clear_calls()
        plugin_state = self.root / "plugin-state-json2"
        for reason in ("planner_busy", "lock_timeout"):
            fake = self.root / f"pairctl-{reason}.py"
            fake.write_text(
                "import json, sys\n"
                f"print(json.dumps({{'status': 'rejected', 'reason': '{reason}'}}))\n"
                "sys.exit(2)\n",
                encoding="utf-8",
            )
            hook = self.run_resume_hook(extra_env={
                "PAIRCTL": str(fake),
                "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
            })
            self.assertEqual(hook.returncode, 0, hook.stderr + hook.stdout)
            self.assertEqual(hook.stdout, "")
            self.assertEqual(hook.stderr, "")
            log = plugin_state / "hook.log"
            logged = log.read_text(encoding="utf-8") if log.exists() else ""
            self.assertNotIn("pairctl_failed", logged, reason)
            self.assertFalse((plugin_state / "pairctl-failed-notified").exists())
            self.assertEqual(self.notification_calls(), [])
            self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")

    def test_rate_limited_stale_dispatch_still_reports_notified(self) -> None:
        # record_notice returns "a new notice was recorded", not "the host showed
        # it": a rate-limited delivery is still a notice, so wake reports True.
        self.set_pending_dispatch(age_s=1000)
        self.clear_calls()
        first = self.invoke(
            "wake", "--source", "command",
            extra_env={"FAKE_HERDR_NOTIFY_REASON": "rate_limited"},
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        payload = json.loads(first.stdout)
        self.assertTrue(payload["dispatch_stale_notified"], payload)
        notices = self.read_state()["notices"]
        self.assertEqual(len(notices), 1, notices)
        self.assertEqual(notices[0]["reason"], "rate_limited")
        self.assertIs(notices[0]["shown"], False)

        # the dedupe key now recorded makes the second wake quiet: False again
        self.clear_calls()
        second = self.invoke("wake", "--source", "command")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertFalse(json.loads(second.stdout)["dispatch_stale_notified"])
        self.assertEqual(len(self.read_state()["notices"]), 1)

    def test_lock_timeout_abandons_event_and_later_wake_retries(self) -> None:
        lock = self.state / "state.lock"
        holder = subprocess.Popen(
            [
                "python3", "-c",
                "import fcntl, sys, time\n"
                "handle = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(handle, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(30)\n",
                str(lock),
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            before = (self.state / "state.json").read_text(encoding="utf-8")
            self.clear_calls()
            timed = self.invoke(
                "wake", "--source", "hook",
                extra_env={"PAIRCTL_LOCK_WAIT_S": "1"},
            )
            self.assertEqual(timed.returncode, 2, timed.stderr + timed.stdout)
            self.assertEqual(
                timed.stdout, '{"status":"rejected","reason":"lock_timeout"}',
            )
            self.assertEqual(timed.stderr, "")
            # abandoned event: state byte-identical, no notification attempted
            self.assertEqual(
                (self.state / "state.json").read_text(encoding="utf-8"), before,
            )
            self.assertEqual(self.read_state()["notices"], [])
            self.assertEqual(self.herdr_calls(), [])
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=5)
        # lock released: the next wake retries cleanly instead of staying stuck
        self.clear_calls()
        retry = self.invoke(
            "wake", "--source", "hook", extra_env={"PAIRCTL_LOCK_WAIT_S": "1"},
        )
        self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
        payload = json.loads(retry.stdout)
        self.assertEqual(payload["status"], "wake_checked")
        self.assertFalse(payload["dispatch_stale_notified"])
        self.assertEqual(payload["notices"], [])
        self.assertEqual(self.read_state()["notices"], [])
        self.assertEqual(self.herdr_calls(), [])

    def test_stale_dispatch_notifies_once(self) -> None:
        created_at = self.set_pending_dispatch(age_s=1000)
        self.clear_calls()

        first = self.invoke("wake", "--source", "command")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair dispatch stale")

        notices = self.read_state()["notices"]
        self.assertEqual(len(notices), 1, notices)
        record = notices[0]
        self.assertEqual(
            set(record), {"at", "title", "body", "reason", "shown", "dedupe_key"},
        )
        self.assertEqual(record["title"], "herdr-pair dispatch stale")
        self.assertEqual(record["dedupe_key"], f"dispatch-stale:p01-r007:{created_at}")
        self.assertEqual(record["reason"], "manual")
        self.assertIs(record["shown"], True)
        self.assertTrue(record["body"])
        self.assertTrue(record["at"])

        # every later wake point stays quiet: same key, no second herdr call
        self.clear_calls()
        second = self.invoke("wake", "--source", "hook")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(self.notification_calls(), [])
        self.assertEqual(len(self.read_state()["notices"]), 1)

        # status runs the same check, then reports the pending dispatch with notices
        blocked = self.invoke("status")
        self.assertEqual(blocked.returncode, 2, blocked.stderr + blocked.stdout)
        payload = json.loads(blocked.stdout)
        self.assertEqual(payload["status"], "PENDING_DISPATCH_UNRESOLVED")
        self.assertEqual(payload["notices"], notices)
        self.assertEqual(self.notification_calls(), [])

        # and the normal (no pending) status still exposes the same array
        state = self.read_state()
        state["pending_dispatch"] = None
        self.write_state(state)
        ok = self.invoke_ok("status")
        self.assertEqual(ok["notices"], notices)

        # herdr itself failing still records the notice instead of vanishing.
        # The stale check only runs when a pending dispatch exists, so plant a
        # fresh stale record again (the block above cleared it on purpose).
        self.set_pending_dispatch(age_s=1000)
        state = self.read_state()
        state["notices"] = []
        self.write_state(state)
        self.clear_calls()
        failed = self.invoke(
            "wake", "--source", "status",
            extra_env={"PAIRCTL_HERDR": str(self.root / "no-such-herdr")},
        )
        self.assertEqual(failed.returncode, 0, failed.stderr + failed.stdout)
        self.assertEqual(self.notification_calls(), [])
        broken = self.read_state()["notices"]
        self.assertEqual(len(broken), 1, broken)
        self.assertEqual(broken[0]["reason"], "herdr_failed")
        self.assertIs(broken[0]["shown"], False)

    def test_dispatch_stale_threshold_is_configurable(self) -> None:
        # PAIRCTL_DISPATCH_STALE_S shrinks the window: 500 s old counts as stale.
        self.set_pending_dispatch(age_s=500)
        self.clear_calls()
        fast = self.invoke(
            "wake", "--source", "status",
            extra_env={"PAIRCTL_DISPATCH_STALE_S": "300"},
        )
        self.assertEqual(fast.returncode, 0, fast.stderr + fast.stdout)
        self.assertEqual(len(self.notification_calls()), 1, self.herdr_calls())

        # An unparsable threshold falls back to the 900 s default: 500 s is fresh.
        state = self.read_state()
        state["notices"] = []
        self.write_state(state)
        self.clear_calls()
        fallback = self.invoke(
            "wake", "--source", "command",
            extra_env={"PAIRCTL_DISPATCH_STALE_S": "banana"},
        )
        self.assertEqual(fallback.returncode, 0, fallback.stderr + fallback.stdout)
        self.assertEqual(self.notification_calls(), [])
        self.assertEqual(self.read_state()["notices"], [])

        # Unset threshold keeps the documented 900 s default: 1000 s is stale.
        created = self.set_pending_dispatch(age_s=1000, round_id="p01-r008")
        self.clear_calls()
        default = self.invoke("wake", "--source", "watcher")
        self.assertEqual(default.returncode, 0, default.stderr + default.stdout)
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        notices = self.read_state()["notices"]
        self.assertEqual(len(notices), 1, notices)
        self.assertEqual(
            notices[0]["dedupe_key"], f"dispatch-stale:p01-r008:{created}",
        )


    # --- executor reports, pane exits, executor-event (issue #12) ----------------------

    PANE_EXITED_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "on_pane_exited.py"
    # Consecutive notices of one round are normally at least 60 s apart; the cases
    # that deliberately push several times shrink the interval to zero.
    ZERO_INTERVAL = {"PAIRCTL_EXECUTOR_NOTICE_MIN_S": "0"}

    def executor_event(
        self, status: str, *, pane: str = "w1:p2", extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.invoke(
            "executor-event", "--pane", pane, "--status", status, extra_env=extra_env,
        )

    def executor_prompts(self) -> list[list[str]]:
        """argv tails of every short report pairctl sent to the planner pane."""
        return [
            c for c in self.herdr_calls()
            if c[:2] == ["agent", "prompt"] and len(c) > 3
            and str(c[3]).startswith("herdr-pair executor")
        ]

    def run_exited_hook(
        self, *, pane_id: str = "w1:p2", extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL", None)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        env["HERDR_PLUGIN_EVENT"] = "pane.exited"
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({
            "pane_id": pane_id,
            "workspace_id": "ws-1",
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(self.PANE_EXITED_HOOK)],
            text=True, capture_output=True, check=False, env=env,
        )

    def clear_stub_calls(self) -> None:
        path = self.root / self.PLUGIN_STUB_NAME
        if path.exists():
            path.unlink()

    def test_executor_done_and_blocked_prompt_and_notice(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()

        done = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(json.loads(done.stdout)["status"], "notice_sent")
        prompts = self.executor_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(prompts[0][2], "w1:p1")  # the short report reaches the planner
        self.assertEqual(
            prompts[0][3].splitlines()[0],
            f"herdr-pair executor done round {round_id} revision 1",
        )
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair executor done")
        self.assertEqual(notes[0][-2:], ["--sound", "done"])
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 1, state["notices"])
        record = state["notices"][0]
        self.assertEqual(record["title"], "herdr-pair executor done")
        self.assertEqual(record["dedupe_key"], f"executor:{round_id}:1:done:")
        self.assertTrue(record["body"])

        blocked = self.executor_event("blocked", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(blocked.returncode, 0, blocked.stderr + blocked.stdout)
        self.assertEqual(json.loads(blocked.stdout)["status"], "notice_sent")
        prompts = self.executor_prompts()
        self.assertEqual(len(prompts), 2, self.herdr_calls())
        self.assertEqual(
            prompts[1][3].splitlines()[0],
            f"herdr-pair executor blocked round {round_id} revision 1",
        )
        notes = self.notification_calls()
        self.assertEqual(len(notes), 2, self.herdr_calls())
        self.assertEqual(notes[1][2], "herdr-pair executor blocked")
        self.assertEqual(notes[1][-2:], ["--sound", "request"])
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 2, state["notices"])
        self.assertEqual(
            state["notices"][1]["dedupe_key"], f"executor:{round_id}:1:blocked:",
        )
        # the pane binding is untouched by a status report
        self.assertEqual(state["rounds"][0]["executor"], "w1:p2")
        self.assertNotIn("executor_pane_gone_at", state["rounds"][0])

    def test_executor_idle_pushes_only_when_report_changes(self) -> None:
        sent = self.send_round(1, body=(
            "[轮次] round_id=<unique-id>\n"
            "[报告] reports/executor-report.md\n"
            "produce that report file\n"
        ))
        round_id = sent["round_id"]
        report = Path(sent["report"])
        self.assertEqual(report, self.cwd.resolve() / "reports" / "executor-report.md")
        self.clear_calls()

        # No report file yet: an idle edge says nothing the planner needs.
        missing = self.executor_event("idle", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(missing.returncode, 0, missing.stderr + missing.stdout)
        self.assertEqual(json.loads(missing.stdout)["status"], "ignored")
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(self.read_state()["notices"], [])

        # The file appearing is news: one push, no sound, hash remembered.
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("version one\n", encoding="utf-8")
        first = self.executor_event("idle", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(json.loads(first.stdout)["status"], "notice_sent")
        prompts = self.executor_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(
            prompts[0][3].splitlines()[0],
            f"herdr-pair executor idle round {round_id} revision 1",
        )
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair executor idle")
        self.assertNotIn("--sound", notes[0])
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 1, state["notices"])
        self.assertEqual(
            state["rounds"][0]["last_executor_report_hash"],
            hashlib.sha256(b"version one\n").hexdigest(),
        )

        # Same bytes again: the report hash is unchanged, so nothing is pushed.
        self.clear_calls()
        unchanged = self.executor_event("idle", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr + unchanged.stdout)
        self.assertEqual(json.loads(unchanged.stdout)["status"], "ignored")
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(len(self.read_state()["notices"]), 1)

        # Changed bytes are news again: exactly one more push. (clear_calls above
        # reset the transcript, so this push is the only prompt in it; the
        # notices list in state is cumulative and reaches two.)
        report.write_text("version two\n", encoding="utf-8")
        changed = self.executor_event("idle", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(changed.returncode, 0, changed.stderr + changed.stdout)
        self.assertEqual(json.loads(changed.stdout)["status"], "notice_sent")
        self.assertEqual(len(self.executor_prompts()), 1, self.herdr_calls())
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 2, state["notices"])
        self.assertEqual(
            state["rounds"][0]["last_executor_report_hash"],
            hashlib.sha256(b"version two\n").hexdigest(),
        )

    def test_executor_event_ignores_working_and_unknown(self) -> None:
        self.send_round(1)
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
        for status in ("working", "unknown"):
            proc = self.executor_event(status, extra_env=self.ZERO_INTERVAL)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "ignored", payload)
            self.assertEqual(self.herdr_calls(), [], status)
        self.assertEqual(self.read_state()["notices"], [])
        self.assertEqual((self.state / "state.json").read_bytes(), before)

    def test_executor_event_dedupes_and_respects_min_interval(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()

        first = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(json.loads(first.stdout)["status"], "notice_sent")
        self.assertEqual(len(self.read_state()["notices"]), 1)

        # Same round, revision, status and report hash: one key, one notice.
        self.clear_calls()
        duplicate = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr + duplicate.stdout)
        self.assertEqual(json.loads(duplicate.stdout)["status"], "ignored")
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(len(self.read_state()["notices"]), 1)
        self.assertEqual(self.read_state()["deferred_notices"], [])

        # A new status inside PAIRCTL_EXECUTOR_NOTICE_MIN_S is deferred, not sent.
        self.clear_calls()
        held = self.executor_event(
            "blocked", extra_env={"PAIRCTL_EXECUTOR_NOTICE_MIN_S": "60"},
        )
        self.assertEqual(held.returncode, 0, held.stderr + held.stdout)
        self.assertEqual(json.loads(held.stdout)["status"], "notice_deferred")
        self.assertEqual(self.herdr_calls(), [])
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 1, state["notices"])
        deferred = state["deferred_notices"]
        self.assertEqual(len(deferred), 1, deferred)
        self.assertEqual(deferred[0]["title"], "herdr-pair executor blocked")
        self.assertEqual(deferred[0]["dedupe_key"], f"executor:{round_id}:1:blocked:")

        # The next event whose interval has already elapsed emits it after all.
        self.clear_calls()
        resent = self.executor_event("blocked", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(resent.returncode, 0, resent.stderr + resent.stdout)
        self.assertEqual(json.loads(resent.stdout)["status"], "notice_sent")
        prompts = self.executor_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())  # sent once, not twice
        self.assertEqual(
            prompts[0][3].splitlines()[0],
            f"herdr-pair executor blocked round {round_id} revision 1",
        )
        state = self.read_state()
        self.assertEqual(len(state["notices"]), 2, state["notices"])
        self.assertEqual(state["deferred_notices"], [])

        # A deferred notice for another status flushes with the next pushed event.
        self.clear_calls()
        held_exit = self.executor_event(
            "exited", extra_env={"PAIRCTL_EXECUTOR_NOTICE_MIN_S": "60"},
        )
        self.assertEqual(json.loads(held_exit.stdout)["status"], "notice_deferred")
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(len(self.read_state()["deferred_notices"]), 1)

        report = self.cwd / "flush-report.md"
        report.write_text("flush me\n", encoding="utf-8")
        state = self.read_state()
        state["rounds"][0]["report"] = str(report)
        self.write_state(state)
        self.clear_calls()
        flushed = self.executor_event("idle", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(flushed.returncode, 0, flushed.stderr + flushed.stdout)
        self.assertEqual(json.loads(flushed.stdout)["status"], "notice_sent")
        titles = sorted(n["title"] for n in self.read_state()["notices"])
        self.assertEqual(titles, [
            "herdr-pair executor blocked",
            "herdr-pair executor done",
            "herdr-pair executor exited",
            "herdr-pair executor idle",
        ], titles)
        self.assertEqual(self.read_state()["deferred_notices"], [])
        # the flushed short report and the current one both reached the planner
        prompt_titles = [p[3].splitlines()[0] for p in self.executor_prompts()]
        self.assertEqual(len(prompt_titles), 2, prompt_titles)
        self.assertTrue(prompt_titles[0].startswith("herdr-pair executor exited"), prompt_titles)
        self.assertTrue(prompt_titles[1].startswith("herdr-pair executor idle"), prompt_titles)

    def test_executor_notices_deferred_while_compact_queued(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        # Arming the resume writes compact_queued as well: both are hold reasons.
        armed = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(armed["queued"], armed)
        self.clear_calls()

        held = self.executor_event("done", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(held.returncode, 0, held.stderr + held.stdout)
        self.assertEqual(json.loads(held.stdout)["status"], "notice_deferred")
        self.assertEqual(self.herdr_calls(), [])  # nothing queued behind /compact
        state = self.read_state()
        self.assertEqual(state["notices"], [])
        deferred = state["deferred_notices"]
        self.assertEqual(len(deferred), 1, deferred)
        self.assertEqual(deferred[0]["title"], "herdr-pair executor done")
        self.assertEqual(deferred[0]["dedupe_key"], f"executor:{round_id}:1:done:")

        # The resume prompt carries the withheld titles and clears the queue.
        self.invoke_ok("rollover", "--reason", "compact")
        self.set_status("idle")
        self.clear_calls()
        delivered = self.invoke_ok("resume-deliver", "--pane", "w1:p1", "--via", "plugin")
        self.assertEqual(delivered["status"], "resume_delivered", delivered)
        prompts = self.auto_continue_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertIn("Deferred executor notices:", prompts[0][3])
        section = prompts[0][3].split("Deferred executor notices:")[1]
        self.assertIn("herdr-pair executor done", section)
        state = self.read_state()
        self.assertEqual(state["deferred_notices"], [])
        self.assertEqual(state["resume_pending"]["status"], "delivered")

    def test_executor_pane_exit_records_time_and_blocks_next_fresh_send(self) -> None:
        sent = self.send_round(1)
        round_id = sent["round_id"]
        self.clear_calls()

        gone = self.executor_event("exited", extra_env=self.ZERO_INTERVAL)
        self.assertEqual(gone.returncode, 0, gone.stderr + gone.stdout)
        self.assertEqual(json.loads(gone.stdout)["status"], "notice_sent")
        state = self.read_state()
        round_item = state["rounds"][0]
        stamp = dt.datetime.fromisoformat(round_item["executor_pane_gone_at"])
        self.assertEqual(stamp.tzinfo, dt.timezone.utc)
        # the bindings themselves are not rewritten by an exit
        self.assertEqual(round_item["executor"], "w1:p2")
        self.assertEqual(state["planner_pane"], "w1:p1")
        prompts = self.executor_prompts()
        self.assertEqual(len(prompts), 1, self.herdr_calls())
        self.assertEqual(prompts[0][2], "w1:p1")
        self.assertEqual(
            prompts[0][3].splitlines()[0],
            f"herdr-pair executor exited round {round_id} revision 1",
        )
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair executor exited")
        self.assertEqual(notes[0][-2:], ["--sound", "request"])
        self.assertEqual(len(state["notices"]), 1)

        # The next dispatch to this pane fails before freshen_executor runs.
        self.clear_calls()
        handoff = self.write_handoff(
            "handoff-gone.md", "[轮次] round_id=<unique-id>\nnext round body\n",
        )
        failed = self.invoke(
            "send-round", "--target", "w1:p2", "--file", str(handoff),
            "--herdr", str(self.herdr), "--scope", "file-2", "--acceptance", "test-2 exit 0",
        )
        self.assertEqual(failed.returncode, 2, failed.stdout)
        self.assertIn("executor_pane_gone", failed.stderr)
        self.assertEqual(self.herdr_calls(), [])
        after = self.read_state()
        self.assertEqual(len(after["rounds"]), 1)
        self.assertIsNone(after["pending_dispatch"])

    def test_planner_pane_exit_expires_resume(self) -> None:
        self.arm_and_advance_epoch()
        self.assertEqual(self.read_state()["resume_pending"]["status"], "pending")
        self.clear_calls()

        gone = self.executor_event("exited", pane="w1:p1")
        self.assertEqual(gone.returncode, 0, gone.stderr + gone.stdout)
        state = self.read_state()
        record = state["resume_pending"]
        self.assertEqual(record["status"], "expired")
        self.assertEqual(record["last_error"], "planner_gone")
        # human notification only: the planner is gone, so there is no short report
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair planner exited")
        self.assertNotIn("--sound", notes[0])
        self.assertEqual(self.executor_prompts(), [])
        self.assertEqual(state["notices"][0]["title"], "herdr-pair planner exited")
        # no active round in this case: nothing about a round is written
        self.assertEqual(state["rounds"], [])

        # Without a resume record the notification still goes out; no record is created.
        state["resume_pending"] = None
        self.write_state(state)
        self.clear_calls()
        again = self.executor_event("exited", pane="w1:p1")
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
        notes = self.notification_calls()
        self.assertEqual(len(notes), 1, self.herdr_calls())
        self.assertEqual(notes[0][2], "herdr-pair planner exited")
        state = self.read_state()
        self.assertIsNone(state["resume_pending"])
        self.assertEqual(len(state["notices"]), 2, state["notices"])

    def test_executor_event_silent_without_delivered_round(self) -> None:
        def assert_ignored(proc: subprocess.CompletedProcess[str], why: str) -> None:
            self.assertEqual(proc.returncode, 0, f"{why}: {proc.stderr}{proc.stdout}")
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "ignored", f"{why}: {payload}")
            self.assertEqual(self.herdr_calls(), [], why)

        # no active round at all
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
        assert_ignored(self.executor_event("done"), "no active round")
        self.assertEqual((self.state / "state.json").read_bytes(), before)

        # an active round whose dispatch is not delivered
        self.send_round(1)
        state = self.read_state()
        state["rounds"][0]["dispatch_status"] = "pending"
        self.write_state(state)
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
        assert_ignored(self.executor_event("done"), "dispatch not delivered")
        self.assertEqual((self.state / "state.json").read_bytes(), before)

        # a delivered round, but this pane is neither its executor nor the planner
        state = self.read_state()
        state["rounds"][0]["dispatch_status"] = "delivered"
        self.write_state(state)
        self.clear_calls()
        before = (self.state / "state.json").read_bytes()
        assert_ignored(self.executor_event("blocked", pane="w9:p9"), "foreign pane")
        assert_ignored(self.executor_event("exited", pane="w9:p9"), "foreign pane exit")
        self.assertEqual((self.state / "state.json").read_bytes(), before)
        self.assertEqual(self.read_state()["notices"], [])

    def test_pane_exited_hook_routes_through_executor_event(self) -> None:
        self.send_round(1)
        self.clear_calls()
        stub = self.write_pairctl_stub()
        plugin_state = self.root / "plugin-state-exited"
        env = {"PAIRCTL": str(stub), "HERDR_PLUGIN_STATE_DIR": str(plugin_state)}

        hook = self.run_exited_hook(pane_id="w1:p2", extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        calls = self.stub_calls()
        self.assertEqual(calls, [[
            "executor-event", "--pane", "w1:p2", "--status", "exited",
            "--cwd", str(self.cwd.resolve()),
            "--state-dir", str(self.state.resolve()),
        ]], calls)
        state = self.read_state()
        self.assertTrue(state["rounds"][0].get("executor_pane_gone_at"), state["rounds"][0])
        notes = [n for n in state["notices"] if n["title"] == "herdr-pair executor exited"]
        self.assertEqual(len(notes), 1, state["notices"])

        # zero hit: a pane this pairing never indexed stays completely silent
        self.clear_stub_calls()
        self.clear_calls()
        miss = self.run_exited_hook(pane_id="wZ:zz", extra_env=env)
        self.assertEqual(miss.returncode, 0, miss.stderr)
        self.assertEqual(miss.stdout, "")
        self.assertEqual(miss.stderr, "")
        self.assertEqual(self.stub_calls(), [])
        self.assertEqual(self.herdr_calls(), [])

        # the planner pane exits: the hook still only resolves the index and calls pairctl
        self.arm_and_advance_epoch()
        self.clear_stub_calls()
        self.clear_calls()
        planner = self.run_exited_hook(pane_id="w1:p1", extra_env=env)
        self.assertEqual(planner.returncode, 0, planner.stderr)
        self.assertEqual(planner.stdout, "")
        self.assertEqual(planner.stderr, "")
        calls = self.stub_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][0:5], [
            "executor-event", "--pane", "w1:p1", "--status", "exited",
        ])
        self.assertEqual(self.read_state()["resume_pending"]["status"], "expired")

    def test_planner_status_hook_routes_executor_to_executor_event(self) -> None:
        self.send_round(1)
        self.clear_calls()
        stub = self.write_pairctl_stub()
        env = {"PAIRCTL": str(stub), "PAIRCTL_EXECUTOR_NOTICE_MIN_S": "0"}

        hook = self.run_resume_hook(pane_id="w1:p2", agent_status="done", extra_env=env)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(hook.stdout, "")
        self.assertEqual(hook.stderr, "")
        calls = self.stub_calls()
        self.assertEqual(calls, [[
            "executor-event", "--pane", "w1:p2", "--status", "done",
            "--cwd", str(self.cwd.resolve()),
            "--state-dir", str(self.state.resolve()),
        ]], calls)
        # executor events never take the resume-deliver path
        self.assertEqual(self.auto_continue_prompts(), [], self.herdr_calls())
        notes = [n for n in self.read_state()["notices"] if n["title"] == "herdr-pair executor done"]
        self.assertEqual(len(notes), 1, notes)

    # --- section 6 plugin actions (issue #7, round p01-r005) -------------------------

    ACTION_SCRIPT = Path(__file__).resolve().parents[1] / "hooks" / "action.py"
    # The only values `resolution.source` may ever carry (round p01-r005 解析).
    ACTION_SOURCES = {"explicit", "env", "pane_index", "focused_cwd", "workspace_cwd"}

    def run_action(
        self,
        action: str,
        *args: str,
        context: dict | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run hooks/action.py black-box: context arrives via env, never via a live host."""
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL"] = str(SCRIPT)
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL_AUTO_COMPACT", None)
        env["PAIRCTL_CONTINUE_AFTER_COMPACT"] = "0"
        # The host machine may export a pair selection of its own; every resolution
        # source under test is injected explicitly, so an ambient one must not leak.
        env.pop("PAIRCTL_CWD", None)
        env.pop("PAIRCTL_STATE_DIR", None)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        if context is None:
            env.pop("HERDR_PLUGIN_CONTEXT_JSON", None)
        else:
            env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(context)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(self.ACTION_SCRIPT), action, *args],
            text=True, capture_output=True, check=False, env=env,
        )

    def action_context(self, focused_pane: str = "w1:p1", cwd: Path | None = None) -> dict:
        where = str(cwd if cwd is not None else self.cwd)
        return {
            "focused_pane_id": focused_pane,
            "focused_pane_cwd": where,
            "workspace_cwd": where,
        }

    def prompts(self) -> list[list[str]]:
        """argv tails of every `herdr agent prompt` this case produced."""
        return [c for c in self.herdr_calls() if c[:2] == ["agent", "prompt"]]

    def test_action_resolution_priority_explicit_wins(self) -> None:
        # A lower-priority env pair and a context cwd both point elsewhere: only the
        # explicit --cwd/--state-dir pair may be honoured, and the real state must
        # answer (a bogus state root would die with a non-JSON pairctl error).
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        bogus_state = self.root / "bogus-state"
        context = self.action_context(cwd=elsewhere)
        proc = self.run_action(
            "pair.status",
            "--cwd", str(self.cwd), "--state-dir", str(self.state),
            context=context,
            extra_env={"PAIRCTL_CWD": str(elsewhere), "PAIRCTL_STATE_DIR": str(bogus_state)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        resolution = payload["resolution"]
        self.assertEqual(resolution["source"], "explicit")
        self.assertEqual(resolution["cwd"], str(self.cwd))
        self.assertEqual(resolution["state_dir"], str(self.state))
        self.assertEqual(resolution["focused_pane"], "w1:p1")
        self.assertEqual(payload["status"], "ok")

    def test_action_resolution_uses_custom_state_dir_from_index(self) -> None:
        # setUp's `init --state-dir self.state --planner-pane w1:p1` recorded a pane
        # index entry; the index must beat focused_pane_cwd/workspace_cwd, which both
        # name a directory whose default state root is empty.
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        context = self.action_context(cwd=elsewhere)
        proc = self.run_action("pair.status", context=context)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        resolution = payload["resolution"]
        self.assertEqual(resolution["source"], "pane_index")
        self.assertEqual(resolution["state_dir"], str(self.state.resolve()))
        self.assertEqual(resolution["cwd"], str(self.cwd.resolve()))
        self.assertEqual(resolution["focused_pane"], "w1:p1")
        self.assertEqual(payload["status"], "ok")

    def test_write_action_rejected_when_focus_is_executor(self) -> None:
        # send-round binds w1:p2 into the pane index as the executor, so the write
        # actions resolve a real state - and must still refuse the executor focus
        # before any pairctl call or herdr prompt happens.
        sent = self.send_round(1)
        self.assertEqual(sent["status"], "round_sent")
        self.clear_calls()
        context = self.action_context(focused_pane="w1:p2")
        for action in ("pair.resume-now", "pair.dispatch-prepared"):
            proc = self.run_action(action, context=context)
            self.assertEqual(proc.returncode, 2, action + ": " + proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["reason"], "focus_not_planner", payload)
            self.assertEqual(payload["resolution"]["source"], "pane_index", payload)
        self.assertEqual(self.herdr_calls(), [], "no herdr call may happen before the focus gate")

    def test_action_output_includes_resolution_source(self) -> None:
        # Every action answers one JSON object carrying `resolution` with all four
        # fields, and pair.status is read-only: state.json survives byte for byte.
        state_path = self.state / "state.json"
        before = state_path.read_text(encoding="utf-8")
        context = self.action_context()
        explicit = self.run_action(
            "pair.status", "--cwd", str(self.cwd), "--state-dir", str(self.state),
            context=context,
        )
        self.assertEqual(explicit.returncode, 0, explicit.stderr + explicit.stdout)
        indexed = self.run_action("pair.status", context=context)
        self.assertEqual(indexed.returncode, 0, indexed.stderr + indexed.stdout)
        for proc in (explicit, indexed):
            payload = json.loads(proc.stdout)
            self.assertIn("status", payload)
            resolution = payload["resolution"]
            for key in ("source", "cwd", "state_dir", "focused_pane"):
                self.assertIn(key, resolution, resolution)
            self.assertIn(resolution["source"], self.ACTION_SOURCES)
        self.assertEqual(json.loads(explicit.stdout)["resolution"]["source"], "explicit")
        self.assertEqual(json.loads(indexed.stdout)["resolution"]["source"], "pane_index")
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)

    def test_resume_now_returns_reason_without_sending(self) -> None:
        # A record exists but its epoch has not advanced: resume-deliver's own
        # reason must reach the action's stdout verbatim, exit 2, no prompt sent.
        armed = self.invoke_ok("compact-self", extra_env=self.RECORD_ENV)
        self.assertTrue(armed["queued"], armed)
        self.clear_calls()
        proc = self.run_action("pair.resume-now", context=self.action_context())
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["reason"], "epoch_not_advanced")
        self.assertEqual(payload["via"], "plugin")
        self.assertEqual(payload["resolution"]["source"], "pane_index")
        self.assertEqual(self.prompts(), [], self.herdr_calls())

    def test_dispatch_prepared_sends_registered_handoff(self) -> None:
        # A first CLI round binds w1:p2 as this state's executor.
        first = self.send_round(1)
        self.invoke_ok(
            "finish-round", "--round-id", first["round_id"], "--status", "accepted",
            "--artifacts", "artifact-1", "--notes", "verified",
        )
        # Only prompts sent from here on belong to prepare/dispatch.
        self.clear_calls()
        context = self.action_context()

        # prepare-round only records the file: no prompt, no round, no dispatch.
        bad = self.write_handoff(
            "prepared-bad.md", "# Bad\nRegenerate scratch in /tmp/scratch\n"
        )
        prepared = self.invoke_ok("prepare-round", "--file", str(bad))
        self.assertEqual(prepared["status"], "round_prepared")
        self.assertEqual(self.read_state()["prepared_round"]["handoff"], str(bad))
        self.assertEqual(self.prompts(), [], "prepare-round must not send")
        self.clear_calls()

        # The registered file still goes through send-round's fence lint.
        linted = self.run_action("pair.dispatch-prepared", context=context)
        self.assertEqual(linted.returncode, 2, linted.stderr + linted.stdout)
        lint_payload = json.loads(linted.stdout)
        self.assertEqual(lint_payload["status"], "rejected")
        self.assertEqual(lint_payload["reason"], "handoff_lint")
        self.assertEqual(lint_payload["resolution"]["source"], "pane_index")
        self.assertEqual(self.prompts(), [], lint_payload)

        # The registered file itself is what gets dispatched: same send-round path
        # as the CLI, target taken from this state's executor.
        good = self.write_handoff(
            "prepared-good.md", "[轮次] round_id=<unique-id>\nprepared dispatch body\n"
        )
        self.invoke_ok("prepare-round", "--file", str(good))
        self.clear_calls()
        proc = self.run_action("pair.dispatch-prepared", context=context)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "round_sent")
        self.assertEqual(payload["target"], "w1:p2")
        self.assertEqual(payload["resolution"]["source"], "pane_index")
        prompts = self.prompts()
        # send-round defaults to a fresh executor context, so the fresh command
        # may prompt too; exactly one prompt may carry the registered handoff.
        dispatched = [c for c in prompts if "prepared dispatch body" in c[3]]
        self.assertEqual(len(dispatched), 1, prompts)
        self.assertEqual(dispatched[0][2], "w1:p2")
        for prompt in prompts:
            self.assertEqual(prompt[2], "w1:p2", prompt)
        state = self.read_state()
        self.assertEqual(state["rounds"][-1]["executor"], "w1:p2")
        self.assertEqual(state["rounds"][-1]["status"], "active")

    def test_dispatch_prepared_without_registration_reports_nothing_prepared(self) -> None:
        proc = self.run_action("pair.dispatch-prepared", context=self.action_context())
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["reason"], "nothing_prepared")
        self.assertIn("resolution", payload)
        self.assertEqual(self.prompts(), [], self.herdr_calls())

    def test_plugin_manifest_lists_section6_actions(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "herdr-plugin.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        actions = manifest.get("actions")
        self.assertIsInstance(actions, list)
        expected = [
            "pair-status",
            "pair-focus-planner",
            "pair-focus-executor",
            "pair-resume-now",
            "pair-dispatch-prepared",
        ]
        self.assertEqual([a.get("id") for a in actions], expected, actions)
        for entry in actions:
            action_id = str(entry.get("id"))
            # The manifest id is dot-free (0.8.0 rejects dots); the command
            # still hands action.py the original dotted action name.
            action_name = action_id.replace("-", ".", 1)
            self.assertTrue(str(entry.get("title") or "").strip(), action_id)
            self.assertEqual(
                entry.get("command"),
                ["python3", "hooks/action.py", action_name],
                action_id,
            )
            self.assertIn("pane", entry.get("contexts") or [], action_id)
        self.assertTrue(
            (manifest_path.parent / "hooks" / "action.py").resolve().is_file()
        )
        events = manifest.get("events") or []
        self.assertEqual(
            [e.get("on") for e in events],
            ["pane.agent_status_changed", "pane.exited"],
            events,
        )

    # --- issue #14: popup board + sidebar replay --------------------------------

    STATUS_PANE = Path(__file__).resolve().parents[1] / "hooks" / "status_pane.py"
    STARTUP_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "on_startup.py"

    def run_plugin_hook(
        self, script: Path, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a hook script black-box with the same injected environment as invoke()."""
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL"] = str(SCRIPT)
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env.pop("PAIRCTL_AUTO_COMPACT", None)
        env["PAIRCTL_CONTINUE_AFTER_COMPACT"] = "0"
        env.pop("PAIRCTL_CWD", None)
        env.pop("PAIRCTL_STATE_DIR", None)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        env.pop("HERDR_PLUGIN_CONTEXT_JSON", None)
        env.pop("HERDR_SOCKET_PATH", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(script)],
            text=True, capture_output=True, check=False, env=env,
        )

    def test_status_payload_includes_board_fields(self) -> None:
        sent = self.send_round(1)
        payload = self.invoke_ok("status")
        for key in (
            "goal", "phase", "rounds", "pending_dispatch_age_s",
            "resume_pending", "notices",
        ):
            self.assertIn(key, payload, payload)
        self.assertIsNone(payload["pending_dispatch_age_s"], payload)
        self.assertIsNone(payload["resume_pending"], payload)
        row = payload["rounds"][0]
        self.assertEqual(row["round_id"], sent["round_id"], payload["rounds"])
        self.assertEqual(row["status"], "active", row)
        self.assertEqual(row["executor"], "w1:p2", row)

        created_at = self.set_pending_dispatch(age_s=120)
        proc = self.invoke("status")
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        pending = json.loads(proc.stdout)
        self.assertEqual(pending["status"], "PENDING_DISPATCH_UNRESOLVED", pending)
        for key in (
            "goal", "phase", "rounds", "pending_dispatch_age_s",
            "resume_pending", "notices",
        ):
            self.assertIn(key, pending, pending)
        self.assertEqual(pending["rounds"], payload["rounds"], pending)
        expect = int(
            time.time() - dt.datetime.fromisoformat(created_at).timestamp()
        )
        age = pending["pending_dispatch_age_s"]
        self.assertIsInstance(age, int, pending)
        self.assertGreaterEqual(age, 0, pending)
        self.assertLessEqual(abs(age - expect), 30, pending)

    def test_status_pane_prints_rate_limited_notice(self) -> None:
        state = self.read_state()
        state.setdefault("notices", []).append({
            "at": "2026-09-22T00:00:00+00:00",
            "title": "herdr-pair resume expired",
            "body": "prompt could not be confirmed",
            "reason": "rate_limited",
            "shown": False,
            "dedupe_key": "test-rate-limited",
        })
        self.write_state(state)
        proc = self.run_plugin_hook(self.STATUS_PANE, extra_env={
            "PAIRCTL_CWD": str(self.cwd),
            "PAIRCTL_STATE_DIR": str(self.state),
        })
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("herdr-pair resume expired", proc.stdout)
        self.assertIn("shown=false", proc.stdout)

    def test_pair_status_opens_popup_from_executor_focus(self) -> None:
        # send-round binds w1:p2 into the pane index as the executor: the
        # read-only status action resolves from that focus and still opens the
        # board. The popup is the only herdr call; agent.view.set is the startup
        # hook's job, never this action's.
        self.send_round(1)
        self.clear_calls()
        state_path = self.state / "state.json"
        before = state_path.read_bytes()
        proc = self.run_action(
            "pair.status", context=self.action_context(focused_pane="w1:p2")
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertIn("phase", payload)
        self.assertEqual(payload["resolution"]["source"], "pane_index", payload)
        self.assertEqual(
            self.herdr_calls(),
            [[
                "plugin", "pane", "open",
                "--plugin", "tc.herdr-pair",
                "--entrypoint", "pair-status",
                "--placement", "popup",
            ]],
            self.herdr_calls(),
        )
        self.assertEqual(state_path.read_bytes(), before)

    def startup_replay(self, plugin_state: Path, sock_path: str) -> list[dict]:
        """Run on_startup.py against a one-shot unix listener; return received frames."""
        received: list[bytes] = []
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(1)
        listener.settimeout(10)

        def serve() -> None:
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                return
            finally:
                listener.close()
            with conn:
                conn.settimeout(5)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received.append(data)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        proc = self.run_plugin_hook(self.STARTUP_HOOK, extra_env={
            "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
            "HERDR_SOCKET_PATH": sock_path,
        })
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        thread.join(timeout=15)
        return [json.loads(raw) for raw in received if raw.strip()]

    def test_startup_replays_saved_sidebar_view(self) -> None:
        self.send_round(1)
        plugin_state = self.root / "plugin-state"
        proc = self.run_action(
            "pair.status",
            context=self.action_context(),
            extra_env={"HERDR_PLUGIN_STATE_DIR": str(plugin_state)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue((plugin_state / "sidebar-view.json").is_file())

        frames = self.startup_replay(plugin_state, str(self.root / "herdr.sock"))
        self.assertEqual(len(frames), 1, frames)
        frame = frames[0]
        self.assertTrue(str(frame.get("id") or "").strip(), frame)
        self.assertEqual(frame.get("method"), "agent.view.set", frame)
        params = frame.get("params") or {}
        self.assertEqual(params.get("source"), "tc.herdr-pair", params)
        self.assertEqual(params.get("label"), "herdr-pair", params)
        view_filter = params.get("filter") or {}
        self.assertEqual(view_filter.get("op"), "in", params)
        self.assertEqual(view_filter.get("field"), "pane_id", params)
        values = view_filter.get("values") or []
        self.assertIn("w1:p1", values, params)
        self.assertIn("w1:p2", values, params)

        # No saved view: the hook writes nothing at all.
        (plugin_state / "sidebar-view.json").unlink()
        frames = self.startup_replay(plugin_state, str(self.root / "herdr2.sock"))
        self.assertEqual(frames, [], frames)

    def test_plugin_manifest_lists_popup_pane_and_startup(self) -> None:
        manifest_path = Path(__file__).resolve().parents[1] / "herdr-plugin.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        panes = [
            p for p in (manifest.get("panes") or []) if p.get("id") == "pair-status"
        ]
        self.assertEqual(len(panes), 1, manifest.get("panes"))
        self.assertEqual(panes[0].get("title"), "Pair status", panes)
        self.assertEqual(
            panes[0].get("command"), ["python3", "hooks/status_pane.py"], panes
        )
        self.assertEqual(panes[0].get("placement"), "popup", panes)
        startup = manifest.get("startup") or []
        self.assertIsInstance(startup, list, startup)
        self.assertIn(
            {"command": ["python3", "hooks/on_startup.py"]}, startup, startup
        )
        self.assertEqual(manifest.get("min_herdr_version"), "0.8.0")
        self.assertEqual(
            [a.get("id") for a in manifest.get("actions") or []],
            [
                "pair-status",
                "pair-focus-planner",
                "pair-focus-executor",
                "pair-resume-now",
                "pair-dispatch-prepared",
            ],
        )
        self.assertEqual(
            [e.get("on") for e in manifest.get("events") or []],
            ["pane.agent_status_changed", "pane.exited"],
        )
        self.assertTrue(self.STATUS_PANE.resolve().is_file())
        self.assertTrue(self.STARTUP_HOOK.resolve().is_file())

    # --- issue #16: herdr 0.8.0 real-machine fixes -----------------------------

    # Round-15 finding: HERDR_PLUGIN_EVENT_JSON is an envelope
    # {"event": "<snake_name>", "data": {pane_id, agent_status, ...}}, while the
    # hooks used to read pane_id/agent_status at the top level. A stub pairctl
    # records the argv each hook would run so flat and enveloped payloads can be
    # compared call-for-call.
    def run_status_hook_argv(
        self,
        event_json: dict,
        pairctl: Path,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env["PAIRCTL"] = str(pairctl)
        env["HERDR_PLUGIN_EVENT"] = "pane.agent_status_changed"
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps(event_json)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(PLUGIN_HOOK)],
            text=True, capture_output=True, check=False, env=env,
        )

    def run_exited_hook_argv(
        self,
        event_json: dict,
        pairctl: Path,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env["PAIRCTL"] = str(pairctl)
        env["HERDR_PLUGIN_EVENT"] = "pane.exited"
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps(event_json)
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(self.PANE_EXITED_HOOK)],
            text=True, capture_output=True, check=False, env=env,
        )

    def test_event_hooks_read_envelope_payload(self) -> None:
        stub = self.write_pairctl_stub()

        # Planner edge: armed resume + advanced epoch -> resume-deliver argv.
        # The stub execs the real pairctl, so each variant gets its own
        # arm/advance: a delivered record is no longer deliverable.
        flat = {"pane_id": "w1:p1", "workspace_id": "ws-1", "agent_status": "idle"}
        self.arm_and_advance_epoch()
        proc = self.run_status_hook_argv(flat, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        flat_calls = self.stub_calls()
        self.assertEqual(len(flat_calls), 1, flat_calls)
        self.assertEqual(flat_calls[0][0], "resume-deliver", flat_calls)
        self.clear_stub_calls()
        self.arm_and_advance_epoch()
        envelope = {"event": "pane_agent_status_changed", "data": flat}
        proc = self.run_status_hook_argv(envelope, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.stub_calls(), flat_calls, self.stub_calls())

        # Executor edge on the same hook: -> executor-event argv.
        self.send_round(1)
        flat_exec = {"pane_id": "w1:p2", "workspace_id": "ws-1", "agent_status": "done"}
        self.clear_stub_calls()
        proc = self.run_status_hook_argv(flat_exec, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        flat_calls = self.stub_calls()
        self.assertEqual(len(flat_calls), 1, flat_calls)
        self.assertEqual(flat_calls[0][0], "executor-event", flat_calls)
        self.clear_stub_calls()
        envelope = {"event": "pane_agent_status_changed", "data": flat_exec}
        proc = self.run_status_hook_argv(envelope, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.stub_calls(), flat_calls, self.stub_calls())

        # Exited hook: -> executor-event --status exited argv.
        flat_exit = {"pane_id": "w1:p2", "workspace_id": "ws-1"}
        self.clear_stub_calls()
        proc = self.run_exited_hook_argv(flat_exit, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        flat_calls = self.stub_calls()
        self.assertEqual(len(flat_calls), 1, flat_calls)
        self.assertEqual(flat_calls[0][:4],
                         ["executor-event", "--pane", "w1:p2", "--status"], flat_calls)
        self.assertEqual(flat_calls[0][4], "exited", flat_calls)
        self.clear_stub_calls()
        envelope = {"event": "pane_exited", "data": flat_exit}
        proc = self.run_exited_hook_argv(envelope, stub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.stub_calls(), flat_calls, self.stub_calls())

    def write_second_herdr(self) -> Path:
        other = self.root / "fake-herdr-b"
        other.write_text(FAKE_HERDR, encoding="utf-8")
        os.chmod(other, 0o755)
        return other

    def second_herdr_calls(self, other: Path) -> list[list[str]]:
        path = Path(str(other) + ".calls.jsonl")
        if not path.exists():
            return []
        return [
            json.loads(line)["argv"][1:]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def test_action_uses_herdr_bin_path(self) -> None:
        # Round-15 finding: plugin processes have HERDR_BIN_PATH but no herdr on
        # PATH. With PAIRCTL_HERDR unset the action's herdr calls must land on
        # the HERDR_BIN_PATH binary.
        other = self.write_second_herdr()
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL"] = str(SCRIPT)
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env.pop("PAIRCTL_HERDR", None)
        env["HERDR_BIN_PATH"] = str(other)
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(self.action_context())
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        proc = subprocess.run(
            ["python3", str(self.ACTION_SCRIPT), "pair.focus-planner"],
            text=True, capture_output=True, check=False, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        calls = self.second_herdr_calls(other)
        self.assertIn(["agent", "focus", "w1:p1"], calls, calls)
        # And the default binary must not have been touched.
        self.assertEqual(self.herdr_calls(), [], self.herdr_calls())

    def test_hook_notify_uses_herdr_bin_path(self) -> None:
        # notify.py's pairctl-failed notification goes through the same binary
        # resolution: no PAIRCTL_HERDR -> HERDR_BIN_PATH.
        other = self.write_second_herdr()
        self.arm_and_advance_epoch()
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env.pop("PAIRCTL_HERDR", None)
        env["HERDR_BIN_PATH"] = str(other)
        env["PAIRCTL"] = str(self.root / "missing-pairctl.py")
        env["HERDR_PLUGIN_EVENT"] = "pane.agent_status_changed"
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({
            "pane_id": "w1:p1", "workspace_id": "ws-1", "agent_status": "idle",
        })
        env.pop("HERDR_PLUGIN_STATE_DIR", None)
        proc = subprocess.run(
            ["python3", str(PLUGIN_HOOK)],
            text=True, capture_output=True, check=False, env=env,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr + proc.stdout)
        calls = self.second_herdr_calls(other)
        notified = [c for c in calls if c[:2] == ["notification", "show"]]
        self.assertEqual(len(notified), 1, calls)

    def test_spawned_watcher_receives_absolute_state_dir(self) -> None:
        # Round-15 finding: compact-self propagated a relative --state-dir into
        # the detached watcher, whose cwd is the state root -> the child looked
        # for the state inside itself and died with 'pair state is not
        # initialized'. The spawned argv must carry the resolved absolute root.
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.state_home)
        env["PAIRCTL_STATE_JSON"] = str(self.state / "state.json")
        env["PAIRCTL_HERDR"] = str(self.herdr)
        env["PAIRCTL_CONTINUE_AFTER_COMPACT"] = "1"
        env["PAIRCTL_CONTINUE_POLL_S"] = "0.05"
        env["PAIRCTL_RESUME_DEADLINE_S"] = "600"  # far deadline: never fires here
        env.pop("PAIRCTL_INTERNAL_WATCHER", None)
        proc = subprocess.run(
            [
                "python3", str(SCRIPT), "compact-self",
                "--cwd", str(self.cwd), "--state-dir", "state",
            ],
            cwd=str(self.root), text=True, capture_output=True, check=False, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["continue_after_compact"]["spawned"], payload)
        pid = int(
            (self.state / "compact-continue.pid").read_text(encoding="utf-8").strip()
        )
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        self.assertIn(b"--state-dir", argv)
        index = argv.index(b"--state-dir")
        self.assertEqual(argv[index + 1].decode(), str(self.state.resolve()), argv)
        # The child runs against the real state: no not-initialized death line.
        time.sleep(0.5)
        log_path = self.state / "compact-continue.log"
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        self.assertNotIn("pair state is not initialized", text)


if __name__ == "__main__":
    unittest.main()
