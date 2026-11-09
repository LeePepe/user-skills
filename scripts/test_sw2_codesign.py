#!/usr/bin/env python3
"""Focused contract tests for Sub-Workflow 2."""

from __future__ import annotations

import base64
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = load("spec_gate", "skills/codebase-design/scripts/spec_gate.py")
research = load("jev_research_helper", "skills/deep-research/scripts/jev_research_helper.py")
visual = load("jev_visual_gate", "skills/my-designer/scripts/jev_visual_gate.py")
design_cases = load("verify_design_cases", "skills/my-designer/scripts/verify_design_cases.py")
xcode_classify = load("classify_changes", "skills/xcode-cli/scripts/classify_changes.py")


class SpecGateTests(unittest.TestCase):
    def test_digest_is_non_circular(self):
        source = "---\nversion: 1\nspec_sha: old\nupdated_at: now\n---\n\nBody\n"
        digest = gate.canonical_spec_digest(source)
        stamped = source.replace("spec_sha: old", f"spec_sha: {digest}")
        self.assertEqual(digest, gate.canonical_spec_digest(stamped))

    def test_proposal_checkpoint_pop_lifecycle_and_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            args = type("Args", (), {
                "handoff_dir": str(path), "agent": "a", "model": "m", "proposal_id": "p1",
                "label": "Option", "interface_summary": "small", "seam_placement": "file",
                "dependency_category": "in_process", "recommended": True, "content": "x",
            })()
            code, result = gate.command_push(args)
            self.assertEqual(code, 0)
            self.assertTrue(result["ok"])
            status_code, status = gate.command_status(type("Args", (), {"handoff_dir": str(path)})())
            self.assertEqual(status["status"], "pending")
            checkpoint_args = type("Args", (), {
                "handoff_dir": str(path), "proposal_id": "p1", "decision": "yes",
                "decided_by": "owner", "reasoning": "", "action": [],
            })()
            self.assertEqual(gate.command_checkpoint(checkpoint_args)[0], 0)
            self.assertEqual(gate.command_pop(type("Args", (), {"handoff_dir": str(path)})())[0], 0)
            args.proposal_id = "p2"
            args.label = "Option 2"
            self.assertEqual(gate.command_push(args)[0], 0)
            self.assertEqual(gate.command_pop(type("Args", (), {"handoff_dir": str(path)})())[0], 1)

    def test_cross_topic_and_cas(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            path.joinpath("proposals.json").write_text(json.dumps({
                "topic_id": "topic-a", "proposals": [], "proposals_sha": "x"
            }), encoding="utf-8")
            args = type("Args", (), {"handoff_dir": str(path), "proposal_id": "missing",
                                     "decision": "no", "decided_by": "owner", "reasoning": "", "action": []})()
            self.assertEqual(gate.command_checkpoint(args)[0], 1)
            spec = path / "spec.md"
            spec.write_text("---\nversion: 1\nspec_sha: old\n---\n\nBody\n", encoding="utf-8")
            args = type("Args", (), {"file": str(spec), "expected_version": 2, "expected_sha": None, "bump_version": False})()
            code, result = gate.command_stamp(args)
            self.assertEqual(code, 1); self.assertEqual(result["code"], "SPEC_VERSION_CONFLICT")

    def test_directional_lock_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp); spec = path / "spec.md"
            spec.write_text("---\nversion: 1\nspec_sha: wrong\n---\n\nBody\n", encoding="utf-8")
            (path / "design.md").write_text("---\nversion: 1\nparent_spec_version: 0\nparent_spec_sha: wrong\ndesign_sha: x\n---\n\nDesign\n", encoding="utf-8")
            code, result = gate.command_verify_lock(type("Args", (), {"handoff_dir": str(path), "mandate_json": json.dumps({"spec_sha": "wrong", "design_sha": "wrong"})})())
            self.assertEqual(code, 1); self.assertIn("STALE_DESIGN", result["issues"]); self.assertIn("STALE_MANDATE", result["issues"])


