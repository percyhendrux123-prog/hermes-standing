"""Minimal host-home API stub for dependency-free plugin tests only."""
import os
from pathlib import Path
import sys
import types

try:
    import hermes_constants
except ModuleNotFoundError as error:
    if error.name != 'hermes_constants':
        raise
    hermes_constants = types.ModuleType('hermes_constants')
    hermes_constants.get_hermes_home = lambda: Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    sys.modules['hermes_constants'] = hermes_constants
