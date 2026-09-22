#!/usr/bin/env python3
"""herdr plugin actions for herdr-pair (issue #7): resolve context, then delegate.

Invoked by herdr as `python3 hooks/action.py <action-id>` (working directory =
the plugin directory) with the invocation context JSON in HERDR_PLUGIN_CONTEXT_JSON.
The script never imports pairctl and never writes state.json: every claim, send
and status read runs as a pairctl child process, and the only local reads are
state.json (planner pane, active-round executor, prepared handoff) plus the
pairctl pane index.

Context resolution, first hit wins, reported as `resolution.source`:

1. `explicit`        --cwd and --state-dir both given on the command line
2. `env`             PAIRCTL_CWD and PAIRCTL_STATE_DIR both exported
3. `pane_index`      focused_pane_id has a fresh entry in
                     $XDG_STATE_HOME/herdr-pair/panes/<id>.json (that entry's
                     cwd and state_dir, so a custom --state-dir still resolves)
4. `focused_cwd`     focused_pane_cwd is non-empty; the state directory is
                     pairctl's default root for that cwd (sha256 of the
                     canonical cwd under XDG_STATE_HOME/herdr-pair)
5. `workspace_cwd`   same, from workspace_cwd

No hit at all: exit 2 with JSON `reason` `unresolved`. Every action's stdout
JSON carries `resolution` = {source, cwd, state_dir, focused_pane}.

Write actions are pair.resume-now and pair.dispatch-prepared: when
focused_pane_id differs from the state's planner_pane they exit 2 with `reason`
`focus_not_planner` before any pairctl call, so no prompt can be sent from the
executor's focus. Read-only actions (pair.status, pair.focus-planner,
pair.focus-executor) succeed with the executor focused too.

Action bodies:

* pair.status           `pairctl status` payload + resolution (state untouched).
                        When the answer carries `phase` — including the exit-2
                        pending-dispatch payload — it also opens the pair-status
                        popup via `herdr plugin pane open` and persists the
                        sidebar view query to
                        $HERDR_PLUGIN_STATE_DIR/sidebar-view.json (replayed by
                        hooks/on_startup.py; the action itself never calls
                        agent.view.set)
* pair.focus-planner    `herdr agent focus <planner_pane>`
* pair.focus-executor   `herdr agent focus <active round executor>`; no active
                        executor => exit 2 `no_executor`
* pair.resume-now       `pairctl resume-deliver --pane <planner_pane> --via plugin`;
                        a pairctl rejection is passed through verbatim with exit 2
* pair.dispatch-prepared the handoff registered by `pairctl prepare-round --file`,
                        through the same `pairctl send-round` the CLI uses (so the
                        fence lint still applies); nothing registered => exit 2
                        `nothing_prepared`
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_STALE_HOURS = 12.0
DEFAULT_STATE_HOME = "~/.local/state"
HERDR_CALL_TIMEOUT = 30.0
PLUGIN_ID = "tc.herdr-pair"
SIDEBAR_LABEL = "herdr-pair"
SIDEBAR_VIEW_FILE = "sidebar-view.json"
STATUS_PANE_ENTRYPOINT = "pair-status"
# Mutating actions: focus must equal the state's planner_pane or they refuse.
WRITE_ACTIONS = frozenset({"pair.resume-now", "pair.dispatch-prepared"})
KNOWN_ACTIONS = (
    "pair.status",
    "pair.focus-planner",
    "pair.focus-executor",
    "pair.resume-now",
    "pair.dispatch-prepared",
)
# The only values `resolution.source` may take (round p01-r005 解析).
RESOLUTION_SOURCES = ("explicit", "env", "pane_index", "focused_cwd", "workspace_cwd")


def canonical_cwd(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def default_state_dir(cwd: str) -> str:
    """pairctl's default state root for a cwd: XDG_STATE_HOME/herdr-pair/<sha256[:20]>."""
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    key = hashlib.sha256(canonical_cwd(cwd).encode()).hexdigest()[:20]
    return str(base / "herdr-pair" / key)


