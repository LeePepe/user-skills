#!/usr/bin/env python3
"""Dual-Case Replay & Historical CLI Friction Tests for skills/my-designer."""

from __future__ import annotations

import binascii
import json
import contextlib
import importlib.util
import io
import types
from unittest import mock
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
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
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
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
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
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
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
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
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
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
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


def _load_script(name):
    spec = importlib.util.spec_from_file_location('replay_' + name, VERIFY_DESIGN_CASES.with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(module, *args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = module.main(list(args))
        except SystemExit as error:
            code = error.code
    return code, json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None, stderr.getvalue()


class PlatformAndSpecReplayTests(unittest.TestCase):
    def test_surface_alias_across_platform_aware_commands_and_top_level(self):
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            root = Path(tmp)
            source, spec, geometry, png = [root / name for name in ('View.swift', 'design.md', 'geometry.json', 'screen.png')]
            source.write_text('Text("Clean").padding(8)')
            geometry.write_text(json.dumps({'nodes': [{'role': 'button', 'frame': {'x': 0, 'y': 0, 'width': 44, 'height': 44}}]}))
            png.write_bytes(_make_png(80, 80, .8))
            specs = {
                'mobile': 'Axis X: single P0 per viewport.\nAxis Y: NavigationStack; 48pt targets.\nAxis Z: solid content; native floating glass.',
                'desktop': 'Axis X: single P0 per viewport.\nAxis Y: NavigationSplitView; 28pt targets; Cmd keyboard parity.\nAxis Z: inactive window dimming.',
            }
            for alias, platform in [('ios', 'mobile'), ('macos', 'desktop')]:
                spec.write_text(specs[platform])
                commands = [
                    ('route', '--dimensions', 'layout-navigation'),
                    ('audit-code', '--path', str(source)),
                    ('audit-geometry', str(geometry)),
                    ('check-whitespace-ratio', str(png)),
                    ('jev-gate', '--stage', 'spec', '--spec-file', str(spec), '--no-jev'),
                ]
                for command in commands:
                    for args in [(command[0], '--surface', alias, *command[1:]), ('--surface', alias, *command)]:
                        with self.subTest(args=args):
                            result = _run_cli(*args)
                            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                            self.assertEqual(json.loads(result.stdout)['platform'], platform)
                result = _run_cli('--surface', alias, '--audit-geometry', str(geometry))
                self.assertEqual(json.loads(result.stdout)['platform'], platform)
                # A 30pt button is valid desktop chrome, but must fail the mobile 44pt floor.
                result = _run_cli('audit-code', '--surface', alias, '--snippet', 'Text("x").glassEffect(.prominent)')
                self.assertEqual(result.returncode, 1)
            geometry.write_text(json.dumps({'nodes': [{'role': 'button', 'frame': {'width': 30, 'height': 30}}]}))
            self.assertEqual(_run_cli('audit-geometry', '--surface', 'ios', str(geometry)).returncode, 1)
            self.assertEqual(_run_cli('audit-geometry', '--surface', 'macos', str(geometry)).returncode, 0)
            spec.write_text('Axis X: single P0.\nAxis Y: Use HTML.\nAxis Z: content glass.')
            self.assertEqual(_run_cli('jev-gate', '--surface', 'ios', '--stage', 'spec', '--spec-file', str(spec), '--no-jev').returncode, 1)

    def test_route_surface_hint_compatibility_and_invalid_alias(self):
        for surface in ('ios', 'macos', 'mobile', 'desktop', 'web', 'popover'):
            result = _run_cli('route', '--platform', 'desktop', '--surface', surface)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['platform'], 'desktop')
        for command in ('route', 'audit-code', 'audit-geometry', 'check-whitespace-ratio', 'jev-gate'):
            result = _run_cli(command, '--surface', 'not-a-platform')
            self.assertEqual(result.returncode, 2)
            self.assertLessEqual(len(result.stderr.splitlines()), 3)
            self.assertIn('eval-cases.json', result.stderr)

    def test_audit_code_validates_both_code_and_spec_file(self):
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            source, spec = Path(tmp) / 'View.swift', Path(tmp) / 'design.md'
            source.write_text('Text("Clean")')
            spec.write_text('# Design\nKeep standard navigation.')
            for flag in ('--spec-file', '--spec'):
                result = _run_cli('audit-code', '--path', str(source), flag, str(spec))
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload['files'], 1)
                self.assertEqual(payload['spec_files'], [str(spec)])
                self.assertTrue(payload['spec_validated'])
            source.write_text('Text("x").glassEffect(.prominent)')
            result = _run_cli('audit-code', '--path', str(source), '--spec-file', str(spec))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(json.loads(result.stdout)['spec_validated'])
            source.write_text('Text("Clean")')
            spec.write_text('# Design\n只保留主列表，去掉冗余抽屉。')
            result = _run_cli('audit-code', '--path', str(source), '--spec-file', str(spec))
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['spec_validated'])
            spec.unlink()
            result = _run_cli('audit-code', '--path', str(source), '--spec-file', str(spec))
            self.assertEqual(result.returncode, 1)
            self.assertIn('SUBTRACTIVE_SPEC_MISSING', result.stdout)
            spec.write_text('# Design\nKeep standard navigation.')
            source.unlink()
            result = _run_cli('audit-code', '--path', str(source), '--spec-file', str(spec))
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertTrue(payload['spec_validated'])
            self.assertIn('code_errors', payload)


