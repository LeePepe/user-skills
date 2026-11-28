#!/usr/bin/env python3
"""Focused positive and counter-case unit tests for story-craft Hybrid RAG."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STORY_CRAFT_DIR = ROOT / "skills" / "story-craft"
STORY_RAG_SCRIPT = STORY_CRAFT_DIR / "scripts" / "story_rag.py"


def _load_story_rag_module():
    spec = importlib.util.spec_from_file_location("story_rag_under_test", STORY_RAG_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(spec.name, None)


class StoryCraftHybridRagTests(unittest.TestCase):
    def test_s2_symlink_line_budgets_plugin_version_and_eval_counter_case_ratio(self) -> None:
        link = STORY_CRAFT_DIR / "scripts" / "skill_runtime"
        self.assertTrue(link.is_symlink(), "story-craft must consume skill_runtime via S2 relative symlink")
        self.assertEqual(link.readlink(), Path("../../skill-contract/scripts/skill_runtime"))
        self.assertEqual(link.resolve(), (ROOT / "skills/skill-contract/scripts/skill_runtime").resolve())

        script_lines = STORY_RAG_SCRIPT.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(script_lines), 220)

        skill_md = STORY_CRAFT_DIR / "SKILL.md"
        self.assertLessEqual(len(skill_md.read_text(encoding="utf-8").splitlines()), 180)
        self.assertLessEqual(skill_md.stat().st_size, 12 * 1024)

        plugin = json.loads((STORY_CRAFT_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "story-craft")
        self.assertEqual(plugin["version"], "1.0.1")

        drawer = json.loads((STORY_CRAFT_DIR / "references" / "eval-cases.json").read_text(encoding="utf-8"))
        active = drawer["active_cases"]
        self.assertGreaterEqual(len(active), 4)
        self.assertLessEqual(len(active), 15)
        counter_cases = [
            c
            for c in active
            if c.get("negative_cmd")
            or c.get("forbidden_regex")
            or c.get("expect_exit", 0) != 0
            or "counter-case" in (c.get("tags") or [])
        ]
        self.assertGreaterEqual(len(counter_cases) / len(active), 0.25)

    def test_cli_topic_and_search_subcommand_extract_desensitized_four_element_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff_root = Path(tmp) / "handoff"
            g1 = handoff_root / "us-cache-stampede-fix"
            (g1 / "receipts").mkdir(parents=True)
            (g1 / "spec.md").write_text(
                "# Goal Spec: Cache Stampede Singleflight Fix\n"
                "- Counter-intuitive pivot: H1 blamed Redis network jitter, whereas root_cause was duplicate cold-miss fan-out.\n"
                "- Quantitative delta: P99 latency 480ms -> 38ms (12.6x faster) across 500/500 replay requests.\n"
                "- Owned Paths: `skills/story-craft/scripts/story_rag.py`\n"
                "- Physical lock: enforce singleflight lease gate in CI verify.\n",
                encoding="utf-8",
            )
            mock_home_path = "/" + "Users/alice/secret/key"
            mock_home_prefix = "/" + "Users/alice"
            mock_linux_path = "/" + "home/bob/private/cred"
            mock_ghp_tok = "ghp_" + "supersecret999"
            (g1 / "ledger.md").write_text(
                "# Goal Ledger: us-cache-stampede-fix\n"
                "- 2026-11-20T10:00:00+08:00 | EVT=PR_MERGED | id=E1 | ref=https://github.com/LeePepe/user-skills/pull/250 | "
                f"note=sha=abcdef1234567 author=alice@example.com path={mock_home_path} linux={mock_linux_path} token={mock_ghp_tok}\n"
                "- 2026-11-21T12:00:00+08:00 | EVT=GOAL_DONE | id=E2 | note=api_key=deadbeefcafe\n",
                encoding="utf-8",
            )
            (g1 / "receipts" / "T1-r10.md").write_text(
                "# R10 Receipt\nVerified 18/18 tests green and locked contract gate in pre-push hook.\n",
                encoding="utf-8",
            )

            g2 = handoff_root / "us-cjk-narrative-card"
            g2.mkdir(parents=True)
            (g2 / "spec.md").write_text(
                "# 叙事卡片脱敏流水线优化\n"
                "- 原本以为是模板缺失，排除 H2 后定位根因是未过滤绝对路径。\n"
                "- 构建耗时 12s -> 2s，覆盖 32 tests。\n"
                "- 新增脱敏门禁规则与回归锁。\n",
                encoding="utf-8",
            )
            (g2 / "ledger.md").write_text(
                "- 2026-11-25T09:00:00+08:00 | EVT=GOAL_DONE | id=E1 | note=PR #261 merged\n",
                encoding="utf-8",
            )

            proc_topic = subprocess.run(
                [
                    sys.executable,
                    str(STORY_RAG_SCRIPT),
                    "--topic",
                    "singleflight cache stampede",
                    "--top-k",
                    "2",
                    "--handoff-root",
                    str(handoff_root),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_topic.returncode, 0, proc_topic.stderr)
            self.assertLessEqual(len(proc_topic.stdout.encode("utf-8")), 12_000)
            payload = json.loads(proc_topic.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["corpus_scope"], "retro-story")
            self.assertGreaterEqual(payload["hit_count"], 1)
            self.assertTrue(payload["prompt_slots_loaded"])
            self.assertTrue(payload["desensitization"]["passed"])
            self.assertIn("secret", payload["desensitization"]["redactions_applied"])
            self.assertIn("email", payload["desensitization"]["redactions_applied"])
            self.assertIn("absolute_path", payload["desensitization"]["redactions_applied"])

            top = payload["matches"][0]
            self.assertEqual(top["goal_id"], "us-cache-stampede-fix")
            self.assertEqual(top["provenance"]["source_goal"], "us-cache-stampede-fix")
            self.assertEqual(top["provenance"]["evidence_window"], "2026-11-20..2026-11-21")
            self.assertIn("spec.md", top["provenance"]["evidence_pointers"])
            self.assertIn("T1-r10.md", top["provenance"]["evidence_pointers"])
            self.assertIn("H1", top["pivot"])
            self.assertIn("480ms -> 38ms", top["delta"])
            self.assertIn("lock", top["takeaway"].lower())

            # Verify explicit commit sha=abcdef123456 is retained, while hex api_key=deadbeefcafe is NEVER in refs or stdout
            self.assertIn("abcdef123456", top["provenance"]["refs"])
            self.assertNotIn("deadbeefcafe", top["provenance"]["refs"])
            chain = payload["narrative_chain"]
            self.assertEqual(set(chain.keys()), {"provenance", "pivot", "delta", "takeaway"})
            for leaked in (mock_ghp_tok, "deadbeefcafe", "alice@example.com", mock_home_prefix, "/" + "home/bob"):
                self.assertNotIn(leaked, proc_topic.stdout)

            # Verify optional `search --query` CJK invocation
            proc_cjk = subprocess.run(
                [
                    sys.executable,
                    str(STORY_RAG_SCRIPT),
                    "search",
                    "--query",
                    "脱敏流水线根因",
                    "--handoff-root",
                    str(handoff_root),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_cjk.returncode, 0, proc_cjk.stderr)
            cjk_payload = json.loads(proc_cjk.stdout)
            self.assertEqual(cjk_payload["matches"][0]["goal_id"], "us-cjk-narrative-card")
            self.assertIn("H2", cjk_payload["matches"][0]["pivot"])

    def test_optional_jev_reranking_and_aci_12kb_stdout_cap(self) -> None:
        mod = _load_story_rag_module()
        with tempfile.TemporaryDirectory() as tmp:
            handoff_root = Path(tmp) / "handoff"
            for idx in range(18):
                gdir = handoff_root / f"goal-narrative-{idx:02d}"
                gdir.mkdir(parents=True)
                (gdir / "spec.md").write_text(
                    f"# Narrative Goal {idx:02d}\n"
                    f"- Counter-intuitive pivot H{(idx % 5) + 1}: {'narrative mechanism detail ' * 16}\n"
                    f"- Delta: latency {100 + idx}ms -> {10 + idx}ms ({'measured sample ' * 14})\n"
                    f"- Takeaway lock: {'contract guard verification rule ' * 14}\n",
                    encoding="utf-8",
                )
                (gdir / "ledger.md").write_text(
                    f"- 2026-11-28T10:00:00+08:00 | EVT=GOAL_DONE | id=E1 | note=PR #{300 + idx} "
                    f'owner=secret_{idx}@corp.internal {{"api_key": "deadbeef00{idx:02d}"}} dir=/' + f"home/dev{idx}/keys\n",
                    encoding="utf-8",
                )

            def fake_jev(state: dict, questions: dict, *, agent_role: str = "") -> dict:
                self.assertEqual(agent_role, "retro-story")
                raw_state = json.dumps(state, ensure_ascii=False)
                self.assertNotIn("@corp.internal", raw_state)
                self.assertNotIn("deadbeef00", raw_state)
                self.assertNotIn("/" + "home/dev", raw_state)
                answers = {}
                for key, cand in state["candidates"].items():
                    prefer = cand["doc_id"].endswith("goal-narrative-07")
                    answers[f"{key}_direct"] = {"noul": 0.96 if prefer else 0.15}
                    answers[f"{key}_tier"] = {"score": 2.95 if prefer else 0.4}
                return {"answers": answers}

            res = mod.run_story_rag("narrative mechanism", handoff_root=handoff_root, top_k=18, use_jev=True, jev_fn=fake_jev)
            self.assertTrue(res["ok"])
            self.assertTrue(res["jev_reranked"])
            self.assertEqual(res["matches"][0]["goal_id"], "goal-narrative-07")
            cli_out = subprocess.run(
                [sys.executable, str(STORY_RAG_SCRIPT), "--topic", "narrative mechanism", "--top-k", "18", "--handoff-root", str(handoff_root), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_out.returncode, 0)
            self.assertLessEqual(len(cli_out.stdout.encode("utf-8")), mod.ACI_MAX_BYTES)
            json.loads(cli_out.stdout)

            # Oversized prompt-slot metadata fallback still enforces <=12KB including newline
            huge_slots = {"global": {"privacy_redlines": "严禁泄露密钥与私有路径" * 900}, "categories": {f"cat_{i}": {} for i in range(40)}}
            bounded = mod._bounded_json({**res, "desensitization": {"passed": True, "privacy_redlines": huge_slots["global"]["privacy_redlines"], "redactions_applied": []}, "matches": res["matches"]}, indent=2)
            self.assertLessEqual(len((bounded + "\n").encode("utf-8")), mod.ACI_MAX_BYTES)
            self.assertTrue(json.loads(bounded)["ok"])

    def test_counter_cases_reject_missing_query_secret_only_query_bad_top_k_and_missing_root(self) -> None:
        # 1. Missing --topic/--query
        missing_q = subprocess.run(
            [sys.executable, str(STORY_RAG_SCRIPT), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(missing_q.returncode, 2)
        self.assertIn("Reference: skills/story-craft/SKILL.md", missing_q.stderr)
        self.assertLessEqual(len(missing_q.stderr.strip().splitlines()), 3)

        # 2. Secret-only query fails closed without leaking secret
        mock_only_tok = "ghp_" + "secretTokenOnly12345"
        secret_q = subprocess.run(
            [sys.executable, str(STORY_RAG_SCRIPT), "--topic", mock_only_tok, "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(secret_q.returncode, 2)
        self.assertNotIn(mock_only_tok, secret_q.stdout + secret_q.stderr)
        self.assertIn("Reference: skills/story-craft/SKILL.md", secret_q.stderr)

        # 3. Out-of-range --top-k
        bad_k = subprocess.run(
            [sys.executable, str(STORY_RAG_SCRIPT), "--topic", "rag", "--top-k", "0", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_k.returncode, 2)
        self.assertFalse(json.loads(bad_k.stdout)["ok"])

        # 4. Missing oversized --handoff-root with embedded secrets/newlines stays <=12KB valid JSON and <=3 stderr lines
        huge_bad_root = "/" + "home/eve/secret_" + ("x" * 15_000) + "_api_key=deadbeefcafe99"
        bad_root = subprocess.run(
            [
                sys.executable,
                str(STORY_RAG_SCRIPT),
                "search",
                "--query",
                "rag",
                "--handoff-root",
                huge_bad_root,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_root.returncode, 2)
        self.assertLessEqual(len(bad_root.stdout.encode("utf-8")), 12_000)
        self.assertFalse(json.loads(bad_root.stdout)["ok"])
        self.assertNotIn("deadbeefcafe99", bad_root.stdout + bad_root.stderr)
        self.assertNotIn("/" + "home/eve", bad_root.stdout + bad_root.stderr)
        self.assertLessEqual(len(bad_root.stderr.strip().splitlines()), 3)

        # 5. Parser error with secret/newline/private-path in flag value scrubs both and keeps <=3 stderr lines
        bad_flag_tok = "ghp_" + "parserLeak12345"
        escaped_priv_path = "/" + "home/alice/private"
        parser_err = subprocess.run(
            [sys.executable, str(STORY_RAG_SCRIPT), "--top-k", f"{bad_flag_tok}\n{escaped_priv_path}\nthird"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(parser_err.returncode, 2)
        self.assertNotIn(bad_flag_tok, parser_err.stderr)
        self.assertNotIn(escaped_priv_path, parser_err.stderr)
        self.assertLessEqual(len(parser_err.stderr.strip().splitlines()), 3)
        self.assertTrue(parser_err.stderr.strip().splitlines()[-1].startswith("Reference: "))

        # 6. Quoted JSON credential in --handoff-root and unreadable directory fail closed with exit 2 and valid JSON
        json_cred_err = subprocess.run(
            [sys.executable, str(STORY_RAG_SCRIPT), "--handoff-root", '{"api_key": "deadbeefcafe"}', "--topic", "narrative", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(json_cred_err.returncode, 2)
        self.assertNotIn("deadbeefcafe", json_cred_err.stdout + json_cred_err.stderr)
        self.assertFalse(json.loads(json_cred_err.stdout)["ok"])

        with tempfile.TemporaryDirectory() as tmp_unreadable:
            locked_dir = Path(tmp_unreadable) / "locked"
            locked_dir.mkdir()
            locked_dir.chmod(0)
            try:
                perm_err = subprocess.run(
                    [sys.executable, str(STORY_RAG_SCRIPT), "--topic", "narrative", "--handoff-root", str(locked_dir), "--json"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(perm_err.returncode, 2)
                self.assertFalse(json.loads(perm_err.stdout)["ok"])
                self.assertLessEqual(len(perm_err.stderr.strip().splitlines()), 3)
            finally:
                locked_dir.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
