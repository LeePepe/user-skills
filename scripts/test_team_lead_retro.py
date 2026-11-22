#!/usr/bin/env python3
"""Positive + Counter-Case Replay tests for `team-lead` L2 script roadblock softening.

Verifies:
1. `goal-queue.py`:
   - Positive Replay: accepts subcommand aliases (`init` -> `init-goal`, `status`/`list` -> `next-wave`),
     flag aliases (`--handoff`/`--dir` -> `--handoff-dir`, `--task`/`--id` -> `--task-id`,
     `--dep` -> `--depends-on`, `--path` -> `--paths`, `--owner` on `next-wave`, `--pr-ref` on `set-status`,
     `--note` on `ack-spec`, `--json` global compatibility), and flags before subcommand.
   - Counter-Case Replay: rejects genuinely invalid subcommands or missing required fields with exit=2
     and emits concise `<=3`-line self-disclosing error pointers to `skills/team-lead/references/ledger.md`
     or `playbook.md`.
2. `herdr-dispatch.sh`:
   - Positive Replay: when `--name <val>` exceeds 32 chars (e.g. `skill-retro-my-designer-goal-tl-r1`),
     automatically truncates and hashes it deterministically to `<=32` chars (`<first-25-chars>-<6-char-sha256>`)
     with a soft stderr advisory note instead of failing with `exit 2`. Also supports `--dir`/`--worktree`,
     `--agent-name`, `--prompt-text`, `--role reviewer`/`tl`/`m2`, and `--add-dir` before `--`.
   - Counter-Case Replay: rejects malformed names with invalid characters (e.g. uppercase or spaces),
     missing required arguments, or V3 Round-1 `writer_escalated` with exit=2 and self-disclosing pointers
     to `skills/team-lead/references/bots-and-agents.md`.
3. `goal-supervisor.py`:
   - Positive Replay: `status --check --handoff-dir <dir>` filters to that specific Goal handoff directory
     gracefully without throwing `unrecognized arguments: --handoff-dir` `exit 2`.
   - Counter-Case Replay: invalid flags or unknown subcommands exit with code 2 and emit self-disclosing
     pointers to `skills/team-lead/references/ledger.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("GTT_LINEAR_SYNC", "0")

REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_QUEUE = REPO_ROOT / "skills" / "team-lead" / "scripts" / "goal-queue.py"
HERDR_DISPATCH = REPO_ROOT / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh"
GOAL_SUPERVISOR = REPO_ROOT / "skills" / "team-lead" / "scripts" / "goal-supervisor.py"


class TeamLeadRetroReplayTests(unittest.TestCase):
    def test_goal_queue_positive_and_counter_case_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff_dir = Path(tmp) / "handoff" / "demo-goal"
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")

            # Positive 1: `init` alias + `--dir` alias + `--json` flag
            init_res = subprocess.run(
                [sys.executable, str(GOAL_QUEUE), "init", "--dir", str(handoff_dir), "--intent", "deliver", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init_res.returncode, 0, init_res.stderr)
            self.assertTrue(json.loads(init_res.stdout)["ok"])

            # Positive 2: `enqueue` with `--handoff`, `--task`, `--path`, `--dep` aliases
            enq1 = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "enqueue",
                    "--handoff",
                    str(handoff_dir),
                    "--task",
                    "T1",
                    "--title",
                    "First task",
                    "--path",
                    "skills/team-lead/scripts/goal-queue.py",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(enq1.returncode, 0, enq1.stderr)
            enq2 = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "enqueue",
                    "--dir",
                    str(handoff_dir),
                    "--id",
                    "T2",
                    "--title",
                    "Second task",
                    "--path",
                    "scripts/test_team_lead_retro.py",
                    "--dep",
                    "T1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(enq2.returncode, 0, enq2.stderr)
            self.assertEqual(json.loads(enq2.stdout)["enqueued"][0]["depends_on"], ["T1"])

            # Positive 3: `--handoff-dir` BEFORE subcommand + `--owner` + `--json` on `next-wave`
            wave_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "--handoff-dir",
                    str(handoff_dir),
                    "next-wave",
                    "--mark-in-progress",
                    "--owner",
                    "demo-writer-r1",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(wave_res.returncode, 0, wave_res.stderr)
            wave_payload = json.loads(wave_res.stdout)
            self.assertEqual([t["task_id"] for t in wave_payload["wave"]], ["T1"])

            # Positive 4: `status` / `list` subcommand aliases + `ack-spec --note` + `set-status --pr-ref`
            status_alias = subprocess.run(
                [sys.executable, str(GOAL_QUEUE), "status", "--handoff-dir", str(handoff_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status_alias.returncode, 0, status_alias.stderr)
            self.assertEqual(json.loads(status_alias.stdout)["in_progress_count"], 1)

            ack_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "ack-spec",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--note",
                    "Owner mandate confirmed",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ack_res.returncode, 0, ack_res.stderr)

            done_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "set-status",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--id",
                    "T1",
                    "--status",
                    "done",
                    "--pr-ref",
                    "receipts/T1-r10.md",
                    "--owner",
                    "demo-writer-r1",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done_res.returncode, 0, done_res.stderr)
            self.assertEqual(json.loads(done_res.stdout)["status"], "done")

            # Counter-Case Replay: invalid subcommand or missing required argument still fails with exit=2
            # and emits <=3-line self-disclosing pointer to skills/team-lead/references/ledger.md
            bad_cmd = subprocess.run(
                [sys.executable, str(GOAL_QUEUE), "nonexistent-subcmd", "--handoff-dir", str(handoff_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_cmd.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", bad_cmd.stderr)
            self.assertLessEqual(len(bad_cmd.stderr.strip().splitlines()), 3)

            bad_enq = subprocess.run(
                [sys.executable, str(GOAL_QUEUE), "enqueue", "--handoff-dir", str(handoff_dir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_enq.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", bad_enq.stderr)

    def test_herdr_dispatch_overlong_name_truncation_and_aliases_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            herdr = tmp_path / "herdr"
            herdr.write_text(
                """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["workspace", "create"]:
    print(json.dumps({"result": {"workspace": {"workspace_id": "w99"}, "root_pane": {"pane_id": "w99:p1"}}}))
