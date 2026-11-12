#!/usr/bin/env python3
"""Tests for knowledge-digest learning-topic:// candidate discovery and retro M1-M5/S1-S12 freshness metadata."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def _load(rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


DIGEST_VAULT = _load("skills/knowledge-digest/scripts/digest_vault.py")
RETRO_ENGINE = _load("skills/retro-loop/scripts/retro_story_engine.py")


class KnowledgeDigestLearningTopicTests(unittest.TestCase):
    @staticmethod
    def _write_ledger(vault: Path, *topics: dict):
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "topic-ledger.json").write_text(json.dumps({"topics": list(topics)}), encoding="utf-8")

    def test_hallmark_merged_and_excluded_while_queued_topics_emit_learning_topic_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            (tools / "repos" / "hallmark").mkdir(parents=True)
            (tools / "repos" / "bama").mkdir(parents=True)
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\n## 実際に使ったメモ\n- `/design-studio` Entry A により統合済み（Vault `T-021`）。\n",
                encoding="utf-8",
            )
            (tools / "repos" / "hallmark" / "README.md").write_text("# Hallmark\n", encoding="utf-8")
            (tools / "repos" / "bama" / "README.md").write_text("# BAMA\n", encoding="utf-8")
            proposals = root / "proposals.jsonl"
            self._write_ledger(
                vault,
                {"topic_key": "hallmark-anti-slop-anatomy", "source_id": "hallmark", "source_uri": "https://github.com/Nutlope/hallmark", "question": "Hallmark usage", "learning_state": "learned", "source_fingerprint": "hallmark"},
                {"topic_key": "bama-agent-cli-story-verification", "source_id": "bama", "source_uri": "https://github.com/daveebbelaar/bama", "question": "BAMA 如何转为可验收用户故事？", "learning_state": "queued", "source_fingerprint": "bama"},
            )

            res = DIGEST_VAULT.discover_topics(
                top=3,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["selected_count"], 1)
            self.assertEqual(res["topics"][0]["topic_key"], "bama-agent-cli-story-verification")
            self.assertFalse((vault / "concepts").exists())

            written = [json.loads(ln) for ln in proposals.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0]["ref"], "learning-topic://bama-agent-cli-story-verification")
            self.assertEqual(written[0]["source"], "Learning")
            self.assertEqual(written[0]["scope"], "personal")

    def test_pending_cap_and_empty_git_no_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            tools.mkdir(parents=True)
            proposals = root / "proposals.jsonl"
            self._write_ledger(
                vault,
                {"topic_key": "bama-agent-cli-story-verification", "source_id": "bama", "source_uri": "https://github.com/daveebbelaar/bama", "question": "BAMA 如何转为可验收用户故事？", "learning_state": "queued", "source_fingerprint": "bama"},
            )

            # Empty tools and empty git roots must NOT fabricate fallback git probes
            empty_res = DIGEST_VAULT.discover_topics(
                top=1,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
            )
            self.assertEqual(empty_res["status"], "no_new_topic")
            self.assertEqual(empty_res["selected_count"], 0)

            # Seed 3 pending learning topics from prior days -> cap reached
            prior_records = [
                {
                    "id": f"learning-topic-{i}",
                    "ref": f"learning-topic://topic-{i}",
                    "topic_key": f"topic-{i}",
                    "ts": "2026-11-01T00:00:00Z",
                    "agent": "knowledge-digest",
                    "title": f"Topic {i}",
                    "detail": "d",
                    "priority": "medium",
                    "status": "pending",
                    "learning_state": "proposed",
                }
                for i in range(3)
            ]
            proposals.write_text("\n".join(json.dumps(r) for r in prior_records) + "\n", encoding="utf-8")
            (tools / "repos" / "bama").mkdir(parents=True)
            (tools / "repos" / "bama" / "README.md").write_text("# BAMA\n", encoding="utf-8")

            capped = DIGEST_VAULT.discover_topics(
                top=1,
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
            )
            self.assertEqual(capped["status"], "no_new_topic")
            self.assertEqual(capped["reason"], "pending_cap_reached")

    def test_retro_m1_to_m5_and_s1_to_s12_freshness_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mp = root / "usage.jsonl"
            records = [
                {"ts": RETRO_ENGINE.datetime.now(RETRO_ENGINE.timezone.utc).isoformat(), "status": 429, "req": "group/steering", "in": 5000, "out": 40, "cache_read": 100, "ttft_ms": 4500, "tries": 2}
                for _ in range(10)
            ]
            mp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

            eff = RETRO_ENGINE.compute_agent_efficiency(
                warehouse_db=root / "missing.db",
                magpie_usage=mp,
                copilot_db=root / "missing_cp.db",
            )
            self.assertEqual(eff["window_cst"], "7d")
            self.assertEqual(eff["coverage_status"], "healthy")
            self.assertGreaterEqual(eff["sample_size"], 10)
            rec_ids = {identity for row in eff["recommendations"] for identity in (row["id"], *row.get("legacy_ids", []))}
            self.assertIn("improve-m3-automated-share", rec_ids)
            self.assertIn("improve-m3-gateway-flow", rec_ids)
            self.assertIn("improve-m4-cache-efficiency", rec_ids)
            self.assertIn("improve-m5-steering-token-share", rec_ids)

            s_roi = RETRO_ENGINE.compute_skill_roi_diagnostics(
                warehouse_db=root / "missing.db",
                copilot_db=root / "missing_cp.db",
                skills_root=root / "empty_skills",
                dev_root=root / "empty_dev",
                magpie_usage=root / "missing_usage.jsonl",
                dsh_sessions=root / "missing_dsh",
            )
            self.assertEqual(s_roi["window_cst"], "7d")
            self.assertEqual(s_roi["coverage_status"], "missing")
            self.assertIsNone(s_roi["S2_gate_catch"]["gate_catch_rate"])
            self.assertEqual(s_roi["S2_gate_catch"]["coverage_status"], "missing")


if __name__ == "__main__":
    unittest.main()
