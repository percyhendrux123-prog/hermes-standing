"""Best-effort card visibility through the same host API as kanban_tools._board."""
import re
from urllib.parse import urlsplit


def receipt_label(kind, target):
    # Only a passing check calls this. Never retain paths, argv, query or expected text.
    if kind == 'http':
        host = urlsplit(target).hostname or ''
        if re.fullmatch(r'[A-Za-z0-9.:-]+', host):
            return 'http ' + host
    return kind if kind in {'http', 'file', 'command'} else 'check'


def write_comment(task, body, board=None):
    # Do not infer a home/path or open raw SQLite: the host owns board resolution,
    # env pins, fences, transactions and comment events (kanban_tools.py:312-326).
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    conn = kbc.connect(board=board)
    try:
        kb.add_comment(conn, task, author='standing', body=body)
    finally:
        conn.close()
