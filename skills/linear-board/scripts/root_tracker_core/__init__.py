"""Cohesive domain modules for Project Root Tracker."""
from __future__ import annotations

from . import shared as _shared
from . import bugs_and_prs as _bugs_and_prs
from . import sinks_and_collect as _sinks_and_collect
from . import markdown as _markdown
from . import linear_sync as _linear_sync
from . import cli as _cli

_MODULES = [
    _shared,
    _bugs_and_prs,
    _sinks_and_collect,
    _markdown,
    _linear_sync,
    _cli,
]
_SYMBOLS: dict[str, object] = {}
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            _SYMBOLS[_name] = _value
for _module in _MODULES:
    _module.__dict__.update(_SYMBOLS)
globals().update(_SYMBOLS)
_ORIGINAL_SYMBOLS = dict(_SYMBOLS)
__all__ = [name for name in globals() if not name.startswith("__")]