elif args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane": {"pane_id": "p99"}}}))
elif args[:2] == ["agent", "get"]:
    if not os.path.exists(os.environ["TEST_CAPTURE"]):
        sys.exit(1)
    print(json.dumps({"interactive_ready": True, "agent_status": "working"}))
elif args[:2] == ["agent", "start"]:
    with open(os.environ["TEST_CAPTURE"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\\n")
    print(json.dumps({"ok": True}))
elif args[:2] == ["agent", "prompt"]:
    print(json.dumps({"ok": True}))
else:
    print(json.dumps({"ok": True}))
""",
                encoding="utf-8",
            )
            herdr.chmod(0o755)
            capture = tmp_path / "calls.jsonl"
            env = {
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "p1",
                "HERDR_MOCK_MAGPIE_ONLINE": "1",
                "HERDR_PROMPT_IDLE_GRACE": "0",
                "MOCK_HERDR_LOG": str(capture),
                "TEST_CAPTURE": str(capture),
            }

            # Positive 1: --name > 32 chars is deterministically truncated to <first-25-chars>-<6-char-sha256> (32 chars)
            overlong_name = "skill-retro-my-designer-goal-tl-r1"
            self.assertGreater(len(overlong_name), 32)
            expected_hash = hashlib.sha256(overlong_name.encode("utf-8")).hexdigest()[:6]
            expected_truncated = f"{overlong_name[:25]}-{expected_hash}"
            self.assertEqual(len(expected_truncated), 32)

            res = subprocess.run(
                [
                    str(HERDR_DISPATCH),
                    "--new-workspace",
                    "--workspace-label",
                    "skill-retro-my-designer",
                    "--role",
                    "tl",
                    "--agent-name",
                    overlong_name,
                    "--kind",
                    "copilot",
                    "--worktree",
                    str(tmp_path),
                    "--prompt-text",
                    "hello world",
                    "--add-dir",
                    str(tmp_path),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("Advisory:", res.stderr)
            self.assertIn("skills/team-lead/references/bots-and-agents.md", res.stderr)
            payload = json.loads(res.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["agent"], expected_truncated)
            self.assertEqual(payload["original_name"], overlong_name)
            self.assertTrue(payload["name_truncated"])

            # Positive 2: `--role reviewer` alias maps to `code_reviewer` (`group/reviewing`)
            if capture.exists():
                capture.unlink()
            rev_res = subprocess.run(
                [
                    str(HERDR_DISPATCH),
                    "--name",
                    "phoenix-langfuse-reviewer-r1",
                    "--kind",
                    "codex",
                    "--role",
                    "reviewer",
                    "--dir",
                    str(tmp_path),
                    "--prompt",
                    "review diff",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rev_res.returncode, 0, rev_res.stderr)
            rev_payload = json.loads(rev_res.stdout)
            self.assertEqual(rev_payload["logical_group"], "group/reviewing")

            # Counter-Case 1: invalid characters in --name (e.g. uppercase) still fail with exit=2 + self-disclosing pointer
            bad_name = subprocess.run(
                [
                    str(HERDR_DISPATCH),
                    "--name",
                    "Invalid_Uppercase_Name",
                    "--kind",
                    "copilot",
                    "--cwd",
                    str(tmp_path),
                    "--prompt",
                    "hello",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_name.returncode, 2)
            self.assertIn("skills/team-lead/references/bots-and-agents.md", bad_name.stderr)

            # Counter-Case 2: overlong Round 1 `-r1` name with `writer_escalated` is still rejected (never bypassed by truncation)
            bad_r1_escalated = subprocess.run(
                [
                    str(HERDR_DISPATCH),
                    "--name",
                    "very-long-goal-name-exceeding-32-chars-writer-r1",
                    "--kind",
                    "codex",
                    "--role",
                    "writer_escalated",
                    "--cwd",
                    str(tmp_path),
                    "--prompt",
                    "fix round 1",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_r1_escalated.returncode, 2)
            self.assertIn("strictly reserved for V3 Round 2+", bad_r1_escalated.stderr)
            self.assertIn("skills/team-lead/references/bots-and-agents.md", bad_r1_escalated.stderr)

    def test_goal_supervisor_status_handoff_dir_and_counter_case_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "handoff"
            goal_clean = root / "clean-goal"
            goal_blocked = root / "other-blocked-goal"
            goal_clean.mkdir(parents=True)
            goal_blocked.mkdir(parents=True)

            (goal_clean / "ledger.md").write_text(
                "# Goal: clean-goal\n- TL: clean-goal-goal-tl-r1\n"
                "2026-11-22T01:00:00Z | EVT=DISPATCHED | id=E1 | task=T1\n",
                encoding="utf-8",
            )
            (goal_clean / "queue.json").write_text(
                json.dumps({"tasks": [{"task_id": "T1", "status": "in_progress"}]}),
                encoding="utf-8",
            )
            (goal_blocked / "ledger.md").write_text(
                "# Goal: other-blocked-goal\n- TL: other-blocked-goal-tl-r1\n"
                "2026-11-22T01:00:00Z | EVT=NEED_GO | id=E1 | note=Approve migration\n",
                encoding="utf-8",
            )
            agents_file = Path(tmp) / "agents.json"
            agents_file.write_text(json.dumps({"agents": []}), encoding="utf-8")

            # Positive 1: `status --check --handoff-dir <goal_clean>` scopes to `clean-goal` and exits 0
            clean_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "status",
                    "--check",
                    "--handoff-dir",
                    str(goal_clean),
                    "--herdr-json",
                    str(agents_file),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(clean_res.returncode, 0, clean_res.stderr)
            clean_payload = json.loads(clean_res.stdout)
            self.assertEqual(clean_payload["goal_id"], "clean-goal")
            self.assertEqual(clean_payload["needs_a_person_count"], 0)
            self.assertTrue(clean_payload["healthy"])

            # Positive 2: `status --check --handoff-dir <goal_blocked>` scopes to `other-blocked-goal` and exits 1
            blocked_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "status",
                    "--check",
                    "--handoff-dir",
                    str(goal_blocked),
                    "--herdr-json",
                    str(agents_file),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(blocked_res.returncode, 1)
            blocked_payload = json.loads(blocked_res.stdout)
            self.assertEqual(blocked_payload["goal_id"], "other-blocked-goal")
            self.assertEqual(blocked_payload["needs_a_person_count"], 1)

            # Counter-Case: invalid flag on `goal-supervisor.py` exits 2 with self-disclosing pointer to ledger.md
            bad_sup = subprocess.run(
                [sys.executable, str(GOAL_SUPERVISOR), "status", "--definitely-invalid-flag"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_sup.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", bad_sup.stderr)


if __name__ == "__main__":
    unittest.main()
