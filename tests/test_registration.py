import importlib.util
from pathlib import Path
import sqlite3
import sys
import types
import json


def load_plugin():
    path = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('standing_plugin_test', path / '__init__.py',
                                                submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Context:
    profile_name = 'worker'
    def __init__(self, tmp_path, settings=None):
        self.tmp_path = tmp_path
        self.settings = settings or {}
        self.tools, self.hooks, self.commands = {}, {}, {}
        self.dispatched = []
    def dispatch_tool(self, name, arguments):
        self.dispatched.append((name, dict(arguments)))
        return json.dumps({'ok': True})
    def get_config(self, key, default=None):
        return self.settings.get(key, default)
    def plugin_db(self):
        return sqlite3.connect(self.tmp_path / 'plugin.db')
    def register_tool(self, **kwargs):
        self.tools[kwargs['name']] = kwargs
    def register_hook(self, name, callback):
        self.hooks[name] = callback
    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


def test_real_registration_flow_with_stubbed_host_api(tmp_path, monkeypatch):
    plugin = load_plugin()
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'task-a')
    monkeypatch.setenv('HERMES_SESSION_ID', 'session-a')
    monkeypatch.setattr(plugin, 'agent_cwd', lambda: tmp_path)
    ctx = Context(tmp_path, {'seed': {'worker': 'L0'}, 'workspace': str(tmp_path)})
    plugin.register(ctx)
    assert set(ctx.tools) == {'standing_proof'}
    assert 'pre_tool_call' in ctx.hooks
    before = ctx.hooks['pre_tool_call']
    assert before('write_file', {'path': 'draft'})['action'] == 'approve'
    assert before('kanban_complete', {})['action'] == 'block'
    (tmp_path / 'proof.txt').write_text('ready')
    result = json.loads(ctx.tools['standing_proof']['handler']({'kind': 'file', 'target': 'proof.txt', 'expect': 'ready'}))
    assert result['passed']
    assert before('kanban_complete', {}) is None
    assert 'worker | L0 | 1 | 0' in ctx.commands['standing']('')
    ctx.hooks['on_session_finalize'](session_id='session-a')
    assert before('kanban_complete', {})['action'] == 'block'


def test_plugin_db_fallback_and_default_actor(tmp_path, monkeypatch):
    plugin = load_plugin()
    storage = types.ModuleType('plugins.plugin_storage')
    names = []
    def connect(name):
        names.append(name)
        return sqlite3.connect(tmp_path / 'fallback.db')
    storage.plugin_db = connect
    monkeypatch.setitem(sys.modules, 'plugins', types.ModuleType('plugins'))
    monkeypatch.setitem(sys.modules, 'plugins.plugin_storage', storage)
    ctx = Context(tmp_path)
    ctx.plugin_db = None
    ctx.profile_name = None
    plugin.register(ctx)
    assert 'default | L1' in ctx.commands['standing']('')
    assert names and set(names) == {'hermes-standing'}


def test_registered_default_proofs_stop_at_l1_until_owner_confirms(tmp_path, monkeypatch):
    plugin = load_plugin()
    monkeypatch.setattr(plugin, 'agent_cwd', lambda: tmp_path)
    ctx = Context(tmp_path, {'seed': {'worker': 'L0'}, 'promote_after': 1, 'workspace': str(tmp_path)})
    plugin.register(ctx)
    (tmp_path / 'artifact').write_text('ready')
    prove = ctx.tools['standing_proof']['handler']
    for _ in range(5):
        result = json.loads(prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}))
        assert result['passed'] is True and result['level'] == 'L1'
    assert ctx.hooks['pre_tool_call']('terminal', {'command': 'pwd'})['action'] == 'approve'
    assert 'now at L2' in ctx.commands['standing']('set worker L2', platform='cli')
    assert json.loads(prove({'kind': 'file', 'target': 'artifact', 'expect': 'ready'}))['level'] == 'L2'


def test_bad_configuration_keeps_completion_fail_closed(tmp_path):
    plugin = load_plugin()
    ctx = Context(tmp_path, {'max_auto_level': 'L3'})
    plugin.register(ctx)
    assert ctx.hooks['pre_tool_call']('write_file', {'path': 'x'}) is None
    assert ctx.hooks['pre_tool_call']('kanban_complete', {})['action'] == 'block'


def test_gateway_relative_paths_use_runtime_cwd_not_process_cwd(tmp_path, monkeypatch):
    plugin = load_plugin()
    workspace = tmp_path / 'session'
    workspace.mkdir()
    runtime = types.ModuleType('agent.runtime_cwd')
    runtime.resolve_agent_cwd = lambda: workspace
    monkeypatch.setitem(sys.modules, 'agent', types.ModuleType('agent'))
    monkeypatch.setitem(sys.modules, 'agent.runtime_cwd', runtime)
    ctx = Context(tmp_path, {'workspace': str(workspace)})
    plugin.register(ctx)
    assert ctx.hooks['pre_tool_call']('write_file', {'path': 'draft'}) is None
    (workspace / 'artifact').write_text('session-file')
    result = json.loads(ctx.tools['standing_proof']['handler']({'kind': 'file', 'target': 'artifact', 'expect': 'session-file'}))
    assert result['passed']
