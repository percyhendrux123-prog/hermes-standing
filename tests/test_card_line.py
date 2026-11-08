"""Card visibility through registered hooks; no Hermes install or live board."""
import json
import sys
import types

import pytest

from tests.test_proofs import http_server
from tests.test_registration import Context, load_plugin


@pytest.fixture
def card(tmp_path, monkeypatch, http_server):
    _, url = http_server
    plugin = load_plugin()
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'task-a')
    monkeypatch.setenv('HERMES_SESSION_ID', 'session-a')
    monkeypatch.setattr(plugin, 'agent_cwd', lambda: tmp_path)
    comments, boards, calls = [], [], []
    ctx = Context(tmp_path, {'seed': {'worker': 'L2'}, 'workspace': str(tmp_path),
                             'http_proof_targets': [url + '/proof?private-query=value']})

    def dispatch(name, arguments):
        calls.append((name, dict(arguments)))
        assert name == 'kanban_comment'
        # Core, not Standing, must choose the author and redact the body.
        assert set(arguments) == {'task_id', 'body', 'board'}
        boards.append(arguments['board'])
        comments.append((arguments['task_id'], arguments['body']))
        return json.dumps({'ok': True})

    ctx.dispatch_tool = dispatch
    plugin.register(ctx)
    return types.SimpleNamespace(ctx=ctx, comments=comments, boards=boards, calls=calls, url=url)


def pass_http(card):
    result = card.ctx.tools['standing_proof']['handler'](
        {'kind': 'http', 'target': card.url + '/proof?private-query=value', 'expect': 'ready'})
    assert json.loads(result)['passed'] is True


def complete(card, args=None, result=None, **kwargs):
    return card.ctx.hooks['post_tool_call'](
        tool_name='kanban_complete', args={} if args is None else args,
        result=json.dumps({'ok': True, 'task_id': 'task-a'}) if result is None else result,
        task_id='host-session-not-a-card', duration_ms=1, **kwargs)


def test_successful_completion_writes_one_score_line_from_receipt_and_ledger(card):
    for _ in range(3):
        pass_http(card)
    assert card.comments == []  # A passing proof is not a completed task.
    assert complete(card) is None
    complete(card)  # Repeated notification must not duplicate the line.
    assert card.comments == [('task-a',
        'Standing: worker L2 · check passed (http 127.0.0.1) · '
        '3 passes this window / 0 fails all time.')]
    assert card.boards == [None]  # Let the host resolve its env/current-board pins.
    assert card.calls == [('kanban_comment', {'task_id': 'task-a', 'body': card.comments[0][1], 'board': None})]
    line = card.comments[0][1]
    for private in ('ready', '/proof', 'private-query', 'value', card.url):
        assert private not in line


def fail_proof(card):
    service = card.ctx.hooks['pre_tool_call'].__self__
    artifact = service.cwd() / 'private-mismatching-artifact'
    artifact.write_text('not the claimed content')
    return json.loads(card.ctx.tools['standing_proof']['handler'](
        {'kind': 'file', 'target': str(artifact), 'expect': 'private-expected-text'}))


def test_failed_proof_posts_one_demotion_line_only_when_level_lowers(card, monkeypatch):
    result = fail_proof(card)
    assert result['passed'] is False and result['level'] == 'L1'
    assert card.comments == [('task-a', 'Standing: worker L2 -> L1 · a check failed.')]
    fail_proof(card)
    assert card.comments[-1][1] == 'Standing: worker L1 -> L0 · a check failed.'
    fail_proof(card)  # Already at L0, so there is no new demotion to announce.
    assert len(card.comments) == 2
    assert complete(card, result='{"ok": false}') is None
    assert len(card.comments) == 2
    monkeypatch.delenv('HERMES_KANBAN_TASK')
    card.ctx.commands['standing']('set worker L2', platform='cli')
    fail_proof(card)
    assert len(card.comments) == 2  # No known task: do not use hook task_id/session as a card.


def test_card_line_can_be_disabled_without_disabling_proof_policy(card):
    service = card.ctx.hooks['pre_tool_call'].__self__
    service.settings.card_line = False
    assert fail_proof(card)['level'] == 'L1'
    assert card.ctx.hooks['pre_tool_call']('kanban_complete', {})['action'] == 'block'
    pass_http(card)
    assert card.ctx.hooks['pre_tool_call']('kanban_complete', {}) is None
    assert complete(card) is None
    assert card.comments == [] and card.boards == []


@pytest.mark.parametrize('value', ['false', 0, 1, None, [], {}])
def test_card_line_is_a_strict_boolean_setting(value):
    from standing.config import Settings
    assert Settings.parse({}).card_line is True
    assert Settings.parse({'card_line': False}).card_line is False
    with pytest.raises(ValueError):
        Settings.parse({'card_line': value})


@pytest.mark.parametrize('result', [
    '{"ok": false}', '{"error": "blocked"}', '{}', 'null', '[]', 'not json',
    '{"ok": "true"}', '{"ok": 1}', '{"ok": true, "task_id": "task-b"}',
])
def test_failed_or_unconfirmed_completion_never_adds_a_line(card, result):
    pass_http(card)
    assert complete(card, result=result) is None
    assert card.comments == [] and card.boards == []


