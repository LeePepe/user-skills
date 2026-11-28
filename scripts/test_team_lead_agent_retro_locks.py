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
        # Git-ignored fixtures must not appear in concurrent repository leak scans.
        self.scratch = REPO_ROOT / ".test_team_lead_agent_retro_locks.pyc"
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
        for role in ("steering", "group/steering", "magpie/group/steering"):
            receipt.write_text(f"Commit SHA: abcdef1234567890\nRole / Model: {role} / replay-model\nV5 Verdict: PASS\n", encoding="utf-8")
            self.assertTrue(module.receipt_exists(goal, "retro-goal-goal-tl-r1"), role)

    def test_failed_receipt_status_is_rejected_even_alongside_pass(self) -> None:
        module = load_module(FEEDBACK_GUARD, "feedback_failed_receipt_replay")
        base = "Commit SHA: abcdef1234567890\nRole / Model: writer / replay-model\nVerification: PASS\n"
        for field in ("Verification: failed", "Verification: FAIL", "status: failed", "V5 Verdict: FAIL", "Verdict: failure"):
            with self.subTest(field=field):
                receipt = self.scratch / "receipt.md"
                receipt.write_text(base + field + "\n", encoding="utf-8")
                self.assertTrue(module.validate_r10_receipt(base + field))
                self.assertFalse(module._receipt_summary(receipt)["valid"])
        for status, valid in (("PASS", True), ("done", True), ("failed", False), ("FAIL", False)):
            receipt = self.scratch / "receipt.json"
            receipt.write_text(json.dumps({"commit_sha": "abcdef1234567890", "role": "writer", "model": "replay",
                                           "status": status}), encoding="utf-8")
            self.assertEqual(module._receipt_summary(receipt)["valid"], valid)
        self.assertEqual(module.validate_r10_receipt(base), [])

    def test_verification_claims_require_real_executables_and_exact_arguments(self) -> None:
        module = load_module(FEEDBACK_GUARD, "feedback_command_token_replay")
        claims = ("pytest -q tests/test_demo.py", "python3 -m unittest scripts.test_demo", "unittest demo",
                  "swift test", "swift build", "xcodebuild -scheme Demo test", "swiftlint lint",
                  "scripts/verify", "./scripts/verify", "npm test", "cargo test", "go test ./...")
        receipt = "Verification: PASS\n" + "".join(f"- `{claim}` — PASS (exit=0)\n" for claim in claims)
        self.assertEqual(module._receipt_verification_commands(receipt), list(claims))
        self.assertEqual(module._receipt_verification_commands("Verification: `swift test` — PASS (exit=0)"), ["swift test"])
        for claim in claims:
            with self.subTest(claim=claim):
                for executed in (claim, f"FLAG=1 env -u UNUSED time -p timeout 30 {claim}",
                                 f"true && {claim}", f"echo prelude; {claim}", f"{claim} && true"):
                    self.assertTrue(module._command_was_successful(claim, [executed]), executed)
                for executed in (f'echo "{claim}"', f'printf "{claim}"', f"grep '{claim}' file",
                                 f"rg '{claim}' file", f'cat "{claim}"', f'true && echo "{claim}"',
                                 f'echo "&&" "{claim}"', f"true # && {claim}", f"{claim} || true",
                                 f"true || {claim}", f"{claim}; true", f"{claim} | cat", f"echo {claim}"):
                    self.assertFalse(module._command_was_successful(claim, [executed]), executed)
        self.assertFalse(module._command_was_successful("pytest -q tests/demo", ["pytest -q tests/demo_extra"]))
        self.assertFalse(module._command_was_successful("pytest -k 'x  y'", ["pytest -k 'x y'"]))
        self.assertFalse(module._command_was_successful("pytest foo", ["pytest foo#bar"]))
        self.assertFalse(module._command_was_successful("pytest", ["cat <<EOF\npytest\nEOF"]))
        self.assertTrue(module._command_was_successful("pytest", ['echo "pytest && swift test"; pytest']))
        events = self.scratch / "executions.jsonl"
        for code, expected in ((None, []), (1, []), (0, ["pytest"])):
            result = {} if code is None else {"exitCode": code}
            events.write_text(json.dumps({"command": "pytest", "success": True, "result": result}) + "\n", encoding="utf-8")
            self.assertEqual(module._successful_event_commands(events), expected)

    def test_receipt_identity_and_round_cannot_be_replaced_by_role_substrings(self) -> None:
        module = load_module(GOAL_SUPERVISOR, "goal_receipt_identity_replay")
        goal, _ = self.goal_fixture("completed output")
        receipts = goal / "receipts"
        receipts.mkdir()
        cases = (
            ("retro-goal-t2-writer-r1", "T1-writer-r1", False),
            ("retro-goal-t2-writer-r1", "retro-goal-t1-writer-r1", False),
            ("retro-goal-writer-r2", "writer-old", False),
            ("retro-goal-writer-r2", "T1-writer-r1", False),
            ("retro-goal-writer-r1", "other-writer-r1", False),
            ("retro-goal-writer-r2", "retro-goal-writer-r1", False),
            ("retro-goal-t2-writer-r2", "T2-writer-r1", False),
            ("retro-goal-t2-writer-r2", "T2-writer-r10", False),
            ("retro-goal-t2-writer-r2", "T2-reviewer-r2", False),
            ("retro-goal-t2-writer-r1", "writer", False),
            ("retro-goal-writer-r1", "T1-writer-r1", False),
            ("retro-goal-t2-writer-r1", "T2", True),
            ("retro-goal-t2-writer-r1", "T2-r10", True),
            ("retro-goal-t2-writer-r2", "T2-writer-r2", True),
            ("retro-goal-tk2a-writer-r2", "TK2A-writer-r2", True),
            ("retro-goal-t2-writer", "T2-writer", True),
            ("retro-goal-writer-r2", "writer_r2", True),
            ("retro-goal-writer-r2", "writer", True),
            ("retro-goal-writer-r2", "retro-goal-writer-r2-r10", True),
            ("retro-goal-writer-r2", "r10-retro-goal-writer-r2", True),
            ("retro-goal-t2-reviewer-r2", "T2-reviewer-r1", False),
            ("retro-goal-t2-reviewer-r2", "T2-reviewer-r2", True),
        )
        for agent, stem, expected in cases:
            with self.subTest(agent=agent, receipt=stem):
                path = receipts / f"{stem}.md"
                role = "reviewer" if "reviewer" in agent else "writer"
                path.write_text(f"Commit SHA: abcdef1234567890\nRole / Model: {role} / replay\nV5 Verdict: PASS\n", encoding="utf-8")
                self.assertEqual(module.receipt_exists(goal, agent), expected)
                path.unlink()

    def test_stuck_loop_negations_are_not_positive_status(self) -> None:
        module = load_module(GOAL_SUPERVISOR, "goal_stuck_negation_replay")
        negative = ("not stuck", "0 stuck", "unstuck", "no stuck", "never stuck", "without stuck", "zero stuck",
                    "not looping", "no looping", "0 looping", "not repeating identical failure",
                    "non-blocking / not repeating identical failure", "leases: 0 blocked · 0 zombie · 0 stuck",
                    "isn't stuck", "aren't looping", "neither stuck nor looping")
        for text in negative:
            with self.subTest(text=text):
                self.assertIsNone(module.evaluate_stuck_loop("writer", "writer", text, jev_override={}))
                self.assertIsNone(module.evaluate_stuck_loop("writer", "writer", "\n".join([text] * 3), jev_override={}))
        for text in ("stuck", "looping", "repeating identical failure", "not stuck, but looping", "error\nerror\nerror"):
            self.assertEqual(module.evaluate_stuck_loop("writer", "writer", text, jev_override={})["type"], "STUCK_LOOP")

    def test_hygiene_longest_goal_prefix_boundaries_and_ambiguous_panes(self) -> None:
        module = load_module(SCRIPTS / "local_hygiene_patrol.py", "hygiene_prefix_replay")
        ledgers = {"app": {"goal_done": True}, "app-ios": {"goal_done": False}}
        for suffix in ("goal-tl-r1", "team-lead-r2", "writer-r1", "reviewer-r1", "rev-r1", "exp-r1", "explorer-r1",
                       "prmon-r1", "pr-monitor-r1", "des-r1", "designer-r1", "tk2a-writer-r1", "t2-reviewer-r2"):
            with self.subTest(suffix=suffix):
                name = f"app-ios-{suffix}"
                self.assertEqual(module._match_agent_to_goal({"name": name}, ledgers), "app-ios")
                self.assertEqual(module._match_worktree_to_goal(self.scratch / name, ledgers), "app-ios")
                self.assertFalse(module._is_worktree_held(self.scratch / name, {"app"}))
                self.assertTrue(module._is_worktree_held(self.scratch / name, {"app-ios"}))
        self.assertEqual(module._match_worktree_to_goal(self.scratch / "app-ios", ledgers), "app-ios")
        self.assertFalse(module._is_worktree_held(self.scratch / "app-ios", {"app"}))
        for field in ("cwd", "terminal_title_stripped"):
            for path in (self.scratch / "worktrees/app-ios", self.scratch / "handoff/app-ios/Sources"):
                self.assertEqual(module._match_agent_to_goal({"name": "unrelated-writer-r1", field: str(path)}, ledgers), "app-ios")
        aliases = {"good-designer": {"tl_stem": "good-des"}, "good-designs": {"tl_stem": "good-des"}}
        self.assertEqual(module._match_agent_to_goal({"name": "good-des-writer-r1"}, aliases), "__ambiguous__")
        self.assertEqual(module._match_agent_to_goal({"name": "good-des-goal-tl-r1"}, aliases), "__ambiguous__")
        self.assertEqual(module._match_worktree_to_goal(self.scratch / "good-des", aliases), "__ambiguous__")
        handoff = self.scratch / "handoff"
        for goal, done in (("app", True), ("app-ios", False)):
            directory = handoff / goal
            directory.mkdir(parents=True)
            (directory / "ledger.md").write_text("EVT=GOAL_DONE\n" if done else "active\n", encoding="utf-8")
        def scan(names):
            return module.scan_herdr_panes(handoff_root=handoff, herdr_json={"agents": [
                {"name": name, "pane_id": f"wx:p{index}", "status": "done"} for index, name in enumerate(names)]})
        report = scan(["app-ios-writer-r1", "app-writer-r1", "orphan-writer-r1"])
        self.assertEqual([item["name"] for item in report["reclaimable_panes"]], ["app-writer-r1", "orphan-writer-r1"])
        self.assertEqual([item["name"] for item in report["active_m1_panes"]], ["app-ios-writer-r1"])
        shutil.rmtree(handoff / "app-ios")
        report = scan(["app-ios-writer-r1", "app-ios-writer-r1"])
        self.assertEqual(report["reclaimable_panes"], [])
        self.assertEqual(len(report["active_m1_panes"]), 2)
        self.assertEqual(module._match_worktree_to_goal(self.scratch / "app-ios", {"app": {}}), "__ambiguous__")
        dev = self.scratch / "dev"
        repo, worktree = dev / "project", dev / "worktrees/app-ios"
        (repo / ".git").mkdir(parents=True)
        worktree.mkdir(parents=True)
        git_gc = mock.Mock(NOISE_NAMES=(), _worktrees=mock.Mock(return_value=[{"worktree": str(worktree), "branch": "feat/app-ios"}]))
        with mock.patch.object(module, "_load_git_gc", return_value=git_gc):
            report = module.scan_worktrees(dev_root=dev, handoff_root=handoff, repos=("project",), herdr_json={"agents": []},
                                          runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
                                          merge_checker=lambda repo, branch: True, use_cache=False)
        self.assertEqual(report["clean_merged_worktrees"], [])
        self.assertIn("ambiguous_goal_identity", report["preserved_worktrees"][0]["preserve_reasons"])
        runner = mock.Mock(side_effect=AssertionError("ambiguous identity must not reach merge probes"))
        self.assertFalse(module._is_positively_merged(repo, worktree, "feat/app-ios", ledgers={"app": {}}, git_gc=git_gc, runner=runner))
        runner.assert_not_called()

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
