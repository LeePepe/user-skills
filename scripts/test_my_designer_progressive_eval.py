#!/usr/bin/env python3
"""Dual-Case Replay & Historical CLI Friction Tests for skills/my-designer."""

from __future__ import annotations

import binascii
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_DESIGN_CASES = REPO_ROOT / "skills" / "my-designer" / "scripts" / "verify_design_cases.py"


def _make_png(
    width: int,
    height: int,
    content_height_ratio: float,
    *,
    bit_depth: int = 8,
    color_type: int = 6,
) -> bytes:
    """Build deterministic non-interlaced PNGs (8/16-bit Grayscale/RGB/RGBA) with exact bottom whitespace."""
    content_rows = max(0, min(height, int(round(height * content_height_ratio))))
    samples_per_pixel = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    bytes_per_sample = 2 if bit_depth == 16 else 1
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray(b"\x00")
        is_content = y < content_rows
        for x in range(width):
            if is_content:
                val = 45 if ((x // 12) + (y // 10)) % 2 == 0 else 215
            else:
                val = 246
            channels: list[int]
            if color_type == 0:
                channels = [val]
            elif color_type == 4:
                channels = [val, 255]
            elif color_type == 2:
                channels = [val, val, min(255, val + 2)]
            else:
                channels = [val, val, min(255, val + 2), 255]
            for ch in channels:
                if bytes_per_sample == 2:
                    row.extend((ch, ch))
                else:
                    row.append(ch)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY_DESIGN_CASES), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class HistoricalExit2ReplayTests(unittest.TestCase):
    """Replay exact historical exit_code=2 CLI invocation patterns from the 55-session telemetry audit."""

    def test_replays_audit_spec_flag_and_positional_multi_file_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_md = root / "design.md"
            spec_md = root / "spec.md"
            valid_subtractive = (
                "# Design Mandate\n"
                "只保留顶部聚焦区，去掉底部冗余栏。\n\n"
                "## Negative DOM/View Assertions\n"
                "- `app.otherElements[\"LegacyBottomDock\"].exists == false` (`count == 0`)\n\n"
                "## Obsolete Test Manifest\n"
                "- `VitalStrideTests/LegacyDockTests.swift:42`\n"
            )
            design_md.write_text(valid_subtractive, encoding="utf-8")
            spec_md.write_text("# Spec\nKeep standard navigation flow.\n", encoding="utf-8")

            # Pattern #1 (1b9f90c7): audit-spec --file <design.md>
            res_file = _run_cli("audit-spec", "--file", str(design_md))
            self.assertEqual(res_file.returncode, 0, res_file.stderr)
            self.assertTrue(json.loads(res_file.stdout)["ok"])

            # Pattern #10/#11/#12 (98256466): audit-spec <design.md> <spec.md> (positional multi-file + --json)
            res_multi = _run_cli("audit-spec", str(design_md), str(spec_md), "--json")
            self.assertEqual(res_multi.returncode, 0, res_multi.stderr)
            payload = json.loads(res_multi.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["spec_files"]), 2)

            # Top-level --audit-spec compatibility
            res_top = _run_cli("--audit-spec", str(design_md), str(spec_md))
            self.assertEqual(res_top.returncode, 0, res_top.stderr)
            self.assertTrue(json.loads(res_top.stdout)["ok"])

    def test_replays_whitespace_dir_positional_max_ratio_and_16bit_png_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shots = Path(tmp) / "snapshots"
            shots.mkdir()
            # 75% content -> 25% bottom whitespace; 16-bit RGBA + 8-bit grayscale (historical exit=1 PNG format bug)
            (shots / "populated-light.png").write_bytes(
                _make_png(393, 240, 0.75, bit_depth=16, color_type=6)
            )
            (shots / "empty-dark.png").write_bytes(
                _make_png(393, 240, 0.72, bit_depth=8, color_type=0)
            )

            # Pattern #2/#3/#9 (7a750967, 0d10f172, 0b6a942d): check-whitespace-ratio --dir <dir>
            res_dir = _run_cli("check-whitespace-ratio", "--dir", str(shots))
            self.assertEqual(res_dir.returncode, 0, res_dir.stderr + res_dir.stdout)
            self.assertTrue(json.loads(res_dir.stdout)["ok"])

            # Pattern #7 (0011fbb0): check-whitespace-ratio <positional_dir>
            res_pos = _run_cli("check-whitespace-ratio", str(shots))
            self.assertEqual(res_pos.returncode, 0, res_pos.stderr)
            self.assertTrue(json.loads(res_pos.stdout)["ok"])

            # Local session pattern (83f2e644 / f0658be8): --image / --file + --max-ratio
            res_alias = _run_cli(
                "check-whitespace-ratio",
                "--image",
                str(shots / "populated-light.png"),
                "--max-ratio",
                "0.40",
            )
            self.assertEqual(res_alias.returncode, 0, res_alias.stderr)
            self.assertTrue(json.loads(res_alias.stdout)["ok"])

    def test_replays_audit_code_positional_multifile_subcommand_aliases_and_snippet_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1 = root / "ViewA.swift"
            s2 = root / "ViewB.swift"
            s1.write_text(
                "import SwiftUI\nstruct ViewA: View { @State private var on = false; var body: some View { Toggle(\"On\", isOn: $on) } }\n",
                encoding="utf-8",
            )
            s2.write_text(
                "import SwiftUI\nstruct ViewB: View { @State private var count = 1; var body: some View { Text(\"\\(count)\") } }\n",
                encoding="utf-8",
            )

            # Pattern #6 (0011fbb0): audit-code --platform mobile File1.swift File2.swift
            res_multi = _run_cli("audit-code", "--platform", "mobile", str(s1), str(s2))
            self.assertEqual(res_multi.returncode, 0, res_multi.stderr)
            payload = json.loads(res_multi.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["files"], 2)

            # Pre-subcommand --platform + check-code / audit aliases + --root/--ui-path
            res_pre = _run_cli("--platform", "desktop", "check-code", str(s1))
            self.assertEqual(res_pre.returncode, 0, res_pre.stderr)
            self.assertEqual(json.loads(res_pre.stdout)["platform"], "desktop")

            res_root = _run_cli("audit", "--root", str(root), "--ui-path", "ViewA.swift")
            self.assertEqual(res_root.returncode, 0, res_root.stderr)
            self.assertEqual(json.loads(res_root.stdout)["files"], 1)

            # Pattern (e241efd0): --check-section-interactivity --snippet '...' without path
            res_snip = _run_cli(
                "--check-section-interactivity",
                "--snippet",
                "struct Bad: View { var body: some View { RoundedRectangle(cornerRadius: 8).fill(.blue) } }",
            )
            self.assertEqual(res_snip.returncode, 1, res_snip.stderr)
            snip_payload = json.loads(res_snip.stdout)
            self.assertEqual(snip_payload["status"], "REJECT_STATIC_PLACEHOLDER")
            self.assertIn("skills/my-designer/references/interaction-layering.md", snip_payload["reference_drawers"])

            # Route aliases (--containers, --archetype, --json)
            res_route = _run_cli(
                "route",
                "--platform",
                "desktop",
                "--containers",
                "MenuBarExtra,NavigationSplitView",
                "--archetype",
                "content-dense",
                "--json",
            )
            self.assertEqual(res_route.returncode, 0, res_route.stderr)
            route_payload = json.loads(res_route.stdout)
            self.assertEqual(route_payload["persona"], "compact")
            self.assertEqual(route_payload["three_tier_spectrum"]["tier_3_exploration_quota"]["formula"], "2+1+1")


