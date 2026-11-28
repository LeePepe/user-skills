from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "skills/skill-retro/scripts/skill_retro.py"


def load_facade():
    spec = importlib.util.spec_from_file_location("test_skill_retro_modular_facade", FACADE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SkillRetroModularTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_facade()

    def test_facade_and_core_budgets_and_runtime_symlink(self):
        self.assertLessEqual(len(FACADE_PATH.read_text(encoding="utf-8").splitlines()), 280)
        core_dir = FACADE_PATH.parent / "skill_retro_core"
        forbidden = ("base64.b64decode", "zlib.decompress", "exec(compile")
        for path in core_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 980, path)
            self.assertFalse(any(token in source for token in forbidden), path)
        runtime = FACADE_PATH.parent / "skill_runtime"
        self.assertTrue(runtime.is_symlink())
        self.assertEqual(runtime.readlink().as_posix(), "../../skill-contract/scripts/skill_runtime")
        self.assertIs(self.mod.SF_DATALESS, self.mod.RUNTIME_SF_DATALESS)
        self.assertTrue(hasattr(self.mod, "redact_payload"))
        self.assertTrue(hasattr(self.mod, "read_copilot_sessions"))

    def test_mock_patch_constants_and_helpers_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "patched-skill" / ".claude-plugin").mkdir(parents=True)
            (root / "patched-skill" / "SKILL.md").write_text("---\nname: patched-skill\ndescription: x\n---\n", encoding="utf-8")
            with mock.patch.object(self.mod, "DEFAULT_SKILLS_ROOT", root), mock.patch.object(
                self.mod, "_redact_user_text", wraps=self.mod._redact_user_text
            ) as redact:
                inventory = self.mod._discover_skill_inventory(root, root)
            self.assertIn("patched-skill", inventory)
            self.assertTrue(redact.called)

    def test_diagnose_defaults_to_bounded_recent_scan(self):
        command = [sys.executable, str(FACADE_PATH), "diagnose", "--skill", "my-designer", "--limit-sessions", "60", "--json"]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=3.0, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertLessEqual(report["sources_ingested"]["sessions_observed"], 60)
        self.assertEqual(report["skill_filter"], "my-designer")
        self.assertIn("--deep-all", subprocess.run([sys.executable, str(FACADE_PATH), "diagnose", "--help"], capture_output=True, text=True, check=True).stdout)

    def test_emit_briefs_include_evidence_and_execution_guardrails(self):
        report = {
            "proposals": [{"skill": "my-designer", "id": "proposal-1"}],
            "inventory_snapshot": {
                "my-designer": {
                    "script_entries": ["scripts/check.py", "scripts/data/defect-cases/index.json"],
                    "internal_doc_cli_drift": ["SKILL.md documents --strict but argparse exposes --safe"],
                    "internal_broken_refs": ["references/missing.md"],
                }
            },
            "layer0_direction_and_triggers": {},
            "layer1_drawer_metabolism": {},
            "layer2_script_efficacy_and_speed": {
                "rigid_roadblocks": [{
                    "skill": "my-designer",
                    "script": "scripts/check.py",
                    "exit_2_count": 1,
                    "exit_2_sessions": 1,
                    "arg_retry_sessions": 1,
                    "failing_command_samples": ["python3 skills/my-designer/scripts/check.py --bad-flag"],
                }],
            },
            "layer3_wild_patterns_and_eval": {},
            "observation_pool": [
                {"skill": "my-designer", "exit_code": 1, "command": "python3 skills/my-designer/scripts/check.py"},
                {"skill": "my-designer", "exit_code": 0, "command": "python3 skills/my-designer/scripts/check.py", "aci_output_bloat": True, "output_chars": 22000},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            dev_root = Path(tmp) / "dev"
            (dev_root / "user-skills").mkdir(parents=True)
            result = self.mod.emit_skill_retro_briefs(report, Path(tmp), skill_filter="my-designer", dev_root=dev_root)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["briefs"][0]["dispatch_cwd"], str(dev_root / "user-skills"))
            self.assertIn(f"--cwd {dev_root / 'user-skills'}", result["briefs"][0]["herdr_dispatch_cmd"])
            self.assertEqual(result["m2_dispatch_plan"]["parallel_m1_dispatches"][0]["dispatch_cwd"], str(dev_root / "user-skills"))
            spec = Path(result["m1_goal_specs"][0]["spec_md"]).read_text(encoding="utf-8")
            brief = Path(result["briefs"][0]["writer_brief"]).read_text(encoding="utf-8")
            combined = spec + brief
            for needle in ("exit=2", "exit=1", ">20KB bloat", "SKILL.md", "argparse", "skills/my-designer/scripts/check.py", "Execution Guardrail", "MUST NOT read `skills/skill-retro/scripts/**`", "herdr-dispatch.sh --role writer", "absolute paths"):
                self.assertIn(needle, combined)


if __name__ == "__main__":
    unittest.main()
