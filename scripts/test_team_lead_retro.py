#!/usr/bin/env python3
"""Positive + Counter-Case Replay tests for `team-lead` L2 script roadblock softening & L3 crystallization.

Verifies:
1. `goal-queue.py`:
   - Positive Replay: accepts subcommand aliases (`init` -> `init-goal`, `status`/`list` -> `next-wave`),
     flag aliases (`--handoff`/`--dir` -> `--handoff-dir`, `--task`/`--id` -> `--task-id`,
     `--dep` -> `--depends-on`, `--path` -> `--paths`, `--owner`/`--actor` on `enqueue`/`next-wave`/`set-status`,
     `--pr-ref`/`--head`/`--pr-head` on `set-status`, `--note` on `ack-spec`, `--json` global compatibility),
     and flags before subcommand.
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
   - Positive Replay: `status --check --handoff-dir <dir>` and `--goal`/`--goal-id` on `status`/`scan`/`observe`/`watch`/`telemetry`
     resolve cleanly against `--handoff-root`.
   - Counter-Case Replay: invalid flags or missing required goal selectors exit with code 2 and emit self-disclosing
     pointers to `skills/team-lead/references/ledger.md`.
4. `ci-monitor.py`:
   - Positive Replay: positional PR number/URL, repo auto-inference, optional `--receipt` default, `--enable-auto` /
     `--enable-auto-merge` / `--auto-merge` / `--merge-method` / `--max-wait` / `--handoff-dir` / `--goal` / `--task` /
     `--no-cleanup-worktree` / `--json`, plus Layer-3 `checks` (`pr-checks`) soft-advisory subcommand (`exit=0`).
   - Counter-Case Replay: invalid flags or missing PR exit 2 with `<=3` lines pointing to `playbook.md` & `eval-cases.json`.
5. `linear-sync.py`, `feedback_guard.py`, and `git-gc.py`:
   - Positive Replay: `--json` compatibility, subcommand/flag aliases, positional `handoff_dir`, and `--head-ref`.
   - Counter-Case Replay: `exit=2` self-disclosing `<=3`-line pointers to `ledger.md`, `playbook.md`, and `eval-cases.json`.
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
CI_MONITOR = REPO_ROOT / "skills" / "team-lead" / "scripts" / "ci-monitor.py"
LINEAR_SYNC = REPO_ROOT / "skills" / "team-lead" / "scripts" / "linear-sync.py"
FEEDBACK_GUARD = REPO_ROOT / "skills" / "team-lead" / "scripts" / "feedback_guard.py"
GIT_GC = REPO_ROOT / "skills" / "team-lead" / "scripts" / "git-gc.py"


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

            # Positive 3: `status --check --handoff-dir <done-goal>` on a completed (GOAL_DONE) goal
            # with live agents in herdr-json exits cleanly with 0 without raising KeyError: 'ledger_names'
            goal_done = root / "done-goal"
            goal_done.mkdir(parents=True)
            (goal_done / "ledger.md").write_text(
                "# Goal: done-goal\n- TL: done-goal-goal-tl-r1\n"
                "2026-11-22T01:00:00Z | EVT=DISPATCHED | id=E1 | task=T1 | from=done-goal-goal-tl-r1 | to=done-goal-writer-r1\n"
                "2026-11-22T01:10:00Z | EVT=GOAL_DONE | id=E2 | from=done-goal-goal-tl-r1 | note=Completed\n",
                encoding="utf-8",
            )
            (goal_done / "queue.json").write_text(
                json.dumps({"tasks": [{"task_id": "T1", "status": "done"}]}),
                encoding="utf-8",
            )
            agents_with_live = Path(tmp) / "agents-live.json"
            agents_with_live.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"name": "unrelated-worker-r1", "status": "working", "cwd": "/tmp/unrelated"},
                            {"name": "done-goal-writer-r1", "status": "done", "cwd": "/tmp/done-goal"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            done_res = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "status",
                    "--check",
                    "--handoff-dir",
                    str(goal_done),
                    "--herdr-json",
                    str(agents_with_live),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(done_res.returncode, 0, done_res.stderr)
            done_payload = json.loads(done_res.stdout)
            self.assertEqual(done_payload["goal_id"], "done-goal")
            self.assertEqual(done_payload["needs_a_person_count"], 0)
            self.assertTrue(done_payload["healthy"])

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
            self.assertLessEqual(len(bad_sup.stderr.strip().splitlines()), 3)

            # Positive 4: `status --check --goal <goal-id>` and `observe/telemetry --goal <goal-id>` resolve via `--handoff-root`
            goal_flag_status = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "status",
                    "--check",
                    "--handoff-root",
                    str(root),
                    "--goal",
                    "clean-goal",
                    "--herdr-json",
                    str(agents_file),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(goal_flag_status.returncode, 0, goal_flag_status.stderr)
            self.assertEqual(json.loads(goal_flag_status.stdout)["goal_id"], "clean-goal")

            goal_flag_observe = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "observe",
                    "--handoff-root",
                    str(root),
                    "--goal-id",
                    "clean-goal",
                    "--herdr-json",
                    str(agents_file),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(goal_flag_observe.returncode, 0, goal_flag_observe.stderr)
            self.assertEqual(json.loads(goal_flag_observe.stdout)["goal_id"], "clean-goal")

            goal_flag_telemetry = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_SUPERVISOR),
                    "telemetry",
                    "--handoff-root",
                    str(root),
                    "--goal",
                    "done-goal",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(goal_flag_telemetry.returncode, 0, goal_flag_telemetry.stderr)
            self.assertTrue(json.loads(goal_flag_telemetry.stdout)["goal_done"])

            # Counter-Case: `observe` without `--handoff-dir` or `--goal` exits 2 with <=3-line self-disclosing pointer
            missing_obs_goal = subprocess.run(
                [sys.executable, str(GOAL_SUPERVISOR), "observe", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing_obs_goal.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", missing_obs_goal.stderr)
            self.assertLessEqual(len(missing_obs_goal.stderr.strip().splitlines()), 3)

    def test_ci_monitor_positive_and_counter_case_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            gh_log = tmp_path / "gh_calls.jsonl"
            fake_gh = bin_dir / "gh"
            fake_gh.write_text(
                """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "merge"]:
    print("auto-merge enabled")
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    pr_arg = args[2]
    state_val = os.environ.get("FAKE_PR_STATE", "MERGED")
    if state_val == "OPEN_THEN_MERGED":
        marker = os.environ["GH_LOG"] + ".seen"
        if os.path.exists(marker):
            state_val = "MERGED"
        else:
            open(marker, "w").write("1")
            state_val = "OPEN"
    print(json.dumps({
        "number": int(pr_arg),
        "state": "MERGED" if state_val == "MERGED" else "OPEN",
        "mergedAt": "2026-11-23T00:00:00Z" if state_val == "MERGED" else None,
        "mergeCommit": {"oid": "abc1234def5678"},
        "mergeStateStatus": "CLEAN",
        "headRefOid": os.environ.get("FAKE_HEAD_OID", "abc1234def5678"),
        "headRefName": "feat/demo",
        "statusCheckRollup": [{"name": "verify", "conclusion": "SUCCESS", "state": "SUCCESS"}],
        "url": f"https://github.com/LeePepe/user-skills/pull/{pr_arg}",
    }))
    sys.exit(0)
