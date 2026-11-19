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
            "variants": {
                "v01": {"title": "calm glass split"},
                "v02": {"summary": "compact split header", "notes": "compact split header"},
                "v03": {"title": "dense table"},
            },
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
        note = next(row for row in result["positive_preferences"] if row["kind"] == "note")
        self.assertEqual(note["text"], "compact split header")
        self.assertEqual(note["count"], 1)
        self.assertEqual(len(note["sources"]), 2)
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
        self.assertEqual(note["count"], 1)

    def test_variant_state_and_per_receipt_inventory_guard(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        receipts = root / "one" / "receipts"
        receipts.mkdir(parents=True)
        (receipts / "preview-selection-r1.json").write_text(json.dumps({"variants": {"v01": {"isSelected": True, "isStarred": True, "notes": "quiet hierarchy", "title": "Quiet"}}, "rejectedVariantIDs": ["v99"]}), encoding="utf-8")
        other = root / "two" / "receipts"
        other.mkdir(parents=True)
        (other / "preview-selection-r1.json").write_text(json.dumps({"rejectedVariantIDs": ["v01"]}), encoding="utf-8")
        result = loop.harvest(root)
        self.assertTrue(any(row["kind"] == "selected" and row["text"] == "quiet hierarchy" for row in result["positive_preferences"]))
        self.assertTrue(any(row["kind"] == "starred" and row["text"] == "quiet hierarchy" for row in result["positive_preferences"]))
        self.assertEqual(result["rejections"], [])

    def test_scoped_visual_harvest_ignores_self_and_boilerplate(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        receipts = root / "real" / "receipts"
        receipts.mkdir(parents=True)
        (receipts / "visual-review.md").write_text("# Findings\nUI Visual Traceback: 0 [IMPL_BUG], 0 [SPEC_GAP]\n[IMPL_BUG] flat cards without hierarchy\n", encoding="utf-8")
        self_goal = root / "loop-design-taste-evolution" / "receipts"
        self_goal.mkdir(parents=True)
        (self_goal / "self-review.md").write_text("[IMPL_BUG] taste_evolution_loop.py self marker\n", encoding="utf-8")
        result = loop.harvest(root)
        self.assertEqual(len(result["visual_tracebacks"]), 1)
        self.assertNotIn("loop-design-taste-evolution", json.dumps(result))


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

    def test_invalid_or_incomplete_mock_fails_closed(self):
        candidate = {"kind": "visual_traceback", "text": "flat cards without hierarchy", "count": 2, "sources": []}
        with self.assertRaises(ValueError):
            loop.cluster({"positive_preferences": [], "rejections": [], "visual_tracebacks": [candidate]}, mock_jev_json="not-json")
        with self.assertRaises(ValueError):
            loop.cluster({"positive_preferences": [], "rejections": [], "visual_tracebacks": [candidate]}, mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_ANTI_PATTERN_GATE"}}}})

    def test_noul_truth_table_and_recurrence_gate(self):
        candidate = {"kind": "rejection", "variant_id": "v1", "text": "cramped density", "count": 2, "sources": []}
        harvested = {"positive_preferences": [], "rejections": [candidate], "visual_tracebacks": []}
        low = {"default": {"answers": {"promotion": {"choice": "PROMOTE_ANTI_PATTERN_GATE"}, "confidence": {"noul": 0.2}, "score": {"score": 0.9}}}}
        self.assertEqual(loop.cluster(harvested, mock_jev_json=low)["candidates"][0]["decision"]["choice"], "ONE_OFF_CONTEXT_NOTE")
        grey = {"default": {"answers": {"promotion": {"choice": "PROMOTE_ANTI_PATTERN_GATE"}, "confidence": {"noul": 0.5}, "score": {"score": 0.9}}}}
        self.assertEqual(loop.cluster(harvested, mock_jev_json=grey)["candidates"][0]["decision"]["source"], "deterministic_fallback_grey_zone")
        high = {"default": {"answers": {"promotion": {"choice": "PROMOTE_ANTI_PATTERN_GATE"}, "confidence": {"noul": 0.9}, "score": {"score": 0.9}}}}
        self.assertEqual(loop.cluster(harvested, mock_jev_json=high)["candidates"][0]["decision"]["choice"], "PROMOTE_ANTI_PATTERN_GATE")
        candidate["count"] = 1
        self.assertEqual(loop.cluster({"positive_preferences": [], "rejections": [candidate], "visual_tracebacks": []}, mock_jev_json=high)["candidates"][0]["decision"]["choice"], "ONE_OFF_CONTEXT_NOTE")

    def test_mock_entry_lookup_supports_one_based_indices_and_text(self):
        harvested = {"positive_preferences": [{"kind": "note", "variant_id": "v1", "text": "compact hierarchy", "count": 2, "sources": []}], "rejections": [], "visual_tracebacks": []}
        answer = {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.9}, "score": {"score": 0.8}}}
        self.assertEqual(len(loop.propose_gates(harvested, mock_jev_json=[answer])["positive_preferences"]), 1)
        self.assertEqual(len(loop.propose_gates(harvested, mock_jev_json={"compact hierarchy": answer})["positive_preferences"]), 1)

    def test_dual_zone_sanitizes_internal_receipt_noise(self):
        harvested = {"positive_preferences": [{"kind": "note", "variant_id": "v1", "text": "DC-015 fail in 4dc6942 <details>raw</details>", "count": 2, "sources": []}], "rejections": [], "visual_tracebacks": []}
        proposal = loop.propose_gates(harvested, mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.9}, "score": {"score": 0.8}}}})
        markdown = loop._issue_markdown(proposal, harvested)
        self.assertTrue(issue_brief.verify_dual_zone_issue(markdown)["ok"])
        self.assertNotIn("<details>raw", markdown)

    def test_loop_check_is_single_pass_for_jev(self):
        tmp, root = TasteHarvestTests().make_receipts()
        self.addCleanup(tmp.cleanup)
        calls = []
        def fake_jev(**kwargs):
            calls.append(kwargs)
            return {"answers": {"promotion": {"choice": "ONE_OFF_CONTEXT_NOTE"}, "confidence": {"noul": 0.9}, "score": {"score": 0.8}}}
        loop.loop_check(root, jev_fn=fake_jev)
        self.assertEqual(len(calls), 6)


