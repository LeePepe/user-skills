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


def load_router():
    spec = importlib.util.spec_from_file_location("m2_router", ROUTER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class M2RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = load_router()

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


if __name__ == "__main__":
    unittest.main()
