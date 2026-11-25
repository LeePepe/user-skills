#!/usr/bin/env python3
"""Positive and counter-case replays for the modular my-designer verifier."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY = REPO_ROOT / "skills/my-designer/scripts/verify_design_cases.py"
DESIGN_STUDIO_VERIFY = REPO_ROOT / "skills/design-studio/scripts/verify_design_cases.py"
CORE = VERIFY.parent / "design_cases_core"


def _load_gate():
    spec = importlib.util.spec_from_file_location("my_designer_retro_gate", VERIFY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_with(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(path), *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return _run_with(VERIFY, *args)


def _json_result(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _png(path: Path) -> None:
    width, height = 100, 100
    raw = b"".join(b"\x00" + (b"\x00\x00\x00\xff" if row < 80 else b"\xff\xff\xff\xff") * width for row in range(height))
    def chunk(kind: bytes, payload: bytes) -> bytes:
        import binascii
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class MyDesignerRetroTests(unittest.TestCase):
    def test_facade_budget_runtime_symlinks_and_callable_patch_state(self) -> None:
        self.assertLessEqual(len(VERIFY.read_text(encoding="utf-8").splitlines()), 220)
        for path in CORE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 750, path)
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"base64\.b64decode|zlib\.decompress|exec\(compile")
        self.assertEqual((VERIFY.parent / "skill_runtime").readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertTrue(DESIGN_STUDIO_VERIFY.is_symlink())
        gate = _load_gate()
        self.assertIs(gate.SF_DATALESS, gate.RUNTIME_SF_DATALESS)
        self.assertTrue(callable(gate.redact_payload))
        with mock.patch.object(gate, "command_route", return_value=7):
            self.assertEqual(gate.main(["route", "--platform", "mobile"]), 7)
        original_root = gate._BASE_VALUES["ROOT"]
        with tempfile.TemporaryDirectory() as tmp:
            patched_root = Path(tmp)
            args = argparse.Namespace(cases_file=str(gate.DEFAULT_CASES), taxonomy=str(gate.DEFAULT_TAXONOMY))
            with mock.patch.object(gate, "ROOT", patched_root):
                with contextlib.redirect_stdout(io.StringIO()):
                    gate.command_flow_map(args)
                self.assertIs(gate.route.ROOT, patched_root)
            with contextlib.redirect_stdout(io.StringIO()):
                gate.command_flow_map(args)
        self.assertIs(gate.ROOT, original_root)
        self.assertIs(gate.route.ROOT, original_root)

    def test_design_studio_symlink_executes_same_json_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "handoff.md"
            spec.write_text("Desktop NavigationSplitView; mobile NavigationStack. States empty/loading/error.\nDeprecated Test Clauses: N/A\n", encoding="utf-8")
            for args in (("flow-map",), ("--route", "--platform", "mobile"), ("--audit-spec", str(spec))):
                left, right = _run_with(VERIFY, *args), _run_with(DESIGN_STUDIO_VERIFY, *args)
                self.assertEqual(left.returncode, right.returncode, args)
                self.assertEqual(_json_result(left), _json_result(right), args)

    def test_flow_map_flag_parity_through_both_entry_paths(self) -> None:
        expected = _run("flow-map")
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for entry in (VERIFY, DESIGN_STUDIO_VERIFY):
            for command in ("flow-map", "--flow-map"):
                with self.subTest(entry=entry, command=command):
                    result = _run_with(entry, command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(_json_result(result), _json_result(expected))

    def test_all_interactivity_and_whitespace_targets_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty"
            empty.mkdir()
            (empty / "unrelated.txt").write_text("not a Swift source or PNG", encoding="utf-8")
            for command, suffix in (("check-section-interactivity", ".swift"), ("check-whitespace-ratio", ".png")):
                good = root / f"good{suffix}"
                if suffix == ".swift":
                    good.write_text('struct Good: View { @State var on = false; var body: some View { Toggle("On", isOn: $on) } }', encoding="utf-8")
                else:
                    _png(good)
                for invalid in (root / f"missing{suffix}", empty):
                    for selector in (command, f"--{command}"):
                        for targets in ((str(invalid), str(good)), ("--path", str(invalid), "--path", str(good)), ("--dir", str(invalid), "--dir", str(good))):
                            with self.subTest(selector=selector, invalid=invalid, targets=targets):
                                result = _run(selector, *targets)
                                self.assertEqual(result.returncode, 1, result.stderr)
                                payload = _json_result(result)
                                self.assertFalse(payload["ok"])
                                self.assertIn(str(invalid), " ".join(payload["errors"]))
                                records = payload["views"] if suffix == ".swift" else payload["metrics"]
                                self.assertIn(str(good), {record["file"] for record in records})
                missing = root / f"missing{suffix}"
                result = _run(f"--{command}", "--path", str(missing), "--dir", str(empty), str(good))
                self.assertEqual(result.returncode, 1, result.stderr)
                errors = " ".join(_json_result(result)["errors"])
                self.assertIn(str(missing), errors)
                self.assertIn(str(empty), errors)

    def test_flag_form_positional_lists_and_mixed_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for command, suffix in (("check-section-interactivity", ".swift"), ("check-whitespace-ratio", ".png")):
                paths = [root / f"{name}{suffix}" for name in ("One", "Two", "Three")]
                for index, path in enumerate(paths):
                    if suffix == ".swift":
                        path.write_text(f'struct Good{index}: View {{ @State var on = false; var body: some View {{ Toggle("On", isOn: $on) }} }}', encoding="utf-8")
                    else:
                        _png(path)
                first, second, third = map(str, paths)
                options = ("--persona", "compact") if suffix == ".png" else ()
                cases = (
                    (f"--{command}", *options, first, second),
                    (f"--{command}", first, *options, second),
                    (f"--{command}", first, "--path", second, *options, third),
                    ("--dir", first, f"--{command}", *options, second, "--path", third),
                )
                for args in cases:
                    with self.subTest(args=args):
                        result = _run(*args)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        payload = _json_result(result)
                        self.assertTrue(payload["ok"])
                        records = payload["views"] if suffix == ".swift" else payload["metrics"]
                        self.assertEqual({record["file"] for record in records}, {path for path in (first, second, third) if path in args})
                missing = str(root / f"missing{suffix}")
                result = _run(f"--{command}", *options, missing, second)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(_json_result(result)["ok"])
                self.assertIn(missing, " ".join(_json_result(result)["errors"]))
                if suffix == ".swift":
                    paths[0].write_text('struct Bad: View { var body: some View { RoundedRectangle(cornerRadius: 8).fill(.blue) } }', encoding="utf-8")
                else:
                    paths[0].write_bytes(b"not a PNG")
                result = _run(f"--{command}", *options, first, second)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(_json_result(result)["ok"])
                self.assertIn(paths[0].name, " ".join(_json_result(result)["errors"]))
            snippet = (root / "Two.swift").read_text(encoding="utf-8")
            missing = str(root / "missing.swift")
            for command in ("check-section-interactivity", "--check-section-interactivity"):
                for labels in ((), ("<snippet>",)):
                    result = _run(command, *labels, "--snippet", snippet)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(_json_result(result)["ok"])
                result = _run(command, "<snippet>", "--snippet", snippet, "--path", missing)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(missing, " ".join(_json_result(result)["errors"]))

    def test_deprecated_test_clauses_require_scoped_concrete_evidence(self) -> None:
        prefix = "Desktop NavigationSplitView; mobile NavigationStack.\nStates: empty, loading, error.\n"
        for clause in (
            "## Deprecated Test Clauses\n## Analytics\nN/A\n",
            "Deprecated Test Clauses: tests TBD\n",
            "Deprecated Test Clauses:\nAnalytics: N/A\n",
            "- Obsolete Test Manifest:\nAnalytics: N/A\n",
            "## Obsolete Test Manifest\n  ### Analytics\nN/A\n",
        ):
            with self.subTest(clause=clause):
                self._assert_gap(prefix + clause, "SPEC_GAP_DEPRECATED_TEST_CLAUSES")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            for clause in (
                "Deprecated Test Clauses: N/A",
                "## Obsolete Test Manifest\n- LegacyTests.swift:42",
                "- Obsolete Test Manifest: test_removeLegacy",
                "Deprecated Test Clauses: testLegacyNavigation",
                "## Deprecated Test Clauses\n- N/A (greenfield)",
                "Deprecated Test Clauses: `assertLegacyCount`",
                "Deprecated Test Clauses: legacy_spec.ts",
                "Deprecated Test Clauses: no deprecated tests",
                "Deprecated Test Clauses: none",
            ):
                with self.subTest(clause=clause):
                    path.write_text(prefix + clause, encoding="utf-8")
                    result = _run("audit-spec", "--strict-spec-gaps", str(path))
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertEqual(_json_result(result)["spec_gap_warnings"], [])

    def test_dual_forms_and_accumulating_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swift = root / "View.swift"
            swift.write_text("import SwiftUI\nstruct ViewA: View { @State var on = false; var body: some View { Toggle(\"On\", isOn: $on) } }\n", encoding="utf-8")
            image = root / "screen.png"
            _png(image)
            for args in (("audit-code", "--platform", "mobile", "--dir", str(root)), ("--audit-code", str(swift))):
                result = _run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
            for args in (("check-section-interactivity", "--dir", str(root)), ("--check-section-interactivity", str(swift))):
                self.assertEqual(_run(*args).returncode, 0)
            for args in (("check-whitespace-ratio", "--dir", str(root)), ("--check-whitespace-ratio", "--path", str(image))):
                self.assertEqual(_run(*args).returncode, 0)
            for command in ("audit-code", "audit-swift", "check-code", "audit"):
                auxiliary = _run(command, "--platform", "mobile", str(swift), "--check-whitespace-ratio", str(image))
                self.assertEqual(auxiliary.returncode, 0, auxiliary.stderr)
                self.assertTrue(_json_result(auxiliary)["ok"])
            artifact = root / "artifact.json"
            artifact.write_text('{"ok": true}', encoding="utf-8")
            for args in (("inspect-json", "--path", str(artifact)), ("--inspect-json", "--path", str(artifact))):
                self.assertEqual(_run(*args).returncode, 0)
            missing = root / "missing.md"
            repeated = _run("--audit-spec", "--path", str(missing), "--path", str(artifact))
            self.assertEqual(repeated.returncode, 1)
            self.assertIn(str(missing), " ".join(_json_result(repeated).get("errors", [])))
            d1, d2 = root / "d1", root / "d2"
            d1.mkdir()
            (d1 / "good.swift").write_text("struct Good: View { @State var value = false; var body: some View { Toggle(\"x\", isOn: $value) } }", encoding="utf-8")
            code = _run("--audit-code", "--platform", "mobile", "--dir", str(d1), str(d2))
            self.assertNotEqual(code.returncode, 0)
            self.assertIn(str(d2), " ".join(_json_result(code).get("code_errors", [])))

    def test_invalid_usage_is_short_and_self_disclosing(self) -> None:
        for args in ((), ("audit-code",), ("audit-spec",), ("--unknown",), ("lookup-defect",), ("--lookup-defect",), ("lookup-defect", "--query", ""), ("lookup-defect", ""), ("--lookup-defect", "")):
            result = _run(*args)
            self.assertEqual(result.returncode, 2, args)
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            self.assertLessEqual(len(lines), 3, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("skills/my-designer/references/routing-index.md", result.stderr)

    def _assert_gap(self, content: str, code: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text(content, encoding="utf-8")
            soft = _run("audit-spec", str(path))
            self.assertEqual(soft.returncode, 0, soft.stderr)
            payload = _json_result(soft)
            self.assertEqual({warning["code"] for warning in payload["spec_gap_warnings"]}, {code})
            for args in (("--strict-spec-gaps", "audit-spec", str(path)), ("audit-spec", "--strict-spec-gaps", str(path))):
                strict = _run(*args)
                self.assertEqual(strict.returncode, 1, strict.stderr)
                self.assertFalse(_json_result(strict)["ok"])

    def test_spec_gap_detectors_are_isolated_and_scoped(self) -> None:
        states = "States: empty, loading, error, success, retry."
        nav = "Desktop NavigationSplitView; mobile NavigationStack."
        dep = "Deprecated Test Clauses: N/A."
        self._assert_gap(f"{states}\n{dep}\n", "SPEC_GAP_PLATFORM_NAVIGATION_DIVERGENCE")
        self._assert_gap(f"{nav}\n{dep}\n", "SPEC_GAP_STATE_COVERAGE")
        self._assert_gap(f"{nav}\n{states}\nAnalytics: N/A\n", "SPEC_GAP_DEPRECATED_TEST_CLAUSES")
        cases = {
            "web": "platform: web\ncontainer: semantic viewport\n" + states + "\n" + dep,
            "scoped-na": "Navigation: N/A\n" + states + "\n" + dep,
            "complete": nav + "\n" + states + "\n" + dep,
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in cases.items():
                path = Path(tmp) / f"{name}.md"
                path.write_text(content, encoding="utf-8")
                result = _run("audit-spec", "--strict-spec-gaps", str(path))
                self.assertEqual(result.returncode, 0, (name, result.stderr))
                self.assertEqual(_json_result(result)["spec_gap_warnings"], [])

    def test_facade_in_process_main_preserves_json_contract(self) -> None:
        gate = _load_gate()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = gate.main(["route", "--platform", "ios", "--dimensions", "layout-navigation"])
        self.assertEqual(result, 0, errors.getvalue())
        self.assertEqual(json.loads(output.getvalue())["platform"], "mobile")


if __name__ == "__main__":
    unittest.main()
