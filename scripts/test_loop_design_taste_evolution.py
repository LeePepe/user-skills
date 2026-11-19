import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
LOOP_PATH = ROOT / "skills/design-studio/scripts/taste_evolution_loop.py"
RUNNER_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
ISSUE_PATH = ROOT / "skills/issue-brief/scripts/verify_issue_format.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


loop = load("test_taste_evolution_loop", LOOP_PATH)
runner = load("test_taste_evolution_runner", RUNNER_PATH)
issue_brief = load("test_taste_evolution_issue_brief", ISSUE_PATH)


class TasteHarvestTests(unittest.TestCase):
    def make_receipts(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        receipts = root / "goal" / "receipts"
        receipts.mkdir(parents=True)
        payload = {
            "schema": 1,
            "selectedVariantID": "v02",
            "starredVariantIDs": ["v01"],
            "notesByVariant": {"v02": "compact split header"},
            "variants": {"v01": {}, "v02": {}, "v03": {}},
        }
        (receipts / "preview-selection-r1.json").write_text(json.dumps(payload), encoding="utf-8")
        active = dict(payload)
        active["rejectedVariantIDs"] = ["v03"]
        (receipts / "preview-selection.json").write_text(json.dumps(active), encoding="utf-8")
        (receipts / "visual-review.md").write_text(
            "[IMPL_BUG] mobile bottom capsule on macOS\n[SPEC_GAP] numeric truncation …\n", encoding="utf-8"
        )
        return tmp, root

    def test_harvests_active_archived_selection_notes_and_tracebacks(self):
        tmp, root = self.make_receipts()
        self.addCleanup(tmp.cleanup)
        result = loop.harvest(root)
        self.assertEqual(result["selection_files"], 2)
        self.assertEqual(result["inventory"], ["v01", "v02", "v03"])
        self.assertTrue(any(row["text"] == "compact split header" for row in result["positive_preferences"]))
        self.assertTrue(any(row["variant_id"] == "v03" for row in result["rejections"]))
        self.assertEqual({"IMPL_BUG", "SPEC_GAP"}, {c for row in result["visual_tracebacks"] for c in row["classifications"]})

    def test_harvest_does_not_invent_rejections_for_unselected_inventory(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        receipts = Path(tmp.name) / "g" / "receipts"
        receipts.mkdir(parents=True)
        (receipts / "preview-selection-r1.json").write_text(
            json.dumps({"selectedVariantID": "v01", "variants": {"v01": {}, "v02": {}, "v03": {}}}), encoding="utf-8"
        )
        self.assertEqual(loop.harvest(Path(tmp.name))["rejections"], [])

    def test_harvest_deduplicates_same_content_but_preserves_sources(self):
        tmp, root = self.make_receipts()
        self.addCleanup(tmp.cleanup)
        result = loop.harvest(root)
        note = next(row for row in result["positive_preferences"] if row["kind"] == "note")
        self.assertEqual(len(note["sources"]), 2)
        self.assertEqual(len([row for row in result["positive_preferences"] if row["text"] == "compact split header"]), 1)


class TasteDecisionTests(unittest.TestCase):
    def test_mock_choice_is_used_without_live_call_and_gate_ids_are_stable(self):
        harvested = {
            "positive_preferences": [{"kind": "note", "variant_id": "v1", "text": "compact hierarchy", "count": 2, "sources": []}],
            "rejections": [],
            "visual_tracebacks": [],
        }
        mock = {"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.9}, "score": {"score": 0.8}}}}
        proposal = loop.propose_gates(harvested, mock_jev_json=mock)
        self.assertEqual(len(proposal["positive_preferences"]), 1)
        self.assertTrue(proposal["positive_preferences"][0]["rule_id"].startswith("IP-"))
        self.assertEqual(proposal["positive_preferences"][0]["decision"]["source"], "jev")

    def test_invalid_mock_falls_back_and_never_uses_incomplete_response(self):
        candidate = {"kind": "visual_traceback", "text": "flat cards without hierarchy", "count": 1, "sources": []}
        clustered = loop.cluster({"positive_preferences": [], "rejections": [], "visual_tracebacks": [candidate]}, mock_jev_json="not-json")
        self.assertEqual(clustered["candidates"][0]["decision"]["choice"], "PROMOTE_ANTI_PATTERN_GATE")
        self.assertIn("invalid_mock", clustered["candidates"][0]["decision"]["source"])

    def test_incomplete_mock_is_rejected_without_live_fallback(self):
        harvested = {
            "positive_preferences": [{"kind": "note", "variant_id": "v1", "text": "compact hierarchy", "count": 2, "sources": []}],
            "rejections": [],
            "visual_tracebacks": [],
        }
        clustered = loop.cluster(harvested, mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}}}})
        decision = clustered["candidates"][0]["decision"]
        self.assertIn("invalid_mock", decision["source"])
        self.assertEqual(decision["choice"], "PROMOTE_POSITIVE_ARCHETYPE")

    def test_grey_and_constrained_choice_use_deterministic_fallback(self):
        harvested = {
            "positive_preferences": [],
            "rejections": [{"kind": "rejection", "variant_id": "v1", "text": "cramped density", "count": 1, "sources": []}],
            "visual_tracebacks": [],
        }
        mock = {"default": {"answers": {"promotion": {"choice": "not-allowed"}, "confidence": {"noul": 0.5}, "score": {"score": 0.4}}}}
        proposal = loop.propose_gates(harvested, mock_jev_json=mock)
        self.assertEqual(len(proposal["anti_pattern_gates"]), 1)
        self.assertTrue(proposal["anti_pattern_gates"][0]["gate_id"].startswith("DC-"))


