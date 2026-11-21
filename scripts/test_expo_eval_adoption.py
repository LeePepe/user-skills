#!/usr/bin/env python3
"""Unit tests for `expo/eval-harness@da0eba3` adoption in `skill-retro` & `skill-contract`."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_RETRO_PY = REPO_ROOT / "skills" / "skill-retro" / "scripts" / "skill_retro.py"
SKILL_CONTRACT_PY = REPO_ROOT / "skills" / "skill-contract" / "scripts" / "skill_contract.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


skill_retro = _load_module("skill_retro_expo_test", SKILL_RETRO_PY)
skill_contract = _load_module("skill_contract_expo_test", SKILL_CONTRACT_PY)


class ExpoEvalAdoptionTests(unittest.TestCase):
    def test_decoupled_uptake_registry_loads_and_maps_skills(self) -> None:
        skill_map, checks_by_id = skill_retro.load_uptake_registry()
        self.assertIn("skill-retro", skill_map)
        self.assertIn("skill-contract", skill_map)
        self.assertIn("ios-architecture-testing", skill_map)
        self.assertIn("my-designer", skill_map)
        for check_ids in skill_map.values():
            for cid in check_ids:
                self.assertIn(cid, checks_by_id, f"skill_map references unknown check_id {cid}")

    def test_negative_check_engagement_gating_prevents_vacuous_pass(self) -> None:
        """Negative check returns not_applicable when domain is untouched, failed when violated, passed when engaged cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1) Empty directory (no Swift files engaged) -> not_applicable (not counted toward uptake_rate)
            res_empty = skill_retro.evaluate_skill_uptake(
                skills=["ios-architecture-testing"],
                triggered_skills=["ios-architecture-testing"],
                target_dir=root,
            )
            ios_empty = res_empty["skills"]["ios-architecture-testing"]
            self.assertEqual(ios_empty["uptake_status"], "not_applicable")
            self.assertIsNone(ios_empty["uptake_rate"])
            self.assertEqual(ios_empty["total"], 0)

            # 2) Violating Swift file using deprecated NavigationView and try! ModelContainer
            (root / "ContentView.swift").write_text(
                'import SwiftUI\nstruct ContentView: View {\n  var body: some View {\n    NavigationView { Text("Hi") }\n  }\n}\n'
                "let c = try! ModelContainer(for: Item.self)\n",
                encoding="utf-8",
            )
            res_bad = skill_retro.evaluate_skill_uptake(
                skills=["ios-architecture-testing"],
                triggered_skills=["ios-architecture-testing"],
                target_dir=root,
            )
            ios_bad = res_bad["skills"]["ios-architecture-testing"]
            self.assertEqual(ios_bad["uptake_status"], "measured")
            self.assertFalse(res_bad["ok"])
            self.assertGreaterEqual(ios_bad["hard_failures"], 2)

            # 3) Clean Swift file engaging NavigationStack, @Relationship(deleteRule:), and accessibilityIdentifier
            (root / "ContentView.swift").write_text(
                "import SwiftUI\nimport SwiftData\n"
                "@Model final class Folder {\n  @Relationship(deleteRule: .cascade) var items: [String] = []\n}\n"
                "struct ContentView: View {\n  var body: some View {\n"
                '    NavigationStack { Text("Notes").accessibilityIdentifier("notes-title") }\n'
                "  }\n}\n",
                encoding="utf-8",
            )
            res_good = skill_retro.evaluate_skill_uptake(
                skills=["ios-architecture-testing"],
                triggered_skills=["ios-architecture-testing"],
                target_dir=root,
            )
            ios_good = res_good["skills"]["ios-architecture-testing"]
            self.assertTrue(res_good["ok"])
            self.assertEqual(ios_good["uptake_status"], "measured")
            self.assertEqual(ios_good["uptake_rate"], 1.0)
            self.assertEqual(ios_good["hard_failures"], 0)

    def test_three_scenario_triad_and_trigger_gating(self) -> None:
        # Scenario 1: skills_unavailable -> baseline control (expected_skills = [])
        res_unavail = skill_retro.evaluate_skill_uptake(
            skills=["skill-retro"],
            triggered_skills=[],
            target_dir=REPO_ROOT,
            scenario="skills_unavailable",
        )
        self.assertEqual(res_unavail["expected_skills"], [])
        self.assertEqual(
            res_unavail["score"]["context_uptake"]["skipped_reason"],
            "skills_unavailable baseline scenario",
        )

        # Scenario 2: skills_available_unmentioned with missed trigger -> uptake gated
        res_missed = skill_retro.evaluate_skill_uptake(
            skills=["skill-retro"],
            triggered_skills=["other-skill"],
            target_dir=REPO_ROOT,
            scenario="skills_available_unmentioned",
        )
        self.assertEqual(res_missed["score"]["trigger_quality"]["recall"], 0.0)
        self.assertEqual(res_missed["score"]["trigger_quality"]["precision"], 0.0)
        self.assertIsNone(res_missed["score"]["context_uptake"]["uptake_rate"])
        self.assertEqual(
            res_missed["score"]["context_uptake"]["skipped_reason"],
            "relevant skill did not trigger",
        )

        # Scenario 3: skills_available_mentioned with full trigger + outcome delta
        res_mentioned = skill_retro.evaluate_skill_uptake(
            skills=["skill-retro"],
            triggered_skills=["skill-retro"],
            target_dir=REPO_ROOT,
            scenario="skills_available_mentioned",
            baseline_evaluator_pct=55.0,
            skill_evaluator_pct=90.0,
            build_success=True,
        )
        self.assertEqual(res_mentioned["score"]["trigger_quality"]["recall"], 1.0)
        self.assertEqual(res_mentioned["score"]["context_uptake"]["uptake_rate"], 1.0)
        self.assertEqual(res_mentioned["score"]["outcome_delta"]["outcome_delta"], 35.0)

    def test_one_verify_one_assertion_and_hard_soft_scoring_discipline(self) -> None:
        # 1) Valid 1:1 case with 1 hard pass + 1 non-fatal soft fail -> partial credit (3 * 1 / 2 = 2 points)
        partial_case = {
            "id": "EV-101",
            "points": 3,
            "verify_lines": [
                "Creation surface is visible",
                "(non-fatal) Row displays timestamp preview",
            ],
            "assertions": [
                {"severity": "hard", "fatal": True, "passed": True, "check": "assert_visible id='create-sheet'"},
                {
                    "severity": "soft",
                    "fatal": False,
                    "passed": False,
                    "check": "timestamp preview check",
                    "evidence": "@e6 [text] 'Notes' (no timestamp visible)",
                    "why_soft": "requires reading element text from accessibility tree",
                },
            ],
        }
        scored_partial = skill_contract.score_case_assertions(partial_case)
        self.assertEqual(scored_partial["discipline_violations"], [])
        self.assertFalse(scored_partial["fatal_failed"])
        self.assertEqual(scored_partial["earned_points"], 2)

        # 2) Fatal hard failure zeroes the entire step score even when non-fatal soft passes
        fatal_fail_case = {
            "id": "EV-102",
            "points": 3,
            "verify_lines": [
                "Creation surface is visible",
                "(non-fatal) Row displays timestamp preview",
            ],
            "assertions": [
                {"severity": "hard", "fatal": True, "passed": False, "check": "assert_visible id='create-sheet'"},
                {
                    "severity": "soft",
                    "fatal": False,
                    "passed": True,
                    "check": "timestamp preview check",
                    "evidence": "@e6 [text] '10:00 AM'",
                    "why_soft": "requires reading element text from accessibility tree",
                },
            ],
        }
        scored_fatal = skill_contract.score_case_assertions(fatal_fail_case)
        self.assertTrue(scored_fatal["fatal_failed"])
        self.assertEqual(scored_fatal["earned_points"], 0)
        self.assertFalse(scored_fatal["passed"])

        # 3) Counter-Case: 1-Verify-1-Assertion inflation violation (2 Verify lines, 3 assertions)
        inflated_case = {
            "id": "EV-103",
            "points": 2,
            "verify_lines": [
                "Creation surface is visible",
                "Input field is ready",
            ],
            "assertions": [
                {"severity": "hard", "fatal": True, "passed": True, "check": "a1"},
                {"severity": "hard", "fatal": True, "passed": True, "check": "a2"},
                {"severity": "hard", "fatal": True, "passed": True, "check": "a3"},
            ],
        }
        scored_inflated = skill_contract.score_case_assertions(inflated_case)
        self.assertTrue(scored_inflated["fatal_failed"])
        self.assertEqual(scored_inflated["earned_points"], 0)
        self.assertTrue(
            any("assertion-count-mismatch" in v for v in scored_inflated["discipline_violations"])
        )

        # 4) Counter-Case: Soft assertion without evidence / why_soft fails discipline check
        unjustified_soft = {
            "id": "EV-104",
            "verify_lines": ["Title matches expected string"],
            "assertions": [
                {"severity": "soft", "fatal": True, "passed": True, "check": "title check", "evidence": ""},
            ],
        }
        scored_unjustified = skill_contract.score_case_assertions(unjustified_soft)
        self.assertTrue(
            any("soft-assertion-missing-evidence" in v for v in scored_unjustified["discipline_violations"])
        )

    def test_cli_uptake_check_and_uptake_eval_subcommands(self) -> None:
        proc1 = subprocess.run(
            [sys.executable, str(SKILL_RETRO_PY), "uptake-check", "--skill", "skill-retro", "--strict", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        data1 = json.loads(proc1.stdout)
        self.assertTrue(data1["ok"])
        self.assertEqual(data1["skills"]["skill-retro"]["uptake_rate"], 1.0)

        proc2 = subprocess.run(
            [
                sys.executable,
                str(SKILL_CONTRACT_PY),
                "uptake-eval",
                "--skill",
                "skill-contract",
                "--baseline-pct",
                "50",
                "--skill-pct",
                "95",
                "--strict",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        data2 = json.loads(proc2.stdout)
        self.assertTrue(data2["ok"])
        self.assertEqual(data2["score"]["outcome_delta"]["outcome_delta"], 45.0)


if __name__ == "__main__":
    unittest.main()
