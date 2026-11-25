"""Cohesive domain modules for team-lead Linear synchronization."""
from __future__ import annotations

from . import _imports as _imports
from . import credentials_and_mode as _credentials_and_mode
from . import graphql_client as _graphql_client
from . import ledger_and_attribution as _ledger_and_attribution
from . import sync_and_archive as _sync_and_archive
from . import commands as _commands
from . import cli as _cli

_MODULES = [
    _credentials_and_mode,
    _graphql_client,
    _ledger_and_attribution,
    _sync_and_archive,
    _commands,
    _cli,
]
_SYMBOLS = {}
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            _SYMBOLS[_name] = _value
for _module in _MODULES:
    _module.__dict__.update(_SYMBOLS)
globals().update(_SYMBOLS)
_ORIGINAL_SYMBOLS = dict(_SYMBOLS)
__all__ = [name for name in globals() if not name.startswith("__")]
