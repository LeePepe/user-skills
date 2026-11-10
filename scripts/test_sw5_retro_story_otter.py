#!/usr/bin/env python3
"""Focused tests for the SW5 retro, story, and otter interfaces."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GTT = load("skills/team-lead/scripts/gtt-telemetry-export.py")
ENGINE = load("skills/retro-loop/scripts/retro_story_engine.py")
DRIFT = load("skills/workflow-hub/scripts/skills-drift-check.py")
DIGEST_VAULT = load("skills/knowledge-digest/scripts/digest_vault.py")


class Sw5Tests(unittest.TestCase):
    def test_evidence_ids_and_manifest(self):
        records = [
            {"record_type": "ledger", "observed_at": "2026-01-01T00:01:00+08:00"},
            {"record_type": "goal", "observed_at": "2026-01-01T00:02:00+08:00"},
        ]
        GTT.assign_evidence_ids(records)
        self.assertEqual(["H00-F1", "H00-S1"], [record["evidence_id"] for record in records])
        manifest = GTT.build_daily_evidence_manifest(records, "2026-01-01")
        self.assertEqual(24, len(manifest["buckets"]))

    def test_state_machine_and_stale_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            ENGINE.init_state(path, "r1", "g1", "sha1")
            ENGINE.transition_state(path, "brief_review", 1)
            ENGINE.transition_state(path, "scripting", 2)
            with self.assertRaises(ENGINE.OptimisticLockError) as error:
                ENGINE.transition_state(path, "script_review", 2)
            self.assertEqual(409, error.exception.code)
            state = ENGINE.transition_state(path, "scripting", 3, evidence_sha="sha2")
            self.assertTrue(state["stale_assets"])

    def test_prompt_composition_and_observe_route(self):
        prompt = ENGINE.compose_prompt(
            "sharing_deck",
            {"pivot": "p", "takeaway_mechanism": "m"},
            {"provenance": "g1", "quantitative_delta": "1", "evidence_ids": ["H00-F1"]},
        )
        self.assertIn("Remove secrets", prompt)
        with tempfile.TemporaryDirectory() as directory:
            proposals = Path(directory) / "proposals.jsonl"
            result = ENGINE.route_retro_candidate({"id": "c1", "title": "T"}, proposals_path=proposals)
            self.assertFalse(result["promoted"])
            self.assertEqual("pending", json.loads(proposals.read_text())["status"])

    def test_drift_clean_and_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "repo" / "skills" / "demo"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
            target = root / "target"
            target.mkdir()
            (target / "demo").symlink_to(source, target_is_directory=True)
            result = DRIFT.inspect_skills_topology([root / "repo"], [target])
            self.assertEqual([], result["issues"])

    def test_agent_efficiency_and_watermarked_daily_cleanup(self):
        gc_mod = load("skills/team-lead/scripts/git-gc.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handoff = root / "handoff"
            goal_done = handoff / "done-goal"
            goal_done.mkdir(parents=True)
            ledger = goal_done / "ledger.md"
            ledger.write_text("# Goal\n- 2026-01-01T00:00:00Z | EVT=GOAL_DONE\n", encoding="utf-8")
            old_log = goal_done / "temp.log"
            old_log.write_text("noise\n", encoding="utf-8")
            import os
            os.utime(ledger, (1000, 1000))
            state_dir = root / "state"
            ns = gc_mod._parser().parse_args([
                "daily-cleanup",
                "--dev-root", str(root),
                "--worktrees-root", str(root / "wt"),
                "--handoff-root", str(handoff),
                "--state-dir", str(state_dir),
                "--log-prune-days", "1",
            ])
            first = gc_mod.daily_cleanup(ns)
            self.assertEqual(first["newly_cleaned_goals"], ["done-goal"])
            self.assertEqual(first["pruned_logs_count"], 1)
            self.assertFalse(old_log.exists())
            # Second run must skip already-watermarked goal
            second = gc_mod.daily_cleanup(ns)
            self.assertEqual(second["newly_cleaned_goals"], [])
            self.assertEqual(second["pruned_logs_count"], 0)

    def test_discover_app_crash_and_perf_queues_proposals(self):
        import contextlib
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wh = root / "warehouse.db"
            qdir = root / "retro"
            qdir.mkdir(parents=True)
            proposals = root / "proposals.jsonl"

            (qdir / "app_crash_and_perf.sql").write_text(
                """
                SELECT
                    app_name,
                    MAX(platform) AS platform,
                    COUNT(*) AS total_events_7d,
                    SUM(CASE WHEN event_kind = 'crash' THEN 1 ELSE 0 END) AS crash_count_7d,
                    SUM(CASE WHEN event_kind = 'crash' THEN 1 ELSE 0 END) AS new_crash_signature_count,
                    ROUND(1.0 - (1.0 * SUM(CASE WHEN event_kind = 'crash' THEN 1 ELSE 0 END) / COUNT(*)), 4) AS crash_free_rate_7d,
                    MAX(signature) AS signature,
                    MAX(stack_top_symbol) AS stack_top_symbol,
                    MAX(metric_ms) AS p95_metric_ms_7d,
                    MIN(metric_ms) AS p95_metric_ms_prev_7d,
                    ROUND(CASE WHEN MIN(metric_ms) > 0 THEN ((MAX(metric_ms) - MIN(metric_ms)) * 100.0) / MIN(metric_ms) ELSE 0.0 END, 2) AS p95_regression_pct,
                    CASE WHEN MIN(metric_ms) > 0 AND ((MAX(metric_ms) - MIN(metric_ms)) * 100.0) / MIN(metric_ms) > 20.0 THEN 1 ELSE 0 END AS is_perf_regressed
                FROM fact_product_telemetry
                GROUP BY app_name;
                """,
                encoding="utf-8",
            )

            with contextlib.closing(sqlite3.connect(wh)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_product_telemetry (
                        event_id TEXT PRIMARY KEY,
                        app_name TEXT NOT NULL,
                        app_version TEXT,
                        build_number TEXT,
                        platform TEXT,
                        event_kind TEXT NOT NULL,
                        signature TEXT,
                        stack_top_symbol TEXT,
                        metric_ms REAL,
                        occurred_at TEXT NOT NULL,
                        cst_day TEXT GENERATED ALWAYS AS (date(occurred_at, '+8 hours')) STORED
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO fact_product_telemetry "
                    "(event_id, app_name, app_version, build_number, platform, event_kind, "
                    "signature, stack_top_symbol, metric_ms, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("e1", "AIDash", "2.0", "100", "macos", "perf", "aidash.ready_scene", "BriefingView.body", 100.0, "2026-11-01T02:00:00Z"),
                        ("e2", "AIDash", "2.1", "108", "macos", "perf", "aidash.ready_scene", "BriefingView.body", 160.0, "2026-11-10T02:00:00Z"),
                        ("e3", "VoxPocket", "1.4", "42", "macos", "crash", "EXC_BAD_ACCESS:Audio", "AudioEngine.start()", None, "2026-11-10T03:00:00Z"),
                    ],
                )
                conn.commit()

            report = ENGINE.discover_aidata_signals(
                warehouse_db=wh,
                queries_dir=qdir,
                proposals_path=proposals,
                auto_propose=True,
            )
            self.assertTrue(report["ok"])
            self.assertFalse(report["degraded"])
            props = report["project_loop_a_proposals"]
            self.assertEqual(len(props), 2)
            by_app = {p["app_name"]: p for p in props}
            self.assertIn("VoxPocket", by_app)
            self.assertEqual(by_app["VoxPocket"]["stack_top_symbol"], "AudioEngine.start()")
            self.assertEqual(by_app["VoxPocket"]["recommended_layer"], "Packages/VoxPocketCore")
            self.assertEqual(by_app["VoxPocket"]["verify_cmd"], "swift test")

            self.assertIn("AIDash", by_app)
            self.assertEqual(by_app["AIDash"]["event_kind"], "perf")
            self.assertEqual(by_app["AIDash"]["recommended_layer"], "Packages/AIDashUI")
            self.assertIn("AIDashUI", by_app["AIDash"]["verify_cmd"])

            lines = [json.loads(ln) for ln in proposals.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(ln["status"] == "pending" for ln in lines))

    def test_digest_vault_archive_discover_and_publish_blog(self):
        drawer = DIGEST_VAULT.load_card_templates()
        self.assertEqual(drawer["schema"], "knowledge-digest-card-templates/v1")
        self.assertGreaterEqual(len(drawer["templates"]), 3)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            tools.mkdir(parents=True)
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\nAnti-AI-slop web design anatomy and multi-viewport linter.\n",
                encoding="utf-8",
            )
            proposals = root / "proposals.jsonl"
            blog_repo = root / "blog"
            (blog_repo / "assets").mkdir(parents=True)
            (blog_repo / "assets" / "blog.css").write_text(
                ".prose th { white-space: nowrap; }\n.prose td { white-space: normal; min-width: 96px; }\n",
                encoding="utf-8",
            )

            disc = DIGEST_VAULT.discover_topics(
                top=2,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                materialize_cards=True,
            )
            self.assertTrue(disc["ok"])
            self.assertGreaterEqual(disc["selected_count"], 1)
            first_slug = disc["topics"][0]["slug"]
            self.assertTrue((vault / "concepts" / f"{first_slug}.md").is_file())
            self.assertIn(f"concepts/{first_slug}.md", (vault / "index.md").read_text(encoding="utf-8"))

            prop_records = [
                json.loads(line)
                for line in proposals.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(prop_records[0]["ref"], f"knowledge://{first_slug}")

            pub = DIGEST_VAULT.publish_blog(
                slug=f"knowledge://{first_slug}",
                blog_repo=blog_repo,
                vault_dir=vault,
                mode="observe",
            )
            self.assertTrue(pub["ok"])
            self.assertEqual(pub["dc_validation"], "PASS")
            html_out = Path(pub["output_html"]).read_text(encoding="utf-8")
            self.assertTrue(html_out.startswith("<!doctype html>"))
            self.assertEqual(DIGEST_VAULT.validate_dc006_to_008(html_out), [])


if __name__ == "__main__":
    unittest.main()
