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

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.test_root = Path(temp.name)
        env = patch.dict("os.environ", {
            "M2_SUPERVISION_STATE_FILE": str(self.test_root / "state.json"),
            "M2_HANDOFF_ROOT": str(self.test_root / "handoff"),
        })
        env.start()
        self.addCleanup(env.stop)

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
        self.assertEqual(len(domain_map["topics"]), 25)
        self.assertEqual({topic["topic_id"] for topic in domain_map["topics"]} & {"financial", "shared-design-system"}, {"financial", "shared-design-system"})
        self.assertEqual(next(topic for topic in domain_map["topics"] if topic["topic_id"] == "financial")["space"], "Financial")
        self.assertEqual(next(topic for topic in domain_map["topics"] if topic["topic_id"] == "shared-design-system")["space"], "shared-design-system")
        self.assertTrue(all(topic.get("space") for topic in domain_map["topics"]))
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
            self.assertEqual(len(registered["topics"]), 26)
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
        self.assertEqual(len([cmd for cmd in calls if cmd[1:3] == ["agent", "prompt"]]), 1)

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
        dispatch = next(cmd for cmd in calls if cmd[0] == "/tmp/fake-herdr-dispatch")
        self.assertTrue("--new-workspace" in dispatch or "--pane" in dispatch)

    def test_cli_plan_outputs_json_without_dispatch_or_temp_markdown(self):
        fixture = {"agents": [{"name": "alpha-m2-r1", "agent_status": "working", "repo": "alpha"}]}
        with tempfile.TemporaryDirectory() as temp:
            fixture_path = Path(temp) / "agents.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            result = subprocess.run([
                str(ROUTER_PATH), "--plan", "--message", "inspect alpha", "--herdr-json", str(fixture_path),
                "--no-dsh", "--json",
                "--mock-jev-json", json.dumps({"route": "ROUTE_SINGLE", "targets": [{"target_repo": "alpha"}]}),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(list(Path(temp).glob("*.md")), [])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["targets"][0]["m2_status"], "EXISTING_M2")
        self.assertIn("preview_markdown", payload)
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
            dispatch_calls = [call for call in calls if "--cwd" in call]
            self.assertEqual(len(dispatch_calls), 2)
            for call_args, receipt in zip(dispatch_calls, result["receipts"]):
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

    def test_plan_renders_linear_markdown_and_terminal_preview_without_orca_or_temp_md(self):
        plan = self.router.build_plan(
            "先检查通知开关，再写学习博客",
            self._learning_sessions(),
            mock_magpie={"route": "ROUTE_CROSS_SESSION", "targets": [
                {"topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "sub_prompt": "检查通知开关详情\n包含多行说明", "rag_context_hint": "upstream-notif-hint", "wave_order": 1},
                {"topic_id": "learning-and-blog", "target_repo": "Personal", "sub_prompt": "整理学习博客", "wave_order": 2},
            ]},
        )
        self.assertNotIn("preview_markdown_path", plan)
        self.assertNotIn("preview_open", plan)
        markdown = plan["preview_markdown"]
        self.assertIn(f"# M2 Dispatch Plan · {plan['plan_id']} (ROUTE_CROSS_SESSION)", markdown)
        self.assertIn("Gemini 4 Argon · 983K context", markdown)
        self.assertIn("检查通知开关详情\n包含多行说明", markdown)
        self.assertIn("upstream-notif-hint", markdown)
        self.assertIn("## Stale / Duplicate M2 Cleanup Candidates", markdown)
        terminal = self.router.preview_text(plan)
        self.assertIn("wave | topic_id | space", terminal)
        self.assertIn("检查通知开关详情", terminal)
        self.assertIn("rag_context_hint: upstream-notif-hint", terminal)
        self.assertIn("upstream-notif-hint", markdown)

        with tempfile.TemporaryDirectory() as temp:
            explicit_path = Path(temp) / "linear-preview.md"
            exported = self.router.build_plan(
                "检查 AIDash",
                [],
                mock_magpie={"route": "ROUTE_SINGLE", "targets": [{"target_repo": "AIDash", "sub_prompt": "check", "wave_order": 1}]},
                preview_md_file=explicit_path,
            )
            self.assertTrue(explicit_path.is_file())
            self.assertEqual(exported["preview_markdown"], explicit_path.read_text(encoding="utf-8"))

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
            "orca file open /tmp/m2-plan-test.md --focus",
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
                if args[1:3] == ["workspace", "list"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"workspaces": []}), stderr="")
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


    def test_done_interactive_session_and_workspace_label_are_reusable(self):
        done = self.router.normalize_session({
            "name": "aidash-m2-r1", "agent_status": "done", "repo": "AIDash",
            "pane_id": "w1P:p1", "workspace_id": "w1P", "agent": "copilot", "cwd": "/repo/AIDash",
        }, "herdr", allow_interactive_herdr=True)
        self.assertIsNotNone(done)
        self.assertEqual(done["status"], "done")
        sessions, warning = self.router.discover_herdr(
            pane_payload={"panes": [{"pane_id": "w1P:p1", "workspace_id": "w1P", "agent": "copilot", "cwd": "/repo/AIDash", "agent_status": "done", "name": "AIDash"}]},
            workspace_payload={"workspaces": [{"workspace_id": "w1P", "label": "AIDash"}]},
        )
        self.assertIsNone(warning)
        self.assertEqual(sessions[0]["workspace_label"], "AIDash")

    def test_new_m2_uses_mapped_workspace_and_never_caller_pane(self):
        calls = []
        plan = {"schema_version": 1, "plan_id": "m2p-space", "approval": {"approved": False}, "discovery": {"workspaces": [{"workspace_id": "w1P", "label": "AIDash"}]}, "targets": [{
            "wave_order": 1, "topic_id": "aidash-aidata", "target_repo": "AIDash", "space": "AIDash", "space_aliases": [],
            "target_workspace_id": "w1P", "m2_status": "NEW_M2", "transport": "herdr", "cwd": "/repo/AIDash", "sub_prompt": "start", "dispatch_script": "/tmp/fake-dispatch",
        }]}
        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["workspace", "list"]:
                return __import__("types").SimpleNamespace(returncode=0, stdout=json.dumps({"workspaces": [{"workspace_id": "w1P", "label": "AIDash"}]}), stderr="")
            return __import__("types").SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "workspace_id": "w1P", "tab_id": "t1", "pane_id": "w1P:p1"}), stderr="")
        with patch.dict("os.environ", {"HERDR_PANE_ID": "w2J:p1"}):
            result = self.router.apply_plan(plan, approve=True, runner=runner)
        dispatch = next(call for call in calls if "--workspace" in call)
        self.assertIn("--workspace", dispatch)
        self.assertEqual(dispatch[dispatch.index("--workspace") + 1], "w1P")
        self.assertEqual(dispatch[dispatch.index("--tab-label") + 1], "m2-aidash-aidata-r1")
        self.assertNotIn("--pane", dispatch)
        self.assertNotIn("w2J:p1", dispatch)
        self.assertEqual(result["receipts"][0]["spawn_mode"], "workspace_tab")

    def test_new_space_is_created_once_then_reused_for_same_space(self):
        calls = []
        targets = [{"wave_order": 1, "topic_id": "new-a", "target_repo": "Alpha", "space": "Personal", "space_aliases": [], "m2_status": "NEW_M2", "transport": "herdr", "cwd": "/repo/Alpha", "sub_prompt": "a", "dispatch_script": "/tmp/fake-dispatch"}, {"wave_order": 1, "topic_id": "new-b", "target_repo": "Beta", "space": "Personal", "space_aliases": [], "m2_status": "NEW_M2", "transport": "herdr", "cwd": "/repo/Beta", "sub_prompt": "b", "dispatch_script": "/tmp/fake-dispatch"}]
        plan = {"schema_version": 1, "plan_id": "m2p-new-space", "approval": {"approved": False}, "discovery": {"workspaces": []}, "targets": targets}
        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["workspace", "list"]:
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            return __import__("types").SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "workspace_id": "w-new", "pane_id": "w-new:p1"}), stderr="")
        self.router.apply_plan(plan, approve=True, runner=runner)
        dispatch_calls = [call for call in calls if "--new-workspace" in call or "--workspace" in call]
        self.assertIn("--new-workspace", dispatch_calls[0])
        self.assertIn("--workspace", dispatch_calls[1])
        self.assertEqual(dispatch_calls[1][dispatch_calls[1].index("--workspace") + 1], "w-new")

    def test_real_flow_preserves_embedded_workspaces_and_reuses_done_space_session(self):
        sessions, warnings = self.router.discover_sessions(
            herdr_payload={
                "panes": [
                    {"pane_id": "w-a:p1", "workspace_id": "w-a", "cwd": "/repo/other", "agent": "copilot", "name": "aidash-done", "agent_status": "done"},
                    {"pane_id": "w-o:p1", "workspace_id": "w-o", "cwd": "/repo/AIDash", "agent": "copilot", "name": "大总管", "agent_status": "working"},
                    {"pane_id": "w-g:p1", "workspace_id": "w-a", "cwd": "/repo/AIDash", "agent": "copilot", "name": "aidash-goal-tl-r1", "agent_status": "working"},
                    {"pane_id": "w-x:p1", "workspace_id": "w-a", "cwd": "/repo/AIDash", "agent": "copilot", "name": "aidash-exited", "agent_status": "exited"},
                ],
                "workspaces": [
                    {"workspace_id": "w-a", "label": "AIDash"},
                    {"workspace_id": "w-o", "label": "大总管"},
                ],
            },
            include_dsh=False,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(sessions.workspaces[0]["workspace_id"], "w-a")
        self.assertIn("aidash-done", {item["session_id"] for item in sessions})
        self.assertNotIn("aidash-goal-tl-r1", {item["session_id"] for item in sessions})
        self.assertNotIn("aidash-exited", {item["session_id"] for item in sessions})
        plan = self.router.build_plan(
            "继续处理 AIDash 指标",
            sessions,
            targets_json={"route": "ROUTE_SINGLE", "targets": [{"topic_id": "aidash-aidata", "target_repo": "AIDash", "sub_prompt": "continue"}]},
            write_preview_md=False,
        )
        self.assertEqual(plan["discovery"]["workspaces"][0]["label"], "AIDash")
        self.assertEqual(plan["targets"][0]["m2_status"], "EXISTING_M2")
        self.assertEqual(plan["targets"][0]["session_id"], "aidash-done")

    def test_apply_revalidates_live_canonical_space_and_ignores_stale_workspace_id(self):
        calls = []
        plan = {
            "schema_version": 1, "plan_id": "m2p-authority", "approval": {"approved": False},
            "discovery": {"workspaces": [{"workspace_id": "w-stale", "label": "AIDash"}]},
            "targets": [{
                "wave_order": 1, "topic_id": "aidash-aidata", "target_repo": "AIDash",
                "space": "Wrong Space", "space_aliases": ["Wrong Alias"], "target_workspace_id": "w-stale",
                "m2_status": "NEW_M2", "transport": "herdr", "cwd": "/repo/AIDash",
                "sub_prompt": "start", "dispatch_script": "/tmp/fake-dispatch",
            }],
        }

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:3] == ["workspace", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"workspaces": [{"workspace_id": "w-live", "label": "AIDash"}]}), stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "workspace_id": "w-live"}), stderr="")

        result = self.router.apply_plan(plan, approve=True, runner=runner)
        dispatch = next(call for call in calls if "--workspace" in call)
        self.assertEqual(dispatch[dispatch.index("--workspace") + 1], "w-live")
        self.assertEqual(result["receipts"][0]["workspace_id"], "w-live")

    def test_space_matching_and_markdown_include_alias_priority_and_workspace_state(self):
        workspaces = [
            {"workspace_id": "w-alias", "label": "Sapphire"},
            {"workspace_id": "w-canonical", "label": "bing-growth-sapphire-app"},
        ]
        target = {"target_repo": "bing-growth-sapphire-app", "topic_id": "sapphire-notification"}
        self.router.resolve_target_space(target, self.router._topic_by_id(self.router.load_domain_map())["sapphire-notification"], workspaces)
        self.assertEqual(target["target_workspace_id"], "w-canonical")
        sessions = [{
            "session_id": "space-session", "target_repo": "unrelated", "title": "Notification",
            "summary": "push notification", "status": "done", "source": "herdr",
            "herdr_target": "space-session", "pane_id": "w-canonical:p1",
            "workspace_id": "w-canonical", "workspace_label": "bing-growth-sapphire-app",
        }]
        self.assertEqual(self.router._target_session(target, self.router._topic_by_id(self.router.load_domain_map())["sapphire-notification"], sessions)["session_id"], "space-session")
        plan = {"plan_id": "m2p-preview", "decision": {"route": "ROUTE_SINGLE"}, "targets": [{**target, "m2_status": "NEW_M2"}]}
        markdown = self.router.render_preview_markdown(plan)
        self.assertIn("| Space |", markdown)
        self.assertIn("bing-growth-sapphire-app (w-canonical)", markdown)
        self.assertIn("- **Space**:", markdown)

    def test_discovered_mixed_case_spaces_and_aliases_reuse_mapped_sessions(self):
        topics = self.router._topic_by_id(self.router.load_domain_map())
        for topic_id in ("sapphire-notification", "dsh-plugins", "nas-smart-home", "aidash-aidata"):
            topic = topics[topic_id]
            for label in [topic["space"], *topic.get("space_aliases", [])]:
                with self.subTest(topic=topic_id, label=label):
                    sessions, warnings = self.router.discover_sessions(
                        herdr_payload={
                            "panes": [{"pane_id": "w-topic:p1", "workspace_id": "w-topic", "name": "topic-m2",
                                       "agent": "copilot", "agent_status": "done", "cwd": "/repo/unrelated", "topic_id": topic_id}],
                            "workspaces": [{"workspace_id": "w-topic", "label": label.swapcase()}],
                        },
                        include_dsh=False,
                    )
                    plan = self.router.build_plan(
                        "continue", sessions, targets_json=[{"topic_id": topic_id}], write_preview_md=False,
                    )
                    self.assertEqual(warnings, [])
                    self.assertEqual(plan["targets"][0]["target_workspace_id"], "w-topic")
                    self.assertEqual(plan["targets"][0]["m2_status"], "EXISTING_M2")
                    self.assertEqual(plan["targets"][0]["session_id"], "topic-m2")

    def test_session_only_discovery_never_invents_workspace_inventory(self):
        for collection in ("panes", "agents", "sessions"):
            for workspace_label in ("", "AIDash"):
                with self.subTest(collection=collection, label=workspace_label):
                    record = {"pane_id": "w-a:p1", "workspace_id": "w-a", "name": "conversation-m2",
                              "title": "Conversation", "agent": "copilot", "agent_status": "done",
                              "cwd": "/repo/AIDash", "workspace_label": workspace_label}
                    sessions, warnings = self.router.discover_sessions(
                        herdr_payload={collection: [record]}, include_dsh=False,
                    )
                    self.assertEqual(warnings, [])
                    self.assertEqual(sessions.workspaces, [])
                    self.assertEqual(sessions[0]["workspace_label"], workspace_label)
                    plan = self.router.build_plan(
                        "continue", sessions, targets_json=[{"topic_id": "aidash-aidata"}], write_preview_md=False,
                    )
                    self.assertEqual(plan["discovery"]["workspaces"], [])
                    self.assertEqual(plan["targets"][0]["m2_status"], "EXISTING_M2")
                    self.assertEqual(plan["targets"][0]["session_id"], "conversation-m2")
                    for payload in ([record], {collection: [record]}, {"result": {collection: [record]}}):
                        self.assertEqual(self.router._workspace_records(payload), [])

    def test_workspace_inventory_shapes_and_explicit_empty_discovery(self):
        inventory = [{"workspace_id": "w-a", "label": "AIDash"}]
        for payload in (inventory, {"workspaces": inventory}, {"result": {"workspaces": inventory}}, []):
            with self.subTest(payload=payload):
                sessions, warnings = self.router.discover_sessions(
                    herdr_payload={
                        "panes": [{"pane_id": "w-a:p1", "workspace_id": "w-a", "name": "conversation-m2",
                                   "agent": "copilot", "agent_status": "done", "cwd": "/repo/AIDash"}],
                        "workspaces": [{"workspace_id": "w-old", "label": "Stale"}],
                    },
                    workspace_payload=payload, include_dsh=False,
                )
                expected = inventory if payload else []
                self.assertEqual(warnings, [])
                self.assertEqual([item["workspace_id"] for item in sessions.workspaces], [item["workspace_id"] for item in expected])
                self.assertEqual(sessions[0]["workspace_label"], "AIDash" if expected else "")
                plan = self.router.build_plan(
                    "continue", sessions, targets_json=[{"topic_id": "aidash-aidata"}], write_preview_md=False,
                )
                self.assertEqual(plan["discovery"]["workspaces"], sessions.workspaces)
                empty_plan = self.router.build_plan(
                    "continue", sessions, workspace_payload=[],
                    targets_json=[{"topic_id": "aidash-aidata"}], write_preview_md=False,
                )
                self.assertEqual(empty_plan["discovery"]["workspaces"], [])

    def test_unmapped_explicit_session_obeys_workspace_boundaries(self):
        for topic_id in ("", self.router.NEW_UNMAPPED_DOMAIN, "new-topic-quantlab-risk"):
            for label, workspace_id, expected in (
                ("大总管", "w-owner", False), ("Other", "w-other", False),
                ("", "w-other", False), ("QuantLab", "w-quant", True), ("", "w-quant", True),
            ):
                with self.subTest(topic=topic_id, label=label, workspace=workspace_id):
                    sessions, _ = self.router.discover_sessions(
                        herdr_payload={"panes": [{
                            "name": "quant-m2", "pane_id": f"{workspace_id}:p1", "workspace_id": workspace_id,
                            "workspace_label": label, "cwd": "/repo/QuantLab", "agent": "copilot", "agent_status": "done",
                        }]}, include_dsh=False,
                    )
                    target = {"topic_id": topic_id, "target_repo": "QuantLab", "space": "QuantLab",
                              "session_id": "quant-m2", "target_workspace_id": "w-quant"}
                    self.assertEqual(self.router._target_session(target, None, sessions) is not None, expected)
        unnamed = self.router.normalize_session({"session_id": "unlabelled", "target_repo": "QuantLab"}, "dsh")
        self.assertEqual(self.router._target_session({"session_id": "unlabelled"}, None, [unnamed]), unnamed)

    def test_apply_custom_domain_map_authority_and_new_domain_registration(self):
        original_map = self.router.load_domain_map()
        for mode in ("explicit", "plan_metadata", "default", "registration"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                map_path = Path(temp) / "custom-map.json"
                custom_map = json.loads(Path(original_map["path"]).read_text(encoding="utf-8"))
                for group in custom_map["groups"]:
                    for topic in group["topics"]:
                        if topic["topic_id"] == "aidash-aidata":
                            topic.update(space="Custom Space", space_aliases=["Custom Alias"])
                map_path.write_text(json.dumps(custom_map), encoding="utf-8")
                registration = mode == "registration"
                raw_target = {"topic_id": self.router.NEW_UNMAPPED_DOMAIN if registration else "aidash-aidata",
                              "target_repo": "QuantLab" if registration else "AIDash", "sub_prompt": "continue",
                              "space": "Registered Space", "space_aliases": ["Registered Alias"]}
                plan = self.router.build_plan(
                    "continue", [], targets_json=[raw_target], write_preview_md=False,
                    domain_map_file=map_path if mode == "plan_metadata" else None,
                )
                target = plan["targets"][0]
                target.update(space="Untrusted", space_aliases=["Untrusted"], transport="herdr", dispatch_script="/tmp/fake-dispatch")
                if registration:
                    target["proposed_domain"] = {**target["proposed_domain"], "space": "Untrusted", "space_aliases": ["Untrusted"]}
                expected_space = "Registered Space" if registration else "AIDash" if mode == "default" else "Custom Space"
                expected_alias = "Registered Alias" if registration else "AIDash" if mode == "default" else "Custom Alias"
                calls = []

                def runner(args, **kwargs):
                    calls.append(args)
                    stdout = {"workspaces": [{"workspace_id": "w-custom", "label": expected_alias}]} if args[1:3] == ["workspace", "list"] else {"ok": True, "workspace_id": "w-custom"}
                    return SimpleNamespace(returncode=0, stdout=json.dumps(stdout), stderr="")

                result = self.router.apply_plan(
                    plan, approve=True, register_new_domains=registration, runner=runner,
                    domain_map_file=map_path if mode in {"explicit", "registration"} else None,
                )
                dispatch = next(call for call in calls if call[0] == "/tmp/fake-dispatch")
                self.assertIn("--workspace", dispatch)
                self.assertEqual(dispatch[dispatch.index("--workspace") + 1], "w-custom")
                self.assertEqual(result["receipts"][0]["space"], expected_space)
                if registration:
                    self.assertEqual(result["registered_new_domains"], [target["topic_id"]])
                    registered_topic = self.router._topic_by_id(self.router.load_domain_map(map_path))[target["topic_id"]]
                    self.assertEqual(registered_topic["space"], "Registered Space")
                    self.assertEqual(registered_topic["space_aliases"], ["Registered Alias"])

    def test_apply_distinguishes_empty_live_inventory_from_invalid_success(self):
        canonical = {"workspace_id": "w-canonical", "label": "bing-growth-sapphire-app"}
        alias = {"workspace_id": "w-alias", "label": "Sapphire"}
        cases = [
            ("[]", ""), ('{"workspaces": []}', ""), ('{"result": {"workspaces": []}}', ""),
            (json.dumps([alias]), "w-alias"),
            (json.dumps({"workspaces": [canonical, alias]}), "w-canonical"),
            (json.dumps({"result": {"workspaces": [alias, canonical]}}), "w-canonical"),
            ('[{"workspace_id": "w-unnamed", "label": ""}]', ""),
            ('{"ok": true, "workspace_id": "w-receipt"}', "w-canonical"),
            ('{"ok": true, "result": []}', "w-canonical"),
            ('{"ok": false, "workspaces": []}', "w-canonical"),
            ('{"error": "unavailable"}', "w-canonical"), ("not json", "w-canonical"),
            ('{"workspaces": "invalid"}', "w-canonical"), ("[{}]", "w-canonical"),
            ('{"result": {"agents": [{"name": "wrong", "workspace_id": "w-receipt"}]}}', "w-canonical"),
        ]
        for stdout, expected_workspace in cases:
            with self.subTest(stdout=stdout):
                plan = self.router.build_plan(
                    "continue", [], workspace_payload=[alias, canonical],
                    targets_json=[{"topic_id": "sapphire-notification", "transport": "herdr", "dispatch_script": "/tmp/fake-dispatch"}],
                    write_preview_md=False,
                )
                calls = []

                def runner(args, **kwargs):
                    calls.append(args)
                    return SimpleNamespace(returncode=0, stdout=stdout if args[1:3] == ["workspace", "list"] else '{"ok": true}', stderr="")

                result = self.router.apply_plan(plan, approve=True, runner=runner)
                dispatch = next(call for call in calls if call[0] == "/tmp/fake-dispatch")
                if expected_workspace:
                    self.assertIn("--workspace", dispatch)
                    self.assertEqual(dispatch[dispatch.index("--workspace") + 1], expected_workspace)
                    self.assertEqual(result["receipts"][0]["spawn_mode"], "workspace_tab")
                else:
                    self.assertIn("--new-workspace", dispatch)
                    self.assertEqual(dispatch[dispatch.index("--workspace-label") + 1], canonical["label"])
                    self.assertEqual(result["receipts"][0]["spawn_mode"], "new_workspace")

    def test_per_m2_watchdog_tasks_and_rolling_cumulative_rollup_abc_d_a2(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "m2-supervision-state.json"
            plan1_path = Path(temp) / "plan1.json"
            receipt1_path = Path(temp) / "apply1.json"
            plan1 = {
                "schema_version": 1,
                "plan_id": "m2p-wave1-abc",
                "approval": {"approved": False},
                "targets": [
                    {"slot_id": "A", "topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "m2_status": "EXISTING_M2", "transport": "stdout", "sub_prompt": "Task A1", "wave_order": 1},
                    {"slot_id": "B", "topic_id": "info-email", "target_repo": "dsh-personal-plugins", "m2_status": "EXISTING_M2", "transport": "stdout", "sub_prompt": "Task B", "wave_order": 1},
                    {"slot_id": "C", "topic_id": "info-teams", "target_repo": "dsh-personal-plugins", "m2_status": "EXISTING_M2", "transport": "stdout", "sub_prompt": "Task C", "wave_order": 1},
                ],
            }
            plan1_path.write_text(json.dumps(plan1), encoding="utf-8")
            apply1 = self.router.apply_plan(
                plan1,
                approve=True,
                plan_file=plan1_path,
                apply_receipt_file=receipt1_path,
                state_file=state_path,
                transport="stdout",
            )
            self.assertEqual(len(apply1["watchdog_tasks"]), 3)
            for idx, expected_slot in enumerate(["A", "B", "C"]):
                task_meta = apply1["watchdog_tasks"][idx]
                self.assertEqual(task_meta["slot_id"], expected_slot)
                self.assertEqual(task_meta["run_seq"], 1)
                self.assertEqual(task_meta["run_label"], expected_slot)
                self.assertEqual(task_meta["watchdog_copilot_cli"]["mode"], "async")
                self.assertTrue(task_meta["watchdog_copilot_cli"]["detach"])
                self.assertIn(f"--watch-target {expected_slot}", task_meta["watchdog_cmd"])

            # 1) A completes -> reports A
            res_a = self.router.watch_dispatched_m2(
                plan1,
                apply_receipt=apply1,
                watch_target="A",
                state_file=state_path,
                max_polls=1,
            )
            self.assertEqual(res_a["just_settled_labels"], ["A"])
            self.assertEqual(res_a["completed_labels"], ["A"])
            self.assertEqual(res_a["completed_rollup_code"], "A")
            self.assertEqual(res_a["running_labels"], ["B", "C"])
            self.assertFalse(res_a["all_board_settled"])
            self.assertIn("Cumulative Completed Rollup**: `A`", res_a["summary_markdown"])

            # 2) B completes -> reports AB
            res_b = self.router.watch_dispatched_m2(
                plan1,
                apply_receipt=apply1,
                watch_target="B",
                state_file=state_path,
                max_polls=1,
            )
            self.assertEqual(res_b["just_settled_labels"], ["B"])
            self.assertEqual(res_b["completed_labels"], ["A", "B"])
            self.assertEqual(res_b["completed_rollup_code"], "AB")
            self.assertEqual(res_b["running_labels"], ["C"])
            self.assertFalse(res_b["all_board_settled"])
            self.assertIn("Cumulative Completed Rollup**: `AB`", res_b["summary_markdown"])

            # 3) Add D and continue A with new task (A2) while C is still running
            plan2_path = Path(temp) / "plan2.json"
            receipt2_path = Path(temp) / "apply2.json"
            plan2 = {
                "schema_version": 1,
                "plan_id": "m2p-wave2-da2",
                "approval": {"approved": False},
                "targets": [
                    {"slot_id": "D", "topic_id": "learning-and-blog", "target_repo": "Personal", "m2_status": "NEW_M2", "transport": "stdout", "sub_prompt": "Task D", "wave_order": 1},
                    {"slot_id": "A", "topic_id": "sapphire-notification", "target_repo": "bing-growth-sapphire-app", "m2_status": "EXISTING_M2", "transport": "stdout", "sub_prompt": "Task A2 follow-up", "wave_order": 1},
                ],
            }
            plan2_path.write_text(json.dumps(plan2), encoding="utf-8")
            apply2 = self.router.apply_plan(
                plan2,
                approve=True,
                plan_file=plan2_path,
                apply_receipt_file=receipt2_path,
                state_file=state_path,
                transport="stdout",
            )
            self.assertEqual(len(apply2["watchdog_tasks"]), 2)
            a2_meta = next(t for t in apply2["watchdog_tasks"] if t["slot_id"] == "A")
            self.assertEqual(a2_meta["run_seq"], 2)
            self.assertEqual(a2_meta["run_label"], "A2")
            self.assertEqual(apply2["board_rollup"]["completed_rollup_code"], "B")
            self.assertEqual(apply2["board_rollup"]["running_labels"], ["A2", "C", "D"])

            # Supersede guard: a stale run_seq=1 watch from plan1 for A must not overwrite A2's running state
            stale_a1 = self.router.watch_dispatched_m2(
                plan1,
                apply_receipt=apply1,
                watch_target="A",
                state_file=state_path,
                max_polls=1,
            )
            self.assertEqual(stale_a1["completed_rollup_code"], "B")
            self.assertIn("A2", stale_a1["running_labels"])

            # 4) C completes -> reports BC (since A is running A2 and D is running)
            res_c = self.router.watch_dispatched_m2(
                plan1,
                apply_receipt=apply1,
                watch_target="C",
                state_file=state_path,
                max_polls=1,
            )
            self.assertEqual(res_c["just_settled_labels"], ["C"])
            self.assertEqual(res_c["completed_labels"], ["B", "C"])
            self.assertEqual(res_c["completed_rollup_code"], "BC")
            self.assertEqual(res_c["running_labels"], ["A2", "D"])
            self.assertFalse(res_c["all_board_settled"])
            self.assertIn("Cumulative Completed Rollup**: `BC`", res_c["summary_markdown"])

            # 5) A2 completes -> reports A2BC
            res_a2 = self.router.watch_dispatched_m2(
                plan2,
                apply_receipt=apply2,
                watch_target="A",
                state_file=state_path,
                max_polls=1,
            )
            self.assertEqual(res_a2["just_settled_labels"], ["A2"])
            self.assertEqual(res_a2["completed_labels"], ["A2", "B", "C"])
            self.assertEqual(res_a2["completed_rollup_code"], "A2BC")
            self.assertEqual(res_a2["running_labels"], ["D"])
            self.assertFalse(res_a2["all_board_settled"])
            self.assertIn("Cumulative Completed Rollup**: `A2BC`", res_a2["summary_markdown"])

            # 6) D completes -> reports A2BCD via CLI --watch-target D
            cli_res = subprocess.run([
                str(ROUTER_PATH),
                "--watch",
                "--plan-file", str(plan2_path),
                "--apply-receipt-file", str(receipt2_path),
                "--state-file", str(state_path),
                "--watch-target", "D",
                "--max-polls", "1",
                "--json",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(cli_res.returncode, 0, cli_res.stderr)
            res_d = json.loads(cli_res.stdout)
            self.assertEqual(res_d["just_settled_labels"], ["D"])
            self.assertEqual(res_d["completed_labels"], ["A2", "B", "C", "D"])
            self.assertEqual(res_d["completed_rollup_code"], "A2BCD")
            self.assertEqual(res_d["running_labels"], [])
            self.assertTrue(res_d["all_board_settled"])
            self.assertIn("Cumulative Completed Rollup**: `A2BCD`", res_d["summary_markdown"])

    def test_watch_requires_true_quiescence_for_all_visible_activity_markers(self):
        markers = ["1 background /tasks", "2 background /tasks", "esc/ctrl+c to interrupt", "Working", "Thinking", "Running tests", "Waiting for reviewer"]
        plan = {"plan_id": "tail-watch", "targets": [{"slot_id": "A", "herdr_target": "m2-tail", "transport": "herdr", "pane_id": "w8:p1"}]}
        for marker in markers:
            with self.subTest(marker=marker):
                poll = [0]

                def runner(args, **kwargs):
                    if args[1:3] == ["pane", "list"]:
                        payload = {"panes": []}
                    elif args[1:3] == ["agent", "list"]:
                        payload = {"agents": [{"name": "m2-tail", "pane_id": "w8:p1", "agent_status": "idle"}]}
                    elif args[1:3] == ["agent", "read"]:
                        return SimpleNamespace(returncode=0, stdout=(marker + "\n" * 10) if poll[0] < 2 else "Delivered successfully", stderr="")
                    else:
                        self.fail(str(args))
                    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

                active = self.router.watch_dispatched_m2(plan, runner=runner, max_polls=1)
                self.assertFalse(active["all_settled"])
                target = active["targets"][0]
                self.assertEqual((target["raw_status"], target["status"]), ("working", "working"))
                self.assertFalse(target["quiescent"])
                self.assertTrue(target["seen_working"])
                settled = self.router.watch_dispatched_m2(plan, runner=runner, max_polls=4, interval=0, sleep_fn=lambda _: poll.__setitem__(0, poll[0] + 1))
                self.assertEqual(settled["polls"], 3)
                self.assertTrue(settled["all_settled"])
                self.assertIn("A", settled["just_settled_labels"])
        self.assertFalse(self.router._has_active_visible_tail("Working\n" + "\n".join(["finished"] * 8)))
        self.assertTrue(self.router._has_active_visible_tail("\x1b[32mThinking\x1b[0m"))

    def test_global_rollup_discovers_cross_plan_m2s_and_handoff_dependency_stages(self):
        root = self.test_root / "handoff"
        goal = root / "voxpocket-holistic"
        goal.mkdir(parents=True)
        (goal / "ledger.md").write_text(
            "# Goal: voxpocket-holistic\n- Linear: TIA-420 (Parent TIA-375)\n- TL: voxpocket-goal-tl-r3\n"
            "## Tasks\n| ID | 依赖 | 标题 | 负责 | 状态 | PR |\n"
            "| T4-rebuild | — | Stage 1 Designer -> Stage 2 Coding | writer-r1 | in_progress | — |\n"
            "## Events\n- EVT=IN_PROGRESS | Stage 1 Designer -> Stage 2 Coding\n", encoding="utf-8")
        state_path = self.test_root / "global.json"
        upstream_plan = {"schema_version": 1, "plan_id": "round-1", "targets": [
            {"slot_id": "A", "topic_id": "team-lead", "sub_prompt": "TIA-426", "transport": "stdout", "wave_order": 1},
            {"slot_id": "B", "topic_id": "my-designer", "sub_prompt": "TIA-424", "transport": "stdout", "wave_order": 1},
        ]}
        receipt = self.router.apply_plan(upstream_plan, approve=True, state_file=state_path, apply_receipt_file=self.test_root / "r1.json", transport="stdout")
        self.router.watch_dispatched_m2(upstream_plan, apply_receipt=receipt, watch_target="A", state_file=state_path, max_polls=1)
        current_plan = {"schema_version": 1, "plan_id": "round-2", "targets": [
            {"slot_id": "C", "topic_id": "VoxPocket", "sub_prompt": f"Parent TIA-375; Goal TIA-420 {goal}", "transport": "stdout", "wave_order": 2, "depends_on": ["TIA-426", "TIA-424"]},
        ]}
        receipt2 = self.router.apply_plan(current_plan, approve=True, state_file=state_path, apply_receipt_file=self.test_root / "r2.json", transport="stdout", handoff_root=root)
        self.assertIn("Wave 2: TIA-420", receipt2["wave_dependency_chain"])
        records = [
            {"name": "m2-voxpocket", "pane_id": "w9:p1", "topic_id": "VoxPocket", "agent_status": "idle", "terminal_title": "TIA-420", "visible": "Delivered\n1 background /tasks", "wave_order": 2, "depends_on": ["TIA-426", "TIA-424"]},
            {"name": "m2-linear", "pane_id": "w10:p1", "agent_status": "working", "terminal_title": "TIA-18", "visible": "Running bridge"},
            {"name": "other-writer-r1", "pane_id": "w11:p1", "agent_status": "working"},
        ]
        # Bind the live pane to the prior-plan slot, which was once misclassified idle.
        state = json.loads(state_path.read_text())
        slot = next(s for s in state["slots"] if s["slot_id"] == "C")
        slot.update(target="m2-voxpocket", pane_id="w9:p1", status="idle")
        state_path.write_text(json.dumps(state))
        result = self.router.supervision_status(state_file=state_path, agent_records=records, handoff_root=root)
        self.assertEqual([s["slot_id"] for s in result["in_flight_details"]], ["B", "C", "m2-linear"])
        vox = next(s for s in result["in_flight_details"] if s["slot_id"] == "C")
        self.assertEqual(vox["linear_issue"], "TIA-420")
        self.assertEqual(vox["parent_linear_issue"], "TIA-375")
        self.assertEqual(vox["raw_status"], "working")
        self.assertIn("T4-rebuild (in_progress, writer-r1)", vox["m1_progress"])
        self.assertIn("Stage 1 Designer -> Stage 2 Coding", vox["m1_progress"])
        chain = result["wave_dependency_chain"]
        self.assertIn("Wave 1: TIA-426 (team-lead) + TIA-424 (my-designer)", chain)
        self.assertIn("-> Wave 2: TIA-420 (VoxPocket: Stage 1 Designer -> Stage 2 Coding)", chain)
        md = result["unified_rollup_markdown"]
        for section in ("🔔 本次完成", "✅ 累计已完成", "⏳ 仍在运行中", "TIA-18", "依赖 Wave 1: TIA-426, TIA-424", "w9:p1"):
            self.assertIn(section, md)
        self.assertEqual(json.loads(state_path.read_text())["slots"][2]["status"], "working")
        # A running handoff also keeps an idle M2 in-flight after its visible marker disappears.
        records[0]["visible"] = "Delivered"
        self.assertEqual(next(s for s in self.router.supervision_status(state_file=state_path, agent_records=records, handoff_root=root)["slots"] if s["slot_id"] == "C")["status"], "working")
        # A target watchdog that just verified true quiescence wins over stale ledger tasks.
        watch_plan = {"plan_id": "round-2", "targets": [{"slot_id": "C", "herdr_target": "m2-voxpocket", "pane_id": "w9:p1", "transport": "herdr", "handoff_dir": str(goal)}]}

        def runner(args, **kwargs):
            payload = {"agents": records} if args[1:3] == ["agent", "list"] else {"panes": []}
            if args[1:3] == ["agent", "read"]:
                return SimpleNamespace(returncode=0, stdout="Delivered", stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        completed = self.router.watch_dispatched_m2(watch_plan, watch_target="C", state_file=state_path, handoff_root=root, runner=runner, max_polls=1)
        self.assertIn("C", completed["completed_labels"])
        self.assertIn("m2-linear", completed["running_labels"])
        self.assertIn(completed["unified_rollup_markdown"], completed["summary_markdown"])

    def test_visible_watchdog_goal_rebinds_stale_title_and_discovered_handoff(self):
        root = self.test_root / "handoff"
        old = root / "voxpocket-tenfold-r1"
        current = root / "voxpocket-holistic-redesign"
        for path, issue, task, status in ((old, "TIA-409", "T1-old", "done"), (current, "TIA-420", "T4-two-stage-rebuild", "in_progress")):
            path.mkdir(parents=True)
            tl = "voxpocket-holistic-goal-tl-r3" if path == current else f"{path.name}-goal-tl-r1"
            (path / "ledger.md").write_text(
                f"# Goal: {path.name}\n- Linear: {issue} (Parent TIA-375)\n- TL: {tl}\n"
                f"## Tasks\n| {task} | — | Rebuild via strict 2-stage Designer (--role designer) -> Coding (--role writer) pipeline | writer | {status} | — |\n"
                + ("## Events\n- EVT=GOAL_DONE\n" if status == "done" else ""), encoding="utf-8")
        record = {"name": "m2-voxpocket", "pane_id": "w9:p1", "agent_status": "idle", "terminal_title": f"RAG: {old}/r3/v02.html", "visible": "Working"}
        state_path = self.test_root / "rebind.json"
        initial = self.router.supervision_status(state_file=state_path, agent_records=[record], handoff_root=root)
        self.assertEqual(initial["slots"][0]["linear_issue"], "TIA-409")
        # Wide terminal padding and old RAG paths must not hide the current watchdog.
        record["visible"] = "\n".join([" " * 300 + "┃"] * 5 + ["Watchdog watch-voxpocket-holistic-t4 is active" + " " * 250 + "┃", "╻" + "▄" * 300, "╹" + "▀" * 300, "1 background /tasks" + " " * 280 + "┃"])
        refreshed = self.router.supervision_status(state_file=state_path, agent_records=[record], handoff_root=root)
        slot = refreshed["slots"][0]
        self.assertEqual(slot["linear_issue"], "TIA-420")
        self.assertEqual(slot["handoff_dir"], str(current))
        self.assertIn("T4-two-stage-rebuild (in_progress, writer)", slot["m1_progress"])
        self.assertEqual(slot["m1_stage_summary"], "Stage 1 Designer -> Stage 2 Coding")
        self.assertIn("watch-voxpocket-holistic-t4", slot["visible_summary"])
        # An Owner-declared binding is authoritative, unlike a discovered binding.
        record["handoff_dir"] = str(old)
        declared = self.router.supervision_status(state_file=state_path, agent_records=[record], handoff_root=root)
        self.assertEqual(declared["slots"][0]["linear_issue"], "TIA-409")

    def test_status_cli_and_shared_default_state_paths(self):
        with patch.dict("os.environ", {"M2_SUPERVISION_STATE_FILE": ""}):
            for anchor in (None, "/tmp/plan-round1.json", "/private/tmp/plan-round2.json"):
                self.assertEqual(self.router._resolve_supervision_state_path(plan_file=anchor, persist_by_default=True), Path("/tmp/m2-supervision-state.json"))
            self.assertEqual(self.router._resolve_supervision_state_path(plan_file=self.test_root / "plan.json"), self.test_root / "m2-supervision-state.json")
        state_path = self.test_root / "cli-state.json"
        fixture = [{"name": "m2-cli", "agent_status": "done", "pane_id": "w17:p1", "terminal_title": "TIA-18", "visible": "1 background /tasks"}]
        cmd = [str(ROUTER_PATH), "--status", "--state-file", str(state_path), "--handoff-root", str(self.test_root / "handoff"), "--herdr-agent-json", json.dumps(fixture)]
        result = subprocess.run(cmd + ["--json"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["in_flight_details"][0]["linear_issue"], "TIA-18")
        self.assertEqual(payload["in_flight_details"][0]["raw_status"], "working")
        md = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(md.returncode, 0, md.stderr)
        self.assertIn("Unified M2 Rollup", md.stdout)


if __name__ == "__main__":
    unittest.main()
