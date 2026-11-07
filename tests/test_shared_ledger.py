"""Two synthetic HERMES_HOME values exercise registration, not a native Hermes host."""
import json
import sqlite3
from contextlib import closing

import pytest

from tests.test_registration import Context, load_plugin


class HomeContext(Context):
    def __init__(self, home, name, settings):
        super().__init__(home, settings)
        self.profile_name = name
        self.local_opens = 0

    def plugin_db(self):
        self.local_opens += 1
        return super().plugin_db()


def test_two_homes_share_worker_earn_loss_visibility_and_owner_control(tmp_path, monkeypatch):
    plugin = load_plugin()
    workspace = tmp_path / 'artifacts'
    workspace.mkdir()
    (workspace / 'proof').write_text('ready')
    worker_home = tmp_path / 'worker-home'
    governor_home = tmp_path / 'governor-home'
    worker_home.mkdir()
    governor_home.mkdir()
    shared = tmp_path / 'owner-records' / 'standing.db'
    settings = {'workspace': str(workspace), 'db_path': str(shared), 'promote_after': 1}
    monkeypatch.setattr(plugin, 'agent_cwd', lambda: workspace)
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    worker = HomeContext(worker_home, 'worker', settings)
    plugin.register(worker)
    prove = worker.tools['standing_proof']['handler']
    assert json.loads(prove({'kind': 'file', 'target': 'proof', 'expect': 'ready'}))['level'] == 'L2'
    assert json.loads(prove({'kind': 'file', 'target': 'proof', 'expect': 'absent-marker'}))['level'] == 'L1'
    monkeypatch.setenv('HERMES_HOME', str(governor_home))
    governor = HomeContext(governor_home, 'governor', settings)
    plugin.register(governor)
    command = governor.commands['standing']
    assert 'worker | L1 | 0 | 1' in command('')
    assert 'file | fail' in command('log worker')
    assert 'worker is now at L0' in command('set worker L0 reviewed', platform='cli')
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    assert worker.hooks['pre_tool_call']('write_file', {'path': 'draft'})['action'] == 'approve'
    monkeypatch.setenv('HERMES_HOME', str(governor_home))
    assert 'worker is now at L2' in command('set worker L2 reviewed', platform='cli')
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    assert worker.hooks['pre_tool_call']('write_file', {'path': 'draft'}) is None
    assert worker.local_opens == governor.local_opens == 0
    with closing(sqlite3.connect(shared)) as db:
        assert db.execute('SELECT level,fails_total FROM actors WHERE name=?', ('worker',)).fetchone() == (2, 1)
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')  # A transaction context alone does not close SQLite.


def test_shared_seed_and_default_never_lower_existing_levels(tmp_path, monkeypatch):
    plugin = load_plugin()
    home = tmp_path / 'home'
    home.mkdir()
    shared = tmp_path / 'standing.db'
    monkeypatch.setenv('HERMES_HOME', str(home))
    owner = HomeContext(home, 'governor', {'db_path': str(shared), 'seed': {'worker': 'L2'}})
    plugin.register(owner)
    owner.commands['standing']('set worker L3 owner-approved', platform='cli')
    worker = HomeContext(home, 'worker', {'db_path': str(shared), 'seed': {'worker': 'L0'}, 'default_level': 'L0'})
    plugin.register(worker)
    assert 'worker | L3' in worker.commands['standing']('')
    assert worker.hooks['pre_tool_call']('unknown_tool', {}) is None


def test_restarts_across_homes_preserve_demotion_and_owner_changes(tmp_path, monkeypatch):
    plugin = load_plugin()
    workspace = tmp_path / 'artifacts'
    workspace.mkdir()
    (workspace / 'proof').write_text('ready')
    shared = tmp_path / 'standing.db'
    worker_home, governor_home = tmp_path / 'worker', tmp_path / 'governor'
    worker_home.mkdir()
    governor_home.mkdir()
    settings = {'workspace': str(workspace), 'db_path': str(shared),
                'seed': {'worker': 'L2'}, 'promote_after': 3}
    monkeypatch.setattr(plugin, 'agent_cwd', lambda: workspace)
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    worker = HomeContext(worker_home, 'worker', settings)
    plugin.register(worker)
    prove = worker.tools['standing_proof']['handler']
    assert json.loads(prove({'kind': 'file', 'target': 'proof', 'expect': 'absent-marker'}))['level'] == 'L1'
    assert json.loads(prove({'kind': 'file', 'target': 'proof', 'expect': 'ready'}))['passes'] == 1
    ledger = worker.hooks['pre_tool_call'].__self__.ledger()
    before, events = ledger.actor('worker'), ledger.log('worker')
    monkeypatch.setenv('HERMES_HOME', str(governor_home))
    governor = HomeContext(governor_home, 'governor', settings)
    plugin.register(governor)  # The governor loading must not repair a worker demotion either.
    command = governor.commands['standing']
    assert 'worker | L1 | 1 | 1' in command('')
    assert ledger.actor('worker') == before and ledger.log('worker') == events
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    reloaded = HomeContext(worker_home, 'worker', {**settings, 'default_level': 'L0'})
    plugin.register(reloaded)
    assert ledger.actor('worker') == before and ledger.log('worker') == events
    assert reloaded.hooks['pre_tool_call']('unknown_tool', {})['action'] == 'approve'
    monkeypatch.setenv('HERMES_HOME', str(governor_home))
    assert 'now at L0' in command('set worker L0 owner-changed', platform='cli')
    monkeypatch.setenv('HERMES_HOME', str(worker_home))
    plugin.register(reloaded)
    assert 'worker | L0 | 0 | 1' in reloaded.commands['standing']('')
    assert ledger.log('worker')[0]['kind'] == 'owner'


def test_default_storage_is_still_home_local(tmp_path, monkeypatch):
    plugin = load_plugin()
    homes = [tmp_path / 'a', tmp_path / 'b']
    contexts = []
    for name, home in zip(('worker', 'governor'), homes):
        home.mkdir()
        monkeypatch.setenv('HERMES_HOME', str(home))
        ctx = HomeContext(home, name, {})
        plugin.register(ctx)
        contexts.append(ctx)
    assert 'worker |' not in contexts[1].commands['standing']('')
    assert all(ctx.local_opens > 0 for ctx in contexts)


@pytest.mark.parametrize('value', ['relative.db', None, [], 1])
def test_invalid_shared_db_path_rejected(value):
    from standing.config import Settings
    with pytest.raises(ValueError):
        Settings.parse({'db_path': value})
