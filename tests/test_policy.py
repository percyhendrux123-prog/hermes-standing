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
