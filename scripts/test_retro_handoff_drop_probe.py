"""Positive and counter-case replay for handoff-loss detection."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROBE = _load("handoff_probe_test", ROOT / "skills/agent-retro/scripts/agent_retro_core/handoff_probe.py")
RETRO_LOOP = _load("retro_loop_handoff_test", ROOT / "skills/retro-loop/scripts/retro_story_engine.py")


def _event(session_id: str, event_type: str, **data):
    return {"type": event_type, "sessionId": session_id, "data": data}


class RetroHandoffDropTests(unittest.TestCase):
    def test_instruction_drop_positive_and_retro_loop_proposal(self):
        events = [
            _event("m2-1", "session.start", role="m2-dispatcher"),
            _event("m2-1", "user.message", content="将 5 类 UI Bug 确定性拦截规则落实到设计技能"),
            _event("m2-1", "tool.execution_complete", toolName="bash",
                   arguments={"command": 'grep "5 类 UI Bug" skills'}),
        ]
        signals = PROBE.detect_handoff_signals(events)
        self.assertEqual([item["code"] for item in signals], ["HANDOFF_INSTRUCTION_DROP"])
        self.assertEqual(signals[0]["upstream_skill"], "personal-m2-assistant")
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "events.jsonl"
            trace.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n")
            report = RETRO_LOOP.discover_aidata_signals(
                warehouse_db=Path(tmp) / "missing.db",
                queries_dir=Path(tmp) / "missing-queries",
                auto_propose=False,
                trace_export_path=trace,
            )
        self.assertEqual(report["handoff_findings"][0]["code"], "HANDOFF_INSTRUCTION_DROP")
        self.assertTrue(any(item["metric_id"] == "HANDOFF_INSTRUCTION_DROP" for item in report["project_loop_a_proposals"]))

    def test_router_parameter_trap_positive(self):
        events = [
            _event("router-1", "session.start", role="m2"),
            _event(
                "router-1",
                "tool.execution_complete",
                toolName="bash",
                arguments={"command": "python3 m2-router.py --plan --message short --targets-json targets.json"},
                result={"content": 'domain_key: my-designer -> new-topic-unmapped-domain, "space": "unspecified"'},
            ),
        ]
        signals = PROBE.detect_handoff_signals(events)
        self.assertEqual([item["code"] for item in signals], ["M2_ROUTER_PARAM_TRAP"])
        self.assertEqual(signals[0]["upstream_skill"], "personal-m2-assistant")

    def test_file_pointer_handoff_is_exempt(self):
        events = [
            _event("m1-1", "session.start", role="team-lead"),
            _event("m1-1", "user.message", content="Read handoff/g1/spec.md and execute"),
            _event("m1-1", "tool.execution_complete", toolName="view", arguments={"path": "handoff/g1/spec.md"}),
            _event("m1-1", "tool.execution_complete", toolName="grep", arguments={"command": "grep spec.md"}),
        ]
        self.assertEqual(PROBE.detect_handoff_signals(events), [])

    def test_owner_short_reply_and_phase_zero_are_exempt(self):
        for content in ("做", "先聊聊 UI 方案"):
            events = [
                _event("owner-1", "session.start", role="owner"),
                _event("owner-1", "user.message", content=content),
                _event("owner-1", "tool.execution_complete", toolName="grep", arguments={"command": "grep UI"}),
            ]
            self.assertEqual(PROBE.detect_handoff_signals(events), [])


if __name__ == "__main__":
    unittest.main()
