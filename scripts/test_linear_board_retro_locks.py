#!/usr/bin/env python3
"""Regression tests for linear-board progressive evolution physical locks (Positive + Counter-Case Replay)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINEAR_SYNC_WRAPPER = ROOT / "skills" / "linear-board" / "scripts" / "linear-sync.py"
DAILY_BRIEF = ROOT / "skills" / "linear-board" / "scripts" / "daily-brief.py"
BOT_BRIDGE = ROOT / "skills" / "linear-board" / "scripts" / "linear-bot-bridge.py"
BACKLOG_GROOMER = ROOT / "skills" / "linear-board" / "scripts" / "backlog_groomer.py"
EMAIL_PIPELINE = ROOT / "skills" / "linear-board" / "scripts" / "email-action-pipeline.py"
ROOT_TRACKER = ROOT / "skills" / "linear-board" / "scripts" / "root_tracker.py"


class LinearBoardRetroLocksTests(unittest.TestCase):
    def _run_cli(self, script: Path, args: list[str], *, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(ROOT),
            input=stdin_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_wrapper_stays_within_thin_wrapper_line_cap(self) -> None:
        lines = LINEAR_SYNC_WRAPPER.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 25, "linear-sync.py wrapper must remain <=25 lines for Valve 4 S3")
        self.assertIn("CANONICAL", "\n".join(lines))

    def test_positive_replay_idea_accepts_milestone_parent_and_legacy_project_alias(self) -> None:
        proc = self._run_cli(
            LINEAR_SYNC_WRAPPER,
            [
                "idea",
                "--title",
                "[Replay] Test Idea",
                "--description",
                "Verify historical exit=2 flags are softened",
                "--project",
                "Agent Workflow",
                "--milestone",
                "Skills Workflow",
                "--parent-issue",
                "TIA-478",
                "--mode",
                "observe",
                "--json",
            ],
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "observe")

    def test_positive_replay_ensure_goal_ignores_ledger_derived_title_and_description_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff_dir = Path(tmp) / "handoff" / "demo-goal"
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "ledger.md").write_text(
                "# Goal: demo-goal\n"
                "- 目标: Verify ensure-goal flag compatibility\n"
                "- Owner: tianpei · TL: demo-goal-tl-r1\n"
                "- 合同版本: team-lead@HEAD\n\n"
                "## Tasks\n"
                "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
                "|---|---|---|---|---|---|\n"
                "| T1 | — | Demo | demo-goal-tl-r1 | in_progress | — |\n\n"
                "## Events\n",
                encoding="utf-8",
            )
            snapshot = {
                "team": {
                    "id": "team-tia",
                    "key": "TIA",
                    "states": {"nodes": [{"id": "st-todo", "name": "Todo", "type": "unstarted"}]},
                    "labels": {"nodes": []},
                    "projects": {"nodes": [{"id": "proj-us", "name": "user-skills"}]},
                },
                "issue": {},
            }
            snap_file = Path(tmp) / "snapshot.json"
            snap_file.write_text(json.dumps(snapshot), encoding="utf-8")

            proc = self._run_cli(
                LINEAR_SYNC_WRAPPER,
                [
                    "ensure-goal",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--title",
                    "Ignored CLI Title",
                    "--description",
                    "Ignored CLI Description",
                    "--project",
                    "Agent Workflow",
                    "--parent-issue",
                    "TIA-478",
                    "--offline-snapshot",
                    str(snap_file),
                    "--mode",
                    "observe",
                    "--json",
                ],
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            create_muts = [m for m in payload["planned_mutations"] if m.get("operation") == "CreateIssue"]
            self.assertEqual(len(create_muts), 1)
            self.assertEqual(create_muts[0]["variables"]["input"].get("projectId"), "proj-us")

    def test_positive_replay_update_issue_maps_legacy_project_names_in_equals_and_space_forms(self) -> None:
        for flag_tokens, expected_proj in (
            (["--project", "Agent Workflow"], "user-skills"),
            (["--project=Personal project"], "Personal Products & Home"),
            (["--project", "Bing"], "Work Business"),
        ):
            proc = self._run_cli(
                LINEAR_SYNC_WRAPPER,
                ["update-issue", "--id", "TIA-463", "--state", "In Progress", *flag_tokens, "--mode", "observe", "--json"],
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["planned_mutations"][0]["variables"]["input"]["projectId"], expected_proj)

    def test_counter_case_idea_preserves_required_title_and_description_passthrough(self) -> None:
        # Counter-case 1: idea WITHOUT --title MUST still fail with exit=2 (not silently stripped!)
        missing_title = self._run_cli(LINEAR_SYNC_WRAPPER, ["idea", "--description", "No title", "--mode", "observe"])
        self.assertEqual(missing_title.returncode, 2)
        self.assertIn("skills/linear-board/references/daily-brief-and-bot.md", missing_title.stderr)

        # Counter-case 2: idea WITH --title and --description preserves both in planned CreateIssue mutation
        valid_idea = self._run_cli(
            LINEAR_SYNC_WRAPPER,
            ["idea", "--title", "Kept Title", "--description", "Kept Body", "--project", "Personal Core & Gateways", "--mode", "observe"],
        )
        self.assertEqual(valid_idea.returncode, 0, msg=valid_idea.stderr)
        payload = json.loads(valid_idea.stdout)
        inp = payload["planned_mutations"][0]["variables"]["input"]
        self.assertEqual(inp["title"], "Kept Title")
        self.assertEqual(inp["description"], "Kept Body")

    def test_counter_case_all_linear_board_clis_emit_self_disclosing_drawer_pointer_on_exit_2(self) -> None:
        for script in (LINEAR_SYNC_WRAPPER, DAILY_BRIEF, BOT_BRIDGE, BACKLOG_GROOMER, EMAIL_PIPELINE, ROOT_TRACKER):
            proc = self._run_cli(script, ["--definitely-invalid-flag-xyz"])
            self.assertEqual(proc.returncode, 2, msg=f"{script.name} expected exit=2")
            self.assertIn(
                "skills/linear-board/references/daily-brief-and-bot.md",
                proc.stderr,
                msg=f"{script.name} stderr missing self-disclosing reference pointer: {proc.stderr!r}",
            )


if __name__ == "__main__":
    unittest.main()
