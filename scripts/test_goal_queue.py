#!/usr/bin/env python3
"""Regression tests for tracked feedback queue invariants."""

from __future__ import annotations

import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


os.environ.setdefault("GTT_LINEAR_SYNC", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
QUEUE = REPO_ROOT / "skills" / "goal-task" / "scripts" / "goal-queue.py"


def manifest(
    *,
    task_ids: list[str],
    feedback_ids: list[str] | None = None,
    common_root_cause: bool = False,
) -> dict[str, Any]:
    ids = feedback_ids or [f"FB-{index}" for index in range(1, len(task_ids) + 1)]
    entries = [
        {
            "feedback_id": feedback_id,
            "acceptance": [
                {
                    "acceptance_id": f"{feedback_id}-AC1",
                    "text": f"Acceptance for {feedback_id}",
                }
            ],
        }
        for feedback_id in ids
    ]
    mappings = []
    for task_id, feedback_id in zip(task_ids, ids):
        mapping: dict[str, Any] = {
            "task_id": task_id,
            "feedback_ids": [feedback_id],
            "acceptance_mapping": [
                {
                    "feedback_id": feedback_id,
                    "acceptance_ids": [f"{feedback_id}-AC1"],
                }
            ],
        }
        if common_root_cause:
            mapping["common_root_cause_evidence"] = {
                "summary": "Synthetic shared reproduction",
                "references": ["test://synthetic/common-root-cause"],
            }
        mappings.append(mapping)
    return {
        "schema_version": 1,
        "entries": entries,
        "task_mappings": mappings,
    }


def manifest_for_tasks(
    task_feedback: dict[str, list[str]],
    *,
    acceptance_by_feedback: dict[str, list[tuple[str, str]]] | None = None,
    mapping_order: list[str] | None = None,
) -> dict[str, Any]:
    acceptance_by_feedback = acceptance_by_feedback or {}
    feedback_ids = list(dict.fromkeys(feedback_id for ids in task_feedback.values() for feedback_id in ids))
    entries = []
    for feedback_id in feedback_ids:
        acceptances = acceptance_by_feedback.get(
            feedback_id,
            [(f"{feedback_id}-AC1", f"Acceptance for {feedback_id}")],
        )
        entries.append(
            {
                "feedback_id": feedback_id,
                "acceptance": [
                    {"acceptance_id": acceptance_id, "text": text}
                    for acceptance_id, text in acceptances
                ],
            }
        )

    ordered_task_ids = mapping_order or list(task_feedback)
    mappings = []
    for task_id in ordered_task_ids:
        feedback_ids_for_task = task_feedback[task_id]
        mapping: dict[str, Any] = {
            "task_id": task_id,
            "feedback_ids": list(feedback_ids_for_task),
            "acceptance_mapping": [
                {
                    "feedback_id": feedback_id,
                    "acceptance_ids": [
                        acceptance_id
                        for acceptance_id, _ in acceptance_by_feedback.get(
                            feedback_id,
                            [(f"{feedback_id}-AC1", f"Acceptance for {feedback_id}")],
                        )
                    ],
                }
                for feedback_id in feedback_ids_for_task
            ],
        }
        if len(feedback_ids_for_task) > 1:
            mapping["common_root_cause_evidence"] = {
                "summary": "Synthetic shared reproduction",
                "references": ["test://synthetic/common-root-cause"],
            }
        mappings.append(mapping)
    return {
        "schema_version": 1,
        "entries": entries,
        "task_mappings": mappings,
    }


class GoalQueueFeedbackGuardTests(unittest.TestCase):
    def test_isolated_cli_loads_sibling_helper(self) -> None:
        script = REPO_ROOT / "skills" / "goal-task" / "scripts" / "goal-queue.py"
        for command in ("enqueue", "set-status"):
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(script), command, "--help"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"goal-task {command}: {result.stderr}",
            )
            self.assertIn("usage:", result.stdout.lower())

    def run_queue(
        self,
        handoff_dir: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(QUEUE), *args, "--handoff-dir", str(handoff_dir)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=check,
        )

    def enqueue(
        self,
        handoff_dir: Path,
        task_ids: list[str],
        *,
        feedback_manifest: dict[str, Any] | None = None,
        paths: str = "src/history.py",
        replacement_of: str | None = None,
        depends_on: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        batch = [
            {
                "task_id": task_id,
                "title": task_id,
                "repo": "synthetic/repo",
                "paths": [paths],
                **({"replacement_of": replacement_of} if replacement_of else {}),
                **({"depends_on": [depends_on]} if depends_on else {}),
            }
            for task_id in task_ids
        ]
        args = ["enqueue", "--batch-json", json.dumps(batch)]
        if feedback_manifest is not None:
            args.extend(["--feedback-manifest", json.dumps(feedback_manifest)])
        return self.run_queue(handoff_dir, *args, check=check)

    def test_grouping_requires_evidence_and_preserves_mapping(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "tracked"
            grouped = manifest(
                task_ids=["TK1"],
                feedback_ids=["FB-1", "FB-2"],
            )
            grouped["task_mappings"][0]["feedback_ids"] = ["FB-1", "FB-2"]
            grouped["task_mappings"][0]["acceptance_mapping"].append(
                {
                    "feedback_id": "FB-2",
                    "acceptance_ids": ["FB-2-AC1"],
                }
            )
            missing_evidence = self.enqueue(
                handoff_dir,
                ["TK1"],
                feedback_manifest=grouped,
                check=False,
            )
            self.assertNotEqual(missing_evidence.returncode, 0)
            self.assertIn("common_root_cause_evidence", missing_evidence.stderr)

            grouped["task_mappings"][0]["common_root_cause_evidence"] = {
                "summary": "Synthetic shared reproduction",
                "references": ["test://synthetic/common-root-cause"],
            }
            accepted = json.loads(
                self.enqueue(
                    handoff_dir,
                    ["TK1"],
                    feedback_manifest=grouped,
                ).stdout
            )
            self.assertEqual(accepted["feedback_validation"], "verified")
            queue_data = json.loads((handoff_dir / "queue.json").read_text())
            self.assertEqual(
                queue_data["feedback_manifest"]["entries"][1]["feedback_id"],
                "FB-2",
            )
            self.assertEqual(queue_data["tasks"][0]["feedback_ids"], ["FB-1", "FB-2"])

    def test_tracked_queue_rejects_coverage_duplicates_and_omissions(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "tracked"
            tracked = manifest(task_ids=["TK1"])
            self.enqueue(handoff_dir, ["TK1"], feedback_manifest=tracked)

            omission = self.enqueue(handoff_dir, ["TK2"], check=False)
            self.assertNotEqual(omission.returncode, 0)
            self.assertIn("manifest", omission.stderr.lower())

            duplicate = manifest(task_ids=["TK1", "TK2"], feedback_ids=["FB-1", "FB-1"])
            duplicate_result = self.enqueue(
                Path(tmp_dir) / "duplicate",
                ["TK1", "TK2"],
                feedback_manifest=duplicate,
                check=False,
            )
            self.assertNotEqual(duplicate_result.returncode, 0)
            self.assertIn("duplicate", duplicate_result.stderr.lower())

    def test_completion_requires_per_acceptance_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "tracked"
            self.enqueue(
                handoff_dir,
                ["TK1"],
                feedback_manifest=manifest(task_ids=["TK1"]),
            )
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress", check=True)

            missing = self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "done",
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("evidence", missing.stderr.lower())

            evidence = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "passed",
                        "evidence": ["test://synthetic/history-regression"],
                    }
                ]
            }
            completed = json.loads(
                self.run_queue(
                    handoff_dir,
                    "set-status",
                    "--task-id",
                    "TK1",
                    "--status",
                    "done",
                    "--evidence-json",
                    json.dumps(evidence),
                ).stdout
            )
            self.assertTrue(completed["drained"])

    def test_supersession_requires_replacement_and_preserves_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "tracked"
            tracked = manifest(task_ids=["TK1", "TK2"], feedback_ids=["FB-1", "FB-2"])
            self.enqueue(handoff_dir, ["TK1", "TK2"], feedback_manifest=tracked)
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")

            replacement_manifest = manifest(
                task_ids=["TK1", "TK2"],
                feedback_ids=["FB-1", "FB-2"],
                common_root_cause=True,
            )
            replacement_manifest["task_mappings"][0]["feedback_ids"] = ["FB-1"]
            replacement_manifest["task_mappings"][1]["feedback_ids"] = ["FB-1", "FB-2"]
            replacement_manifest["task_mappings"][1]["acceptance_mapping"].insert(
                0,
                {
                    "feedback_id": "FB-1",
                    "acceptance_ids": ["FB-1-AC1"],
                },
            )
            bad = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "replaced",
                        "evidence": ["test://synthetic/replacement"],
                    }
                ]
            }
            bad_result = self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--superseded-by",
                "TK2",
                "--evidence-json",
                json.dumps(bad),
                "--feedback-manifest",
                json.dumps(replacement_manifest),
                check=False,
            )
            self.assertNotEqual(bad_result.returncode, 0)
            self.assertIn("replacement_task_id", bad_result.stderr)

            good = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "replaced",
                        "replacement_task_id": "TK2",
                        "evidence": ["test://synthetic/replacement"],
                    }
                ]
            }
            result = json.loads(
                self.run_queue(
                    handoff_dir,
                    "set-status",
                    "--task-id",
                    "TK1",
                    "--status",
                    "cancelled",
                    "--superseded-by",
                    "TK2",
                    "--evidence-json",
                    json.dumps(good),
                    "--feedback-manifest",
                    json.dumps(replacement_manifest),
                ).stdout
            )
            self.assertEqual(result["feedback_validation"], "verified")
            queue_data = json.loads((handoff_dir / "queue.json").read_text())
            self.assertEqual(queue_data["tasks"][0]["superseded_by"], "TK2")
            self.assertEqual(queue_data["tasks"][1]["feedback_ids"], ["FB-1", "FB-2"])

    def test_legacy_queue_is_explicitly_unverified_but_compatible(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "legacy"
            enqueued = json.loads(self.enqueue(handoff_dir, ["TK1"]).stdout)
            self.assertEqual(enqueued["feedback_validation"], "unverified_legacy")
            wave = json.loads(
                self.run_queue(handoff_dir, "next-wave", "--mark-in-progress").stdout
            )
            self.assertEqual(wave["feedback_validation"], "unverified_legacy")
            done = json.loads(
                self.run_queue(
                    handoff_dir,
                    "set-status",
                    "--task-id",
                    "TK1",
                    "--status",
                    "done",
                ).stdout
            )
            self.assertEqual(done["feedback_validation"], "unverified_legacy")

    def test_malformed_manifest_and_ci_cancellation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "tracked"
            malformed = self.enqueue(
                handoff_dir,
                ["TK1"],
                feedback_manifest={"schema_version": 1, "entries": []},
                check=False,
            )
            self.assertNotEqual(malformed.returncode, 0)
            self.assertIn("non-empty", malformed.stderr)

            self.enqueue(
                handoff_dir,
                ["TK1"],
                feedback_manifest=manifest(task_ids=["TK1"]),
            )
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")
            evidence = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "cancelled",
                        "evidence": ["authorized scope decision"],
                    }
                ]
            }
            cancelled = self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--authorization",
                "owner://synthetic-approval",
                "--note",
                "CI failed",
                "--evidence-json",
                json.dumps(evidence),
                check=False,
            )
            self.assertNotEqual(cancelled.returncode, 0)
            self.assertIn("CI", cancelled.stderr)

    def test_real_one_to_one_replacement_runs_and_completes(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "replacement"
            initial = manifest_for_tasks({"TK1": ["FB-1"]})
            self.enqueue(handoff_dir, ["TK1"], feedback_manifest=initial)
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")

            replacement = manifest_for_tasks({"TK1": ["FB-1"], "TK2": ["FB-1"]})
            enqueued = self.enqueue(
                handoff_dir,
                ["TK2"],
                feedback_manifest=replacement,
                replacement_of="TK1",
            )
            self.assertEqual(json.loads(enqueued.stdout)["feedback_validation"], "verified")

            evidence = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "replaced",
                        "replacement_task_id": "TK2",
                        "evidence": ["test://synthetic/replacement"],
                    }
                ]
            }
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--superseded-by",
                "TK2",
                "--evidence-json",
                json.dumps(evidence),
            )
            wave = json.loads(
                self.run_queue(
                    handoff_dir,
                    "next-wave",
                    "--mark-in-progress",
                ).stdout
            )
            self.assertEqual([task["task_id"] for task in wave["wave"]], ["TK2"])

            completed = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "passed",
                        "evidence": ["test://synthetic/replacement-delivery"],
                    }
                ]
            }
            result = json.loads(
                self.run_queue(
                    handoff_dir,
                    "set-status",
                    "--task-id",
                    "TK2",
                    "--status",
                    "done",
                    "--evidence-json",
                    json.dumps(completed),
                ).stdout
            )
            self.assertTrue(result["drained"])

    def test_hot_append_preserves_all_original_acceptance_text_and_ids(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "immutable"
            tracked = manifest_for_tasks(
                {"TK1": ["FB-1"], "TK2": ["FB-2"]},
                acceptance_by_feedback={
                    "FB-1": [
                        ("FB-1-AC1", "History renders"),
                        ("FB-1-AC2", "Parallel test requirement"),
                    ],
                    "FB-2": [("FB-2-AC1", "Weather renders")],
                },
            )
            self.enqueue(handoff_dir, ["TK1", "TK2"], feedback_manifest=tracked)
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")

            rewritten = manifest_for_tasks(
                {"TK1": ["FB-1"], "TK2": ["FB-2"], "TK3": ["FB-3"]},
                acceptance_by_feedback={
                    "FB-1": [("FB-1-AC1", "History renders")],
                    "FB-2": [("FB-2-AC1", "Weather was rewritten")],
                    "FB-3": [("FB-3-AC1", "New request")],
                },
            )
            result = self.enqueue(
                handoff_dir,
                ["TK3"],
                feedback_manifest=rewritten,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("preserve", result.stderr.lower())

    def test_chained_replacements_are_order_independent_and_keep_terminal_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "chain"
            initial = manifest_for_tasks({"TK1": ["FB-1"]})
            self.enqueue(handoff_dir, ["TK1"], feedback_manifest=initial)
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")

            tk2_manifest = manifest_for_tasks({"TK1": ["FB-1"], "TK2": ["FB-1"]})
            self.enqueue(
                handoff_dir,
                ["TK2"],
                feedback_manifest=tk2_manifest,
                replacement_of="TK1",
            )
            evidence_12 = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "replaced",
                        "replacement_task_id": "TK2",
                        "evidence": ["test://synthetic/chain-12"],
                    }
                ]
            }
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--superseded-by",
                "TK2",
                "--evidence-json",
                json.dumps(evidence_12),
            )

            tk3_manifest = manifest_for_tasks(
                {"TK1": ["FB-1"], "TK2": ["FB-1"], "TK3": ["FB-1"]},
                mapping_order=["TK2", "TK3", "TK1"],
            )
            self.enqueue(
                handoff_dir,
                ["TK3"],
                feedback_manifest=tk3_manifest,
                replacement_of="TK2",
            )
            evidence_23 = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "replaced",
                        "replacement_task_id": "TK3",
                        "evidence": ["test://synthetic/chain-23"],
                    }
                ]
            }
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK2",
                "--status",
                "cancelled",
                "--superseded-by",
                "TK3",
                "--evidence-json",
                json.dumps(evidence_23),
            )
            wave = json.loads(
                self.run_queue(
                    handoff_dir,
                    "next-wave",
                    "--mark-in-progress",
                ).stdout
            )
            self.assertEqual([task["task_id"] for task in wave["wave"]], ["TK3"])

    def test_explicit_cancelled_dependency_stays_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "explicit-dependency"
            first_manifest = manifest_for_tasks({"TK1": ["FB-1"]})
            self.enqueue(handoff_dir, ["TK1"], feedback_manifest=first_manifest)
            tracked = manifest_for_tasks({"TK1": ["FB-1"], "TK2": ["FB-2"]})
            self.enqueue(
                handoff_dir,
                ["TK2"],
                feedback_manifest=tracked,
                depends_on="TK1",
            )
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")
            evidence = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "cancelled",
                        "evidence": ["owner cancelled undelivered work"],
                    }
                ]
            }
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--authorization",
                "owner://synthetic-cancel",
                "--evidence-json",
                json.dumps(evidence),
            )
            wave = json.loads(self.run_queue(handoff_dir, "next-wave").stdout)
            self.assertEqual(wave["wave"], [])
            blocked = self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK2",
                "--status",
                "in_progress",
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("dependencies", blocked.stderr)

    def test_malformed_tracked_state_never_downgrades_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "malformed"
            handoff_dir.mkdir()
            (handoff_dir / "queue.json").write_text(
                json.dumps(
                    {
                        "goal": "malformed",
                        "version": 1,
                        "feedback_manifest": None,
                        "tasks": [
                            {
                                "task_id": "TK1",
                                "status": "planned",
                                "feedback_ids": ["FB-1"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_queue(handoff_dir, "next-wave", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tracked", result.stderr.lower())
            self.assertNotIn("unverified_legacy", result.stdout)

    def test_supersession_cycles_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "cycle"
            handoff_dir.mkdir()
            tracked = manifest_for_tasks({"TK1": ["FB-1"], "TK2": ["FB-1"]})
            (handoff_dir / "queue.json").write_text(
                json.dumps(
                    {
                        "goal": "cycle",
                        "version": 1,
                        "feedback_manifest": tracked,
                        "tasks": [
                            {
                                "task_id": "TK1",
                                "status": "cancelled",
                                "superseded_by": "TK2",
                                "feedback_ids": ["FB-1"],
                                "acceptance_mapping": [
                                    {
                                        "feedback_id": "FB-1",
                                        "acceptance_ids": ["FB-1-AC1"],
                                    }
                                ],
                            },
                            {
                                "task_id": "TK2",
                                "status": "cancelled",
                                "superseded_by": "TK1",
                                "feedback_ids": ["FB-1"],
                                "acceptance_mapping": [
                                    {
                                        "feedback_id": "FB-1",
                                        "acceptance_ids": ["FB-1-AC1"],
                                    }
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_queue(handoff_dir, "next-wave", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cycle", result.stderr.lower())

    def test_reopening_task_clears_stale_terminal_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "reopen"
            self.enqueue(
                handoff_dir,
                ["TK1"],
                feedback_manifest=manifest(task_ids=["TK1"]),
            )
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")
            evidence = {
                "acceptance": [
                    {
                        "feedback_id": "FB-1",
                        "acceptance_id": "FB-1-AC1",
                        "status": "cancelled",
                        "evidence": ["owner decision"],
                    }
                ]
            }
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "cancelled",
                "--authorization",
                "owner://synthetic-reopen",
                "--evidence-json",
                json.dumps(evidence),
            )
            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id",
                "TK1",
                "--status",
                "in_progress",
            )
            task = json.loads((handoff_dir / "queue.json").read_text())["tasks"][0]
            self.assertNotIn("acceptance_evidence", task)
            self.assertNotIn("cancellation_authorization", task)
            self.assertNotIn("superseded_by", task)

    def test_mandate_drift_ack_and_ruling(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "mandate"
            (handoff_dir / "briefs").mkdir(parents=True)
            (handoff_dir / "spec.md").write_text("v1", encoding="utf-8")
            (handoff_dir / "briefs" / "TK1.md").write_text("brief", encoding="utf-8")
            self.run_queue(
                handoff_dir,
                "enqueue",
                "--task-id", "TK1",
                "--paths", "src/a",
                "--brief", "briefs/TK1.md",
            )
            (handoff_dir / "spec.md").write_text("v2", encoding="utf-8")
            drift = json.loads(self.run_queue(handoff_dir, "next-wave").stdout)
            self.assertEqual(drift["wave"], [])
            task = json.loads((handoff_dir / "queue.json").read_text())["tasks"][0]
            self.assertEqual(task["status"], "blocked")
            self.run_queue(handoff_dir, "ack-spec", "--task-id", "TK1")
            self.run_queue(
                handoff_dir, "ruling", "--task-id", "TK1", "--add-paths", "src/b",
                "--decision", "expand", "--why", "adapter required", "--blast-radius", "local",
            )
            task = json.loads((handoff_dir / "queue.json").read_text())["tasks"][0]
            self.assertEqual(task["paths"], ["src/a", "src/b"])
            self.assertEqual(task["mandate"]["owned_paths"], ["src/a", "src/b"])

    def test_human_priority_slot_and_evolve_budget(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "evolve"
            self.run_queue(
                handoff_dir,
                "enqueue",
                "--batch-json",
                json.dumps([
                    {"task_id": "N1", "repo": "r", "paths": ["a"]},
                    {"task_id": "N2", "repo": "r", "paths": ["b"]},
                    {"task_id": "H1", "repo": "r", "paths": ["c"], "priority": "human"},
                ]),
            )
            wave = json.loads(
                self.run_queue(handoff_dir, "next-wave", "--max-workers", "2", "--mark-in-progress").stdout
            )
            self.assertEqual(len(wave["wave"]), 3)
            self.assertEqual(wave["normal_active_count"], 2)
            self.assertEqual(wave["human_active_count"], 1)
            self.assertTrue(wave["human_slot_used"])
            self.run_queue(handoff_dir, "init-goal", "--intent", "evolve", "--budget-sec", "1")
            self.run_queue(handoff_dir, "budget", "--elapsed-sec", "1")
            stopped = json.loads(self.run_queue(handoff_dir, "next-wave").stdout)
            self.assertTrue(stopped["budget_exhausted"])

    def test_steer_handback_and_herdr_effect(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "steer"
            self.enqueue(handoff_dir, ["TK1"], paths="src/a")
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")
            steered = json.loads(
                self.run_queue(
                    handoff_dir,
                    "steer",
                    "--task-id", "TK1",
                    "--trigger-id", "tr-1",
                    "--message", "update the active task",
                ).stdout
            )
            self.assertEqual(steered["disposition"], "steered")
            with sqlite3.connect(handoff_dir / "state.sqlite") as db:
                effect = db.execute(
                    "SELECT kind, key, value_json FROM effects WHERE kind='herdr_deliver'"
                ).fetchone()
                self.assertIsNotNone(effect)
                self.assertEqual(effect[1], "TK1:tr-1")
                self.assertIn("update the active task", effect[2])

            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id", "TK1",
                "--status", "handback",
                "--trigger-id", "tr-2",
            )
            rejected = json.loads(
                self.run_queue(
                    handoff_dir,
                    "steer",
                    "--paths", "src/a",
                    "--trigger-id", "tr-2",
                    "--message", "do not re-steer",
                ).stdout
            )
            self.assertEqual(rejected["disposition"], "needs_tl_eval")

            self.run_queue(
                handoff_dir,
                "set-status",
                "--task-id", "TK1",
                "--status", "handback",
                "--trigger-id", "tr-3",
                "--keep-working",
            )
            kept = json.loads(
                self.run_queue(
                    handoff_dir,
                    "steer",
                    "--task-id", "TK1",
                    "--trigger-id", "tr-4",
                    "--message", "continue",
                ).stdout
            )
            self.assertEqual(kept["disposition"], "steered")

            missing_selector = json.loads(
                self.run_queue(
                    handoff_dir,
                    "steer",
                    "--message", "needs owner evaluation",
                ).stdout
            )
            self.assertEqual(missing_selector["disposition"], "needs_tl_eval")

    def test_state_sqlite_tables_effect_coalescing_and_later(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "state"
            self.enqueue(handoff_dir, ["TK1"])
            with sqlite3.connect(handoff_dir / "state.sqlite") as db:
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue({"goal_state", "tasks", "events", "effects"} <= tables)
                first = db.execute(
                    "SELECT generation FROM effects WHERE kind='linear_sync'"
                ).fetchone()[0]
                db.execute(
                    "UPDATE effects SET status='done' WHERE kind='linear_sync'"
                )
                db.commit()
                db.execute(
                    "INSERT INTO effects(kind,key,value_json,seq,due_epoch_ms,since) "
                    "VALUES ('herdr_deliver','same','{}',99,0,'0')"
                )
                db.commit()
                db.execute(
                    "INSERT INTO effects(kind,key,value_json,seq,due_epoch_ms,since) "
                    "VALUES ('telemetry_notify','retry','{}',100,0,'0')"
                )
                db.commit()
                self.assertGreaterEqual(first, 1)

            state_module_path = REPO_ROOT / "skills" / "goal-task" / "scripts" / "state_store.py"
            spec = importlib.util.spec_from_file_location("state_store_test", state_module_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            connection = module.connect(handoff_dir)
            try:
                with module.transaction(connection):
                    module.enqueue_effect(connection, "herdr_deliver", "same", {"v": 2}, 10)
                    row = connection.execute(
                        "SELECT generation, status FROM effects WHERE kind='herdr_deliver' AND key='same'"
                    ).fetchone()
                    self.assertEqual(row["generation"], 2)
                    self.assertEqual(row["status"], "pending")
                    module.record_effect_result(
                        connection, "herdr_deliver", "same", error=module.Later(20)
                    )
                row = connection.execute(
                    "SELECT attempts, due_epoch_ms FROM effects WHERE kind='herdr_deliver' AND key='same'"
                ).fetchone()
                self.assertEqual(row["attempts"], 0)
                self.assertEqual(row["due_epoch_ms"], 20)
            finally:
                connection.close()

    def test_experiment_keep_discard_and_budget_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "experiment"
            self.enqueue(handoff_dir, ["TK1"])
            self.run_queue(handoff_dir, "init-goal", "--intent", "evolve")
            self.run_queue(
                handoff_dir,
                "experiment", "--task-id", "TK1", "--decision", "discard",
                "--baseline-metric", "1", "--observed-metric", "0",
            )
            self.run_queue(
                handoff_dir,
                "experiment", "--task-id", "TK1", "--decision", "keep",
                "--baseline-metric", "1", "--observed-metric", "1",
            )
            self.run_queue(
                handoff_dir,
                "experiment", "--task-id", "TK1", "--decision", "discard",
                "--baseline-metric", "1", "--observed-metric", "0",
            )
            self.run_queue(
                handoff_dir,
                "experiment", "--task-id", "TK1", "--decision", "discard",
                "--baseline-metric", "1", "--observed-metric", "0",
            )
            stopped = json.loads(self.run_queue(handoff_dir, "next-wave").stdout)
            self.assertTrue(stopped["budget_exhausted"])
            with sqlite3.connect(handoff_dir / "state.sqlite") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 4)

    def test_receive_routes_and_bootstrap_fixture_defects(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            handoff_dir = Path(tmp_dir) / "receive"
            self.enqueue(handoff_dir, ["TK1"], paths="src/a")
            self.run_queue(handoff_dir, "next-wave", "--mark-in-progress")

            def receive(event_id: str, *, paths: list[str] | None = None, grey: bool = False) -> dict[str, Any]:
                event = {
                    "id": event_id, "source": "owner", "actor": "lee", "thread": "t",
                    "human": True, "addressed": "", "at": "2026-11-05T00:00:00Z",
                    "text": event_id, "meta": {"paths": paths or [], "ambiguous_grey_zone": grey},
                }
                path = handoff_dir / f"{event_id}.json"
                path.write_text(json.dumps(event), encoding="utf-8")
                return json.loads(self.run_queue(handoff_dir, "receive", "--file", str(path)).stdout)

            self.assertEqual(receive("steer-1", paths=["src/a"])["disposition"], "steered")
            self.assertEqual(receive("grey-1", grey=True)["disposition"], "needs_owner_decision")
            self.assertEqual(receive("enqueue-1")["disposition"], "enqueued")
            with sqlite3.connect(handoff_dir / "state.sqlite") as db:
                disposition = db.execute(
                    "SELECT disposition_json FROM inbox_triggers WHERE trigger_id='grey-1'"
                ).fetchone()[0]
                self.assertIn("needs_owner_decision", disposition)
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM events WHERE evt='NEED_OWNER_DECISION'"
                    ).fetchone()[0],
                    1,
                )

            fixture = REPO_ROOT / "skills" / "goal-task" / "tests" / "fixtures" / "bootstrap_ledger_target"
            spec = importlib.util.spec_from_file_location("ledger_fixture", fixture / "ledger_app.py")
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            rows = module.read_transactions(fixture / "sample_txns.csv")
            self.assertEqual(module.month_total(rows, 2026, 2), 41)
            self.assertEqual(module.category_total(rows, "travel"), 25)
            with self.assertRaises(module.AmbiguousDatePolicyError):
                module.read_transactions(fixture / "sample_txns.csv", strict_audit=True)


if __name__ == "__main__":
    unittest.main()
