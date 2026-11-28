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
        self.assertGreaterEqual(json.loads(drawer.stdout)["cases_run"], 14)
        self.assertLessEqual(json.loads(drawer.stdout)["cases_run"], 15)

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

    def test_invalid_frontmatter_unquoted_colon_fails_strict_audit(self) -> None:
        holder, workspace = self.make_workspace()
        try:
            bad = workspace / "skills/bad-yaml"
            (bad / ".claude-plugin").mkdir(parents=True)
            (bad / ".claude-plugin/plugin.json").write_text(
                json.dumps({"name": "bad-yaml", "description": "Valid JSON description.", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (bad / "SKILL.md").write_text(
                "---\nname: bad-yaml\ndescription: 3-stage Skill: unquoted colon breaks YAML parsers.\n---\n# Bad\n",
                encoding="utf-8",
            )
            failed = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "bad-yaml", "--strict", "--no-cache")
            self.assertNotEqual(failed.returncode, 0)
            findings = json.loads(failed.stdout)["findings"]
            self.assertTrue(any(f["code"] == "invalid-frontmatter" for f in findings), findings)

            (bad / "SKILL.md").write_text(
                '---\nname: bad-yaml\ndescription: "3-stage Skill: quoted colon parses cleanly."\n---\n# Good\n',
                encoding="utf-8",
            )
            passed = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "bad-yaml", "--strict", "--no-cache")
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertTrue(json.loads(passed.stdout)["ok"])
        finally:
            holder.cleanup()

    def test_layer_a_soft_advisories_malformed_fence_namespace_and_table_decay(self) -> None:
        holder, workspace = self.make_workspace()
        try:
            pkg = workspace / "skills/hygiene-demo"
            (pkg / ".claude-plugin").mkdir(parents=True)
            (pkg / "references").mkdir(parents=True)
            (pkg / "scripts/demo_core").mkdir(parents=True)
            (pkg / ".claude-plugin/plugin.json").write_text(
                json.dumps({"name": "hygiene-demo", "description": "Demo skill for Layer A advisories.", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (pkg / "SKILL.md").write_text(
                "---\nname: hygiene-demo\ndescription: Demo skill for Layer A advisories.\n---\n"
                "# Hygiene Demo\n\n"
                "See [guide.md](references/guide.md).\n\n"
                "Inline quadruple-backtick example (e.g., ```` ```bash swift test ``` ````) must NOT trigger.\n\n"
                "~~~~markdown\n"
                "```bash inside_outer_tilde_fence ```\n"
                "~~~~\n\n"
                "```bash swift build --package-path Packages/Demo ```\n",
                encoding="utf-8",
            )
            repeated_cell = "deterministic pass/fail contract invariant"
            table_rows = "\n".join(
                f"| N-{idx:02d} | {repeated_cell if idx < 5 else f'distinct row {idx} value'} |"
                for idx in range(8)
            )
            (pkg / "references/guide.md").write_text(
                "# Guide\n\n"
                "| Node | Gate |\n"
                "|---|---|\n"
                f"{table_rows}\n\n"
                "```bash xcodebuild -list -json ```\n",
                encoding="utf-8",
            )
            (pkg / "scripts/demo_core/__init__.py").write_text("", encoding="utf-8")
            (pkg / "scripts/demo_core/mod_a.py").write_text(
                "def _shared_helper(x):\n"
                "    d = {}\n"
                "    d.setdefault('ok', x)\n"
                "    globals().setdefault('INJECTED', 1)\n"
                "    return d\n",
                encoding="utf-8",
            )
            (pkg / "scripts/demo_core/mod_b.py").write_text(
                "import sys\n"
                "def _shared_helper(x):\n"
                "    sys.__dict__.setdefault('_polluted', True)\n"
                "    return x\n",
                encoding="utf-8",
            )

            audited = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "hygiene-demo", "--strict", "--no-cache")
            self.assertEqual(audited.returncode, 0, audited.stderr)
            payload = json.loads(audited.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["findings"], [])
            advisories = payload["advisories"]
            for adv in advisories:
                self.assertEqual(adv["tier"], "soft_advisory_exit_0")
            fences = [a for a in advisories if a["code"] == "malformed-code-fence"]
            self.assertEqual(len(fences), 2, fences)
            pollution = [a for a in advisories if a["code"] == "ast-namespace-pollution"]
            self.assertEqual(len(pollution), 3, pollution)
            kinds = {a["kind"] for a in pollution}
            self.assertEqual(kinds, {"namespace_setdefault", "duplicate_core_helper"})
            decay = [a for a in advisories if a["code"] == "boilerplate-table-decay"]
            self.assertEqual(len(decay), 1, decay)
            self.assertEqual(decay[0]["rows"], 8)
            self.assertEqual(decay[0]["repeated_rows"], 5)

            # Counter-case: clean multiline fences, ordinary dict.setdefault, distinct helpers, and <50% repetition emit 0 advisories
            (pkg / "SKILL.md").write_text(
                "---\nname: hygiene-demo\ndescription: Demo skill for Layer A advisories.\n---\n"
                "# Hygiene Demo\n\nSee [guide.md](references/guide.md).\n\n"
                "```bash\nswift build --package-path Packages/Demo\n```\n",
                encoding="utf-8",
            )
            clean_rows = "\n".join(f"| N-{idx:02d} | distinct verification contract {idx:02d} |" for idx in range(8))
            (pkg / "references/guide.md").write_text(
                f"# Guide\n\n| Node | Gate |\n|---|---|\n{clean_rows}\n",
                encoding="utf-8",
            )
            (pkg / "scripts/demo_core/mod_a.py").write_text(
                "def _helper_a(x):\n    bucket = {}\n    return bucket.setdefault('k', x)\n",
                encoding="utf-8",
            )
            (pkg / "scripts/demo_core/mod_b.py").write_text(
                "def _helper_b(x):\n    return x\n",
                encoding="utf-8",
            )
            clean = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "hygiene-demo", "--strict", "--no-cache")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            clean_payload = json.loads(clean.stdout)
            self.assertEqual(clean_payload["findings"], [])
            self.assertEqual(clean_payload["advisories"], [])
        finally:
            holder.cleanup()

    def test_vacuous_good_snippet_and_fake_prose_echo_test_soft_advisories(self) -> None:
        holder, workspace = self.make_workspace()
        try:
            pkg = workspace / "skills/eval-hygiene-demo"
            (pkg / ".claude-plugin").mkdir(parents=True)
            (pkg / "references").mkdir(parents=True)
            (pkg / "scripts/data/defect-cases").mkdir(parents=True)
            (pkg / ".claude-plugin/plugin.json").write_text(
                json.dumps({"name": "eval-hygiene-demo", "description": "Demo skill for eval drawer soft advisories.", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (pkg / "SKILL.md").write_text(
                "---\nname: eval-hygiene-demo\ndescription: Demo skill for eval drawer soft advisories.\n---\n"
                "# Eval Hygiene Demo\n\nSee [eval-cases.json](references/eval-cases.json).\n",
                encoding="utf-8",
            )
            (pkg / "references/eval-cases.json").write_text(
                json.dumps(
                    {
                        "schema": "skill-eval-cases/v1",
                        "version": 1,
                        "active_cases": [
                            {
                                "id": "EV-001",
                                "title": "Trivial placeholder good_snippet triggers vacuous-good-snippet",
                                "tags": ["ui", "counter-case"],
                                "forbidden_regex": "VStack\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)[\\s\\S]*?Card\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)",
                                "bad_snippet": 'VStack { Text("19°C"); Card { Text("19°C") } }',
                                "good_snippet": 'Text("Clean")',
                            },
                            {
                                "id": "EV-002",
                                "title": "Static Markdown prose-echo only case triggers fake-prose-echo-test",
                                "tags": ["prose"],
                                "file": "SKILL.md",
                                "must_contain": ["Eval Hygiene Demo"],
                            },
                            {
                                "id": "EV-003",
                                "title": "Shared-token adversarial twin does NOT trigger vacuous-good-snippet",
                                "tags": ["ui", "counter-case"],
                                "forbidden_regex": "VStack\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)[\\s\\S]*?Card\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)",
                                "bad_snippet": 'VStack { Text("19°C"); Card { Text("19°C") } }',
                                "good_snippet": 'if isMetric { Text("19°C") } else { Text("66°F") }',
                            },
                            {
                                "id": "EV-004",
                                "title": "Executable CLI cmd + file check does NOT trigger fake-prose-echo-test",
                                "tags": ["cli"],
                                "cmd": "python3 -c 'print(1)'",
                                "expect_exit": 0,
                                "file": "SKILL.md",
                                "must_contain": ["Eval Hygiene Demo"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (pkg / "scripts/data/defect-cases/index.json").write_text(
                json.dumps(
                    {
                        "schema": "apple-design-defect-cases/v1",
                        "active_cases": [
                            {
                                "id": "DC-001",
                                "platforms": ["mobile"],
                                "dimensions": ["density"],
                                "containers": ["card"],
                                "title": "Defect drawer zero-shared-token snippet also surfaces vacuous-good-snippet",
                                "regex": "duplicateMetricBadge\\(",
                                "bad_snippet": "HeroCard { duplicateMetricBadge(primaryValue) }",
                                "good_snippet": "EmptyStatePlaceholderView(statusMessage: ok)",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            audited = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "eval-hygiene-demo", "--strict", "--no-cache")
            self.assertEqual(audited.returncode, 0, audited.stderr)
            payload = json.loads(audited.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["findings"], [])

            advisories = payload["advisories"]
            for adv in advisories:
                self.assertEqual(adv["tier"], "soft_advisory_exit_0")
                self.assertIn("valves-and-metabolism.md", adv["reference"])
                self.assertTrue(adv["hint"])

            vacuous = [a for a in advisories if a["code"] == "vacuous-good-snippet"]
            self.assertEqual({a["id"] for a in vacuous}, {"EV-001", "DC-001"}, vacuous)
            prose_echo = [a for a in advisories if a["code"] == "fake-prose-echo-test"]
            self.assertEqual([a["id"] for a in prose_echo], ["EV-002"], prose_echo)

            # Counter-case: upgrading EV-001/DC-001 to shared-token adversarial twins and EV-002 to executable cmd clears all advisories
            (pkg / "references/eval-cases.json").write_text(
                json.dumps(
                    {
                        "schema": "skill-eval-cases/v1",
                        "version": 1,
                        "active_cases": [
                            {
                                "id": "EV-003",
                                "title": "Shared-token adversarial twin",
                                "tags": ["ui", "counter-case"],
                                "forbidden_regex": "VStack\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)[\\s\\S]*?Card\\s*\\{[\\s\\S]*?Text\\(\"19°C\"\\)",
                                "bad_snippet": 'VStack { Text("19°C"); Card { Text("19°C") } }',
                                "good_snippet": 'if isMetric { Text("19°C") } else { Text("66°F") }',
                            },
                            {
                                "id": "EV-004",
                                "title": "Executable CLI cmd + file check",
                                "tags": ["cli"],
                                "cmd": "python3 -c 'print(1)'",
                                "expect_exit": 0,
                                "file": "SKILL.md",
                                "must_contain": ["Eval Hygiene Demo"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (pkg / "scripts/data/defect-cases/index.json").write_text(
                json.dumps(
                    {
                        "schema": "apple-design-defect-cases/v1",
                        "active_cases": [
                            {
                                "id": "DC-001",
                                "platforms": ["mobile"],
                                "dimensions": ["density"],
                                "containers": ["card"],
                                "title": "Shared-token twin in defect drawer",
                                "regex": "HeroCard\\s*\\{\\s*duplicateMetricBadge",
                                "bad_snippet": "HeroCard { duplicateMetricBadge(primaryValue) }",
                                "good_snippet": "CompactSummaryRow { singleMetricBadge(primaryValue) }",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            clean = run_cli("audit", "--root", str(workspace / "skills"), "--skills", "eval-hygiene-demo", "--strict", "--no-cache")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            clean_payload = json.loads(clean.stdout)
            self.assertEqual(clean_payload["findings"], [])
            self.assertEqual(clean_payload["advisories"], [])

            # Also verify skill-contract and skill-retro themselves have 0 vacuous-good-snippet or fake-prose-echo-test advisories
            live_audit = run_cli("audit", "--skills", "skill-contract,skill-retro", "--strict", "--no-cache")
            self.assertEqual(live_audit.returncode, 0, live_audit.stderr)
            live_eval_hygiene = [
                a for a in json.loads(live_audit.stdout)["advisories"]
                if a["code"] in {"vacuous-good-snippet", "fake-prose-echo-test"}
            ]
            self.assertEqual(live_eval_hygiene, [])
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