class SelfDisclosingGuidanceAndDualCasePersonaTests(unittest.TestCase):
    """Verify self-disclosing L1 drawer pointers on failures and Positive vs Counter-Case persona scope isolation."""

    def test_failures_emit_exact_l1_reference_drawer_pointers_while_clean_runs_stay_quiet(self) -> None:
        # 1. DC-012 interaction layering failure -> points to interaction-layering.md + fix_rule
        bad_dc012 = (
            "Button { select() } label: {\n"
            "    VStack { if expanded { Button(\"Run\") {} } }.frame(minHeight: 36)\n"
            "}.buttonStyle(.plain)"
        )
        res_dc012 = _run_cli("audit-code", "--platform", "desktop", "--snippet", bad_dc012)
        self.assertEqual(res_dc012.returncode, 1)
        p_dc012 = json.loads(res_dc012.stdout)
        self.assertIn("skills/my-designer/references/interaction-layering.md", p_dc012["reference_drawers"])
        self.assertLessEqual(len(p_dc012["guidance"]), 3)
        self.assertTrue(any("interaction-layering.md" in line for line in p_dc012["guidance"]))
        self.assertTrue(any(f.get("fix_rule") for f in p_dc012["findings"]))

        # 2. DC-014 instructional clutter -> points to breathing-and-clarity.md
        bad_dc014 = 'VStack { Text("点击任务置顶聚焦 · 标题与属性展示处直接点按修改") }'
        res_dc014 = _run_cli("audit-code", "--platform", "mobile", "--snippet", bad_dc014)
        self.assertEqual(res_dc014.returncode, 1)
        p_dc014 = json.loads(res_dc014.stdout)
        self.assertIn("skills/my-designer/references/breathing-and-clarity.md", p_dc014["reference_drawers"])

        # 3. Subtractive spec failure -> points to modernization-dimensions.md
        with tempfile.TemporaryDirectory() as tmp:
            bad_spec = Path(tmp) / "design.md"
            bad_spec.write_text("# Design\n只保留主列表，去掉冗余抽屉。\n", encoding="utf-8")
            res_spec = _run_cli("audit-spec", str(bad_spec))
            self.assertEqual(res_spec.returncode, 1)
            p_spec = json.loads(res_spec.stdout)
            self.assertIn("skills/my-designer/references/modernization-dimensions.md", p_spec["reference_drawers"])

        # 4. Clean run stays minimal without noisy guidance
        good_swift = 'Text("Clean").padding(8).frame(width: 48, height: 44)'
        res_clean = _run_cli("audit-code", "--platform", "mobile", "--snippet", good_swift)
        self.assertEqual(res_clean.returncode, 0)
        p_clean = json.loads(res_clean.stdout)
        self.assertNotIn("guidance", p_clean)

        # 5. Genuine CLI usage error (no args at all) prints actionable stderr hint to routing-index.md
        res_noargs = _run_cli()
        self.assertEqual(res_noargs.returncode, 2)
        self.assertIn("skills/my-designer/references/routing-index.md", res_noargs.stderr)

    def test_dual_case_replay_positive_compact_cockpit_vs_counter_breathing_companion(self) -> None:
        """Dual-Case Replay: compact scene tightens locally without breaking breathing companion or global 0.40 default."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Case A: Dense cockpit screen (18% bottom whitespace -> 82% content)
            dense_cockpit = root / "aidash-cockpit.png"
            dense_cockpit.write_bytes(_make_png(400, 200, 0.82))

            # Case B: Loose screen with 34% bottom whitespace (66% content)
            # - Fails under --persona compact (> 25% cap)
            # - Passes under global default (<= 40% cap) AND --persona breathing (<= 55% cap, >= 12% floor)
            airy_companion = root / "vitalstride-breathing.png"
            airy_companion.write_bytes(_make_png(400, 200, 0.66))

            # Case C: Over-breathing editorial companion with 48% whitespace (52% content)
            # - Fails global default (> 40%), but PASSES --persona breathing (<= 55%)
            generous_editorial = root / "companion-editorial.png"
            generous_editorial.write_bytes(_make_png(400, 200, 0.52))

            # Case D: Over-cramped 4% whitespace (96% content) on a breathing companion
            # - Emits soft advisory (exit 0) under --persona breathing pointing to breathing-and-clarity.md
            cramped_companion = root / "cramped-companion.png"
            cramped_companion.write_bytes(_make_png(400, 200, 0.96))

            # Positive Case: compact cockpit passes dense_cockpit (18% <= 25%) and rejects loose 34% dead space
            pos_pass = _run_cli("check-whitespace-ratio", "--persona", "compact", str(dense_cockpit))
            self.assertEqual(pos_pass.returncode, 0)
            self.assertEqual(json.loads(pos_pass.stdout)["max_dead_space_ratio"], 0.25)

            pos_reject = _run_cli("check-whitespace-ratio", "--persona", "compact", str(airy_companion))
            self.assertEqual(pos_reject.returncode, 1)
            pos_reject_json = json.loads(pos_reject.stdout)
            self.assertIn("VISUAL_EXCESSIVE_DEAD_SPACE", pos_reject_json["errors"][0])
            self.assertIn("skills/my-designer/references/breathing-and-clarity.md", pos_reject_json["reference_drawers"])

            # Anchor Counter-Case 1: global default remains 0.40 (unpolluted by compact cockpit rule!)
            default_pass = _run_cli("check-whitespace-ratio", str(airy_companion))
            self.assertEqual(default_pass.returncode, 0)
            self.assertEqual(json.loads(default_pass.stdout)["max_dead_space_ratio"], 0.40)

            default_reject_48 = _run_cli("check-whitespace-ratio", str(generous_editorial))
            self.assertEqual(default_reject_48.returncode, 1)

            # Anchor Counter-Case 2: breathing companion accepts generous_editorial (48% <= 55%)
            breath_pass = _run_cli("check-whitespace-ratio", "--persona", "breathing", str(generous_editorial))
            self.assertEqual(breath_pass.returncode, 0)
            breath_pass_json = json.loads(breath_pass.stdout)
            self.assertEqual(breath_pass_json["persona"], "breathing")
            self.assertEqual(breath_pass_json["max_dead_space_ratio"], 0.55)
            self.assertNotIn("advisories", breath_pass_json)

            # Soft Advisory on cramped companion: exits 0 (not a rigid roadblock) + discloses breathing-and-clarity.md
            breath_adv = _run_cli("check-whitespace-ratio", "--persona", "breathing", str(cramped_companion))
            self.assertEqual(breath_adv.returncode, 0)
            breath_adv_json = json.loads(breath_adv.stdout)
            self.assertTrue(breath_adv_json["ok"])
            self.assertTrue(any("SOFT_ADVISORY_CRAMPED_BREATHING" in a for a in breath_adv_json["advisories"]))
            self.assertIn("skills/my-designer/references/breathing-and-clarity.md", breath_adv_json["reference_drawers"])


if __name__ == "__main__":
    unittest.main()
