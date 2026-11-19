import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
                "source_id": "Acme/Already",
                "source_uri": "https://github.com/acme/already",
                "source_uris": ["https://GitHub.com/Acme/Already.git/"],
                "question": "How does a swarm work?",
                "learning_state": "learned",
                "source_fingerprint": hashlib.sha256(b"https://github.com/acme/already").hexdigest()[:12],
                "aliases": ["old-swarm-name"],
                "coverage_refs": [],
                "unknown_future_field": {"preserve": True},
            }])
            tools = root / "collected-tools"
            tools.mkdir()
            (tools / "fresh-swarm.md").write_text(
                "---\nsource_uri: https://github.com/acme/fresh\n---\nA multi-agent swarm with a deterministic gate.", encoding="utf-8"
            )
            (tools / "already-covered.md").write_text(
                "---\nsource_uri: https://github.com/acme/already\n---\nSwarm notes.", encoding="utf-8"
            )
            (tools / "variant.md").write_text(
                "---\nsource_uri: https://GitHub.com/Acme/Already.git/\n---\nA multi-agent swarm variant.", encoding="utf-8"
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
            "cand_0_dimension_noul": {"noul": 0.9},
        }})["ranked"]
        markdown = research.digest_dual_zone("2026-11-19", ranked)
        self.assertIn("<!-- multica-cursor:skill-agent-research-2026-11-19 -->", markdown)
        self.assertIn("<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>", markdown)
        self.assertIn("DRAFT TL;DR", markdown)
        self.assertIn("DRAFT actionable checklist", markdown)
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

    def test_low_signal_and_nonfinite_answers_are_suppressed(self):
        candidate = research._candidate(
            key="weak-gate", title="Weak Gate", uri="https://github.com/acme/weak-gate", source_ref="README.md", excerpt="gate"
        )
        result = research.rerank_r09([candidate], mock_jev_json={"answers": {
            "cand_0_direct": {"noul": float("nan")},
            "cand_0_tier": {"score": float("inf")},
            "cand_0_dimension": {"choice": "INVALID"},
            "cand_0_dimension_noul": {"noul": 0.1},
        }})
        self.assertEqual(result["ranked"][0]["r09"]["action"], "KEEP_PRIMARY")
        low = research.rerank_r09([candidate], mock_jev_json={"answers": {
            "cand_0_direct": {"noul": 0.1}, "cand_0_tier": {"score": 1},
            "cand_0_dimension": {"choice": "DETERMINISTIC_GATE"}, "cand_0_dimension_noul": {"noul": 0.9},
        }})
        self.assertEqual(low["ranked"][0]["r09"]["action"], "DROP_NOISE")

    def test_fact_repo_snapshot_jsonl_is_ingested_and_missing_radar_is_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = self._ledger(root, [])
            radar = root / "radar.jsonl"
            radar.write_text(json.dumps({"fact_repo_snapshot": [{"repo": "Org/WorkflowGate", "description": "A deterministic workflow gate", "html_url": "https://github.com/Org/WorkflowGate"}]}) + "\n", encoding="utf-8")
            found = research.discover(vault_dir=vault, collected_tools_dir=root / "missing-tools", radar_path=radar)
            self.assertEqual(found["source_status"]["radar"], "ok")
            self.assertEqual(found["candidates"][0]["source_uri"], "https://github.com/org/workflowgate")
            missing = research.discover(vault_dir=vault, collected_tools_dir=root / "missing-tools", radar_path=root / "missing-radar")
            self.assertEqual(missing["source_status"]["radar"], "missing")
            self.assertTrue(any("radar source missing" in item for item in missing["diagnostics"]))

    def test_public_fetch_uses_readme_text_and_rejects_html(self):
        class Response:
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size):
                return self.body.encode()
        with patch.object(research, "urlopen", return_value=Response("# README\\n" + "grounded source evidence " * 20)):
            body, error = research._fetch_public_evidence("https://github.com/Org/Repo")
        self.assertIsNone(error)
        self.assertIn("README", body)
        with patch.object(research, "urlopen", return_value=Response("<!DOCTYPE html><head>metadata</head>")):
            body, error = research._fetch_public_evidence("https://github.com/Org/Repo")
        self.assertIsNone(body)
        self.assertIn("non-README", error)

    def test_scheduled_collector_invokes_external_fetch_and_persists_real_watermark(self):
        payload = {"ok": True, "status": "SKIPPED_NO_HIGH_SIGNAL", "has_delta": False, "delta_count": 0, "watermark": "skill-agent-research:2026-11-19:abc", "date": "2026-11-19", "items": []}
        with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")) as proc:
            collected = runner._default_loop_collector(runner.resolve_loop_spec("skill-agent-daily-research"), target_date=date(2026, 11, 19), dry_run=False)
        self.assertEqual(collected["status"], "SKIPPED_NO_HIGH_SIGNAL")
        command = proc.call_args.args[0]
        self.assertIn("--fetch-public", command)

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "watermarks.json"
            collected = {"date": "2026-11-19", "has_delta": True, "delta_count": 1, "severity": "medium", "watermark": "wm-real", "cursor": "skill-agent-research-2026-11-19", "title": "Research", "summary": "candidate", "items": [{"what": "candidate", "why": "owner review"}]}
            def fake_runner(args, **kwargs):
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"issues": []}), stderr="")
                if args[:2] == ["issue", "create"]:
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"id": "issue-1"}), stderr="")
                return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")
            response = {"answers": {"actionable_probability": {"noul": 0.9}, "delivery_route": {"choice": "dual_zone_issue"}, "delivery_route_match": {"noul": 0.9}, "urgency_score": {"score": 0.8}}}
            result = runner.run_loop("skill-agent-daily-research", target_date=date(2026, 11, 19), state_path=state, collector_fn=lambda spec, target_date: collected, jev_fn=lambda **kwargs: response, runner=fake_runner, dry_run=False)
            self.assertEqual(result["status"], "ACTION_TAKEN")
            self.assertEqual(runner.load_watermarks(state)["skill-agent-daily-research"]["watermark"], "wm-real")

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
