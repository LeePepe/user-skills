#!/usr/bin/env python3
"""Backward-compatible CLI facade for the modular digest_vault implementation."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
import sys
import threading

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import digest_vault_core as _core
from digest_vault_core import *  # noqa: F401,F403
from digest_vault_core import (  # noqa: F401
    _append_valid_proposal, _build_parser, _cst_date_str, _cst_day_str,
    _daily_description_keys, _daily_digest_markdown, _existing_vault_corpus,
    _inspect_vault_coverage, _learning_brief, _legacy_automated_learning_brief,
    _linear_transport_from_repo, _load_helper_module, _load_issue_brief_module,
    _load_learning_state_registry, _load_topic_ledger,
    _merge_daily_digest_description, _normalize_source_uri,
    _reconcile_collected_tools_entities, _reconcile_proposals_history,
    _rerank_candidates_r09, _save_learning_state_registry, _save_topic_ledger,
    _scan_collected_tools_candidates, _scan_git_tech_stack_candidates,
    _select_balanced_by_track, _topic_exclusion_reason,
    _upsert_linear_daily_issue, _vault_coverage,
)

_CORE_MODULES = tuple(getattr(_core, "_MODULES", ()))
_CANONICAL = dict(getattr(_core, "_ORIGINAL_SYMBOLS", {}))
_CORE_FUNCTIONS = {
    name: fn
    for name, fn in _CANONICAL.items()
    if inspect.isfunction(fn) and fn.__module__.startswith("digest_vault_core")
}
_FACADE_WRAPPERS: dict[str, object] = {}
_BRIDGE_LOCK = getattr(_core, "_FACADE_BRIDGE_LOCK", None) or threading.RLock()
setattr(_core, "_FACADE_BRIDGE_LOCK", _BRIDGE_LOCK)


@contextmanager
def _scoped_facade_state():
    with _BRIDGE_LOCK:
        targets = (*_CORE_MODULES, _core)
        saved: list[tuple[dict[str, object], str, bool, object]] = []
        for name, base_val in _CANONICAL.items():
            if name not in globals():
                continue
            current = globals()[name]
            desired = base_val if _FACADE_WRAPPERS.get(name) is current else current
            for mod in targets:
                ns = mod.__dict__
                if ns.get(name) is not desired:
                    saved.append((ns, name, name in ns, ns.get(name)))
                    ns[name] = desired
        try:
            yield
        finally:
            for ns, name, had_key, prior_val in reversed(saved):
                if had_key:
                    ns[name] = prior_val
                else:
                    ns.pop(name, None)


def _wrap_core_function(name: str):
    target = _CORE_FUNCTIONS[name]

    def _wrapped(*args, **kwargs):
        with _scoped_facade_state():
            return target(*args, **kwargs)

    _wrapped.__name__ = name
    _wrapped.__qualname__ = name
    _wrapped.__doc__ = target.__doc__
    return _wrapped


for _sym_name, _sym_val in _CANONICAL.items():
    if _sym_name in _CORE_FUNCTIONS:
        _FACADE_WRAPPERS[_sym_name] = _wrap_core_function(_sym_name)
        globals()[_sym_name] = _FACADE_WRAPPERS[_sym_name]
    else:
        globals()[_sym_name] = _sym_val

if __name__ == "__main__":
    raise SystemExit(main())
