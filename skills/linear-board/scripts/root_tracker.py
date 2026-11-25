#!/usr/bin/env python3
"""Project Root Tracker: offline-first collection, one-way Linear sync, and HTML war-room."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import root_tracker_core as _core

_CORE_MODULES = list(getattr(_core, "_MODULES", ()))
_FACADE_NAMES = tuple(
    name for name in vars(_core) if not name.startswith("__") and name not in {"_MODULES", "_SYMBOLS", "_ORIGINAL_SYMBOLS"}
)
_CANONICAL = getattr(_core, "_ORIGINAL_SYMBOLS", vars(_core))
_CORE_CALLABLES = {
    name: val for name, val in _CANONICAL.items()
    if name in _FACADE_NAMES and (inspect.isfunction(val) or inspect.isclass(val)) and getattr(val, "__module__", "").startswith("root_tracker_core")
}
_BASE_VALUES = {name: _CANONICAL.get(name) for name in _FACADE_NAMES}
_FACADE_WRAPPERS: dict[str, object] = {}


def _sync_facade_state() -> None:
    """Propagate unittest.mock.patch changes on facade globals into every core module."""
    for name in _FACADE_NAMES:
        if name not in globals():
            continue
        val = globals()[name]
        desired = _BASE_VALUES.get(name) if name in _FACADE_WRAPPERS and val is _FACADE_WRAPPERS[name] else val
        for module in _CORE_MODULES:
            if module.__dict__.get(name) is not desired:
                module.__dict__[name] = desired
        if _core.__dict__.get(name) is not desired:
            _core.__dict__[name] = desired


def _wrap_callable(fn):
    raw_fn = getattr(fn, "_facade_raw_fn", fn)

    def _wrapped(*args, **kwargs):
        _sync_facade_state()
        return raw_fn(*args, **kwargs)

    _wrapped._facade_raw_fn = raw_fn
    _wrapped.__name__ = getattr(raw_fn, "__name__", "_wrapped")
    _wrapped.__qualname__ = getattr(raw_fn, "__qualname__", _wrapped.__name__)
    _wrapped.__doc__ = getattr(raw_fn, "__doc__", None)
    return _wrapped


def _instrument_class(cls) -> type:
    if issubclass(cls, BaseException):
        return cls
    ns: dict[str, object] = {"__module__": __name__, "__doc__": cls.__doc__}
    for attr, member in cls.__dict__.items():
        if attr.startswith("__") and attr != "__init__":
            continue
        if inspect.isfunction(member):
            ns[attr] = _wrap_callable(member)
        elif isinstance(member, staticmethod) and inspect.isfunction(member.__func__):
            ns[attr] = staticmethod(_wrap_callable(member.__func__))
        elif isinstance(member, classmethod) and inspect.isfunction(member.__func__):
            ns[attr] = classmethod(_wrap_callable(member.__func__))
    return type(cls.__name__, (cls,), ns)


for _name in _FACADE_NAMES:
    _value = _BASE_VALUES[_name]
    if _name in _CORE_CALLABLES and inspect.isfunction(_value):
        globals()[_name] = _wrap_callable(_value)
        _FACADE_WRAPPERS[_name] = globals()[_name]
    elif _name in _CORE_CALLABLES and inspect.isclass(_value):
        globals()[_name] = _instrument_class(_value)
        _FACADE_WRAPPERS[_name] = globals()[_name]
    else:
        globals()[_name] = _value

__all__ = [name for name in _FACADE_NAMES if not name.startswith("_")] + [name for name in _FACADE_NAMES if name.startswith("_")]

if __name__ == "__main__":
    raise SystemExit(main())
