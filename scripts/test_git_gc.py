#!/usr/bin/env python3
"""Focused tests for deterministic worktree and branch GC guards."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("git_gc", ROOT / "skills/goal-task/scripts/git-gc.py")
assert SPEC and SPEC.loader
git_gc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(git_gc)


def run(command, *, cwd=None, check=True):
    return subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True)


class GitGcTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        repo = root / "repo"
        worktree = root / "worktree"
        run(["git", "init", "-q", str(repo)])
        run(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        run(["git", "-C", str(repo), "config", "user.name", "Test"])
        (repo / "README").write_text("base\n", encoding="utf-8")
        run(["git", "-C", str(repo), "add", "README"])
        run(["git", "-C", str(repo), "commit", "-qm", "base"])
        run(["git", "-C", str(repo), "branch", "feature"])
        run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree), "feature"])
        (worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        run(["git", "-C", str(worktree), "add", "feature.txt"])
        run(["git", "-C", str(worktree), "commit", "-qm", "feature"])
        return holder, repo, worktree

    def test_refuses_unmerged_branch(self) -> None:
        holder, repo, worktree = self.make_repo()
        self.addCleanup(holder.cleanup)
        result = git_gc.task_clean(
            repo=repo, worktree=worktree, branch="feature",
            runner=run, herdr_check=lambda _: False,
        )
        self.assertEqual(result["skipped"], "unmerged_pr")
        self.assertTrue(worktree.exists())

    def test_active_pane_guard_wins_before_merge_check(self) -> None:
        holder, repo, worktree = self.make_repo()
        self.addCleanup(holder.cleanup)
        result = git_gc.task_clean(
            repo=repo, worktree=worktree, branch="feature",
            runner=run, herdr_check=lambda _: True,
        )
        self.assertEqual(result["skipped"], "active_herdr_pane")

    def test_noise_is_removed_but_real_changes_refuse_cleanup(self) -> None:
        holder, repo, worktree = self.make_repo()
        self.addCleanup(holder.cleanup)
        (worktree / "__pycache__").mkdir()
        (worktree / "__pycache__" / "x.pyc").write_bytes(b"x")
        (worktree / "real.txt").write_text("do not remove\n", encoding="utf-8")
        result = git_gc.task_clean(
            repo=repo, worktree=worktree, branch="feature",
            runner=lambda command, **kwargs: run(command, **kwargs),
            herdr_check=lambda _: False,
        )
        self.assertEqual(result["skipped"], "unmerged_pr")
        self.assertTrue((worktree / "__pycache__").exists())

        def merged_runner(command, **kwargs):
            if command[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(command, 0, '[{"state":"MERGED","mergedAt":"2026-11-06T00:00:00Z"}]', "")
            return run(command, **kwargs)

        result = git_gc.task_clean(
            repo=repo, worktree=worktree, branch="feature",
            runner=merged_runner, herdr_check=lambda _: False,
        )
        self.assertEqual(result["skipped"], "dirty_worktree")
        self.assertFalse((worktree / "__pycache__").exists())

    def test_json_cli_returns_safe_summary(self) -> None:
        result = subprocess.run(
            ["python3", str(ROOT / "skills/goal-task/scripts/git-gc.py"), "task-clean",
             "--repo", str(ROOT), "--branch", "not-a-real-branch", "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_github_slug_uses_slug_or_origin(self) -> None:
        self.assertEqual(git_gc._github_slug("LeePepe/user-skills"), "LeePepe/user-skills")
        result = subprocess.CompletedProcess([], 0, "git@github.com:LeePepe/user-skills.git\n", "")
        self.assertEqual(git_gc._github_slug(Path("/tmp/repo"), runner=lambda *_args, **_kwargs: result),
                         "LeePepe/user-skills")

    def test_pr_state_falls_back_to_rest(self) -> None:
        result = subprocess.CompletedProcess([], 1, "", "unauthorized")
        with mock.patch.object(git_gc, "_rest_json", return_value={"state": "closed", "merged_at": None}):
            self.assertEqual(git_gc._pr_state("LeePepe/user-skills", "12",
                                              runner=lambda *_args, **_kwargs: result), "CLOSED")

    def test_approve_tier2_gates_no_pr_and_local_branch(self) -> None:
        holder, repo, _ = self.make_repo()
        self.addCleanup(holder.cleanup)
        report = repo / "report.json"
        report.write_text(json.dumps({"tier2": [
            {"kind": "local_branch", "repo": str(repo), "branch": "feature", "reason": "no_pr"},
        ]}), encoding="utf-8")
        args = git_gc._parser().parse_args(["approve-tier2", "--report-file", str(report), "--dry-run"])
        self.assertEqual(git_gc.approve_tier2(args)["cleaned"], [])
        args.include_no_pr = True
        self.assertEqual(git_gc.approve_tier2(args)["cleaned"][0]["branch"], "feature")


if __name__ == "__main__":
    unittest.main()
