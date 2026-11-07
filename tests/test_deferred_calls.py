"""Deferred calls must enforce the same gates as direct calls."""
import json
import sqlite3

import pytest

from standing.config import Settings
from standing.ledger import Ledger
from standing.proofs import ProofRunner
from standing.service import Standing


@pytest.fixture
def service(tmp_path):
    settings = Settings.parse({'workspace': str(tmp_path)})
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'ledger.db'))
    return Standing(settings, lambda: ledger, ProofRunner(cwd=lambda: tmp_path, workspace=settings.workspace),
                    actor=lambda: 'worker', task=lambda: 'task-a', session=lambda: '',
                    cwd=lambda: tmp_path)


def call(name, arguments=None):
    return {'name': name, 'arguments': {} if arguments is None else arguments}


def test_deferred_completion_without_proof_blocks(service):
    result = service.before('tool_call', {'calls': [call('kanban_complete')]}, session_id='s1')
    assert result['action'] == 'block'
    assert 'passing check' in result['message']


def test_deferred_completion_proof_is_bound_to_session_and_task(service, tmp_path):
    (tmp_path / 'artifact').write_text('ready')
    proof = service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}, session_id='s1')
    assert json.loads(proof)['passed'] is True
    args = {'calls': [call('kanban_complete', {'task_id': 'task-a'})]}
    assert service.before('tool_call', args, session_id='s1') is None
    assert service.before('tool_call', args, session_id='s2')['action'] == 'block'
    args['calls'][0]['arguments']['task_id'] = 'task-b'
    assert service.before('tool_call', args, session_id='s1')['action'] == 'block'
    service.task_context = lambda: 'task-b'
    assert service.before('tool_call', args, session_id='s1')['action'] == 'block'


@pytest.mark.parametrize('failure', ['ledger', 'identity'])
def test_deferred_completion_errors_fail_closed(service, monkeypatch, failure):
    def broken(*args):
        raise OSError('private-sentinel')
    monkeypatch.setattr(service, 'ledger_factory' if failure == 'ledger' else 'actor_name', broken)
    result = service.before('tool_call', {'calls': [call('kanban_complete')]}, session_id='s1')
    assert result['action'] == 'block'
    assert 'private-sentinel' not in result['message']


@pytest.mark.parametrize('tool', ['read_file', 'tool_describe', 'tool_search'])
def test_deferred_read_does_not_request_approval(service, tool):
    assert service.before('tool_call', {'calls': [call(tool)]}) is None


def test_deferred_scrub_at_l2_requests_approval(service):
    service.ledger().set_level('worker', 2)
    result = service.before('tool_call', {'calls': [call('cronjob', {'action': 'remove'})]})
    assert result['action'] == 'approve'


@pytest.mark.parametrize('names,action', [
    (['read_file', 'send_message'], 'approve'),
    (['send_message', 'read_file'], 'approve'),
    (['kanban_complete', 'send_message', 'read_file'], 'block'),
    (['read_file', 'send_message', 'kanban_complete'], 'block'),
    (['send_message', 'kanban_complete', 'read_file'], 'block'),
])
def test_deferred_mixed_batch_returns_strictest(service, names, action):
    result = service.before('tool_call', {'calls': [call(name) for name in names]}, session_id='s1')
    assert result['action'] == action


@pytest.mark.parametrize('args', [
    None, [], {}, {'calls': None}, {'calls': []}, {'calls': 'not-a-list'},
    {'calls': {}}, {'calls': [None]}, {'calls': [call('read_file'), 42]},
    {'calls': [{'name': 'read_file'}]}, {'calls': [{'arguments': {}}]},
    {'calls': [call('')]}, {'calls': [call(42)]},
    {'calls': [{'name': 'read_file', 'arguments': []}]},
])
def test_malformed_deferred_batch_requests_approval_at_any_level(service, args):
    service.ledger().set_level('worker', 3)
    assert service.before('tool_call', args)['action'] == 'approve'


@pytest.mark.parametrize('args', [
    {'calls': [{'name': 'kanban_complete'}]},
    {'calls': [{'name': 'kanban_complete', 'arguments': None}]},
    {'calls': {'name': 'kanban_complete', 'arguments': {}}},
    {'calls': [call('kanban_complete'), None]},
    {'calls': [[call('kanban_complete')]]},
    {'calls': [call('tool_call', {'calls': [{'name': 'kanban_complete'}]})]},
])
def test_malformed_batch_containing_completion_blocks_even_after_proof(service, tmp_path, args):
    (tmp_path / 'artifact').write_text('ready')
    assert json.loads(service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'},
                                    session_id='s1'))['passed']
    assert service.before('tool_call', args, session_id='s1')['action'] == 'block'


def test_nested_deferred_calls_enforce_completion_without_recursion_limit(service):
    args = {'calls': [call('kanban_complete')]}
    for _ in range(1100):
        args = {'calls': [call('tool_call', args)]}
    assert service.before('tool_call', args, session_id='s1')['action'] == 'block'


def test_all_inner_calls_are_checked_even_after_a_block(service, monkeypatch):
    checked = []
    actor = service.ledger().actor

    def record(name):
        checked.append(name)
        return actor(name)

    monkeypatch.setattr(service.ledger(), 'actor', record)
    args = {'calls': [call('kanban_complete'), call('read_file'), call('send_message')]}
    assert service.before('tool_call', args, session_id='s1')['action'] == 'block'
    assert checked == ['worker', 'worker', 'worker']
