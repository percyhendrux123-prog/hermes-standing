import pytest


# L2 permits shell/unknown work so proven unattended workers are not denied by default.
@pytest.mark.parametrize('level,category,local,gate', [
    (0, 'read', False, False), (0, 'write', True, True),
    (1, 'write', True, False), (1, 'write', False, True),
    (1, 'send', False, True), (1, 'spin', False, True),
    (2, 'send', False, False), (2, 'spin', False, False),
    (2, 'spend', False, True), (2, 'scrub', True, True),
    (2, 'unknown', False, False), (3, 'unknown', False, False),
    (0, 'shell', False, True), (1, 'shell', False, True), (2, 'shell', False, False),
])
def test_level_gates(level, category, local, gate):
    from standing.policy import needs_approval
    assert needs_approval(level, {category}, local=local) is gate


@pytest.mark.parametrize('tool,args,category', [
    ('read_file', {}, 'read'), ('search_files', {}, 'read'),
    ('write_file', {'path': 'draft.txt'}, 'write'),
    ('send_message', {}, 'send'), ('connectors__gmail__send_email', {}, 'send'),
    ('mcp__stripe__create_payment', {}, 'spend'),
    ('cronjob_manage', {'action': 'create'}, 'spin'),
    ('delegate_task', {'tasks': [{}]}, 'spin'), ('kanban_create', {}, 'spin'),
    ('terminal', {'command': 'git push origin main'}, 'send'),
    ('terminal', {'command': 'netlify deploy --prod'}, 'spin'),
    ('terminal', {'command': 'rm -rf anything'}, 'scrub'),
    ('connectors__drive__delete_file', {}, 'scrub'),
    ('unknown_plugin', {}, 'unknown'),
])
def test_classifies_categories(tool, args, category):
    from standing.policy import classify
    assert category in classify(tool, args)


def test_workspace_paths_and_symlinks(tmp_path):
    from standing.policy import local_write
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'link').symlink_to(outside, target_is_directory=True)
    assert local_write({'path': 'draft.txt'}, root, root)
    assert not local_write({'path': '../outside/draft.txt'}, root, root)
    assert not local_write({'path': 'link/draft.txt'}, root, root)
    assert not local_write({'path': str(root) + '-other/file'}, root, root)
    assert not local_write({'path': 'draft.txt'}, root, outside)
    assert not local_write({}, root, root)


def test_bulk_patch_decoy_path_never_bypasses_workspace_gate(tmp_path):
    from standing.policy import local_write
    args = {'mode': 'patch', 'path': str(tmp_path / 'decoy'),
            'patch': '*** Begin Patch\n*** Update File: /outside/file\n@@\n-old\n+new\n*** End Patch'}
    assert not local_write(args, tmp_path, tmp_path)


@pytest.mark.parametrize('level', range(4))
@pytest.mark.parametrize('category', ['read', 'write', 'shell', 'send', 'spend', 'spin', 'scrub', 'unknown'])
def test_full_default_level_matrix(level, category):
    from standing.policy import needs_approval
    permitted = {
        0: {'read'}, 1: {'read', 'write'}, 2: {'read', 'write', 'shell', 'send', 'spin', 'unknown'},
        3: {'read', 'write', 'shell', 'send', 'spend', 'spin', 'scrub', 'unknown'},
    }
    assert needs_approval(level, {category}, local=True) == (category not in permitted[level])


def test_multiple_categories_and_configured_map():
    from standing.policy import needs_approval, DEFAULT_LEVELS
    assert needs_approval(2, {'send', 'scrub'})
    levels = dict(DEFAULT_LEVELS, L1=['read'])
    assert needs_approval(1, {'write'}, local=True, levels=levels)


@pytest.mark.parametrize('tool,args,category', [
    ('terminal', {'command': 'bash -c "echo safe"'}, 'shell'),
    ('terminal', {'command': 'git push; rm file'}, 'scrub'),
    ('cronjob_manage', {'action': 'list'}, 'read'),
    ('delegate_task', {'action': 'list'}, 'read'),
    ('patch', {'mode': 'patch', 'patch': '*** Delete File: file'}, 'scrub'),
])
def test_compound_and_administrative_categories(tool, args, category):
    from standing.policy import classify
    assert category in classify(tool, args)


V4A = '*** Begin Patch\n*** Update File: {}\n@@\n-old\n+new\n*** End Patch'


def test_patch_mode_inside_workspace_is_a_local_write(tmp_path):
    """0.1.3: a worker at L1/L2 editing its own workspace in patch mode is not stopped."""
    from standing.policy import classify, local_write, needs_approval
    for target in ('src/app.py', str(tmp_path / 'src' / 'app.py')):
        args = {'mode': 'patch', 'patch': V4A.format(target)}
        local = local_write(args, tmp_path, tmp_path)
        assert local
        for level in (1, 2):
            assert not needs_approval(level, classify('patch', args), local)
        assert needs_approval(0, classify('patch', args), local)


def test_patch_mode_multi_file_needs_every_target_inside(tmp_path):
    from standing.policy import local_write
    inside = ('*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n'
              '*** Add File: docs/new.md\n+hello\n*** End Patch')
    assert local_write({'mode': 'patch', 'patch': inside}, tmp_path, tmp_path)
    mixed = inside.replace('docs/new.md', '/outside/new.md')
    assert not local_write({'mode': 'patch', 'patch': mixed}, tmp_path, tmp_path)
    assert not local_write({'mode': 'patch', 'patch': inside.replace('a.py', '../a.py')}, tmp_path, tmp_path)


