import pytest
from tests.test_service import service  # noqa: F401  (fixture: actor worker, task task-a, default L1)


@pytest.mark.parametrize('tool', ['kanban_block', 'kanban_comment', 'kanban_request_review', 'kanban_heartbeat'])
def test_worker_can_report_on_its_own_card_at_any_level(service, tool):
    for level in (0, 1, 2):
        service.ledger().set_level('worker', level)
        assert service.before(tool, {'task_id': 'task-a', 'body': 'x'}, session_id='s1') is None
        assert service.before(tool, {'body': 'x'}, session_id='s1') is None  # omitted task_id = own card


@pytest.mark.parametrize('tool', ['kanban_block', 'kanban_comment', 'kanban_request_review'])
def test_another_cards_lifecycle_still_needs_approval(service, tool):
    result = service.before(tool, {'task_id': 'task-b', 'body': 'x'}, session_id='s1')
    assert result is not None and result['action'] == 'approve'


def test_no_assigned_task_falls_through_to_level_gate(service):
    service.task_context = lambda: ''
    result = service.before('kanban_comment', {'task_id': 'task-a', 'body': 'x'}, session_id='s1')
    assert result is not None and result['action'] == 'approve'


def test_completion_is_still_proof_gated(service):
    assert service.before('kanban_complete', {}, session_id='s1')['action'] == 'block'
