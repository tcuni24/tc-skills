import json
import os
from pathlib import Path
import re
import subprocess

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
elif args[:2] == ['tab', 'list']: result = {'tabs': [{'tab_id': 't', 'label': 'hello', 'workspace_id': 'w'}]}
elif args[:2] == ['agent', 'list']: result = {'agents': [{'agent': 'pi', 'pane_id': 'p', 'tab_id': 't', 'agent_status': 'idle'}]}
elif args[:2] == ['tab', 'create']: result = {'tab': {'tab_id': 't'}, 'root_pane': {'pane_id': 'p'}}
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
    env = {k: v for k, v in os.environ.items() if k not in ('HERDR_BIN_PATH', 'FLEET_ROOT')}
    env.update(FLEET_ROOT=str(root), FLEET_HERDR=str(fake), LOG=str(tmp_path / 'log'), HERDR_ENV='1', HERDR_WORKSPACE_ID='w')

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
    assert [c[:2] for c in calls] == [['tab', 'create'], ['agent', 'start'], ['pane', 'split'], ['agent', 'prompt']]
    prompt = calls[-1][-1]
    assert str(dest / 'web') in prompt and 'app-web' in prompt and prompt.endswith('do work')
    p = fleet('new', 'bad', '--no-fetch', check=False, FAIL='pane split')
    assert p.returncode and not (root / 'worktrees/bad').exists()
    assert not git(root / 'app-web', 'branch', '--list', 'feat/bad')
    assert fleet('new', 'Bad!', check=False).returncode


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
    prompt = json.loads(log.read_text().splitlines()[-1])[-1]
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
    assert ['agent', 'prompt', 'p', '继续'] in calls
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
