#!/usr/bin/env python3
"""Focused tests for the SW5 retro, story, and otter interfaces."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GTT = load("skills/team-lead/scripts/gtt-telemetry-export.py")
ENGINE = load("skills/retro-loop/scripts/retro_story_engine.py")
DRIFT = load("skills/workflow-hub/scripts/skills-drift-check.py")


class Sw5Tests(unittest.TestCase):
    def test_evidence_ids_and_manifest(self):
        records = [
            {"record_type": "ledger", "observed_at": "2026-01-01T00:01:00+08:00"},
            {"record_type": "goal", "observed_at": "2026-01-01T00:02:00+08:00"},
        ]
        GTT.assign_evidence_ids(records)
        self.assertEqual(["H00-F1", "H00-S1"], [record["evidence_id"] for record in records])
        manifest = GTT.build_daily_evidence_manifest(records, "2026-01-01")
        self.assertEqual(24, len(manifest["buckets"]))

    def test_state_machine_and_stale_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            ENGINE.init_state(path, "r1", "g1", "sha1")
            ENGINE.transition_state(path, "brief_review", 1)
            ENGINE.transition_state(path, "scripting", 2)
            with self.assertRaises(ENGINE.OptimisticLockError) as error:
                ENGINE.transition_state(path, "script_review", 2)
            self.assertEqual(409, error.exception.code)
            state = ENGINE.transition_state(path, "scripting", 3, evidence_sha="sha2")
            self.assertTrue(state["stale_assets"])

    def test_prompt_composition_and_observe_route(self):
        prompt = ENGINE.compose_prompt(
            "sharing_deck",
            {"pivot": "p", "takeaway_mechanism": "m"},
            {"provenance": "g1", "quantitative_delta": "1", "evidence_ids": ["H00-F1"]},
        )
        self.assertIn("Remove secrets", prompt)
        with tempfile.TemporaryDirectory() as directory:
            proposals = Path(directory) / "proposals.jsonl"
            result = ENGINE.route_retro_candidate({"id": "c1", "title": "T"}, proposals_path=proposals)
            self.assertFalse(result["promoted"])
            self.assertEqual("pending", json.loads(proposals.read_text())["status"])

    def test_drift_clean_and_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "repo" / "skills" / "demo"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
            target = root / "target"
            target.mkdir()
            (target / "demo").symlink_to(source, target_is_directory=True)
            result = DRIFT.inspect_skills_topology([root / "repo"], [target])
            self.assertEqual([], result["issues"])


if __name__ == "__main__":
    unittest.main()
