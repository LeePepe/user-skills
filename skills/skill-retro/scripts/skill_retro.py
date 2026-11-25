#!/usr/bin/env python3
"""Thin CLI and backward-compatible facade for the modular skill-retro engine."""
from __future__ import annotations
import inspect
import sys
from pathlib import Path

# spec_from_file_location callers load this file as a standalone module.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import skill_retro_core as _core

_CORE_MODULES = list(getattr(_core, "_MODULES", ()))
_FACADE_NAMES = tuple(name for name in vars(_core) if not name.startswith("__") and name not in {"_MODULES", "_SYMBOLS", "_ORIGINAL_SYMBOLS"})
_CANONICAL = getattr(_core, "_ORIGINAL_SYMBOLS", vars(_core))
_CORE_FUNCTIONS = {
    name: value
    for name, value in _CANONICAL.items()
    if name in _FACADE_NAMES and inspect.isfunction(value) and value.__module__.startswith("skill_retro_core")
}
_BASE_VALUES = {name: _CANONICAL.get(name) for name in _FACADE_NAMES}
_FACADE_WRAPPERS = {}

def _sync_facade_state() -> None:
    """Propagate only values changed by unittest.mock.patch on the facade."""
    for name in _FACADE_NAMES:
        if name not in globals():
            continue
        value = globals()[name]
        desired = _BASE_VALUES.get(name) if name in _FACADE_WRAPPERS and value is _FACADE_WRAPPERS[name] else value
        for module in _CORE_MODULES:
            if module.__dict__.get(name) is not desired:
                module.__dict__[name] = desired
        if _core.__dict__.get(name) is not desired:
            _core.__dict__[name] = desired

def _make_facade_function(name):
    def _facade_call(*args, **kwargs):
        _sync_facade_state()
        return _CORE_FUNCTIONS[name](*args, **kwargs)
    _facade_call.__name__ = name
    _facade_call.__qualname__ = name
    _facade_call.__doc__ = getattr(_CORE_FUNCTIONS[name], "__doc__", None)
    return _facade_call

for _name in _FACADE_NAMES:
    _value = _BASE_VALUES[_name]
    if _name in _CORE_FUNCTIONS:
        globals()[_name] = _make_facade_function(_name)
        _FACADE_WRAPPERS[_name] = globals()[_name]
    else:
        globals()[_name] = _value

__all__ = [name for name in _FACADE_NAMES if not name.startswith("_")] + [name for name in _FACADE_NAMES if name.startswith("_")]

if __name__ == "__main__":
    raise SystemExit(main())
