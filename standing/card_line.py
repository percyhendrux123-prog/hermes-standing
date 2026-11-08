"""Privacy-preserving labels for best-effort task-card comments."""
import re
from urllib.parse import urlsplit


def receipt_label(kind, target):
    # Only a passing check calls this. Never retain paths, argv, query or expected text.
    if kind == 'http':
        host = urlsplit(target).hostname or ''
        if re.fullmatch(r'[A-Za-z0-9.:-]+', host):
            return 'http ' + host
    return kind if kind in {'http', 'file', 'command'} else 'check'
