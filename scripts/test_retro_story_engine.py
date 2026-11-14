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


if __name__ == "__main__":
    unittest.main()
