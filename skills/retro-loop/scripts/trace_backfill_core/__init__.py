"""Domain modules for the trace backfill compatibility facade."""
from __future__ import annotations
from . import common as _common
from . import data_loaders as _data_loaders
from . import trace_builders as _trace_builders
from . import collection as _collection
from . import exporters as _exporters
from . import cli as _cli

_CORE_MODULES = [_common, _data_loaders, _trace_builders, _collection, _exporters, _cli]
_SYMBOLS = {}
for _module in _CORE_MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            _SYMBOLS[_name] = _value
for _module in _CORE_MODULES:
    _module.__dict__.update(_SYMBOLS)
globals().update(_SYMBOLS)
_ORIGINAL_SYMBOLS = dict(_SYMBOLS)
__all__ = [name for name in globals() if not name.startswith("__")]
