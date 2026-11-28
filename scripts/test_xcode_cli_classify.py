#!/usr/bin/env python3
"""Positive and counter-case replay for Apple change and verify-gate routing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "xcode-cli"
SCRIPT = SKILL_DIR / "scripts" / "classify_changes.py"
SPEC = importlib.util.spec_from_file_location("xcode_classify", SCRIPT)
assert SPEC and SPEC.loader
CLASSIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLASSIFIER)


class ClassifyChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="classify-test-")
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.git("init", "--quiet", "-b", "main")
        self.git("config", "user.name", "Task Writer")
        self.git("config", "user.email", "task@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        self.write("Package.swift", "// Package fixture\n")
        self.write("Sources/Core.swift", "let value = 0\n")
        self.write("README.md", "Fixture\n")
        self.commit()
        self.git("checkout", "--quiet", "-b", "feature")

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.PIPE, text=True).strip()

    def write(self, path: str, text: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self) -> None:
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              capture_output=True, text=True, timeout=15)

    def payload(self, *args: str) -> dict:
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_repository_aliases_json_and_positional_worktree(self) -> None:
        self.write("Sources/Core.swift", "let value = 1\n")
        for alias in ("--repo", "--repo-dir", "--worktree", "--cwd"):
            with self.subTest(alias=alias):
                data = self.payload(alias, str(self.repo), "--json")
                self.assertEqual((data["route"], data["packageRoots"]), ("swiftpm", ["."]))
        self.assertEqual(self.payload(str(self.repo))["changedFiles"], ["Sources/Core.swift"])
        self.assertEqual(self.payload("--repo", str(self.repo), "project.yml")["changedFiles"], ["project.yml"])

    def test_clean_branch_auto_detects_origin_main_and_local_main(self) -> None:
        self.write("Sources/Core.swift", "let value = 1\n")
        self.commit()
        for remote in (False, True):
            with self.subTest(remote=remote):
                if remote:
                    self.git("update-ref", "refs/remotes/origin/main", "main")
                data = self.payload()
                self.assertEqual((data["route"], data["changedFiles"]), ("swiftpm", ["Sources/Core.swift"]))
        self.git("checkout", "--quiet", "main")
        self.assertEqual(self.payload()["route"], "none")

    def test_dirty_scope_and_committed_only_preserve_distinct_modes(self) -> None:
        self.write("Sources/Core.swift", "let value = 1\n")
        self.commit()
        self.write("App/App.entitlements", "<plist/>\n")
        self.git("add", "App/App.entitlements")
        self.write("App/Info.plist", "<plist/>\n")
        self.assertEqual(self.payload()["changedFiles"], ["App/App.entitlements", "App/Info.plist"])
        self.assertEqual(self.payload("--base", "main")["route"], "mixed")
        data = self.payload("--committed-only")
        self.assertEqual((data["route"], data["changedFiles"]), ("swiftpm", ["Sources/Core.swift"]))
        self.git("checkout", "--quiet", "main")
        self.assertEqual(self.payload("--committed-only")["route"], "none")

    def test_head_aliases_select_requested_commit(self) -> None:
        self.write("Sources/Core.swift", "let value = 1\n")
        self.commit()
        self.git("branch", "selected")
        self.write("App/App.entitlements", "<plist/>\n")
        self.commit()
        for head in ("--head", "--head-ref"):
            with self.subTest(head=head):
                data = self.payload("--base-ref", "main", head, "selected", "--committed-only")
                self.assertEqual(data["changedFiles"], ["Sources/Core.swift"])
        self.assertEqual(self.payload("--head", "selected", "--committed-only")["route"], "swiftpm")

    def test_two_and_three_dot_ranges_on_diverged_branches(self) -> None:
        self.write("Sources/Core.swift", "let value = 1\n")
        self.commit()
        self.git("checkout", "--quiet", "main")
        self.write("App/App.entitlements", "<plist/>\n")
        self.commit()
        self.git("checkout", "--quiet", "feature")
        for flag in ("--range", "--diff-range"):
            with self.subTest(flag=flag):
                self.assertEqual(self.payload(flag, "main..feature")["route"], "mixed")
                self.assertEqual(self.payload(flag, "main...feature")["changedFiles"], ["Sources/Core.swift"])
        self.assertEqual(self.payload("--base", "main")["route"], "swiftpm")

    def test_no_default_base_is_not_an_error(self) -> None:
        self.git("branch", "-D", "main")
        self.assertEqual(self.payload()["route"], "none")
        self.write("Sources/Core.swift", "let value = 1\n")
        self.assertEqual(self.payload()["route"], "swiftpm")

    def test_parser_and_git_failures_self_disclose_in_three_lines(self) -> None:
        cases = [
            ["--unknown"], ["--repo"], ["--repo", "missing-repository"],
            ["--base", "missing-ref"], ["--head-ref", "missing-ref"],
            ["--diff-range", "main"], ["--range", "main..missing-ref"],
            ["--base", "main", "--range", "main..HEAD"], ["--head", "HEAD", "--range", "main..HEAD"],
            ["--base", ""], ["--register-case"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                lines = result.stderr.splitlines()
                self.assertEqual(len(lines), 3)
                self.assertIn("Fix:", lines[1])
                self.assertIn("command-routing.md", lines[2])
                self.assertIn("build-cases.json", lines[2])

    def health(self, verify: str = "", hook: str = "", hook_path: str = ".githooks/pre-push") -> dict:
        if verify:
            self.write("scripts/verify", verify)
        if hook:
            self.write(hook_path, hook)
        return CLASSIFIER.verify_gate_health(self.repo)

    def test_verify_gate_positive_fast_spm_tiering_and_standard_output(self) -> None:
        verify = '''swift build
swift test --filter CoreTests
changed_layers=$(git diff --name-only HEAD)
if [ "${RUN_XCODEBUILD:-0}" = "1" ]; then
  xcodebuild test -derivedDataPath .build/DerivedData
fi
'''
        health = self.health(verify, 'exec "${ROOT}/scripts/verify"\n')
        self.assertEqual(health["tier"], "local_fast_spm")
        self.assertTrue(health["has_verify_script"])
        self.assertTrue(health["uses_incremental_layer_selection"])
        self.assertTrue(health["has_derived_data_or_build_lock"])
        self.assertFalse(health["prepush_blocks_on_unconditional_xcodebuild"])
        self.assertEqual(self.payload("--check-verify-gate"), {"verifyGateHealth": health})
        self.assertEqual(self.payload("README.md")["verifyGateHealth"], health)

    def test_verify_gate_direct_and_delegated_blocking_hooks(self) -> None:
        for path in (".githooks/pre-push", "scripts/hooks/pre-push"):
            with self.subTest(path=path):
                target = self.repo / path
                self.addCleanup(target.unlink, missing_ok=True)
                health = self.health("swift test\nxcodebuild test\n", "bash scripts/verify\n", path)
                self.assertEqual(health["tier"], "blocking_prepush_xcodebuild")
                target.unlink()
        health = self.health(hook="xcrun xcodebuild test-without-building\n")
        self.assertTrue(health["prepush_blocks_on_unconditional_xcodebuild"])

    def test_verify_gate_guards_scope_branches_and_short_circuit(self) -> None:
        guarded_hooks = [
            'if [[ "${RUN_HEAVY:-0}" == 1 ]]; then xcodebuild test; fi\n',
            'if [ "$mode" = ci ]; then xcodebuild test; fi\n',
            'if [ "$RUN_HEAVY" != 1 ]; then swift test; else xcodebuild test; fi\n',
            '[ "$RUN_XCODEBUILD" = 1 ] && xcodebuild test\n',
            '[ "$RUN_HEAVY" != 1 ] || xcodebuild test\n',
            'heavy() { xcodebuild test; }\nif [ "$RUN_HEAVY" = 1 ]; then heavy; fi\n',
            'unused() { xcodebuild test; }\nswift test\n',
        ]
        for hook in guarded_hooks:
            with self.subTest(hook=hook):
                self.assertFalse(self.health(hook=hook)["prepush_blocks_on_unconditional_xcodebuild"])
        blocking_hooks = [
            'if [ "$RUN_HEAVY" = 1 ]; then echo enabled; fi\nxcodebuild test\n',
            'if [ "$RUN_HEAVY" = 1 ]; then swift test; else xcodebuild test; fi\n',
            'if [[ "$RUN_HEAVY" = 1 || "$ALWAYS" = 1 ]]; then xcodebuild test; fi\n',
            'heavy() { xcodebuild test; }\nheavy\n',
            'if ! [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n',
            'if [[ "$RUN_HEAVY" != 1 && "$OTHER" = 1 ]]; then swift test; else xcodebuild test; fi\n',
            'echo "$RUN_HEAVY = 1" && xcodebuild test\n',
        ]
        for hook in blocking_hooks:
            with self.subTest(hook=hook):
                self.assertTrue(self.health(hook=hook)["prepush_blocks_on_unconditional_xcodebuild"])

    def test_verify_gate_forced_ci_or_env_opt_in_is_still_blocking(self) -> None:
        cases = [
            ('if [ "$mode" = ci ]; then xcodebuild test; fi\n', 'scripts/verify --mode ci\n'),
            ('if [ "$mode" = ci ]; then xcodebuild test; fi\n', 'scripts/verify --mode=ci\n'),
            ('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', 'RUN_HEAVY=1 scripts/verify\n'),
            ('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', 'RUN_HEAVY=1\nscripts/verify\n'),
            ('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', 'export RUN_HEAVY=1\nscripts/verify\n'),
            ('if [ "$RUN_XCODEBUILD" = 1 ]; then xcodebuild test; fi\n', 'export RUN_XCODEBUILD=1\nscripts/verify\n'),
            ('if [ "$RUN_XCODEBUILD" = 1 ]; then xcodebuild test; fi\n', 'RUN_XCODEBUILD=1\nscripts/verify\n'),
            ('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', 'helper() { scripts/verify; }\nexport RUN_HEAVY=1\nhelper\n'),
            ('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', 'helper() { export RUN_HEAVY=1; scripts/verify; }\nhelper\n'),
            ('heavy() { if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi; }\nheavy\n', 'RUN_HEAVY=1 scripts/verify\n'),
        ]
        for verify, hook in cases:
            with self.subTest(hook=hook):
                self.assertTrue(self.health(verify, hook)["prepush_blocks_on_unconditional_xcodebuild"])
                self.assertFalse(self.health(verify, 'if [ "$RUN_HEAVY" = 1 ]; then ' + hook.strip() + '; fi\n')["prepush_blocks_on_unconditional_xcodebuild"])
        for hook in ('export RUN_HEAVY=0\nscripts/verify\n', 'echo export RUN_HEAVY=1\nscripts/verify\n'):
            with self.subTest(hook=hook):
                self.assertFalse(self.health('if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi\n', hook)["prepush_blocks_on_unconditional_xcodebuild"])

    def test_verify_gate_comments_build_only_and_absent_hooks_are_counter_cases(self) -> None:
        health = self.health()
        self.assertFalse(any(health[key] for key in (
            "has_verify_script", "uses_incremental_layer_selection", "has_derived_data_or_build_lock",
            "prepush_blocks_on_unconditional_xcodebuild",
        )))
        health = self.health("# swift test --changed -derivedDataPath DerivedData\nxcodebuild test\n")
        self.assertFalse(health["prepush_blocks_on_unconditional_xcodebuild"])
        health = self.health(hook='''# RUN_HEAVY=1 xcodebuild test
# flock build.lock --layers
printf "xcodebuild test DerivedData --changed"
xcodebuild build
''')
        self.assertFalse(health["prepush_blocks_on_unconditional_xcodebuild"])
        self.assertFalse(health["uses_incremental_layer_selection"])
        self.assertFalse(health["has_derived_data_or_build_lock"])
        self.assertEqual(health["tier"], "not_detected")

    def test_verify_gate_incremental_and_lock_indicators(self) -> None:
        health = self.health("swift test\nselect_layers --changed\nflock build.lock swift build\n")
        self.assertTrue(health["uses_incremental_layer_selection"])
        self.assertTrue(health["has_derived_data_or_build_lock"])

    def test_hybrid_root_package_swift_and_app_target_routing_counter_cases(self) -> None:
        self.write(
            "Package.swift",
            """// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "HybridWorkspace",
    targets: [
        .target(name: "CoreKit", dependencies: [.target(name: "Shared")]),
        .target(name: "CustomFeature", path: "Modules/FeatureSources"),
        .testTarget(name: "CoreKitTests", dependencies: ["CoreKit"]),
    ]
)
""",
        )
        self.write("App.xcodeproj/project.pbxproj", "// !$*UTF8*$!\n")
        self.write("Sources/CoreKit/Service.swift", "public struct Service {}\n")
        self.write("Modules/FeatureSources/Feature.swift", "public struct Feature {}\n")
        self.write("App/Sources/AppMain.swift", "@main struct AppMain {}\n")
        self.write("Apps/macOS/MacRootApp.swift", "@main struct MacRootApp {}\n")
        self.write("Sources/App/LegacyAppView.swift", "struct LegacyAppView {}\n")
        self.write("UITests/LaunchUITests.swift", "final class LaunchUITests {}\n")

        pkg_only = CLASSIFIER.classify(
            self.repo,
            ["Sources/CoreKit/Service.swift", "Modules/FeatureSources/Feature.swift"],
        )
        self.assertEqual(pkg_only["route"], "swiftpm")
        self.assertEqual(pkg_only["packageRoots"], ["."])
        self.assertEqual(pkg_only["xcodeInputs"], [])

        for app_path in (
            "App/Sources/AppMain.swift",
            "Apps/macOS/MacRootApp.swift",
            "Sources/App/LegacyAppView.swift",
            "UITests/LaunchUITests.swift",
        ):
            with self.subTest(app_path=app_path):
                app_only = CLASSIFIER.classify(self.repo, [app_path])
                self.assertEqual(app_only["route"], "xcode")
                self.assertEqual(app_only["packageRoots"], [])
                self.assertEqual(app_only["xcodeInputs"][0]["path"], app_path)

        mixed = CLASSIFIER.classify(
            self.repo,
            ["Sources/CoreKit/Service.swift", "App/Sources/AppMain.swift"],
        )
        self.assertEqual(mixed["route"], "mixed")
        self.assertEqual(mixed["packageRoots"], ["."])
        self.assertEqual(mixed["packageFiles"], ["Sources/CoreKit/Service.swift"])
        self.assertEqual(mixed["xcodeInputs"][0]["path"], "App/Sources/AppMain.swift")

        sub_pkg_manifest = "Packages/SubPkg/Package.swift"
        self.write(sub_pkg_manifest, '// swift-tools-version: 6.0\nlet package = Package(name: "SubPkg")\n')
        self.write("Packages/SubPkg/Sources/SubPkg/Item.swift", "public struct Item {}\n")
        sub_pkg = CLASSIFIER.classify(self.repo, ["Packages/SubPkg/Sources/SubPkg/Item.swift"])
        self.assertEqual((sub_pkg["route"], sub_pkg["packageRoots"]), ("swiftpm", ["Packages/SubPkg"]))

    def test_case_selection_preserves_xc004_and_xc005_and_uses_single_source_drawer(self) -> None:
        self.write(
            "Package.swift",
            '// swift-tools-version: 6.0\nlet package = Package(targets: [.executableTarget(name: "AppPreviewHost")])\n',
        )
        self.write(
            "Sources/ConcurrencyActor.swift",
            "@MainActor public final class ConcurrencyActor: Sendable {}\n",
        )
        self.write("App/AppView.swift", "@MainActor struct AppView {}\n")

        mixed = CLASSIFIER.classify(
            self.repo,
            ["Sources/ConcurrencyActor.swift", "App/AppView.swift"],
        )
        self.assertEqual(mixed["route"], "mixed")
        matched_ids = [c["id"] for c in mixed["matchedBuildCases"]]
        self.assertIn("XC-005", matched_ids)
        self.assertIn("XC-004", matched_ids)
        self.assertEqual(matched_ids[:2], ["XC-005", "XC-004"])

        xcode_concurrency = CLASSIFIER.classify(self.repo, ["App/AppView.swift"])
        xcode_ids = [c["id"] for c in xcode_concurrency["matchedBuildCases"]]
        self.assertIn("XC-004", xcode_ids)
        self.assertIn("XC-005", xcode_ids)

        custom_cases = self.repo / "custom-build-cases.json"
        drawer = json.loads((SKILL_DIR / "references" / "build-cases.json").read_text(encoding="utf-8"))
        for item in drawer["active_cases"]:
            if item["id"] == "XC-005":
                item["title"] = "Custom Single-Source XC-005 Title"
            elif item["id"] == "XC-004":
                item["title"] = "Custom Single-Source XC-004 Title"
        custom_cases.write_text(json.dumps(drawer, indent=2), encoding="utf-8")

        looked_up_005 = CLASSIFIER.get_build_case("XC-005", cases_file=custom_cases)
        looked_up_004 = CLASSIFIER.get_build_case("XC-004", cases_file=custom_cases)
        self.assertIsNotNone(looked_up_005)
        self.assertIsNotNone(looked_up_004)
        self.assertEqual(looked_up_005["title"], "Custom Single-Source XC-005 Title")
        self.assertEqual(looked_up_004["title"], "Custom Single-Source XC-004 Title")

        custom_classified = CLASSIFIER.classify(
            self.repo,
            ["Sources/ConcurrencyActor.swift", "App/AppView.swift"],
            cases_file=custom_cases,
        )
        by_id = {c["id"]: c["title"] for c in custom_classified["matchedBuildCases"]}
        self.assertEqual(by_id["XC-005"], "Custom Single-Source XC-005 Title")
        self.assertEqual(by_id["XC-004"], "Custom Single-Source XC-004 Title")

    def test_preview_host_health_scopes_signing_and_dylib_to_target_blocks(self) -> None:
        self.write(
            "Package.swift",
            '// swift-tools-version: 6.0\nlet package = Package(targets: [.executableTarget(name: "AppPreviewHost")])\n',
        )
        self.write(
            "project.yml",
            """targets:
  AppPreviewHost:
    settings:
      ENABLE_DEBUG_DYLIB: YES
      DEVELOPMENT_TEAM: "ABCDE12345"
  HelperTool:
    settings:
      ENABLE_DEBUG_DYLIB: NO
      CODE_SIGNING_ALLOWED: NO
      CODE_SIGN_STYLE: Automatic
      DEVELOPMENT_TEAM: ""
