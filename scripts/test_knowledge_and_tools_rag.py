#!/usr/bin/env python3
"""Unit tests for Hybrid RAG integration in knowledge-digest and save-tool."""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_RUNTIME = (ROOT / "skills" / "skill-contract" / "scripts" / "skill_runtime").resolve()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DIGEST_VAULT = _load_module(
    "test_kd_digest_vault",
    ROOT / "skills" / "knowledge-digest" / "scripts" / "digest_vault.py",
)
TOOL_SEARCH = _load_module(
    "test_st_tool_vault_search",
    ROOT / "skills" / "save-tool" / "scripts" / "tool_vault_search.py",
)


class KnowledgeAndToolsRagTests(unittest.TestCase):
    def test_s2_relative_symlinks_and_package_budgets(self) -> None:
        expected_rel = Path("../../skill-contract/scripts/skill_runtime")
        for skill in ("knowledge-digest", "save-tool"):
            link = ROOT / "skills" / skill / "scripts" / "skill_runtime"
            self.assertTrue(link.is_symlink(), f"{skill} must wire skill_runtime via relative symlink")
            self.assertEqual(link.readlink(), expected_rel)
            self.assertEqual(link.resolve(), CANONICAL_RUNTIME)

            skill_md = ROOT / "skills" / skill / "SKILL.md"
            self.assertLessEqual(len(skill_md.read_text(encoding="utf-8").splitlines()), 180)
            self.assertLessEqual(skill_md.stat().st_size, 12 * 1024)

            eval_doc = json.loads((ROOT / "skills" / skill / "references" / "eval-cases.json").read_text(encoding="utf-8"))
            cases = eval_doc["active_cases"]
            self.assertGreaterEqual(len(cases), 4)
            self.assertLessEqual(len(cases), 15)
            counter_count = sum(
                1
                for c in cases
                if "negative_cmd" in c or any(t in {"counter-case", "negative"} for t in c.get("tags", []))
            )
            self.assertGreaterEqual(counter_count / len(cases), 0.25)

        tool_script = ROOT / "skills" / "save-tool" / "scripts" / "tool_vault_search.py"
        self.assertLessEqual(len(tool_script.read_text(encoding="utf-8").splitlines()), 200)

        kd_plugin = json.loads((ROOT / "skills/knowledge-digest/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        st_plugin = json.loads((ROOT / "skills/save-tool/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(kd_plugin["version"], "1.0.2")
        self.assertEqual(st_plugin["version"], "1.0.1")

    def test_knowledge_digest_search_retrieves_top_k_and_caps_aci_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "knowledge-vault"
            (vault / "concepts").mkdir(parents=True)
            (vault / "craft").mkdir(parents=True)
            (vault / "concepts" / "liquid-glass-optics.md").write_text(
                "---\ntitle: Liquid Glass 光学折射与视觉层级\ncategory: 工程与设计\n"
                "tags: [swiftui, liquid-glass, optics]\n"
                "insight: 玻璃材质只用于 Z>=10 悬浮导航层，严禁放入正文滚轴。\n---\n"
                "# Liquid Glass 光学折射\n悬浮导航栏与 120Hz 折射率标定。\n",
                encoding="utf-8",
            )
            (vault / "craft" / "scale-analogy-spine.md").write_text(
                "---\ntitle: 反常识尺度与生活教具叙事法\ncategory: 叙事与表达技法\n"
                "tags: [storytelling, analogy]\n---\n"
                "# 反常识尺度与生活教具\n用单个具象生活物品替代抽象百科定义。\n",
                encoding="utf-8",
            )
            for idx in range(18):
                (vault / "concepts" / f"bulk-card-{idx:02d}.md").write_text(
                    f"---\ntitle: Bulk Card {idx}\ntags: [折射, bulk]\n---\n# Bulk {idx}\n"
                    + ("光学折射悬浮材质测试段落 " * 60)
                    + "\n",
                    encoding="utf-8",
                )
            (vault / "topic-ledger.json").write_text(
                json.dumps({"schema_version": "personal-topic-ledger/v1", "topics": [{"topic_key": "internal-ledger", "title": "折射 internal"}]}),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills/knowledge-digest/scripts/digest_vault.py"),
                    "search",
                    "--query",
                    "Liquid Glass 悬浮导航折射",
                    "--top-k",
                    "15",
                    "--vault-dir",
                    str(vault),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            raw_bytes = proc.stdout.encode("utf-8")
            self.assertLessEqual(len(raw_bytes), 12_000)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["corpus_scope"], "knowledge-vault")
            self.assertEqual(payload["allowed_scopes"], ["knowledge-vault"])
            self.assertGreaterEqual(payload["hit_count"], 1)
            self.assertEqual(payload["hits"][0]["doc_id"], "knowledge-vault:concepts/liquid-glass-optics.md")
            self.assertNotIn("knowledge-vault:topic-ledger.json", [h["doc_id"] for h in payload["hits"]])

    def test_knowledge_digest_search_jev_reranking_and_isolation_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            vault = base / "knowledge-vault"
            (vault / "concepts").mkdir(parents=True)
            (vault / "concepts" / "shallow.md").write_text(
                "---\ntitle: FIFO mention\n---\n# FIFO\nfifo fifo fifo keyword repetition\n",
                encoding="utf-8",
            )
            (vault / "concepts" / "deep-fifo.md").write_text(
                "---\ntitle: 免赎回费 FIFO 窗口对账原理\n---\n# FIFO Window\n按 30/180/365 天批次先进先出免赎回费。\n",
                encoding="utf-8",
            )

            def fake_jev(state: dict, _questions: dict, *, agent_role: str = "") -> dict:
                self.assertEqual(agent_role, "knowledge-vault")
                answers = {}
                for key, cand in state["candidates"].items():
                    direct = "deep-fifo" in cand["doc_id"]
                    answers[f"{key}_direct"] = {"noul": 0.95 if direct else 0.10}
                    answers[f"{key}_tier"] = {"score": 2.9 if direct else 0.2}
                return {"answers": answers}

            res = DIGEST_VAULT.search_knowledge_vault(
                query="fifo",
                vault_dir=vault,
                top_k=2,
                use_jev=True,
                jev_fn=fake_jev,
            )
            self.assertTrue(res["jev_reranked"])
            self.assertEqual(res["hits"][0]["doc_id"], "knowledge-vault:concepts/deep-fifo.md")

            with self.assertRaises(DIGEST_VAULT.VaultIsolationError):
                DIGEST_VAULT.search_knowledge_vault(query="glass", vault_dir=vault, corpus_scope="design-studio")
            with self.assertRaises(DIGEST_VAULT.VaultIsolationError):
                DIGEST_VAULT.search_knowledge_vault(
                    query="glass",
                    vault_dir=vault,
                    allowed_scopes=["knowledge-vault", "design-studio"],
                )
            design_dir = base / "design-studio" / "vault"
            design_dir.mkdir(parents=True)
            with self.assertRaises(DIGEST_VAULT.VaultIsolationError):
                DIGEST_VAULT.search_knowledge_vault(query="glass", vault_dir=design_dir)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = DIGEST_VAULT.main(["search", "--query", "glass", "--vault-dir", str(design_dir), "--json"])
            self.assertEqual(rc, 1)
            err_payload = json.loads(buf.getvalue())
            self.assertFalse(err_payload["ok"])
            self.assertEqual(err_payload["status"], "vault_isolation_error")

    def test_save_tool_search_enforces_zero_execution_and_scope_isolation(self) -> None:
        source_path = ROOT / "skills" / "save-tool" / "scripts" / "tool_vault_search.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("subprocess", imported_names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"system", "popen", "execv", "execve", "spawnv"})

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tools_dir = base / "collected-tools"
            (tools_dir / "repos" / "bama").mkdir(parents=True)
            marker = base / "pwned.txt"
            poison_py = tools_dir / "install_hook.py"
            poison_py.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('executed')\n",
                encoding="utf-8",
            )
            poison_sh = tools_dir / "repos" / "bama" / "setup.sh"
            poison_sh.write_text(f"#!/bin/sh\necho pwned > {marker}\n", encoding="utf-8")
            poison_sh.chmod(0o755)

            (tools_dir / "README.md").write_text(
                "# Collected Tools\n| file | kind | summary |\n|---|---|---|\n| hallmark.md | skill | Anti-AI-slop web anatomy |\n",
                encoding="utf-8",
            )
            (tools_dir / "hallmark.md").write_text(
                "# Hallmark\nWeb anti-slop macrostructures and editorial typography.\n- link: https://github.com/Nutlope/hallmark\n",
                encoding="utf-8",
            )
            (tools_dir / "repos" / "bama" / "README.md").write_text(
                "# BAMA Agent CLI\nmacOS user story verification via deterministic CLI without Xcode GUI.\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(source_path),
                    "search",
                    "--query",
                    "BAMA macOS Agent CLI",
                    "--top-k",
                    "5",
                    "--vault-dir",
                    str(tools_dir),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists(), "tool_vault_search must never execute scripts in collected-tools")
            self.assertLessEqual(len(proc.stdout.encode("utf-8")), 12_000)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["zero_execution"])
            self.assertEqual(payload["corpus_scope"], "collected-tools")
            self.assertEqual(payload["hits"][0]["doc_id"], "collected-tools:repos/bama/README.md")

            with self.assertRaises(TOOL_SEARCH.VaultIsolationError):
                TOOL_SEARCH.search_tool_vault(query="hallmark", vault_dir=tools_dir, corpus_scope="design-studio")
            with self.assertRaises(TOOL_SEARCH.VaultIsolationError):
                TOOL_SEARCH.search_tool_vault(
                    query="hallmark",
                    vault_dir=tools_dir,
                    allowed_scopes=["collected-tools", "design-studio"],
                )
            design_dir = base / "design-vault"
            design_dir.mkdir()
            with self.assertRaises(TOOL_SEARCH.VaultIsolationError):
                TOOL_SEARCH.search_tool_vault(query="hallmark", vault_dir=design_dir)


if __name__ == "__main__":
    unittest.main()
