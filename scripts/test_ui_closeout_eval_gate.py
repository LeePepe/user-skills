#!/usr/bin/env python3
"""Regression tests for UI Task closeout gates (`EV-M2-015`) in `m2-dispatcher` and `team-lead`."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
M2_PREFLIGHT = REPO_ROOT / "skills/m2-dispatcher/scripts/m2_preflight.py"
FEEDBACK_GUARD = REPO_ROOT / "skills/team-lead/scripts/feedback_guard.py"


class UiCloseoutEvalGateTests(unittest.TestCase):
    def _init_swift_worktree(self, wt: Path) -> str:
        wt.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(wt), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(wt), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(wt), "config", "user.name", "UI Gate Test"], check=True)
        views = wt / "App" / "Views"
        views.mkdir(parents=True, exist_ok=True)
        (views / "RideSummaryView.swift").write_text("struct RideSummaryView {}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-qm", "initial baseline"], check=True)
        (views / "RideSummaryView.swift").write_text(
            "struct RideSummaryView { let headerHeight = 72 }\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(wt), "commit", "-qam", "update RideSummaryView"], check=True)
        return subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_m2_preflight_check_ui_closeout_positive_and_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "cycling-ui-goal"
            (handoff / "briefs").mkdir(parents=True)
            (handoff / "receipts").mkdir(parents=True)
            (handoff / "design.md").write_text("# UI Design Spec\n", encoding="utf-8")
            (handoff / "ledger.md").write_text(
                "# Goal: cycling-ui-goal\n\n## Events\n"
                "- 2026-11-28T10:00:00+08:00 | EVT=DISPATCHED | id=E1 | task=T1 | to=cycling-ui-goal-tl-r1 | note=herdr-dispatch.sh --role team_lead\n",
                encoding="utf-8",
            )
            (handoff / "receipts" / "T1.md").write_text(
                "# R10 Receipt: T1\n"
                "- Commit SHA: 8f4e2a1\n"
                "- Role / Model: writer / gpt-5.6-sol\n"
                "- Snapshots: snapshots/ride_summary_light.png, snapshots/ride_summary_dark.png\n"
                "- Whitespace Audit: check-whitespace-ratio --persona compact PASS (dead_space=18%)\n"
                "- Visual Traceback: magpie/group/designer review PASS — 0 [IMPL_BUG]\n",
                encoding="utf-8",
            )
            for subcmd in ("check-ui-closeout", "check-ui-dispatch"):
                proc = subprocess.run(
                    [sys.executable, str(M2_PREFLIGHT), subcmd, "--handoff-dir", str(handoff), "--json"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["ui_scope"])
                self.assertIn("snapshots/ride_summary_light.png", payload["snapshots"])

    def test_m2_preflight_check_ui_closeout_blocks_bypass_compile_only_and_unresolved_impl_bug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "bypassed-ui-goal"
            (handoff / "receipts").mkdir(parents=True)
            (handoff / "design.md").write_text("# UI Design Spec\n", encoding="utf-8")
            (handoff / "ledger.md").write_text("# Goal: bypassed-ui-goal\n\n## Events\n", encoding="utf-8")
            r10 = handoff / "receipts" / "T1.md"
            r10.write_text(
                "# R10 Receipt\n"
                "- Commit SHA: 8f4e2a1\n"
                "- Role / Model: writer / gpt-5.6-sol\n"
                "- Verification: swift build && swift test PASS\n",
                encoding="utf-8",
            )
            bad = subprocess.run(
                [sys.executable, str(M2_PREFLIGHT), "check-ui-closeout", "--handoff-dir", str(handoff), "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad.returncode, 1)
            payload = json.loads(bad.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "UI_CLOSEOUT_GATE_FAILED")
            self.assertEqual(
                set(payload["missing"]),
                {
                    "m1_herdr_dispatch_missing",
                    "ui_snapshot_png_missing",
                    "ui_whitespace_check_missing",
                    "ui_designer_visual_traceback_missing",
                },
            )
            self.assertEqual(payload["guidance"], "skills/m2-dispatcher/references/pr-dag-auto-heal.md")

            # Add dispatch + PNG + whitespace + Designer traceback, but leave an unresolved [IMPL_BUG]
            (handoff / "ledger.md").write_text(
                "## Events\n- 2026-11-28T10:05:00+08:00 | EVT=READY | id=E1 | to=bypassed-ui-goal-tl-r1\n",
                encoding="utf-8",
            )
            r10.write_text(
                "# R10 Receipt\n"
                "- Commit SHA: 8f4e2a1\n"
                "- Role / Model: writer / gpt-5.6-sol\n"
                "- Snapshots: snapshots/collapsed_center.png\n"
                "- Whitespace: check-whitespace-ratio PASS\n"
                "- Visual Traceback: magpie/group/designer FAIL — [IMPL_BUG] 600pt center black collapse\n",
                encoding="utf-8",
            )
            bug_proc = subprocess.run(
                [sys.executable, str(M2_PREFLIGHT), "check-ui-closeout", "--handoff-dir", str(handoff), "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bug_proc.returncode, 1)
            bug_payload = json.loads(bug_proc.stdout)
            self.assertFalse(bug_payload["ok"])
            self.assertEqual(bug_payload["missing"], ["ui_unresolved_impl_bug"])

    def test_feedback_guard_pre_review_check_ui_positive_negative_and_legacy_non_ui_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wt = root / "wt"
            sha = self._init_swift_worktree(wt)

            # 1. Legacy synthetic .swift fixture WITHOUT design.md or --require-ui-traceback stays compatible
            legacy_brief = root / "legacy_brief.md"
            legacy_brief.write_text("# Brief\n## Owned Paths\n- `App/**`\n", encoding="utf-8")
            legacy_receipt = root / "legacy_r10.md"
            legacy_receipt.write_text(
                f"Commit SHA: {sha}\nRole / Model: writer / gpt-5.6-sol\nV5 Verdict: PASS\n",
                encoding="utf-8",
            )
            legacy_proc = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--worktree",
                    str(wt),
                    "--brief",
                    str(legacy_brief),
                    "--receipt",
                    str(legacy_receipt),
                    "--base-ref",
                    "HEAD~1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(legacy_proc.returncode, 0, legacy_proc.stdout + legacy_proc.stderr)

            # 2. Handoff dir with design.md rejects compile-only R10 receipt
            handoff = root / "handoff" / "ui-goal"
            (handoff / "briefs").mkdir(parents=True)
            (handoff / "receipts").mkdir(parents=True)
            (handoff / "design.md").write_text("# Design Spec\n", encoding="utf-8")
            brief = handoff / "briefs" / "T1.md"
            brief.write_text("# Brief\n## Owned Paths\n- `App/**`\n", encoding="utf-8")
            receipt = handoff / "receipts" / "T1.md"
            receipt.write_text(
                f"Commit SHA: {sha}\nRole / Model: writer / gpt-5.6-sol\nVerification: swift build && swift test PASS\n",
                encoding="utf-8",
            )
            compile_only = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--worktree",
                    str(wt),
                    "--brief",
                    str(brief),
                    "--receipt",
                    str(receipt),
                    "--base-ref",
                    "HEAD~1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_only.returncode, 1)
            bad_report = json.loads(compile_only.stdout)
            bad_codes = {v["code"] for v in bad_report["violations"]}
            self.assertIn("ui_snapshot_png_missing", bad_codes)
            self.assertIn("ui_whitespace_check_missing", bad_codes)
            self.assertIn("ui_designer_visual_traceback_missing", bad_codes)
            self.assertEqual(bad_report["guidance"], "skills/team-lead/references/playbook.md")
            self.assertIn("skills/team-lead/references/playbook.md", compile_only.stderr)

            # 3. Unresolved [IMPL_BUG] is rejected even with PNG & whitespace check
            receipt.write_text(
                f"Commit SHA: {sha}\n"
                "Role / Model: writer / gpt-5.6-sol\n"
                "V5 Verdict: FAIL\n"
                "Snapshots: snapshots/ride_summary.png\n"
                "Whitespace: check-whitespace-ratio PASS\n"
                "Visual Traceback: magpie/group/designer found [IMPL_BUG] middle 600pt black collapse\n",
                encoding="utf-8",
            )
            impl_bug_proc = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--worktree",
                    str(wt),
                    "--brief",
                    str(brief),
                    "--receipt",
                    str(receipt),
                    "--base-ref",
                    "HEAD~1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(impl_bug_proc.returncode, 1)
            impl_bug_codes = {v["code"] for v in json.loads(impl_bug_proc.stdout)["violations"]}
            self.assertEqual(impl_bug_codes, {"ui_unresolved_impl_bug"})

            # 4. Complete UI R10 receipt passes
            receipt.write_text(
                f"Commit SHA: {sha}\n"
                "Role / Model: writer / gpt-5.6-sol\n"
                "V5 Verdict: PASS\n"
                "Snapshots: snapshots/ride_summary.png\n"
                "Whitespace: check-whitespace-ratio PASS\n"
                "Visual Traceback: magpie/group/designer PASS (zero [IMPL_BUG])\n",
                encoding="utf-8",
            )
            ok_proc = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--worktree",
                    str(wt),
                    "--brief",
                    str(brief),
                    "--receipt",
                    str(receipt),
                    "--base-ref",
                    "HEAD~1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ok_proc.returncode, 0, ok_proc.stdout + ok_proc.stderr)
            ok_report = json.loads(ok_proc.stdout)
            self.assertTrue(ok_report["ok"])
            self.assertTrue(ok_report["ui_closeout_enforced"])


if __name__ == "__main__":
    unittest.main()
