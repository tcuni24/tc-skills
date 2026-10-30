#!/usr/bin/env python3
"""herdr-fleet: one coordinator, many task tabs.

Each task gets a set of git worktrees under <root>/worktrees/<slug>/ and a
herdr tab running its own agent. The project root is the nearest directory
holding .herdr-fleet.toml (or FLEET_ROOT); see references/workflow.md.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

CONFIG = '.herdr-fleet.toml'
HERDR = os.environ.get('FLEET_HERDR') or os.environ.get('HERDR_BIN_PATH') or 'herdr'
SLUG = re.compile(r'^[a-z][a-z0-9-]{0,31}$')
NAME = re.compile(r'^[A-Za-z0-9._-]+$')
ROOT = None
REPOS = {}
SETTINGS = {}


class SubmissionError(RuntimeError):
    pass


def run(*args, cwd=None, check=True, input=None):
    p = subprocess.run(args, cwd=cwd, text=True, input=input, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"命令失败 {' '.join(map(str, args))}: {p.stderr.strip()}")
    return p.stdout.strip()


def git(repo, *args, check=True, input=None):
    return run('git', '-C', str(repo), *map(str, args), check=check, input=input)


def herdr(*args, json_output=True):
    output = run(HERDR, *map(str, args))
    return json.loads(output)['result'] if json_output else output


def find_root():
    if os.environ.get('FLEET_ROOT'):
        root = Path(os.environ['FLEET_ROOT']).resolve()
        if not (root / CONFIG).is_file():
            raise RuntimeError(f'FLEET_ROOT 下没有 {CONFIG}：{root}')
        return root
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for d in (start, *start.parents):
            if (d / CONFIG).is_file():
                return d
    raise RuntimeError(f'找不到 {CONFIG}：请在项目根目录创建（格式见 references/workflow.md），或设置 FLEET_ROOT')


def load_config(root):
    try:
        raw = tomllib.loads((root / CONFIG).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RuntimeError(f'{CONFIG} 无法读取：{e}') from e
    repos = {}
    for key, item in (raw.get('repos') or {}).items():
        if not re.fullmatch(r'[a-z][a-z0-9]*', key) or not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise RuntimeError(f'{CONFIG} 中 repos.{key} 无效：键须为小写字母和数字，且须有 path')
        dirname, shared = item.get('dir', key), item.get('shared', [])
        if (not isinstance(dirname, str) or not NAME.fullmatch(dirname) or dirname in ('TASK.md', '.', '..')
                or not isinstance(shared, list)
                or not all(isinstance(s, str) and NAME.fullmatch(s) and s not in ('.', '..') for s in shared)):
            raise RuntimeError(f'{CONFIG} 中 repos.{key} 的 dir 或 shared 无效')
        repos[key] = {'path': (root / item['path']).resolve(), 'dir': dirname, 'base': item.get('base'),
                      'shared': shared, 'lockfile': item.get('lockfile'), 'install': item.get('install')}
    dirs = [r['dir'] for r in repos.values()]
    if not repos or len(set(dirs)) != len(dirs):
        raise RuntimeError(f'{CONFIG} 须至少配置一个 [repos.<key>]，且各 dir 不重复')
    agents = raw.get('agents', {})
    if not isinstance(agents, dict):
        raise RuntimeError(f'{CONFIG} 中 agents 须为表')
    for kind, item in agents.items():
        try:
            valid_agent(kind)
        except RuntimeError as e:
            raise RuntimeError(f'{CONFIG} 中 agents.{kind} 的 agent 种类无效') from e
        if not isinstance(item, dict) or not isinstance(item.get('args', []), list) or not all(isinstance(arg, str) for arg in item.get('args', [])):
            raise RuntimeError(f'{CONFIG} 中 agents.{kind}.args 须为字符串列表')
    return repos, {'agent': raw.get('agent', 'pi'), 'agents': agents, 'root_base': raw.get('root_base')}


def valid_slug(slug):
    if not SLUG.fullmatch(slug):
        raise RuntimeError('任务名须以小写字母开头，只含小写字母、数字、连字符，最长 32 字符')
    return slug


def valid_agent(agent):
    if not re.fullmatch(r'[a-z][a-z0-9-]*', agent or ''):
        raise RuntimeError(f'agent 种类无效：{agent}')
    return agent


def base_ref(repo):
    return git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')


def repo_base(key):
    return REPOS[key]['base'] or base_ref(REPOS[key]['path'])


def fetch(repo, base):
    if base.startswith('origin/'):
        p = subprocess.run(['git', '-C', str(repo), 'fetch', 'origin', base[7:]], capture_output=True, text=True)
        if p.returncode:
            print(f'警告：fetch {base} 失败，使用本地引用：{p.stderr.strip()}', file=sys.stderr)


def worktrees(repo):
    records = []
    for block in git(repo, 'worktree', 'list', '--porcelain').split('\n\n'):
        entry = {}
        for line in block.splitlines():
            key, _, value = line.partition(' ')
            entry[key] = value
        if 'worktree' in entry:
            records.append(entry)
    return records


def merge_status(repo, branch, base, original_base=None):
    git(repo, 'rev-parse', '--verify', base + '^{commit}')
    if original_base and not git(repo, 'rev-list', f'{original_base}..{branch}'):
        return 'no-commits'
    if git(repo, 'rev-parse', branch) == git(repo, 'rev-parse', base):
        return 'no-commits'
    p = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', branch, base])
    if p.returncode == 0:
        return 'merged'
    if p.returncode != 1:
        raise RuntimeError('无法计算合并状态')
    cherry = git(repo, 'cherry', base, branch).splitlines()
    if cherry and all(line.startswith('- ') for line in cherry):
        return 'merged'
    mb = git(repo, 'merge-base', base, branch)
    tree = git(repo, 'rev-parse', f'{branch}^{{tree}}')
    # commit-tree needs an identity even in repositories without local git config.
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME='fleet', GIT_AUTHOR_EMAIL='fleet@localhost', GIT_COMMITTER_NAME='fleet', GIT_COMMITTER_EMAIL='fleet@localhost')
    objects = Path(git(repo, 'rev-parse', '--git-common-dir'))
    if not objects.is_absolute():
        objects = repo / objects
    # The synthetic squash commit goes to a throwaway object dir so the repository is never written.
    with tempfile.TemporaryDirectory(prefix='fleet-objects-') as temp:
        env['GIT_OBJECT_DIRECTORY'] = temp
        env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = str(objects.resolve() / 'objects')
        p = subprocess.run(['git', '-C', str(repo), 'commit-tree', tree, '-p', mb], input='fleet merge check\n', capture_output=True, text=True, env=env)
        if p.returncode:
            raise RuntimeError('无法计算 squash 合并状态：' + p.stderr.strip())
        cherry = subprocess.run(['git', '-C', str(repo), 'cherry', base, p.stdout.strip()], capture_output=True, text=True, env=env)
        if cherry.returncode:
            raise RuntimeError('无法计算 squash 合并状态：' + cherry.stderr.strip())
        if cherry.stdout.startswith('- '):
            return 'squash-merged'
    return 'unmerged'


def task_dir(slug):
    return ROOT / 'worktrees' / valid_slug(slug)


def load(slug):
    try:
        text = (task_dir(slug) / 'TASK.md').read_text()
        data = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
    except (OSError, IndexError, ValueError) as e:
        raise RuntimeError(f'任务不存在或 TASK.md 无效：{slug}') from e
    unknown = [key for key in data['repos'] if key not in REPOS]
    if unknown:
        raise RuntimeError(f'任务 {slug} 用到的仓库 {",".join(unknown)} 不在 {CONFIG} 中')
    return data


def save(data):
    path = task_dir(data['slug']) / 'TASK.md'
    before, rest = path.read_text().split('```json\n', 1)
    _, after = rest.split('\n```', 1)
    path.write_text(before + '```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```' + after)


def submit(data, pane, text):
    agent = herdr('agent', 'get', pane)['agent']
    if agent['agent_status'] == 'blocked':
        raise RuntimeError(f"agent 已 blocked，请先 fleet peek {data['slug']}")
    seq = agent['state_change_seq']
    if not isinstance(seq, int):
        raise RuntimeError('agent 未提供有效的 state_change_seq')
    data['submit_seq'] = seq
    save(data)
    p = subprocess.run([HERDR, 'agent', 'prompt', pane, text, '--wait', '--until', 'working', '--until', 'blocked', '--timeout', '5000'], capture_output=True, text=True)
    if p.returncode:
        try:
            error = json.loads(p.stdout or p.stderr).get('error', {}).get('code')
        except ValueError:
            error = None
        if error in ('timeout', 'agent_prompt_stalled'):
            current = herdr('agent', 'get', pane)['agent']
            if current['state_change_seq'] > seq and current['agent_status'] in ('idle', 'done', 'blocked'):
                return
        if error == 'agent_blocked':
            raise RuntimeError(f"agent 已 blocked，请先 fleet peek {data['slug']}")
        raise RuntimeError(f'开始确认失败：{p.stderr or p.stdout}')


def tasks():
    root = ROOT / 'worktrees'
    if not root.exists():
        return []
    result = []
    for path in sorted(root.iterdir()):
        if path.is_dir() and SLUG.fullmatch(path.name) and (path / 'TASK.md').is_file():
            try:
                result.append(load(path.name))
            except RuntimeError:
                continue
    return result


def herdr_state():
    try:
        return herdr('tab', 'list', '--workspace', workspace())['tabs'], herdr('agent', 'list')['agents']
    except (RuntimeError, OSError, KeyError, ValueError):
        return [], []


def workspace(explicit=None):
    w = explicit or os.environ.get('HERDR_WORKSPACE_ID')
    if w:
        return w
    for item in herdr('workspace', 'list')['workspaces']:
        if item.get('focused'):
            return item['workspace_id']
    raise RuntimeError('没有聚焦的 herdr workspace')


def find_tab(slug, tabs):
    return next((t for t in tabs if t.get('label') == slug), None)


def link_shared(key, path):
    """Symlink shared entries (e.g. node_modules) from the main checkout; return why not, or None."""
    cfg = REPOS[key]
    if not cfg['shared']:
        return None
    lock = cfg['lockfile']
    if lock and (path / lock).exists() and (cfg['path'] / lock).exists() and (path / lock).read_bytes() != (cfg['path'] / lock).read_bytes():
        # Installing through the symlink would rewrite the main checkout's dependencies.
        return f'{lock} 与主 checkout 不同'
    missing = [n for n in cfg['shared'] if not (cfg['path'] / n).exists()]
    if missing:
        return f'主 checkout 缺少 {", ".join(missing)}'
    for n in cfg['shared']:
        if not (path / n).exists():
            (path / n).symlink_to(cfg['path'] / n, target_is_directory=(cfg['path'] / n).is_dir())
    return None


def agent_options(a, data=None):
    saved = (data or {}).get('agent', {})
    kind = valid_agent(a.agent or saved.get('kind') or SETTINGS['agent'])
    args = a.agent_arg if a.agent_arg is not None else (
        saved.get('args', []) if saved and kind == saved.get('kind') else SETTINGS['agents'].get(kind, {}).get('args', []))
    return {'kind': kind, 'args': [] if kind == 'none' else args}


def create_tab(data, agent, focus=False, explicit=None, prompt=None):
    if not explicit and not os.environ.get('HERDR_ENV'):
        print('未在 herdr 中且未指定 workspace，跳过 tab', file=sys.stderr)
        return
    w = workspace(explicit)
    dest = task_dir(data['slug'])
    tab = herdr('tab', 'create', '--workspace', w, '--cwd', dest, '--label', data['slug'], '--focus' if focus else '--no-focus')
    tid = tab['tab']['tab_id']
    try:
        pane = tab['root_pane']['pane_id']
        if agent['kind'] != 'none':
            herdr('agent', 'start', data['slug'], '--kind', agent['kind'], '--pane', pane, '--timeout', '60000',
                  *(['--', *agent['args']] if agent['args'] else []))
        herdr('pane', 'split', pane, '--direction', 'right', '--cwd', dest, '--no-focus')
    except Exception:
        herdr('tab', 'close', tid)
        raise
    if prompt and agent['kind'] != 'none':
            context = [f'任务目录：{dest}']
            for key, details in data['repos'].items():
                cfg = REPOS[key]
                context.append(f"{dest / cfg['dir']}：{cfg['path'].name} 的 worktree，分支 {details['branch']}（base {details['base']}）")
            for key, info in data.get('install', {}).items():
                how = f"执行 {info['command']}" if info.get('command') else '自行安装依赖'
                context.append(f"{REPOS[key]['dir']} 未链接 {', '.join(REPOS[key]['shared'])}（{info['reason']}）：需要时在 {dest / REPOS[key]['dir']} {how}。")
            context.append(f'只在上述 worktree 中修改和提交，不要改主 checkout（{ROOT}/<仓库>）。开始前先读各 worktree 里的 AGENTS.md。')
            context.append('最终回复必须包含以下回报块：\nFLEET-REPORT\n结果：完成 / 部分完成 / 阻塞\nPR：链接或无\n测试：每条命令及退出码\n证据边界：哪些已真实验证，哪些只是合成数据、mock、空页面或未覆盖\n需要决策：需要用户决定的事项或无\nEND FLEET-REPORT')
            try:
                submit(data, pane, '\n'.join(context) + '\n\n' + prompt)
            except (RuntimeError, OSError, ValueError, KeyError) as e:
                raise SubmissionError(str(e)) from e
    return True


def parse_bases(items):
    bases = {}
    for item in items or []:
        key, sep, ref = item.partition('=')
        if not sep or not ref or key not in REPOS:
            raise RuntimeError(f'--base 格式为 <仓库键>=<ref>，仓库键须为 {",".join(REPOS)} 之一')
        bases[key] = ref
    return bases


def new(a):
    slug = valid_slug(a.slug)
    agent = agent_options(a)
    if not re.fullmatch(r'[a-z][a-z0-9-]*', a.type):
        raise RuntimeError('分支类型无效')
    keys = a.repos.split(',') if a.repos else list(REPOS)
    if not keys or len(set(keys)) != len(keys) or any(k not in REPOS for k in keys):
        raise RuntimeError(f'仓库须为 {",".join(REPOS)} 的组合')
    bases = parse_bases(a.base)
    dest = task_dir(slug)
    if dest.exists():
        raise RuntimeError('任务目录已存在，请使用 fleet open')
    data = {'slug': slug, 'created': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'repos': {}, 'agent': agent}
    made = []
    dest.mkdir(parents=True)
    if (ROOT / '.git').exists() and subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', str(dest)]).returncode:
        print('警告：worktrees/ 未被项目根仓库的 .gitignore 忽略', file=sys.stderr)
    try:
        for key in keys:
            repo, dirname = REPOS[key]['path'], REPOS[key]['dir']
            base = bases.get(key) or repo_base(key)
            if not a.no_fetch:
                fetch(repo, base)
            commit = git(repo, 'rev-parse', '--verify', base + '^{commit}')
            branch = f'{a.type}/{slug}'
            exists = subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet', 'refs/heads/' + branch]).returncode == 0
            if exists and any(w.get('branch') == 'refs/heads/' + branch for w in worktrees(repo)):
                raise RuntimeError(f'分支已被其他 worktree 使用：{branch}')
            path = dest / dirname
            git(repo, 'worktree', 'add', *([] if exists else ['-b', branch]), path, branch if exists else base)
            made.append((repo, path, branch, not exists))
            data['repos'][key] = {'base': base, 'base_commit': commit, 'branch': branch}
            reason = link_shared(key, path)
            if reason:
                command = REPOS[key]['install']
                data.setdefault('install', {})[key] = {'reason': reason, 'command': command}
                print(f"提示：{path} {reason}，未链接 {', '.join(REPOS[key]['shared'])}；" + (f'请在该目录执行 {command}' if command else '请在该目录自行安装依赖'), file=sys.stderr)
        text = '# 任务\n\n```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```\n'
        if a.prompt:
            text += '\n## 需求\n\n' + a.prompt + '\n'
        (dest / 'TASK.md').write_text(text)
    except Exception:
        for repo, path, branch, created in reversed(made):
            git(repo, 'worktree', 'remove', '--force', path, check=False)
            if created:
                git(repo, 'branch', '-D', branch, check=False)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    try:
        create_tab(data, agent, a.focus, a.workspace, a.prompt)
    except SubmissionError:
        raise
    except Exception:
        for repo, path, branch, created in reversed(made):
            git(repo, 'worktree', 'remove', '--force', path, check=False)
            if created: git(repo, 'branch', '-D', branch, check=False)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    print(f'已创建任务 {slug}')


def repo_info(repo, path, branch, base, original_base=None):
    dirty = len(git(path, 'status', '--porcelain').splitlines())
    counts = git(repo, 'rev-list', '--left-right', '--count', f'{branch}...{base}').split()
    return {'branch': branch, 'dirty': dirty, 'ahead': int(counts[0]), 'behind': int(counts[1]), 'merge': merge_status(repo, branch, base, original_base)}


def task_phase(agent_status, repos):
    if agent_status == '-': return 'no-tab'
    if agent_status in ('working', 'blocked', 'unknown'): return agent_status
    if agent_status not in ('idle', 'done') or not repos: return 'unknown'
    values = list(repos.values())
    if all(r['merge'] in ('merged', 'squash-merged') for r in values): return 'merged'
    if any(r['dirty'] > 0 for r in values): return 'uncommitted'
    if any(r['ahead'] > 0 and r['merge'] == 'unmerged' for r in values): return 'review'
    if all(r['merge'] == 'no-commits' for r in values): return 'idle-empty'
    return 'unknown'


def agent_kind(data, agent):
    # The live pane's kind is the truth; TASK.md's record (absent on tasks that predate it) is the fallback.
    return (agent or {}).get('agent') or data.get('agent', {}).get('kind') or '?'


def task_row(data, tabs, agents, do_fetch=False):
    tab = find_tab(data['slug'], tabs)
    agent = next((x for x in agents if tab and x.get('tab_id') == tab['tab_id']), None)
    repos, errors = {}, []
    for key, info in data['repos'].items():
        try:
            if do_fetch: fetch(REPOS[key]['path'], info['base'])
            repos[key] = repo_info(REPOS[key]['path'], task_dir(data['slug']) / REPOS[key]['dir'], info['branch'], info['base'], info['base_commit'])
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            errors.append(f'{key}: {e}')
    state = agent.get('agent_status', '-') if agent else '-'
    row = {'slug': data['slug'], 'tab': tab['tab_id'] if tab else '-', 'agent': state, 'repos': repos,
           'agent_kind': agent_kind(data, agent), 'phase': 'error' if errors else task_phase(state, repos)}
    if errors: row['error'] = '; '.join(errors)
    return row


def display(rows, as_json):
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif rows:
        keys = list(rows[0])
        vals = [[json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (dict, list)) else str(row[k]) for k in keys] for row in rows]
        widths = [max(len(keys[i]), *(len(row[i]) for row in vals)) for i in range(len(keys))]
        print('  '.join(k.ljust(widths[i]) for i, k in enumerate(keys)))
        for row in vals:
            print('  '.join(v.ljust(widths[i]) for i, v in enumerate(row)))


def ls(a):
    tabs, agents = herdr_state()
    rows = []
    for data in tasks():
        try:
            rows.append(task_row(data, tabs, agents, a.fetch))
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            tab = find_tab(data['slug'], tabs)
            agent = next((x for x in agents if tab and x.get('tab_id') == tab['tab_id']), None)
            rows.append({'slug': data['slug'], 'tab': tab['tab_id'] if tab else '-', 'agent': agent.get('agent_status', '-') if agent else '-',
                         'repos': {}, 'agent_kind': agent_kind(data, agent), 'phase': 'error', 'error': str(e)})
    if a.json:
        display(rows, True)
    else:
        display([{'slug': r['slug'], 'tab': r['tab'], 'agent': f"{r['agent_kind']} {r['agent']}", 'phase': r['phase'],
                  'repos': r.get('error') or '; '.join(f"{k} +{v['ahead']}/-{v['behind']} dirty{v['dirty']} {v['merge']}" for k, v in r['repos'].items())} for r in rows], False)


def task_agent(slug):
    load(slug)
    tabs, agents = herdr_state()
    tab = find_tab(slug, tabs)
    agent = next((x for x in agents if tab and x.get('tab_id') == tab['tab_id']), None)
    if not agent:
        raise RuntimeError('任务没有运行中的 agent')
    # The 'agent' field of `agent list` is the kind (e.g. pi), shared by many panes; only pane_id is unique.
    return agent['pane_id']


def pr_info(repo, branch):
    try:
        p = subprocess.run([os.environ.get('FLEET_GH', 'gh'), 'pr', 'list', '--head', branch, '--state', 'all', '--json', 'number,url,state', '--limit', '1'], cwd=repo, capture_output=True, text=True, timeout=10)
        result = json.loads(p.stdout) if p.returncode == 0 else []
        return result[0] if isinstance(result, list) and result else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def last_report(output):
    lines = output.splitlines(keepends=True)
    start = None
    report = None
    for i, line in enumerate(lines):
        if line.rstrip('\r\n') == 'FLEET-REPORT':
            start = i
        elif line.rstrip('\r\n') == 'END FLEET-REPORT' and start is not None:
            report = ''.join(lines[start:i + 1]).rstrip('\r\n')
            start = None
    return report


def status(a):
    if a.lines < 0: raise RuntimeError('--lines 不能为负数')
    data = load(a.slug)
    tabs, agents = herdr_state()
    row = task_row(data, tabs, agents)
    suggestions = {'working': '等待或使用 fleet wait', 'blocked': '使用 fleet peek 查看问题并交用户决策',
                   'uncommitted': '用 fleet send 让 agent 提交', 'review': '核查 diff，让 agent 推送并提 PR',
                   'idle-empty': '使用 fleet peek 确认是否卡住', 'merged': f'先执行 fleet done {a.slug} --dry-run，再执行 fleet done {a.slug}',
                   'no-tab': f'使用 fleet open {a.slug}', 'unknown': '使用 fleet peek 检查状态', 'error': '检查仓库错误'}
    row['next_step'] = suggestions[row['phase']]
    for key, info in data['repos'].items():
        if key not in row['repos']: continue
        repo, path = REPOS[key]['path'], task_dir(a.slug) / REPOS[key]['dir']
        try:
            changes = git(path, 'status', '--porcelain').splitlines()
            commits = git(repo, 'log', '--oneline', f"{info['base']}..{info['branch']}", '-10').splitlines()
            stat = git(repo, 'diff', '--stat', f"{info['base']}...{info['branch']}").splitlines()
            row['repos'][key].update(base=info['base'], files=changes[:20], files_omitted=max(0, len(changes)-20),
                                     commits=commits, diff_stat=stat[:20], diff_stat_omitted=max(0, len(stat)-20), pr=pr_info(repo, info['branch']))
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            row['phase'], row['next_step'] = 'error', suggestions['error']
            row['error'] = '; '.join(filter(None, (row.get('error'), f'{key}: {e}')))
            del row['repos'][key]
    tab = find_tab(a.slug, tabs)
    agent = next((x for x in agents if tab and x.get('tab_id') == tab['tab_id']), None)
    row['agent_output'] = herdr('agent', 'read', agent['pane_id'], '--source', 'recent-unwrapped', '--lines', a.lines, json_output=False) if agent and a.lines else ''
    row['report'] = last_report(row['agent_output'])
    if a.json:
        print(json.dumps(row, ensure_ascii=False, indent=2)); return
    print(f"{row['slug']}  tab: {row['tab']}  agent: {row['agent_kind']} {row['agent']}  phase: {row['phase']}\n下一步：{row['next_step']}")
    if row.get('error'): print(f"仓库错误：{row['error']}")
    for key, info in row['repos'].items():
        print(f"\n{key}: branch {info['branch']}  base {info['base']}  +{info['ahead']}/-{info['behind']}  {info['merge']}")
        print('未提交文件：\n' + ('\n'.join(info['files']) or '无'))
        if info['files_omitted']: print(f"另有 {info['files_omitted']} 行")
        print('提交：\n' + ('\n'.join(info['commits']) or '无'))
        print('diff stat：\n' + ('\n'.join(info['diff_stat']) or '无'))
        if info['diff_stat_omitted']: print(f"另有 {info['diff_stat_omitted']} 行")
        print('PR: ' + (f"#{info['pr']['number']} {info['pr']['state']} {info['pr']['url']}" if info['pr'] else '未知'))
    print('\n回报：\n' + (row['report'] or '未找到回报块'))
    if row['agent_output']: print('\nagent 最近输出：\n' + row['agent_output'])


def legacy_wait(a):
    if a.timeout is not None and a.timeout < 0: raise RuntimeError('--timeout 不能为负数')
    pane = task_agent(a.slug)
    args = [HERDR, 'agent', 'wait', pane] + (['--timeout', str(int(a.timeout * 1000))] if a.timeout is not None else [])
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=a.timeout + 2 if a.timeout is not None else None)
    except subprocess.TimeoutExpired:
        print('错误：等待超时', file=sys.stderr); return 4
    if p.returncode:
        error = (p.stderr + ' ' + p.stdout).strip()
        codes = []
        for output in (p.stdout, p.stderr):
            try:
                parsed = json.loads(output)
                if isinstance(parsed, dict) and isinstance(parsed.get('error'), dict):
                    codes.append(parsed['error'].get('code'))
            except ValueError:
                pass
        timed_out = 'timeout' in codes if codes else bool(re.search(r'\btimed\s+out\b|超时', error, re.I))
        if timed_out:
            print(f'错误：等待超时：{error}', file=sys.stderr); return 4
        raise RuntimeError(f'agent wait 失败：{error}')
    data = load(a.slug)
    tabs, agents = herdr_state()
    agent = next((x for x in agents if x.get('pane_id') == pane), None)
    state = agent.get('agent_status', '-') if agent else '-'
    if state not in ('idle', 'done', 'blocked'): raise RuntimeError(f'等待后 agent 状态无效：{state}')
    try:
        row = task_row(data, tabs, agents)
        phase = row['phase']
        if row.get('error'): print(f"仓库错误：{row['error']}", file=sys.stderr)
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        phase = 'error'
        print(f'仓库错误：{e}', file=sys.stderr)
    print(f'{a.slug} {phase} {state}')
    return 3 if state == 'blocked' else 0


def wait(a):
    if a.timeout is not None and a.timeout < 0: raise RuntimeError('--timeout 不能为负数')
    if len(a.slugs) == 1 and 'submit_seq' not in load(a.slugs[0]):
        a.slug = a.slugs[0]
        return legacy_wait(a)
    pending = {slug: (load(slug), task_agent(slug)) for slug in a.slugs}
    deadline = time.monotonic() + a.timeout if a.timeout is not None else None
    completed = {}
    while pending:
        tabs, agents = herdr_state()
        for slug, (data, pane) in list(pending.items()):
            agent = herdr('agent', 'get', pane)['agent']
            state = agent['agent_status']
            if state in ('idle', 'done', 'blocked') and ('submit_seq' not in data or agent['state_change_seq'] > data['submit_seq']):
                current_agents = [dict(item, agent_status=state) if item.get('pane_id') == pane else item for item in agents]
                row = task_row(data, tabs, current_agents)
                phase = row['phase']
                if row.get('error'): print(f"仓库错误：{row['error']}", file=sys.stderr)
                completed[slug] = (phase, state)
                del pending[slug]
        if completed and (a.any or not pending):
            for slug in a.slugs:
                if slug in completed:
                    phase, state = completed[slug]
                    print(f'{slug} {phase} {state}')
            return 3 if any(state == 'blocked' for _, state in completed.values()) else 0
        if deadline is not None and time.monotonic() >= deadline:
            print('错误：等待超时', file=sys.stderr); return 4
        time.sleep(min(0.2, max(0, deadline - time.monotonic())) if deadline is not None else 0.2)


def done_checks(a, data, dest):
    checks = []
    expected = {'TASK.md', *(REPOS[key]['dir'] for key in data['repos'])}
    actual = {item.name for item in dest.iterdir()}
    if actual != expected:
        extra = sorted(actual - expected)
        raise RuntimeError('任务目录含有额外文件或缺少仓库，拒绝清理' + (f'：{", ".join(extra[:10])}' + (f' 等 {len(extra)} 个' if len(extra) > 10 else '') if extra else ''))
    for key, info in data['repos'].items():
        repo = REPOS[key]['path']
        path = dest / REPOS[key]['dir']
        try:
            entry = next((w for w in worktrees(repo) if Path(w['worktree']) == path), None)
            if entry and 'locked' in entry:
                raise RuntimeError(f"{key} worktree 已锁定，拒绝清理" + (f"：{entry['locked']}" if entry['locked'] else ''))
            if not a.no_fetch: fetch(repo, info['base'])
            if git(path, 'branch', '--show-current') != info['branch']:
                raise RuntimeError(f'{key} 当前分支与任务记录不符，拒绝清理')
            status = repo_info(repo, path, info['branch'], info['base'], info['base_commit'])
            reasons = []
            if status['dirty'] and not a.discard: reasons.append(f'{key} 有未提交文件，需要 --discard')
            if status['merge'] == 'unmerged' and not a.force:
                unpushed = git(repo, 'rev-list', '--count', info['branch'], '--not', '--remotes=origin')
                reasons.append(f'{key} 未合并（未推送提交 {unpushed} 个），需要 --force；分支会保留')
            checks.append((key, repo, path, info['branch'], status['merge'], '; '.join(reasons)))
        except (RuntimeError, OSError, ValueError) as e:
            checks.append((key, repo, path, info['branch'], None, str(e)))
    return checks


def done(a):
    data = load(a.slug)
    dest = task_dir(a.slug)
    checks = done_checks(a, data, dest)
    if a.dry_run:
        for key, repo, path, branch, state, reason in checks:
            print(f"{key}: " + (f'被拒绝：{reason}' if reason else f'将删除 worktree {path}；' + (f'将保留分支 {branch}' if state == 'unmerged' else f'将删除分支 {branch}')))
        if any(c[-1] for c in checks): return 1
        print('将删除 TASK.md 和任务目录，并关闭存在的 tab')
        return 0
    errors = [c[-1] for c in checks if c[-1]]
    if errors: raise RuntimeError('; '.join(errors))
    tabs, _ = herdr_state()
    tab = find_tab(a.slug, tabs)
    for key, repo, path, branch, status, _ in checks:
        for name in REPOS[key]['shared']:
            if (path / name).is_symlink():
                (path / name).unlink()
        git(repo, 'worktree', 'remove', *(['--force'] if a.discard else []), path)
        print(f'已删除 worktree {path}')
        if status == 'unmerged':
            print(f'保留未合并分支 {branch}')
        else:
            git(repo, 'branch', '-D', branch)
            print(f'已删除分支 {branch}')
        git(repo, 'worktree', 'prune')
    (dest / 'TASK.md').unlink()
    dest.rmdir()
    print('完成', flush=True)
    if tab:
        herdr('tab', 'close', tab['tab_id'])
        print(f"已关闭 tab {tab['tab_id']}")
    return 0


def gc(a):
    repositories = [(key, cfg['path']) for key, cfg in REPOS.items()]
    if (ROOT / '.git').exists() and ROOT not in [p for _, p in repositories]:
        repositories.append(('root', ROOT))
    bases = {}
    for key, repo in repositories:
        try:
            bases[key] = repo_base(key) if key in REPOS else base_ref(repo)
        except RuntimeError:
            bases[key] = None if key in REPOS else SETTINGS['root_base']
    names = {n for cfg in REPOS.values() for n in cfg['shared']}
    links = [Path(item['worktree']) / n for _, repo in repositories for item in worktrees(repo) for n in names]
    links = [p for p in links if p.is_symlink()]
    rows = []
    for key, repo in repositories:
        for item in worktrees(repo)[1:]:
            path = Path(item['worktree'])
            branch = item.get('branch', '').removeprefix('refs/heads/') or 'HEAD'
            deps = [p for p in links if p.parent != path and p.resolve().is_relative_to(path.resolve())]
            prunable = 'prunable' in item
            dirty = None if prunable else len(git(path, 'status', '--porcelain').splitlines())
            status = merge_status(repo, branch, bases[key]) if bases[key] else 'unknown'
            layout = 'task' if path.parent.parent == ROOT / 'worktrees' and path.name in {c['dir'] for c in REPOS.values()} else 'legacy'
            rows.append({'repo': key, 'path': str(path), 'layout': layout, 'branch': branch, 'dirty': dirty, 'merge': status,
                         'dependents': [str(p) for p in deps], 'prunable': prunable})
            if not a.apply or dirty != 0 or status in ('unmerged', 'unknown'):
                continue
            # Repoint links that lean on this worktree at the same entry in the main checkout.
            targets = [(p, repo / p.resolve().relative_to(path.resolve())) for p in deps]
            missing = [str(t) for _, t in targets if not t.exists()]
            if missing:
                print(f'跳过 {path}：主 checkout 缺少 {", ".join(missing)}，无法改指依赖它的软链', file=sys.stderr)
                continue
            for p, target in targets:
                p.unlink()
                p.symlink_to(target, target_is_directory=target.is_dir())
            for n in names:
                if (path / n).is_symlink():
                    (path / n).unlink()
            git(repo, 'worktree', 'remove', path)
            git(repo, 'worktree', 'prune')
    display(rows, a.json)


def main():
    global ROOT, REPOS, SETTINGS
    p = argparse.ArgumentParser(description='herdr-fleet：任务 worktree 与 herdr tab 管理')
    sub = p.add_subparsers(dest='command', required=True)
    n = sub.add_parser('new')
    n.add_argument('slug'); n.add_argument('-r', '--repos', help='逗号分隔的仓库键，默认全部')
    n.add_argument('--type', default='feat'); n.add_argument('--fix', action='store_const', const='fix', dest='type')
    n.add_argument('--base', action='append', metavar='KEY=REF', help='覆盖某个仓库的 base，可重复')
    n.add_argument('--no-fetch', action='store_true')
    n.add_argument('--agent', help='herdr agent 种类，none 表示不启动；默认取配置的 agent')
    n.add_argument('--agent-arg', action='append', metavar='ARG', help='agent 启动参数，可重复；以 - 开头的值须写成 --agent-arg=--flag')
    n.add_argument('--prompt'); n.add_argument('--focus', action='store_true'); n.add_argument('--workspace')
    l = sub.add_parser('ls'); l.add_argument('--json', action='store_true'); l.add_argument('--fetch', action='store_true')
    st = sub.add_parser('status'); st.add_argument('slug'); st.add_argument('--lines', type=int, default=12); st.add_argument('--json', action='store_true')
    w = sub.add_parser('wait'); w.add_argument('slugs', nargs='+'); w.add_argument('--timeout', type=float, metavar='SEC')
    modes = w.add_mutually_exclusive_group(); modes.add_argument('--any', action='store_true'); modes.add_argument('--all', action='store_true')
    pk = sub.add_parser('peek'); pk.add_argument('slug'); pk.add_argument('--lines', type=int, default=60)
    s = sub.add_parser('send'); s.add_argument('slug'); s.add_argument('text')
    o = sub.add_parser('open'); o.add_argument('slug'); o.add_argument('--agent')
    o.add_argument('--agent-arg', action='append', metavar='ARG', help='agent 启动参数，可重复；以 - 开头的值须写成 --agent-arg=--flag')
    d = sub.add_parser('done'); d.add_argument('slug'); d.add_argument('--force', action='store_true'); d.add_argument('--discard', action='store_true'); d.add_argument('--no-fetch', action='store_true'); d.add_argument('--dry-run', action='store_true')
    g = sub.add_parser('gc'); g.add_argument('--apply', action='store_true'); g.add_argument('--json', action='store_true')
    a = p.parse_args()
    try:
        ROOT = find_root()
        REPOS, SETTINGS = load_config(ROOT)
        if a.command == 'new': new(a)
        elif a.command == 'ls': ls(a)
        elif a.command == 'status': status(a)
        elif a.command == 'wait': return wait(a)
        elif a.command == 'done': return done(a)
        elif a.command == 'gc': gc(a)
        elif a.command == 'peek': print(herdr('agent', 'read', task_agent(a.slug), '--source', 'recent-unwrapped', '--lines', a.lines, json_output=False))
        elif a.command == 'send': submit(load(a.slug), task_agent(a.slug), a.text)
        elif a.command == 'open':
            data = load(a.slug)
            tab = find_tab(a.slug, herdr_state()[0])
            if tab: herdr('tab', 'focus', tab['tab_id'])
            else:
                agent = agent_options(a, data)
                if create_tab(data, agent, focus=True):
                    data['agent'] = agent
                    path = task_dir(a.slug) / 'TASK.md'
                    text = path.read_text()
                    before, rest = text.split('```json\n', 1)
                    _, after = rest.split('\n```', 1)
                    path.write_text(before + '```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```' + after)
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(f'错误：{e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
