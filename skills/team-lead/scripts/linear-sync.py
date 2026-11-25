#!/usr/bin/env python3
"""Backward-compatible CLI facade for team-lead Linear synchronization."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import linear_sync_core as _core

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
    and value.__module__.startswith("linear_sync_core")
}
_CORE_METHODS: dict[tuple[type, str], object] = {}
for _cls in _CANONICAL.values():
    if inspect.isclass(_cls) and _cls.__module__.startswith("linear_sync_core"):
        for _mname, _mval in list(_cls.__dict__.items()):
            if inspect.isfunction(_mval):
                _CORE_METHODS[(_cls, _mname)] = _mval

_BASE_VALUES = {name: _CANONICAL.get(name) for name in _FACADE_NAMES}
_FACADE_WRAPPERS: dict[str, object] = {}


def _sync_facade_state() -> None:
    """Propagate unittest.mock.patch changes on the facade to every domain module."""
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


def _wrap_callable(fn):
    def _wrapped(*args, **kwargs):
        _sync_facade_state()
        return fn(*args, **kwargs)
    _wrapped.__name__ = fn.__name__
    _wrapped.__qualname__ = fn.__qualname__
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


for (_cls, _mname), _mfn in _CORE_METHODS.items():
    setattr(_cls, _mname, _wrap_callable(_mfn))

for _name in _FACADE_NAMES:
    _value = _BASE_VALUES[_name]
    if _name in _CORE_FUNCTIONS:
        globals()[_name] = _wrap_callable(_CORE_FUNCTIONS[_name])
        _FACADE_WRAPPERS[_name] = globals()[_name]
    else:
        globals()[_name] = _value

__all__ = list(_FACADE_NAMES)

if __name__ == "__main__":
    raise SystemExit(main())
