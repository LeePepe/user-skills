#!/usr/bin/env python3
"""Regression tests for bounded aidata inspector output."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INSPECTOR = REPO / "skills/aidash-metrics/scripts/inspect_aidata_pipeline.py"
LINEAGE_POINTER = "skills/aidash-metrics/references/aidata-l1-l5-lineage.md"
SHARD_COUNT = 220


class AidataCompactTests(unittest.TestCase):
    def make_fixture(self, root: Path, *, multi_metric: bool = False) -> None:
        (root / "L1_collect/raw/claude_jsonl").mkdir(parents=True)
        for index in range(SHARD_COUNT):
            record = {
                "skill_names": ["aidash-metrics"],
                "usage": {"input_tokens": index + 1, "output_tokens": 2},
            }
            if multi_metric:
                record.update(
                    {
                        "reference_path": "references/README.md",
                        "script_path": "scripts/inspect.py",
                        "duration_ms": 12,
                        "eval_case": "compact-output",
                        "interception": True,
                        "escape": False,
                    }
                )
            shard = root / f"L1_collect/raw/claude_jsonl/shard-{index:03d}.jsonl"
            shard.write_text(json.dumps(record) + "\n", encoding="utf-8")
        (root / "schema").mkdir()
        warehouse_ddl = "CREATE TABLE fact_turn(skill_names TEXT, input_tokens INTEGER, output_tokens INTEGER);\n"
        query = "SELECT skill_names AS skill, input_tokens + output_tokens AS tokens FROM fact_turn;\n"
        snapshot = (
            "def build_snapshot(report, daily):\n"
            "    return {'skills_daily': daily, 'coverage_status': report.get('coverage_status'), "
            "'source_as_of': report.get('source_as_of')}\n"
        )
        if multi_metric:
            warehouse_ddl = (
                "CREATE TABLE aaa_partial(input_tokens INTEGER);\n"
                "CREATE TABLE fact_turn(skill_names TEXT, input_tokens INTEGER, output_tokens INTEGER);\n"
                "CREATE TABLE reference_hit(reference_path TEXT);\n"
                "CREATE TABLE script_call(script_path TEXT, duration_ms INTEGER);\n"
                "CREATE TABLE fact_gtt_event(eval_case TEXT, interception INTEGER, \"escape\" INTEGER);\n"
            )
            query = (
                "SELECT skill_names AS skill, input_tokens + output_tokens AS tokens FROM fact_turn;\n"
                "SELECT count(*) AS reference_hits, reference_path FROM reference_hit GROUP BY reference_path;\n"
                "SELECT avg(duration_ms) AS script_latency, duration_ms, max(duration_ms) AS p95_ms FROM script_call;\n"
                "SELECT count(*) AS eval_effect, eval_case, interception, \"escape\" FROM fact_gtt_event;\n"
            )
            snapshot = (
                "def build_snapshot(report, daily):\n"
                "    return {'skills_daily': daily, 'coverage_status': report.get('coverage_status'), "
                "'source_as_of': report.get('source_as_of'), "
                "'reference_hits': {'reference_path': report.get('reference_path')}, "
                "'script_latency': {'duration_ms': report.get('duration_ms'), 'p95_ms': report.get('p95_ms')}, "
                "'eval_effect': {'eval_case': report.get('eval_case'), "
                "'interception': report.get('interception'), 'escape': report.get('escape')}}\n"
            )
        (root / "schema/warehouse.sql").write_text(warehouse_ddl, encoding="utf-8")
        if multi_metric:
            (root / "L2_normalize/clean").mkdir(parents=True)
            with sqlite3.connect(root / "L2_normalize/clean/claude_jsonl.db") as db:
                db.executescript(
                    "CREATE TABLE turn(skill_names TEXT, input_tokens INTEGER, output_tokens INTEGER);\n"
                    "CREATE TABLE reference_hit(reference_path TEXT);\n"
                    "CREATE TABLE script_call(script_path TEXT, duration_ms INTEGER);\n"
                    "CREATE TABLE eval_event(eval_case TEXT, interception INTEGER, \"escape\" INTEGER);\n"
                )
            (root / "L3_merge").mkdir()
            with sqlite3.connect(root / "L3_merge/warehouse.db") as db:
                db.executescript(warehouse_ddl)
        (root / "L4_serve/queries/retro").mkdir(parents=True)
        (root / "L4_serve/queries/retro/skill_roi_diagnostics.sql").write_text(query, encoding="utf-8")
        (root / "L5_apps/digest").mkdir(parents=True)
        (root / "L5_apps/digest/skills_roi_snapshot.py").write_text(snapshot, encoding="utf-8")

    def run_inspector(self, root: Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-B", str(INSPECTOR), *flags, "--json"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_summary_is_bounded_and_retains_l1_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            result = self.run_inspector(root, "--aidata-root", str(root), "--metrics", "skills_roi")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stdout.encode()), 15_000)
            payload = json.loads(result.stdout)
            self.assertGreaterEqual(payload["l1_total_count"], SHARD_COUNT)
            self.assertEqual(payload["l1_total_files"], SHARD_COUNT)
            self.assertLess(len(payload["layers"]["l1"]), payload["l1_total_count"])
            self.assertLess(
                len(payload["metrics"]["skills_roi"]["coverage"]["l1"]["evidence"]),
                payload["l1_total_files"],
            )

    def test_multi_metric_compaction_preserves_semantic_contracts(self) -> None:
        metrics = ("skills_roi", "reference_hits", "script_latency", "eval_effect")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root, multi_metric=True)
            compacted = self.run_inspector(
                root,
                "--aidata-root",
                str(root),
                "--metrics",
                ",".join(metrics),
            )
            full = self.run_inspector(
                root,
                "--aidata-root",
                str(root),
                "--full",
                "--metrics",
                ",".join(metrics),
            )
            self.assertEqual(compacted.returncode, 0, compacted.stderr)
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertLess(len(compacted.stdout.encode()), 15_000)
            compact_payload = json.loads(compacted.stdout)
            full_payload = json.loads(full.stdout)
            for metric in metrics:
                compact_metric = compact_payload["metrics"][metric]
                full_metric = full_payload["metrics"][metric]
                self.assertEqual(compact_metric["matches"], full_metric["matches"], metric)
                for layer, coverage in compact_metric["coverage"].items():
                    full_coverage = full_metric["coverage"][layer]
                    self.assertEqual(coverage["required_dimensions"], full_coverage["required_dimensions"])
                    self.assertEqual(coverage["fields"], full_coverage["fields"])
                    for entry in coverage["evidence"]:
                        identity = tuple(entry[key] for key in ("path", "source", "kind", "table", "complete"))
                        matching = next(
                            candidate
                            for candidate in full_coverage["evidence"]
                            if tuple(candidate[key] for key in ("path", "source", "kind", "table", "complete")) == identity
                        )
                        self.assertEqual(entry["fields"], matching["fields"])
            representative = compact_payload["metrics"]["skills_roi"]["coverage"]["l3"]["evidence"][0]
            self.assertEqual(representative["table"], "fact_turn")
            self.assertTrue(representative["complete"])

    def test_summary_alias_and_full_restore_unlimited_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            default = self.run_inspector(root, "--aidata-root", str(root), "--metrics", "skills_roi")
            summary = self.run_inspector(root, "--aidata-root", str(root), "--summary", "--metrics", "skills_roi")
            full = self.run_inspector(root, "--aidata-root", str(root), "--full", "--metrics", "skills_roi")
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual(summary.returncode, 0, summary.stderr)
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertEqual(json.loads(default.stdout), json.loads(summary.stdout))
            full_payload = json.loads(full.stdout)
            self.assertEqual(len(full_payload["layers"]["l1"]), SHARD_COUNT)
            self.assertGreater(len(full.stdout.encode()), len(default.stdout.encode()))

    def test_root_aliases_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_fixture(root)
            for alias in ("--root", "--dir", "--aidata-dir"):
                result = self.run_inspector(root, alias, str(root), "--metric", "skills_roi")
                self.assertEqual(result.returncode, 0, f"{alias}: {result.stderr}")
                self.assertEqual(json.loads(result.stdout)["aidata_root"], str(root.resolve()))

    def test_argument_failures_are_short_and_self_disclosing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                ("--not-a-real-flag",),
                ("--aidata-root", str(root / "missing")),
                ("--aidata-root", str(root), "--metrics", "unknown_metric"),
            )
            for flags in cases:
                result = self.run_inspector(root, *flags)
                self.assertEqual(result.returncode, 2, flags)
                self.assertLessEqual(len(result.stderr.splitlines()), 3, result.stderr)
                self.assertIn(LINEAGE_POINTER, result.stderr)
            missing = self.run_inspector(root, "--aidata-root", str(root / "missing"))
            self.assertIn("missing", missing.stderr)
            unknown = self.run_inspector(root, "--aidata-root", str(root), "--metrics", "unknown_metric")
            self.assertIn("unknown metric(s)", unknown.stderr)


if __name__ == "__main__":
    unittest.main()
