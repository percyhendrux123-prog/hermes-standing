"""Owner settings. Invalid configuration is surfaced, never silently grants authority."""
from dataclasses import dataclass, field
from pathlib import Path
import re
from .policy import DEFAULT_LEVELS
from .proofs import argv, proof_url


def level(value, maximum=3):
    if isinstance(value, str) and re.fullmatch(r'L[0-3]', value):
        value = int(value[1])
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Use a level from L0 through L' + str(maximum) + '.')
    return value


def profile(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', value):
        raise ValueError('Use a short profile name without spaces or control characters.')
    return value


@dataclass
class Settings:
    seed: dict = field(default_factory=dict)
    default_level: int = 1
    levels: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_LEVELS.items()})
    workspace: str = ''
    file_proof_roots: list = field(default_factory=list)
    db_path: str = ''
    http_proof_targets: list = field(default_factory=list)
    promote_after: int = 10
    max_auto_level: int = 1
    require_proof_on_complete: bool = True
    card_line: bool = True
    exempt_profiles: list = field(default_factory=list)
    command_allowlist: list = field(default_factory=list)
    proof_timeout: int = 10

    @classmethod
    def parse(cls, config):
        if not isinstance(config, dict) or set(config) - set(cls.__dataclass_fields__):
            raise ValueError('Standing settings contain an unknown name or are not a map.')
        settings = cls()
        for key, value in config.items():
            if key in {'default_level', 'max_auto_level'}:
                value = level(value, maximum=2)  # L3 is exclusively the owner's /standing set command.
            elif key == 'seed':
                if not isinstance(value, dict):
                    raise ValueError('Seeds must map profile names to levels.')
                value = {profile(k): level(v, maximum=2) for k, v in value.items()}
            elif key in {'promote_after', 'proof_timeout'}:
                if type(value) is not int or value < 1 or (key == 'proof_timeout' and value > 20):
                    raise ValueError('Use a positive whole number; proof timeout cannot exceed 20 seconds.')
            elif key in {'workspace', 'db_path'}:
                if not isinstance(value, str) or (value and not Path(value).expanduser().is_absolute()):
                    raise ValueError('Workspace and database paths must be absolute, or empty.')
            elif key == 'file_proof_roots':
                if not isinstance(value, list) or any(not isinstance(x, str) or not Path(x).expanduser().is_absolute() for x in value):
                    raise ValueError('File proof roots must be a list of absolute artifact paths.')
            elif key == 'http_proof_targets':
                if not isinstance(value, list):
                    raise ValueError('HTTP proof targets must be a list of owner-selected URL prefixes.')
                for target in value:
                    proof_url(target)
            elif key in {'require_proof_on_complete', 'card_line'}:
                if type(value) is not bool:
                    raise ValueError(key + ' must be true or false.')
            elif key == 'exempt_profiles':
                if not isinstance(value, list):
                    raise ValueError('Exempt profiles must be a list of profile names.')
                value = [profile(x) for x in value]
            elif key == 'command_allowlist':
                if not isinstance(value, list):
                    raise ValueError('The command allowlist must be a list of exact commands.')
                value = [argv(x) for x in value]
            elif key == 'levels':
                if not isinstance(value, dict) or set(value) - set(DEFAULT_LEVELS):
                    raise ValueError('The level map uses L0, L1, L2, and L3.')
                merged = {k: list(v) for k, v in DEFAULT_LEVELS.items()}
                for name, categories in value.items():
                    if not isinstance(categories, list) or any(x not in {'read', 'local_write', 'shell', 'send', 'spend', 'spin', 'scrub', 'write', 'unknown', '*'} for x in categories):
                        raise ValueError('The level map contains an unknown category.')
                    if name != 'L3' and '*' in categories:
                        raise ValueError('Only L3 may use the all-actions category.')
                    merged[name] = categories
                value = merged
            setattr(settings, key, value)
        return settings
