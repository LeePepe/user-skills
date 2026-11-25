"""Positive and counter-case replay for v2 parity and horizon profilers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

_test_path = Path(__file__).with_name("test_agent_retro.py")
_test_spec = importlib.util.spec_from_file_location("test_agent_retro", _test_path)
assert _test_spec and _test_spec.loader
_test_module = importlib.util.module_from_spec(_test_spec)
sys.modules[_test_spec.name] = _test_module
_test_spec.loader.exec_module(_test_module)
AR = _test_module.AR


class AgentRetroV2Tests(unittest.TestCase):
    def test_target_watermark_and_pre_fix_counter(self):
        now = datetime.now(timezone.utc)
        events = [{"type": "skill.invoked", "timestamp": (now + timedelta(seconds=i)).isoformat(),
                   "data": {"sessionId": "s", "skillName": "team-lead"}} for i in range(2)]
        watermark = {"target_skill_watermarks": {"team-lead": {"last_modified_epoch": now.timestamp() + 10}}}
        observations = [AR._observation("I5_cross_agent_wild_glue", "invoke", 5, ["s: glue"], skill="team-lead")]
        AR._classify_observations(observations, events, watermark)
        self.assertEqual(observations[0]["fix_bucket"], "pre_fix")
        self.assertEqual(AR._selected({"observations": observations}, include_pre_fix=False), [])
        self.assertTrue(AR._selected({"observations": observations}, include_pre_fix=True))

    def test_cross_skill_contract_check_has_zero_token_shape(self):
        result = AR._cross_skill_contract_check()
        self.assertIn("checked", result)
        self.assertIn("findings", result)
        self.assertIsInstance(result["findings"], list)

    def test_cross_agent_glue_positive_and_counter(self):
        events = []
        for index in range(5):
            events.append({"type": "tool.execution_complete", "data": {"sessionId": f"s{index}",
                "toolName": "bash", "arguments": {"command": "herdr agent list | jq '.[]'"}, "result": {"content": "ok"}}})
        rows = AR._metric_scan(events, [], {})
        self.assertIn("I5_cross_agent_wild_glue", {row["metric"] for row in rows})
        counter = AR._metric_scan(events[:4], [], {})
        self.assertNotIn("I5_cross_agent_wild_glue", {row["metric"] for row in counter})

    def test_jev_mock_is_bounded_and_never_live(self):
        result = AR._run_jev([{"upstream_skill": "team-lead"}], enabled=True,
                             mock={"choice": "team-lead", "noul": 0.9})
        self.assertEqual(result["decision"]["choice"], "team-lead")
        self.assertEqual(result["bounded_seconds"], 1.5)

    def test_cross_skill_contract_hermetic_positive_counter_and_same_skill_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            (root / "caller").mkdir(parents=True)
            (root / "callee" / "scripts").mkdir(parents=True)
            (root / "caller" / "SKILL.md").write_text("python3 skills/callee/scripts/tool.py bad --nope\npython3 skills/caller/scripts/tool.py bad --nope\n")
            (root / "callee" / "scripts" / "tool.py").write_text(
                "import argparse\np=argparse.ArgumentParser()\np.add_argument('command', choices=['good'])\np.add_argument('--ok')\n"
            )
            broken = AR._cross_skill_contract_check(root)
            self.assertFalse(broken["ok"])
            self.assertTrue(any("unknown flag" in item for item in broken["findings"]))
            self.assertTrue(any("unknown subcommand" in item for item in broken["findings"]))
            (root / "caller" / "SKILL.md").write_text("python3 skills/callee/scripts/tool.py good --ok x -- --ignored\n")
            valid = AR._cross_skill_contract_check(root)
            self.assertTrue(valid["ok"], valid)

    def test_warehouse_signals_use_real_schema_and_counter_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "warehouse.db"
            with sqlite3.connect(db_path) as db:
                db.executescript("""
                    CREATE TABLE dim_goal (is_status_drifted INTEGER);
                    CREATE TABLE fact_gtt_event (evt_type TEXT, note_preview TEXT);
                    CREATE TABLE fact_gtt_task_run (round_no INTEGER, v5_verdict TEXT);
                    CREATE TABLE fact_request (model TEXT, model_canon TEXT, total_tokens INTEGER);
                    INSERT INTO dim_goal VALUES (1), (0);
                    INSERT INTO fact_gtt_event VALUES ('REVIEW_FAIL', 'r3'), ('INPUT_BLOCKED', 'x'), ('UNBLOCK', 'x');
                    INSERT INTO fact_gtt_task_run VALUES (3, 'FAIL'), (2, 'FAIL'), (4, 'PASS');
                    INSERT INTO fact_request VALUES ('magpie/group/steering', 'group/steering', 30), ('x', 'opus', 10), ('x', 'other', 60);
                """)
            signals = AR._warehouse_signals(db_path)
            self.assertTrue(signals["available"])
            self.assertEqual(signals["signals"]["status_drifted_goals"], 1)
            self.assertEqual(signals["signals"]["review_fail_r3_plus"], 1)
            self.assertEqual(signals["signals"]["task_runs_total"], 3)
            self.assertEqual(signals["signals"]["steering_tokens"], 40)
            self.assertAlmostEqual(signals["signals"]["steering_token_share"], 0.4)
            self.assertEqual(AR._warehouse_signals(Path(tmp) / "missing.db")["available"], False)

    def test_receipt_rollup_grounding_and_relay_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
            for skill, merge_sha in (("team-lead", sha), ("m2-dispatcher", "deadbeef")):
                result = AR.command_record_receipt(SimpleNamespace(
                    handoff_root=str(root), skill=skill, goal_id=f"goal-{skill}", status="FIXED_AND_MERGED",
                    merge_sha=merge_sha, pr=None, summary="replay"))
                self.assertTrue(result["written"])
            rollup = AR.command_rollup(SimpleNamespace(handoff_root=str(root), days=1, date="2026-11-25", issue_brief=False))
            grounded = {row["merge_sha"]: row["grounded"] for row in rollup["payload"]["receipt_grounding"]}
            self.assertTrue(grounded[sha])
            self.assertFalse(grounded["deadbeef"])
            relay = AR.command_emit_relay(SimpleNamespace(handoff_root=str(root), from_skill="team-lead", to_skill="m2-dispatcher", requirement="verify receipt"))
            self.assertTrue(relay["written"])
            with self.assertRaisesRegex(ValueError, "distinct"):
                AR.command_emit_relay(SimpleNamespace(handoff_root=str(root), from_skill="team-lead", to_skill="team-lead", requirement="bad"))

    def test_jev_thresholds_are_deterministic_including_grey_zone(self):
        selected = [{"upstream_skill": "team-lead"}]
        high = AR._run_jev(selected, enabled=True, mock={"choice": "team-lead", "noul": 0.9})
        low = AR._run_jev(selected, enabled=True, mock={"choice": "team-lead", "noul": 0.1})
        grey = AR._run_jev(selected, enabled=True, mock={"choice": "team-lead", "noul": 0.5})
        self.assertEqual(high["decision"]["choice"], "team-lead")
        self.assertEqual(low["decision"]["choice"], "deterministic_fallback")
        self.assertEqual(grey["decision"]["choice"], "deterministic_fallback")

    def _long(self, extra):
        base = [{"type": "session.start", "timestamp": "2026-11-25T00:00:00+00:00",
                 "data": {"sessionId": "long", "selectedModel": "magpie/group/steering"}}]
        base.extend({"type": "assistant.message", "timestamp": f"2026-11-25T00:{i // 60:02d}:{i % 60:02d}+00:00",
                     "data": {"sessionId": "long", "turn": i, "content": "ok", "outputTokens": 100}}
                    for i in range(1, 41))
        return base + extra

    def test_hzn1_positive_and_counter(self):
        event = {"type": "tool.execution_complete", "data": {"sessionId": "long", "turn": 1,
            "toolName": "view", "result": {"content": "x" * 12000}}}
        rows = AR._horizon_profile(self._long([event]), [], min_turns=40)
        self.assertIn("HZN1_context_carry_tax", {row["metric"] for row in rows})
        counter_event = {"type": "tool.execution_complete", "data": {"sessionId": "long", "turn": 1,
            "toolName": "view", "result": {"content": "small"}}}
        rows = AR._horizon_profile(self._long([counter_event]), [], min_turns=40)
        self.assertNotIn("HZN1_context_carry_tax", {row["metric"] for row in rows})
        referenced = {"type": "tool.execution_complete", "data": {"sessionId": "long", "turn": 1,
            "toolCallId": "carry-1", "toolName": "view", "arguments": {"path": "src/big.py"},
            "result": {"content": "x" * 12000}}}
        later = {"type": "assistant.message", "data": {"sessionId": "long", "turn": 2,
            "content": "The carry-1 output from big.py was used."}}
        rows = AR._horizon_profile(self._long([referenced, later]), [], min_turns=40)
        self.assertNotIn("HZN1_context_carry_tax", {row["metric"] for row in rows})

    def test_hzn2_positive_and_counter(self):
        child = {"type": "subagent.completed", "timestamp": "2026-11-25T00:00:00+00:00", "data": {"sessionId": "long"}}
        parent = {"type": "assistant.message", "timestamp": "2026-11-25T00:04:00+00:00", "data": {"sessionId": "long", "turn": 42, "content": "wake"}}
        rows = AR._horizon_profile(self._long([child, parent]), [], min_turns=40)
        self.assertIn("HZN2_wakeup_lag_and_missed_parallelism", {row["metric"] for row in rows})
        rows = AR._horizon_profile(self._long([child, dict(parent, timestamp="2026-11-25T00:01:00+00:00")]), [], min_turns=40)
        self.assertNotIn("HZN2_wakeup_lag_and_missed_parallelism", {row["metric"] for row in rows})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "queue.json").write_text(json.dumps({"tasks": [
                {"depends_on": [], "paths": ["src/a.py"], "started_at": "2026-11-25T00:00:00Z", "completed_at": "2026-11-25T00:01:00Z"},
                {"depends_on": [], "paths": ["src/b.py"], "started_at": "2026-11-25T00:02:00Z", "completed_at": "2026-11-25T00:03:00Z"},
            ]}))
            record = {"path": path, "ledger": "", "name": "record-m1-receipt"}
            rows = AR._horizon_profile(self._long([]), [record], min_turns=40)
            self.assertIn("HZN2_wakeup_lag_and_missed_parallelism", {row["metric"] for row in rows})
            (path / "queue.json").write_text(json.dumps({"tasks": [
                {"depends_on": ["a"], "paths": ["src/a.py"], "started_at": "2026-11-25T00:00:00Z", "completed_at": "2026-11-25T00:01:00Z"},
                {"depends_on": [], "paths": ["src/b.py"], "started_at": "2026-11-25T00:02:00Z", "completed_at": "2026-11-25T00:03:00Z"},
            ]}))
            rows = AR._horizon_profile(self._long([]), [record], min_turns=40)
            self.assertNotIn("HZN2_wakeup_lag_and_missed_parallelism", {row["metric"] for row in rows})

    def test_hzn3_positive_and_counter(self):
        edits = [{"type": "tool.execution_complete", "data": {"sessionId": "long", "toolName": "edit", "arguments": {"path": "src/a.py"}}} for _ in range(4)]
        edits += [{"type": "tool.execution_complete", "data": {"sessionId": "long", "toolName": "bash", "arguments": {"command": "./scripts/verify"}, "durationMs": 16000}} for _ in range(3)]
        rows = AR._horizon_profile(self._long(edits), [], min_turns=40)
        self.assertIn("HZN3_rework_detour_and_premature_full_verify", {row["metric"] for row in rows})
        rows = AR._horizon_profile(self._long(edits[:2]), [], min_turns=40)
        self.assertNotIn("HZN3_rework_detour_and_premature_full_verify", {row["metric"] for row in rows})

    def test_hzn4_positive_and_counter(self):
        failed = [{"type": "tool.execution_complete", "data": {"sessionId": "long", "turn": 41,
            "toolName": "bash", "shellExecution": {"exitCode": 1}}} for _ in range(4)]
        rows = AR._horizon_profile(self._long(failed), [], min_turns=40)
        self.assertIn("HZN4_context_fatigue_inflection", {row["metric"] for row in rows})
        rows = AR._horizon_profile(self._long([]), [], min_turns=40)
        self.assertNotIn("HZN4_context_fatigue_inflection", {row["metric"] for row in rows})

    def test_hzn5_positive_and_counter(self):
        steering = [{"type": "assistant.message", "data": {"sessionId": "long", "turn": 41 + i,
            "selectedModel": "magpie/group/steering", "content": "read status", "outputTokens": 20}} for i in range(5)]
        rows = AR._horizon_profile(self._long(steering), [], min_turns=40)
        self.assertIn("HZN5_steering_overkill_mechanical_span", {row["metric"] for row in rows})
        counter = [dict(item, data=dict(item["data"], outputTokens=100)) for item in steering]
        rows = AR._horizon_profile(self._long(counter), [], min_turns=40)
        self.assertNotIn("HZN5_steering_overkill_mechanical_span", {row["metric"] for row in rows})


if __name__ == "__main__":
    unittest.main()
