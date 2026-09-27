"""pairctl state: State primitives: paths, locking, atomic writes, load/save, output and the job ledger."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterator

from .constants import DIR_MODE, FILE_MODE, PENDING_GUIDANCE


class PendingDispatchError(ValueError):
    def __init__(
        self, pending: dict[str, Any], notices: list[dict[str, Any]] | None = None,
        board: dict[str, Any] | None = None,
    ) -> None:
        self.pending = pending
        # status carries the notices array even on the pending-error path (issue #11).
        self.notices = notices
        # status also carries the six popup-board fields on that path (issue #14).
        self.board = board
        super().__init__("unresolved pending_dispatch")


class LockTimeoutError(Exception):
    """PAIRCTL_LOCK_WAIT_S elapsed before the state lock was acquired (issue #11).

    Deliberately not a ValueError: main() must answer with the exact
    lock_timeout payload on stdout and an empty stderr, not PAIRCTL_ERROR.
    """


class FreshError(ValueError):
    """The executor pane could not be verified as freshly cleared."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_cwd(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def default_root(cwd: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    key = hashlib.sha256(cwd.encode()).hexdigest()[:20]
    return base / "herdr-pair" / key


def pane_index_path(pane_id: str) -> Path:
    """Per-pane index file: $XDG_STATE_HOME/herdr-pair/panes/<pane_id>.json (issue #10).

    Shared by every pair state directory, so it lives outside any single state root
    and is never protected by a state lock.
    """
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return base / "herdr-pair" / "panes" / f"{pane_id}.json"


def paths(args: argparse.Namespace) -> dict[str, Path]:
    cwd = canonical_cwd(args.cwd)
    root = Path(args.state_dir).expanduser().resolve() if args.state_dir else default_root(cwd)
    return {
        "root": root,
        "state": root / "state.json",
        "lock": root / "state.lock",
        "ledger": root / "jobs.tsv",
        "checkpoint": root / "CHECKPOINT.md",
        "contracts": root / "contracts",
        "planner_session": root / "planner-session.json",
        "snapshots": root / "snapshots",
    }


def chmod_private(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def lock_wait_s() -> float | None:
    """PAIRCTL_LOCK_WAIT_S seconds (issue #11): bound on acquiring the state lock.

    Unset, unparsable or negative keeps the historical blocking lock; a valid
    value lets a hook event be abandoned on contention and retried by the next
    wake-up source instead of hanging behind another writer.
    """
    raw = (os.environ.get("PAIRCTL_LOCK_WAIT_S") or "").strip()
    if not raw:
        return None
    try:
        wait = float(raw)
    except ValueError:
        return None
    return wait if wait >= 0 else None


@contextlib.contextmanager
def locked(root: Path, lock_path: Path, create: bool = False) -> Iterator[None]:
    if create:
        root.mkdir(parents=True, exist_ok=True)
        chmod_private(root, DIR_MODE)
    if not root.is_dir():
        raise ValueError("pair state is not initialized")
    with lock_path.open("a+", encoding="utf-8") as handle:
        chmod_private(lock_path, FILE_MODE)
        wait = lock_wait_s()
        if wait is None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"state lock not acquired within {wait}s"
                        )
                    time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chmod_private(path.parent, DIR_MODE)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        chmod_private(Path(tmp_name), FILE_MODE)
        os.replace(tmp_name, path)
        chmod_private(path, FILE_MODE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def record_pane(pane_id: str, state_dir: str, cwd: str, role: str) -> None:
    """Index which pair state directory a pane belongs to (issue #10).

    The file is a JSON array of {state_dir, cwd, role, recorded_at} elements keyed
    by state_dir + role: rewriting one refreshes recorded_at in place and moves the
    element to the end of the array instead of appending a duplicate. An empty
    pane id writes nothing. The index only feeds the plugin resume hook, so
    state.json stays the single source of truth: a corrupt file restarts the
    array and an unwritable location never fails the command it decorates.
    """
    if not pane_id:
        return
    path = pane_index_path(pane_id)
    raw: Any = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None  # missing or corrupt: rebuild from this write
    entries: list[dict[str, str]] = (
        [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    )
    key = (state_dir, role)
    entries = [
        e for e in entries
        if (str(e.get("state_dir") or ""), str(e.get("role") or "")) != key
    ]
    entries.append({
        "state_dir": state_dir, "cwd": cwd, "role": role, "recorded_at": now(),
    })
    try:
        atomic_text(path, json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        pass  # best-effort cache; the state machine does not depend on it


def load(path: Path, cwd: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("pair state is not initialized; run init")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("cwd") != cwd:
        raise ValueError(f"state cwd mismatch: {data.get('cwd')!r} != {cwd!r}")
    # Issue #11: states written before notices existed gain the array on load.
    data.setdefault("notices", [])
    # Issue #12: executor notices withheld during compaction queue up here.
    data.setdefault("deferred_notices", [])
    ver = data.get("version")
    if ver == 1:
        data["version"] = 2
        if data.get("pending_dispatch"):
            data["pending_dispatch"]["legacy_unconfirmed_protocol"] = True
        for r in data.get("rounds", []):
            if r.get("status") == "active":
                r["work_status"] = "unconfirmed_protocol"
                r["dispatch_status"] = "delivered"
                r["current_revision"] = 0
                r.setdefault("revisions", {})
                r.setdefault("receipts", [])
            else:
                r["work_status"] = r.get("status")
                r["dispatch_status"] = "delivered"
                r["current_revision"] = 0
                r.setdefault("revisions", {})
                r.setdefault("receipts", [])
        save(path, data)
    elif ver == 2:
        pass
    else:
        raise ValueError(f"unsupported state version: {ver!r}")
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = now()
    atomic_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def persist(pp: dict[str, Path], data: dict[str, Any]) -> None:
    save(pp["state"], data)
    write_ledger(pp["ledger"], data)


def load_ready(pp: dict[str, Path], cwd: str) -> dict[str, Any]:
    data = load(pp["state"], cwd)
    # jobs.tsv is a derived view of state.json, never a second source of truth.
    write_ledger(pp["ledger"], data)
    pending = data.get("pending_dispatch")
    if pending:
        raise PendingDispatchError(pending)
    return data


def pending_payload(pending: dict[str, Any]) -> dict[str, Any]:
    round_id = str(pending.get("round_id") or "")
    target = str(pending.get("target") or "")
    return {
        "status": "PENDING_DISPATCH_UNRESOLVED",
        "round_consumed": False,
        "resent": False,
        "counted": False,
        "round_id": round_id,
        "target": target,
        "session_switched": False,
        "guidance": (
            f"{PENDING_GUIDANCE} target={target} round_id={round_id}."
        ),
    }


def board_fields(data: dict[str, Any]) -> dict[str, Any]:
    """The six fields the pair.status popup board renders (issue #14).

    Both `pairctl status` exits — the normal answer and the PENDING_DISPATCH_
    UNRESOLVED rejection — carry this exact set, computed once from the state
    already loaded under the lock. `pending_dispatch_age_s` stays null unless a
    pending record with a parsable created_at exists; `resume_pending` is null
    when no record was ever armed.
    """
    age: int | None = None
    pending = data.get("pending_dispatch")
    if isinstance(pending, dict):
        stamp = parse_stamp(pending.get("created_at"))
        if stamp is not None:
            age = max(0, int(time.time() - stamp.timestamp()))
    return {
        "goal": str(data.get("goal") or ""),
        "phase": data.get("phase"),
        "rounds": [
            {
                "round_id": str(item.get("round_id") or ""),
                "status": str(item.get("status") or ""),
                "executor": str(item.get("executor") or ""),
            }
            for item in (data.get("rounds") or [])
            if isinstance(item, dict)
        ],
        "pending_dispatch_age_s": age,
        "resume_pending": data.get("resume_pending"),
        "notices": data.get("notices", []),
    }


def output(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def tsv(value: Any) -> str:
    return str(value or "").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def write_ledger(path: Path, data: dict[str, Any]) -> None:
    fields = [
        "round_id", "phase", "queue", "job_id", "label", "submitter",
        "command", "log", "expected_artifacts", "completion_assertion",
        "state", "cancel_retry_owner", "created_at", "updated_at",
    ]
    lines = ["\t".join(fields)]
    for job in data["jobs"]:
        lines.append("\t".join(tsv(job.get(field)) for field in fields))
    atomic_text(path, "\n".join(lines) + "\n")


def table_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def parse_stamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def unknown_usage(budget: int, reason: str, source: str = "unknown") -> dict[str, Any]:
    return {
        "status": "unknown", "context_tokens": None, "budget": budget,
        "over_budget": False, "session_started_at": None, "session_age_hours": None,
        "stale": False, "source": source, "measured_at": now(), "reason": reason,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def env_flag(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "off", "false", "no"}


def env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def parse_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def clip(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
