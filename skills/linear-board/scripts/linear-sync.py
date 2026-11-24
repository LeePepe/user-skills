#!/usr/bin/env python3
"""Shared entrypoint for the canonical team-lead Linear synchronizer."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path
CANONICAL = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "linear-sync.py"
_DROP = {"--milestone", "--project-milestone", "--parent-issue", "--parent", "--parent-id"}
_PROJ = {"agent workflow": "user-skills", "personal project": "Personal Products & Home", "bing": "Work Business"}
def _adapt(argv: list[str]) -> list[str]:
    sub = next((t for t in argv if t in {"idea", "ensure-goal", "ensure", "sync-ledger", "sync", "list-issues", "issues", "get-issue", "update-issue", "comment", "archive-completed", "auth"}), "")
    drop = _DROP | ({"--title", "--description"} if sub in {"ensure-goal", "ensure", "sync-ledger", "sync"} else set())
    out, i = [], 0
    while i < len(argv):
        t = argv[i]
        if any(t == f or t.startswith(f + "=") for f in drop): i += 2 if ("=" not in t and i + 1 < len(argv)) else 1
        elif t == "--project" and i + 1 < len(argv): out += [t, _PROJ.get(argv[i + 1].strip().casefold(), argv[i + 1])]; i += 2
        elif t.startswith("--project="): v = t.split("=", 1)[1]; out += ["--project=" + _PROJ.get(v.strip().casefold(), v)]; i += 1
        else: out.append(t); i += 1
    return out
if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("_canonical_ls", CANONICAL); assert spec and spec.loader; mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    try: rc = mod.main(_adapt(sys.argv[1:]))
    except SystemExit as e: rc = int(e.code) if isinstance(e.code, int) else 2
    if rc == 2: sys.stderr.write("  Board Reference: skills/linear-board/references/daily-brief-and-bot.md\n")
    raise SystemExit(rc)
