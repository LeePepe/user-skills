"""Domain modules for the my-designer compatibility facade."""
from . import shared as _shared
from . import audit as _audit
from . import route as _route
from . import vision_audit as _vision_audit
from . import cli as _cli

_MODULES = [_shared, _audit, _route, _vision_audit, _cli]
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
