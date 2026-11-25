#!/usr/bin/env python3
"""Regression tests for the retro-loop skill evolution contract."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "retro-loop" / "scripts"
ENGINE_PATH = SCRIPT_DIR / "retro_story_engine.py"
EXPORTER_PATH = SCRIPT_DIR / "trace_backfill_exporter.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetroLoopContractTests(unittest.TestCase):
    def test_trace_modules_and_shared_runtime_fit_budgets(self) -> None:
        self.assertLessEqual(len(EXPORTER_PATH.read_text().splitlines()), 220)
        core_files = sorted((SCRIPT_DIR / "trace_backfill_core").glob("*.py"))
        self.assertTrue(core_files)
        for path in core_files:
            self.assertLessEqual(len(path.read_text().splitlines()), 750, path.name)
        runtime = SCRIPT_DIR / "skill_runtime"
        self.assertTrue(runtime.is_symlink())
        self.assertEqual(runtime.readlink(), Path("../../skill-contract/scripts/skill_runtime"))

    def test_patch_object_on_facade_reaches_core_cli(self) -> None:
        exporter = _load(EXPORTER_PATH, "trace_backfill_exporter_contract_test")
        replacement = ([], [])
        with mock.patch.object(exporter, "collect_historical_traces", return_value=replacement) as patched:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = exporter.main(["--target", "both", "--limit-goals", "0", "--limit-sessions", "0", "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(patched.called)
        self.assertEqual(json.loads(output.getvalue())["summary"]["total_traces"], 0)

    def test_live_cli_flags_and_self_disclosing_unknown_flags(self) -> None:
        commands = [
            ["discover", "--days", "7", "--no-auto-propose"],
            ["agent-efficiency", "--days", "7", "--no-auto-propose"],
            ["skill-roi", "--days", "7", "--no-auto-propose", "--skills-root", "/tmp/retro-loop-no-skills", "--dev-root", "/tmp/retro-loop-no-dev", "--dsh-sessions", "/tmp/retro-loop-no-sessions", "--no-icloud-mirror"],
            ["discover", "--window-days", "7", "--summary"],
            ["agent-efficiency", "--window-days", "7", "--summary"],
            ["skill-roi", "--window-days", "7", "--summary", "--skills-root", "/tmp/retro-loop-no-skills", "--dev-root", "/tmp/retro-loop-no-dev", "--dsh-sessions", "/tmp/retro-loop-no-sessions", "--no-icloud-mirror"],
        ]
        for args in commands:
            proc = subprocess.run([sys.executable, str(ENGINE_PATH), *args], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, (args, proc.stderr))
        for path, args in ((ENGINE_PATH, ["discover", "--unknown-flag"]), (EXPORTER_PATH, ["--unknown-flag"])):
            proc = subprocess.run([sys.executable, str(path), *args], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertLessEqual(len(proc.stderr.splitlines()), 3)
            self.assertTrue(proc.stderr.rstrip().endswith("Reference: skills/retro-loop/SKILL.md"))

    def test_eval_drawer_and_plugin_contract(self) -> None:
        drawer = json.loads((ROOT / "skills/retro-loop/references/eval-cases.json").read_text())
        cases = drawer["active_cases"]
        self.assertGreaterEqual(len(cases), 8)
        self.assertLessEqual(len(cases), 12)
        counter_cases = [case for case in cases if case.get("expect_exit", 0) != 0 or case.get("should_trigger") is False or case.get("forbidden_regex") or case.get("negative_cmd")]
        self.assertGreaterEqual(len(counter_cases) / len(cases), 0.33)
        plugin = json.loads((ROOT / "skills/retro-loop/.claude-plugin/plugin.json").read_text())
        self.assertEqual(plugin["version"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
