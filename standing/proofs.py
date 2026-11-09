"""Execute only explicitly requested checks; never return target content."""
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import shlex
import os
import subprocess
import selectors
import signal
import time
import stat
from urllib.parse import urlsplit, unquote
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

LIMIT = 2 * 1024 * 1024
META = re.compile(r'[;&|<>`$\\\n\r\x00*?\[\]{}()!]')


PROOF_LOCATIONS = ('Sandboxed workers: use http or allowlisted command proofs; '
                   'file proofs run on the machine Standing runs on. '
                   'HTTP targets and exact read-only commands must be owner-allowlisted there.')


@dataclass(frozen=True)
class Outcome:
    passed: bool | None  # None means the check could not run, not a false claim.
    reason: str

    @property
    def result(self):
        return 'unavailable' if self.passed is None else 'pass' if self.passed else 'fail'


def unavailable(reason):
    return Outcome(None, f'{reason} No pass or demotion was recorded. {PROOF_LOCATIONS}')


def argv(target):
    if isinstance(target, str):
        if META.search(target):
            raise ValueError('Shell characters are not allowed.')
        words = shlex.split(target)
    elif isinstance(target, list) and all(isinstance(x, str) for x in target):
        words = list(target)
    else:
        raise ValueError('Use a command string or a list of words.')
    if not words or any(not x or META.search(x) for x in words):
        raise ValueError('Shell characters and empty commands are not allowed.')
    return words


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def credential_path(path):
    parts = tuple(part.lower() for part in path.parts)
    if any(part in {'.ssh', '.aws', '.gnupg', 'auth.json', '.netrc', '.npmrc', '.pypirc'}
           or 'credentials' in part or 'keychain' in part or part.startswith(('.env', 'id_'))
           or part.endswith(('.pem', '.key')) for part in parts):
        return True
    return any(pair in {('.config', 'gcloud'), ('.docker', 'config.json')}
               for pair in zip(parts, parts[1:]))


def proof_url(value):
    if not isinstance(value, str) or re.search(r'[\s\x00-\x1f\x7f\\]', value):
        raise ValueError('Use an absolute HTTP proof URL without credentials or control characters.')
    url = urlsplit(value)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username is not None or url.password is not None or url.fragment:
        raise ValueError('Use an absolute HTTP proof URL without credentials or fragments.')
    url.port  # Validate port before any network access.
    path = unquote(url.path)
    if re.search(r'[\x00-\x1f\x7f\\%;]', path) or any(part in {'.', '..'} for part in path.split('/')):
        raise ValueError('HTTP proof paths cannot contain traversal or ambiguous escapes.')
    return url


def origin(url):
    return url.scheme, url.hostname, url.port if url.port is not None else (443 if url.scheme == 'https' else 80)


@contextmanager
def artifact_stream(path):
    # Traverse the validated canonical path by held descriptors. A link swapped
    # into ANY component between validation and open must fail, never be followed.
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            # fstat the directory type: some gVisor sandboxes follow directory
            # symlinks when O_DIRECTORY and O_NOFOLLOW are combined.
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise ValueError('Artifact parent is not a directory.')
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('File proofs require a regular artifact file.')
            stream = os.fdopen(descriptor, 'rb')
            descriptor = None  # stream now owns the descriptor, including on exceptions.
            with stream:
                yield stream
        finally:
            if descriptor is not None:
                os.close(descriptor)
    finally:
        os.close(directory)


