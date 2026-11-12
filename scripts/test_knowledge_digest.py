#!/usr/bin/env python3
"""Unit tests for knowledge-digest candidate discovery, source reconciliation, quota/cooldowns, and handle-learning-event."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_digest_vault():
    path = ROOT / "skills" / "knowledge-digest" / "scripts" / "digest_vault.py"
    spec = importlib.util.spec_from_file_location("digest_vault_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIGEST = _load_digest_vault()


class KnowledgeDigestTests(unittest.TestCase):
    def test_propose_aidash_defaults_to_candidate_only_and_emits_learning_topic_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            (tools / "repos" / "awesome-cjk-design-md").mkdir(parents=True)
            (tools / "repos" / "awesome-cjk-design-md" / "README.md").write_text(
                "# Awesome Design MD JP\n\nCJK and Latin mixed-script line breaking rules (Kinsoku Shori).\n",
                encoding="utf-8",
            )
            proposals = root / "proposals.jsonl"
            state_path = root / "learning-topics-state.json"

            res = DIGEST.discover_topics(
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 12, 8, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "selected")
            self.assertEqual(res["selected_count"], 1)
            self.assertEqual(res["archived_cards"], [])
            self.assertFalse(res["materialized"])
            self.assertFalse((vault / "concepts").exists())
            self.assertFalse((vault / "index.md").exists())

            written = res["proposals_written"]
            self.assertEqual(len(written), 1)
            rec = written[0]
            self.assertEqual(rec["id"], "learning-topic-jp-mixed-script-line-breaking")
            self.assertEqual(rec["ref"], "learning-topic://jp-mixed-script-line-breaking")
            self.assertEqual(rec["origin_ref"], "learning-topic://jp-mixed-script-line-breaking")
            self.assertEqual(rec["topic_key"], "jp-mixed-script-line-breaking")
            self.assertEqual(rec["source"], "Learning")
            self.assertEqual(rec["scope"], "personal")
            self.assertEqual(rec["priority"], "P2")
            self.assertEqual(rec["status"], "pending")
            self.assertEqual(DIGEST.validate_proposal_record(rec), [])

            # Same-day rerun must respect 1/day quota and return no_new_topic
            rerun = DIGEST.discover_topics(
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(rerun["status"], "no_new_topic")
            self.assertEqual(rerun["quota_blocked_reason"], "daily_slot_already_used")
            self.assertEqual(rerun["proposals_written"], [])

    def test_source_reconciliation_merges_hallmark_excludes_integrated_and_unknown_clones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            (vault / "concepts").mkdir(parents=True)
            (vault / "craft").mkdir(parents=True)
            (vault / "thoughts").mkdir(parents=True)
            (vault / "raw").mkdir(parents=True)

            # Seed existing vault cards (including derived_concept / derived_craft in raw)
            (vault / "concepts" / "liquid-glass-optics-and-swiftui-26-27.md").write_text(
                "---\ntitle: Liquid Glass\n---\n# Liquid Glass\n",
                encoding="utf-8",
            )
            (vault / "raw" / "wifi-video-script-breakdown.md").write_text(
                "---\ntitle: WiFi Script\nderived_concept: ../concepts/wifi-invisible-light.md\nderived_craft: ../craft/science-video-invisible-to-visible.md\n---\n# Raw WiFi\n",
                encoding="utf-8",
            )

            tools = root / "collected-tools"
            (tools / "repos" / "hallmark").mkdir(parents=True)
            (tools / "repos" / "hallmark" / "README.md").write_text("# Hallmark repo\n", encoding="utf-8")
            # Case A: hallmark.md WITHOUT integration evidence -> merges with repos/hallmark into 1 entity (not 2)
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\nWeb anti-slop anatomy reference.\n",
                encoding="utf-8",
            )
            # Covered canonical entity via concepts/liquid-glass-optics-and-swiftui-26-27.md
            (tools / "repos" / "awesome-ios-design-md").mkdir(parents=True)
            (tools / "repos" / "awesome-ios-design-md" / "README.md").write_text("# iOS Design MD\n", encoding="utf-8")
            # Reconciled queued entity
            (tools / "repos" / "bama").mkdir(parents=True)
            (tools / "repos" / "bama" / "README.md").write_text("# BAMA\n\nAgent CLI verification.\n", encoding="utf-8")
            # Unverified repo clone without note or topic-ledger entry -> unknown
            (tools / "repos" / "mystery-unverified-clone").mkdir(parents=True)
            (tools / "repos" / "mystery-unverified-clone" / "README.md").write_text("# Mystery\n", encoding="utf-8")

            cands_a, summary_a = DIGEST._reconcile_collected_tools_entities(
                tools,
                DIGEST._existing_vault_corpus(vault),
                vault_dir=vault,
            )
            self.assertIn("hallmark", summary_a["merged_entities"])
            self.assertIn("awesome-ios-design-md", summary_a["covered_sources"])
            self.assertIn("mystery-unverified-clone", summary_a["unknown_sources"])
            hallmark_cands = [c for c in cands_a if c["source_id"] == "hallmark"]
            self.assertEqual(len(hallmark_cands), 1)
            self.assertEqual(hallmark_cands[0]["topic_key"], "hallmark-anti-slop-anatomy")
            self.assertFalse(any(c["source_id"] == "mystery-unverified-clone" for c in cands_a))

            # Case B: hallmark.md WITH integration evidence -> excluded from first-time exploration
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\n## 实际使用与接入记录\n- 已接入 design-studio Entry A (A1..A4) 統合済み。\n",
                encoding="utf-8",
            )
            cands_b, summary_b = DIGEST._reconcile_collected_tools_entities(
                tools,
                DIGEST._existing_vault_corpus(vault),
                vault_dir=vault,
            )
            self.assertIn("hallmark", summary_b["integrated_sources"])
            self.assertFalse(any(c["source_id"] == "hallmark" for c in cands_b))
            self.assertEqual([c["topic_key"] for c in cands_b], ["bama-agent-cli-story-verification"])

            # Verify raw WiFi file with derived_concept is treated as covered, not uncovered raw
            cov = DIGEST._inspect_vault_coverage(vault)
            self.assertIn("raw/wifi-video-script-breakdown.md", cov["covered_raw_files"])
            self.assertEqual(cov["uncovered_raw_items"], [])

            # Verify empty git roots never fabricate fallback git probe topics
            self.assertEqual(DIGEST._scan_git_tech_stack_candidates((), cov["corpus_text"]), [])

    def test_pending_cap_cooldowns_and_sync_triage_separation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            for slug in ("awesome-cjk-design-md", "bama", "genjutsu", "impeccable", "taste-skill"):
                (tools / "repos" / slug).mkdir(parents=True)
                (tools / "repos" / slug / "README.md").write_text(f"# {slug}\n\nConcrete details.\n", encoding="utf-8")

            proposals = root / "proposals.jsonl"
            state_path = root / "learning-topics-state.json"

            # Propose on 3 distinct CST days to reach pending cap = 3
            for day_num, exp_tk in (
                (10, "jp-mixed-script-line-breaking"),
                (11, "bama-agent-cli-story-verification"),
                (12, "genjutsu-three-tier-choreography-gate"),
            ):
                res = DIGEST.discover_topics(
                    propose_aidash=True,
                    vault_dir=vault,
                    collected_tools_dir=tools,
                    git_roots=(),
                    proposals_path=proposals,
                    state_path=state_path,
                    now_utc=datetime(2026, 11, day_num, 6, 0, 0, tzinfo=timezone.utc),
                )
                self.assertEqual(res["status"], "selected")
                self.assertEqual(res["topics"][0]["topic_key"], exp_tk)

            # Day 13: 3 pending items exist -> blocked by pending_cap_reached
            day13 = DIGEST.discover_topics(
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 13, 6, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(day13["status"], "no_new_topic")
            self.assertEqual(day13["quota_blocked_reason"], "pending_cap_reached")
            self.assertEqual(day13["proposals_written"], [])

            # Transfer item 1 via sync_triage (simulating inbox.sync_triage rewriting ref to linear://TIA-901)
            transfer_res = DIGEST.handle_learning_event(
                action="sync_triage",
                topic_key="jp-mixed-script-line-breaking",
                external_id="linear://TIA-901",
                event_id="evt-sync-1",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
                now_utc=datetime(2026, 11, 13, 7, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(transfer_res["ok"])
            self.assertEqual(transfer_res["learning_state"], "transferred")
            self.assertFalse(transfer_res["exploration_authorized"])
            self.assertEqual(transfer_res["external_id"], "linear://TIA-901")

            # Dismiss item 2 -> 30d cooldown, no auto-revive even after 40 days
            dismiss_res = DIGEST.handle_learning_event(
                action="Dismiss",
                topic_key="bama-agent-cli-story-verification",
                event_id="evt-dismiss-1",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
                now_utc=datetime(2026, 11, 13, 7, 5, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(dismiss_res["learning_state"], "dismissed")
            self.assertFalse(dismiss_res["auto_revive"])
            self.assertEqual(dismiss_res["next_eligible_at"], "2026-12-13")

            # Snooze item 3 -> 7d cooldown (eligible on 2026-11-20)
            snooze_res = DIGEST.handle_learning_event(
                action="Snooze",
                topic_key="genjutsu-three-tier-choreography-gate",
                event_id="evt-snooze-1",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
                now_utc=datetime(2026, 11, 13, 7, 10, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(snooze_res["learning_state"], "snoozed")
            self.assertEqual(snooze_res["next_eligible_at"], "2026-11-20")

            # On 2026-11-15 (within 7d snooze): transferred, dismissed, and snoozed are all excluded; next queued item is impeccable
            day15 = DIGEST.discover_topics(
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 15, 6, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(day15["status"], "selected")
            self.assertEqual(day15["topics"][0]["topic_key"], "impeccable-mode-operate-vs-persuade-critique")
            self.assertEqual(day15["excluded_reasons"]["jp-mixed-script-line-breaking"], "already_transferred_to_linear")
            self.assertEqual(
                day15["excluded_reasons"]["bama-agent-cli-story-verification"],
                "dismissed_cooldown_or_no_auto_revive",
            )
            self.assertEqual(
                day15["excluded_reasons"]["genjutsu-three-tier-choreography-gate"],
                "snoozed_cooldown",
            )

            # On 2026-11-21 (after 7d snooze elapsed): genjutsu is eligible again, while dismissed bama still does NOT auto-revive
            day21 = DIGEST.discover_topics(
                propose_aidash=True,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 21, 6, 0, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(day21["status"], "selected")
            self.assertEqual(day21["topics"][0]["topic_key"], "genjutsu-three-tier-choreography-gate")
            self.assertEqual(
                day21["excluded_reasons"]["bama-agent-cli-story-verification"],
                "dismissed_cooldown_or_no_auto_revive",
            )

    def test_handle_learning_event_star_guard_approve_then_done_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            proposals = root / "proposals.jsonl"
            state_path = root / "learning-topics-state.json"

            # 1. Star on learning-topic:// is a harmless bookmark and never publishes blog or syncs design-studio
            star_res = DIGEST.handle_learning_event(
                action="Star",
                ref="learning-topic://jp-mixed-script-line-breaking",
                event_id="evt-star-100",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
            )
            self.assertTrue(star_res["ok"])
            self.assertEqual(star_res["action"], "bookmarked")
            self.assertFalse(star_res["blog_published"])
            self.assertFalse(star_res["exploration_authorized"])
            self.assertFalse(star_res["exploration_started"])
            self.assertFalse(star_res["design_studio_synced"])

            # Calling publish_blog directly with learning-topic:// is rejected
            with self.assertRaises(ValueError):
                DIGEST.publish_blog(slug="learning-topic://jp-mixed-script-line-breaking", vault_dir=vault)

            # 2. Owner Approve (深度调研) authorizes exploration with route="deep-research"
            app_res = DIGEST.handle_learning_event(
                action="深度调研",
                topic_key="jp-mixed-script-line-breaking",
                event_id="evt-approve-200",
                note="重点对比 Web CSS line-break: strict 与 Apple TextKit 2",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
            )
            self.assertTrue(app_res["ok"])
            self.assertFalse(app_res["deduplicated"])
            self.assertEqual(app_res["learning_state"], "approved")
            self.assertTrue(app_res["exploration_authorized"])
            self.assertEqual(app_res["exploration_route"], "deep-research")
            self.assertFalse(app_res["design_studio_synced"])
            self.assertEqual(DIGEST.validate_proposal_record(app_res["proposal_written"]), [])

            # Replay of the exact same event_id is deduplicated idempotently
            replay_res = DIGEST.handle_learning_event(
                action="深度调研",
                topic_key="jp-mixed-script-line-breaking",
                event_id="evt-approve-200",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
            )
            self.assertTrue(replay_res["ok"])
            self.assertTrue(replay_res["deduplicated"])
            self.assertEqual(replay_res["revision"], app_res["revision"])

            # 3. Subsequent Owner Done with a new event_id on the same topic_key is NOT swallowed
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = DIGEST.main(
                    [
                        "handle-learning-event",
                        "--action",
                        "Done",
                        "--topic-key",
                        "jp-mixed-script-line-breaking",
                        "--event-id",
                        "evt-done-300",
                        "--proposals-path",
                        str(proposals),
                        "--state-path",
                        str(state_path),
                        "--vault",
                        str(vault),
                    ]
                )
            self.assertEqual(rc, 0)
            done_res = json.loads(buf.getvalue())
            self.assertTrue(done_res["ok"])
            self.assertFalse(done_res["deduplicated"])
            self.assertEqual(done_res["learning_state"], "learned")
            self.assertEqual(done_res["outcome"], "owner_reported")
            self.assertGreater(done_res["revision"], app_res["revision"])
            self.assertEqual(DIGEST.validate_proposal_record(done_res["proposal_written"]), [])
            self.assertEqual(done_res["proposal_written"]["status"], "dismissed")

            # 4. Late/stale Approve after terminal learned state is rejected unless Reopen is called
            late_app = DIGEST.handle_learning_event(
                action="Approve",
                topic_key="jp-mixed-script-line-breaking",
                event_id="evt-late-400",
                proposals_path=proposals,
                state_path=state_path,
                vault_dir=vault,
            )
            self.assertFalse(late_app["ok"])
            self.assertTrue(late_app["conflict"])
            self.assertEqual(late_app["learning_state"], "learned")


if __name__ == "__main__":
    unittest.main()
