#!/usr/bin/env python3
"""Unit tests for knowledge-digest candidate discovery, source reconciliation, quota/cooldowns, and handle-learning-event."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_digest_vault():
    path = ROOT / "skills" / "knowledge-digest" / "scripts" / "digest_vault.py"
    spec = importlib.util.spec_from_file_location("digest_vault_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIGEST = _load_digest_vault()
SEED = json.loads((ROOT / "skills/knowledge-digest/tests/personal-topic-ledger-seed.json").read_text(encoding="utf-8"))


def seed_ledger(vault: Path, source_ids: tuple[str, ...]) -> None:
    topics = []
    for index, sid in enumerate(source_ids, 1):
        item = dict(SEED["CANONICAL_COLLECTED_TOOLS_RECONCILIATION"][sid])
        item.update(learning_state="queued", source_fingerprint="fixture-source", rank=index, scope="personal")
        topics.append(item)
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "topic-ledger.json").write_text(json.dumps({"topics": topics}), encoding="utf-8")


class FakeLinearTransport:
    def __init__(self, issues=None):
        self.issues = list(issues or [])
        self.created = []
        self.updated = []

    def list_issues(self, team_key, *, query="", limit=50):
        return [issue for issue in self.issues if not query or query in issue.get("title", "")]

    def team_metadata(self, team_key):
        return {"id": "team-tia", "states": {"nodes": [{"id": "backlog", "type": "backlog"}]}}

    def create_issue(self, **kwargs):
        issue = {
            "id": "issue-created",
            "identifier": "TIA-900",
            "url": "https://linear.test/TIA-900",
            **kwargs,
        }
        self.created.append(issue)
        self.issues.append(issue)
        return issue

    def update_issue(self, issue_id, **kwargs):
        issue = next(issue for issue in self.issues if issue.get("id") == issue_id or issue.get("identifier") == issue_id)
        issue.update(kwargs)
        self.updated.append(issue)
        return issue


def normalized_linear_markdown(description: str) -> str:
    description = re.sub(r"\]\((?!<)([^)]+)\)", r"](<\1>)", description)
    description = re.sub(r"(?m)^  - ", "  * ", description)
    description = re.sub(r"(?m)^    1\. ", "    2. ", description)
    return re.sub(r"(?<!\\)\[(\d+)\]", r"\\[\1\\]", description).rstrip()


def digest_topics():
    topics = []
    for key, question in [
        ("event-stream-order", "事件流如何用序列号恢复顺序而不重复执行副作用？"),
        ("cache-budget", "缓存如何用容量预算约束淘汰而不破坏一致性？"),
        ("schema-transition", "迁移器如何把旧模式转为新模式但保留回滚边界？"),
    ]:
        item = {"topic_key": key, "title": question, "question": question, "source_id": "fixture-engine", "source_uri": "https://example.test/engine", "why_now": "engine/contract.md:12（顺序与容量边界尚未验证）", "category": "工程与设计"}
        item["learning_brief"] = DIGEST._learning_brief(item)
        topics.append(item)
    return topics


def legacy_daily_description(topics):
    lines = ["<!-- GTT-LINEAR-DEDUP:daily-learn-2026-11-14 -->", "", "## 今日学习清单", ""]
    lines.extend(f"- [ ] **[{index}] {item['title']}** (`{item['topic_key']}`)" for index, item in enumerate(topics, 1))
    for item in topics:
        lines.extend(["", f"### {item['title']} (`{item['topic_key']}`)", "", f"**为什么现在值得学 (Why Now)**：Owner 编写的完整导读 {item['topic_key']}", "", "1. 原理与状态转换？", "2. 容量为何影响顺序？", "3. 如何构造失败用例？", "", f"**推荐展开命令**：`/learn {item['topic_key']}` 或 `/deep-research {item['topic_key']}`", "**来源与定位**：[engine](https://example.test/engine)"])
    return "\n".join(lines)


def old_appended_daily_description(topics):
    lines = ["<!-- GTT-LINEAR-DEDUP:daily-learn-2026-11-14 -->", "", "候选学习仅在 Owner Gate 批准后展开；本 issue 不自动归档或发布。", ""]
    for item in topics:
        brief = DIGEST._legacy_automated_learning_brief(item)
        lines.extend([f"- [ ] [{item['topic_key']}](learning-topic://{item['topic_key']}) {item['title']}", f"  - 为什么现在值得学：{brief['why_now']}", "  - 3 个核心痛点追问预览：", *[f"    1. {q}" for q in brief["preview_questions"]], f"  - 推荐展开命令：`{brief['recommended_command']}`", ""])
    return "\n".join(lines)


class KnowledgeDigestTests(unittest.TestCase):
    def assert_daily_topics_once(self, description, topics):
        rows = re.findall(r"(?m)^[-*+] \[[ xX]\] .+$", description)
        self.assertEqual(len(rows), len(topics))
        for item in topics:
            self.assertEqual(sum(f"learning-topic://{item['topic_key']}" in row or f"`{item['topic_key']}`" in row for row in rows), 1)
        self.assertEqual(description.count("KD-DAILY-START:2026-11-14"), 1)
        self.assertEqual(description.count("KD-DAILY-END:2026-11-14"), 1)

    def test_legacy_daily_adoption_preserves_rich_briefs_checks_and_notes(self):
        topics = digest_topics()
        original = normalized_linear_markdown(legacy_daily_description(topics)).replace("- [ ]", "* [X]", 1)
        original += "\n\nOwner notes: preserve `custom_contract` and [link](<https://example.test/note>)."
        merged = DIGEST._merge_daily_digest_description(original, DIGEST._daily_digest_markdown("2026-11-14", topics), topics)
        self.assert_daily_topics_once(merged, topics)
        self.assertIn("* [x]", merged)
        self.assertIn("Owner notes: preserve `custom_contract`", merged)
        self.assertEqual(merged.count("Owner 编写的完整导读"), 3)
        for item in topics:
            section = original[original.index(f"### {item['title']}"):].split("\n### ", 1)[0]
            self.assertIn(section, merged)
        self.assertEqual(DIGEST._merge_daily_digest_description(merged, DIGEST._daily_digest_markdown("2026-11-14", topics), topics), merged)

    def test_duplicate_daily_repair_recognizes_normalized_old_generated_fields(self):
        topics = digest_topics()
        legacy = legacy_daily_description(topics)
        appended = old_appended_daily_description(topics).replace("- [ ]", "- [x]", 1)
        appended = appended.replace(topics[0]["title"], topics[0]["title"] + " Owner inline note: keep this too.", 1)
        appended = appended.replace("  - 3 个核心痛点追问预览：", "  - Owner note: keep my extra constraint.\n  - 3 个核心痛点追问预览：", 1)
        original = normalized_linear_markdown(legacy + "\n\nOwner note between blocks.\n\n" + appended + "\nOwner final note.")
        merged = DIGEST._merge_daily_digest_description(original, DIGEST._daily_digest_markdown("2026-11-14", topics), topics)
        self.assert_daily_topics_once(merged, topics)
        self.assertEqual(merged.count("Owner 编写的完整导读"), 3)
        self.assertNotIn("3 个核心痛点追问预览：", merged)
        self.assertEqual(merged.count("GTT-LINEAR-DEDUP:"), 1)
        self.assertIn("- [x]", merged)
        for note in ("Owner note between blocks.", "Owner note: keep my extra constraint.", "Owner inline note: keep this too.", "Owner final note."):
            self.assertIn(note, merged)
        roundtrip = normalized_linear_markdown(merged)
        self.assertEqual(DIGEST._merge_daily_digest_description(roundtrip, DIGEST._daily_digest_markdown("2026-11-14", topics), topics), roundtrip)

    def test_duplicate_edited_guide_and_broken_boundaries_fail_safe(self):
        topics = digest_topics()
        edited = old_appended_daily_description(topics).replace("输入/输出边界是什么？", "Owner changed mechanism and evidence?", 1)
        original = legacy_daily_description(topics) + "\n\n" + edited
        issue = {"id": "issue-1", "title": "[Daily Learn 2026-11-14] 今日 3 个推荐学习主题", "description": original}
        transport = FakeLinearTransport([issue])
        with self.assertRaisesRegex(ValueError, "Cannot safely remove"):
            DIGEST._upsert_linear_daily_issue(transport, "2026-11-14", topics)
        self.assertEqual(transport.updated, [])
        self.assertEqual(issue["description"], original)
        broken = DIGEST._daily_digest_markdown("2026-11-14", topics).replace("<!-- KD-DAILY-END:2026-11-14 -->", "")
        with self.assertRaisesRegex(ValueError, "boundaries"):
            DIGEST._merge_daily_digest_description(broken, DIGEST._daily_digest_markdown("2026-11-14", topics), topics)

    def test_new_normalized_daily_issue_is_not_rewritten_after_owner_edits(self):
        topics = digest_topics()
        topics[0]["learning_guide"] = {"why_now": "Owner rich guide", "preview_questions": ["Specific input?", "Ordering constraint?", "Failure witness?"], "recommended_command": "/learn event-stream-order"}
        topics[0]["learning_brief"] = DIGEST._learning_brief(topics[0])
        transport = FakeLinearTransport()
        issue = DIGEST._upsert_linear_daily_issue(transport, "2026-11-14", topics)
        issue["description"] = normalized_linear_markdown(issue["description"]).replace("- [ ]", "- [x]", 1).replace("Owner rich guide", "Owner rich guide with new private evidence") + "\n\n## Owner notes\nDo not overwrite."
        original = issue["description"]
        rerun = DIGEST._upsert_linear_daily_issue(transport, "2026-11-14", topics)
        self.assertEqual(rerun["description"], original)
        self.assertEqual(len(transport.created), 1)
        self.assertEqual(transport.updated, [])
        self.assert_daily_topics_once(original, topics)
        self.assertEqual(original.count("为什么现在值得学："), 3)

    def test_generated_duplicate_without_legacy_heading_keeps_one_brief(self):
        topics = digest_topics()
        old = normalized_linear_markdown(old_appended_daily_description(topics))
        merged = DIGEST._merge_daily_digest_description(old + "\n\n" + old, DIGEST._daily_digest_markdown("2026-11-14", topics), topics)
        self.assert_daily_topics_once(merged, topics)
        self.assertEqual(merged.count("为什么现在值得学："), 3)
        self.assertEqual(DIGEST._merge_daily_digest_description(merged, DIGEST._daily_digest_markdown("2026-11-14", topics), topics), merged)

    def test_cli_legacy_repair_and_repeat_use_real_graphql_with_normalization(self):
        self._exercise_normalized_cli(duplicate=True)

    def test_cli_legacy_only_recovers_exact_keys_without_local_daily_state(self):
        self._exercise_normalized_cli(duplicate=False)

    def _exercise_normalized_cli(self, *, duplicate):
        sync_mod = DIGEST._load_helper_module("normalized_linear_sync", ROOT / "skills/team-lead/scripts/linear-sync.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault, tools = root / "vault", root / "tools"
            source_ids = ("awesome-cjk-design-md", "bama", "genjutsu")
            seed_ledger(vault, source_ids)
            for source_id in source_ids:
                readme = tools / "repos" / source_id / "README.md"
                readme.parent.mkdir(parents=True)
                readme.write_text("# Fixture source\n", encoding="utf-8")
            topics = json.loads((vault / "topic-ledger.json").read_text())["topics"]
            for item in topics:
                item["learning_brief"] = DIGEST._learning_brief(item)
            issue = {"id": "issue-existing", "identifier": "TIA-414", "url": "https://linear.test/TIA-414", "title": "[Daily Learn 2026-11-14] 今日 3 个推荐学习主题", "description": normalized_linear_markdown(legacy_daily_description(topics) + ("\n\n" + old_appended_daily_description(topics) if duplicate else ""))}
            writes = []
            def boundary(query, variables):
                if "ListIssues" in query:
                    return {"data": {"issues": {"nodes": [dict(issue)]}}}
                if "UpdateIssue" in query:
                    writes.append(variables)
                    issue.update(variables["input"])
                    issue["description"] = normalized_linear_markdown(issue["description"])
                    return {"data": {"issueUpdate": {"success": True, "issue": dict(issue)}}}
                raise AssertionError("Unexpected network operation: " + query)
            client = sync_mod.GraphQLClient("fixture-token", transport=boundary)
            args = ["discover-topics", "--top", "3", "--propose-aidash", "--propose-linear", "--no-materialize", "--no-git", "--vault", str(vault), "--collected-tools-dir", str(tools), "--proposals-path", str(root / "proposals.jsonl"), "--state-path", str(root / "state.json")]
            with mock.patch.object(DIGEST, "_linear_transport_from_repo", return_value=client), mock.patch.object(DIGEST, "datetime", wraps=datetime) as clock:
                clock.now.return_value = datetime(2026, 11, 14, 2, tzinfo=timezone.utc)
                first_output = io.StringIO()
                with contextlib.redirect_stdout(first_output):
                    self.assertEqual(DIGEST.main(args), 0)
                first = json.loads(first_output.getvalue())
                self.assertEqual(first["selected_count"], 3)
                repaired = issue["description"]
                self.assert_daily_topics_once(repaired, topics)
                self.assertEqual(repaired.count("Owner 编写的完整导读"), 3)
                done_key = topics[0]["topic_key"]
                done = DIGEST.handle_learning_event(action="Done", topic_key=done_key, event_id="owner-done", proposals_path=root / "proposals.jsonl", state_path=root / "state.json", vault_dir=vault, now_utc=datetime(2026, 11, 14, 2, 1, tzinfo=timezone.utc))
                self.assertTrue(done["ok"])
                issue["description"] = issue["description"].replace("- [ ]", "- [x]", 1) + "\nOwner related reference: [another topic](<learning-topic://not-a-daily-selection>)."
                repaired = issue["description"]
                # A changed CLI quota must not truncate an already fixed daily selection.
                args[2] = "1"
                second_output = io.StringIO()
                with contextlib.redirect_stdout(second_output):
                    self.assertEqual(DIGEST.main(args), 0)
                second = json.loads(second_output.getvalue())
            self.assertEqual(second["selected_count"], 3)
            self.assertEqual(issue["description"], repaired)
            self.assertEqual(len(writes), 1)
            self.assertEqual(second["proposals_written"], [])
            self.assertEqual({topic["topic_key"] for topic in second["topics"]}, {topic["topic_key"] for topic in topics})
            state = json.loads((root / "state.json").read_text())
            self.assertEqual(set(state["daily_linear_issues"]["2026-11-14"]["topic_keys"]), {topic["topic_key"] for topic in topics})
            self.assertEqual(state["topics"][done_key]["learning_state"], "learned")
            self.assertEqual(state["topics"][done_key]["revision"], done["revision"])
            self.assertFalse(state["topics"][done_key]["exploration_authorized"])
            self.assertEqual(len((root / "proposals.jsonl").read_text().splitlines()), 4)
            self.assertFalse((vault / "concepts").exists())
            self.assertFalse((vault / "index.md").exists())
            with mock.patch.object(client, "list_issues", return_value=[]):
                missing = DIGEST.discover_topics(top=3, propose_linear=True, linear_transport=client, vault_dir=vault, collected_tools_dir=tools, git_roots=(), proposals_path=root / "proposals.jsonl", state_path=root / "state.json", now_utc=datetime(2026, 11, 14, 3, tzinfo=timezone.utc))
            self.assertFalse(missing["ok"])
            self.assertIn("refusing to create a duplicate", missing["error"])
            self.assertEqual(len(writes), 1)

    def test_learning_questions_use_mechanism_constraint_and_evidence(self):
        item = digest_topics()[0]
        questions = item["learning_brief"]["preview_questions"]
        self.assertEqual(len(set(questions)), 3)
        self.assertNotIn(item["question"], "\n".join(questions))
        self.assertIn("序列号恢复顺序", questions[0])
        self.assertIn("重复执行副作用", questions[1])
        self.assertIn("engine/contract.md:12", questions[2])

    def test_linear_three_topic_quota_and_structured_briefs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault, tools = root / "vault", root / "tools"
            source_ids = ("awesome-cjk-design-md", "bama", "genjutsu")
            for source_id in source_ids:
                readme = tools / "repos" / source_id / "README.md"
                readme.parent.mkdir(parents=True)
                readme.write_text(f"# {source_id}\\nConcrete engineering gap.\\n", encoding="utf-8")
            seed_ledger(vault, source_ids)
            transport = FakeLinearTransport()
            result = DIGEST.discover_topics(
                top=3,
                propose_aidash=True,
                propose_linear=True,
                linear_transport=transport,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=root / "proposals.jsonl",
                state_path=root / "state.json",
                now_utc=datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["selected_count"], 3)
            self.assertEqual(len(transport.created), 1)
            for topic in result["topics"]:
                brief = topic["learning_brief"]
                self.assertTrue(brief["why_now"])
                self.assertEqual(len(brief["preview_questions"]), 3)
                self.assertRegex(brief["recommended_command"], r"^/(learn|deep-research) ")

    def test_explicit_top_three_without_linear_and_default_one_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, vault, tools = Path(tmp), Path(tmp) / "vault", Path(tmp) / "tools"
            source_ids = ("awesome-cjk-design-md", "bama", "genjutsu")
            for source_id in source_ids:
                readme = tools / "repos" / source_id / "README.md"
                readme.parent.mkdir(parents=True)
                readme.write_text(f"# {source_id}\\n", encoding="utf-8")
            seed_ledger(vault, source_ids)
            result = DIGEST.discover_topics(top=3, propose_aidash=True, vault_dir=vault, collected_tools_dir=tools, git_roots=(), proposals_path=root / "proposals.jsonl", state_path=root / "state.json", now_utc=datetime(2026, 11, 14, 2, tzinfo=timezone.utc))
            self.assertEqual(result["selected_count"], 3)
            default = DIGEST.discover_topics(propose_aidash=True, vault_dir=vault, collected_tools_dir=tools, git_roots=(), proposals_path=root / "other.jsonl", state_path=root / "other-state.json", now_utc=datetime(2026, 11, 15, 2, tzinfo=timezone.utc))
            self.assertEqual(default["selected_count"], 1)

    def test_real_repository_linear_transport_and_adapter_boundary(self) -> None:
        sync_path = ROOT / "skills" / "team-lead" / "scripts" / "linear-sync.py"
        sync_mod = DIGEST._load_helper_module("real_linear_sync_for_test", sync_path)
        self.assertIsNotNone(sync_mod)
        with mock.patch.object(DIGEST, "_load_helper_module", return_value=sync_mod), mock.patch.object(sync_mod, "load_api_key", return_value="test-token"):
            client = DIGEST._linear_transport_from_repo()
        self.assertIsInstance(client, sync_mod.GraphQLClient)
        self.assertEqual(client.api_key, "test-token")

        issue = {"id": "issue-1", "identifier": "TIA-1", "url": "https://linear.test/TIA-1", "title": "[Daily Learn 2026-11-14] 今日 3 个推荐学习主题", "description": "<!-- GTT-LINEAR-DEDUP:daily-learn-2026-11-14 -->\\nlearning-topic://bama-agent-cli-story-verification"}
        calls = []
        def boundary(query, variables):
            calls.append((query, variables))
            if "ListIssues" in query:
                return {"data": {"issues": {"nodes": [issue]}}}
            if "UpdateIssue" in query:
                return {"data": {"issueUpdate": {"success": True, "issue": issue}}}
            if "TeamMetadata" in query:
                return {"data": {"teams": {"nodes": [{"id": "team", "states": {"nodes": []}}]}}}
            if "CreateIssue" in query:
                return {"data": {"issueCreate": {"success": True, "issue": {**issue, "id": "issue-2", "identifier": "TIA-2"}}}}
            raise AssertionError(query)

        real_client = sync_mod.GraphQLClient("test-token", transport=boundary)
        topic = {"topic_key": "bama-agent-cli-story-verification", "title": "BAMA", "learning_brief": {"why_now": "source gap", "preview_questions": ["q1", "q2", "q3"], "recommended_command": "/learn bama-agent-cli-story-verification"}}
        updated = DIGEST._upsert_linear_daily_issue(real_client, "2026-11-14", [topic])
        self.assertEqual(updated["identifier"], "TIA-1")
        self.assertTrue(any("UpdateIssue" in query for query, _ in calls))
        calls.clear()
        empty_client = sync_mod.GraphQLClient("test-token", transport=lambda q, v: {"data": {"issues": {"nodes": []}}} if "ListIssues" in q else {"data": {"teams": {"nodes": [{"id": "team", "states": {"nodes": []}}]}}} if "TeamMetadata" in q else {"data": {"issueCreate": {"success": True, "issue": {**issue, "identifier": "TIA-2"}}}})
        created = DIGEST._upsert_linear_daily_issue(empty_client, "2026-11-14", [topic])
        self.assertEqual(created["identifier"], "TIA-2")

    def test_linear_failure_persists_registry_and_cli_returns_nonzero(self) -> None:
        class FailingTransport(FakeLinearTransport):
            def create_issue(self, **kwargs):
                raise RuntimeError("Linear credentials are not configured")

        with tempfile.TemporaryDirectory() as tmp:
            root, vault, tools = Path(tmp), Path(tmp) / "vault", Path(tmp) / "tools"
            readme = tools / "repos" / "awesome-cjk-design-md" / "README.md"
            readme.parent.mkdir(parents=True)
            readme.write_text("# source\\n", encoding="utf-8")
            seed_ledger(vault, ("awesome-cjk-design-md",))
            proposals, state = root / "proposals.jsonl", root / "state.json"
            result = DIGEST.discover_topics(top=3, propose_aidash=True, propose_linear=True, linear_transport=FailingTransport(), vault_dir=vault, collected_tools_dir=tools, git_roots=(), proposals_path=proposals, state_path=state, now_utc=datetime(2026, 11, 14, 2, tzinfo=timezone.utc))
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "linear_error")
            self.assertTrue(proposals.exists())
            self.assertTrue(state.exists())
        with mock.patch.object(DIGEST, "discover_topics", return_value={"ok": False, "error": "safe failure"}):
            self.assertEqual(DIGEST.main(["discover-topics"]), 1)

    def test_linear_reconciles_remote_issue_when_local_daily_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault, tools = root / "vault", root / "tools"
            source_ids = ("awesome-cjk-design-md", "bama", "genjutsu")
            for source_id in source_ids:
                readme = tools / "repos" / source_id / "README.md"
                readme.parent.mkdir(parents=True)
                readme.write_text(f"# {source_id}\\nConcrete engineering gap.\\n", encoding="utf-8")
            seed_ledger(vault, source_ids)
            day = "2026-11-14"
            title = f"[Daily Learn {day}] 今日 3 个推荐学习主题"
            marker = f"<!-- GTT-LINEAR-DEDUP:daily-learn-{day} -->"
            keys = ["jp-mixed-script-line-breaking", "bama-agent-cli-story-verification", "genjutsu-three-tier-choreography-gate"]
            remote = {"id": "issue-existing", "identifier": "TIA-414", "url": "https://linear.test/TIA-414", "title": title, "description": marker + "\\n- [x] owner checked bama\\nOwner note: preserve this guide.\\n" + "\\n".join(f"learning-topic://{key}" for key in keys)}
            transport = FakeLinearTransport([remote])
            result = DIGEST.discover_topics(
                top=3, propose_aidash=True, propose_linear=True, linear_transport=transport,
                vault_dir=vault, collected_tools_dir=tools, git_roots=(),
                proposals_path=root / "proposals.jsonl", state_path=root / "missing-state.json",
                now_utc=datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["linear_issue"]["identifier"], "TIA-414")
            self.assertEqual(len(transport.created), 0)
            self.assertEqual(len(transport.updated), 1)
            self.assertEqual(result["selected_count"], 3)
            self.assertIn(marker, transport.updated[0]["description"])
            state = json.loads((root / "missing-state.json").read_text())
            self.assertEqual(state["daily_linear_digests"][day]["external_id"], "linear://TIA-414")
            self.assertEqual({state["topics"][key]["external_id"] for key in keys}, {"linear://TIA-414"})
            ledger = json.loads((vault / "topic-ledger.json").read_text())
            self.assertEqual({item["external_id"] for item in ledger["topics"]}, {"linear://TIA-414"})
            self.assertIn("- [x] owner checked bama", transport.updated[0]["description"])
            self.assertIn("Owner note: preserve this guide.", transport.updated[0]["description"])

            rerun = DIGEST.discover_topics(
                top=3, propose_aidash=True, propose_linear=True, linear_transport=transport,
                vault_dir=vault, collected_tools_dir=tools, git_roots=(),
                proposals_path=root / "proposals.jsonl", state_path=root / "missing-state.json",
                now_utc=datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(rerun["selected_count"], 3)
            self.assertEqual(len(transport.created), 0)
            self.assertEqual(len(transport.updated), 1)
            records = [json.loads(line) for line in (root / "proposals.jsonl").read_text().splitlines()]
            self.assertEqual(len(records), 3)

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
            seed_ledger(vault, ("awesome-cjk-design-md",))

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

            seed_ledger(vault, ("hallmark", "awesome-ios-design-md", "bama"))
            ledger_file = vault / "topic-ledger.json"
            ledger = json.loads(ledger_file.read_text())
            ledger["topics"][1]["learning_state"] = "learned"
            ledger_file.write_text(json.dumps(ledger))
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
            # Usage evidence is reconciled explicitly into the authority ledger; a generic note marker alone grants no learned state.
            ledger["topics"][0]["learning_state"] = "learned"
            ledger_file.write_text(json.dumps(ledger))
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

            seed_ledger(vault, ("awesome-cjk-design-md", "bama", "genjutsu", "impeccable", "taste-skill"))

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

    def test_personal_topic_ledger_v1_schema_preservation_and_offline_discovery_gap(self) -> None:
        fixture_env = os.environ.get("PERSONAL_TOPIC_LEDGER_FIXTURE", "")
        fixture_src = (
            Path(fixture_env)
            if fixture_env
            else Path.home() / "Development" / "handoff" / "personal-daily-learning-topics" / "fixtures" / "personal-topic-ledger-v1.json"
        )
        if fixture_src.is_file():
            canonical_doc = json.loads(fixture_src.read_text(encoding="utf-8"))
        else:
            canonical_doc = {
                "schema_version": "personal-topic-ledger/v1",
                "updated_at": "2026-11-12T21:55:00+08:00",
                "policy": dict(SEED["_CANONICAL_FIXTURE_DOC"]["policy"]),
                "vault_coverage_inventory": list(SEED["_CANONICAL_FIXTURE_DOC"]["vault_coverage_inventory"]),
                "sources": [
                    {
                        "source_id": sid,
                        "source_uri": meta["source_uri"],
                        "local_paths": [f"collected-tools/repos/{sid}"],
                        "git_head_sha": "deadbeef1234",
                        "design_integration": "verified",
                        "reconciliation_status": "learned" if meta.get("default_state") != "queued" else "queued_subtopic",
                        "coverage_refs": meta.get("coverage_refs", []),
                    }
                    for sid, meta in SEED["CANONICAL_COLLECTED_TOOLS_RECONCILIATION"].items()
                ],
                "topics": [
                    *(
                        {
                            "source_id": sid,
                            "source_uri": meta["source_uri"],
                            "topic_key": meta["topic_key"],
                            "aliases": meta["aliases"],
                            "question": meta["question"],
                            "title": meta["title"],
                            "category": meta["category"],
                            "coverage_refs": meta["coverage_refs"],
                            "why_now": meta["why_now"],
                            "learning_state": "learned" if meta.get("default_state") != "queued" else "queued",
                            "rank": 0 if meta.get("default_state") != "queued" else meta.get("rank_order", 1),
                            "priority_rank": None if meta.get("default_state") != "queued" else meta.get("rank_order", 1),
                            "scope": "personal",
                            "schema_version": 1,
                            "source_kind": "collected_tools",
                            "source_uris": [meta["source_uri"]],
                            "source_fingerprint": "1234567890ab",
                            "revision": 1,
                            "origin_ref": f"learning-topic://{meta['topic_key']}",
                            "legacy_ref": f"knowledge://{meta['topic_key']}",
                            "action_id": f"knowledge-{meta['topic_key']}",
                            "aidash_card_preview": {"source": "Retro", "ref": f"knowledge://{meta['topic_key']}"},
                        }
                        for sid, meta in SEED["CANONICAL_COLLECTED_TOOLS_RECONCILIATION"].items()
                    ),
                    *(
                        {
                            "topic_key": Path(inv["path"]).stem,
                            "aliases": [],
                            "scope": "personal",
                            "source_id": f"knowledge-vault-{Path(inv['path']).stem}",
                            "source_kind": "knowledge_vault",
                            "source_uri": inv["path"],
                            "source_uris": [inv["path"]],
                            "source_fingerprint": "sha256:0000",
                            "title": inv["title"],
                            "question": inv["title"],
                            "why_now": "已归档；仅用于主题与派生覆盖去重，不重新提案。",
                            "coverage_refs": [inv["path"]],
                            "learning_state": "learned",
                            "priority_rank": None,
                            "revision": 1,
                            "origin_ref": f"learning-topic://{Path(inv['path']).stem}",
                            "legacy_ref": f"knowledge://{Path(inv['path']).stem}",
                            "action_id": f"knowledge-{Path(inv['path']).stem}",
                            "aidash_card_preview": None,
                        }
                        for inv in SEED["_CANONICAL_FIXTURE_DOC"]["vault_coverage_inventory"]
                        if inv["folder"] in {"concepts", "thoughts"}
                    ),
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "knowledge-vault"
            tools = root / "collected-tools"
            proposals = root / "proposals.jsonl"
            state_path = root / "learning-topics-state.json"
            vault.mkdir(parents=True)
            tools.mkdir(parents=True)

            # Copy canonical fixture into temp vault ONLY (never touching production)
            ledger_file = vault / "topic-ledger.json"
            ledger_file.write_text(json.dumps(canonical_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            # Seed collected-tools with hallmark note + 19 repo dirs to simulate full 19-source workspace
            (tools / "hallmark.md").write_text(
                "# Hallmark\n\n## 実際に使ったメモ\n- `/design-studio` Entry A (A1..A4) により統合済み。\n",
                encoding="utf-8",
            )
            for s_item in canonical_doc.get("sources", []):
                sid = s_item["source_id"]
                rdir = tools / "repos" / sid
                rdir.mkdir(parents=True, exist_ok=True)
                (rdir / "README.md").write_text(f"# {sid}\n\nRepository overview for {sid}.\n", encoding="utf-8")

            orig_topic_keys = [t["topic_key"] for t in canonical_doc["topics"]]

            # Run offline deterministic discover_topics (--no-materialize --top 1)
            disc = DIGEST.discover_topics(
                top=1,
                propose_aidash=False,
                materialize_cards=False,
                vault_dir=vault,
                collected_tools_dir=tools,
                git_roots=(),
                proposals_path=proposals,
                state_path=state_path,
                now_utc=datetime(2026, 11, 12, 14, 0, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(disc["ok"])
            self.assertEqual(disc["status"], "selected")
            self.assertEqual(disc["selected_count"], 1)
            self.assertEqual(disc["candidate_count"], 13)
            selected_tk = disc["topics"][0]["topic_key"]
            self.assertEqual(selected_tk, "jp-mixed-script-line-breaking")
            self.assertNotIn("hallmark-anti-slop-anatomy", [t["topic_key"] for t in disc["topics"]])
            self.assertFalse(any(t["topic_key"].endswith("-core-patterns") for t in disc["topics"]))
            self.assertFalse((vault / "concepts").exists())

            # Verify _save_topic_ledger preserves schema_version, policy, sources[19], vault_coverage_inventory[11],
            # all 26 topic_key values, aliases, rank, category, source_id, and aidash_card_preview metadata
            loaded_map = DIGEST._load_topic_ledger(vault)
            loaded_map["jp-mixed-script-line-breaking"]["learning_state"] = "proposed"
            loaded_map["jp-mixed-script-line-breaking"]["proposed_on"] = "2026-11-12"
            DIGEST._save_topic_ledger(vault, loaded_map)

            saved_doc = json.loads(ledger_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_doc["schema_version"], "personal-topic-ledger/v1")
            self.assertEqual(saved_doc["policy"], canonical_doc["policy"])
            self.assertEqual(len(saved_doc["sources"]), len(canonical_doc["sources"]))
            self.assertEqual(
                len(saved_doc["vault_coverage_inventory"]),
                len(canonical_doc["vault_coverage_inventory"]),
            )
            self.assertEqual([t["topic_key"] for t in saved_doc["topics"]], orig_topic_keys)

            by_saved_tk = {t["topic_key"]: t for t in saved_doc["topics"]}
            jp_saved = by_saved_tk["jp-mixed-script-line-breaking"]
            self.assertEqual(jp_saved["learning_state"], "proposed")
            self.assertEqual(jp_saved["proposed_on"], "2026-11-12")
            self.assertEqual(jp_saved["rank"], 1)
            self.assertEqual(jp_saved["source_id"], "awesome-cjk-design-md")
            self.assertIn("awesome-cjk-design-md-core-patterns", jp_saved["aliases"])
            self.assertIsNotNone(jp_saved.get("aidash_card_preview"))
            self.assertEqual(jp_saved["aidash_card_preview"]["source"], "Retro")
            self.assertEqual(
                jp_saved["aidash_card_preview"]["ref"],
                "knowledge://jp-mixed-script-line-breaking",
            )

    def test_existing_unassigned_daily_issue_gets_owner_and_footer(self):
        class Transport:
            def __init__(self): self.updated = []
            def update_issue(self, issue_id, **kwargs): self.updated.append((issue_id, kwargs)); return kwargs
        transport = Transport()
        topics = [{"topic_key": "topic-one", "title": "Topic one", "learning_brief": {"why_now": "why", "preview_questions": ["q1", "q2", "q3"], "recommended_command": "/learn topic-one"}}]
        existing = {"id": "i", "title": "old", "description": "<!-- KD-DAILY-START:2026-11-14 -->\n- [x] old owner check\n<!-- KD-DAILY-END:2026-11-14 -->\n", "assignee": None}
        result = DIGEST._upsert_linear_daily_issue(transport, "2026-11-14", topics, existing_issue=existing)
        self.assertEqual(transport.updated[0][1]["assignee_id"], "0680d5e4-720a-4724-a1d4-b9660c0d1495")
        self.assertIn("@bot 1", transport.updated[0][1]["description"])
        self.assertIn("old owner check", transport.updated[0][1]["description"])


if __name__ == "__main__":
    unittest.main()
