#!/usr/bin/env python3
"""Deterministic Dual-Zone Issue formatter and verifier (`issue-brief`).

Enforces the Dual-Zone Issue Format Contract for Multica and Linear issues:
1. Upper Zone (Human Zone, before `<details>`):
   - `<= 45` non-empty visible lines by default (excluding HTML comments and `---` dividers),
     supporting concrete numbers, percentages, currency amounts, fund codes (`017811`),
     issue/PR identifiers (`TIA-15`, `#196`), file/module names, and compact Markdown tables!
   - Contains `**一句话结论**：...` (or `**核心结论**：...`) and checkable `- [ ]` / `- [x]` items.
   - Rejects repetitive boilerplate checklist items (`duplicate_boilerplate_items`).
   - Zero internal metric/stage codes (`S1..S12`, `M1..M5`, `EV-*`, `DC-*`, `H1..H5`, `R10`, `V1..V5`),
     zero raw commit SHAs / UUIDs, and zero fenced code blocks or CLI invocations.
2. Lower Zone (AI Context Zone):
   - Balanced `<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>...</details>`.
   - Carries `<!-- multica-cursor:... -->` watermark.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

CANONICAL_AI_SUMMARY = "🤖 AI 执行上下文与确定性证据（人无需展开）"
CANONICAL_DETAILS_OPEN = f"<details><summary>{CANONICAL_AI_SUMMARY}</summary>"

HTML_COMMENT_BLOCK_RE = re.compile(r"<!--[\s\S]*?-->")
HTML_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*-->\s*$")
CURSOR_RE = re.compile(r"<!--\s*(?:multica-cursor|issue-brief-cursor)\s*:\s*([^\s>][^>]*?)\s*-->")
DETAILS_OPEN_RE = re.compile(
    r"<details>\s*<summary>\s*" + re.escape(CANONICAL_AI_SUMMARY) + r"\s*</summary>",
    re.MULTILINE,
)
DETAILS_ANY_OPEN_RE = re.compile(r"<details\b[^>]*>", re.IGNORECASE)
DETAILS_CLOSE_RE = re.compile(r"</details>", re.IGNORECASE)

TLDR_RE = re.compile(r"^(?:>\s*)?\*\*(?:一句话结论|核心结论)\*\*\s*[：:]\s*\S+")
CHECKBOX_ITEM_RE = re.compile(r"^\s*[-*+]\s*\[[ xX]\]\s+\S+")

INTERNAL_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])"
    r"(?![A-Za-z0-9_-])"
)
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
HEX_SHA_RE = re.compile(r"(?<![A-Za-z0-9_-])[0-9a-f]{7,40}(?![A-Za-z0-9_-])")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
CLI_COMMAND_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_/-])(?:python3?|pytest|bash|sh|curl|gh|multica|git)\s+\S+(?:\s+--?[A-Za-z0-9_-]+|\s+\S+\.py\b))"
    r"|(?:\./scripts/[A-Za-z0-9_./-]+)"
    r"|(?:\bskills/[A-Za-z0-9_-]+/scripts/[A-Za-z0-9_.-]+\.py\b)"
)
MARKDOWN_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|.*$")


def _normalize_comment(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("<!--") and text.endswith("-->"):
        return text
    return f"<!-- {text} -->"


def _format_decision_item(index: int, item: str | dict[str, Any]) -> str:
    if isinstance(item, str):
        stripped = item.strip()
        if CHECKBOX_ITEM_RE.match(stripped):
            return stripped
        return f"- [ ] {index}. {stripped}"

    if isinstance(item, dict) and item.get("group_header"):
        header_text = str(item["group_header"]).strip()
        if not header_text:
            raise ValueError("group_header must be non-empty")
        return header_text

    if not isinstance(item, dict):
        raise TypeError(f"Unsupported item type: {type(item).__name__}")

    checked = "x" if item.get("checked") else " "
    what = str(item.get("what") or item.get("action") or item.get("title") or item.get("text") or "").strip()
    why = str(item.get("why") or item.get("reason") or "").strip()
    prefix = "" if re.match(r"^\d+\.\s+", what) else f"{index}. "
    body = f"{prefix}{what} — {why}" if why else f"{prefix}{what}"
    subitems = item.get("subitems") or []
    lines = [f"- [{checked}] {body}".rstrip()]
    for sub in subitems:
        sub_text = str(sub).strip()
        if not sub_text:
            continue
        if CHECKBOX_ITEM_RE.match(sub_text):
            lines.append(f"  {sub_text}")
        else:
            lines.append(f"  - [ ] {sub_text}")
    return "\n".join(lines)


def render_dual_zone_issue(
    *,
    tldr: str,
    items: Sequence[str | dict[str, Any]],
    cursor: str,
    ai_context_md: str = "",
    ai_payload: dict[str, Any] | list[Any] | None = None,
    extra_comments: Sequence[str] | None = None,
    heading: str | None = None,
    footer: str | None = None,
    trailing_comments: Sequence[str] | None = None,
) -> str:
    """Render a Dual-Zone compliant Markdown issue body."""
    clean_cursor = str(cursor or "").strip()
    if not clean_cursor:
        raise ValueError("cursor must be a non-empty string")

    clean_tldr = str(tldr or "").strip()
    if not clean_tldr:
        raise ValueError("tldr must be a non-empty string")
    if clean_tldr.startswith(">"):
        clean_tldr = clean_tldr.lstrip(">").strip()
    for prefix in ("**一句话结论**：", "**一句话结论**:", "一句话结论：", "一句话结论:"):
        if clean_tldr.startswith(prefix):
            clean_tldr = clean_tldr[len(prefix) :].strip()
            break

    lines: list[str] = []
    seen_comments: set[str] = set()
    cursor_comment = f"<!-- multica-cursor:{clean_cursor} -->"
    for raw_c in [*(extra_comments or ()), cursor_comment]:
        norm_c = _normalize_comment(raw_c)
        if norm_c and norm_c not in seen_comments:
            seen_comments.add(norm_c)
            lines.append(norm_c)

    lines.append("")
    if heading and heading.strip():
        lines.append(heading.strip())
        lines.append("")

    lines.append(f"**一句话结论**：{clean_tldr}")
    lines.append("")

    formatted_items: list[str] = []
    decision_idx = 0
    for item in items:
        if isinstance(item, dict) and item.get("group_header"):
            formatted_items.append(_format_decision_item(decision_idx, item))
        else:
            decision_idx += 1
            formatted_items.append(_format_decision_item(decision_idx, item))
    if decision_idx == 0:
        raise ValueError("items must contain at least one checkable decision item")
    lines.extend(formatted_items)

    if footer and footer.strip():
        lines.append("")
        lines.append(footer.strip())

    lines.extend(["", CANONICAL_DETAILS_OPEN, ""])

    lower_blocks: list[str] = []
    if ai_context_md and ai_context_md.strip():
        lower_blocks.append(ai_context_md.strip())
    if ai_payload is not None:
        payload_json = json.dumps(ai_payload, ensure_ascii=False, indent=2)
        lower_blocks.append(f"```json\n{payload_json}\n```")
    if not lower_blocks:
        lower_blocks.append(f"- `cursor`: `{clean_cursor}`")

    lines.append("\n\n".join(lower_blocks))
    lines.extend(["", "</details>"])

    for raw_tc in trailing_comments or ():
        norm_tc = _normalize_comment(raw_tc)
        if norm_tc:
            lines.append(norm_tc)

    return "\n".join(lines).rstrip() + "\n"


def _strip_markdown_link_targets(line: str) -> str:
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)


def _extract_checkbox_parts(line: str) -> tuple[str, str, str] | None:
    stripped = _strip_markdown_link_targets(line).strip()
    m = re.match(r"^[-*+]\s*\[[ xX]\]\s+(.*)$", stripped)
    if not m:
        return None
    body = m.group(1).strip()
    body = re.sub(r"^(?:\*\*\s*)?(?:\[\d+\]|\d+\.)\s*", "", body)
    body = body.replace("**", "").replace("`", "").strip()
    parts = re.split(r"\s+(?:—|--|·|:|：)\s+", body, maxsplit=1)
    what_part = re.sub(r"\s+", " ", parts[0]).strip().lower()
    why_part = re.sub(r"\s+", " ", parts[1]).strip().lower() if len(parts) > 1 else ""
    full_norm = re.sub(r"\s+", " ", body).strip().lower()
    return full_norm, what_part, why_part


GENERIC_BOILERPLATE_WHY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)specification\s+references\s+a\s+path\s+absent\s+from\s+the\s+main\s+snapshot"),
    re.compile(r"(?i)deprecated\s+test\s+clause\s+remains\s+in\s+the\s+handoff"),
    re.compile(r"降低返工率与无效上下文消耗"),
    re.compile(r"避免污染主分支基线与阻塞后续自动同步"),
    re.compile(r"核对数仓或消费端状态"),
)


def _detect_duplicate_boilerplate_items(human_visible_lines: Sequence[tuple[int, str]]) -> list[dict[str, Any]]:
    checkbox_entries: list[tuple[int, str, str, str]] = []
    for line_no, line in human_visible_lines:
        parsed = _extract_checkbox_parts(line)
        if parsed is not None:
            checkbox_entries.append((line_no, *parsed))
    if len(checkbox_entries) < 2:
        return []

    seen_full: dict[str, int] = {}
    seen_what: dict[str, int] = {}
    seen_why: dict[str, list[tuple[int, str]]] = {}
    duplicate_lines: list[int] = []
    reasons: list[str] = []

    for line_no, full_norm, what_part, why_part in checkbox_entries:
        if full_norm in seen_full:
            duplicate_lines.append(line_no)
            reasons.append(f"line {line_no} duplicates checklist item on line {seen_full[full_norm]}")
            continue
        seen_full[full_norm] = line_no

        if what_part and what_part in seen_what:
            duplicate_lines.append(line_no)
            reasons.append(f"line {line_no} repeats identical target action '{what_part[:40]}' from line {seen_what[what_part]}")
            continue
        if what_part:
            seen_what[what_part] = line_no

        if len(why_part) >= 6:
            seen_why.setdefault(why_part, []).append((line_no, what_part))

    for why_part, occurrences in seen_why.items():
        if len(occurrences) >= 2 and any(pat.search(why_part) for pat in GENERIC_BOILERPLATE_WHY_PATTERNS):
            for ln, _ in occurrences[1:]:
                if ln not in duplicate_lines:
                    duplicate_lines.append(ln)
                    reasons.append(f"line {ln} repeats boilerplate rationale '{why_part[:40]}'")

    if not duplicate_lines:
        return []
    return [
        {
            "code": "duplicate_boilerplate_items",
            "lines": sorted(set(duplicate_lines)),
            "details": reasons,
            "message": (
                "Human Zone contains repetitive/boilerplate checklist items "
                f"at lines {sorted(set(duplicate_lines))}; each item must name a distinct concrete target and specific metric/reason."
            ),
        }
    ]


def verify_dual_zone_issue(
    markdown_text: str,
    *,
    max_human_lines: int = 45,
    require_cursor: bool = True,
    require_tldr: bool = True,
    require_checkbox: bool = True,
    allow_compact_table: bool = True,
) -> dict[str, Any]:
    """Validate a Markdown string against the Dual-Zone Issue Format Contract."""
    text = str(markdown_text or "")
    violations: list[dict[str, Any]] = []

    cursor_matches = CURSOR_RE.findall(text)
    cursor_value = cursor_matches[0].strip() if cursor_matches else None
    if require_cursor and not cursor_value:
        violations.append(
            {
                "code": "missing-cursor",
                "message": "Missing <!-- multica-cursor:... --> watermark comment.",
            }
        )

    details_match = DETAILS_OPEN_RE.search(text)
    all_opens = list(DETAILS_ANY_OPEN_RE.finditer(text))
    all_closes = list(DETAILS_CLOSE_RE.finditer(text))

    if details_match is None:
        violations.append(
            {
                "code": "missing-ai-details",
                "message": f"Missing canonical collapsible AI block: {CANONICAL_DETAILS_OPEN}",
            }
        )
        upper_raw = text
        lower_raw = ""
    else:
        if len(all_opens) != len(all_closes) or all_closes[-1].start() < details_match.end():
            violations.append(
                {
                    "code": "unbalanced-details",
                    "message": "Unbalanced <details>...</details> tags in issue body.",
                }
            )
        upper_raw = text[: details_match.start()]
        close_after = [m for m in all_closes if m.start() >= details_match.end()]
        lower_raw = text[details_match.end() : close_after[-1].start()] if close_after else ""
        if not lower_raw.strip():
            violations.append(
                {
                    "code": "empty-ai-context",
                    "message": "Lower AI Context Zone inside <details> must not be empty.",
                }
            )

    upper_without_comments = HTML_COMMENT_BLOCK_RE.sub("", upper_raw)
    human_visible_lines: list[tuple[int, str]] = []
    in_fence = False
    fence_detected = False

    for line_no, raw_line in enumerate(upper_without_comments.splitlines(), 1):
        stripped = raw_line.strip()
        if FENCE_RE.match(raw_line):
            fence_detected = True
            in_fence = not in_fence
            human_visible_lines.append((line_no, stripped))
            continue
        if not stripped or HTML_COMMENT_LINE_RE.match(raw_line) or re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            continue
        human_visible_lines.append((line_no, stripped))

    human_line_count = len(human_visible_lines)
    if human_line_count > max_human_lines:
        violations.append(
            {
                "code": "human-zone-line-budget",
                "message": f"Human Zone has {human_line_count} non-empty lines (cap <= {max_human_lines}).",
                "human_zone_lines": human_line_count,
                "cap": max_human_lines,
            }
        )

    if fence_detected or in_fence:
        violations.append(
            {
                "code": "human-zone-fenced-code",
                "message": "Multi-line fenced code blocks (``` or ~~~) are forbidden in the Human Zone.",
            }
        )

    has_tldr = any(TLDR_RE.match(line) for _, line in human_visible_lines)
    if require_tldr and not has_tldr:
        violations.append(
            {
                "code": "missing-tldr",
                "message": "Human Zone must include plain-Chinese '**一句话结论**：...'.",
            }
        )

    checkbox_count = sum(1 for _, line in human_visible_lines if CHECKBOX_ITEM_RE.match(line))
    if require_checkbox and checkbox_count == 0:
        violations.append(
            {
                "code": "missing-checkable-items",
                "message": "Human Zone must include at least one checkable '- [ ]' item.",
            }
        )

    violations.extend(_detect_duplicate_boilerplate_items(human_visible_lines))

    for line_no, line in human_visible_lines:
        inspect_text = _strip_markdown_link_targets(line)
        code_hits = INTERNAL_CODE_RE.findall(inspect_text)
        if code_hits:
            violations.append(
                {
                    "code": "human-zone-internal-code",
                    "line": line_no,
                    "tokens": sorted(set(code_hits)),
                    "message": f"Forbidden internal metric/stage code(s) {sorted(set(code_hits))} in Human Zone line {line_no}.",
                }
            )

        uuid_hits = UUID_RE.findall(inspect_text)
        if uuid_hits:
            violations.append(
                {
                    "code": "human-zone-uuid",
                    "line": line_no,
                    "tokens": uuid_hits,
                    "message": f"Forbidden UUID in Human Zone line {line_no}.",
                }
            )
        else:
            sha_hits = [
                tok
                for tok in HEX_SHA_RE.findall(inspect_text)
                if (any(ch in "abcdef" for ch in tok) and any(ch in "0123456789" for ch in tok))
                or len(tok) == 40
            ]
            if sha_hits:
                violations.append(
                    {
                        "code": "human-zone-commit-sha",
                        "line": line_no,
                        "tokens": sha_hits,
                        "message": f"Forbidden raw commit SHA {sha_hits} in Human Zone line {line_no}.",
                    }
                )

        if CLI_COMMAND_RE.search(inspect_text):
            violations.append(
                {
                    "code": "human-zone-cli-command",
                    "line": line_no,
                    "message": f"Forbidden long CLI command in Human Zone line {line_no}; move CLI commands into <details>.",
                }
            )

        if not allow_compact_table and MARKDOWN_TABLE_ROW_RE.match(line):
            violations.append(
                {
                    "code": "human-zone-markdown-table",
                    "line": line_no,
                    "message": f"Markdown table row in Human Zone line {line_no}; move tables into <details>.",
                }
            )

    return {
        "ok": len(violations) == 0,
        "human_zone_lines": human_line_count,
        "max_human_lines": max_human_lines,
        "checkbox_count": checkbox_count,
        "has_tldr": has_tldr,
        "has_ai_details": details_match is not None,
        "cursor": cursor_value,
        "violations": violations,
    }


def _read_input_text(path_arg: str | None) -> str:
    if not path_arg or path_arg == "-":
        return sys.stdin.read()
    return Path(path_arg).read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_issue_format.py",
        description="Render and verify Dual-Zone Multica / Linear issue Markdown.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_render = sub.add_parser("render", help="Render structured JSON into Dual-Zone Markdown.")
    p_render.add_argument("--input", default="-", help="JSON input path or '-' for stdin.")
    p_render.add_argument("--output", type=Path, default=None, help="Optional output Markdown path.")
    p_render.add_argument("--verify", action="store_true", default=True, help="Verify rendered output before returning.")
    p_render.add_argument("--no-verify", dest="verify", action="store_false")

    p_verify = sub.add_parser("verify", help="Verify a Markdown file or stdin against the Dual-Zone contract.")
    p_verify.add_argument("path", nargs="?", default="-", help="Markdown file path or '-' for stdin.")
    p_verify.add_argument("--max-human-lines", type=int, default=45)
    p_verify.add_argument("--allow-compact-table", action="store_true", default=True)
    p_verify.add_argument("--forbid-markdown-table", dest="allow_compact_table", action="store_false")
    p_verify.add_argument("--no-require-cursor", dest="require_cursor", action="store_false", default=True)
    p_verify.add_argument("--json", action="store_true", default=True, help="Emit structured JSON report (default).")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if ns.subcommand == "render":
        try:
            raw = _read_input_text(ns.input)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("render input must be a JSON object")
            rendered = render_dual_zone_issue(
                tldr=payload["tldr"],
                items=payload["items"],
                cursor=payload["cursor"],
                ai_context_md=str(payload.get("ai_context_md") or ""),
                ai_payload=payload.get("ai_payload"),
                extra_comments=payload.get("extra_comments"),
                heading=payload.get("heading"),
                footer=payload.get("footer"),
                trailing_comments=payload.get("trailing_comments"),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(
                json.dumps({"ok": False, "error": f"render failed: {exc}"}, ensure_ascii=False, indent=2),
                file=sys.stderr,
            )
            return 2

        if ns.verify:
            report = verify_dual_zone_issue(rendered)
            if not report["ok"]:
                print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
                return 2

        if ns.output is not None:
            ns.output.parent.mkdir(parents=True, exist_ok=True)
            ns.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0

    if ns.subcommand == "verify":
        try:
            md_text = _read_input_text(ns.path)
        except (OSError, UnicodeError) as exc:
            report = {
                "ok": False,
                "violations": [{"code": "unreadable-input", "message": f"{type(exc).__name__}: {exc}"}],
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

        report = verify_dual_zone_issue(
            md_text,
            max_human_lines=ns.max_human_lines,
            require_cursor=ns.require_cursor,
            allow_compact_table=ns.allow_compact_table,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
