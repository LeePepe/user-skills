#!/usr/bin/env python3
"""Focused tests for the SW5 retro, story, and otter interfaces."""

import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

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
                "# Hallmark\n\n## 実際に使ったメモ\n- `/design-studio` Entry A により統合済み（Vault `T-021`）。\n",
                encoding="utf-8",
            )
            (tools / "repos" / "bama").mkdir(parents=True)
            (tools / "repos" / "bama" / "README.md").write_text(
                "# BAMA\n\nAgent CLI macOS story verification and architecture layering.\n",
                encoding="utf-8",
            )
            proposals = root / "proposals.jsonl"
            (vault).mkdir(parents=True)
            (vault / "topic-ledger.json").write_text(json.dumps({"topics": [
                {"topic_key": "bama-agent-cli-story-verification", "source_id": "bama", "source_uri": "https://github.com/daveebbelaar/bama", "question": "BAMA 如何转为可验收用户故事？", "learning_state": "queued", "source_fingerprint": "bama"}
            ]}), encoding="utf-8")
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
            self.assertFalse((vault / "concepts" / f"{first_slug}.md").exists())
            self.assertFalse((vault / "index.md").exists())

            prop_records = [
                json.loads(line)
                for line in proposals.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(prop_records[0]["ref"], f"learning-topic://{first_slug}")
            self.assertEqual(prop_records[0]["source"], "Learning")
            self.assertEqual(prop_records[0]["scope"], "personal")

            card_source = root / "approved-card.md"
            card_source.write_text(DIGEST_VAULT.render_concept_card_markdown(disc["topics"][0]), encoding="utf-8")
            DIGEST_VAULT.archive_card(slug=first_slug, category="工程与设计", title=disc["topics"][0]["title"], file_path=card_source, vault_dir=vault)
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

    def test_learning_topic_dedup_hallmark_no_materialize_and_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            (tools / "repos" / "hallmark").mkdir(parents=True)
            (tools / "repos" / "awesome-cjk-design-md").mkdir(parents=True)
            (tools / "repos" / "bama").mkdir(parents=True)
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\n## 実際に使ったメモ\n- `/design-studio` Entry A により統合済み（Vault `T-021`、`DC-009`）。\n",
                encoding="utf-8",
            )
            (tools / "repos" / "hallmark" / "README.md").write_text("# Hallmark Repo\n", encoding="utf-8")
            (tools / "repos" / "awesome-cjk-design-md" / "README.md").write_text("# Awesome Design MD JP\n", encoding="utf-8")
            (tools / "repos" / "bama" / "README.md").write_text("# BAMA\n", encoding="utf-8")
            proposals = root / "proposals.jsonl"
            vault.mkdir(parents=True)
            (vault / "topic-ledger.json").write_text(json.dumps({"topics": [
                {"topic_key": "hallmark-anti-slop-anatomy", "source_id": "hallmark", "source_uri": "https://github.com/Nutlope/hallmark", "question": "Hallmark usage", "learning_state": "learned", "source_fingerprint": "hallmark"},
                {"topic_key": "jp-mixed-script-line-breaking", "source_id": "awesome-cjk-design-md", "source_uri": "https://github.com/kzhrknt/awesome-design-md-jp", "question": "日文与拉丁文混排如何避免断行错误？", "learning_state": "queued", "source_fingerprint": "cjk", "rank": 1},
                {"topic_key": "bama-agent-cli-story-verification", "source_id": "bama", "source_uri": "https://github.com/daveebbelaar/bama", "question": "BAMA 如何转为可验收用户故事？", "learning_state": "queued", "source_fingerprint": "bama"}
            ]}), encoding="utf-8")

            disc = DIGEST_VAULT.discover_topics(
                top=2,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
            )
            self.assertTrue(disc["ok"])
            self.assertEqual(disc["selected_count"], 1)
            self.assertEqual(disc["topics"][0]["topic_key"], "jp-mixed-script-line-breaking")
            self.assertFalse((vault / "concepts").exists())
            self.assertEqual(len(disc["proposals_written"]), 1)
            rec = disc["proposals_written"][0]
            self.assertEqual(rec["ref"], "learning-topic://jp-mixed-script-line-breaking")
            self.assertEqual(rec["source"], "Learning")
            self.assertEqual(rec["learning_state"], "proposed")

            # Re-running on the same CST day must enforce daily cap = 1 and return no_new_topic
            disc2 = DIGEST_VAULT.discover_topics(
                top=2,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
            )
            self.assertEqual(disc2["status"], "no_new_topic")
            self.assertEqual(disc2["reason"], "daily_cap_reached")
            self.assertEqual(disc2["selected_count"], 0)

    def test_skill_roi_diagnostics_and_auto_propose(self):
        import contextlib
        import sqlite3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wh = root / "warehouse.db"
            cp = root / "session-store.db"
            proposals = root / "proposals.jsonl"
            skills_root = root / "skills"
            dev_root = root / "dev"
            dev_root.mkdir(parents=True)

            # Create two mock skills: one healthy, one with uncalled scripts + R1 fail + churn
            sk_good = skills_root / "knowledge-digest"
            (sk_good / "scripts").mkdir(parents=True)
            (sk_good / "references").mkdir(parents=True)
            (sk_good / "SKILL.md").write_text("---\nname: knowledge-digest\n---\n# Good\n", encoding="utf-8")
            (sk_good / "scripts" / "digest_vault.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (sk_good / "references" / "eval-cases.json").write_text(
                json.dumps({"active_cases": [{"id": "EV-001", "registered_at": "2099-01-01"}]}),
                encoding="utf-8",
            )

            sk_low = skills_root / "my-designer"
            (sk_low / "scripts").mkdir(parents=True)
            (sk_low / "references").mkdir(parents=True)
            (sk_low / "SKILL.md").write_text("---\nname: my-designer\n---\n# Designer\n", encoding="utf-8")
            (sk_low / "scripts" / "verify_design_cases.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

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
                        status TEXT
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
                conn.execute(
                    """
                    CREATE TABLE fact_ci_job_run (
                        job_id INTEGER PRIMARY KEY,
                        failure_category TEXT,
                        cst_day TEXT
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO fact_turn VALUES (?, ?, ?, ?, ?, ?, ?, date('now'))",
                    [
                        ("s-good", "knowledge-digest", '["knowledge-digest"]', '["Bash", "Read"]', 1000, 200, 500, ),
                        ("s-low", "my-designer", '["my-designer"]', '["Read", "Read", "Read", "Read"]', 15000, 2000, 1000, ),
                    ],
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_task_run VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("learn-blog", "T1", "code_reviewer", 1, "PASS", "completed"),
                        ("ui-feel-design", "T1", "code_reviewer", 1, "FAIL", "failed"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?)",
                    [
                        ("ui-feel-design", "REVIEW_FAIL", "[IMPL_BUG] missing padding check"),
                        ("ui-feel-design", "RULING", "[SPEC_GAP] missing dark mode token"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO fact_ci_job_run VALUES (?, ?, date('now'))",
                    [
                        (1, "true_positive_gate"),
                        (2, "config_drift_missing_secret"),
                        (3, None),
                    ],
                )
                conn.commit()

            with contextlib.closing(sqlite3.connect(cp)) as conn:
                conn.execute(
                    "CREATE TABLE session_files (session_id TEXT, file_path TEXT, tool_name TEXT)"
                )
                for _ in range(5):
                    conn.execute(
                        "INSERT INTO session_files VALUES ('s-low', '/repo/skills/my-designer/SKILL.md', 'edit')"
                    )
                conn.commit()

            report = ENGINE.compute_skill_roi_diagnostics(
                warehouse_db=wh,
                copilot_db=cp,
                skills_root=skills_root,
                dev_root=dev_root,
                magpie_usage=root / "missing_usage.jsonl",
                dsh_sessions=root / "missing_dsh",
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["S2_gate_catch"]["true_positive_catches_7d"], 1)
            self.assertEqual(report["S3_skill_health"]["total_skills"], 2)
            self.assertGreaterEqual(report["S3_skill_health"]["eval_case_total"], 1)
            self.assertEqual(len(report["S11_context_composition"]["slices"]), 5)
            self.assertTrue(report["S12_tool_context_burn"])
            self.assertTrue(report["low_roi_proposals"])
            top_prop = report["low_roi_proposals"][0]
            self.assertEqual(top_prop["ref"], "skill-roi://my-designer")
            self.assertIn("my-designer", top_prop["title"])

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ENGINE.main([
                    "skill-roi",
                    "--warehouse-db", str(wh),
                    "--copilot-db", str(cp),
                    "--skills-root", str(skills_root),
                    "--dev-root", str(dev_root),
                    "--magpie-usage", str(root / "missing_usage.jsonl"),
                    "--dsh-sessions", str(root / "missing_dsh"),
                    "--auto-propose",
                    "--proposals-path", str(proposals),
                ])
            self.assertEqual(rc, 0)
            written = [json.loads(ln) for ln in proposals.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(written[0]["ref"], "skill-roi://my-designer")
            self.assertEqual(written[0]["status"], "pending")


class Round2LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.tools = self.root / "tools"
        self.vault.mkdir()
        self.tools.mkdir()
        self.proposals = self.root / "proposals.jsonl"
        self.topics = []
        self.add_topic("line-breaks")

    def add_topic(self, key, **overrides):
        topic = {"topic_key": key, "source_id": "demo", "source_uri": "https://github.com/example/demo",
                 "question": f"How does {key} preserve interrupted gesture velocity?", "learning_state": "queued",
                 "source_fingerprint": "reviewed-readme", "aliases": [key], "coverage_refs": []}
        topic.update(overrides)
        self.topics.append(topic)
        (self.vault / "topic-ledger.json").write_text(json.dumps({"topics": self.topics}))
        repo = self.tools / "repos" / "demo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "README.md").write_text("# Demo\nA source with concrete gesture mechanics.\n")
        return topic

    def discover(self, **kwargs):
        return DIGEST_VAULT.discover_topics(propose_aidash=True, vault_dir=self.vault,
            collected_tools_dir=self.tools, proposals_path=self.proposals, git_roots=(), **kwargs)

    def history_record(self, **kwargs):
        record = {"id": "learning-prior", "topic_key": "prior", "ref": "learning-topic://prior",
                  "ts": "2026-11-12T10:00:00Z", "agent": "knowledge-digest", "title": "Prior question",
                  "status": "pending", "learning_state": "proposed", "proposed_on": "2026-11-12",
                  "created_at": "2026-11-12T09:00:00Z"}
        record.update(kwargs)
        return record

    def test_discovery_closes_lock_on_success_health_error_and_unexpected_exception(self):
        original = Path.open
        handles = []
        def track(path, *args, **kwargs):
            handle = original(path, *args, **kwargs)
            if path.name.endswith(".lock"):
                handles.append(handle)
            return handle
        self.addCleanup(lambda: [h.close() for h in handles if not h.closed])
        with mock.patch.object(Path, "open", track):
            self.assertTrue(self.discover()["ok"])
            self.assertTrue(handles[-1].closed, "normal return must explicitly close the sidecar")
            (self.vault / "topic-ledger.json").write_text("broken")
            self.assertFalse(self.discover()["ok"])
            self.assertTrue(handles[-1].closed)
            with mock.patch.object(DIGEST_VAULT, "_existing_vault_corpus", side_effect=OSError("read failed")):
                with self.assertRaises(OSError):
                    self.discover()
            self.assertTrue(handles[-1].closed, "unexpected read failure must release lock")

    def test_queued_authority_beats_design_integration_and_never_materializes(self):
        (self.tools / "repos/demo/README.md").write_text("# Demo\n已接入 design-studio; mechanics still unexplored.\n")
        with mock.patch.object(DIGEST_VAULT, "_rerank_candidates_r09", side_effect=AssertionError("LLM forbidden")), \
             mock.patch.object(DIGEST_VAULT, "render_concept_card_markdown", side_effect=AssertionError("materialization forbidden")):
            result = self.discover(materialize_cards=True)
        self.assertEqual(1, result["selected_count"])
        self.assertFalse((self.vault / "concepts").exists())
        self.assertFalse((self.vault / "index.md").exists())

    def test_full_metadata_derived_coverage_does_not_hide_distinct_question(self):
        self.add_topic("distinct-question", coverage_refs=["concepts/map.md"])
        for folder in ("concepts", "craft", "thoughts", "raw"):
            (self.vault / folder).mkdir()
            (self.vault / folder / "covered.md").write_text(
                "---\ntitle: Long metadata\n" + "# padding\n" * 120 +
                "topic_key: line-breaks\nderived_concept: concepts/finished.md\n---\nCovered question.\n")
        result = self.discover()
        self.assertEqual("distinct-question", result["topics"][0]["topic_key"])
        self.assertIn("derived_concept: concepts/finished.md", DIGEST_VAULT._existing_vault_corpus(self.vault))

    def test_daily_slot_uses_immutable_creation_not_later_status(self):
        with mock.patch.object(DIGEST_VAULT, "datetime", FrozenDateTime):
            for record, expected in (
                (self.history_record(ts="2026-11-12T11:00:00Z", status="approved", learning_state="learned"), 0),
                (self.history_record(proposed_on="2026-11-11", created_at="2026-11-11T09:00:00Z"), 1),
                (self.history_record(proposed_on="", created_at="2026-11-11T09:00:00Z"), 1),
            ):
                with self.subTest(record=record):
                    self.proposals.write_text(json.dumps(record) + "\n")
                    self.assertEqual(expected, self.discover()["selected_count"])

    def test_ledger_alias_history_and_normalized_urls_preserve_distinct_questions(self):
        self.topics[0]["aliases"] = ["old-line-breaks"]
        self.add_topic("second-question", source_uri="ssh://git@github.com/example/demo.git")
        rec = self.history_record(topic_key="old-line-breaks", source_uri="git@github.com:example/demo.git",
                                  proposed_on="2026-11-01", created_at="2026-11-01T00:00:00Z",
                                  learning_state="learned", status="approved")
        self.proposals.write_text(json.dumps(rec) + "\n")
        result = self.discover()
        self.assertEqual("second-question", result["topics"][0]["topic_key"])
        self.assertEqual("https://github.com/example/demo", result["topics"][0]["source_uri"])

    def test_note_and_repo_url_aliases_merge_but_shared_source_alias_keeps_distinct_questions(self):
        self.topics[0].update(source_id="renamed-demo", aliases=["demo", "demo-core-patterns"])
        self.add_topic("second-question", source_id="renamed-demo", aliases=["demo", "demo-core-patterns"],
                       source_uri="git@github.com:example/demo.git")
        (self.tools / "repos/demo/README.md").unlink()
        (self.tools / "saved-name.md").write_text("---\nsource_uri: ssh://git@github.com/example/demo.git\n---\n# Source evidence\n")
        first = self.discover()
        self.assertEqual("line-breaks", first["topics"][0]["topic_key"])
        record = first["proposals_written"][0]
        record.update(proposed_on="2026-10-01", created_at="2026-10-01T00:00:00Z", learning_state="learned", status="approved")
        self.proposals.write_text(json.dumps(record) + "\n")
        second = self.discover()
        self.assertEqual("second-question", second["topics"][0]["topic_key"])

    def test_invalid_ledger_state_and_corrupt_history_fail_closed(self):
        self.topics[0]["learning_state"] = "quueued"
        (self.vault / "topic-ledger.json").write_text(json.dumps({"topics": self.topics}))
        self.assertEqual("health_error", self.discover()["status"])
        self.topics[0]["learning_state"] = "queued"
        (self.vault / "topic-ledger.json").write_text(json.dumps({"topics": self.topics}))
        self.proposals.write_text('{"id":"missing-fields"}\n')
        self.assertEqual("health_error", self.discover()["status"])

    def test_snooze_expiry_terminal_and_transferred_states(self):
        with mock.patch.object(DIGEST_VAULT, "datetime", FrozenDateTime):
            for state, eligible, count in (("snoozed", "2026-11-12T16:00:00Z", 0),
                                            ("snoozed", "2026-11-10T00:00:00Z", 1),
                                            ("dismissed", "2026-09-01T00:00:00Z", 0),
                                            ("blocked", "", 0), ("transferred", "", 0)):
                with self.subTest(state=state, eligible=eligible):
                    self.topics[0].update(learning_state=state, next_eligible_at=eligible,
                                          updated_at="2026-10-01T00:00:00Z")
                    (self.vault / "topic-ledger.json").write_text(json.dumps({"topics": self.topics}))
                    self.proposals.unlink(missing_ok=True)
                    self.assertEqual(count, self.discover()["selected_count"])

    def test_parallel_daily_cap_and_pending_cap_are_atomic(self):
        self.add_topic("second-question")
        self.add_topic("third-question")
        for pending in (0, 3):
            with self.subTest(pending=pending):
                self.proposals.write_text("".join(json.dumps(self.history_record(
                    id=f"learning-existing-{i}", topic_key=f"existing-{i}", proposed_on="2026-10-01",
                    created_at="2026-10-01T00:00:00Z")) + "\n" for i in range(pending)))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda _: self.discover(), range(4)))
                self.assertEqual(1 if not pending else 0, sum(r["selected_count"] for r in results))
                self.assertEqual(pending or 1, len(self.proposals.read_text().splitlines()))


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = cls(2026, 11, 12, 15, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class Round2MetricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wh = self.root / "warehouse.db"
        self.cp = self.root / "copilot.db"
        self.mp = self.root / "usage.jsonl"
        self.clock = mock.patch.object(ENGINE, "datetime", FrozenDateTime)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def efficiency(self):
        return ENGINE.compute_agent_efficiency(self.wh, self.mp, self.cp)

    def diagnostics(self):
        return ENGINE.compute_skill_roi_diagnostics(self.wh, self.cp, self.root / "skills",
            self.root / "dev", self.mp, self.root / "dsh")

    def seed_reviews(self, timestamped=True):
        with contextlib.closing(sqlite3.connect(self.wh)) as conn:
            conn.execute("CREATE TABLE fact_gtt_task_run (goal_id, task_id, round_no, v5_verdict, role_name, status" +
                         (", observed_at" if timestamped else "") + ")")
            conn.execute("CREATE TABLE fact_gtt_event (goal_id, evt_type, note_preview, cst_day)")
            for key, stamp, verdict, rounds in (("old", "2026-11-05T15:59:59Z", "FAIL", 3),
                                               ("start", "2026-11-05T16:00:00Z", "FAIL", 1),
                                               ("recent", "2026-11-11T08:00:00Z", "PASS", 1),
                                               ("future", "2026-11-12T16:00:00Z", "FAIL", 3)):
                row = [key, "T1", rounds, verdict, "reviewer", "completed"]
                if timestamped:
                    row.append(stamp)
                conn.execute("INSERT INTO fact_gtt_task_run VALUES (" + ",".join("?" for _ in row) + ")", row)
            conn.commit()

    def test_m1_m2_use_cst_bounds_actual_observed_freshness_and_population(self):
        self.seed_reviews()
        with contextlib.closing(sqlite3.connect(self.cp)) as conn:
            conn.execute("CREATE TABLE session_files (session_id, file_path, tool_name, first_seen_at)")
            for key, stamp, edits in (("old", "2026-11-05T15:59:59Z", 9),
                                       ("recent", "2026-11-10T10:00:00Z", 5),
                                       ("quiet", "2026-11-11T09:00:00Z", 1),
                                       ("future", "2026-11-12T16:00:00Z", 10)):
                conn.executemany("INSERT INTO session_files VALUES (?, ?, 'edit', ?)", [(key, key + ".py", stamp)] * edits)
            conn.commit()
        report = self.efficiency()
        m1 = report["metrics"]["M1_review_ping_pong"]
        m2 = report["metrics"]["M2_edit_churn"]
        self.assertEqual(2, m1["reviewed_tasks"])
        self.assertEqual(0.5, m1["r1_fail_rate"])
        self.assertEqual(5, m2["max_edits_single_file"])
        self.assertEqual(6, m2["sample_size"])
        self.assertEqual("2026-11-11T17:00:00+08:00", report["source_as_of"])
        self.assertEqual("2026-11-06T00:00:00+08:00", m1["window_start"])
        self.assertEqual("2026-11-13T00:00:00+08:00", m1["window_end"])
        for proposal in report["recommendations"]:
            source = m1 if "m1" in proposal["id"] else m2
            self.assertEqual(source["source_as_of"], proposal["source_as_of"])
            self.assertEqual(source["sample_size"], proposal["sample_size"])
            self.assertIn("window_start", proposal)

    def test_schema_without_timestamps_is_unknown_not_all_time_7d(self):
        self.seed_reviews(timestamped=False)
        with contextlib.closing(sqlite3.connect(self.cp)) as conn:
            conn.execute("CREATE TABLE session_files (session_id, file_path, tool_name)")
            conn.executemany("INSERT INTO session_files VALUES ('s','x','edit')", [()] * 6)
            conn.commit()
        report = self.efficiency()
        for name in ("M1_review_ping_pong", "M2_edit_churn"):
            self.assertEqual("unknown", report["metrics"][name]["coverage_status"])
            self.assertEqual(0, report["metrics"][name]["sample_size"])
        self.assertIsNone(report["source_as_of"])
        self.assertEqual([], report["recommendations"])

    def test_gateway_window_reads_all_records_and_keeps_per_metric_samples(self):
        old = {"ts": "2026-10-01T00:00:00Z", "status": 403, "req": "group/steering", "in": 100, "out": 40}
        valid = {"ts": "2026-11-10T10:00:00Z", "status": 200, "req": "group/coding", "in": 1,
                 "cache_read": 9, "out": 200, "ttft_ms": 100}
        no_tokens = {"ts": "2026-11-11T10:00:00Z", "status": 200}
        self.mp.write_text("\n".join(json.dumps(r) for r in [valid, no_tokens] + [old] * 5001) + "\n")
        report = self.efficiency()
        m3, m4, m5 = (report["metrics"][key] for key in ("M3_flow_and_blockers", "M4_cache_efficiency", "M5_steering_vs_coding_split"))
        self.assertEqual(2, m3["sampled_requests"])
        self.assertEqual(0, m3["gateway_4xx_count"])
        self.assertEqual(2, m3["sample_size"])
        self.assertEqual(1, m4["sample_size"])
        self.assertEqual(1, m5["sample_size"])
        self.assertEqual(0.9, m4["overall_cache_hit_rate"])
        self.assertEqual("2026-11-10T18:00:00+08:00", m4["source_as_of"])
        self.assertEqual([], report["recommendations"])

    def test_missing_and_unstamped_sources_do_not_fabricate_health(self):
        self.mp.write_text(json.dumps({"status": 403, "in": 100}) + "\n")
        report = self.efficiency()
        self.assertEqual("unknown", report["coverage_status"])
        self.assertIsNone(report["source_as_of"])
        self.assertIsNone(report["metrics"]["M4_cache_efficiency"]["overall_cache_hit_rate"])
        diag = self.diagnostics()
        self.assertIsNone(diag["S1_pin_drift"]["all_up_to_date"])
        self.assertIsNone(diag["S11_context_composition"]["kv_cache_hit_pct"])
        self.assertIsNone(diag["S11_context_composition"]["compressed_saved_tokens"])

    def test_idle_scripts_and_no_classified_failures_are_unknown(self):
        skill = self.root / "skills" / "demo"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n")
        (skill / "scripts/check.py").write_text("print('check')\n")
        with contextlib.closing(sqlite3.connect(self.wh)) as conn:
            conn.execute("CREATE TABLE fact_ci_job_run (failure_category, cst_day)")
            conn.execute("INSERT INTO fact_ci_job_run VALUES (NULL, '2026-11-11')")
            conn.commit()
        report = self.diagnostics()
        row = report["S4_usage_and_r1"]["top_used_skills"][0]
        for name in ("script_touch_rate", "unpracticed_rate", "r1_pass_rate", "roi_score"):
            self.assertIsNone(row[name])
        self.assertNotEqual("健康达标", row["diagnosis"])
        self.assertEqual(0, row["sample_size"])
        self.assertIsNone(report["S2_gate_catch"]["gate_catch_rate"])
        self.assertEqual("unknown", report["S2_gate_catch"]["coverage_status"])

    def test_context_and_tool_proxies_and_proposals_keep_estimated_freshness(self):
        with contextlib.closing(sqlite3.connect(self.wh)) as conn:
            conn.execute("CREATE TABLE fact_turn (session_id, attribution_skill, skill_names, tool_calls, input_tokens, output_tokens, cache_read, cst_day)")
            conn.execute("INSERT INTO fact_turn VALUES ('s','demo','[]','[\"Bash\"]',100000,1000,20000,'2026-11-11')")
            conn.execute("INSERT INTO fact_turn VALUES ('old','demo','[]','[\"Bash\"]',999999,0,0,'2026-10-01')")
            conn.commit()
        report = self.diagnostics()
        s11 = report["S11_context_composition"]
        self.assertTrue(s11["estimated"])
        self.assertTrue(all(row["estimated"] for row in s11["slices"]))
        self.assertEqual(1, s11["sample_size"])
        self.assertEqual("2026-11-11T00:00:00+08:00", s11["source_as_of"])
        self.assertIsNone(s11["compressed_saved_tokens"], "no measured compression saving is available")
        self.assertTrue(report["S12_tool_context_burn"])
        for row in report["S12_tool_context_burn"]:
            self.assertTrue(row["estimated"])
            self.assertEqual(1, row["sample_size"])
        proposals = self.root / "proposals.jsonl"
        for candidate in report["low_roi_proposals"]:
            result = ENGINE.route_retro_candidate(candidate, proposals_path=proposals)
            proposal = result["proposal"]
            self.assertEqual(candidate["source_as_of"], proposal["source_as_of"])
            self.assertIn("window_start", proposal)
            if "tool" in candidate["id"]:
                self.assertTrue(proposal["estimated"])

    def test_review_only_null_script_rates_format_and_parallel_autoproposal_idempotence(self):
        skill = self.root / "skills" / "team-lead"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: team-lead\n---\n# Team lead\n")
        (skill / "scripts/check.py").write_text("print('check')\n")
        self.seed_reviews()
        report = self.diagnostics()
        row = report["S4_usage_and_r1"]["top_used_skills"][0]
        self.assertIsNone(row["unpracticed_rate"])
        self.assertTrue(report["low_roi_proposals"])
        self.assertIn("unpracticed_rate=unknown", report["low_roi_proposals"][0]["detail"])
        proposals = self.root / "proposals.jsonl"
        argv = ["skill-roi", "--auto-propose", "--proposals-path", str(proposals)]
        with mock.patch.object(ENGINE, "compute_skill_roi_diagnostics", return_value=report), contextlib.redirect_stdout(io.StringIO()):
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual([0] * 4, list(pool.map(lambda _: ENGINE.main(argv), range(4))))
        records = [json.loads(line) for line in proposals.read_text().splitlines()]
        self.assertEqual(len(report["low_roi_proposals"]), len(records))
        self.assertTrue(all("window_start" in record for record in records))

    def test_retro_append_holds_same_sidecar_and_rejects_corrupt_history(self):
        proposals = self.root / "proposals.jsonl"
        original = json.dumps
        def checked(record, **kwargs):
            with proposals.with_name("proposals.jsonl.lock").open("a+") as other:
                with self.assertRaises(BlockingIOError, msg="retro append must own the same sidecar"):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(record, **kwargs)
        with mock.patch.object(ENGINE.json, "dumps", checked):
            ENGINE.route_retro_candidate({"id": "metric", "title": "Metric"}, proposals_path=proposals)
        proposals.write_text("{corrupt}\n")
        with self.assertRaises(ValueError):
            ENGINE.route_retro_candidate({"id": "metric", "title": "Metric"}, proposals_path=proposals)


class UpstreamIntegrationTests(unittest.TestCase):
    setUp = Round2LearningTests.setUp
    add_topic = Round2LearningTests.add_topic
    discover = Round2LearningTests.discover

    def test_event_and_discovery_share_lock_identity_and_immutable_day(self):
        self.add_topic("second-question")
        first = self.discover(now_utc=datetime(2026, 11, 11, 8, tzinfo=timezone.utc))["proposals_written"][0]
        original = DIGEST_VAULT._append_valid_proposal
        def append(path, record):
            with path.with_name(path.name + ".lock").open("a+") as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(path, record)
        with mock.patch.object(DIGEST_VAULT, "_append_valid_proposal", side_effect=append), \
             mock.patch.object(DIGEST_VAULT, "publish_blog", side_effect=AssertionError("publication forbidden")):
            approved = DIGEST_VAULT.handle_learning_event(action="Approve", topic_key=first["topic_key"],
                event_id="approve", proposals_path=self.proposals, vault_dir=self.vault,
                now_utc=datetime(2026, 11, 12, 8, tzinfo=timezone.utc))
        self.assertEqual(first["id"], approved["proposal_written"]["id"])
        self.assertEqual(first["created_at"], approved["proposal_written"]["created_at"])
        self.assertEqual("2026-11-11", approved["proposal_written"]["proposed_on"])
        next_day = self.discover(now_utc=datetime(2026, 11, 12, 9, tzinfo=timezone.utc), materialize_before_approval=True)
        self.assertEqual("second-question", next_day["topics"][0]["topic_key"])
        self.assertFalse((self.vault / "concepts").exists())
        with self.proposals.with_name(self.proposals.name + ".lock").open("a+") as other:
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_alias_decisions_replay_terminal_revision_and_corrupt_state(self):
        self.topics[0]["aliases"] = ["old-question"]
        self.add_topic("second-question")
        self.discover()
        kwargs = {"proposals_path": self.proposals, "vault_dir": self.vault, "topic_key": "old-question"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: DIGEST_VAULT.handle_learning_event(action="Approve", event_id="same", **kwargs), range(4)))
        self.assertEqual(1, sum(not result.get("deduplicated") for result in results))
        task_keys = {result.get("exploration_task_key") for result in results if result.get("exploration_task_key")}
        self.assertEqual(1, len(task_keys))
        approved_rev = max(result["revision"] for result in results)
        done = DIGEST_VAULT.handle_learning_event(action="Done", event_id="done", **kwargs)
        stale = DIGEST_VAULT.handle_learning_event(action="Approve", event_id="late", revision=approved_rev, **kwargs)
        self.assertFalse(stale["ok"])
        self.assertEqual("learned", stale["learning_state"])
        self.assertGreater(done["revision"], approved_rev)
        (self.root / "learning-topics-state.json").write_text("{corrupt}")
        self.assertEqual("health_error", self.discover()["status"])

    def test_auto_propose_preserves_metadata_and_dedups_closed_legacy_aliases(self):
        candidate = {"id": "reduce-m1-ping-pong", "ref": "retro://agent-efficiency-m1",
            "legacy_ids": ["improve-m1-review-pingpong"], "legacy_refs": ["proposal://improve-m1-review-pingpong"],
            "title": "Review repair", "window_cst": "7d", "window_start": "2026-11-06T00:00:00+08:00",
            "window_end": "2026-11-13T00:00:00+08:00", "source_as_of": "2026-11-11T00:00:00+08:00",
            "sample_size": 2, "coverage_status": "healthy", "estimated": True}
        legacy = {**candidate, "id": candidate["legacy_ids"][0], "ref": candidate["legacy_refs"][0],
            "ts": "2026-11-11T00:00:00Z", "agent": "retro-loop", "status": "approved"}
        self.proposals.write_text(json.dumps(legacy) + "\n")
        self.assertEqual([], ENGINE._queue_recommendations([candidate], self.proposals))
        fresh = {**candidate, "id": "new-metric", "ref": "retro://new-metric", "legacy_ids": [], "legacy_refs": []}
        with ThreadPoolExecutor(max_workers=4) as pool:
            emitted = list(pool.map(lambda _: ENGINE._queue_recommendations([fresh], self.proposals), range(4)))
        self.assertEqual(1, sum(len(rows) for rows in emitted))
        record = json.loads(self.proposals.read_text().splitlines()[-1])
        for field in ("window_cst", "window_start", "window_end", "source_as_of", "sample_size", "coverage_status", "estimated"):
            self.assertEqual(fresh[field], record[field])

    def test_ci_only_and_blocker_only_breaches_keep_unknown_token_rates(self):
        wh = self.root / "warehouse.db"
        with contextlib.closing(sqlite3.connect(wh)) as conn:
            conn.execute("CREATE TABLE fact_ci_job_run (failure_category, cst_day)")
            conn.execute("INSERT INTO fact_ci_job_run VALUES ('build_or_test_failure', '2026-11-11')")
            conn.execute("CREATE TABLE fact_gtt_event (evt_type, cst_day)")
            conn.executemany("INSERT INTO fact_gtt_event VALUES ('TASK_BLOCKED', '2026-11-11')", [()] * 4)
            conn.commit()
        with mock.patch.object(ENGINE, "datetime", FrozenDateTime):
            report = ENGINE.compute_agent_efficiency(wh, self.root / "missing-usage", self.root / "missing-copilot",
                auto_propose=True, proposals_path=self.proposals)
        self.assertEqual(2, report["proposals_written"])
        m3, m5 = (report["metrics"][key] for key in ("M3_flow_and_blockers", "M5_steering_vs_coding_split"))
        self.assertIsNone(m3["retry_rate"])
        self.assertIsNone(m5["steering_token_share"])
        self.assertEqual(0, m5["first_pass_pr_green_rate"])
        self.assertEqual(1, m5["first_pass_pr_green_freshness"]["sample_size"])
        self.assertTrue(all(row["sample_size"] > 0 for row in report["recommendations"]))


if __name__ == "__main__":
    unittest.main()
