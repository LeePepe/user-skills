#!/usr/bin/env python3
"""Thin CLI and backward-compatible facade for trace backfill."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from trace_backfill_core import _CORE_MODULES, _ORIGINAL_SYMBOLS, _SYMBOLS

_FACADE_NAMES = tuple(_SYMBOLS)
_CANONICAL = _ORIGINAL_SYMBOLS
_CORE_FUNCTIONS = {
    name: value for name, value in _CANONICAL.items()
    if inspect.isfunction(value) and value.__module__.startswith("trace_backfill_core")
}
_BASE_VALUES = {name: _CANONICAL.get(name) for name in _FACADE_NAMES}
_FACADE_WRAPPERS = {}


def _sync_facade_state() -> None:
    """Propagate values changed by unittest.mock.patch.object on this facade."""
    for name in _FACADE_NAMES:
        if name not in globals():
            continue
        value = globals()[name]
        desired = _BASE_VALUES.get(name)
        if name in _FACADE_WRAPPERS and value is _FACADE_WRAPPERS[name]:
            desired = _BASE_VALUES[name]
        else:
            desired = value
        for module in _CORE_MODULES:
            module.__dict__[name] = desired


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

__all__ = list(_FACADE_NAMES)

if __name__ == "__main__":
    raise SystemExit(main())
