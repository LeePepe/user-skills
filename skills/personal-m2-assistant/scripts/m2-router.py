#!/usr/bin/env python3
"""Thin, backward-compatible CLI facade for the unified M2 ingress."""
from __future__ import annotations

import sys
from pathlib import Path

# Keep direct execution and importlib-loaded legacy wrappers on the same path.
_ROUTER_DIR = Path(__file__).resolve().parent
if str(_ROUTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ROUTER_DIR))

from m2_router_core import load_into

load_into(globals())


if __name__ == "__main__":
    raise SystemExit(main())
