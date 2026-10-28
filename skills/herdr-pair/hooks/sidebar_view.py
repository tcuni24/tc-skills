#!/usr/bin/env python3
"""Sidebar agent-view bookkeeping for herdr-pair (issue #14).

The agent-panel filter is a list of pane ids, and pane ids die with their
panes. A saved view is therefore only a hint: every read validates it against
`agent.list` before the host applies it, and a filter that would match nothing
is cleared instead of replayed. Hooks keep the saved snapshot honest so a pair
session that ended months ago cannot hide every agent in the sidebar.

All functions are best-effort: a missing state dir, socket or herdr error is
silent, because a plugin that cannot reach the host must stay invisible.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

PLUGIN_ID = "tc.herdr-pair"
SIDEBAR_LABEL = "herdr-pair"
SIDEBAR_VIEW_FILE = "sidebar-view.json"
SOCKET_TIMEOUT_S = 5.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def view_path() -> Path | None:
    """The persisted view file, or None when the host gave no state dir."""
    base = (os.environ.get("HERDR_PLUGIN_STATE_DIR") or "").strip()
    if not base:
        return None
    return Path(base).expanduser() / SIDEBAR_VIEW_FILE


def read_params() -> dict | None:
    path = view_path()
    if path is None:
        return None
    try:
        params = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return params if isinstance(params, dict) else None


def write_params(params: dict) -> None:
    path = view_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(params, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def remove_params() -> None:
    path = view_path()
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def pane_values(params: dict | None) -> list[str]:
    """The pane ids in a `pane_id in [...]` filter, de-duplicated in order."""
    if not isinstance(params, dict):
        return []
    view_filter = params.get("filter")
    if not isinstance(view_filter, dict):
        return []
    if view_filter.get("op") != "in" or view_filter.get("field") != "pane_id":
        return []
    values = view_filter.get("values")
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        pane_id = str(value).strip()
        if pane_id and pane_id not in result:
            result.append(pane_id)
    return result


def _narrow(params: dict, values: list[str]) -> dict:
    narrowed = dict(params)
    narrowed["filter"] = {"op": "in", "field": "pane_id", "values": list(values)}
    return narrowed


def _request(method: str, params: dict) -> dict | None:
    """One newline-JSON request on the host socket; None on any failure."""
    sock_path = (os.environ.get("HERDR_SOCKET_PATH") or "").strip()
    if not sock_path:
        return None
    frame = (
        json.dumps({"id": "herdr-pair-sidebar-view", "method": method, "params": params},
                   ensure_ascii=False)
        + "\n"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(SOCKET_TIMEOUT_S)
            sock.connect(sock_path)
            sock.sendall(frame.encode("utf-8"))
            chunks = b""
            while b"\n" not in chunks:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks += chunk
                if len(chunks) > MAX_RESPONSE_BYTES:
                    return None
    except OSError:
        return None
    try:
        response = json.loads(chunks.split(b"\n", 1)[0].decode("utf-8"))
    except ValueError:
        return None
    result = response.get("result")
    return result if isinstance(result, dict) else None


def live_pane_ids() -> set[str] | None:
    """Pane ids herdr currently reports as agents; None when it cannot answer."""
    result = _request("agent.list", {})
    if result is None:
        return None
    agents = result.get("agents")
    if not isinstance(agents, list):
        return None
    pane_ids: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        pane_id = str(agent.get("pane_id") or "").strip()
        if pane_id:
            pane_ids.add(pane_id)
    return pane_ids


def _source(params: dict) -> str:
    return str(params.get("source") or PLUGIN_ID)


def replay() -> None:
    """Startup: apply the saved view only where at least one pane is alive.

    An empty `agent.list` is not proof that the saved panes are stale — agent
    detection may still be settling — so that case leaves the file untouched
    and applies nothing. A confirmed all-stale view is cleared, which is what
    keeps the panel from opening with "no matching agents".
    """
    params = read_params()
    values = pane_values(params)
    if params is None or not values:
        return
    live = live_pane_ids()
    if not live:
        return
    keep = [pane_id for pane_id in values if pane_id in live]
    if not keep:
        _request("agent.view.clear", {"source": _source(params)})
        remove_params()
        return
    narrowed = _narrow(params, keep)
    _request("agent.view.set", narrowed)
    if keep != values:
        write_params(narrowed)


def pane_exited(pane_id: str) -> None:
    """Drop a pane that just exited; clear the view when no pane is left.

    The exited pane is dropped even when the socket is unreachable, so the
    saved file never keeps a pane herdr has already reported as gone.
    """
    params = read_params()
    values = pane_values(params)
    if params is None or pane_id not in values:
        return
    keep = [value for value in values if value != pane_id]
    live: set[str] | None = None
    if keep:
        live = live_pane_ids()
        if live is not None:
            keep = [value for value in keep if value in live]
    if not keep:
        _request("agent.view.clear", {"source": _source(params)})
        remove_params()
        return
    narrowed = _narrow(params, keep)
    if live is not None:
        _request("agent.view.set", narrowed)
    write_params(narrowed)
