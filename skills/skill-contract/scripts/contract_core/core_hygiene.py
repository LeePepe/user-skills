"""Layer A zero-token static AST & package structure hygiene checks for skill-contract."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .core_common import EVAL_DRAWERS, _enrich_finding

SINGLE_LINE_FENCE_RE = re.compile(
    r"^\s*```(?![`])(?P<lang>[A-Za-z0-9_-]+)[ \t]+(?P<body>\S.*?)\s*```(?![`])\s*$"
)
FENCE_DELIM_RE = re.compile(r"^\s*(?P<delim>`{3,}|~{3,})(?P<rest>.*)$")
TABLE_SEP_CELL_RE = re.compile(r"^:?-{2,}:?$")
SNIPPET_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|\d+\s*°[CF]|\d+%", re.UNICODE)
SNIPPET_STOPWORDS = frozenset(
    {
        "let",
        "var",
        "func",
        "struct",
        "class",
        "import",
        "return",
        "true",
        "false",
        "text",
        "view",
        "body",
        "some",
        "div",
        "span",
        "def",
        "pass",
    }
)


def detect_malformed_code_fences(text: str, rel_file: str, *, skill: str) -> list[dict[str, Any]]:
    """Detect single-line triple-backtick fences (` ```lang cmd ``` `) outside outer fenced blocks."""
    findings: list[dict[str, Any]] = []
    active_fence: tuple[str, int] | None = None
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        fence_match = FENCE_DELIM_RE.match(raw_line)
        if active_fence is not None:
            if fence_match is not None:
                delim = fence_match.group("delim")
                rest = fence_match.group("rest")
                if delim[0] == active_fence[0] and len(delim) >= active_fence[1] and not rest.strip():
                    active_fence = None
            continue
        single = SINGLE_LINE_FENCE_RE.match(raw_line)
        if single is not None:
            findings.append(
                _enrich_finding(
                    {
                        "skill": skill,
                        "code": "malformed-code-fence",
                        "file": rel_file,
                        "tier": "soft_advisory_exit_0",
                        "line": lineno,
                        "language": single.group("lang"),
                        "match": raw_line.strip()[:200],
                    }
                )
            )
            continue
        if fence_match is not None:
            delim = fence_match.group("delim")
            rest = fence_match.group("rest")
            if delim[0] * len(delim) not in rest:
                active_fence = (delim[0], len(delim))
    return findings


def _is_namespace_setdefault_call(node: ast.AST) -> str | None:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"):
        return None
    target = node.func.value
    if (
        isinstance(target, ast.Call)
        and isinstance(target.func, ast.Name)
        and target.func.id == "globals"
        and not target.args
        and not target.keywords
    ):
        return "globals().setdefault"
    if isinstance(target, ast.Attribute) and target.attr == "__dict__":
        return "__dict__.setdefault"
    return None


def scan_ast_namespace_pollution(pkg_path: Path) -> list[dict[str, Any]]:
    """Emit `ast-namespace-pollution` soft advisories for namespace setdefault injection or duplicate `*_core` helpers."""
    skill = pkg_path.name
    scripts_dir = pkg_path / "scripts"
    if not scripts_dir.is_dir():
        return []
    root_kind = "skills" if pkg_path.parent.name == "skills" else ".agents/skills"
    advisories: list[dict[str, Any]] = []

    for py_path in sorted(scripts_dir.rglob("*.py")):
        if py_path.is_symlink() or not py_path.is_file() or "__pycache__" in py_path.parts:
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"), filename=str(py_path))
            rel_file = f"{root_kind}/{skill}/scripts/{py_path.relative_to(scripts_dir).as_posix()}"
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            pattern = _is_namespace_setdefault_call(node)
            if pattern is not None:
                advisories.append(
                    _enrich_finding(
                        {
                            "skill": skill,
                            "code": "ast-namespace-pollution",
                            "file": rel_file,
                            "tier": "soft_advisory_exit_0",
                            "kind": "namespace_setdefault",
                            "pattern": pattern,
                            "line": getattr(node, "lineno", 1),
                        }
                    )
                )

    for core_dir in sorted(scripts_dir.glob("*_core")):
        if not core_dir.is_dir() or core_dir.is_symlink():
            continue
        helper_defs: dict[str, list[str]] = {}
        for py_path in sorted(core_dir.glob("*.py")):
            if py_path.is_symlink() or not py_path.is_file() or py_path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"), filename=str(py_path))
            except (OSError, SyntaxError):
                continue
            for stmt in tree.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if stmt.name.startswith("__") and stmt.name.endswith("__"):
                        continue
                    helper_defs.setdefault(stmt.name, []).append(py_path.name)
        core_rel = f"{root_kind}/{skill}/scripts/{core_dir.relative_to(scripts_dir).as_posix()}"
        for fn_name, modules in sorted(helper_defs.items()):
            distinct_modules = sorted(set(modules))
            if len(distinct_modules) >= 2:
                advisories.append(
                    _enrich_finding(
                        {
                            "skill": skill,
                            "code": "ast-namespace-pollution",
                            "file": f"{core_rel}/{distinct_modules[0]}",
                            "tier": "soft_advisory_exit_0",
                            "kind": "duplicate_core_helper",
                            "symbol": fn_name,
                            "modules": [f"{core_rel}/{mod}" for mod in distinct_modules],
                        }
                    )
                )
    return advisories


def _split_markdown_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    parts = re.split(r"(?<!\\)\|", stripped)
    return [" ".join(part.replace(r"\|", "|").split()).strip() for part in parts]


def _is_markdown_table_separator(line: str) -> bool:
    if "|" not in line:
        return False
    cells = _split_markdown_table_cells(line)
    non_empty = [cell for cell in cells if cell]
    return len(non_empty) >= 2 and all(TABLE_SEP_CELL_RE.match(cell) for cell in non_empty)


def scan_boilerplate_table_decay(text: str, rel_file: str, *, skill: str) -> list[dict[str, Any]]:
    """Detect Markdown tables in `references/*.md` with `>=8` data rows and `>=50%` identical `>=20`-char cells."""
    advisories: list[dict[str, Any]] = []
    lines = text.splitlines()
    active_fence: tuple[str, int] | None = None
    idx = 0
    total_lines = len(lines)

    while idx < total_lines:
        raw_line = lines[idx]
        fence_match = FENCE_DELIM_RE.match(raw_line)
        if active_fence is not None:
            if fence_match is not None:
                delim = fence_match.group("delim")
                rest = fence_match.group("rest")
                if delim[0] == active_fence[0] and len(delim) >= active_fence[1] and not rest.strip():
                    active_fence = None
            idx += 1
            continue
        if fence_match is not None:
            delim = fence_match.group("delim")
            rest = fence_match.group("rest")
            if delim[0] * len(delim) not in rest and SINGLE_LINE_FENCE_RE.match(raw_line) is None:
                active_fence = (delim[0], len(delim))
            idx += 1
            continue

        if "|" in raw_line and idx + 1 < total_lines and _is_markdown_table_separator(lines[idx + 1]):
            header_line = idx + 1
            data_rows: list[list[str]] = []
            cursor = idx + 2
            while cursor < total_lines:
                row_raw = lines[cursor]
                if not row_raw.strip() or "|" not in row_raw or FENCE_DELIM_RE.match(row_raw) is not None:
                    break
                data_rows.append(_split_markdown_table_cells(row_raw))
                cursor += 1
            row_count = len(data_rows)
            if row_count >= 8:
                cell_row_counts: dict[str, int] = {}
                for row_cells in data_rows:
                    for cell in {c for c in row_cells if len(c) >= 20}:
                        cell_row_counts[cell] = cell_row_counts.get(cell, 0) + 1
                repeated = [
                    (count, cell)
                    for cell, count in cell_row_counts.items()
                    if count * 2 >= row_count
                ]
                if repeated:
                    repeated.sort(key=lambda item: (-item[0], -len(item[1]), item[1]))
                    top_count, top_cell = repeated[0]
                    advisories.append(
                        _enrich_finding(
                            {
                                "skill": skill,
                                "code": "boilerplate-table-decay",
                                "file": rel_file,
                                "tier": "soft_advisory_exit_0",
                                "line": header_line,
                                "rows": row_count,
                                "repeated_rows": top_count,
                                "repeat_ratio": round(top_count / row_count, 4),
                                "repeated_cell": top_cell[:120],
                            }
                        )
                    )
            idx = cursor
            continue
        idx += 1
    return advisories


def _meaningful_snippet_tokens(snippet: str) -> set[str]:
    tokens: set[str] = set()
    for match in SNIPPET_TOKEN_RE.finditer(snippet or ""):
        tok = match.group(0).lower().replace(" ", "")
        if tok not in SNIPPET_STOPWORDS:
            tokens.add(tok)
    return tokens


def is_prose_only_eval_case(entry: Mapping[str, Any]) -> bool:
    """Return True if an active eval case only checks static Markdown prose substrings without any executable gate."""
    has_exec = any(
        str(entry.get(key) or "").strip()
        for key in ("cmd", "negative_cmd", "bad_snippet", "forbidden_regex", "regex", "target_cmd")
    ) or bool(entry.get("target_assertions") or entry.get("assertions") or entry.get("verify_lines") or entry.get("jev_starred_id"))
    if has_exec:
        return False
    file_ref = str(entry.get("file") or "").strip().lower()
    if not file_ref.endswith(".md"):
        return False
    return bool(entry.get("must_contain") or entry.get("must_not_contain"))


def scan_eval_drawer_advisories(
    pkg_path: Path,
    *,
    include_prose_only: bool = True,
) -> list[dict[str, Any]]:
    """Scan active eval/defect drawers for `vacuous-good-snippet` and `fake-prose-echo-test` soft advisories."""
    skill = pkg_path.name
    advisories: list[dict[str, Any]] = []
    for drawer_sub in EVAL_DRAWERS:
        drawer_path = pkg_path / drawer_sub
        if not drawer_path.is_file() or drawer_path.is_symlink():
            continue
        try:
            doc = json.loads(drawer_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(doc, Mapping):
            continue
        entries = doc.get("active_cases")
        if not isinstance(entries, list):
            continue
        drawer_rel = f"{skill}/{drawer_sub}"
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_id = str(entry.get("id") or "<unidentified>")
            raw_bad = entry.get("bad_snippet")
            raw_good = entry.get("good_snippet")
            if isinstance(raw_bad, str) and isinstance(raw_good, str):
                bad_s = raw_bad.strip()
                good_s = raw_good.strip()
                if bad_s and good_s:
                    bad_tokens = _meaningful_snippet_tokens(bad_s)
                    good_tokens = _meaningful_snippet_tokens(good_s)
                    shared_tokens = sorted(bad_tokens & good_tokens)
                    short_placeholder = len(good_s) <= 25 and len(bad_s) > 25
                    zero_shared_tokens = len(bad_s) >= 20 and bool(bad_tokens) and not shared_tokens
                    if short_placeholder or zero_shared_tokens:
                        reason = "trivial_short_placeholder" if short_placeholder and shared_tokens else (
                            "trivial_short_and_zero_shared_tokens" if short_placeholder else "zero_shared_domain_tokens"
                        )
                        advisories.append(
                            _enrich_finding(
                                {
                                    "skill": skill,
                                    "code": "vacuous-good-snippet",
                                    "file": drawer_rel,
                                    "tier": "soft_advisory_exit_0",
                                    "id": entry_id,
                                    "reason": reason,
                                    "bad_length": len(bad_s),
                                    "good_length": len(good_s),
                                    "shared_tokens": shared_tokens,
                                }
                            )
                        )
            if include_prose_only and drawer_sub == "references/eval-cases.json" and is_prose_only_eval_case(entry):
                advisories.append(
                    _enrich_finding(
                        {
                            "skill": skill,
                            "code": "fake-prose-echo-test",
                            "file": drawer_rel,
                            "tier": "soft_advisory_exit_0",
                            "id": entry_id,
                            "target_file": str(entry.get("file") or ""),
                        }
                    )
                )
    return advisories


__all__ = [
    "SINGLE_LINE_FENCE_RE",
    "SNIPPET_STOPWORDS",
    "SNIPPET_TOKEN_RE",
    "detect_malformed_code_fences",
    "is_prose_only_eval_case",
    "scan_ast_namespace_pollution",
    "scan_boilerplate_table_decay",
    "scan_eval_drawer_advisories",
]
