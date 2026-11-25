#!/usr/bin/env python3
"""Regression tests for modular root_tracker.py facade and root_tracker_core deep modules."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "skills" / "linear-board" / "scripts" / "root_tracker.py"
CORE_DIR = FACADE_PATH.parent / "root_tracker_core"
RUNTIME_LINK = FACADE_PATH.parent / "skill_runtime"


def _load_facade(module_name: str = "linear_board_root_tracker_modular"):
    spec = importlib.util.spec_from_file_location(module_name, FACADE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class LinearBoardModularTests(unittest.TestCase):
    def test_facade_line_budget_sys_path_and_anti_cheat_guards(self) -> None:
        facade_source = FACADE_PATH.read_text(encoding="utf-8")
        self.assertLessEqual(
            len(facade_source.splitlines()),
            90,
            f"root_tracker.py must stay <=90 lines (got {len(facade_source.splitlines())})",
        )
        self.assertIn("Path(__file__).resolve().parent", facade_source)
        self.assertIn("sys.path.insert(0", facade_source)

        core_files = sorted(CORE_DIR.glob("*.py"))
        self.assertGreaterEqual(len(core_files), 6)
        self.assertFalse(list(CORE_DIR.glob("part_*.py")))

        forbidden_calls = {"exec", "eval", "compile"}
        forbidden_modules = {"zlib", "base64"}
        for path in [FACADE_PATH, *core_files]:
            source = path.read_text(encoding="utf-8")
            if path != FACADE_PATH:
                self.assertLessEqual(
                    len(source.splitlines()),
                    750,
                    f"{path.name} must stay <=750 lines (got {len(source.splitlines())})",
                )
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_modules, f"Forbidden import in {path.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden_modules, f"Forbidden import in {path.name}")
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, forbidden_calls, f"Forbidden call {node.func.id} in {path.name}")

    def test_skill_runtime_symlink_and_core_module_imports(self) -> None:
        self.assertTrue(RUNTIME_LINK.is_symlink())
        self.assertEqual(RUNTIME_LINK.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertTrue((RUNTIME_LINK / "__init__.py").is_file())

        tracker = _load_facade("root_tracker_runtime_check")
        self.assertIn(str(FACADE_PATH.parent), sys.path)
        self.assertTrue(callable(tracker.redact_payload))
        self.assertTrue(callable(tracker.compact_json_dumps))
        expected_modules = {"shared", "bugs_and_prs", "sinks_and_collect", "markdown", "linear_sync", "cli"}
        loaded_modules = {mod.__name__.rsplit(".", 1)[-1] for mod in tracker._CORE_MODULES}
        self.assertEqual(expected_modules, loaded_modules)
        for mod in tracker._CORE_MODULES:
            self.assertIs(importlib.import_module(mod.__name__), mod)

    def test_importlib_symbols_and_monkeypatch_propagation_across_functions_and_classes(self) -> None:
        tracker = _load_facade("root_tracker_patch_check")
        required_symbols = (
            "SCRIPT_DIR",
            "DEFAULT_TOPIC_MAPS",
            "WORKSTREAMS",
            "LANDING_OPTIONS",
            "SIGNALS",
            "OneWayBoundaryError",
            "LinearSyncError",
            "LinearClient",
            "collect",
            "collect_prs",
            "collect_eval_sinks",
            "classify_jev",
            "link_companion_prs",
            "build_root_markdown",
            "parse_manifest",
            "parse_root_meta",
            "load_teams_timeline",
            "sync_linear",
            "render_html",
            "_load_topic_map",
            "_workstream_markdown",
            "_merge_discovery",
            "_linear_query",
            "_cli",
            "main",
        )
        for symbol in required_symbols:
            self.assertTrue(hasattr(tracker, symbol), f"Missing symbol on facade: {symbol}")

        # Verify monkeypatching a global constant on the facade propagates into core functions and resets cleanly.
        original_maps = tracker.DEFAULT_TOPIC_MAPS
        with tempfile.TemporaryDirectory() as tmp:
            custom_map = Path(tmp) / "custom-map.json"
            custom_map.write_text(
                json.dumps({"groups": [{"topics": [{"topic_id": "modular-custom-topic", "title": "Custom Topic"}]}]}),
                encoding="utf-8",
            )
            with mock.patch.object(tracker, "DEFAULT_TOPIC_MAPS", (custom_map,)):
                topics = tracker._load_topic_map()
                self.assertEqual([t["topic_id"] for t in topics], ["modular-custom-topic"])
            restored_topics = tracker._load_topic_map()
            self.assertNotEqual([t["topic_id"] for t in restored_topics], ["modular-custom-topic"])
        self.assertEqual(tracker.DEFAULT_TOPIC_MAPS, original_maps)

        # Verify monkeypatching a private helper (`_linear_query`) on the facade propagates into `LinearClient` methods.
        client = tracker.LinearClient("fake-token")
        with mock.patch.object(tracker, "_linear_query", return_value={"team": {"id": "team-patched"}}) as patched_query:
            self.assertEqual(client._team(), "team-patched")
            patched_query.assert_called_once()

        # Verify monkeypatching a top-level function (`collect`) on the facade propagates into `main()` / `_cli()`.
        with tempfile.TemporaryDirectory() as tmp:
            out_snap = Path(tmp) / "snap.json"
            out_html = Path(tmp) / "index.html"
            fake_snapshot = {"roots": [], "unassigned": [], "signals": []}
            with mock.patch.object(tracker, "collect", return_value=fake_snapshot) as patched_collect:
                rc = tracker.main(["collect", "--output", str(out_snap), "--dashboard", str(out_html)])
            self.assertEqual(rc, 0)
            patched_collect.assert_called_once()
            self.assertEqual(json.loads(out_snap.read_text(encoding="utf-8")), fake_snapshot)
            self.assertTrue(out_html.is_file())

    def test_cli_help_subcommands_and_self_disclosing_error(self) -> None:
        subcommands = (
            "collect",
            "refresh",
            "sync-linear",
            "sync-roots",
            "sync-teams-timeline",
            "classify-jev",
            "render-html",
        )
        top_help = subprocess.run(
            [sys.executable, str(FACADE_PATH), "--help"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(top_help.returncode, 0, msg=top_help.stderr)
        for sub in subcommands:
            self.assertIn(sub, top_help.stdout)
            sub_help = subprocess.run(
                [sys.executable, str(FACADE_PATH), sub, "--help"],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(sub_help.returncode, 0, msg=f"{sub} --help failed: {sub_help.stderr}")

        classify_proc = subprocess.run(
            [
                sys.executable,
                str(FACADE_PATH),
                "classify-jev",
                "--item-json",
                json.dumps({"title": "Discovery Feed Kumo component", "repository": "repo/app"}),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(classify_proc.returncode, 0, msg=classify_proc.stderr)
        self.assertEqual(json.loads(classify_proc.stdout)["topic_id"], "sapphire-discovery-kumo")

        bad_proc = subprocess.run(
            [sys.executable, str(FACADE_PATH), "--invalid-modular-flag"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(bad_proc.returncode, 2)
        self.assertIn("Fix:", bad_proc.stderr)
        self.assertIn("skills/linear-board/references/daily-brief-and-bot.md", bad_proc.stderr)


if __name__ == "__main__":
    unittest.main()