class TasteCliAndRunnerTests(unittest.TestCase):
    def test_cli_commands_emit_json_and_issue_is_verified(self):
        tmp, root = TasteHarvestTests().make_receipts()
        self.addCleanup(tmp.cleanup)
        proc = subprocess.run(["python3", str(LOOP_PATH), "propose-gates", "--handoff-root", str(root)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gate_candidates", proc.stdout)
        report = loop.loop_check(root, mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.8}, "score": {"score": 0.8}}}})
        self.assertTrue(issue_brief.verify_dual_zone_issue(report["dual_zone_markdown"])["ok"])

    def test_runner_registers_collector_and_no_delta_watermark_is_stable(self):
        tmp, root = TasteHarvestTests().make_receipts()
        self.addCleanup(tmp.cleanup)
        old = os.environ.get("DESIGN_TASTE_HANDOFF_ROOT")
        os.environ["DESIGN_TASTE_HANDOFF_ROOT"] = str(root)
        self.addCleanup(lambda: os.environ.__setitem__("DESIGN_TASTE_HANDOFF_ROOT", old) if old is not None else os.environ.pop("DESIGN_TASTE_HANDOFF_ROOT", None))
        spec = runner.resolve_loop_spec("design-taste-evolution")
        collected = runner._default_loop_collector(spec, target_date=__import__("datetime").date(2026, 11, 19), mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.8}, "score": {"score": 0.8}}}})
        self.assertEqual(collected["loop_id"], "design-taste-evolution")
        self.assertIn("watermark", collected)
        with tempfile.TemporaryDirectory() as state:
            state_file = Path(state) / "watermarks.json"
            first = runner.run_loop("design-taste-evolution", state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json={"answers": {"actionable_probability": {"noul": 0.1}, "delivery_route": {"choice": "ignore"}}})
            self.assertEqual(first["status"], "SKIPPED_NOT_ACTIONABLE")
            second = runner.run_loop("design-taste-evolution", state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json={"answers": {"actionable_probability": {"noul": 0.1}, "delivery_route": {"choice": "ignore"}}})
            self.assertEqual(second["status"], "SKIPPED_NO_DELTA")

    def test_autopilot_description_directs_mac_execution(self):
        spec = runner.resolve_loop_spec("design-taste-evolution")
        self.assertIn("Mac node", runner.build_loop_autopilot_description(spec))
        self.assertIn("NAS filesystem as the evidence source", runner.build_loop_autopilot_description(spec))


if __name__ == "__main__":
    unittest.main()
