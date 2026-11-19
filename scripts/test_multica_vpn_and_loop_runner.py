import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parents[1]
LOOP_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
M2_SYMLINK_PATH = ROOT / "skills/m2-dispatcher/scripts/multica_loop_runner.py"
BRIEF_PATH = ROOT / "skills/linear-board/scripts/daily-brief.py"
ISSUE_BRIEF_PATH = ROOT / "skills/issue-brief/scripts/verify_issue_format.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


loop_runner = _load("test_multica_loop_runner_mod", LOOP_PATH)
daily_brief = _load("test_daily_brief_multica_mod", BRIEF_PATH)
issue_brief = _load("test_issue_brief_mod", ISSUE_BRIEF_PATH)


class MulticaVpnVerificationTests(unittest.TestCase):
    def test_symlink_points_to_canonical_team_lead_script(self):
        self.assertTrue(M2_SYMLINK_PATH.is_symlink())
        self.assertEqual(M2_SYMLINK_PATH.resolve(), LOOP_PATH.resolve())

    def test_verify_vpn_passes_with_cross_network_http_cookie_and_ws_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            multica_dir = Path(tmp)
            (multica_dir / "desktop.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "apiUrl": "http://192.168.31.17:18080",
                        "wsUrl": "ws://192.168.31.17:18080/ws",
                        "appUrl": "http://192.168.31.17:13000",
                    }
                ),
                encoding="utf-8",
            )
            (multica_dir / "config.json").write_text(
                json.dumps(
                    {
                        "server_url": "http://192.168.31.17:18080",
                        "app_url": "http://192.168.31.17:13000",
                        "workspace_id": "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c",
                        "token": "mul_test_token",
                    }
                ),
                encoding="utf-8",
            )

            def fake_http(url: str, *, method: str = "GET", headers=None, body=None, timeout: float = 8.0):
                hdrs = dict(headers or {})
                if method == "GET" and url.endswith("/login"):
                    return 200, {"content-type": ["text/html"]}, "<html>Multica Login</html>"
                if method == "GET" and url.endswith("/api/config"):
                    return 200, {"x-middleware-rewrite": ["http://server:8080/api/config"]}, '{"allow_signup":true}'
                if method == "GET" and url.endswith("/health"):
                    return 200, {}, '{"status":"ok"}'
                if method == "OPTIONS":
                    origin = hdrs.get("Origin", "")
                    return (
                        200,
                        {
                            "access-control-allow-origin": [origin],
                            "access-control-allow-credentials": ["true"],
                        },
                        "",
                    )
                if method == "POST" and url.endswith("/auth/send-code"):
                    return 200, {}, '{"message":"Verification code sent"}'
                if method == "POST" and url.endswith("/auth/verify-code"):
                    return (
                        200,
                        {
                            "set-cookie": [
                                "multica_auth=jwt_abc123; Path=/; HttpOnly; SameSite=Strict",
                                "multica_csrf_session=csrf_xyz; Path=/; SameSite=Strict",
                            ]
                        },
                        '{"token":"jwt_abc123"}',
                    )
                if method == "GET" and url.endswith("/api/workspaces"):
                    if "multica_auth=jwt_abc123" in hdrs.get("Cookie", ""):
                        return 200, {}, '[{"slug":"leepepe"}]'
                    return 401, {}, '{"error":"unauthorized"}'
                return 404, {}, "not found"

            def fake_ws(ws_url: str, *, origin=None, cookie_header=None, timeout: float = 6.0):
                return {"ok": True, "status_code": 101, "status_line": "HTTP/1.1 101 Switching Protocols", "origin": origin}

            report = loop_runner.verify_vpn_access(
                multica_dir=multica_dir,
                http_requester=fake_http,
                ws_prober=fake_ws,
            )
            self.assertTrue(report["ok"], report)
            for key in (
                "desktop_zero_localhost",
                "web_and_proxy_reachability",
                "cors_preflight",
                "auth_and_cookie_session",
                "websocket_upgrade",
            ):
                self.assertTrue(report["checks"][key]["ok"], key)

    def test_verify_vpn_flags_localhost_config_and_secure_cookie_over_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            multica_dir = Path(tmp)
            (multica_dir / "desktop.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "apiUrl": "http://localhost:8080",
                        "wsUrl": "ws://127.0.0.1:8080/ws",
                        "appUrl": "http://192.168.31.17:13000",
                    }
                ),
                encoding="utf-8",
            )
            (multica_dir / "config.json").write_text(
                json.dumps({"server_url": "http://192.168.31.17:18080", "app_url": "http://192.168.31.17:13000"}),
                encoding="utf-8",
            )

            def bad_http(url: str, *, method: str = "GET", headers=None, body=None, timeout: float = 8.0):
                if url.endswith("/auth/verify-code"):
                    return (
                        200,
                        {
                            "set-cookie": [
                                "multica_auth=jwt_1; Path=/; Domain=192.168.31.17; Secure; HttpOnly; SameSite=Strict"
                            ]
                        },
                        '{"token":"jwt_1"}',
                    )
                return 200, {"access-control-allow-origin": [(headers or {}).get("Origin", "")]}, "{}"

            report = loop_runner.verify_vpn_access(
                multica_dir=multica_dir,
                http_requester=bad_http,
                ws_prober=lambda *a, **kw: {"ok": False, "status_code": 403},
            )
            self.assertFalse(report["ok"])
            self.assertFalse(report["checks"]["desktop_zero_localhost"]["ok"])
            self.assertFalse(report["checks"]["auth_and_cookie_session"]["ok"])
            auth_findings = " ".join(report["checks"]["auth_and_cookie_session"]["findings"])
            self.assertIn("Secure over plain HTTP", auth_findings)
            self.assertIn("IP literal Domain", auth_findings)
            self.assertFalse(report["checks"]["websocket_upgrade"]["ok"])


