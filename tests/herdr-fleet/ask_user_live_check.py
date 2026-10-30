#!/usr/bin/env python3
"""Isolated live check of ask_user's herdr:blocked reporting.

Spawns a REAL pi TUI process on a pty with extension discovery disabled and
two explicit extensions: skills/herdr-fleet/extensions/ask-user.ts and the
locally installed herdr pi integration (herdr-agent-state.ts, loaded with -e
exactly as herdr installs it). HERDR_* point at a fake pane id and a fake unix
socket served by this script, so the real integration speaks its real
pane.report_agent wire protocol without touching the live herdr daemon or any
real pane.

The script types `/ask-user ...` into the pty, asserts the pane is reported
blocked while the dialog waits and returns to idle after the answer, and dumps
the captured frames. Usage:

    python3 tests/herdr-fleet/ask_user_live_check.py [--integration PATH] [--out PATH]
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time

SKILL = Path(__file__).resolve().parents[2] / 'skills' / 'herdr-fleet'


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--ext', default=str(SKILL / 'extensions' / 'ask-user.ts'))
    p.add_argument('--integration', default=str(Path.home() / '.pi/agent/extensions/herdr-agent-state.ts'))
    p.add_argument('--pi', default='pi')
    p.add_argument('--out', default='')
    p.add_argument('--timeout', type=float, default=30.0)
    return p.parse_args()


def sock_server(path, records, ready):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(8)
    ready.set()
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            data = b''
            while not data.endswith(b'\n'):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            for line in data.decode().splitlines():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    records.append({'unparsed': line})
            try:
                conn.sendall(b'{}\n')
            except OSError:
                pass


def main():
    a = parse_args()
    for name, path in (('extension', a.ext), ('integration', a.integration)):
        if not Path(path).is_file():
            print(f'missing {name}: {path}', file=sys.stderr)
            return 2
    import threading

    with tempfile.TemporaryDirectory(prefix='ask-user-live-') as tmp:
        sock_path = os.path.join(tmp, 'herdr.sock')
        agent_dir = os.path.join(tmp, 'agent')
        os.makedirs(agent_dir)
        records, ready = [], threading.Event()
        server = threading.Thread(target=sock_server, args=(sock_path, records, ready), daemon=True)
        server.start()
        ready.wait(5)

        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
        env = {
            **os.environ,
            'PI_CODING_AGENT_DIR': agent_dir,
            'HERDR_ENV': '1',
            'HERDR_PANE_ID': 'live-check-pane',
            'HERDR_SOCKET_PATH': sock_path,
            'TERM': 'xterm-256color',
        }
        proc = subprocess.Popen(
            [a.pi, '--no-extensions', '--no-session', '-e', a.integration, '-e', a.ext],
            stdin=slave, stdout=slave, stderr=slave, cwd=tmp, env=env, close_fds=True)
        os.close(slave)
        os.set_blocking(master, False)
        sel = selectors.DefaultSelector()
        sel.register(master, selectors.EVENT_READ)

        raw = bytearray()
        deadline = time.monotonic() + a.timeout

        def pump(until):
            while time.monotonic() < until:
                for key, _ in sel.select(0.2):
                    try:
                        chunk = os.read(key.fd, 65536)
                    except OSError:
                        chunk = b''
                    if not chunk:
                        return False
                    raw.extend(chunk)
            return True

        def agent_states():
            return [r['params']['state'] for r in records
                    if r.get('method') == 'pane.report_agent' and isinstance(r.get('params'), dict)]

        def wait_for(pred, label, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                pump(time.monotonic() + 0.2)
                if pred():
                    return True
            print(f'TIMEOUT waiting for {label}', file=sys.stderr)
            return False

        steps = []
        ok = True
        ok &= wait_for(lambda: agent_states() and agent_states()[-1] == 'idle', 'initial idle', deadline - time.monotonic())
        steps.append(('startup->idle', ok))
        if ok:
            os.write(master, b'/ask-user Approve deploy? | yes | no\r')
            ok = wait_for(lambda: 'blocked' in agent_states(), 'blocked report', 20)
            steps.append(('ask-user->blocked', ok))
        if ok:
            time.sleep(0.5)
            os.write(master, b'\r')
            ok = wait_for(lambda: agent_states() and agent_states()[-1] != 'blocked', 'idle after answer', 20)
            steps.append(('answer->recover', ok))

        proc.send_signal(signal.SIGTERM)
        pump(time.monotonic() + 2)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()

        out = a.out or str(Path(tmp) / 'capture.json')
        report = {
            'records': records,
            'agent_states': agent_states(),
            'pty_tail': raw.decode(errors='replace')[-3000:],
        }
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({'steps': steps, 'agent_states': agent_states(),
                          'blocked_messages': [r['params'].get('message') for r in records
                                               if r.get('method') == 'pane.report_agent' and r['params'].get('state') == 'blocked'],
                          'capture': out}, indent=2, ensure_ascii=False))
        return 0 if all(ok for _, ok in steps) else 1


if __name__ == '__main__':
    sys.exit(main())
