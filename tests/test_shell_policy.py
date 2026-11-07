"""L2 must let proven workers use shells without silently permitting scrub/spend."""
import pytest

from standing.policy import classify, needs_approval
from tests.test_deferred_calls import service


@pytest.mark.parametrize('tool,args', [
    ('terminal', {'command': 'printf ready | sort'}),
    ('terminal', {'command': 'bash -c "printf ready"'}),
    ('terminal', {'command': 'python3 -c "print(1)"'}),
    ('terminal', {'command': 'pwd'}),
    ('execute_code', {'code': 'from hermes_tools import read_file\nprint(read_file("draft.txt"))'}),
])
def test_unmatched_terminal_and_code_are_shell_allowed_at_l2(tool, args):
    categories = classify(tool, args)
    assert categories == {'shell'}
    assert needs_approval(1, categories)
    assert not needs_approval(2, categories)


@pytest.mark.parametrize('tool,key', [('terminal', 'command'), ('execute_code', 'code')])
@pytest.mark.parametrize('text,categories', [
    ('printf ready; rm draft', {'scrub'}),
    ('bash -c "rm draft" | sort', {'scrub'}),
    ('git push origin main | sort', {'send'}),
    ('netlify deploy --prod && printf ready', {'spin'}),
    ('git push; rm draft; netlify deploy', {'send', 'scrub', 'spin'}),
])
def test_composition_preserves_all_recognized_risk_categories(tool, key, text, categories):
    assert classify(tool, {key: text}) == categories
    assert needs_approval(2, categories) is ('scrub' in categories)


@pytest.mark.parametrize('tool,args,gate', [
    ('terminal', {'command': 'printf ready | sort'}, False),
    ('terminal', {'command': 'python3 -c "print(1)"'}, False),
    ('execute_code', {'code': 'print(1)'}, False),
    ('unrecognized_plugin', {}, False),
    ('terminal', {'command': 'rm draft | sort'}, True),
    ('cronjob', {'action': 'remove'}, True),
    ('connectors__stripe__create_payment', {}, True),
])
@pytest.mark.parametrize('deferred', [False, True])
def test_l2_real_service_gates_direct_and_deferred_actions(service, tool, args, gate, deferred):
    service.ledger().set_level('worker', 2)
    if deferred:
        tool, args = 'tool_call', {'calls': [{'name': tool, 'arguments': args}]}
    result = service.before(tool, args, session_id='s1')
    assert (result is not None) is gate
    if gate:
        assert result['action'] == 'approve'


def test_l1_shell_still_requests_approval_from_real_service(service):
    assert service.before('terminal', {'command': 'printf ready | sort'})['action'] == 'approve'
