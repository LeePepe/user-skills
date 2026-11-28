"""Domain modules for the skill-retro compatibility facade."""
from __future__ import annotations
from . import brief_emitter as _brief_emitter
from . import cli as _cli
from . import diagnose_engine as _diagnose_engine
from . import fs_guard as _fs_guard
from . import inventory_scan as _inventory_scan
from . import reality_check as _reality_check
from . import receipts as _receipts
from . import retro_engine as _retro_engine
from . import roi_diagnostics as _roi_diagnostics
from . import roi_support as _roi_support
from . import trace_ingest as _trace_ingest
from . import triage_jev as _triage_jev
from . import uptake_eval as _uptake_eval
from . import verify_findings as _verify_findings
_MODULES = [_brief_emitter, _cli, _diagnose_engine, _fs_guard, _inventory_scan, _reality_check, _receipts, _retro_engine, _roi_diagnostics, _roi_support, _trace_ingest, _triage_jev, _uptake_eval, _verify_findings]
_SYMBOLS = {}
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            _SYMBOLS[_name] = _value
for _module in _MODULES:
    _module.__dict__.update(_SYMBOLS)
globals().update(_SYMBOLS)
_ORIGINAL_SYMBOLS = dict(_SYMBOLS)
route_retro_Candidate = route_retro_candidate
globals()["route_retro_Candidate"] = route_retro_Candidate
for _module in _MODULES:
    _module.__dict__["route_retro_Candidate"] = route_retro_Candidate
__all__ = [name for name in globals() if not name.startswith("__")]
