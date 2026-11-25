"""Cohesive AST partitions for the skill-retro compatibility facade."""
from __future__ import annotations
from . import core_00 as _core_00
from . import core_01 as _core_01
from . import core_02 as _core_02
from . import core_03 as _core_03
from . import core_04 as _core_04
from . import core_05 as _core_05
from . import core_06 as _core_06
from . import core_07 as _core_07
from . import core_08 as _core_08
from . import core_09 as _core_09
from . import core_10 as _core_10
from . import core_11 as _core_11
_MODULES = [_core_00, _core_01, _core_02, _core_03, _core_04, _core_05, _core_06, _core_07, _core_08, _core_09, _core_10, _core_11]
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
