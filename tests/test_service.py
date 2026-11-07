import json
import sqlite3
import pytest


@pytest.fixture
def service(tmp_path):
    from standing.config import Settings
    from standing.ledger import Ledger
    from standing.proofs import ProofRunner
    from standing.service import Standing
    settings = Settings.parse({'workspace': str(tmp_path), 'promote_after': 2})
    ledger = Ledger(lambda: sqlite3.connect(tmp_path / 'ledger.db'), promote_after=2)
    return Standing(settings, lambda: ledger, ProofRunner(cwd=lambda: tmp_path, workspace=settings.workspace),
                    actor=lambda: 'worker', task=lambda: 'task-a', session=lambda: '',
                    cwd=lambda: tmp_path)


def test_completion_requires_real_proof_in_same_session_and_task(service, tmp_path):
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    (tmp_path / 'artifact').write_text('ready')
    result = json.loads(service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}, session_id='s1'))
    assert result['passed'] is True
    assert result['proof'].startswith('standing:')
    assert result['level'] == 'L1'
    assert service.before('kanban_complete', {}, session_id='s1') is None
    assert service.before('kanban_complete', {}, session_id='s2')['action'] == 'block'
    assert service.before('kanban_complete', {'task_id': 'task-b'}, session_id='s1')['action'] == 'block'


def test_missing_target_is_unavailable_without_changing_standing(service, tmp_path):
    (tmp_path / 'artifact').write_text('ready')
    service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}, session_id='other')
    before = service.ledger().actor('worker')
    result = json.loads(service.prove({'kind': 'file', 'target': 'absent', 'expect': 'ready'}, session_id='s1'))
    assert result['result'] == 'unavailable'
    assert result['passed'] is None  # Neither a pass nor a false claim.
    assert result['level'] == 'L1' and result['passes'] == 1 and result['fails'] == 0
    assert service.ledger().actor('worker') == before
    assert service.ledger().log('worker')[0]['result'] == 'unavailable'
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    for guidance in ('http', 'allowlisted command', 'file proofs run on the machine Standing runs on'):
        assert guidance in result['message']


def test_failed_proof_demotes_and_never_counts_for_completion(service, tmp_path):
    (tmp_path / 'artifact').write_text('not ready')
    result = json.loads(service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'expected marker'}, session_id='s1'))
    assert result['passed'] is False
    assert result['level'] == 'L0'
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    assert service.before('write_file', {'path': str(tmp_path / 'draft')}, session_id='s1')['action'] == 'approve'
    assert 'L0' in service.command()


def test_hook_error_is_open_except_completion(service, monkeypatch, caplog):
    def broken():
        raise RuntimeError('fake-private-target-must-not-log')
    monkeypatch.setattr(service, 'ledger_factory', broken)
    assert service.before('write_file', {'path': 'a'}) is None
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    assert 'fake-private-target-must-not-log' not in caplog.text
    assert 'Standing' in caplog.text


def test_proof_disabled_and_exemptions_only_affect_completion(service):
    service.settings.require_proof_on_complete = False
    assert service.before('kanban_complete', {}) is None
    service.settings.require_proof_on_complete = True
    service.settings.exempt_profiles = ['worker']
    assert service.before('kanban_complete', {}) is None
    assert service.before('send_message', {})['action'] == 'approve'


def test_owner_command_local_only_and_no_plain_reason_storage(service):
    assert 'local' in service.command('set worker L3 reason', platform='telegram').lower()
    assert 'L1' in service.command()
    assert 'L3' in service.command('set worker L3 private-reason', platform='cli')
    assert 'private-reason' not in str(service.ledger().log('worker'))
    assert service.before('totally_unknown', {}) is None
    assert 'Owner set the level' in service.command('log worker')


def test_sessionless_proof_never_authorizes_a_session(service, tmp_path):
    (tmp_path / 'artifact').write_text('ready')
    assert json.loads(service.prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}))['passed']
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'


def test_db_failure_after_a_pass_still_blocks_completion(service, tmp_path, monkeypatch):
    (tmp_path / 'artifact').write_text('ready')
    args = {'kind': 'file', 'target': 'artifact', 'expect': 'ready'}
    assert json.loads(service.prove(args, session_id='s1'))['passed']
    def broken(name):
        raise OSError('private-path')
    monkeypatch.setattr(service.ledger(), 'actor', broken)
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    assert service.before('write_file', {'path': 'a'}, session_id='s1') is None


def test_proof_in_one_task_does_not_authorize_another(service, tmp_path):
    (tmp_path / 'artifact').write_text('ready')
    args = {'kind': 'file', 'target': 'artifact', 'expect': 'ready'}
    assert json.loads(service.prove(args, session_id='s1'))['passed']
    service.task_context = lambda: 'task-b'
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    service.task_context = lambda: ''
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'


def test_finalize_removes_only_one_sessions_receipts(service, tmp_path):
    (tmp_path / 'artifact').write_text('ready')
    args = {'kind': 'file', 'target': 'artifact', 'expect': 'ready'}
    service.prove(args, session_id='s1')
    service.prove(args, session_id='s2')
    service.finalize(session_id='s1')
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
    assert service.before('kanban_complete', {}, session_id='s2') is None


def test_ledger_and_messages_do_not_retain_target_or_expect(service, tmp_path):
    path = tmp_path / 'private-target-sentinel'
    path.write_text('private-expect-sentinel')
    response = service.prove({'kind': 'file', 'target': str(path), 'expect': 'private-expect-sentinel'}, session_id='s1')
    events = str(service.ledger().log('worker'))
    for value in ('private-target-sentinel', 'private-expect-sentinel'):
        assert value not in response
        assert value not in events


def test_model_cannot_set_levels_and_unknown_command_context_refused(service):
    assert 'local' in service.command('set worker L3').lower()
    assert service.before('standing_set', {})['action'] == 'approve'
    assert 'local' in service.command('set worker L3', platform='cli', source=object()).lower()