@pytest.mark.parametrize('context', ['no-proof', 'other-session', 'other-task', 'finalized', 'no-task'])
def test_success_line_requires_matching_session_task_receipt(card, monkeypatch, context):
    if context != 'no-proof':
        pass_http(card)
    kwargs = {}
    if context == 'other-session':
        kwargs['session_id'] = 'session-b'
    if context == 'other-task':
        kwargs['args'] = {'task_id': 'task-b'}
    if context == 'finalized':
        card.ctx.hooks['on_session_finalize'](session_id='session-a')
    if context == 'no-task':
        monkeypatch.delenv('HERMES_KANBAN_TASK')
    assert complete(card, **kwargs) is None
    assert card.comments == [] and card.boards == []


@pytest.mark.parametrize('location', ['dispatch', 'ledger'])
def test_card_storage_errors_log_type_and_do_not_change_completion(card, monkeypatch, caplog, location):
    pass_http(card)
    def broken(*args, **kwargs):
        raise OSError('private-error-target-and-expected-text')
    if location == 'dispatch':
        monkeypatch.setattr(card.ctx, 'dispatch_tool', broken)
    else:
        service = card.ctx.hooks['post_tool_call'].__self__
        monkeypatch.setattr(service.ledger(), 'actor', broken)
    result = {'ok': True, 'task_id': 'task-a'}
    assert complete(card, result=result) is None
    assert result == {'ok': True, 'task_id': 'task-a'}
    assert 'card line (OSError)' in caplog.text
    assert 'private-error' not in caplog.text


def test_demotion_comment_error_does_not_lose_recorded_proof(card, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OSError('private-storage-error')
    monkeypatch.setattr(card.ctx, 'dispatch_tool', broken)
    result = fail_proof(card)
    assert result['passed'] is False and result['level'] == 'L1'
    assert 'error' not in result
    assert 'card line (OSError)' in caplog.text
    assert 'private-storage-error' not in caplog.text


def test_ambiguous_committed_write_error_is_not_retried(card, monkeypatch):
    pass_http(card)
    dispatch = card.ctx.dispatch_tool
    def committed_then_error(*args, **kwargs):
        dispatch(*args, **kwargs)
        raise OSError('error-after-commit')
    monkeypatch.setattr(card.ctx, 'dispatch_tool', committed_then_error)
    complete(card)
    complete(card)
    assert len(card.comments) == 1


def test_explicit_board_forwarded_unchanged_to_public_dispatch(card):
    pass_http(card)
    complete(card, args={'board': 'owner-selected-board'})
    assert card.boards == ['owner-selected-board']
    assert card.calls[0][1] == {'task_id': 'task-a', 'body': card.comments[0][1],
                              'board': 'owner-selected-board'}


def test_public_tool_refusal_is_best_effort_without_retry_or_fallback(card, monkeypatch):
    pass_http(card)
    service = card.ctx.hooks['post_tool_call'].__self__
    before = service.ledger().actor('worker')
    attempts = []
    def refused(name, arguments):
        attempts.append((name, arguments))
        return json.dumps({'error': 'approval required'})
    monkeypatch.setattr(card.ctx, 'dispatch_tool', refused)
    result = {'ok': True, 'task_id': 'task-a'}
    assert complete(card, result=result) is None
    complete(card, result=result)
    assert len(attempts) == 1 and attempts[0][0] == 'kanban_comment'
    assert result == {'ok': True, 'task_id': 'task-a'}
    assert service.ledger().actor('worker') == before
    assert service.before('kanban_complete', {}) is None
    assert card.comments == []
    assert fail_proof(card)['level'] == 'L1'  # Refusal cannot lose a demotion either.
    assert len(attempts) == 2 and attempts[1][1]['board'] is None


def test_dispatch_hook_reentry_does_not_duplicate_completion_line(card, monkeypatch):
    pass_http(card)
    dispatch = card.ctx.dispatch_tool
    def reentrant(name, arguments):
        card.ctx.hooks['post_tool_call'](name, arguments, '{"ok": true}')
        complete(card)
        return dispatch(name, arguments)
    monkeypatch.setattr(card.ctx, 'dispatch_tool', reentrant)
    complete(card)
    assert len(card.calls) == 1


def test_success_uses_last_passing_receipt_not_a_later_failed_target(card):
    pass_http(card)
    fail_proof(card)
    complete(card)
    assert card.comments[-1][1] == ('Standing: worker L1 · check passed (http 127.0.0.1) · '
                                  '0 passes this window / 1 fails all time.')
    assert 'private' not in str(card.comments)


@pytest.mark.parametrize('kind', ['file', 'command'])
def test_non_http_receipt_never_discloses_target_or_expected_text(card, tmp_path, kind):
    path = tmp_path / 'private-artifact-name'
    path.write_text('private-expected-text')
    target = str(path)
    if kind == 'command':
        script = tmp_path / 'private-command.py'
        script.write_text('print("private-expected-text")\n')
        target = [sys.executable, str(script)]
        service = card.ctx.hooks['pre_tool_call'].__self__
        service.runner.commands = [target]
    response = card.ctx.tools['standing_proof']['handler'](
        {'kind': kind, 'target': target, 'expect': 'private-expected-text'})
    assert json.loads(response)['passed']
    complete(card)
    assert f'check passed ({kind})' in card.comments[0][1]
    for private in ('private-artifact', 'private-expected', str(tmp_path), sys.executable):
        assert private not in str(card.comments)