class MulticaLoopRunnerPipelineTests(unittest.TestCase):
    def _sample_collected(self, *, watermark: str = "wm-1", has_delta: bool = True, severity: str = "medium"):
        return {
            "loop_id": "daily-learn-recommend",
            "date": "2026-11-19",
            "has_delta": has_delta,
            "delta_count": 2 if has_delta else 0,
            "severity": severity,
            "watermark": watermark,
            "cursor": "daily-learn-2026-11-19",
            "title": "[Daily Learn 2026-11-19] 今日双轨学习选题推荐",
            "summary": "今日发现 2 个高价值双轨学习候选主题，勾选即可生成知识卡片。",
            "items": [
                {"what": "学习选题：渐进式技能四阀门", "why": "降低技能膨胀与返工率"},
                {"what": "学习选题：全天候资产相关性回测", "why": "完善个人投资组合诊断"},
            ],
            "raw": {"count": 2},
        }

    def test_step1_zero_delta_or_matching_watermark_exits_at_layer0(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            loop_runner.save_watermarks({"daily-learn-recommend": {"watermark": "wm-same"}}, state_file)
            jev_called = False

            def boom_jev(*args, **kwargs):
                nonlocal jev_called
                jev_called = True
                return None

            res = loop_runner.run_loop(
                "daily-learn-recommend",
                target_date=date(2026, 11, 19),
                state_path=state_file,
                collector_fn=lambda spec, target_date: self._sample_collected(watermark="wm-same"),
                jev_fn=boom_jev,
            )
            self.assertEqual(res["status"], "SKIPPED_NO_DELTA")
            self.assertEqual(res["layer_reached"], 0)
            self.assertEqual(res["token_cost"], 0)
            self.assertFalse(jev_called)

    def test_step2_jev_low_probability_suppresses_layer2_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            cli_calls = []

            def fake_runner(args, **kwargs):
                cli_calls.append(args)
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            mock_jev = json.dumps(
                {
                    "answers": {
                        "actionable_probability": {"noul": 0.12},
                        "delivery_route": {"choice": "ignore"},
                        "delivery_route_match": {"noul": 0.91},
                        "urgency_score": {"score": 0.10},
                    }
                }
            )
            res = loop_runner.run_loop(
                "daily-learn-recommend",
                target_date=date(2026, 11, 19),
                state_path=state_file,
                mock_jev_json=mock_jev,
                collector_fn=lambda spec, target_date: self._sample_collected(watermark="wm-noise"),
                runner=fake_runner,
            )
            self.assertEqual(res["status"], "SKIPPED_NOT_ACTIONABLE")
            self.assertEqual(res["layer_reached"], 1)
            self.assertEqual(cli_calls, [])
            saved = loop_runner.load_watermarks(state_file)
            self.assertEqual(saved["daily-learn-recommend"]["watermark"], "wm-noise")

    def test_step3_actionable_dual_zone_issue_creates_verified_issue_and_retires_prior_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            calls = []

            def fake_runner(args, *, stdin_text=None, **kwargs):
                calls.append((list(args), stdin_text))
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps(
                            {
                                "issues": [
                                    {
                                        "id": "old-learn-id",
                                        "title": "[Daily Learn 2026-11-18] 昨日学习主题",
                                        "status": "todo",
                                        "assignee_id": loop_runner.DEFAULT_OWNER_ID,
                                        "description": "<!-- multica-cursor:daily-learn-2026-11-18 -->",
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if args[:2] == ["issue", "create"]:
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps({"id": "new-learn-id", "identifier": "TIA-99"}),
                        stderr="",
                    )
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            mock_jev = {
                "answers": {
                    "actionable_probability": {"noul": 0.88},
                    "delivery_route": {"choice": "dual_zone_issue"},
                    "delivery_route_match": {"noul": 0.92},
                    "urgency_score": {"score": 0.75},
                }
            }
            res = loop_runner.run_loop(
                "daily-learn-recommend",
                target_date=date(2026, 11, 19),
                state_path=state_file,
                mock_jev_json=mock_jev,
                collector_fn=lambda spec, target_date: self._sample_collected(watermark="wm-act-1"),
                runner=fake_runner,
            )
            self.assertEqual(res["status"], "ACTION_TAKEN")
            self.assertEqual(res["layer_reached"], 2)
            self.assertEqual(res["delivery"]["action"], "created")
            self.assertEqual(res["delivery"]["retired_prior_ids"], ["old-learn-id"])

            create_call = next((c for c in calls if c[0][:2] == ["issue", "create"]), None)
            self.assertIsNotNone(create_call)
            body = create_call[1]
            self.assertTrue(issue_brief.verify_dual_zone_issue(body)["ok"])

    def test_step3_autopilot_trigger_route_triggers_registered_autopilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            calls = []

            def fake_runner(args, *, stdin_text=None, **kwargs):
                calls.append(list(args))
                if args[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps(
                            {
                                "autopilots": [
                                    {
                                        "id": "ap-roi-123",
                                        "title": "Daily Skills ROI Diagnostic Autopilot (P3 -> Owner)",
                                        "description": "`skills-roi-diagnostic`",
                                    }
                                ]
                            }
                        ),
                        stderr="",
                    )
                if args[:2] == ["autopilot", "trigger"]:
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps({"run_id": "run-777", "status": "queued"}),
                        stderr="",
                    )
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            mock_jev = {
                "answers": {
                    "actionable_probability": {"noul": 0.91},
                    "delivery_route": {"choice": "autopilot_trigger"},
                    "delivery_route_match": {"noul": 0.95},
                    "urgency_score": {"score": 0.80},
                }
            }
            collected = self._sample_collected(watermark="wm-roi-1")
            collected["loop_id"] = "skills-roi-diagnostic"
            res = loop_runner.run_loop(
                "skills-roi-diagnostic",
                target_date=date(2026, 11, 19),
                state_path=state_file,
                mock_jev_json=mock_jev,
                collector_fn=lambda spec, target_date: collected,
                runner=fake_runner,
            )
            self.assertEqual(res["status"], "ACTION_TAKEN")
            self.assertEqual(res["delivery"]["action"], "triggered_autopilot")
            self.assertEqual(res["delivery"]["autopilot_id"], "ap-roi-123")
            self.assertIn(["autopilot", "trigger", "ap-roi-123", "--output", "json"], calls)


class DailyBriefDualTargetTests(unittest.TestCase):
    def test_generate_publish_multica_produces_verified_dual_zone_issue_and_keeps_plan_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev_root = Path(tmp) / "dev"
            handoff_root = Path(tmp) / "handoff"
            dev_root.mkdir()
            handoff_root.mkdir()
            calls = []

            def fake_multica_runner(args, *, stdin_text=None, **kwargs):
                calls.append((list(args), stdin_text))
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(["multica", *args], 0, stdout='{"issues":[]}', stderr="")
                if args[:2] == ["issue", "create"]:
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps({"id": "mul-brief-1", "identifier": "TIA-4"}),
                        stderr="",
                    )
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            payload = daily_brief.generate(
                date(2026, 11, 19),
                dev_root=dev_root,
                handoff_root=handoff_root,
                client=None,
                propose_linear=False,
                publish_multica=True,
                dry_run=False,
                multica_runner=fake_multica_runner,
            )
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["multica_issue"]["action"], "created")
            self.assertEqual(payload["multica_issue"]["issue"]["identifier"], "TIA-4")
            dz_verify = issue_brief.verify_dual_zone_issue(payload["markdown"])
            self.assertTrue(dz_verify["ok"], dz_verify)
            self.assertLessEqual(dz_verify["human_zone_lines"], 15)
            self.assertEqual(dz_verify["cursor"], "daily-brief-2026-11-19")
            self.assertEqual(daily_brief.validate_markdown(payload["plan_a_markdown"]), [])


if __name__ == "__main__":
    unittest.main()
