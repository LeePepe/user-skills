#!/usr/bin/env python3
"""Focused regression tests for dual-tier budgets and L1 routing hygiene."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "skills/skill-contract/scripts/skill_contract.py"
SKILLS = ROOT / "skills"


class DualTierValvesAndRoutingTests(unittest.TestCase):
    def _run(self, *args: str, cwd: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(CONTRACT), *args],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        return result, payload

    def _package(self, root: Path, name: str = "demo") -> Path:
        package = root / "skills" / name
        (package / "references").mkdir(parents=True)
        (package / "scripts").mkdir()
        (package / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo\n---\n\n"
            "[human](references/notes.md) [data](references/data.json)\n",
            encoding="utf-8",
        )
        return package

    def test_soft_advisories_are_non_failing_and_hygiene_facts_expose_them(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = self._package(root)
            (package / "SKILL.md").write_text(
                package.joinpath("SKILL.md").read_text(encoding="utf-8") + ("x" * (13 * 1024)),
                encoding="utf-8",
            )
            (package / "references" / "notes.md").write_text("n" * (13 * 1024), encoding="utf-8")
            (package / "references" / "data.json").write_text(
                json.dumps({"active_cases": [], "padding": "x" * (17 * 1024)}), encoding="utf-8"
            )
            for suffix in ("py", "sh", "ts"):
                (package / "scripts" / f"soft.{suffix}").write_text("pass\n" * 801, encoding="utf-8")

            result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["findings"], [])
            codes = {item["code"] for item in payload["advisories"]}
            self.assertTrue({"l0-bytes-soft", "l1-bytes-soft", "data-drawer-bytes-soft", "script-lines-soft"} <= codes)

            facts_result, facts = self._run("hygiene-facts", "--repo", str(root), "--no-check-diff", "--json")
            self.assertEqual(facts_result.returncode, 0, facts_result.stderr)
            self.assertTrue(facts["advisories"])
            self.assertEqual({item["code"] for item in facts["advisories"]}, codes)

    def test_non_exempt_script_caps_are_cli_gated_per_suffix(self) -> None:
        for suffix in ("py", "sh", "ts"):
            relative = f"skills/demo/scripts/fixture.{suffix}"
            with self.subTest(suffix=suffix, lines=1000), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                package = self._package(root)
                script = package / "scripts" / f"fixture.{suffix}"
                script.write_text("x\n" * 1000, encoding="utf-8")
                result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["findings"], [])
                advisories = [
                    item for item in payload["advisories"]
                    if item["code"] == "script-lines-soft" and item["file"] == relative
                ]
                self.assertEqual(len(advisories), 1)

            with self.subTest(suffix=suffix, lines=1001), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                package = self._package(root)
                script = package / "scripts" / f"fixture.{suffix}"
                script.write_text("x\n" * 1001, encoding="utf-8")
                result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(len(payload["findings"]), 1)
                finding = payload["findings"][0]
                self.assertEqual(finding["code"], "script-lines")
                self.assertEqual(finding["script"], relative)

    def test_real_legacy_monolith_caps_are_cli_ratcheted(self) -> None:
        spec = importlib.util.spec_from_file_location("dual_tier_contract", CONTRACT)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        core_facts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_facts)

        for relative in (
            "skills/skill-contract/scripts/skill_contract.py",
            "skills/skill-retro/scripts/skill_retro.py",
            "skills/agent-retro/scripts/agent_retro.py",
        ):
            self.assertNotIn(relative, core_facts.LEGACY_SCRIPT_LINE_CAPS)

        legacy_caps = {
            "skills/team-lead/scripts/herdr-dispatch.sh": 1269,
            "skills/m2-dispatcher/scripts/skill-slash-expander.ts": 1058,
        }
        for relative, cap in legacy_caps.items():
            source = ROOT / relative
            self.assertEqual(len(source.read_text(encoding="utf-8").splitlines()), cap)
            with self.subTest(script=relative, lines=cap), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                package = root / Path(relative).parents[1]
                (package / "references").mkdir(parents=True)
                (package / "scripts").mkdir()
                (package / "SKILL.md").write_text("---\ndescription: demo\n---\n", encoding="utf-8")
                target = root / relative
                shutil.copyfile(source, target)

                result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["findings"], [])

                target.write_text(target.read_text(encoding="utf-8") + "growth\n", encoding="utf-8")
                result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(len(payload["findings"]), 1)
                finding = payload["findings"][0]
                self.assertEqual(finding["code"], "script-monolith-growth")
                self.assertEqual(finding["script"], relative)
                self.assertEqual(finding["cap"], cap)

    def test_long_single_line_prose_is_not_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / "skills" / "prose"
            package.mkdir(parents=True)
            paragraph = "A dense prose paragraph remains readable to the model. " * 10
            (package / "SKILL.md").write_text(f"---\ndescription: prose\n---\n{paragraph}\n", encoding="utf-8")
            result, payload = self._run("audit", "--repo", str(root), "--all", "--strict", "--no-cache")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["findings"], [])
            self.assertEqual(payload["advisories"], [])

    def test_operational_routing_rows_do_not_use_eval_drawer(self) -> None:
        route_terms = ("Reviewer", "Role: Reviewer", "M1-TL", "Role: M1-TL", "Designer", "primary task")
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "|" in line and "eval-cases.json" in line and any(term in line for term in route_terms):
                    self.fail(f"operational row routes to eval drawer: {path}: {line}")

        expected_markers = {
            "team-lead": "runtime-profiles.yaml",
            "m2-dispatcher": "m2-domain-map.json",
            "personal-m2-assistant": "m2-domain-map.json",
            "skill-retro": "uptake_map.json",
        }
        marker = "Script-Only Data Drawer"
        for skill, drawer in expected_markers.items():
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(drawer, text)
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
