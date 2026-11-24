#!/usr/bin/env python3
"""Shared entrypoint for the canonical team-lead Linear synchronizer."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path
CANONICAL = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "linear-sync.py"
_DROP, _PROJ = {"--milestone", "--project-milestone", "--parent-issue", "--parent", "--parent-id"}, {"agent workflow": "user-skills", "personal project": "Personal Products & Home", "bing": "Work Business"}
_VALS = {"--title", "--description", "--team", "--state", "--label", "--source", "--dedup-key", "--repo", "--offline-snapshot", "--handoff-dir", "--handoff", "--dir", "--issue", "--issue-id", "--id", "--event-note", "--query", "--limit", "--priority", "--assignee", "--delegate", "--add-label", "--body", "--retention-days", "--max-active-issues", "--header", "--client-id", "--client-secret", "--port", "--mode"}
_FLAGS = _VALS | {"-h", "--help", "--json", "--quiet", "--record-observe", "--create-as-user", "--key-stdin", "--agent-scopes", "--force"}
def _adapt(argv: list[str], mod: object) -> list[str]:
    argv = getattr(mod, "_normalize_argv")(argv); drop = _DROP | ({"--title", "--description"} if argv and argv[0] in {"ensure-goal", "ensure", "sync-ledger", "sync"} else set()); out, i = [], 0
    while i < len(argv):
        t = argv[i]; nx = argv[i + 1] if i + 1 < len(argv) else None
        if any(t == f or t.startswith(f + "=") for f in drop): i += 2 if ("=" not in t and nx is not None and not nx.startswith("-")) else 1
        elif t == "--project" and nx is not None and nx not in _FLAGS: out += [t, _PROJ.get(nx.strip().casefold(), nx)]; i += 2
        elif t.startswith("--project="): v = t.split("=", 1)[1]; out += ["--project=" + _PROJ.get(v.strip().casefold(), v)]; i += 1
        elif t in _VALS and nx is not None and nx.split("=", 1)[0] not in _FLAGS: out += [f"{t}={nx}"] if nx.startswith("-") else [t, nx]; i += 2
        else: out.append(t); i += 1
    return out
if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("_canonical_ls", CANONICAL); assert spec and spec.loader; mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    try: rc = mod.main(_adapt(sys.argv[1:], mod))
    except SystemExit as e: rc = int(e.code) if isinstance(e.code, int) else 2
    if rc == 2: sys.stderr.write("  Board Reference: skills/linear-board/references/daily-brief-and-bot.md\n")
    raise SystemExit(rc)
