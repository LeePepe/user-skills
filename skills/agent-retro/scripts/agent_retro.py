#!/usr/bin/env python3
"""Backward-compatible CLI facade for the modular agent-retro implementation."""
from __future__ import annotations

from pathlib import Path
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_retro_core import *  # noqa: F401,F403

if __name__ == "__main__":
    main()
