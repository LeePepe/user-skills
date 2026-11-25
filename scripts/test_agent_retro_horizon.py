"""Positive and counter-case replay for v2 parity and horizon profilers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import importlib.util
import sys
import unittest

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

    def test_hzn2_positive_and_counter(self):
        child = {"type": "subagent.completed", "timestamp": "2026-11-25T00:00:00+00:00", "data": {"sessionId": "long"}}
        parent = {"type": "assistant.message", "timestamp": "2026-11-25T00:04:00+00:00", "data": {"sessionId": "long", "turn": 42, "content": "wake"}}
        rows = AR._horizon_profile(self._long([child, parent]), [], min_turns=40)
        self.assertIn("HZN2_wakeup_lag_and_missed_parallelism", {row["metric"] for row in rows})
        rows = AR._horizon_profile(self._long([child, dict(parent, timestamp="2026-11-25T00:01:00+00:00")]), [], min_turns=40)
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
