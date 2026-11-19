#!/usr/bin/env python3
"""Deterministic Swift 6 concurrency, SwiftUI view-identity, and native interaction-feel auditor."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]

CARDS: tuple[dict[str, Any], ...] = (
    {
        "index": 1,
        "slug": "swift-concurrency-expert-coding",
        "concept_path": "knowledge-vault/concepts/swift-concurrency-expert-coding.md",
        "origin": "dimillian-skills + claude-code-apple-skills",
        "focus": "Swift 6 strict concurrency, Sendable boundaries, narrow @MainActor, actor isolation, async/await cancellation & reentrancy",
        "rules": ["CS-001", "CS-002"],
    },
    {
        "index": 2,
        "slug": "swift-concurrency-memory-safety",
        "concept_path": "knowledge-vault/concepts/swift-concurrency-memory-safety.md",
        "origin": "claude-code-apple-skills",
        "focus": "Task lifecycle owner, cooperative cancellation, actor reentrancy state re-check, closure capture memory safety, TSan",
        "rules": ["CS-003", "CS-004"],
    },
    {
        "index": 3,
        "slug": "swiftui-architecture-and-performance",
        "concept_path": "knowledge-vault/concepts/swiftui-architecture-and-performance.md",
        "origin": "dimillian-skills + claude-code-apple-skills",
        "focus": "Stable domain ForEach identity, @Observable/@StateObject ownership, hoisting sort/format/decode out of body",
        "rules": ["CS-005", "CS-006"],
    },
    {
        "index": 4,
        "slug": "swiftui-state-layout-and-view-structure",
        "concept_path": "knowledge-vault/concepts/swiftui-state-layout-and-view-structure.md",
        "origin": "claude-code-apple-skills",
        "focus": "State wrapper ownership matrix, bounded layout without raw GeometryReader thrash, semantic view decomposition, 4-axis Previews",
        "rules": ["CS-007", "CS-008"],
    },
    {
        "index": 5,
        "slug": "apple-design-and-game-feel",
        "concept_path": "knowledge-vault/concepts/apple-design-and-game-feel.md",
        "origin": "claude-code-apple-skills",
        "focus": "Native timing, velocity-preserving springs, haptics alignment, Liquid Glass restraint, accessibilityReduceMotion",
        "rules": ["CS-009", "CS-010"],
    },
)


def _strip_swift_comments(source: str) -> str:
    """Mask line and block comments while preserving line numbers and // SAFETY: markers."""
    lines: list[str] = []
    in_block = False
    for line in source.splitlines():
        out = []
        i = 0
        in_str = False
        while i < len(line):
            if in_block:
                end = line.find("*/", i)
                if end == -1:
                    i = len(line)
                else:
                    in_block = False
                    i = end + 2
                continue
            ch = line[i]
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_str = not in_str
                out.append(ch)
                i += 1
                continue
            if not in_str and line[i : i + 2] == "/*":
                in_block = True
                i += 2
                continue
            if not in_str and line[i : i + 2] == "//":
                comment = line[i:]
                if "SAFETY:" in comment:
                    out.append(comment)
                break
            out.append(ch)
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)


