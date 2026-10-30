#!/usr/bin/env python3
"""Route registered pane events to fleet without modifying its state."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    event = os.environ.get('HERDR_PLUGIN_EVENT')
    if event not in ('pane.agent_status_changed', 'pane.exited', 'pane.closed', 'tab.closed'):
        return 0
    try:
        payload = json.loads(os.environ.get('HERDR_PLUGIN_EVENT_JSON', '{}'))
        data = payload.get('data', payload)
        state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
        panes = json.loads((state / 'herdr-fleet/panes.json').read_text())
        status = data.get('agent_status') if event == 'pane.agent_status_changed' else 'exited'
        if status not in ('working', 'idle', 'done', 'blocked', 'exited'):
            return 0
        if event == 'tab.closed':
            tab = data.get('tab_id')
            if not isinstance(tab, str) or not tab:
                return 0
            targets = [(pane, entry.get('root')) for pane, entry in panes.items()
                       if isinstance(entry, dict) and entry.get('role') == 'task' and entry.get('tab_id') == tab]
        else:
            pane = data.get('pane_id')
            if not isinstance(pane, str):
                return 0
            targets = [(pane, panes.get(pane, {}).get('root'))]
    except (OSError, ValueError, AttributeError, TypeError):
        return 0
    fleet = Path(__file__).resolve().parents[1] / 'scripts/fleet.py'
    failed = False
    for pane, root in targets:
        if not isinstance(root, str) or not root:
            continue
        env = {**os.environ, 'FLEET_ROOT': root}
        result = subprocess.run([sys.executable, str(fleet), 'event', '--pane', pane, '--status', status], env=env)
        failed |= result.returncode != 0
    if failed:
        herdr = os.environ.get('FLEET_HERDR') or os.environ.get('HERDR_BIN_PATH') or 'herdr'
        try:
            subprocess.run([herdr, 'notification', 'show', 'herdr-fleet event failed', '--body', 'Check herdr plugin log list and fleet inbox'], check=False)
        except OSError:
            pass
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
