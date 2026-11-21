#!/usr/bin/env python3
"""Backward-compatible wrapper delegating to standalone skills/skill-retro/scripts/skill_retro.py."""

from __future__ import annotations

from pathlib import Path

_CANONICAL_PATH = (
    Path(__file__).resolve().parents[2]
    / "skill-retro"
    / "scripts"
    / "skill_retro.py"
)
_LEGACY_FILE = globals().get("__file__")
globals()["__file__"] = str(_CANONICAL_PATH)
try:
    exec(compile(_CANONICAL_PATH.read_bytes(), str(_CANONICAL_PATH), "exec"), globals(), globals())
finally:
    if _LEGACY_FILE is not None:
        globals()["__file__"] = _LEGACY_FILE