class StandaloneGateReplayTests(unittest.TestCase):
    def test_jev_truth_table_and_guard(self):
        jev = _load_script('design_jev')
        code, payload, _ = _invoke(jev, '--self-check', '--json')
        self.assertEqual(code, 0)
        self.assertTrue(payload['ok'])
        self.assertEqual(jev.validate_response({'q': {'noul': 0, 'status': 'PASS'}}, {'q': 'Evidence'}, 'mock')['status'], 'REJECT_REVISE')
        with mock.patch.object(jev.urllib.request, 'build_opener') as network:
            with self.assertRaises(ValueError):
                jev.live_response('http://example.com/v1/systemone', 1, {'evidence': [1]}, {'q': 'Evidence'})
            network.assert_not_called()

    def test_standalone_self_checks_noargs_and_failure_guidance(self):
        for name, refs in [
            ('design_evidence', ('design-flow-architecture.md', 'navigation-evidence-contract.md', 'breathing-and-clarity.md')),
            ('design_jev', ('eval-cases.json', 'routing-index.md')),
        ]:
            script = VERIFY_DESIGN_CASES.with_name(name + '.py')
            result = subprocess.run([sys.executable, str(script), '--self-check', '--json'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok'])
            module = _load_script(name)
            for args, expected in [((), 2), (('--unknown',), 2),
                                   ((('--verify-traceback', 'missing.json') if name == 'design_evidence' else ('--stage', 'spec', '--no-jev')), 1)]:
                code, _, stderr = _invoke(module, *args)
                self.assertEqual(code, expected)
                self.assertLessEqual(len(stderr.splitlines()), 3)
                for ref in refs:
                    self.assertIn('skills/my-designer/references/' + ref, stderr)

    def test_standalone_evidence_checks_and_jev_stage_reject_countercases(self):
        evidence, jev = _load_script('design_evidence'), _load_script('design_jev')
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            root = Path(tmp)
            geometry, image, trace, spec = [root / n for n in ('geometry.json', 'viewport.png', 'trace.json', 'design.md')]
            geometry.write_text(json.dumps({'nodes': [{'role': 'button', 'frame': {'width': 44, 'height': 44}}]}))
            for alias in ('mobile', 'ios', 'macos', 'desktop', 'web'):
                code, payload, _ = _invoke(evidence, '--audit-geometry', str(geometry), '--platform', alias, '--collision-threshold', '3')
                self.assertEqual(code, 0)
                self.assertTrue(payload['ok'])
            geometry.write_text(json.dumps({'nodes': [{'role': 'button', 'frame': {'width': 20, 'height': 20}}]}))
            self.assertEqual(_invoke(evidence, '--audit-geometry', str(geometry), '--platform', 'ios')[0], 1)
            image.write_bytes(_make_png(80, 80, .7))
            for flag in ('--check-whitespace', '--check-whitespace-ratio'):
                self.assertEqual(_invoke(evidence, flag, str(root), '--max-dead-space', '.4')[0], 0)
                self.assertEqual(_invoke(evidence, flag, str(image), '--max-dead-space', '.1')[0], 1)
            image.write_bytes(_make_png(390, 844, .8))
            report = {'schema': 'visual-traceback/v1', 'findings': [],
                      'rendered_evidence': [{'kind': 'screenshot', 'source': 'viewport.png#target', 'observation': 'Actual viewport inspected'}],
                      'clean': {'spec_source': 'design.md:1', 'implementation_source': 'viewport.png#target',
                                'checks': [{'requirement': '44pt target', 'assertion': {'operator': 'minimum', 'required': 44, 'observed': 44}}]}}
            trace.write_text(json.dumps(report))
            self.assertEqual(_invoke(evidence, '--verify-traceback', str(trace), '--platform', 'ios')[0], 0)
            self.assertEqual(_invoke(jev, '--stage', 'traceback', '--surface', 'ios', '--spec-file', str(trace), '--no-jev')[0], 0)
            report['clean']['checks'][0]['assertion']['observed'] = 20
            trace.write_text(json.dumps(report))
            self.assertEqual(_invoke(evidence, '--verify-traceback', str(trace), '--platform', 'ios')[0], 1)
            self.assertEqual(_invoke(jev, '--stage', 'traceback', '--surface', 'ios', '--spec-file', str(trace), '--no-jev')[0], 1)
            spec.write_text('Axis X: single P0 per viewport.\nAxis Y: NavigationStack; 48pt targets.\nAxis Z: solid content.')
            gate = _load_script('verify_design_cases')
            answers = {q: {'noul': 1} for q in gate.TAXONOMY['jev_verification_gates']['mobile']}
            common = ('--stage', 'spec', '--platform', 'ios', '--spec-file', str(spec))
            self.assertEqual(_invoke(jev, *common, '--mock-jev-json', json.dumps(answers))[0], 0)
            for q in answers:
                answers[q]['noul'] = 0
            self.assertEqual(_invoke(jev, *common, '--mock-jev-json', json.dumps(answers))[0], 1)
            self.assertEqual(_invoke(jev, *common, '--no-jev', '--mock-jev-json', '{}')[0], 1)
            self.assertEqual(_invoke(jev, '--stage', 'gallery', '--gallery-dir', str(root), '--no-jev')[0], 1)


class RuntimeCacheAndFallbackReplayTests(unittest.TestCase):
    def test_catalog_cache_reuses_module_and_invalidates_on_mtime(self):
        evidence = _load_script('design_evidence')
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            root = Path(tmp)
            catalog, prompts = root / 'design_catalog.py', root / 'gallery_prompts.py'
            catalog.write_text('value = 1\n')
            prompts.write_text('')
            with mock.patch.object(evidence, 'CATALOG_PATH', catalog):
                first = evidence.catalog()
                self.assertIs(first, evidence.catalog())
                self.assertIn((str(catalog), catalog.stat().st_mtime_ns), evidence._CATALOG_CACHE)
                catalog.write_text('value = 222\n')
                self.assertIsNot(first, evidence.catalog())
                catalog.unlink()
                self.assertFalse(evidence.catalog().available)
                errors, _ = evidence.gallery_errors(root, {'platform': 'mobile'})
                self.assertTrue(errors)
                catalog.write_text('value = 1\n')
                prompts.unlink()
                self.assertFalse(evidence.catalog().available)

    def test_import_without_design_studio_and_prompt_fallback(self):
        source = VERIFY_DESIGN_CASES.with_name('design_evidence.py').read_text()
        namespace = {'__file__': str(VERIFY_DESIGN_CASES), '__name__': 'decoupled_evidence'}
        real_is_file = Path.is_file
        with mock.patch.object(Path, 'is_file', lambda path: False if path.name == 'gallery_prompts.py' else real_is_file(path)):
            exec(compile(source, 'decoupled_evidence', 'exec'), namespace)
        paragraphs = namespace['_prompt_paragraphs']('# Heading\n\nA long common paragraph for duplicate detection.')
        self.assertEqual(namespace['_overlap_ratio'](paragraphs, paragraphs), 1)
        self.assertEqual(namespace['_overlap_ratio'](paragraphs, set()), 0)
        self.assertTrue(namespace['self_check']()['ok'])

    def test_portal_runtime_cache_reuses_success_and_failure_and_invalidates(self):
        evidence = _load_script('design_evidence')
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            root = Path(tmp)
            verifier = root / 'gallery_chooser_test.cjs'
            verifier.write_text('// runtime verifier\n')
            portal = root / 'index.html'
            runtime = 'function selectionSummary(){} function updateTelemetry(){} selectionSummary(config.goal,currentState(),root,config.cards);'
            portal.write_text('<script>' + runtime + '</script>')
            with mock.patch.object(evidence, 'CATALOG_PATH', root / 'design_catalog.py'), mock.patch.object(evidence.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
                first = evidence.portal_errors(root)
                self.assertEqual(first, evidence.portal_errors(root))
                self.assertEqual(run.call_count, 1)
                self.assertEqual(len(evidence._PORTAL_RUNTIME_CACHE), 1)
                portal.write_text('<script>' + runtime + '// changed runtime</script>')
                run.return_value.returncode = 1
                errors = evidence.portal_errors(root)
                self.assertTrue(any('runtime behavior failed' in error for error in errors))
                self.assertEqual(errors, evidence.portal_errors(root))
                self.assertEqual(run.call_count, 2)
                verifier.write_text('// changed verifier\n')
                evidence.portal_errors(root)
                self.assertEqual(run.call_count, 3)

    def test_missing_taxonomy_routes_and_jev_fallback_but_invalid_json_rejected(self):
        gate = _load_script('verify_design_cases')
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            root = Path(tmp)
            missing = root / 'missing.json'
            with mock.patch.object(gate, 'DEFAULT_TAXONOMY', missing):
                self.assertEqual(gate._load_taxonomy(), gate._FALLBACK_TAXONOMY)
            for platform in ('mobile', 'desktop', 'web'):
                code, payload, _ = _invoke(gate, 'route', '--platform', platform, '--taxonomy', str(missing))
                self.assertEqual(code, 0)
                self.assertTrue(gate._load_taxonomy(missing)['jev_verification_gates'][platform])
            # Simulate an absent design-studio vault as well as taxonomy.
            original_read = gate._read_json
            with mock.patch.object(gate, '_read_json', side_effect=lambda path: None if 'design-studio' in str(path) else original_read(path)):
                code, payload, _ = _invoke(gate, 'flow-map', '--taxonomy', str(missing))
            self.assertEqual(code, 0)
            self.assertEqual(payload['shared_tiers']['tier_1_indexed_vault']['desktop_macos_cards'], 0)
            self.assertEqual(len(payload['reference_drawers']), 8)
            spec = root / 'design.md'
            spec.write_text('Axis X: single P0 per viewport.\nAxis Y: NavigationStack; 48pt targets.\nAxis Z: solid content.')
            common = ('jev-gate', '--surface', 'ios', '--stage', 'spec', '--spec-file', str(spec), '--taxonomy', str(missing))
            answers = {q: {'noul': 1} for q in gate._FALLBACK_TAXONOMY['jev_verification_gates']['mobile']}
            self.assertEqual(_invoke(gate, *common, '--mock-jev-json', json.dumps(answers))[0], 0)
            for q in answers:
                answers[q]['noul'] = 0
            self.assertEqual(_invoke(gate, *common, '--mock-jev-json', json.dumps(answers))[0], 1)
            missing.write_text('not json')
            self.assertEqual(_invoke(gate, *common, '--no-jev')[0], 1)


class InspectJsonReplayTests(unittest.TestCase):
    def test_preview_traceback_cases_and_generic_are_read_only_soft_advisories(self):
        gate = _load_script('verify_design_cases')
        artifacts = [
            ('preview-selection.json', {'selectedVariantID': 'v02', 'starredVariantIDs': ['v01'], 'notesByVariant': {'v02': 'clear'}}, 'preview-selection'),
            ('trace.json', {'schema': 'visual-traceback/v1', 'findings': [{'classification': 'SPEC_GAP'}], 'rendered_evidence': []}, 'visual-traceback/v1'),
            ('index.json', {'active_cases': [{'id': 'DC-001'}]}, 'defect-cases/index'),
            ('eval.json', {'schema': 'skill-eval-cases/v1', 'active_cases': []}, 'skill-eval-cases/v1'),
            ('generic.json', {'items': [1, 2], 'version': 1}, 'generic-json'),
            ('list.json', [1, 2], 'generic-json'),
        ]
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            for name, data, kind in artifacts:
                path = Path(tmp) / name
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                for args in [('inspect-json', str(path)), ('--inspect-json', str(path))]:
                    code, payload, stderr = _invoke(gate, *args)
                    self.assertEqual(code, 0, stderr)
                    self.assertTrue(payload['ok'])
                    self.assertEqual(payload['mode'], 'soft_advisory')
                    self.assertEqual(payload['artifact_kind'], kind)
                    if kind == 'preview-selection':
                        self.assertEqual(payload['summary']['selectedVariantID'], 'v02')
                        self.assertEqual(payload['summary']['notesByVariant'], {'v02': 'clear'})
                    if name == 'generic.json':
                        self.assertEqual(payload['summary']['counts']['items'], 2)
                    self.assertEqual(path.read_bytes(), before)

    def test_invalid_json_or_path_emits_guidance(self):
        gate = _load_script('verify_design_cases')
        with tempfile.TemporaryDirectory(dir=VERIFY_DESIGN_CASES.parent) as tmp:
            path = Path(tmp) / 'bad.json'
            for content in (None, '{broken', b'\xff'):
                if content is not None:
                    path.write_bytes(content if isinstance(content, bytes) else content.encode())
                code, payload, stderr = _invoke(gate, '--inspect-json', str(path))
                self.assertEqual(code, 1)
                self.assertFalse(payload['ok'])
                self.assertLessEqual(len(stderr.splitlines()), 3)
                self.assertIn('routing-index.md', stderr)
                self.assertIn('eval-cases.json', stderr)


class ManifestAndTriggerBoundaryReplayTests(unittest.TestCase):
    def test_manifest_and_explicit_trigger_boundaries(self):
        skill = VERIFY_DESIGN_CASES.parents[1]
        manifest = json.loads((skill / '.claude-plugin/plugin.json').read_text())
        text = (skill / 'SKILL.md').read_text()
        description = text.split('description: "', 1)[1].split('"', 1)[0]
        self.assertEqual(manifest, {'name': 'my-designer', 'description': description, 'version': '2.0.2', 'category': 'design'})
        self.assertLessEqual(len(description), 320)
        self.assertLess(len(text.splitlines()), 80)
        self.assertLess(len(text.encode()), 12 * 1024)
        for trigger in ('Entry B of the Dual-Entry Design Flow', 'SwiftUI View', 'UI redesign', 'layout/density/whitespace',
                        'PreviewHostKit/#Preview', 'Liquid Glass', 'design.md/spec.md', 'Visual Traceback [IMPL_BUG]/[SPEC_GAP]',
                        'Not for external inspiration vault ingestion (use design-studio)', 'backend telemetry readers', 'entitlements', 'non-UI unit tests'):
            self.assertIn(trigger, description)
        for keyword in ('No Code Demo', 'r5-todo-swiftui-preview', '2+1+1'):
            self.assertIn(keyword, text)
        gate = _load_script('verify_design_cases')
        linked = {Path(path).name for path, _ in gate.DRAWER_GUIDANCE.values()}
        drawers = {path.name for path in (skill / 'references').iterdir() if path.suffix in {'.md', '.json'}}
        self.assertEqual(linked, drawers)
        self.assertEqual(len(linked), 8)


if __name__ == "__main__":
    unittest.main()
