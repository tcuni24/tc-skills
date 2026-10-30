import json
import os
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

SKILL = Path(__file__).resolve().parents[2] / 'skills' / 'herdr-fleet'
SCRIPT = SKILL / 'scripts' / 'fleet.py'

CONFIG = '''agent = "pi"

[repos.web]
path = "app-web"
dir = "web"
shared = ["node_modules"]
lockfile = "pnpm-lock.yaml"
install = "pnpm i"

[repos.be]
path = "app-api"
dir = "backend"
'''

FAKE_HERDR = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')
if os.environ.get('FAIL') and args[:2] == os.environ['FAIL'].split(): sys.exit(1)
if args[:2] == ['workspace', 'list']: result = {'workspaces': [{'workspace_id': 'w', 'focused': True}]}
elif args[:2] == ['tab', 'list']: result = {'tabs': [] if os.environ.get('NOTAB') else [{'tab_id': 't', 'label': 'hello', 'workspace_id': 'w'}] + ([{'tab_id': 'u', 'label': 'other', 'workspace_id': 'w'}] if os.environ.get('MULTI') else [])}
elif args[:2] == ['agent', 'list']: result = {'agents': [] if os.environ.get('NOAGENT') else [{'agent': 'pi', 'pane_id': 'p', 'tab_id': 't', 'agent_status': os.environ.get('AGENT_STATUS', 'idle')}] + ([{'agent': 'pi', 'pane_id': 'q', 'tab_id': 'u', 'agent_status': os.environ.get('OTHER_STATUS', 'idle')}] if os.environ.get('MULTI') else [])}
elif args[:2] == ['agent', 'get']:
    gets = sum(json.loads(line)[:2] == ['agent', 'get'] for line in open(os.environ['LOG']))
    seq = os.environ.get('GET_AFTER_SEQ', '10') if gets > int(os.environ.get('GET_AFTER_N', '999999')) else os.environ.get('GET_SEQ', '10')
    result = {'agent': {'agent_status': os.environ.get('OTHER_STATUS', 'idle') if args[2] == 'q' else os.environ.get('GET_STATUS', 'idle'), 'state_change_seq': int(os.environ.get('OTHER_SEQ', seq) if args[2] == 'q' else seq)}}
elif args[:2] == ['agent', 'prompt'] and '--wait' in args:
    if os.environ.get('PROMPT_FAIL'):
        print(json.dumps({'error': {'code': os.environ['PROMPT_FAIL']}})); sys.exit(1)
    result = {'type': 'agent_prompted'}
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


def cmd(*args, cwd=None, env=None, check=True):
    return subprocess.run(list(map(str, args)), cwd=cwd, env=env, text=True, capture_output=True, check=check)


def git(path, *args):
    return cmd('git', '-C', path, *args).stdout.strip()


def make_repo(tmp_path, repo, base):
    origin = tmp_path / (repo.name + '.git')
    cmd('git', 'init', '--bare', '--initial-branch=' + base, origin)
    cmd('git', 'init', '--initial-branch=' + base, repo)
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    (repo / '.gitignore').write_text('node_modules\n')
    (repo / 'file').write_text('initial\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'initial')
    git(repo, 'remote', 'add', 'origin', origin); git(repo, 'push', '-u', 'origin', base)
    git(repo, 'remote', 'set-head', 'origin', base)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    for name, base in [('app-web', 'develop'), ('app-api', 'master')]:
        make_repo(tmp_path, root / name, base)
    (root / 'app-web/node_modules').mkdir()
    (root / '.herdr-fleet.toml').write_text(CONFIG)
    fake = tmp_path / 'herdr'
    fake.write_text(FAKE_HERDR)
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ('HERDR_BIN_PATH', 'FLEET_ROOT', 'HERDR_PANE_ID')}
    env.update(FLEET_ROOT=str(root), FLEET_HERDR=str(fake), LOG=str(tmp_path / 'log'), HERDR_ENV='1', HERDR_WORKSPACE_ID='w', XDG_STATE_HOME=str(tmp_path / 'state'))

    def fleet(*args, check=True, cwd=None, drop=(), **updates):
        run_env = {k: v for k, v in {**env, **updates}.items() if k not in drop}
        return cmd('python3', SCRIPT, *args, env=run_env, cwd=cwd, check=check)
    return root, fleet, tmp_path / 'log'


