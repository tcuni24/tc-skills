#!/usr/bin/env python3
"""Route registered pane events to fleet without modifying its state."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    event = os.environ.get('HERDR_PLUGIN_EVENT')
    if event not in ('pane.agent_status_changed', 'pane.exited'):
        return 0
    try:
        payload = json.loads(os.environ.get('HERDR_PLUGIN_EVENT_JSON', '{}'))
        data = payload.get('data', payload)
        pane = data.get('pane_id')
        status = 'exited' if event == 'pane.exited' else data.get('agent_status')
        if not isinstance(pane, str) or status not in ('working', 'idle', 'done', 'blocked', 'exited'):
            return 0
        state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
        panes = json.loads((state / 'herdr-fleet/panes.json').read_text())
        root = panes.get(pane, {}).get('root')
        if not isinstance(root, str) or not root:
            return 0
    except (OSError, ValueError, AttributeError, TypeError):
        return 0
    env = {**os.environ, 'FLEET_ROOT': root}
    fleet = Path(__file__).resolve().parents[1] / 'scripts/fleet.py'
    result = subprocess.run([sys.executable, str(fleet), 'event', '--pane', pane, '--status', status], env=env)
    if result.returncode:
        herdr = os.environ.get('FLEET_HERDR') or os.environ.get('HERDR_BIN_PATH') or 'herdr'
        try:
            subprocess.run([herdr, 'notification', 'show', 'herdr-fleet event failed', '--body', 'Check herdr plugin log list and fleet inbox'], check=False)
        except OSError:
            pass
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
