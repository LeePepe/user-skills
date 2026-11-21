#!/usr/bin/env python3
"""Regression tests for codebase-design drawer wiring, spec_gate, and tech-context/ADR drift patrol."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATROL_SCRIPT = ROOT / "skills" / "codebase-design" / "scripts" / "spec_code_drift_patrol.py"
SPEC_GATE_SCRIPT = ROOT / "skills" / "codebase-design" / "scripts" / "spec_gate.py"
SKILL_RETRO_SCRIPT = ROOT / "skills" / "skill-retro" / "scripts" / "skill_retro.py"
EXPECTED_DRAWERS = (
    "skills/codebase-design/references/ADR-FORMAT.md",
    "skills/codebase-design/references/CONTEXT-FORMAT.md",
    "skills/codebase-design/references/DEEPENING.md",
    "skills/codebase-design/references/DESIGN-IT-TWICE.md",
    "skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


patrol = _load("codebase_design_patrol_mod", PATROL_SCRIPT)
gate = _load("codebase_design_spec_gate_mod", SPEC_GATE_SCRIPT)
skill_retro = _load("codebase_design_skill_retro_mod", SKILL_RETRO_SCRIPT)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class CodebaseDesignPatrolTests(unittest.TestCase):
    def _init_repo_fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        holder = tempfile.TemporaryDirectory()
        base = Path(holder.name)
        dev = base / "Development"
        handoff = dev / "handoff"
        repo = dev / "SampleRepo"
        repo.mkdir(parents=True)
        handoff.mkdir(parents=True)
        _git("init", "-q", "-b", "main", cwd=repo)
        _git("config", "user.email", "test@example.com", cwd=repo)
        _git("config", "user.name", "Test", cwd=repo)
        return holder, dev, handoff, repo

    def test_positive_and_counter_case_for_tech_context_and_adr_contracts(self) -> None:
        holder, dev, handoff, repo = self._init_repo_fixture()
        self.addCleanup(holder.cleanup)

        (repo / "Packages" / "Core" / "Sources").mkdir(parents=True)
        (repo / "Packages" / "Core" / "Sources" / "Model.swift").write_text("public struct Model {}\n", encoding="utf-8")
        (repo / "docs" / "architecture").mkdir(parents=True)
        (repo / "docs" / "adr").mkdir(parents=True)
        (repo / "docs" / "adr" / "README.md").write_text("Index without H1 is allowed for README.md.\n", encoding="utf-8")
        (repo / "docs" / "adr" / "template.md").write_text("Template without H1 is allowed for template.md.\n", encoding="utf-8")
        (repo / "docs" / "adr" / "0001-use-sqlite.md").write_text(
            "# Use SQLite for Local Cache\n\nKeep offline storage single-file and deterministic.\n",
            encoding="utf-8",
        )
        (repo / "docs" / "architecture" / "tech-context.md").write_text(
            "---\nlayer: _root\n---\n\n# SampleRepo tech context\n\n"
            "| Layer | Responsibility | tech-context | depends_on |\n"
            "|---|---|---|---|\n"
            "| Core | Domain types | `Packages/Core/tech-context.md` | (none) |\n",
            encoding="utf-8",
        )
        (repo / "Packages" / "Core" / "tech-context.md").write_text(
            "---\nlayer: Core\nowns: [Packages/Core/**]\ndepends_on: []\n---\n\n# Core\n\nPure domain model.\n",
            encoding="utf-8",
        )
        _git("add", ".", cwd=repo)
        _git("commit", "-qm", "clean tech-context and adr baseline", cwd=repo)

        clean_report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("SampleRepo",))
        self.assertEqual(clean_report["findings"], [])
        self.assertTrue(clean_report["clean"])

        # Counter-case: broken layer table path, broken leaf owns prefix/explicit path, and malformed ADR heading
        (repo / "docs" / "architecture" / "tech-context.md").write_text(
            "---\nlayer: _root\n---\n\n# SampleRepo tech context\n\n"
            "| Layer | Responsibility | tech-context | depends_on |\n"
            "|---|---|---|---|\n"
            "| Core | Domain types | `Packages/Core/tech-context.md` | (none) |\n"
            "| Ghost | Missing layer | `Packages/Ghost/tech-context.md` | Core |\n",
            encoding="utf-8",
        )
        (repo / "Packages" / "Core" / "tech-context.md").write_text(
            "---\nlayer: Core\nowns:\n  - Packages/Core/**\n  - Packages/Vanished/**\n  - Sources/MissingFile.swift\ndepends_on: []\n---\n\n# Core\n",
            encoding="utf-8",
        )
        (repo / "docs" / "adr" / "0002-missing-heading.md").write_text(
            "---\nstatus: accepted\n---\n\n## Context without H1 Title\n\nBody text.\n",
            encoding="utf-8",
        )
        _git("add", ".", cwd=repo)
        _git("commit", "-qm", "introduce tech-context and adr drift", cwd=repo)

        drift_proc = subprocess.run(
            [
                sys.executable,
                str(PATROL_SCRIPT),
                "scan",
                "--dev-root",
                str(dev),
                "--handoff-root",
                str(handoff),
                "--repos",
                "SampleRepo",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        drift_report = json.loads(drift_proc.stdout)
        by_evidence = {item["evidence"]: item for item in drift_report["spec_stale_findings"]}
        self.assertIn("Packages/Ghost/tech-context.md", by_evidence)
        self.assertIn("Packages/Vanished/**", by_evidence)
        self.assertIn("Sources/MissingFile.swift", by_evidence)
        self.assertIn("docs/adr/0002-missing-heading.md", by_evidence)

        for key in ("Packages/Ghost/tech-context.md", "Packages/Vanished/**", "Sources/MissingFile.swift"):
            self.assertEqual(by_evidence[key]["code"], "missing_referenced_path")
            self.assertEqual(by_evidence[key]["reference_drawer"], "skills/codebase-design/references/CONTEXT-FORMAT.md")

        adr_finding = by_evidence["docs/adr/0002-missing-heading.md"]
        self.assertEqual(adr_finding["code"], "malformed_adr_heading")
        self.assertEqual(adr_finding["reference_drawer"], "skills/codebase-design/references/ADR-FORMAT.md")
        self.assertIn("skills/codebase-design/references/ADR-FORMAT.md", adr_finding["remediation"])
        self.assertIn("skills/codebase-design/references/CONTEXT-FORMAT.md", drift_proc.stderr)
        self.assertIn("skills/codebase-design/references/ADR-FORMAT.md", drift_proc.stderr)

    def test_self_disclosing_drawer_pointers_on_spec_gate_and_patrol_failures(self) -> None:
        for script_path in (SPEC_GATE_SCRIPT, PATROL_SCRIPT):
            bad_cli = subprocess.run(
                [sys.executable, str(script_path), "--definitely-invalid-flag"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_cli.returncode, 2)
            stderr_lines = [line for line in bad_cli.stderr.strip().splitlines() if line.strip()]
            self.assertLessEqual(len(stderr_lines), 3)
            for drawer in EXPECTED_DRAWERS:
                self.assertIn(drawer, bad_cli.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            handoff_dir = Path(tmp) / "goal-phase0"
            handoff_dir.mkdir(parents=True)

            # 1) no_topic failure on pop
            no_topic = subprocess.run(
                [sys.executable, str(SPEC_GATE_SCRIPT), "pop", "--handoff-dir", str(handoff_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(no_topic.returncode, 1)
            no_topic_json = json.loads(no_topic.stdout)
            self.assertEqual(no_topic_json["status"], "no_topic")
            self.assertEqual(no_topic_json["reference"], "skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md")
            self.assertIn("skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md", no_topic.stderr)

            # 2) stale_checkpoint failure after subsequent push
            subprocess.run(
                [
                    sys.executable,
                    str(SPEC_GATE_SCRIPT),
                    "push",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--agent",
                    "designer-a",
                    "--model",
                    "test-model",
                    "--proposal-id",
                    "p1",
                    "--label",
                    "Minimal Interface",
                    "--interface-summary",
                    "1 entry point",
                    "--seam-placement",
                    "port",
                    "--dependency-category",
                    "in_process",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SPEC_GATE_SCRIPT),
                    "checkpoint",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--proposal-id",
                    "p1",
                    "--decision",
                    "approve",
                    "--decided-by",
                    "owner",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SPEC_GATE_SCRIPT),
                    "push",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--agent",
                    "designer-b",
                    "--model",
                    "test-model",
                    "--proposal-id",
                    "p2",
                    "--label",
                    "Flexible Interface",
                    "--interface-summary",
                    "2 entry points",
                    "--seam-placement",
                    "adapter",
                    "--dependency-category",
                    "local_substitutable",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            stale_chk = subprocess.run(
                [sys.executable, str(SPEC_GATE_SCRIPT), "pop", "--handoff-dir", str(handoff_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(stale_chk.returncode, 1)
            stale_chk_json = json.loads(stale_chk.stdout)
            self.assertEqual(stale_chk_json["status"], "stale_checkpoint")
            self.assertEqual(stale_chk_json["reference"], "skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md")

            # 3) SPEC_VERSION_CONFLICT on stamp & STALE_SPEC / STALE_DESIGN on verify-lock
            spec_md = handoff_dir / "spec.md"
            spec_md.write_text("---\nversion: 1\nspec_sha: outdated\n---\n\n# Spec\n", encoding="utf-8")
            (handoff_dir / "design.md").write_text(
                "---\nversion: 1\nparent_spec_version: 0\nparent_spec_sha: outdated\ndesign_sha: x\n---\n\n# Design\n",
                encoding="utf-8",
            )
            cas_conflict = subprocess.run(
                [sys.executable, str(SPEC_GATE_SCRIPT), "stamp", "--file", str(spec_md), "--expected-version", "2"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cas_conflict.returncode, 1)
            cas_json = json.loads(cas_conflict.stdout)
            self.assertEqual(cas_json["code"], "SPEC_VERSION_CONFLICT")
            self.assertEqual(cas_json["reference"], "skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md")

            verify_lock = subprocess.run(
                [sys.executable, str(SPEC_GATE_SCRIPT), "verify-lock", "--handoff-dir", str(handoff_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(verify_lock.returncode, 1)
            verify_json = json.loads(verify_lock.stdout)
            self.assertIn("STALE_SPEC", verify_json["issues"])
            self.assertIn("STALE_DESIGN", verify_json["issues"])
            self.assertEqual(verify_json["reference"], "skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md")
            self.assertIn("skills/codebase-design/references/PHASE0-PROPOSAL-CHECKPOINT.md", verify_lock.stderr)

    def test_flow_map_and_skill_retro_probe_report_zero_unwired_drawers(self) -> None:
        flow_res = subprocess.run(
            [sys.executable, str(PATROL_SCRIPT), "--flow-map"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        flow_payload = json.loads(flow_res.stdout)
        self.assertTrue(flow_payload["ok"])
        self.assertEqual(flow_payload["skill"], "codebase-design")
        self.assertTrue(flow_payload["anti_explosion_valves"]["all_l1_docs_within_budget"])
        self.assertEqual(set(flow_payload["shared_tiers"]["tier_2_reference_drawers"]), set(EXPECTED_DRAWERS))

        pkg_dir = ROOT / "skills" / "codebase-design"
        for ref_md in sorted((pkg_dir / "references").glob("*.md")):
            rel_ref = f"references/{ref_md.name}"
            self.assertTrue(
                skill_retro._drawer_linked_in_scripts(pkg_dir, rel_ref),
                f"{rel_ref} must be wired in codebase-design/scripts/",
            )

        probe_res = subprocess.run(
            [
                sys.executable,
                str(SKILL_RETRO_SCRIPT),
                "probe",
                "--skill",
                "codebase-design",
                "--skills-root",
                str(ROOT / "skills"),
                "--fast",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        probe_payload = json.loads(probe_res.stdout)
        self.assertTrue(probe_payload["ok"])
        self.assertEqual(len(probe_payload["skills"]), 1)
        entry = probe_payload["skills"][0]
        self.assertEqual(entry["skill"], "codebase-design")
        self.assertEqual(entry["signals"]["l1_unwired_drawers"], 0)
        self.assertTrue(entry["budget_ok"])


if __name__ == "__main__":
    unittest.main()
