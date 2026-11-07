import pytest


def test_config_defaults_and_customization(tmp_path):
    from standing.config import Settings
    settings = Settings.parse({})
    assert settings.default_level == 1
    assert settings.promote_after == 10
    assert settings.max_auto_level == 2
    assert settings.require_proof_on_complete is True
    settings = Settings.parse({'seed': {'worker': 'L0'}, 'workspace': str(tmp_path),
                               'levels': {'L1': ['read']}, 'exempt_profiles': ['owner']})
    assert settings.seed == {'worker': 0}
    assert settings.levels['L1'] == ['read']
    assert settings.exempt_profiles == ['owner']


def test_shell_category_can_be_configured():
    from standing.config import Settings
    settings = Settings.parse({'levels': {'L1': ['read', 'shell']}})
    assert settings.levels['L1'] == ['read', 'shell']
    assert settings.levels['L2'] == ['read', 'local_write', 'shell', 'send', 'spin', 'unknown']


@pytest.mark.parametrize('value', [
    {'promote_after': 0}, {'promote_after': True}, {'default_level': 'L4'},
    {'max_auto_level': 'L3'}, {'seed': {'worker': 'L3'}}, {'workspace': 'relative'},
    {'require_proof_on_complete': 'false'}, {'exempt_profiles': 'worker'},
    {'levels': {'L0': ['nonsense']}}, {'typo': True},
    {'command_allowlist': ['git rev-parse HEAD; bad']},
])
def test_invalid_config_is_rejected(value):
    from standing.config import Settings
    with pytest.raises(ValueError):
        Settings.parse(value)
