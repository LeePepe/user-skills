#!/usr/bin/env python3
"""Regression and contract tests for `design-studio` Hybrid RAG (`us-rag-design-studio`)."""
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

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_STUDIO_DIR = REPO_ROOT / "skills" / "design-studio"
SCRIPTS_DIR = DESIGN_STUDIO_DIR / "scripts"
DESIGN_CATALOG_PATH = SCRIPTS_DIR / "design_catalog.py"
DESIGN_RAG_PATH = SCRIPTS_DIR / "design_rag.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DESIGN_CATALOG = _load_module("test_design_studio_rag_catalog", DESIGN_CATALOG_PATH)
DESIGN_RAG = DESIGN_CATALOG.RAG


def _run_catalog(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
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


class DesignStudioHybridRagTests(unittest.TestCase):
    def test_s2_relative_symlink_and_imports_bound_to_skill_runtime(self) -> None:
        runtime_link = SCRIPTS_DIR / "skill_runtime"
        self.assertTrue(runtime_link.is_symlink())
        self.assertEqual(runtime_link.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertIsNotNone(getattr(DESIGN_CATALOG, "load_design_vault_corpus", None))
        self.assertIsNotNone(getattr(DESIGN_CATALOG, "hybrid_search", None))
        self.assertIsNotNone(getattr(DESIGN_CATALOG, "VaultIsolationError", None))

        plugin = json.loads((DESIGN_STUDIO_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "1.0.1")

    def test_rag_query_retrieves_8d_taxonomy_motion_and_measured_hex_palette(self) -> None:
        res = _run_catalog("--rag-query", "温润瑞士纸感 #FAF7F2", "--top-k", "3", "--platform", "desktop", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertLessEqual(len(res.stdout.encode("utf-8")), 12_000)

        payload = json.loads(res.stdout)
        self.assertEqual(payload["schema"], "design-vault-rag/v1")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["corpus_scope"], "design-vault")
        self.assertEqual(payload["platform_filter"], "desktop")
        self.assertGreaterEqual(payload["hit_count"], 1)

        top_hit = payload["hits"][0]
        self.assertEqual(top_hit["id"], "CL-002")
        self.assertEqual(top_hit["taxonomy_8d"]["prefix"], "CL")
        self.assertEqual(top_hit["taxonomy_8d"]["primary_dimension"], "color-palette")
        self.assertIn("#FAF7F2", top_hit["hex_palette"])
        self.assertTrue(any(sw.get("hex") == "#FAF7F2" for sw in top_hit["measured_palette"]))
        self.assertIn("motion_tier", top_hit["motion"])

    def test_search_subcommand_rag_compatibility_and_legacy_search_preservation(self) -> None:
        # `search --query <query> --json` routes through Hybrid RAG while preserving legacy `search --query <query>`
        rag_search = _run_catalog("search", "--query", "滑动确认", "--platform", "mobile", "--top-k", "3", "--json")
        self.assertEqual(rag_search.returncode, 0, rag_search.stderr)
        self.assertLessEqual(len(rag_search.stdout.encode("utf-8")), 12_000)
        rag_payload = json.loads(rag_search.stdout)
        self.assertEqual(rag_payload["schema"], "design-vault-rag/v1")
        self.assertEqual(rag_payload["corpus_scope"], "design-vault")
        self.assertEqual(rag_payload["platform_filter"], "mobile")
        self.assertTrue(any(h["id"] == "MP-002" for h in rag_payload["hits"]))
        mp2 = next(h for h in rag_payload["hits"] if h["id"] == "MP-002")
        self.assertEqual(mp2["motion"]["motion_tier"], "M2")
        self.assertIn("spring_settle", mp2["motion"]["state_machine"])

        # Legacy `search --query <query>` without RAG/JSON flags preserves catalog duplicate-check contract
        legacy = _run_catalog("search", "--query", "滑动确认")
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        legacy_payload = json.loads(legacy.stdout)
        self.assertIn("has_duplicate", legacy_payload)
        self.assertTrue(any(m["id"] == "MP-002" for m in legacy_payload["matches"]))

    def test_jev_reranking_and_measured_palette_struct_on_custom_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "design-vault"
            vault.mkdir(parents=True)
            (vault / "desktop-macos.json").write_text(
                json.dumps(
                    {
                        "schema": "design-vault-platform/v2",
                        "platform": "desktop",
                        "inspirations": [
                            {
                                "id": "CL-901",
                                "name": "Decoy Palette Mention",
                                "primary_dimension": "color-palette",
                                "sub_category": "semantic-brand-palette",
                                "platforms_v2": ["desktop"],
                                "canonical_prompt": "aurora glass palette aurora glass palette superficial note",
                            },
                            {
                                "id": "GM-902",
                                "name": "Measured Spectral Glass",
                                "category": "glass-material",
                                "motion_tier": "M2",
                                "state_machine": "idle -> hover -> specular_shift -> settle",
                                "primary_dimension": "glass-material",
                                "secondary_dimensions": ["color-palette"],
                                "sub_category": "liquid-glass-chrome",
                                "platforms_v2": ["desktop"],
                                "canonical_prompt": "True aurora glass refraction with measured swatches.",
                                "measured_palette": [
                                    {"hex": "#0F172A", "ratio": 0.62},
                                    {"hex": "#38BDF8", "ratio": 0.24},
                                ],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_jev(state: dict, questions: dict, *, agent_role: str = "") -> dict:
                self.assertIn(agent_role, {"design-vault", "design-studio"})
                answers = {}
                for key, cand in state["candidates"].items():
                    win = "GM-902" in cand["doc_id"]
                    answers[f"{key}_direct"] = {"noul": 0.96 if win else 0.08}
                    answers[f"{key}_tier"] = {"score": 3.0 if win else 0.2}
                return {"answers": answers}

            payload = DESIGN_RAG.run_design_vault_rag(
                "aurora glass palette",
                vault_dir=vault,
                dim_prefixes=DESIGN_CATALOG.DIMENSION_PREFIXES,
                platform="desktop",
                top_k=2,
                use_jev=True,
                jev_fn=fake_jev,
            )
            self.assertTrue(payload["jev_reranked"])
            self.assertEqual(payload["hits"][0]["id"], "GM-902")
            self.assertEqual(payload["hits"][0]["measured_palette"][0]["hex"], "#0F172A")
            self.assertIn("#38BDF8", payload["hits"][0]["hex_palette"])

    def test_vault_isolation_deterministically_rejects_knowledge_vault_access(self) -> None:
        VaultIsolationError = DESIGN_CATALOG.VaultIsolationError
        with tempfile.TemporaryDirectory() as tmp:
            kv_dir = Path(tmp) / "knowledge-vault"
            kv_dir.mkdir(parents=True)
            (kv_dir / "secret.json").write_text(
                json.dumps({"platform": "web", "inspirations": [{"id": "KV-1", "name": "Private Note"}]}),
                encoding="utf-8",
            )

            with self.assertRaises(VaultIsolationError):
                DESIGN_RAG.run_design_vault_rag(
                    "Private Note",
                    vault_dir=SCRIPTS_DIR / "data" / "vault",
                    dim_prefixes=DESIGN_CATALOG.DIMENSION_PREFIXES,
                    corpus_scope="knowledge-vault",
                )

            with self.assertRaises(VaultIsolationError):
                DESIGN_RAG.run_design_vault_rag(
                    "Private Note",
                    vault_dir=kv_dir,
                    dim_prefixes=DESIGN_CATALOG.DIMENSION_PREFIXES,
                    corpus_scope="design-vault",
                )

            # CLI exit code 1 with clear error message and zero Traceback
            cli_scope = subprocess.run(
                [
                    sys.executable,
                    str(DESIGN_CATALOG_PATH),
                    "--rag-query",
                    "note",
                    "--corpus-scope",
                    "knowledge-vault",
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_scope.returncode, 1)
            self.assertNotIn("Traceback", cli_scope.stderr)
            self.assertIn("design-vault", cli_scope.stderr)

            cli_dir = subprocess.run(
                [
                    sys.executable,
                    str(DESIGN_CATALOG_PATH),
                    "search",
                    "--query",
                    "note",
                    "--vault-dir",
                    str(kv_dir),
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_dir.returncode, 1)
            self.assertNotIn("Traceback", cli_dir.stderr)
            self.assertIn("knowledge-vault isolation", cli_dir.stderr)

    def test_aci_stdout_budget_stays_within_12kb_under_dense_top_k(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "design-vault"
            vault.mkdir(parents=True)
            cards = [
                {
                    "id": f"VS-{900 + idx:03d}",
                    "name": f"Dense Editorial Card {idx} " + ("架构视觉留白" * 8),
                    "category": "layout-craft",
                    "motion_tier": "M1",
                    "state_machine": "ready -> inspect -> commit -> settle " * 4,
                    "primary_dimension": "visual-style",
                    "secondary_dimensions": ["color-palette", "typography-copy", "layout-navigation"],
                    "sub_category": "editorial-grid",
                    "platforms_v2": ["desktop", "mobile", "web"],
                    "canonical_prompt": ("Editorial layout inspiration #FAF7F2 #121316 #38BDF8 " * 20),
                    "measured_palette": [{"hex": "#FAF7F2", "ratio": 0.5}, {"hex": "#121316", "ratio": 0.3}],
                }
                for idx in range(25)
            ]
            (vault / "desktop-macos.json").write_text(
                json.dumps({"platform": "desktop", "inspirations": cards}, ensure_ascii=False),
                encoding="utf-8",
            )
            res = _run_catalog(
                "--rag-query",
                "Editorial layout inspiration #FAF7F2",
                "--vault-dir",
                str(vault),
                "--top-k",
                "25",
                "--json",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            raw_bytes = len(res.stdout.encode("utf-8"))
            self.assertLessEqual(raw_bytes, 12_000)
            parsed = json.loads(res.stdout)
            self.assertTrue(parsed["ok"])
            self.assertGreaterEqual(parsed["hit_count"], 1)
            for hit in parsed["hits"]:
                self.assertEqual(hit["taxonomy_8d"]["primary_dimension"], "visual-style")
                self.assertEqual(hit["taxonomy_8d"]["prefix"], "VS")
                self.assertEqual(hit["motion"]["motion_tier"], "M1")
                self.assertIn("#FAF7F2", hit["hex_palette"])
                self.assertTrue(any(sw["hex"] == "#FAF7F2" for sw in hit["measured_palette"]))

            # Also verify real vault dense query (`--rag-query palette --top-k 25`) keeps enriched fields intact
            real_dense = _run_catalog("--rag-query", "palette", "--top-k", "25", "--json")
            self.assertEqual(real_dense.returncode, 0, real_dense.stderr)
            self.assertLessEqual(len(real_dense.stdout.encode("utf-8")), 12_000)
            real_parsed = json.loads(real_dense.stdout)
            self.assertGreaterEqual(real_parsed["hit_count"], 1)
            self.assertTrue(all(h["taxonomy_8d"]["primary_dimension"] for h in real_parsed["hits"]))
            self.assertTrue(any(h["hex_palette"] for h in real_parsed["hits"]))

    def test_enrichment_redacts_secrets_paths_and_respects_dataless_guard(self) -> None:
        secret_tok = "ghp_" + "SecretTokenValue987654"
        priv_path = "/" + "Users/alice/private/palette.png"
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "design-vault"
            vault.mkdir(parents=True)
            vfile = vault / "desktop-macos.json"
            vfile.write_text(
                json.dumps(
                    {
                        "platform": "desktop",
                        "inspirations": [
                            {
                                "id": "CL-950",
                                "name": f"Secret Swatch Card {priv_path}",
                                "category": "layout-craft",
                                "motion_tier": "M2",
                                "state_machine": f"idle -> {secret_tok} -> {priv_path}",
                                "primary_dimension": "color-palette",
                                "sub_category": "semantic-brand-palette",
                                "platforms_v2": ["desktop"],
                                "canonical_prompt": "Editorial palette with #0A84FF accent.",
                                "measured_palette": [
                                    {
                                        "hex": "#0A84FF",
                                        "ratio": 0.75,
                                        "role": f"accent {secret_tok} {priv_path}",
                                        "raw_leak": f"Bearer {secret_tok} {priv_path}",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            res = _run_catalog("--rag-query", "Editorial palette #0A84FF", "--vault-dir", str(vault), "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertLessEqual(len(res.stdout.encode("utf-8")), 12_000)
            self.assertNotIn(secret_tok, res.stdout)
            self.assertNotIn("Users/alice", res.stdout)
            self.assertNotIn("raw_leak", res.stdout)
            hit = json.loads(res.stdout)["hits"][0]
            self.assertEqual(hit["measured_palette"][0]["hex"], "#0A84FF")
            self.assertIn("<redacted>", hit["motion"]["state_machine"])

            # Simulate SF_DATALESS on the JSON file and verify _index_vault_cards skips it without reading
            real_fstat = os.fstat
            real_lstat = os.lstat
            dataless_flag = getattr(os, "SF_DATALESS", 0x40000000)

            class _FakeStat:
                def __init__(self, st: os.stat_result) -> None:
                    self._st = st
                    self.st_flags = getattr(st, "st_flags", 0) | dataless_flag

                def __getattr__(self, item: str):
                    return getattr(self._st, item)

            def fake_lstat(p, *a, **kw):
                st = real_lstat(p, *a, **kw)
                return _FakeStat(st) if Path(p).name == "desktop-macos.json" else st

            with mock.patch("os.lstat", side_effect=fake_lstat):
                idx = DESIGN_RAG._index_vault_cards(vault, "desktop")
                self.assertEqual(idx, {})

    def test_design_rag_helper_cli_self_check_and_self_disclosing_error(self) -> None:
        ok_proc = subprocess.run(
            [sys.executable, str(DESIGN_RAG_PATH), "--self-check", "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ok_proc.returncode, 0, ok_proc.stderr)
        self.assertTrue(json.loads(ok_proc.stdout)["ok"])

        bad_proc = subprocess.run(
            [sys.executable, str(DESIGN_RAG_PATH), "--unknown-flag"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_proc.returncode, 2)
        self.assertNotIn("Traceback", bad_proc.stderr)
        self.assertIn("skills/design-studio/references/eval-cases.json", bad_proc.stderr)


if __name__ == "__main__":
    unittest.main()
