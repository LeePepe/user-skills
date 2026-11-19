import importlib.util
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parents[1]
RESEARCH_PATH = ROOT / "skills/knowledge-digest/scripts/skill_agent_research_loop.py"
RUNNER_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


research = load("test_skill_agent_research", RESEARCH_PATH)
runner = load("test_skill_agent_loop_runner", RUNNER_PATH)


class SkillAgentResearchLoopTests(unittest.TestCase):
    def _ledger(self, root, topics):
        vault = root / "vault"
        vault.mkdir()
        (vault / "topic-ledger.json").write_text(json.dumps({"schema_version": "personal-topic-ledger/v1", "topics": topics}), encoding="utf-8")
        return vault

    def test_discover_is_workflow_scoped_and_deduplicates_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = self._ledger(root, [{
                "topic_key": "already-covered",
                "source_uri": "https://github.com/acme/already",
                "question": "How does a swarm work?",
                "learning_state": "queued",
                "source_fingerprint": "covered",
                "aliases": ["old-swarm-name"],
                "coverage_refs": [],
            }])
            tools = root / "collected-tools"
            tools.mkdir()
            (tools / "fresh-swarm.md").write_text(
                "---\nsource_uri: https://github.com/acme/fresh\n---\nA multi-agent swarm with a deterministic gate.", encoding="utf-8"
            )
            (tools / "already-covered.md").write_text(
                "---\nsource_uri: https://github.com/acme/already\n---\nSwarm notes.", encoding="utf-8"
            )
            result = research.discover(vault_dir=vault, collected_tools_dir=tools, radar_path=root / "missing")
            self.assertTrue(result["ok"], result)
            self.assertEqual([row["topic_key"] for row in result["candidates"]], ["fresh-swarm"])
            self.assertEqual(result["track"], "workflow")

    def test_rerank_uses_formula_and_malformed_jev_falls_back(self):
        candidates = [research._candidate(
            key="gate", title="Deterministic Gate", uri="https://github.com/acme/gate", source_ref="README.md", excerpt="eval gate"
        )]
        result = research.rerank_r09(candidates, mock_jev_json="{malformed")
        self.assertTrue(result["ok"])
        row = result["ranked"][0]
        self.assertAlmostEqual(row["r09"]["composite_score"], round(0.65 * 0.78 + 0.35 * (2.4 / 3.0), 6))
        self.assertTrue(any("fallback" in msg for msg in result["diagnostics"]))

    def test_dual_zone_has_cursor_and_bounded_human_zone(self):
        candidate = research._candidate(
            key="context-skill", title="Context Engineering Skill", uri="https://github.com/acme/context", source_ref="repo/README.md", excerpt="context skill"
        )
        ranked = research.rerank_r09([candidate], mock_jev_json={"answers": {
            "cand_0_direct": {"noul": 0.9},
            "cand_0_tier": {"score": 3},
            "cand_0_dimension": {"choice": "CONTEXT_ENGINEERING"},
        }})["ranked"]
        markdown = research.digest_dual_zone("2026-11-19", ranked)
        self.assertIn("<!-- multica-cursor:skill-agent-research-2026-11-19 -->", markdown)
        self.assertIn("<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>", markdown)
        self.assertTrue(research._load_module("test_issue_brief", research.ISSUE_BRIEF_PATH).verify_dual_zone_issue(markdown)["ok"])

    def test_loop_check_is_candidate_only_and_runner_watermark_skips_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = self._ledger(root, [])
            tools = root / "collected-tools"
            tools.mkdir()
            (tools / "harness.md").write_text("source_uri: https://github.com/acme/harness\nAgent harness runtime", encoding="utf-8")
            first = research.loop_check(vault_dir=vault, collected_tools_dir=tools, radar_path=root / "missing", mock_jev_json={"answers": {
                "cand_0_direct": {"noul": 0.9}, "cand_0_tier": {"score": 3}, "cand_0_dimension": {"choice": "HARNESS_RUNTIME"}
            }}, day="2026-11-19")
            self.assertEqual(first["status"], "ACTIONABLE")
            self.assertNotIn("knowledge-vault/concepts", json.dumps(first))

            state = root / "state.json"
            collected = dict(first)
            calls = []
            def collect(spec, target_date):
                calls.append(target_date)
                return collected
            a = runner.run_loop("skill-agent-daily-research", target_date=date(2026, 11, 19), state_path=state, collector_fn=collect, dry_run=True, mock_jev_json={"answers": {"actionable_probability": {"noul": 0.9}, "delivery_route": {"choice": "dual_zone_issue"}, "delivery_route_match": {"noul": 0.9}, "urgency_score": {"score": 0.8}}})
            self.assertEqual(a["status"], "ACTION_TAKEN")
            runner.save_watermarks({"skill-agent-daily-research": {"watermark": collected["watermark"]}}, state)
            b = runner.run_loop("skill-agent-daily-research", target_date=date(2026, 11, 19), state_path=state, collector_fn=collect)
            self.assertEqual(b["status"], "SKIPPED_NO_DELTA")
            self.assertEqual(len(calls), 2)

    def test_sync_selector_only_updates_requested_loop(self):
        calls = []
        def fake_runner(args, **kwargs):
            calls.append(list(args))
            if args[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"autopilots": []}), stderr="")
            if args[:2] == ["autopilot", "create"]:
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"id": "ap-1"}), stderr="")
            if args[:2] == ["autopilot", "trigger-list"]:
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"triggers": []}), stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")
        result = runner.sync_autopilots(loop_id="skill-agent-daily-research", runner=fake_runner)
        self.assertEqual([item["loop_id"] for item in result["autopilots"]], ["skill-agent-daily-research"])
        self.assertFalse(any("daily-brief" in " ".join(call) for call in calls))
        create_call = next(call for call in calls if call[:2] == ["autopilot", "create"])
        description = create_call[create_call.index("--description") + 1]
        self.assertIn("run-loop --loop \"skill-agent-daily-research\"", description)
        self.assertIn("0-Agent Precheck", description)


if __name__ == "__main__":
    unittest.main()
