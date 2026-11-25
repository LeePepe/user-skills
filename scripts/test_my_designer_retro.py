#!/usr/bin/env python3
"""Positive and counter-case replays for the modular my-designer verifier."""

from __future__ import annotations

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
CORE = VERIFY.parent / "design_cases_core"


def _load_gate():
    spec = importlib.util.spec_from_file_location("my_designer_retro_gate", VERIFY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args], cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )


def _png(path: Path) -> None:
    width, height = 100, 100
    rows = []
    for row in range(height):
        pixel = b"\x00\x00\x00\xff" if row < 80 else b"\xff\xff\xff\xff"
        rows.append(b"\x00" + pixel * width)
    raw = b"".join(rows)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        import binascii
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xffffffff)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class MyDesignerRetroTests(unittest.TestCase):
    def test_facade_budget_runtime_symlinks_and_patch_state(self) -> None:
        self.assertLessEqual(len(VERIFY.read_text(encoding="utf-8").splitlines()), 220)
        for path in CORE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 750, path)
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"base64\.b64decode|zlib\.decompress|exec\(compile")
        runtime = VERIFY.parent / "skill_runtime"
        self.assertTrue(runtime.is_symlink())
        self.assertEqual(runtime.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        design_studio_gate = REPO_ROOT / "skills/design-studio/scripts/verify_design_cases.py"
        self.assertTrue(design_studio_gate.is_symlink())
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-taxonomy.json"
            gate = _load_gate()
            with mock.patch.object(gate, "DEFAULT_TAXONOMY", missing):
                self.assertEqual(gate._load_taxonomy(), gate._FALLBACK_TAXONOMY)

    def test_dual_forms_and_path_aliases_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swift = root / "View.swift"
            swift.write_text("import SwiftUI\nstruct ViewA: View { @State var on = false; var body: some View { Toggle(\"On\", isOn: $on) } }\n", encoding="utf-8")
            image = root / "screen.png"
            _png(image)
            for args in (("audit-code", "--platform", "mobile", "--dir", str(root)), ("--audit-code", str(swift))):
                result = _run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])
            for args in (("check-section-interactivity", "--dir", str(root)), ("--check-section-interactivity", str(swift))):
                result = _run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
            for args in (("check-whitespace-ratio", "--dir", str(root)), ("--check-whitespace-ratio", "--path", str(image))):
                result = _run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])
            artifact = root / "artifact.json"
            artifact.write_text('{"ok": true}', encoding="utf-8")
            for args in (("inspect-json", "--path", str(artifact)), ("--inspect-json", str(artifact))):
                result = _run(*args)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_usage_is_short_and_self_disclosing(self) -> None:
        for args in ((), ("audit-code",), ("audit-spec",), ("--unknown",)):
            result = _run(*args)
            self.assertEqual(result.returncode, 2, args)
            lines = [line for line in result.stderr.splitlines() if line.strip()]
            self.assertLessEqual(len(lines), 3, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("skills/my-designer/references/routing-index.md", result.stderr)

    def test_spec_gap_soft_advisory_and_strict_counter_case(self) -> None:
        incomplete = "# Apple design\nUse NavigationStack on mobile.\n"
        complete = """# Handoff
Desktop uses NavigationSplitView; mobile uses NavigationStack.
States: empty, loading, error, success, retry.
## Deprecated Test Clauses
N/A for legacy tests.
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad.md"
            good = root / "good.md"
            bad.write_text(incomplete, encoding="utf-8")
            good.write_text(complete, encoding="utf-8")
            soft = _run("audit-spec", "--path", str(bad))
            self.assertEqual(soft.returncode, 0, soft.stderr)
            soft_payload = json.loads(soft.stdout)
            self.assertTrue(soft_payload["spec_gap_warnings"])
            self.assertTrue(all(w["tag"] == "[SPEC_GAP]" for w in soft_payload["spec_gap_warnings"]))
            self.assertIn("navigation-evidence-contract.md", "\n".join(soft_payload["guidance"]))
            strict = _run("--audit-spec", "--strict-spec-gaps", str(bad))
            self.assertEqual(strict.returncode, 1, strict.stderr)
            self.assertFalse(json.loads(strict.stdout)["ok"])
            clean = _run("audit-spec", "--strict-spec-gaps", "--dir", str(good))
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertEqual(json.loads(clean.stdout)["spec_gap_warnings"], [])

    def test_facade_in_process_main_preserves_json_contract(self) -> None:
        gate = _load_gate()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = gate.main(["route", "--platform", "ios", "--dimensions", "layout-navigation"])
        self.assertEqual(result, 0, errors.getvalue())
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["platform"], "mobile")


if __name__ == "__main__":
    unittest.main()