""",
        )
        health_yml_ok = CLASSIFIER._inspect_preview_host_health(self.repo)
        self.assertEqual(health_yml_ok["status"], "ok")
        self.assertTrue(health_yml_ok["enable_debug_dylib_yes"])
        self.assertTrue(health_yml_ok["local_signing_valid"])
        self.assertIsNone(health_yml_ok["triggered_case"])

        self.write(
            "project.yml",
            """targets:
  AppPreviewHost:
    settings:
      ENABLE_DEBUG_DYLIB: YES
      CODE_SIGNING_ALLOWED: NO
  HelperTool:
    settings:
      ENABLE_DEBUG_DYLIB: YES
      DEVELOPMENT_TEAM: "ABCDE12345"
""",
        )
        health_yml_bad = CLASSIFIER._inspect_preview_host_health(self.repo)
        self.assertEqual(health_yml_bad["status"], "invalid_local_signing")
        self.assertFalse(health_yml_bad["local_signing_valid"])
        self.assertEqual(health_yml_bad["triggered_case"], "XC-005")
        (self.repo / "project.yml").unlink()

        self.write(
            "App.xcodeproj/project.pbxproj",
            """// !$*UTF8*$!
{
  objects = {
    AA01 /* AppPreviewHost */ = {
      isa = PBXNativeTarget;
      name = AppPreviewHost;
      buildConfigurationList = AA02 /* Build configuration list for PBXNativeTarget "AppPreviewHost" */;
    };
    AA02 /* Build configuration list for PBXNativeTarget "AppPreviewHost" */ = {
      isa = XCConfigurationList;
      buildConfigurations = (
        AA03 /* Debug */,
      );
    };
    AA03 /* Debug */ = {
      isa = XCBuildConfiguration;
      buildSettings = {
        PRODUCT_NAME = AppPreviewHost;
        ENABLE_DEBUG_DYLIB = YES;
        DEVELOPMENT_TEAM = ABCDE12345;
      };
      name = Debug;
    };
    BB01 /* UnrelatedHelper */ = {
      isa = PBXNativeTarget;
      name = UnrelatedHelper;
      buildConfigurationList = BB02 /* Build configuration list for PBXNativeTarget "UnrelatedHelper" */;
    };
    BB02 /* Build configuration list for PBXNativeTarget "UnrelatedHelper" */ = {
      isa = XCConfigurationList;
      buildConfigurations = (
        BB03 /* Debug */,
      );
    };
    BB03 /* Debug */ = {
      isa = XCBuildConfiguration;
      buildSettings = {
        PRODUCT_NAME = UnrelatedHelper;
        ENABLE_DEBUG_DYLIB = NO;
        CODE_SIGNING_ALLOWED = NO;
        CODE_SIGN_STYLE = Automatic;
        DEVELOPMENT_TEAM = "";
      };
      name = Debug;
    };
  };
}
""",
        )
        health_pbx_ok = CLASSIFIER._inspect_preview_host_health(self.repo)
        self.assertEqual(health_pbx_ok["status"], "ok")
        self.assertTrue(health_pbx_ok["enable_debug_dylib_yes"])
        self.assertTrue(health_pbx_ok["local_signing_valid"])
        self.assertIsNone(health_pbx_ok["triggered_case"])

        self.write(
            "App.xcodeproj/project.pbxproj",
            """// !$*UTF8*$!
{
  objects = {
    AA01 /* AppPreviewHost */ = {
      isa = PBXNativeTarget;
      name = AppPreviewHost;
      buildConfigurationList = AA02 /* Build configuration list for PBXNativeTarget "AppPreviewHost" */;
    };
    AA02 /* Build configuration list for PBXNativeTarget "AppPreviewHost" */ = {
      isa = XCConfigurationList;
      buildConfigurations = (
        AA03 /* Debug */,
      );
    };
    AA03 /* Debug */ = {
      isa = XCBuildConfiguration;
      buildSettings = {
        PRODUCT_NAME = AppPreviewHost;
        ENABLE_DEBUG_DYLIB = YES;
        CODE_SIGNING_ALLOWED = NO;
      };
      name = Debug;
    };
    BB03 /* Debug */ = {
      isa = XCBuildConfiguration;
      buildSettings = {
        PRODUCT_NAME = UnrelatedHelper;
        ENABLE_DEBUG_DYLIB = YES;
        DEVELOPMENT_TEAM = ABCDE12345;
      };
      name = Debug;
    };
  };
}
""",
        )
        health_pbx_bad = CLASSIFIER._inspect_preview_host_health(self.repo)
        self.assertEqual(health_pbx_bad["status"], "invalid_local_signing")
        self.assertFalse(health_pbx_bad["local_signing_valid"])
        self.assertEqual(health_pbx_bad["triggered_case"], "XC-005")

    def test_markdown_code_fences_and_eval_counter_case_ratio_contract(self) -> None:
        import re as _re

        single_line_fence_re = _re.compile(r"(?m)^\s*```[^\n`]+```")
        for md_path in sorted(SKILL_DIR.rglob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            self.assertIsNone(
                single_line_fence_re.search(text),
                f"Single-line fenced block found in {md_path.relative_to(ROOT)}",
            )
            fence_lines = [ln for ln in text.splitlines() if ln.lstrip().startswith("```")]
            self.assertEqual(
                len(fence_lines) % 2,
                0,
                f"Unbalanced triple-backtick fences in {md_path.relative_to(ROOT)}",
            )

        eval_doc = json.loads((SKILL_DIR / "references" / "eval-cases.json").read_text(encoding="utf-8"))
        active = eval_doc.get("active_cases", [])
        self.assertLessEqual(len(active), 15)
        counter_count = sum(
            1
            for item in active
            if any(str(item.get(k) or "").strip() for k in ("bad_snippet", "forbidden_regex", "negative_cmd"))
            or item.get("expect_fail_exit") is not None
            or item.get("expect_exit") not in (None, 0)
            or item.get("should_trigger") is False
            or any(str(t).strip().lower() in {"negative", "counter", "counter-case"} for t in (item.get("tags") or []))
        )
        self.assertGreaterEqual(counter_count / max(1, len(active)), 0.25)


if __name__ == "__main__":
    unittest.main()
