#!/usr/bin/env python3
"""Shared fixture for the herdr-pair regression suite.

`PairctlCase` owns one case's temporary state root, the fake `herdr`
executable and every helper the test modules share. A test module declares
`class SomethingTest(support.PairctlCase)` plus its `test_*` methods, and
never reaches into another test module's fixture.
"""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

# AF_UNIX refuses sun_path of 108 bytes or more, so a deep checkout (CI, git
# worktree) must put its case directories somewhere shorter than the tests dir.
SOCKET_PATH_LIMIT = 100
SOCKET_NAME = "herdr.sock"


def temp_root() -> tempfile.TemporaryDirectory[str]:
    """Per-case temp dir, kept short enough for the startup hook's unix socket."""
    tests_dir = Path(__file__).resolve().parent
    for base in (tests_dir, Path(tempfile.gettempdir())):
        if len(str(base)) + len("/tmpXXXXXXXX/") + len(SOCKET_NAME) <= SOCKET_PATH_LIMIT:
            return tempfile.TemporaryDirectory(dir=base)
    return tempfile.TemporaryDirectory(prefix="pctl-", dir=tempfile.gettempdir())


SKILL = Path(__file__).resolve().parents[2] / "skills" / "herdr-pair"
SCRIPT = SKILL / "scripts" / "pairctl.py"
HOOK = SKILL / "scripts" / "claude_session_start_hook.py"
PLUGIN_HOOK = SKILL / "hooks" / "on_planner_status.py"
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




class PairctlCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_root()
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
    def read_state(self) -> dict:
        return json.loads((self.state / "state.json").read_text(encoding="utf-8"))
    def write_state(self, data: dict) -> None:
        (self.state / "state.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
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
    REQUIRED_RECORD_KEYS = (
        "armed_at", "epoch_at_arm", "planner_pane", "state_dir",
        "mechanism", "deadline", "status", "attempts", "last_error",
    )
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
    PANE_EXITED_HOOK = SKILL / "hooks" / "on_pane_exited.py"
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
    ACTION_SCRIPT = SKILL / "hooks" / "action.py"
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
    STATUS_PANE = SKILL / "hooks" / "status_pane.py"
    STARTUP_HOOK = SKILL / "hooks" / "on_startup.py"
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
    def startup_replay(self, plugin_state: Path, sock_path: str) -> list[dict]:
        """Run on_startup.py against a one-shot unix listener; return received frames."""
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(1)
        try:
            proc = self.run_plugin_hook(self.STARTUP_HOOK, extra_env={
                "HERDR_PLUGIN_STATE_DIR": str(plugin_state),
                "HERDR_SOCKET_PATH": sock_path,
            })
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            # The hook has already exited, so a connection it made is queued by
            # now: the "hook writes nothing" case must not wait out a timeout.
            ready, _, _ = select.select([listener], [], [], 2.0)
            if not ready:
                return []
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(5)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            return [json.loads(data)] if data.strip() else []
        finally:
            listener.close()
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
