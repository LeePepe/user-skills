#!/usr/bin/env python3
"""Read-only, fail-closed aidata L1-L5 contract inspector."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DESCRIPTION = "Inspect bounded aidata layers and classify metric-specific evidence without executing source files."
MAX_TEXT = 2_000_000
METRICS = {
    "skills_roi": {"l1": ("skill_names", "skills_roi", "skill_roi"), "l2": ("skill_names", "usage"), "l3": ("fact_turn", "skill_names", "token_count"), "l4": ("skill_roi", "skill_name", "tokens"), "l5": ("SkillsRoi", "skills_roi", "coverage", "freshness")},
    "reference_hits": {"l1": ("reference_hits", "reference_path", "file_path"), "l2": ("reference_hits", "reference_path", "file_path"), "l3": ("reference_hit", "reference_path", "file_path"), "l4": ("reference_hits", "file_path"), "l5": ("reference_hits", "reference_path")},
    "script_latency": {"l1": ("script_path", "duration_ms", "script_latency"), "l2": ("script_path", "duration_ms"), "l3": ("script_call", "script_path", "duration_ms", "p95_ms"), "l4": ("script_latency", "duration_ms", "p95_ms"), "l5": ("script_latency", "duration_ms", "p95_ms")},
    "eval_effect": {"l1": ("eval_case", "interception", "escape"), "l2": ("eval_case", "interception", "escape"), "l3": ("fact_gtt_event", "interception", "escape"), "l4": ("fact_gtt_event", "interception", "escape"), "l5": ("eval_effect", "interception", "escape")},
}
REQUIRED = {
    "l1": ("L1_collect/raw",), "l2": ("L2_normalize/clean",), "l3": ("L3_merge/warehouse.db", "schema/warehouse.sql"),
    "l4": ("L4_serve/queries/retro/skill_roi_diagnostics.sql",), "l5": ("L5_apps/digest/skills_roi_snapshot.py",),
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument("--aidata-root", required=True, type=Path)
    p.add_argument("--metric", action="append", dest="metrics", choices=sorted(METRICS))
    p.add_argument("--metrics", dest="metrics_csv", help="Comma-separated metric names")
    p.add_argument("--check-contract", action="store_true", help="Validate required layer/schema/SQL/Snapshot artifacts")
    p.add_argument("--json", action="store_true", dest="as_json")
    return p


def safe_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def bounded_files(root: Path) -> list[Path]:
    allowed = ("L1_collect", "L2_normalize", "L3_merge", "L4_serve", "L5_apps", "schema", "adapters")
    excluded = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
    result = []
    for base in (root / name for name in allowed):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in excluded or part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if path.is_symlink():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise OSError(f"symlink escapes aidata root: {safe_rel(root, path)}")
                continue
            if path.is_file():
                size = path.stat().st_size
                if size > MAX_TEXT:
                    continue
                if path.suffix.lower() not in {".jsonl", ".json", ".sql", ".py", ".md", ".txt"} and path.name not in {"warehouse.db"}:
                    continue
                result.append(path)
    return sorted(set(result))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def schema_text(root: Path, path: Path) -> str:
    if path.name != "warehouse.db":
        return read_text(path)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            rows = db.execute("select name, sql from sqlite_master where type in ('table','view') and sql is not null").fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise OSError(f"cannot read SQLite schema {safe_rel(root, path)}: {exc}") from exc
    return "\n".join(f"{name} {sql}" for name, sql in rows)


def has_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text, re.I)]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.aidata_root.expanduser()
    if not root.is_dir():
        print(f"aidata root is missing or not a directory: {root}", file=sys.stderr); return 2
    requested = list(args.metrics or [])
    if args.metrics_csv: requested.extend(x.strip() for x in args.metrics_csv.split(",") if x.strip())
    requested = list(dict.fromkeys(requested or METRICS))
    unknown = sorted(set(requested) - set(METRICS))
    if unknown:
        print(f"unknown metric(s): {', '.join(unknown)}", file=sys.stderr); return 2
    try:
        all_files = bounded_files(root)
        rels = {safe_rel(root, p) for p in all_files}
        layer_paths = {layer: sorted(r for r in rels if any(r == c or r.startswith(c + "/") for c in candidates)) for layer, candidates in REQUIRED.items()}
        required_paths = [root / "schema/warehouse.sql", root / "L4_serve/queries/retro/skill_roi_diagnostics.sql", root / "L5_apps/digest/skills_roi_snapshot.py"]
        required_paths += [p for p in all_files if safe_rel(root, p).startswith("L1_collect/raw/") and p.suffix == ".jsonl"]
        if args.check_contract:
            required_paths += [root / "L3_merge/warehouse.db"]
        for path in required_paths:
            if not path.is_file(): raise OSError(f"required contract file is missing: {safe_rel(root, path)}")
            if path.name != "warehouse.db": read_text(path)
        layer_text = {"l1": "\n".join(read_text(p) for p in all_files if safe_rel(root, p).startswith("L1_collect/raw/") and p.suffix == ".jsonl"), "l2": "\n".join(read_text(p) for p in all_files if safe_rel(root, p).startswith("L2_normalize/clean/") and p.suffix != ".db"), "l3": read_text(root / "schema/warehouse.sql") + "\n" + schema_text(root, root / "L3_merge/warehouse.db"), "l4": read_text(root / "L4_serve/queries/retro/skill_roi_diagnostics.sql"), "l5": read_text(root / "L5_apps/digest/skills_roi_snapshot.py")}
    except (OSError, UnicodeError) as exc:
        print(f"cannot inspect aidata contract: {exc}", file=sys.stderr); return 3
    raw_retained = bool(layer_text["l1"].strip())
    evidence = {}
    for metric in requested:
        coverage = {}
        matches = []
        for layer, text in layer_text.items():
            found = has_terms(text, METRICS[metric][layer])
            coverage[layer] = {"status": "observed" if found else "missing", "fields": found}
            if found: matches.append(f"{layer}:{','.join(found)}")
        missing_l1 = coverage["l1"]["status"] == "missing"
        complete = all(len(coverage[layer]["fields"]) == len(METRICS[metric][layer]) for layer in layer_text)
        status = "ready" if raw_retained and complete else "needs_l1_collection" if missing_l1 or not raw_retained else "needs_l2_l4_extension"
        evidence[metric] = {"status": status, "matches": matches, "coverage": coverage, "raw_retained": raw_retained}
    statuses = [item["status"] for item in evidence.values()]
    status = "needs_l1_collection" if "needs_l1_collection" in statuses else "needs_l2_l4_extension" if "needs_l2_l4_extension" in statuses else "ready"
    reason = {"ready": "all requested metrics have observed metric-specific L1-L5 evidence", "needs_l2_l4_extension": "retained L1 evidence exists but downstream metric fields are incomplete", "needs_l1_collection": "required metric-specific L1 evidence is absent or empty"}[status]
    result = {"aidata_root": str(root), "status": status, "reason": reason, "layers": layer_paths, "metrics": evidence}
    if args.check_contract and any(not layer_paths[layer] for layer in REQUIRED):
        result["contract_error"] = "required L1-L5 source/schema/SQL/Snapshot evidence is incomplete"
        if args.as_json: print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else: print(result["contract_error"], file=sys.stderr)
        return 3
    if args.as_json: print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{status}: {reason}")
        for layer, matches in layer_paths.items(): print(f"{layer}: {len(matches)} evidence file(s)")
    return 0


if __name__ == "__main__": raise SystemExit(main())
