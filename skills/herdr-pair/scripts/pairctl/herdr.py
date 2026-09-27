"""pairctl herdr: The only place that talks to the herdr host: executable resolution, argv calls, agent probes."""

from __future__ import annotations

import argparse
import subprocess
import time
from typing import Any
from herdr_bin import resolve_herdr  # noqa: E402  (skill root added to sys.path above)

from .constants import (
    FRESH_COMMANDS,
    FRESH_MARKERS,
    FRESH_MAX_LINES,
    FRESH_OK_STATUS,
    HERDR_CALL_TIMEOUT,
)
from .state import FreshError, clip, now, parse_json_payload


def herdr_bin(args: argparse.Namespace) -> str:
    # Plugin hooks run pairctl with HERDR_BIN_PATH set but no herdr on PATH.
    return resolve_herdr(getattr(args, "herdr", None))


def run_herdr(
    args: argparse.Namespace, tail: list[str], timeout: float = HERDR_CALL_TIMEOUT
) -> tuple[int, str, str, Any]:
    """Run one herdr subcommand with an argv list (no shell). Never raises on herdr errors."""
    argv = [herdr_bin(args), *tail]
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, shell=False, timeout=timeout,
        )
    except OSError as exc:
        raise ValueError(f"cannot run herdr ({argv[0]}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"herdr timed out after {timeout}s: {' '.join(tail[:3])}") from exc
    return proc.returncode, proc.stdout, proc.stderr, parse_json_payload(proc.stdout)


def agent_info(args: argparse.Namespace, target: str) -> dict[str, Any]:
    code, out, err, payload = run_herdr(args, ["agent", "get", target])
    result = payload.get("result") if isinstance(payload, dict) else None
    agent = result.get("agent") if isinstance(result, dict) else None
    if code != 0 or not isinstance(agent, dict) or (isinstance(payload, dict) and payload.get("error")):
        raise ValueError(
            f"herdr agent get {target} failed: {herdr_error_code(payload) or clip(err or out, 300)}"
        )
    return agent


def agent_screen(args: argparse.Namespace, target: str, lines: int) -> str:
    """Visible screen text of the target pane. `herdr agent read` prints raw text, not JSON."""
    code, out, err, _ = run_herdr(
        args, ["agent", "read", target, "--source", "visible", "--lines", str(lines)],
    )
    if code != 0:
        raise ValueError(f"herdr agent read {target} failed: {clip(err or out, 300)}")
    return out


def verify_fresh(screen: str, markers: tuple[str, ...]) -> str:
    """Return how the screen proved fresh ('marker' or 'heuristic'), or '' if it did not."""
    if markers:
        return "marker" if any(m in screen for m in markers) else ""
    nonblank = [line for line in screen.splitlines() if line.strip()]
    return "heuristic" if len(nonblank) <= FRESH_MAX_LINES else ""


def freshen_executor(args: argparse.Namespace, target: str) -> dict[str, Any]:
    """Send the executor's fresh-session command and verify on screen that it took effect.

    Fails closed: any doubt raises FreshError and the handoff is not sent.
    """
    info = agent_info(args, target)
    kind = str(info.get("agent") or "")
    status = str(info.get("agent_status") or "")
    if status not in FRESH_OK_STATUS:
        raise FreshError(
            f"refusing to clear executor {target} ({kind}): agent_status={status!r}; "
            "only idle/done panes are cleared"
        )
    command = args.fresh_command or FRESH_COMMANDS.get(kind)
    if not command:
        raise FreshError(f"no fresh-session command known for agent kind {kind!r}")
    code, out, err, payload = run_herdr(args, ["agent", "prompt", target, command])
    if code != 0 or not is_agent_prompted(payload):
        raise FreshError(
            f"{command} was not accepted by {target}: "
            f"{herdr_error_code(payload) or 'unknown_response'} {clip(err or out, 300)}"
        )
    markers = tuple(args.fresh_marker or ()) + FRESH_MARKERS.get(kind, ())
    deadline = time.monotonic() + args.fresh_timeout
    started = time.monotonic()
    screen = ""
    while True:
        try:
            screen = agent_screen(args, target, args.fresh_lines)
        except ValueError:
            screen = ""
        verified = verify_fresh(screen, markers)
        if verified:
            return {
                "kind": kind,
                "command": command,
                "verified": verified,
                "markers": list(markers),
                "elapsed_s": round(time.monotonic() - started, 2),
                "at": now(),
            }
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    tail = "\n".join(line for line in screen.splitlines() if line.strip())[-600:]
    raise FreshError(
        f"{command} was sent to {target} ({kind}) but the screen did not confirm a fresh "
        f"session within {args.fresh_timeout}s (markers={list(markers)!r}); screen tail: {tail!r}"
    )


def is_agent_prompted(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    if payload.get("type") == "agent_prompted":
        return True
    result = payload.get("result")
    if isinstance(result, dict) and not result.get("error"):
        return result.get("type") == "agent_prompted"
    return False


def herdr_error_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "")
    result = payload.get("result")
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "")
        if result.get("type") == "agent_prompt_stalled":
            return "agent_prompt_stalled"
    if payload.get("type") == "agent_prompt_stalled":
        return "agent_prompt_stalled"
    return ""