class ProofRunner:
    def __init__(self, cwd, command_allowlist=(), timeout=10, *, workspace='', file_proof_roots=(), http_proof_targets=()):
        self.cwd = cwd
        self.commands = [argv(x) for x in command_allowlist]
        self.timeout = timeout
        self.workspace = Path(workspace).expanduser().absolute() if workspace else None
        self.file_roots = [Path(x).expanduser().absolute() for x in file_proof_roots]
        if not self.file_roots and self.workspace:
            self.file_roots = [self.workspace]
        self.http_targets = [proof_url(x) for x in http_proof_targets]

    def run(self, kind, target, expect):
        if not isinstance(expect, str) or not expect.strip():
            return unavailable('The check needs a non-empty expected result.')
        try:
            if kind == 'file':
                passed = self.file(target, expect)
            elif kind == 'http':
                if not self.http_targets:
                    return unavailable('HTTP proofs are disabled until the owner configures http_proof_targets.')
                passed = self.http(target, expect)
            elif kind == 'command':
                words = argv(target)
                if words not in self.commands:
                    return unavailable('That exact command is not on the owner’s read-only list.')
                passed = self.command(words, expect)
            else:
                return unavailable('Choose a file, http, or command check.')
            return Outcome(bool(passed), 'The check passed.' if passed else 'The result did not match the expected result.')
        except Exception:
            # Exceptions can contain paths, URL credentials, output or other private content.
            return unavailable('The check could not be run safely or the target was unavailable.')

    def file(self, target, expect):
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = Path(self.cwd()) / path
        # Reject the original spelling BEFORE resolution or content access.
        if credential_path(path) or not self.file_roots:
            raise ValueError('File proof target is not permitted.')
        from hermes_constants import get_hermes_home
        home = get_hermes_home().expanduser().absolute()
        resolved = path.resolve()
        for candidate, roots, protected_home, workspace in (
            (Path(os.path.abspath(path)), self.file_roots, home, self.workspace),
            (resolved, [root.resolve() for root in self.file_roots], home.resolve(),
             self.workspace.resolve() if self.workspace else None),
        ):
            if credential_path(candidate) or not any(candidate.is_relative_to(root) for root in roots):
                raise ValueError('File proof target is not permitted.')
            if candidate.is_relative_to(protected_home) and not (workspace and candidate.is_relative_to(workspace)):
                raise ValueError('File proof target is not permitted.')
        with artifact_stream(resolved) as stream:
            digest = expect.removeprefix('sha256:')
            if re.fullmatch(r'[0-9a-fA-F]{64}', digest):
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(65536), b''):
                    hasher.update(chunk)
                return hasher.hexdigest() == digest.lower()
            return expect.encode('utf-8') in stream.read(LIMIT)

    def http(self, target, expect):
        url = proof_url(target)
        path = url.path or '/'
        if not any(origin(url) == origin(prefix) and url.query == prefix.query and
                   (path.startswith(prefix.path or '/') if (prefix.path or '/').endswith('/')
                    else path == prefix.path) for prefix in self.http_targets):
            raise ValueError('HTTP proof target is not permitted.')
        # Check the owner scope BEFORE even constructing an opener. No ambient proxies or redirects.
        opener = build_opener(ProxyHandler({}), NoRedirect())
        request = Request(target, headers={'User-Agent': 'hermes-standing/0.1.3'}, method='GET')
        try:
            response = opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            # HTTPError owns a response too, including when NoRedirect refuses it.
            error.close()
            raise
        with response:
            if response.status != 200:
                return False
            # Reject ambiguous framing rather than letting urllib treat it as unbounded.
            lengths = response.headers.get_all('Content-Length', [])
            if len(lengths) > 1 or (lengths and not re.fullmatch(r'[0-9]+', lengths[0].strip())):
                raise ValueError('HTTP proof response has ambiguous framing.')
            # Bounded HTTPResponse.read does not raise on a short Content-Length body.
            # Require all advertised bytes in the proof prefix before judging its content.
            length = response.length
            content = response.read(LIMIT)
            if length is not None and len(content) < min(length, LIMIT):
                raise ValueError('HTTP proof response ended early.')
            return expect.encode('utf-8') in content

    def command(self, words, expect):
        # Only exact owner-configured argv; no shell, inherited secrets, input, or stderr retention.
        environment = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
        deadline = time.monotonic() + self.timeout
        output = bytearray()
        with subprocess.Popen(words, shell=False, cwd=self.cwd(), env=environment,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              start_new_session=True) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise subprocess.TimeoutExpired(words, self.timeout)
                        # At most one byte beyond the budget is read, solely to detect overflow.
                        chunk = os.read(process.stdout.fileno(), min(65536, LIMIT + 1 - len(output)))
                        if not chunk:
                            break
                        if len(output) + len(chunk) > LIMIT:
                            raise ValueError('Command stdout exceeded the proof budget.')
                        output.extend(chunk)
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
                return code == 0 and expect.encode('utf-8') in output
            except BaseException:
                # Kill the isolated command group on overflow/timeout, including pipe-holding helpers.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
