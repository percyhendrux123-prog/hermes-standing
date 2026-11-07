from pathlib import Path
import re
from standing.config import Settings
from standing.policy import DEFAULT_LEVELS
from tests.test_registration import Context, load_plugin


def test_manifest_declares_all_settings_and_actual_hooks(tmp_path):
    text = (Path(__file__).resolve().parents[1] / 'plugin.yaml').read_text()
    assert 'name: hermes-standing\n' in text
    assert 'version: "0.1.0"' in text
    assert 'requires_hermes: ">=0.22.5"' in text
    assert 'api_version: 1' in text
    assert 'provides_tools: [standing_proof]' in text
    settings = text.split('config_schema:\n', 1)[1]
    assert set(re.findall(r'^  ([a-z_]+):$', settings, re.M)) == set(Settings.__dataclass_fields__)
    ctx = Context(tmp_path)
    load_plugin().register(ctx)
    declared = re.search(r'provides_hooks: \[([^\]]+)\]', text)[1]
    assert {name.strip() for name in declared.split(',')} == set(ctx.hooks)
    assert ctx.tools['standing_proof']['schema']['parameters']['additionalProperties'] is False
    assert set(ctx.tools['standing_proof']['schema']['parameters']['required']) == {'kind', 'target', 'expect'}


def test_manifest_and_readme_l2_defaults_match_runtime():
    root = Path(__file__).resolve().parents[1]
    expected = f"L2: [{', '.join(DEFAULT_LEVELS['L2'])}]"
    assert expected in (root / 'plugin.yaml').read_text()
    assert expected in (root / 'README.md').read_text()
