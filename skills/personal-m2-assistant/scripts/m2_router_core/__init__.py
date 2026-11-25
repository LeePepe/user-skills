"""Cohesive domain modules for the personal M2 router."""
from __future__ import annotations

from . import _imports as _imports
from . import constants_and_paths as _constants_and_paths
from . import apply_engine as _apply_engine
from . import cleanup_and_eviction as _cleanup_and_eviction
from . import cli as _cli
from . import dispatch_apply as _dispatch_apply
from . import domain_map as _domain_map
from . import herdr_discovery as _herdr_discovery
from . import hub_format as _hub_format
from . import issue_watch as _issue_watch
from . import jev_and_intent as _jev_and_intent
from . import linear_sync as _linear_sync
from . import permission_guard as _permission_guard
from . import plan_builder as _plan_builder
from . import quota_protection as _quota_protection
from . import safety_paths as _safety_paths
from . import session_context as _session_context
from . import watchdog as _watchdog
from . import rollup_state as _rollup_state

_MODULES = [
    _constants_and_paths, _session_context, _quota_protection, _herdr_discovery,
    _domain_map, _jev_and_intent, _cleanup_and_eviction, _plan_builder,
    _apply_engine, _rollup_state, _hub_format, _linear_sync, _dispatch_apply, _safety_paths,
    _permission_guard, _issue_watch, _watchdog, _cli,
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
