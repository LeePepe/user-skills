#!/usr/bin/env python3
"""Unit tests for retro-loop M1-M5 auto-propose and skill-roi freshness/recommendations."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_engine():
    path = ROOT / "skills" / "retro-loop" / "scripts" / "retro_story_engine.py"
    spec = importlib.util.spec_from_file_location("retro_story_engine_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _load_engine()


class RetroStoryEngineTests(unittest.TestCase):
    def test_compute_agent_efficiency_m1_to_m5_recommendations_and_auto_propose_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wh = root / "warehouse.db"
            cp = root / "session-store.db"
            mp = root / "usage.jsonl"
            proposals = root / "proposals.jsonl"

            with contextlib.closing(sqlite3.connect(wh)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_task_run (
                        goal_id TEXT,
                        task_id TEXT,
                        role_name TEXT,
                        round_no INTEGER,
                        v5_verdict TEXT,
                        cst_day TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_event (
                        goal_id TEXT,
                        evt_type TEXT,
                        cst_day TEXT
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_task_run VALUES (?, ?, ?, ?, ?, date('now'))",
                    [
                        ("goal-alpha", "TK1", "code_reviewer", 1, "FAIL"),
                        ("goal-alpha", "TK1", "code_reviewer", 2, "FAIL"),
                        ("goal-alpha", "TK1", "code_reviewer", 3, "PASS"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_event VALUES (?, 'TASK_BLOCKED', date('now'))",
                    [("goal-alpha",), ("goal-beta",)],
                )
                conn.commit()

            with contextlib.closing(sqlite3.connect(cp)) as conn:
                conn.execute("CREATE TABLE session_files (session_id TEXT, file_path TEXT, tool_name TEXT, timestamp TEXT)")
                for _ in range(6):
                    conn.execute(
                        "INSERT INTO session_files VALUES ('sess-12345678', '/repo/src/churn.py', 'edit', datetime('now'))"
                    )
                conn.commit()

            # Create usage records breaching M3 (high ttft, retries, 429), M4 (low cache hit < 0.75), M5 (steering share > 0.35)
            usage_rows = [
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "req": "group/steering",
                    "status": 429,
                    "in": 10000,
                    "out": 40,
                    "cache_read": 1000,
                    "tries": 2,
                    "retried_ms": 500,
                    "ttft_ms": 4500,
                }
                for _ in range(10)
            ] + [
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "req": "group/coding",
                    "status": 200,
                    "in": 5000,
                    "out": 500,
                    "cache_read": 1000,
                    "tries": 1,
                    "retried_ms": 0,
                    "ttft_ms": 3800,
                }
                for _ in range(5)
            ]
            mp.write_text("\n".join(json.dumps(r) for r in usage_rows) + "\n", encoding="utf-8")

            report = ENGINE.compute_agent_efficiency(
                warehouse_db=wh,
                magpie_usage=mp,
                copilot_db=cp,
                auto_propose=True,
                proposals_path=proposals,
            )
            self.assertTrue(report["ok"])
            recs = report["recommendations"]
            by_id = {r["id"]: r for r in recs}

            expected_ids = {
                "reduce-m1-ping-pong": ("retro://agent-efficiency-m1", "agent-efficiency-m1-review-pingpong"),
                "reduce-m2-retry-tax": ("retro://agent-efficiency-m2", "agent-efficiency-m2-retry-tax"),
                "improve-m3-automated-share": ("retro://agent-efficiency-m3", "agent-efficiency-m3-automated-share"),
                "improve-m4-steer-autonomy": ("retro://agent-efficiency-m4", "agent-efficiency-m4-steer-autonomy"),
                "improve-m5-first-pass-pr-green": ("retro://agent-efficiency-m5", "agent-efficiency-m5-first-pass-pr-green"),
            }
            self.assertEqual(set(by_id.keys()), set(expected_ids.keys()))
            for rid, (exp_ref, exp_metric) in expected_ids.items():
                rec = by_id[rid]
                self.assertEqual(rec["ref"], exp_ref)
                self.assertEqual(rec["metric_id"], exp_metric)
                self.assertEqual(rec["scope"], "personal")
                self.assertEqual(rec["priority"], "P1")
                self.assertTrue(rec["title"])
                self.assertTrue(rec["detail"])
                self.assertTrue(rec["action"])

            self.assertEqual(report["proposals_written"], 5)
            self.assertEqual(report["proposals_skipped"], 0)

            lines = [json.loads(ln) for ln in proposals.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 5)
            for row in lines:
                for req_key in ("id", "ts", "agent", "title", "detail", "priority", "status", "ref", "scope"):
                    self.assertTrue(str(row.get(req_key) or "").strip(), f"Missing {req_key} in {row}")
                self.assertEqual(row["scope"], "personal")
                self.assertEqual(row["status"], "pending")

            # Mark M3 as approved and M5 as dismissed; rerun must skip all 5 (3 pending + 1 approved + 1 dismissed)
            with proposals.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            **lines[2],
                            "status": "approved",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                fh.write(
                    json.dumps(
                        {
                            **lines[4],
                            "status": "dismissed",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            second = ENGINE.compute_agent_efficiency(
                warehouse_db=wh,
                magpie_usage=mp,
                copilot_db=cp,
                auto_propose=True,
                proposals_path=proposals,
            )
            self.assertEqual(second["proposals_written"], 0)
            self.assertEqual(second["proposals_skipped"], 5)
            lines_after = [json.loads(ln) for ln in proposals.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines_after), 7)

    def test_skill_roi_freshness_window_anchor_and_cli_auto_propose(self) -> None:
        class FixtureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                instant = cls(2026, 10, 20, 12, tzinfo=timezone.utc)
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        # Keep the fixed October observations inside the live metric window.
        clock = mock.patch.object(ENGINE, "datetime", FixtureClock)
        clock.start()
        self.addCleanup(clock.stop)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wh_stale = root / "warehouse-stale.db"
            wh_missing = root / "warehouse-missing.db"
            cp = root / "session-store.db"
            proposals = root / "proposals.jsonl"
            skills_root = root / "skills"
            dev_root = root / "dev"
            dev_root.mkdir(parents=True)

            # Create a skill with high repair loop and a bloated zero-trigger skill
            sk_repair = skills_root / "my-designer"
            (sk_repair / "scripts").mkdir(parents=True)
            (sk_repair / "references").mkdir(parents=True)
            (sk_repair / "SKILL.md").write_text("---\nname: my-designer\n---\n# Designer\n", encoding="utf-8")
            (sk_repair / "scripts" / "verify_design_cases.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            sk_bloat = skills_root / "heavy-idle-skill"
            (sk_bloat / "references").mkdir(parents=True)
            (sk_bloat / "SKILL.md").write_text(
                "---\nname: heavy-idle-skill\n---\n" + "\n".join(f"line {i}" for i in range(135)) + "\n",
                encoding="utf-8",
            )

            with contextlib.closing(sqlite3.connect(wh_stale)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_turn (
                        session_id TEXT,
                        attribution_skill TEXT,
                        skill_names TEXT,
                        tool_calls TEXT,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cache_read INTEGER,
                        cst_day TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_task_run (
                        goal_id TEXT,
                        task_id TEXT,
                        role_name TEXT,
                        round_no INTEGER,
                        v5_verdict TEXT,
                        status TEXT,
                        cst_day TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_event (
                        goal_id TEXT,
                        evt_type TEXT,
                        note_preview TEXT
                    )
                    """
                )
                conn.execute("CREATE TABLE skill_day (skill TEXT, day TEXT, calls INTEGER)")
                # Stale October snapshot
                conn.execute(
                    "INSERT INTO fact_turn VALUES ('s1', 'my-designer', '[\"my-designer\"]', '[\"Read\",\"Read\",\"Read\",\"Read\"]', 12000, 1000, 500, '2026-10-15')"
                )
                conn.execute(
                    "INSERT INTO fact_gtt_task_run VALUES ('ui-design-goal', 'T1', 'code_reviewer', 1, 'FAIL', 'failed', '2026-10-15')"
                )
                conn.execute(
                    "INSERT INTO fact_gtt_event VALUES ('ui-design-goal', 'REVIEW_FAIL', '[IMPL_BUG] broken layout')"
                )
                conn.execute("INSERT INTO skill_day VALUES ('my-designer', '2026-10-16', 3)")
                conn.commit()

            stale_report = ENGINE.compute_skill_roi_diagnostics(
                warehouse_db=wh_stale,
                copilot_db=cp,
                skills_root=skills_root,
                dev_root=dev_root,
                auto_propose=True,
                proposals_path=proposals,
            )
            self.assertEqual(stale_report["query_id"], "retro/skill_roi_diagnostics")
            self.assertEqual(stale_report["window_anchor"]["source_as_of"], "2026-10-16")
            self.assertIsNotNone(stale_report["staleness_days"])
            self.assertGreater(stale_report["staleness_days"], 2)
            self.assertEqual(stale_report["window_anchor"]["coverage_status"], "stale")
            # Historical source watermark is not substituted for current metric observations.
            anchor = stale_report["window_anchor"]
            self.assertEqual(anchor["source_as_of"], "2026-10-16")
            self.assertEqual(anchor["coverage_status"], "stale")
            self.assertEqual(anchor["window_days_7d"], 7)
            self.assertEqual(anchor["window_days_30d"], 30)

            recs = stale_report["recommendations"]
            self.assertGreaterEqual(len(recs), 1)
            rec_by_id = {r["id"]: r for r in recs}
            self.assertIn("skill-roi-my-designer", rec_by_id)
            self.assertEqual(rec_by_id["skill-roi-my-designer"]["ref"], "retro://skill-roi/my-designer")
            self.assertEqual(rec_by_id["skill-roi-my-designer"]["scope"], "personal")
            self.assertGreater(stale_report["proposals_written"], 0)

            # Second run via CLI must skip already-written proposals
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ENGINE.main(
                    [
                        "skill-roi",
                        "--warehouse-db",
                        str(wh_stale),
                        "--copilot-db",
                        str(cp),
                        "--skills-root",
                        str(skills_root),
                        "--dev-root",
                        str(dev_root),
                        "--auto-propose",
                        "--proposals-path",
                        str(proposals),
                    ]
                )
            self.assertEqual(rc, 0)
            cli_out = json.loads(buf.getvalue())
            self.assertEqual(cli_out["proposals_written"], 0)
            self.assertGreater(cli_out["proposals_skipped"], 0)

            # Missing warehouse DB returns coverage_status == "missing" and source_as_of is None
            missing_report = ENGINE.compute_skill_roi_diagnostics(
                warehouse_db=wh_missing,
                copilot_db=cp,
                skills_root=skills_root,
                dev_root=dev_root,
            )
            self.assertIsNone(missing_report["source_as_of"])
            self.assertIsNone(missing_report["staleness_days"])
            self.assertEqual(missing_report["coverage_status"], "missing")
            missing_details = {d["skill"]: d for d in missing_report["skill_details"]}
            self.assertIsNone(missing_details["my-designer"]["scripts"][0]["calls_30d"])

    def test_skill_details_global_and_repo_bound_with_t_timestamp_usage_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wh = root / "warehouse.db"
            cp = root / "missing-copilot.db"
            mp = root / "usage.jsonl"
            skills_root = root / "skills"
            dev_root = root / "dev"

            # Global skill with references, scripts, EV + DC cases
            sk_global = skills_root / "aidash-metrics"
            (sk_global / "references" / "defect-cases").mkdir(parents=True)
            (sk_global / "scripts").mkdir(parents=True)
            (sk_global / "SKILL.md").write_text(
                "---\nname: aidash-metrics\ndescription: Evidence-first AI metric contract for AIDash.\n---\n# aidash-metrics\nBody line 1\nBody line 2\n",
                encoding="utf-8",
            )
            (sk_global / "references" / "aidata-l1-l5-lineage.md").write_text(
                "# Lineage\nLine 1\nLine 2\n",
                encoding="utf-8",
            )
            (sk_global / "references" / "unhit-drawer.md").write_text(
                "# Never read\n",
                encoding="utf-8",
            )
            (sk_global / "scripts" / "inspect_aidata_pipeline.py").write_text(
                "#!/usr/bin/env python3\nprint('ok')\n",
                encoding="utf-8",
            )
            (sk_global / "scripts" / "unused_helper.sh").write_text(
                "#!/usr/bin/env bash\necho noop\n",
                encoding="utf-8",
            )
            (sk_global / "references" / "eval-cases.json").write_text(
                json.dumps(
                    {
                        "schema": "skill-eval-cases/v2",
                        "skill": "aidash-metrics",
                        "active_cases": [
                            {
                                "id": "EV-001",
                                "title": "Inspector CLI help check",
                                "cmd": f"python3 {sk_global}/scripts/inspect_aidata_pipeline.py --help",
                                "expect_stdout_contains": ["--aidata-root"],
                            },
                            {
                                "id": "EV-002",
                                "title": "Contract clause check",
                                "file": "skills/aidash-metrics/SKILL.md",
                                "must_contain": ["Four mandatory gates", ("/" + "Users/secretuser/private")],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (sk_global / "references" / "defect-cases" / "index.json").write_text(
                json.dumps(
                    {
                        "schema": "apple-design-defect-cases/v1",
                        "active_cases": [
                            {
                                "id": "DC-001",
                                "title": "Forbid bare academic codes",
                                "forbidden_regex": "S1[0-2]?_",
                                "bad_snippet": "Text(\"S1_pin_drift\")",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            # Repo-bound skill under dev_root/AIDash/.claude/skills/aidash-content
            sk_repo = dev_root / "AIDash" / ".claude" / "skills" / "aidash-content"
            (sk_repo / "references").mkdir(parents=True)
            (sk_repo / "SKILL.md").write_text(
                "---\nname: aidash-content\ndescription: Layer-through briefing card contract.\n---\n# aidash-content\n",
                encoding="utf-8",
            )
            (sk_repo / "references" / "payload-shapes.md").write_text("# Shapes\n", encoding="utf-8")

            now_iso = datetime.now(timezone.utc).isoformat()
            # Usage records using key "t" (as emitted by ~/.config/magpie/usage.jsonl)
            mp.write_text(
                json.dumps(
                    {
                        "t": now_iso,
                        "req": "group/steering",
                        "status": 200,
                        "in": 2000,
                        "out": 300,
                        "cache_read": 8000,
                        "tries": 1,
                        "ttft_ms": 420,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with contextlib.closing(sqlite3.connect(wh)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_turn (
                        session_id TEXT,
                        attribution_skill TEXT,
                        skill_names TEXT,
                        tool_calls TEXT,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cache_read INTEGER,
                        ts TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE reference_hits (
                        source TEXT,
                        session_id TEXT,
                        turn_uuid TEXT,
                        skill TEXT,
                        reference_path TEXT,
                        byte_len INTEGER,
                        ts TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE script_call (
                        source TEXT,
                        session_id TEXT,
                        turn_uuid TEXT,
                        skill TEXT,
                        script_path TEXT,
                        exit_code INTEGER,
                        duration_ms REAL,
                        ts TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_task_run (
                        goal_id TEXT,
                        task_id TEXT,
                        role_name TEXT,
                        skills_used TEXT,
                        round_no INTEGER,
                        v5_verdict TEXT,
                        status TEXT,
                        ts TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE fact_gtt_event (
                        goal_id TEXT,
                        evt_type TEXT,
                        skills_used TEXT,
                        note_preview TEXT,
                        eval_case TEXT,
                        interception INTEGER,
                        "escape" INTEGER,
                        ts TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO fact_turn VALUES ('sess-a', 'aidash-metrics', '[\"aidash-metrics\"]', '[\"Read\", \"Bash\"]', 4000, 500, 12000, ?)",
                    (now_iso,),
                )
                conn.executemany(
                    "INSERT INTO reference_hits VALUES ('copilot_cli', ?, ?, 'aidash-metrics', 'skills/aidash-metrics/references/aidata-l1-l5-lineage.md', 640, ?)",
                    [
                        ("sess-a", "t1", now_iso),
                        ("sess-b", "t2", now_iso),
                    ],
                )
                conn.executemany(
                    "INSERT INTO script_call VALUES ('copilot_cli', ?, ?, 'aidash-metrics', 'skills/aidash-metrics/scripts/inspect_aidata_pipeline.py', ?, ?, ?)",
                    [
                        ("sess-a", "c1", 0, 120.0, now_iso),
                        ("sess-a", "c2", 0, 240.0, now_iso),
                        ("sess-b", "c3", 1, 980.0, now_iso),
                    ],
                )
                conn.execute(
                    "INSERT INTO fact_gtt_task_run VALUES ('g1', 'T1', 'code_reviewer', '[\"aidash-metrics\"]', 1, 'PASS', 'completed', ?)",
                    (now_iso,),
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_event VALUES ('g1', 'EVAL_GATE', '[\"aidash-metrics\"]', '', ?, ?, ?, ?)",
                    [
                        ("EV-001", 1, 0, now_iso),
                        ("EV-001", 1, 0, now_iso),
                        ("DC-001", 0, 1, now_iso),
                    ],
                )
                conn.commit()

            report = ENGINE.compute_skill_roi(
                warehouse_db=wh,
                copilot_db=cp,
                skills_root=skills_root,
                dev_root=dev_root,
                magpie_usage=mp,
            )
            self.assertTrue(report["ok"])
            details_by_skill = {item["skill"]: item for item in report["skill_details"]}
            self.assertIn("aidash-metrics", details_by_skill)
            self.assertIn("aidash-content", details_by_skill)

            gm = details_by_skill["aidash-metrics"]
            self.assertEqual(gm["scope"], "global")
            self.assertEqual(gm["repo"], "user-skills")
            self.assertEqual(gm["skill_dir"], "~/Development/user-skills/skills/aidash-metrics")
            self.assertEqual(gm["skill_md_path"], "SKILL.md")
            self.assertGreater(gm["skill_md_lines"], 0)
            self.assertGreater(gm["frontmatter_tokens_est"], 0)
            self.assertGreaterEqual(gm["body_tokens_est"], gm["frontmatter_tokens_est"])
            self.assertGreaterEqual(gm["loads_30d"], 2)
            self.assertEqual(gm["status_label"], "active")
            self.assertGreater(gm["source_breakdown"]["claude"], 0)
            self.assertGreater(gm["source_breakdown"]["copilot"], 0)
            self.assertEqual(gm["kv_cache_hit_pct"], 75.0)

            refs_by_path = {r["rel_path"]: r for r in gm["references"]}
            self.assertEqual(refs_by_path["references/aidata-l1-l5-lineage.md"]["hits_30d"], 2)
            self.assertEqual(refs_by_path["references/aidata-l1-l5-lineage.md"]["sessions_30d"], 2)
            self.assertFalse(refs_by_path["references/aidata-l1-l5-lineage.md"]["is_unhit"])
            self.assertEqual(refs_by_path["references/unhit-drawer.md"]["hits_30d"], 0)
            self.assertTrue(refs_by_path["references/unhit-drawer.md"]["is_unhit"])

            scripts_by_path = {s["rel_path"]: s for s in gm["scripts"]}
            insp_sc = scripts_by_path["scripts/inspect_aidata_pipeline.py"]
            self.assertEqual(insp_sc["calls_30d"], 3)
            self.assertAlmostEqual(insp_sc["success_rate"], 0.6667, places=4)
            self.assertEqual(insp_sc["p95_ms"], 980.0)
            self.assertFalse(insp_sc["is_unpracticed"])
            unused_sc = scripts_by_path["scripts/unused_helper.sh"]
            self.assertEqual(unused_sc["calls_30d"], 0)
            self.assertIsNone(unused_sc["success_rate"])
            self.assertIsNone(unused_sc["p95_ms"])
            self.assertTrue(unused_sc["is_unpracticed"])

            cases_by_id = {c["id"]: c for c in gm["eval_cases"]}
            self.assertEqual(cases_by_id["EV-001"]["kind"], "cli")
            self.assertEqual(cases_by_id["EV-001"]["interceptions_30d"], 2)
            self.assertEqual(cases_by_id["EV-001"]["escapes_30d"], 0)
            self.assertEqual(cases_by_id["EV-002"]["kind"], "contract")
            self.assertEqual(cases_by_id["DC-001"]["kind"], "regex")
            self.assertEqual(cases_by_id["DC-001"]["escapes_30d"], 1)

            rb = details_by_skill["aidash-content"]
            self.assertEqual(rb["scope"], "repo_bound")
            self.assertEqual(rb["repo"], "AIDash")
            self.assertEqual(rb["skill_dir"], "~/Development/AIDash/.claude/skills/aidash-content")
            self.assertEqual(rb["status_label"], "zombie_30d")

            # Verify _usage_records timestamp key "t" works in compute_agent_efficiency M3/M4
            eff = ENGINE.compute_agent_efficiency(warehouse_db=wh, magpie_usage=mp, copilot_db=cp)
            self.assertEqual(eff["metrics"]["M3_flow_and_blockers"]["sampled_requests"], 1)
            self.assertEqual(eff["metrics"]["M4_cache_efficiency"]["overall_cache_hit_rate"], 0.8)

            # Verify zero macOS user home path leaks in serialized report
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("/" + "Users/", serialized)


if __name__ == "__main__":
    unittest.main()
