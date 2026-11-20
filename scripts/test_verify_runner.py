#!/usr/bin/env python3
"""Focused unit tests for scripts/verify_runner.py and skill_contract.py self-disclosing guidance."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "scripts" / "verify_runner.py"
VERIFY_BIN = REPO_ROOT / "scripts" / "verify"
SKILL_CONTRACT_PATH = REPO_ROOT / "skills" / "skill-contract" / "scripts" / "skill_contract.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


verify_runner = _load_module("test_verify_runner_mod", RUNNER_PATH)
skill_contract = _load_module("test_skill_contract_guidance_mod", SKILL_CONTRACT_PATH)


class VerifyRunnerAndGuidanceTests(unittest.TestCase):
    def test_all_mode_covers_100_percent_of_unittest_methods_with_zero_lost_tests(self) -> None:
        # Count every test method via unittest.TestLoader across the 3 canonical discovery roots
        loader = unittest.TestLoader()
        discovered_count = 0
        for rel_dir in ("skills/code-review/scripts", "skills/design-studio/scripts", "scripts"):
            target_dir = REPO_ROOT / rel_dir
            suite = loader.discover(start_dir=str(target_dir), pattern="test_*.py", top_level_dir=str(target_dir))
            discovered_count += suite.countTestCases()

        tier0, wave = verify_runner.build_execution_plan(
            scope_mode="all",
            target_skills=[],
            jobs=8,
            repo_root=REPO_ROOT,
        )
        self.assertEqual([t.task_id for t in tier0], ["git-diff-check", "git-diff-cached-check", "skill-contract-audit"])
        self.assertTrue(any(t.task_id == "skill-contract-eval-all" for t in wave))

        planned_tests = sum(t.expected_tests for t in wave)
        self.assertEqual(
            planned_tests,
            discovered_count,
            f"--all plan expected {planned_tests} tests, but unittest discover found {discovered_count}",
        )
        self.assertGreaterEqual(planned_tests, 570)

    def test_classify_scope_distinguishes_isolated_skill_vs_shared_triggers(self) -> None:
        mode, targets, _ = verify_runner.classify_scope(["skills/retro-loop/SKILL.md"], repo_root=REPO_ROOT)
        self.assertEqual(mode, "scoped")
        self.assertEqual(targets, ["retro-loop"])

        mode, targets, _ = verify_runner.classify_scope(
            ["skills/issue-brief/SKILL.md", "skills/code-review/references/checklist.md"],
            repo_root=REPO_ROOT,
        )
        self.assertEqual(mode, "scoped")
        self.assertEqual(targets, ["code-review", "issue-brief"])

        # Shared scripts, CI workflows, hooks, root docs, clean tree, and skill_contract.py escalate to 'all'
        for full_trigger in (
            [],
            ["scripts/verify"],
            ["scripts/test_verify_skills.py"],
            [".github/workflows/ci.yml"],
            [".githooks/pre-push"],
            [".claude-plugin/marketplace.json"],
            ["AGENTS.md"],
            ["README.md"],
            ["skills/skill-contract/scripts/skill_contract.py"],
            ["skills/nonexistent-skill-xyz/SKILL.md"],
        ):
            with self.subTest(changed=full_trigger):
                m, t, _ = verify_runner.classify_scope(full_trigger, repo_root=REPO_ROOT)
                self.assertEqual(m, "all")
                self.assertEqual(t, [])

        m_forced, _, _ = verify_runner.classify_scope(
            ["skills/retro-loop/SKILL.md"],
            force_all=True,
            repo_root=REPO_ROOT,
        )
        self.assertEqual(m_forced, "all")

    def test_tiered_fast_fail_stops_before_wave_when_tier0_fails(self) -> None:
        tier0 = [
            verify_runner.TaskSpec("fast-pass", 0, "pass", (sys.executable, "-c", "raise SystemExit(0)")),
            verify_runner.TaskSpec("fast-fail", 0, "fail", (sys.executable, "-c", "import sys; sys.stderr.write('tier0 boom\\n'); raise SystemExit(1)")),
        ]
        wave = [
            verify_runner.TaskSpec("heavy-wave", 2, "wave", (sys.executable, "-c", "raise SystemExit(0)"), expected_tests=5),
        ]
        ok, results = verify_runner.execute_plan(tier0, wave, jobs=4, repo_root=REPO_ROOT)
        self.assertFalse(ok)
        self.assertEqual([r.spec.task_id for r in results], ["fast-pass", "fast-fail"])
        self.assertIn("tier0 boom", results[-1].stderr)

    def test_parallel_wave_preserves_deterministic_order_and_surfaces_failures(self) -> None:
        wave = [
            verify_runner.TaskSpec(
                "shard-a",
                2,
                "shard-a",
                (sys.executable, "-c", "import sys, time; time.sleep(0.05); sys.stderr.write('Ran 2 tests in 0.050s\\n')"),
                expected_tests=2,
                weight=10.0,
            ),
            verify_runner.TaskSpec(
                "shard-b",
                2,
                "shard-b",
                (sys.executable, "-c", "import sys; sys.stderr.write('Ran 3 tests in 0.001s\\n')"),
                expected_tests=3,
                weight=5.0,
            ),
        ]
        ok, results = verify_runner.execute_plan([], wave, jobs=2, repo_root=REPO_ROOT)
        self.assertTrue(ok)
        self.assertEqual([r.spec.task_id for r in results], ["shard-a", "shard-b"])
        self.assertEqual(sum(r.tests_ran for r in results), 5)

    def test_verify_cli_rejects_unknown_flags_with_exit_2(self) -> None:
        proc = subprocess.run(
            [str(VERIFY_BIN), "--unknown-flag-xyz"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)

    def test_skill_contract_audit_emits_self_disclosing_drawer_pointers_on_valve_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pkg = repo / "skills" / "demo-broken"
            (pkg / "references").mkdir(parents=True)
            # Trigger description-length (>320) + orphan-reference
            (pkg / "SKILL.md").write_text(
                "---\nname: demo-broken\ndescription: \"" + ("x" * 330) + "\"\n---\n\n# Demo\n",
                encoding="utf-8",
            )
            (pkg / "references" / "unlinked-guide.md").write_text("# Unlinked\n", encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(SKILL_CONTRACT_PATH), "audit", "--repo", str(repo), "--strict"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["guidance_drawer"], "skills/skill-contract/references/valves-and-metabolism.md")
            by_code = {f["code"]: f for f in payload["findings"]}
            self.assertIn("description-length", by_code)
            self.assertIn("orphan-reference", by_code)
            self.assertIn(
                "valves-and-metabolism.md#valve-3--budgets",
                by_code["description-length"]["reference"],
            )
            self.assertIn(
                "valves-and-metabolism.md#valve-4--sharing-and-metabolism",
                by_code["orphan-reference"]["reference"],
            )
            self.assertTrue(by_code["description-length"]["hint"])
            self.assertIn("valves-and-metabolism.md", proc.stderr)


if __name__ == "__main__":
    unittest.main()
