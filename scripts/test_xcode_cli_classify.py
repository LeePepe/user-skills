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
        self.directory = tempfile.TemporaryDirectory(prefix=".classify-test-", dir=SKILL_DIR)
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
            ('heavy() { if [ "$RUN_HEAVY" = 1 ]; then xcodebuild test; fi; }\nheavy\n', 'RUN_HEAVY=1 scripts/verify\n'),
        ]
        for verify, hook in cases:
            with self.subTest(hook=hook):
                self.assertTrue(self.health(verify, hook)["prepush_blocks_on_unconditional_xcodebuild"])
                self.assertFalse(self.health(verify, 'if [ "$RUN_HEAVY" = 1 ]; then ' + hook.strip() + '; fi\n')["prepush_blocks_on_unconditional_xcodebuild"])

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


if __name__ == "__main__":
    unittest.main()
