#!/usr/bin/env python3
"""Unit tests for Multica Skills ROI Autopilot (`multica_skills_autopilot.py`)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_autopilot_module():
    path = ROOT / "skills" / "retro-loop" / "scripts" / "multica_skills_autopilot.py"
    spec = importlib.util.spec_from_file_location("multica_skills_autopilot_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTOPILOT = _load_autopilot_module()


def _sample_raw_diagnostics() -> dict:
    return {
        "ok": True,
        "wall_clock_date": "2026-11-18",
        "window_cst": "7d",
        "source_as_of": "2026-11-18T19:00:00+08:00",
        "sample_size": 1420,
        "coverage_status": "healthy",
        "window_anchor": {
            "anchor_date": "2026-11-18",
            "source_as_of": "2026-11-18",
            "staleness_days": 0,
            "coverage_status": "fresh",
        },
        "S1_pin_drift": {
            "outdated_pin_count": 2,
            "all_up_to_date": False,
        },
        "S3_skill_health": {
            "total_skills": 30,
            "audit_pass_count": 27,
            "audit_pass_rate": 0.9,
            "top3_l0_watermarks": [
                {"skill": "legacy-bloated-skill", "l0_lines": 240, "l0_cap": 180},
                {"skill": "retro-loop", "l0_lines": 106, "l0_cap": 180},
            ],
            "top3_l1_watermarks": [],
            "eval_case_total": 68,
            "eval_case_weekly_delta": 4,
        },
        "S2_gate_catch": {
            "eval_effect": {
                "interceptions_7d": 3,
                "escapes_7d": 1,
                "interception_rate": 0.75,
                "by_eval_case": {"case-a": {"interceptions": 3, "escapes": 1}},
            }
        },
        "S4_usage_and_r1": {
            "active_skills_count": 12,
            "idle_skills_30d": ["unused-skill-a", "unused-skill-b"],
            "empty_load_rate": 0.25,
        },

        "S6_load_vs_script": [
            {
                "skill": "team-lead",
                "loads_30d": 120,
                "script_execs": 12,
                "script_touch_rate": 0.1,
                "script_p95_ms": 450.0,
                "script_success_rate": 0.9,
                "unpracticed_rate": 0.9,
                "empty_load_rate": 0.7,
                "reference_hit_count": 2,
                "eval_interceptions": 3,
                "eval_escapes": 1,
            },
            {
                "skill": "aidash-content",
                "loads_30d": 4,
                "script_execs": 2,
                "script_touch_rate": 0.5,
                "unpracticed_rate": 0.5,
            },
        ],
        "S7_disclosure_funnel": [
            {
                "skill": "team-lead",
                "l0_only_rate": 0.7,
                "l1_selective_rate": 0.2,
                "l1_greedy_violation_rate": 0.1,
                "avg_context_tokens": 18000,
            }
        ],
        "S9_escape_attribution": [
            {"skill": "team-lead", "impl_bug_escapes": 5, "spec_gap_escapes": 8},
            {"skill": "my-designer", "impl_bug_escapes": 2, "spec_gap_escapes": 4},
        ],
        "S10_skill_roi_ranking": [
            {
                "skill": "team-lead",
                "diagnostic_status": "high_repair_loop",
                "roi_score": 0.085,
                "r1_pass_rate": 0.5,
                "r1_total": 6,
                "loads_30d": 120,
                "loads_7d": 25,
                "script_execs": 12,
                "script_p95_ms": 450.0,
                "script_success_rate": 0.9,
                "unpracticed_rate": 0.9,
                "empty_load_rate": 0.7,
                "reference_hit_count": 2,
                "eval_interceptions": 3,
                "eval_escapes": 1,
                "post_invocation_repair_loop_rate": 0.35,
                "impl_bug_escapes": 5,
                "spec_gap_escapes": 8,
                "edit_churn_count": 1,
                "l1_greedy_violation_rate": 0.1,
                "diagnosis": "光读不练率 90%（脚本未充分触发）；[IMPL_BUG] 漏网 5 次",
                "source_as_of": "2026-11-18T18:30:00+08:00",
                "sample_size": 120,
                "needs_optimization": True,
            },
            {
                "skill": "my-designer",
                "diagnostic_status": "high_repair_loop",
                "roi_score": None,
                "r1_pass_rate": 0.3333,
                "r1_total": 3,
                "loads_30d": 24,
                "loads_7d": 6,
                "script_execs": 18,
                "unpracticed_rate": 0.25,
                "post_invocation_repair_loop_rate": 0.6,
                "impl_bug_escapes": 2,
                "spec_gap_escapes": 4,
                "edit_churn_count": 0,
                "l1_greedy_violation_rate": 0.0,
                "diagnosis": "[SPEC_GAP] 盲区 4 次",
                "source_as_of": "2026-11-18T18:00:00+08:00",
                "sample_size": 24,
                "needs_optimization": True,
            },
        ],
        "S12_tool_context_burn": [
            {"tool_name": "bash", "call_count": 40, "total_tokens": 90000, "p95_tokens": 12000}
        ],
        "recommendations": [
            {"id": "skill-roi-team-lead", "skill": "team-lead", "priority": "P1"}
        ],
    }


class MulticaSkillsAutopilotTests(unittest.TestCase):
    def test_collect_snapshot_ranks_low_roi_and_renders_p3_markdown(self) -> None:
        snap = AUTOPILOT.collect_skills_roi_snapshot(
            diagnostics_fn=lambda **_kwargs: _sample_raw_diagnostics(),
            top_n=3,
        )
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["collected_date"], "2026-11-18")
        self.assertEqual(snap["dedup_key"], "skills-roi-autopilot-2026-11-18")
        self.assertEqual(snap["target_priority"]["multica_priority"], "medium")
        self.assertEqual(snap["target_priority"]["linear_priority_number"], 3)
        self.assertEqual(snap["target_owner"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
        self.assertIn("[P3 / Priority 3]", snap["default_issue_title"])
        self.assertEqual(len(snap["top_findings"]), 2)
        self.assertEqual(snap["top_findings"][0]["skill"], "team-lead")
        self.assertTrue(snap["top_findings"][0]["remediation_commands"])
        self.assertIn("multica-skills-autopilot:skills-roi-autopilot-2026-11-18", snap["markdown_brief"])
        self.assertIn("Priority 3 (`medium`)", snap["markdown_brief"])
        self.assertIn("lee@multica.local", snap["markdown_brief"])
        self.assertEqual(snap["top_findings"][0]["script_p95_ms"], 450.0)
        self.assertEqual(snap["top_findings"][0]["reference_hit_count"], 2)
        self.assertEqual(snap["summary"]["eval_effect"]["interceptions_7d"], 3)
        self.assertIn("Empty-load", snap["markdown_brief"])
        self.assertIn("450.0 ms", snap["markdown_brief"])
        issue_brief = AUTOPILOT._load_issue_brief()
        dz_report = issue_brief.verify_dual_zone_issue(snap["markdown_brief"])
        self.assertTrue(dz_report["ok"], dz_report)
        self.assertLessEqual(dz_report["human_zone_lines"], 15)
        self.assertEqual(dz_report["cursor"], "skills-roi-autopilot-2026-11-18")

    def test_collect_snapshot_reads_real_gap_metrics_from_warehouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            warehouse = root / "warehouse.db"
            copilot = root / "session-store.db"
            skills_root = root / "skills"
            dev_root = root / "dev"
            dev_root.mkdir()
            skill_dir = skills_root / "alpha"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "references").mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
            with sqlite3.connect(warehouse) as conn:
                conn.execute("CREATE TABLE fact_turn (session_id TEXT, attribution_skill TEXT, skill_names TEXT, tool_calls TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER, cst_day TEXT)")
                conn.execute("CREATE TABLE reference_hits (session_id TEXT, skill TEXT, reference_path TEXT, byte_len INTEGER, cst_day TEXT)")
                conn.execute("CREATE TABLE script_call (session_id TEXT, skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, cst_day TEXT)")
                conn.execute("CREATE TABLE fact_gtt_task_run (goal_id TEXT, task_id TEXT, role_name TEXT, round_no INTEGER, v5_verdict TEXT, status TEXT, skills_used TEXT, cst_day TEXT)")
                conn.execute('CREATE TABLE fact_gtt_event (goal_id TEXT, evt_type TEXT, note_preview TEXT, eval_case TEXT, interception INTEGER, "escape" INTEGER, cst_day TEXT)')
                today = "date('now')"
                conn.execute("INSERT INTO fact_turn VALUES (?, ?, ?, ?, ?, ?, ?, date('now'))", ("s1", "alpha", '["alpha"]', "[]", 100, 10, 0))
                conn.execute("INSERT INTO fact_turn VALUES (?, ?, ?, ?, ?, ?, ?, date('now'))", ("s2", "alpha", '["alpha"]', "[]", 100, 10, 0))
                conn.execute(f"INSERT INTO reference_hits VALUES ('s1', 'alpha', 'skills/alpha/references/a.md', 42, {today})")
                for duration in (10, 20, 30, 40):
                    conn.execute(f"INSERT INTO script_call VALUES ('s1', 'alpha', 'skills/alpha/scripts/check.py', 0, {duration}, {today})")
                conn.execute("INSERT INTO fact_gtt_task_run VALUES (?, ?, ?, ?, ?, ?, ?, date('now'))", ("goal-alpha", "T1", "reviewer", 1, "PASS", "done", '["alpha"]'))
                conn.execute(f"INSERT INTO fact_gtt_event VALUES ('goal-alpha', 'EVAL', '', 'case-a', 1, 0, {today})")
                conn.execute(f"INSERT INTO fact_gtt_event VALUES ('goal-alpha', 'EVAL', '', 'case-a', 0, 1, {today})")
                conn.commit()
            sqlite3.connect(copilot).close()
            report = _load_autopilot_module().collect_skills_roi_snapshot(
                warehouse_db=warehouse,
                copilot_db=copilot,
                skills_root=skills_root,
                dev_root=dev_root,
                top_n=5,
            )
            row = next(item for item in report["top_findings"] if item["skill"] == "alpha")
            self.assertEqual(row["script_p95_ms"], 40.0)
            self.assertEqual(row["reference_hit_count"], 1)
            self.assertEqual(row["empty_load_rate"], 0.5)
            self.assertEqual(row["eval_interceptions"], 1)
            self.assertEqual(row["eval_escapes"], 1)
            self.assertEqual(report["summary"]["eval_effect"]["escapes_7d"], 1)

    def test_file_actionable_issue_creates_p3_assigned_to_owner_and_deduplicates(self) -> None:
        snap = AUTOPILOT.collect_skills_roi_snapshot(
            diagnostics_fn=lambda **_kwargs: _sample_raw_diagnostics()
        )
        recorded_calls: list[tuple[list[str], str | None]] = []
        existing_issues: list[dict] = []

        def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
            recorded_calls.append((args, stdin_text))
            if args[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"issues": existing_issues}), "")
            if args[:2] == ["issue", "create"]:
                created = {
                    "id": "issue-uuid-001",
                    "identifier": "MUL-101",
                    "title": args[args.index("--title") + 1],
                    "priority": args[args.index("--priority") + 1],
                    "status": args[args.index("--status") + 1],
                    "assignee_id": args[args.index("--assignee-id") + 1],
                    "description": stdin_text or "",
                }
                existing_issues.append(created)
                return subprocess.CompletedProcess(args, 0, json.dumps(created), "")
            raise AssertionError(f"Unexpected command: {args}")

        first = AUTOPILOT.file_actionable_issue(
            snap,
            analysis_markdown="### Root Cause\n- Sink recurring escapes into deterministic scripts.",
            runner=fake_runner,
        )
        self.assertEqual(first["action"], "created")
        self.assertEqual(first["issue"]["priority"], "medium")
        self.assertEqual(first["issue"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
        self.assertEqual(first["issue"]["status"], "backlog")
        self.assertIn("Agent Root-Cause Analysis", first["issue"]["description"])

        second = AUTOPILOT.file_actionable_issue(
            snap,
            analysis_markdown="### Root Cause\n- Duplicate run on same day.",
            runner=fake_runner,
        )
        self.assertEqual(second["action"], "deduplicated_existing")
        self.assertEqual(second["issue"]["id"], "issue-uuid-001")
        create_calls = [c for c, _ in recorded_calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(create_calls), 1)

    def test_setup_multica_autopilot_idempotent_create_and_update(self) -> None:
        state: dict[str, list[dict]] = {"autopilots": [], "triggers": []}

        def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
            del stdin_text
            if args[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"autopilots": state["autopilots"]}), "")
            if args[:2] == ["autopilot", "create"]:
                ap = {
                    "id": "ap-uuid-123",
                    "title": args[args.index("--title") + 1],
                    "mode": args[args.index("--mode") + 1],
                    "agent_id": args[args.index("--agent") + 1],
                }
                state["autopilots"].append(ap)
                return subprocess.CompletedProcess(args, 0, json.dumps(ap), "")
            if args[:2] == ["autopilot", "update"]:
                ap = state["autopilots"][0]
                ap["updated"] = True
                return subprocess.CompletedProcess(args, 0, json.dumps(ap), "")
            if args[:2] == ["autopilot", "trigger-list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"triggers": state["triggers"]}), "")
            if args[:2] == ["autopilot", "trigger-add"]:
                trig = {
                    "id": "trig-uuid-456",
                    "kind": "schedule",
                    "cron": args[args.index("--cron") + 1],
                    "timezone": args[args.index("--timezone") + 1],
                }
                state["triggers"].append(trig)
                return subprocess.CompletedProcess(args, 0, json.dumps(trig), "")
            if args[:2] == ["autopilot", "trigger"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"run_id": "run-uuid-789"}), "")
            raise AssertionError(f"Unexpected command: {args}")

        created = AUTOPILOT.setup_multica_autopilot(trigger_now=True, runner=fake_runner)
        self.assertEqual(created["autopilot_action"], "created")
        self.assertEqual(created["trigger_action"], "created")
        self.assertEqual(created["autopilot_id"], "ap-uuid-123")
        self.assertEqual(created["triggered_run"]["run_id"], "run-uuid-789")

        updated = AUTOPILOT.setup_multica_autopilot(trigger_now=False, runner=fake_runner)
        self.assertEqual(updated["autopilot_action"], "updated")
        self.assertEqual(updated["trigger_action"], "unchanged")
        self.assertIsNone(updated["triggered_run"])

    def test_cli_collect_and_run_once_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "collect.json"
            analysis_md = Path(tmp) / "analysis.md"
            analysis_md.write_text("- Custom agent finding for `team-lead`.\n", encoding="utf-8")

            with mock.patch.object(
                AUTOPILOT,
                "_load_retro_engine",
                return_value=mock.Mock(compute_skill_roi_diagnostics=lambda **_kw: _sample_raw_diagnostics()),
            ):
                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    rc = AUTOPILOT.main(["collect", "--output", str(out_json)])
                self.assertEqual(rc, 0)
                self.assertTrue(out_json.is_file())
                parsed = json.loads(out_json.read_text(encoding="utf-8"))
                self.assertEqual(parsed["top_findings"][0]["skill"], "team-lead")

                buf2 = io.StringIO()
                with mock.patch("sys.stdout", buf2):
                    rc2 = AUTOPILOT.main(
                        [
                            "run-once",
                            "--output",
                            str(out_json),
                            "--analysis-file",
                            str(analysis_md),
                            "--dry-run",
                        ]
                    )
                self.assertEqual(rc2, 0)
                run_res = json.loads(buf2.getvalue())
                self.assertTrue(run_res["ok"])
                self.assertEqual(run_res["issue_result"]["action"], "dry_run")
                self.assertEqual(run_res["issue_result"]["priority"], "medium")
                self.assertEqual(run_res["issue_result"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
                self.assertIn("Custom agent finding", run_res["issue_result"]["description"])


if __name__ == "__main__":
    unittest.main()
