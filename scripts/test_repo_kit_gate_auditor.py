#!/usr/bin/env python3
"""Unit tests for `skills/repo-kit/scripts/repo_gate_auditor.py` & `skill-retro` self-driven loop."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "skills" / "repo-kit" / "scripts" / "repo_gate_auditor.py"
SKILL_RETRO_PATH = ROOT / "skills" / "skill-retro" / "scripts" / "skill_retro.py"


def _load_mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AUDITOR = _load_mod("test_repo_gate_auditor_mod", AUDITOR_PATH)
SKILL_RETRO = _load_mod("test_repo_kit_skill_retro_mod", SKILL_RETRO_PATH)


def _init_git_repo(repo_dir: Path, *, hooks_path: str | None = None) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
    cfg_lines = ["[core]", "\trepositoryformatversion = 0"]
    if hooks_path:
        cfg_lines.append(f"\thooksPath = {hooks_path}")
    (repo_dir / ".git" / "config").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")
    return repo_dir


def _write_exec(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class RepoKitGateAuditorTests(unittest.TestCase):
    def test_detects_all_six_rg_rules_and_computes_speedup_angles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dev_root = Path(tmp) / "Development"
            dev_root.mkdir(parents=True, exist_ok=True)

            # 0. Healthy repo (counter-case: 100 score, 0 findings)
            healthy = _init_git_repo(dev_root / "healthy-app", hooks_path=".githooks")
            _write_exec(
                healthy / "scripts" / "verify",
                "#!/usr/bin/env bash\n"
                "base=\"${VERIFY_BASE:-origin/main}\"\n"
                "changed=\"$(git diff --name-only \"$base\" HEAD)\"\n"
                "python3 -c 'from concurrent.futures import ThreadPoolExecutor'\n"
                "context run $layers --mode local\n",
            )
            _write_exec(
                healthy / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\n"
                "if [ \"${VERIFY_ALL:-0}\" = \"1\" ]; then exec \"$REPO_ROOT/scripts/verify\" --all; fi\n"
                "exec \"$REPO_ROOT/scripts/verify\"\n",
            )
            (healthy / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            (healthy / ".github" / "workflows" / "ci.yml").write_text(
                "name: CI\non: [push]\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: ./scripts/verify --all\n",
                encoding="utf-8",
            )
            (healthy / ".github" / "repo-contract.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "guide": "docs/development.md",
                        "shared_ci": "a" * 40,
                        "dependencies": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            # 1. RG-001: missing_verify_entrypoint
            rg1 = _init_git_repo(dev_root / "rg1-no-verify", hooks_path=".githooks")
            _write_exec(rg1 / ".githooks" / "pre-push", "#!/usr/bin/env bash\necho push\n")

            # 2. RG-002 & RG-003 & RG-004 & RG-005 & RG-006 combined drifted repo
            drifted = _init_git_repo(dev_root / "drifted-monolith", hooks_path=".git/hooks-local")
            _write_exec(
                drifted / "scripts" / "verify",
                "#!/usr/bin/env bash\n"
                "layers=\"Core UI\"\n"
                "for layer in $layers; do\n"
                "  context run \"$layer\" --mode local\n"
                "done\n",
            )
            # Tracked .githooks/pre-push forces --all AND core.hooksPath points elsewhere (.git/hooks-local)
            _write_exec(
                drifted / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
            )
            (drifted / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
            (drifted / ".github" / "workflows" / "ci.yml").write_text(
                "name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo lint-only\n",
                encoding="utf-8",
            )
            (drifted / "AGENTS.md").write_text(
                "# AGENTS\nSee https://github.com/org/shared-ci/blob/"
                + ("b" * 40)
                + "/ai/agent-protocol.md\n",
                encoding="utf-8",
            )

            healthy_rep = AUDITOR.inspect_single_repo(
                healthy, dev_root=dev_root, shared_ci_head="a" * 40
            )
            self.assertEqual(healthy_rep["gate_health_score"], 100)
            self.assertEqual(healthy_rep["triggered_rules"], [])
            for angle_val in healthy_rep["speedup_recommendations"].values():
                self.assertEqual(angle_val, "configured")

            rg1_rep = AUDITOR.inspect_single_repo(rg1, dev_root=dev_root, shared_ci_head="a" * 40)
            self.assertIn("RG-001", rg1_rep["triggered_rules"])

            drifted_rep = AUDITOR.inspect_single_repo(
                drifted, dev_root=dev_root, shared_ci_head="a" * 40
            )
            for expected_rule in ("RG-002", "RG-003", "RG-004", "RG-005", "RG-006"):
                self.assertIn(expected_rule, drifted_rep["triggered_rules"])
            self.assertLess(drifted_rep["gate_health_score"], 50)

            scan_out = AUDITOR.scan_repositories(dev_root=dev_root, flow_map=True)
            self.assertTrue(scan_out["ok"])
            self.assertEqual(scan_out["repos_scanned"], 3)
            self.assertEqual(scan_out["healthy_repos_count"], 1)
            self.assertEqual(scan_out["drifted_repos_count"], 2)
            self.assertGreaterEqual(len(scan_out["actionable_proposals"]), 5)
            self.assertIn("flow_map", scan_out)

    def test_cli_subcommands_and_self_disclosing_error_on_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = _init_git_repo(Path(tmp) / "sample-repo", hooks_path=".githooks")
            _write_exec(
                repo_dir / "scripts" / "verify",
                "#!/usr/bin/env bash\ngit diff --name-only origin/main HEAD\n",
            )
            _write_exec(
                repo_dir / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\"\n",
            )

            for subcmd in ("scan", "bottlenecks", "loop-check"):
                proc = subprocess.run(
                    [sys.executable, str(AUDITOR_PATH), subcmd, "--repo", str(repo_dir), "--json"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["repos_scanned"], 1)

            # Default scan when invoked directly with --repo
            proc_default = subprocess.run(
                [sys.executable, str(AUDITOR_PATH), "--repo", str(repo_dir), "--json"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_default.returncode, 0, proc_default.stderr)
            self.assertEqual(json.loads(proc_default.stdout)["schema"], "repo-gate-audit/v1")

            proc_summary = subprocess.run(
                [
                    sys.executable,
                    str(AUDITOR_PATH),
                    "check",
                    "--target-dir",
                    str(repo_dir),
                    "--summary",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_summary.returncode, 0, proc_summary.stderr)
            summary_payload = json.loads(proc_summary.stdout)
            self.assertTrue(summary_payload["ok"])
            self.assertEqual(summary_payload["schema"], "repo-gate-audit/v1")

            proc_bad_flag = subprocess.run(
                [sys.executable, str(AUDITOR_PATH), "--unknown-flag-xyz"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_bad_flag.returncode, 2)
            self.assertLessEqual(len(proc_bad_flag.stderr.splitlines()), 3)
            self.assertIn("skills/repo-kit/references/repo-bound-skills-and-gates.md", proc_bad_flag.stderr)

            # Negative case: invalid subcommand exits 2 with pointer to repo-bound-skills-and-gates.md
            proc_bad = subprocess.run(
                [sys.executable, str(AUDITOR_PATH), "bad-subcmd"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_bad.returncode, 2)
            self.assertIn("skills/repo-kit/references/repo-bound-skills-and-gates.md", proc_bad.stderr)

    def test_skill_retro_discovers_repo_kit_and_ingests_cross_repo_gate_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev_root = root / "dev"
            drifted = _init_git_repo(dev_root / "slow-repo", hooks_path=".githooks")
            _write_exec(
                drifted / "scripts" / "verify",
                "#!/usr/bin/env bash\nfor layer in $layers; do context run \"$layer\"; done\n",
            )
            _write_exec(
                drifted / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
            )

            probe = SKILL_RETRO.probe_skills(
                skill="repo-kit",
                skills_root=ROOT / "skills",
                dev_root=dev_root,
                fast=True,
            )
            self.assertTrue(probe["ok"])
            self.assertEqual(probe["skills_probed"], 1)
            self.assertEqual(probe["skills"][0]["skill"], "repo-kit")
            self.assertEqual(probe["skills"][0]["signals"]["internal_doc_cli_drift_count"], 0)
            self.assertGreaterEqual(probe["skills"][0]["signals"]["eval_counter_case_ratio"], 0.25)
            self.assertIn("cross_repo_gate_health", probe)
            self.assertGreaterEqual(probe["cross_repo_gate_health"]["total_findings"], 2)

            diag = SKILL_RETRO.compute_skill_retro(
                skill="repo-kit",
                skills_root=ROOT / "skills",
                dev_root=dev_root,
                mirror_roi_snapshot=False,
                isolated_fixtures=True,
            )
            self.assertTrue(diag["ok"])
            self.assertEqual(diag["skill_filter"], "repo-kit")
            self.assertEqual(diag["status"], "ACTIONABLE")
            self.assertGreaterEqual(diag["cross_repo_gate_health"]["total_findings"], 2)
            self.assertTrue(any(p["skill"] == "repo-kit" for p in diag["proposals"]))

    def test_delegating_hook_replays_counter_case_without_rg004(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev_root = root / "dev"
            repo = _init_git_repo(dev_root / "delegating", hooks_path=".git/hooks-local")
            _write_exec(
                repo / "scripts" / "verify",
                "#!/usr/bin/env bash\n"
                "git diff --name-only origin/main HEAD\n",
            )
            _write_exec(
                repo / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\"\n",
            )
            _write_exec(
                repo / ".git" / "hooks-local" / "pre-push",
                "#!/usr/bin/env bash\n"
                "for delegate in \"$REPO_ROOT/scripts/hooks/pre-push\" \"$REPO_ROOT/.githooks/pre-push\"; do\n"
                "  if [[ -x \"$delegate\" ]]; then exec \"$delegate\" \"$@\"; fi\n"
                "done\n",
            )

            delegated = AUDITOR.inspect_single_repo(repo, dev_root=dev_root)
            self.assertNotIn("RG-004", delegated["triggered_rules"])

            _write_exec(
                repo / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
            )
            forcing = AUDITOR.inspect_single_repo(repo, dev_root=dev_root)
            self.assertIn("RG-004", forcing["triggered_rules"])

            _write_exec(
                repo / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\n"
                "for layer in $layers; do context run \"$layer\"; done\n"
                "exec \"$REPO_ROOT/scripts/verify\"\n",
            )
            serial = AUDITOR.inspect_single_repo(repo, dev_root=dev_root)
            self.assertIn("RG-003", serial["triggered_rules"])

    def test_summary_compaction_and_full_output_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev_root = root / "dev"
            for index in range(25):
                repo = _init_git_repo(dev_root / f"repo-{index:02d}", hooks_path=".githooks")
                _write_exec(
                    repo / "scripts" / "verify",
                    "#!/usr/bin/env bash\n"
                    "for layer in $layers; do context run \"$layer\"; done\n",
                )
                _write_exec(
                    repo / ".githooks" / "pre-push",
                    "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
                )

            compact = AUDITOR.scan_repositories(dev_root=dev_root, no_cache=True)
            compact_json = json.dumps(compact, ensure_ascii=False, indent=2)
            self.assertTrue(compact["summary_compacted"])
            self.assertLess(len(compact_json.encode("utf-8")), 18_000)
            self.assertEqual(compact["repos_scanned"], 25)

            full = AUDITOR.scan_repositories(dev_root=dev_root, full=True, no_cache=True)
            full_json = json.dumps(full, ensure_ascii=False, indent=2)
            self.assertNotIn("summary_compacted", full)
            self.assertGreater(len(full_json.encode("utf-8")), 20_000)

            single = AUDITOR.scan_repositories(repo=dev_root / "repo-00", no_cache=True)
            self.assertNotIn("summary_compacted", single)

    def test_watermark_cache_hit_and_gate_file_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev_root = root / "dev"
            repo = _init_git_repo(dev_root / "cached", hooks_path=".githooks")
            _write_exec(
                repo / "scripts" / "verify",
                "#!/usr/bin/env bash\ngit diff --name-only origin/main HEAD\n",
            )
            _write_exec(
                repo / ".githooks" / "pre-push",
                "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\"\n",
            )

            original = AUDITOR.inspect_single_repo
            with mock.patch.object(AUDITOR, "inspect_single_repo", wraps=original) as inspected:
                AUDITOR.scan_repositories(dev_root=dev_root)
                AUDITOR.scan_repositories(dev_root=dev_root)
                self.assertEqual(inspected.call_count, 1)

                _write_exec(
                    repo / "scripts" / "verify",
                    "#!/usr/bin/env bash\n"
                    "git diff --name-only origin/main HEAD\n"
                    "echo changed\n",
                )
                AUDITOR.scan_repositories(dev_root=dev_root)
                self.assertEqual(inspected.call_count, 2)

                _write_exec(
                    repo / ".githooks" / "pre-push",
                    "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
                )
                AUDITOR.scan_repositories(dev_root=dev_root)
                self.assertEqual(inspected.call_count, 3)

    def test_external_hooks_path_outside_repo_dir_and_scan_repos_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev_root = root / "dev"
            external_hooks_dir = root / "global-git-hooks"
            _write_exec(
                external_hooks_dir / "pre-push",
                "#!/usr/bin/env bash\n"
                "for layer in $layers; do context run \"$layer\"; done\n"
                "exec \"$REPO_ROOT/scripts/verify\" --all\n",
            )
            repo_ext = _init_git_repo(dev_root / "ext-hooks-repo", hooks_path=str(external_hooks_dir))
            _write_exec(
                repo_ext / "scripts" / "verify",
                "#!/usr/bin/env bash\ngit diff --name-only origin/main HEAD\n",
            )

            rep = AUDITOR.inspect_single_repo(repo_ext, dev_root=dev_root)
            self.assertIn("RG-003", rep["triggered_rules"])
            self.assertIn("RG-004", rep["triggered_rules"])
            self.assertTrue(any("global-git-hooks/pre-push" in f["evidence"] for f in rep["findings"]))

            scan_out = AUDITOR.scan_repos(dev_root=dev_root)
            self.assertTrue(scan_out["ok"])
            self.assertEqual(scan_out["repos_scanned"], 1)
            self.assertEqual(scan_out["scan_errors_count"], 0)

    def test_shared_provider_rg002_exemption_has_consumer_counter_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = _init_git_repo(root / "shared-ci")
            _write_exec(
                provider / "scripts" / "verify",
                "#!/usr/bin/env bash\n"
                "# Fast self-provider mode runs all suites in parallel.\n"
                "VERIFY_JOBS=${VERIFY_JOBS:-4}\n"
                "python3 -c 'from concurrent.futures import ThreadPoolExecutor'\n",
            )
            (provider / "README.md").write_text("Fast self-provider mode runs all suites.\n", encoding="utf-8")
            provider_report = AUDITOR.inspect_single_repo(provider)
            self.assertNotIn("RG-002", provider_report["triggered_rules"])

            consumer = _init_git_repo(root / "consumer")
            _write_exec(consumer / "scripts" / "verify", "#!/usr/bin/env bash\npytest\n")
            consumer_report = AUDITOR.inspect_single_repo(consumer)
            self.assertIn("RG-002", consumer_report["triggered_rules"])

    def test_scope_conditioned_rg001_exempts_upstream_but_catches_owned_engineering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = _init_git_repo(root / "falcon")
            (upstream / ".github" / "workflows").mkdir(parents=True)
            (upstream / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
            (upstream / "src").mkdir()
            (upstream / ".git" / "config").write_text(
                "[core]\n\trepositoryformatversion = 0\n[remote \"origin\"]\n\turl = https://github.com/nocoo/falcon.git\n",
                encoding="utf-8",
            )
            upstream_report = AUDITOR.inspect_single_repo(upstream)
            self.assertNotIn("RG-001", upstream_report["triggered_rules"])
            self.assertEqual(upstream_report["scope_classification"], "exempt_third_party_upstream")

            owned = _init_git_repo(root / "owned-app")
            (owned / ".github" / "workflows").mkdir(parents=True)
            (owned / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
            (owned / "AGENTS.md").write_text("# Engineering repo\n", encoding="utf-8")
            owned_report = AUDITOR.inspect_single_repo(owned)
            self.assertIn("RG-001", owned_report["triggered_rules"])

    def test_pr_facts_positive_and_invalid_counter_case(self) -> None:
        gh_payload = {
            "number": 42,
            "title": "Facts only",
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "mergedAt": None,
            "mergeCommit": None,
            "headRefName": "feature/facts",
            "headRefOid": "a" * 40,
            "url": "https://github.com/org/repo/pull/42",
            "statusCheckRollup": [
                {"name": "unit", "conclusion": "SUCCESS"},
                {"name": "lint", "conclusion": "FAILURE"},
                {"name": "build", "status": "IN_PROGRESS"},
            ],
        }
        completed = subprocess.CompletedProcess(["gh"], 0, json.dumps(gh_payload), "")
        with mock.patch.object(AUDITOR.subprocess, "run", return_value=completed):
            report, exit_code, stderr = AUDITOR.build_pr_facts_report(pr="42", repo="org/repo")
        self.assertEqual(exit_code, 0)
        self.assertIsNone(stderr)
        self.assertEqual(report["check_counts"], {"total": 3, "passed": 1, "failed": 1, "pending": 1})
        self.assertEqual(report["failed_checks"], ["lint"])
        self.assertLessEqual(len(json.dumps(report).encode("utf-8")), 20_000)

        proc = subprocess.run(
            [sys.executable, str(AUDITOR_PATH), "pr-facts", "--pr", "not-a-number", "--repo", "org/repo"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertLessEqual(len(proc.stderr.splitlines()), 3)
        self.assertIn("skills/repo-kit/references/repo-bound-skills-and-gates.md", proc.stderr)


if __name__ == "__main__":
    unittest.main()
