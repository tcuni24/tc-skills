"""Tests for skills/herdr-fleet/extensions/ask-user.ts.

The scenarios run through tests/herdr-fleet/ask_user_driver.mjs, which loads
the extension with pi's own extension loader (jiti + module aliases) and
captures real EventBus emissions plus stub dialog calls. These tests require
a managed pi install (PI_CODING_AGENT_DIR or ~/.pi/agent) and node; they skip
cleanly when either is absent.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SKILL = Path(__file__).resolve().parents[2] / 'skills' / 'herdr-fleet'
EXTENSION = SKILL / 'extensions' / 'ask-user.ts'
DRIVER = Path(__file__).resolve().parent / 'ask_user_driver.mjs'


def _agent_dir():
    return Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent')


def _pkg_dir():
    try:
        version = (_agent_dir() / 'install/current-version').read_text().strip()
    except OSError:
        return None
    pkg = _agent_dir() / 'install/releases' / version / 'node_modules/@earendil-works/pi-coding-agent'
    return pkg if pkg.is_dir() else None


@pytest.fixture(scope='module')
def driver():
    if shutil.which('node') is None:
        pytest.skip('node is required to exercise the pi extension')
    if _pkg_dir() is None:
        pytest.skip('managed pi install not found (PI_CODING_AGENT_DIR or ~/.pi/agent)')

    def run(scenario):
        proc = subprocess.run(
            ['node', str(DRIVER), scenario, '--ext', str(EXTENSION)],
            capture_output=True, text=True, timeout=30, check=True)
        return json.loads(proc.stdout)
    return run


def blocked_events(report):
    return [entry['data'] for entry in report['timeline'] if entry['type'] == 'blocked-event']


def dialog_entries(report):
    return [entry for entry in report['timeline'] if entry['type'].endswith('.open')]


def test_extension_loads_and_registers(driver):
    report = driver('select-answer')
    assert report['loadErrors'] == []
    assert report['registered']['tools'] == ['ask_user']
    assert report['registered']['commands'] == ['ask-user']


def test_select_answer_blocks_then_recovers(driver):
    report = driver('select-answer')
    assert blocked_events(report) == [
        {'active': True, 'label': 'Pick one'},
        {'active': False},
    ]
    (dialog,) = dialog_entries(report)
    assert dialog['type'] == 'select.open' and dialog['options'] == ['A', 'B']
    assert dialog['opts']['hasSignal'] is False
    result = report['results'][0]
    assert result['content'][0]['text'] == 'User selected: B'
    assert result['details']['status'] == 'answered'
    assert result['structuredContent'] == {'status': 'answered', 'answer': 'B'}
    assert not result['isError']


def test_input_answer_trims_and_blocks(driver):
    report = driver('input-answer')
    assert [e['active'] for e in blocked_events(report)] == [True, False]
    (dialog,) = dialog_entries(report)
    assert dialog['type'] == 'input.open'
    assert report['results'][0]['structuredContent'] == {'status': 'answered', 'answer': 'bob'}


def test_placeholder_forwarded_to_input(driver):
    report = driver('input-placeholder')
    (dialog,) = dialog_entries(report)
    assert dialog['type'] == 'input.open' and dialog['placeholder'] == 'your name'


@pytest.mark.parametrize('scenario', ['select-cancel', 'input-cancel', 'input-blank'])
def test_cancel_paths_release_blocked(driver, scenario):
    report = driver(scenario)
    assert [e['active'] for e in blocked_events(report)] == [True, False]
    result = report['results'][0]
    assert result['details']['status'] == 'cancelled'
    assert result['structuredContent'] == {'status': 'cancelled', 'answer': None}
    assert 'cancelled' in result['content'][0]['text'].lower()
    assert not result['isError']


def test_no_ui_never_emits_blocked(driver):
    report = driver('no-ui')
    assert blocked_events(report) == []
    assert dialog_entries(report) == []
    result = report['results'][0]
    assert result['details']['status'] == 'unavailable'
    assert result['isError'] and 'mode: print' in result['content'][0]['text']


def test_empty_options_is_invalid_and_emits_nothing(driver):
    report = driver('empty-options')
    assert blocked_events(report) == []
    assert dialog_entries(report) == []
    result = report['results'][0]
    assert result['details']['status'] == 'invalid' and result['isError']


def test_dialog_error_recovers_blocked(driver):
    report = driver('dialog-throw')
    assert [e['active'] for e in blocked_events(report)] == [True, False]
    result = report['results'][0]
    assert result['details']['status'] == 'error' and result['isError']
    assert 'boom' in result['content'][0]['text']


def test_aborted_before_wait_emits_nothing(driver):
    report = driver('pre-aborted')
    assert blocked_events(report) == []
    assert dialog_entries(report) == []
    assert report['results'][0]['details']['status'] == 'cancelled'


def test_abort_during_wait_recovers_blocked(driver):
    report = driver('abort-during-wait')
    assert [e['active'] for e in blocked_events(report)] == [True, False]
    (dialog,) = dialog_entries(report)
    assert dialog['opts']['hasSignal'] is True
    assert report['results'][0]['details']['status'] == 'cancelled'


def test_timeout_forwarded_to_dialog(driver):
    report = driver('timeout-forward')
    (dialog,) = dialog_entries(report)
    assert dialog['opts']['timeout'] == 1500


def test_blocked_wraps_only_the_wait(driver):
    report = driver('select-answer')
    kinds = [entry['type'] for entry in report['timeline']]
    first_blocked = kinds.index('blocked-event')
    last_blocked = len(kinds) - 1 - kinds[::-1].index('blocked-event')
    assert kinds.index('select.open') > first_blocked
    assert kinds.index('select.close') < last_blocked


def test_concurrent_calls_stay_balanced(driver):
    report = driver('concurrent')
    actives = [e['active'] for e in blocked_events(report)]
    assert actives.count(True) == 2 and actives.count(False) == 2
    # Each wait releases its own blocker; a counter nets to zero, so no other
    # owner is unblocked early.
    assert sum(1 if a else -1 for a in actives) == 0
    assert actives[:2] == [True, True] and actives[-2:] == [False, False]
    assert [r['details']['answer'] for r in report['results']] == ['A', 'B']


def test_command_select_exercises_same_wait_path(driver):
    report = driver('command-select')
    assert blocked_events(report) == [{'active': True, 'label': 'Pick one'}, {'active': False}]
    (dialog,) = dialog_entries(report)
    assert dialog['type'] == 'select.open' and dialog['options'] == ['A', 'B']
    notify = [entry for entry in report['timeline'] if entry['type'] == 'notify']
    assert notify[-1]['message'] == 'User selected: A'


def test_command_input_and_usage(driver):
    report = driver('command-input')
    assert [e['active'] for e in blocked_events(report)] == [True, False]
    assert any(entry['type'] == 'input.open' for entry in dialog_entries(report))
    report = driver('command-usage')
    assert blocked_events(report) == [] and dialog_entries(report) == []
    notify = [entry for entry in report['timeline'] if entry['type'] == 'notify']
    assert notify[-1]['notifyType'] == 'error' and 'Usage' in notify[-1]['message']
