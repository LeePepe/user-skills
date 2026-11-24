#!/usr/bin/env python3
"""Read-only, fail-closed aidata L1-L5 contract inspector."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

DESCRIPTION = "Inspect bounded aidata layers and classify metric-specific evidence without executing source files."
LINEAGE_POINTER = "skills/aidash-metrics/references/aidata-l1-l5-lineage.md"
MAX_TEXT = 2_000_000
MAX_RECORDS = 128
MAX_JSON_BYTES = 15_000
SESSION_SOURCES = {"claude_jsonl", "copilot_cli", "claude_job", "state_db", "hermes_messages", "hermes_tools", "raven", "gecko"}
EVENT_SOURCES = SESSION_SOURCES | {"gtt_ledger", "gtt_receipt", "bot_tl", "aidash_events"}
# Each tuple is one required dimension; its members are alternatives, not extra obligations.
ATTRIBUTION = ("skill_names", "attributionskill", "attribution_skill")
INPUT = ("usage.input_tokens", "input_tokens")
OUTPUT = ("usage.output_tokens", "output_tokens")
METRICS = {
    "skills_roi": {
        "l1": (ATTRIBUTION, INPUT, OUTPUT),
        "l2": (ATTRIBUTION, ("input_tokens",), ("output_tokens",)),
        "l3": (("fact_turn",), ATTRIBUTION, ("input_tokens",), ("output_tokens",)),
        "l4": (("fact_turn",), ("skill", "skill_name"), ("tokens", "session_tokens", "avg_context_tokens")),
        "l5": (("skills_daily", "skills_roi"), ("coverage_status", "coverage"), ("source_as_of", "freshness")),
    },
    "reference_hits": {
        "l1": (("reference_path", "reference_hits.reference_path"),),
        "l2": (("reference_path",),),
        "l3": (("reference_hit", "reference_hits"), ("reference_path", "file_path")),
        "l4": (("reference_hits",), ("reference_path", "file_path")),
        "l5": (("reference_hits",), ("reference_path",)),
    },
    "script_latency": {
        "l1": (("script_path",), ("duration_ms",)),
        "l2": (("script_path",), ("duration_ms",)),
        "l3": (("script_call",), ("script_path",), ("duration_ms",)),
        "l4": (("script_latency",), ("duration_ms",), ("p95_ms",)),
        "l5": (("script_latency",), ("duration_ms",), ("p95_ms",)),
    },
    "eval_effect": {
        "l1": (("eval_case",), ("interception",), ("escape",)),
        "l2": (("eval_case",), ("interception",), ("escape",)),
        "l3": (("fact_gtt_event",), ("eval_case",), ("interception",), ("escape",)),
        "l4": (("eval_effect",), ("interception",), ("escape",)),
        "l5": (("eval_effect",), ("interception",), ("escape",)),
    },
}
CANONICAL = {
    "l3": "schema/warehouse.sql",
    "l4": "L4_serve/queries/retro/skill_roi_diagnostics.sql",
    "l5": "L5_apps/digest/skills_roi_snapshot.py",
}


class LineageArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        one_line = " ".join(str(message).splitlines())
        self._print_message(f"argument error: {one_line}\nSee {LINEAGE_POINTER}\n", sys.stderr)
        raise SystemExit(2)


def parser() -> argparse.ArgumentParser:
    p = LineageArgumentParser(description=DESCRIPTION)
    p.add_argument("--aidata-root", "--root", "--dir", "--aidata-dir", dest="aidata_root", required=True, type=Path)
    p.add_argument("--metric", action="append", dest="metrics", help="Metric name (repeatable)")
    p.add_argument("--metrics", dest="metrics_csv", help="Comma-separated metric names")
    p.add_argument("--summary", action="store_true", help="Use the bounded JSON summary (the default)")
    p.add_argument("--full", action="store_true", help="Include unlimited raw lists in JSON output")
    p.add_argument("--check-contract", action="store_true", help="Validate required collector/clean DDL/schema/SQL/Snapshot sources; generated data is optional")
    p.add_argument("--json", action="store_true", dest="as_json")
    return p


def safe_rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def bounded_files(root: Path) -> list[Path]:
    result = []
    for name in ("L1_collect", "L2_normalize", "L3_merge", "L4_serve", "L5_apps", "schema", "adapters"):
        base = root / name
        if base.is_symlink():
            raise OSError(f"symlinked contract directory: {name}")
        if not base.exists():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            parent = Path(directory)
            for entry in dirs + files:
                path = parent / entry
                if path.is_symlink() and not path.resolve().is_relative_to(root):
                    raise OSError(f"symlink escapes aidata root: {safe_rel(root, path)}")
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules"} and not (parent / d).is_symlink()]
            for filename in files:
                path = parent / filename
                if filename.startswith(".") or path.is_symlink():
                    continue
                rel = safe_rel(root, path)
                if (rel.startswith("L1_collect/raw/") and path.suffix == ".jsonl"
                        or rel.startswith("L2_normalize/clean/") and path.suffix == ".db"
                        or rel == "L3_merge/warehouse.db" or rel in CANONICAL.values()
                        or path.parent == root / "adapters" and path.suffix == ".py"):
                    result.append(path)
    return sorted(result)


def read_text(root: Path, path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"required contract file is missing or symlinked: {safe_rel(root, path)}")
    if path.stat().st_size > MAX_TEXT:
        raise OSError(f"contract file exceeds text limit: {safe_rel(root, path)}")
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise OSError(f"cannot read contract file: {safe_rel(root, path)}") from exc


def sql_fields(text: str) -> set[str]:
    # Ignore comments and values; quoted SQL identifiers remain identifiers.
    text = re.sub(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'", " ", text, flags=re.S)
    return {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text)}


def ddl_fields(text: str) -> set[str]:
    fields = sql_fields(text)
    return fields if {"create", "table"}.issubset(fields) else set()


def sqlite_fields(root: Path, path: Path) -> list[tuple[str, set[str]]]:
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'").fetchall()
            tables = []
            for (name,) in rows:
                quoted = name.replace('"', '""')
                columns = {row[1].lower() for row in db.execute(f'PRAGMA table_xinfo("{quoted}")')}
                tables.append((name, columns | {name.lower()}))
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise OSError(f"cannot read SQLite schema: {safe_rel(root, path)} ({type(exc).__name__})") from exc
    if not tables:
        raise OSError(f"empty SQLite schema: {safe_rel(root, path)}")
    return tables


def json_fields(value: object, prefix: str = "") -> set[str]:
    fields = set()
    if isinstance(value, dict):
        for key, item in value.items():
            name = prefix + str(key).lower()
            fields.add(name)
            fields.update(json_fields(item, name + "."))
    elif isinstance(value, list):
        for item in value:
            fields.update(json_fields(item, prefix))
    return fields


def dict_fields(node: ast.Dict, prefix: str = "") -> set[str]:
    fields = set()
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            name = prefix + key.value.lower()
            fields.add(name)
            if isinstance(value, ast.Dict):
                fields.update(dict_fields(value, name + "."))
    return fields


def python_tree(root: Path, path: Path) -> ast.Module:
    try:
        return ast.parse(read_text(root, path))
    except SyntaxError as exc:
        raise OSError(f"invalid Python contract: {safe_rel(root, path)} (line {exc.lineno})") from exc


def adapter_contract(tree: ast.Module) -> tuple[list[set[str]], set[str]]:
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    projections = []
    clean = set()
    pending = ["collect"]
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited or name not in functions or name == "normalize":
            continue
        visited.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Dict):
                projections.append(dict_fields(node))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                pending.append(node.func.id)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                clean.update(ddl_fields(value.value))
    return projections, clean


def snapshot_fields(tree: ast.Module) -> set[str]:
    fields = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            fields.update(dict_fields(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                fields.add(key.value.lower())
    return fields


def raw_shapes(root: Path, path: Path) -> tuple[list[set[str]], bool]:
    shapes = set()
    used = 0
    limited = False
    try:
        with path.open("rb") as handle:
            for _ in range(MAX_RECORDS):
                remaining = MAX_TEXT - used
                line = handle.readline(remaining + 1)
                if not line:
                    break
                if len(line) > remaining:
                    limited = True
                    break
                used += len(line)
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                shapes.add(frozenset(json_fields(record)))
            else:
                limited = bool(handle.read(1))
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise OSError(f"invalid/unreadable raw schema sample: {safe_rel(root, path)} ({type(exc).__name__})") from exc
    return [set(shape) for shape in sorted(shapes, key=lambda shape: sorted(shape))], limited


def inspect(root: Path) -> tuple[dict, dict, list[str]]:
    layers = {layer: [] for layer in ("l1", "l2", "l3", "l4", "l5")}
    entries = {layer: [] for layer in layers}
    limited_shards = []

    def add(layer: str, path: Path, source: str, kind: str, fields: set[str], table: str | None = None) -> None:
        rel = safe_rel(root, path)
        layers[layer].append(rel)
        entries[layer].append({"path": rel, "source": source, "kind": kind, "fields": fields, "table": table})

    for path in bounded_files(root):
        rel = safe_rel(root, path)
        if path.parent == root / "adapters":
            tree = python_tree(root, path)
            projections, clean = adapter_contract(tree)
            if any(isinstance(node, ast.FunctionDef) and node.name == "collect" for node in tree.body):
                for fields in projections:
                    if fields:
                        add("l1", path, path.stem, "declared", fields)
            if clean:
                add("l2", path, path.stem, "declared", clean)
        elif rel.startswith("L1_collect/raw/"):
            source = path.relative_to(root / "L1_collect/raw").parts[0]
            shapes, limited = raw_shapes(root, path)
            if limited:
                limited_shards.append(rel)
            for fields in shapes:
                add("l1", path, source, "observed", fields)
        elif path.suffix == ".db":
            layer = "l3" if rel == "L3_merge/warehouse.db" else "l2"
            for table, fields in sqlite_fields(root, path):
                add(layer, path, path.stem, "observed", fields, table)
    for layer, rel in CANONICAL.items():
        path = root / rel
        if layer == "l5":
            fields = snapshot_fields(python_tree(root, path))
            if not fields:
                raise OSError(f"empty Snapshot contract: {rel}")
        else:
            text = read_text(root, path)
            fields = ddl_fields(text) if layer == "l3" else sql_fields(text)
            if not fields or layer == "l4" and "select" not in fields:
                raise OSError(f"invalid/empty SQL contract: {rel}")
        add(layer, path, "canonical", "declared", fields)
    return {layer: sorted(set(paths)) for layer, paths in layers.items()}, entries, limited_shards


def l1_counts(layers: dict[str, list[str]]) -> tuple[int, int]:
    paths = layers.get("l1", [])
    raw_files = [path for path in paths if path.startswith("L1_collect/raw/")]
    return len(paths), len(raw_files)


def _truncate_lists(value: object, limit: int) -> object:
    if isinstance(value, dict):
        return {key: _truncate_lists(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_lists(item, limit) for item in value[:limit]]
    return value


def compact_result(result: dict, l1_total_count: int, l1_total_files: int) -> dict:
    """Keep representative evidence while making the JSON summary bounded."""
    for limit in (16, 8, 4, 2, 1):
        compact = _truncate_lists(result, limit)
        assert isinstance(compact, dict)
        compact["l1_total_count"] = l1_total_count
        compact["l1_total_files"] = l1_total_files
        encoded = json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if len(encoded) < MAX_JSON_BYTES:
            return compact
    compact = _truncate_lists(result, 0)
    assert isinstance(compact, dict)
    compact["l1_total_count"] = l1_total_count
    compact["l1_total_files"] = l1_total_files
    compact["sampling"] = {"limit_reached_shards": [], "truncated": True}
    compact["layers"] = {layer: [] for layer in ("l1", "l2", "l3", "l4", "l5")}
    for metric in compact.get("metrics", {}).values():
        metric["matches"] = []
        for coverage in metric.get("coverage", {}).values():
            coverage["evidence"] = []
    return compact


def metric_coverage(metric: str, layer: str, entries: list[dict]) -> dict:
    dimensions = METRICS[metric][layer]
    allowed = EVENT_SOURCES if metric == "eval_effect" else SESSION_SOURCES
    eligible = [entry for entry in entries if layer not in {"l1", "l2"} or entry["source"] in allowed]
    evidence = []
    full_kinds = set()
    found = set()
    for entry in eligible:
        matches = [sorted(set(aliases) & entry["fields"]) for aliases in dimensions]
        fields = sorted({field for group in matches for field in group})
        if fields:
            full = all(matches)
            evidence.append({key: entry[key] for key in ("path", "source", "kind", "table")} | {"fields": fields, "complete": full})
            found.update(fields)
            if full:
                full_kinds.add(entry["kind"])
    status = "observed" if "observed" in full_kinds else "declared" if full_kinds else "partial" if found else "missing"
    return {"status": status, "complete": bool(full_kinds), "fields": sorted(found),
            "required_dimensions": [list(aliases) for aliases in dimensions], "evidence": evidence,
            "sample_present": any(entry["kind"] == "observed" for entry in eligible)}


def lineage_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"See {LINEAGE_POINTER}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.aidata_root.expanduser().resolve()
    if not root.is_dir():
        return lineage_error(f"aidata root is missing or not a directory: {root}")
    requested = list(args.metrics or [])
    if args.metrics_csv:
        requested.extend(x.strip() for x in args.metrics_csv.split(",") if x.strip())
    requested = list(dict.fromkeys(requested or METRICS))
    unknown = sorted(set(requested) - set(METRICS))
    if unknown:
        return lineage_error(f"unknown metric(s): {', '.join(unknown)}")
    try:
        layers, entries, limited_shards = inspect(root)
        if args.check_contract:
            source_contract = all(any(entry["kind"] == "declared" and entry["source"] in EVENT_SOURCES for entry in entries[layer]) for layer in ("l1", "l2"))
            if not source_contract or any(not layers[layer] for layer in layers):
                raise OSError("required collector/clean DDL/schema/SQL/Snapshot sources are incomplete")
    except (OSError, UnicodeError) as exc:
        print(f"cannot inspect aidata contract: {exc}", file=sys.stderr)
        return 3
    evidence = {}
    for metric in requested:
        coverage = {layer: metric_coverage(metric, layer, items) for layer, items in entries.items()}
        raw_retained = coverage["l1"]["status"] == "observed"
        complete = all(item["complete"] for item in coverage.values())
        if not coverage["l1"]["sample_present"] or not coverage["l1"]["complete"]:
            status = "needs_l1_collection"
            reason = "no eligible L1 sample or incomplete metric-specific retained/declared projection; values and unrelated shards are not evidence"
        elif raw_retained and complete:
            status = "ready"
            reason = "retained L1 field shape and complete downstream contracts; not a population/freshness guarantee"
        else:
            status = "needs_l2_l4_extension"
            reason = "L1 contract exists but observed retention or downstream fields need verification/extension"
        evidence[metric] = {"status": status, "reason": reason, "coverage": coverage, "raw_retained": raw_retained,
                            "matches": [f"{layer}:{','.join(item['fields'])}" for layer, item in coverage.items() if item["fields"]]}
    statuses = [item["status"] for item in evidence.values()]
    status = "needs_l1_collection" if "needs_l1_collection" in statuses else "needs_l2_l4_extension" if "needs_l2_l4_extension" in statuses else "ready"
    result = {"aidata_root": str(root), "status": status, "reason": "worst requested metric status; observed shapes and declared contracts are separate",
              "sampling": {"max_records_per_shard": MAX_RECORDS, "max_bytes_per_shard": MAX_TEXT, "values_used_as_evidence": False, "limit_reached_shards": limited_shards},
              "layers": layers, "metrics": evidence}
    if args.as_json:
        encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            l1_total_count, l1_total_files = l1_counts(layers)
            if args.full:
                result = dict(result)
                result["l1_total_count"] = l1_total_count
                result["l1_total_files"] = l1_total_files
            else:
                result = compact_result(result, l1_total_count, l1_total_files)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{status}: {result['reason']}")
        for metric, item in evidence.items():
            print(f"{metric}: {item['status']} — {item['reason']}")
        for layer, paths in layers.items():
            print(f"{layer}: {len(paths)} evidence file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
