#!/usr/bin/env python3
"""Zero-Token 5-tuple, node ID, broken-edge, vault-isolation, and Apple appearance guard for howto."""

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
SYSTEM_APPEARANCE_POINTER = "skills/workflow-hub/references/how-to-nodes.md#6-high-value-recipes"
APPEARANCE_LEDGER_POINTER = "skills/my-designer/references/appearance-ledger.md"
JEV_GUIDE_POINTER = "skills/howto/references/jev-systemone-guide.md"

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
    r"(?i)knowledge-vault[^\n。；;!?！？]{0,60}(?:自动同步|auto[-_\s]?sync)[^\n。；;!?！？]{0,60}design-studio"
)
_SAVE_TOOL_EXEC_RE = re.compile(
    r"(?i)save-tool[^\n。；;!?！？]{0,60}(?:自动安装|直接运行|执行代码|auto[-_\s]?install|execute\s+tool|run\s+tool)"
)
_CUSTOM_THEME_ENGINE_RE = re.compile(
    r"(?i)(?:"
    r"\.designTheme\b|"
    r"\bdesignTheme\b\s*(?:state|store|engine|manager|modifier|key|binding|enum|environment|plumbing|管道|状态机)|"
    r"(?:build|add|create|implement|wire|plumb|persist|store|expose)\b[^\n。；;!?！？]{0,40}\bdesignTheme\b|"
    r"\b(?:in-app|app-private|custom|manual|user-selectable)\s+(?:light/dark\s+|dark[- ]mode\s+)?theme\s+"
    r"(?:toggle|toggles|switcher|switchers|selector|picker|state\s+machine|state\s+machines|engine|engines|manager)\b|"
    r"\b(?:custom|manual|in-app)\s+(?:dark[- ]mode|appearance|color[- ]scheme)\s+"
    r"(?:toggle|toggles|switcher|switchers|selector|picker|state\s+machine|state\s+machines)\b|"
    r"\b(?:web[-_\s]?css(?:[-_\s]?style)?\s+)?parallel[-_\s]+theme\s+"
    r"(?:engine|engines|state\s+machine|state\s+machines|store|plumbing)\b|"
    r"(?:(?<![A-Za-z0-9_])@AppStorage|\bUserDefaults\b)[^\n。；;!?！？]{0,56}\b(?:designTheme|themeMode|selectedTheme|appTheme|isDarkMode|colorSchemeOverride)\b|"
    r"(?:自建|自定义|应用内|应用私有|手动|独立|平行)[^\n。；;!?！？]{0,20}(?:主题切换(?:器|开关)?|暗黑模式开关|深色模式开关|主题状态机|主题引擎|平行主题|designTheme)"
    r")"
)

_NEGATION_RE = re.compile(
    r"(?i)(?:永不|禁止|严禁|不自动|不得|不会|不要|禁自建|禁建|杜绝|切勿|拦截|零自定义|零私有|"
    r"(?<![A-Za-z0-9_])(?:never|must\s+not|do\s+not|don't|cannot|not\s+auto|avoid|forbid|forbids|forbidden|"
    r"reject|rejects|rejecting|without|zero(?=\s+(?:custom|private|in-app|manual|parallel|web|theme\b))|no\s+custom)(?![A-Za-z0-9_]))"
)
_REMOVAL_RE = re.compile(
    r"(?i)(?:移除|弃用|废除|删除|清理|"
    r"(?<![A-Za-z0-9_])(?:remove|removes|removing|drop|dropping|delete|deleting|retire|retiring)(?![A-Za-z0-9_]))"
)
_EXCLUSION_PREP_RE = re.compile(
    r"(?i)(?:而非|而不是|取代|替代|(?<![A-Za-z0-9_])(?:instead\s+of|rather\s+than)(?![A-Za-z0-9_]))"
)
_CLAUSE_SPLIT_RE = re.compile(
    r"[。；;！!？?\n]+|(?:\.(?=\s|$))|(?:\s*[|｜]\s*)|"
    r"(?:[,，]\s*(?=(?:save-tool|knowledge-vault|design-studio|并且|而且|同时|但是|但|然而|不过|and\b|but\b|however\b|yet\b|then\b)))",
    re.IGNORECASE,
)
_SUBCLAUSE_SPLIT_RE = re.compile(
    r"(?i)(?:"
    r"(?<![A-Za-z0-9_])(?:but|however|yet|though|although|while|whereas|then|instead(?!\s+of))(?![A-Za-z0-9_])|"
    r"(?:但是|但|然而|不过|却|转而|改用)|"
    r"(?<![A-Za-z0-9_])and\s+(?=(?:add|build|create|implement|wire|plumb|persist|store|expose|introduce|bind|use|adopt|follow)\b)"
    r")"
)
_JEV_DIRECT_BYPASS_RE = re.compile(
    r"(?i)(?:"
    r"\bcurl\b|\bwget\b|\brequests?\.(?:get|post|request)\b|\burllib(?:\.request)?\b|\bfetch\s*\(|"
    r"https?://(?:127\.0\.0\.1|localhost):7024/v1/systemone\b|"
    r"(?:127\.0\.0\.1|localhost):7024/v1/systemone\b"
    r")"
)
_JEV_FALCON_CHAIN_RE = re.compile(
    r"(?i)(?::19823/v1/systemone\b|\bFalcon\b|\bFALCON_SOURCE_KEY\b|\bBearer\b)"
)


