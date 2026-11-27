#!/usr/bin/env python3
"""Replay tests for the four team-lead agent-retro physical locks."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "team-lead" / "scripts"
FEEDBACK_GUARD = SCRIPTS / "feedback_guard.py"
CI_MONITOR = SCRIPTS / "ci-monitor.py"
GOAL_SUPERVISOR = SCRIPTS / "goal-supervisor.py"
HERDR_DISPATCH = SCRIPTS / "herdr-dispatch.sh"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TeamLeadAgentRetroLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = REPO_ROOT / ".test_team_lead_agent_retro_locks"
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.scratch.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def fixture_repo(self, count: int, *, role: str = "writer") -> tuple[Path, Path, Path, str]:
        root = self.scratch / f"fixture-{count}-{role.replace('/', '-') }"
        root.mkdir(parents=True)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "test@example.invalid")
        self.git(root, "config", "user.name", "Replay Test")
        (root / "skills/team-lead").mkdir(parents=True)
        (root / "skills/team-lead/base.txt").write_text("base\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "fixture baseline")
        for index in range(count):
            (root / f"skills/team-lead/changed-{index}.txt").write_text(f"changed {index}\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "fixture change")
        sha = self.git(root, "rev-parse", "HEAD")
        brief = root / "brief.md"
        brief.write_text("# Brief\n\n## Owned Paths\n- `skills/team-lead/**`\n", encoding="utf-8")
        receipt = root / "receipt.md"
        receipt.write_text(
            f"Commit SHA: {sha}\nRole / Model: {role} / replay-model\nVerification: fixture replay PASS\n",
            encoding="utf-8",
        )
        return root, brief, receipt, sha

    def run_guard(self, root: Path, brief: Path, receipt: Path, *extra: str) -> tuple[dict, subprocess.CompletedProcess[str]]:
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(FEEDBACK_GUARD), "pre-review-check",
             "--worktree", str(root), "--brief", str(brief), "--receipt", str(receipt),
             "--base-ref", "HEAD~1", *extra],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        return json.loads(result.stdout), result

    def test_feedback_guard_budget_and_trace_positive_and_counter_cases(self) -> None:
        for role in ("m1_tl", "magpie/group/steering", "`group/steering`"):
            root, brief, receipt, _ = self.fixture_repo(4, role=role)
            actor_role = "m1_tl" if role == "m1_tl" else None
            report, result = self.run_guard(root, brief, receipt, *(('--actor-role', actor_role) if actor_role else ()))
            self.assertEqual(result.returncode, 1)
            self.assertIn("m1_inline_file_budget_exceeded", {item["code"] for item in report["violations"]})

        root, brief, receipt, _ = self.fixture_repo(3, role="steering")
        report, result = self.run_guard(root, brief, receipt)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("m1_inline_file_budget_exceeded", {item["code"] for item in report["violations"]})

        root, brief, receipt, _ = self.fixture_repo(5, role="writer")
        report, result = self.run_guard(root, brief, receipt)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("m1_inline_file_budget_exceeded", {item["code"] for item in report["violations"]})

        root, brief, receipt, _ = self.fixture_repo(1, role="writer")
        events = root / "events.jsonl"
        receipt.write_text(
            receipt.read_text(encoding="utf-8")
            + "- `python3 -I -B scripts/test_demo.py` — PASS (exit=0)\n"
            + "- ./scripts/verify — PASS (exit=0)\n",
            encoding="utf-8",
        )
        events.write_text(
            json.dumps({"data": {"type": "tool.execution_start", "toolCallId": "tool-call", "input": {"command": "python3 -I -B scripts/test_demo.py && ./scripts/verify"}}})
            + "\n"
            + json.dumps({"data": {"type": "tool.execution_complete", "toolCallId": "tool-call", "success": True, "result": {"exitCode": 1, "content": "<shellId: 1 completed with exit code 1>"}}})
            + "\n",
            encoding="utf-8",
        )
        report, result = self.run_guard(root, brief, receipt, "--events-jsonl", str(events))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ungrounded_receipt_verification", {item["code"] for item in report["violations"]})

        events.write_text(
            json.dumps({"data": {"type": "tool.execution_start", "toolCallId": "tool-call", "input": {"command": "python3 -I -B scripts/test_demo.py && ./scripts/verify"}}})
            + "\n"
            + json.dumps({"data": {"type": "tool.execution_complete", "toolCallId": "tool-call", "success": True, "result": {"exitCode": 0, "content": "<shellId: 1 completed with exit code 0>"}}})
            + "\n",
            encoding="utf-8",
        )
        report, result = self.run_guard(root, brief, receipt, "--events-jsonl", str(events))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("ungrounded_receipt_verification", {item["code"] for item in report["violations"]})

    def test_ci_monitor_cancelled_and_failure_replay(self) -> None:
        module = load_module(CI_MONITOR, "ci_monitor_replay")
        args = lambda receipt, exit_on_green: type("Args", (), {
            "receipt": str(receipt), "repo": "example/repo", "pr": 7, "head_sha": "abc",
            "interval": 0, "timeout": 10, "notify_agent": None, "exit_on_green": exit_on_green,
            "enable_auto": False, "auto_merge": None, "merge_method": "squash", "squash": False,
            "delete_branch": False, "no_cleanup_worktree": True, "worktree": None, "branch": None,
        })()

        def cancelled_then_success(command):
            if command[:3] == ["gh", "pr", "view"]:
                return json.dumps({"state": "OPEN", "headRefOid": "abc", "statusCheckRollup": [
                    {"name": "build", "conclusion": "CANCELLED"},
                    {"name": "build", "conclusion": "SUCCESS"},
                ], "url": "https://example.invalid/pr/7"})
            raise AssertionError(command)

        receipt = self.scratch / "green.md"
        result, code = module.monitor_pr(args(receipt, True), run=cancelled_then_success, sleep=lambda _: None, now=lambda: 0)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "PR_CHECKS_GREEN")

        def genuine_failure(command):
            if command[:3] == ["gh", "pr", "view"]:
                return json.dumps({"state": "OPEN", "headRefOid": "abc", "statusCheckRollup": [{"name": "build", "conclusion": "FAILURE"}], "url": "url"})
            raise AssertionError(command)

        result, code = module.monitor_pr(args(self.scratch / "failure.md", False), run=genuine_failure, sleep=lambda _: None, now=lambda: 0)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "PR_CHECK_FAILED")

        def required_cancelled(command):
            if command[:3] == ["gh", "pr", "view"]:
                return json.dumps({"state": "OPEN", "headRefOid": "abc", "statusCheckRollup": [{"name": "required", "conclusion": "CANCELLED", "isRequired": True}], "url": "url"})
            raise AssertionError(command)

        result, code = module.monitor_pr(args(self.scratch / "cancelled.md", True), run=required_cancelled, sleep=lambda _: None, now=lambda: 0)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "PR_CHECK_CANCELLED")
        self.assertNotEqual(result["status"], "PR_CHECK_FAILED")

        workflow_args = type("WorkflowArgs", (), {
            "receipt": str(self.scratch / "workflow.md"), "run_id": "run", "trigger": False,
            "workflow": "ci.yml", "repo": "example/repo", "ref": "main", "force": False,
            "interval": 0, "timeout": 10, "marker_tag": "last", "notify_agent": None,
        })()
        def cancelled_workflow(command):
            if command[:3] == ["gh", "run", "view"]:
                return json.dumps({"status": "completed", "conclusion": "cancelled", "jobs": [], "headSha": "abc", "url": "url"})
            if command[:2] == ["git", "ls-remote"]:
                return ""
            raise AssertionError(command)
        result, code = module.monitor_workflow(workflow_args, run=cancelled_workflow, sleep=lambda _: None, now=lambda: 0)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "WORKFLOW_CANCELLED")

    def goal_fixture(self, visible: str) -> tuple[Path, Path]:
        root = self.scratch / "handoff"
        goal = root / "retro-goal"
        shutil.rmtree(root, ignore_errors=True)
        goal.mkdir(parents=True)
        (goal / "ledger.md").write_text(
            "Goal ID: retro-goal TL: retro-goal-goal-tl-r1\n2026-11-25T00:00:00Z EVT=START\n",
            encoding="utf-8",
        )
        agents = root / "agents.json"
        agents.write_text(json.dumps({"agents": [{
            "name": "retro-goal-goal-tl-r1", "status": "idle", "cwd": str(goal), "visible": visible,
        }]}), encoding="utf-8")
        return goal, agents

    def test_goal_supervisor_visible_activity_and_quiescence(self) -> None:
        module = load_module(GOAL_SUPERVISOR, "goal_supervisor_replay")
        for marker in ("Waiting for reviewer", "2 background /tasks", "Thinking", "ctrl+c to interrupt"):
            goal, agents = self.goal_fixture(marker)
            args = type("WatchArgs", (), {
                "handoff_dir": str(goal), "goal": None, "handoff_root": str(goal.parent),
                "interval": 1, "max_wait": 4, "herdr_json": str(agents), "write_alerts": False, "mode": None,
            })()
            sleeps = 0
            def settle(_: float) -> None:
                nonlocal sleeps
                sleeps += 1
                if sleeps >= 2:
                    payload = json.loads(agents.read_text(encoding="utf-8"))
                    payload["agents"][0]["visible"] = "completed output"
                    agents.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(module.time, "sleep", side_effect=settle):
                result = module.watch(args)
            self.assertEqual(result["reason"], "TL_IDLE_OR_DONE")

        goal, agents = self.goal_fixture("completed output")
        args = type("WatchArgs", (), {
            "handoff_dir": str(goal), "goal": None, "handoff_root": str(goal.parent),
            "interval": 1, "max_wait": 4, "herdr_json": str(agents), "write_alerts": False, "mode": None,
        })()
        with mock.patch.object(module.time, "sleep", return_value=None):
            result = module.watch(args)
        self.assertEqual(result["reason"], "TL_IDLE_OR_DONE")

    def test_goal_supervisor_accepts_only_grounded_r10_receipts(self) -> None:
        module = load_module(GOAL_SUPERVISOR, "goal_supervisor_receipt_replay")
        goal, _ = self.goal_fixture("completed output")
        receipts = goal / "receipts"
        receipts.mkdir()
        receipt = receipts / "team_lead-r10.md"
        receipt.write_text(
            "# R10\n- Commit SHA: n/a\n- Role: team_lead\n- Model: replay-model\n- V5 Verdict: PASS\n",
            encoding="utf-8",
        )
        self.assertFalse(module.receipt_exists(goal, "retro-goal-goal-tl-r1"))

        receipt.write_text(
            "# R10\n- Commit SHA: abcdef1234567890abcdef1234567890abcdef12\n"
            "- Role / Model: team_lead / replay-model\n- V5 Verdict: PASS (replay)\n",
            encoding="utf-8",
        )
        self.assertTrue(module.receipt_exists(goal, "retro-goal-goal-tl-r1"))

    def fake_herdr(self, root: Path, capture: Path) -> Path:
        fake = root / "herdr"
        fake.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane": {"pane_id": "p2"}}}))
elif args[:2] == ["agent", "start"]:
    with open(os.environ["CAPTURE"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
    print(json.dumps({"ok": True}))
elif args[:2] == ["agent", "get"]:
    print(json.dumps({"interactive_ready": True, "agent_status": "working"}))
else:
    print(json.dumps({"ok": True}))
""", encoding="utf-8")
        fake.chmod(0o755)
        return fake

    def test_herdr_add_dir_pair_and_dangling_counter_cases(self) -> None:
        fake_root = self.scratch / "herdr"
        fake_root.mkdir()
        capture = fake_root / "capture.jsonl"
        self.fake_herdr(fake_root, capture)
        valid = fake_root / "valid"
        valid.mkdir()
        env = {**os.environ, "PATH": f"{fake_root}:{os.environ['PATH']}", "CAPTURE": str(capture),
               "HERDR_ENV": "1", "HERDR_PANE_ID": "p1", "HERDR_MOCK_MAGPIE_ONLINE": "1"}
        result = subprocess.run([str(HERDR_DISPATCH), "--name", "lock-writer", "--kind", "copilot", "--cwd", str(fake_root), "--prompt", "run", "--add-dir", str(valid), "--", "--no-ask-user"], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        start_args = json.loads(capture.read_text(encoding="utf-8").splitlines()[0])
        index = start_args.index("--add-dir")
        self.assertEqual(start_args[index:index + 2], ["--add-dir", str(valid)])
        self.assertNotIn(str(valid), start_args[:index])

        missing = fake_root / "missing"
        capture.unlink()
        result = subprocess.run([str(HERDR_DISPATCH), "--name", "lock-writer", "--kind", "copilot", "--cwd", str(fake_root), "--prompt", "run", "--add-dir", str(missing), "--", "--no-ask-user"], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        start_args = json.loads(capture.read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn(str(missing), start_args)

        for extra in (["--add-dir"], ["--add-dir", "--no-ask-user"]):
            result = subprocess.run([str(HERDR_DISPATCH), "--name", "lock-writer", "--kind", "copilot", "--cwd", str(fake_root), "--prompt", "run", "--", *extra], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("skills/team-lead/references/bots-and-agents.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
