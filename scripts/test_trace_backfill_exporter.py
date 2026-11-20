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
                "data": {"content": "Read handoff/sample-goal-eval/spec.md and run retro-loop"},
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
                "data": {
                    "toolCallId": "call_1",
                    "success": True,
                    "shellExecution": {"exitCode": 0},
                    "result": {"content": "ok\n<shellId: 1 completed with exit code 0>"},
                },
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

    def test_sparse_goal_never_fabricates_unexecuted_spans_or_brief_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal_dir = root / "handoff" / "sparse-goal"
            (goal_dir / "briefs").mkdir(parents=True)
            (goal_dir / "ledger.md").write_text(
                "# Goal: sparse-goal\n"
                "- 目标: Sparse goal without executed telemetry\n"
                "## Tasks\n"
                "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
                "|---|---|---|---|---|---|\n"
                "| T1 | — | Planned only | — | ready | — |\n"
                "## Events\n"
                "- 2026-11-20T12:00:00+08:00 | EVT=READY | id=E1 | task=T1 | ref=briefs/T1.md\n",
                encoding="utf-8",
            )
            # Mentioning a script/skill in a brief MUST NOT be fabricated into executed spans
            (goal_dir / "briefs" / "T1.md").write_text(
                "Run `python3 skills/team-lead/scripts/feedback_guard.py` and read `skills/team-lead/SKILL.md`.\n",
                encoding="utf-8",
            )

            (goal_dir / "receipts").mkdir()
            (goal_dir / "receipts" / "T1-writer-r1.md").write_text(
                "Not executed: python3 skills/team-lead/scripts/feedback_guard.py; "
                "not loaded or read: skills/team-lead/SKILL.md or "
                "skills/team-lead/references/ledger.md. Follow-up recommendations only.\n"
                "Example, not measured: input_tokens=900 output_tokens=100.\n",
                encoding="utf-8",
            )

            tr = EXPORTER.build_goal_trace(
                goal_dir,
                EXPORTER._load_warehouse_maps(root / "nonexistent.db"),
                linked_sessions=[],
                skills_root=ROOT / "skills",
            )
            assert tr is not None
            cats = {sp.category for sp in tr.spans}
            self.assertEqual(cats, {"root", "stage_role"})
            self.assertEqual(tr.output_payload["total_input_tokens"], 0)
            self.assertEqual(
                set(tr.output_payload["missing_categories"]),
                {"skill_load", "l1_reference_read", "l2_script_exec", "llm_generation"},
            )
            (goal_dir / "receipts" / "T1-writer-r1.md").write_text(
                "- Tokens: input_tokens=100 output_tokens=20 cache_read=30\n", encoding="utf-8",
            )
            attested_tokens = EXPORTER.build_goal_trace(goal_dir, EXPORTER._load_warehouse_maps(root / "none.db"))
            generations = [sp for sp in attested_tokens.spans if sp.category == "llm_generation"]
            self.assertEqual(len(generations), 1)
            self.assertEqual(attested_tokens.output_payload["total_input_tokens"], 100)
            self.assertEqual(generations[0].end_ns - generations[0].start_ns, 1_000_000)
            self.assertIs(generations[0].attributes["timing.measured"], False)

    def test_user_mentions_are_not_skill_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdir = root / "session-state" / "negative-mentions"
            sdir.mkdir(parents=True)
            events_path = sdir / "events.jsonl"
            events_path.write_text(json.dumps({
                "type": "user.message",
                "timestamp": "2026-11-20T02:00:00Z",
                "data": {"content": "Do not load skills/team-lead/SKILL.md or read "
                         "skills/team-lead/references/ledger.md or execute "
                         "python3 skills/team-lead/scripts/feedback_guard.py."},
            }) + "\n", encoding="utf-8")
            tr = EXPORTER.build_session_trace_from_copilot(
                events_path, EXPORTER._load_warehouse_maps(root / "none.db"), {},
            )
            self.assertEqual({sp.category for sp in tr.spans}, {"root", "stage_role"})

    def test_point_events_and_measured_tool_timing_in_goal_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff, sessions, _, db = self._seed_synthetic_workspace(root)
            events_path = next(sessions.glob("*/events.jsonl"))
            events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
            for idx, (tool, args, complete) in enumerate([
                ("skill", {"skill": "team-lead"}, True),
                ("view", {"path": "skills/team-lead/references/ledger.md"}, True),
                ("skill", {"skill": "deep-research"}, False),
                ("view", {"path": "skills/deep-research/SKILL.md"}, False),
            ]):
                events.append({
                    "type": "tool.execution_start", "timestamp": f"2026-11-20T02:10:{10 + idx:02d}.000Z",
                    "data": {"toolCallId": f"timing-{idx}", "toolName": tool, "arguments": args},
                })
                if complete:
                    events.append({
                        "type": "tool.execution_complete", "timestamp": f"2026-11-20T02:10:{10 + idx:02d}.250Z",
                        "data": {"toolCallId": f"timing-{idx}", "success": True},
                    })
            events.append({
                "type": "user.message", "timestamp": "2026-11-20T02:10:14Z",
                "data": {"content": '<skill-context name="code-review">\nLoaded context\n</skill-context>'},
            })
            events_path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")
            sid = events_path.parent.name
            usage = {sid: {
                "skill_turn": [{"skill": "skill-contract", "event_id": "usage-skill", "ts": "2026-11-20T02:10:15Z"}],
                "reference_hit": [{"reference_path": "skills/skill-contract/SKILL.md", "event_id": "usage-ref",
                                   "ts": "2026-11-20T02:10:15Z"}],
            }}
            maps = EXPORTER._load_warehouse_maps(db)
            activity = EXPORTER.parse_copilot_session_activity(events_path, maps, usage)
            traces = [
                EXPORTER.build_session_trace_from_activity(activity),
                EXPORTER.build_goal_trace(handoff / "sample-goal-eval", maps, [activity]),
            ]
            for tr in traces:
                with self.subTest(kind=tr.trace_kind):
                    spans = [sp for sp in tr.spans if sp.category in ("skill_load", "l1_reference_read")]
                    self.assertTrue(any(sp.attributes.get("skill.name") == "code-review" for sp in spans))
                    measured = [sp for sp in spans if sp.attributes["evidence.source"] in
                                ("copilot:tool:timing-0", "copilot:view:timing-1")]
                    self.assertEqual(len(measured), 2)
                    for sp in spans:
                        is_measured = sp in measured
                        self.assertEqual(sp.end_ns - sp.start_ns, (250 if is_measured else 1) * 1_000_000)
                        for payload in (sp.attributes, sp.output_payload):
                            self.assertIs(payload["timing.measured"], is_measured)
                            self.assertEqual(payload["timing.provenance"],
                                             "measured" if is_measured else "point_event_unmeasured")
            m2 = next(sp for sp in traces[1].spans if sp.attributes.get("stage.role") == "M2")
            self.assertEqual(m2.end_ns - m2.start_ns, 1_000_000)
            self.assertIs(m2.attributes["timing.measured"], False)
            self.assertEqual(m2.output_payload["timing.provenance"], "point_event_unmeasured")

    def test_all_fallback_generations_use_one_authoritative_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdir = root / "session-state" / "fallback-rows"
            sdir.mkdir(parents=True)
            events = [{"type": "user.message", "timestamp": "2026-11-20T02:00:00Z",
                       "data": {"content": "Read recorded telemetry"}}]
            events_path = sdir / "events.jsonl"
            rows = [{"kind": "skill_turn", "session_id": sdir.name, "skill": "team-lead",
                     "ts": f"2026-11-20T02:00:0{idx}Z", "event_id": f"row-{idx}",
                     "model": "test-model", "input_tokens": tokens, "output_tokens": 10}
                    for idx, tokens in enumerate((100, 200, 300), start=1)]
            usage_path = root / "usage.jsonl"
            usage_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            usage = EXPORTER._load_usage_jsonl(usage_path)
            maps = EXPORTER._load_warehouse_maps(root / "none.db")
            maps["turns_by_session"][sdir.name] = [dict(row, turn_uuid=f"wh-{idx}") for idx, row in enumerate(rows)]
            for source, expected_count, expected_tokens in (("usage", 3, 600), ("warehouse", 3, 600), ("copilot", 1, 900)):
                with self.subTest(source=source):
                    if source == "copilot":
                        events.append({"type": "session.usage_record", "timestamp": "2026-11-20T02:00:05Z",
                                       "data": {"usage": {"inputTokens": 900, "duration": 250}}})
                    events_path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")
                    selected_usage = usage if source != "warehouse" else {sdir.name: {"skill_turn": [{"skill": "team-lead"}]}}
                    tr = EXPORTER.build_session_trace_from_copilot(events_path, maps, selected_usage)
                    generations = [sp for sp in tr.spans if sp.category == "llm_generation"]
                    self.assertEqual(len(generations), expected_count)
                    self.assertEqual(sum(sp.input_tokens for sp in generations), expected_tokens)
                    self.assertEqual(tr.output_payload["total_input_tokens"], expected_tokens)

    def test_session_exports_all_generations_and_reconciles_root_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_id = "99999999-1111-2222-3333-444444444444"
            sdir = root / "session-state" / sess_id
            sdir.mkdir(parents=True)
            events: list[dict[str, object]] = [
                {
                    "type": "session.start",
                    "data": {"sessionId": sess_id, "startTime": "2026-11-20T03:00:00.000Z", "selectedModel": "gpt-5.4"},
                },
                {
                    "type": "user.message",
                    "timestamp": "2026-11-20T03:00:01.000Z",
                    "data": {"content": "Run 15 turns"},
                },
            ]
            for idx in range(15):
                events.append(
                    {
                        "id": f"u-{idx}",
                        "type": "session.usage_record",
                        "timestamp": f"2026-11-20T03:00:{idx + 2:02d}.000Z",
                        "data": {
                            "usage": {
                                "model": "gpt-5.4",
                                "inputTokens": 1000 + idx,
                                "outputTokens": 100 + idx,
                                "cacheReadTokens": 500 + idx,
                                "duration": 250,
                            }
                        },
                    }
                )
            (sdir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

            tr = EXPORTER.build_session_trace_from_copilot(
                sdir / "events.jsonl",
                EXPORTER._load_warehouse_maps(root / "none.db"),
                {},
                skills_root=ROOT / "skills",
            )
            assert tr is not None
            llm_spans = [sp for sp in tr.spans if sp.category == "llm_generation"]
            self.assertEqual(len(llm_spans), 15)
            self.assertEqual(tr.output_payload["llm_generations"], 15)
            self.assertEqual(sum(sp.input_tokens for sp in llm_spans), tr.output_payload["total_input_tokens"])
            self.assertEqual(sum(sp.output_tokens for sp in llm_spans), tr.output_payload["total_output_tokens"])
            self.assertEqual(sum(sp.cache_read for sp in llm_spans), tr.output_payload["total_cache_read"])

            lf_events = EXPORTER.build_langfuse_ingestion_events(tr)
            lf_gens = [e for e in lf_events if e["type"] == "generation-create"]
            self.assertEqual(len(lf_gens), 15)
            self.assertEqual(
                sum(e["body"]["usage"]["input"] for e in lf_gens),
                tr.output_payload["total_input_tokens"],
            )

    def test_process_exit_code_and_async_shell_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sess_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            sdir = root / "session-state" / sess_id
            sdir.mkdir(parents=True)
            events = [
                {
                    "type": "session.start",
                    "data": {"sessionId": sess_id, "startTime": "2026-11-20T04:00:00.000Z", "selectedModel": "gpt-5.4"},
                },
                # Case A: Tool transport success=True, but process exited with code 2
                {
                    "type": "tool.execution_start",
                    "timestamp": "2026-11-20T04:00:01.000Z",
                    "data": {
                        "toolCallId": "call_sync_fail",
                        "toolName": "bash",
                        "arguments": {"command": "bash skills/team-lead/scripts/herdr-dispatch.sh --role invalid"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": "2026-11-20T04:00:01.400Z",
                    "data": {
                        "toolCallId": "call_sync_fail",
                        "success": True,
                        "shellExecution": {"exitCode": 2},
                        "result": {"content": "Error: unsupported --role\n<shellId: 115 completed with exit code 2>"},
                    },
                },
                # Case B: Async bash starts, then completes in read_bash with exit code 0
                {
                    "type": "tool.execution_start",
                    "timestamp": "2026-11-20T04:00:02.000Z",
                    "data": {
                        "toolCallId": "call_bg_start",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/team-lead/scripts/ci-monitor.py pr --pr 1"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": "2026-11-20T04:00:02.050Z",
                    "data": {
                        "toolCallId": "call_bg_start",
                        "success": True,
                        "shellExecution": None,
                        "result": {"content": "<command started in background with shellId: bg_1>"},
                    },
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": "2026-11-20T04:00:05.000Z",
                    "data": {
                        "toolCallId": "call_bg_read",
                        "toolName": "read_bash",
                        "arguments": {"shellId": "bg_1", "delay": 0},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": "2026-11-20T04:00:05.100Z",
                    "data": {
                        "toolCallId": "call_bg_read",
                        "success": True,
                        "shellExecution": None,
                        "result": {"content": "PR_CHECKS_GREEN\n<shellId: bg_1 completed with exit code 0>"},
                    },
                },
            ]
            (sdir / "events.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n" + (" " * 512) + "\n",
                encoding="utf-8",
            )

            tr = EXPORTER.build_session_trace_from_copilot(
                sdir / "events.jsonl",
                EXPORTER._load_warehouse_maps(root / "none.db"),
                {},
                skills_root=ROOT / "skills",
            )
            assert tr is not None
            scripts = [sp for sp in tr.spans if sp.category == "l2_script_exec"]
            self.assertEqual(len(scripts), 2)
            by_name = {sp.name: sp for sp in scripts}

            herdr_sp = by_name["L2 Script Exec: herdr-dispatch.sh"]
            self.assertEqual(herdr_sp.attributes["script.exit_code"], 2)
            self.assertIs(herdr_sp.status_ok, False)

            cimon_sp = by_name["L2 Script Exec: ci-monitor.py"]
            self.assertEqual(cimon_sp.attributes["script.exit_code"], 0)
            self.assertIs(cimon_sp.status_ok, True)
            self.assertGreaterEqual(int(cimon_sp.attributes["script.duration_ms"]), 3000)

            events.extend([
                {"type": "tool.execution_start", "timestamp": "2026-11-20T04:00:06Z",
                 "data": {"toolCallId": "transport-failure", "toolName": "bash",
                          "arguments": {"command": "python3 skills/team-lead/scripts/feedback_guard.py"}}},
                {"type": "tool.execution_complete", "timestamp": "2026-11-20T04:00:06.100Z",
                 "data": {"toolCallId": "transport-failure", "success": False, "error": {"message": "unavailable"}}},
            ])
            (sdir / "events.jsonl").write_text("\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8")
            maps = EXPORTER._load_warehouse_maps(root / "none.db")
            activity = EXPORTER.parse_copilot_session_activity(sdir / "events.jsonl", maps, {})
            self.assertIsNone(activity.script_execs[-1][4])
            handoff, _, _, _ = self._seed_synthetic_workspace(root)
            for tr in (EXPORTER.build_session_trace_from_activity(activity),
                       EXPORTER.build_goal_trace(handoff / "sample-goal-eval", maps, [activity])):
                failed = next(sp for sp in tr.spans if sp.name == "L2 Script Exec: feedback_guard.py")
                self.assertIsNone(failed.status_ok)
                self.assertIsNone(failed.output_payload["exit_code"])
                self.assertNotIn("script.exit_code", failed.attributes)
                self.assertEqual(failed.attributes["script.exit_status"], "unknown")
                self.assertIs(failed.attributes["tool.transport_success"], False)
                observation = next(ev for ev in EXPORTER.build_langfuse_ingestion_events(tr)
                                   if ev["body"]["id"] == failed.span_id)
                self.assertEqual(observation["body"]["level"], "WARNING")

    def test_fallback_session_deduplication_preserves_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_jsonl = root / "usage.jsonl"
            usage_jsonl.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "kind": "skill_turn",
                                "session_id": "sess-overlap",
                                "ts": "2026-11-20T01:00:00Z",
                                "skill": "team-lead",
                                "model": "claude-opus-4-6",
                                "input_tokens": 100,
                                "output_tokens": 20,
                            }
                        ),
                        json.dumps(
                            {
                                "kind": "skill_turn",
                                "session_id": "sess-unique-2",
                                "ts": "2026-11-20T01:05:00Z",
                                "skill": "retro-loop",
                                "model": "claude-opus-4-6",
                                "input_tokens": 200,
                                "output_tokens": 40,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            warehouse_db = root / "warehouse.db"
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                conn.execute(
                    "CREATE TABLE fact_turn (turn_uuid TEXT, session_id TEXT, ts TEXT, model TEXT, "
                    "input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER)"
                )
                conn.executemany(
                    "INSERT INTO fact_turn VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("t1", "sess-overlap", "2026-11-20T01:00:00Z", "claude-opus-4-6", 100, 20, 0),
                        ("t2", "sess-unique-3", "2026-11-20T01:10:00Z", "gpt-5.4", 300, 60, 0),
                    ],
                )
                conn.commit()

            _, sessions = EXPORTER.collect_historical_traces(
                handoff_root=root / "empty-handoff",
                copilot_sessions_dir=root / "empty-copilot",
                usage_jsonl=usage_jsonl,
                warehouse_db=warehouse_db,
                limit_goals=0,
                limit_sessions=3,
                skills_root=ROOT / "skills",
            )
            self.assertEqual([s.entity_id for s in sessions], ["sess-overlap", "sess-unique-2", "sess-unique-3"])
            self.assertEqual(len({s.trace_id for s in sessions}), 3)

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

    def test_normalize_role_skips_non_role_matrix_and_classifies_real_roles(self) -> None:
        self.assertIsNone(EXPORTER._normalize_role("all-skills-before-after-matrix"))
        self.assertIsNone(EXPORTER._normalize_role("baseline-metrics"))
        self.assertIsNone(EXPORTER._normalize_role("contract-telemetry"))
        self.assertEqual(EXPORTER._normalize_role("TK1-writer-r10"), "Writer")
        self.assertEqual(EXPORTER._normalize_role("TK1-reviewer-r10"), "Reviewer")
        self.assertEqual(EXPORTER._normalize_role("m2-verifier-report"), "Verifier")
        self.assertEqual(EXPORTER._normalize_role("m2-anticheat-report"), "AntiCheat")
        self.assertEqual(EXPORTER._normalize_role("DES-r1"), "Designer")
        self.assertEqual(EXPORTER._normalize_role("GOAL-R10"), "M1 TL")
        self.assertEqual(EXPORTER._normalize_role("TK1-r1"), "Writer")

    def test_goal_grouped_multi_trace_session_and_reconciled_receipt_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff_root = root / "handoff"
            goal_id = "demo-multi-agent-goal"
            goal_dir = handoff_root / goal_id
            (goal_dir / "receipts").mkdir(parents=True)
            (goal_dir / "briefs").mkdir(parents=True)

            (goal_dir / "spec.md").write_text("# Spec\nGoal-grouped relay test.\n", encoding="utf-8")
            (goal_dir / "ledger.md").write_text(
                f"# Goal: {goal_id}\n"
                "- 目标: Verify Goal-grouped Sessions and unified Receipt/Session spans\n"
                "- Owner: owner · 协调者 bot: m2-hub · TL: demo-tl-r1\n\n"
                "## Tasks\n"
                "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
                "|---|---|---|---|---|---|\n"
                "| TK1 | — | Demo task | demo-writer-r1 | done | #1 |\n\n"
                "## Events\n"
                "- 2026-11-20T08:00:00Z | EVT=READY | id=E1 | task=TK1\n"
                "- 2026-11-20T08:40:00Z | EVT=GOAL_DONE | id=E2 | task=ALL | ref=receipts/GOAL-R10.md\n",
                encoding="utf-8",
            )
            (goal_dir / "receipts" / "all-skills-before-after-matrix.md").write_text(
                "# Matrix Artifact\n| Skill | Status |\n|---|---|\n| retro-loop | PASS |\n",
                encoding="utf-8",
            )
            (goal_dir / "receipts" / "GOAL-R10.md").write_text(
                "# Goal R10\n- Final Verdict: PASS\n- Summary: Delivered full relay.\n",
                encoding="utf-8",
            )
            (goal_dir / "receipts" / "TK1-writer-r10.md").write_text(
                "# TK1 Writer R10\n- V5 Verdict: PASS\n- Implemented exporter hierarchy.\n",
                encoding="utf-8",
            )
            (goal_dir / "receipts" / "TK1-reviewer-r10.md").write_text(
                "# TK1 Reviewer R10\n- **V5 Verdict**: **PASS**\n- Independent two-axis review green.\n",
                encoding="utf-8",
            )

            sessions_dir = root / "session-state"

            def _write_session(
                sid: str,
                model: str,
                cwd: str,
                prompt: str,
                start_ts: str,
                rcpt_write: str | None,
                skill_name: str,
            ) -> None:
                sdir = sessions_dir / sid
                sdir.mkdir(parents=True)
                evs: list[dict[str, object]] = [
                    {
                        "type": "session.start",
                        "data": {"sessionId": sid, "startTime": start_ts, "selectedModel": model, "context": {"cwd": cwd}},
                    },
                    {"type": "user.message", "timestamp": start_ts, "data": {"content": prompt}},
                    {
                        "type": "skill.invoked",
                        "timestamp": start_ts,
                        "data": {"name": skill_name, "path": "skills/team-lead/SKILL.md", "content": "# Skill\n"},
                    },
                    {
                        "type": "tool.execution_start",
                        "timestamp": start_ts,
                        "data": {
                            "toolCallId": f"bash-{sid[:4]}",
                            "toolName": "bash",
                            "arguments": {"command": "python3 ./scripts/verify"},
                        },
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": start_ts,
                        "data": {
                            "toolCallId": f"bash-{sid[:4]}",
                            "success": True,
                            "shellExecution": {"exitCode": 0},
                            "result": {"content": "<shellId: 1 completed with exit code 0>"},
                        },
                    },
                    {
                        "id": f"usage-{sid[:4]}",
                        "type": "session.usage_record",
                        "timestamp": start_ts,
                        "data": {
                            "usage": {
                                "model": model,
                                "inputTokens": 1200,
                                "outputTokens": 240,
                                "cacheReadTokens": 400,
                                "duration": 300,
                            }
                        },
                    },
                ]
                if rcpt_write:
                    evs.insert(
                        3,
                        {
                            "type": "tool.execution_start",
                            "timestamp": start_ts,
                            "data": {
                                "toolCallId": f"create-{sid[:4]}",
                                "toolName": "create",
                                "arguments": {"path": str(goal_dir / "receipts" / rcpt_write)},
                            },
                        },
                    )
                (sdir / "events.jsonl").write_text(
                    "\n".join(json.dumps(e) for e in evs) + "\n" + (" " * 600) + "\n",
                    encoding="utf-8",
                )

            _write_session(
                "tl111111-0000-0000-0000-000000000001",
                "magpie/group/steering",
                str(ROOT),
                f"Load /team-lead. Execute Goal {goal_id}. Read handoff/{goal_id}/spec.md and briefs/TK1.md.",
                "2026-11-20T08:01:00.000Z",
                "GOAL-R10.md",
                "team-lead",
            )
            _write_session(
                "wr222222-0000-0000-0000-000000000002",
                "magpie/group/coding",
                f"/tmp/worktrees/{goal_id}-tk1",
                f"You Do Not Dispatch Subagents. Read handoff/{goal_id}/briefs/TK1.md and write handoff/{goal_id}/receipts/TK1-writer-r10.md.",
                "2026-11-20T08:10:00.000Z",
                "TK1-writer-r10.md",
                "retro-loop",
            )
            _write_session(
                "rv333333-0000-0000-0000-000000000003",
                "magpie/group/reviewing",
                f"/tmp/worktrees/{goal_id}-tk1",
                f"You Do Not Dispatch Subagents. Load /code-review. Read handoff/{goal_id}/briefs/TK1.md and write handoff/{goal_id}/receipts/TK1-reviewer-r10.md.",
                "2026-11-20T08:25:00.000Z",
                "TK1-reviewer-r10.md",
                "code-review",
            )
            _write_session(
                "solo4444-0000-0000-0000-000000000004",
                "gpt-5.4",
                "/tmp/standalone",
                "Standalone ad-hoc question unrelated to any handoff goal.",
                "2026-11-20T09:00:00.000Z",
                None,
                "deep-research",
            )

            goals, sessions = EXPORTER.collect_historical_traces(
                handoff_root=handoff_root,
                copilot_sessions_dir=sessions_dir,
                usage_jsonl=root / "none.jsonl",
                warehouse_db=root / "none.db",
                limit_goals=10,
                limit_sessions=10,
                skills_root=ROOT / "skills",
            )
            self.assertEqual(len(goals), 1)
            goal_tr = goals[0]

            # 1. Non-role matrix must NOT appear as an M1 TL stage_role span; it belongs in M1 TL output artifacts
            stage_spans = [sp for sp in goal_tr.spans if sp.category == "stage_role"]
            stage_names = [sp.name for sp in stage_spans]
            self.assertFalse(any("before-after-matrix" in n for n in stage_names), stage_names)
            self.assertFalse(any("Session Worker" in n for n in stage_names), stage_names)

            children_by_parent: dict[str | None, list[object]] = {}
            for sp in goal_tr.spans:
                children_by_parent.setdefault(sp.parent_span_id, []).append(sp)

            m1_sp = next(sp for sp in stage_spans if sp.attributes.get("stage.role") == "M1 TL")
            self.assertIn("all-skills-before-after-matrix.md", [a["filename"] for a in m1_sp.output_payload["artifacts"]])
            self.assertIn("Goal R10", m1_sp.output_payload["receipt_excerpt"])

            # 2. Writer and Reviewer receipt spans have child execution spans AND <=600-char receipt_excerpt
            writer_sp = next(sp for sp in stage_spans if sp.attributes.get("stage.role") == "Writer")
            reviewer_sp = next(sp for sp in stage_spans if sp.attributes.get("stage.role") == "Reviewer")
            for role_sp, expected_substr in (
                (writer_sp, "TK1 Writer R10"),
                (reviewer_sp, "TK1 Reviewer R10"),
            ):
                kids = children_by_parent.get(role_sp.span_id, [])
                self.assertGreater(len(kids), 0, f"{role_sp.name} should have reconciled session child spans")
                self.assertIn("receipt_excerpt", role_sp.output_payload)
                self.assertIn(expected_substr, role_sp.output_payload["receipt_excerpt"])
                self.assertLessEqual(len(role_sp.output_payload["receipt_excerpt"]), 600)

            # 3. Goal-linked session traces bind sessionId = f"goal:{goal_id}", while standalone keeps its session_id
            session_by_prefix = {st.entity_id[:8]: st for st in sessions}
            for pref, expected_role in (("tl111111", "M1 TL"), ("wr222222", "Writer"), ("rv333333", "Reviewer")):
                st = session_by_prefix[pref]
                self.assertIn(f"({expected_role})", st.name)
                lf_trace_body = EXPORTER.build_langfuse_ingestion_events(st)[0]["body"]
                self.assertEqual(lf_trace_body["sessionId"], f"goal:{goal_id}")
                self.assertEqual(st.input_payload["role"], expected_role)
                self.assertEqual(st.input_payload["goal_id"], goal_id)
                self.assertIn("receipt_excerpt", st.output_payload)
                self.assertEqual(st.output_payload["verdict"], "PASS")

            solo_tr = session_by_prefix["solo4444"]
            solo_lf_body = EXPORTER.build_langfuse_ingestion_events(solo_tr)[0]["body"]
            self.assertEqual(solo_lf_body["sessionId"], solo_tr.entity_id)


if __name__ == "__main__":
    unittest.main()
