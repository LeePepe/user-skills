#!/usr/bin/env python3
"""Unit tests for skills/retro-loop/scripts/trace_backfill_exporter.py."""

from __future__ import annotations

import base64
import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_exporter():
    path = ROOT / "skills" / "retro-loop" / "scripts" / "trace_backfill_exporter.py"
    spec = importlib.util.spec_from_file_location("trace_backfill_exporter_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORTER = _load_exporter()


class TraceBackfillExporterTests(unittest.TestCase):
    def _seed_synthetic_workspace(self, root: Path) -> tuple[Path, Path, Path, Path]:
        handoff_root = root / "handoff"
        goal_dir = handoff_root / "sample-goal-eval"
        (goal_dir / "receipts").mkdir(parents=True)
        (goal_dir / "briefs").mkdir(parents=True)

        (goal_dir / "spec.md").write_text(
            "# Spec: Sample Goal\nMandate: Verify nested span waterfall generation.\n",
            encoding="utf-8",
        )
        (goal_dir / "ledger.md").write_text(
            "# Goal: sample-goal-eval\n"
            "- 目标: 测试历史 Goal Trace 还原与五类嵌套子 Span\n"
            "- Owner: owner · 协调者 bot: m2-skills-hub · TL: sample-tl-r1\n"
            "- Linear: TIA-999\n\n"
            "## Tasks\n"
            "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
            "|---|---|---|---|---|---|\n"
            "| TK1 | — | Sample task | writer-r1 | done | https://github.com/LeePepe/user-skills/pull/1 |\n\n"
            "## Events\n"
            "- 2026-11-20T10:00:00+08:00 | EVT=READY | id=E1 | task=TK1 | note=repo=LeePepe/user-skills\n"
            "- 2026-11-20T10:05:00+08:00 | EVT=GOAL_DONE | id=E2 | task=ALL | ref=receipts/TK1-writer-r10.md\n",
            encoding="utf-8",
        )
        (goal_dir / "receipts" / "TK1-writer-r10.md").write_text(
            "# TK1 Writer R10\n"
            "- Skill: `team-lead` and `retro-loop`\n"
            "- Read: skills/retro-loop/SKILL.md and skills/team-lead/references/ledger.md\n"
            "- Ran: python3 skills/skill-contract/scripts/skill_contract.py audit --strict\n"
            "- Tokens: input_tokens=4200 output_tokens=950\n"
            "- V5 Verdict: PASS\n",
            encoding="utf-8",
        )

        sessions_dir = root / "session-state"
        sess_id = "12345678-aaaa-bbbb-cccc-1234567890ab"
        sdir = sessions_dir / sess_id
        sdir.mkdir(parents=True)
        events = [
            {
                "type": "session.start",
                "data": {
                    "sessionId": sess_id,
                    "startTime": "2026-11-20T02:10:00.000Z",
                    "selectedModel": "claude-opus-4-6",
                    "context": {"cwd": "/tmp/work"},
                },
            },
            {
                "type": "user.message",
                "timestamp": "2026-11-20T02:10:01.000Z",
                "data": {"content": "Implement retro-loop trace backfill exporter"},
            },
            {
                "type": "skill.invoked",
                "timestamp": "2026-11-20T02:10:02.000Z",
                "data": {
                    "name": "retro-loop",
                    "path": str(ROOT / "skills" / "retro-loop" / "SKILL.md"),
                    "content": "# Retro Loop\n",
                },
            },
            {
                "type": "tool.execution_start",
                "timestamp": "2026-11-20T02:10:03.000Z",
                "data": {
                    "toolCallId": "call_1",
                    "toolName": "bash",
                    "arguments": {"command": "python3 skills/retro-loop/scripts/retro_story_engine.py discover"},
                },
            },
            {
                "type": "tool.execution_complete",
                "timestamp": "2026-11-20T02:10:04.250Z",
                "data": {"toolCallId": "call_1", "success": True},
            },
            {
                "type": "session.usage_record",
                "timestamp": "2026-11-20T02:10:05.000Z",
                "data": {
                    "usage": {
                        "model": "claude-opus-4-6",
                        "inputTokens": 3100,
                        "outputTokens": 620,
                        "cacheReadTokens": 1800,
                        "duration": 900,
                    }
                },
            },
        ]
        raw_lines = "\n".join(json.dumps(e) for e in events) + "\n" + (" " * 600) + "\n"
        (sdir / "events.jsonl").write_text(raw_lines, encoding="utf-8")

        usage_jsonl = root / "usage.jsonl"
        usage_jsonl.write_text(
            json.dumps(
                {
                    "kind": "reference_hit",
                    "session_id": sess_id,
                    "ts": "2026-11-20T02:10:02.500Z",
                    "skill": "team-lead",
                    "reference_path": "skills/team-lead/references/ledger.md",
                    "byte_len": 2400,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        warehouse_db = root / "warehouse.db"
        with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
            conn.execute(
                "CREATE TABLE dim_goal (goal_id TEXT, coordinator_bot TEXT, tl_agent TEXT, tl_model TEXT, "
                "derived_actual_status TEXT, created_at TEXT, last_event_at TEXT)"
            )
            conn.execute(
                "INSERT INTO dim_goal VALUES ('sample-goal-eval', 'm2-skills-hub', 'sample-tl-r1', "
                "'claude-opus-4-6', 'done', '2026-11-20T10:00:00+08:00', '2026-11-20T10:05:00+08:00')"
            )
            conn.execute(
                "CREATE TABLE fact_gtt_task_run (goal_id TEXT, task_id TEXT, role_name TEXT, "
                "round_no INTEGER, model_id TEXT, status TEXT, v5_verdict TEXT)"
            )
            conn.execute(
                "INSERT INTO fact_gtt_task_run VALUES ('sample-goal-eval', 'TK1', 'Reviewer', 1, "
                "'gpt-5.4', 'done', 'PASS')"
            )
            conn.commit()

        return handoff_root, sessions_dir, usage_jsonl, warehouse_db

    def test_deterministic_ids_and_five_span_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff_root, sessions_dir, usage_jsonl, warehouse_db = self._seed_synthetic_workspace(root)

            goals_a, sessions_a = EXPORTER.collect_historical_traces(
                handoff_root=handoff_root,
                copilot_sessions_dir=sessions_dir,
                usage_jsonl=usage_jsonl,
                warehouse_db=warehouse_db,
                limit_goals=10,
                limit_sessions=10,
                skills_root=ROOT / "skills",
            )
            goals_b, sessions_b = EXPORTER.collect_historical_traces(
                handoff_root=handoff_root,
                copilot_sessions_dir=sessions_dir,
                usage_jsonl=usage_jsonl,
                warehouse_db=warehouse_db,
                limit_goals=10,
                limit_sessions=10,
                skills_root=ROOT / "skills",
            )

            self.assertEqual(len(goals_a), 1)
            self.assertEqual(len(sessions_a), 1)
            self.assertEqual(goals_a[0].trace_id, goals_b[0].trace_id)
            self.assertEqual(sessions_a[0].trace_id, sessions_b[0].trace_id)
            self.assertEqual(
                [s.span_id for s in goals_a[0].spans],
                [s.span_id for s in goals_b[0].spans],
            )

            for tr in (goals_a[0], sessions_a[0]):
                cats = {s.category for s in tr.spans}
                self.assertEqual(
                    cats,
                    {"root", "stage_role", "skill_load", "l1_reference_read", "l2_script_exec", "llm_generation"},
                )
                span_ids = {s.span_id for s in tr.spans}
                for sp in tr.spans:
                    if sp.category == "root":
                        self.assertIsNone(sp.parent_span_id)
                    else:
                        self.assertIn(sp.parent_span_id, span_ids)
                        self.assertGreater(sp.end_ns, sp.start_ns)

    def test_live_mock_dual_export_and_cli_dry_run(self) -> None:
        received: dict[str, list[tuple[dict[str, str], bytes]]] = {"otlp": [], "langfuse": []}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                if self.path == "/v1/traces":
                    received["otlp"].append((hdrs, body))
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                elif self.path == "/api/public/ingestion":
                    received["langfuse"].append((hdrs, body))
                    batch = json.loads(body.decode("utf-8")).get("batch", [])
                    resp = {"successes": [{"id": item["id"], "status": 201} for item in batch], "errors": []}
                    self.send_response(207)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(resp).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                handoff_root, sessions_dir, usage_jsonl, warehouse_db = self._seed_synthetic_workspace(root)
                cfg_path = root / "config.json"
                cfg_path.write_text(
                    json.dumps(
                        {
                            "phoenix": {
                                "otlp_http_endpoint": f"http://127.0.0.1:{port}/v1/traces",
                                "project_name": "unit-test-proj",
                            },
                            "langfuse": {
                                "local_url": f"http://127.0.0.1:{port}",
                                "public_key": "pk-lf-test",
                                "secret_key": "sk-lf-test",
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = EXPORTER.main(
                        [
                            "--target",
                            "both",
                            "--handoff-root",
                            str(handoff_root),
                            "--copilot-sessions-dir",
                            str(sessions_dir),
                            "--usage-jsonl",
                            str(usage_jsonl),
                            "--warehouse-db",
                            str(warehouse_db),
                            "--config",
                            str(cfg_path),
                            "--json",
                        ]
                    )
                self.assertEqual(rc, 0)
                payload = json.loads(buf.getvalue())
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["summary"]["goal_traces"], 1)
                self.assertEqual(payload["summary"]["session_traces"], 1)
                self.assertEqual(len(received["otlp"]), 1)
                self.assertEqual(received["otlp"][0][0].get("content-type"), "application/x-protobuf")
                self.assertIn(b"unit-test-proj", received["otlp"][0][1])

                self.assertEqual(len(received["langfuse"]), 1)
                expected_auth = "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode("ascii")
                self.assertEqual(received["langfuse"][0][0].get("authorization"), expected_auth)
                lf_batch = json.loads(received["langfuse"][0][1].decode("utf-8"))["batch"]
                event_types = {item["type"] for item in lf_batch}
                self.assertEqual(event_types, {"trace-create", "span-create", "generation-create"})
        finally:
            server.shutdown()
            server.server_close()

    def test_missing_langfuse_credentials_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff_root, sessions_dir, usage_jsonl, warehouse_db = self._seed_synthetic_workspace(root)
            cfg_path = root / "empty_config.json"
            cfg_path.write_text("{}", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = EXPORTER.main(
                    [
                        "--target",
                        "langfuse",
                        "--handoff-root",
                        str(handoff_root),
                        "--copilot-sessions-dir",
                        str(sessions_dir),
                        "--usage-jsonl",
                        str(usage_jsonl),
                        "--warehouse-db",
                        str(warehouse_db),
                        "--config",
                        str(cfg_path),
                        "--json",
                    ]
                )
            self.assertEqual(rc, 1)
            out = json.loads(buf.getvalue())
            self.assertFalse(out["ok"])
            self.assertIn("credentials missing", out["exports"]["error"])


if __name__ == "__main__":
    unittest.main()
