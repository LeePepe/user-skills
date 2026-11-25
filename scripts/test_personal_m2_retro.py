"""Focused regression locks for the personal-m2-assistant retro."""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "skills/personal-m2-assistant/scripts/m2-router.py"
CORE_DIR = ROUTER_PATH.parent / "m2_router_core"


def load_router():
    spec = importlib.util.spec_from_file_location("personal_m2_router_retro", ROUTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PersonalM2RetroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = load_router()

    def test_progressive_disclosure_bounds_and_shared_runtime_link(self):
        self.assertLessEqual(len(ROUTER_PATH.read_text(encoding="utf-8").splitlines()), 250)
        modules = sorted(CORE_DIR.glob("*.py"))
        self.assertTrue(modules)
        self.assertFalse(list(CORE_DIR.glob("part_*.py")))
        self.assertNotRegex(ROUTER_PATH.read_text(encoding="utf-8"), r"\b(?:exec|compile)\s*\(")
        self.assertNotRegex((CORE_DIR / "__init__.py").read_text(encoding="utf-8"), r"\b(?:exec|compile)\s*\(")
        expected = {
            "constants_and_paths", "domain_map", "jev_and_intent", "herdr_discovery",
            "linear_sync", "plan_builder", "dispatch_apply", "permission_guard",
            "watchdog", "cli",
        }
        imported = {module.__name__.rsplit(".", 1)[-1] for module in self.router._CORE_MODULES}
        self.assertTrue(expected.issubset(imported))
        for module in self.router._CORE_MODULES:
            self.assertIs(importlib.import_module(module.__name__), module)
            self.assertLessEqual(len(Path(module.__file__).read_text(encoding="utf-8").splitlines()), 950, module)
        runtime = ROUTER_PATH.parent / "skill_runtime"
        self.assertTrue(runtime.is_symlink())
        self.assertEqual(runtime.readlink().as_posix(), "../../skill-contract/scripts/skill_runtime")
        self.assertTrue((runtime / "__init__.py").exists())

    def test_phase0_discussion_is_raw_and_never_prelocked(self):
        message = "先讨论一下通知开关有哪些方案，不改代码"
        self.assertTrue(self.router.is_exploratory_discussion_message(message))
        plan = self.router.build_plan(message, [], mock_jev={}, transport="stdout")
        self.assertTrue(plan["phase0_discussion_only"])
        self.assertTrue(plan["targets"])
        for target in plan["targets"]:
            self.assertTrue(target["phase0_discussion_only"])
            self.assertEqual(target["sub_prompt"], message)
            self.assertEqual(target["m2_action"], "PHASE0_DISCUSSION_ONLY")
            self.assertNotIn("Hot-Append", json.dumps(target, ensure_ascii=False))

    def test_non_discussion_counter_case_does_not_trigger_phase0(self):
        message = "请直接修改代码并运行测试"
        self.assertFalse(self.router.is_exploratory_discussion_message(message))
        plan = self.router.build_plan(message, [], mock_jev={}, transport="stdout")
        self.assertFalse(plan["phase0_discussion_only"])
        self.assertFalse(any(t.get("phase0_discussion_only") for t in plan["targets"]))

    def test_cli_errors_disclose_fix_and_reference(self):
        result = subprocess.run(
            [sys.executable, str(ROUTER_PATH), "--message", "请直接修改代码"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Fix:", result.stderr)
        self.assertIn("Reference:", result.stderr)


if __name__ == "__main__":
    unittest.main()