sys.exit(1)
""",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            handoff_dir = tmp_path / "handoff" / "demo-ci-goal"
            handoff_dir.mkdir(parents=True)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "GH_LOG": str(gh_log),
                "FAKE_PR_STATE": "OPEN_THEN_MERGED",
            }

            # Positive 1: positional PR number + optional `--receipt` (handoff-dir default) + `--enable-auto-merge`
            # + `--merge-method squash` + `--max-wait` + `--goal` + `--task` + `--no-cleanup-worktree` + `--json`
            pr_pos = subprocess.run(
                [
                    sys.executable,
                    str(CI_MONITOR),
                    "pr",
                    "226",
                    "--repo",
                    "LeePepe/user-skills",
                    "--enable-auto-merge",
                    "--merge-method",
                    "squash",
                    "--interval",
                    "0",
                    "--max-wait",
                    "600",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--goal",
                    "demo-ci-goal",
                    "--task",
                    "T1",
                    "--no-cleanup-worktree",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(pr_pos.returncode, 0, pr_pos.stderr)
            pr_payload = json.loads(pr_pos.stdout)
            self.assertEqual(pr_payload["status"], "PR_MERGED")
            self.assertEqual(pr_payload["pr"], 226)
            self.assertIsNone(pr_payload["gc_result"])
            expected_receipt = handoff_dir / "receipts" / "ci-monitor-pr-226.md"
            self.assertEqual(pr_payload["receipt"], str(expected_receipt))
            self.assertTrue(expected_receipt.is_file())
            logged_calls = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(
                any(
                    call[:2] == ["pr", "merge"]
                    and "--auto" in call
                    and "--squash" in call
                    and "--match-head-commit" in call
                    for call in logged_calls
                ),
                logged_calls,
            )

            # Positive 1b: bare `--auto-merge` respects `--merge-method rebase`, and `--head-sha` mismatch
            # aborts with `PR_HEAD_DRIFT` BEFORE invoking `gh pr merge`
            gh_log.write_text("", encoding="utf-8")
            seen_marker = Path(str(gh_log) + ".seen")
            if seen_marker.exists():
                seen_marker.unlink()
            rebase_res = subprocess.run(
                [
                    sys.executable,
                    str(CI_MONITOR),
                    "pr",
                    "226",
                    "--repo",
                    "LeePepe/user-skills",
                    "--auto-merge",
                    "--merge-method",
                    "rebase",
                    "--interval",
                    "0",
                    "--no-cleanup-worktree",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rebase_res.returncode, 0, rebase_res.stderr)
            rebase_calls = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(
                any(call[:2] == ["pr", "merge"] and "--rebase" in call for call in rebase_calls),
                rebase_calls,
            )

            gh_log.write_text("", encoding="utf-8")
            if seen_marker.exists():
                seen_marker.unlink()
            drift_res = subprocess.run(
                [
                    sys.executable,
                    str(CI_MONITOR),
                    "pr",
                    "226",
                    "--repo",
                    "LeePepe/user-skills",
                    "--head-sha",
                    "deadbeef00000000",
                    "--enable-auto-merge",
                    "--no-cleanup-worktree",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(drift_res.returncode, 1)
            self.assertEqual(json.loads(drift_res.stdout)["status"], "PR_HEAD_DRIFT")
            drift_calls = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertFalse(any(call[:2] == ["pr", "merge"] for call in drift_calls), drift_calls)

            # Positive 2: positional PR URL auto-extracts PR number + infers `--repo` + `--auto-merge squash` + pre-subcommand `--json`
            gh_log.write_text("", encoding="utf-8")
            if seen_marker.exists():
                seen_marker.unlink()
            pr_url_res = subprocess.run(
                [
                    sys.executable,
                    str(CI_MONITOR),
                    "--json",
                    "pr",
                    "https://github.com/LeePepe/user-skills/pull/226",
                    "--auto-merge",
                    "squash",
                    "--enable-auto",
                    "--interval",
                    "0",
                    "--no-cleanup-worktree",
                    "--out",
                    str(tmp_path / "url-receipt.md"),
                ],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(pr_url_res.returncode, 0, pr_url_res.stderr)
            url_payload = json.loads(pr_url_res.stdout)
            self.assertEqual(url_payload["repo"], "LeePepe/user-skills")
            self.assertEqual(url_payload["pr"], 226)

            # Positive 3: Layer-3 soft-crystallized `checks` subcommand (and `pr-checks` alias) exits 0 with facts-only JSON
            env_open = {**env, "FAKE_PR_STATE": "OPEN"}
            checks_res = subprocess.run(
                [
                    sys.executable,
                    str(CI_MONITOR),
                    "checks",
                    "https://github.com/LeePepe/user-skills/pull/226",
                    "--json",
                ],
                cwd=tmp_path,
                env=env_open,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checks_res.returncode, 0, checks_res.stderr)
            checks_payload = json.loads(checks_res.stdout)
            self.assertTrue(checks_payload["ok"])
            self.assertEqual(checks_payload["gate_tier"], "soft_advisory_exit_0")
            self.assertEqual(checks_payload["pr"], 226)
            self.assertEqual(checks_payload["repo"], "LeePepe/user-skills")
            self.assertEqual(len(checks_payload["checks"]), 1)

            alias_checks = subprocess.run(
                [sys.executable, str(CI_MONITOR), "pr-checks", "226", "--repo", "LeePepe/user-skills"],
                cwd=tmp_path,
                env=env_open,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(alias_checks.returncode, 0, alias_checks.stderr)
            self.assertEqual(json.loads(alias_checks.stdout)["gate_tier"], "soft_advisory_exit_0")

            # Counter-Case 1: invalid flag exits 2 with <=3 stderr lines pointing to playbook.md & eval-cases.json
            bad_flag = subprocess.run(
                [sys.executable, str(CI_MONITOR), "pr", "226", "--unknown-flag-xyz"],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_flag.returncode, 2)
            self.assertIn("skills/team-lead/references/playbook.md", bad_flag.stderr)
            self.assertIn("skills/team-lead/references/eval-cases.json", bad_flag.stderr)
            self.assertLessEqual(len(bad_flag.stderr.strip().splitlines()), 3)

            # Counter-Case 2: missing PR number/URL exits 2 with <=3 stderr lines pointing to playbook.md
            missing_pr = subprocess.run(
                [sys.executable, str(CI_MONITOR), "pr", "--repo", "LeePepe/user-skills"],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing_pr.returncode, 2)
            self.assertIn("skills/team-lead/references/playbook.md", missing_pr.stderr)
            self.assertLessEqual(len(missing_pr.stderr.strip().splitlines()), 3)

    def test_linear_sync_positive_and_counter_case_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff_dir = Path(tmp) / "handoff" / "demo-linear-goal"
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "ledger.md").write_text(
                "# Goal: demo-linear-goal\n- Owner: tianpei\n- Status: in_progress\n\n"
                "## Tasks\n| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
                "|---|---|---|---|---|---|\n| T1 | — | Demo | writer | in_progress | — |\n",
                encoding="utf-8",
            )

            # Positive 1: `ensure-goal --handoff-dir ... --mode observe --json` + `ensure` alias + positional handoff_dir
            ens_res = subprocess.run(
                [
                    sys.executable,
                    str(LINEAR_SYNC),
                    "ensure-goal",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--project",
                    "user-skills",
                    "--mode",
                    "observe",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ens_res.returncode, 0, ens_res.stderr)
            self.assertTrue(json.loads(ens_res.stdout)["ok"])

            ens_alias = subprocess.run(
                [
                    sys.executable,
                    str(LINEAR_SYNC),
                    "--json",
                    "ensure",
                    str(handoff_dir),
                    "--issue-id",
                    "TIA-99",
                    "--mode",
                    "observe",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ens_alias.returncode, 0, ens_alias.stderr)
            self.assertEqual(json.loads(ens_alias.stdout)["identifier"], "TIA-99")

            # Positive 2: `sync-ledger --dir ... --json` and `sync` alias
            sync_res = subprocess.run(
                [
                    sys.executable,
                    str(LINEAR_SYNC),
                    "sync-ledger",
                    "--dir",
                    str(handoff_dir),
                    "--mode",
                    "observe",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sync_res.returncode, 0, sync_res.stderr)
            self.assertTrue(json.loads(sync_res.stdout)["ok"])

            sync_alias = subprocess.run(
                [sys.executable, str(LINEAR_SYNC), "sync", str(handoff_dir), "--mode", "observe", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sync_alias.returncode, 0, sync_alias.stderr)
            self.assertTrue(json.loads(sync_alias.stdout)["ok"])

            # Counter-Case 1: invalid subcommand/flag exits 2 with <=3 lines pointing to ledger.md
            bad_sub = subprocess.run(
                [sys.executable, str(LINEAR_SYNC), "nonexistent-cmd", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_sub.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", bad_sub.stderr)
            self.assertLessEqual(len(bad_sub.stderr.strip().splitlines()), 3)

            # Counter-Case 2: `LinearSyncError` (missing handoff directory) exits 2 with Reference: skills/team-lead/references/ledger.md
            bad_dir = subprocess.run(
                [
                    sys.executable,
                    str(LINEAR_SYNC),
                    "ensure-goal",
                    "--handoff-dir",
                    str(Path(tmp) / "missing-handoff-dir"),
                    "--mode",
                    "observe",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_dir.returncode, 2)
            self.assertIn("Reference: skills/team-lead/references/ledger.md", bad_dir.stderr)
            self.assertLessEqual(len(bad_dir.stderr.strip().splitlines()), 3)

    def test_goal_queue_actor_and_head_aliases_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff_dir = Path(tmp) / "handoff" / "actor-head-goal"
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "enqueue",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "T1",
                    "--title",
                    "First",
                    "--paths",
                    "skills/team-lead/scripts/goal-queue.py",
                    "--actor",
                    "demo-writer-r1",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "enqueue",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "T2",
                    "--title",
                    "Second",
                    "--paths",
                    "scripts/test_team_lead_retro.py",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            # Positive: `next-wave --actor <agent>` + `set-status --head <sha>` and `--pr-head <sha>` (including pre-subcommand)
            wave = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "next-wave",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--mark-in-progress",
                    "--actor",
                    "wave-actor-r1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(wave.returncode, 0, wave.stderr)

            head_done = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "set-status",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "T1",
                    "--status",
                    "done",
                    "--actor",
                    "wave-actor-r1",
                    "--head",
                    "deadbeef1234",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(head_done.returncode, 0, head_done.stderr)

            pr_head_done = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "--pr-head",
                    "cafebabe5678",
                    "--actor",
                    "wave-actor-r1",
                    "set-status",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "T2",
                    "--status",
                    "done",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(pr_head_done.returncode, 0, pr_head_done.stderr)

            queue_state = json.loads((handoff_dir / "queue.json").read_text(encoding="utf-8"))
            by_id = {item["task_id"]: item for item in queue_state["tasks"]}
            self.assertEqual(by_id["T1"]["pr"], "deadbeef1234")
            self.assertEqual(by_id["T1"]["owner"], "wave-actor-r1")
            self.assertEqual(by_id["T2"]["pr"], "cafebabe5678")

            # Counter-Case: invalid status value still fails with exit=2 and ledger.md pointer
            bad_status = subprocess.run(
                [
                    sys.executable,
                    str(GOAL_QUEUE),
                    "set-status",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "T1",
                    "--status",
                    "not-a-valid-status",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_status.returncode, 2)
            self.assertIn("skills/team-lead/references/ledger.md", bad_status.stderr)

    def test_feedback_guard_and_git_gc_positive_and_counter_case_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wt = tmp_path / "wt"
            wt.mkdir()
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(wt)], env=clean_env, check=True)
            subprocess.run(["git", "-C", str(wt), "config", "user.name", "Bot"], env=clean_env, check=True)
            subprocess.run(["git", "-C", str(wt), "config", "user.email", "bot@example.com"], env=clean_env, check=True)
            (wt / "skills").mkdir()
            (wt / "skills" / "demo.py").write_text("X = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "add", "-A"], env=clean_env, check=True)
            subprocess.run(["git", "-C", str(wt), "commit", "-qm", "init"], env=clean_env, check=True)
            subprocess.run(["git", "-C", str(wt), "branch", "-M", "main"], env=clean_env, check=True)
            subprocess.run(["git", "-C", str(wt), "checkout", "-qb", "feat/demo"], env=clean_env, check=True)
            (wt / "skills" / "demo.py").write_text("X = 2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "commit", "-qam", "feat"], env=clean_env, check=True)
            sha = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                env=clean_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            brief = tmp_path / "brief.md"
            brief.write_text("# Brief\n- **Owned Paths**:\n  - `skills/**`\n", encoding="utf-8")
            receipt = tmp_path / "r10.md"
            receipt.write_text(
                f"# R10\n- Commit SHA: {sha}\n- Role / Model: writer / gpt-5.6-luna\n- V5 Verdict: PASS\n",
                encoding="utf-8",
            )

            # Positive 1: `feedback_guard.py pre-review-check` with `--cwd`, `--brief-file`, `--receipt-file`, `--head-ref HEAD`, `--json`
            fg_ok = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--cwd",
                    str(wt),
                    "--brief-file",
                    str(brief),
                    "--receipt-file",
                    str(receipt),
                    "--head-ref",
                    "HEAD",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=clean_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(fg_ok.returncode, 0, fg_ok.stderr + fg_ok.stdout)
            fg_payload = json.loads(fg_ok.stdout)
            self.assertTrue(fg_payload["ok"])
            self.assertEqual(fg_payload["head_ref"], "HEAD")

            # Counter-Case 1: `feedback_guard.py` invalid flag exits 2 with <=3 lines pointing to eval-cases.json & playbook.md
            fg_bad = subprocess.run(
                [sys.executable, str(FEEDBACK_GUARD), "pre-review-check", "--unknown-flag"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(fg_bad.returncode, 2)
            self.assertIn("skills/team-lead/references/eval-cases.json", fg_bad.stderr)
            self.assertIn("skills/team-lead/references/playbook.md", fg_bad.stderr)
            self.assertLessEqual(len(fg_bad.stderr.strip().splitlines()), 3)

            # Positive 2: `git-gc.py task-clean --dry-run --json` accepts `--json`
            gc_ok = subprocess.run(
                [
                    sys.executable,
                    str(GIT_GC),
                    "task-clean",
                    "--repo",
                    str(wt),
                    "--branch",
                    "feat/demo",
                    "--dry-run",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=clean_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(gc_ok.returncode, 0, gc_ok.stderr)
            self.assertTrue(json.loads(gc_ok.stdout)["ok"])

            # Counter-Case 2: `git-gc.py` invalid flag exits 2 with <=3 lines pointing to playbook.md
            gc_bad = subprocess.run(
                [sys.executable, str(GIT_GC), "task-clean", "--invalid-gc-flag"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(gc_bad.returncode, 2)
            self.assertIn("skills/team-lead/references/playbook.md", gc_bad.stderr)
            self.assertLessEqual(len(gc_bad.stderr.strip().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
