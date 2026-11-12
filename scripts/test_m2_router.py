import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "skills" / "m2-dispatcher" / "scripts" / "m2-router.py"
CANONICAL_ROUTER_PATH = ROOT / "skills" / "personal-m2-assistant" / "scripts" / "m2-router.py"


def load_router(path=ROUTER_PATH):
    spec = importlib.util.spec_from_file_location("m2_router", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class M2RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = load_router()

    def test_canonical_router_is_source_and_legacy_path_delegates(self):
        canonical = load_router(CANONICAL_ROUTER_PATH)
        self.assertEqual(self.router.SCHEMA_VERSION, canonical.SCHEMA_VERSION)
        self.assertEqual(self.router.DEFAULT_DOMAIN_MAP_PATH, canonical.DEFAULT_DOMAIN_MAP_PATH)
        self.assertIn("personal-m2-assistant", ROUTER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(self.router.load_domain_map()["path"], canonical.load_domain_map()["path"])

    def test_discovery_normalizes_herdr_and_dsh_and_ignores_terminal_agents(self):
        herdr, warnings = self.router.discover_herdr(payload={"result": {"agents": [
            {"name": "repo-a-m2-r1", "agent_status": "working", "repo": "repo-a", "pane_id": "w1:p1"},
            {"name": "repo-a-goal-tl-r1", "agent_status": "working", "repo": "repo-a"},
            {"name": "repo-b-m2-r1", "agent_status": "done", "repo": "repo-b"},
        ]}})
        dsh, _ = self.router.discover_dsh(payload={"sessions": [
            {"sessionId": "dsh-a", "title": "[M2] Repo A", "goal": "repo-a", "status": "idle", "isDshSession": True},
        ]})
        self.assertEqual(warnings, None)
        self.assertEqual([item["session_id"] for item in herdr], ["repo-a-m2-r1"])
        self.assertEqual(herdr[0]["target_repo"], "repo-a")
        self.assertEqual(dsh[0]["source"], "dsh")

    def test_discovery_merges_panes_and_agents_and_excludes_background_worktrees(self):
        herdr, warning = self.router.discover_herdr(
            pane_payload={"panes": [
                {"pane_id": "w27:p1", "cwd": "/tmp/FlightBook", "agent": "copilot", "status": "working", "title": "FlightBook"},
                {"pane_id": "w9:p2", "cwd": "/tmp/worktrees/task", "agent": "copilot", "status": "working", "title": "task"},
                {"pane_id": "w10:p1", "cwd": "/tmp/repo", "agent": "copilot", "status": "working", "name": "repo-goal-tl-r1"},
            ]},
            agent_payload={"agents": [
                {"pane_id": "w27:p1", "name": "FlightBook", "agent_status": "working"},
                {"pane_id": "w55:p1", "cwd": "/tmp/AIDash", "agent": "claude", "agent_status": "idle", "name": "AIDash"},
            ]},
        )
        self.assertIsNone(warning)
        self.assertEqual({item["target_repo"] for item in herdr}, {"FlightBook", "AIDash"})
        self.assertEqual(herdr[0]["source"], "herdr")

    def test_deterministic_fallback_splits_sequential_and_parallel_targets(self):
        sessions = [
            {"session_id": "shared", "target_repo": "shared-ci", "title": "shared-ci", "status": "working", "cwd": "/repo/shared-ci", "source": "herdr", "herdr_target": "shared-ci", "pane_id": "w1:p1", "workspace_id": "w1"},
            {"session_id": "dash", "target_repo": "AIDash", "title": "AIDash", "status": "working", "cwd": "/repo/AIDash", "source": "herdr", "herdr_target": "AIDash", "pane_id": "w2:p1", "workspace_id": "w2"},
            {"session_id": "sapphire", "target_repo": "bing-growth-sapphire-app", "title": "Sapphire", "status": "working", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "Sapphire", "pane_id": "w3:p1", "workspace_id": "w3"},
        ]
        plan = self.router.build_plan(
            "先让 shared-ci 更新设计门禁，再让 AIDash 和 bing-growth-sapphire-app 同步接入",
            sessions,
            mock_jev={},
        )
        self.assertEqual([(item["target_repo"], item["wave_order"]) for item in plan["targets"]], [
            ("shared-ci", 1), ("AIDash", 2), ("bing-growth-sapphire-app", 2),
        ])
        self.assertTrue(all(item["m2_status"] == "EXISTING_M2" for item in plan["targets"]))

    def test_falcon_boolean_noul_falls_back_to_deterministic_multi_target_plan(self):
        sessions = [
            {"session_id": "dash", "target_repo": "AIDash", "title": "AIDash", "status": "working", "cwd": "/repo/AIDash", "source": "herdr", "herdr_target": "AIDash", "pane_id": "w2:p1", "workspace_id": "w2"},
            {"session_id": "sapphire", "target_repo": "bing-growth-sapphire-app", "title": "Sapphire", "status": "working", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "Sapphire", "pane_id": "w3:p1", "workspace_id": "w3"},
        ]
        plan = self.router.build_plan(
            "先让 shared-ci 更新门禁，再让 AIDash 和 Sapphire 同步接入",
            sessions,
            mock_jev={"answers": {"route_mode": {"choice": "ROUTE_SINGLE"}, "targets": {"noul": True}}},
        )
        self.assertEqual(plan["decision"]["route"], "ROUTE_CROSS_SESSION")
        self.assertEqual(plan["decision"]["source"], "deterministic_fallback")
        self.assertEqual(
            [(item["target_repo"], item["wave_order"], item["m2_status"]) for item in plan["targets"]],
            [("shared-ci", 1, "NEW_M2"), ("AIDash", 2, "EXISTING_M2"), ("bing-growth-sapphire-app", 2, "EXISTING_M2")],
        )

    def test_plan_contains_owner_gate_status_and_jev_multi_target_contract(self):
        sessions, warnings = self.router.discover_sessions(
            herdr_payload={"agents": [{"name": "alpha-m2-r1", "agent_status": "working", "repo": "alpha"}]},
            dsh_payload={"sessions": [{"sessionId": "dsh-beta", "title": "[M2] Beta", "status": "idle", "target_repo": "beta"}]},
        )
        plan = self.router.build_plan(
            "同步 alpha 的结论到 beta，并保留上游上下文",
            sessions,
            warnings,
            mock_jev={"model": "test-jev", "answers": {
                "route_mode": {"choice": "ROUTE_CROSS_SESSION", "confidence": 0.94},
                "targets": [
                    {"target_repo": "alpha", "sub_prompt": "整理 alpha 结论", "wave_order": 1, "rag_context_hint": "alpha-summary"},
                    {"target_repo": "beta", "sub_prompt": "接收 alpha 结论", "wave_order": 2},
                ],
            }},
        )
        self.assertEqual(plan["schema_version"], 1)
        self.assertFalse(plan["approval"]["approved"])
        self.assertEqual(plan["decision"]["route"], "ROUTE_CROSS_SESSION")
        self.assertEqual([item["m2_status"] for item in plan["targets"]], ["EXISTING_M2", "EXISTING_M2"])
        self.assertEqual(plan["waves"], [1, 2])
        self.assertEqual(plan["targets"][0]["rag_context_hint"], "alpha-summary")

    def test_magpie_mock_semantically_rewrites_and_binds_targets(self):
        sessions = [{
            "session_id": "dash-session",
            "target_repo": "AIDash",
            "title": "AIDash",
            "status": "working",
            "cwd": "/repo/AIDash",
            "source": "herdr",
            "herdr_target": "AIDash",
            "pane_id": "w2:p1",
            "workspace_id": "w2",
        }]
        plan = self.router.build_plan(
            "把指标同步到 AIDash",
            sessions,
            mock_magpie={"route": "ROUTE_SINGLE", "reason": "明确命中活跃会话", "targets": [{
                "target_repo": "AIDash",
                "session_id": "dash-session",
                "sub_prompt": "在 AIDash 中接入该指标并验证卡片展示",
                "wave_order": 1,
                "rag_context_hint": None,
            }]},
        )
        self.assertEqual(plan["decision"]["source"], "mock-magpie")
        self.assertEqual(plan["decision"]["reason"], "明确命中活跃会话")
        self.assertEqual(plan["targets"][0]["m2_status"], "EXISTING_M2")
        self.assertEqual(plan["targets"][0]["session_id"], "dash-session")
        self.assertIn("接入该指标", plan["targets"][0]["sub_prompt"])

    def test_targets_json_skips_magpie_request(self):
        with patch.object(self.router, "_request_magpie", side_effect=AssertionError("Magpie must be skipped")):
            plan = self.router.build_plan(
                "route this request",
                [],
                targets_json=[{"target_repo": "alpha", "sub_prompt": "inspect alpha", "wave_order": 1}],
            )
        self.assertEqual(plan["decision"]["source"], "targets-json")
        self.assertEqual(plan["targets"][0]["target_repo"], "alpha")
        self.assertEqual(plan["targets"][0]["m2_status"], "NEW_M2")

    def test_magpie_request_posts_one_step_contract(self):
        calls = []

        def poster(url, payload, **kwargs):
            calls.append((url, payload, kwargs))
            return {"choices": [{"message": {"content": json.dumps({
                "route": "ROUTE_SINGLE",
                "reason": "one active session",
                "targets": [{"target_repo": "alpha", "session_id": "alpha-session", "sub_prompt": "inspect alpha", "wave_order": 1, "rag_context_hint": None}],
            })}}]}

        with patch.object(self.router, "_post_json", side_effect=poster):
            decision = self.router._request_magpie(
                "inspect alpha",
                [{"session_id": "alpha-session", "target_repo": "alpha"}],
                base_url="http://127.0.0.1:3425/",
                model="group/steering",
            )
        self.assertEqual(calls[0][0], "http://127.0.0.1:3425/v1/chat/completions")
        self.assertEqual(calls[0][1]["model"], "group/steering")
        request_input = json.loads(calls[0][1]["messages"][1]["content"])
        self.assertEqual(request_input["user_message"], "inspect alpha")
        self.assertEqual(request_input["active_m2_sessions"][0]["session_id"], "alpha-session")
        self.assertEqual(decision["targets"][0]["sub_prompt"], "inspect alpha")

    def test_new_target_is_marked_new_without_dispatch_side_effects(self):
        plan = self.router.build_plan(
            "新建一个 M2 处理 gamma 发布任务",
            [],
            mock_jev={"answers": {"route_mode": {"choice": "CREATE_NEW_M2"}, "targets": [{"target_repo": "gamma", "sub_prompt": "处理发布任务"}]}},
            transport="stdout",
        )
        self.assertEqual(plan["targets"][0]["m2_status"], "NEW_M2")
        self.assertEqual(plan["targets"][0]["transport"], "stdout")
        self.assertIn("Owner approval required", self.router.preview_text(plan))

    def test_apply_requires_explicit_owner_approval_and_preserves_wave_order(self):
        plan = self.router.build_plan(
            "send work",
            [],
            mock_jev={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"target_repo": "second", "sub_prompt": "second prompt", "wave_order": 2},
                {"target_repo": "first", "sub_prompt": "first prompt", "wave_order": 1},
            ]},
            transport="stdout",
        )
        with self.assertRaisesRegex(self.router.RouterError, "approval"):
            self.router.apply_plan(plan)
        result = self.router.apply_plan(plan, approve=True)
        self.assertEqual([receipt["target_repo"] for receipt in result["receipts"]], ["first", "second"])
        self.assertEqual(result["receipts"][0]["method"], "stdout")

    def test_apply_existing_herdr_uses_public_agent_prompt(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-test",
            "approval": {"approved": False},
            "targets": [{
                "target_repo": "alpha", "m2_status": "EXISTING_M2", "transport": "herdr",
                "herdr_target": "alpha-m2-r1", "sub_prompt": "continue alpha", "wave_order": 1,
            }],
        }
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

        result = self.router.apply_plan(plan, approve=True, runner=runner)
        self.assertEqual(result["receipts"][0]["method"], "herdr.agent.prompt")
        self.assertEqual(calls[0][1:3], ["agent", "prompt"])
        self.assertEqual(calls[0][3:], ["alpha-m2-r1", "continue alpha"])

    def test_dsh_existing_delivery_falls_back_to_omni_dispatch(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-test",
            "approval": {"approved": False},
            "targets": [{
                "target_repo": "beta", "m2_status": "EXISTING_M2", "transport": "dsh",
                "source": "dsh", "session_id": "dsh-beta", "base_url": "http://dsh.test",
                "sub_prompt": "continue beta", "wave_order": 1,
            }],
        }
        calls = []

        def poster(url, payload, **kwargs):
            calls.append((url, payload))
            if url.endswith("/dsh-session-sink/api/deliver"):
                raise self.router.RouterError("sink unavailable")
            return {"ok": True, "fallback": True}

        result = self.router.apply_plan(plan, approve=True, poster=poster)
        self.assertEqual(result["receipts"][0]["method"], "dsh.omni-hub.dispatch")
        self.assertTrue(calls[-1][0].endswith("/m2-omni-hub/api/dispatch"))

    def test_domain_map_has_four_groups_and_23_topics(self):
        domain_map = self.router.load_domain_map()
        self.assertEqual(len(domain_map["groups"]), 4)
        self.assertEqual(len(domain_map["topics"]), 23)
        self.assertNotIn("app-ios", {topic["topic_id"] for topic in domain_map["topics"]})
        self.assertEqual(domain_map["topics"][0]["topic_id"], "sapphire-notification")

    def test_legacy_domain_map_matches_canonical_domain_map(self):
        legacy_path = ROOT / "skills" / "m2-dispatcher" / "references" / "m2-domain-map.json"
        canonical_path = ROOT / "skills" / "personal-m2-assistant" / "references" / "m2-domain-map.json"
        self.assertEqual(
            json.loads(legacy_path.read_text(encoding="utf-8")),
            json.loads(canonical_path.read_text(encoding="utf-8")),
        )

    def test_topic_routing_distinguishes_same_repo_business_panes(self):
        sessions = [
            {"session_id": "notification", "target_repo": "bing-growth-sapphire-app", "title": "通知开关 M2", "summary": "气象单位设置", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "notification", "pane_id": "w1:p1", "workspace_id": "w1"},
            {"session_id": "china", "target_repo": "bing-growth-sapphire-app", "title": "中国区 bug M2", "summary": "反馈列表", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "china", "pane_id": "w1:p2", "workspace_id": "w1"},
        ]
        plan = self.router.build_plan(
            "同步 Notification Center 和 China Bugs 的状态",
            sessions,
            mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"target_repo": "bing-growth-sapphire-app", "sub_prompt": "处理 Push 通知开关和气象单位", "wave_order": 1},
                {"target_repo": "bing-growth-sapphire-app", "sub_prompt": "整理 China Bugs devsync Bug List", "wave_order": 1},
            ]},
        )
        self.assertEqual([target["topic_id"] for target in plan["targets"]], ["sapphire-notification", "sapphire-china-bugs"])
        self.assertEqual([target["domain_status"] for target in plan["targets"]], ["MAPPED_DOMAIN", "MAPPED_DOMAIN"])
        self.assertEqual([target["session_id"] for target in plan["targets"]], ["notification", "china"])

    def test_topic_map_covers_webiq_and_personal_product(self):
        webiq = self.router.build_plan("更新 WebIQSearch Query Lens", [], mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "WebIQSearch", "sub_prompt": "更新 Query Lens", "wave_order": 1}]})
        voxpocket = self.router.build_plan("修复 VoxPocket 语音输入", [], mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "VoxPocket", "sub_prompt": "修复语音输入", "wave_order": 1}]})
        self.assertEqual(webiq["targets"][0]["topic_id"], "webiq-search")
        self.assertEqual(webiq["targets"][0]["domain_status"], "MAPPED_DOMAIN")
        self.assertEqual(voxpocket["targets"][0]["topic_id"], "voxpocket")

    def test_unmapped_topic_is_previewed_and_can_be_registered_after_approval(self):
        plan = self.router.build_plan(
            "为新量化研究工具建立独立 M2",
            [],
            mock_jev={"answers": {"route_mode": {"choice": "CREATE_NEW_M2"}, "primary_topic": {"choice": "NEW_UNMAPPED_DOMAIN"}, "has_unmapped_domain": {"noul": True}}},
            mock_magpie={"route": "CREATE_NEW_M2", "targets": [{"target_repo": "QuantLab", "sub_prompt": "建立量化研究工具 M2", "wave_order": 1, "dispatch_script": "/tmp/fake-herdr-dispatch"}]},
        )
        self.assertEqual(plan["targets"][0]["domain_status"], "NEW_DOMAIN_CANDIDATE")
        self.assertTrue(plan["new_domain_candidates"])
        preview = self.router.preview_text(plan)
        self.assertIn("topic_id", preview)
        self.assertIn("NEW_DOMAIN_CANDIDATE", preview)
        with tempfile.TemporaryDirectory() as temp:
            map_path = Path(temp) / "map.json"
            map_path.write_text((ROOT / "skills/m2-dispatcher/references/m2-domain-map.json").read_text(), encoding="utf-8")
            result = self.router.apply_plan(
                plan,
                approve=True,
                register_new_domains=True,
                domain_map_file=map_path,
                runner=lambda args, **kwargs: SimpleNamespace(returncode=0, stdout='{"ok": true, "reused_existing": false}', stderr=""),
            )
            self.assertEqual(result["registered_new_domains"], [plan["targets"][0]["topic_id"]])
            registered = self.router.load_domain_map(map_path)
            new_domain_map = json.loads(map_path.read_text(encoding="utf-8"))
            self.assertEqual(len(registered["topics"]), 24)
            new_topic = next(topic for topic in registered["topics"] if topic["topic_id"] == plan["targets"][0]["topic_id"])
            self.assertTrue(len(new_topic["keywords"]) > 0)
            self.assertEqual(len(new_domain_map["groups"]), 5)

    def test_learning_and_blog_replaces_personal_digest_topic(self):
        plan = self.router.build_plan(
            "学习量子计算并把结果写成技术博客，归档到 knowledge-vault",
            [],
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "Personal", "sub_prompt": "学习并写博客", "wave_order": 1}]},
        )
        self.assertEqual(plan["targets"][0]["topic_id"], "learning-and-blog")
        self.assertEqual(plan["targets"][0]["domain_status"], "MAPPED_DOMAIN")

    def _learning_sessions(self):
        return [
            {"session_id": "learn-old", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客", "status": "idle", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-old", "pane_id": "w2:p1", "workspace_id": "w2"},
            {"session_id": "learn-duplicate", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客 (old)", "status": "idle", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-duplicate", "pane_id": "w2:p2", "workspace_id": "w2"},
            {"session_id": "learn-working", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客 (active)", "status": "working", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-working", "pane_id": "w2:p3", "workspace_id": "w2"},
        ]

    def test_plan_surfaces_idle_and_duplicate_cleanup_candidates(self):
        plan = self.router.build_plan(
            "写一篇技术博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
        )
        self.assertEqual({candidate["reason"] for candidate in plan["stale_m2_candidates"]}, {"idle_existing_m2", "duplicate_m2"})
        self.assertEqual(len(plan["targets"][0]["cleanup_candidates"]), 2)
        self.assertIn("Stale / Duplicate M2 Cleanup Candidates", self.router.preview_text(plan))
        self.assertNotIn("learn-working", {candidate["session_id"] for candidate in plan["stale_m2_candidates"]})

    def test_cleanup_duplicates_closes_only_duplicate_idle_panes(self):
        plan = self.router.build_plan(
            "写一篇技术博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
        )
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [{"pane_id": "w2:p1", "status": "idle", "name": "learn-old"}, {"pane_id": "w2:p2", "status": "idle", "name": "learn-duplicate"}]}), stderr="")
            if args[1:3] == ["agent", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [{"pane_id": "w2:p1", "agent_status": "idle", "name": "learn-old"}, {"pane_id": "w2:p2", "agent_status": "idle", "name": "learn-duplicate"}]}), stderr="")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        result = self.router.apply_plan(plan, approve=True, cleanup_duplicates=True, runner=runner)
        self.assertEqual([item["pane_id"] for item in result["cleanup_receipts"]], ["w2:p2"])
        self.assertTrue(any(cmd[-3:] == ["pane", "close", "w2:p2"] for cmd in calls))
        self.assertEqual(calls[-1][1:3], ["agent", "prompt"])

    def test_cleanup_protects_current_pane_and_w1_p1(self):
        sessions = [
            {"session_id": "learn-w1", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客 w1", "status": "idle", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-w1", "pane_id": "w1:p1", "workspace_id": "w1"},
            {"session_id": "learn-cur", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客 cur", "status": "idle", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-cur", "pane_id": "w9:p9", "workspace_id": "w9"},
            {"session_id": "learn-safe", "topic_id": "learning-and-blog", "target_repo": "Personal", "title": "学习与写博客 safe", "status": "idle", "cwd": "/repo/Personal", "source": "herdr", "herdr_target": "learn-safe", "pane_id": "w2:p2", "workspace_id": "w2"},
        ]
        with patch.dict("os.environ", {"HERDR_PANE_ID": "w9:p9"}):
            plan = self.router.build_plan(
                "写一篇技术博客",
                sessions,
                mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
            )
            self.assertEqual([c["pane_id"] for c in plan["stale_m2_candidates"]], ["w2:p2"])
            closed_res = self.router._close_herdr_pane({"pane_id": "w9:p9"})
            self.assertTrue(closed_res["skipped"])
            self.assertEqual(closed_res["reason"], "protected_pane")
            w1_res = self.router._close_herdr_pane({"pane_id": "w1:p1"})
            self.assertTrue(w1_res["skipped"])
            self.assertEqual(w1_res["reason"], "protected_pane")

    def test_replace_stale_m2_closes_idle_panes_and_spawns_fresh_target(self):
        plan = self.router.build_plan(
            "写一篇技术博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
        )
        plan["targets"][0]["dispatch_script"] = "/tmp/fake-herdr-dispatch"
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [{"pane_id": "w2:p1", "status": "idle", "name": "learn-old"}, {"pane_id": "w2:p2", "status": "idle", "name": "learn-duplicate"}]}), stderr="")
            if args[1:3] == ["agent", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [{"pane_id": "w2:p1", "agent_status": "idle", "name": "learn-old"}, {"pane_id": "w2:p2", "agent_status": "idle", "name": "learn-duplicate"}]}), stderr="")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        result = self.router.apply_plan(plan, approve=True, replace_stale_m2=True, runner=runner)
        self.assertEqual({item["pane_id"] for item in result["cleanup_receipts"]}, {"w2:p1", "w2:p2"})
        self.assertEqual(calls[-1][0], "/tmp/fake-herdr-dispatch")
        self.assertTrue("--new-workspace" in calls[-1] or "--pane" in calls[-1])

    def test_cli_plan_outputs_json_without_dispatch(self):
        fixture = {"agents": [{"name": "alpha-m2-r1", "agent_status": "working", "repo": "alpha"}]}
        with tempfile.TemporaryDirectory() as temp:
            fixture_path = Path(temp) / "agents.json"
            preview_md = Path(temp) / "preview.md"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            result = subprocess.run([
                str(ROUTER_PATH), "--plan", "--message", "inspect alpha", "--herdr-json", str(fixture_path),
                "--no-dsh", "--no-open-preview", "--preview-md-file", str(preview_md), "--json",
                "--mock-jev-json", json.dumps({"route": "ROUTE_SINGLE", "targets": [{"target_repo": "alpha"}]}),
            ], capture_output=True, text=True, check=False)
            self.assertTrue(preview_md.is_file())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["targets"][0]["m2_status"], "EXISTING_M2")
        self.assertFalse(payload["approval"]["approved"])

    def test_apply_requires_explicit_approve_flag_even_if_plan_claims_approved(self):
        plan = self.router.build_plan(
            "send work",
            [],
            mock_jev={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "first", "sub_prompt": "first prompt", "wave_order": 1}]},
            transport="stdout",
        )
        plan["approval"]["approved"] = True
        with self.assertRaisesRegex(self.router.RouterError, "approval"):
            self.router.apply_plan(plan, approve=False)

    def test_stdout_transport_simulates_cleanup_without_side_effects(self):
        plan = self.router.build_plan(
            "写一篇技术博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
            transport="stdout",
        )
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as temp:
            map_path = Path(temp) / "map.json"
            original_map = (ROOT / "skills/m2-dispatcher/references/m2-domain-map.json").read_text(encoding="utf-8")
            map_path.write_text(original_map, encoding="utf-8")
            result = self.router.apply_plan(
                plan,
                approve=True,
                cleanup_duplicates=True,
                register_new_domains=True,
                domain_map_file=map_path,
                transport="auto",
                runner=runner,
            )
            self.assertEqual(calls, [])
            self.assertTrue(result["cleanup_receipts"][0]["simulated"])
            self.assertFalse(result["receipts"][0]["created"])
            self.assertEqual(map_path.read_text(encoding="utf-8"), original_map)

    def test_cleanup_revalidates_live_status_and_skips_pane_that_became_working(self):
        plan = self.router.build_plan(
            "写一篇技术博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1}]},
        )
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [{"pane_id": "w2:p2", "status": "working", "name": "learn-duplicate"}]}), stderr="")
            if args[1:3] == ["agent", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [{"pane_id": "w2:p2", "agent_status": "working", "name": "learn-duplicate"}]}), stderr="")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        result = self.router.apply_plan(plan, approve=True, cleanup_duplicates=True, runner=runner)
        self.assertTrue(result["cleanup_receipts"][0]["skipped"])
        self.assertIn("pane_not_idle", result["cleanup_receipts"][0]["reason"])
        self.assertFalse(any(cmd[1:3] == ["pane", "close"] for cmd in calls))

        empty_res = self.router.apply_plan(
            plan,
            approve=True,
            cleanup_duplicates=True,
            runner=lambda args, **kwargs: (
                SimpleNamespace(returncode=1, stdout="", stderr="err")
                if args[1:3] in (["pane", "list"], ["agent", "list"])
                else SimpleNamespace(returncode=0, stdout="ok", stderr="")
            ),
        )
        self.assertTrue(empty_res["cleanup_receipts"][0]["skipped"])
        self.assertEqual(empty_res["cleanup_receipts"][0]["reason"], "revalidation_inventory_unavailable")

        missing_pane_res = self.router.apply_plan(
            plan,
            approve=True,
            cleanup_duplicates=True,
            runner=lambda args, **kwargs: (
                SimpleNamespace(returncode=1, stdout="", stderr="err")
                if args[1:3] == ["pane", "list"]
                else SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [{"name": "learn-duplicate", "agent_status": "idle"}]}), stderr="")
                if args[1:3] == ["agent", "list"]
                else SimpleNamespace(returncode=0, stdout="ok", stderr="")
            ),
        )
        self.assertTrue(missing_pane_res["cleanup_receipts"][0]["skipped"])
        self.assertEqual(missing_pane_res["cleanup_receipts"][0]["reason"], "pane_identity_changed")

    def test_topic_routing_prefers_exact_topic_id_and_excludes_shared_repo_stem_conflicts(self):
        sessions = [
            {"session_id": "notification", "topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "title": "Sapphire Notification Center", "status": "idle", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "notification", "pane_id": "w1:p2", "workspace_id": "w1"},
            {"session_id": "china", "topic_id": "sapphire-china-bugs", "target_repo": "bing-growth-sapphire-app", "title": "Sapphire China Bugs", "status": "working", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "china", "pane_id": "w1:p3", "workspace_id": "w1"},
        ]
        plan = self.router.build_plan(
            "检查 Sapphire Notification 推送开关",
            sessions,
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [
                {"topic_id": "sapphire-notification", "session_id": "china", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "检查推送开关", "wave_order": 1},
            ]},
        )
        self.assertEqual(plan["targets"][0]["session_id"], "notification")

        unrelated_single = self.router.build_plan(
            "检查 Sapphire Notification 推送开关",
            [{"session_id": "unrelated", "target_repo": "bing-growth-sapphire-app", "title": "Sapphire General", "status": "working", "cwd": "/repo/bing-growth-sapphire-app", "source": "herdr", "herdr_target": "unrelated", "pane_id": "w1:p4", "workspace_id": "w1"}],
            mock_magpie={"route": "ROUTE_SINGLE", "targets": [
                {"topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "检查推送开关", "wave_order": 1},
            ]},
        )
        self.assertEqual(unrelated_single["targets"][0]["m2_status"], "NEW_M2")

    def test_offline_deterministic_fallback_uses_checked_in_domain_map(self):
        with patch.object(self.router, "_request_magpie", side_effect=OSError("offline")), patch.object(self.router, "_jev_decision", return_value=None):
            plan = self.router.build_plan("先让 AIDash 检查指标，然后让 VoxPocket 修复语音输入", [])
        self.assertEqual([(t["topic_id"], t["target_repo"], t["wave_order"], t["domain_status"]) for t in plan["targets"]], [
            ("aidash-aidata", "AIDash", 1, "MAPPED_DOMAIN"),
            ("voxpocket", "VoxPocket", 2, "MAPPED_DOMAIN"),
        ])

    def test_conflicting_jev_single_route_does_not_truncate_multi_target_decomposition(self):
        plan = self.router.build_plan(
            "同时更新 AIDash 和 VoxPocket",
            [],
            mock_jev={"answers": {"route_mode": {"choice": "ROUTE_SINGLE"}}},
            mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"target_repo": "AIDash", "sub_prompt": "更新 AIDash", "wave_order": 1},
                {"target_repo": "VoxPocket", "sub_prompt": "更新 VoxPocket", "wave_order": 2},
            ]},
        )
        self.assertEqual(plan["decision"]["route"], "ROUTE_CROSS_SESSION")
        self.assertEqual(len(plan["targets"]), 2)

    def test_new_m2_herdr_uses_distinct_topic_agent_names_and_auto_transport_resolves_adapter(self):
        plan = self.router.build_plan(
            "同时新建 Sapphire Notification、Sapphire China Bugs 与两个 QuantLab 新话题会话",
            [],
            mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "通知任务", "wave_order": 1, "dispatch_script": "/tmp/fake-herdr-dispatch"},
                {"topic_id": "sapphire-china-bugs", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "中国区任务", "wave_order": 1, "dispatch_script": "/tmp/fake-herdr-dispatch"},
                {"topic_id": "new-topic-quantlab-risk", "target_repo": "QuantLab", "sub_prompt": "风控任务", "wave_order": 1, "dispatch_script": "/tmp/fake-herdr-dispatch"},
                {"topic_id": "new-topic-quantlab-trading", "target_repo": "QuantLab", "sub_prompt": "交易任务", "wave_order": 1, "dispatch_script": "/tmp/fake-herdr-dispatch"},
            ]},
        )
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok": true, "reused_existing": false}', stderr="")

        result = self.router.apply_plan(plan, approve=True, runner=runner)
        names = [receipt["target"] for receipt in result["receipts"]]
        self.assertEqual(len(set(names)), 4)
        self.assertTrue(all(receipt["method"] == "herdr-dispatch" for receipt in result["receipts"]))
        self.assertTrue(all(receipt["created"] for receipt in result["receipts"]))

    def test_personal_m2_assistant_skill_links_resolve(self):
        skill_md = ROOT / "skills" / "personal-m2-assistant" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        import re
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        self.assertTrue(links)
        for rel in links:
            self.assertTrue((skill_md.parent / rel).resolve().exists(), f"broken link in personal-m2-assistant/SKILL.md: {rel}")

    def test_mixed_transport_plan_preserves_per_target_stdout_isolation(self):
        plan = self.router.build_plan(
            "同时处理学习和 AIDash",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "写博客", "wave_order": 1, "transport": "stdout"},
                {"topic_id": "aidash-aidata", "target_repo": "AIDash", "sub_prompt": "看指标", "wave_order": 2, "transport": "herdr", "dispatch_script": "/tmp/fake-herdr-dispatch"},
            ]},
        )
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"ok": true, "reused_existing": false}', stderr="")

        result = self.router.apply_plan(plan, approve=True, replace_stale_m2=True, runner=runner)
        self.assertTrue(all(item["simulated"] for item in result["cleanup_receipts"]))
        self.assertEqual(result["receipts"][0]["method"], "stdout")
        self.assertEqual(result["receipts"][1]["method"], "herdr-dispatch")
        self.assertFalse(any(cmd[1:3] == ["pane", "close"] for cmd in calls))

    def test_custom_domain_map_multi_topic_singleton_guard_and_long_topic_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            custom_map_path = Path(temp) / "custom-map.json"
            base_map = json.loads((ROOT / "skills/personal-m2-assistant/references/m2-domain-map.json").read_text(encoding="utf-8"))
            base_map["groups"].append({
                "group": "quant",
                "title": "Quant",
                "topics": [
                    {"topic_id": "quantlab-risk", "title": "QuantLab Risk", "target_repo": "QuantLab", "cwd": "~/Development/QuantLab", "keywords": ["风控"], "aliases": []},
                    {"topic_id": "quantlab-trading", "title": "QuantLab Trading", "target_repo": "QuantLab", "cwd": "~/Development/QuantLab", "keywords": ["交易"], "aliases": []},
                ],
            })
            custom_map_path.write_text(json.dumps(base_map, ensure_ascii=False, indent=2), encoding="utf-8")
            unrelated_plan = self.router.build_plan(
                "检查 QuantLab 风控",
                [{"session_id": "ql-general", "target_repo": "QuantLab", "title": "QuantLab General", "status": "working", "cwd": "/repo/QuantLab", "source": "herdr", "herdr_target": "ql-general", "pane_id": "w8:p1", "workspace_id": "w8"}],
                domain_map_file=custom_map_path,
                mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "quantlab-risk", "target_repo": "QuantLab", "sub_prompt": "检查风控", "wave_order": 1}]},
            )
            self.assertEqual(unrelated_plan["targets"][0]["m2_status"], "NEW_M2")

        name_a = self.router._new_herdr_agent_name({"topic_id": "new-topic-quantlab-portfolio-analysis-risk", "target_repo": "QuantLab"})
        name_b = self.router._new_herdr_agent_name({"topic_id": "new-topic-quantlab-portfolio-analysis-trading", "target_repo": "QuantLab"})
        self.assertNotEqual(name_a, name_b)
        self.assertLessEqual(len(name_a), 32)
        self.assertLessEqual(len(name_b), 32)

        unreg_conflict = self.router.build_plan(
            "QuantLab 交易",
            [{"session_id": "ql-risk", "topic_id": "new-topic-quantlab-risk", "target_repo": "QuantLab", "title": "QuantLab Risk", "status": "idle", "cwd": "/repo/QuantLab", "source": "herdr", "herdr_target": "ql-risk", "pane_id": "w8:p2", "workspace_id": "w8"}],
            targets_json=[{"topic_id": "new-topic-quantlab-trading", "target_repo": "QuantLab", "sub_prompt": "执行交易策略", "wave_order": 1}],
        )
        self.assertEqual(unreg_conflict["targets"][0]["m2_status"], "NEW_M2")
        self.assertEqual(unreg_conflict["stale_m2_candidates"], [])

        plan_risk = self.router.build_plan(
            "QuantLab 风控",
            [],
            mock_magpie={"route": "CREATE_NEW_M2", "targets": [{"target_repo": "QuantLab", "sub_prompt": "处理风控", "wave_order": 1}]},
        )
        plan_trading = self.router.build_plan(
            "QuantLab 交易",
            [],
            mock_magpie={"route": "CREATE_NEW_M2", "targets": [{"target_repo": "QuantLab", "sub_prompt": "执行交易策略", "wave_order": 1}]},
        )
        self.assertNotEqual(plan_risk["targets"][0]["topic_id"], plan_trading["targets"][0]["topic_id"])
        self.assertNotEqual(
            self.router._new_herdr_agent_name(plan_risk["targets"][0]),
            self.router._new_herdr_agent_name(plan_trading["targets"][0]),
        )

        en_risk = self.router.build_plan(
            "QuantLab risk dashboard",
            [],
            mock_magpie={"route": "CREATE_NEW_M2", "targets": [{"target_repo": "QuantLab", "sub_prompt": "Implement the risk dashboard", "wave_order": 1}]},
        )
        en_trading = self.router.build_plan(
            "QuantLab trading dashboard",
            [],
            mock_magpie={"route": "CREATE_NEW_M2", "targets": [{"target_repo": "QuantLab", "sub_prompt": "Implement the trading dashboard", "wave_order": 1}]},
        )
        self.assertNotEqual(en_risk["targets"][0]["topic_id"], en_trading["targets"][0]["topic_id"])
        self.assertNotEqual(
            self.router._new_herdr_agent_name(en_risk["targets"][0]),
            self.router._new_herdr_agent_name(en_trading["targets"][0]),
        )

    def test_default_model_context_and_tilde_cwd_expansion_across_pipeline(self):
        domain_map = self.router.load_domain_map()
        for topic in domain_map["topics"]:
            self.assertFalse(topic["cwd"].startswith("~"), f"unexpanded cwd in domain map: {topic}")
            self.assertTrue(topic["cwd"].startswith("/"))

        normalized = self.router.normalize_session({
            "session_id": "s1",
            "name": "aidash-m2-r1",
            "agent_status": "idle",
            "cwd": "~/Development/AIDash",
            "is_m2": True,
        }, "herdr")
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["cwd"], str(Path("~/Development/AIDash").expanduser()))

        with tempfile.TemporaryDirectory() as temp:
            preview_path = Path(temp) / "plan-preview.md"
            plan = self.router.build_plan(
                "在 AIDash 和新项目 AlphaX 中分别检查状态",
                [],
                mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                    {"topic_id": "aidash-aidata", "target_repo": "AIDash", "sub_prompt": "检查 AIDash", "wave_order": 1, "dispatch_script": "/bin/echo"},
                    {"topic_id": "NEW_UNMAPPED_DOMAIN", "target_repo": "AlphaX", "cwd": "~/Development/AlphaX", "sub_prompt": "初始化 AlphaX", "wave_order": 2, "dispatch_script": "/bin/echo"},
                ]},
                preview_md_file=preview_path,
            )
            self.assertEqual(plan["default_model"], "gemini-4-argon")
            self.assertEqual(plan["default_context"], "long_context")
            self.assertEqual(plan["default_model_label"], "Gemini 4 Argon · 983K context")
            for target in plan["targets"]:
                self.assertEqual(target["model"], "gemini-4-argon")
                self.assertEqual(target["context"], "long_context")
                self.assertEqual(target["model_label"], "Gemini 4 Argon · 983K context")
                self.assertFalse(target["cwd"].startswith("~"))
                self.assertTrue(target["cwd"].startswith("/"))

            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0, stdout='{"ok": true, "reused_existing": false, "pane_id": "w9:p1"}', stderr="")

            result = self.router.apply_plan(plan, approve=True, plan_file=Path(temp) / "plan.json", runner=runner)
            self.assertEqual(len(calls), 2)
            for call_args, receipt in zip(calls, result["receipts"]):
                self.assertIn("--cwd", call_args)
                cwd_arg = call_args[call_args.index("--cwd") + 1]
                self.assertFalse(cwd_arg.startswith("~"))
                self.assertEqual( call_args[-5:], ["--", "--model", "gemini-4-argon", "--context", "long_context"])
                self.assertEqual(receipt["model"], "gemini-4-argon")
                self.assertEqual(receipt["context"], "long_context")
                self.assertEqual(receipt["model_label"], "Gemini 4 Argon · 983K context")
                self.assertEqual(receipt["cwd"], cwd_arg)
            self.assertEqual(result["watchdog_copilot_cli"]["mode"], "async")
            self.assertTrue(result["watchdog_copilot_cli"]["detach"])
            self.assertEqual(result["watchdog_copilot_cli"]["shellId"], f"watch-m2-{plan['plan_id']}")
            self.assertIn("--watch", result["watchdog_cmd"])

        dsh_calls = []

        def dsh_poster(url, payload, **kwargs):
            dsh_calls.append((url, payload))
            if url.endswith("/create-session"):
                return {"ok": True, "session": {"sessionId": "dsh-new-1"}}
            return {"ok": True}

        dsh_plan = {
            "schema_version": 1,
            "plan_id": "m2p-dsh",
            "approval": {"approved": False},
            "targets": [{
                "target_repo": "BetaRepo",
                "m2_status": "NEW_M2",
                "transport": "dsh",
                "base_url": "http://dsh.test",
                "cwd": "~/Development/BetaRepo",
                "sub_prompt": "start beta",
                "wave_order": 1,
            }],
        }
        dsh_res = self.router.apply_plan(dsh_plan, approve=True, poster=dsh_poster)
        self.assertEqual(dsh_calls[0][1]["cwd"], str(Path("~/Development/BetaRepo").expanduser()))
        self.assertEqual(dsh_res["receipts"][0]["cwd"], str(Path("~/Development/BetaRepo").expanduser()))

    def test_plan_writes_markdown_preview_and_opens_orca_with_focus(self):
        orca_calls = []

        def ok_runner(args, **kwargs):
            orca_calls.append(args)
            return SimpleNamespace(returncode=0, stdout="opened", stderr="")

        with tempfile.TemporaryDirectory() as temp:
            md_path = Path(temp) / "m2-preview.md"
            plan = self.router.build_plan(
                "先检查通知开关，再写学习博客",
                self._learning_sessions(),
                mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                    {"topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "检查通知开关详情\n包含多行说明", "rag_context_hint": "upstream-notif-hint", "wave_order": 1},
                    {"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "整理学习博客", "wave_order": 2},
                ]},
                preview_md_file=md_path,
                open_preview=True,
                orca_bin="/usr/local/bin/orca",
                runner=ok_runner,
            )
            self.assertTrue(md_path.is_file())
            self.assertEqual(plan["preview_markdown_path"], str(md_path))
            self.assertTrue(plan["preview_open"]["ok"])
            self.assertEqual(orca_calls, [["/usr/local/bin/orca", "file", "open", str(md_path), "--focus"]])
            md_content = md_path.read_text(encoding="utf-8")
            self.assertIn(f"# M2 Dispatch Plan · {plan['plan_id']} (ROUTE_CROSS_SESSION)", md_content)
            self.assertIn("Gemini 4 Argon · 983K context", md_content)
            self.assertIn("检查通知开关详情\n包含多行说明", md_content)
            self.assertIn("upstream-notif-hint", md_content)
            self.assertIn("## Stale / Duplicate M2 Cleanup Candidates", md_content)
            self.assertIn(f"Preview Markdown: {md_path}", self.router.preview_text(plan))

            # Verify graceful fallback when orca fails or is missing
            fail_plan = self.router.build_plan(
                "检查 AIDash",
                [],
                mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "AIDash", "sub_prompt": "check", "wave_order": 1}]},
                preview_md_file=Path(temp) / "fail-preview.md",
                open_preview=True,
                orca_bin="/nonexistent/orca",
                runner=lambda args, **kwargs: SimpleNamespace(returncode=127, stdout="", stderr="orca not found"),
            )
            self.assertFalse(fail_plan["preview_open"]["ok"])
            self.assertIn("orca not found", fail_plan["preview_open"]["warning"])

    def test_watch_auto_unblocks_safe_allow_dialogs_and_settles_on_idle(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-watch-allow",
            "targets": [
                {
                    "wave_order": 1,
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "herdr_target": "m2-aidash-r1",
                    "pane_id": "w5:p1",
                    "transport": "herdr",
                    "cwd": "~/Development/AIDash",
                },
                {
                    "wave_order": 2,
                    "topic_id": "learning-and-blog",
                    "target_repo": "Personal",
                    "herdr_target": "m2-personal-r1",
                    "pane_id": "w6:p1",
                    "transport": "herdr",
                    "cwd": "~/Development/Personal",
                },
            ],
        }
        poll_state = {"step": 0}
        sent_keys = []

        def runner(args, **kwargs):
            cmd = args[1:3]
            step = poll_state["step"]
            if cmd == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:pane:list", "result": {"panes": [
                    {"pane_id": "w5:p1", "name": "m2-aidash-r1"},
                    {"pane_id": "w6:p1", "name": "m2-personal-r1"},
                ]}}), stderr="")
            if cmd == ["agent", "list"]:
                if step == 0:
                    # Target 1 blocked on Allow path access, Target 2 blocked on Curate Memory
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:agent:list", "result": {"agents": [
                        {"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": "blocked", "completion_seq": 1},
                        {"pane_id": "w6:p1", "name": "m2-personal-r1", "agent_status": "needs_input", "completion_seq": 1},
                    ]}}), stderr="")
                if step == 1:
                    # Target 1 blocked on Confirm folder trust, Target 2 blocked on whitelisted CMD_CONFIRM
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:agent:list", "result": {"agents": [
                        {"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": "blocked", "completion_seq": 1},
                        {"pane_id": "w6:p1", "name": "m2-personal-r1", "agent_status": "blocked", "completion_seq": 1},
                    ]}}), stderr="")
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:agent:list", "result": {"agents": [
                    {"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": "idle", "completion_seq": 2},
                    {"pane_id": "w6:p1", "name": "m2-personal-r1", "agent_status": "idle", "completion_seq": 2},
                ]}}), stderr="")
            if cmd == ["agent", "read"]:
                target_name = args[3]
                if step == 0:
                    if target_name == "m2-aidash-r1":
                        return SimpleNamespace(returncode=0, stdout=(
                            "Allow path access to ~/Development/AIDash/aidata?\n"
                            "❯ 1. Yes\n"
                            "  2. Yes, and remember for the rest of the session\n"
                        ), stderr="")
                    return SimpleNamespace(returncode=0, stdout="Curate Memory: store_memory entry?\n❯ 1. Allow\n", stderr="")
                if step == 1:
                    if target_name == "m2-aidash-r1":
                        return SimpleNamespace(returncode=0, stdout="Confirm folder trust: Do you trust the files in this folder ~/Development/AIDash?\n❯ Yes, I trust\n", stderr="")
                    return SimpleNamespace(returncode=0, stdout=(
                        "CMD_CONFIRM: Do you want to run command?\n"
                        "Command: git status\n"
                        "❯ 1. Run once\n"
                        "  2. Always allow for this session\n"
                    ), stderr="")
                return SimpleNamespace(returncode=0, stdout=f"{target_name} completed all checks.", stderr="")
            if cmd == ["agent", "send-keys"]:
                sent_keys.append(args[3:])
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        def sleep_fn(_):
            poll_state["step"] += 1

        apply_receipt = {
            "ok": True,
            "plan_id": "m2p-watch-allow",
            "approved": True,
            "receipts": [
                {"wave_order": 1, "topic_id": "aidash-aidata", "target_repo": "AIDash", "ok": True, "method": "herdr-dispatch", "target": "m2-aidash-r1", "pane_id": "w5:p1"},
                {"wave_order": 2, "topic_id": "learning-and-blog", "target_repo": "Personal", "ok": True, "method": "herdr-dispatch", "target": "m2-personal-r1", "pane_id": "w6:p1"},
            ],
        }
        watch_res = self.router.watch_dispatched_m2(
            plan,
            apply_receipt=apply_receipt,
            interval=0.01,
            max_wait=10.0,
            max_polls=5,
            herdr_bin="herdr",
            runner=runner,
            sleep_fn=sleep_fn,
        )
        self.assertTrue(watch_res["ok"])
        self.assertTrue(watch_res["auto_unblock_enabled"])
        self.assertFalse(watch_res["passive_only"])
        self.assertTrue(watch_res["all_settled"])
        self.assertFalse(watch_res["needs_owner_attention"])
        self.assertEqual(sent_keys, [
            ["m2-aidash-r1", "enter"],
            ["m2-personal-r1", "enter"],
            ["m2-aidash-r1", "enter"],
            ["m2-personal-r1", "enter"],
        ])
        aidash_report = watch_res["targets"][0]
        personal_report = watch_res["targets"][1]
        self.assertEqual([u["kind"] for u in aidash_report["auto_unblocked"]], ["allow_path_access", "confirm_folder_trust"])
        self.assertEqual([u["kind"] for u in personal_report["auto_unblocked"]], ["curate_memory", "whitelisted_cmd_confirm"])
        self.assertIn("completed all checks", aidash_report["visible_summary"])

    def test_watch_rejects_path_traversal_symlink_escape_and_unsafe_commands(self):
        safe_roots = self.router._safe_roots_for_target("~/Development/AIDash")
        self.assertFalse(self.router._path_is_under_safe_roots("~/Development/AIDash/../../.ssh/id_rsa", safe_roots))
        self.assertFalse(self.router._path_is_under_safe_roots("/tmp/../etc/passwd", safe_roots))
        self.assertTrue(self.router._path_is_under_safe_roots("~/Development/AIDash/aidata", safe_roots))

        # S1: Spaced/quoted/Unicode paths and symlinks inside a safe root pointing outside roots
        dev_root = Path("~/Development").expanduser()
        dev_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(dev_root)) as temp:
            spaced_link = Path(temp) / "review link"
            spaced_link.symlink_to("/etc")
            unicode_outside = "/etc/配置 机密/secret.txt"
            safe_spaced_dir = Path(temp) / "safe 文件夹"
            safe_spaced_dir.mkdir(parents=True, exist_ok=True)
            temp_safe_roots = [*safe_roots, str(Path(temp))]

            # Complete path extracted including space: <temp>/review link -> symlink to /etc -> rejected!
            extracted_spaced = self.router._extract_single_dialog_path(f'Allow path access to "{spaced_link}"?')
            self.assertEqual(extracted_spaced, str(spaced_link))
            self.assertFalse(self.router._path_is_under_safe_roots(extracted_spaced, temp_safe_roots))

            sent_for_symlink = []
            res_symlink = self.router._classify_and_unblock_dialog(
                f"Allow path access to {spaced_link}?\n❯ 1. Yes\n",
                target_ref="m2-test-r1",
                target_cwd=str(Path(temp)),
                live_status="blocked",
                auto_unblock_enabled=True,
                runner=lambda args, **kw: (sent_for_symlink.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
            )
            self.assertEqual(res_symlink["action"], "owner_decision")
            self.assertEqual(sent_for_symlink, [])

            res_unicode_outside = self.router._classify_and_unblock_dialog(
                f'Allow path access to "{unicode_outside}"?\n❯ 1. Yes\n',
                target_ref="m2-test-r1",
                target_cwd="~/Development/AIDash",
                live_status="blocked",
                auto_unblock_enabled=True,
                runner=lambda args, **kw: (sent_for_symlink.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
            )
            self.assertEqual(res_unicode_outside["action"], "owner_decision")
            self.assertEqual(sent_for_symlink, [])

            # Multi-path or wildcard dialog must be rejected
            res_multi = self.router._classify_and_unblock_dialog(
                f"Allow path access to {safe_spaced_dir} /etc/passwd?\n❯ 1. Yes\n",
                target_ref="m2-test-r1",
                target_cwd="~/Development/AIDash",
                live_status="blocked",
                auto_unblock_enabled=True,
                runner=lambda args, **kw: (sent_for_symlink.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
            )
            self.assertEqual(res_multi["action"], "owner_decision")
            self.assertEqual(sent_for_symlink, [])

            # Safe spaced/Unicode directory is extracted intact and allowed when under safe roots
            extracted_safe_unicode = self.router._extract_single_dialog_path(f'Allow path access to "{safe_spaced_dir}"?')
            self.assertEqual(extracted_safe_unicode, str(safe_spaced_dir))
            self.assertTrue(self.router._path_is_under_safe_roots(extracted_safe_unicode, temp_safe_roots))

        # S2: Unsafe command strings and argument forms must never be whitelisted
        unsafe_cmds = [
            "ls & bash -c id",
            "git status && python3 --version",
            "git status | head -n 5",
            "git status > ~/.ssh/authorized_keys",
            "git status >> /tmp/out",
            "git clean -fd",
            "git branch review-created",
            "git branch -D feat/x",
            "git branch -m old new",
            "git checkout main",
            "git switch main",
            "git stash",
            "git tag v1",
            "git config user.name x",
            "git apply patch.diff",
            "git cherry-pick abc",
            "git -c core.pager=sh status",
            "gh repo delete owner/repo --yes",
            "gh pr merge 123",
            "gh pr view 123 --web",
            "gh api -X POST /repos/o/r/issues",
            "herdr agent send-keys w1:p1 enter",
            "herdr agent prompt w1:p1 hi",
            "herdr pane close w2:p1",
            "herdr server stop",
            "orca file open /etc/passwd --focus",
            "orca file open /tmp/m2-plan-test.md",
            "python3 -c 'import os'",
            "python3 -m http.server",
            "python3 arbitrary_script.py",
            "env FOO=1 bash -c id",
            "FOO=1 git status",
            "find . -fprint /tmp/x",
            "find . -exec id {} +",
            "rg --pre=sh foo",
            "jq -f /etc/passwd .",
        ]
        for bad_cmd in unsafe_cmds:
            with self.subTest(bad_cmd=bad_cmd):
                self.assertFalse(self.router._is_whitelisted_command(bad_cmd), f"unsafe command falsely whitelisted: {bad_cmd}")

        # Legitimate single-line read-only commands must be whitelisted
        safe_cmds = [
            "git status",
            "git --no-pager diff --stat",
            "git log -n 5 --oneline",
            "git branch --show-current",
            "git branch -a",
            "git branch -r",
            "git branch --list",
            "git worktree list",
            "gh pr view 127",
            "gh pr checks 127",
            "gh run list",
            "gh issue list",
            "herdr agent list",
            "herdr pane list",
            "herdr workspace list",
            "herdr agent get m2-aidash-r1",
            "herdr agent read m2-aidash-r1 --source visible --lines 40",
            "herdr agent wait m2-aidash-r1 --timeout 60000",
            "orca file open /tmp/m2-plan-test.md --focus",
            "python3 --version",
            "python3 -m unittest scripts/test_m2_router.py",
            "env",
            "ls -la",
        ]
        for good_cmd in safe_cmds:
            with self.subTest(good_cmd=good_cmd):
                self.assertTrue(self.router._is_whitelisted_command(good_cmd), f"safe command rejected: {good_cmd}")

        # S2: Multiline command confirmation (`Command: git status` followed by trailing `touch /tmp/pwn`) must reject whole block
        multiline_cmd_dialog = (
            "CMD_CONFIRM: Do you want to run command?\n"
            "Command: git status\n"
            "touch /tmp/pwn\n"
            "❯ 1. Run once\n"
        )
        sent_multiline = []
        multiline_res = self.router._classify_and_unblock_dialog(
            multiline_cmd_dialog,
            target_ref="m2-multi-cmd",
            target_cwd="~/Development/AIDash",
            live_status="blocked",
            auto_unblock_enabled=True,
            runner=lambda args, **kw: (sent_multiline.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
        )
        self.assertEqual(multiline_res["action"], "owner_decision")
        self.assertEqual(multiline_res["kind"], "non_whitelisted_cmd")
        self.assertEqual(sent_multiline, [])

        # S3: Completed CMD_CONFIRM in scrollback followed by "Proceed with deployment?" must never send keys
        stale_scrollback_then_business = (
            "CMD_CONFIRM\n"
            "Command: git status\n"
            "Completed.\n"
            "Proceed with deployment?\n"
            "❯ 1. Yes\n"
            "  2. No\n"
        )
        sent_stale = []
        stale_res = self.router._classify_and_unblock_dialog(
            stale_scrollback_then_business,
            target_ref="m2-stale-r1",
            target_cwd="~/Development/AIDash",
            live_status="blocked",
            auto_unblock_enabled=True,
            runner=lambda args, **kw: (sent_stale.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
        )
        self.assertEqual(stale_res["action"], "owner_decision")
        self.assertEqual(stale_res["blocked_reason"], "owner_decision_required")
        self.assertEqual(sent_stale, [])

        # S3: Non-blocked live_status (idle/working) must never trigger send-keys even if visible text has a dialog
        idle_res = self.router._classify_and_unblock_dialog(
            "CMD_CONFIRM: Do you want to run command?\nCommand: git status\n❯ 1. Run once\n",
            target_ref="m2-idle-r1",
            target_cwd="~/Development/AIDash",
            live_status="idle",
            auto_unblock_enabled=True,
            runner=lambda args, **kw: (sent_stale.append(args), SimpleNamespace(returncode=0, stdout="", stderr=""))[1],
        )
        self.assertEqual(idle_res["action"], "none")
        self.assertEqual(sent_stale, [])

        # Verify no-path folder trust fails closed when target_cwd is outside baseline roots
        no_path_outside = self.router._classify_and_unblock_dialog(
            "Confirm folder trust: Do you trust the files in this folder?\n❯ Yes, I trust",
            target_ref="m2-untrusted-r1",
            target_cwd="/etc/untrusted",
            live_status="blocked",
            auto_unblock_enabled=True,
            runner=lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        self.assertEqual(no_path_outside["action"], "owner_decision")
        self.assertEqual(no_path_outside["blocked_reason"], "owner_decision_required")

    def test_watch_passive_without_verified_apply_receipt_and_exact_identity_binding(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-auth-check",
            "targets": [{
                "wave_order": 1,
                "topic_id": "aidash-aidata",
                "target_repo": "AIDash",
                "herdr_target": "m2-aidash-old",
                "pane_id": "w5:p1",
                "transport": "herdr",
                "cwd": "~/Development/AIDash",
            }],
        }
        sent_keys = []

        def blocked_runner(args, **kwargs):
            cmd = args[1:3]
            if cmd == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:pane:list", "result": {"panes": [{"pane_id": "w5:p1", "name": "m2-aidash-old"}]}}), stderr="")
            if cmd == ["agent", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "cli:agent:list", "result": {"agents": [{"pane_id": "w5:p1", "name": "m2-aidash-old", "agent_status": "blocked"}]}}), stderr="")
            if cmd == ["agent", "read"]:
                return SimpleNamespace(returncode=0, stdout="CMD_CONFIRM: Confirm command\nCommand: git status\n❯ 1. Run once\n", stderr="")
            if cmd == ["agent", "send-keys"]:
                sent_keys.append(args)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")
            raise AssertionError(args)

        # 1) No apply_receipt -> passive mode only, never sends keys even on safe git status dialog
        no_receipt_res = self.router.watch_dispatched_m2(
            plan,
            apply_receipt=None,
            interval=0.01,
            max_wait=1.0,
            max_polls=1,
            runner=blocked_runner,
            sleep_fn=lambda _: None,
        )
        self.assertEqual(sent_keys, [])
        self.assertFalse(no_receipt_res["auto_unblock_enabled"])
        self.assertTrue(no_receipt_res["passive_only"])
        self.assertTrue(no_receipt_res["needs_owner_attention"])
        self.assertEqual(no_receipt_res["targets"][0]["blocked_reason"], "passive_watch_no_verified_apply_receipt")

        # 2) Mismatched plan_id or unapproved receipt -> passive mode only
        for bad_receipt in (
            {"ok": True, "approved": False, "plan_id": "m2p-auth-check", "receipts": [{"ok": True, "method": "herdr-dispatch", "target": "m2-aidash-old"}]},
            {"ok": True, "approved": True, "plan_id": "m2p-other-plan", "receipts": [{"ok": True, "method": "herdr-dispatch", "target": "m2-aidash-old"}]},
        ):
            res_bad = self.router.watch_dispatched_m2(
                plan,
                apply_receipt=bad_receipt,
                interval=0.01,
                max_wait=1.0,
                max_polls=1,
                runner=blocked_runner,
                sleep_fn=lambda _: None,
            )
            self.assertEqual(sent_keys, [])
            self.assertFalse(res_bad["auto_unblock_enabled"])

        # 3) F2: apply_plan persists apply_receipt_path, includes --apply-receipt-file in watchdog_cmd,
        # and watch_dispatched_m2 binds exact replaced Herdr target + newly created dsh session ID (no repo fallback)
        with tempfile.TemporaryDirectory() as temp:
            receipt_file = Path(temp) / "apply-receipt.json"
            multi_plan = {
                "schema_version": 1,
                "plan_id": "m2p-f2-bind",
                "approval": {"approved": False},
                "targets": [
                    {
                        "wave_order": 1,
                        "topic_id": "learning-and-blog",
                        "target_repo": "Personal",
                        "m2_status": "EXISTING_M2",
                        "session_id": "learn-stale",
                        "herdr_target": "learn-stale",
                        "pane_id": "w2:p1",
                        "transport": "herdr",
                        "cwd": "~/Development/Personal",
                        "dispatch_script": "/bin/fake-herdr-dispatch",
                        "cleanup_candidates": [{"session_id": "learn-stale", "pane_id": "w2:p1", "reason": "idle_existing_m2"}],
                        "sub_prompt": "write blog",
                    },
                    {
                        "wave_order": 2,
                        "topic_id": "info-email",
                        "target_repo": "dsh-personal-plugins",
                        "m2_status": "NEW_M2",
                        "transport": "dsh",
                        "base_url": "http://dsh.test",
                        "cwd": "~/Development/dsh-personal-plugins",
                        "sub_prompt": "check email",
                    },
                ],
                "stale_m2_candidates": [{"session_id": "learn-stale", "pane_id": "w2:p1", "reason": "idle_existing_m2"}],
            }

            def apply_runner(args, **kwargs):
                if args[1:3] == ["pane", "list"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [{"pane_id": "w2:p1", "status": "idle", "name": "learn-stale"}]}), stderr="")
                if args[1:3] == ["agent", "list"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [{"pane_id": "w2:p1", "agent_status": "idle", "name": "learn-stale"}]}), stderr="")
                if args[1:3] == ["pane", "close"]:
                    return SimpleNamespace(returncode=0, stdout="closed", stderr="")
                if args[0] == "/bin/fake-herdr-dispatch":
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "pane_id": "w9:p1", "agent": "m2-learning-and-blog-pe-9015cf-r1", "reused_existing": False}), stderr="")
                raise AssertionError(args)

            def apply_poster(url, payload, **kwargs):
                if url.endswith("/create-session"):
                    return {"ok": True, "session": {"sessionId": "dsh-created-99"}}
                return {"ok": True}

            apply_res = self.router.apply_plan(
                multi_plan,
                approve=True,
                replace_stale_m2=True,
                plan_file=Path(temp) / "plan.json",
                apply_receipt_file=receipt_file,
                runner=apply_runner,
                poster=apply_poster,
            )
            self.assertTrue(receipt_file.is_file())
            self.assertIn("--apply-receipt-file", apply_res["watchdog_cmd"])
            self.assertIn(str(receipt_file), apply_res["watchdog_copilot_cli"]["command"])
            persisted = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["plan_id"], "m2p-f2-bind")
            self.assertTrue(persisted["approved"])
            spawned_target = persisted["receipts"][0]["target"]
            self.assertEqual(persisted["receipts"][0]["pane_id"], "w9:p1")
            self.assertEqual(persisted["receipts"][1]["target"], "dsh-created-99")

            # Watching with persisted receipt must match w9:p1 / spawned_target and dsh-created-99,
            # NOT an unrelated same-repo session ("other-personal-m2" or "dsh-other-same-repo")
            def watch_runner(args, **kwargs):
                cmd = args[1:3]
                if cmd == ["pane", "list"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"panes": [
                        {"pane_id": "w2:p9", "name": "other-personal-m2", "cwd": "~/Development/Personal"},
                        {"pane_id": "w9:p1", "name": spawned_target, "cwd": "~/Development/Personal"},
                    ]}}), stderr="")
                if cmd == ["agent", "list"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"agents": [
                        {"pane_id": "w2:p9", "name": "other-personal-m2", "agent_status": "working", "completion_seq": 1},
                        {"pane_id": "w9:p1", "name": spawned_target, "agent_status": "idle", "completion_seq": 5},
                    ]}}), stderr="")
                if cmd == ["agent", "read"]:
                    return SimpleNamespace(returncode=0, stdout=f"output from {args[3]}", stderr="")
                raise AssertionError(args)

            def watch_fetcher(url, **kwargs):
                return {"sessions": [
                    {"sessionId": "dsh-other-same-repo", "title": "[M2] Other", "target_repo": "dsh-personal-plugins", "status": "working", "isDshSession": True},
                    {"sessionId": "dsh-created-99", "title": "[M2] Created", "target_repo": "dsh-personal-plugins", "status": "idle", "summary": "email done", "isDshSession": True},
                ]}

            watch_bound = self.router.watch_dispatched_m2(
                multi_plan,
                apply_receipt=persisted,
                dsh_state_url="http://dsh.test/m2-omni-hub/api/state",
                interval=0.01,
                max_wait=5.0,
                max_polls=2,
                runner=watch_runner,
                fetcher=watch_fetcher,
                sleep_fn=lambda _: None,
            )
            self.assertTrue(watch_bound["ok"])
            self.assertTrue(watch_bound["all_settled"])
            self.assertEqual(watch_bound["targets"][0]["target"], spawned_target)
            self.assertEqual(watch_bound["targets"][0]["pane_id"], "w9:p1")
            self.assertEqual(watch_bound["targets"][0]["status"], "idle")
            self.assertEqual(watch_bound["targets"][1]["target"], "dsh-created-99")
            self.assertEqual(watch_bound["targets"][1]["status"], "idle")

    def test_watch_missing_target_and_outage_recovery_behavior(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-f1-missing",
            "targets": [{
                "wave_order": 1,
                "topic_id": "aidash-aidata",
                "target_repo": "AIDash",
                "herdr_target": "m2-aidash-r1",
                "pane_id": "w5:p1",
                "transport": "herdr",
                "cwd": "~/Development/AIDash",
            }],
        }
        # 1) Permanently missing target retries until max_polls and returns ok=False, all_settled=False, needs_owner_attention=True
        polls_seen = {"count": 0}

        def missing_runner(args, **kwargs):
            if args[1:3] in (["pane", "list"], ["agent", "list"]):
                return SimpleNamespace(returncode=1, stdout="", stderr="herdr daemon unavailable")
            raise AssertionError(args)

        missing_res = self.router.watch_dispatched_m2(
            plan,
            interval=0.01,
            max_wait=5.0,
            max_polls=3,
            runner=missing_runner,
            sleep_fn=lambda _: polls_seen.__setitem__("count", polls_seen["count"] + 1),
        )
        self.assertEqual(missing_res["polls"], 3)
        self.assertFalse(missing_res["ok"])
        self.assertFalse(missing_res["all_settled"])
        self.assertTrue(missing_res["needs_owner_attention"])
        self.assertEqual(missing_res["targets"][0]["status"], "missing")
        self.assertEqual(missing_res["targets"][0]["blocked_reason"], "target_missing_or_unreachable")

        # 2) Transient discovery outage on poll 0 followed by recovery on poll 1 -> working -> poll 2 idle succeeds
        step_state = {"poll": 0}

        def recovery_runner(args, **kwargs):
            p = step_state["poll"]
            cmd = args[1:3]
            if p == 0:
                return SimpleNamespace(returncode=1, stdout="", stderr="transient socket error")
            if cmd == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"panes": [{"pane_id": "w5:p1", "name": "m2-aidash-r1"}]}}), stderr="")
            if cmd == ["agent", "list"]:
                st = "working" if p == 1 else "idle"
                return SimpleNamespace(returncode=0, stdout=json.dumps({"result": {"agents": [{"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": st, "completion_seq": p}]}}), stderr="")
            if cmd == ["agent", "read"]:
                return SimpleNamespace(returncode=0, stdout="recovered and done", stderr="")
            raise AssertionError(args)

        rec_res = self.router.watch_dispatched_m2(
            plan,
            interval=0.01,
            max_wait=5.0,
            max_polls=5,
            runner=recovery_runner,
            sleep_fn=lambda _: step_state.__setitem__("poll", step_state["poll"] + 1),
        )
        self.assertEqual(rec_res["polls"], 3)
        self.assertTrue(rec_res["ok"])
        self.assertTrue(rec_res["all_settled"])
        self.assertFalse(rec_res["needs_owner_attention"])
        self.assertEqual(rec_res["targets"][0]["status"], "idle")

    def test_watch_preserves_business_ask_user_and_non_whitelisted_commands_for_owner(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-watch-owner",
            "targets": [
                {
                    "wave_order": 1,
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "herdr_target": "m2-aidash-r1",
                    "pane_id": "w5:p1",
                    "transport": "herdr",
                    "cwd": "~/Development/AIDash",
                },
                {
                    "wave_order": 2,
                    "topic_id": "learning-and-blog",
                    "target_repo": "Personal",
                    "herdr_target": "m2-personal-r1",
                    "pane_id": "w6:p1",
                    "transport": "herdr",
                    "cwd": "~/Development/Personal",
                },
            ],
        }
        sent_keys = []

        def runner(args, **kwargs):
            cmd = args[1:3]
            if cmd == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [
                    {"pane_id": "w5:p1", "name": "m2-aidash-r1"},
                    {"pane_id": "w6:p1", "name": "m2-personal-r1"},
                ]}), stderr="")
            if cmd == ["agent", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [
                    {"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": "blocked"},
                    {"pane_id": "w6:p1", "name": "m2-personal-r1", "agent_status": "needs_input"},
                ]}), stderr="")
            if cmd == ["agent", "read"]:
                target_name = args[3]
                if target_name == "m2-aidash-r1":
                    return SimpleNamespace(returncode=0, stdout="ask_user: 请选择方案 A 或方案 B (Owner decision required)", stderr="")
                return SimpleNamespace(returncode=0, stdout="CMD_CONFIRM: Confirm command\nCommand: git push --force origin main\n", stderr="")
            if cmd == ["agent", "send-keys"]:
                sent_keys.append(args)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        watch_res = self.router.watch_dispatched_m2(
            plan,
            interval=0.01,
            max_wait=5.0,
            max_polls=2,
            herdr_bin="herdr",
            runner=runner,
            sleep_fn=lambda _: None,
        )
        self.assertEqual(sent_keys, [])
        self.assertTrue(watch_res["all_settled"])
        self.assertTrue(watch_res["needs_owner_attention"])
        for t in watch_res["targets"]:
            self.assertTrue(t["needs_owner_attention"])
            self.assertEqual(t["blocked_reason"], "owner_decision_required")
            self.assertEqual(t["auto_unblocked"], [])
        self.assertIn("NEEDS OWNER (owner_decision_required)", watch_res["summary_markdown"])

    def test_watch_handles_initial_idle_race_before_declaring_settled(self):
        plan = {
            "schema_version": 1,
            "plan_id": "m2p-watch-race",
            "targets": [{
                "wave_order": 1,
                "topic_id": "aidash-aidata",
                "target_repo": "AIDash",
                "herdr_target": "m2-aidash-r1",
                "pane_id": "w5:p1",
                "transport": "herdr",
                "cwd": "~/Development/AIDash",
            }],
        }
        state = {"poll": 0}

        def runner(args, **kwargs):
            cmd = args[1:3]
            if cmd == ["pane", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"panes": [{"pane_id": "w5:p1", "name": "m2-aidash-r1"}]}), stderr="")
            if cmd == ["agent", "list"]:
                p = state["poll"]
                # Poll 0: still idle right after prompt; Poll 1: working; Poll 2: idle with completion_seq incremented
                status = "idle" if p == 0 else ("working" if p == 1 else "idle")
                seq = 10 if p < 2 else 11
                return SimpleNamespace(returncode=0, stdout=json.dumps({"agents": [
                    {"pane_id": "w5:p1", "name": "m2-aidash-r1", "agent_status": status, "completion_seq": seq},
                ]}), stderr="")
            if cmd == ["agent", "read"]:
                return SimpleNamespace(returncode=0, stdout=f"poll-{state['poll']}-output", stderr="")
            raise AssertionError(args)

        watch_res = self.router.watch_dispatched_m2(
            plan,
            interval=0.01,
            max_wait=5.0,
            max_polls=5,
            herdr_bin="herdr",
            runner=runner,
            sleep_fn=lambda _: state.__setitem__("poll", state["poll"] + 1),
        )
        self.assertEqual(watch_res["polls"], 3)
        self.assertTrue(watch_res["all_settled"])
        self.assertEqual(watch_res["targets"][0]["status"], "idle")
        self.assertEqual(watch_res["targets"][0]["completion_seq"], 11)
        self.assertIn("poll-2-output", watch_res["targets"][0]["visible_summary"])

    def test_cli_watch_mode_outputs_structured_json(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path = Path(temp) / "plan.json"
            receipt_path = Path(temp) / "receipt.json"
            plan_path.write_text(json.dumps({
                "schema_version": 1,
                "plan_id": "m2p-cli-watch",
                "targets": [{
                    "wave_order": 1,
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "transport": "stdout",
                }],
            }), encoding="utf-8")
            receipt_path.write_text(json.dumps({
                "ok": True,
                "plan_id": "m2p-cli-watch",
                "receipts": [{
                    "wave_order": 1,
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "method": "stdout",
                    "target": "AIDash",
                    "prompt": "simulated prompt",
                }],
            }), encoding="utf-8")
            result = subprocess.run([
                str(ROUTER_PATH), "--watch", "--plan-file", str(plan_path),
                "--apply-receipt-file", str(receipt_path), "--max-polls", "1", "--json",
            ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["all_settled"])
        self.assertEqual(payload["targets"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
