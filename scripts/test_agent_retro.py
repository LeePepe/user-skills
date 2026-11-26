"""Hermetic positive/counter-case coverage for the standalone agent-retro probe."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

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
        self.assertEqual(len(metrics), 27)
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

    def test_collect_writes_snapshot_and_cohort_watermark(self):
        output = FIXTURE / "collect.json"
        watermark = FIXTURE / "agent-retro-watermark.json"
        args = self.ns(days=3, handoff_root=str(FIXTURE / "handoff"))
        args.output = output
        args.watermark_file = watermark
        result = AR.command_collect(args)
        self.assertEqual(result["schema"], "agent-retro-collect/v1")
        self.assertEqual(result["summary"]["sessions"], 1)
        self.assertTrue(output.is_file())
        self.assertTrue(watermark.is_file())
        self.assertEqual(json.loads(watermark.read_text())["cohort_watermark"], result["watermark"])

    def test_file_issue_apply_creates_deduplicates_updates_and_surfaces_runner_failure(self):
        args = self.ns(date="2026-11-25", apply=True, dry_run=False, server="http://mock", workspace="mock", owner="owner")
        calls = []

        def create_runner(command, **kwargs):
            calls.append((list(command), kwargs))
            if command[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"issues": []}), stderr="")
            if command[:2] == ["issue", "create"]:
                self.assertEqual(kwargs["stdin_text"].count("agent-retro-autopilot-2026-11-25"), 1)
                self.assertIn("`sessions=1`", kwargs["stdin_text"])
                self.assertIn("`coverage=healthy`", kwargs["stdin_text"])
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "created-1"}), stderr="")
            raise AssertionError(command)

        args.runner = create_runner
        created = AR.command_file_issue(args)
        self.assertEqual(created["action"], "created")
        self.assertTrue(created["published"])
        create_call = next(command for command, _ in calls if command[:2] == ["issue", "create"])
        self.assertIn("--priority", create_call)
        self.assertIn("--status", create_call)
        self.assertIn("--assignee-id", create_call)

        calls.clear()
        def update_runner(command, **kwargs):
            calls.append((list(command), kwargs))
            if command[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"issues": [{
                    "id": "existing-1", "title": "[Agent Retro 2026-11-25]", "status": "backlog",
                    "description": "agent-retro-autopilot-2026-11-25",
                }]}), stderr="")
            if command[:2] == ["issue", "update"]:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "existing-1"}), stderr="")
            raise AssertionError(command)

        args.runner = update_runner
        updated = AR.command_file_issue(args)
        self.assertEqual(updated["action"], "updated_existing")
        update_call = next(command for command, _ in calls if command[:2] == ["issue", "update"])
        self.assertIn("--no-start", update_call)

        def failing_runner(command, **kwargs):
            if command[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="mock failure")
            raise AssertionError(command)

        args.runner = failing_runner
        with self.assertRaisesRegex(RuntimeError, "issue list failed"):
            AR.command_file_issue(args)

    def test_setup_autopilot_apply_is_idempotent_for_update_and_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            update_calls = []
            stale_trigger = {"id": "trigger-1", "kind": "schedule", "cron": "0 4 * * *", "timezone": "UTC", "label": "old"}

            def update_runner(command, **kwargs):
                update_calls.append(list(command))
                if command[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"autopilots": [{"id": "ap-existing", "title": AR.DEFAULT_AUTOPILOT_TITLE}]}), stderr="")
                if command[:2] == ["autopilot", "update"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "ap-existing"}), stderr="")
                if command[:2] == ["autopilot", "trigger-list"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"triggers": [stale_trigger]}), stderr="")
                if command[:2] == ["autopilot", "trigger-update"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "trigger-1"}), stderr="")
                raise AssertionError(command)

            args = SimpleNamespace(handoff_root=tmp, dry_run=False, apply=True, server="http://mock", workspace="mock",
                                   receipt=Path(tmp) / "update-receipt.json", runner=update_runner)
            result = AR.command_autopilot(args)
            self.assertEqual(result["autopilot_action"], "updated")
            self.assertEqual(result["trigger_action"], "updated")
            self.assertTrue(any(command[:2] == ["autopilot", "trigger-update"] for command in update_calls))
            self.assertIn("Stage 1+2", result["prompt"])
            self.assertIn("agent-retro-diagnostic", result["prompt"])
            self.assertTrue(args.receipt.is_file())

            create_calls = []
            def create_runner(command, **kwargs):
                create_calls.append(list(command))
                if command[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"autopilots": []}), stderr="")
                if command[:2] == ["autopilot", "create"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "ap-new"}), stderr="")
                if command[:2] == ["autopilot", "trigger-list"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"triggers": []}), stderr="")
                if command[:2] == ["autopilot", "trigger-add"]:
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "trigger-new"}), stderr="")
                raise AssertionError(command)

            create_args = SimpleNamespace(handoff_root=tmp, dry_run=False, apply=True, server="http://mock", workspace="mock",
                                          receipt=None, runner=create_runner)
            created = AR.command_autopilot(create_args)
            self.assertEqual(created["autopilot_action"], "created")
            self.assertEqual(created["trigger_action"], "created")
            self.assertTrue(any(command[:2] == ["autopilot", "trigger-add"] for command in create_calls))

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

    def test_real_split_tool_events_pair_exit_marker_and_output_bloat(self):
        now = datetime.now(timezone.utc).isoformat()
        split_path = FIXTURE / "sessions" / "session-split" / "events.jsonl"
        split_path.parent.mkdir(parents=True)
        events = [
            {"type": "hook.start", "timestamp": now, "data": {"name": "noise"}},
            {"type": "tool.execution_start", "timestamp": now, "data": {
                "toolCallId": "split-1", "toolName": "bash",
                "arguments": {"command": "python3 check.py"},
            }},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "toolCallId": "split-1", "success": False,
                "result": {"content": "<shellId: split-1 completed with exit code 2>\nOutput too large to read at once (...)"},
            }},
        ]
        split_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        parsed, source = AR._read_events(FIXTURE / "sessions", 30, fast=True)
        tools = [event for event in parsed if AR._tool_call_id(event) == "split-1"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(AR._tool_name(tools[0]), "bash")
        self.assertEqual(AR._command(tools[0]), "python3 check.py")
        self.assertEqual(AR._exit_code(tools[0]), 2)
        self.assertIn("Output too large to read at once", AR._result_content(tools[0]))
        self.assertEqual(source["events_read"], len(parsed))
        self.assertNotIn("hook.start", {AR._event_type(event) for event in parsed})

    def test_subagent_completed_same_group_reviewer_is_r5(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {"type": "session.start", "timestamp": now, "data": {
                "sessionId": "parent", "selectedModel": "magpie/group/steering",
                "context": {"cwd": "/tmp/worktrees/example"},
            }},
            {"type": "skill.invoked_ref", "timestamp": now, "data": {"name": "team-lead"}},
            {"type": "subagent.completed", "timestamp": now, "data": {
                "agentName": "code-review", "agentDisplayName": "Reviewer",
                "model": "magpie/group/steering", "totalTokens": 12, "durationMs": 4,
            }},
        ]
        rows = AR._metric_scan(events, [], {})
        self.assertIn("R5_reviewer_same_vendor", {row["metric"] for row in rows})

    def test_active_lease_requires_recent_owned_path_or_matching_worktree(self):
        old = FIXTURE / "handoff" / "old-goal"
        old.mkdir(parents=True)
        old_ledger = old / "ledger.md"
        old_ledger.write_text("status: in_progress\n", encoding="utf-8")
        stale = datetime.now().timestamp() - 3 * 60 * 60
        os.utime(old_ledger, (stale, stale))

        recent = FIXTURE / "handoff" / "agent-retro-real-trace-fix"
        recent.mkdir(parents=True)
        (recent / "ledger.md").write_text(
            "status: in_progress\n\n## Owned Paths\n- skills/m2-dispatcher/**\n\n"
            "Run python3 skills/team-lead/scripts/ci-monitor.py workflow\n",
            encoding="utf-8",
        )
        done = FIXTURE / "handoff" / "done-goal"
        done.mkdir(parents=True)
        (done / "ledger.md").write_text("EVT=GOAL_DONE\nstatus: completed\n", encoding="utf-8")

        os.environ["AGENT_RETRO_WORKTREES"] = str(FIXTURE / "worktrees")
        records, _ = AR._handoffs(FIXTURE / "handoff")
        by_name = {record["name"]: record for record in records}
        self.assertFalse(by_name["old-goal"]["active_lease"])
        self.assertTrue(by_name["agent-retro-real-trace-fix"]["active_lease"])
        self.assertFalse(by_name["done-goal"]["active_lease"])
        self.assertEqual(AR._active_leases(records), {"m2-dispatcher"})

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

    def test_i3_attribution_uses_target_or_first_invoked_not_agent_retro_fallback(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {"type": "session.start", "timestamp": now, "data": {"sessionId": "s1"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"sessionId": "s1", "skillName": "skill-contract"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"sessionId": "s1", "skillName": "my-designer"}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "s1", "toolName": "bash",
                "arguments": {"command": "python3 skills/my-designer/scripts/check.py --bad"},
                "shellExecution": {"exitCode": 2}, "result": {"content": "usage"},
            }},
            {"type": "session.start", "timestamp": now, "data": {"sessionId": "s2"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"sessionId": "s2", "skillName": "team-lead"}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "s2", "toolName": "view",
                "arguments": {"path": "handoff/goal/brief.md"}, "result": {"content": "x" * 21050},
            }},
        ]
        rows = [row for row in AR._metric_scan(events, [], {}) if row["metric"] == "I3_l2_exit2_and_output_bloat"]
        by_sid = {row["session_ids"][0]: row for row in rows}
        self.assertEqual(by_sid["s1"]["upstream_skill"], "my-designer")
        self.assertEqual(by_sid["s2"]["upstream_skill"], "team-lead")
        self.assertTrue(all(row["upstream_skill"] != "agent-retro" for row in rows))

    def test_i3_progressive_disclosure_recovery_is_healthy_counter_case(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {"type": "session.start", "timestamp": now, "data": {"sessionId": "recover"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"sessionId": "recover", "skillName": "skill-contract"}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "recover", "toolName": "view",
                "arguments": {"path": "handoff/goal/T1.md"}, "result": {"content": "Output too large to read at once"},
            }},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "recover", "toolName": "view",
                "arguments": {"path": "handoff/goal/T1.md", "view_range": [1, 120]}, "result": {"content": "ok"},
            }},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "recover", "toolName": "bash",
                "arguments": {"command": "python3 skills/skill-contract/scripts/skill_contract.py audit --summary"},
                "shellExecution": {"exitCode": 0}, "result": {"content": "pass"},
            }},
        ]
        rows = [row for row in AR._metric_scan(events, [], {}) if row["metric"] == "I3_l2_exit2_and_output_bloat"]
        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "healthy_progressive_disclosure" for row in rows))
        self.assertTrue(all(row["upstream_skill"] == "skill-contract" for row in rows))

    def test_i3_unrecovered_exit2_not_exempted_by_unrelated_commands(self):
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {"type": "session.start", "timestamp": now, "data": {"sessionId": "unrecovered"}},
            {"type": "skill.invoked", "timestamp": now, "data": {"sessionId": "unrecovered", "skillName": "my-designer"}},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "unrecovered", "toolName": "bash",
                "arguments": {"command": "python3 skills/my-designer/scripts/check.py --bad"},
                "shellExecution": {"exitCode": 2}, "result": {"content": "usage"},
            }},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "unrecovered", "toolName": "bash",
                "arguments": {"command": "git status"},
                "shellExecution": {"exitCode": 0}, "result": {"content": "clean"},
            }},
            {"type": "tool.execution_complete", "timestamp": now, "data": {
                "sessionId": "unrecovered", "toolName": "view",
                "arguments": {"path": "README.md", "view_range": [1, 20]}, "result": {"content": "hi"},
            }},
        ]
        rows = [row for row in AR._metric_scan(events, [], {}) if row["metric"] == "I3_l2_exit2_and_output_bloat"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "actionable")
        self.assertEqual(rows[0]["upstream_skill"], "my-designer")

    def test_classify_and_aggregate_observations_preserves_mixed_post_fix_counts(self):
        now = datetime.now(timezone.utc)
        rows = [
            AR._observation(
                "I3_l2_exit2_and_output_bloat", "invoke", 1, ["s-pre: tool output >20KB"],
                skill="team-lead", sessions=1, session_ids=["s-pre"],
            ),
            AR._observation(
                "I3_l2_exit2_and_output_bloat", "invoke", 2, ["s-post: tool output >20KB"],
                skill="team-lead", sessions=1, session_ids=["s-post"],
            ),
            AR._observation(
                "H5_cross_skill_contract_drift", "handoff", 3,
                ["agent-retro/SKILL.md: unknown flag --skill"], skill="agent-retro", sessions=1,
            ),
        ]
        events = [
            {"type": "session.start", "timestamp": (now - timedelta(days=2)).isoformat(), "data": {"sessionId": "s-pre"}},
            {"type": "session.start", "timestamp": (now + timedelta(days=1)).isoformat(), "data": {"sessionId": "s-post"}},
        ]
        watermark = {"target_skill_watermarks": {"team-lead": {"last_modified_epoch": int(now.timestamp())}}}
        AR._classify_observations(rows, events, watermark)
        aggregated = {row["metric"]: row for row in AR._aggregate_observations(rows)}
        i3 = aggregated["I3_l2_exit2_and_output_bloat"]
        self.assertEqual(i3["count"], 3)
        self.assertEqual(i3["scenario_sessions"], 2)
        self.assertEqual(i3["pre_fix_count"], 1)
        self.assertEqual(i3["post_fix_count"], 2)
        self.assertEqual(i3["fix_bucket"], "mixed")
        self.assertEqual(aggregated["H5_cross_skill_contract_drift"]["session_ids"], [])
        self.assertEqual(aggregated["H5_cross_skill_contract_drift"]["count"], 3)

    def test_classify_observations_uses_session_ids_not_truncated_evidence(self):
        now = datetime.now(timezone.utc)
        observations = [AR._observation(
            "I3_l2_exit2_and_output_bloat", "invoke", 6,
            [f"pre-{index}: tool output >20KB" for index in range(5)] + ["post-1: tool output >20KB"],
            skill="team-lead", session_ids=[*(f"pre-{index}" for index in range(5)), "post-1"],
        )]
        events = []
        watermark = {"target_skill_watermarks": {"team-lead": {"last_modified_epoch": int(now.timestamp())}}}
        for index in range(5):
            events.append({"type": "session.start", "timestamp": (now - timedelta(days=2)).isoformat(),
                           "data": {"sessionId": f"pre-{index}"}})
        events.append({"type": "session.start", "timestamp": (now + timedelta(days=1)).isoformat(),
                       "data": {"sessionId": "post-1"}})
        AR._classify_observations(observations, events, watermark)
        self.assertEqual(observations[0]["pre_fix_count"], 5)
        self.assertEqual(observations[0]["post_fix_count"], 1)

    def test_collect_snapshot_honors_explicit_target_date(self):
        snap = AR.collect_agent_retro_snapshot(
            days=1, handoff_root=FIXTURE / "handoff", persist=False, target_date="2026-11-25",
        )
        self.assertEqual(snap["date"], "2026-11-25")
        self.assertEqual(snap["cursor"], "agent-retro-autopilot-2026-11-25")


if __name__ == "__main__":
    unittest.main()
