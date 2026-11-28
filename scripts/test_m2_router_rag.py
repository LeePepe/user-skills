#!/usr/bin/env python3
"""Focused unit tests for personal-m2-assistant Handoff Hybrid RAG integration."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "skills" / "personal-m2-assistant" / "scripts" / "m2-router.py"
LEGACY_ROUTER_PATH = ROOT / "skills" / "m2-dispatcher" / "scripts" / "m2-router.py"


def load_router(path: Path = ROUTER_PATH):
    spec = importlib.util.spec_from_file_location("m2_router_rag_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_handoff_corpus(handoff_root: Path) -> None:
    g1 = handoff_root / "sapphire-notif-toggle-v1"
    (g1 / "receipts").mkdir(parents=True, exist_ok=True)
    (g1 / "spec.md").write_text(
        "# Goal Spec: Sapphire Notification Toggle V1\n"
        "- Target repo: `bing-growth-sapphire-app`\n"
        "- Owned Paths: `Sources/Notification/ToggleStore.swift`, `Tests/NotificationTests.swift`\n"
        "- Decision: keep OS-level push permission separate from in-app notification toggle.\n",
        encoding="utf-8",
    )
    (g1 / "ledger.md").write_text(
        "# Goal: sapphire-notif-toggle-v1\n"
        "## Events\n"
        "- 2026-10-10T10:00:00+08:00 | EVT=REVIEW_PASS | id=E2 | note=V5 PASS\n"
        "- 2026-10-10T10:30:00+08:00 | EVT=GOAL_DONE | id=E3 | note=merged\n",
        encoding="utf-8",
    )
    (g1 / "receipts" / "T1-r10.md").write_text(
        "# R10 Receipt\n"
        "- Pitfall: avoid mutating NotificationCenter observer token off main actor.\n",
        encoding="utf-8",
    )

    g2 = handoff_root / "aidash-token-roi-card"
    g2.mkdir(parents=True, exist_ok=True)
    (g2 / "spec.md").write_text(
        "# Goal Spec: AIDash Token ROI Card\n"
        "- Target repo: `AIDash`\n"
        "- Owned Paths: `aidata/L4_serve/token_roi.sql`, `AIDash/Views/TokenROICard.swift`\n"
        "- Decision: wire L1->L4 SQL before SwiftUI snapshot view.\n",
        encoding="utf-8",
    )
    (g2 / "ledger.md").write_text(
        "# Goal: aidash-token-roi-card\n"
        "## Events\n"
        "- 2026-10-12T12:00:00+08:00 | EVT=GOAL_DONE | id=E2 | note=done\n",
        encoding="utf-8",
    )

    for idx in range(3):
        extra = handoff_root / f"sapphire-notif-followup-{idx}"
        extra.mkdir(parents=True, exist_ok=True)
        (extra / "spec.md").write_text(
            f"# Goal Spec: Sapphire Notification Followup {idx}\n"
            f"- Owned Paths: `Sources/Notification/Followup{idx}.swift`\n"
            f"- Summary: notification toggle followup iteration {idx} for bing-growth-sapphire-app.\n",
            encoding="utf-8",
        )


class M2RouterHandoffRagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = load_router()

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.test_root = Path(temp.name)
        self.handoff_root = self.test_root / "handoff"
        self.handoff_root.mkdir(parents=True, exist_ok=True)
        env = patch.dict(
            "os.environ",
            {
                "M2_SUPERVISION_STATE_FILE": str(self.test_root / "state.json"),
                "M2_HANDOFF_ROOT": str(self.handoff_root),
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_plan_enriches_targets_with_top3_handoff_rag_hits_and_hint(self) -> None:
        seed_handoff_corpus(self.handoff_root)
        plan = self.router.build_plan(
            "更新 Sapphire 通知开关逻辑并补齐 Sources/Notification/ToggleStore.swift 测试",
            [],
            targets_json=[
                {
                    "topic_id": "sapphire-notification",
                    "target_repo": "bing-growth-sapphire-app",
                    "sub_prompt": "更新通知开关 ToggleStore.swift 并检查 main actor observer pitfall",
                    "wave_order": 1,
                }
            ],
            transport="stdout",
        )
        target = plan["targets"][0]
        self.assertIn("rag_hits", target)
        self.assertGreaterEqual(len(target["rag_hits"]), 1)
        self.assertLessEqual(len(target["rag_hits"]), 3)
        self.assertEqual(target["rag_hits"][0]["doc_id"], "handoff:sapphire-notif-toggle-v1")
        self.assertIn("Sources/Notification/ToggleStore.swift", target["rag_hits"][0]["owned_paths"])
        self.assertIn("[handoff:sapphire-notif-toggle-v1]", target["rag_context_hint"])
        self.assertTrue(target["context_pass"]["enabled"])
        self.assertEqual(target["context_pass"]["rag_hits"], target["rag_hits"])
        self.assertIn("rag_context_hint", plan["preview_markdown"])

        applied = self.router.apply_plan(
            plan,
            approve=True,
            transport="stdout",
            state_file=self.test_root / "state.json",
            apply_receipt_file=self.test_root / "apply.json",
            no_sync_linear=True,
        )
        self.assertIn("[RAG/context hint]", applied["receipts"][0]["prompt"])
        self.assertIn("handoff:sapphire-notif-toggle-v1", applied["receipts"][0]["prompt"])

        cli_proc = subprocess.run(
            [
                sys.executable,
                str(ROUTER_PATH),
                "--plan",
                "--message",
                "更新 Sapphire 通知开关 ToggleStore.swift 测试",
                "--targets-json",
                json.dumps([
                    {
                        "topic_id": "sapphire-notification",
                        "target_repo": "bing-growth-sapphire-app",
                        "sub_prompt": "更新通知开关 ToggleStore.swift 并检查 main actor observer pitfall",
                        "wave_order": 1,
                    }
                ]),
                "--no-herdr",
                "--no-dsh",
                "--no-sync-linear",
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli_proc.returncode, 0, cli_proc.stderr)
        cli_plan = json.loads(cli_proc.stdout)
        self.assertEqual(cli_plan["targets"][0]["rag_hits"][0]["doc_id"], "handoff:sapphire-notif-toggle-v1")
        self.assertIn("[handoff:sapphire-notif-toggle-v1]", cli_plan["targets"][0]["rag_context_hint"])

    def test_plan_preserves_upstream_hint_and_appends_rag_hint(self) -> None:
        seed_handoff_corpus(self.handoff_root)
        plan = self.router.build_plan(
            "同步 AIDash Token ROI 卡片的数仓 SQL 口径",
            [],
            targets_json=[
                {
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "sub_prompt": "更新 aidata/L4_serve/token_roi.sql 与 TokenROICard.swift",
                    "rag_context_hint": "upstream-wave1-schema-v2",
                    "wave_order": 2,
                }
            ],
            transport="stdout",
        )
        hint = plan["targets"][0]["rag_context_hint"]
        self.assertTrue(hint.startswith("upstream-wave1-schema-v2 | "))
        self.assertIn("[handoff:aidash-token-roi-card]", hint)

    def test_plan_degrades_gracefully_when_handoff_root_empty_or_missing(self) -> None:
        missing_root = self.test_root / "nonexistent-handoff"
        plan = self.router.build_plan(
            "更新 AIDash 看板卡片指标脚本 skills/aidash-metrics/SKILL.md",
            [],
            targets_json=[
                {
                    "topic_id": "aidash-aidata",
                    "target_repo": "AIDash",
                    "sub_prompt": "更新 AIDash 指标卡片并验证快照 SQL 脚本 skills/aidash-metrics/SKILL.md",
                    "wave_order": 1,
                }
            ],
            handoff_root=missing_root,
            transport="stdout",
        )
        target = plan["targets"][0]
        self.assertEqual(target["rag_hits"], [])
        self.assertFalse(target.get("rag_context_hint"))
        self.assertFalse(target["context_pass"]["enabled"])

    def test_phase0_exploratory_discussion_skips_rag_prelock(self) -> None:
        seed_handoff_corpus(self.handoff_root)
        msg = "先讨论一下 Sapphire 通知开关有哪些方案，不改代码"
        plan = self.router.build_plan(msg, [], mock_jev={}, transport="stdout")
        self.assertTrue(plan["phase0_discussion_only"])
        for target in plan["targets"]:
            self.assertEqual(target["rag_hits"], [])
            self.assertIsNone(target.get("rag_context_hint"))
            self.assertEqual(target["sub_prompt"], msg)

    def test_cli_rag_query_json_and_use_jev_rerank_bounded_under_12kb(self) -> None:
        seed_handoff_corpus(self.handoff_root)
        mock_jev_file = self.test_root / "mock_jev.json"
        mock_jev_file.write_text(
            json.dumps(
                {
                    "answers": {
                        "cand_0_direct": {"noul": 0.20},
                        "cand_0_tier": {"score": 0.5},
                        "cand_1_direct": {"noul": 0.96},
                        "cand_1_tier": {"score": 3.0},
                    }
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(ROUTER_PATH),
                "--rag-query",
                "Sapphire notification toggle observer",
                "--handoff-root",
                str(self.handoff_root),
                "--rag-top-k",
                "3",
                "--use-jev",
                "--mock-jev-json",
                str(mock_jev_file),
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
        self.assertEqual(payload["corpus_scope"], "handoff")
        self.assertTrue(payload["jev_reranked"])
        self.assertLessEqual(payload["hit_count"], 3)
        self.assertGreaterEqual(payload["hit_count"], 1)
        self.assertIsNotNone(payload["hits"][0].get("jev_score"))
        self.assertTrue(payload["rag_context_hint"])

        # Verify legacy delegator also supports --rag-query.
        legacy_proc = subprocess.run(
            [
                sys.executable,
                str(LEGACY_ROUTER_PATH),
                "--rag-query",
                "TokenROICard token_roi.sql",
                "--handoff-root",
                str(self.handoff_root),
                "-- top-k".replace(" ", ""),
                "2",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(legacy_proc.returncode, 0, legacy_proc.stderr)
        self.assertIn("handoff:aidash-token-roi-card", legacy_proc.stdout)

    def test_rag_query_counter_cases_reject_empty_query_and_nonpositive_top_k(self) -> None:
        empty_proc = subprocess.run(
            [sys.executable, str(ROUTER_PATH), "--rag-query", "   ", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(empty_proc.returncode, 2)
        err_payload = json.loads(empty_proc.stdout)
        self.assertFalse(err_payload["ok"])
        self.assertIn("--rag-query requires a non-empty query", err_payload["error"])

        bad_topk = subprocess.run(
            [sys.executable, str(ROUTER_PATH), "--rag-query", "toggle", "--rag-top-k", "0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_topk.returncode, 2)
        self.assertIn("--rag-top-k must be >= 1", bad_topk.stderr)
        self.assertIn("skills/personal-m2-assistant/references/eval-cases.json", bad_topk.stderr)

    def test_vault_isolation_counter_case_rejects_foreign_scope_docs(self) -> None:
        from skill_runtime.hybrid_rag import RagDoc, VaultIsolationError

        foreign_doc = RagDoc(
            doc_id="design:ios:card-01",
            corpus_scope="design-studio",
            title="Glass Card",
            summary="Liquid glass card inspiration",
            body="SwiftUI card",
        )
        with self.assertRaises(VaultIsolationError):
            self.router.query_handoff_rag("Glass Card", docs=[foreign_doc])

    def test_rag_query_redacts_home_paths_and_caps_bytes_when_hits_empty(self) -> None:
        home_prefix = "/" + "Users" + "/alice"
        oversized_root = Path(f"{home_prefix}/Development/" + ("nested/" * 2200) + "handoff")
        res = self.router.query_handoff_rag(
            "查询测试 " * 800,
            handoff_root=oversized_root,
            docs=[],
        )
        encoded = json.dumps(res, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), 12_000)
        self.assertNotIn(home_prefix, json.dumps(res, ensure_ascii=False))
        self.assertLessEqual(len(self.router.format_rag_query_text(res).encode("utf-8")), 12_000)

    def test_plan_use_jev_with_routing_only_mock_vs_cand_rerank_mock(self) -> None:
        seed_handoff_corpus(self.handoff_root)
        bob_prefix = "/" + "Users" + "/bob"
        # Routing-only mock_jev should not shadow or corrupt RAG hits when cand_* keys are absent.
        with patch("skill_runtime.hybrid_rag._default_jev_fn", return_value=None) as default_jev:
            routing_plan = self.router.build_plan(
                "更新 Sapphire 通知开关 ToggleStore.swift 并检查测试",
                [],
                targets_json=[
                    {
                        "topic_id": "sapphire-notification",
                        "target_repo": "bing-growth-sapphire-app",
                        "sub_prompt": "更新通知开关 ToggleStore.swift 并检查测试",
                        "rag_context_hint": f"{bob_prefix}/secret-upstream " + ("x" * 900),
                        "wave_order": 1,
                    }
                ],
                mock_jev={"answers": {"route_mode": {"choice": "ROUTE_SINGLE"}}},
                use_jev_rag=True,
                transport="stdout",
            )
            self.assertTrue(default_jev.called)
        target = routing_plan["targets"][0]
        self.assertGreaterEqual(len(target["rag_hits"]), 1)
        self.assertNotIn(bob_prefix, target["rag_context_hint"])
        self.assertLessEqual(len(target["rag_context_hint"].encode("utf-8")), 840)

        # Candidate-bearing mock_jev reranks hits during --plan --use-jev.
        reranked_plan = self.router.build_plan(
            "更新 Sapphire 通知开关 ToggleStore.swift 并检查测试",
            [],
            targets_json=[
                {
                    "topic_id": "sapphire-notification",
                    "target_repo": "bing-growth-sapphire-app",
                    "sub_prompt": "更新通知开关 ToggleStore.swift 并检查测试",
                    "wave_order": 1,
                }
            ],
            mock_jev={
                "answers": {
                    "cand_0_direct": {"noul": 0.15},
                    "cand_0_tier": {"score": 0.5},
                    "cand_1_direct": {"noul": 0.98},
                    "cand_1_tier": {"score": 3.0},
                }
            },
            use_jev_rag=True,
            transport="stdout",
        )
        self.assertIsNotNone(reranked_plan["targets"][0]["rag_hits"][0].get("jev_score"))


    def test_eval_drawer_cap_counter_case_ratio_and_internal_reality(self) -> None:
        sys.path.insert(0, str(ROOT / "skills" / "skill-retro" / "scripts"))
        from skill_retro_core.reality_check import _is_eval_counter_case, internal_reality_check

        drawer_path = ROOT / "skills" / "personal-m2-assistant" / "references" / "eval-cases.json"
        drawer = json.loads(drawer_path.read_text(encoding="utf-8"))
        cases = drawer["active_cases"]
        self.assertLessEqual(len(cases), 15)
        counter_ratio = sum(1 for c in cases if _is_eval_counter_case(c)) / len(cases)
        self.assertGreaterEqual(counter_ratio, 0.25)

        reality = internal_reality_check(ROOT / "skills" / "personal-m2-assistant")
        self.assertTrue(reality["ok"], reality)


if __name__ == "__main__":
    unittest.main()
