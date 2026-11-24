"""Positive and counter-case replays for the iOS architecture-testing CLI contract."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "skills/ios-architecture-testing/scripts/audit_arch_testing.py"
REFERENCE = "skills/ios-architecture-testing/references/architecture-data-testing-cards.md"


class ArchitectureTestingRetroLocksTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(AUDITOR), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_json_stdout(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(result.stderr, "", result.stderr)
        return json.loads(result.stdout)

    def test_positive_replays_accept_default_aliases_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            swift_file = Path(temp_dir) / "Clean.swift"
            swift_file.write_text("struct Clean: View { var body: some View { NavigationStack { Text(\\\"x\\\") } } }", encoding="utf-8")
            cases = (
                (),
                ("--strict", "--path", str(swift_file)),
                ("--root", str(swift_file)),
                ("--dir", str(swift_file)),
                ("audit", str(swift_file), "--json"),
                ("scan", str(swift_file)),
                ("check-source", str(swift_file)),
            )
            for args in cases:
                with self.subTest(args=args):
                    result = self.run_cli(*args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(self.assert_json_stdout(result)["ok"])

    def test_positive_replays_accept_matrix_aliases_and_json(self) -> None:
        matrix = json.dumps({"capabilities": []})
        for args in (("--matrix", matrix, "--json"), ("check-matrix", matrix), ("matrix", matrix), ("audit-matrix", matrix)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.assert_json_stdout(result)["violation_count"], 0)

    def test_counter_case_unknown_flag_is_self_disclosing(self) -> None:
        result = self.run_cli("--unknown-flag")
        self.assertEqual(result.returncode, 2)
        self.assertLessEqual(len(result.stderr.splitlines()), 3)
        self.assertIn(REFERENCE, result.stderr)
        self.assertIn("remediation", result.stderr)

    def test_counter_case_missing_path_is_self_disclosing(self) -> None:
        result = self.run_cli("--path", "/tmp/ios-architecture-testing-does-not-exist.swift")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"], "path_not_found:/tmp/ios-architecture-testing-does-not-exist.swift")
        self.assertEqual(payload["reference"], REFERENCE)
        self.assertIn("remediation", payload)

    def test_counter_case_strict_source_violation_is_self_disclosing(self) -> None:
        result = self.run_cli("--strict", "--snippet", 'NavigationView { Text("legacy") }')
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertIn("AT-001", {finding["rule_id"] for finding in payload["findings"]})
        self.assertEqual(payload["reference"], REFERENCE)
        self.assertIn("remediation", payload)

    def test_counter_case_each_source_rule_still_fails_strict(self) -> None:
        probes = {
            "AT-001": "NavigationView { Text(\"x\") }",
            "AT-002": 'Button { } label: { Image(systemName: "gear") }',
            "AT-003": "@Relationship var items: [Item] = []",
            "AT-004": "try! ModelContainer(for: Folder.self)",
            "AT-005": "@Test func empty() { let x = 1; _ = x }",
            "AT-006": "@Test func sleepy() { #expect(true); Task.sleep() }",
            "AT-008": "CKContainer.default()",
            "AT-010": "LanguageModelSession()",
            "AT-011": "struct AppRootShell: View { var body: some View { TabView { Text(\"x\") } } }",
        }
        for rule_id, snippet in probes.items():
            with self.subTest(rule_id=rule_id):
                result = self.run_cli("audit-source", "--strict", "--snippet", snippet)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(rule_id, result.stderr)
                self.assertIn(REFERENCE, result.stderr)

    def test_counter_case_matrix_rules_still_fail_strict(self) -> None:
        matrix = json.dumps(
            {
                "capabilities": [{"name": "ai", "availability_checked": False, "fallback_mode": "", "permission_denied_handled": False, "offline_or_timeout_budget_ms": 0, "memory_budget_mb": 0}],
                "work_packages": [
                    {"id": "WP1", "owned_paths": ["Sources/Shared"], "verify_cmd": "swift test"},
                    {"id": "WP2", "owned_paths": ["Sources/Shared/Sub"], "verify_cmd": "swift test"},
                ],
                "proposes_new_skill": True,
                "skill_audit_checked_existing": False,
            }
        )
        result = self.run_cli("check-capability-matrix", "--strict", "--matrix-json", matrix)
        self.assertEqual(result.returncode, 2)
        for rule_id in ("AT-007", "AT-009", "AT-010"):
            self.assertIn(rule_id, result.stderr)
        self.assertIn(REFERENCE, result.stderr)

    def test_counter_case_clean_platform_divergence_has_no_false_positive(self) -> None:
        source = """#if os(macOS)
struct MacShell: View { var body: some View { NavigationSplitView { Sidebar() } detail: { Detail() }.toolbar { ToolbarItem { Button("1") {}.keyboardShortcut("1", modifiers: .command) } } } }
#elseif os(iOS)
struct IOSShell: View { var body: some View { NavigationStack { Content() }.safeAreaInset(edge: .bottom) { Capsule().glassEffect() } } }
#endif
"""
        result = self.run_cli("--strict", "--snippet", source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.assert_json_stdout(result)["finding_count"], 0)


if __name__ == "__main__":
    unittest.main()