@pytest.mark.parametrize('payload', [
    None, '', 7, '*** Begin Patch\n*** End Patch', 'no headers at all',
    '*** Begin Patch\n*** Update File: \n*** End Patch',
])
def test_patch_mode_without_readable_targets_is_gated(tmp_path, payload):
    from standing.policy import local_write
    assert not local_write({'mode': 'patch', 'path': 'ok.txt', 'patch': payload}, tmp_path, tmp_path)


def test_patch_payload_outside_patch_mode_is_gated(tmp_path):
    from standing.policy import local_write
    assert not local_write({'path': 'ok.txt', 'patch': V4A.format('ok.txt')}, tmp_path, tmp_path)
    assert not local_write({'mode': 'replace', 'path': 'ok.txt', 'patch': V4A.format('ok.txt')}, tmp_path, tmp_path)


def test_patch_mode_symlink_escape_and_loose_spacing(tmp_path):
    from standing.policy import local_write
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'link').symlink_to(outside, target_is_directory=True)
    assert not local_write({'mode': 'patch', 'patch': V4A.format('link/file')}, workspace, workspace)
    # Core tolerates loose spacing and CRLF in headers; Standing must read the same targets.
    loose = '***Update  File:   /outside/file\r\n@@\r\n-old\r\n+new'
    assert not local_write({'mode': 'patch', 'patch': loose}, workspace, workspace)
    assert local_write({'mode': 'patch', 'patch': loose.replace('/outside/file', 'file')}, workspace, workspace)


@pytest.mark.parametrize('header', [
    '*** Delete File: old.py', '***Delete  File: old.py', '*** Move File: a.py -> b.py',
    '*** Move File: a.py -> /outside/b.py',
])
def test_patch_delete_or_move_stays_scrub_even_inside_workspace(tmp_path, header):
    from standing.policy import classify, local_write, needs_approval
    args = {'mode': 'patch', 'patch': f'*** Begin Patch\n{header}\n*** End Patch'}
    categories = classify('patch', args)
    assert 'scrub' in categories
    local = local_write(args, tmp_path, tmp_path)
    for level in (0, 1, 2):
        assert needs_approval(level, categories, local)
    if '/outside/' in header:
        assert not local


def test_scrub_marker_inside_added_content_is_not_a_delete(tmp_path):
    from standing.policy import classify
    args = {'mode': 'patch', 'patch': '*** Begin Patch\n*** Add File: notes.md\n+*** Delete File: x\n*** End Patch'}
    assert classify('patch', args) == {'write'}


@pytest.mark.parametrize('header', [
    '*** Delete\rFile: old.py', '*** Move\rFile: old.py -> new.py', '***\rDelete File:\rold.py',
    '*** Delete File: old.py\r', '*** Move  File:old.py->new.py',
])
def test_carriage_return_inside_a_header_is_still_scrub(tmp_path, header):
    """Core's header regex accepts a bare CR as whitespace; Standing must see the same delete/move."""
    from standing.policy import classify, local_write, needs_approval
    args = {'mode': 'patch', 'patch': f'*** Begin Patch\n{header}\n*** End Patch'}
    categories = classify('patch', args)
    assert 'scrub' in categories
    for level in (0, 1, 2):
        assert needs_approval(level, categories, local_write(args, tmp_path, tmp_path))


@pytest.mark.parametrize('bad', ['~no_such_user_7fd182/ignored', 'a\x00b', '~'])
def test_unresolvable_patch_target_is_never_local_and_never_raises(tmp_path, monkeypatch, bad):
    from standing.policy import local_write
    monkeypatch.setenv('HOME', str(tmp_path.parent / 'elsewhere'))
    payload = f'*** Add File: {bad}\n*** Begin Patch\n*** Add File: /outside/new\n+x\n*** End Patch'
    assert local_write({'mode': 'patch', 'patch': payload}, tmp_path, tmp_path) is False
    assert local_write({'path': bad}, tmp_path, tmp_path) is False


def test_resolution_error_is_not_local(tmp_path, monkeypatch):
    from pathlib import Path
    from standing.policy import local_write
    def boom(self, *args, **kwargs):
        raise RuntimeError('cannot expand')
    root = str(tmp_path)
    monkeypatch.setattr(Path, 'expanduser', boom)
    assert local_write({'mode': 'patch', 'patch': V4A.format('a.py')}, root, root) is False
    assert local_write({'path': 'a.py'}, root, root) is False


def test_outside_symlink_pointing_inside_is_not_a_local_patch_target(tmp_path):
    from standing.policy import local_write
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'real.py').write_text('x')
    alias = tmp_path / 'alias.py'
    alias.symlink_to(workspace / 'real.py')
    for payload in (f'*** Delete File: {alias}', f'*** Move File: {alias} -> {workspace}/b.py', V4A.format(alias)):
        assert not local_write({'mode': 'patch', 'patch': payload}, workspace, workspace)
    assert local_write({'mode': 'patch', 'patch': V4A.format('real.py')}, workspace, workspace)
