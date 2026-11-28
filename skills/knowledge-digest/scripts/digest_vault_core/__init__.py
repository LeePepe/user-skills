"""Cohesive domain modules for the digest_vault backward-compatible facade."""

from __future__ import annotations

from . import cards_and_blog as _cards_and_blog
from . import cli as _cli
from . import common as _common
from . import daily_digest as _daily_digest
from . import discovery as _discovery
from . import events_and_reconcile as _events_and_reconcile
from . import ledger_and_scan as _ledger_and_scan
from . import rag_search as _rag_search

_MODULES = (
    _common,
    _cards_and_blog,
    _ledger_and_scan,
    _daily_digest,
    _discovery,
    _events_and_reconcile,
    _rag_search,
    _cli,
)
_SYMBOLS: dict[str, object] = {}
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            _SYMBOLS[_name] = _value

for _module in _MODULES:
    _module.__dict__.update(_SYMBOLS)

globals().update(_SYMBOLS)
_ORIGINAL_SYMBOLS = dict(_SYMBOLS)

__all__ = [name for name in _SYMBOLS if not name.startswith("_")] + [
    name for name in _SYMBOLS if name.startswith("_")
]