def _extract_body_blocks(masked: str) -> list[tuple[int, str]]:
    """Return (start_line_1based, body_text) for SwiftUI `var body: some View` blocks."""
    blocks: list[tuple[int, str]] = []
    pattern = re.compile(r"\bvar\s+body\s*:\s*some\s+View\s*\{")
    line_offsets = [0]
    for line in masked.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    def line_for_offset(offset: int) -> int:
        lo, hi = 0, len(line_offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_offsets[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    for match in pattern.finditer(masked):
        brace_start = masked.find("{", match.start())
        if brace_start == -1:
            continue
        depth = 0
        idx = brace_start
        while idx < len(masked):
            if masked[idx] == "{":
                depth += 1
            elif masked[idx] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((line_for_offset(match.start()), masked[brace_start : idx + 1]))
                    break
            idx += 1
    return blocks


def _strip_nonisolated_subblocks(block: str) -> str:
    """Blank out `nonisolated` methods/computed properties inside an `@MainActor` type while preserving line count."""
    chars = list(block)
    for match in re.finditer(r"\bnonisolated\b[^{;\n]*\{", block):
        brace_start = block.find("{", match.start())
        if brace_start == -1:
            continue
        depth = 0
        idx = brace_start
        while idx < len(block):
            if block[idx] == "{":
                depth += 1
            elif block[idx] == "}":
                depth -= 1
                if depth == 0:
                    for pos in range(brace_start, idx + 1):
                        if chars[pos] != "\n":
                            chars[pos] = " "
                    break
            idx += 1
    return "".join(chars)


def _extract_mainactor_blocks(masked: str) -> list[tuple[int, str]]:
    """Return (start_line_1based, block_text) for braced `@MainActor` types, functions, computed properties, or closures."""
    blocks: list[tuple[int, str]] = []
    line_offsets = [0]
    for line in masked.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    def line_for_offset(offset: int) -> int:
        lo, hi = 0, len(line_offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_offsets[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    valid_header_re = re.compile(
        r"^\s*(?:\(\s*unsafe\s*\)\s*)?"
        r"(?:@[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?\s*)*"
        r"(?:(?:public|internal|package|fileprivate|private|open|final|static|class|override|mutating|Sending|sending)\s+)*"
        r"(?:"
        r"(?:class|struct|enum|extension)\s+[^{;=]+|"
        r"(?:func|init)\b[^{;=]*|"
        r"var\s+[A-Za-z_][A-Za-z0-9_]*\s*:\s*[^{;=\n]+|"
        r""
        r")\s*$",
        re.DOTALL,
    )

    for match in re.finditer(r"@MainActor\b", masked):
        brace_start = masked.find("{", match.end())
        if brace_start == -1:
            continue
        header = masked[match.end() : brace_start]
        if not valid_header_re.match(header):
            continue
        depth = 0
        idx = brace_start
        while idx < len(masked):
            if masked[idx] == "{":
                depth += 1
            elif masked[idx] == "}":
                depth -= 1
                if depth == 0:
                    cleaned = _strip_nonisolated_subblocks(masked[brace_start : idx + 1])
                    blocks.append((line_for_offset(brace_start), cleaned))
                    break
            idx += 1
    return blocks


def audit_swift_source(source: str, label: str = "<snippet>") -> list[dict[str, Any]]:
    masked = _strip_swift_comments(source)
    lines = masked.splitlines()
    findings: list[dict[str, Any]] = []

    def add(rule_id: str, card_slug: str, severity: str, line_no: int, title: str, remediation: str) -> None:
        findings.append(
            {
                "file": label,
                "line": line_no,
                "rule_id": rule_id,
                "card_slug": card_slug,
                "severity": severity,
                "title": title,
                "remediation": remediation,
            }
        )

    for idx, line in enumerate(lines, start=1):
        if "@unchecked Sendable" in line:
            window = "\n".join(lines[max(0, idx - 3) : min(len(lines), idx + 2)])
            if "SAFETY:" not in window:
                add(
                    "CS-001",
                    "swift-concurrency-expert-coding",
                    "high",
                    idx,
                    "Unchecked Sendable without documented thread-safety proof",
                    "Convert to an immutable value DTO/actor, or document lock/queue invariance with a `// SAFETY:` proof and TSan test.",
                )
        if re.search(r"\bTask\.detached\b", line):
            add(
                "CS-003",
                "swift-concurrency-memory-safety",
                "high",
                idx,
                "Untracked Task.detached drops structured actor inheritance and lifecycle cancellation",
                "Bind async work to SwiftUI `.task(id:)` or store a structured `Task` handle on the owning model and cancel on teardown.",
            )
        if re.search(r"\[\s*unowned\s+self\b", line):
            add(
                "CS-004",
                "swift-concurrency-memory-safety",
                "high",
                idx,
                "Unsafe [unowned self] capture in concurrent/closure context risks crash after teardown",
                "Define explicit task ownership + cancellation or capture `[weak self]` / value inputs instead of `[unowned self]`.",
            )
        if re.search(r"\bForEach\s*\([^)]*(?:\.indices\b|0\s*\.\.<)", line) or re.search(
            r"\bid\s*:\s*\\\.self\b", line
        ) and ".enumerated()" in line:
            add(
                "CS-005",
                "swiftui-architecture-and-performance",
                "high",
                idx,
                "Unstable index-based ForEach identity causes subtree thrash and broken animations",
                "Conform row items to `Identifiable` with a stable domain ID instead of `.indices` or positional offsets.",
            )
        if re.search(r"\bGeometryReader\s*\{", line) and "onGeometryChange" not in masked:
            add(
                "CS-007",
                "swiftui-state-layout-and-view-structure",
                "medium",
                idx,
                "Raw GeometryReader alters parent-child sizing proposals and risks layout loops",
                "Prefer `ViewThatFits`, `containerRelativeFrame`, or `.onGeometryChange(for:)` unless bounded coordinate measurement is required.",
            )

    for start_line, ma_block in _extract_mainactor_blocks(masked):
        if re.search(r"\b(?:Data\s*\(\s*contentsOf\s*:|Thread\.sleep\s*\()", ma_block):
            rel_offset = next(
                (
                    i
                    for i, ln in enumerate(ma_block.splitlines())
                    if re.search(r"\b(?:Data\s*\(\s*contentsOf\s*:|Thread\.sleep\s*\()", ln)
                ),
                0,
            )
            add(
                "CS-002",
                "swift-concurrency-expert-coding",
                "high",
                start_line + rel_offset,
                "Blocking synchronous I/O or sleep detected under @MainActor context",
                "Move synchronous disk/network decoding and delays off `@MainActor` into a dedicated `actor` or async background function.",
            )

    for start_line, body in _extract_body_blocks(masked):
        if re.search(r"\b(?:DateFormatter|ISO8601DateFormatter|NumberFormatter)\s*\(\s*\)|\.sorted\s*\(", body) or re.search(
            r"\bUUID\s*\(\s*\)", body
        ):
            add(
                "CS-006",
                "swiftui-architecture-and-performance",
                "high",
                start_line,
                "Expensive formatter allocation, sorting, or inline UUID() inside SwiftUI body",
                "Hoist sorting and formatter instances to cached model state or static properties, and never generate fresh `UUID()` inside `body`.",
            )
        if re.search(r"\b(?:URLSession\.shared|ModelContainer\s*\(|Task\s*\{\s*await\s+URLSession)", body) or re.search(
            r"\b[A-Z][A-Za-z0-9_]*(?:ViewModel|Store|Service|Repository)\s*\(", body
        ):
            add(
                "CS-008",
                "swiftui-state-layout-and-view-structure",
                "high",
                start_line,
                "Model or service instantiated directly inside SwiftUI View.body",
                "Own models via `@State` / `@StateObject` or inject protocol/closure seams from the parent; keep `body` pure.",
            )

    spring_re = re.compile(
        r"(?:\.(?:repeatForever|spring|interactiveSpring)\b|withAnimation\s*\(\s*\.(?:spring|interactiveSpring))"
    )
    has_continuous_or_spring = bool(spring_re.search(masked))
    if has_continuous_or_spring and "accessibilityReduceMotion" not in masked:
        line_no = next(
            (i for i, ln in enumerate(lines, start=1) if spring_re.search(ln)),
            1,
        )
        add(
            "CS-009",
            "apple-design-and-game-feel",
            "medium",
            line_no,
            "Spring or repeating animation without accessibilityReduceMotion fallback",
            "Read `@Environment(\\.accessibilityReduceMotion)` and provide an instant/crossfade fallback when Reduce Motion is enabled.",
        )

    glass_hits = [
        i
        for i, ln in enumerate(lines, start=1)
        if re.search(r"\.(?:glassEffect|ultraThinMaterial|thinMaterial|regularMaterial)\b", ln)
    ]
    if len(glass_hits) >= 3:
        add(
            "CS-010",
            "apple-design-and-game-feel",
            "medium",
            glass_hits[0],
            "Stacked glass/material modifiers risk muddy contrast and GPU overdraw",
            "Restrict Liquid Glass / material chrome to a single floating navigation or toolbar layer above solid content.",
        )

    return findings


RULE_PROBES: dict[str, str] = {
    "CS-001": "final class BadBox: @unchecked Sendable {}",
    "CS-002": "@MainActor final class Screen { func load(u: URL) throws { _ = try Data(contentsOf: u) } }",
    "CS-003": "func go() { Task.detached { } }",
    "CS-004": "func go() { Task { [unowned self] in _ = self } }",
    "CS-005": "struct V: View { let a: [Int]; var body: some View { ForEach(a.indices, id: \\.self) { Text(\"\\($0)\") } } }",
    "CS-006": "struct V: View { var body: some View { let f = DateFormatter(); Text(\"\\(f)\") } }",
    "CS-007": "struct V: View { var body: some View { GeometryReader { _ in Text(\"x\") } } }",
    "CS-008": "struct V: View { var body: some View { let vm = FeedViewModel(); Text(\"\\(vm)\") } }",
    "CS-009": "struct V: View { var body: some View { Text(\"x\").animation(.spring(duration: 0.2), value: 1) } }",
    "CS-010": "struct V: View { var body: some View { VStack { Text(\"a\").glassEffect()\nText(\"b\").ultraThinMaterial\nText(\"c\").regularMaterial } } }",
}


def check_card_coverage() -> dict[str, Any]:
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    ref_md = (SKILL_DIR / "references" / "concurrency-and-swiftui-cards.md").read_text(encoding="utf-8")

    missing: list[str] = []
    covered: list[dict[str, Any]] = []
    for card in CARDS:
        slug = card["slug"]
        in_skill = f"{slug}.md" in skill_md
        in_ref = card["concept_path"] in ref_md
        live_rules = all(
            rule in {item["rule_id"] for item in audit_swift_source(RULE_PROBES.get(rule, ""))}
            for rule in card["rules"]
        )
        ok = in_skill and in_ref and live_rules
        if not ok:
            missing.append(slug)
        covered.append(
            {
                "slug": slug,
                "concept_path": card["concept_path"],
                "in_skill_md": in_skill,
                "in_reference_md": in_ref,
                "in_script_rules": live_rules,
                "rules": card["rules"],
                "covered": ok,
            }
        )
    return {
        "ok": len(missing) == 0,
        "skill": "ios-concurrency-swiftui",
        "expected_cards": len(CARDS),
        "covered_cards": sum(1 for item in covered if item["covered"]),
        "missing_cards": missing,
        "cards": covered,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit Swift 6 concurrency, SwiftUI view structure, and native interaction feel.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-cards", help="List the 5 mapped TIA-3 cards and deterministic rule IDs.")
    sub.add_parser("check-card-coverage", help="Verify 100%% 5-card coverage across SKILL.md, references/, and script rules.")

    audit_p = sub.add_parser("audit-source", help="Run deterministic Swift/SwiftUI checks on files or an inline snippet.")
    audit_p.add_argument("--path", action="append", default=[], help="Swift file or directory to scan.")
    audit_p.add_argument("--snippet", default=None, help="Inline Swift snippet to scan.")
    audit_p.add_argument("--strict", action="store_true", help="Exit with code 2 when findings are non-empty.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list-cards":
        print(json.dumps({"ok": True, "skill": "ios-concurrency-swiftui", "count": len(CARDS), "cards": list(CARDS)}, indent=2))
        return 0

    if args.command == "check-card-coverage":
        report = check_card_coverage()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    targets: list[tuple[str, str]] = []
    if args.snippet is not None:
        targets.append(("<snippet>", args.snippet))
    for raw in args.path:
        p = Path(raw)
        if not p.exists():
            print(json.dumps({"ok": False, "error": f"path_not_found:{raw}"}), file=sys.stderr)
            return 2
        if p.is_file():
            targets.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
        else:
            for swift_file in sorted(p.rglob("*.swift")):
                targets.append((str(swift_file), swift_file.read_text(encoding="utf-8", errors="replace")))

    if not targets:
        print(json.dumps({"ok": False, "error": "no_source_supplied"}), file=sys.stderr)
        return 2

    all_findings: list[dict[str, Any]] = []
    for label, content in targets:
        all_findings.extend(audit_swift_source(content, label=label))

    payload = {
        "ok": len(all_findings) == 0,
        "skill": "ios-concurrency-swiftui",
        "files_scanned": len(targets),
        "finding_count": len(all_findings),
        "findings": all_findings,
    }
    if args.strict and all_findings:
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
