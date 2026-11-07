"""only a completed, mismatching check is a false claim."""
import hashlib
import json
import sys
from urllib.error import URLError

import pytest

from standing.proofs import ProofRunner
from tests.test_proofs import http_server
from tests.test_card_line import card, complete
from tests.test_service import service


@pytest.mark.parametrize('path', ['/truncated-match', '/truncated-mismatch',
                                  '/truncated-combined-match', '/truncated-combined-mismatch',
                                  '/truncated-duplicate-match', '/truncated-duplicate-mismatch'])
def test_truncated_http_is_unavailable_not_a_pass_or_false_claim(service, http_server, path):
    _, url = http_server
    runner = ProofRunner(service.cwd, http_proof_targets=[url + '/'])
    outcome = runner.run('http', url + path, 'ready')
    assert outcome.result == 'unavailable' and outcome.passed is None
    service.runner = runner
    before = service.ledger().actor('worker')
    result = json.loads(service.prove({'kind': 'http', 'target': url + path, 'expect': 'ready'}, session_id='s1'))
    assert result['result'] == 'unavailable' and result['passed'] is None
    assert service.ledger().actor('worker') == before
    assert service.ledger().log('worker')[0]['result'] == 'unavailable'
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'


def test_valid_http_content_length_keeps_completed_pass_and_fail(http_server, tmp_path):
    _, url = http_server
    runner = ProofRunner(lambda: tmp_path, http_proof_targets=[url + '/'])
    assert runner.run('http', url + '/framed-match', 'ready').result == 'pass'
    assert runner.run('http', url + '/framed-mismatch', 'ready').result == 'fail'


@pytest.mark.parametrize('kind', [[], {}, 2, None, True])
def test_malformed_kind_is_logged_as_unavailable(service, kind):
    result = json.loads(service.prove({'kind': kind, 'target': 'private-target', 'expect': 'private-expected'}, session_id='s1'))
    assert result['result'] == 'unavailable' and result['passed'] is None
    event = service.ledger().log('worker')[0]
    assert event['kind'] == 'invalid' and event['result'] == 'unavailable'
    assert 'private-' not in str(event) and 'private-' not in result['message']
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'


@pytest.mark.parametrize('kind', ['file', 'http', 'command'])
def test_completed_checks_have_distinct_pass_and_fail_results(tmp_path, http_server, kind):
    _, url = http_server
    artifact = tmp_path / 'artifact'
    artifact.write_text('ready')
    command = [sys.executable, '-V']
    runner = ProofRunner(lambda: tmp_path, workspace=str(tmp_path),
                         http_proof_targets=[url + '/'], command_allowlist=[command])
    target, marker = {'file': (str(artifact), 'ready'), 'http': (url + '/ready', 'ready'),
                      'command': (command, 'Python')}[kind]
    passed = runner.run(kind, target, marker)
    failed = runner.run(kind, target, 'absent-marker')
    assert passed.result == 'pass' and passed.passed is True
    assert failed.result == 'fail' and failed.passed is False
    if kind == 'file':
        mismatch = runner.run('file', target, hashlib.sha256(b'other').hexdigest())
        assert mismatch.result == 'fail' and mismatch.passed is False


@pytest.mark.parametrize('case', ['missing-file', 'refused-path', 'credential-path', 'no-file-scope',
                                 'http-scope', 'no-http-scope', 'missing-http', 'redirect',
                                 'unlisted-command', 'missing-command', 'command-timeout',
                                 'invalid-command', 'empty-expect', 'invalid-kind'])
def test_unavailable_targets_are_not_false_claims(tmp_path, http_server, case):
    server, url = http_server
    missing_command = [str(tmp_path / 'nonexistent-executable')]
    script = tmp_path / 'slow.py'
    script.write_text("import time\nprint('ready', flush=True)\ntime.sleep(5)\n")
    slow = [sys.executable, str(script)]
    workspace = tmp_path / 'artifacts'
    workspace.mkdir()
    (workspace / '.env').write_text('synthetic-only')
    runner = ProofRunner(lambda: workspace, workspace=str(workspace), timeout=0.1,
                         http_proof_targets=[url + '/ready', url + '/missing', url + '/redirect'],
                         command_allowlist=[missing_command, slow])
    kind, target, expect = {
        'missing-file': ('file', 'absent-private-path', 'ready'),
        'refused-path': ('file', str(tmp_path / 'outside-private-path'), 'ready'),
        'credential-path': ('file', '.env', 'synthetic-only'),
        'no-file-scope': ('file', 'artifact', 'ready'),
        'http-scope': ('http', url + '/unlisted', 'ready'),
        'no-http-scope': ('http', url + '/ready', 'ready'),
        'missing-http': ('http', url + '/missing', 'ready'),
        'redirect': ('http', url + '/redirect', 'ready'),
        'unlisted-command': ('command', ['/usr/bin/env'], 'ready'),
        'missing-command': ('command', missing_command, 'ready'),
        'command-timeout': ('command', slow, 'ready'),
        'invalid-command': ('command', 'echo ready | cat', 'ready'),
        'empty-expect': ('file', 'artifact', ''),
        'invalid-kind': ('invented', 'target', 'ready'),
    }[case]
    if case == 'no-file-scope':
        runner.file_roots = []
    if case == 'no-http-scope':
        runner.http_targets = []
    outcome = runner.run(kind, target, expect)
    assert outcome.result == 'unavailable'
    assert outcome.passed is None
    assert 'No pass or demotion' in outcome.reason
    assert 'file proofs run on the machine Standing runs on' in outcome.reason
    assert 'private-path' not in outcome.reason
    if case in {'http-scope', 'no-http-scope'}:
        assert server.hits == []


def test_unavailable_results_leave_card_and_record_unchanged(card, tmp_path, monkeypatch):
    service = card.ctx.hooks['pre_tool_call'].__self__
    prove = card.ctx.tools['standing_proof']['handler']
    # Preserve an earned window but do not provide this session a completion receipt.
    service.ledger().proof('worker', 'other-task', 'file', 'hash', True, 'The check passed.')
    before = service.ledger().actor('worker')
    for args in [
        {'kind': 'file', 'target': 'absent-private-path', 'expect': 'private-expected'},
        {'kind': 'file', 'target': str(tmp_path.parent / 'outside-private-path'), 'expect': 'private-expected'},
        {'kind': 'http', 'target': card.url + '/unlisted', 'expect': 'private-expected'},
        {'kind': 'command', 'target': ['/usr/bin/env'], 'expect': 'private-expected'},
    ]:
        result = json.loads(prove(args))
        assert result['result'] == 'unavailable' and result['passed'] is None
        assert service.ledger().actor('worker') == before
        assert service.ledger().log('worker')[0]['result'] == 'unavailable'
        assert service.before('kanban_complete', {})['action'] == 'block'
        complete(card)  # No success comment even for a reported successful completion.
        assert card.comments == [] and card.boards == []
        assert 'private-' not in result['message']
        assert 'private-' not in str(service.ledger().log('worker'))
    for error in [PermissionError('private-error'), URLError('private-error'), TimeoutError('private-error')]:
        def broken(*args, **kwargs):
            raise error
        with monkeypatch.context() as patch:
            patch.setattr(service.runner, 'http', broken)
            response = json.loads(prove({'kind': 'http', 'target': card.url, 'expect': 'private-expected'}))
        assert response['result'] == 'unavailable' and response['passed'] is None
        assert service.ledger().actor('worker') == before
        assert card.comments == [] and card.boards == []
        assert 'private-' not in response['message']
    assert 'http | unavailable' in service.command('log worker')
