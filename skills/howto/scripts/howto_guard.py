#!/usr/bin/env python3
"""Zero-Token 5-tuple, node ID, broken-edge, and vault-isolation validator for howto."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_ROOT = SKILL_DIR.parent
DEFAULT_NODES_DOC = SKILLS_ROOT / "workflow-hub" / "references" / "how-to-nodes.md"

_TUPLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "Inbound": re.compile(r"\bInbound\b", re.IGNORECASE),
    "Internal": re.compile(r"\bInternal\b", re.IGNORECASE),
    "Calls": re.compile(r"\bCalls\b", re.IGNORECASE),
    "PointsTo": re.compile(r"\bPoints\s*To\b|\bPointsTo\b", re.IGNORECASE),
    "Trigger_Notify": re.compile(r"\bTrigger\b|\bNotify\b", re.IGNORECASE),
}

_NODE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])([EQIGPO]\d+[a-z]?)(?![A-Za-z0-9_-])")
_TK_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])(TK-\d{2})(?![A-Za-z0-9_-])")

_AUTO_SYNC_VAULT_RE = re.compile(
    r"(?i)knowledge-vault[^\n。；;]{0,60}(?:自动同步|auto[-_\s]?sync)[^\n。；;]{0,60}design-studio"
)
_NEGATION_RE = re.compile(r"(?i)(?:永不|禁止|严禁|不自动|不得|never|must\s+not|do\s+not|not\s+auto)")
_SAVE_TOOL_EXEC_RE = re.compile(
    r"(?i)save-tool[^\n。；;]{0,60}(?:自动安装|直接运行|执行代码|auto[-_\s]?install|execute\s+tool|run\s+tool)"
)


def load_canonical_catalog(nodes_doc: Path = DEFAULT_NODES_DOC) -> tuple[set[str], set[str]]:
    """Parse canonical node IDs (including expanded ranges like O1–O4) and TK-01..TK-16 from how-to-nodes.md."""
    valid_nodes: set[str] = {
        *(f"E{i}" for i in range(1, 13)),
        "E4a",
        *(f"Q{i}" for i in range(1, 4)),
        *(f"I{i}" for i in range(1, 8)),
        *(f"G{i}" for i in range(1, 3)),
        *(f"P{i}" for i in range(1, 10)),
        *(f"O{i}" for i in range(1, 7)),
    }
    valid_tks: set[str] = {f"TK-{i:02d}" for i in range(1, 17)}

    if not nodes_doc.is_file():
        return valid_nodes, valid_tks

    text = nodes_doc.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        first_col = line.split("|")[1].strip()
        for rng in re.finditer(r"\b([EQIGPO])(\d+)[–-](\1)?(\d+)\b", first_col):
            prefix = rng.group(1)
            start_n, end_n = int(rng.group(2)), int(rng.group(4))
            for num in range(min(start_n, end_n), max(start_n, end_n) + 1):
                valid_nodes.add(f"{prefix}{num}")
        for m_node in re.finditer(r"\b([EQIGPO]\d+[a-z]?)\b", first_col):
            valid_nodes.add(m_node.group(1))
        for m_tk in re.finditer(r"\b(TK-\d{2})\b", first_col):
            valid_tks.add(m_tk.group(1))
    return valid_nodes, valid_tks


def audit_5tuple(text: str) -> dict[str, Any]:
    missing = [name for name, pat in _TUPLE_PATTERNS.items() if not pat.search(text)]
    return {
        "ok": len(missing) == 0,
        "present": [name for name in _TUPLE_PATTERNS if name not in missing],
        "missing": missing,
    }


def check_nodes(text: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    valid_nodes, valid_tks = load_canonical_catalog(nodes_doc)
    raw_tokens = set(_NODE_TOKEN_RE.findall(text))
    # P0 appears in Notify priority levels (P0/P1/P2, P0 拍板/告警); ignore P0 while strictly checking P1..P9+.
    referenced_nodes = sorted(tok for tok in raw_tokens if tok != "P0")
    referenced_tks = sorted(set(_TK_TOKEN_RE.findall(text)))
    unknown_nodes = [n for n in referenced_nodes if n not in valid_nodes]
    unknown_tks = [tk for tk in referenced_tks if tk not in valid_tks]
    return {
        "ok": not unknown_nodes and not unknown_tks,
        "referenced_nodes": referenced_nodes,
        "referenced_broken_edges": referenced_tks,
        "unknown_nodes": unknown_nodes,
        "unknown_broken_edges": unknown_tks,
    }


def check_vault_redlines(text: str) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, line in enumerate(text.splitlines(), 1):
        if _AUTO_SYNC_VAULT_RE.search(line) and not _NEGATION_RE.search(line):
            violations.append({
                "code": "vault_isolation_violation",
                "line": str(idx),
                "detail": "knowledge-vault must never auto-sync to design-studio",
            })
        if _SAVE_TOOL_EXEC_RE.search(line) and not _NEGATION_RE.search(line):
            violations.append({
                "code": "save_tool_zero_exec_violation",
                "line": str(idx),
                "detail": "save-tool is archive/index-only and must maintain zero execution",
            })
    return violations


def validate_recipe(text: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    tuple_res = audit_5tuple(text)
    node_res = check_nodes(text, nodes_doc=nodes_doc)
    redline_violations = check_vault_redlines(text)

    violations: list[dict[str, Any]] = []
    if not tuple_res["ok"]:
        violations.append({
            "code": "missing_5tuple_dimensions",
            "missing": tuple_res["missing"],
            "detail": f"Recipe is missing 5-tuple dimension(s): {', '.join(tuple_res['missing'])}",
        })
    if node_res["unknown_nodes"]:
        violations.append({
            "code": "unknown_howto_nodes",
            "unknown_nodes": node_res["unknown_nodes"],
            "detail": f"Unknown How-To node ID(s): {', '.join(node_res['unknown_nodes'])}",
        })
    if node_res["unknown_broken_edges"]:
        violations.append({
            "code": "unknown_broken_edges",
            "unknown_broken_edges": node_res["unknown_broken_edges"],
            "detail": f"Unknown broken-edge ID(s): {', '.join(node_res['unknown_broken_edges'])}",
        })
    violations.extend(redline_violations)

    return {
        "ok": len(violations) == 0,
        "five_tuple": tuple_res,
        "nodes": node_res,
        "violations": violations,
    }


def _read_input_text(file_path: Path | None, inline_text: str | None) -> str:
    if inline_text is not None:
        return inline_text
    if file_path is not None:
        resolved = file_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"input file not found: {resolved}")
        return resolved.read_text(encoding="utf-8")
    raise ValueError("either --file or --text is required")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic 5-tuple, node, and vault-isolation guard for howto")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name, help_msg in (
        ("validate-recipe", "Validate 5-tuple completeness, canonical node/TK IDs, and vault isolation redlines"),
        ("check-nodes", "Validate referenced How-To node IDs and TK-01..TK-16 broken-edge IDs"),
        ("audit-5tuple", "Check presence of Inbound, Internal, Calls, PointsTo, and Trigger/Notify"),
    ):
        p = sub.add_parser(cmd_name, help=help_msg)
        p.add_argument("--file", type=Path, default=None, help="Markdown recipe or spec file to validate")
        p.add_argument("--text", default=None, help="Inline recipe text to validate")
        p.add_argument("--nodes-doc", type=Path, default=DEFAULT_NODES_DOC, help="Path to how-to-nodes.md")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        content = _read_input_text(args.file, args.text)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if args.command == "audit-5tuple":
        report = audit_5tuple(content)
    elif args.command == "check-nodes":
        report = check_nodes(content, nodes_doc=args.nodes_doc)
    else:
        report = validate_recipe(content, nodes_doc=args.nodes_doc)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