def pane_index_path(pane_id: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", DEFAULT_STATE_HOME)).expanduser()
    return base / "herdr-pair" / "panes" / f"{pane_id}.json"


def parse_stamp(value: object) -> dt.datetime | None:
    try:
        stamp = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp


def stale_hours() -> float:
    try:
        return float(os.environ.get("PAIRCTL_SESSION_STALE_HOURS", DEFAULT_STALE_HOURS))
    except ValueError:
        return DEFAULT_STALE_HOURS


def load_context() -> dict:
    try:
        raw = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "") or "{}")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def pane_index_entry(pane_id: str) -> dict | None:
    """Newest unexpired index entry for this pane, or None (zero hit / all stale)."""
    if not pane_id:
        return None
    try:
        raw = json.loads(pane_index_path(pane_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    limit = dt.timedelta(hours=stale_hours())
    now = dt.datetime.now(dt.timezone.utc)
    fresh = []
    for entry in entries:
        stamp = parse_stamp(entry.get("recorded_at"))
        if stamp is None or now - stamp > limit:
            continue  # stale or unparsable: treated as nonexistent
        if not str(entry.get("state_dir") or "") or not str(entry.get("cwd") or ""):
            continue
        fresh.append((stamp, entry))
    if not fresh:
        return None
    return max(fresh, key=lambda pair: pair[0])[1]


def resolve(args: argparse.Namespace, ctx: dict) -> tuple[str, str, str] | None:
    """(source, cwd, state_dir) of the highest-priority hit, else None."""
    if args.cwd and args.state_dir:
        return "explicit", args.cwd, args.state_dir
    env_cwd = (os.environ.get("PAIRCTL_CWD") or "").strip()
    env_state = (os.environ.get("PAIRCTL_STATE_DIR") or "").strip()
    if env_cwd and env_state:
        return "env", env_cwd, env_state
    entry = pane_index_entry(str(ctx.get("focused_pane_id") or ""))
    if entry is not None:
        return (
            "pane_index",
            str(entry.get("cwd") or ""),
            str(entry.get("state_dir") or ""),
        )
    focused = str(ctx.get("focused_pane_cwd") or "").strip()
    if focused:
        return "focused_cwd", focused, default_state_dir(focused)
    workspace = str(ctx.get("workspace_cwd") or "").strip()
    if workspace:
        return "workspace_cwd", workspace, default_state_dir(workspace)
    return None


def load_state(state_dir: str) -> dict | None:
    """Read-only state.json lookup; None when it cannot be read as an object."""
    try:
        raw = json.loads(
            (Path(state_dir).expanduser() / "state.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def pairctl_path() -> str:
    return os.environ.get("PAIRCTL") or str(
        Path(__file__).resolve().parent.parent / "scripts" / "pairctl.py"
    )


def run_pairctl(cwd: str, state_dir: str, tail: list[str]) -> tuple[int, dict | None, str]:
    """Run one pairctl subcommand against the resolved selection.

    Returns (exit code, parsed JSON object or None, failure detail). A pairctl
    that cannot answer (missing file, timeout, non-JSON stdout) yields None so
    the caller can fail loudly instead of pretending the action happened.
    """
    path = pairctl_path()
    if not Path(path).is_file():
        return 2, None, f"pairctl not found: {path}"
    cmd = [sys.executable, path, *tail, "--cwd", cwd, "--state-dir", state_dir]
    env = os.environ.copy()
    env.setdefault("PAIRCTL_LOCK_WAIT_S", "5")
    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True, check=False, timeout=30, env=env
        )
    except subprocess.TimeoutExpired:
        return 2, None, "pairctl timed out"
    except OSError as exc:
        return 2, None, f"pairctl spawn failed: {exc}"
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail_line = detail[-1] if detail else ""
        suffix = f": {tail_line}" if tail_line else ""
        return 2, None, f"pairctl stdout was not JSON (exit {proc.returncode}){suffix}"
    if not isinstance(payload, dict):
        return 2, None, "pairctl stdout was not a JSON object"
    return proc.returncode, payload, ""


def herdr_bin() -> str:
    # Plugin subprocesses have HERDR_BIN_PATH but no herdr on PATH (issue #16).
    return (
        os.environ.get("PAIRCTL_HERDR")
        or os.environ.get("HERDR_BIN_PATH")
        or "herdr"
    )


def run_focus(pane: str) -> tuple[bool, str]:
    """`herdr agent focus <pane>`; (False, detail) when the host could not answer."""
    argv = [herdr_bin(), "agent", "focus", pane]
    try:
        proc = subprocess.run(
            argv, text=True, capture_output=True, check=False, timeout=HERDR_CALL_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return False, f"herdr agent focus {pane} timed out"
    except OSError as exc:
        return False, f"cannot run herdr ({argv[0]}): {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, detail[-1] if detail else f"exit {proc.returncode}"
    return True, ""


def active_executor(state: dict | None) -> str:
    """Executor of the active round ("" when no round has one)."""
    for item in (state or {}).get("rounds") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "active":
            continue
        executor = str(item.get("executor") or "").strip()
        if executor:
            return executor
    return ""


def dispatch_target(state: dict | None) -> str:
    """Who a prepared handoff goes to: the most recent round's bound executor.

    send-round refuses while a round is active, so by the time dispatch-prepared
    may run there is normally no active round; the last round that bound an
    executor pane (send-round / start-round record it, and the pane index keeps
    the same binding) names the pairing's executor. States without any round
    have no target: the first dispatch still goes through the CLI, which is the
    only path that names an executor pane explicitly.
    """
    executor = active_executor(state)
    if executor:
        return executor
    for item in reversed((state or {}).get("rounds") or []):
        if not isinstance(item, dict):
            continue
        bound = str(item.get("executor") or "").strip()
        if bound:
            return bound
    return ""


def fail(action: str, reason: str, resolution: dict, **extra: object) -> int:
    emit({
        "status": "rejected",
        "reason": reason,
        "action": action,
        "resolution": resolution,
        **extra,
    })
    return 2


def open_status_popup() -> None:
    """`herdr plugin pane open` for the status board; best-effort, never fatal."""
    argv = [
        herdr_bin(), "plugin", "pane", "open",
        "--plugin", PLUGIN_ID,
        "--entrypoint", STATUS_PANE_ENTRYPOINT,
        "--placement", "popup",
    ]
    try:
        subprocess.run(
            argv, text=True, capture_output=True, check=False,
            timeout=HERDR_CALL_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # the board is a convenience: a failed open must not fail the action


def sidebar_view_params(payload: dict, state: dict | None) -> dict | None:
    """agent.view.set params persisted for the startup replay (issue #14).

    Values lead with planner_pane and add the active round's executor; with no
    active round the last non-empty executor in `rounds` stands in, and a state
    with neither leaves the planner pane alone. None means "do not write".
    """
    planner = str(payload.get("planner_pane") or "").strip() or str(
        (state or {}).get("planner_pane") or ""
    ).strip()
    if not planner:
        return None
    rounds = payload.get("rounds")
    if not isinstance(rounds, list):
        rounds = (state or {}).get("rounds") or []
    executor = ""
    for item in rounds:
        if not isinstance(item, dict) or str(item.get("status") or "") != "active":
            continue
        executor = str(item.get("executor") or "").strip()
        if executor:
            break
    if not executor:
        for item in reversed(rounds):
            if not isinstance(item, dict):
                continue
            executor = str(item.get("executor") or "").strip()
            if executor:
                break
    values = [planner]
    if executor and executor not in values:
        values.append(executor)
    return {
        "source": PLUGIN_ID,
        "label": SIDEBAR_LABEL,
        "filter": {"op": "in", "field": "pane_id", "values": values},
    }


def write_sidebar_view(params: dict) -> None:
    """Persist the view query for hooks/on_startup.py; failures stay silent."""
    base = (os.environ.get("HERDR_PLUGIN_STATE_DIR") or "").strip()
    if not base:
        return
    try:
        path = Path(base).expanduser() / SIDEBAR_VIEW_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(params, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def run_pair_status(cwd: str, state_dir: str, resolution: dict) -> int:
    """pair.status: echo the status JSON, then board side-effects (issue #14).

    The answer is emitted first so stdout stays one clean JSON line; the popup
    open and sidebar persist afterwards only run when the answer carried
    `phase`, which covers both the normal exit and the exit-2 pending-dispatch
    payload. A non-JSON or phase-less answer opens nothing.
    """
    code, payload, detail = run_pairctl(cwd, state_dir, ["status"])
    if payload is None:
        emit({
            "status": "failed",
            "reason": "pairctl_failed",
            "action": "pair.status",
            "detail": detail,
            "resolution": resolution,
        })
        return 2
    merged = {**payload, "action": "pair.status", "resolution": resolution}
    emit(merged)
    if "phase" in payload:
        open_status_popup()
        view = sidebar_view_params(payload, load_state(state_dir))
        if view is not None:
            write_sidebar_view(view)
    if str(payload.get("status") or "") == "rejected":
        return 2
    return code


def run_pairctl_action(
    action: str, cwd: str, state_dir: str, resolution: dict, tail: list[str]
) -> int:
    code, payload, detail = run_pairctl(cwd, state_dir, tail)
    if payload is None:
        emit({
            "status": "failed",
            "reason": "pairctl_failed",
            "action": action,
            "detail": detail,
            "resolution": resolution,
        })
        return 2
    merged = {**payload, "action": action, "resolution": resolution}
    emit(merged)
    # Rejections answer exit 2 whatever pairctl did; answers pass the code through
    # (pair.status may exit 2 for PENDING_DISPATCH_UNRESOLVED or 20 for rollover).
    if str(payload.get("status") or "") == "rejected":
        return 2
    return code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="herdr-pair plugin action entrypoint", add_help=True
    )
    parser.add_argument("action", help="action id from herdr-plugin.toml")
    parser.add_argument("--cwd", help="explicit pair cwd (highest resolution priority)")
    parser.add_argument("--state-dir", help="explicit pair state directory")
    args = parser.parse_args()

    ctx = load_context()
    focused_pane = str(ctx.get("focused_pane_id") or "")
    hit = resolve(args, ctx)
    if hit is None:
        return fail(
            args.action,
            "unresolved",
            {"source": "", "cwd": "", "state_dir": "", "focused_pane": focused_pane},
        )
    source, cwd, state_dir = hit
    resolution = {
        "source": source,
        "cwd": cwd,
        "state_dir": state_dir,
        "focused_pane": focused_pane,
    }
    if args.action not in KNOWN_ACTIONS:
        return fail(args.action, "unknown_action", resolution)

    if args.action in WRITE_ACTIONS:
        # Focus gate before anything that could claim, send or prompt: pairctl is
        # never called when the executor (or no pane at all) holds the focus.
        state = load_state(state_dir)
        if state is None:
            return fail(args.action, "state_unavailable", resolution)
        planner = str(state.get("planner_pane") or "")
        if focused_pane != planner:
            return fail(
                args.action,
                "focus_not_planner",
                resolution,
                planner_pane=planner,
            )

    if args.action == "pair.status":
        return run_pair_status(cwd, state_dir, resolution)

    if args.action == "pair.focus-planner":
        planner = str((load_state(state_dir) or {}).get("planner_pane") or "")
        if not planner:
            return fail(args.action, "no_planner", resolution)
        ok, detail = run_focus(planner)
        if not ok:
            return fail(args.action, "focus_failed", resolution, detail=detail, pane=planner)
        emit({
            "status": "focused",
            "action": args.action,
            "pane": planner,
            "role": "planner",
            "resolution": resolution,
        })
        return 0

    if args.action == "pair.focus-executor":
        state = load_state(state_dir)
        executor = active_executor(state)
        if not executor:
            return fail(args.action, "no_executor", resolution)
        ok, detail = run_focus(executor)
        if not ok:
            return fail(
                args.action, "focus_failed", resolution, detail=detail, pane=executor
            )
        emit({
            "status": "focused",
            "action": args.action,
            "pane": executor,
            "role": "executor",
            "resolution": resolution,
        })
        return 0

    if args.action == "pair.resume-now":
        # The focus gate already proved focused_pane == planner_pane, so pairctl
        # gets the recorded planner pane and pairctl alone decides the claim.
        planner = str((load_state(state_dir) or {}).get("planner_pane") or "")
        if not planner:
            return fail(args.action, "no_planner", resolution)
        return run_pairctl_action(
            args.action,
            cwd,
            state_dir,
            resolution,
            ["resume-deliver", "--pane", planner, "--via", "plugin"],
        )

    # pair.dispatch-prepared
    state = load_state(state_dir)
    prepared = (state or {}).get("prepared_round")
    handoff = str(prepared.get("handoff") or "") if isinstance(prepared, dict) else ""
    if not handoff:
        return fail(args.action, "nothing_prepared", resolution)
    target = dispatch_target(state)
    if not target:
        return fail(args.action, "no_executor", resolution)
    return run_pairctl_action(
        args.action,
        cwd,
        state_dir,
        resolution,
        ["send-round", "--target", target, "--file", handoff],
    )


if __name__ == "__main__":
    sys.exit(main())
