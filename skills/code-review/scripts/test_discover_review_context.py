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
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")}
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=check)


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="code-review-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
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

    def test_positive_and_counter_case_replay_cli_aliases_and_external_handoff_spec(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\n")
        write(root, "app.py", "value = 1\n")
        self.commit(root, "base")
        write(root, "app.py", "value = 2\n")

        # Positive Case Replay: external handoff spec.md + CLI aliases (--repo-dir, --base-ref, --spec-file, --head-ref)
        handoff_dir = Path(tempfile.mkdtemp(prefix="code-review-handoff-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(handoff_dir, ignore_errors=True))
        ext_spec = handoff_dir / "spec.md"
        ext_spec.write_text("# External Handoff Spec\n", encoding="utf-8")
        (handoff_dir / "design.md").write_text("# External Design\n", encoding="utf-8")

        pos_res = run(
            "python3",
            str(SCRIPT),
            "--repo-dir",
            str(root),
            "--base-ref",
            "main",
            "--head-ref",
            "HEAD",
            "--spec-file",
            str(ext_spec),
            "--json",
            cwd=root,
        )
        pos_data = json.loads(pos_res.stdout)
        self.assertEqual(pos_data["base_ref"], "main")
        self.assertEqual(pos_data["spec_kit"]["active_feature_dir"], str(handoff_dir.resolve()))
        self.assertEqual(pos_data["spec_kit"]["artifacts"]["spec"], str(ext_spec.resolve()))
        self.assertEqual(pos_data["spec_kit"]["artifacts"]["design"], str(
            (handoff_dir / "design.md").resolve()
        ))

        # Positional repo + auto-detected default branch (omitting --repo and --base)
        pos_auto = json.loads(run("python3", str(SCRIPT), str(root), cwd=root).stdout)
        self.assertEqual(pos_auto["base_ref"], "main")
        self.assertEqual(pos_auto["unmapped_files"], ["app.py"])

        # Counter-Case Replay: missing external spec or invalid base fails closed with self-disclosing reference pointers
        bad_spec = run(
            "python3",
            str(SCRIPT),
            "--worktree",
            str(root),
            "--base",
            "main",
            "--spec",
            str(handoff_dir / "nonexistent-spec.md"),
            cwd=root,
            check=False,
        )
        self.assertEqual(bad_spec.returncode, 2)
        self.assertIn("skills/code-review/references/output-contract.md", bad_spec.stderr)
        self.assertIn("skills/code-review/references/reviewer-prompts.md", bad_spec.stderr)

        bad_flag = run("python3", str(SCRIPT), "--definitely-invalid-flag", cwd=root, check=False)
        self.assertEqual(bad_flag.returncode, 2)
        self.assertIn("skills/code-review/references/review-rubric.md", bad_flag.stderr)
        self.assertIn("skills/code-review/references/output-contract.md", bad_flag.stderr)

    def test_json_frontmatter_context_index_leaf_globs_dependencies_and_exclusions(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\n")
        write(root, ".specify/memory/constitution.md", "# Constitution\n")
        write(
            root,
            "CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "index",\n  "routes": [\n    {"patterns": ["Packages/**"], "context": "Packages/CONTEXT.md"},\n    {"patterns": ["pipeline/**"], "context": "pipeline/CONTEXT.md"},\n    {"patterns": ["scripts/**", "AGENTS.md"], "context": "scripts/CONTEXT.md"}\n  ],\n  "exclusions": [\n    {"patterns": ["CONTEXT.md", "docs/**"], "reason": "Root routing & support docs."}\n  ]\n}\n---\n# Root Router\n',
        )
        write(
            root,
            "Packages/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "index",\n  "routes": [\n    {"patterns": ["AppCore/**"], "context": "AppCore/CONTEXT.md"},\n    {"patterns": ["AppUI/**"], "context": "AppUI/CONTEXT.md"}\n  ],\n  "exclusions": [\n    {"patterns": ["CONTEXT.md"], "reason": "Package index."}\n  ]\n}\n---\n',
        )
        write(
            root,
            "Packages/AppCore/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "leaf",\n  "layer": "AppCore",\n  "parent": "Packages/CONTEXT.md",\n  "scope": ["AppCore/**"],\n  "dependencies": [],\n  "dependents": ["AppUI"],\n  "red_lines": ["No UI imports in AppCore."],\n  "gates": [\n    {"id": "spm-test", "kind": "test", "mode": "both", "command": ["swift", "test", "--package-path", "Packages/AppCore"]}\n  ]\n}\n---\n',
        )
        write(
            root,
            "Packages/AppUI/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "leaf",\n  "layer": "AppUI",\n  "parent": "Packages/CONTEXT.md",\n  "scope": ["AppUI/**"],\n  "dependencies": ["AppCore"],\n  "dependents": [],\n  "red_lines": ["Use design tokens."],\n  "gates": [\n    {"id": "spm-test", "kind": "test", "mode": "both", "command": ["swift", "test", "--package-path", "Packages/AppUI"]}\n  ]\n}\n---\n',
        )
        write(
            root,
            "pipeline/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "index",\n  "routes": [\n    {"patterns": ["config.py", "CONTEXT.foundation.md"], "test_paths": ["tests/test_config.py"], "context": "CONTEXT.foundation.md"},\n    {"patterns": ["L5_apps/**"], "test_paths": ["tests/test_l5_*.py"], "context": "L5_apps/CONTEXT.md"}\n  ],\n  "exclusions": [\n    {"patterns": ["CONTEXT.md"], "reason": "Pipeline router."}\n  ]\n}\n---\n',
        )
        write(
            root,
            "pipeline/CONTEXT.foundation.md",
            '---\n{\n  "schema": 1,\n  "kind": "leaf",\n  "layer": "PipelineFoundation",\n  "parent": "pipeline/CONTEXT.md",\n  "scope": ["config.py", "CONTEXT.foundation.md"],\n  "test_paths": ["tests/test_config.py"],\n  "dependencies": [],\n  "dependents": ["PipelineL5"],\n  "red_lines": ["Keep tracked defaults neutral."]\n}\n---\n',
        )
        write(
            root,
            "pipeline/L5_apps/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "leaf",\n  "layer": "PipelineL5",\n  "parent": "pipeline/CONTEXT.md",\n  "scope": ["L5_apps/**"],\n  "test_paths": ["tests/test_l5_*.py"],\n  "dependencies": ["PipelineFoundation"],\n  "dependents": [],\n  "red_lines": ["Do not invent absent upstream values."]\n}\n---\n',
        )
        write(
            root,
            "scripts/CONTEXT.md",
            '---\n{\n  "schema": 1,\n  "kind": "leaf",\n  "layer": "RepoInfra",\n  "parent": "CONTEXT.md",\n  "scope": ["scripts/**", "AGENTS.md"],\n  "dependencies": [],\n  "dependents": [],\n  "red_lines": ["Single verify entrypoint."]\n}\n---\n',
        )
        write(root, "Packages/AppCore/Model.swift", "let x = 1\n")
        write(root, "Packages/AppUI/Screen.swift", "let y = 1\n")
        write(root, "pipeline/config.py", "DEBUG = False\n")
        write(root, "pipeline/L5_apps/render.py", "def render(): return 1\n")
        write(root, "pipeline/tests/test_l5_render.py", "def test_r(): assert True\n")
        write(root, "docs/notes.md", "v1\n")
        self.commit(root, "base")

        run("git", "checkout", "-b", "feat/json-context", cwd=root)
        write(root, "Packages/AppCore/Model.swift", "let x = 2\n")
        write(root, "Packages/AppUI/Screen.swift", "let y = 2\n")
        write(root, "pipeline/config.py", "DEBUG = True\n")
        write(root, "pipeline/tests/test_l5_render.py", "def test_r(): assert 1 == 1\n")
        write(root, "AGENTS.md", "# Updated Index\n")
        write(root, "docs/notes.md", "v2\n")
        self.commit(root, "touch json context layers")

        result = self.discover(root, "--base", "main")
        self.assertNotIn("no layer tech-context frontmatter", result["coverage_gaps"])
        self.assertEqual(result["unmapped_files"], [])
        self.assertEqual(result["context"].get("excluded_support_files"), ["docs/notes.md"])

        layers_by_name = {layer["name"]: layer for layer in result["layers"]}
        self.assertEqual(layers_by_name["AppCore"]["changed_files"], ["Packages/AppCore/Model.swift"])
        self.assertEqual(layers_by_name["AppCore"]["depended_by"], ["AppUI"])
        self.assertEqual(layers_by_name["AppCore"]["test"], "swift test --package-path Packages/AppCore")
        self.assertEqual(layers_by_name["AppUI"]["depends_on"], ["AppCore"])
        self.assertEqual(layers_by_name["AppUI"]["changed_files"], ["Packages/AppUI/Screen.swift"])
        self.assertEqual(layers_by_name["PipelineFoundation"]["changed_files"], ["pipeline/config.py"])
        self.assertEqual(layers_by_name["PipelineL5"]["changed_files"], ["pipeline/tests/test_l5_render.py"])
        self.assertEqual(layers_by_name["RepoInfra"]["changed_files"], ["AGENTS.md"])

    def test_yaml_owns_globs_and_fast_git_ls_files_ignores_excluded_dirs(self) -> None:
        root = self.make_repo()
        write(root, ".gitignore", ".venv/\n.shared-ci/\n")
        write(root, "AGENTS.md", "# Index\n")
        write(
            root,
            "AppTarget/tech-context.md",
            "---\nlayer: AppUI\nowns: [Sources/AppUI/**, SharedWidgets/**, project.yml]\ntest_paths: [Tests/AppUITests/**]\ndepends_on: [Core]\n---\n",
        )
        write(root, "Sources/AppUI/HomeView.swift", "let h = 1\n")
        write(root, "project.yml", "name: Demo\n")
        self.commit(root, "base")

        # Populate heavy ignored directories with decoy CONTEXT.md files that git ls-files must skip
        write(root, ".shared-ci/templates/CONTEXT.md", "---\nlayer: BogusSharedCI\n---\n")
        write(root, ".venv/lib/site-packages/pkg/CONTEXT.md", "---\nlayer: BogusVenv\n---\n")
        write(root, "Sources/AppUI/HomeView.swift", "let h = 2\n")
        write(root, "Tests/AppUITests/HomeTests.swift", "let t = 1\n")
        write(root, "project.yml", "name: Demo2\n")

        result = self.discover(root, "--base", "main")
        layer_names = {layer["name"] for layer in result["layers"]}
        self.assertEqual(layer_names, {"AppUI"})
        self.assertEqual(result["unmapped_files"], [])
        self.assertEqual(
            result["layers"][0]["changed_files"],
            ["Sources/AppUI/HomeView.swift", "Tests/AppUITests/HomeTests.swift", "project.yml"],
        )

    def test_diff_range_head_equals_base_fallback_empty_ok_and_exit_code_1(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\n")
        write(root, "app.py", "value = 1\n")
        self.commit(root, "initial")
        write(root, "app.py", "value = 2\n")
        self.commit(root, "second commit on main")

        # 1. On main where HEAD == base_ref and --base is omitted -> auto-falls back to HEAD~1
        auto_fb = self.discover(root)
        self.assertEqual(auto_fb.get("fallback_base"), "HEAD~1 (HEAD == base_ref)")
        self.assertFalse(auto_fb["empty_surface"])
        self.assertEqual([c["path"] for c in auto_fb["changed_files"]], ["app.py"])

        # 2. --strict-non-empty disables HEAD~1 fallback and exits with domain code 1 (NOT 2!)
        strict_empty = self.discover(root, "--strict-non-empty", check=False)
        self.assertEqual(strict_empty.returncode, 1)
        self.assertNotEqual(strict_empty.returncode, 2)
        self.assertIn("skills/code-review/references/review-rubric.md", strict_empty.stderr)

        # 3. Explicit --base main when clean has empty surface -> exits 1 (NOT 2)
        explicit_empty = self.discover(root, "--base", "main", check=False)
        self.assertEqual(explicit_empty.returncode, 1)

        # 4. --empty-ok / --allow-empty returns exit 0 with empty_surface=true
        allowed_empty = self.discover(root, "--base", "main", "--empty-ok")
        self.assertTrue(allowed_empty["empty_surface"])
        self.assertEqual(allowed_empty["changed_files"], [])

        # 5. --range / --diff-range pins exact commit range
        ranged = self.discover(root, "--range", "HEAD~1..HEAD")
        self.assertEqual(ranged["mode"], "committed-only")
        self.assertEqual([c["path"] for c in ranged["changed_files"]], ["app.py"])
        self.assertIn("..", ranged["diff_command"])

    def test_validate_findings_schema_and_verdict_computation(self) -> None:
        root = self.make_repo()
        findings_file = root / "findings.json"

        # Clean empty findings -> PASS
        findings_file.write_text("[]\n", encoding="utf-8")
        res_pass = json.loads(
            run("python3", str(SCRIPT), "--validate-findings", str(findings_file), cwd=root).stdout
        )
        self.assertTrue(res_pass["ok"])
        self.assertEqual(res_pass["verdict"], "PASS")

        # P2 only -> PASS WITH FOLLOW-UP
        p2_item = {
            "axis": "standards",
            "severity": "P2",
            "layer": "Core",
            "path": "Core/File.py:10",
            "title": "Document retry constant owner",
            "source": "Core/CONTEXT.md:red_lines",
            "evidence": "Retry constant lacks owner note permitted for follow-up.",
            "remediation": "Add follow-up owner comment in Core/File.py.",
        }
        findings_file.write_text(json.dumps([p2_item]), encoding="utf-8")
        res_p2 = json.loads(
            run("python3", str(SCRIPT), "--validate-findings", str(findings_file), cwd=root).stdout
        )
        self.assertTrue(res_p2["ok"])
        self.assertEqual(res_p2["verdict"], "PASS WITH FOLLOW-UP")

        # P1 -> CHANGES REQUESTED
        p1_item = dict(p2_item, axis="spec", severity="P1", title="Reject empty identifier input")
        findings_file.write_text(json.dumps([p2_item, p1_item]), encoding="utf-8")
        res_p1 = json.loads(
            run("python3", str(SCRIPT), "--validate-findings", str(findings_file), cwd=root).stdout
        )
        self.assertTrue(res_p1["ok"])
        self.assertEqual(res_p1["verdict"], "CHANGES REQUESTED")
        self.assertEqual(res_p1["summary"]["standards"]["worst"], "P2")
        self.assertEqual(res_p1["summary"]["spec"]["worst"], "P1")

        # P0 via stdin ('-') -> FAIL (still schema-valid so exit=0)
        p0_item = dict(p1_item, axis="standards", severity="P0", title="Remove upward UI import")
        stdin_proc = subprocess.run(
            ["python3", str(SCRIPT), "--validate-findings", "-"],
            cwd=root,
            input=json.dumps([p1_item, p0_item]),
            text=True,
            capture_output=True,
            check=True,
        )
        res_p0 = json.loads(stdin_proc.stdout)
        self.assertTrue(res_p0["ok"])
        self.assertEqual(res_p0["verdict"], "FAIL")

        # Malformed finding missing required fields -> ok=False, verdict=INCONCLUSIVE, exit=1
        bad_proc = subprocess.run(
            ["python3", str(SCRIPT), "--validate-findings", "-"],
            cwd=root,
            input=json.dumps([{"axis": "invalid_axis", "severity": "P9"}]),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(bad_proc.returncode, 1)
        bad_payload = json.loads(bad_proc.stdout)
        self.assertFalse(bad_payload["ok"])
        self.assertEqual(bad_payload["verdict"], "INCONCLUSIVE")
        self.assertTrue(bad_payload["errors"])

    def test_inline_findings_aliases_and_counter_cases(self) -> None:
        root = self.make_repo()

        for flag in ("--validate-findings", "--findings", "--findings-json"):
            result = run("python3", str(SCRIPT), flag, "[]", cwd=root)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["verdict"], "PASS")

        object_result = run("python3", str(SCRIPT), "--validate-findings", '{"findings": []}', cwd=root)
        self.assertEqual(json.loads(object_result.stdout)["verdict"], "PASS")

        for raw in ('[{"bad": 1}]', "{bad"):
            malformed = run("python3", str(SCRIPT), "--validate-findings", raw, cwd=root, check=False)
            self.assertEqual(malformed.returncode, 1)
            malformed_payload = json.loads(malformed.stdout)
            self.assertFalse(malformed_payload["ok"])
            self.assertEqual(malformed_payload["verdict"], "INCONCLUSIVE")

        missing = run(
            "python3",
            str(SCRIPT),
            "--validate-findings",
            "/nonexistent/path.json",
            cwd=root,
            check=False,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("skills/code-review/references/output-contract.md", missing.stderr)
        self.assertLessEqual(len(missing.stderr.splitlines()), 3)

    def test_discovery_cache_hit_invalidation_and_summary_contract(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\\n")
        write(root, "app.py", "value = 1\\n")
        self.commit(root, "base")
        write(root, "app.py", "value = 2\\n")

        first = run("python3", str(SCRIPT), "--base", "main", cwd=root)
        second = run("python3", str(SCRIPT), "--base", "main", cwd=root)
        self.assertEqual(first.stdout, second.stdout)
        uncached = run("python3", str(SCRIPT), "--base", "main", "--no-cache", cwd=root)
        self.assertEqual(first.stdout, uncached.stdout)

        cache_dir = Path(tempfile.gettempdir()) / "code-review-discovery-cache"
        cached_before_edit = set(cache_dir.glob("*.json"))
        write(root, "app.py", "value = 3\\n")
        run("python3", str(SCRIPT), "--base", "main", cwd=root)
        cached_after_edit = set(cache_dir.glob("*.json"))
        self.assertGreater(len(cached_after_edit), len(cached_before_edit))

        summary = json.loads(run("python3", str(SCRIPT), "--base", "main", "--summary", cwd=root).stdout)
        self.assertEqual(summary["changed_files_total"], 1)
        self.assertEqual([item["path"] for item in summary["changed_files"]], ["app.py"])

    def test_pr_flag_resolves_pull_ref_and_fails_closed_when_unresolvable(self) -> None:
        root = self.make_repo()
        write(root, "AGENTS.md", "# Index\n")
        write(root, "app.py", "value = 1\n")
        self.commit(root, "base")

        # Create a PR commit recorded under refs/pull/42/head while main stays on an unrelated commit
        run("git", "checkout", "-b", "pr-42-branch", cwd=root)
        write(root, "pr_feature.py", "enabled = True\n")
        self.commit(root, "pr 42 feature")
        pr_sha = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
        run("git", "update-ref", "refs/pull/42/head", pr_sha, cwd=root)

        run("git", "checkout", "main", cwd=root)
        write(root, "unrelated_local.py", "local = True\n")
        self.commit(root, "local head commit on main")

        # Stub `gh` in a local bin directory so tests are deterministic and offline
        fake_bin = Path(tempfile.mkdtemp(prefix="code-review-fake-bin-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_bin, ignore_errors=True))
        fake_gh = fake_bin / "gh"
        fake_gh.write_text("#!/bin/sh\necho 'no gh remote configured' >&2\nexit 1\n", encoding="utf-8")
        fake_gh.chmod(0o755)
        base_env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("GIT_")}
        base_env["PATH"] = f"{fake_bin}:{base_env.get('PATH', '')}"

        # 1. Resolvable refs/pull/42/head reviews PR 42 (not unrelated_local.py)
        ok_pr = subprocess.run(
            ["python3", str(SCRIPT), "--repo", str(root), "--pr", "42"],
            cwd=root,
            env=base_env,
            text=True,
            capture_output=True,
            check=True,
        )
        ok_data = json.loads(ok_pr.stdout)
        self.assertEqual([c["path"] for c in ok_data["changed_files"]], ["pr_feature.py"])
        self.assertEqual(ok_data["mode"], "committed-only")

        # 2. Unresolvable --pr 999 fails closed with exit=1 (never silently falls back to local HEAD!)
        bad_pr = subprocess.run(
            ["python3", str(SCRIPT), "--repo", str(root), "--pr", "999"],
            cwd=root,
            env=base_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(bad_pr.returncode, 1)
        self.assertEqual(bad_pr.stdout.strip(), "")
        self.assertIn("cannot resolve pull request", bad_pr.stderr)
        self.assertIn("skills/code-review/references/review-rubric.md", bad_pr.stderr)
        self.assertIn("skills/code-review/references/output-contract.md", bad_pr.stderr)


if __name__ == "__main__":
    unittest.main()
