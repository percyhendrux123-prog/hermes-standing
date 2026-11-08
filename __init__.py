"""Standing — earned trust, independent checks, native Hermes approval directives."""
import logging
import os
from pathlib import Path

logger = logging.getLogger('hermes-standing')

SCHEMA = {
    'name': 'standing_proof',
    'description': 'Independently check a file, HTTP page, or exact owner-allowlisted read-only command. '
                   'A pass earns one point and a proof handle; only a completed, mismatching check lowers your level. '
                   'An unavailable check earns no pass and causes no demotion. '
                   'Sandboxed workers: use http or allowlisted command proofs; file proofs run on the machine Standing runs on. '
                   'Before kanban_complete, run a passing proof in this worker task and session. '
                   'Do not put secrets in targets or expected results.',
    'parameters': {
        'type': 'object', 'required': ['kind', 'target', 'expect'], 'additionalProperties': False,
        'properties': {
            'kind': {'type': 'string', 'enum': ['http', 'file', 'command']},
            'target': {'oneOf': [{'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}}],
                       'description': 'URL, file path, or exact command argv list (preferred) or command string.'},
            'expect': {'type': 'string', 'minLength': 1,
                       'description': 'Non-empty expected substring, or a file’s SHA-256 (optionally sha256: prefixed).'},
        },
    },
}
GUIDANCE = ('Standing records independently checked results, not claims of success. Before marking your '
            'assigned kanban task done, call standing_proof(kind, target, expect) and obtain a passing check '
            'in this session. Use a relevant artifact, HTTP page, or exact owner-allowlisted read-only command. '
            'Only a completed, mismatching check lowers your level; an unavailable check does not. '
            'Sandboxed workers: use http or allowlisted command proofs; file proofs run on the machine Standing runs on. '
            'Never use secret files or credentials as proof targets. '
            'Standing may ask the owner for approval; it never overrides other Hermes safety gates.')


def agent_cwd():
    # Public guide "Register slash commands", lines 2267-2277; host agent/runtime_cwd.py:105-107
    # (reference commit 2bd378ed1b). A gateway's os.getcwd() is NOT the caller's workspace.
    try:
        from agent.runtime_cwd import resolve_agent_cwd
    except ImportError:
        return Path.cwd()  # Older host without the helper; documented compatibility fallback.
    return resolve_agent_cwd()


def session_id():
    # Public guide "Register slash commands", lines 2271-2288: session-local identity accessor.
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return os.environ.get('HERMES_SESSION_ID', '')
    return get_session_env('HERMES_SESSION_ID') or ''


def register(ctx):
    from .standing.config import Settings
    from .standing.ledger import Ledger
    from .standing.proofs import ProofRunner
    from .standing.service import Standing

    defaults = Settings()
    config_error = False
    try:
        config = {name: ctx.get_config(name, default=getattr(defaults, name))
                  for name in Settings.__dataclass_fields__}
        settings = Settings.parse(config)
    except Exception as exc:
        logger.warning('Standing settings need attention (%s); completion remains protected.', type(exc).__name__)
        settings, config_error = defaults, True

    def actor():
        # Host hermes_cli/plugins.py PluginContext.profile_name:401-409 (2bd378ed1b):
        # derives the active profile from HERMES_HOME on CLI, gateway and kanban, else "default".
        return getattr(ctx, 'profile_name', None) or 'default'

    def task():
        # Host tools/kanban_tools.py:_default_task_id, lines 189-206 (2bd378ed1b):
        # dispatcher supplies HERMES_KANBAN_TASK. It is context, never the actor identity.
        return os.environ.get('HERMES_KANBAN_TASK', '')

    def connect():
        if settings.db_path:
            import sqlite3
            path = Path(settings.db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            return sqlite3.connect(path, timeout=10)
        factory = getattr(ctx, 'plugin_db', None)
        if callable(factory):
            return factory()
        # Guide's per-plugin persistent storage API; host plugins/plugin_storage.py:36-46.
        # The copied PluginContext does not expose ctx.plugin_db, so keep the documented fallback.
        from plugins.plugin_storage import plugin_db
        return plugin_db('hermes-standing')

    def ledger():
        return Ledger(connect, settings.default_level, settings.promote_after, settings.max_auto_level)

    runner = ProofRunner(agent_cwd, settings.command_allowlist, settings.proof_timeout,
                         workspace=settings.workspace, file_proof_roots=settings.file_proof_roots,
                         http_proof_targets=settings.http_proof_targets)
    service = Standing(settings, ledger, runner, actor, task, session_id, agent_cwd, config_error, ctx=ctx)
    # Apply insert-only starting levels at load. Failed storage must not disable the hooks.
    try:
        service.ledger().actor(actor())
    except Exception as exc:
        logger.warning('Standing storage is not ready (%s); completion remains protected.', type(exc).__name__)
    ctx.register_tool(name='standing_proof', toolset='standing', schema=SCHEMA, handler=service.prove)
    ctx.register_hook('pre_tool_call', service.before)
    ctx.register_hook('post_tool_call', service.after)
    ctx.register_hook('pre_llm_call', lambda **kwargs: {'context': GUIDANCE})
    ctx.register_hook('on_session_finalize', service.finalize)
    ctx.register_hook('on_session_reset', service.finalize)
    ctx.register_command('standing', service.command,
                         description='Show earned trust, recent checks, or set a level locally.',
                         args_hint='[log <profile> | set <profile> <L0-L3> [reason]]')
