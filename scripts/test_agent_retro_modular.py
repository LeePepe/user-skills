"""Focused positive/counter-case coverage for the modular agent-retro facade."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "agent-retro" / "scripts" / "agent_retro.py"
spec = importlib.util.spec_from_file_location("agent_retro_modular_test", SCRIPT)
assert spec and spec.loader
AR = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = AR
spec.loader.exec_module(AR)


class AgentRetroModularTests(unittest.TestCase):
    def _long_session(self, turns):
        events = [{
            "type": "session.start", "timestamp": "2026-11-25T00:00:00+00:00",
            "data": {"sessionId": "long", "selectedModel": "magpie/group/steering"},
        }]
        for turn, (assistant, tool) in enumerate(turns, 41):
            events.append({
                "type": "assistant.message", "timestamp": f"2026-11-25T00:{turn // 60:02d}:{turn % 60:02d}+00:00",
                "data": {"sessionId": "long", "turn": turn, "selectedModel": "magpie/group/steering", **assistant},
            })
            events.append({
                "type": "tool.execution_complete", "data": {"sessionId": "long", "toolName": tool[0], **tool[1]},
            })
        return events

    def test_facade_caps_and_runtime_symlink(self):
        self.assertLessEqual(len(SCRIPT.read_text().splitlines()), 240)
        target = SCRIPT.parent / "skill_runtime"
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertIs(AR.main, AR.cli.main)

    def test_hzn5_mechanical_polling_positive(self):
        turns = [
            ({"reasoningText": "", "outputTokens": 20, "content": "poll"},
             ("read_bash", {"arguments": {"command": "read_bash(delay=5)"}}))
            for _ in range(7)
        ]
        rows = AR._horizon_profile(self._long_session(turns), [], min_turns=40)
        self.assertIn("HZN5_steering_overkill_mechanical_span", {row["metric"] for row in rows})

    def test_hzn5_deep_code_read_counter_case(self):
        turns = [
            ({"reasoningText": "architectural source inspection " * 40, "outputTokens": 80, "content": "inspect"},
             ("view", {"arguments": {"path": f"src/module_{i}.py"}, "result": {"content": "source"}}))
            for i in range(7)
        ]
        rows = AR._horizon_profile(self._long_session(turns), [], min_turns=40)
        self.assertNotIn("HZN5_steering_overkill_mechanical_span", {row["metric"] for row in rows})

    def test_skill_slug_counter_case_and_parent_child_signals(self):
        events = [{"type": "assistant.message", "data": {"sessionId": "parent", "role": "team-lead",
            "selectedModel": "magpie/group/steering", "content": "skills/*/scripts/foo.py"}}]
        self.assertNotIn("*", AR._skill_names(events))
        self.assertFalse(AR.is_valid_skill_slug("*"))

        for session, parent in (("parent", None), ("child", "parent")):
            for path in ("src/a.py", "src/b.py", "src/c.py"):
                events.append({"type": "tool.execution_complete", "data": {
                    "sessionId": session, "parentSessionId": parent, "toolName": "view",
                    "arguments": {"path": path}, "result": {"content": "source"},
                }})
        events.append({"type": "assistant.message", "data": {"sessionId": "parent", "role": "team-lead",
            "selectedModel": "magpie/group/steering", "content": "task(general-purpose) --role writer"}})
        rows = AR._metric_scan(events, [], {})
        metrics = {row["metric"] for row in rows}
        self.assertIn("R3_m1_inline_overwork", metrics)
        self.assertIn("HZN1_context_carry_tax", metrics)

    def test_wildcard_command_never_resolves_star_skill_in_probe_or_triage(self):
        obs = AR._observation("I3_l2_exit2_and_output_bloat", "invoke", 1, ["s1: exit=2"], skill="*")
        self.assertEqual(obs["skill"], "agent-retro")
        self.assertEqual(obs["upstream_skill"], "agent-retro")

        now = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            session_dir = tmp_path / "sessions" / "session-wildcard"
            handoff_dir = tmp_path / "handoff"
            session_dir.mkdir(parents=True)
            handoff_dir.mkdir(parents=True)
            events = [
                {"type": "session.start", "timestamp": now, "data": {"sessionId": "session-wildcard"}},
                {
                    "type": "tool.execution_complete",
                    "timestamp": now,
                    "data": {
                        "sessionId": "session-wildcard",
                        "toolName": "bash",
                        "arguments": {"command": "ls skills/*/scripts/foo.py"},
                        "shellExecution": {"exitCode": 2},
                        "result": {"content": "No such file or directory"},
                    },
                },
            ]
            (session_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            prev_sessions = os.environ.get("AGENT_RETRO_SESSIONS")
            prev_handoff = os.environ.get("AGENT_RETRO_HANDOFF_ROOT")
            os.environ["AGENT_RETRO_SESSIONS"] = str(tmp_path / "sessions")
            os.environ["AGENT_RETRO_HANDOFF_ROOT"] = str(handoff_dir)
            try:
                probe_args = AR.cli.build_parser().parse_args(["probe", "--days", "3", "--fast", "--json"])
                probe = AR.command_probe(probe_args)
                probe_skills = {
                    row.get("skill")
                    for row in probe.get("observations", []) + probe.get("healthy_exemptions", [])
                } | {
                    row.get("upstream_skill")
                    for row in probe.get("observations", []) + probe.get("healthy_exemptions", [])
                } | {
                    row.get("skill") for row in probe.get("skill_attribution", [])
                }
                self.assertNotIn("*", probe_skills)
                i3 = [row for row in probe["observations"] if row["metric"] == "I3_l2_exit2_and_output_bloat"]
                self.assertTrue(i3)
                self.assertEqual(i3[0]["skill"], "team-lead")
                self.assertEqual(i3[0]["upstream_skill"], "team-lead")

                triage_args = AR.cli.build_parser().parse_args(["triage", "--days", "3", "--fast", "--json"])
                triage = AR.command_triage(triage_args)
                triage_skills = {
                    row.get("skill") for row in triage.get("picked", [])
                } | {
                    row.get("upstream_skill") for row in triage.get("picked", [])
                } | set(triage.get("m2_dispatch_plan", {}).get("skills", []))
                self.assertNotIn("*", triage_skills)
            finally:
                if prev_sessions is None:
                    os.environ.pop("AGENT_RETRO_SESSIONS", None)
                else:
                    os.environ["AGENT_RETRO_SESSIONS"] = prev_sessions
                if prev_handoff is None:
                    os.environ.pop("AGENT_RETRO_HANDOFF_ROOT", None)
                else:
                    os.environ["AGENT_RETRO_HANDOFF_ROOT"] = prev_handoff


if __name__ == "__main__":
    unittest.main()
