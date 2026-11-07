#!/usr/bin/env python3
"""Shared entrypoint for the canonical team-lead Linear synchronizer."""

from __future__ import annotations

import runpy
from pathlib import Path


CANONICAL = (
    Path(__file__).resolve().parents[2]
    / "team-lead"
    / "scripts"
    / "linear-sync.py"
)


if __name__ == "__main__":
    runpy.run_path(str(CANONICAL), run_name="__main__")
