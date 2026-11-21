#!/usr/bin/env python3
"""Backward-compatible entry point for personal-m2-assistant/scripts/m2-router.py.
Self-disclosing error pointers: skills/m2-dispatcher/references/bots-and-agents.md,
skills/m2-dispatcher/references/pr-dag-auto-heal.md, skills/m2-dispatcher/references/runtime-profiles.yaml.
"""
from __future__ import annotations
from pathlib import Path
import sys

_REFS = "skills/m2-dispatcher/references/bots-and-agents.md · skills/m2-dispatcher/references/pr-dag-auto-heal.md · skills/m2-dispatcher/references/runtime-profiles.yaml"
_CANONICAL_PATH = Path(__file__).resolve().parents[2] / "personal-m2-assistant" / "scripts" / "m2-router.py"
_LEGACY_FILE = globals().get("__file__")
globals()["__file__"] = str(_CANONICAL_PATH)
try:
    exec(compile(_CANONICAL_PATH.read_bytes(), str(_CANONICAL_PATH), "exec"), globals(), globals())
except SystemExit as _exc:
    if _exc.code not in (None, 0):
        sys.stderr.write(f"  Reference: {_REFS}\n")
    raise
finally:
    if _LEGACY_FILE is not None:
        globals()["__file__"] = _LEGACY_FILE
