#!/usr/bin/env python3
"""Deterministic iOS architecture, SwiftData migration, test-contract, batch-refactor, capability-seam, and Apple Intelligence fallback auditor."""
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
        "index": 10,
        "slug": "ios-architecture-navigation-migration",
        "concept_path": "knowledge-vault/concepts/ios-architecture-navigation-migration.md",
        "origin": "claude-code-apple-skills",
        "focus": "State-driven NavigationStack/sheet/tab & deep-link routing, macOS vs iOS navigation divergence, OS API migration inventory, VoiceOver/Dynamic Type/keyboard acceptance",
        "rules": ["AT-001", "AT-002", "AT-011"],
    },
    {
        "index": 11,
        "slug": "swiftdata-persistence-and-migration",
        "concept_path": "knowledge-vault/concepts/swiftdata-persistence-and-migration.md",
        "origin": "claude-code-apple-skills",
        "focus": "SwiftData invariants, explicit @Relationship(deleteRule:), VersionedSchema + SchemaMigrationPlan fixtures, user-facing recovery",
        "rules": ["AT-003", "AT-004"],
    },
    {
        "index": 12,
        "slug": "test-contracts-and-regression-gates",
        "concept_path": "knowledge-vault/concepts/test-contracts-and-regression-gates.md",
        "origin": "claude-code-apple-skills",
        "focus": "Behavior contracts, characterization tests before refactoring, combined snapshot + integration + mutation/coverage ratchet gates",
        "rules": ["AT-005", "AT-006"],
    },
    {
        "index": 13,
        "slug": "refactor-review-and-skill-audit",
        "concept_path": "knowledge-vault/concepts/refactor-review-and-skill-audit.md",
        "origin": "dimillian-skills",
        "focus": "Bounded batch-refactor work packages (disjoint Owned Paths, DAG, verify commands), reversible commits, skill deduplication audit",
        "rules": ["AT-007"],
    },
    {
        "index": 14,
        "slug": "feature-generators-and-apple-platform-integration",
        "concept_path": "knowledge-vault/concepts/feature-generators-and-apple-platform-integration.md",
        "origin": "claude-code-apple-skills",
        "focus": "CloudKit, Push, Deep Links, and Offline capabilities behind protocol seams with permission-denied, offline, and recovery tests",
        "rules": ["AT-008", "AT-009"],
    },
    {
        "index": 15,
        "slug": "apple-intelligence-and-special-platforms",
        "concept_path": "knowledge-vault/concepts/apple-intelligence-and-special-platforms.md",
        "origin": "claude-code-apple-skills",
        "focus": "Apple Intelligence, Core ML, and multi-platform (iPhone/iPad/Mac/Watch/Vision) capability matrix, mandatory fallback, timeout & memory budgets",
        "rules": ["AT-010"],
    },
)


