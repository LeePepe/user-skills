#!/usr/bin/env python3
"""Regression tests for team-lead modular linear-sync facade, SKILL.md <=12KB budget, and CLI flag aliases."""
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

os.environ.setdefault("GTT_LINEAR_SYNC", "0")

ROOT = Path(__file__).resolve().parents[1]
TEAM_LEAD_DIR = ROOT / "skills" / "team-lead"
SCRIPTS_DIR = TEAM_LEAD_DIR / "scripts"
SKILL_MD = TEAM_LEAD_DIR / "SKILL.md"
LINEAR_SYNC = SCRIPTS_DIR / "linear-sync.py"
CORE_DIR = SCRIPTS_DIR / "linear_sync_core"
GOAL_SUPERVISOR = SCRIPTS_DIR / "goal-supervisor.py"
GOAL_QUEUE = SCRIPTS_DIR / "goal-queue.py"
FEEDBACK_GUARD = SCRIPTS_DIR / "feedback_guard.py"
SKILL_CONTRACT = ROOT / "skills" / "skill-contract" / "scripts" / "skill_contract.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TeamLeadModularTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.linear_sync = _load_module("test_team_lead_modular_linear_sync", LINEAR_SYNC)
        cls.goal_supervisor = _load_module("test_team_lead_modular_goal_supervisor", GOAL_SUPERVISOR)
        cls.goal_queue = _load_module("test_team_lead_modular_goal_queue", GOAL_QUEUE)
        cls.feedback_guard = _load_module("test_team_lead_modular_feedback_guard", FEEDBACK_GUARD)

    def test_linear_sync_facade_core_budgets_and_runtime_symlink(self) -> None:
        facade_source = LINEAR_SYNC.read_text(encoding="utf-8")
        self.assertLessEqual(len(facade_source.splitlines()), 90)
        self.assertIn("Path(__file__).resolve().parent", facade_source)

        core_files = sorted(CORE_DIR.glob("*.py"))
        self.assertGreaterEqual(len(core_files), 6)
        forbidden = ("zlib", "base64", "exec(", "eval(")
        for path in core_files:
            source = path.read_text(encoding="utf-8")
            self.assertLessEqual(len(source.splitlines()), 750, f"{path.name} exceeds 750 lines")
            for token in forbidden:
                self.assertNotIn(token, source, f"Forbidden anti-cheat token {token!r} in {path.name}")

        runtime_link = SCRIPTS_DIR / "skill_runtime"
        self.assertTrue(runtime_link.is_symlink())
        self.assertEqual(runtime_link.readlink().as_posix(), "../../skill-contract/scripts/skill_runtime")
        self.assertTrue((runtime_link / "cli_contract.py").is_file())

    def test_mock_patch_on_facade_propagates_to_core_functions_and_methods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_tmp = Path(tmp).resolve()
            cred_file = real_tmp / "creds.json"
            with mock.patch.object(self.linear_sync, "_credential_path", return_value=cred_file), \
                 mock.patch.object(self.linear_sync, "_clip_credential_path", return_value=real_tmp / "clip.json"), \
                 mock.patch.dict(os.environ, {}, clear=True):
                self.linear_sync.save_credentials("Bearer tok-patched", actor="app")
                self.assertEqual(self.linear_sync.load_api_key(), "Bearer tok-patched")
                self.assertTrue(self.linear_sync._oauth_app_enabled())

            class FakeResp:
                def read(self) -> bytes:
                    return json.dumps({"data": {"viewer": {"id": "u1"}}}).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args) -> bool:
                    return False

            with mock.patch.object(self.linear_sync, "urlopen", return_value=FakeResp()) as patched_open:
                client = self.linear_sync.GraphQLClient("Bearer live-tok", allow_refresh=False)
                data = client.execute("{ viewer { id } }")
                self.assertEqual(data["viewer"]["id"], "u1")
                self.assertTrue(patched_open.called)

    def test_skill_md_and_references_respect_byte_and_line_budgets(self) -> None:
        skill_bytes = SKILL_MD.stat().st_size
        self.assertLessEqual(skill_bytes, 12_000, f"SKILL.md is {skill_bytes}B (must be <= 12,000B)")

        for ref_md in sorted((TEAM_LEAD_DIR / "references").glob("*.md")):
            lines = len(ref_md.read_text(encoding="utf-8").splitlines())
            size = ref_md.stat().st_size
            self.assertLessEqual(lines, 160, f"{ref_md.name} exceeds 160 lines ({lines})")
            self.assertLessEqual(size, 16 * 1024, f"{ref_md.name} exceeds 16KB ({size}B)")

        audit = subprocess.run(
            [sys.executable, str(SKILL_CONTRACT), "audit", "--skill", "team-lead", "--strict", "--no-cache"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(audit.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])
        advisory_codes = {item["code"] for item in payload.get("advisories", [])}
        self.assertNotIn("l0-bytes-soft", advisory_codes)
        self.assertNotIn("l1-bytes-soft", advisory_codes)
        self.assertLessEqual((TEAM_LEAD_DIR / "references/bots-and-agents.md").stat().st_size, 12_288)

    def test_writer_routing_agrees_on_leaf_drawer_and_pre_review_gate(self) -> None:
        for path, drawer in ((SKILL_MD, "references/bots-and-agents.md"),
                             (ROOT / "skills/m2-dispatcher/SKILL.md", "../team-lead/references/bots-and-agents.md")):
            rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("| Role: Writer |")]
            self.assertEqual(rows, [f"| Role: Writer | `{drawer}` | `feedback_guard.py pre-review-check` |"])

    def test_owned_paths_preserve_project_directories_not_repository_metadata(self) -> None:
        paths = ("Resources/Assets", "Sources/Core", "App/Views", "Models/Domain", "Modules/Auth", "MyPkg/sub-module")
        brief = "- **Repository**: `SomeOrg/another-repo`\n- **Owned Paths**: `LeePepe/user-skills`\n"
        brief += "".join(f"  - `{path}`\n" for path in paths)
        brief += "- **Interfaces**: `other/not-owned`\n"
        self.assertEqual(self.feedback_guard.extract_owned_paths_from_brief(brief), list(paths))
        self.assertEqual(self.feedback_guard._normalize_owned_token("octo/repo"), "")
        for path in paths:
            self.assertTrue(self.feedback_guard._matches_owned_path(f"{path}/file.swift", paths))
        self.assertFalse(self.feedback_guard._matches_owned_path("Sources/CoreExtra/file.swift", paths))

    def test_cli_flag_aliases_and_self_disclosing_parsers(self) -> None:
        from skill_runtime.cli_contract import SelfDisclosingArgumentParser

        self.assertTrue(issubclass(self.linear_sync._SelfDisclosingLinearSyncParser, SelfDisclosingArgumentParser))
        self.assertTrue(issubclass(self.goal_supervisor._SelfDisclosingSupervisorParser, SelfDisclosingArgumentParser))
        self.assertTrue(issubclass(self.goal_queue._SelfDisclosingQueueParser, SelfDisclosingArgumentParser))
        self.assertTrue(issubclass(self.feedback_guard._SelfDisclosingFeedbackGuardParser, SelfDisclosingArgumentParser))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff_dir = tmp_path / "handoff" / "alias-goal"
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "spec.md").write_text("# Spec\n\n## Owned Paths\n- `skills/team-lead/**`\n", encoding="utf-8")
            (handoff_dir / "ledger.md").write_text(
                "# Goal: alias-goal\n- TL: alias-goal-tl-r1\n"
                "2026-11-25T10:00:00Z | EVT=GOAL_START | id=E1\n",
                encoding="utf-8",
            )
            agents_json = tmp_path / "agents.json"
            agents_json.write_text(json.dumps({"agents": []}), encoding="utf-8")

            for flag in ("--dir", "--handoff", "--root"):
                q_res = subprocess.run(
                    [sys.executable, str(GOAL_QUEUE), "status", flag, str(handoff_dir), "--json"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(q_res.returncode, 0, f"goal-queue {flag} failed: {q_res.stderr}")
                self.assertTrue(json.loads(q_res.stdout)["ok"])

                sup_res = subprocess.run(
                    [
                        sys.executable,
                        str(GOAL_SUPERVISOR),
                        "observe",
                        flag,
                        str(handoff_dir),
                        "--herdr-json",
                        str(agents_json),
                        "--json",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(sup_res.returncode, 0, f"goal-supervisor {flag} failed: {sup_res.stderr}")
                self.assertEqual(json.loads(sup_res.stdout)["goal_id"], "alias-goal")

                ls_res = subprocess.run(
                    [sys.executable, str(LINEAR_SYNC), "sync", flag, str(handoff_dir), "--mode", "observe", "--json"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(ls_res.returncode, 0, f"linear-sync {flag} failed: {ls_res.stderr}")
                self.assertTrue(json.loads(ls_res.stdout)["ok"])

            for flag in ("--dir", "--root"):
                fg_diff = subprocess.run(
                    [sys.executable, str(FEEDBACK_GUARD), "diff-check", flag, str(ROOT)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(fg_diff.returncode, 0, f"feedback_guard diff-check {flag} failed: {fg_diff.stderr}")
                self.assertTrue(json.loads(fg_diff.stdout)["ok"])

                fg_rec = subprocess.run(
                    [sys.executable, str(FEEDBACK_GUARD), "list-receipts", flag, str(handoff_dir)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(fg_rec.returncode, 0, f"feedback_guard list-receipts {flag} failed: {fg_rec.stderr}")
                self.assertTrue(json.loads(fg_rec.stdout)["ok"])

            for script, cmd in (
                (GOAL_SUPERVISOR, ["status", "--unknown-flag-xyz"]),
                (GOAL_QUEUE, ["status", "--unknown-flag-xyz"]),
                (FEEDBACK_GUARD, ["pre-review-check", "--unknown-flag-xyz"]),
                (LINEAR_SYNC, ["sync-ledger", "--unknown-flag-xyz"]),
            ):
                bad = subprocess.run(
                    [sys.executable, str(script), *cmd],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(bad.returncode, 2)
                lines = [ln for ln in bad.stderr.strip().splitlines() if ln.strip()]
                self.assertLessEqual(len(lines), 3, f"{script.name} stderr exceeded 3 lines: {bad.stderr!r}")
                self.assertIn("skills/team-lead/references/", bad.stderr)


if __name__ == "__main__":
    unittest.main()
