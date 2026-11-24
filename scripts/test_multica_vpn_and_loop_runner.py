import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parents[1]
LOOP_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
M2_SYMLINK_PATH = ROOT / "skills/m2-dispatcher/scripts/multica_loop_runner.py"
VPN_BRIDGE_PATH = ROOT / "skills/team-lead/scripts/multica_vpn_bridge.py"
M2_VPN_BRIDGE_SYMLINK_PATH = ROOT / "skills/m2-dispatcher/scripts/multica_vpn_bridge.py"
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
vpn_bridge = _load("test_multica_vpn_bridge_mod", VPN_BRIDGE_PATH)
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

    def test_verify_vpn_with_openvpn_gateway_and_client_peer_bundle(self):
        sample_ifconfig = (
            "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n"
            "\tinet 127.0.0.1 netmask 0xff000000\n"
            "utun10: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500\n"
            "\tinet 10.9.0.6 --> 10.9.0.1 netmask 0xffffff00\n"
        )
        tunnels = loop_runner.detect_active_openvpn_tunnel(ifconfig_output=sample_ifconfig)
        self.assertEqual(
            tunnels,
            [{"interface": "utun10", "client_ip": "10.9.0.6", "gateway_ip": "10.9.0.1"}],
        )

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
                    }
                ),
                encoding="utf-8",
            )

            seen_urls = []
            seen_ws = []

            def fake_http(url: str, *, method: str = "GET", headers=None, body=None, timeout: float = 8.0):
                seen_urls.append((method, url))
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
                                "multica_auth=jwt_vpn_ok; Path=/; HttpOnly; SameSite=Lax",
                                "multica_csrf_session=csrf_vpn_ok; Path=/; SameSite=Lax",
                            ]
                        },
                        '{"token":"jwt_vpn_ok"}',
                    )
                if method == "GET" and url.endswith("/api/workspaces"):
                    if "multica_auth=jwt_vpn_ok" in hdrs.get("Cookie", ""):
                        return 200, {}, '[{"slug":"leepepe"}]'
                    return 401, {}, '{"error":"unauthorized"}'
                return 404, {}, "not found"

            def fake_ws(ws_url: str, *, origin=None, cookie_header=None, timeout: float = 6.0):
                seen_ws.append((ws_url, origin, cookie_header))
                return {"ok": True, "status_code": 101, "status_line": "HTTP/1.1 101 Switching Protocols", "origin": origin}

            report = loop_runner.verify_vpn_access(
                multica_dir=multica_dir,
                auto_probe_vpn_gateway=True,
                ifconfig_output=sample_ifconfig,
                http_requester=fake_http,
                ws_prober=fake_ws,
            )
            self.assertTrue(report["ok"], report)
            gw_section = report["checks"]["openvpn_gateway_bridge"]
            self.assertTrue(gw_section["ok"], gw_section)
            self.assertEqual(gw_section["gateway"]["app_url"], "http://10.9.0.1:13000")
            self.assertEqual(gw_section["gateway"]["cookie_samesite"], "lax")
            self.assertIn("10.9.0.6", gw_section["client_peers"])
            self.assertTrue(gw_section["client_peers"]["10.9.0.6"]["ok"])
            self.assertTrue(any(u == "http://10.9.0.1:13000/login" for _, u in seen_urls))
            self.assertTrue(any(ws.startswith("ws://10.9.0.1:18080/ws") for ws, _, _ in seen_ws))


