#!/usr/bin/env python3
"""Focused regression tests for graduated legacy script caps, stale-legacy-cap advisories, and CLI flag aliases."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/skill-contract/scripts/skill_contract.py"
SCRIPTS_DIR = ROOT / "skills/skill-contract/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from contract_core import core_common, core_facts  # noqa: E402

GRADUATED_FACADES: tuple[str, ...] = (
    "skills/personal-m2-assistant/scripts/m2-router.py",
    "skills/my-designer/scripts/verify_design_cases.py",
    "skills/retro-loop/scripts/trace_backfill_exporter.py",
    "skills/knowledge-digest/scripts/digest_vault.py",
    "skills/team-lead/scripts/linear-sync.py",
    "skills/linear-board/scripts/root_tracker.py",
)


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class SkillContractGraduatedCapsTests(unittest.TestCase):
    def _make_package(self, repo: Path, name: str = "demo") -> Path:
        pkg = repo / "skills" / name
        (pkg / ".claude-plugin").mkdir(parents=True)
        (pkg / "references").mkdir()
        (pkg / "scripts").mkdir()
        (pkg / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Demo skill for {name}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
        (pkg / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "description": f"Demo {name}", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        return pkg

    def test_six_modularized_facades_are_graduated_and_under_100_lines(self) -> None:
        spec = importlib.util.spec_from_file_location("skill_contract_facade", CLI)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        facade_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(facade_mod)

        for rel in GRADUATED_FACADES:
            path = ROOT / rel
            self.assertTrue(path.is_file(), f"Missing facade file: {rel}")
            lines = len(path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(lines, 100, f"{rel} expected <=100 lines, got {lines}")
            self.assertNotIn(rel, core_facts.LEGACY_SCRIPT_LINE_CAPS)
            self.assertNotIn(rel, facade_mod.LEGACY_SCRIPT_LINE_CAPS)

    def test_graduated_facade_enforces_hard_1000_and_soft_800_budgets(self) -> None:
        rel = "skills/knowledge-digest/scripts/digest_vault.py"
        with tempfile.TemporaryDirectory(prefix="graduated-cap-") as raw:
            repo = Path(raw)
            pkg = self._make_package(repo, "knowledge-digest")
            target = pkg / "scripts" / "digest_vault.py"

            target.write_text("pass\n" * 801, encoding="utf-8")
            soft_proc = run_cli("audit", "--repo", str(repo), "--strict", "--no-cache")
            self.assertEqual(soft_proc.returncode, 0, soft_proc.stderr)
            soft_payload = json.loads(soft_proc.stdout)
            self.assertEqual(soft_payload["findings"], [])
            self.assertTrue(
                any(
                    item["code"] == "script-lines-soft"
                    and item["file"] == rel
                    and item["tier"] == "soft_advisory_exit_0"
                    for item in soft_payload["advisories"]
                )
            )

            target.write_text("pass\n" * 1001, encoding="utf-8")
            hard_proc = run_cli("audit", "--repo", str(repo), "--strict", "--no-cache")
            self.assertEqual(hard_proc.returncode, 1)
            hard_payload = json.loads(hard_proc.stdout)
            self.assertEqual(len(hard_payload["findings"]), 1)
            self.assertEqual(hard_payload["findings"][0]["code"], "script-lines")
            self.assertEqual(hard_payload["findings"][0]["script"], rel)
            self.assertEqual(hard_payload["findings"][0]["cap"], 1000)

    def test_stale_legacy_cap_emits_soft_advisory_at_or_below_800_lines_only(self) -> None:
        self.assertIn("stale-legacy-cap", core_common.VALVE_FINDING_GUIDANCE)
        rel = "skills/demo/scripts/legacy_monolith.py"
        with tempfile.TemporaryDirectory(prefix="stale-legacy-cap-") as raw:
            repo = Path(raw)
            pkg = self._make_package(repo, "demo")
            script = pkg / "scripts" / "legacy_monolith.py"

            with mock.patch.dict(core_facts.LEGACY_SCRIPT_LINE_CAPS, {rel: 1400}, clear=False):
                # Positive case: listed legacy monolith drops to <= 800 lines -> stale-legacy-cap soft advisory (exit 0 even with --strict).
                script.write_text("x = 1\n" * 800, encoding="utf-8")
                advisories_800 = core_facts.audit_package_advisories(pkg)
                findings_800 = core_facts.audit_package(pkg, repo_bound=True)
                self.assertEqual(findings_800, [])
                stale_items = [a for a in advisories_800 if a["code"] == "stale-legacy-cap"]
                self.assertEqual(len(stale_items), 1)
                self.assertEqual(stale_items[0]["file"], rel)
                self.assertEqual(stale_items[0]["tier"], "soft_advisory_exit_0")
                self.assertEqual(stale_items[0]["lines"], 800)
                self.assertEqual(stale_items[0]["threshold"], 800)
                self.assertEqual(stale_items[0]["cap"], 1400)
                self.assertIn("LEGACY_SCRIPT_LINE_CAPS", stale_items[0]["hint"])

                repo_advisories = core_facts._repo_advisories(repo)
                self.assertTrue(any(a["code"] == "stale-legacy-cap" and a["file"] == rel for a in repo_advisories))

                # Counter-case: listed legacy monolith at 801 lines (still > 800 and <= cap) -> neither stale-legacy-cap nor script-lines-soft.
                script.write_text("x = 1\n" * 801, encoding="utf-8")
                advisories_801 = core_facts.audit_package_advisories(pkg)
                self.assertEqual([a for a in advisories_801 if a["file"] == rel], [])

    def test_cli_repeatable_and_csv_skill_flags_and_path_aliases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skill-contract-cli-") as raw:
            repo = Path(raw)
            shutil.copytree(ROOT / "skills/skill-contract", repo / "skills/skill-contract", symlinks=True)
            alpha = self._make_package(repo, "alpha")
            beta = self._make_package(repo, "beta")
            for pkg in (alpha, beta):
                (pkg / "SKILL.md").write_text(
                    f"---\nname: {pkg.name}\ndescription: Demo {pkg.name}\n---\n\nSee [eval](references/eval-cases.json).\n",
                    encoding="utf-8",
                )
                (pkg / "references" / "eval-cases.json").write_text(
                    json.dumps(
                        {
                            "schema": "skill-eval-cases/v1",
                            "version": 1,
                            "active_cases": [
                                {
                                    "id": "EV-001",
                                    "title": "Check SKILL.md",
                                    "tags": ["smoke"],
                                    "file": "SKILL.md",
                                    "must_contain": [f"name: {pkg.name}"],
                                }
                            ],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

            # Positive replay 1: repeatable --skill flags + --path repo alias on audit & eval
            audit_rep = run_cli(
                "audit", "--path", str(repo), "--skill", "alpha", "--skill", "beta,skill-contract", "--strict", "--no-cache"
            )
            self.assertEqual(audit_rep.returncode, 0, audit_rep.stderr)
            self.assertEqual(json.loads(audit_rep.stdout)["skills"], 3)

            eval_rep = run_cli(
                "eval", "--path", str(repo), "--skill", "alpha", "--skill", "beta", "--strict", "--no-cache"
            )
            self.assertEqual(eval_rep.returncode, 0, eval_rep.stderr)
            self.assertEqual(json.loads(eval_rep.stdout)["cases_run"], 2)

            # Positive replay 2: --path pointing directly to a single skill directory or SKILL.md
            single_dir = run_cli("audit", "--path", str(alpha), "--strict", "--no-cache")
            self.assertEqual(single_dir.returncode, 0, single_dir.stderr)
            self.assertEqual(json.loads(single_dir.stdout)["skills"], 1)

            single_md = run_cli("audit", "--path", str(beta / "SKILL.md"), "--strict", "--no-cache")
            self.assertEqual(single_md.returncode, 0, single_md.stderr)
            self.assertEqual(json.loads(single_md.stdout)["skills"], 1)

            # Positive replay 3: flag-first --skill without subcommand defaults to audit without mutually-exclusive crash
            flag_first = run_cli("--path", str(repo), "--skill", "alpha", "--strict", "--no-cache")
            self.assertEqual(flag_first.returncode, 0, flag_first.stderr)
            self.assertEqual(json.loads(flag_first.stdout)["skills"], 1)

            # Positive replay 4: hygiene-facts --path accepts both JSON file and repo directory unambiguously
            sample_json = repo / "sample.json"
            sample_json.write_text(json.dumps({"hello": "world", "items": [1, 2]}), encoding="utf-8")
            hf_json = run_cli("hygiene-facts", "--repo", str(repo), "--path", str(sample_json), "--no-check-diff")
            self.assertEqual(hf_json.returncode, 0, hf_json.stderr)
            self.assertEqual(json.loads(hf_json.stdout)["json_summary"]["top_level_keys"], ["hello", "items"])

            hf_dir = run_cli("hygiene-facts", "--path", str(repo), "--no-check-diff")
            self.assertEqual(hf_dir.returncode, 0, hf_dir.stderr)
            self.assertIsNone(json.loads(hf_dir.stdout)["json_summary"])

    def test_counter_case_invalid_flag_and_unknown_target_emit_bounded_3_line_stderr(self) -> None:
        for bad_args in (
            ("audit", "--nonexistent-flag"),
            ("audit", "definitely-not-a-skill-or-path"),
        ):
            with self.subTest(args=bad_args):
                proc = run_cli(*bad_args)
                self.assertEqual(proc.returncode, 2)
                lines = [line for line in proc.stderr.splitlines() if line.strip()]
                self.assertLessEqual(len(lines), 3, proc.stderr)
                self.assertTrue(
                    lines[-1].startswith("Reference: skills/skill-contract/references/valves-and-metabolism.md"),
                    proc.stderr,
                )


if __name__ == "__main__":
    unittest.main()
