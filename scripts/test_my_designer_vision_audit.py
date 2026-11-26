#!/usr/bin/env python3
"""Focused regression tests for the fail-closed my-designer vision gate."""
from __future__ import annotations

import binascii
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY = REPO_ROOT / "skills/my-designer/scripts/verify_design_cases.py"


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)

    width, height = 390, 844
    raw = b"".join(b"\x00" + bytes([220, 220, 220, 255]) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(VERIFY), *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


class VisionAuditTests(unittest.TestCase):
    def _fixture(self, root: Path, response: object, *, nested: bool = False) -> tuple[Path, Path]:
        image_dir = root / ("nested/screens" if nested else "screens")
        image_dir.mkdir(parents=True)
        image = image_dir / "screen.png"
        image.write_bytes(_png())
        response_path = root / "response.json"
        response_path.write_text(json.dumps(response), encoding="utf-8")
        return image, response_path

    def test_clean_pass_p2_warning_and_blocking_p0_p1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_image, clean_response = self._fixture(root, {"pass": True, "defects": []})
            clean = _run("vision-audit", "--image", str(clean_image), "--mock-response", str(clean_response))
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertTrue(json.loads(clean.stdout)["pass"])

            p2_image, p2_response = self._fixture(root / "p2", {"pass": True, "defects": [{"severity": "P2", "category": "proportion", "description": "minor", "evidence": "pixel region"}]})
            p2 = _run("vision-audit", "--image", str(p2_image), "--mock-response", str(p2_response))
            self.assertEqual(p2.returncode, 0, p2.stderr)
            self.assertTrue(json.loads(p2.stdout)["pass"])

            p0_image, p0_response = self._fixture(root / "p0", {"pass": False, "defects": [{"severity": "P0", "category": "overlap", "description": "intersects", "evidence": {"region": [1, 2, 3, 4]}}]})
            p0 = _run("vision-audit", "--image", str(p0_image), "--mock-response", str(p0_response))
            self.assertEqual(p0.returncode, 1)
            self.assertEqual(json.loads(p0.stdout)["blocking_defect_count"], 1)

            p1_image, p1_response = self._fixture(root / "p1", {"pass": True, "defects": [{"severity": "P1", "category": "alignment", "description": "drift", "evidence": "pixel region"}]})
            p1 = _run("vision-audit", "--image", str(p1_image), "--mock-response", str(p1_response))
            self.assertEqual(p1.returncode, 1)
            self.assertEqual(json.loads(p1.stdout)["blocking_defect_count"], 1)

    def test_malformed_and_invalid_responses_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image, response = self._fixture(root, {})
            malformed = _run("vision-audit", "--image", str(image), "--mock-response", str(response))
            self.assertEqual(malformed.returncode, 1)
            self.assertFalse(json.loads(malformed.stdout)["results"][0]["valid"])

            response.write_text(json.dumps({"pass": True, "defects": [{"severity": "P3", "category": "unknown", "description": "x", "evidence": "y"}]}), encoding="utf-8")
            invalid = _run("vision-audit", "--image", str(image), "--mock-response", str(response))
            self.assertEqual(invalid.returncode, 1)
            self.assertTrue(json.loads(invalid.stdout)["results"][0]["errors"])

            response.write_text(json.dumps({"pass": True, "defects": [], "approved": True}), encoding="utf-8")
            extra = _run("vision-audit", "--image", str(image), "--mock-response", str(response))
            self.assertEqual(extra.returncode, 1)
            self.assertIn("exactly pass and defects", json.loads(extra.stdout)["results"][0]["errors"][0])

    def test_recursive_discovery_empty_directory_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image, response = self._fixture(root, {"pass": True, "defects": []}, nested=True)
            receipt = root / "out/report.json"
            discovered = _run("vision-audit", "--dir", str(root), "--platform", "web", "--out", str(receipt), "--mock-response", str(response))
            self.assertEqual(discovered.returncode, 0, discovered.stderr)
            payload = json.loads(discovered.stdout)
            self.assertEqual(payload["checked_count"], 1)
            self.assertEqual(json.loads(receipt.read_text())["schema"], "my-designer/vision-audit-v1")

            alias = _run("vision-audit", "--image", str(image), "--platform", "ios", "--mock-response", str(response))
            self.assertEqual(alias.returncode, 0, alias.stderr)
            self.assertEqual(json.loads(alias.stdout)["platform"], "mobile")

            empty = root / "empty"
            empty.mkdir()
            no_images = _run("vision-audit", "--dir", str(empty), "--mock-response", str(response))
            self.assertEqual(no_images.returncode, 1)
            self.assertEqual(json.loads(no_images.stdout)["checked_count"], 0)

    def test_symlink_and_corrupt_png_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image, response = self._fixture(root, {"pass": True, "defects": []})
            symlink = root / "symlink.png"
            try:
                symlink.symlink_to(image)
            except OSError:
                self.skipTest("symlinks unavailable")
            linked = _run("vision-audit", "--image", str(symlink), "--mock-response", str(response))
            self.assertEqual(linked.returncode, 1)
            self.assertIn("symlinked", json.loads(linked.stdout)["results"][0]["errors"][0])

            def chunk(kind: bytes, payload: bytes) -> bytes:
                return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)

            corrupt = root / "corrupt.png"
            corrupt.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 390, 844, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", b"not-zlib")
                + chunk(b"IEND", b"")
            )
            result = _run("vision-audit", "--image", str(corrupt), "--mock-response", str(response))
            self.assertEqual(result.returncode, 1)
            self.assertIn("invalid PNG", json.loads(result.stdout)["results"][0]["errors"][0])

    def test_facade_exports_and_plan_c_routes(self) -> None:
        facade = _run("lookup-defect", "Compact", "Pill", "Visual-vs-Hit-Area")
        self.assertEqual(facade.returncode, 0, facade.stderr)
        matches = json.loads(facade.stdout)["matches"]
        self.assertTrue(any(item["id"] == "DC-017" for item in matches))
        route = _run("route", "--platform", "mobile", "--dimensions", "layout-navigation")
        self.assertEqual(route.returncode, 0, route.stderr)
        ids = {case["id"] for case in json.loads(route.stdout)["plan_c_active_cases"]}
        self.assertTrue({"DC-017", "DC-018", "DC-019"}.issubset(ids))
        sys.path.insert(0, str(VERIFY.parent))
        import design_cases_core  # type: ignore
        self.assertTrue(any(module.__name__.endswith("vision_audit") for module in design_cases_core._MODULES))
        self.assertIn("command_vision_audit", vars(design_cases_core))


if __name__ == "__main__":
    unittest.main()
