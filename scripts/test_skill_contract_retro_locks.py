from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/skill-contract/scripts/skill_contract.py"


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, text=True, capture_output=True, check=False)


class SkillContractRetroLocksTests(unittest.TestCase):
    def make_workspace(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        holder = tempfile.TemporaryDirectory(prefix="skill-contract-retro-")
        workspace = Path(holder.name)
        skills = workspace / "skills"
        skills.mkdir()
        shutil.copytree(ROOT / "skills/skill-contract", skills / "skill-contract", symlinks=True)
        return holder, workspace

    def test_positive_replay_defaults_and_drawer_aliases(self) -> None:
        bare = run_cli()
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertTrue(json.loads(bare.stdout)["ok"])

        unscoped = run_cli("audit", "--strict", "--no-cache")
        self.assertEqual(unscoped.returncode, 0, unscoped.stderr)
        self.assertTrue(json.loads(unscoped.stdout)["ok"])

        drawer = run_cli("eval", "--drawer", str(ROOT / "skills/skill-contract/references/eval-cases.json"), "--strict", "--no-cache")
        self.assertEqual(drawer.returncode, 0, drawer.stderr)
        self.assertEqual(json.loads(drawer.stdout)["cases_run"], 14)

    def test_root_normalization_and_comma_skill_selection(self) -> None:
        holder, workspace = self.make_workspace()
        try:
            other = workspace / "skills/other"
            (other / ".claude-plugin").mkdir(parents=True)
            (other / "SKILL.md").write_text("---\nname: other\ndescription: Other skill\n---\n", encoding="utf-8")
            (other / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "other", "description": "Other", "version": "1.0.0"}), encoding="utf-8")
            root = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "skill-contract,other", "--strict", "--no-cache")
            self.assertEqual(root.returncode, 0, root.stderr)
            self.assertEqual(json.loads(root.stdout)["skills"], 2)
        finally:
            holder.cleanup()

    def test_parser_error_is_self_disclosing_and_bounded(self) -> None:
        proc = run_cli("audit", "--definitely-invalid-flag")
        self.assertEqual(proc.returncode, 2)
        lines = [line for line in proc.stderr.splitlines() if line]
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].startswith("Reference: skills/skill-contract/references/valves-and-metabolism.md"))

    def test_hygiene_facts_soft_and_strict_modes(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="skill-contract-hygiene-")
        repo = Path(holder.name)
        try:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            tracked = repo / "tracked.txt"
            tracked.write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
            tracked.write_text("dirty   \n", encoding="utf-8")
            payload = run_cli("hygiene-facts", "--repo", str(repo), "--json")
            self.assertEqual(payload.returncode, 0)
            facts = json.loads(payload.stdout)
            self.assertFalse(facts["diff_clean"])
            self.assertEqual(facts["gate_tier"], "soft_advisory_exit_0")

            json_file = repo / "facts.json"
            json_file.write_text(json.dumps({"items": [1, {"ok": True}]}), encoding="utf-8")
            inspected = run_cli("hygiene-facts", "--repo", str(repo), "--json-file", str(json_file), "--no-check-diff")
            self.assertEqual(inspected.returncode, 0)
            self.assertEqual(json.loads(inspected.stdout)["json_summary"]["top_level_keys"], ["items"])

            strict = run_cli("hygiene-facts", "--repo", str(repo), "--json-file", "/tmp/nonexistent-skill-retro-missing.json", "--strict")
            self.assertEqual(strict.returncode, 1)
            self.assertLessEqual(len(strict.stderr.splitlines()), 2)
            self.assertIn("valves-and-metabolism.md", strict.stderr)
        finally:
            holder.cleanup()

    def test_watermark_versions_invalidate_after_manifest_edit(self) -> None:
        holder, workspace = self.make_workspace()
        try:
            first = run_cli("versions", "--root", str(workspace / "skills"), "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_skill = next(item for item in json.loads(first.stdout)["skills"] if item["skill"] == "skill-contract")
            expected_ver = json.loads((workspace / "skills/skill-contract/.claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
            self.assertEqual(first_skill["plugin_version"], expected_ver)
            manifest = workspace / "skills/skill-contract/.claude-plugin/plugin.json"
            document = json.loads(manifest.read_text(encoding="utf-8"))
            document["version"] = "1.2.3"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            second = run_cli("versions", "--root", str(workspace / "skills"), "--json")
            self.assertEqual(second.returncode, 0, second.stderr)
            second_skill = next(item for item in json.loads(second.stdout)["skills"] if item["skill"] == "skill-contract")
            self.assertEqual(second_skill["plugin_version"], "1.2.3")
        finally:
            holder.cleanup()

    def test_retro_probe_reports_counter_cases_and_no_doc_drift(self) -> None:
        probe = subprocess.run(
            [sys.executable, "skills/skill-retro/scripts/skill_retro.py", "probe", "--skill", "skill-contract", "--fast", "--json"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        skill = json.loads(probe.stdout)["skills"][0]
        self.assertGreaterEqual(skill["eval_counter_case_ratio"], 0.25)
        drift = skill.get("internal_doc_cli_drift_count", (skill.get("signals") or {}).get("internal_doc_cli_drift_count"))
        self.assertEqual(drift, 0)


if __name__ == "__main__":
    unittest.main()
