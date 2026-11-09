#!/usr/bin/env python3
"""Tests for discover_review_context.py using temporary git repositories."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("discover_review_context.py")


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check)


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="code-review-test-"))
        run("git", "init", "-b", "main", cwd=root)
        run("git", "config", "user.email", "test@example.invalid", cwd=root)
        run("git", "config", "user.name", "Test", cwd=root)
        return root

    def commit(self, root: Path, message: str) -> None:
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-m", message, cwd=root)

    def discover(self, root: Path, *extra: str, check: bool = True):
        result = run("python3", str(SCRIPT), "--repo", str(root), *extra, cwd=root, check=check)
        return json.loads(result.stdout) if check else result

    def test_layered_spec_kit_worktree_surface(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\n")
        write(root, ".specify/memory/constitution.md", "# Constitution\n")
        write(root, "specs/001-feature/spec.md", "# Spec\n")
        write(root, "specs/001-feature/plan.md", "# Plan\n")
        write(root, "specs/001-feature/tasks.md", "# Tasks\n")
        write(root, "Packages/Core/tech-context.md", "---\nlayer: Core\ndepends_on: []\nred_lines:\n  - no UI imports\ntest: swift test --package-path Packages/Core\nowns: [Sources]\n---\n")
        write(root, "Packages/Core/source.swift", "let base = 1\n")
        write(root, "Packages/UI/tech-context.md", "---\nlayer: UI\ndepends_on: [Core]\nred_lines:\n  - use tokens\ntest: swift test --package-path Packages/UI\nowns: [Sources]\n---\n")
        write(root, "Packages/UI/view.swift", "let baseView = 1\n")
        self.commit(root, "base")
        run("git", "checkout", "-b", "001-feature", cwd=root)
        write(root, "Packages/Core/source.swift", "let committed = 2\n")
        self.commit(root, "feature core")
        write(root, "Packages/UI/view.swift", "let worktree = 2\n")
        write(root, "Packages/Core/new.swift", "let untracked = 3\n")

        result = self.discover(root, "--base", "main")
        self.assertEqual(result["spec_kit"]["active_feature_dir"], "specs/001-feature")
        self.assertEqual(len(result["commits"]), 1)
        self.assertIn(result["fixed_point"], result["diff_command"])
        self.assertEqual(
            {item["path"] for item in result["changed_files"]},
            {"Packages/Core/new.swift", "Packages/Core/source.swift", "Packages/UI/view.swift"},
        )
        mapped = {layer["name"]: layer["changed_files"] for layer in result["layers"]}
        self.assertEqual(mapped["Core"], ["Packages/Core/new.swift", "Packages/Core/source.swift"])
        self.assertEqual(mapped["UI"], ["Packages/UI/view.swift"])
        self.assertEqual(result["unmapped_files"], [])

    def test_unlayered_repository_declares_gaps(self) -> None:
        root = self.make_repo()
        write(root, "app.py", "value = 1\n")
        self.commit(root, "base")
        write(root, "app.py", "value = 2\n")
        result = self.discover(root, "--base", "main")
        self.assertIn("no AGENTS.md/CLAUDE.md routing index", result["coverage_gaps"])
        self.assertIn("no constitution source", result["coverage_gaps"])
        self.assertIn("no layer tech-context frontmatter", result["coverage_gaps"])
        self.assertEqual(result["unmapped_files"], ["app.py"])

    def test_invalid_base_fails_closed(self) -> None:
        root = self.make_repo()
        write(root, "app.py", "value = 1\n")
        self.commit(root, "base")
        result = self.discover(root, "--base", "does-not-exist", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("discovery failed", result.stderr)

    def test_explicit_spec_resolves_ambiguous_worktree(self) -> None:
        root = self.make_repo()
        write(root, "specs/001-first/spec.md", "# First\n")
        write(root, "specs/002-second/spec.md", "# Second\n")
        write(root, "app.py", "value = 1\n")
        self.commit(root, "base")
        write(root, "app.py", "value = 2\n")
        result = self.discover(root, "--base", "main", "--spec", "specs/002-second")
        self.assertEqual(result["spec_kit"]["active_feature_dir"], "specs/002-second")

    def test_flow_map_and_scoped_review_smells_drawer(self) -> None:
        root = self.make_repo()
        flow = json.loads(run("python3", str(SCRIPT), "--flow-map", cwd=root).stdout)
        self.assertTrue(flow["ok"])
        self.assertTrue(flow["anti_explosion_valves"]["all_l1_docs_within_budget"])
        self.assertIn("RS-001", flow["shared_tiers"]["tier_1_json_drawer"]["active_smell_ids"])

        write(root, "Packages/UI/tech-context.md", "---\nlayer: UI\ndepends_on: [Core]\n---\n")
        write(root, "Packages/UI/View.swift", "let v = 1\n")
        self.commit(root, "base")
        write(root, "Packages/UI/View.swift", "let v = 2\n")
        result = self.discover(root, "--base", "main")
        self.assertIn("matched_review_smells", result)
        self.assertLessEqual(len(result["matched_review_smells"]), 3)
        smell_ids = [s["id"] for s in result["matched_review_smells"]]
        self.assertIn("RS-002", smell_ids)
        self.assertNotIn("RS-003", smell_ids)


if __name__ == "__main__":
    unittest.main()
