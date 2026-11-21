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


if __name__ == "__main__":
    unittest.main()