def test_new_and_rollback(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '--no-fetch', '--prompt', 'do work')
    dest = root / 'worktrees/hello'
    assert git(dest / 'web', 'branch', '--show-current') == 'feat/hello'
    assert git(dest / 'backend', 'branch', '--show-current') == 'feat/hello'
    node = dest / 'web/node_modules'
    assert node.is_symlink() and node.readlink().is_absolute() and node.resolve() == root / 'app-web/node_modules'
    assert 'origin/develop' in (dest / 'TASK.md').read_text()
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert [c[:2] for c in calls] == [['tab', 'create'], ['agent', 'start'], ['pane', 'split'], ['agent', 'get'], ['agent', 'prompt']]
    assert calls[1] == ['agent', 'start', 'hello', '--kind', 'pi', '--pane', 'p', '--timeout', '60000']
    prompt = calls[-1][3]
    assert str(dest / 'web') in prompt and 'app-web' in prompt and prompt.endswith('do work')
    assert all(field in prompt for field in ('FLEET-REPORT', 'END FLEET-REPORT', '结果', 'PR', '测试', '证据边界', '需要决策', '退出码'))
    p = fleet('new', 'bad', '--no-fetch', check=False, FAIL='pane split')
    assert p.returncode and not (root / 'worktrees/bad').exists()
    assert not git(root / 'app-web', 'branch', '--list', 'feat/bad')
    assert fleet('new', 'Bad!', check=False).returncode


def test_send_blocked_before_submission(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '--no-fetch')
    result = fleet('send', 'hello', 'text', GET_STATUS='blocked', check=False)
    assert result.returncode == 1 and 'peek' in result.stderr
    assert not any(json.loads(line)[:2] == ['agent', 'prompt'] for line in log.read_text().splitlines())


def test_round_wait_rejects_old_idle_and_accepts_new_idle(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '--no-fetch', '--prompt', 'work')
    assert task_data(root, 'hello')['submit_seq'] == 10
    assert fleet('wait', 'hello', '--timeout', '0', check=False).returncode == 4
    result = fleet('wait', 'hello', '--timeout', '0', GET_SEQ='12')
    assert result.stdout.strip() == 'hello idle-empty idle'


def test_confirmation_failure_preserves_task(setup):
    root, fleet, log = setup
    result = fleet('new', 'hello', '--no-fetch', '--prompt', 'work', PROMPT_FAIL='timeout', check=False)
    assert result.returncode == 1 and (root / 'worktrees/hello/web').exists()
    assert not any(json.loads(line)[:2] == ['tab', 'close'] for line in log.read_text().splitlines())


@pytest.mark.parametrize('command', ['new', 'send'])
def test_fast_completion_after_confirmation_timeout(setup, command):
    root, fleet, log = setup
    if command == 'send': fleet('new', 'hello', '--no-fetch')
    log.write_text('')
    args = ('new', 'hello', '--no-fetch', '--prompt', 'work') if command == 'new' else ('send', 'hello', 'work')
    fleet(*args, PROMPT_FAIL='timeout', GET_AFTER_N='1', GET_AFTER_SEQ='12')
    assert task_data(root, 'hello')['submit_seq'] == 10
    assert fleet('wait', 'hello', '--timeout', '0', GET_SEQ='12').returncode == 0


def test_round_wait_multiple_and_blocked(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch', '--prompt', 'work')
    fleet('new', 'other', '-r', 'web', '--no-fetch', '--agent', 'none')
    path = root / 'worktrees/other/TASK.md'
    text = path.read_text().replace('"slug": "other"', '"slug": "other", "submit_seq": 9')
    path.write_text(text)
    result = fleet('wait', 'hello', 'other', '--timeout', '0', check=False)
    assert result.returncode == 1  # missing tab is an error
    assert fleet('wait', 'hello', 'other', '--timeout', '0', MULTI='1', GET_SEQ='11', OTHER_SEQ='9', check=False).returncode == 4
    any_result = fleet('wait', 'hello', 'other', '--any', '--timeout', '0', MULTI='1', GET_SEQ='11', OTHER_SEQ='9')
    assert any_result.stdout.strip() == 'hello idle-empty idle'
    all_result = fleet('wait', 'hello', 'other', '--all', '--timeout', '0', MULTI='1', GET_SEQ='11', OTHER_SEQ='10', OTHER_STATUS='blocked', check=False)
    assert all_result.returncode == 3 and len(all_result.stdout.splitlines()) == 2
    assert fleet('wait', 'hello', '--timeout', '0', GET_STATUS='blocked', GET_SEQ='11', check=False).returncode == 3


def task_data(root, slug):
    text = (root / 'worktrees' / slug / 'TASK.md').read_text()
    return json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])


