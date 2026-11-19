import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
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

    def test_tracked_binary_and_non_utf8_blobs_preserve_paths_without_crashing(self):
        holder, dev, handoff = self.fixture(clean=True)
        self.addCleanup(holder.cleanup)
        repo = dev / "Financial"
        (repo / "Assets").mkdir()
        (repo / "Assets" / "icon.bin").write_bytes(b"\\x89PNG\\r\\n\\x1a\\n\\x8d\\xff")
        (repo / "Assets" / "legacy.txt").write_bytes(b"route: legacy\\xff\\n")
        git("add", ".", cwd=repo)
        git("commit", "-qm", "add binary and non-utf8 files", cwd=repo)
        (handoff / "goal-a" / "spec.md").write_text("Repository: Financial\\nBinary asset: `Assets/icon.bin`\\nLegacy text: `Assets/legacy.txt`\\n", encoding="utf-8")
        report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["missing_repositories"], [])

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
        with self.assertRaises(ValueError):
            patrol.triage_findings([finding], mock_jev_json="not-json", jev_fn=lambda **kwargs: calls.append(kwargs) or (_ for _ in ()).throw(AssertionError("live Jev called")))
        self.assertEqual(calls, [])
        offline = patrol.triage_findings([finding], jev_fn=lambda **kwargs: None)
        self.assertTrue(offline["actionable"])

    def test_runner_suppresses_noise_and_persists_layer0_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "watermarks.json"
            collected = {
                "loop_id": "spec-code-drift-patrol",
                "has_delta": True,
                "delta_count": 1,
                "watermark": "spec-code-drift-patrol:stable",
                "actionable": False,
                "jev": {"decision_source": "mock", "actionable_findings": []},
            }
            first = runner.run_loop("spec-code-drift-patrol", target_date=date(2026, 11, 19), state_path=state, collector_fn=lambda spec, target_date: collected, dry_run=False)
            second = runner.run_loop("spec-code-drift-patrol", target_date=date(2026, 11, 19), state_path=state, collector_fn=lambda spec, target_date: collected, dry_run=False)
            self.assertEqual(first["status"], "SKIPPED_NOT_ACTIONABLE")
            self.assertEqual(second["status"], "SKIPPED_NO_DELTA")
            self.assertEqual(runner.load_watermarks(state)["spec-code-drift-patrol"]["watermark"], collected["watermark"])

    def test_foreign_repo_handoff_and_prose_slashes_are_not_scanned(self):
        holder, dev, handoff = self.fixture(clean=True)
        self.addCleanup(holder.cleanup)
        foreign = handoff / "foreign-goal"
        foreign.mkdir()
        (foreign / "spec.md").write_text("- **Repository**: LeePepe/user-skills\\nFinancial is only prose: `P0/P1`, `UI/UX`, `/howto`\\n", encoding="utf-8")
        report = patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))
        self.assertEqual(report["spec_stale_findings"], [])
        self.assertTrue(any(item["kind"] == "unassociated_handoff" for item in report["unsupported"]))

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
        second = runner._default_loop_collector(runner.resolve_loop_spec("spec-code-drift-patrol"), target_date=date(2026, 11, 19), dry_run=True)
        self.assertEqual(first["watermark"], second["watermark"])
        checked = patrol.loop_check(dev_root=dev, handoff_root=handoff, repos=("Financial",), target_date=date(2026, 1, 2), mock_jev_json={item["id"]: {"answers": {"drift_classification": {"choice": "CODE_VIOLATES_SPEC_BUG"}, "severity_confidence": {"noul": 0.9}}} for item in patrol.scan(dev_root=dev, handoff_root=handoff, repos=("Financial",))["findings"]})
        self.assertEqual(checked["title"], "[P3 / Priority 3][Spec-Code Drift] 2026-01-02 规格与代码一致性巡检清单")
        fake = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=json.dumps({"autopilots": [{"id": "ap-1", "title": "Daily Spec-Code Drift Patrol Autopilot (P3 -> Owner)"}]}), stderr="")
        self.assertEqual(runner._find_autopilot_id(runner.resolve_loop_spec("spec-code-drift-patrol"), server_url="", workspace_id="", runner=fake, multica_bin=None), "ap-1")


if __name__ == "__main__":
    unittest.main()
