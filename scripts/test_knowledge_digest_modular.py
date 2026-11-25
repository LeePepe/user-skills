"""Focused regression tests for modular digest_vault facade, line budgets, CLI, and eval drawer."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "skills" / "knowledge-digest" / "scripts" / "digest_vault.py"
CORE_DIR = FACADE_PATH.parent / "digest_vault_core"
EVAL_DRAWER_PATH = ROOT / "skills" / "knowledge-digest" / "references" / "eval-cases.json"
PLUGIN_PATH = ROOT / "skills" / "knowledge-digest" / ".claude-plugin" / "plugin.json"


def load_facade():
    spec = importlib.util.spec_from_file_location("test_knowledge_digest_modular_facade", FACADE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KnowledgeDigestModularTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_facade()

    def test_facade_and_core_line_caps_and_anti_cheat_redlines(self) -> None:
        facade_source = FACADE_PATH.read_text(encoding="utf-8")
        self.assertLessEqual(len(facade_source.splitlines()), 90)
        self.assertIn("str(Path(__file__).resolve().parent)", facade_source)

        core_files = sorted(CORE_DIR.glob("*.py"))
        self.assertGreaterEqual(len(core_files), 5)

        forbidden_calls = {"zlib", "base64", "exec", "eval"}
        for py_file in [FACADE_PATH, *core_files]:
            source = py_file.read_text(encoding="utf-8")
            line_count = len(source.splitlines())
            self.assertLessEqual(line_count, 750, f"{py_file.name} exceeds 750 lines ({line_count})")
            # Anti-cheat: forbid zlib/base64/exec/eval imports or calls, and horizontal ';' statement packing
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_calls, py_file.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden_calls, py_file.name)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"exec", "eval"}, py_file.name)
            for idx, raw_line in enumerate(source.splitlines(), 1):
                code_part = raw_line.split("#", 1)[0]
                if ";" in code_part and not any(q in code_part for q in ('"', "'")):
                    self.fail(f"Horizontal semicolon packing detected at {py_file.name}:{idx}")

    def test_skill_runtime_relative_symlink_and_plugin_version(self) -> None:
        runtime_link = FACADE_PATH.parent / "skill_runtime"
        self.assertTrue(runtime_link.is_symlink())
        self.assertEqual(runtime_link.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertIs(self.mod.SF_DATALESS, self.mod.RUNTIME_SF_DATALESS)
        self.assertTrue(hasattr(self.mod, "SelfDisclosingArgumentParser"))

        plugin = json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))
        parts = [int(part) for part in str(plugin["version"]).split(".")]
        self.assertEqual(len(parts), 3)
        self.assertGreater(tuple(parts), (1, 0, 0))

    def test_importlib_symbol_compatibility_and_mock_patch_propagation(self) -> None:
        expected_symbols = (
            "archive_card",
            "discover_topics",
            "handle_learning_event",
            "publish_blog",
            "precheck_card",
            "reconcile_learning_state",
            "infer_topic_track",
            "load_card_templates",
            "render_concept_card_markdown",
            "render_dual_zone_digest_issue",
            "validate_dc006_to_008",
            "validate_proposal_record",
            "DAILY_LINEAR_FOOTER",
            "_append_valid_proposal",
            "_daily_description_keys",
            "_daily_digest_markdown",
            "_existing_vault_corpus",
            "_inspect_vault_coverage",
            "_learning_brief",
            "_legacy_automated_learning_brief",
            "_linear_transport_from_repo",
            "_load_helper_module",
            "_load_issue_brief_module",
            "_load_topic_ledger",
            "_merge_daily_digest_description",
            "_normalize_source_uri",
            "_reconcile_collected_tools_entities",
            "_rerank_candidates_r09",
            "_save_topic_ledger",
            "_scan_git_tech_stack_candidates",
            "_upsert_linear_daily_issue",
            "main",
        )
        for symbol in expected_symbols:
            self.assertTrue(hasattr(self.mod, symbol), f"missing re-exported symbol: {symbol}")

        with mock.patch.object(self.mod, "discover_topics", return_value={"ok": False, "error": "patched"}):
            self.assertEqual(self.mod.main(["discover-topics", "--no-git"]), 1)
        # Verify unpatching restores canonical implementation cleanly
        self.assertIs(self.mod.discover_topics, self.mod._FACADE_WRAPPERS["discover_topics"])

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            tools = Path(tmp) / "tools"
            vault.mkdir()
            tools.mkdir()
            with mock.patch.object(self.mod, "_existing_vault_corpus", side_effect=ValueError("corpus_boom")):
                res = self.mod.discover_topics(
                    top=1,
                    vault_dir=vault,
                    collected_tools_dir=tools,
                    git_roots=(),
                    proposals_path=Path(tmp) / "proposals.jsonl",
                )
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "health_error")
            self.assertIn("corpus_boom", res["error"])

            ok_after = self.mod.discover_topics(
                top=1,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=Path(tmp) / "proposals.jsonl",
            )
            self.assertTrue(ok_after["ok"])
            self.assertEqual(ok_after["status"], "no_new_topic")

    def test_cli_positive_and_counter_case_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            card = root / "sample-card.md"
            card.write_text(
                self.mod.render_concept_card_markdown(
                    {"slug": "modular-vault-smoke", "title": "Modular Vault Smoke", "category": "工程与设计", "excerpt": "smoke"},
                    updated_date="2026-11-25",
                ),
                encoding="utf-8",
            )
            arch = subprocess.run(
                [
                    sys.executable,
                    str(FACADE_PATH),
                    "archive",
                    "--slug",
                    "modular-vault-smoke",
                    "--category",
                    "工程与设计",
                    "--title",
                    "Modular Vault Smoke",
                    "--file",
                    str(card),
                    "--vault-dir",
                    str(vault),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(arch.returncode, 0, arch.stderr)
            archived_payload = json.loads(arch.stdout)
            self.assertTrue(archived_payload["ok"])

            precheck_ok = subprocess.run(
                [
                    sys.executable,
                    str(FACADE_PATH),
                    "precheck-card",
                    "--file",
                    archived_payload["card_path"],
                    "--vault-dir",
                    str(vault),
                    "--require-index",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(precheck_ok.returncode, 0, precheck_ok.stderr)
            self.assertTrue(json.loads(precheck_ok.stdout)["ok"])

            bad_quota = subprocess.run(
                [sys.executable, str(FACADE_PATH), "discover-topics", "--top", "0", "--no-git", "--vault-dir", str(vault)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_quota.returncode, 1)
            self.assertEqual(json.loads(bad_quota.stdout)["status"], "invalid_quota")

            bad_flag = subprocess.run(
                [sys.executable, str(FACADE_PATH), "--unknown-modular-flag"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_flag.returncode, 2)
            self.assertIn("skills/knowledge-digest/references/templates.md", bad_flag.stderr)

    def test_eval_drawer_cap_and_counter_case_ratio(self) -> None:
        doc = json.loads(EVAL_DRAWER_PATH.read_text(encoding="utf-8"))
        active = doc.get("active_cases", [])
        self.assertGreater(len(active), 0)
        self.assertLessEqual(len(active), 15)
        counter_cases = [
            c
            for c in active
            if "negative_cmd" in c
            or int(c.get("expect_exit", 0) or 0) != 0
            or "counter-case" in (c.get("tags") or [])
        ]
        self.assertGreaterEqual(len(counter_cases) / len(active), 0.25)


if __name__ == "__main__":
    unittest.main()