def test_registration_open_and_dry_run(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch', HERDR_PANE_ID='coordinator')
    assert task_data(root, 'hello')['coordinator_pane'] == 'coordinator'
    index = json.loads((log.parent / 'state/herdr-fleet/panes.json').read_text())
    assert index['p'] == {'root': str(root), 'slug': 'hello', 'role': 'task'}
    assert index['coordinator'] == {'root': str(root), 'role': 'coordinator'}
    assert {p.name for p in (root / 'worktrees/hello').iterdir()} == {'TASK.md', 'web'}
    fleet('done', 'hello', '--dry-run', '--no-fetch')
    fleet('open', 'hello', NOTAB='1', NEW_PANE='new-task', HERDR_PANE_ID='new-coordinator')
    assert task_data(root, 'hello')['coordinator_pane'] == 'new-coordinator'
    index = json.loads((log.parent / 'state/herdr-fleet/panes.json').read_text())
    assert 'p' not in index and 'coordinator' not in index
    assert index['new-task']['slug'] == 'hello' and 'new-coordinator' in index


def test_inbox_bind_and_cleanup(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch', HERDR_PANE_ID='old')
    path = log.parent / 'state/herdr-fleet/inbox.json'
    events = [dict(root=str(root), slug='hello', coordinator_pane='old', status=s, delivery=d,
                   created='2026-01-01', dedup_key=str(i), attempts=0, last_error=None)
              for i, (s, d) in enumerate([('idle', 'pending'), ('blocked', 'delivered')])]
    path.write_text(json.dumps(events))
    before = path.read_bytes()
    assert json.loads(fleet('inbox', '--json').stdout) == events[:1]
    assert json.loads(fleet('inbox', '--all', '--json').stdout) == events
    assert 'hello' in fleet('inbox').stdout and path.read_bytes() == before
    fleet('bind', HERDR_PANE_ID='new')
    assert task_data(root, 'hello')['coordinator_pane'] == 'new'
    assert [event['coordinator_pane'] for event in json.loads(path.read_text())] == ['new', 'old']
    fleet('done', 'hello', '--no-fetch')
    assert json.loads(path.read_text()) == []
    assert not json.loads((path.parent / 'panes.json').read_text())


def test_concurrent_done_preserves_unrelated_inbox_records(setup):
    root, fleet, log = setup
    for slug in ('hello', 'other'):
        fleet('new', slug, '-r', 'web', '--no-fetch', HERDR_PANE_ID='coordinator', NEW_PANE=slug)
    path = log.parent / 'state/herdr-fleet/inbox.json'
    events = [dict(root=str(root), slug=s, coordinator_pane='coordinator', status='idle', delivery='pending',
                   created='2026-01-01', dedup_key=s, attempts=0, last_error=None) for s in ('hello', 'other', 'keep')]
    path.write_text(json.dumps(events))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda s: fleet('done', s, '--no-fetch'), ('hello', 'other')))
    assert all(r.returncode == 0 for r in results)
    assert json.loads(path.read_text()) == events[2:]


