#!/usr/bin/env python3
"""Read-only, fail-closed aidata L1-L5 contract inspector."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DESCRIPTION = "Inspect aidata layers and classify metric evidence without executing source files."
METRICS = {
    "skills_roi": ("skill", "roi", "skills_roi", "skill_roi"),
    "reference_hits": ("reference", "references", "file_hit", "path"),
    "script_latency": ("script", "latency", "p95", "duration"),
    "eval_effect": ("eval", "interception", "escape", "fact_gtt_event"),
}
REQUIRED = {
    "l1": ("L1_collect/raw", "L1_raw", "raw"),
    "l2": ("L2_normalize/clean", "L2_clean", "clean"),
    "l3": ("L3_merge/warehouse.db", "warehouse.db", "schema"),
    "l4": ("L4_serve/queries/retro/skill_roi_diagnostics.sql", "skill_roi_diagnostics.sql"),
    "l5": ("L5_apps/digest/skills_roi_snapshot.py", "skills_roi_snapshot.py"),
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument("--aidata-root", required=True, type=Path)
    p.add_argument("--metric", action="append", dest="metrics", choices=sorted(METRICS))
    p.add_argument("--metrics", dest="metrics_csv", help="Comma-separated metric names")
    p.add_argument("--check-contract", action="store_true", help="Validate the discovered layer contract")
    p.add_argument("--json", action="store_true", dest="as_json")
    return p


def files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.stat().st_size <= 2_000_000]


def text_for(paths: list[Path]) -> str:
    chunks = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError):
            continue
    return "\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.aidata_root.expanduser()
    if not root.is_dir():
        print(f"aidata root is missing or not a directory: {root}", file=sys.stderr)
        return 2
    try:
        all_files = files(root)
    except OSError as exc:
        print(f"cannot inspect aidata root: {exc}", file=sys.stderr)
        return 2
    rels = {str(p.relative_to(root)) for p in all_files}
    names = {p.name for p in all_files}
    layer_paths = {}
    for layer, candidates in REQUIRED.items():
        matches = sorted(r for r in rels if any(c in r or r.endswith(c) for c in candidates))
        layer_paths[layer] = matches
    requested = list(args.metrics or [])
    if args.metrics_csv:
        requested.extend(x.strip() for x in args.metrics_csv.split(",") if x.strip())
    requested = list(dict.fromkeys(requested or METRICS))
    unknown = sorted(set(requested) - set(METRICS))
    if unknown:
        print(f"unknown metric(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    source_text = text_for(all_files)
    modeled_files = [p for p in all_files if any(layer in str(p.relative_to(root)) for layer in ("L3_merge", "L4_serve", "L5_apps"))]
    modeled_text = text_for(modeled_files)
    raw_files = [p for p in all_files if "L1_collect/raw" in str(p.relative_to(root)) or "/raw/" in str(p)]
    raw_content = text_for(raw_files)
    evidence = {}
    for metric in requested:
        terms = METRICS[metric]
        hits = sorted({str(p.relative_to(root)) for p in all_files if any(t.lower() in p.name.lower() for t in terms)})
        hits += [f"content:{t}" for t in terms if re.search(re.escape(t), source_text, re.I)]
        modeled_hits = [term for term in terms if re.search(re.escape(term), modeled_text, re.I)]
        evidence[metric] = {"matches": list(dict.fromkeys(hits)), "modeled_matches": modeled_hits, "modeled": bool(modeled_hits) and all(layer_paths[layer] for layer in ("l3", "l4", "l5"))}
    all_layers = all(layer_paths.values())
    raw_retained = bool(raw_files) and bool(raw_content.strip())
    modeled = all_layers and all(item["modeled"] for item in evidence.values())
    if modeled:
        status = "ready"
        reason = "all requested metrics have source, model, serve, and snapshot evidence"
    elif not raw_retained:
        status = "needs_l1_collection"
        reason = "no readable retained L1 raw fixture content was found"
    else:
        status = "needs_l2_l4_extension"
        reason = "retained L1 content exists but requested metric evidence is not fully modeled"
    result = {"aidata_root": str(root), "status": status, "reason": reason, "layers": layer_paths, "metrics": evidence}
    if args.check_contract and not all_layers:
        result["contract_error"] = "required L1-L5 source/schema/SQL/Snapshot evidence is incomplete"
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(result["contract_error"], file=sys.stderr)
        return 3
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{status}: {reason}")
        for layer, matches in layer_paths.items():
            print(f"{layer}: {len(matches)} evidence file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