class MulticaVpnBridgeTests(unittest.TestCase):
    def test_vpn_bridge_symlink_points_to_team_lead_script(self):
        self.assertTrue(M2_VPN_BRIDGE_SYMLINK_PATH.is_symlink())
        self.assertEqual(M2_VPN_BRIDGE_SYMLINK_PATH.resolve(), VPN_BRIDGE_PATH.resolve())

    def test_azure_gateway_bind_validation_rejects_public_or_wildcard_ips(self):
        self.assertEqual(
            vpn_bridge.validate_internal_gateway_bind_hosts(["10.9.0.1", "10.8.0.1"]),
            ["10.9.0.1", "10.8.0.1"],
        )
        for forbidden in ("0.0.0.0", "::", "172.17.0.4", "20.198.1.2", "192.168.31.17"):
            with self.assertRaises(ValueError):
                vpn_bridge.validate_internal_gateway_bind_hosts([forbidden])

        rendered = vpn_bridge.render_azure_gateway_bridge_script()
        self.assertIn("ALLOWED_GATEWAY_IPS = ('10.9.0.1', '10.8.0.1')", rendered)
        self.assertIn("PREFERRED_CLIENT_IP = '10.9.0.6'", rendered)

    def test_async_tcp_bridge_server_forwards_bidirectional_payloads(self):
        import asyncio

        async def _exercise():
            async def echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                data = await reader.read(1024)
                writer.write(b"UPSTREAM:" + data)
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            upstream = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
            up_port = upstream.sockets[0].getsockname()[1]

            bridge = vpn_bridge.AsyncTcpBridgeServer(
                [
                    vpn_bridge.PortMapping(
                        bind_host="127.0.0.1",
                        listen_port=0,
                        upstream_host="127.0.0.1",
                        upstream_port=up_port,
                    )
                ]
            )
            await bridge.start()
            bridge_port = bridge.bound_endpoints[0][1]
            try:
                r, w = await asyncio.open_connection("127.0.0.1", bridge_port)
                w.write(b"hello-vpn")
                if w.can_write_eof():
                    w.write_eof()
                await w.drain()
                reply = await r.read(1024)
                w.close()
                await w.wait_closed()
                self.assertEqual(reply, b"UPSTREAM:hello-vpn")
            finally:
                await bridge.close()
                upstream.close()
                await upstream.wait_closed()

        asyncio.run(_exercise())

    def test_install_launchd_dry_run_emits_valid_keepalive_plist_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            plist_file = Path(tmp) / "com.tianpli.multica.vpn-bridge.plist"
            res = vpn_bridge.install_launchd_agent(
                plist_path=plist_file,
                log_dir=Path(tmp),
                dry_run=True,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["payload"]["Label"], "com.tianpli.multica.vpn-bridge")
            self.assertTrue(res["payload"]["KeepAlive"])
            self.assertTrue(res["payload"]["RunAtLoad"])


