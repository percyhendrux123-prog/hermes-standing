"""Independent send/spend/spin/scrub rules, informed by Axioma's public categories.

Shell and unknown operations are allowed at L2. This is a hook policy, not a shell sandbox.
"""
from fnmatch import fnmatchcase
from pathlib import Path
import re

DEFAULT_LEVELS = {
    'L0': ['read'],
    'L1': ['read', 'local_write'],
    'L2': ['read', 'local_write', 'shell', 'send', 'spin', 'unknown'],
    'L3': ['*'],
}
READ_TOOLS = {'read_file', 'search_files', 'web_search', 'web_extract', 'vision_analyze',
              'skills_list', 'skill_view', 'tool_describe', 'tool_search', 'hermes_tool_search',
              'kanban_show', 'kanban_list', 'kanban_attachments', 'session_search'}
WRITE_TOOLS = {'write_file', 'patch', 'apply_patch'}
# Lifecycle reports a worker makes about its own assigned card (see Standing.before).
OWN_CARD_TOOLS = {'kanban_block', 'kanban_comment', 'kanban_request_review', 'kanban_heartbeat'}
# Deliberately own small table, not an import or wholesale copy of Axioma's implementation.
RULES = {
    'send': ['*send_email*', '*send_message*', '*create_post*', '*post_message*', '*post_tweet*', '*publish*'],
    'spend': ['*create_payment*', '*confirm_payment*', '*capture_payment*', '*create_checkout*',
              '*create_purchase*', '*create_charge*', '*create_refund*', '*buy_*', '*purchase*'],
    'spin': ['kanban_create', '*deploy*', '*create_job*', '*job_create*', '*cron*create*'],
    'scrub': ['*delete*', '*remove*', '*unlink*', '*rmdir*', '*move*', '*destroy*', '*scrub*'],
}


# Same line shapes Hermes core's V4A parser accepts (tools/patch_parser.py), so Standing reads
# the targets the patch will really touch. Matched at line start, as core does. Core's ``\\s``
# also accepts a bare carriage return inside a header, so nothing is stripped but a line-end CR.
PATCH_WRITES = re.compile(r'\*\*\*\s*(?:Update|Add)\s+File:\s*(.+)')
PATCH_DELETE = re.compile(r'\*\*\*\s*Delete\s+File:\s*(.+)')
PATCH_MOVE = re.compile(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)')


def patch_ops(payload):
    """(targets, removes) named by a V4A payload: every path, and whether any op deletes or moves.

    Reads every line, not only those between Begin/End markers, so it can only see more
    operations than core applies, never fewer."""
    targets, removes = [], False
    if not isinstance(payload, str):
        return targets, removes
    for line in payload.split('\n'):
        line = line.removesuffix('\r')
        move = PATCH_MOVE.match(line)
        delete = None if move else PATCH_DELETE.match(line)
        write = None if move or delete else PATCH_WRITES.match(line)
        if move:
            targets += [move.group(1).strip(), move.group(2).strip()]
        elif delete or write:
            targets.append((delete or write).group(1).strip())
        removes = removes or bool(move or delete)
    return targets, removes

def classify(tool, args):
    tool = tool.lower()
    leaf = tool.split('__')[-1]
    found = {category for category, patterns in RULES.items()
             if any(fnmatchcase(tool, pattern) for pattern in patterns)}
    if tool in {'cronjob', 'cronjob_manage'}:
        action = str(args.get('action', '')).strip().lower()
        if action in {'list', 'get', 'show', 'status'}:
            found.add('read')
        elif action in {'remove', 'delete', 'clear', 'pause', 'stop', 'disable', 'cancel'}:
            found.add('scrub')
        elif action in {'create', 'add', 'update', 'edit', 'resume', 'enable', 'run', 'trigger'}:
            found.add('spin')
        else:
            found.add('unknown')
    if tool == 'delegate_task':
        action = str(args.get('action', 'spawn')).strip().lower()
        if action in {'list', 'get', 'show', 'status'}:
            found.add('read')
        else:
            found.add('spin')
    if tool in {'terminal', 'execute_code'}:
        command = str(args.get('command' if tool == 'terminal' else 'code', '')).replace('\\\n', '')
        plain = re.sub(r'[\x27"\\]', '', command)
        if re.search(r'\b(?:rm|mv|unlink|rmdir|truncate)\b|git\s+(?:reset|clean)\b', plain):
            found.add('scrub')
        if re.search(r'\bgit\s+push\b|\bgh\s+(?:pr\s+(?:create|merge)|release\s+create)\b', plain):
            found.add('send')
        if re.search(r'\b(?:deploy|terraform\s+apply|kubectl\s+apply|crontab)\b', plain):
            found.add('spin')
        # L2 permits worker shells/composition; recognized risky categories still take precedence.
        if not found:
            found.add('shell')
    elif tool in WRITE_TOOLS:
        found.add('write')
        if patch_ops(args.get('patch'))[1]:
            found.add('scrub')  # Deleting or moving a file stays scrub at every level below L3.
    elif tool in READ_TOOLS or re.match(r'^(?:read|search|list|get)_', leaf):
        found.add('read')
    elif tool.startswith('kanban_') and tool not in {'kanban_create'}:
        found.add('write')  # Shared board mutation is not an in-workspace file write.
    return found or {'unknown'}


def inside(value, root, cwd, entry=False):
    """True only when the path provably resolves inside root. Any doubt or error is False."""
    try:
        if not isinstance(value, str) or not value or '\x00' in value:
            return False
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(cwd) / path
        if not path.resolve().is_relative_to(root):
            return False
        # A patch can delete or move the directory entry itself, so the entry (its resolved
        # parent plus its own name) must also sit inside: an outside symlink that points in
        # is not a local target.
        return not entry or (path.parent.resolve() / path.name).is_relative_to(root)
    except Exception:
        return False  # Unknown users, loops, bad encodings: never local, never an error upstream.


def local_write(args, workspace, cwd):
    if not workspace:
        return False
    try:
        root = Path(workspace).expanduser().resolve()
    except Exception:
        return False
    if args.get('mode') == 'patch' or args.get('patch'):
        # Patch mode: the targets live in the payload, never in the optional path field.
        # Local only when it is a real patch-mode call and EVERY named file (and move
        # destination) resolves inside the workspace. Anything unreadable gates the call.
        if args.get('mode') != 'patch':
            return False
        targets = patch_ops(args.get('patch'))[0]
        return bool(targets) and all(inside(target, root, cwd, entry=True) for target in targets)
    return inside(args.get('path', args.get('file_path')), root, cwd)


def needs_approval(level, categories, local=False, levels=None):
    if level == 3:
        return False
    allowed = set((levels or DEFAULT_LEVELS)[f'L{level}'])
    effective = {'local_write' if item == 'write' and local else item for item in categories}
    return not effective.issubset(allowed)
