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
slots = load("design_slots", "skills/my-designer/scripts/design_slots.py")
research = load("jev_research_helper", "skills/deep-research/scripts/jev_research_helper.py")
visual = load("jev_visual_gate", "skills/my-designer/scripts/jev_visual_gate.py")


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


class DesignSlotTests(unittest.TestCase):
    def test_merge_modes_and_locked_redlines(self):
        redline = slots.DesignSlot("role-handoff-only", "Role", "locked", 0, True, "locked", 1)
        default = slots.DesignSlot("craft", "Craft", "base", 1, True, "append", 1)
        appended = slots.DesignSlot("craft", "Craft", "extra", 1, True, "append", 1)
        self.assertEqual(slots.merge_design_slots([redline], [default], [appended])[1].body, "base\n\nextra")
        cleared = slots.DesignSlot("craft", "Craft", "", 1, True, "override", 1)
        self.assertEqual(len(slots.merge_design_slots([], [default], [cleared])), 0)
        disabled = slots.DesignSlot("craft", "Craft", "ignored", 1, False, "override", 1)
        self.assertEqual(slots.merge_design_slots([], [default], [disabled])[0].body, "base")
        with self.assertRaisesRegex(ValueError, "RESERVED_GLOBAL_REDLINE"):
            slots.merge_design_slots([redline], [], [redline])

    def test_all_category_prompts(self):
        categories = {"macos-statusbar-popover", "ios-dense-single-hand", "web-saas-data-workspace", "editorial-narrative"}
        for category in categories:
            result = slots.compose_designer_prompt(category, {"job": "test"}, {"source": "fact"})
            self.assertTrue(result["ok"]); self.assertEqual(len(result["receipt"]["prompt_sha"]), 64)
            self.assertIn("trusted=\"untrusted_factual_only\"", result["prompt"])


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


if __name__ == "__main__":
    unittest.main()
