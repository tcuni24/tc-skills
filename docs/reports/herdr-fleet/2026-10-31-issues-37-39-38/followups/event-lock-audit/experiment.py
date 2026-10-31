#!/usr/bin/env python3
"""Bounded-concurrency probe: does task_event's in-lock `agent get` stall other tasks?

Diagnoses report item C on tc-skills @ 98831bf: `task_event` calls
`herdr agent get` while holding the global `fleet_state(write=True)` flock.
The probe parks task-a's event handler inside a fake-herdr `agent get`
barrier, then checks — via /proc/locks pending-flock entries on the state
lock inode — whether a second task's `event`, an unrelated pane's `event`,
and a read-only `inbox` queue behind the same lock. No sleeps are used for
correctness: every wait is a deadline-bounded poll for a concrete condition,
and the fake-herdr pause is itself bounded (10 s internal deadline).

Everything runs under a TemporaryDirectory: FLEET_ROOT points at a synthetic
root, XDG_STATE_HOME at a synthetic state dir, FLEET_HERDR at the fake
(binary copied verbatim from tests/herdr-fleet/test_fleet.py @ 98831bf).
The repository under test is only read; nothing is modified.

Exit 0 = all assertions held (blocking observed, clean finish, state intact).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

FLEET = Path('/home/glx/scripts/tc-skills-fleet/worktrees/event-lock-audit/tc-skills/skills/herdr-fleet/scripts/fleet.py')

CONFIG = '''agent = "none"

[repos.web]
path = "app-web"
dir = "web"
'''

# Verbatim copy of FAKE_HERDR from tests/herdr-fleet/test_fleet.py @ 98831bf.
FAKE_HERDR = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')
if os.environ.get('BARRIER') and args[:2] == os.environ.get('BARRIER_CALL', '').split():
    barrier = Path(os.environ['BARRIER'])
    barrier.touch()
    deadline = time.monotonic() + 10
    while not barrier.with_suffix('.release').exists():
        if time.monotonic() >= deadline: sys.exit(2)
        time.sleep(0.01)
if args == ['--version']: print(os.environ.get('HERDR_VERSION', 'herdr 0.9.1')); sys.exit(0)
if os.environ.get('FAIL') and args[:2] == os.environ['FAIL'].split(): sys.exit(1)
if args == ['integration', 'status']:
    print(os.environ.get('INTEGRATIONS', 'pi: current (v9) (/path)')); sys.exit(0)
if args[:2] == ['plugin', 'list']: result = {'plugins': json.loads(os.environ.get('PLUGINS', '[]'))}
elif args[:2] == ['pane', 'get']:
    if os.environ.get('COORD_MISSING'): sys.exit(1)
    result = {'pane': {'pane_id': args[2]}}
elif args[:2] == ['workspace', 'list']: result = {'workspaces': [{'workspace_id': 'w', 'focused': True}]}
elif args[:2] == ['tab', 'list']: result = {'tabs': [] if os.environ.get('NOTAB') else [{'tab_id': 't', 'label': 'hello', 'workspace_id': 'w'}] + ([{'tab_id': 'u', 'label': 'other', 'workspace_id': 'w'}] if os.environ.get('MULTI') else [])}
elif args[:2] == ['agent', 'list']: result = {'agents': [] if os.environ.get('NOAGENT') else [{'agent': 'pi', 'pane_id': 'p', 'tab_id': 't', 'agent_status': os.environ.get('AGENT_STATUS', 'idle')}] + ([{'agent': 'pi', 'pane_id': 'q', 'tab_id': 'u', 'agent_status': os.environ.get('OTHER_STATUS', 'idle')}] if os.environ.get('MULTI') else [])}
elif args[:2] == ['agent', 'get']:
    if args[2] in ('coordinator', 'old', 'new'):
        if os.environ.get('COORD_MISSING'):
            print(json.dumps({'error': {'code': 'pane_not_found'}})); sys.exit(1)
        status = Path(os.environ['COORD_STATUS_FILE']).read_text().strip() if os.environ.get('COORD_STATUS_FILE') else os.environ.get('COORD_STATUS', 'working')
        print(json.dumps({'result': {'agent': {'agent_status': status, 'state_change_seq': 1}}})); sys.exit(0)
    if os.environ.get('FAKE_STATE_FILE') and Path(os.environ['FAKE_STATE_FILE']).exists():
        print(json.dumps({'result': {'agent': json.loads(Path(os.environ['FAKE_STATE_FILE']).read_text())}})); sys.exit(0)
    gets = sum(json.loads(line)[:2] == ['agent', 'get'] for line in open(os.environ['LOG']))
    seq = os.environ.get('GET_AFTER_SEQ', '10') if gets > int(os.environ.get('GET_AFTER_N', '999999')) else os.environ.get('GET_SEQ', '10')
    result = {'agent': {'agent_status': os.environ.get('OTHER_STATUS', 'idle') if args[2] == 'q' else os.environ.get('GET_STATUS', 'idle'), 'state_change_seq': int(os.environ.get('OTHER_SEQ', seq) if args[2] == 'q' else seq)}}
elif args[:2] == ['agent', 'prompt'] and '--wait' in args:
    if args[4:] != ['--wait', '--until', 'working', '--until', 'blocked', '--timeout', '5000']:
        print('invalid confirmation arguments', file=sys.stderr); sys.exit(1)
    if os.environ.get('PROMPT_FAIL'):
        print(json.dumps({'error': {'code': os.environ['PROMPT_FAIL']}})); sys.exit(1)
    if os.environ.get('FAKE_STATE_FILE'):
        Path(os.environ['FAKE_STATE_FILE']).write_text(json.dumps({'agent_status': 'working', 'state_change_seq': 11}))
    result = {'type': 'agent_prompted'}
elif args[:2] == ['agent', 'prompt']:
    time.sleep(float(os.environ.get('DELIVERY_DELAY', '0')))
    result = {'type': os.environ.get('DELIVERY_RESULT', 'agent_prompted')}
elif args[:2] == ['agent', 'wait']:
    if os.environ.get('WAIT_STDOUT'):
        print(os.environ['WAIT_STDOUT']); sys.exit(1)
    if os.environ.get('WAIT_FAIL'):
        print(os.environ['WAIT_FAIL'], file=sys.stderr); sys.exit(1)
    result = {}  # The real wait response schema is not specified by --help.
elif args[:2] == ['agent', 'read']:
    print(os.environ.get('AGENT_OUTPUT', 'agent output tail')); sys.exit(0)
elif args[:2] == ['tab', 'create']: result = {'tab': {'tab_id': 't'}, 'root_pane': {'pane_id': os.environ.get('NEW_PANE', 'p')}}
elif args[:2] == ['pane', 'split']: result = {'pane': {'pane_id': 'q'}}
else: result = {}
print(json.dumps({'result': result}))
'''

FAKE_ENV_KEYS = {
    'LOG', 'BARRIER', 'BARRIER_CALL', 'FAIL', 'COORD_MISSING', 'COORD_STATUS', 'COORD_STATUS_FILE',
    'FAKE_STATE_FILE', 'GET_AFTER_SEQ', 'GET_AFTER_N', 'GET_SEQ', 'GET_STATUS', 'OTHER_SEQ', 'OTHER_STATUS',
    'AGENT_STATUS', 'MULTI', 'NOTAB', 'NOAGENT', 'PLUGINS', 'INTEGRATIONS', 'HERDR_VERSION', 'NEW_PANE',
    'PROMPT_FAIL', 'DELIVERY_DELAY', 'DELIVERY_RESULT', 'WAIT_STDOUT', 'WAIT_FAIL', 'AGENT_OUTPUT',
    'FLEET_ROOT', 'FLEET_HERDR', 'FLEET_GH', 'HERDR_BIN_PATH', 'HERDR_PANE_ID', 'HERDR_ENV',
    'HERDR_WORKSPACE_ID', 'XDG_STATE_HOME',
}

results = []


def record(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''), flush=True)


def wait_for(desc, condition, deadline_s=15):
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f'timed out waiting for: {desc}')


def lock_entries(inode):
    """/proc/locks rows for `inode` -> [{'pending', 'pid', 'mode', 'raw'}].

    Format: `<id>: [->] FLOCK ADVISORY WRITE <pid> maj:min:ino start end`;
    the `->` marks a queued (blocked) request, so mode/pid/inode sit at fixed
    offsets from the dev:ino field regardless of the marker.
    """
    rows = []
    for line in Path('/proc/locks').read_text().splitlines():
        parts = line.split()
        try:
            i = next(i for i, p in enumerate(parts) if p.count(':') == 2 and p.rsplit(':', 1)[1] == str(inode))
        except StopIteration:
            continue
        rows.append({'pending': '->' in parts, 'pid': int(parts[i - 1]), 'mode': parts[i - 2], 'raw': line.strip()})
    return rows


def fleet_cmd(env, *args):
    return subprocess.Popen([sys.executable, str(FLEET), *args], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main():
    work = Path(tempfile.mkdtemp(prefix='fleet-lock-audit-'))
    print(f'workdir: {work}')
    root = work / 'root'
    (root / 'worktrees').mkdir(parents=True)
    (root / 'app-web').mkdir()
    (root / '.herdr-fleet.toml').write_text(CONFIG)
    fake = work / 'fake-herdr'
    fake.write_text(FAKE_HERDR)
    fake.chmod(0o755)
    state = work / 'state' / 'herdr-fleet'
    state.mkdir(parents=True)
    log = work / 'herdr.log'

    base_env = {k: v for k, v in os.environ.items() if k not in FAKE_ENV_KEYS}
    base_env.update(FLEET_ROOT=str(root), FLEET_HERDR=str(fake), LOG=str(log),
                    HERDR_ENV='1', HERDR_WORKSPACE_ID='w', XDG_STATE_HOME=str(work / 'state'),
                    GET_STATUS='done', GET_SEQ='12', COORD_STATUS='working')

    for slug in ('task-a', 'task-b'):
        dest = root / 'worktrees' / slug
        dest.mkdir()
        task = {'slug': slug, 'created': '2026-10-31T00:00:00+00:00', 'repos': {},
                'agent': {'kind': 'none', 'args': []}, 'coordinator_pane': 'coordinator',
                'event_round': {'baseline': 10, 'notified': False, 'last_terminal_seq': -1},
                'submit_seq': 9}
        (dest / 'TASK.md').write_text('# 任务\n\n```json\n' + json.dumps(task) + '\n```\n')
    (state / 'panes.json').write_text(json.dumps({
        'pa': {'root': str(root), 'slug': 'task-a', 'role': 'task', 'tab_id': 'ta'},
        'pb': {'root': str(root), 'slug': 'task-b', 'role': 'task', 'tab_id': 'tb'},
        'coordinator': {'root': str(root), 'role': 'coordinator'}}))

    barrier = work / 'barrier'

    # --- Act 1: park task-a's event inside the in-lock `agent get` barrier ---
    env_a = {**base_env, 'BARRIER': str(barrier), 'BARRIER_CALL': 'agent get'}
    proc_a = fleet_cmd(env_a, 'event', '--pane', 'pa', '--status', 'done')
    wait_for('task-a fake-herdr to reach the agent-get barrier', barrier.exists)
    record('A parked inside agent get', proc_a.poll() is None, f'pid={proc_a.pid}')

    lock_file = state / 'lock'
    inode = lock_file.stat().st_ino
    holders = [r for r in lock_entries(inode) if not r['pending']]
    record('A holds global fleet_state lock during agent get',
           any(r['pid'] == proc_a.pid and r['mode'] == 'WRITE' for r in holders),
           f'inode={inode} holders={holders}')

    # --- Act 2: launch waiters while the barrier is held ---
    proc_b = fleet_cmd(dict(base_env), 'event', '--pane', 'pb', '--status', 'done')   # task-b event
    proc_c = fleet_cmd(dict(base_env), 'event', '--pane', 'pz', '--status', 'done')   # unregistered-pane event
    proc_i = fleet_cmd(dict(base_env), 'inbox', '--json')                             # read-only inbox

    def all_queued():
        rows = lock_entries(inode)
        pending = {r['pid'] for r in rows if r['pending']}
        return rows if {proc_b.pid, proc_c.pid, proc_i.pid} <= pending else None

    try:
        rows = wait_for('B/C/I pending flock entries on the state lock inode', all_queued)
        print('--- /proc/locks rows on state lock inode (barrier held) ---')
        for r in rows:
            who = {proc_a.pid: 'A:event pa', proc_b.pid: 'B:event pb', proc_c.pid: 'C:event pz', proc_i.pid: 'I:inbox'}.get(r['pid'], '?')
            print(f'  {r["raw"]}    <- {who}')
        record('B/C/I queued on the same global lock', True)
        for name, proc in (('B', proc_b), ('C', proc_c), ('I', proc_i)):
            wchan = Path(f'/proc/{proc.pid}/wchan').read_text().strip()
            print(f'  {name} wchan={wchan}')
    except AssertionError as e:
        record('B/C/I queued on the same global lock', False, str(e))

    record('B still running behind barrier', proc_b.poll() is None)
    record('C still running behind barrier', proc_c.poll() is None)
    record('I still running behind barrier', proc_i.poll() is None)

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    record('A issued agent get pa', ['agent', 'get', 'pa'] in calls)
    record('B has NOT reached its own agent get pb', ['agent', 'get', 'pb'] not in calls,
           'log shows B never passed the fleet_state gate while barrier held')
    record('no agent prompt attempted', not any(c[:2] == ['agent', 'prompt'] for c in calls))

    # --- Act 3: release; everyone must finish cleanly ---
    barrier.with_suffix('.release').touch()
    out = {}
    for name, proc in (('A', proc_a), ('B', proc_b), ('C', proc_c), ('I', proc_i)):
        stdout, stderr = proc.communicate(timeout=30)
        out[name] = (proc.returncode, stdout.strip(), stderr.strip())
        print(f'--- {name} exit={proc.returncode} stdout={stdout.strip()!r} stderr={stderr.strip()!r}')

    record('all four processes exit 0', all(v[0] == 0 for v in out.values()))

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    record('B reached agent get pb after release', ['agent', 'get', 'pb'] in calls)
    print('--- fake-herdr call log ---')
    for line in log.read_text().splitlines():
        print(' ', line.strip())

    inbox = json.loads((state / 'inbox.json').read_text())
    pending = sorted((e['slug'], e['status'], e['delivery']) for e in inbox)
    record('inbox has both done events pending', pending == [('task-a', 'done', 'pending'), ('task-b', 'done', 'pending')],
           json.dumps(pending))
    seen = json.loads(out['I'][1] or '[]')
    record('I observed A\'s already-appended event', len(seen) >= 1, f'I saw {len(seen)} pending event(s)')

    for slug in ('task-a', 'task-b'):
        text = (root / 'worktrees' / slug / 'TASK.md').read_text()
        data = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
        record(f'{slug} event_round notified', data['event_round'] == {'baseline': 10, 'notified': True, 'last_terminal_seq': 12},
               json.dumps(data['event_round']))

    print('\n=== SUMMARY ===')
    failed = [r for r in results if not r[1]]
    print(f'{len(results) - len(failed)}/{len(results)} checks passed; workdir kept at {work}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
