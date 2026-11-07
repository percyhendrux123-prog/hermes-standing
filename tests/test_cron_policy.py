import pytest


ACTIONS = [
    *[(action, 'read') for action in ('list', 'get', 'show', 'status')],
    *[(action, 'spin') for action in ('create', 'add', 'update', 'edit', 'resume', 'enable', 'run', 'trigger')],
    *[(action, 'scrub') for action in ('remove', 'delete', 'clear', 'pause', 'stop', 'disable', 'cancel')],
    *[(action, 'unknown') for action in ('', 'invented', 'remove_all', None)],
]


@pytest.mark.parametrize('tool', ['cronjob', 'cronjob_manage'])
@pytest.mark.parametrize('action,category', ACTIONS)
@pytest.mark.parametrize('level', range(4))
def test_cron_action_by_level_matrix(tool, action, category, level):
    from standing.policy import classify, needs_approval
    categories = classify(tool, {'action': action})
    assert categories == {category}
    # unknown cron actions follow L2's explicit unknown allowance; scrub still gates.
    allowed = {'read'} if level < 2 else {'read', 'spin', 'unknown'}
    assert needs_approval(level, categories) is (level != 3 and category not in allowed)


@pytest.mark.parametrize('tool', ['cronjob', 'cronjob_manage'])
def test_cron_missing_or_malformed_action_is_unknown(tool):
    from standing.policy import classify
    assert classify(tool, {}) == {'unknown'}
    assert classify(tool, {'action': ['create']}) == {'unknown'}
    assert classify(tool, {'action': ' DELETE '}) == {'scrub'}
