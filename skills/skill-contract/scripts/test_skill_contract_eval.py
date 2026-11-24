#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("skill_contract.py")
spec = importlib.util.spec_from_file_location("skill_contract_eval_under_test", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class SkillContractEvalBudgetTests(unittest.TestCase):
    def test_timeout_mutation_can_be_an_expected_negative(self) -> None:
        case = {
            "id": "TEST-timeout",
            "negative_cmd": [sys.executable, "-c", "import time; time.sleep(0.2)"],
            "expect_fail_exit": 124,
            "expect_timeout": True,
            "timeout_sec": 0.05,
            "max_duration_ms": 1000,
        }
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(module.evaluate_case(case, Path(tmp), Path(tmp)), [])

    def test_stdout_budget_catches_output_mutation(self) -> None:
        case = {
            "id": "TEST-output-budget",
            "cmd": [sys.executable, "-c", "print('x' * 2048)"],
            "expect_exit": 0,
            "max_stdout_bytes": 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            reasons = module.evaluate_case(case, Path(tmp), Path(tmp))
        self.assertTrue(any("stdout" in reason and "max_stdout_bytes" in reason for reason in reasons))

    def test_description_eval_reports_heldout_metrics(self) -> None:
        repo = SCRIPT.parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            heldout = Path(tmp) / "heldout.json"
            heldout.write_text(json.dumps([{"query": "audit a skill contract drawer", "should_trigger": True}]), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "description-eval",
                    "--skill",
                    "skill-contract",
                    "--repo",
                    str(repo),
                    "--heldout",
                    str(heldout),
                    "--mock-jev-json",
                    "[true,false,true,true]",
                    "--strict",
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["heldout_total"], 1)
            self.assertEqual(payload["corpus_metrics"]["heldout"]["recall"], 1.0)

    def test_inventory_is_symlink_aware(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills" / "alpha" / "references").mkdir(parents=True)
            (root / "skills" / "beta" / "references").mkdir(parents=True)
            for name in ("alpha", "beta"):
                (root / "skills" / name / "SKILL.md").write_text("---\nname: %s\ndescription: test\n---\n" % name, encoding="utf-8")
            drawer = root / "skills" / "alpha" / "references" / "eval-cases.json"
            drawer.write_text('{"active_cases": [], "trigger_evals": []}\n', encoding="utf-8")
            (root / "skills" / "beta" / "references" / "eval-cases.json").symlink_to(drawer)
            args = type("Args", (), {"repo": str(root)})()
            original = module._discover_packages
            try:
                # inventory_cmd is exercised through its JSON output to avoid coupling to print formatting.
                module._discover_packages = lambda repo=None: [root / "skills" / "alpha", root / "skills" / "beta"]
                from contextlib import redirect_stdout
                from io import StringIO
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(module.inventory_cmd(args), 0)
                payload = __import__("json").loads(output.getvalue())
                self.assertEqual(payload["canonical_totals"]["drawers"], 1)
                self.assertEqual(payload["duplicate_drawers"][0]["skills"], ["alpha", "beta"])
            finally:
                module._discover_packages = original


if __name__ == "__main__":
    unittest.main()
