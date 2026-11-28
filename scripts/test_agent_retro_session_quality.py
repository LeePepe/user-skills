"""Positive and counter-case coverage for session-quality soft advisories."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / ".agent-retro-quality-fixtures"
SCRIPT = ROOT / "skills" / "agent-retro" / "scripts" / "agent_retro.py"
spec = importlib.util.spec_from_file_location("agent_retro_quality_test", SCRIPT)
assert spec and spec.loader
AR = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = AR
spec.loader.exec_module(AR)


class AgentRetroSessionQualityTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(FIXTURE, ignore_errors=True)
        (FIXTURE / "sessions").mkdir(parents=True)
        (FIXTURE / "handoff").mkdir(parents=True)
        self.now = datetime.now(timezone.utc).isoformat()

    def tearDown(self):
        for name in ("AGENT_RETRO_SESSIONS", "AGENT_RETRO_HANDOFF_ROOT"):
            os.environ.pop(name, None)
        shutil.rmtree(FIXTURE, ignore_errors=True)

    def event(self, session, tool=None, args=None, *, role=None, content=None, exit_code=None, result=None, goal=None):
        data = {"sessionId": session}
        if role:
            data["role"] = role
        if goal:
            data["goalId"] = goal
        if content is not None:
            data["content"] = content
        if tool:
            data.update({"toolName": tool, "arguments": args or {}})
            if exit_code is not None:
                data["shellExecution"] = {"exitCode": exit_code}
            if result is not None:
                data["result"] = {"content": result}
            return {"type": "tool.execution_complete", "timestamp": self.now, "data": data}
        return {"type": "session.start", "timestamp": self.now, "data": data}

    def quality(self, events, handoffs=None):
        return AR._session_quality_rows(events, handoffs or [])

    def test_regex_churn_positive_and_two_edit_counter_case(self):
        events = [self.event("regex", role="writer", goal="us-my-designer-boundary")]
        for _ in range(3):
            events.append(self.event("regex", "edit", {"path": "skills/my-designer/scripts/design_contract.py", "new_str": "re.compile(r'boundary')"}))
        rows = self.quality(events)
        self.assertEqual(rows[0]["kind"], "session_false_positive_churn")
        self.assertEqual(rows[0]["subkind"], "multi_round_regex_boundary_rework")
        self.assertEqual(rows[0]["upstream_skill"], "my-designer")
        self.assertEqual(rows[0]["tier"], "soft_advisory_exit_0")
        self.assertIn("Shared-Token Adversarial Twin", rows[0]["remediation"])

        counter = [self.event("regex-counter", role="writer")]
        for _ in range(2):
            counter.append(self.event("regex-counter", "edit", {"path": "skills/my-designer/scripts/design_contract.py", "new_str": "re.compile(r'boundary')"}))
        self.assertFalse(self.quality(counter))

    def test_waiver_and_ledger_review_fail_positive(self):
        events = [self.event("waiver", role="writer", goal="us-my-designer-waiver"),
                  self.event("waiver", "edit", {"path": "skills/my-designer/scripts/design_contract.py", "new_str": "# design-contract-allow"})]
        rows = self.quality(events)
        self.assertEqual(rows[0]["subkind"], "in_session_waiver_bypass_churn")
        ledger = {"name": "agent-retro-my-designer", "ledger": "EVT=REVIEW_FAIL regex boundary\nEVT=REVIEW_FAIL false-positive negation", "spec": "Owned Paths: skills/my-designer/scripts/verify_design.py", "briefs": [], "receipts": []}
        rows = self.quality([], [ledger])
        self.assertEqual(rows[0]["subkind"], "multi_round_regex_boundary_rework")
        self.assertEqual(rows[0]["skill"], "my-designer")

    def test_prose_echo_positive_and_negative_probe_counter_case(self):
        mutation = {"path": "scripts/test_foo.py", "new_str": "self.assertIn('x', path.read_text())  # SKILL.md"}
        rows = self.quality([self.event("writer", role="writer"), self.event("writer", "edit", mutation)])
        self.assertTrue(any(row["subkind"] == "prose_echo_or_vacuous_test_mutation" for row in rows))
        events = [self.event("writer-counter", role="writer"), self.event("writer-counter", "edit", mutation),
                  self.event("writer-counter", "bash", {"command": "python3 validator.py --snippet bad"}, exit_code=1)]
        self.assertFalse(any(row["subkind"] == "prose_echo_or_vacuous_test_mutation" for row in self.quality(events)))

    def test_fake_command_zero_pass_and_real_test_counter_case(self):
        events = [self.event("fake", "bash", {"command": "echo pytest"}, exit_code=0),
                  self.event("fake", "bash", {"command": "validator"}, result='{"ok": true, "files": 0}')]
        rows = self.quality([self.event("fake", role="writer"), *events])
        self.assertTrue(any(row["subkind"] == "fake_command_or_zero_file_pass" for row in rows))
        counter = [self.event("real", role="writer"), self.event("real", "bash", {"command": "python3 -m unittest"}, exit_code=0, result='{"ok": true, "files": 3}')]
        self.assertFalse(any(row["subkind"] == "fake_command_or_zero_file_pass" for row in self.quality(counter)))

    def test_reviewer_pass_requires_adversarial_probe(self):
        base = [self.event("review", role="reviewer", content="EVT=REVIEW_PASS"),
                self.event("review", "edit", {"path": "skills/my-designer/scripts/verify_design.py", "new_str": "return True"})]
        self.assertTrue(any(row["subkind"] == "reviewer_pass_without_adversarial_probe" for row in self.quality(base)))
        counter = base + [self.event("review", "bash", {"command": "python3 verify.py --snippet bad"}, exit_code=1)]
        self.assertFalse(any(row["subkind"] == "reviewer_pass_without_adversarial_probe" for row in self.quality(counter)))

    def test_probe_diagnose_triage_surface_soft_block_and_preserve_27_metrics(self):
        session = FIXTURE / "sessions" / "quality"
        session.mkdir()
        events = [self.event("quality", role="writer", goal="us-my-designer-quality"),
                  self.event("quality", "edit", {"path": "scripts/test_quality.py", "new_str": "Text('Clean')"})]
        (session / "events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
        os.environ["AGENT_RETRO_SESSIONS"] = str(FIXTURE / "sessions")
        os.environ["AGENT_RETRO_HANDOFF_ROOT"] = str(FIXTURE / "handoff")
        args = type("Args", (), {"days": 30, "role": None, "layer": None, "fast": True, "handoff_root": str(FIXTURE / "handoff"), "handoff_dir": None})()
        probe = AR.command_probe(args)
        self.assertEqual(len(probe["metrics"]), 27)
        self.assertEqual(probe["soft_session_quality_advisories"]["tier"], "soft_advisory_exit_0")
        diagnosis = AR.command_diagnose(type("Args", (), {"days": 30, "role": None, "layer": None, "fast": True, "handoff_root": str(FIXTURE / "handoff"), "handoff_dir": None, "skill": "agent-retro"})())
        self.assertEqual(diagnosis["soft_session_quality_advisories"]["tier"], "soft_advisory_exit_0")
        triage = AR.command_triage(type("Args", (), {"days": 30, "role": None, "layer": None, "fast": True, "handoff_root": str(FIXTURE / "handoff"), "handoff_dir": None, "pick_one": False, "pick_top": 3, "use_jev": False, "mock_jev_json": None, "emit_briefs": None, "include_pre_fix": False})())
        self.assertEqual(triage["soft_session_quality_advisories"]["tier"], "soft_advisory_exit_0")

    def test_real_negative_cli_probe_is_exit_one(self):
        missing = FIXTURE / "missing.json"
        proc = subprocess.run([sys.executable, str(SCRIPT), "inspect-json", "--path", str(missing), "--strict", "--json"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("hard_fail_exit_1", proc.stdout)


if __name__ == "__main__":
    unittest.main()
