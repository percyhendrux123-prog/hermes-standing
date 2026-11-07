import hashlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def test_file_contains_and_hash_are_actually_checked(tmp_path):
    from standing.proofs import ProofRunner
    path = tmp_path / 'artifact.txt'
    path.write_text('ready for review')
    runner = ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path))
    assert runner.run('file', 'artifact.txt', 'ready').passed
    assert runner.run('file', str(path), hashlib.sha256(path.read_bytes()).hexdigest()).passed
    assert not runner.run('file', str(path), 'missing').passed
    assert not runner.run('file', 'absent', 'ready').passed


@pytest.fixture
def http_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.hits.append(self.path)
            status = 302 if self.path == '/redirect' else 404 if self.path == '/missing' else 200
            self.send_response(status)
            if status == 302:
                self.send_header('Location', '/ready')
            if self.path.startswith('/truncated-'):
                self.send_header('Content-Length', '100, 100' if 'combined' in self.path else '100')
                if 'duplicate' in self.path:
                    self.send_header('Content-Length', '100')
                self.send_header('Connection', 'close')
            elif self.path.startswith('/framed-'):
                self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'wrong' if self.path.endswith('mismatch') else b'ready')

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.hits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_http_checks_status_body_and_refuses_redirect(http_server, tmp_path, monkeypatch):
    from standing.proofs import ProofRunner
    server, url = http_server
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    runner = ProofRunner(cwd=lambda: tmp_path, http_proof_targets=[url + '/ready', url + '/missing', url + '/redirect'])
    assert runner.run('http', url + '/ready', 'ready').passed
    assert not runner.run('http', url + '/ready', 'absent').passed
    assert not runner.run('http', url + '/missing', 'ready').passed
    assert not runner.run('http', url + '/redirect', 'ready').passed
    assert server.hits == ['/ready', '/ready', '/missing', '/redirect']


@pytest.mark.parametrize('path', ['/missing', '/redirect'])
def test_http_error_responses_are_explicitly_closed(http_server, tmp_path, monkeypatch, path):
    from urllib.error import HTTPError
    import standing.proofs as proofs

    server, url = http_server
    errors = []
    real_build = proofs.build_opener

    def tracked_build(*handlers):
        opener = real_build(*handlers)
        real_open = opener.open

        def tracked_open(*args, **kwargs):
            try:
                return real_open(*args, **kwargs)
            except HTTPError as error:
                errors.append(error)
                raise

        monkeypatch.setattr(opener, 'open', tracked_open)
        return opener

    monkeypatch.setattr(proofs, 'build_opener', tracked_build)
    runner = proofs.ProofRunner(cwd=lambda: tmp_path, http_proof_targets=[url + path])
    try:
        assert runner.run('http', url + path, 'ready').result == 'unavailable'
        assert server.hits == [path]  # Redirects remain refused, never followed.
        assert len(errors) == 1
        assert errors[0].closed
        assert errors[0].fp.closed
    finally:
        for error in errors:
            error.close()  # Keep the failing regression itself leak-free.


def test_http_default_refuses_without_connecting(http_server, tmp_path):
    from standing.proofs import ProofRunner
    server, url = http_server
    outcome = ProofRunner(cwd=lambda: tmp_path).run('http', url + '/ready', 'ready')
    assert not outcome.passed
    assert 'owner' in outcome.reason.lower() and 'http_proof_targets' in outcome.reason
    assert server.hits == []


def test_http_owner_prefixes_pin_origin_path_and_query(http_server, tmp_path):
    from standing.proofs import ProofRunner
    server, url = http_server
    runner = ProofRunner(cwd=lambda: tmp_path,
                         http_proof_targets=[url + '/proofs/', url + '/health', url + '/query?mode=ready'])
    assert runner.run('http', url + '/proofs/result', 'ready').passed
    assert runner.run('http', url + '/health', 'ready').passed
    assert runner.run('http', url + '/query?mode=ready', 'ready').passed
    allowed_hits = list(server.hits)
    for target in [url + '/delete', url + '/health-other', url + '/health?send=data',
                   url + '/proofs/result?send=data', url + '/query?mode=ready&send=data',
                   url + '/proofs/../delete', url + '/proofs/%2e%2e/delete',
                   url + '/proofs/%252e%252e/delete', url + '/proofs/%2f../delete',
                   url + '/proofs/./delete', url + '/proofs/\\..\\delete',
                   url + '/proofs/result#fragment', url + '/proofs/\nresult']:
        assert not runner.run('http', target, 'ready').passed, target
    assert server.hits == allowed_hits


def test_http_unconfigured_targets_refused_before_opener_creation(tmp_path, monkeypatch):
    import standing.proofs as proofs
    def forbidden(*args, **kwargs):
        pytest.fail('Unconfigured URL reached the network path')
    monkeypatch.setattr(proofs, 'build_opener', forbidden)
    runner = proofs.ProofRunner(cwd=lambda: tmp_path, http_proof_targets=['https://approved.test/proofs/'])
    for target in ['https://approved.test.evil.test/proofs/result',
                   'https://approved.test:8443/proofs/result', 'http://approved.test/proofs/result',
                   'https://user@approved.test/proofs/result', 'https://elsewhere.test/proofs/result']:
        assert not runner.run('http', target, 'ready').passed