def load_canonical_catalog(nodes_doc: Path = DEFAULT_NODES_DOC) -> tuple[set[str], set[str]]:
    """Parse canonical node IDs (including expanded ranges like O1–O4) and TK-xx exclusively from how-to-nodes.md."""
    resolved = nodes_doc.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"canonical How-To catalog not found: {resolved}")

    text = resolved.read_text(encoding="utf-8")
    valid_nodes: set[str] = set()
    valid_tks: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cols = line.split("|")
        if len(cols) < 3:
            continue
        first_col = cols[1].strip()
        for rng in re.finditer(r"\b([EQIGPO])(\d+)[–-](\1)?(\d+)\b", first_col):
            prefix = rng.group(1)
            start_n, end_n = int(rng.group(2)), int(rng.group(4))
            for num in range(min(start_n, end_n), max(start_n, end_n) + 1):
                valid_nodes.add(f"{prefix}{num}")
        for m_node in re.finditer(r"\b([EQIGPO]\d+[a-z]?)\b", first_col):
            valid_nodes.add(m_node.group(1))
        for m_tk in re.finditer(r"\b(TK-\d{2})\b", first_col):
            valid_tks.add(m_tk.group(1))

    if not valid_nodes:
        raise ValueError(f"canonical How-To catalog contains zero node definitions: {resolved}")
    return valid_nodes, valid_tks


def audit_5tuple(text: str) -> dict[str, Any]:
    missing = [name for name, pat in _TUPLE_PATTERNS.items() if not pat.search(text)]
    return {
        "ok": len(missing) == 0,
        "present": [name for name in _TUPLE_PATTERNS if name not in missing],
        "missing": missing,
    }


