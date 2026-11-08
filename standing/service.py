"""Bind independently executed proofs to the actor, worker task and session."""
import hashlib
import json
import logging
import threading
from .card_line import receipt_label
from .config import level, profile
from .policy import OWN_CARD_TOOLS, classify, local_write, needs_approval

logger = logging.getLogger('hermes-standing')
MISSING = ('Standing needs a passing check before this task can be marked done. '
           'Run standing_proof for this task in this session, then try again.')
UNAVAILABLE = ('Standing cannot check this task’s proof right now, so it has not been marked done. '
               'Ask the owner to check Standing, then try again.')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


class Standing:
    def __init__(self, settings, ledger_factory, runner, actor, task, session, cwd, config_error=False, *, ctx):
        self.ctx = ctx
        self.settings = settings
        self.ledger_factory = ledger_factory
        self.runner = runner
        self.actor_name = actor
        self.task_context = task
        self.session_context = session
        self.cwd = cwd
        self.config_error = config_error
        self._ledger = None
        self.receipts = {}
        self.card_lines = set()
        self.lock = threading.RLock()

    def ledger(self):
        if self.config_error:
            raise ValueError('Standing settings need attention.')
        with self.lock:
            if self._ledger is None:
                ledger = self.ledger_factory()
                ledger.seed(self.settings.seed)
                self._ledger = ledger
            return self._ledger

    def identity(self, session_id=''):
        return (profile(self.actor_name()), str(session_id or self.session_context() or ''),
                str(self.task_context() or '').strip())

    def _before_calls(self, args, session_id, **kwargs):
        # Flatten iteratively so nested wrappers cannot exhaust the proof gate's call stack.
        pending = [('tool_call', args)]
        result = None
        malformed = False
        rank = {None: 0, 'approve': 1, 'block': 2}
        while pending:
            name, arguments = pending.pop()
            if name == 'tool_call':
                calls = arguments.get('calls') if isinstance(arguments, dict) else None
                if not isinstance(calls, list) or not calls:
                    malformed = True
                    continue
                for call in reversed(calls):
                    if (not isinstance(call, dict) or not isinstance(call.get('name'), str)
                            or not call['name'].strip() or not isinstance(call.get('arguments'), dict)):
                        malformed = True
                        continue
                    pending.append((call['name'], call['arguments']))
            else:
                directive = self.before(name, arguments, session_id=session_id, **kwargs)
                if directive and rank[directive['action']] > rank[result['action'] if result else None]:
                    result = directive
        if malformed:
            # Even invalid containers can name a completion. Never lose its fail-closed gate.
            values = [args]
            while values:
                value = values.pop()
                if isinstance(value, dict):
                    if value.get('name') == 'kanban_complete':
                        return {'action': 'block', 'message': UNAVAILABLE}
                    values.extend(value.values())
                elif isinstance(value, list):
                    values.extend(value)
            if result is None:
                result = {'action': 'approve', 'message':
                          'Standing could not read this tool batch. The owner’s approval is required.'}
        return result

    def before(self, tool_name, args, session_id='', **kwargs):
        if tool_name == 'tool_call':
            return self._before_calls(args, session_id, **kwargs)
        complete = tool_name == 'kanban_complete'
        # Default true survives failed initialization/identity lookup. An exemption must be known.
        protected = complete and self.settings.require_proof_on_complete
        try:
            actor = profile(self.actor_name())
            protected = protected and actor not in self.settings.exempt_profiles
            if complete and not protected:
                return None
            if tool_name == 'standing_proof':
                return None  # Read-only proof path is available at all levels to earn standing.
            if tool_name in OWN_CARD_TOOLS and not complete:
                # A worker may always report on its OWN assigned card (block, comment, review,
                # heartbeat) at any level: otherwise an unattended worker can finish and never
                # say so. Another task's card, or no assigned task, falls through to the level gate.
                own = str(self.task_context() or '').strip()
                target = str(args.get('task_id') or own).strip()
                if own and target == own:
                    return None
            if protected:
                who, session, task = self.identity(session_id)
                target_task = str(args.get('task_id') or task).strip()
                self.ledger().actor(who)  # Storage failure must also fail closed after a prior pass.
                with self.lock:
                    passed = bool(session and task and target_task == task and (who, session, task) in self.receipts)
                return None if passed else {'action': 'block', 'message': MISSING}
            actor_state = self.ledger().actor(actor)
            categories = classify(tool_name, args)
            local = tool_name in {'write_file', 'patch', 'apply_patch'} and local_write(
                args, self.settings.workspace, self.cwd())
            if needs_approval(actor_state['level'], categories, local, self.settings.levels):
                return {'action': 'approve', 'message':
                        f"Standing: {actor} is at L{actor_state['level']}. This action needs the owner’s approval. "
                        'Other Hermes safety gates still apply.'}
        except Exception as exc:
            logger.warning('Standing could not check a tool call (%s).', type(exc).__name__)
            if protected:
                return {'action': 'block', 'message': UNAVAILABLE}
        return None

    def prove(self, args, session_id='', **kwargs):
        try:
            actor, session, task = self.identity(session_id)
            kind, target, expect = args.get('kind'), args.get('target'), args.get('expect')
            detail_hash = fingerprint([kind, target, expect])
            # Execute independently; never accept a model-supplied result or stdout.
            outcome = self.runner.run(kind, target, expect)
            event_kind = kind if isinstance(kind, str) and kind in {'http', 'file', 'command'} else 'invalid'
            event_id, state = self.ledger().proof(actor, task, event_kind,
                                                detail_hash, outcome.passed, outcome.reason)
            if outcome.passed and session and task:
                with self.lock:
                    self.receipts[(actor, session, task)] = receipt_label(kind, target)
            if outcome.passed is False and task and state['level'] < state['previous_level']:
                self._demotion_line(actor, task, state)
            return json.dumps({'proof': f'standing:{event_id}', 'passed': outcome.passed, 'result': outcome.result,
                               'message': outcome.reason, 'level': f"L{state['level']}",
                               'passes': state['passes_window'], 'fails': state['fails_total']})
        except Exception as exc:
            logger.warning('Standing could not record a proof (%s).', type(exc).__name__)
            return json.dumps({'error': 'Standing could not record this check. It has not counted toward completion.'})

    def _demotion_line(self, actor, task, state):
        try:
            if self.settings.card_line:
                body = f"Standing: {actor} L{state['previous_level']} -> L{state['level']} · a check failed."
                self.ctx.dispatch_tool("kanban_comment", {"task_id": task, "body": body, "board": None})
        except Exception as exc:
            logger.warning('Standing could not add a card line (%s).', type(exc).__name__)

    def after(self, tool_name, args, result, session_id='', **kwargs):
        if tool_name != 'kanban_complete':
            return None
        try:
            if not self.settings.card_line:
                return None
            payload = json.loads(result) if isinstance(result, str) else result
            if not isinstance(payload, dict) or payload.get('ok') is not True:
                return None
            actor, session, task = key = self.identity(session_id)
            target_task = str(args.get('task_id') or task).strip()
            if not session or not task or target_task != task or payload.get('task_id', task) != task:
                return None
            with self.lock:
                if key not in self.receipts or key in self.card_lines:
                    return None
                state = self.ledger().actor(actor)
                body = (f"Standing: {actor} L{state['level']} · check passed ({self.receipts[key]}) · "
                        f"{state['passes_window']} passes this window / {state['fails_total']} fails all time.")
                # Claim the notification before I/O: even a commit followed by a
                # close/error must not be retried into a duplicate. Best-effort, no retry.
                self.card_lines.add(key)
                self.ctx.dispatch_tool("kanban_comment", {"task_id": task, "body": body, "board": args.get("board")})
        except Exception as exc:
            logger.warning('Standing could not add a card line (%s).', type(exc).__name__)
        return None  # Observation only: never change the completion's result.

    def finalize(self, session_id='', **kwargs):
        try:
            session = str(session_id or self.session_context() or '')
            if session:
                with self.lock:
                    self.receipts = {key: value for key, value in self.receipts.items() if key[1] != session}
                    self.card_lines = {key for key in self.card_lines if key[1] != session}
        except Exception as exc:
            logger.warning('Standing could not clear session checks (%s).', type(exc).__name__)

    def command(self, raw='', platform='', source=None, **kwargs):
        try:
            parts = str(raw or '').strip().split()
            ledger = self.ledger()
            ledger.actor(profile(self.actor_name()))
            if not parts:
                rows = ['Actor | Level | Passes (this window) | Fails (all time) | Last event']
                for actor in ledger.listing():
                    rows.append(f"{actor['name']} | L{actor['level']} | {actor['passes_window']} | "
                                f"{actor['fails_total']} | {actor['last_event'] or 'No checks yet.'}")
                return '\n'.join(rows)
            if parts[0] == 'log' and len(parts) == 2:
                events = ledger.log(profile(parts[1]))
                return '\n'.join(f"{x['at']} | {x['kind']} | {x['result']} | {x['reason']}" for x in events) or 'No events yet.'
            if parts[0] == 'set' and len(parts) >= 3:
                # No remote owner-identity API is assumed. Only a human's local slash command can set L3.
                # Guide: "Register slash commands", calling-session platform/source keywords.
                if platform not in {'cli', 'tui', 'desktop', 'local'} or source is not None:
                    return 'Only the owner at a local Hermes terminal or desktop can change Standing levels.'
                name, new_level = profile(parts[1]), level(parts[2])
                reason_hash = fingerprint(' '.join(parts[3:])) if len(parts) > 3 else ''
                ledger.set_level(name, new_level, reason_hash)
                return f'Standing: {name} is now at L{new_level}. Other Hermes gates still apply.'
            return 'Use /standing, /standing log <profile>, or /standing set <profile> <L0-L3> [reason].'
        except Exception as exc:
            logger.warning('Standing command unavailable (%s).', type(exc).__name__)
            return 'Standing could not read or change its record. Check the settings and local storage, then try again.'
