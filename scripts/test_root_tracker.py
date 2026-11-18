import importlib.util
import json
import re
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

    def test_china_release_does_not_match_repository_only_local_query_pr(self):
        item = {"id": "PR#279", "repository": "san/bing-growth-sapphire-app", "title": "local query cleanup", "branch": "feature/local-query"}
        china = {"root_id": "china-release", "name": "China Release", "repos": ["san/bing-growth-sapphire-app"], "branch_globs": ["*china*", "*ab114*"], "handoff_files": ["*china-summary*"], "integration_branches": ["devsync1030"], "keywords": ["china", "bingchina", "san/bing-growth-sapphire-app"]}
        result = root_tracker.classify_jev(item, [china], jev_function=lambda *args, **kwargs: None)
        self.assertEqual(result["root_id"], "NEW_ROOT_CANDIDATE")

    def test_shared_repository_without_context_becomes_new_root_candidate(self):
        roots = [
            {"root_id": "china-release", "repos": ["san/bing-growth-sapphire-app"], "branch_globs": ["*china*"], "handoff_files": [], "bug_list_paths": [], "integration_branches": [], "keywords": ["china"]},
            {"root_id": "sapphire-notification", "repos": ["bing-growth-sapphire-app"], "branch_globs": ["*notification*"], "handoff_files": [], "bug_list_paths": [], "integration_branches": [], "keywords": ["notification"]},
            {"root_id": "sapphire-smartbanner", "repos": ["san/bing-growth-sapphire-app"], "branch_globs": ["*smartbanner*"], "handoff_files": [], "bug_list_paths": [], "integration_branches": [], "keywords": ["smartbanner"]},
        ]
        result = root_tracker.classify_jev(
            {"id": "PR#278", "repository": "san/bing-growth-sapphire-app", "title": "[Docs] Personalized query PRD, privacy boundary and design references", "branch": "docs/local-query-prd"},
            roots,
            jev_function=lambda *args, **kwargs: {"root_id": "sapphire-notification", "confidence": 1},
        )
        self.assertEqual(result["root_id"], "NEW_ROOT_CANDIDATE")

    def test_repo_relative_topic_map_survives_empty_home(self):
        repo_map = Path(root_tracker.SCRIPT_DIR).parents[1] / "personal-m2-assistant/references/m2-domain-map.json"
        self.assertTrue(repo_map.is_file())
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(root_tracker.Path, "home", return_value=Path(home)), mock.patch.object(
                root_tracker, "DEFAULT_TOPIC_MAPS", (repo_map, Path(home) / "missing.json")
            ):
                topics = root_tracker._load_topic_map()
        self.assertGreater(len(topics), 0)
        self.assertTrue(any(topic.get("topic_id") == "sapphire-discovery-kumo" for topic in topics))

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
            snapshot = root_tracker.collect(roots_path=roots, summary_path=summary, devsync_path=devsync, worktrees_path=worktrees, supervisor_path=supervisor, eval_path=evals, latest_discovery_path=base / "missing-latest.json", aidash_eval_path=base / "missing-aidash.json", teams_timeline_path=base / "teams-timeline.json", canonical_rules=[])
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
            snapshot = root_tracker.collect(roots_path=roots, supervisor_path=supervisor, eval_path=eval_path, latest_discovery_path=latest, aidash_eval_path=aidash, summary_path=base / "missing-summary.md", devsync_path=base / "missing-devsync", worktrees_path=base / "missing-worktrees", teams_timeline_path=base / "teams-timeline.json", canonical_rules=[])
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

    def test_pr_workstream_uses_concise_sections_and_agent_state(self):
        root = {"root_id": "china-release", "name": "China Release"}
        items = [
            {
                "type": "pr", "pr_number": 580, "signal": "WAIT_POP_OR_MERGE",
                "title": "PoP gate", "head_sha": "pop-head",
                "pending_decisions": [{"kind": "owner_action", "status": "awaiting_owner", "decision_id": "D580", "linear_identifier": "TIA-350", "url": "https://linear.app/TIA-350"}],
            },
            {"type": "pr", "pr_number": 617, "signal": "OBSERVE", "state": "OPEN", "isDraft": True,
             "title": "Waiting approval", "head_sha": "wait-head", "pending_decisions": [{"kind": "owner_decision", "status": "completed", "linear_identifier": "TIA-347"}]},
            {"type": "pr", "pr_number": 620, "signal": "S3_CI_FAILURE", "head_sha": "ci-head", "unresolved_count": 2, "title": "CI repair"},
            {"type": "pr", "pr_number": 686, "signal": "MERGED", "state": "MERGED", "title": "Landed"},
        ]
        markdown = root_tracker._workstream_markdown(root, "PR & PoP", items)
        self.assertIn("> 🔴 **待你操作 1**（PoP **1** · 决策 **0**）", markdown)
        self.assertIn("### 🔴 待你操作 (1)", markdown)
        self.assertIn("### ⏳ 在途 PR (2)", markdown)
        self.assertIn("### ✅ 已合入 (1)", markdown)
        self.assertIn("待点 PoP 合入", markdown)
        self.assertIn("评论已清零，等 Review", markdown)
        self.assertIn("必需 CI 失败/取消（待恢复/重跑）", markdown)
        self.assertIn("DRAFT", markdown)
        self.assertIn("已修完 Resolve: TIA-347", markdown)
        self.assertNotIn("AUTO_HEAL_", markdown.split("<!-- AGENT-PR-STATE:v1", 1)[0])
        self.assertNotIn("S3_CI_FAILURE", markdown.split("<!-- AGENT-PR-STATE:v1", 1)[0])
        self.assertNotIn("等 Review Approve / CI", markdown)
        state = json.loads(re.search(r"<!-- AGENT-PR-STATE:v1 (.+) -->", markdown).group(1))
        self.assertEqual(state["root_id"], "china-release")
        self.assertEqual(state["pop_ready"], [{"pr": 580, "head_sha": "pop-head", "decision_id": "D580"}])
        self.assertEqual(state["in_flight"][1]["stage_code"], "S3_CI_FAILURE")
        self.assertEqual(state["merged"], [686])

    def test_china_release_closed_vs_merged_required_checks_on_hold_and_unassigned_bugs(self):
        bugs = [
            {"id": "AB#11453356", "type": "bug", "raw_status": "REMOTE CONFIG", "status": "REMOTE CONFIG", "pr_number": 544, "pr_closed": True, "ship_blocker": True, "assigned_to": "Tianpei Li"},
            {"id": "AB#11452501", "type": "bug", "raw_status": "ON HOLD", "status": "ON HOLD", "pr_number": 566, "pr_closed": False, "ship_blocker": True, "assigned_to": "Tianpei Li", "owner_domain": "#566 (Draft)", "title": "App Rating", "resolution": "Draft PR #566 保持挂起"},
            {"id": "AB#11440469", "type": "bug", "raw_status": "BLOCKING", "status": "EXTERNAL (外部法务网页)", "pr_number": None, "pr_closed": False, "ship_blocker": False, "assigned_to": "", "owner_domain": "外部法务网页", "title": "第三方服务说明英文", "resolution": "微软服务端未提供中文页面"},
        ]
        config = {
            "watched_prs": [
                {"key": "san/bing-growth-sapphire-app#500", "pr_number": 500, "repository": "san/bing-growth-sapphire-app", "enabled": True},
                {"key": "san/bing-growth-sapphire-app#544", "pr_number": 544, "repository": "san/bing-growth-sapphire-app", "enabled": True},
                {"key": "san/bing-growth-sapphire-app#566", "pr_number": 566, "repository": "san/bing-growth-sapphire-app", "enabled": True, "isDraft": True},
                {"key": "san/bing-growth-sapphire-app#619", "pr_number": 619, "repository": "san/bing-growth-sapphire-app", "enabled": True},
                {"key": "san/bing-growth-sapphire-app#545", "pr_number": 545, "repository": "san/bing-growth-sapphire-app", "enabled": True},
            ],
            "pending_decisions": {},
        }
        discovery = {
            "overview": [
                {"key": "san/bing-growth-sapphire-app#566", "number": 566, "state": "OPEN", "isDraft": True, "signals": ["s1_conflict"]},
                {
                    "key": "san/bing-growth-sapphire-app#619",
                    "number": 619,
                    "state": "OPEN",
                    "signals": ["s3_failed_checks"],
                    "statusCheckRollup": [
                        {"name": "[Backend][VoiceSearch] gh-code-ci", "conclusion": "CANCELLED"},
                        {"name": "[iOS][Gated] PR Validation (GHE)", "conclusion": "SUCCESS", "isRequired": True},
                    ],
                },
                {
                    "key": "san/bing-growth-sapphire-app#545",
                    "number": 545,
                    "state": "OPEN",
                    "signals": ["s3_failed_checks"],
                    "statusCheckRollup": [
                        {"name": "[Proxima][Mini App] Build", "conclusion": "CANCELLED", "isRequired": True},
                    ],
                },
            ]
        }
        prs = root_tracker.collect_prs(config, bugs, discovery)
        by_num = {item["pr_number"]: item for item in prs}
        self.assertNotIn(544, by_num)
        self.assertEqual(by_num[500]["signal"], "MERGED")
        self.assertEqual(by_num[566]["signal"], "ON_HOLD")
        self.assertEqual(by_num[619]["signal"], "OBSERVE")
        self.assertEqual(by_num[545]["signal"], "AUTO_HEAL_S3_CI_FAILURE")

        pr_md = root_tracker._workstream_markdown({"root_id": "china-release"}, "PR & PoP", prs)
        self.assertIn("> 🟡 **待你推进 / 裁决 2**（必需 CI 待恢复/重跑 **1** · ON HOLD 待裁决 **1** · 待催审 **1**）", pr_md)
        self.assertNotIn("无需你操作", pr_md)
        self.assertIn("ON HOLD（待裁决本版豁免或解封）", pr_md)
        self.assertIn("必需 CI 失败/取消（待恢复/重跑）", pr_md)

        bug_md = root_tracker._workstream_markdown({"root_id": "china-release"}, "Bugs & Devsync", bugs)
        self.assertIn("需二选一裁决：摘除 Ship Blocker 本版豁免 vs 解除 #566 Hold", bug_md)
        self.assertIn("待指派责任人/定本版取舍", bug_md)

    def test_pr_workstream_serializes_decision_metadata(self):
        markdown = root_tracker._workstream_markdown(
            {"root_id": "china-release"},
            "PR & PoP",
            [{
                "type": "decision", "pr_number": 617, "title": "Choose A or B", "head_sha": "head617",
                "decision_id": "D617", "pending_decisions": [{
                    "kind": "owner_decision", "status": "awaiting_owner", "decision_id": "D617",
                    "linear_identifier": "TIA-347", "url": "https://linear.app/tianpeili/issue/TIA-347",
                }],
            }],
        )
        payload = json.loads(re.search(r"<!-- AGENT-PR-STATE:v1 (.+) -->", markdown).group(1))
        self.assertEqual(payload["decisions"], [{"pr": 617, "decision_id": "D617", "head_sha": "head617"}])
        self.assertIn("待拍板回复 A/B", markdown)

    def test_pr_workstream_omits_empty_action_sections_and_placeholders(self):
        items = [
            {"type": "pr", "pr_number": 630, "signal": "OBSERVE", "state": "OPEN", "title": "In flight", "head_sha": "h630"},
            *({"type": "pr", "pr_number": number, "signal": "MERGED", "state": "MERGED", "title": f"Merged {number}"} for number in (358, 392, 520, 542)),
        ]
        markdown = root_tracker._workstream_markdown({"root_id": "sapphire-notification"}, "PR & PoP", items)
        self.assertIn("> 🟢 **无需你操作** · 在途 **1** · 已合入 **4**", markdown)
        self.assertIn("### ⏳ 在途 PR (1)", markdown)
        self.assertIn("### ✅ 已合入 (4)", markdown)
        self.assertNotIn("### 🔴 待你操作", markdown)
        self.assertNotIn("No active", markdown)

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
                    {"type": "pr", "pr_number": 1, "signal": "WAIT_POP_OR_MERGE", "head_sha": "deadbeef1234"},
                    {"type": "pr", "pr_number": 2, "signal": "S3_CI_FAILURE", "head_sha": "cafebabe5678"},
                    {"type": "pr", "pr_number": 3, "signal": "MERGED", "state": "MERGED", "head_sha": "feedface9012"},
                ]},
                "Bugs & Devsync": {"items": [
                    {"type": "bug", "status": "FIXED", "title": "fixed"},
                    {"type": "bug", "status": "BLOCKING", "title": "blocked"},
                ]},
                "Repo & Skill Sinks": {"items": [{"type": "eval_sink", "id": "E1"}]},
            },
        }
        root_def = {
            "root_id": "china-release",
            "name": "China Release",
            "description": "Executive summary.",
            "repos": ["san/bing-growth-sapphire-app"],
            "integration_branches": ["devsync1030"],
        }
        ws_links = {
            "PR & PoP": {"id": "u1", "identifier": "TIA-390", "url": "https://linear.app/TIA-390"},
            "Bugs & Devsync": {"id": "u2", "identifier": "TIA-391", "url": "https://linear.app/TIA-391"},
            "Repo & Skill Sinks": {"id": "u3", "identifier": "TIA-392", "url": "https://linear.app/TIA-392"},
        }
        markdown = root_tracker.build_root_markdown(root_def, snapshot_root, workstream_links=ws_links)
        self.assertLessEqual(len(markdown.splitlines()), 35)
        layer1_idx = markdown.index("## ⚡ TL;DR & 下一步需要你做的")
        layer2_idx = markdown.index("## 📋 总体信息")
        layer3_idx = markdown.index("<details><summary>🤖 AI 知道的信息</summary>")
        self.assertLess(layer1_idx, layer2_idx)
        self.assertLess(layer2_idx, layer3_idx)
        self.assertTrue(markdown.endswith("</details>"))

        human_visible = markdown[:layer3_idx]
        for forbidden in ("deadbeef1234", "cafebabe5678", "WAIT_POP_OR_MERGE", "S3_CI_FAILURE", "AUTO_HEAL_", "OBSERVE", "<!-- ROOT-MANIFEST:v1"):
            self.assertNotIn(forbidden, human_visible)

        self.assertIn("Milestone Readiness Scoreboard", markdown)
        self.assertIn("| 维度 | 当前进度 |", markdown)
        self.assertIn("| --- | --- |", markdown)
        self.assertIn("Current Milestone Blockers", markdown)
        self.assertIn("three sub-issues", markdown)
        self.assertLess(markdown.index("Milestone Readiness Scoreboard"), markdown.index("Current Milestone Blockers"))
        self.assertNotIn("| PR | Signal", markdown)
        self.assertNotIn("| Bug ID | Status", markdown)
        self.assertNotIn("Repo & Skill Sinks（", markdown)

        meta = root_tracker.parse_root_meta(markdown)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["root_id"], "china-release")
        self.assertEqual(meta["workstream_issues"]["PR & PoP"]["identifier"], "TIA-390")
        self.assertEqual(len(meta["pr_heads"]), 3)
        manifest = root_tracker.parse_manifest(markdown)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["root_id"], "china-release")
        self.assertEqual(
            markdown,
            root_tracker.build_root_markdown(root_def, snapshot_root, workstream_links=ws_links),
        )

    def test_non_china_root_has_no_china_non_pr_text_and_shows_no_action_needed(self):
        snapshot_root = {
            "workstreams": {
                "PR & PoP": {"items": [
                    {"type": "pr", "pr_number": 580, "signal": "MERGED", "state": "MERGED", "head_sha": "abc123456"}
                ]},
                "Bugs & Devsync": {"items": []},
                "Repo & Skill Sinks": {"items": []},
            }
        }
        markdown = root_tracker.build_root_markdown(
            {"root_id": "sapphire-notification", "name": "Sapphire Notification", "description": "Notification."},
            snapshot_root,
        )
        self.assertIn("## ⚡ TL;DR & 下一步需要你做的", markdown)
        self.assertIn("当前无非 PR 可修阻塞或外部配置卡点（0 条）", markdown)
        self.assertIn("- 当前无需你立即操作", markdown)
        self.assertNotIn("start-china", markdown)
        self.assertNotIn("11453356", markdown)
        self.assertNotIn("abc123456", markdown.split("<details><summary>🤖 AI 知道的信息</summary>", 1)[0])

    def test_linear_boundary_refuses_non_linear_writer(self):
        class ForbiddenClient:
            boundary = "github"

            def upsert_root(self, record):
                raise AssertionError("must not be called")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(root_tracker.OneWayBoundaryError):
                root_tracker.sync_linear(self.roots, client=ForbiddenClient(), offline=False, teams_timeline_path=Path(directory) / "teams.json")

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

    def test_linear_client_finds_legacy_pr_title_and_updates_new_title(self):
        client = root_tracker.LinearClient("token")
        client._team = lambda: "team-id"
        calls = []
        with mock.patch.object(root_tracker, "_linear_query", side_effect=[
            {"issues": {"nodes": []}},
            {"issues": {"nodes": [{"id": "issue-id", "identifier": "TIA-394", "title": "[Sapphire Notification · PR & PoP] legacy", "parent": {"id": "parent-id"}}]}},
        ]):
            found = client._find_issue("[Sapphire Notification] PR 进度与待办", "parent-id")
        self.assertEqual(found["identifier"], "TIA-394")

        client._project_and_milestone = lambda root_id, root_name: (None, None)
        client.active_tia83_for_root = lambda root_id: []
        client._find_issue = lambda title, parent_id: {"id": "issue-id", "description": "old"}
        client._mutate = lambda mutation, variables: calls.append(variables) or {"issueUpdate": {"issue": {"id": "issue-id"}}}
        client.upsert_workstream(
            {"id": "parent-id"},
            {"root_id": "sapphire-notification", "name": "Sapphire Notification"},
            {"stream": "PR & PoP", "title": "[Sapphire Notification] PR 进度与待办", "description": "new", "items": []},
        )
        self.assertEqual(calls[0]["input"]["title"], "[Sapphire Notification] PR 进度与待办")

    def test_linear_client_deduplicates_duplicate_workstream_children(self):
        client = root_tracker.LinearClient("token")
        mutations = []
        with mock.patch.object(root_tracker, "_linear_query", return_value={
            "issue": {
                "children": {
                    "nodes": [
                        {"id": "dup-id", "identifier": "TIA-459", "title": "[China Release · PR & PoP] legacy", "createdAt": "2026-11-16T01:03:52Z", "state": {"type": "backlog"}},
                        {"id": "canon-id", "identifier": "TIA-390", "title": "[China Release] PR 进度与待办", "createdAt": "2026-11-13T03:21:31Z", "state": {"type": "unstarted"}},
                    ]
                }
            }
        }):
            client._mutate = lambda mutation, variables: mutations.append(variables) or {"issueUpdate": {"success": True}}
            canonical = client.deduplicate_root_children({"id": "parent-id"}, "China Release")
        self.assertEqual(canonical["PR & PoP"]["identifier"], "TIA-390")
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0]["id"], "dup-id")
        self.assertIsNone(mutations[0]["input"]["parentId"])
        self.assertEqual(mutations[0]["input"]["stateId"], client.CANCELED_ID)

    def test_parse_manifest_and_root_meta_unescape_linear_markdown(self):
        escaped = (
            '<!-- ROOT-META:v1 {"pr_heads":\\[{"head_sha":"abc","pr":1,"signal":"MERGED","state":"MERGED"}\\],'
            '"root_id":"china-release","signal_sources":{"branches":\\["*china*"\\]}} -->\n'
            '<!-- ROOT-MANIFEST:v1 {"branch_globs": \\["*china*"\\], "root_id": "china-release"} -->'
        )
        meta = root_tracker.parse_root_meta(escaped)
        manifest = root_tracker.parse_manifest(escaped)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["root_id"], "china-release")
        self.assertEqual(meta["pr_heads"][0]["pr"], 1)
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["branch_globs"], ["*china*"])

    def test_linear_client_refreshes_live_action_children_in_state_block(self):
        client = root_tracker.LinearClient("token")
        client._project_and_milestone = lambda root_id, root_name: (None, None)
        client.active_tia83_for_root = lambda root_id: [{
            "id": "child-id", "identifier": "TIA-999", "url": "https://linear.app/tianpeili/issue/TIA-999",
            "title": "[PR Action] #630 Ready — Complete PoP / Merge (abc1234) — D630",
            "description": "## PR Owner Action — PoP / Merge Gate\n**Head:** `abc1234`\n<!-- [DECISION:D630] -->",
            "state": {"name": "Todo"},
        }]
        client._find_issue = lambda title, parent_id: {"id": "issue-id"}
        calls = []
        client._mutate = lambda mutation, variables: calls.append(variables) or {"issueUpdate": {"issue": {"id": "issue-id"}}}
        client.upsert_workstream(
            {"id": "parent-id"},
            {"root_id": "sapphire-notification", "name": "Sapphire Notification"},
            {"stream": "PR & PoP", "title": "[Sapphire Notification] PR 进度与待办", "description": "old", "items": []},
        )
        description = calls[0]["input"]["description"]
        self.assertIn("### 🔴 待你操作 (1)", description)
        self.assertIn('"head_sha":"abc1234"', description)
        self.assertIn('"decision_id":"D630"', description)

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
        self.assertLessEqual(len(result["roots"][0]["description"].splitlines()), 55)


if __name__ == "__main__":
    unittest.main()
