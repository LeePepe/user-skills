import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import root_tracker
except ModuleNotFoundError:
    module_path = Path(__file__).resolve().parents[1] / "skills/linear-board/scripts/root_tracker.py"
    spec = importlib.util.spec_from_file_location("root_tracker", module_path)
    root_tracker = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(root_tracker)
    sys.modules["root_tracker"] = root_tracker


class RootTrackerTests(unittest.TestCase):
    def setUp(self):
        self.roots = [
            {
                "root_id": "china-release",
                "name": "China Release",
                "repos": ["repo/app"],
                "branch_globs": ["*china*", "*ab114*"],
                "handoff_files": ["*china-summary*"],
                "integration_branches": ["devsync1030"],
                "keywords": ["bing cn", "ab114"],
            },
            {
                "root_id": "other-root",
                "name": "Other",
                "repos": ["repo/other"],
                "branch_globs": ["*other*"],
                "handoff_files": [],
                "integration_branches": [],
                "keywords": ["other"]
            },
        ]

    def test_fallback_classification_returns_new_root_candidate(self):
        item = {"id": "mystery", "title": "unrelated work", "source": "unknown"}
        result = root_tracker.classify_jev(item, self.roots, jev_function=lambda *args, **kwargs: None)
        self.assertEqual(result["root_id"], "NEW_ROOT_CANDIDATE")
        self.assertEqual(result["method"], "fallback-keyword")

    def test_deterministic_branch_beats_shared_repository(self):
        item = {"repository": "repo/app", "branch": "feature/other-cleanup"}
        result = root_tracker.classify_jev(item, self.roots, jev_function=lambda *args, **kwargs: None)
        self.assertEqual(result["root_id"], "other-root")

    def test_discovery_kumo_topic_maps_to_sapphire_root(self):
        roots = self.roots + [{"root_id": "sapphire-discovery-kumo", "name": "Sapphire Discovery Kumo", "keywords": ["kumo"]}]
        result = root_tracker.classify_jev({"title": "Discovery Feed Kumo component", "repository": "repo/app"}, roots)
        self.assertEqual(result["topic_id"], "sapphire-discovery-kumo")
        self.assertEqual(result["root_id"], "sapphire-discovery-kumo")

    def test_aggregate_parses_bugs_worktrees_prs_and_eval_sinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            summary = base / "bugs.md"
            summary.write_text(
                "| # | Bug ID | 状态 | PR / 归属 | 1 句话描述什么问题 | 1 句话描述如何修复的 / 为什么 Blocking |\n"
                "| 1 | [AB#11453378](url) | **BLOCKING** | [#543](url) | Bing CN issue | needs backend |\n",
                encoding="utf-8",
            )
            devsync = base / "devsync1030"
            devsync.mkdir()
            (devsync / "manifest.json").write_text(json.dumps({"entries": [{"bug_id": "AB#11453378", "workflow_status": "BLOCKING", "pr": 543, "branch": "ab11453378-tk1"}]}), encoding="utf-8")
            worktrees = base / "worktrees"
            (worktrees / "ab11453378-tk1").mkdir(parents=True)
            supervisor = base / "my-prs.json"
            supervisor.write_text(json.dumps({
                "watched_prs": [{"pr_number": 543, "repository": "repo/app", "branch": "ab11453378-tk1", "title": "Bing CN fix", "enabled": True}],
                "pending_decisions": {"x": {"pr_number": 543, "kind": "owner_decision", "status": "awaiting_owner", "question": "Blocking decision"}},
            }), encoding="utf-8")
            evals = base / "eval-actions.jsonl"
            evals.write_text(json.dumps({"itemId": "e1", "pr_number": 543, "score": 0.8, "title": "Bing CN reusable test gap"}) + "\n" + json.dumps({"itemId": "e2", "score": 0.6}) + "\n", encoding="utf-8")
            roots = base / "roots.json"
            roots.write_text(json.dumps({"roots": self.roots}), encoding="utf-8")
            snapshot = root_tracker.collect(roots_path=roots, summary_path=summary, devsync_path=devsync, worktrees_path=worktrees, supervisor_path=supervisor, eval_path=evals, latest_discovery_path=base / "missing-latest.json", aidash_eval_path=base / "missing-aidash.json", canonical_rules=[])
            china = snapshot["roots"][0]
            self.assertEqual(china["workstreams"]["Bugs & Devsync"]["count"], 3)
            self.assertEqual(china["workstreams"]["PR & PoP"]["items"][0]["signal"], "P0_BLOCKING_DECISION")
            self.assertEqual(china["workstreams"]["Repo & Skill Sinks"]["count"], 1)
            self.assertEqual(snapshot["unassigned"], [])

    def test_collect_merges_latest_discovery_and_deduplicates_eval_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            latest = base / "latest.json"
            latest.write_text(json.dumps({
                "ok": True,
                "overview": [{"pr_number": 580, "title": "Bing CN China fix"}],
                "awaiting_owner_decisions": [{"decision_id": "POP580-x", "pr_number": 580, "status": "awaiting_owner", "kind": "owner_action"}],
            }), encoding="utf-8")
            supervisor = base / "my-prs.json"
            supervisor.write_text(json.dumps({"watched_prs": [], "pending_decisions": {}}), encoding="utf-8")
            eval_path = base / "eval.jsonl"
            eval_path.write_text(json.dumps({"id": "E1", "score": 0.8, "title": "China reusable one"}) + "\n", encoding="utf-8")
            aidash = base / "aidash.jsonl"
            aidash.write_text(json.dumps({"id": "E1", "score": 0.9, "title": "China duplicate"}) + "\n" + json.dumps({"id": "E2", "score": 0.8, "title": "China two"}) + "\n", encoding="utf-8")
            roots = base / "roots.json"
            roots.write_text(json.dumps({"roots": self.roots}), encoding="utf-8")
            snapshot = root_tracker.collect(roots_path=roots, supervisor_path=supervisor, eval_path=eval_path, latest_discovery_path=latest, aidash_eval_path=aidash, summary_path=base / "missing-summary.md", devsync_path=base / "missing-devsync", worktrees_path=base / "missing-worktrees", canonical_rules=[])
        pr_numbers = {item.get("pr_number") for item in snapshot["roots"][0]["workstreams"]["PR & PoP"]["items"]}
        self.assertIn(580, pr_numbers)
        sink_ids = [item["id"] for item in snapshot["roots"][0]["workstreams"]["Repo & Skill Sinks"]["items"]]
        self.assertEqual(sink_ids.count("E1"), 1)
        self.assertIn("E2", sink_ids)

    def test_eval_extractor_schema_uses_nested_score_and_three_landing_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eval-actions.jsonl"
            path.write_text(json.dumps({
                "id": "EVAL-PR617",
                "title": "Bing CN snapshot gap",
                "source": {"entityId": "PR#617"},
                "jevAssessment": {"score": 0.91, "verdict": "EVAL_WORTHY"},
                "landingOptions": [{"optionId": "LAND_PR_SNAPSHOT_TEST"}],
            }) + "\n", encoding="utf-8")
            sinks = root_tracker.collect_eval_sinks(path)
        self.assertEqual(sinks[0]["pr_number"], 617)
        self.assertEqual(sinks[0]["score"], 0.91)
        self.assertEqual(sinks[0]["landing_options"], list(root_tracker.LANDING_OPTIONS))

    def test_rendering_contains_root_switch_filters_and_actions(self):
        snapshot = {"roots": [{"root_id": "china-release", "name": "China Release", "summary": {}, "workstreams": {name: {"items": []} for name in root_tracker.WORKSTREAMS}}]}
        with tempfile.TemporaryDirectory() as directory:
            output = root_tracker.render_html(snapshot, Path(directory) / "dashboard/index.html")
            html = output.read_text(encoding="utf-8")
        self.assertIn("China Release", html)
        self.assertIn("PoP Ready", html)
        self.assertIn("record-feedback", html)
        self.assertIn("devsync revert", html)
        self.assertIn("P0 Blocking Decision", html)

    def test_pr_workstream_uses_four_sections_and_reconciliation_badge(self):
        root = {"root_id": "china-release", "name": "China Release"}
        items = [
            {
                "type": "pr", "pr_number": 580, "signal": "WAIT_POP_OR_MERGE",
                "title": "PoP gate", "head_sha": "pop-head",
                "pending_decisions": [{"kind": "owner_action", "status": "awaiting_owner", "linear_identifier": "TIA-350", "url": "https://linear.app/TIA-350"}],
            },
            {"type": "pr", "pr_number": 617, "signal": "OBSERVE", "state": "OPEN", "isDraft": True,
             "title": "Waiting approval", "pending_decisions": [{"kind": "owner_decision", "status": "completed", "linear_identifier": "TIA-347"}]},
            {"type": "pr", "pr_number": 620, "signal": "S3_CI_FAILURE", "title": "CI repair"},
            {"type": "pr", "pr_number": 686, "signal": "MERGED", "state": "MERGED", "title": "Landed"},
        ]
        markdown = root_tracker._workstream_markdown(root, "PR & PoP", items)
        sections = [
            "## 1. 🟢 待你点 PoP / Merge",
            "## 2. 🚨 待你拍板 Blocking 决策",
            "## 3. 🤖 在途 Open PR 监督",
            "## 4. ✅ 已合入 Merged",
        ]
        self.assertEqual([markdown.index(section) for section in sections], sorted(markdown.index(section) for section in sections))
        self.assertIn("⏳ 评论已清零，等 Reviewer Approve / CI", markdown)
        self.assertIn("DRAFT", markdown)
        self.assertIn("已修完 Resolve: TIA-347", markdown)
        self.assertIn("[#686]", markdown)
        self.assertNotIn("Landed", markdown)

    def test_discovery_head_advance_reconciles_pending_decision(self):
        config = {
            "watched_prs": [],
            "pending_decisions": {
                "san/bing-growth-sapphire-app#617:5221157": {
                    "decision_id": "D617-5221157", "pr_number": 617,
                    "status": "awaiting_owner", "head_sha": "old-head",
                },
            },
        }
        merged = root_tracker._merge_discovery(config, {
            "overview": [{"number": 617, "headRefOid": "new-head-123", "unresolvedCount": 0, "signals": []}],
        })
        decision = merged["pending_decisions"]["san/bing-growth-sapphire-app#617:5221157"]
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["reconciled_by"], "cross_session_head:new-head-")

    def test_companion_pr_matching_is_scan_driven(self):
        prs = [
            {"pr_number": 617, "title": "Add snapshot coverage", "url": "https://example/617", "state": "OPEN"},
            {"pr_number": 686, "title": "Follow-up snapshot test", "url": "https://example/686", "state": "MERGED"},
        ]
        sinks = [{"type": "eval_sink", "pr_number": 617, "title": "Snapshot test gap", "match_text": "snapshot"}]
        root_tracker.link_companion_prs(prs, sinks)
        companion = sinks[0]["companion_prs"]
        self.assertEqual([item["pr_number"] for item in companion], [686])
        self.assertEqual(companion[0]["match_reason"], "keyword:snapshot")
        rendered = root_tracker._workstream_markdown(
            {"root_id": "china-release", "name": "China Release"},
            "Repo & Skill Sinks", sinks,
        )
        self.assertIn("跨会话落地", rendered)
        self.assertIn("PR #686", rendered)

    def test_root_markdown_is_l0_scoreboard_only(self):
        snapshot_root = {
            "summary": {},
            "workstreams": {
                "PR & PoP": {"items": [
                    {"type": "pr", "pr_number": 1, "signal": "WAIT_POP_OR_MERGE"},
                    {"type": "pr", "pr_number": 2, "signal": "S3_CI_FAILURE"},
                    {"type": "pr", "pr_number": 3, "signal": "MERGED", "state": "MERGED"},
                ]},
                "Bugs & Devsync": {"items": [
                    {"type": "bug", "status": "FIXED", "title": "fixed"},
                    {"type": "bug", "status": "BLOCKING", "title": "blocked"},
                ]},
                "Repo & Skill Sinks": {"items": [{"type": "eval_sink", "id": "E1"}]},
            },
        }
        markdown = root_tracker.build_root_markdown(
            {"root_id": "china-release", "name": "China Release", "description": "Executive summary."},
            snapshot_root,
        )
        self.assertLessEqual(len(markdown.splitlines()), 15)
        self.assertIn("Milestone Readiness Scoreboard", markdown)
        self.assertIn("Current Milestone Blockers", markdown)
        self.assertIn("three sub-issues", markdown)
        self.assertNotIn("| PR | Signal", markdown)
        self.assertNotIn("| Bug ID | Status", markdown)
        self.assertNotIn("Repo & Skill Sinks（", markdown)

    def test_linear_boundary_refuses_non_linear_writer(self):
        class ForbiddenClient:
            boundary = "github"

            def upsert_root(self, record):
                raise AssertionError("must not be called")

        with self.assertRaises(root_tracker.OneWayBoundaryError):
            root_tracker.sync_linear(self.roots, client=ForbiddenClient(), offline=False)

    def test_dry_run_does_not_open_network_or_write_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            with mock.patch("root_tracker.urllib.request.urlopen", side_effect=AssertionError("network")):
                result = root_tracker.sync_linear(self.roots, cache_path=cache, dry_run=True, client=None)
            self.assertEqual(result["mode"], "dry-run")
            self.assertFalse(cache.exists())

    def test_load_teams_timeline_normalizes_milestones_context_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "teams.json"
            path.write_text(json.dumps({
                "release_version": "34.4",
                "timeline": [{"title": "Code Freeze", "targetDate": "2026-11-20", "dri": "Alice"}, {"name": "Release Date", "date": "2026-12-01"}],
                "highlights": [{"text": "QA exit depends on China sign-off", "owner": "Bob", "deepLink": "https://teams.example/context"}],
                "sources": [{"title": "China Release thread", "url": "https://teams.example/thread", "conversationId": "19:abc"}],
            }), encoding="utf-8")
            timeline = root_tracker.load_teams_timeline(path, persist=False)
        self.assertEqual(timeline["version"], "34.4")
        self.assertEqual(timeline["milestones"][0]["date"], "2026-11-20")
        self.assertEqual(timeline["target_date"], "2026-12-01")
        self.assertIn("QA exit", timeline["key_context"][0]["summary"])
        self.assertTrue(any(link["url"].endswith("thread") for link in timeline["deep_links"]))

    def test_unconfirmed_teams_timeline_does_not_update_linear_milestone(self):
        class FakeClient:
            boundary = "linear"
            def __init__(self): self.milestones = []
            def upsert_root(self, record): return {"id": "root"}
            def upsert_workstream(self, parent, record, workstream): return {"id": workstream["stream"]}
            def upsert_project_milestone(self, root_id, root_name, timeline): self.milestones.append(timeline); return {}
        with tempfile.TemporaryDirectory() as directory:
            timeline_path = Path(directory) / "teams.json"
            timeline_path.write_text(json.dumps({"target_date": "2026-12-01", "milestones": [{"name": "Release Date", "date": "2026-12-01"}]}), encoding="utf-8")
            client = FakeClient()
            result = root_tracker.sync_linear(self.roots, cache_path=Path(directory) / "cache.json", client=client, teams_timeline_path=timeline_path, snapshot_roots={"china-release": {"workstreams": {}}})
        self.assertEqual(client.milestones, [])
        self.assertNotIn("Release Timeline & Teams Context", result["roots"][0]["description"])

    def test_linear_client_project_milestone_update_writes_confirmed_fields(self):
        client = root_tracker.LinearClient("token")
        client._project_and_milestone = lambda root_id, root_name: ("project", "milestone")
        calls = []
        client._mutate = lambda mutation, variables: calls.append((mutation, variables)) or {"projectMilestoneUpdate": {"projectMilestone": {"id": "milestone"}}}
        result = client.upsert_project_milestone("china-release", "China Release", {"target_date": "2026-12-01", "version": "34.4", "milestones": [], "key_context": [], "deep_links": []})
        self.assertEqual(result["id"], "milestone")
        self.assertIn("projectMilestoneUpdate", calls[0][0])
        self.assertEqual(calls[0][1]["input"]["targetDate"], "2026-12-01")
        self.assertIn("Release/version: 34.4", calls[0][1]["input"]["description"])

    def test_confirmed_teams_timeline_updates_l0_and_project_milestone(self):
        class FakeClient:
            boundary = "linear"
            def __init__(self): self.milestones = []
            def upsert_root(self, record): return {"id": "root"}
            def upsert_workstream(self, parent, record, workstream): return {"id": workstream["stream"]}
            def upsert_project_milestone(self, root_id, root_name, timeline): self.milestones.append((root_id, root_name, timeline)); return {"id": "milestone"}
        with tempfile.TemporaryDirectory() as directory:
            timeline_path = Path(directory) / "teams.json"
            timeline_path.write_text(json.dumps({"confirmed": True, "targetDate": "2026-12-01", "milestones": [{"name": "Release Date", "date": "2026-12-01"}], "key_context": [{"summary": "QA exit confirmed"}], "deep_links": [{"title": "Teams", "url": "https://teams.example/thread"}]}), encoding="utf-8")
            client = FakeClient()
            result = root_tracker.sync_linear(self.roots, cache_path=Path(directory) / "cache.json", client=client, teams_timeline_path=timeline_path, snapshot_roots={"china-release": {"workstreams": {}}})
        self.assertEqual(len(client.milestones), 1)
        self.assertEqual(client.milestones[0][2]["target_date"], "2026-12-01")
        self.assertIn("Release Timeline & Teams Context", result["roots"][0]["description"])
        self.assertIn("QA exit confirmed", result["roots"][0]["description"])
        self.assertIn("teams.example/thread", result["roots"][0]["description"])
        self.assertLessEqual(len(result["roots"][0]["description"].splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