def test_legacy_task_without_coordinator_and_bind_requires_pane(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    assert 'coordinator_pane' not in task_data(root, 'hello')
    assert fleet('bind', check=False).returncode == 1
    assert json.loads(fleet('inbox', '--json').stdout) == []
    fleet('done', 'hello', '--no-fetch')
    assert not (root / 'worktrees/hello').exists()


def starts(log):
    return [call for line in log.read_text().splitlines() if (call := json.loads(line))[:2] == ['agent', 'start']]


def test_agent_args_config_and_reopen(setup):
    root, fleet, log = setup
    (root / '.herdr-fleet.toml').write_text(CONFIG + '\n[agents.devin]\nargs = ["--model", "configured"]\n')
    fleet('new', 'hello', '-r', 'web', '--no-fetch', '--agent', 'devin')
    prefix = ['agent', 'start', 'hello', '--kind', 'devin', '--pane', 'p', '--timeout', '60000']
    assert starts(log)[-1] == prefix + ['--', '--model', 'configured']
    assert task_data(root, 'hello')['agent'] == {'kind': 'devin', 'args': ['--model', 'configured']}
    fleet('open', 'hello', NOTAB='1')
    assert starts(log)[-1] == prefix + ['--', '--model', 'configured']
    fleet('open', 'hello', '--agent-arg=--foo', '--agent-arg=bar', NOTAB='1')
    assert starts(log)[-1] == prefix + ['--', '--foo', 'bar']
    assert task_data(root, 'hello')['agent'] == {'kind': 'devin', 'args': ['--foo', 'bar']}
    fleet('open', 'hello', NOTAB='1')
    assert starts(log)[-1] == prefix + ['--', '--foo', 'bar']
    fleet('open', 'hello', '--agent', 'pi', NOTAB='1')
    assert starts(log)[-1] == ['agent', 'start', 'hello', '--kind', 'pi', '--pane', 'p', '--timeout', '60000']
    fleet('new', 'other', '-r', 'web', '--no-fetch', '--agent', 'devin', '--agent-arg=--foo', '--agent-arg=bar')
    assert starts(log)[-1] == ['agent', 'start', 'other', '--kind', 'devin', '--pane', 'p', '--timeout', '60000', '--', '--foo', 'bar']
    assert task_data(root, 'other')['agent'] == {'kind': 'devin', 'args': ['--foo', 'bar']}


def test_old_task_and_agent_none(setup):
    root, fleet, log = setup
    (root / '.herdr-fleet.toml').write_text(CONFIG + '\n[agents.pi]\nargs = ["legacy default"]\n')
    fleet('new', 'hello', '-r', 'web', '--no-fetch', '--agent', 'none', '--agent-arg=--ignored')
    assert not starts(log)
    assert task_data(root, 'hello')['agent'] == {'kind': 'none', 'args': []}
    path = root / 'worktrees/hello/TASK.md'
    text = path.read_text()
    data = task_data(root, 'hello')
    del data['agent']
    before, rest = text.split('```json\n', 1)
    _, after = rest.split('\n```', 1)
    path.write_text(before + '```json\n' + json.dumps(data) + '\n```' + after)
    assert json.loads(fleet('status', 'hello', '--json').stdout)['agent_kind'] == 'pi'
    fleet('open', 'hello', NOTAB='1')
    assert starts(log)[-1] == ['agent', 'start', 'hello', '--kind', 'pi', '--pane', 'p', '--timeout', '60000', '--', 'legacy default']
    count = len(starts(log))
    fleet('open', 'hello', '--agent', 'none', NOTAB='1')
    assert len(starts(log)) == count


def test_ls_agent_kind_falls_back_to_herdr_for_old_tasks(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch', '--agent', 'none')
    assert json.loads(fleet('ls', '--json').stdout)[0]['agent_kind'] == 'pi'  # fake herdr's live agent; TASK.md says none
    path = root / 'worktrees/hello/TASK.md'
    text = path.read_text()
    data = task_data(root, 'hello')
    del data['agent']
    before, rest = text.split('```json\n', 1)
    _, after = rest.split('\n```', 1)
    path.write_text(before + '```json\n' + json.dumps(data) + '\n```' + after)
    assert json.loads(fleet('ls', '--json').stdout)[0]['agent_kind'] == 'pi'
    assert fleet('ls', NOTAB='1').stdout.split('\n')[1].split()[2] == '?'  # no record and no live agent


def test_open_preserves_task_on_failure_and_requirement_on_success(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch', '--prompt', '中文需求', '--agent-arg=原参数')
    path = root / 'worktrees/hello/TASK.md'
    before = path.read_text()
    assert fleet('open', 'hello', '--agent-arg=新参数', NOTAB='1', FAIL='pane split', check=False).returncode == 1
    assert path.read_text() == before
    fleet('open', 'hello', '--agent-arg=新参数', NOTAB='1')
    assert '中文需求' in path.read_text()
    assert task_data(root, 'hello')['agent'] == {'kind': 'pi', 'args': ['新参数']}


@pytest.mark.parametrize('config, error', [
    ('[agents.devin]\nargs = "bad"\n', 'agents.devin.args'),
    ('[agents.devin]\nargs = ["ok", 1]\n', 'agents.devin.args'),
    ('[agents.Bad]\nargs = []\n', 'agents.Bad'),
    ('agents = []\n', 'agents'),
])
def test_invalid_agent_config(setup, config, error):
    root, fleet, _ = setup
    (root / '.herdr-fleet.toml').write_text(config + '\n' + CONFIG)
    result = fleet('ls', check=False)
    assert result.returncode != 0 and error in result.stderr and '错误' in result.stderr


def test_agent_start_failure_rolls_back(setup):
    root, fleet, log = setup
    result = fleet('new', 'bad', '--no-fetch', '--agent-arg=--foo', check=False, FAIL='agent start')
    assert result.returncode != 0 and not (root / 'worktrees/bad').exists()
    for repo in ('app-web', 'app-api'):
        assert not git(root / repo, 'branch', '--list', 'feat/bad')
        assert 'worktrees/bad' not in git(root / repo, 'worktree', 'list', '--porcelain')
    assert json.loads(log.read_text().splitlines()[-1]) == ['tab', 'close', 't']


def test_done_preflight_and_force(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '--no-fetch')
    web = root / 'worktrees/hello/web'
    (web / 'dirty').write_text('dirty')
    assert fleet('done', 'hello', '--no-fetch', check=False).returncode
    assert web.exists() and (root / 'worktrees/hello/backend').exists()
    (web / 'dirty').unlink()
    (web / 'file').write_text('change')
    git(web, 'add', 'file'); git(web, 'commit', '-m', 'work')
    assert fleet('done', 'hello', '--no-fetch', check=False).returncode
    fleet('done', 'hello', '--no-fetch', '--force')
    assert not web.exists()
    assert git(root / 'app-web', 'branch', '--list', 'feat/hello')
    assert json.loads(log.read_text().splitlines()[-1])[:2] == ['tab', 'close']


def test_done_normal(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '--no-fetch')
    fleet('done', 'hello', '--no-fetch')
    assert not (root / 'worktrees/hello').exists()
    assert not git(root / 'app-web', 'branch', '--list', 'feat/hello')
    assert (root / 'app-web/node_modules').is_dir()


def test_merge_states(setup):
    root, fleet, _ = setup
    web = root / 'app-web'
    for slug in ('empty', 'merged', 'squashed', 'unmerged'):
        fleet('new', slug, '-r', 'web', '--no-fetch', '--agent', 'none')
        path = root / 'worktrees' / slug / 'web'
        if slug != 'empty':
            (path / slug).write_text(slug)
            git(path, 'add', slug); git(path, 'commit', '-m', slug)
            if slug == 'squashed':
                (path / slug).write_text('second change')
                git(path, 'add', slug); git(path, 'commit', '-m', 'second')
    git(web, 'merge', '--no-ff', 'feat/merged', '-m', 'merge')
    git(web, 'merge', '--squash', 'feat/squashed'); git(web, 'commit', '-m', 'squash')
    git(web, 'push', 'origin', 'develop')
    rows = json.loads(fleet('ls', '--json').stdout)
    assert {r['slug']: r['repos']['web']['merge'] for r in rows} == {'empty': 'no-commits', 'merged': 'merged', 'squashed': 'squash-merged', 'unmerged': 'unmerged'}
    # A squash-merged branch that was never pushed (or whose remote was deleted) needs no --force.
    fleet('done', 'squashed', '--no-fetch')
    assert not (root / 'worktrees/squashed').exists()
    assert not git(web, 'branch', '--list', 'feat/squashed')
    assert fleet('done', 'unmerged', '--no-fetch', check=False).returncode


def test_lockfile_mismatch_skips_link_and_tells_the_agent(setup):
    root, fleet, log = setup
    web = root / 'app-web'
    (web / 'pnpm-lock.yaml').write_text('v1\n')
    git(web, 'add', 'pnpm-lock.yaml'); git(web, 'commit', '-m', 'lock'); git(web, 'push', 'origin', 'develop')
    (web / 'pnpm-lock.yaml').write_text('main checkout differs\n')
    p = fleet('new', 'locky', '-r', 'web', '--no-fetch', '--prompt', 'go')
    node = root / 'worktrees/locky/web/node_modules'
    assert not node.exists() and 'pnpm i' in p.stderr
    prompt = json.loads(log.read_text().splitlines()[-1])[3]
    assert 'pnpm-lock.yaml' in prompt and f"{root / 'worktrees/locky/web'} 执行 pnpm i" in prompt
    # An independent install (ignored by git) must not block cleanup.
    (node / 'pkg').mkdir(parents=True); (node / 'pkg/index.js').write_text('x')
    fleet('done', 'locky', '--no-fetch')
    assert not (root / 'worktrees/locky').exists()
    assert (web / 'node_modules').is_dir()


def test_send_and_peek_target_pane(setup):
    _, fleet, log = setup
    fleet('new', 'hello', '--no-fetch')
    fleet('send', 'hello', '继续')
    fleet('peek', 'hello', '--lines', '5')
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(call[:4] == ['agent', 'prompt', 'p', '继续'] and '--wait' in call for call in calls)
    assert ['agent', 'read', 'p', '--source', 'recent-unwrapped', '--lines', '5'] in calls


def test_gc_legacy_dependents(setup):
    root, fleet, _ = setup
    cmd('git', 'init', '--initial-branch=master', root)
    git(root, 'config', 'user.email', 'test@example.com'); git(root, 'config', 'user.name', 'Test')
    (root / '.gitignore').write_text('app-web\napp-api\nworktrees\n')
    git(root, 'add', '.gitignore'); git(root, 'commit', '-m', 'initial')
    git(root, 'update-ref', 'refs/remotes/origin/master', 'HEAD')
    (root / '.herdr-fleet.toml').write_text('root_base = "origin/master"\n' + CONFIG)
    web = root / 'app-web'
    a, b = web / 'worktrees/a', web / 'worktrees/b'
    git(web, 'worktree', 'add', '-b', 'a', a, 'origin/develop')
    git(web, 'worktree', 'add', '-b', 'b', b, 'origin/develop')
    (a / 'node_modules').mkdir()
    (b / 'node_modules').symlink_to(a / 'node_modules')
    (b / 'dirty').write_text('untracked')
    rows = json.loads(fleet('gc', '--json').stdout)
    assert next(r for r in rows if r['path'] == str(a))['dependents'] == [str(b / 'node_modules')]
    assert {r['repo'] for r in rows} == {'web'}
    fleet('gc', '--apply')
    assert not a.exists() and b.exists()
    assert (b / 'node_modules').resolve() == web / 'node_modules'


def test_config_discovery_bases_and_errors(setup, tmp_path):
    root, fleet, _ = setup
    sub = root / 'app-api' / 'nested'
    sub.mkdir()
    # Without FLEET_ROOT the config is found by walking up from the working directory.
    fleet('new', 'found', '-r', 'be', '--no-fetch', '--agent', 'none', '--base', 'be=master', cwd=sub, drop=('FLEET_ROOT',))
    data = (root / 'worktrees/found/TASK.md').read_text()
    assert '"base": "master"' in data and '"web"' not in data
    assert fleet('new', 'x', '--base', 'nope=main', '--no-fetch', check=False).returncode
    assert fleet('new', 'x', '-r', 'web,web', '--no-fetch', check=False).returncode
    lonely = tmp_path / 'elsewhere'
    lonely.mkdir()
    p = fleet('ls', cwd=lonely, drop=('FLEET_ROOT',), check=False)
    assert p.returncode and '.herdr-fleet.toml' in p.stderr
    (root / '.herdr-fleet.toml').write_text('[repos.web]\npath = "app-web"\ndir = "TASK.md"\n')
    assert fleet('ls', check=False).returncode


def test_skill_layout():
    text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    front = re.match(r'---\nname: (\S+)\ndescription: (.+?)\n---\n', text, re.S)
    assert front and front.group(1) == 'herdr-fleet'
    assert 'references/workflow.md' in text and (SKILL / 'references/workflow.md').is_file()
    for command in ('new', 'ls', 'peek', 'send', 'done', 'gc', 'open'):
        assert f'fleet {command}' in text
    meta = (SKILL / 'agents/openai.yaml').read_text(encoding='utf-8')
    assert '$herdr-fleet' in meta


def test_phases_and_ls_errors(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    path = root / 'worktrees/hello/web'
    def phase(**env): return json.loads(fleet('ls', '--json', **env).stdout)[0]['phase']
    assert phase(NOTAB='1') == 'no-tab'
    for state in ('working', 'blocked', 'unknown'):
        assert phase(AGENT_STATUS=state) == state
    assert phase() == 'idle-empty'
    (path / 'file').write_text('changed')
    assert phase() == 'uncommitted'
    git(path, 'add', 'file'); git(path, 'commit', '-m', 'work')
    assert phase() == 'review'
    text = fleet('ls').stdout
    assert '{' not in text and 'web +1/-0 dirty0 unmerged' in text
    row = json.loads(fleet('ls', '--json').stdout)[0]
    assert {'slug', 'tab', 'agent', 'repos', 'agent_kind', 'phase'} <= row.keys()
    assert {'branch', 'dirty', 'ahead', 'behind', 'merge'} <= row['repos']['web'].keys()
    git(root / 'app-web', 'merge', '--no-ff', 'feat/hello', '-m', 'merge')
    git(root / 'app-web', 'push', 'origin', 'develop')
    assert phase(AGENT_STATUS='done') == 'merged'
    (path / 'file').write_text('dirty after merge')
    assert phase() == 'merged'
    fleet('new', 'other', '-r', 'web', '--no-fetch')
    git(path, 'branch', '-m', 'moved')
    rows = json.loads(fleet('ls', '--json').stdout)
    assert rows[0]['phase'] == 'error' and rows[0]['error'] and rows[1]['slug'] == 'other'


def test_status_and_pr(setup, tmp_path):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    path = root / 'worktrees/hello/web'
    (path / 'file').write_text('changed')
    git(path, 'add', 'file'); git(path, 'commit', '-m', 'example commit')
    (path / 'untracked').write_text('x')
    p = fleet('status', 'hello', FLEET_GH=str(tmp_path / 'missing'))
    assert all(s in p.stdout for s in ('uncommitted', 'example commit', 'file |', '?? untracked', 'agent output tail', 'PR: 未知'))
    gh = tmp_path / 'gh'
    gh.write_text('#!/bin/sh\nprintf \'[{"number":12,"url":"https://example.test/pr/12","state":"OPEN"}]\'\n')
    gh.chmod(0o755)
    row = json.loads(fleet('status', 'hello', '--json', FLEET_GH=str(gh)).stdout)
    assert row['repos']['web']['pr']['url'] == 'https://example.test/pr/12'
    assert row['repos']['web']['files'] == ['?? untracked']
    assert 'example commit' in row['repos']['web']['commits'][0]
    assert row['repos']['web']['diff_stat'] and row['agent_output'] == 'agent output tail'
    assert 'https://example.test/pr/12' in fleet('status', 'hello', FLEET_GH=str(gh)).stdout
    assert not json.loads(fleet('status', 'hello', '--json', '--lines', '0', NOTAB='1').stdout)['agent_output']


@pytest.mark.parametrize(('output', 'expected'), [
    ('before\nFLEET-REPORT\n结果：完成\nEND FLEET-REPORT\nafter', 'FLEET-REPORT\n结果：完成\nEND FLEET-REPORT'),
    ('FLEET-REPORT\nold\nEND FLEET-REPORT\nFLEET-REPORT\nnew\nEND FLEET-REPORT', 'FLEET-REPORT\nnew\nEND FLEET-REPORT'),
    ('FLEET-REPORT\nincomplete', None),
    ('FLEET-REPORT\nold\nEND FLEET-REPORT\nFLEET-REPORT\nincomplete', 'FLEET-REPORT\nold\nEND FLEET-REPORT'),
    ('nothing', None),
])
def test_status_report_blocks(setup, output, expected):
    _, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    text = fleet('status', 'hello', AGENT_OUTPUT=output)
    row = json.loads(fleet('status', 'hello', '--json', AGENT_OUTPUT=output).stdout)
    assert text.returncode == 0 and row['report'] == expected
    assert (expected or '未找到回报块') in text.stdout
    assert {'slug', 'tab', 'agent', 'agent_kind', 'phase', 'repos', 'next_step', 'agent_output'} <= row.keys()
    ls = json.loads(fleet('ls', '--json').stdout)[0]
    assert {'slug', 'tab', 'agent', 'agent_kind', 'phase', 'repos'} <= ls.keys()
    assert 'report' not in ls


def test_status_zero_lines_skips_report_read(setup):
    _, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    log.write_text('')
    row = json.loads(fleet('status', 'hello', '--json', '--lines', '0', AGENT_OUTPUT='FLEET-REPORT\nx\nEND FLEET-REPORT').stdout)
    assert row['agent_output'] == '' and row['report'] is None
    assert not any(json.loads(line)[:2] == ['agent', 'read'] for line in log.read_text().splitlines())
    assert '未找到回报块' in fleet('status', 'hello', '--lines', '0').stdout


def test_status_limits(setup, tmp_path):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    path = root / 'worktrees/hello/web'
    for i in range(22):
        (path / f'file-{i:02}').write_text('x')
    row = json.loads(fleet('status', 'hello', '--json', '--lines', '0', FLEET_GH=str(tmp_path / 'missing')).stdout)
    assert len(row['repos']['web']['files']) == 20 and row['repos']['web']['files_omitted'] == 2
    assert '另有 2 行' in fleet('status', 'hello', '--lines', '0', FLEET_GH=str(tmp_path / 'missing')).stdout
    p = fleet('status', 'hello', '--lines', '-1', check=False)
    assert p.returncode == 1 and '--lines 不能为负数' in p.stderr


@pytest.mark.parametrize('failure', ['missing-worktree', 'invalid-base'])
def test_status_repository_error_preserves_other_repositories(setup, failure):
    root, fleet, _ = setup
    fleet('new', 'hello', '--no-fetch')
    if failure == 'missing-worktree':
        git(root / 'app-api', 'worktree', 'remove', root / 'worktrees/hello/backend')
    else:
        git(root / 'app-api', 'update-ref', '-d', 'refs/remotes/origin/master')
    row = json.loads(fleet('status', 'hello', '--json', '--lines', '0').stdout)
    assert row['phase'] == 'error' and 'be:' in row['error']
    assert row['repos']['web']['base'] == 'origin/develop' and 'be' not in row['repos']
    text = fleet('status', 'hello', '--lines', '0').stdout
    assert 'phase: error' in text and 'be:' in text and 'web: branch' in text


def test_ls_table_distinguishes_tab_without_agent(setup):
    _, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    header, row = fleet('ls', NOAGENT='1').stdout.splitlines()
    assert header.split()[:3] == ['slug', 'tab', 'agent']
    assert row.split()[:4] == ['hello', 't', 'pi', '-']
    assert fleet('ls', NOTAB='1').stdout.splitlines()[1].split()[:2] == ['hello', '-']


def test_wait(setup):
    _, fleet, log = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    assert fleet('wait', 'hello', '--timeout', '1.5').stdout.strip() == 'hello idle-empty idle'
    assert ['agent', 'wait', 'p', '--timeout', '1500'] in [json.loads(x) for x in log.read_text().splitlines()]
    assert fleet('wait', 'hello', AGENT_STATUS='blocked', check=False).returncode == 3
    assert fleet('wait', 'hello', WAIT_FAIL='timed out', check=False).returncode == 4
    assert fleet('wait', 'hello', WAIT_STDOUT='{"error":{"code":"timeout","message":"timed out waiting for agent status"}}', check=False).returncode == 4
    assert fleet('wait', 'hello', WAIT_FAIL='invalid timeout argument', check=False).returncode == 1
    assert fleet('wait', 'hello', WAIT_STDOUT='{"error":{"code":"invalid_argument","message":"timed out parameter invalid"}}', check=False).returncode == 1
    assert fleet('wait', 'hello', WAIT_FAIL='other failure', check=False).returncode == 1
    assert fleet('wait', 'hello', NOTAB='1', check=False).returncode == 1


@pytest.mark.parametrize('state, code', [('idle', 0), ('done', 0), ('blocked', 3)])
def test_wait_terminal_state_survives_repository_error(setup, state, code):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    git(root / 'app-web', 'worktree', 'remove', root / 'worktrees/hello/web')
    result = fleet('wait', 'hello', AGENT_STATUS=state, check=False)
    assert result.returncode == code and result.stdout.strip() == f'hello error {state}'
    assert '仓库错误' in result.stderr


def test_done_rejects_locked_second_worktree_without_changes(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '--no-fetch')
    dest = root / 'worktrees/hello'
    git(root / 'app-api', 'worktree', 'lock', '--reason', 'in use', dest / 'backend')
    before = (dest / 'TASK.md').read_bytes()
    for extra in (('--dry-run',), ()):
        result = fleet('done', 'hello', '--no-fetch', '--force', '--discard', *extra, check=False)
        assert result.returncode == 1 and '锁定' in (result.stdout + result.stderr)
        assert (dest / 'TASK.md').read_bytes() == before
        assert (dest / 'web/node_modules').is_symlink()
        for repo, dirname in [('app-web', 'web'), ('app-api', 'backend')]:
            assert (dest / dirname).is_dir()
            assert git(root / repo, 'branch', '--list', 'feat/hello')
        assert not any(json.loads(line)[:2] == ['tab', 'close'] for line in log.read_text().splitlines())


def test_done_dry_run_and_extra_files(setup):
    root, fleet, log = setup
    fleet('new', 'hello', '--no-fetch')
    dest = root / 'worktrees/hello'
    (dest / 'unexpected').write_text('x')
    for extra in (('--dry-run',), ()):
        p = fleet('done', 'hello', '--no-fetch', *extra, check=False)
        assert p.returncode == 1 and 'unexpected' in p.stderr
    (dest / 'unexpected').unlink()
    (dest / 'web/dirty').write_text('x')
    dry = fleet('done', 'hello', '--no-fetch', '--dry-run', check=False)
    assert dry.returncode == 1 and '--discard' in dry.stdout and 'backend' in dry.stdout
    assert fleet('done', 'hello', '--no-fetch', check=False).returncode == 1
    before = log.read_text()
    dry = fleet('done', 'hello', '--no-fetch', '--dry-run', '--discard')
    assert '将删除' in dry.stdout and dest.exists() and (dest / 'web/dirty').exists()
    assert 'tab", "close' not in log.read_text()[len(before):]
    result = fleet('done', 'hello', '--no-fetch', '--discard')
    assert '已删除 worktree' in result.stdout and '已删除分支' in result.stdout and '已关闭 tab' in result.stdout


def test_done_dry_run_unmerged(setup):
    root, fleet, _ = setup
    fleet('new', 'hello', '-r', 'web', '--no-fetch')
    path = root / 'worktrees/hello/web'
    (path / 'file').write_text('change')
    git(path, 'add', 'file'); git(path, 'commit', '-m', 'work')
    dry = fleet('done', 'hello', '--no-fetch', '--dry-run', check=False)
    assert dry.returncode == 1 and '--force' in dry.stdout
    assert fleet('done', 'hello', '--no-fetch', check=False).returncode == 1
    dry = fleet('done', 'hello', '--no-fetch', '--dry-run', '--force')
    assert '将保留分支 feat/hello' in dry.stdout and path.exists()
    fleet('done', 'hello', '--no-fetch', '--force')
    assert git(root / 'app-web', 'branch', '--list', 'feat/hello')