def _extract_test_blocks(source: str) -> list[tuple[int, str, str]]:
    """Return (line_no, func_name, body) for XCTest `func test...` or Swift Testing `@Test func ...`."""
    blocks: list[tuple[int, str, str]] = []
    pattern = re.compile(r"(?:@Test(?:\([^)]*\))?\s+)?func\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?:async\s*)?(?:throws\s*)?\{")
    offsets = [0]
    for ln in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(ln))

    def line_of(pos: int) -> int:
        lo, hi = 0, len(offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offsets[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    for m in pattern.finditer(source):
        full_match = m.group(0)
        name = m.group(1)
        if not (name.startswith("test") or "@Test" in full_match):
            continue
        brace = source.find("{", m.start())
        if brace == -1:
            continue
        depth = 0
        idx = brace
        while idx < len(source):
            if source[idx] == "{":
                depth += 1
            elif source[idx] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((line_of(m.start()), name, source[brace : idx + 1]))
                    break
            idx += 1
    return blocks


def audit_source(source: str, label: str = "<snippet>") -> list[dict[str, Any]]:
    lines = source.splitlines()
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

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.split("//", 1)[0]
        if re.search(r"\bNavigationView\s*\{", line):
            add(
                "AT-001",
                "ios-architecture-navigation-migration",
                "high",
                idx,
                "Deprecated NavigationView prevents state-driven path & deep-link routing",
                "Migrate to `NavigationStack(path:)` or `NavigationSplitView` backed by an explicit value-type route state.",
            )
        if re.search(r"\bButton\s*\{[^}]*\}\s*label:\s*\{\s*Image\s*\(\s*systemName:", line) and "accessibilityLabel" not in source:
            add(
                "AT-002",
                "ios-architecture-navigation-migration",
                "medium",
                idx,
                "Icon-only Button without `.accessibilityLabel` breaks VoiceOver navigation",
                "Attach a localized `.accessibilityLabel(...)` describing the action intent.",
            )
        if "@Relationship" in line and "deleteRule:" not in line:
            add(
                "AT-003",
                "swiftdata-persistence-and-migration",
                "high",
                idx,
                "SwiftData `@Relationship` declared without explicit `deleteRule:`",
                "Specify `deleteRule:` (`.cascade`, `.nullify`, `.deny`) explicitly and cover deletion invariants in schema migration tests.",
            )
        if re.search(r"\btry!\s+ModelContainer\b", line):
            add(
                "AT-004",
                "swiftdata-persistence-and-migration",
                "high",
                idx,
                "Unconditional `try! ModelContainer` crashes user launches on store corruption or schema drift",
                "Catch `ModelContainer` initialization errors, run `SchemaMigrationPlan`, and present a recoverable user error state.",
            )
        if re.search(r"\b(?:CKContainer\.default\s*\(\s*\)|UNUserNotificationCenter\.current\s*\(\s*\))", line) and "protocol " not in source:
            add(
                "AT-008",
                "feature-generators-and-apple-platform-integration",
                "medium",
                idx,
                "Direct system capability singleton call without protocol seam prevents offline/permission-denied testing",
                "Wrap CloudKit / Push Notifications behind an injected protocol adapter with deterministic permission-denied and offline test doubles.",
            )
        if re.search(r"\b(?:LanguageModelSession|SystemLanguageModel|VNCoreMLRequest)\b", line) and not re.search(
            r"(?:#available\b|\b(?:isAvailable|availability|fallback)\b)", source
        ):
            add(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                "high",
                idx,
                "Apple Intelligence / Core ML invoked without availability check and non-AI fallback",
                "Gate with `#available` + runtime model `availability` check and provide a deterministic fallback path for unsupported devices.",
            )

    for line_no, func_name, body in _extract_test_blocks(source):
        if not re.search(r"(?:\b(?:XCTAssert\w*|XCTFail|XCTUnwrap|withKnownIssue)\b|#(?:expect|require)\b)", body):
            add(
                "AT-005",
                "test-contracts-and-regression-gates",
                "high",
                line_no,
                f"Test `{func_name}` contains no behavioral assertion (`#expect` / `XCTAssert*`)",
                "Assert observable outputs, state transitions, or thrown error contracts rather than executing assertionless smoke calls.",
            )
        if re.search(r"\b(?:Task\.sleep|Thread\.sleep|usleep)\s*\(", body):
            add(
                "AT-006",
                "test-contracts-and-regression-gates",
                "medium",
                line_no,
                f"Test `{func_name}` relies on wall-clock sleep synchronization",
                "Inject a deterministic `Clock` / scheduler or await explicit `confirmation` / `XCTestExpectation` signals.",
            )

    uncommented_lines = [raw.split("//", 1)[0] for raw in lines]
    uncommented = "\n".join(uncommented_lines)
    dock_line = 0
    for idx, line in enumerate(uncommented_lines, start=1):
        if re.search(
            r"\b(?:bottomCapsule\w*|floatingCapsule\w*|floatingDock\w*|bottomDock\w*|FinAppRootShell)\b",
            line,
        ):
            dock_line = idx
            break
    if dock_line == 0:
        for idx, line in enumerate(uncommented_lines, start=1):
            window = "\n".join(uncommented_lines[max(0, idx - 4) : min(len(uncommented_lines), idx + 4)])
            if (
                re.search(r"\b(?:safeAreaInset\s*\(\s*edge\s*:\s*\.bottom|overlay\s*\(\s*alignment\s*:\s*\.bottom)\b", line)
                and re.search(r"\b(?:Capsule|glassEffect|GlassEffectContainer|dock|Tab)\b", window, re.IGNORECASE)
            ):
                dock_line = idx
                break
    if dock_line == 0:
        is_root_shell = bool(re.search(r"\b(?:RootShell|AppRoot|MainShell|RootNavigation)\w*\s*:\s*View\b", uncommented))
        if is_root_shell:
            for idx, line in enumerate(uncommented_lines, start=1):
                if re.search(r"\bTabView\b", line):
                    dock_line = idx
                    break

    if dock_line > 0:
        has_platform_split = bool(re.search(r"#if\s+os\s*\(\s*(?:macOS|iOS)\s*\)", uncommented))
        has_macos_desktop_nav = bool(
            re.search(r"\b(?:NavigationSplitView|ToolbarItem|CommandMenu|Commands)\b", uncommented)
            and re.search(r"\.keyboardShortcut\s*\(", uncommented)
        )
        macos_has_bottom_capsule = False
        active_os: str | None = None
        for line in uncommented_lines:
            stripped = line.strip()
            if re.match(r"^#if\s+os\s*\(\s*macOS\s*\)", stripped):
                active_os = "macOS"
            elif re.match(r"^#if\s+os\s*\(\s*iOS\s*\)", stripped):
                active_os = "iOS"
            elif re.match(r"^#elseif\s+os\s*\(\s*macOS\s*\)", stripped):
                active_os = "macOS"
            elif re.match(r"^#elseif\s+os\s*\(\s*iOS\s*\)", stripped):
                active_os = "iOS"
            elif stripped.startswith("#else"):
                active_os = "macOS" if active_os == "iOS" else ("iOS" if active_os == "macOS" else None)
            elif stripped.startswith("#endif"):
                active_os = None
            elif active_os == "macOS" and re.search(
                r"\b(?:bottomCapsule\w*|floatingCapsule\w*|floatingDock\w*|bottomDock\w*|safeAreaInset\s*\(\s*edge\s*:\s*\.bottom)\b",
                line,
            ):
                macos_has_bottom_capsule = True

        ios_only_guard = has_platform_split and not bool(re.search(r"os\s*\(\s*macOS\s*\)|#else\b", uncommented))
        if macos_has_bottom_capsule or (not ios_only_guard and (not has_platform_split or not has_macos_desktop_nav)):
            add(
                "AT-011",
                "ios-architecture-navigation-migration",
                "high",
                dock_line,
                "macos_ios_navigation_not_diverged: Cross-platform root/bottom-dock navigation lacks macOS vs iOS divergence (`#if os(macOS)` / `#if os(iOS)`)",
                "Split root navigation chrome by platform: iOS uses bottom floating glass capsule dock / bottom `TabView` + single-column `NavigationStack`; macOS uses `NavigationSplitView` or top Commander `ToolbarItem` + multi-column layout + `⌘1..⌘4` `.keyboardShortcut` with zero bottom mobile capsule dock.",
            )

    return findings


def _paths_overlap(path_a: str, path_b: str) -> bool:
    a = path_a.strip("/").strip()
    b = path_b.strip("/").strip()
    if not a or not b:
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def check_capability_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []

    def fail(rule_id: str, card_slug: str, code: str, detail: str) -> None:
        violations.append({"rule_id": rule_id, "card_slug": card_slug, "code": code, "detail": detail})

    raw_caps = matrix.get("capabilities")
    if raw_caps is not None and not isinstance(raw_caps, list):
        fail(
            "AT-010",
            "apple-intelligence-and-special-platforms",
            "invalid_capabilities_list",
            "`capabilities` must be a JSON array of capability objects.",
        )
        raw_caps = []

    for idx, cap in enumerate(raw_caps or []):
        if not isinstance(cap, dict):
            fail(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                f"malformed_capability_entry:{idx}",
                f"`capabilities[{idx}]` must be a JSON object.",
            )
            continue
        raw_name = cap.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else f"<unnamed:{idx}>"
        if not isinstance(raw_name, str) or not raw_name.strip():
            fail(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                f"missing_capability_name:{idx}",
                f"`capabilities[{idx}]` requires a non-empty string `name`.",
            )
        if cap.get("availability_checked") is not True:
            fail(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                f"missing_availability_check:{name}",
                f"Capability `{name}` must check OS/device/model availability (`availability_checked: true`) before invocation.",
            )
        fallback = cap.get("fallback_mode")
        if not isinstance(fallback, str) or not fallback.strip():
            fail(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                f"missing_fallback_mode:{name}",
                f"Capability `{name}` must define a non-empty string `fallback_mode` for unsupported hardware or disabled permissions.",
            )
        if cap.get("permission_denied_handled") is not True:
            fail(
                "AT-009",
                "feature-generators-and-apple-platform-integration",
                f"missing_permission_denied_path:{name}",
                f"Capability `{name}` must test and handle permission-denied / restricted states (`permission_denied_handled: true`).",
            )
        timeout_ms = cap.get("offline_or_timeout_budget_ms")
        mem_mb = cap.get("memory_budget_mb")
        if not isinstance(timeout_ms, (int, float)) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            fail(
                "AT-009",
                "feature-generators-and-apple-platform-integration",
                f"missing_timeout_budget:{name}",
                f"Capability `{name}` requires positive numeric `offline_or_timeout_budget_ms`.",
            )
        if not isinstance(mem_mb, (int, float)) or isinstance(mem_mb, bool) or mem_mb <= 0:
            fail(
                "AT-010",
                "apple-intelligence-and-special-platforms",
                f"missing_memory_budget:{name}",
                f"Capability `{name}` requires positive numeric `memory_budget_mb`.",
            )

    raw_packages = matrix.get("work_packages")
    if raw_packages is not None and not isinstance(raw_packages, list):
        fail(
            "AT-007",
            "refactor-review-and-skill-audit",
            "invalid_work_packages_list",
            "`work_packages` must be a JSON array of work-package objects.",
        )
        raw_packages = []

    packages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, pkg in enumerate(raw_packages or []):
        if not isinstance(pkg, dict):
            fail(
                "AT-007",
                "refactor-review-and-skill-audit",
                f"malformed_work_package:{idx}",
                f"`work_packages[{idx}]` must be a JSON object.",
            )
            continue
        raw_id = pkg.get("id")
        pid = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else f"WP{idx+1}"
        if not isinstance(raw_id, str) or not raw_id.strip():
            fail("AT-007", "refactor-review-and-skill-audit", f"missing_package_id:{idx}", f"`work_packages[{idx}]` requires a non-empty string `id`.")
        elif pid in seen_ids:
            fail("AT-007", "refactor-review-and-skill-audit", f"duplicate_package_id:{pid}", f"Duplicate work package ID `{pid}`.")
        seen_ids.add(pid)
        packages.append({**pkg, "_pid": pid})

    dep_graph: dict[str, set[str]] = {}
    for pkg in packages:
        pid = pkg["_pid"]
        owned = pkg.get("owned_paths")
        if not isinstance(owned, list) or not owned or not all(isinstance(p, str) and p.strip() for p in owned):
            fail("AT-007", "refactor-review-and-skill-audit", f"empty_owned_paths:{pid}", f"Work package `{pid}` must declare non-empty string `owned_paths`.")
        verify_cmd = pkg.get("verify_cmd")
        if not isinstance(verify_cmd, str) or not verify_cmd.strip():
            fail("AT-007", "refactor-review-and-skill-audit", f"missing_verify_cmd:{pid}", f"Work package `{pid}` must declare a non-empty string `verify_cmd`.")
        raw_deps = pkg.get("depends_on") or []
        if not isinstance(raw_deps, list) or not all(isinstance(d, str) and d.strip() for d in raw_deps):
            fail("AT-007", "refactor-review-and-skill-audit", f"invalid_depends_on:{pid}", f"Work package `{pid}` `depends_on` must be a list of package IDs.")
            dep_graph[pid] = set()
        else:
            deps = {d.strip() for d in raw_deps}
            unknown = sorted(deps - seen_ids)
            if unknown:
                fail("AT-007", "refactor-review-and-skill-audit", f"unknown_dependency:{pid}", f"Work package `{pid}` depends on unknown package(s): {', '.join(unknown)}.")
            dep_graph[pid] = deps & seen_ids

    # Detect dependency cycles and compute transitive ancestors in the DAG
    visiting: set[str] = set()
    visited: set[str] = set()
    ancestors: dict[str, set[str]] = {pid: set() for pid in dep_graph}
    has_cycle = False

    def dfs(node: str) -> set[str]:
        nonlocal has_cycle
        if node in visited:
            return ancestors[node]
        if node in visiting:
            has_cycle = True
            return set()
        visiting.add(node)
        reach: set[str] = set()
        for dep in dep_graph.get(node, ()):
            reach.add(dep)
            reach.update(dfs(dep))
        visiting.remove(node)
        visited.add(node)
        ancestors[node] = reach
        return reach

    for pid in dep_graph:
        dfs(pid)
    if has_cycle:
        fail(
            "AT-007",
            "refactor-review-and-skill-audit",
            "dependency_cycle_detected",
            "`work_packages` contains a dependency cycle; refactor waves must form a strict DAG.",
        )

    for i, pkg in enumerate(packages):
        pid = pkg["_pid"]
        owned = pkg.get("owned_paths") if isinstance(pkg.get("owned_paths"), list) else []
        for other in packages[i + 1 :]:
            oid = other["_pid"]
            # Serialized if either transitively depends on the other (and graph is acyclic)
            if not has_cycle and (oid in ancestors.get(pid, set()) or pid in ancestors.get(oid, set())):
                continue
            other_owned = other.get("owned_paths") if isinstance(other.get("owned_paths"), list) else []
            for pa in owned:
                for pb in other_owned:
                    if isinstance(pa, str) and isinstance(pb, str) and _paths_overlap(pa, pb):
                        fail(
                            "AT-007",
                            "refactor-review-and-skill-audit",
                            f"parallel_path_overlap:{pid}:{oid}",
                            f"Concurrent work packages `{pid}` and `{oid}` overlap on `{pa}` vs `{pb}` without DAG serialization.",
                        )

    if matrix.get("proposes_new_skill") is True and matrix.get("skill_audit_checked_existing") is not True:
        fail(
            "AT-007",
            "refactor-review-and-skill-audit",
            "missing_skill_dedup_audit",
            "Creating a new skill requires `skill_audit_checked_existing = true` to prevent overlapping skill sprawl.",
        )

    return {
        "ok": len(violations) == 0,
        "skill": "ios-architecture-testing",
        "violation_count": len(violations),
        "violations": violations,
    }


def _probe_arch_rule_fires(rule_id: str) -> bool:
    source_probes = {
        "AT-001": "struct V: View { var body: some View { NavigationView { Text(\"x\") } } }",
        "AT-002": "struct V: View { var body: some View { Button { } label: { Image(systemName: \"gear\") } } }",
        "AT-003": "@Model final class Folder { @Relationship var items: [Item] = [] }",
        "AT-004": "func boot() { _ = try! ModelContainer(for: Folder.self) }",
        "AT-005": "@Test func empty() { let x = 1; _ = x }",
        "AT-006": "@Test func sleepy() async throws { #expect(true); try await Task.sleep(for: .milliseconds(10)) }",
        "AT-008": "func sync() { _ = CKContainer.default() }",
        "AT-010": "func ask() { _ = LanguageModelSession() }",
        "AT-011": "struct AppRootShell: View { var body: some View { NavigationStack { Content() }.overlay(alignment: .bottom) { Capsule().glassEffect() } } }",
    }
    if rule_id in source_probes:
        return rule_id in {item["rule_id"] for item in audit_source(source_probes[rule_id])}
    matrix_report = check_capability_matrix(
        {
            "capabilities": [
                {
                    "name": "ai",
                    "availability_checked": False,
                    "fallback_mode": None,
                    "permission_denied_handled": False,
                    "offline_or_timeout_budget_ms": 0,
                    "memory_budget_mb": 0,
                }
            ],
            "work_packages": [
                {"id": "WP1", "owned_paths": ["Sources/Core"], "verify_cmd": "swift test"},
                {"id": "WP2", "owned_paths": ["Sources/Core/Sub"], "verify_cmd": "swift test"},
            ],
        }
    )
    return rule_id in {item["rule_id"] for item in matrix_report["violations"]}


def check_card_coverage() -> dict[str, Any]:
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    ref_md = (SKILL_DIR / "references" / "architecture-data-testing-cards.md").read_text(encoding="utf-8")

    missing: list[str] = []
    covered: list[dict[str, Any]] = []
    for card in CARDS:
        slug = card["slug"]
        in_skill = f"{slug}.md" in skill_md
        in_ref = card["concept_path"] in ref_md
        live_rules = all(_probe_arch_rule_fires(rule) for rule in card["rules"])
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
        "skill": "ios-architecture-testing",
        "expected_cards": len(CARDS),
        "covered_cards": sum(1 for item in covered if item["covered"]),
        "missing_cards": missing,
        "cards": covered,
    }


REFERENCE_PATH = "skills/ios-architecture-testing/references/architecture-data-testing-cards.md"


class _SelfDisclosingArchTestingParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(
            f"error: {message}\n"
            "remediation: choose a supported subcommand or provide a valid source/matrix target.\n"
            f"Reference: {REFERENCE_PATH}",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _with_default_command(argv: list[str]) -> list[str]:
    if not argv:
        return ["check-card-coverage"]

    commands = {
        "list-cards",
        "cards",
        "check-card-coverage",
        "coverage",
        "check-coverage",
        "audit-source",
        "audit",
        "scan",
        "check-source",
        "check-capability-matrix",
        "check-matrix",
        "matrix",
        "audit-matrix",
    }
    if argv[0] in commands:
        return argv

    matrix_flags = ("--matrix-json", "--matrix", "--spec-json", "--json-file")
    source_flags = ("--path", "--root", "--dir", "--source", "--file", "--snippet")
    if any(arg == flag or arg.startswith(f"{flag}=") for arg in argv for flag in matrix_flags):
        return ["check-capability-matrix", *argv]
    if any(arg == flag or arg.startswith(f"{flag}=") for arg in argv for flag in source_flags):
        return ["audit-source", *argv]
    if argv == ["--json"]:
        return ["check-card-coverage", *argv]
    if not argv[0].startswith("-"):
        return ["audit-source", *argv]
    return argv


def _runtime_error(error: str, remediation: str, **fields: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "skill": "ios-architecture-testing",
        **fields,
        "error": error,
        "remediation": remediation,
        "reference": REFERENCE_PATH,
    }


def _print_runtime_error(error: str, remediation: str, **fields: Any) -> int:
    print(json.dumps(_runtime_error(error, remediation, **fields)), file=sys.stderr)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingArchTestingParser(
        description="Audit iOS architecture, SwiftData migrations, test contracts, batch refactors, and platform capabilities."
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_SelfDisclosingArchTestingParser)

    sub.add_parser("list-cards", aliases=["cards"], help="List the 6 mapped TIA-3 cards and deterministic rule IDs.").add_argument("--json", action="store_true")
    sub.add_parser("check-card-coverage", aliases=["coverage", "check-coverage"], help="Verify 100%% 6-card coverage across SKILL.md, references/, and script rules.").add_argument("--json", action="store_true")

    audit_p = sub.add_parser("audit-source", aliases=["audit", "scan", "check-source"], help="Scan Swift source/tests for navigation, SwiftData, test-contract, seam, and fallback defects.")
    audit_p.add_argument("--path", "--root", "--dir", "--source", "--file", dest="option_paths", action="append", default=[], help="Swift file or directory to scan.")
    audit_p.add_argument("paths", nargs="*", help="Swift files or directories to scan.")
    audit_p.add_argument("--snippet", default=None, help="Inline Swift snippet to scan.")
    audit_p.add_argument("--strict", action="store_true", help="Exit with code 2 when findings are non-empty.")
    audit_p.add_argument("--json", action="store_true", help="Keep JSON output (the default).")

    mat_p = sub.add_parser("check-capability-matrix", aliases=["check-matrix", "matrix", "audit-matrix"], help="Validate Apple Intelligence/platform fallback matrices and batch-refactor work packages.")
    mat_p.add_argument("--matrix-json", "--matrix", "--spec-json", "--json-file", dest="matrix_json", help="Inline JSON string or path to a JSON file.")
    mat_p.add_argument("matrix_arg", nargs="?", help="Inline JSON string or path to a JSON file.")
    mat_p.add_argument("--strict", action="store_true", help="Exit with code 2 when violations are non-empty.")
    mat_p.add_argument("--json", action="store_true", help="Keep JSON output (the default).")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(_with_default_command(raw_argv))
    if args.command in {"list-cards", "cards"}:
        print(json.dumps({"ok": True, "skill": "ios-architecture-testing", "count": len(CARDS), "cards": list(CARDS)}, indent=2))
        return 0

    if args.command in {"check-card-coverage", "coverage", "check-coverage"}:
        report = check_card_coverage()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    if args.command in {"check-capability-matrix", "check-matrix", "matrix", "audit-matrix"}:
        matrix_arg = args.matrix_json or args.matrix_arg
        if not matrix_arg:
            return _print_runtime_error(
                "invalid_matrix_json:no matrix supplied",
                "provide JSON with --matrix-json (or --matrix) or as the matrix positional argument.",
                violation_count=0,
                violations=[],
            )
        candidate = Path(matrix_arg)
        try:
            raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else matrix_arg
            matrix_data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            return _print_runtime_error(
                f"invalid_matrix_json:{exc}",
                "provide valid inline JSON or a readable JSON file path.",
                violation_count=0,
                violations=[],
            )
        if not isinstance(matrix_data, dict):
            return _print_runtime_error(
                "matrix_must_be_object",
                "provide a JSON object containing capabilities and/or work_packages.",
                violation_count=0,
                violations=[],
            )
        report = check_capability_matrix(matrix_data)
        if args.strict and not report["ok"]:
            report.update(
                reference=REFERENCE_PATH,
                remediation="fix the reported rule violations, then rerun without --strict to inspect the JSON report.",
            )
            print(json.dumps(report, indent=2), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2))
        return 0

    targets: list[tuple[str, str]] = []
    if args.snippet is not None:
        targets.append(("<snippet>", args.snippet))
    for raw in [*args.option_paths, *args.paths]:
        p = Path(raw)
        if not p.exists():
            return _print_runtime_error(
                f"path_not_found:{raw}",
                "provide an existing Swift file/directory, or use --snippet for inline source.",
                finding_count=0,
                findings=[],
            )
        if p.is_file():
            targets.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
        else:
            for swift_file in sorted(p.rglob("*.swift")):
                targets.append((str(swift_file), swift_file.read_text(encoding="utf-8", errors="replace")))

    if not targets:
        return _print_runtime_error(
            "no_source_supplied",
            "provide a Swift path/positional target or use --snippet with inline source.",
            finding_count=0,
            findings=[],
        )

    findings: list[dict[str, Any]] = []
    for label, content in targets:
        findings.extend(audit_source(content, label=label))

    payload = {
        "ok": len(findings) == 0,
        "skill": "ios-architecture-testing",
        "files_scanned": len(targets),
        "finding_count": len(findings),
        "findings": findings,
    }
    if args.strict and findings:
        payload.update(
            reference=REFERENCE_PATH,
            remediation="fix the reported findings, then rerun the source audit to confirm a clean contract.",
        )
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
