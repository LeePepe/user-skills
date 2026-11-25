"""Cohesive domain modules for Project Root Tracker."""
from __future__ import annotations

import inspect

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
_FACADE_REGISTRIES: list[tuple[dict[str, object], dict[str, object]]] = []
_FACADE_NAMES = tuple(name for name in _ORIGINAL_SYMBOLS if not name.startswith("__"))


def _sync_facade_state() -> None:
    """Propagate unittest.mock.patch changes on any loaded facade into every core module."""
    for name in _FACADE_NAMES:
        base_val = _ORIGINAL_SYMBOLS.get(name)
        desired = base_val
        for source, wrappers in _FACADE_REGISTRIES:
            if name not in source:
                continue
            val = source[name]
            cand = base_val if name in wrappers and val is wrappers[name] else val
            if cand is not base_val:
                desired = cand
        for module in _MODULES:
            if module.__dict__.get(name) is not desired:
                module.__dict__[name] = desired
        if globals().get(name) is not desired:
            globals()[name] = desired


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
    if issubclass(cls, BaseException) or getattr(cls, "_facade_instrumented", False):
        return cls
    for attr, member in list(cls.__dict__.items()):
        if attr.startswith("__") and attr != "__init__":
            continue
        if inspect.isfunction(member):
            setattr(cls, attr, _wrap_callable(member))
        elif isinstance(member, staticmethod) and inspect.isfunction(member.__func__):
            setattr(cls, attr, staticmethod(_wrap_callable(member.__func__)))
        elif isinstance(member, classmethod) and inspect.isfunction(member.__func__):
            setattr(cls, attr, classmethod(_wrap_callable(member.__func__)))
    cls._facade_instrumented = True
    return cls


def bind_facade(facade_globals: dict[str, object]) -> list[str]:
    """Populate a facade namespace and register it for bidirectional mock patch syncing."""
    wrappers: dict[str, object] = {}
    for name in _FACADE_NAMES:
        val = _ORIGINAL_SYMBOLS[name]
        is_core = getattr(val, "__module__", "").startswith("root_tracker_core")
        if is_core and inspect.isfunction(val):
            facade_globals[name] = _wrap_callable(val)
            wrappers[name] = facade_globals[name]
        elif is_core and inspect.isclass(val):
            facade_globals[name] = _instrument_class(val)
        else:
            facade_globals[name] = val
    _FACADE_REGISTRIES.append((facade_globals, wrappers))
    facade_globals["_CORE_MODULES"] = list(_MODULES)
    facade_globals["_BASE_VALUES"] = dict(_ORIGINAL_SYMBOLS)
    facade_globals["_FACADE_WRAPPERS"] = wrappers
    facade_globals["_sync_facade_state"] = _sync_facade_state
    return [name for name in _FACADE_NAMES if not name.startswith("_")] + [name for name in _FACADE_NAMES if name.startswith("_")]


__all__ = [name for name in _FACADE_NAMES]
