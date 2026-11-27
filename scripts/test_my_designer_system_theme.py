#!/usr/bin/env python3
"""Dual-Case Replay & boundary regression tests for OS-native system appearance in skills/my-designer."""
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills/my-designer"
VERIFY = SKILL_DIR / "scripts/verify_design_cases.py"
APPEARANCE_LEDGER = SKILL_DIR / "references/appearance-ledger.md"
SKILL_MD = SKILL_DIR / "SKILL.md"
CASES_JSON = SKILL_DIR / "scripts/data/defect-cases/index.json"
EVAL_JSON = SKILL_DIR / "references/eval-cases.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_out(proc: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(proc.stdout)


class MyDesignerSystemThemeTests(unittest.TestCase):
    def test_appearance_ledger_and_skill_md_respect_budgets_and_boundary(self) -> None:
        skill_text = SKILL_MD.read_text(encoding="utf-8")
        ledger_text = APPEARANCE_LEDGER.read_text(encoding="utf-8")
        self.assertLessEqual(len(skill_text.splitlines()), 80)
        self.assertLessEqual(len(skill_text.encode("utf-8")), 12 * 1024)
        self.assertLessEqual(len(ledger_text.splitlines()), 160)
        self.assertLessEqual(len(ledger_text.encode("utf-8")), 12 * 1024)
        self.assertIn("OS-Native Dynamic Appearance Rule", ledger_text)
        self.assertIn("OS-native", skill_text)

        forbidden_worker_tokens = (
            "ENABLE_DEBUG_DYLIB",
            "OTHER_SWIFT_FLAGS",
            "SWIFT_ACTIVE_COMPILATION_CONDITIONS",
            "Package.swift",
            ".xcodeproj",
            "xcodebuild",
            "xcodegen",
            "project.yml",
        )
        for doc in (SKILL_MD, *(SKILL_DIR / "references").glob("*.md")):
            content = doc.read_text(encoding="utf-8")
            for token in forbidden_worker_tokens:
                self.assertNotIn(token, content, f"{doc.name} leaked worker/build token {token!r}")

        for py_file in (SKILL_DIR / "scripts/design_cases_core").glob("*.py"):
            self.assertLessEqual(len(py_file.read_text(encoding="utf-8").splitlines()), 750, py_file)

    def test_drawers_stay_within_15_case_cap_with_one_in_one_out(self) -> None:
        defect_doc = json.loads(CASES_JSON.read_text(encoding="utf-8"))
        eval_doc = json.loads(EVAL_JSON.read_text(encoding="utf-8"))
        self.assertLessEqual(len(defect_doc["active_cases"]), 15)
        self.assertLessEqual(len(eval_doc["active_cases"]), 15)
        self.assertIn("DC-020", {c["id"] for c in defect_doc["active_cases"]})
        self.assertIn("DC-001", {c["id"] for c in defect_doc.get("archived_cases", [])})
        self.assertIn("EV-020", {c["id"] for c in eval_doc["active_cases"]})

        lookup = _run("lookup-defect", "designTheme")
        self.assertEqual(lookup.returncode, 0, lookup.stderr)
        self.assertIn("DC-020", {m["id"] for m in _json_out(lookup)["matches"]})

    def test_audit_code_catches_custom_theme_plumbing_and_honors_token_test_waiver(self) -> None:
        bad_design_theme = """
import SwiftUI
struct AppRootView: View {
    var body: some View {
        WorkspaceView()
            .designTheme(customTheme)
    }
}
"""
        bad_env_theme = """
import SwiftUI
struct SheetHostView: View {
    var body: some View {
        DetailSheet()
            .environment(\\.theme, Theme(name: "dusk", isDark: true))
    }
}
"""
        for platform in ("desktop", "mobile"):
            for snippet in (bad_design_theme, bad_env_theme):
                with self.subTest(platform=platform, snippet=snippet.strip().splitlines()[1]):
                    res = _run("audit-code", "--platform", platform, "--snippet", snippet)
                    self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                    payload = _json_out(res)
                    self.assertFalse(payload["ok"])
                    case_ids = {f["case_id"] for f in payload["findings"]}
                    self.assertIn("UI_CUSTOM_THEME_PLUMBING", case_ids)
                    self.assertIn("DC-020", case_ids)
                    self.assertIn(
                        "skills/my-designer/references/appearance-ledger.md",
                        payload.get("reference_drawers", []),
                    )
                    self.assertTrue(
                        any("appearance-ledger.md" in line for line in payload.get("guidance", [])),
                        payload.get("guidance"),
                    )

        waived_and_clean = """
import SwiftUI
struct CleanProductView: View {
    @Environment(\\.colorScheme) private var colorScheme
    var body: some View {
        // .designTheme(commentedOutShouldNotTrigger)
        let literal = ".environment(\\\\.theme, Theme(isDark: false))"
        Text(literal)
    }
}
struct LowLevelTokenTests {
    func verifyTokenResolution() {
        _ = TokenProbeView().designTheme(fixtureTheme) // design-contract-allow: low-level token fixture
        _ = TokenProbeView().environment(\\.theme, Theme(isDark: true)) // design-contract-allow
    }
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            swift_path = Path(tmp) / "CleanAndWaived.swift"
            swift_path.write_text(waived_and_clean, encoding="utf-8")
            clean_res = _run("audit-code", "--platform", "mobile", str(swift_path))
            self.assertEqual(clean_res.returncode, 0, clean_res.stdout + clean_res.stderr)
            clean_payload = _json_out(clean_res)
            self.assertTrue(clean_payload["ok"])
            self.assertEqual(clean_payload["findings"], [])

    def test_audit_spec_flags_custom_in_app_theme_switchers_and_allows_os_tracking(self) -> None:
        base_spec = (
            "Desktop NavigationSplitView; mobile NavigationStack.\n"
            "States: empty, loading, error.\n"
            "Deprecated Test Clauses: N/A.\n"
        )
        bad_clauses = (
            "Add a custom light/dark theme toggle in the settings toolbar.",
            "Provide an in-app appearance switcher so users can flip dark/light mode privately.",
            "Implement a custom theme state machine for per-sheet dark mode.",
            "在顶部栏新增应用内深浅色切换开关。",
        )
        good_clauses = (
            "Appearance tracks native OS light/dark appearance automatically; forbid custom in-app light/dark theme switchers.",
            "Do not add an in-app dark/light theme toggle; semantic VS-005 / CL-006 tokens follow system appearance.",
            "严禁内置自定义深浅色切换器，明暗外观完全跟随系统。",
        )
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "design.md"
            for clause in bad_clauses:
                with self.subTest(bad=clause):
                    spec_path.write_text(base_spec + clause + "\n", encoding="utf-8")
                    soft = _run("audit-spec", str(spec_path))
                    self.assertEqual(soft.returncode, 0, soft.stderr)
                    soft_payload = _json_out(soft)
                    codes = {w["code"] for w in soft_payload["spec_gap_warnings"]}
                    self.assertEqual(codes, {"SPEC_GAP_CUSTOM_THEME_TOGGLE"})
                    self.assertIn(
                        "skills/my-designer/references/appearance-ledger.md",
                        soft_payload.get("reference_drawers", []),
                    )
                    strict = _run("audit-spec", "--strict-spec-gaps", str(spec_path))
                    self.assertEqual(strict.returncode, 1, strict.stderr)
                    self.assertFalse(_json_out(strict)["ok"])

            for clause in good_clauses:
                with self.subTest(good=clause):
                    spec_path.write_text(base_spec + clause + "\n", encoding="utf-8")
                    ok_res = _run("audit-spec", "--strict-spec-gaps", str(spec_path))
                    self.assertEqual(ok_res.returncode, 0, ok_res.stdout + ok_res.stderr)
                    ok_payload = _json_out(ok_res)
                    self.assertTrue(ok_payload["ok"])
                    self.assertEqual(ok_payload["spec_gap_warnings"], [])


if __name__ == "__main__":
    unittest.main()
