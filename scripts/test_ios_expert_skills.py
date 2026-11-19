#!/usr/bin/env python3
"""Regression tests for the 3 independent iOS skills promoted from the 15 TIA-3 cards."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SKILLS_DIR: Path = REPO_ROOT / "skills"
SKILL_CONTRACT: Path = SKILLS_DIR / "skill-contract" / "scripts" / "skill_contract.py"

EXPECTED_CARD_MAP: dict[str, tuple[str, ...]] = {
    "ios-concurrency-swiftui": (
        "swift-concurrency-expert-coding",
        "swift-concurrency-memory-safety",
        "swiftui-architecture-and-performance",
        "swiftui-state-layout-and-view-structure",
        "apple-design-and-game-feel",
    ),
    "ios-debugging-performance": (
        "ios-debugger-review-swarm",
        "instruments-performance-and-swiftui-debugging",
        "macos-swiftpm-menubar-packaging",
        "app-store-release-security-rejection-gates",
    ),
    "ios-architecture-testing": (
        "ios-architecture-navigation-migration",
        "swiftdata-persistence-and-migration",
        "test-contracts-and-regression-gates",
        "refactor-review-and-skill-audit",
        "feature-generators-and-apple-platform-integration",
        "apple-intelligence-and-special-platforms",
    ),
}

SCRIPT_PATHS: dict[str, Path] = {
    "ios-concurrency-swiftui": SKILLS_DIR / "ios-concurrency-swiftui" / "scripts" / "audit_concurrency_swiftui.py",
    "ios-debugging-performance": SKILLS_DIR / "ios-debugging-performance" / "scripts" / "audit_debug_perf_release.py",
    "ios-architecture-testing": SKILLS_DIR / "ios-architecture-testing" / "scripts" / "audit_arch_testing.py",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IOSExpertSkillsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.concurrency = _load_module("ios_concurrency_swiftui_mod", SCRIPT_PATHS["ios-concurrency-swiftui"])
        cls.debugging = _load_module("ios_debugging_performance_mod", SCRIPT_PATHS["ios-debugging-performance"])
        cls.architecture = _load_module("ios_architecture_testing_mod", SCRIPT_PATHS["ios-architecture-testing"])

    def test_fifteen_tia3_cards_partitioned_exhaustively_across_three_skills(self) -> None:
        all_slugs: list[str] = []
        for skill, expected_slugs in EXPECTED_CARD_MAP.items():
            script = SCRIPT_PATHS[skill]
            proc = subprocess.run(
                [sys.executable, str(script), "check-card-coverage"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["expected_cards"], len(expected_slugs))
            self.assertEqual(payload["covered_cards"], len(expected_slugs))
            actual_slugs = tuple(item["slug"] for item in payload["cards"])
            self.assertEqual(actual_slugs, expected_slugs)
            all_slugs.extend(actual_slugs)

        self.assertEqual(len(all_slugs), 15)
        self.assertEqual(len(set(all_slugs)), 15)

        # Ensure Topics 3 (emilkowalski-skills) and 4 (Kinsoku Shori) were NOT consumed as card sources
        for skill in EXPECTED_CARD_MAP:
            for md_file in (SKILLS_DIR / skill).rglob("*.md"):
                text = md_file.read_text(encoding="utf-8")
                self.assertNotIn("emilkowalski-skills", text)
                self.assertNotIn("Kinsoku Shori", text)

    def test_concurrency_swiftui_source_auditor_catches_and_clears_expected_patterns(self) -> None:
        bad = """
        final class TokenStore: @unchecked Sendable {}
        @MainActor
        final class FeedModel {
            func load(url: URL) throws -> Data { try Data(contentsOf: url) }
        }
        struct FeedView: View {
            let items: [String]
            var body: some View {
                GeometryReader { _ in
                    let formatter = DateFormatter()
                    ForEach(items.indices, id: \\.self) { idx in
                        Text(items[idx])
                            .animation(.spring(duration: 0.3), value: idx)
                            .onAppear {
                                Task.detached { [unowned self] in _ = formatter }
                            }
                    }
                }
            }
        }
        """
        findings = self.concurrency.audit_swift_source(bad)
        rules = {item["rule_id"] for item in findings}
        self.assertTrue({"CS-001", "CS-002", "CS-003", "CS-004", "CS-005", "CS-006", "CS-007", "CS-009"}.issubset(rules), rules)

        good = """
        // SAFETY: OSAllocatedUnfairLock serializes all mutable access.
        final class LockedBox: @unchecked Sendable {}
        @MainActor
        final class ScreenModel {
            var title = "Ready"
            nonisolated func decodeOffMain(url: URL) throws -> Data {
                try Data(contentsOf: url)
            }
        }
        struct Coordinator {
            @MainActor var badgeText: String = ""
            func backgroundRead(url: URL) throws -> Data {
                try Data(contentsOf: url)
            }
        }
        actor DiskStore {
            func read(url: URL) throws -> Data { try Data(contentsOf: url) }
        }
        struct ItemRow: View {
            @Environment(\\.accessibilityReduceMotion) private var reduceMotion
            let items: [FeedItem]
            var body: some View {
                ForEach(items) { item in
                    Text(item.title)
                        .animation(reduceMotion ? nil : .spring(duration: 0.25), value: item.isUnread)
                }
            }
        }
        """
        self.assertEqual(self.concurrency.audit_swift_source(good), [])

    def test_debugging_perf_release_auditor_and_bundle_gates(self) -> None:
        bad_swift = """
        func loadAvatar(path: String) {
            let _ = try? riskyWrite()
            let _ = try! mustWork()
            let cache = NSCache<NSString, NSData>()
            let data = try? Data(contentsOf: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
            _ = UserDefaults.standard.string(forKey: path)
            _ = (cache, data)
        }
        """
        findings = self.debugging.audit_source(bad_swift, has_privacy_manifest=False)
        rules = {item["rule_id"] for item in findings}
        self.assertEqual(rules, {"DP-001", "DP-002", "DP-003", "DP-004", "DP-005", "DP-007"})

        bad_bundle = self.debugging.verify_bundle_spec(
            {
                "mode": "both",
                "build_configuration": "Debug",
                "executable_present": False,
                "resources_copied": False,
                "architectures": [],
                "distribution": "direct",
                "codesigned": False,
                "notarized_and_stapled": False,
                "launch_smoke_tested": False,
                "privacy_manifest_present": False,
                "required_reason_apis": ["NSPrivacyAccessedAPICategoryUserDefaults"],
                "declared_reason_apis": [],
                "permissions_used": ["NSCameraUsageDescription"],
                "info_plist": {"NSCameraUsageDescription": "TBD"},
                "supports_account_creation": True,
                "supports_account_deletion": False,
                "privacy_policy_url": "http://example.com",
            }
        )
        self.assertFalse(bad_bundle["ok"])
        codes = {item["code"] for item in bad_bundle["violations"]}
        self.assertIn("menubar_missing_lsuielement", codes)
        self.assertIn("undeclared_required_reason_apis", codes)
        self.assertIn("missing_account_deletion", codes)

    def test_architecture_testing_auditor_and_capability_matrix(self) -> None:
        bad_arch = """
        struct LegacyRoot: View {
            var body: some View {
                NavigationView {
                    Button { } label: { Image(systemName: "bell") }
                }
            }
        }
        @Model final class Bucket {
            @Relationship var notes: [Note] = []
        }
        func setup() {
            _ = try! ModelContainer(for: Bucket.self)
            _ = CKContainer.default()
            _ = LanguageModelSession()
        }
        @Test func emptyCheck() async throws {
            try await Task.sleep(for: .milliseconds(20))
        }
        """
        findings = self.architecture.audit_source(bad_arch)
        rules = {item["rule_id"] for item in findings}
        self.assertEqual(rules, {"AT-001", "AT-002", "AT-003", "AT-004", "AT-005", "AT-006", "AT-008", "AT-010"})

        valid_swift_testing = """
        @Test func routerResolvesDeepLink() throws {
            let route = try #require(Router.parse("app://item/42"))
            #expect(route == .detail(42))
        }
        """
        self.assertEqual(self.architecture.audit_source(valid_swift_testing), [])

        serialized = self.architecture.check_capability_matrix(
            {
                "capabilities": [
                    {
                        "name": "on_device_summary",
                        "availability_checked": True,
                        "fallback_mode": "plain_excerpt",
                        "permission_denied_handled": True,
                        "offline_or_timeout_budget_ms": 2000,
                        "memory_budget_mb": 80,
                    }
                ],
                "work_packages": [
                    {"id": "WP1", "owned_paths": ["Sources/Core"], "verify_cmd": "swift test --filter CoreTests"},
                    {
                        "id": "WP2",
                        "owned_paths": ["Sources/Feature"],
                        "depends_on": ["WP1"],
                        "verify_cmd": "swift test --filter FeatureTests",
                    },
                    {
                        "id": "WP3",
                        "owned_paths": ["Sources/Core/Sub"],
                        "depends_on": ["WP2"],
                        "verify_cmd": "swift test --filter SubTests",
                    },
                ],
                "proposes_new_skill": True,
                "skill_audit_checked_existing": True,
            }
        )
        self.assertTrue(serialized["ok"], serialized)

        null_and_cycle = self.architecture.check_capability_matrix(
            {
                "capabilities": [
                    None,
                    {
                        "name": "broken_ai",
                        "availability_checked": True,
                        "fallback_mode": None,
                        "permission_denied_handled": True,
                        "offline_or_timeout_budget_ms": 1000,
                        "memory_budget_mb": 64,
                    },
                ],
                "work_packages": [
                    {"id": "A", "owned_paths": ["Sources/Shared"], "depends_on": ["B"], "verify_cmd": "swift test"},
                    {"id": "B", "owned_paths": ["Sources/Shared"], "depends_on": ["A"], "verify_cmd": "swift test"},
                ],
            }
        )
        self.assertFalse(null_and_cycle["ok"])
        codes = {item["code"] for item in null_and_cycle["violations"]}
        self.assertIn("malformed_capability_entry:0", codes)
        self.assertIn("missing_fallback_mode:broken_ai", codes)
        self.assertIn("dependency_cycle_detected", codes)

    def test_skill_contract_audit_eval_and_offline_description_eval_pass_for_all_three(self) -> None:
        for skill in EXPECTED_CARD_MAP:
            drawer = json.loads((SKILLS_DIR / skill / "references" / "eval-cases.json").read_text(encoding="utf-8"))
            mock_table = [bool(item["should_trigger"]) for item in drawer["trigger_evals"]]
            for subcmd in (
                ["audit", "--skill", skill, "--strict"],
                ["eval", "--skill", skill, "--strict"],
                ["description-eval", "--skill", skill, "--mock-jev-json", json.dumps(mock_table), "--strict"],
            ):
                proc = subprocess.run(
                    [sys.executable, str(SKILL_CONTRACT), *subcmd],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"{skill} {' '.join(subcmd)} failed:\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
