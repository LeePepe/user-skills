"""Cohesive implementation modules for the agent-retro compatibility facade."""
from . import base, metrics, contracts, horizon, analysis, commands, cli

_MODULES = (base, metrics, contracts, horizon, analysis, commands, cli)
for _module in _MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__"):
            globals()[_name] = _value

__all__ = tuple(name for name in globals() if not name.startswith("__"))