@pytest.mark.parametrize('targets', ['https://example.test/', ['ftp://example.test/'],
                                  ['https://user:secret@example.test/'], ['https://example.test/#fragment'],
                                  ['https://example.test:bad/'], ['relative'], [None]])
def test_invalid_http_targets_rejected(targets):
    from standing.config import Settings
    with pytest.raises(ValueError):
        Settings.parse({'http_proof_targets': targets})


@pytest.mark.parametrize('target', ['file:///etc/passwd', 'ftp://example.test', 'http://user:password@localhost', 'http://localhost/#secret'])
def test_http_rejects_non_http_and_credentials(target, tmp_path):
    from standing.proofs import ProofRunner
    assert not ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path)).run('http', target, 'x').passed


def test_real_allowlisted_command_and_exact_argv(tmp_path, monkeypatch):
    from standing.proofs import ProofRunner
    monkeypatch.setenv('STANDING_FAKE_SECRET', 'do-not-inherit')
    command = ['/usr/bin/env']
    runner = ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command])
    assert runner.run('command', command, 'LC_ALL=C').passed
    assert not runner.run('command', command, 'do-not-inherit').passed
    assert not runner.run('command', command + ['extra'], 'LC_ALL=C').passed
    assert not runner.run('command', ['sh', '-c', 'touch unsafe'], 'clean').passed
    assert not (tmp_path / 'unsafe').exists()


@pytest.mark.parametrize('meta', [';', '&', '|', '>', '<', '`', '$', '\n', '\r', '\\', '*', '?', '[', ']', '{', '}', '(', ')', '!', '\x00'])
def test_command_metacharacters_rejected_even_when_listed(meta, tmp_path):
    from standing.proofs import ProofRunner, argv
    with pytest.raises(ValueError):
        argv(['git', 'rev-parse', 'HEAD' + meta])
    assert not ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path)).run('command', 'git rev-parse HEAD' + meta, 'x').passed


def test_command_nonzero_exit_rejects_matching_stdout(tmp_path):
    from standing.proofs import ProofRunner
    script = tmp_path / 'nonzero.py'
    script.write_text("print('ready', flush=True)\nraise SystemExit(1)\n")
    command = [sys.executable, str(script)]
    runner = ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command])
    assert not runner.run('command', command, 'ready').passed


def test_command_timeout_rejects_matching_stdout(tmp_path):
    from standing.proofs import ProofRunner
    script = tmp_path / 'slow.py'
    script.write_text("import time\nprint('ready', flush=True)\ntime.sleep(1)\n")
    command = [sys.executable, str(script)]
    runner = ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command], timeout=0.1)
    assert not runner.run('command', command, 'ready').passed


def test_command_output_budget_rejects_marker_and_stops_producer(tmp_path, monkeypatch):
    import standing.proofs as proofs
    monkeypatch.setattr(proofs, 'LIMIT', 1024)
    script = tmp_path / 'noisy.py'
    script.write_text("import os\nfrom pathlib import Path\nprint('ready', flush=True)\n"
                      "for n in range(256):\n    os.write(1, b'x' * 8192)\n"
                      "Path('producer-finished').write_text('done')\n")
    command = [sys.executable, str(script)]
    runner = proofs.ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command])
    assert not runner.run('command', command, 'ready').passed
    assert not (tmp_path / 'producer-finished').exists()


def test_command_exact_stdout_budget_can_pass(tmp_path, monkeypatch):
    import standing.proofs as proofs
    monkeypatch.setattr(proofs, 'LIMIT', 1024)
    script = tmp_path / 'bounded.py'
    script.write_text("import os\nos.write(1, b'ready' + b'x' * 1019)\n")
    command = [sys.executable, str(script)]
    runner = proofs.ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command])
    assert runner.run('command', command, 'ready').passed


def test_command_timeout_includes_closed_stdout_process_and_reaps_it(tmp_path):
    import os
    from standing.proofs import ProofRunner
    script = tmp_path / 'closed.py'
    script.write_text("import os, time\nfrom pathlib import Path\n"
                      "Path('child-pid').write_text(str(os.getpid()))\n"
                      "os.write(1, b'ready')\nos.close(1)\ntime.sleep(5)\n")
    command = [sys.executable, str(script)]
    runner = ProofRunner(cwd=lambda: tmp_path, command_allowlist=[command], timeout=0.2)
    assert not runner.run('command', command, 'ready').passed
    pid = int((tmp_path / 'child-pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize('kind', ['file', 'http', 'command', 'invented'])
@pytest.mark.parametrize('expect', ['', '   ', None])
def test_empty_expectations_do_not_earn_trust(kind, expect, tmp_path):
    from standing.proofs import ProofRunner
    assert not ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path)).run(kind, 'target', expect).passed


def test_secret_file_and_symlink_not_read(tmp_path):
    from standing.proofs import ProofRunner
    secret = tmp_path / '.env'
    secret.write_text('fake-secret')
    (tmp_path / 'ordinary').symlink_to(secret)
    runner = ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path))
    assert not runner.run('file', str(secret), 'fake-secret').passed
    assert not runner.run('file', 'ordinary', 'fake-secret').passed


def test_secret_named_symlink_not_read(tmp_path):
    from standing.proofs import ProofRunner
    (tmp_path / 'payload').write_text('fake-secret')
    (tmp_path / '.env').symlink_to(tmp_path / 'payload')
    assert not ProofRunner(cwd=lambda: tmp_path, workspace=str(tmp_path)).run('file', '.env', 'fake-secret').passed
