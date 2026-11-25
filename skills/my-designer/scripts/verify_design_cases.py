#!/usr/bin/env python3
"""Stable CLI facade for the modular my-designer design-case verifier."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from design_cases_core import audit, cli, route, shared  # noqa: E402

# Re-export the historical surface, including private helpers used by tests and adapters.
for _module in (shared, audit, route, cli):
    for _name in dir(_module):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_module, _name))


def _sync_state() -> None:
    """Keep patched facade attributes visible to the implementation modules."""
    for _name in ("ROOT", "EVIDENCE", "JEV", "DEFAULT_CASES", "DEFAULT_TAXONOMY", "TAXONOMY", "DRAWER_GUIDANCE"):
        if _name in globals():
            for _module in (shared, audit, route, cli):
                if hasattr(_module, _name):
                    setattr(_module, _name, globals()[_name])
    # A few historical tests patch this helper on the facade before invoking flow-map.
    if "_read_json" in globals():
        route._read_json = globals()["_read_json"]
        cli._read_json = globals()["_read_json"]


def _load_taxonomy(path=None):
    _sync_state()
    return shared._load_taxonomy(path)


def main(argv: list[str] | None = None) -> int:
    _sync_state()
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
