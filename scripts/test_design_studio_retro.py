#!/usr/bin/env python3
"""Dual-Case Replay & Progressive Evolution Regression Tests for `skills/design-studio`."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
DESIGN_STUDIO_DIR = SKILLS_DIR / "design-studio"
DESIGN_CATALOG_PATH = DESIGN_STUDIO_DIR / "scripts" / "design_catalog.py"
SKILL_RETRO_PATH = SKILLS_DIR / "skill-retro" / "scripts" / "skill_retro.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DESIGN_CATALOG = _load_module("test_design_studio_retro_catalog", DESIGN_CATALOG_PATH)
SKILL_RETRO = _load_module("test_design_studio_retro_skill_retro", SKILL_RETRO_PATH)


def _run_catalog_inproc(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    out_buf, err_buf = io.StringIO(), io.StringIO()
    env_ctx = mock.patch.dict(os.environ, env, clear=False) if env is not None else contextlib.nullcontext()
    with env_ctx, contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        try:
            rc = DESIGN_CATALOG.main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
    return subprocess.CompletedProcess(
        args=[sys.executable, str(DESIGN_CATALOG_PATH), *args],
        returncode=int(rc or 0),
        stdout=out_buf.getvalue(),
        stderr=err_buf.getvalue(),
    )


class DesignStudioRetroReplayTests(unittest.TestCase):
    """Positive + Counter-Case Replays for L0/L1/L2/L3/creative/eval remediations in design-studio."""

    def test_no_arg_exit2_self_disclosing_error_vs_positive_query_and_stats(self) -> None:
        # Real subprocess Counter-Case: running design_catalog.py with no args exits 2 without raw Traceback
        no_arg = subprocess.run(
            [sys.executable, str(DESIGN_CATALOG_PATH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(no_arg.returncode, 2)
        self.assertNotIn("Traceback", no_arg.stderr)
        err_lines = [line for line in no_arg.stderr.strip().splitlines() if line.strip()]
        self.assertLessEqual(len(err_lines), 3, no_arg.stderr)
        for ref in (
            "skills/design-studio/references/toolchain-20-matrix.md",
            "skills/design-studio/references/remotion-video.md",
            "skills/design-studio/references/eval-cases.json",
        ):
            self.assertIn(ref, no_arg.stderr)

        # Positive Case 1: top-level --stats exits 0 with valid JSON
        stats_res = _run_catalog_inproc("--stats")
        self.assertEqual(stats_res.returncode, 0, stats_res.stderr)
        stats_payload = json.loads(stats_res.stdout)
        self.assertEqual(stats_payload["schema"], "design-studio-stats/v1")
        self.assertTrue(stats_payload["ok"])

        # Positive Case 2: top-level -q filter exits 0 with ranked matches
        query_res = _run_catalog_inproc("-q", "filter")
        self.assertEqual(query_res.returncode, 0, query_res.stderr)
        query_payload = json.loads(query_res.stdout)
        self.assertTrue(query_payload["ok"])
        self.assertTrue(query_payload["matches"])

    def test_surface_alias_and_positional_query_positive_and_counter_cases(self) -> None:
        # Positive: --surface ios + positional query on vault-search normalizes to mobile
        vs_res = _run_catalog_inproc("vault-search", "gesture", "--surface", "ios", "--top", "2")
        self.assertEqual(vs_res.returncode, 0, vs_res.stderr)
        vs_data = json.loads(vs_res.stdout)
        self.assertEqual(vs_data["platforms_queried"], ["mobile"])
        self.assertLessEqual(len(vs_data["matches"]), 2)

        # Positive: --surface ios + positional query on search normalizes to mobile
        s_res = _run_catalog_inproc("search", "滑动确认", "--surface", "ios")
        self.assertEqual(s_res.returncode, 0, s_res.stderr)
        s_data = json.loads(s_res.stdout)
        self.assertTrue(s_data["matches"])

        # Positive: --surface ios on classify scopes classified platform to mobile
        cls_res = _run_catalog_inproc("classify", "--text", "glass card layout", "--surface", "ios", "--no-jev")
        self.assertEqual(cls_res.returncode, 0, cls_res.stderr)
        cls_data = json.loads(cls_res.stdout)
        self.assertEqual(cls_data["platform_filter"], "mobile")
        self.assertEqual(cls_data["results"][0]["platforms"], ["mobile"])

        # Positive: --surface video on recommend with --platform macos filters Video/Offline
        rec_res = _run_catalog_inproc("recommend", "--platform", "macos", "--surface", "video")
        self.assertEqual(rec_res.returncode, 0, rec_res.stderr)
        rec_items = json.loads(rec_res.stdout)["recommendations"]
        self.assertTrue(all("Video/Offline" in item["surface_modes"] for item in rec_items))

        # Positive: --surface ios on scaffold-gallery normalizes to mobile
        with tempfile.TemporaryDirectory() as tmp:
            gal_res = _run_catalog_inproc(
                "scaffold-gallery", "--goal", "surface-alias", "--round", "1",
                "--surface", "ios", "--no-blog-mount",
                env=dict(os.environ, HOME=tmp),
            )
            self.assertEqual(gal_res.returncode, 0, gal_res.stderr)
            gal_manifest = json.loads(gal_res.stdout)["manifest"]
            self.assertEqual(gal_manifest["platform"], "mobile")

        # Counter-Cases: invalid --surface and missing search query exit 2 with self-disclosing pointer
        for bad_args in (
            ("vault-search", "--surface", "invalid-surface", "--query", "gesture"),
            ("recommend", "--surface", "invalid-surface"),
            ("search",),
        ):
            with self.subTest(bad_args=bad_args):
                bad_res = _run_catalog_inproc(*bad_args)
                self.assertEqual(bad_res.returncode, 2)
                self.assertNotIn("Traceback", bad_res.stderr)
                self.assertIn("skills/design-studio/references/eval-cases.json", bad_res.stderr)

    def test_l3_stats_inspect_vault_and_ceremonial_helper_entrypoints(self) -> None:
        # Positive: stats and inspect-vault return soft_exit_0 with 2+1+1 creative spectrum facts
        for subcmd in ("stats", "inspect-vault"):
            res = _run_catalog_inproc(subcmd, "--surface", "macos", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["schema"], "design-studio-stats/v1")
            self.assertEqual(payload["advisory_mode"], "soft_exit_0")
            self.assertEqual(payload["platform_filter"], "desktop")
            self.assertGreaterEqual(payload["catalog"]["tools_count"], 20)
            self.assertGreaterEqual(payload["eval_cases_count"], 8)
            self.assertTrue(payload["creative_spectrum_2_1_1"]["vs_005_to_008_ready"])
            self.assertEqual(
                payload["creative_spectrum_2_1_1"]["archetypes_present"],
                ["VS-005", "VS-006", "VS-007", "VS-008"],
            )

        # Positive & Counter-Case on gallery_chooser.py, gallery_prompts.py, navigation_demo.py
        for script_name in ("gallery_chooser.py", "gallery_prompts.py", "navigation_demo.py"):
            script_path = DESIGN_STUDIO_DIR / "scripts" / script_name
            mod_name = script_name[:-3]
            with self.subTest(script=script_name):
                pos = subprocess.run(
                    [sys.executable, str(script_path), "--self-check", "--json"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(pos.returncode, 0, pos.stderr)
                pos_data = json.loads(pos.stdout)
                self.assertTrue(pos_data["ok"])
                self.assertEqual(pos_data["module"], mod_name)

                neg = subprocess.run(
                    [sys.executable, str(script_path), "--unknown-flag"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(neg.returncode, 2)
                self.assertNotIn("Traceback", neg.stderr)
                self.assertIn("skills/design-studio/references/eval-cases.json", neg.stderr)
                self.assertIn("skills/design-studio/references/toolchain-20-matrix.md", neg.stderr)

    def test_skill_retro_probe_fast_reports_zero_unwired_drawers_and_eight_eval_cases(self) -> None:
        for ref_rel in (
            "references/eval-cases.json",
            "references/remotion-video.md",
            "references/toolchain-20-matrix.md",
        ):
            self.assertTrue(SKILL_RETRO._drawer_linked_in_l0(DESIGN_STUDIO_DIR, ref_rel), ref_rel)
            self.assertTrue(SKILL_RETRO._drawer_linked_in_scripts(DESIGN_STUDIO_DIR, ref_rel), ref_rel)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (
                mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", tmp_path / "proposals.jsonl"),
                mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", tmp_path / "skills-roi.json"),
                mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", tmp_path / "warehouse.db"),
                mock.patch.object(SKILL_RETRO, "DEFAULT_USAGE_JSONL", tmp_path / "usage.jsonl"),
            ):
                probe = SKILL_RETRO.probe_skills(
                    skill="design-studio",
                    skills_root=SKILLS_DIR,
                    handoff_root=tmp_path / "handoff",
                    dev_root=tmp_path,
                    fast=True,
                )
        self.assertTrue(probe["ok"])
        self.assertEqual(len(probe["skills"]), 1)
        entry = probe["skills"][0]
        self.assertEqual(entry["skill"], "design-studio")
        self.assertTrue(entry["budget_ok"])
        self.assertLessEqual(entry["l0_lines"], 80)
        self.assertGreaterEqual(entry["eval_case_count"], 8)
        self.assertEqual(entry["signals"]["l1_unwired_drawers"], 0)


if __name__ == "__main__":
    unittest.main()
