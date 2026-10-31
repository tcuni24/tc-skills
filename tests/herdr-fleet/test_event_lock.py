"""Regression tests: `fleet event` must not run `agent get` under the global lock.

task_event used to call `herdr agent get` inside `fleet_state(write=True)`, so
one task's stalled IPC queued every other task's event and inbox. These tests
use a self-contained fake herdr plus a synthetic FLEET_ROOT/XDG_STATE_HOME —
no `fleet new`, no git, no real panes. Every wait is a deadline-bounded poll
for a concrete condition (barrier file, process exit, log entries), never a
timing guess.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

SKILL = Path(__file__).resolve().parents[2] / 'skills' / 'herdr-fleet'
SCRIPT = SKILL / 'scripts' / 'fleet.py'

CONFIG = '''agent = "none"

[repos.web]
path = "app-web"
dir = "web"
'''

# Minimal fake herdr covering the event/inbox/deliver paths used here. Pane
# 'pa' answers agent get from GET_*, 'pb' from OTHER_*, 'coordinator' from
# COORD_STATUS; BARRIER/BARRIER_CALL parks a chosen call, FAIL fails it.
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
if os.environ.get('FAIL') and args[:2] == os.environ['FAIL'].split(): sys.exit(1)
if args[:2] == ['agent', 'get']:
    if args[2] == 'coordinator':
        status = os.environ.get('COORD_STATUS', 'working')
        print(json.dumps({'result': {'agent': {'agent_status': status, 'state_change_seq': 1}}})); sys.exit(0)
    if args[2] == 'pb':
        status, seq = os.environ.get('OTHER_STATUS', 'idle'), int(os.environ.get('OTHER_SEQ', '10'))
    else:
        status, seq = os.environ.get('GET_STATUS', 'idle'), int(os.environ.get('GET_SEQ', '10'))
    print(json.dumps({'result': {'agent': {'agent_status': status, 'state_change_seq': seq}}})); sys.exit(0)
if args[:2] == ['agent', 'prompt']: result = {'type': 'agent_prompted'}
elif args[:2] == ['tab', 'list']: result = {'tabs': [{'tab_id': 't-' + s, 'label': s, 'workspace_id': 'w'} for s in ('task-a', 'task-b')]}
elif args[:2] == ['agent', 'list']: result = {'agents': [{'agent': 'none', 'pane_id': 'p' + s[-1], 'tab_id': 't-' + s, 'agent_status': 'idle'} for s in ('task-a', 'task-b')]}
elif args[:2] == ['notification', 'show']: result = {}
else: result = {}
print(json.dumps({'result': result}))
'''

ENV_KEYS = {
    'LOG', 'BARRIER', 'BARRIER_CALL', 'FAIL', 'COORD_STATUS', 'GET_STATUS', 'GET_SEQ',
    'OTHER_STATUS', 'OTHER_SEQ', 'FLEET_ROOT', 'FLEET_HERDR', 'FLEET_GH', 'HERDR_BIN_PATH',
    'HERDR_PANE_ID', 'HERDR_ENV', 'HERDR_WORKSPACE_ID', 'XDG_STATE_HOME',
}


def wait_for(desc, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f'timed out waiting for: {desc}')


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'root'
    (root / 'worktrees').mkdir(parents=True)
    (root / 'app-web').mkdir()
    (root / '.herdr-fleet.toml').write_text(CONFIG)
    fake = tmp_path / 'fake-herdr'
    fake.write_text(FAKE_HERDR)
    fake.chmod(0o755)
    state = tmp_path / 'state' / 'herdr-fleet'
    state.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(FLEET_ROOT=str(root), FLEET_HERDR=str(fake), LOG=str(tmp_path / 'log'),
               HERDR_ENV='1', HERDR_WORKSPACE_ID='w', XDG_STATE_HOME=str(tmp_path / 'state'))

    class Fleet:
        def run(self, *args, check=True, **updates):
            return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                  env={**env, **updates}, text=True, capture_output=True, check=check)

        def spawn(self, *args, **updates):
            return subprocess.Popen([sys.executable, str(SCRIPT), *map(str, args)],
                                    env={**env, **updates}, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)

    return root, Fleet(), tmp_path / 'log', state


def make_task(root, state, slug, pane):
    dest = root / 'worktrees' / slug
    dest.mkdir(parents=True, exist_ok=True)
    task = {'slug': slug, 'created': '2026-10-31T00:00:00+00:00', 'repos': {},
            'agent': {'kind': 'none', 'args': []}, 'coordinator_pane': 'coordinator',
            'event_round': {'baseline': 10, 'notified': False, 'last_terminal_seq': -1},
            'submit_seq': 9}
    (dest / 'TASK.md').write_text('# 任务\n\n```json\n' + json.dumps(task, indent=2) + '\n```\n')
    path = state / 'panes.json'
    panes = json.loads(path.read_text()) if path.exists() else {}
    panes[pane] = {'root': str(root), 'slug': slug, 'role': 'task', 'tab_id': 't-' + slug}
    panes.setdefault('coordinator', {'root': str(root), 'role': 'coordinator'})
    path.write_text(json.dumps(panes))


def task_data(root, slug):
    text = (root / 'worktrees' / slug / 'TASK.md').read_text()
    return json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def finish(proc, timeout=15):
    _, stderr = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, stderr


def release(barrier, procs):
    """Let the barriered process finish, then sweep anything still running."""
    barrier.with_suffix('.release').touch()
    deadline = time.monotonic() + 20
    for proc in procs:
        if proc is None or proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()


def test_event_get_does_not_block_other_tasks_or_inbox(setup, tmp_path):
    root, fleet, log, state = setup
    make_task(root, state, 'task-a', 'pa')
    make_task(root, state, 'task-b', 'pb')
    barrier = tmp_path / 'barrier'
    proc_a = fleet.spawn('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12',
                         BARRIER=str(barrier), BARRIER_CALL='agent get')
    procs = []
    try:
        wait_for('task-a parked inside agent get', barrier.exists)
        assert proc_a.poll() is None
        # While task-a's query is parked, a second task's event, an
        # unregistered-pane event and a read-only inbox must all complete:
        # only task-a's own task lock is held, never the cross-task state lock.
        procs = [fleet.spawn('event', '--pane', 'pb', '--status', 'done', OTHER_STATUS='done', OTHER_SEQ='12'),
                 fleet.spawn('event', '--pane', 'pz', '--status', 'done'),
                 fleet.spawn('inbox', '--json')]
        for proc in procs:
            finish(proc)
        assert proc_a.poll() is None  # task-a is still parked, yet everyone finished
        assert ['agent', 'get', 'pb'] in calls(log)
        barrier.with_suffix('.release').touch()
        finish(proc_a)
    finally:
        release(barrier, [proc_a, *procs])
    rows = json.loads(fleet.run('inbox', '--json').stdout)
    assert sorted((row['slug'], row['status']) for row in rows) == [('task-a', 'done'), ('task-b', 'done')]
    for slug in ('task-a', 'task-b'):
        assert task_data(root, slug)['event_round'] == {'baseline': 10, 'notified': True, 'last_terminal_seq': 12}


@pytest.mark.parametrize('rebind', ['other-task', 'removed'])
def test_event_result_dropped_when_pane_rebound_during_query(setup, tmp_path, rebind):
    root, fleet, log, state = setup
    make_task(root, state, 'task-a', 'pa')
    make_task(root, state, 'task-b', 'pb')
    barrier = tmp_path / 'barrier'
    proc_a = fleet.spawn('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12',
                         BARRIER=str(barrier), BARRIER_CALL='agent get')
    try:
        wait_for('task-a parked inside agent get', barrier.exists)
        # The pane is re-bound (open/register_task/done style writers) while
        # the query is in flight; the stale result must not reach a new task.
        panes_path = state / 'panes.json'
        panes = json.loads(panes_path.read_text())
        if rebind == 'other-task':
            panes['pa'] = {'root': str(root), 'slug': 'task-b', 'role': 'task', 'tab_id': 'tb'}
        else:
            del panes['pa']
        temp = panes_path.with_suffix('.tmp')
        temp.write_text(json.dumps(panes))
        os.replace(temp, panes_path)
        barrier.with_suffix('.release').touch()
        finish(proc_a)
    finally:
        release(barrier, [proc_a])
    assert json.loads(fleet.run('inbox', '--all', '--json').stdout) == []
    for slug in ('task-a', 'task-b'):
        assert task_data(root, slug)['event_round']['notified'] is False


def test_same_task_event_waits_on_task_lock_not_state_lock(setup, tmp_path):
    root, fleet, log, state = setup
    make_task(root, state, 'task-a', 'pa')
    make_task(root, state, 'task-b', 'pb')
    barrier = tmp_path / 'barrier'
    proc_a = fleet.spawn('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12',
                         BARRIER=str(barrier), BARRIER_CALL='agent get')
    proc_b = proc_a2 = None
    try:
        wait_for('task-a parked inside agent get', barrier.exists)
        proc_a2 = fleet.spawn('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12')
        proc_b = fleet.spawn('event', '--pane', 'pb', '--status', 'done', OTHER_STATUS='done', OTHER_SEQ='12')
        finish(proc_b)  # proves the global state lock is free while task-a is parked
        assert proc_a2.poll() is None  # the same-task event still waits on task_lock(task-a)
        assert sum(call == ['agent', 'get', 'pa'] for call in calls(log)) == 1
        barrier.with_suffix('.release').touch()
        finish(proc_a)
        finish(proc_a2)
    finally:
        release(barrier, [proc_a, proc_a2, proc_b])
    rows = json.loads(fleet.run('inbox', '--all', '--json').stdout)
    # Serialization plus dedup: the second event sees the first one's notified
    # round and folds into the single pending record.
    assert len(rows) == 2 and sorted(row['slug'] for row in rows) == ['task-a', 'task-b']


def test_send_waits_for_inflight_event_then_supersedes(setup, tmp_path):
    root, fleet, log, state = setup
    make_task(root, state, 'task-a', 'pa')
    make_task(root, state, 'task-b', 'pb')
    barrier = tmp_path / 'barrier'
    proc_a = fleet.spawn('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12',
                         BARRIER=str(barrier), BARRIER_CALL='agent get')
    proc_send = proc_b = None
    try:
        wait_for('task-a parked inside agent get', barrier.exists)
        proc_send = fleet.spawn('send', 'task-a', 'next round', GET_STATUS='working', GET_SEQ='12')
        proc_b = fleet.spawn('event', '--pane', 'pb', '--status', 'done', OTHER_STATUS='done', OTHER_SEQ='12')
        finish(proc_b)  # global state lock is free
        # send already passed its unlocked tab/agent lookup but cannot enter
        # task_lock(task-a): no `agent get`/`agent prompt` of its own yet.
        assert proc_send.poll() is None
        assert not any(call[:2] == ['agent', 'prompt'] for call in calls(log))
        assert sum(call == ['agent', 'get', 'pa'] for call in calls(log)) == 1
        barrier.with_suffix('.release').touch()
        finish(proc_a)
        finish(proc_send)
    finally:
        release(barrier, [proc_a, proc_send, proc_b])
    # The send established round 12, inheriting the previous terminal seq.
    assert task_data(root, 'task-a')['event_round'] == {'baseline': 12, 'notified': False, 'last_terminal_seq': 12}
    rows = json.loads(fleet.run('inbox', '--all', '--json').stdout)
    # task-b appended while task-a was parked, so it leads; the round-10 done
    # recorded before send's new round is superseded, never delivered as a
    # fresh completion — the ordering matches the old lock model.
    assert [(row['slug'], row['status'], row['delivery']) for row in rows] == [
        ('task-b', 'done', 'pending'), ('task-a', 'done', 'superseded')]


def test_event_get_failure_and_exited_semantics_unchanged(setup):
    root, fleet, log, state = setup
    make_task(root, state, 'task-a', 'pa')
    result = fleet.run('event', '--pane', 'pa', '--status', 'done', GET_STATUS='done', GET_SEQ='12',
                       FAIL='agent get', check=False)
    assert result.returncode == 1 and '错误' in result.stderr
    assert json.loads(fleet.run('inbox', '--json').stdout) == []
    assert task_data(root, 'task-a')['event_round']['notified'] is False
    log.write_text('')
    fleet.run('event', '--pane', 'pa', '--status', 'exited')
    assert not any(call == ['agent', 'get', 'pa'] for call in calls(log))  # exited never queries
    rows = json.loads(fleet.run('inbox', '--json').stdout)
    assert len(rows) == 1 and rows[0]['status'] == 'exited' and rows[0]['slug'] == 'task-a'