class MulticaLoopRunnerPipelineTests(unittest.TestCase):
    def test_agent_retro_diagnostic_loop_spec_and_hermetic_collector(self):
        spec = loop_runner.resolve_loop_spec("agent-retro-diagnostic")
        self.assertEqual(spec.loop_id, "agent-retro-diagnostic")
        self.assertEqual(spec.cron, "40 4 * * *")
        self.assertEqual(spec.delivery_mode, "auto_fix_and_digest_only")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions" / "session-1"
            sessions.mkdir(parents=True)
            handoff = root / "handoff"
            handoff.mkdir()
            now = "2026-11-25T00:00:00+00:00"
            (sessions / "events.jsonl").write_text(json.dumps({
                "type": "session.start", "timestamp": now,
                "data": {"sessionId": "session-1", "role": "m2-dispatcher"},
            }) + "\n", encoding="utf-8")
            previous = {key: os.environ.get(key) for key in ("AGENT_RETRO_SESSIONS", "AGENT_RETRO_HANDOFF_ROOT")}
            os.environ["AGENT_RETRO_SESSIONS"] = str(root / "sessions")
            os.environ["AGENT_RETRO_HANDOFF_ROOT"] = str(handoff)
            try:
                collected = loop_runner._default_loop_collector(spec, target_date=date(2026, 11, 25), dry_run=True)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(collected["loop_id"], "agent-retro-diagnostic")
            self.assertEqual(collected["agent_retro"]["sessions"], 1)
            self.assertIn("agent-retro-autopilot-2026-11-25", collected["cursor"])

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

    def test_sync_autopilots_updates_live_4e06bf2a_in_place_and_fixes_stale_path_and_cron(self):
        calls = []
        autopilots_state = [
            {
                "id": "4e06bf2a-5842-4b3a-928f-fcde3bea7c41",
                "title": "Daily Skills ROI Diagnostic Autopilot (Auto-Fix + Daily Brief Digest)",
                "description": 'python3 "$HOME/Development/user-skills/skills/m2-dispatcher/scripts/multica_loop_runner.py" run-loop --loop "skills-roi-diagnostic" --json',
            }
        ]
        triggers_state = [
            {
                "id": "ad0a2d4e-c902-433f-99cb-6bc318561454",
                "kind": "schedule",
                "cron_expression": "20 8 * * *",
                "timezone": "Asia/Shanghai",
                "label": "Daily CST Morning Skills ROI Diagnostic",
            }
        ]

        def fake_runner(args, *, stdin_text=None, **kwargs):
            calls.append(list(args))
            if args[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(["multica", *args], 0, stdout=json.dumps({"autopilots": autopilots_state}), stderr="")
            if args[:2] == ["autopilot", "create"]:
                raise AssertionError("sync_autopilots must update 4e06bf2a in-place, never create a duplicate!")
            if args[:2] == ["autopilot", "update"]:
                autopilots_state[0]["title"] = args[args.index("--title") + 1]
                autopilots_state[0]["description"] = args[args.index("--description") + 1]
                return subprocess.CompletedProcess(["multica", *args], 0, stdout=json.dumps(autopilots_state[0]), stderr="")
            if args[:2] == ["autopilot", "trigger-list"]:
                return subprocess.CompletedProcess(["multica", *args], 0, stdout=json.dumps({"triggers": triggers_state}), stderr="")
            if args[:2] == ["autopilot", "trigger-update"]:
                self.assertEqual(args[2], "4e06bf2a-5842-4b3a-928f-fcde3bea7c41")
                self.assertEqual(args[3], "ad0a2d4e-c902-433f-99cb-6bc318561454")
                triggers_state[0]["cron_expression"] = args[args.index("--cron") + 1]
                triggers_state[0]["timezone"] = args[args.index("--timezone") + 1]
                triggers_state[0]["label"] = args[args.index("--label") + 1]
                return subprocess.CompletedProcess(["multica", *args], 0, stdout=json.dumps(triggers_state[0]), stderr="")
            return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

        res = loop_runner.sync_autopilots(loop_id="skill-retro-diagnostic", runner=fake_runner)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["autopilots"]), 1)
        synced_entry = res["autopilots"][0]
        self.assertEqual(synced_entry["autopilot_id"], "4e06bf2a-5842-4b3a-928f-fcde3bea7c41")
        self.assertEqual(synced_entry["action"], "updated")
        self.assertEqual(synced_entry["trigger_action"], "updated")
        self.assertIn("Skill Progressive Retro", autopilots_state[0]["title"])
        self.assertIn("skills/team-lead/scripts/multica_loop_runner.py", autopilots_state[0]["description"])
        self.assertNotIn("skills/m2-dispatcher/scripts/multica_loop_runner.py", autopilots_state[0]["description"])
        self.assertEqual(triggers_state[0]["cron_expression"], "20 4 * * *")

    def test_engineering_patrol_loops_default_to_auto_fix_and_digest_only_unless_forced(self):
        for loop_id in (
            "local-herdr-worktree-gc",
            "spec-code-drift-patrol",
            "design-taste-evolution",
            "backlog-grooming-and-push",
            "skills-roi-diagnostic",
            "aidash-retro-delta",
        ):
            self.assertEqual(loop_runner.resolve_loop_spec(loop_id).delivery_mode, "auto_fix_and_digest_only", loop_id)

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            buf_file = Path(tmp) / "daily-digest-buffer.json"
            multica_calls = []

            def forbid_multica(args, **kwargs):
                multica_calls.append(list(args))
                return subprocess.CompletedProcess(["multica", *args], 0, stdout='{"issues":[]}', stderr="")

            collected = {
                "loop_id": "spec-code-drift-patrol",
                "date": "2026-11-20",
                "has_delta": True,
                "delta_count": 1,
                "actionable": True,
                "severity": "medium",
                "watermark": "spec-code-drift-patrol:wm-buf-1",
                "cursor": "spec-code-drift-patrol-wm-buf-1",
                "title": "[P3 / Priority 3][Spec-Code Drift] 2026-11-20 规格与代码一致性巡检清单",
                "summary": "检测到 1 项规格与主线实现漂移。",
                "items": [{"what": "同步 VitalStride `spec.md:12`", "why": "消除路径漂移"}],
                "jev": {"actionable": True, "decision_source": "mock"},
            }
            res = loop_runner.run_loop(
                "spec-code-drift-patrol",
                target_date=date(2026, 11, 20),
                state_path=state_file,
                digest_buffer_path=buf_file,
                collector_fn=lambda _s, target_date: collected,
                runner=forbid_multica,
            )
            self.assertEqual(res["status"], "ACTION_TAKEN")
            self.assertEqual(res["route"], "auto_fix_and_digest_only")
            self.assertEqual(res["delivery"]["action"], "buffered_to_daily_digest")
            self.assertEqual(multica_calls, [])
            buf_data = loop_runner.load_daily_digest_buffer(buf_file)
            self.assertIn("spec-code-drift-patrol", buf_data["entries"]["2026-11-20"])

            # With force_create_issue=True, standalone issue creation still works
            forced = loop_runner.run_loop(
                "spec-code-drift-patrol",
                target_date=date(2026, 11, 20),
                state_path=state_file,
                digest_buffer_path=buf_file,
                force=True,
                force_create_issue=True,
                collector_fn=lambda _s, target_date: collected,
                runner=lambda args, **kw: subprocess.CompletedProcess(
                    ["multica", *args],
                    0,
                    stdout='{"issues":[]}' if args[:2] == ["issue", "list"] else '{"id":"forced-1","identifier":"TIA-999"}',
                    stderr="",
                ),
            )
            self.assertEqual(forced["route"], "dual_zone_issue")
            self.assertEqual(forced["delivery"]["action"], "created")


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

    def test_skills_roi_loop_wires_pure_skill_retro_gates_cohort_and_surfaces_in_daily_brief(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wh = root / "warehouse.db"
            cp = root / "session-store.db"
            sessions_dir = root / "sessions"
            sessions_dir.mkdir()
            usage_jsonl = root / "usage.jsonl"
            usage_jsonl.write_text("", encoding="utf-8")
            handoff_root = root / "handoff"
            handoff_root.mkdir()
            skills_root = root / "skills"
            (skills_root / "my-designer" / "references").mkdir(parents=True)
            (skills_root / "my-designer" / "scripts").mkdir(parents=True)
            (skills_root / "my-designer" / ".claude-plugin").mkdir(parents=True)
            (skills_root / "my-designer" / "SKILL.md").write_text(
                "---\nname: my-designer\ndescription: UI design skill\n---\n# my-designer\n",
                encoding="utf-8",
            )
            (skills_root / "my-designer" / ".claude-plugin" / "plugin.json").write_text(
                '{"name":"my-designer","description":"UI","category":"design"}\n',
                encoding="utf-8",
            )
            (skills_root / "my-designer" / "references" / "unwired-guide.md").write_text(
                "# Unwired Guide\n",
                encoding="utf-8",
            )
            (skills_root / "my-designer" / "scripts" / "check_ui.py").write_text(
                "#!/usr/bin/env python3\n",
                encoding="utf-8",
            )
            dev_root = root / "dev"
            dev_root.mkdir()
            proposals_path = root / "proposals.jsonl"
            wm_file = root / "skill-retro-wm.json"
            state_file = root / "loop-watermarks.json"
            digest_buf_file = root / "daily-digest-buffer.json"

            # Seed 2 UI sessions where check_ui.py failed exit=2 (rigid_roadblock) and unwired-guide.md was never read
            from datetime import datetime, timedelta, timezone as _tz
            now_iso = (datetime.now(_tz(timedelta(hours=8))) - timedelta(hours=1)).replace(microsecond=0)
            for idx in (1, 2):
                sdir = sessions_dir / f"sess-{idx}"
                sdir.mkdir()
                events = [
                    {"type": "session.start", "timestamp": now_iso.isoformat(), "data": {"selectedModel": "gpt-5.4"}},
                    {
                        "type": "skill.invoked",
                        "timestamp": (now_iso + timedelta(seconds=1)).isoformat(),
                        "data": {"name": "my-designer", "path": "skills/my-designer/SKILL.md"},
                    },
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now_iso + timedelta(seconds=2)).isoformat(),
                        "data": {
                            "toolCallId": f"call-{idx}",
                            "toolName": "bash",
                            "arguments": {"command": f"python3 {skills_root}/my-designer/scripts/check_ui.py --bad-flag"},
                        },
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now_iso + timedelta(seconds=3)).isoformat(),
                        "data": {"toolCallId": f"call-{idx}", "result": {"content": "<shellId: 1 completed with exit code 2>"}},
                    },
                ]
                (sdir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

            import sqlite3
            import contextlib as _cl
            with _cl.closing(sqlite3.connect(wh)):
                pass
            with _cl.closing(sqlite3.connect(cp)):
                pass

            env_overrides = {
                "SKILLS_ROI_WAREHOUSE_DB": str(wh),
                "SKILLS_ROI_COPILOT_DB": str(cp),
                "SKILLS_ROI_COPILOT_SESSIONS_DIR": str(sessions_dir),
                "SKILLS_ROI_USAGE_JSONL": str(usage_jsonl),
                "SKILLS_ROI_HANDOFF_ROOT": str(handoff_root),
                "SKILLS_ROI_SKILLS_ROOT": str(skills_root),
                "SKILLS_ROI_DEV_ROOT": str(dev_root),
                "SKILLS_ROI_PROPOSALS_PATH": str(proposals_path),
                "MULTICA_SKILL_RETRO_WATERMARK_FILE": str(wm_file),
                "MULTICA_DAILY_DIGEST_BUFFER_FILE": str(digest_buf_file),
            }
            mock_jev = {
                "answers": {
                    "actionable_probability": {"noul": 0.90},
                    "delivery_route": {"choice": "ignore"},
                    "delivery_route_match": {"noul": 0.80},
                    "urgency_score": {"score": 0.80},
                }
            }
            real_loader = loop_runner._load_module

            def patched_loader(name: str, path: Path):
                if name == "loop_icloud_skills_sync_telem":
                    return _mock.Mock(export_telemetry_shards=lambda: {"ok": True})
                return real_loader(name, path)

            with _mock.patch.dict("os.environ", env_overrides), _mock.patch.object(
                loop_runner,
                "_load_module",
                side_effect=patched_loader,
            ):
                # 1st scheduled run: executes pure skill-retro CLI via skill-retro-diagnostic alias, queues proposal, buffers into daily-digest-buffer.json
                res1 = loop_runner.run_loop(
                    "skill-retro-diagnostic",
                    target_date=date(2026, 11, 20),
                    state_path=state_file,
                    digest_buffer_path=digest_buf_file,
                    mock_jev_json=mock_jev,
                )
                self.assertEqual(res1["status"], "ACTION_TAKEN")
                self.assertEqual(res1["route"], "auto_fix_and_digest_only")
                self.assertTrue(res1["watermark"].startswith("skill-retro:cohort:"))
                self.assertTrue(proposals_path.is_file())
                buf_entry = res1["delivery"]["entry"]
                self.assertGreaterEqual(buf_entry["skill_retro"]["high_signal_proposals_count"], 1)

                # 2nd scheduled run on next day with unchanged cohort: skips at Layer 0 with 0 token cost!
                jev_called = False

                def forbid_jev(*_a, **_kw):
                    nonlocal jev_called
                    jev_called = True
                    return None

                res2 = loop_runner.run_loop(
                    "skills-roi-diagnostic",
                    target_date=date(2026, 11, 21),
                    state_path=state_file,
                    digest_buffer_path=digest_buf_file,
                    jev_fn=forbid_jev,
                )
                self.assertEqual(res2["status"], "SKIPPED_NO_DELTA")
                self.assertEqual(res2["layer_reached"], 0)
                self.assertEqual(res2["token_cost"], 0)
                self.assertFalse(jev_called)

                # 3. Daily Brief surfaces buffered skill-retro proposals cleanly in Dual-Zone Human + AI Context zones
                brief_payload = daily_brief.generate(
                    date(2026, 11, 20),
                    dev_root=dev_root,
                    handoff_root=handoff_root,
                    client=None,
                    propose_linear=False,
                    publish_multica=False,
                    dual_zone=True,
                    dry_run=True,
                )
                self.assertTrue(brief_payload["ok"])
                dz_md = brief_payload["markdown"]
                dz_check = issue_brief.verify_dual_zone_issue(dz_md)
                self.assertTrue(dz_check["ok"], dz_check)
                self.assertIn("技能自演进提案", dz_md)
                self.assertIn("4-Layer Skill Retro Digest (`retro_story_engine.py skill-retro`)", dz_md)
                self.assertIn("check_ui.py", dz_md)
                self.assertEqual(daily_brief.validate_markdown(brief_payload["plan_a_markdown"]), [])

                # 4. Schedule ordering invariant: skills-roi-diagnostic cron runs BEFORE daily-brief (04:20 < 05:00),
                # AND if a brief runs on the next day before today's patrol runs, yesterday's actionable buffer still surfaces.
                roi_spec = loop_runner.resolve_loop_spec("skills-roi-diagnostic")
                brief_spec = loop_runner.resolve_loop_spec("daily-brief")
                roi_min, roi_hr = (int(tok) for tok in roi_spec.cron.split()[:2])
                brief_min, brief_hr = (int(tok) for tok in brief_spec.cron.split()[:2])
                self.assertLess((roi_hr, roi_min), (brief_hr, brief_min))

                next_day_brief = daily_brief.generate(
                    date(2026, 11, 21),
                    dev_root=dev_root,
                    handoff_root=handoff_root,
                    client=None,
                    propose_linear=False,
                    publish_multica=False,
                    dual_zone=True,
                    dry_run=True,
                )
                self.assertIn("技能自演进提案", next_day_brief["markdown"])
                self.assertTrue(issue_brief.verify_dual_zone_issue(next_day_brief["markdown"])["ok"])


if __name__ == "__main__":
    unittest.main()
