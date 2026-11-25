#!/usr/bin/env python3
"""Thin, backward-compatible CLI facade for the unified M2 ingress."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROUTER_DIR = Path(__file__).resolve().parent
if str(_ROUTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ROUTER_DIR))

import m2_router_core as _core

_CORE_MODULES = list(getattr(_core, "_MODULES", ()))
_FACADE_NAMES = tuple(
    name for name in vars(_core)
    if not name.startswith("__")
    and name not in {"_MODULES", "_SYMBOLS", "_ORIGINAL_SYMBOLS"}
)
_CANONICAL = getattr(_core, "_ORIGINAL_SYMBOLS", vars(_core))
_CORE_FUNCTIONS = {
    name: value for name, value in _CANONICAL.items()
    if name in _FACADE_NAMES and inspect.isfunction(value)
    and value.__module__.startswith("m2_router_core")
}
_BASE_VALUES = {name: _CANONICAL.get(name) for name in _FACADE_NAMES}
_FACADE_WRAPPERS = {}


def _sync_facade_state() -> None:
    """Propagate unittest.mock.patch changes to every domain module."""
    for name in _FACADE_NAMES:
        if name not in globals():
            continue
        value = globals()[name]
        desired = (
            _BASE_VALUES.get(name)
            if name in _FACADE_WRAPPERS and value is _FACADE_WRAPPERS[name]
            else value
        )
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

__all__ = [name for name in _FACADE_NAMES]

if __name__ == "__main__":
    raise SystemExit(main())
