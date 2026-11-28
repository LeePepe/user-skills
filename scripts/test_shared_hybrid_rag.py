#!/usr/bin/env python3
"""Unit tests for shared skill_runtime/hybrid_rag.py and S2 relative symlinks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SCRIPTS = ROOT / "skills" / "skill-contract" / "scripts"
if str(CANONICAL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CANONICAL_SCRIPTS))

from skill_runtime import (  # noqa: E402
    ACI_MAX_BYTES,
    ALLOWED_CORPUS_SCOPES,
    RagDoc,
    RagHit,
    VaultIsolationError,
    format_rag_context_hint,
    hybrid_search,
    load_design_vault_corpus,
    load_handoff_corpus,
    load_markdown_vault_corpus,
    tokenize_cjk_and_latin,
)
import skill_runtime.hybrid_rag as hybrid_rag_mod  # noqa: E402


class SharedHybridRagRuntimeTests(unittest.TestCase):
    def test_s2_relative_symlinks_across_wave2_target_skills(self) -> None:
        canonical = (CANONICAL_SCRIPTS / "skill_runtime").resolve()
        self.assertTrue((canonical / "hybrid_rag.py").is_file())
        lines = (canonical / "hybrid_rag.py").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 320)

        plugin = json.loads((ROOT / "skills/skill-contract/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "1.2.3")

        expected_rel = Path("../../skill-contract/scripts/skill_runtime")
        for skill in ("personal-m2-assistant", "knowledge-digest", "design-studio", "story-craft"):
            link = ROOT / "skills" / skill / "scripts" / "skill_runtime"
            self.assertTrue(link.is_symlink(), f"{skill} must use a relative symlink, never copied files")
            self.assertEqual(link.readlink(), expected_rel)
            self.assertEqual(link.resolve(), canonical)

            init_py = link / "__init__.py"
            spec = importlib.util.spec_from_file_location(
                f"test_{skill.replace('-', '_')}_skill_runtime",
                init_py,
                submodule_search_locations=[str(link)],
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
                self.assertIsNotNone(getattr(module, "hybrid_search", None))
                self.assertIsNotNone(getattr(module, "RagDoc", None))
            finally:
                sys.modules.pop(spec.name, None)

    def test_cjk_and_latin_tokenization_and_bm25_search_plus_pure_python_fallback(self) -> None:
        toks = tokenize_cjk_and_latin("卡片揭角 skills/design-studio/scripts/design_catalog.py #0A84FF")
        self.assertIn("卡片", toks)
        self.assertIn("片揭", toks)
        self.assertIn("揭角", toks)
        self.assertIn("design_catalog", toks)
        self.assertIn("0a84ff", toks)

        docs = [
            RagDoc(
                doc_id="handoff:us-howto-apple-theme",
                corpus_scope="handoff",
                title="Apple OS-Native Appearance Guard",
                summary="Enforce NSAppearance dynamic color tokens and forbid custom theme plumbing.",
                body="Fix skills/my-designer/references/appearance-ledger.md and howto_guard.py.",
                tags=("apple", "appearance", "howto"),
                paths=("skills/howto/scripts/howto_guard.py",),
            ),
            RagDoc(
                doc_id="handoff:us-linear-board-sync",
                corpus_scope="handoff",
                title="Linear Daily Brief Sync",
                summary="每日 05:00 Daily Brief 四桶顺推与 TIA 看板幂等更新。",
                body="处理 linear-board 的双向评论同步与增量游标。",
                tags=("linear-board", "daily-brief"),
                paths=("skills/linear-board/scripts/linear_board.py",),
            ),
        ]
        cjk_res = hybrid_search("每日四桶顺推看板", docs, corpus_scope="personal-m2-assistant", top_k=2)
        self.assertTrue(cjk_res["ok"])
        self.assertGreaterEqual(cjk_res["hit_count"], 1)
        self.assertEqual(cjk_res["hits"][0]["doc_id"], "handoff:us-linear-board-sync")

        latin_res = hybrid_search("NSAppearance howto_guard", docs, corpus_scope="handoff", top_k=2)
        self.assertEqual(latin_res["hits"][0]["doc_id"], "handoff:us-howto-apple-theme")

        with mock.patch.object(hybrid_rag_mod.sqlite3, "connect", side_effect=sqlite3.OperationalError("no such module: fts5")):
            fallback_res = hybrid_search("每日四桶顺推", docs, corpus_scope="handoff", top_k=2)
            self.assertEqual(fallback_res["backend"], "python_bm25")
            self.assertEqual(fallback_res["hits"][0]["doc_id"], "handoff:us-linear-board-sync")

    def test_jev_reranking_and_offline_deterministic_fallback(self) -> None:
        docs = [
            RagDoc(
                doc_id="knowledge-vault:keyword-spam",
                corpus_scope="knowledge-vault",
                title="RAG keyword mention",
                summary="Mentions hybrid rag hybrid rag hybrid rag without substance.",
                body="hybrid rag keyword repetition",
            ),
            RagDoc(
                doc_id="knowledge-vault:deep-architecture",
                corpus_scope="knowledge-vault",
                title="Two-Stage BM25 + System One Reranker",
                summary="Combines Layer-0 SQLite FTS5 recall with Layer-1 Falcon score reranking.",
                body="Detailed architecture for hybrid rag retrieval.",
            ),
        ]

        def fake_jev(state: dict, questions: dict, *, agent_role: str = "") -> dict:
            self.assertEqual(agent_role, "knowledge-digest")
            cands = state["candidates"]
            answers = {}
            for key, cand in cands.items():
                is_deep = "deep-architecture" in cand["doc_id"]
                answers[f"{key}_direct"] = {"noul": 0.94 if is_deep else 0.12}
                answers[f"{key}_tier"] = {"score": 2.9 if is_deep else 0.3}
            return {"answers": answers}

        reranked = hybrid_search(
            "hybrid rag",
            docs,
            corpus_scope="knowledge-digest",
            top_k=2,
            use_jev=True,
            jev_fn=fake_jev,
        )
        self.assertTrue(reranked["jev_reranked"])
        self.assertEqual(reranked["hits"][0]["doc_id"], "knowledge-vault:deep-architecture")
        self.assertIsNotNone(reranked["hits"][0]["jev_score"])

        def timeout_jev(_state: dict, _questions: dict) -> dict:
            raise TimeoutError("Falcon 1.5s timeout")

        fallback = hybrid_search(
            "hybrid rag",
            docs,
            corpus_scope="knowledge-digest",
            top_k=2,
            use_jev=True,
            jev_fn=timeout_jev,
        )
        self.assertFalse(fallback["jev_reranked"])
        self.assertEqual(fallback["hits"][0]["doc_id"], "knowledge-vault:keyword-spam")
        self.assertIsNone(fallback["hits"][0]["jev_score"])

    def test_vault_isolation_guard_blocks_cross_vault_queries(self) -> None:
        self.assertIn("knowledge-vault", ALLOWED_CORPUS_SCOPES["knowledge-digest"])
        self.assertNotIn("knowledge-vault", ALLOWED_CORPUS_SCOPES["design-studio"])
        self.assertNotIn("design-studio", ALLOWED_CORPUS_SCOPES["knowledge-digest"])

        kv_doc = RagDoc(doc_id="kv:1", corpus_scope="knowledge-vault", title="KV note", summary="secret learning note")
        ds_doc = RagDoc(doc_id="ds:1", corpus_scope="design-studio", title="Card Peel", summary="SwiftUI peel motion")

        with self.assertRaises(VaultIsolationError):
            hybrid_search("note", [kv_doc], corpus_scope="design-studio")
        with self.assertRaises(VaultIsolationError):
            hybrid_search("note", [ds_doc], corpus_scope="design-studio", allowed_scopes=["knowledge-vault"])
        with self.assertRaises(VaultIsolationError):
            hybrid_search("peel", [ds_doc], corpus_scope="knowledge-digest")
        with self.assertRaises(VaultIsolationError):
            hybrid_search("peel", [kv_doc], corpus_scope="save-tool", allowed_scopes=["design-studio"])
        with self.assertRaises(VaultIsolationError):
            load_markdown_vault_corpus(ROOT, corpus_scope="design-studio")

    def test_corpus_loaders_handoff_markdown_and_design_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            # 1. Handoff corpus loader
            handoff = base / "handoff"
            (handoff / "_watch").mkdir(parents=True)
            gdir = handoff / "us-demo-goal"
            (gdir / "receipts").mkdir(parents=True)
            (gdir / "spec.md").write_text(
                "# Goal Spec: Demo Goal\n- Build `skills/demo/scripts/run.py` safely.\n",
                encoding="utf-8",
            )
            (gdir / "ledger.md").write_text(
                "- 2026-11-28T10:00:00+08:00 | EVT=GOAL_CREATED | id=E0\n"
                "- 2026-11-28T10:30:00+08:00 | EVT=GOAL_DONE | id=E1\n",
                encoding="utf-8",
            )
            (gdir / "receipts" / "T1-r10.md").write_text("Verified unit tests pass with 0 regressions.\n", encoding="utf-8")
            h_docs = load_handoff_corpus(handoff)
            self.assertEqual(len(h_docs), 1)
            self.assertEqual(h_docs[0].corpus_scope, "handoff")
            self.assertIn("GOAL_DONE", h_docs[0].tags)
            self.assertIn("skills/demo/scripts/run.py", h_docs[0].tags)

            # 2. Markdown/JSON vault loader (read-only, skips executable scripts)
            kv_root = base / "knowledge-vault"
            (kv_root / "concepts").mkdir(parents=True)
            (kv_root / "concepts" / "rag-valve.md").write_text(
                "---\ntitle: Hybrid RAG Valve\ncategory: architecture\ntags: [rag, bm25]\ninsight: Layer-0 FTS5 recall before Jev.\n---\n# Hybrid RAG\nBody text.\n",
                encoding="utf-8",
            )
            (kv_root / "tool.json").write_text(
                json.dumps({"name": "sqlite-fts5", "summary": "Embedded full-text search", "tags": ["sqlite"]}),
                encoding="utf-8",
            )
            (kv_root / "evil.py").write_text("raise RuntimeError('save-tool must never execute scripts')\n", encoding="utf-8")
            m_docs = load_markdown_vault_corpus(kv_root, corpus_scope="knowledge-vault")
            self.assertEqual(len(m_docs), 2)
            res_m = hybrid_search("FTS5 embedded", m_docs, corpus_scope="save-tool", top_k=3)
            self.assertGreaterEqual(res_m["hit_count"], 1)

            # 3. Design vault loader (real repository vault + measured hex palette fixture)
            dv_root = base / "design-vault"
            dv_root.mkdir()
            (dv_root / "desktop-macos.json").write_text(
                json.dumps(
                    {
                        "platform": "desktop",
                        "inspirations": [
                            {
                                "id": "MP-999",
                                "name": "光谱玻璃卡 (Spectral Glass Card)",
                                "category": "glass-material",
                                "primary_dimension": "color-palette",
                                "platforms_v2": ["desktop"],
                                "canonical_prompt": "Measured palette #1E293B and #38BDF8 with crisp specular border.",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            d_docs = load_design_vault_corpus(dv_root, platform="desktop")
            self.assertEqual(len(d_docs), 1)
            self.assertIn("#1E293B", d_docs[0].metadata["hex_palette"])
            res_d = hybrid_search("光谱玻璃 #38BDF8", d_docs, corpus_scope="design-studio", top_k=1)
            self.assertEqual(res_d["hits"][0]["doc_id"], "design:desktop:MP-999")

            real_vault = ROOT / "skills" / "design-studio" / "scripts" / "data" / "vault"
            real_docs = load_design_vault_corpus(real_vault, platform="desktop")
            self.assertGreater(len(real_docs), 10)
            peel = hybrid_search("卡片揭角 corner peel", real_docs, corpus_scope="design-studio", top_k=3)
            self.assertTrue(any("MP-001" in h["doc_id"] for h in peel["hits"]))

    def test_aci_output_budget_cap_and_context_hint_redaction(self) -> None:
        huge_docs = [
            RagDoc(
                doc_id=f"handoff:goal-{i:03d}",
                corpus_scope="handoff",
                title=f"Goal {i} with path /" + "Users/alice/secret",
                summary=("Detailed summary about hybrid rag budget enforcement " * 12),
                body=("hybrid rag " * 300),
                tags=tuple(f"tag-{j}" for j in range(12)),
                paths=("/" + f"Users/alice/repo/file_{i}.py",),
                metadata={"blob": "x" * 2_000, "token": "ghp_1234567890abcdef"},
            )
            for i in range(30)
        ]
        payload = hybrid_search("hybrid rag budget", huge_docs, corpus_scope="story-craft", top_k=25)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), ACI_MAX_BYTES)
        serialized = encoded.decode("utf-8")
        self.assertNotIn("Users/alice", serialized)
        self.assertNotIn("ghp_1234567890abcdef", serialized)

        hint = format_rag_context_hint(
            [RagHit("handoff:1", "handoff", 1.0, 1.0, None, "Title", "Snippet at " + ("/" + "Users/bob/work ") * 30)],
            max_chars=180,
        )
        self.assertLessEqual(len(hint), 180)
        self.assertNotIn("Users/bob", hint)


if __name__ == "__main__":
    unittest.main()