class TasteCliAndRunnerTests(unittest.TestCase):
    def test_cli_commands_emit_json_and_issue_is_verified(self):
        tmp, root = TasteHarvestTests().make_receipts()
        self.addCleanup(tmp.cleanup)
        proc = subprocess.run(["python3", str(LOOP_PATH), "propose-gates", "--handoff-root", str(root)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gate_candidates", proc.stdout)
        r1 = json.loads((root / "goal" / "receipts" / "preview-selection-r1.json").read_text(encoding="utf-8"))
        (root / "goal" / "receipts" / "preview-selection-r2.json").write_text(json.dumps(r1), encoding="utf-8")
        report = loop.loop_check(root, mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.8}, "score": {"score": 0.8}}}})
        self.assertTrue(issue_brief.verify_dual_zone_issue(report["dual_zone_markdown"])["ok"])
        self.assertEqual(report["cursor"], f"design-taste-evolution-{report['proposal']['signature']}")

    def test_runner_registers_collector_and_no_delta_watermark_is_stable(self):
        tmp, root = TasteHarvestTests().make_receipts()
        self.addCleanup(tmp.cleanup)
        old = os.environ.get("DESIGN_TASTE_HANDOFF_ROOT")
        os.environ["DESIGN_TASTE_HANDOFF_ROOT"] = str(root)
        self.addCleanup(lambda: os.environ.__setitem__("DESIGN_TASTE_HANDOFF_ROOT", old) if old is not None else os.environ.pop("DESIGN_TASTE_HANDOFF_ROOT", None))
        spec = runner.resolve_loop_spec("design-taste-evolution")
        collected = runner._default_loop_collector(spec, target_date=__import__("datetime").date(2026, 11, 19), mock_jev_json={"default": {"answers": {"promotion": {"choice": "PROMOTE_POSITIVE_ARCHETYPE"}, "confidence": {"noul": 0.8}, "score": {"score": 0.8}}}})
        collected["has_delta"] = True
        collected["delta_count"] = 1
        collected["severity"] = "medium"
        self.assertEqual(collected["loop_id"], "design-taste-evolution")
        self.assertIn("watermark", collected)
        with tempfile.TemporaryDirectory() as state:
            state_file = Path(state) / "watermarks.json"
            first = runner.run_loop("design-taste-evolution", state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json={"answers": {"actionable_probability": {"noul": 0.1}, "delivery_route": {"choice": "ignore"}}})
            self.assertEqual(first["status"], "SKIPPED_NOT_ACTIONABLE")
            second = runner.run_loop("design-taste-evolution", state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json={"answers": {"actionable_probability": {"noul": 0.1}, "delivery_route": {"choice": "ignore"}}})
            self.assertEqual(second["status"], "SKIPPED_NO_DELTA")

    def test_runner_action_creates_cursor_matched_dual_zone_issue_then_skips(self):
        spec = runner.resolve_loop_spec("design-taste-evolution")
        cursor = "design-taste-evolution-testcursor"
        markdown = issue_brief.render_dual_zone_issue(tldr="发现可供 Owner 审阅的新证据", items=[{"what": "审阅稳定的界面偏好", "why": "确认是否进入后续设计门禁"}], cursor=cursor, ai_payload={"loop_id": spec.loop_id, "summary": "compact"})
        collected = {"loop_id": spec.loop_id, "date": "2026-11-19", "has_delta": True, "delta_count": 1, "severity": "medium", "watermark": "design-taste-evolution:watermark", "cursor": cursor, "title": "[P3 / Priority 3][Taste Evolution] 2026-11-19 UI 品味偏好与反模式候选", "summary": "发现可供 Owner 审阅的新证据", "items": [{"what": "审阅稳定的界面偏好", "why": "确认是否进入后续设计门禁"}], "dual_zone_markdown": markdown}
        calls = []
        def fake_runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[:2] == ["issue", "list"]:
                payload = {"issues": []}
            elif argv[:2] == ["issue", "create"]:
                payload = {"id": "issue-1", "description": kwargs.get("stdin_text", "")}
            else:
                payload = {}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        with tempfile.TemporaryDirectory() as state:
            state_file = Path(state) / "watermarks.json"
            mock = {"answers": {"actionable_probability": {"noul": 0.9}, "delivery_route": {"choice": "dual_zone_issue"}, "delivery_route_match": {"noul": 0.9}, "urgency_score": {"score": 0.8}}}
            first = runner.run_loop(spec.loop_id, state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json=mock, runner=fake_runner)
            self.assertEqual(first["status"], "ACTION_TAKEN")
            self.assertTrue(any(cursor in str(kwargs.get("stdin_text", "")) for _, kwargs in calls))
            second = runner.run_loop(spec.loop_id, state_path=state_file, collector_fn=lambda *a, **k: collected, mock_jev_json=mock, runner=fake_runner)
            self.assertEqual(second["status"], "SKIPPED_NO_DELTA")

    def test_autopilot_description_directs_mac_execution(self):
        spec = runner.resolve_loop_spec("design-taste-evolution")
        self.assertIn("Mac node", runner.build_loop_autopilot_description(spec))
        self.assertIn("NAS filesystem as the evidence source", runner.build_loop_autopilot_description(spec))


if __name__ == "__main__":
    unittest.main()
