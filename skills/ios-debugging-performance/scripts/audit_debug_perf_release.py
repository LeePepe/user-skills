#!/usr/bin/env python3
"""Deterministic iOS/macOS debugging hygiene, Instruments performance anti-pattern, SwiftPM MenuBar bundle, and App Store rejection gate checker."""
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
        "index": 6,
        "slug": "ios-debugger-review-swarm",
        "concept_path": "knowledge-vault/concepts/ios-debugger-review-swarm.md",
        "origin": "dimillian-skills",
        "focus": "Reproducible bug isolation, minimal proof experiment, multi-role review/bug-hunt swarm, fail-first regression test",
        "rules": ["DP-001", "DP-002"],
    },
    {
        "index": 7,
        "slug": "instruments-performance-and-swiftui-debugging",
        "concept_path": "knowledge-vault/concepts/instruments-performance-and-swiftui-debugging.md",
        "origin": "claude-code-apple-skills + dimillian-skills",
        "focus": "5-bucket Instruments profiling (CPU, Allocations, Layout, I/O, View-Body updates), single-variable experiments, regression budgets",
        "rules": ["DP-003", "DP-004"],
    },
    {
        "index": 8,
        "slug": "macos-swiftpm-menubar-packaging",
        "concept_path": "knowledge-vault/concepts/macos-swiftpm-menubar-packaging.md",
        "origin": "dimillian-skills",
        "focus": "Pure SwiftPM macOS MenuBar .app assembly, Info.plist/LSUIElement/arch check, Developer ID codesign + notarization + launch smoke test",
        "rules": ["DP-005", "DP-006"],
    },
    {
        "index": 9,
        "slug": "app-store-release-security-rejection-gates",
        "concept_path": "knowledge-vault/concepts/app-store-release-security-rejection-gates.md",
        "origin": "claude-code-apple-skills",
        "focus": "Archive/IPA pre-submission gates: permission purpose strings, PrivacyInfo.xcprivacy Required Reason APIs, account deletion, metadata parity",
        "rules": ["DP-007", "DP-008"],
    },
)

REQUIRED_REASON_API_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NSPrivacyAccessedAPICategoryUserDefaults", re.compile(r"\bUserDefaults\.(?:standard|init)\b")),
    ("NSPrivacyAccessedAPICategoryFileTimestamp", re.compile(r"\.(?:creationDate|modificationDate|contentModificationDateKey)\b")),
    ("NSPrivacyAccessedAPICategorySystemBootTime", re.compile(r"\bProcessInfo\.processInfo\.systemUptime\b")),
    ("NSPrivacyAccessedAPICategoryDiskSpace", re.compile(r"\.(?:systemFreeSize|volumeAvailableCapacityForImportantUsageKey)\b")),
)


def audit_source(source: str, label: str = "<snippet>", has_privacy_manifest: bool = True) -> list[dict[str, Any]]:
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

    for idx, line in enumerate(lines, start=1):
        stripped = line.split("//", 1)[0]
        if re.search(r"\b(?:let\s+_\s*=\s*try\?|_\s*=\s*try\?)", stripped) or re.search(
            r"\bcatch\s*\{\s*\}", stripped
        ):
            add(
                "DP-001",
                "ios-debugger-review-swarm",
                "high",
                idx,
                "Silent error swallowing (`_ = try?` or empty `catch {}`) destroys root-cause evidence chains",
                "Log structured failure context (`Logger`) or propagate typed errors so bug-hunt/repro traces remain observable.",
            )
        if re.search(r"\btry!\s+", stripped):
            add(
                "DP-002",
                "ios-debugger-review-swarm",
                "high",
                idx,
                "Unconditional `try!` crash path in production code",
                "Handle errors explicitly with recovery state or test-only `XCTUnwrap` / `#require`.",
            )
        if re.search(r"\bNSCache\s*<[^>]+>\s*\(\s*\)", stripped) and not re.search(
            r"\b(?:countLimit|totalCostLimit)\b", source
        ):
            add(
                "DP-003",
                "instruments-performance-and-swiftui-debugging",
                "medium",
                idx,
                "NSCache initialized without `countLimit` or `totalCostLimit` risks memory pressure & eviction churn",
                "Configure explicit `countLimit` and `totalCostLimit` and verify memory peak + hit rate in Instruments Allocations.",
            )
        if re.search(r"\b(?:UIImage|NSImage)\s*\(\s*contentsOfFile\s*:|\bData\s*\(\s*contentsOf\s*:", stripped):
            add(
                "DP-004",
                "instruments-performance-and-swiftui-debugging",
                "high",
                idx,
                "Synchronous file/network Data or image decoding risks main-thread hangs and scroll hitches",
                "Decode and downsample thumbnails asynchronously off the main thread and verify with Instruments Time Profiler.",
            )
        if re.search(r"\bFileManager\.default\.currentDirectoryPath\b", stripped):
            add(
                "DP-005",
                "macos-swiftpm-menubar-packaging",
                "high",
                idx,
                "Process working-directory assumption breaks double-clicked macOS `.app` bundles",
                "Resolve packaged resources through `Bundle.main` / `Bundle.module` or `URL.applicationSupportDirectory`.",
            )
        if not has_privacy_manifest:
            for category, pattern in REQUIRED_REASON_API_PATTERNS:
                if pattern.search(stripped):
                    add(
                        "DP-007",
                        "app-store-release-security-rejection-gates",
                        "high",
                        idx,
                        f"Required Reason API ({category}) used without PrivacyInfo.xcprivacy manifest",
                        f"Declare `{category}` with an approved reason code in `PrivacyInfo.xcprivacy` before archiving.",
                    )

    return findings


