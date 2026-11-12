#!/usr/bin/env python3
"""Backward-compatible entry point for the personal M2 assistant router.

The implementation is canonical under ``personal-m2-assistant``. Executing it
in this module's namespace preserves legacy imports and monkey-patching.
"""

from __future__ import annotations

from pathlib import Path


_CANONICAL_PATH = (
    Path(__file__).resolve().parents[2]
    / "personal-m2-assistant"
    / "scripts"
    / "m2-router.py"
)
_LEGACY_FILE = globals().get("__file__")
globals()["__file__"] = str(_CANONICAL_PATH)
try:
    exec(compile(_CANONICAL_PATH.read_bytes(), str(_CANONICAL_PATH), "exec"), globals(), globals())
finally:
    if _LEGACY_FILE is not None:
        globals()["__file__"] = _LEGACY_FILE
