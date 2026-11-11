#!/usr/bin/env python3
"""Block agent commits on main/master in a primary checkout."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def main() -> int:
    try:
        git_dir = Path(git_value("rev-parse", "--git-dir")).resolve()
        common_dir = Path(git_value("rev-parse", "--git-common-dir")).resolve()
        branch = git_value("symbolic-ref", "--short", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return 0
    if git_dir != common_dir or branch not in {"main", "master"}:
        return 0
    if os.environ.get("ALLOW_PRIMARY_COMMIT") == "1":
        return 0
    if not (
        os.environ.get("HERDR_ENV") == "1"
        or bool(os.environ.get("GTT_AGENT_ROLE"))
        or os.environ.get("GTT_BLOCK_PRIMARY_COMMIT") == "1"
    ):
        return 0
    try:
        repo = git_value("rev-parse", "--show-toplevel")
    except (OSError, subprocess.CalledProcessError):
        repo = "<repository>"
    print(
        "Blocked agent commit in primary checkout. Use a singleton worktree: "
        f"python3 ~/Development/user-skills/skills/team-lead/scripts/goal-queue.py ensure-worktree --repo {repo} --goal <goal>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
