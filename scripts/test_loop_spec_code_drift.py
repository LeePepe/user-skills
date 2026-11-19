import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/codebase-design/scripts/spec_code_drift_patrol.py"
RUNNER = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


patrol = _load("test_spec_code_drift_patrol", SCRIPT)
runner = _load("test_spec_code_drift_runner", RUNNER)


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class SpecCodeDriftPatrolTests(unittest.TestCase):
    def fixture(self, *, clean: bool = False):
        holder = tempfile.TemporaryDirectory()
        base = Path(holder.name)
        dev = base / "Development"
        handoff = dev / "handoff" / "goal-a"
        repo = dev / "Financial"
        repo.mkdir(parents=True)
        handoff.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=repo)
        git("config", "user.email", "test@example.com", cwd=repo)
        git("config", "user.name", "Test", cwd=repo)
        (repo / "Sources").mkdir()
        (repo / "Tests").mkdir()
        (repo / "Sources" / "Router.swift").write_text("struct Router {}\n", encoding="utf-8")
        test_body = "func testRouter() { XCTAssertTrue(true) }\n"
        if clean:
            test_body += "func testRejectsInvalidInput() { XCTAssertThrowsError(try rejectInvalidInput()) }\n"
        (repo / "Tests" / "RouterTests.swift").write_text(test_body, encoding="utf-8")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "initial", cwd=repo)
        spec = """# Spec\nRepository: Financial\n\nContract tests: `Tests/RouterTests.swift`\nNegative assertion: rejected invalid input\nReferenced module: `Sources/Router.swift`\n"""
        if not clean:
            spec += "Deleted module: `Sources/Deleted.swift`\nDeprecated Test Clauses to Remove/Rewrite\n- keep old route assertion\n"
        (handoff / "spec.md").write_text(spec, encoding="utf-8")
        return holder, dev, handoff.parent

    def test_scan_reports_spec_staleness_and_keeps_dirty_feature_files_out(self):
        holder, dev, handoff = self.fixture()
        self.addCleanup(holder.cleanup)
        repo = dev / "Financial"
        (repo / "feature_only.swift").write_text("dirty feature\n", encoding="utf-8")
        report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))
        codes = {row["code"] for row in report["spec_stale_findings"]}
        self.assertIn("missing_referenced_path", codes)
        self.assertIn("deprecated_test_clause", codes)
        self.assertNotIn("feature_only.swift", json.dumps(report))

    def test_clean_fixture_and_noise_triage(self):
        holder, dev, handoff = self.fixture(clean=True)
        self.addCleanup(holder.cleanup)
        report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))
        self.assertEqual(report["findings"], [])
        verdict = patrol.triage_findings(
            [{"id": "x", "repo": "Financial", "direction": "code_compliance", "code": "x", "action_type": "FIX_CODE_BUG"}],
            mock_jev_json={"answers": {"drift_classification": {"choice": "BENIGN_NOISE"}, "severity_confidence": {"noul": 0.92}}},
        )
        self.assertFalse(verdict["actionable"])

    def test_jev_valid_grey_invalid_and_offline_paths(self):
        finding = {"id": "f1", "repo": "Financial", "direction": "spec_staleness", "code": "missing_referenced_path", "action_type": "SYNC_SPEC_DOC"}
        grey = patrol.triage_findings([finding], mock_jev_json={"answers": {"drift_classification": {"choice": "SPEC_OUTDATED_SYNC_DOC"}, "severity_confidence": {"noul": 0.5}}})
        self.assertTrue(grey["actionable"])
        self.assertEqual(grey["decisions"][0]["decision_source"], "deterministic_fallback_grey")
        calls = []
        invalid = patrol.triage_findings([finding], mock_jev_json="not-json", jev_fn=lambda **kwargs: calls.append(kwargs) or (_ for _ in ()).throw(AssertionError("live Jev called")))
        self.assertTrue(invalid["actionable"])
        self.assertEqual(calls, [])
        offline = patrol.triage_findings([finding], jev_fn=lambda **kwargs: None)
        self.assertTrue(offline["actionable"])

    def test_at011_and_runner_repeat_scan(self):
        holder, dev, handoff = self.fixture(clean=True)
        self.addCleanup(holder.cleanup)
        repo = dev / "Financial"
        (repo / "Sources" / "Root.swift").write_text("struct FinAppRootShell: View { var body: some View { NavigationStack { Content() }.overlay(alignment: .bottom) { floatingDock } } }\n", encoding="utf-8")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "add root", cwd=repo)
        (handoff / "goal-a" / "spec.md").write_text("Repository: Financial\nmacOS and iOS navigation contract\n", encoding="utf-8")
        report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))
        self.assertIn("AT-011", {row["code"] for row in report["code_compliance_findings"]})
        os.environ["SPEC_CODE_DRIFT_PATROL_DEV_ROOT"] = str(dev)
        os.environ["SPEC_CODE_DRIFT_PATROL_HANDOFF_ROOT"] = str(handoff)
        os.environ["SPEC_CODE_DRIFT_PATROL_REPOS"] = "Financial"
        self.addCleanup(lambda: [os.environ.pop(key, None) for key in ("SPEC_CODE_DRIFT_PATROL_DEV_ROOT", "SPEC_CODE_DRIFT_PATROL_HANDOFF_ROOT", "SPEC_CODE_DRIFT_PATROL_REPOS")])
        first = runner._default_loop_collector(runner.resolve_loop_spec("spec-code-drift-patrol"), target_date=__import__("datetime").date(2026, 11, 19), dry_run=True)
        second = runner._default_loop_collector(runner.resolve_loop_spec("spec-code-drift-patrol"), target_date=__import__("datetime").date(2026, 11, 19), dry_run=True)
        self.assertEqual(first["watermark"], second["watermark"])


if __name__ == "__main__":
    unittest.main()
