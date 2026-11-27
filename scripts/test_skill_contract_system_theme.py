#!/usr/bin/env python3
"""Regression tests for the Valve 1 custom-theme-plumbing repo-audit gate in skill-contract."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/skill-contract/scripts/skill_contract.py"
SCRIPTS_DIR = ROOT / "skills/skill-contract/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from contract_core import core_common, core_facts  # noqa: E402


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class SkillContractSystemThemeTests(unittest.TestCase):
    def _write_swift(self, repo: Path, rel: str, content: str) -> Path:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_valve1_documentation_and_finding_guidance_wired(self) -> None:
        self.assertIn("custom-theme-plumbing", core_common.VALVE_FINDING_GUIDANCE)
        ref, hint = core_common.VALVE_FINDING_GUIDANCE["custom-theme-plumbing"]
        self.assertEqual(
            ref,
            "skills/skill-contract/references/valves-and-metabolism.md#valve-1--sink-deterministic-checks",
        )
        self.assertIn("skills/my-designer/references/appearance-ledger.md", hint)

        valves_doc = ROOT / "skills/skill-contract/references/valves-and-metabolism.md"
        text = valves_doc.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 160)
        self.assertLessEqual(valves_doc.stat().st_size, 12 * 1024)
        self.assertIn("custom-theme-plumbing", text)
        self.assertIn("skills/my-designer/references/appearance-ledger.md", text)

    def test_positive_replay_clean_os_native_swift_app_passes_strict_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-theme-clean-") as raw:
            repo = Path(raw)
            self._write_swift(
                repo,
                "Apps/DemoApp/ContentView.swift",
                (
                    "import SwiftUI\n"
                    "struct ContentView: View {\n"
                    "    @Environment(\\.colorScheme) private var colorScheme\n"
                    "    var body: some View {\n"
                    "        Text(\"OS Native\")\n"
                    "            .foregroundStyle(Color.primary)\n"
                    "            .background(Color(.windowBackgroundColor))\n"
                    "            .environment(\\.theme, Theme(palette: .systemSemantic))\n"
                    "    }\n"
                    "}\n"
                ),
            )
            # Comments (including nested /* /* */ */), multiline/raw string literals, Tests/, and explicit // design-contract-allow lines must not false-positive.
            self._write_swift(
                repo,
                "Sources/DesignKit/LegacyMigrationNote.swift",
                (
                    "import SwiftUI\n"
                    "// Removed legacy .designTheme(.dark) and .environment(\\.theme, Theme(isDark: true))\n"
                    "/* outer comment\n"
                    "   /* nested inner comment */\n"
                    "   view.designTheme(.dark)\n"
                    "   view.environment(\\.theme, Theme(palette: makePalette(), isDark: true))\n"
                    "*/\n"
                    "let docNote = \".designTheme(custom) or .environment(\\\\.theme, Theme(isDark: false))\"\n"
                    "let multiNote = \"\"\"\n"
                    "Example: \".designTheme(.dark)\" and .environment(\\.theme, Theme(isDark: true))\n"
                    "\"\"\"\n"
                    "let rawNote = #\"Example: \".designTheme(.dark)\" and Theme(isDark: true)\"#\n"
                    "func migratedBridge<V: View>(_ view: V) -> some View {\n"
                    "    view.designTheme(.system) // design-contract-allow: transitional shim\n"
                    "}\n"
                ),
            )
            self._write_swift(
                repo,
                "Apps/DemoAppTests/ThemeSnapshotTests.swift",
                (
                    "import SwiftUI\n"
                    "func renderFixture() {\n"
                    "    _ = EmptyView().designTheme(.dark)\n"
                    "    _ = EmptyView().environment(\\.theme, Theme(name: \"dark\", isDark: true))\n"
                    "}\n"
                ),
            )
            self._write_swift(
                repo,
                "Packages/FeatureKit/Tests/FeatureKitTests/ThemeTests.swift",
                "func testLegacy() { _ = view.designTheme(.light) }\n",
            )

            proc = run_cli("audit", "--repo", str(repo), "--strict", "--no-cache")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["findings"], [])

    def test_counter_case_replay_catches_design_theme_and_manual_isdark_plumbing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-theme-dirty-") as raw:
            repo = Path(raw)
            self._write_swift(
                repo,
                "Apps/VitalApp/RootView.swift",
                (
                    "import SwiftUI\n"
                    "struct RootView: View {\n"
                    "    var body: some View {\n"
                    "        DashboardView()\n"
                    "            .designTheme(appTheme)\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write_swift(
                repo,
                "Sources/AppShell/WindowHost.swift",
                (
                    "import SwiftUI\n"
                    "struct WindowHost: View {\n"
                    "    var isDarkMode: Bool\n"
                    "    var body: some View {\n"
                    "        ContentView()\n"
                    "            .environment(\n"
                    "                \\.theme,\n"
                    "                Theme(palette: makePalette(accent: Color(.sRGB, red: 0.2, green: 0.4, blue: 0.8)), isDark: isDarkMode)\n"
                    "            )\n"
                    "    }\n"
                    "}\n"
                ),
            )
            self._write_swift(
                repo,
                "Packages/SharedUI/Sources/SharedUI/CardView.swift",
                "struct CardView { func apply() { root.designTheme(.midnight) } }\n",
            )

            direct = core_facts.check_repo_appearance_hygiene(repo)
            self.assertEqual(len(direct), 3)

            proc = run_cli("audit", "--repo", str(repo), "--strict", "--no-cache")
            self.assertEqual(proc.returncode, 1)
            self.assertIn(
                "skills/skill-contract/references/valves-and-metabolism.md#valve-1--sink-deterministic-checks",
                proc.stderr,
            )
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            findings = payload["findings"]
            self.assertEqual(len(findings), 3)
            files = {item["file"] for item in findings}
            self.assertEqual(
                files,
                {
                    "Apps/VitalApp/RootView.swift",
                    "Sources/AppShell/WindowHost.swift",
                    "Packages/SharedUI/Sources/SharedUI/CardView.swift",
                },
            )
            for item in findings:
                self.assertEqual(item["code"], "custom-theme-plumbing")
                self.assertEqual(
                    item["reference"],
                    "skills/skill-contract/references/valves-and-metabolism.md#valve-1--sink-deterministic-checks",
                )
                self.assertEqual(
                    item["guidance_drawer"],
                    "skills/my-designer/references/appearance-ledger.md",
                )
                self.assertIn("skills/my-designer/references/appearance-ledger.md", item["hint"])

    def test_string_interpolation_retains_executable_code_while_masking_literals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-theme-interp-") as raw:
            repo = Path(raw)
            source = (
                'let cleanNestedRaw = "\\(f(#"a"b"#)) .designTheme(.dark)"\n'
                'let cleanNestedMulti = "\\(f(\"\"\"\nquote " inside .designTheme(.dark)\n\"\"\")) .designTheme(.dark)"\n'
                'let dirtyPlain = "probe: \\(view.designTheme(.dark))"\n'
                'let dirtyNestedRaw = "\\(f(#"x"))"z"#, view.designTheme(.dark)))"\n'
                'let allowedNestedRaw = "\\(f(#"x"))"z"#, view.designTheme(.dark)))" // design-contract-allow\n'
            )
            self._write_swift(repo, "Sources/Feature/Interpolated.swift", source)
            masked = core_facts._mask_swift_non_code(source)
            self.assertEqual(len(masked), len(source))
            self.assertEqual(masked.count("\n"), source.count("\n"))

            findings = core_facts.check_repo_appearance_hygiene(repo)
            self.assertEqual(len(findings), 2)
            self.assertEqual([f["line"] for f in findings], [5, 6])
            self.assertTrue(all(f["code"] == "custom-theme-plumbing" for f in findings))

    def test_watermark_cache_invalidates_when_swift_source_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sc-theme-cache-") as raw:
            repo = Path(raw)
            swift_file = self._write_swift(
                repo,
                "Sources/Feature/Screen.swift",
                "import SwiftUI\nstruct Screen: View { var body: some View { Text(\"Clean\") } }\n",
            )
            first = run_cli("audit", "--repo", str(repo), "--strict")
            self.assertEqual(first.returncode, 0, first.stderr)

            swift_file.write_text(
                "import SwiftUI\nstruct Screen: View { var body: some View { Text(\"Dirty\").designTheme(.dark) } }\n",
                encoding="utf-8",
            )
            second = run_cli("audit", "--repo", str(repo), "--strict")
            self.assertEqual(second.returncode, 1)
            self.assertEqual(json.loads(second.stdout)["findings"][0]["code"], "custom-theme-plumbing")


if __name__ == "__main__":
    unittest.main()
