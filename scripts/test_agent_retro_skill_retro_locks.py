"""Replay locks for skill-retro-triggered agent-retro CLI compatibility changes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "agent-retro" / "scripts" / "agent_retro.py"
FIXTURE = ROOT / "scripts" / "fixtures" / "agent_retro"


class AgentRetroSkillRetroLocksTests(unittest.TestCase):
    def run_cli(self, args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(SCRIPT), *args]
        return subprocess.run(cmd, cwd=str(cwd or ROOT), text=True, capture_output=True, env=env)

    def fixture_env(self, handoff_root: Path | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["AGENT_RETRO_SESSIONS"] = str(FIXTURE / "sessions")
        env["AGENT_RETRO_HANDOFF_ROOT"] = str(handoff_root or (FIXTURE / "handoff"))
        return env

    def test_diagnose_accepts_positional_goal_id_and_worktree_fallback(self):
        env = self.fixture_env()
        positional = self.run_cli(["diagnose", "agent-retro", "--mode", "full", "--aspects", "l0,l1", "--json"], env=env)
        self.assertEqual(positional.returncode, 0, positional.stderr)
        self.assertEqual(json.loads(positional.stdout)["skill"], "agent-retro")

        by_goal = self.run_cli(["diagnose", "--goal-id", "agent-retro-my-designer", "--json"], env=env)
        self.assertEqual(by_goal.returncode, 0, by_goal.stderr)
        self.assertEqual(json.loads(by_goal.stdout)["skill"], "my-designer")

        by_cwd = self.run_cli(["diagnose", "--json"], cwd=ROOT, env=env)
        self.assertEqual(by_cwd.returncode, 0, by_cwd.stderr)
        self.assertEqual(json.loads(by_cwd.stdout)["skill"], "agent-retro")

    def test_diagnose_missing_skill_fails_closed_with_three_line_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_cli(["diagnose", "--json"], cwd=Path(tmp), env=self.fixture_env())
        self.assertEqual(proc.returncode, 2)
        self.assertIn("agent-retro-playbook.md", proc.stderr)
        self.assertIn("eval-cases.json", proc.stderr)
        self.assertLessEqual(len(proc.stderr.splitlines()), 3)

    def test_record_receipt_compatibility_flags_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "handoff"
            summary_file = Path(tmp) / "summary.txt"
            summary_file.write_text("compat replay summary\n", encoding="utf-8")
            proc = self.run_cli(
                [
                    "record-m1-receipt",
                    "--skill", "agent-retro",
                    "--goal-id", "agent-retro-agent-retro",
                    "--status", "HEALTHY_NO_ISSUES",
                    "--head-sha", "deadbeef",
                    "--pr-number", "123",
                    "--writers-spawned", "0",
                    "--reviewers-spawned", "0",
                    "--m1-tl-agent-name", "skill-retro-agent-retro-goal-tl-r1",
                    "--summary-file", str(summary_file),
                    "--mode", "full",
                    "--aspects", "l0,l1,l2,l3,creative,eval",
                    "--dir", str(handoff),
                    "--json",
                ],
                env=self.fixture_env(handoff),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            receipt = Path(payload["path"])
            text = receipt.read_text(encoding="utf-8")
            self.assertIn("Merge SHA: `deadbeef`", text)
            self.assertIn("Writers Spawned: `0`", text)
            self.assertIn("compat replay summary", text)

    def test_inspect_json_soft_and_strict_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "good.json"
            bad = root / "bad.json"
            good.write_text(json.dumps({"a": {"b": [1, 2, 3]}, "rows": [1, 2]}), encoding="utf-8")
            bad.write_text("{bad", encoding="utf-8")

            soft = self.run_cli(["inspect-json", "--dir", str(root), "--keys", "a.b,rows", "--head", "2", "--json"])
            self.assertEqual(soft.returncode, 0, soft.stderr)
            soft_payload = json.loads(soft.stdout)
            self.assertEqual(soft_payload["schema"], "agent-retro-json-facts/v1")
            self.assertEqual(soft_payload["gate"], "soft_advisory_exit_0")
            self.assertGreaterEqual(soft_payload["summary"]["warnings"], 1)
            self.assertIn("agent-retro-playbook.md", soft.stderr)
            self.assertIn("eval-cases.json", soft.stderr)
            self.assertLessEqual(len(soft.stderr.splitlines()), 3)

            strict = self.run_cli(["inspect-json", "--file", str(bad), "--strict", "--json"])
            self.assertEqual(strict.returncode, 1)
            self.assertIn("agent-retro-playbook.md", strict.stderr)
            self.assertIn("eval-cases.json", strict.stderr)
            self.assertLessEqual(len(strict.stderr.splitlines()), 3)

    def test_probe_triage_diagnose_and_rollup_stay_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "handoff"
            handoff.mkdir(parents=True)
            env = self.fixture_env(handoff)
            for args in (
                ["probe", "--mode", "full", "--aspects", "l0,l1,l2", "--json"],
                ["triage", "--pick-top", "3", "--json"],
                ["diagnose", "--skill", "agent-retro", "--json"],
                ["rollup", "--handoff-root", str(handoff), "--json"],
            ):
                proc = self.run_cli(args, env=env)
                self.assertEqual(proc.returncode, 0, (args, proc.stderr))
                self.assertTrue(json.loads(proc.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