def verify_bundle_spec(spec: dict[str, Any]) -> dict[str, Any]:
    mode = str(spec.get("mode", "app_store")).strip().lower()
    violations: list[dict[str, str]] = []

    def fail(rule_id: str, card_slug: str, code: str, detail: str) -> None:
        violations.append({"rule_id": rule_id, "card_slug": card_slug, "code": code, "detail": detail})

    if mode in {"macos_menubar", "both"}:
        plist = spec.get("info_plist") if isinstance(spec.get("info_plist"), dict) else {}
        for key in ("CFBundleIdentifier", "CFBundleName", "CFBundleShortVersionString", "CFBundleExecutable"):
            if not str(plist.get(key, "")).strip():
                fail("DP-006", "macos-swiftpm-menubar-packaging", f"missing_plist_{key}", f"Info.plist missing non-empty `{key}`.")
        if plist.get("LSUIElement") is not True:
            fail(
                "DP-006",
                "macos-swiftpm-menubar-packaging",
                "menubar_missing_lsuielement",
                "MenuBar `.app` must set `LSUIElement = true` to suppress unintended Dock icon / app-switcher artifacts.",
            )
        if not spec.get("executable_present", False):
            fail("DP-006", "macos-swiftpm-menubar-packaging", "missing_executable", "Bundle `Contents/MacOS/<Executable>` is missing.")
        if not spec.get("resources_copied", True):
            fail("DP-006", "macos-swiftpm-menubar-packaging", "missing_resources", "SwiftPM resource bundles were not copied into `Contents/Resources`.")
        archs = spec.get("architectures") or []
        if not isinstance(archs, list) or not archs:
            fail("DP-006", "macos-swiftpm-menubar-packaging", "missing_architectures", "At least one verified Mach-O architecture (`arm64`/`x86_64`) is required.")
        if spec.get("distribution", "direct") == "direct":
            if not spec.get("codesigned", False):
                fail("DP-006", "macos-swiftpm-menubar-packaging", "unsigned_bundle", "Developer ID `codesign` verification is required before distribution.")
            if not spec.get("notarized_and_stapled", False):
                fail("DP-006", "macos-swiftpm-menubar-packaging", "unnotarized_bundle", "Notarization + `xcrun stapler staple` is required for Gatekeeper launch.")
        if not spec.get("launch_smoke_tested", False):
            fail("DP-006", "macos-swiftpm-menubar-packaging", "missing_launch_smoke_test", "Run a non-repo-cwd `.app` launch smoke test before release.")

    if mode in {"app_store", "both"}:
        if str(spec.get("build_configuration", "")).lower() != "release":
            fail(
                "DP-008",
                "app-store-release-security-rejection-gates",
                "non_release_archive",
                "App Store release review must inspect a `Release` archive/IPA, never a `Debug` build.",
            )
        if not spec.get("privacy_manifest_present", False):
            fail(
                "DP-007",
                "app-store-release-security-rejection-gates",
                "missing_privacy_manifest",
                "`PrivacyInfo.xcprivacy` must be bundled in the archive.",
            )
        used_apis = set(spec.get("required_reason_apis") or [])
        declared_apis = set(spec.get("declared_reason_apis") or [])
        undeclared = sorted(used_apis - declared_apis)
        if undeclared:
            fail(
                "DP-007",
                "app-store-release-security-rejection-gates",
                "undeclared_required_reason_apis",
                f"Required Reason APIs missing from PrivacyInfo.xcprivacy: {', '.join(undeclared)}",
            )
        plist = spec.get("info_plist") if isinstance(spec.get("info_plist"), dict) else {}
        for perm_key in spec.get("permissions_used") or []:
            purpose = str(plist.get(perm_key, "")).strip()
            if len(purpose) < 12 or purpose.lower() in {"access needed", "permission required", "tbd", "todo"}:
                fail(
                    "DP-008",
                    "app-store-release-security-rejection-gates",
                    f"invalid_permission_purpose_{perm_key}",
                    f"`{perm_key}` must explain concrete user-facing purpose (>=12 chars, non-placeholder).",
                )
        if spec.get("supports_account_creation", False) and not spec.get("supports_account_deletion", False):
            fail(
                "DP-008",
                "app-store-release-security-rejection-gates",
                "missing_account_deletion",
                "Apps supporting account creation must provide in-app account deletion (App Store Review Guideline 5.1.1(v)).",
            )
        privacy_url = str(spec.get("privacy_policy_url", "")).strip()
        if not privacy_url.startswith("https://"):
            fail(
                "DP-008",
                "app-store-release-security-rejection-gates",
                "invalid_privacy_policy_url",
                "A live `https://` privacy policy URL is required for App Store submission.",
            )

    if mode not in {"macos_menubar", "app_store", "both"}:
        fail("DP-008", "app-store-release-security-rejection-gates", "invalid_mode", f"Unsupported mode `{mode}`; expected `macos_menubar`, `app_store`, or `both`.")

    return {
        "ok": len(violations) == 0,
        "skill": "ios-debugging-performance",
        "mode": mode,
        "violation_count": len(violations),
        "violations": violations,
    }


