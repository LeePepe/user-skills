#!/usr/bin/env python3
"""Project Root Tracker: offline-first collection, one-way Linear sync, and HTML war-room."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import root_tracker_core as _core

__all__ = _core.bind_facade(globals())

if __name__ == "__main__":
    raise SystemExit(main())
