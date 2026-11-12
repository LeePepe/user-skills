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
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            result = subprocess.run([
                str(ROUTER_PATH), "--plan", "--message", "inspect alpha", "--herdr-json", str(fixture_path),
                "--no-dsh", "--json", "--mock-jev-json", json.dumps({"route": "ROUTE_SINGLE", "targets": [{"target_repo": "alpha"}]}),
            ], capture_output=True, text=True, check=False)
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


if __name__ == "__main__":
    unittest.main()
