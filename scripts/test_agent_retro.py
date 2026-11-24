"""Hermetic positive/counter-case coverage for the standalone agent-retro probe."""
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
FIXTURE = ROOT / ".agent-retro-test-fixtures"
SCRIPT = ROOT / "skills" / "agent-retro" / "scripts" / "agent_retro.py"


def load_module():
    spec = importlib.util.spec_from_file_location("agent_retro_test_module", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AR = load_module()


class AgentRetroTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(FIXTURE, ignore_errors=True)
        os.environ["AGENT_RETRO_SESSIONS"] = str(FIXTURE / "sessions")
        os.environ["AGENT_RETRO_HANDOFF_ROOT"] = str(FIXTURE / "handoff")
        (FIXTURE / "sessions" / "session-mixed").mkdir(parents=True)
        (FIXTURE / "handoff" / "goal-done" / "briefs").mkdir(parents=True)
        (FIXTURE / "handoff" / "goal-done" / "receipts").mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {"type": "session.start", "timestamp": now, "data": {"sessionId": "session-mixed", "role": "m2-dispatcher"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"skillName": "m2-dispatcher"}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {"toolCallId": "edit-1", "toolName": "edit", "arguments": {"path": "src/main.py"}, "result": {"content": "ok"}}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {"toolCallId": "fail-1", "toolName": "bash", "arguments": {"command": "python3 check.py"}, "shellExecution": {"exitCode": 1}, "result": {"content": "bad"}}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {"toolCallId": "drawer-1", "toolName": "view", "arguments": {"path": "skills/example/references/fix.md"}, "result": {"content": "fix"}}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {"toolCallId": "pass-1", "toolName": "bash", "arguments": {"command": "python3 check.py"}, "shellExecution": {"exitCode": 0}, "result": {"content": "pass"}}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {"toolCallId": "bloat-1", "toolName": "bash", "arguments": {"command": "python3 noisy.py"}, "shellExecution": {"exitCode": 2}, "result": {"content": "x" * 21001}}},
        ]
        (FIXTURE / "sessions" / "session-mixed" / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        (FIXTURE / "handoff" / "goal-done" / "ledger.md").write_text("# Goal\nstatus: done\n", encoding="utf-8")
        (FIXTURE / "handoff" / "goal-done" / "spec.md").write_text("Owned Paths: src/main.py\n", encoding="utf-8")

    def tearDown(self):
        os.environ.pop("AGENT_RETRO_SESSIONS", None)
        os.environ.pop("AGENT_RETRO_HANDOFF_ROOT", None)
        os.environ.pop("AGENT_RETRO_WORKTREES", None)
        os.environ.pop("SF_DATALESS", None)
        os.environ.pop("AGENT_RETRO_WAREHOUSE", None)
        shutil.rmtree(FIXTURE, ignore_errors=True)

    def ns(self, **kwargs):
        defaults = {"days": 30, "role": None, "layer": None, "fast": True,
                    "handoff_root": str(FIXTURE / "handoff"), "handoff_dir": None,
                    "pick_one": False, "pick_top": 3, "use_jev": False,
                    "mock_jev_json": None, "emit_briefs": None, "skill": None,
                    "issue_brief": False, "date": None, "apply": False,
                    "server": "", "workspace": "", "owner": "", "agent": "agent-retro"}
        defaults.update(kwargs)
        return type("Args", (), defaults)()

    def test_nested_and_flat_event_fields_are_parsed_and_all_metrics_exist(self):
        result = AR._scan(self.ns())
        metrics = {row["metric"] for row in result["metrics"]}
        self.assertEqual(len(metrics), 20)
        observations = {row["metric"] for row in result["observations"]}
        self.assertIn("R2_m2_direct_code_edit", observations)
        self.assertIn("I3_l2_exit2_and_output_bloat", observations)
        self.assertIn("H3_r10_receipt_gap_or_dirty_schema", observations)

    def test_progressive_disclosure_is_exempt_and_positive(self):
        result = AR._scan(self.ns())
        healthy = [x for x in result["healthy_exemptions"] if x["metric"] == "E1_blind_spin_loop_vs_progressive_catch"]
        self.assertTrue(healthy)
        self.assertNotIn("E1_blind_spin_loop_vs_progressive_catch", {x["metric"] for x in result["observations"]})

    def test_counter_case_three_identical_failures_is_actionable(self):
        events_path = FIXTURE / "sessions" / "session-mixed" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        for i in range(3):
            events.append({"type": "tool.execution_complete", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"toolName": "bash", "arguments": {"command": "python3 spin.py"}, "shellExecution": {"exitCode": 1}, "result": {"content": "same"}}})
        events_path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        result = AR._scan(self.ns())
        item = [x for x in result["observations"] if x["metric"] == "E1_blind_spin_loop_vs_progressive_catch"]
        self.assertTrue(item)
        self.assertFalse(any(x["status"] == "healthy_progressive_disclosure" for x in item))

    def test_triage_uses_priority_for_objective_low_sample_issues_and_caps_three(self):
        result = AR.command_triage(self.ns(pick_one=False, use_jev=True, mock_jev_json=None, emit_briefs=None))
        self.assertLessEqual(result["picked_count"], 3)
        self.assertEqual(result["m2_dispatch_plan"]["m2_coordinator_count"], 1)

    def test_emit_briefs_is_non_clobbering(self):
        args = self.ns(skill="m2-dispatcher", pick_one=True, emit_briefs=str(FIXTURE / "out"))
        first = AR.command_emit(args)
        self.assertTrue(first["emitted"])
        target = Path(first["emitted"][0]["path"])
        (target / "spec.md").write_text("owner content", encoding="utf-8")
        second = AR.command_emit(args)
        self.assertEqual((target / "spec.md").read_text(), "owner content")
        self.assertFalse(second["emitted"][0]["spec_created"])

    def test_rollup_issue_is_verified_dual_zone(self):
        args = self.ns(issue_brief=True, date="2026-11-24")
        result = AR.command_rollup(args)
        self.assertTrue(result["issue_verified"]["ok"], result["issue_verified"])
        self.assertIn("[Agent Retro 2026-11-24]", result["issue_markdown"])

    def test_cli_errors_are_two_and_point_to_playbook(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "not-a-command"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("agent-retro-playbook.md", proc.stderr)
        self.assertLessEqual(len(proc.stderr.splitlines()), 3)

    def test_cli_alias_and_dry_run_do_not_publish_or_schedule(self):
        env = os.environ.copy()
        env["AGENT_RETRO_SESSIONS"] = str(FIXTURE / "sessions")
        env["AGENT_RETRO_HANDOFF_ROOT"] = str(FIXTURE / "handoff")
        for command in ("file-issue", "setup-autopilot"):
            proc = subprocess.run([sys.executable, str(SCRIPT), command, "--dry-run", "--dir", str(FIXTURE / "handoff"), "--json"], cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["dry_run"])
        self.assertFalse((FIXTURE / "handoff" / "agent-retro-autopilot.json").exists())

    def test_target_skill_is_preserved_and_first_fault_is_attributed(self):
        direct = AR._observation("I3_l2_exit2_and_output_bloat", "invoke", 1, ["s: noisy"], skill="target-skill")
        self.assertEqual(direct["skill"], "target-skill")
        self.assertEqual(direct["upstream_skill"], "target-skill")
        events = [
            {"type": "session.start", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"sessionId": "faults", "role": "m1"}},
            {"type": "assistant.message", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"content": "[SPEC_GAP] design.md caused review fail R1 FAIL R2 FAIL R3 FAIL"}},
            {"type": "tool.execution_complete", "timestamp": datetime.now(timezone.utc).isoformat(), "data": {"toolName": "bash", "arguments": {"command": "python3 verify.py"}, "shellExecution": {"exitCode": 1}, "result": {"content": "PR_CHECK_FAILED"}}},
        ]
        rows = AR._metric_scan(events, [], {})
        e2 = next(row for row in rows if row["metric"] == "E2_r3_plus_divergence")
        self.assertEqual(e2["upstream_skill"], "my-designer")
        events[1]["data"]["content"] = "verification passed locally; remote CI PR_CHECK_FAILED"
        rows = AR._metric_scan(events, [], {})
        e3 = next(row for row in rows if row["metric"] == "E3_gate_escape_and_uncrystallized_spec_gap")
        self.assertEqual(e3["upstream_skill"], "repo-kit")

    def test_outcome_gate_ignores_healthy_intermediate_trajectory_and_r3_counts_mutations_only(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [{"type": "session.start", "timestamp": now, "data": {"sessionId": "healthy", "role": "m1", "selectedModel": "google/gemini"}},
                  {"type": "user.message", "timestamp": now, "data": {"content": "start"}}]
        for index in range(4):
            events.append({"type": "tool.execution_complete", "timestamp": now, "data": {"toolName": "view", "arguments": {"path": f"src/{index}.py"}, "result": {"content": "ok"}}})
        events.append({"type": "goal.completed", "timestamp": now, "data": {"content": "DONE"}})
        gate, eligible = AR._outcome_gate(AR._session_groups(events))
        self.assertEqual(gate["healthy"]["status"], "OUTCOME_HEALTHY")
        self.assertNotIn("healthy", eligible)
        rows = AR._metric_scan(events, [], {}, eligible)
        self.assertNotIn("R3_m1_inline_overwork", {row["metric"] for row in rows})

    def test_reviewer_vendor_comparison_and_h4_evidence(self):
        now = datetime.now(timezone.utc).isoformat()
        reviewer = [{"type": "session.start", "timestamp": now, "data": {"sessionId": "goal-review", "goalId": "goal-x", "role": "reviewer", "selectedModel": "google/gemini", "writerModel": "google/gemini-writer"}}]
        rows = AR._metric_scan(reviewer, [], {})
        self.assertIn("R5_reviewer_same_vendor", {row["metric"] for row in rows})
        handoff = {"name": "goal-x", "ledger": "status: in_progress", "spec": "Owner constraint alpha beta gamma delta epsilon", "briefs": [("brief.md", "Owner constraint")], "receipts": [], "active_lease": True}
        related = [{"type": "assistant.message", "timestamp": now, "data": {"sessionId": "goal-x", "goalId": "goal-x", "content": "I will delegate and verify"}},
                   {"type": "tool.execution_complete", "timestamp": now, "data": {"sessionId": "goal-x", "goalId": "goal-x", "toolName": "view", "arguments": {"path": "src/x.py"}, "result": {"content": "x"}}}]
        related += [dict(related[1], data=dict(related[1]["data"])) for _ in range(2)]
        related[-1]["data"]["toolName"] = "edit"
        rows = AR._metric_scan(related, [handoff], {})
        self.assertIn("H4_compaction_and_handoff_loss", {row["metric"] for row in rows})

    def test_e4_scope_version_cohorts_and_active_lease_exclusion(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [{"type": "tool.execution_complete", "timestamp": now, "data": {"sessionId": "goal-a", "goalId": "goal-a", "toolName": "bash", "arguments": {"command": "pytest tests/a.py"}, "shellExecution": {"exitCode": 0}, "result": {"content": "ok"}}}]
        record = {"name": "goal-a", "ledger": "goalId: goal-a", "spec": "", "briefs": [], "receipts": [("r10.md", "pytest tests/a.py\nPR_MERGED")], "active_lease": False}
        rows = AR._metric_scan(events, [record], {})
        self.assertIn("E4_unfaithful_receipt_and_cancelled_noise", {row["metric"] for row in rows})
        observations = [AR._observation("R2_m2_direct_code_edit", "role", 2, ["goal-a: edit"], skill="m2-dispatcher")]
        watermark = AR._version_watermark(events, observations)
        self.assertIn("scenario_sessions", watermark["post_fix"])
        self.assertTrue(watermark["post_fix"]["anomalies"] >= 0)
        os.environ["AGENT_RETRO_WORKTREES"] = str(FIXTURE / "worktrees")
        (FIXTURE / "worktrees" / "agent-retro-m2-dispatcher").mkdir(parents=True)
        lease = FIXTURE / "handoff" / "agent-retro-m2-dispatcher"
        lease.mkdir(parents=True)
        (lease / "ledger.md").write_text("status: in_progress\n", encoding="utf-8")
        scan = AR._scan(self.ns())
        triage = AR.command_triage(self.ns())
        self.assertIn("m2-dispatcher", scan["active_leases"])
        self.assertIn("m2-dispatcher", triage["excluded_by_active_lease"])

    def test_dataless_guard_and_output_redaction(self):
        os.environ["SF_DATALESS"] = "1"
        args = self.ns()
        os.environ["AGENT_RETRO_WAREHOUSE"] = os.sep + "Volumes" + os.sep + "Unavailable/warehouse.db"
        self.assertIsNone(AR._roots(args)[2])
        user_path = os.sep + "Users" + os.sep + "alice/project"
        redacted = AR._redact(user_path + " " + "ghp" + "_secret " + "lin_api" + "_secret " + "sk" + "-secret " + "Bearer" + " abc123")
        self.assertNotIn(user_path, redacted)
        self.assertNotIn("ghp" + "_secret", redacted)
        self.assertNotIn("lin_api" + "_secret", redacted)
        self.assertNotIn("sk-secret", redacted)
        self.assertIn("[REDACTED_TOKEN]", redacted)


if __name__ == "__main__":
    unittest.main()