def check_nodes(text: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    try:
        valid_nodes, valid_tks = load_canonical_catalog(nodes_doc)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return {
            "ok": False,
            "catalog_error": str(exc),
            "referenced_nodes": [],
            "referenced_broken_edges": [],
            "unknown_nodes": [],
            "unknown_broken_edges": [],
        }
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


def _clause_is_negated(
    clause: str,
    match: re.Match[str],
    *,
    lookback: int = 24,
    allow_removal: bool = False,
) -> bool:
    prefix = clause[max(0, match.start() - lookback):match.start()]
    excl_hits = list(_EXCLUSION_PREP_RE.finditer(prefix))
    if excl_hits:
        before_excl = prefix[:excl_hits[-1].start()]
        if _NEGATION_RE.search(before_excl) or (allow_removal and _REMOVAL_RE.search(before_excl)):
            return False
        return True
    if re.match(r"(?i)^\s*(?:instead\s+of|rather\s+than|而非|而不是)\b", clause[match.end():match.end() + 20]):
        return False
    window = clause[max(0, match.start() - lookback):match.end()]
    if _NEGATION_RE.search(window):
        return True
    return bool(allow_removal and _REMOVAL_RE.search(window))


def check_system_appearance_redlines(text: str) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, line in enumerate(text.splitlines(), 1):
        for raw_clause in _CLAUSE_SPLIT_RE.split(line):
            for subclause in _SUBCLAUSE_SPLIT_RE.split(raw_clause):
                clause = subclause.strip()
                if not clause:
                    continue
                for m_theme in _CUSTOM_THEME_ENGINE_RE.finditer(clause):
                    if not _clause_is_negated(clause, m_theme, lookback=80, allow_removal=True):
                        violations.append({
                            "code": "custom_theme_engine_violation",
                            "line": str(idx),
                            "detail": (
                                "UI light/dark appearance must follow Apple OS-native dynamic system appearance "
                                "(NSAppearance / UITraitCollection / @Environment(\\.colorScheme) + semantic dynamic tokens); "
                                "never build custom .designTheme, in-app theme switchers, or Web-CSS-style parallel theme engines."
                            ),
                            "pointer": f"{SYSTEM_APPEARANCE_POINTER} | {APPEARANCE_LEDGER_POINTER}",
                        })
                        break
    return violations


def check_vault_redlines(text: str) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, line in enumerate(text.splitlines(), 1):
        for raw_clause in _CLAUSE_SPLIT_RE.split(line):
            clause = raw_clause.strip()
            if not clause:
                continue
            m_vault = _AUTO_SYNC_VAULT_RE.search(clause)
            if m_vault and not _clause_is_negated(clause, m_vault):
                violations.append({
                    "code": "vault_isolation_violation",
                    "line": str(idx),
                    "detail": "knowledge-vault must never auto-sync to design-studio",
                })
            m_save = _SAVE_TOOL_EXEC_RE.search(clause)
            if m_save and not _clause_is_negated(clause, m_save):
                violations.append({
                    "code": "save_tool_zero_exec_violation",
                    "line": str(idx),
                    "detail": "save-tool is archive/index-only and must maintain zero execution",
                })
    return violations


def check_jev_redlines(text: str) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, line in enumerate(text.splitlines(), 1):
        if _JEV_DIRECT_BYPASS_RE.search(line) and not _JEV_FALCON_CHAIN_RE.search(line):
            violations.append({
                "code": "jev_bypass_falcon_violation",
                "line": str(idx),
                "detail": (
                    "Jev/System One calls must route through authenticated Falcon :19823 "
                    "and must not call local Raven :7024 directly."
                ),
                "pointer": JEV_GUIDE_POINTER,
            })
    return violations


def _build_self_disclosing_pointer(violations: Sequence[dict[str, Any]]) -> str:
    codes = {str(v.get("code") or "") for v in violations}
    if "jev_bypass_falcon_violation" in codes:
        return (
            "howto_guard: jev_bypass_falcon_violation — direct unauthenticated Raven :7024 System One call rejected.\n"
            "Fix: route through authenticated Falcon :19823 with the configured source key.\n"
            f"Reference: {JEV_GUIDE_POINTER}"
        )
    if "custom_theme_engine_violation" in codes:
        return (
            "howto_guard: custom_theme_engine_violation — custom .designTheme / in-app theme switcher / parallel theme engine rejected.\n"
            "Fix: follow Apple OS-native dynamic system appearance (NSAppearance / UITraitCollection / @Environment(\\.colorScheme)) via E7 -> P7 -> E4 -> E9.\n"
            f"Reference: {SYSTEM_APPEARANCE_POINTER} ({APPEARANCE_LEDGER_POINTER})"
        )
    first_code = str(violations[0].get("code") or "recipe_violation") if violations else "recipe_violation"
    first_detail = str(violations[0].get("detail") or "Recipe validation failed.") if violations else "Recipe validation failed."
    return (
        f"howto_guard: {first_code} — {first_detail}\n"
        "Fix: ensure Inbound/Internal/Calls/PointsTo/Trigger&Notify 5-tuple uses canonical nodes and respects vault redlines.\n"
        "Reference: skills/workflow-hub/references/how-to-nodes.md"
    )


def validate_recipe(text: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    tuple_res = audit_5tuple(text)
    node_res = check_nodes(text, nodes_doc=nodes_doc)
    redline_violations = check_vault_redlines(text)
    jev_violations = check_jev_redlines(text)
    appearance_violations = check_system_appearance_redlines(text)

    violations: list[dict[str, Any]] = []
    if not tuple_res["ok"]:
        violations.append({
            "code": "missing_5tuple_dimensions",
            "missing": tuple_res["missing"],
            "detail": f"Recipe is missing 5-tuple dimension(s): {', '.join(tuple_res['missing'])}",
        })
    if node_res.get("catalog_error"):
        violations.append({
            "code": "missing_or_invalid_catalog",
            "detail": str(node_res["catalog_error"]),
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
    violations.extend(jev_violations)
    violations.extend(appearance_violations)

    report: dict[str, Any] = {
        "ok": len(violations) == 0,
        "five_tuple": tuple_res,
        "nodes": node_res,
        "violations": violations,
    }
    if violations:
        report["pointer"] = _build_self_disclosing_pointer(violations)
    return report


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
    if not report["ok"] and report.get("pointer"):
        sys.stderr.write(f"{report['pointer']}\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
