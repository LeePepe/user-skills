#!/usr/bin/env python3
"""Unit tests for skill-contract description-eval."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "skills" / "skill-contract" / "scripts" / "skill_contract.py"


def load_contract():
    spec = importlib.util.spec_from_file_location("skill_contract", CONTRACT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {CONTRACT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = load_contract()


class DescriptionEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "skills").mkdir()
        self.original_root = CONTRACT.ROOT
        CONTRACT.ROOT = self.root

    def tearDown(self) -> None:
        CONTRACT.ROOT = self.original_root
        self.tmp.cleanup()

    def _write_skill(self, name: str, description: str) -> Path:
        package = self.root / "skills" / name
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n"
            f"# {name}\n\nKeep deterministic checks in scripts.\n",
            encoding="utf-8",
        )
        return package

    def _write_evals(self, package: Path, evals: list[dict], threshold: float = 0.2) -> Path:
        path = package / "evals" / "description-eval.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"threshold": threshold, "evals": evals}, indent=2) + "\n", encoding="utf-8")
        return path

    def test_dry_run_does_not_write(self) -> None:
        package = self._write_skill("demo", "Audit progressive disclosure budgets.")
        original = (package / "SKILL.md").read_text(encoding="utf-8")
        self._write_evals(package, [
            {"id": "t1", "prompt": "description-eval trigger routing", "should_trigger": True},
            {"id": "n1", "prompt": "deploy kubernetes cluster", "should_trigger": False},
        ])
        code, result = CONTRACT.run_description_eval(skill="demo")
        self.assertEqual(code, 1)
        self.assertFalse(result["applied"])
        self.assertTrue(result["improved"])
        self.assertEqual((package / "SKILL.md").read_text(encoding="utf-8"), original)

    def test_apply_writes_when_score_improves_and_audit_passes(self) -> None:
        package = self._write_skill("demo", "Audit progressive disclosure budgets.")
        self._write_evals(package, [
            {"id": "t1", "prompt": "description-eval trigger routing", "should_trigger": True},
            {"id": "n1", "prompt": "deploy kubernetes cluster", "should_trigger": False},
        ])
        code, result = CONTRACT.run_description_eval(skill="demo", apply=True)
        self.assertTrue(result["improved"])
        self.assertTrue(result["applied"])
        self.assertFalse(result["rolled_back"])
        self.assertTrue(result["audit"]["ok"])
        written = (package / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("description-eval", written)
        self.assertGreaterEqual(result["proposed"]["passed"], result["baseline"]["passed"])
        self.assertIn(code, {0, 1})

    def test_apply_rolls_back_when_audit_strict_fails(self) -> None:
        package = self._write_skill("demo", "Audit progressive disclosure budgets.")
        original = (package / "SKILL.md").read_text(encoding="utf-8")
        self._write_evals(package, [
            {"id": "t1", "prompt": "progressRail dashboard", "should_trigger": True},
            {"id": "n1", "prompt": "deploy kubernetes cluster", "should_trigger": False},
        ])
        code, result = CONTRACT.run_description_eval(
            skill="demo",
            apply=True,
            candidate="Use when the user mentions progressRail budgets.",
        )
        self.assertEqual(code, 1)
        self.assertTrue(result["rolled_back"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["audit"]["ok"])
        self.assertEqual((package / "SKILL.md").read_text(encoding="utf-8"), original)

    def test_missing_eval_file_is_opt_in_error(self) -> None:
        self._write_skill("demo", "Audit progressive disclosure budgets.")
        code, result = CONTRACT.run_description_eval(skill="demo")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], "missing-eval-file")

    def test_audit_does_not_require_description_eval(self) -> None:
        self._write_skill("demo", "Audit progressive disclosure budgets.")
        findings = CONTRACT.audit_package(self.root / "skills" / "demo")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