def _probe_rule_fires(rule_id: str) -> bool:
    source_probes = {
        "DP-001": "func f() { let _ = try? save() }",
        "DP-002": "func f() { _ = try! save() }",
        "DP-003": "func f() { let c = NSCache<NSString, NSData>(); _ = c }",
        "DP-004": "func f(u: URL) throws { _ = try Data(contentsOf: u) }",
        "DP-005": "func f() { _ = FileManager.default.currentDirectoryPath }",
        "DP-007": "func f() { _ = UserDefaults.standard.string(forKey: \"k\") }",
    }
    if rule_id in source_probes:
        return rule_id in {
            item["rule_id"] for item in audit_source(source_probes[rule_id], has_privacy_manifest=False)
        }
    spec_report = verify_bundle_spec(
        {
            "mode": "both",
            "build_configuration": "Debug",
            "privacy_manifest_present": False,
            "info_plist": {},
            "supports_account_creation": True,
            "supports_account_deletion": False,
        }
    )
    return rule_id in {item["rule_id"] for item in spec_report["violations"]}


def check_card_coverage() -> dict[str, Any]:
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    ref_md = (SKILL_DIR / "references" / "debugging-perf-release-cards.md").read_text(encoding="utf-8")

    missing: list[str] = []
    covered: list[dict[str, Any]] = []
    for card in CARDS:
        slug = card["slug"]
        in_skill = f"{slug}.md" in skill_md
        in_ref = card["concept_path"] in ref_md
        live_rules = all(_probe_rule_fires(rule) for rule in card["rules"])
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
        "skill": "ios-debugging-performance",
        "expected_cards": len(CARDS),
        "covered_cards": sum(1 for item in covered if item["covered"]),
        "missing_cards": missing,
        "cards": covered,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit iOS/macOS debugging, Instruments performance, SwiftPM MenuBar packaging, and App Store release gates.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-cards", help="List the 4 mapped TIA-3 cards and deterministic rule IDs.")
    sub.add_parser("check-card-coverage", help="Verify 100%% 4-card coverage across SKILL.md, references/, and script rules.")

    audit_p = sub.add_parser("audit-source", help="Scan Swift source for silent error swallowing, main-thread I/O, cwd bugs, or missing privacy manifests.")
    audit_p.add_argument("--path", action="append", default=[], help="Swift file or directory to scan.")
    audit_p.add_argument("--snippet", default=None, help="Inline Swift snippet to scan.")
    audit_p.add_argument("--missing-privacy-manifest", action="store_true", help="Flag Required Reason APIs when PrivacyInfo.xcprivacy is absent.")
    audit_p.add_argument("--strict", action="store_true", help="Exit with code 2 when findings are non-empty.")

    spec_p = sub.add_parser("verify-bundle-spec", help="Validate pure-SwiftPM macOS MenuBar bundle or App Store release-readiness JSON.")
    spec_p.add_argument("--spec-json", required=True, help="Inline JSON string or path to a JSON file.")
    spec_p.add_argument("--strict", action="store_true", help="Exit with code 2 when violations are non-empty.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list-cards":
        print(json.dumps({"ok": True, "skill": "ios-debugging-performance", "count": len(CARDS), "cards": list(CARDS)}, indent=2))
        return 0

    if args.command == "check-card-coverage":
        report = check_card_coverage()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    if args.command == "verify-bundle-spec":
        candidate = Path(args.spec_json)
        try:
            raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else args.spec_json
            spec_data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"invalid_spec_json:{exc}"}), file=sys.stderr)
            return 2
        if not isinstance(spec_data, dict):
            print(json.dumps({"ok": False, "error": "spec_must_be_object"}), file=sys.stderr)
            return 2
        report = verify_bundle_spec(spec_data)
        if args.strict and not report["ok"]:
            print(json.dumps(report, indent=2), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2))
        return 0

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

    findings: list[dict[str, Any]] = []
    for label, content in targets:
        findings.extend(audit_source(content, label=label, has_privacy_manifest=not args.missing_privacy_manifest))

    payload = {
        "ok": len(findings) == 0,
        "skill": "ios-debugging-performance",
        "files_scanned": len(targets),
        "finding_count": len(findings),
        "findings": findings,
    }
    if args.strict and findings:
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