class ProgressiveRouterTests(unittest.TestCase):
    def test_route_discloses_hig_and_plan_a_lenses(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = design_cases.main(["route", "--platform", "desktop", "--dimensions", "glass-material,chart-dataviz"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["baseline_mode"], "plan-a-apple-hig")
        self.assertIn("DC-002", [c["id"] for c in payload["plan_c_active_cases"]])
        self.assertIn("liquid-glass.md", payload["progressive_disclosure"]["hig_files_to_load"])
        self.assertIn("charts.md", payload["progressive_disclosure"]["hig_files_to_load"])
        self.assertIn("designing-for-macos.md", payload["progressive_disclosure"]["distilled_in_plan_a"])
        self.assertIn("liquid-glass.md", payload["progressive_disclosure"]["on_demand_external_refs"])
        self.assertNotIn("designing-for-macos.md", payload["progressive_disclosure"]["on_demand_external_refs"])
        ext_routing = payload["progressive_disclosure"]["external_skill_routing"]
        self.assertIn("allowed_native_sources", ext_routing)
        self.assertIn("apple-design-skill", ext_routing["allowed_native_sources"])
        self.assertIn("shipswift", ext_routing["allowed_native_sources"])
        self.assertIn("awesome-ios-design-md", ext_routing["allowed_native_sources"])
        self.assertIn("genjutsu_native_only", ext_routing["allowed_native_sources"])
        self.assertIn("claude-code-apple-skills", ext_routing["allowed_native_sources"])
        self.assertIn("impeccable_native_only", ext_routing["allowed_native_sources"])
        self.assertIn("charts", ext_routing["allowed_native_sources"]["shipswift"]["focused_packages"])
        self.assertIn("metal_shaders", ext_routing["allowed_native_sources"]["shipswift"]["focused_packages"])
        self.assertNotIn("modules", ext_routing["allowed_native_sources"]["shipswift"]["focused_packages"])
        self.assertEqual(payload["progressive_disclosure"]["context_budget"]["vault_search_top_k"], 3)
        self.assertEqual(payload["progressive_disclosure"]["context_budget"]["l1_doc_max_lines"], 130)
        self.assertEqual(payload["progressive_disclosure"]["appearance_ledger"], "skills/my-designer/references/appearance-ledger.md")
        self.assertEqual(payload["progressive_disclosure"]["interaction_layering_ref"], "skills/my-designer/references/interaction-layering.md")
        self.assertIsNone(payload["progressive_disclosure"]["breathing_and_clarity_ref"])

        buf_vs = io.StringIO()
        with contextlib.redirect_stdout(buf_vs):
            design_cases.main(["route", "--platform", "desktop", "--dimensions", "visual-style,typography-copy"])
        payload_vs = json.loads(buf_vs.getvalue())
        self.assertEqual(
            payload_vs["progressive_disclosure"]["breathing_and_clarity_ref"],
            "skills/my-designer/references/breathing-and-clarity.md",
        )
        vs_shipswift_focused = payload_vs["progressive_disclosure"]["external_skill_routing"]["allowed_native_sources"]["shipswift"]["focused_packages"]
        self.assertNotIn("charts", vs_shipswift_focused)
        self.assertIn("metal_shaders", vs_shipswift_focused)
        self.assertTrue(any("taste-skill" in b for b in ext_routing["blocked_web_only_sources"]))
        ledger_text = (ROOT / "skills" / "my-designer" / "references" / "appearance-ledger.md").read_text(encoding="utf-8")
        for token in ("Cross-Domain Archetype Remix Matrix", "VS-005", "VS-006", "VS-007", "VS-008", "CL-006", "CL-007", "CL-008"):
            self.assertIn(token, ledger_text)

    def test_flow_map_and_anti_explosion_budget(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = design_cases.main(["flow-map"])
        self.assertEqual(code, 0)
        fm = json.loads(buf.getvalue())
        self.assertTrue(fm["ok"])
        self.assertTrue(fm["anti_explosion_valves"]["all_l1_docs_within_budget"])
        self.assertIn("entry_a_ingest", fm)
        self.assertIn("shared_tiers", fm)
        self.assertIn("entry_b_design", fm)
        self.assertIn("DC-003", fm["shared_tiers"]["tier_3_executable_gates"]["plan_c_case_ids"])
        for doc_path, lines in fm["shared_tiers"]["tier_2_l1_docs_line_counts"].items():
            self.assertLessEqual(lines, 130, f"{doc_path} exceeds 130-line cap ({lines})")


class ResearchTests(unittest.TestCase):
    def test_r09_score_and_truth_table(self):
        answers = {}
        for i, (direct, tier) in enumerate(((0.9, 3), (0.8, 1), (0.5, 2), (0.2, 3), (0.4, 1))):
            answers[f"cand_{i}_direct"] = {"noul": direct}
            answers[f"cand_{i}_tier"] = {"score": tier, "probabilities": {"0": 0.1, "2": 0.7, "3": 0.2}}
        rows = research.evaluate_r09_truth_table(answers)
        by_index = {row["index"]: row["action"] for row in rows}
        self.assertEqual(by_index, {0: "KEEP_PRIMARY", 1: "EXPAND_SNIPPET", 2: "KEEP_SUPPORTING", 3: "DROP_NOISE", 4: "LOW_PRIORITY_BUFFER"})
        self.assertEqual(rows[0]["composite_score"], 0.935)

    def test_r06_step1_and_truth_table(self):
        step = research.check_citation_step1("A “quoted”  value", "A \"quoted\" value")
        self.assertEqual(step["status"], "quote_found")
        missing = research.check_citation_step1("not present", "source")
        self.assertEqual(missing["verdict"], "FABRICATED_QUOTE")
        self.assertEqual(research.evaluate_r06_truth_table({"status": "missing"})["confidence_cap"], "REFUTED")
        answers = {"relation": {"choice": "supports", "probabilities": {"supports": 0.9}},
                   "addresses_claim": {"noul": 0.9}, "quant_and_scope_intact": {"noul": 0.9}}
        self.assertEqual(research.evaluate_r06_truth_table({"status": "quote_found"}, answers)["verdict"], "VERIFIED")
        answers["relation"] = {"choice": "contradicts", "probabilities": {"contradicts": 0.8}}
        self.assertEqual(research.evaluate_r06_truth_table({"status": "quote_found"}, answers)["verdict"], "CONTRADICTED")

    def test_research_flow_map_and_topic_channel_routing(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = research.main(["flow-map"])
        self.assertEqual(rc, 0)
        fm = json.loads(buf.getvalue())
        self.assertTrue(fm["ok"])
        self.assertTrue(fm["anti_explosion_valves"]["all_l1_docs_within_budget"])
        self.assertIn("ST-001", fm["shared_tiers"]["tier_1_json_drawer"]["tier_rule_ids"])

        buf_route = io.StringIO()
        with contextlib.redirect_stdout(buf_route):
            rc_route = research.main(["route", "--topic-type", "tooling", "--depth", "standard"])
        self.assertEqual(rc_route, 0)
        routed = json.loads(buf_route.getvalue())
        self.assertEqual(routed["active_channels"], ["github", "web"])
        self.assertIn("academic", routed["skipped_channels"])
        self.assertLessEqual(len(routed["matched_source_tiers"]), 3)

        buf_xc = io.StringIO()
        with contextlib.redirect_stdout(buf_xc):
            rc_xc = xcode_classify.main(["--flow-map"])
        self.assertEqual(rc_xc, 0)
        xc_fm = json.loads(buf_xc.getvalue())
        self.assertTrue(xc_fm["ok"])
        self.assertIn("XC-001", xc_fm["shared_tiers"]["tier_1_json_drawer"]["active_case_ids"])
        swiftpm_cases = [c["id"] for c in xcode_classify.match_build_cases("swiftpm")]
        self.assertIn("XC-001", swiftpm_cases)
        self.assertNotIn("XC-002", swiftpm_cases)


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class VisualTests(unittest.TestCase):
    def test_layer0_matrix_width_and_polarity(self):
        snapshots = {f"{state}-{appearance}": png(20, 4, (250, 250, 250) if appearance == "light" else (10, 10, 10))
                     for state in visual.STATES for appearance in visual.APPEARANCES}
        result = visual.audit_snapshots_layer0(snapshots, 10, 2)
        self.assertTrue(result["ok"])
        snapshots["empty-dark"] = png(19, 4, (10, 10, 10))
        self.assertFalse(visual.audit_snapshots_layer0(snapshots, 10, 2)["ok"])

    def test_request_mapping_and_truth_table(self):
        request = visual.build_r05_request("screen", "populated", "light", {"full": png(4, 4, (255, 255, 255))})
        self.assertEqual(request["model"], "jev-1.14.0"); self.assertEqual(request["headers"]["TypeSafe-Danger-Zone-Images"], "true")
        self.assertEqual(visual.map_tile_box_to_image({"left": .1, "right": .5, "top": .2, "bottom": .6}, .29, .71, 100, 200), [10, 75, 50, 108])
        self.assertEqual(visual.map_zoom_box_to_image({"left": 0, "top": 0, "right": 1, "bottom": 1}, [.1, .2, .5, .6], 100, 200), [10, 40, 50, 120])
        self.assertEqual(visual.evaluate_r05_truth_table(.1)["verdict"], "PASS_CLEAN")
        self.assertTrue(visual.evaluate_r05_truth_table(.9, .8, None, 0, [10, 10, 30, 30], (100, 100))["fail"])
        self.assertEqual(visual.evaluate_r05_truth_table(.5, 0, .1, .8)["verdict"], "PASS_CLEARED_ON_ZOOM")
        self.assertEqual(visual.evaluate_r05_truth_table(.5, 0, .8, .8)["verdict"], "FAIL_CONFIRMED_ON_ZOOM")


class DesignCasesAndRoutingTests(unittest.TestCase):
    def test_plan_a_default_and_plan_c_case_promotion(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp)
            cases_file = root / "index.json"
            src_cases = ROOT / "skills" / "my-designer" / "references" / "defect-cases" / "index.json"
            taxonomy_file = ROOT / "skills" / "design-studio" / "references" / "taxonomy-v2.json"
            cases_file.write_bytes(src_cases.read_bytes())

            swift_file = root / "SampleView.swift"
            swift_file.write_text(
                "import SwiftUI\nstruct SampleView: View {\n"
                "  var body: some View {\n"
                "    Text(\"Hi\").frame(width: 380).padding(16)\n"
                "  }\n}\n",
                encoding="utf-8",
            )
            # Under pure Plan A default (active_cases == []), frame-before-padding is not flagged until promoted to Plan C
            self.assertEqual(
                design_cases.main([
                    "audit-code", "--cases-file", str(cases_file), "--taxonomy", str(taxonomy_file),
                    "--platform", "desktop", "--path", str(swift_file),
                ]),
                0,
            )
            # Register a new Plan C defect case (DC-999) when discovered
            self.assertEqual(
                design_cases.main([
                    "register-case", "--cases-file", str(cases_file), "--taxonomy", str(taxonomy_file),
                    "--id", "DC-999", "--title", "Popover Frame Before Padding",
                    "--platforms", "desktop", "--dimensions", "layout-navigation",
                    "--containers", "NSPopover",
                    "--symptom", "Clips trailing content",
                    "--fix-rule", "Use .padding(P).frame(width: W)",
                    "--forbidden-regex", r"\.frame\(\s*width\s*:[^)]+\)\s*\.padding\(",
                ]),
                0,
            )
            # Now Plan C catches the defect on desktop
            self.assertEqual(
                design_cases.main([
                    "audit-code", "--cases-file", str(cases_file), "--taxonomy", str(taxonomy_file),
                    "--platform", "desktop", "--dimensions", "layout-navigation",
                    "--container", "NSPopover", "--path", str(swift_file),
                ]),
                1,
            )

            # Plan A + Plan C catch SwiftUI anti-slop & anti-flat-tiling defects:
            # purple-blue gradient, macOS .hoverEffect, flattened SWWallet, duplicate Donut+ProgressView
            slop_file = root / "SlopView.swift"
            slop_file.write_text(
                "import SwiftUI\n"
                "struct SWWallet: View {\n"
                "  var body: some View { VStack { ForEach(0..<3, id: \\.self) { _ in Text(\"Flat\") } } }\n"
                "}\n"
                "struct SlopView: View {\n"
                "  var body: some View {\n"
                "    VStack {\n"
                "      SWDonutChart()\n"
                "      ProgressView(value: 0.5)\n"
                "      LinearGradient(colors: [.purple, .blue], startPoint: .top, endPoint: .bottom)\n"
                "        .hoverEffect(.highlight)\n"
                "    }\n"
                "  }\n}\n",
                encoding="utf-8",
            )
            buf_audit = io.StringIO()
            with contextlib.redirect_stdout(buf_audit):
                rc = design_cases.main([
                    "audit-code", "--cases-file", str(cases_file), "--taxonomy", str(taxonomy_file),
                    "--platform", "desktop", "--path", str(slop_file),
                ])
            self.assertEqual(rc, 1)
            audit_ids = {f["id"] for f in json.loads(buf_audit.getvalue())["findings"]}
            for expected_id in (
                "SLOP-PURPLE-GRADIENT",
                "HIG-MACOS-HOVER-EFFECT",
                "SHIPSWIFT-SWWALLET-FLATTENED",
                "SLOP-DUPLICATE-DATA-REPRESENTATION",
                "DC-001",
                "DC-002",
            ):
                self.assertIn(expected_id, audit_ids)

            # WWDC25/26 static rules: HIG-IOS-SCREEN-BOUNDS (idiom/orientation), HIG-LAZY-LEAF-CONDITIONAL + DC-003,
            # HIG-APPKIT-LEGACY-MOUSELOOP, and HIG-DRAG-WILL-BEGIN-UI
            wwdc_file = root / "WWDC26Violations.swift"
            wwdc_file.write_text(
                "import SwiftUI\n"
                "struct FeedView: View {\n"
                "  let items = [1, 2, 3]\n"
                "  var body: some View {\n"
                "    LazyVStack {\n"
                "      ForEach(items, id: \\.self) { item in\n"
                "        if item > 1 { Text(\"\\(item)\") }\n"
                "      }\n"
                "    }\n"
                "  }\n"
                "  func checkIdiom() -> Bool {\n"
                "    UIDevice.current.userInterfaceIdiom == .phone || interfaceOrientation.isLandscape\n"
                "  }\n"
                "  override func mouseDown(with event: NSEvent) {\n"
                "    while let next = window?.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) { _ = next }\n"
                "  }\n"
                "  func dragInteraction(_ interaction: UIDragInteraction, sessionWillBegin session: UIDragSession) {\n"
                "    withAnimation { present(UIAlertController(), animated: true) }\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            buf_wwdc = io.StringIO()
            with contextlib.redirect_stdout(buf_wwdc):
                rc_wwdc = design_cases.main([
                    "audit-code", "--cases-file", str(cases_file), "--taxonomy", str(taxonomy_file),
                    "--platform", "desktop", "--path", str(wwdc_file),
                ])
            self.assertEqual(rc_wwdc, 1)
            wwdc_ids = {f["id"] for f in json.loads(buf_wwdc.getvalue())["findings"]}
            for expected_wwdc_id in (
                "HIG-IOS-SCREEN-BOUNDS",
                "HIG-LAZY-LEAF-CONDITIONAL",
                "HIG-APPKIT-LEGACY-MOUSELOOP",
                "HIG-DRAG-WILL-BEGIN-UI",
                "DC-003",
            ):
                self.assertIn(expected_wwdc_id, wwdc_ids)


if __name__ == "__main__":
    unittest.main()
