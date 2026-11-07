"""F1: synthetic files only; a refusal must happen before opening content."""
from pathlib import Path
import os

import pytest

from standing.proofs import ProofRunner


CREDENTIAL_PATHS = [
    'auth.json', 'credentials.json', 'service/MyCredentials.db', 'cert.pem', 'signing.key',
    'id_rsa', '.env', '.env.production', '.environment', '.netrc', '.npmrc', '.pypirc',
    'Library/Keychains/login.keychain-db', '.ssh/config', '.aws/config', '.gnupg/pubring',
    '.config/gcloud/configurations/default', '.docker/config.json',
]


def observe_opens(monkeypatch):
    opened = []
    original_path_open, original_os_open = Path.open, os.open
    def path_open(path, *args, **kwargs):
        opened.append(path)
        return original_path_open(path, *args, **kwargs)
    def descriptor_open(path, *args, **kwargs):
        opened.append(path)
        return original_os_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', path_open)
    monkeypatch.setattr(os, 'open', descriptor_open)
    return opened


@pytest.mark.parametrize('relative', CREDENTIAL_PATHS)
@pytest.mark.parametrize('link_mode', ['direct', 'to_secret', 'secret_name'])
def test_credential_paths_never_opened(tmp_path, monkeypatch, relative, link_mode):
    secret = tmp_path / relative
    secret.parent.mkdir(parents=True, exist_ok=True)
    ordinary = tmp_path / 'ordinary-artifact'
    if link_mode == 'secret_name':
        ordinary.write_text('synthetic-marker')
        secret.symlink_to(ordinary)
        target = secret
    else:
        secret.write_text('synthetic-marker')
        if link_mode == 'to_secret':
            ordinary.symlink_to(secret)
            target = ordinary
        else:
            target = secret
    runner = ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path))
    opened = observe_opens(monkeypatch)
    assert not runner.run('file', str(target), 'synthetic-marker').passed
    assert opened == []


def test_no_configured_workspace_refuses_even_ordinary_file(tmp_path, monkeypatch):
    artifact = tmp_path / 'artifact'
    artifact.write_text('ready')
    def forbidden_open(*args, **kwargs):
        pytest.fail('A file was opened without an owner-configured proof scope')
    monkeypatch.setattr(Path, 'open', forbidden_open)
    monkeypatch.setattr(os, 'open', forbidden_open)
    assert not ProofRunner(cwd=lambda: tmp_path).run('file', str(artifact), 'ready').passed


def test_owner_file_roots_and_workspace(tmp_path, monkeypatch):
    from standing.config import Settings
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    artifact = workspace / 'artifact'
    artifact.write_text('ready')
    outside = tmp_path / 'outside'
    outside.write_text('ready')
    (workspace / 'escape').symlink_to(outside)
    roots = [str(workspace)]
    settings = Settings.parse({'workspace': str(workspace), 'file_proof_roots': roots})
    runner = ProofRunner(cwd=lambda: workspace, workspace=settings.workspace,
                         file_proof_roots=settings.file_proof_roots)
    assert runner.run('file', str(artifact), 'ready').passed
    opened = observe_opens(monkeypatch)
    assert not runner.run('file', str(outside), 'ready').passed
    assert not runner.run('file', 'escape', 'ready').passed
    assert opened == []
    # Exact file targets can be explicitly selected without granting their parent.
    runner = ProofRunner(cwd=lambda: workspace, file_proof_roots=[str(outside)])
    assert runner.run('file', str(outside), 'ready').passed
    assert not runner.run('file', str(artifact), 'ready').passed


def test_hermes_home_refused_except_configured_workspace(tmp_path, monkeypatch):
    home = tmp_path / 'hermes-home'
    workspace = home / 'workspace'
    workspace.mkdir(parents=True)
    (home / 'state.db').write_text('synthetic-marker')
    (workspace / 'artifact').write_text('synthetic-marker')
    (workspace / 'link').symlink_to(home / 'state.db')
    monkeypatch.setenv('HERMES_HOME', str(home))
    runner = ProofRunner(cwd=lambda: workspace, workspace=str(workspace), file_proof_roots=[str(home)])
    assert runner.run('file', 'artifact', 'synthetic-marker').passed
    opened = observe_opens(monkeypatch)
    assert not runner.run('file', str(home / 'state.db'), 'synthetic-marker').passed
    assert not runner.run('file', 'link', 'synthetic-marker').passed
    assert opened == []


@pytest.mark.parametrize('roots', ['relative', ['relative'], [None]])
def test_invalid_file_proof_roots_rejected(roots):
    from standing.config import Settings
    with pytest.raises(ValueError):
        Settings.parse({'file_proof_roots': roots})


def test_entry_symlink_swap_after_resolution_never_opens_secret(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    artifact = workspace / 'artifact'
    artifact.write_text('ordinary')
    secret = tmp_path / 'credentials.json'
    secret.write_text('synthetic-marker')
    entry = workspace / 'entry'
    entry.symlink_to(artifact)
    original = Path.resolve
    swapped = []
    def resolve_then_swap(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == entry and not swapped:
            entry.unlink()
            entry.symlink_to(secret)
            swapped.append(True)
        return result
    monkeypatch.setattr(Path, 'resolve', resolve_then_swap)
    runner = ProofRunner(cwd=lambda: workspace, workspace=str(workspace))
    assert not runner.run('file', str(entry), 'synthetic-marker').passed
    assert swapped


@pytest.mark.parametrize('swap', ['leaf', 'parent'])
def test_swap_at_open_cannot_follow_leaf_or_parent_symlink(tmp_path, monkeypatch, swap):
    import os
    workspace = tmp_path / 'workspace'
    directory = workspace / 'directory'
    directory.mkdir(parents=True)
    artifact = directory / 'artifact'
    artifact.write_text('ordinary')
    secret_dir = tmp_path / '.aws'
    secret_dir.mkdir()
    secret = secret_dir / 'artifact'
    secret.write_text('synthetic-marker')
    swapped = []
    def replace_entry():
        if swapped:
            return
        if swap == 'leaf':
            artifact.unlink()
            artifact.symlink_to(secret)
        else:
            directory.rename(workspace / 'held')
            directory.symlink_to(secret_dir, target_is_directory=True)
        swapped.append(True)
    original_path_open = Path.open
    def path_open(path, *args, **kwargs):
        if path == artifact:
            replace_entry()
        return original_path_open(path, *args, **kwargs)
    original_os_open = os.open
    def descriptor_open(path, flags, *args, **kwargs):
        if kwargs.get('dir_fd') is not None and Path(path).name == ('artifact' if swap == 'leaf' else 'directory'):
            replace_entry()
        return original_os_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', path_open)
    monkeypatch.setattr(os, 'open', descriptor_open)
    runner = ProofRunner(cwd=lambda: workspace, workspace=str(workspace))
    assert not runner.run('file', str(artifact), 'synthetic-marker').passed
    assert swapped
