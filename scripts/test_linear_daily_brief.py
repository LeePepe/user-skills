import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("daily_brief", ROOT / "skills/linear-board/scripts/daily-brief.py")
daily_brief = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(daily_brief)
SYNC_SPEC = importlib.util.spec_from_file_location("brief_test_linear_sync", ROOT / "skills/team-lead/scripts/linear-sync.py")
linear_sync = importlib.util.module_from_spec(SYNC_SPEC)
assert SYNC_SPEC.loader
SYNC_SPEC.loader.exec_module(linear_sync)

TODAY = date(2026, 11, 15)
YESTERDAY = date(2026, 11, 14)
MARKER = "<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-15 -->"
TITLE = "[Daily Brief 2026-11-15] 昨日汇总 (11-14) 与今日 Todo (11-15)"


def issue(identifier, title="Personal goal", state="Todo", state_type="unstarted", **kwargs):
    return {"id": identifier, "identifier": identifier, "title": title, "url": f"https://linear.app/test/{identifier}",
            "description": "", "state": {"name": state, "type": state_type}, "priority": 3,
            "project": None, "team": {"id": "tia", "key": "TIA", "name": "Personal"},
            "parent": None, "labels": {"nodes": []}, **kwargs}


class FakeClient:
    def __init__(self, issues=(), scopes=None):
        self.issues = list(issues)
        self.scopes = scopes or {}
        self.scope_calls = []
        self.list_calls = []
        self.mutations = []
        self.states = [{"id": "todo", "name": "Todo", "type": "unstarted"}]
        self.projects = [{"id": "proj-user-skills", "name": "user-skills"}]
        self.milestones = [{"id": "ms-skills-workflow", "name": "Skills Workflow"}]
        self.parent = issue("parent", "[Epic] Skills Workflow")
        self.read_error = None

    def list_issues(self, key, *, limit):
        self.list_calls.append((key, limit))
        if self.read_error: raise self.read_error
        return self.issues

    def issue_scope(self, identifier):
        self.scope_calls.append(identifier)
        value = self.scopes[identifier]
        if isinstance(value, Exception): raise value
        return value

    def team_metadata(self, key):
        if key != "TIA": raise AssertionError(key)
        return {"id": "tia", "key": "TIA", "states": {"nodes": self.states}, "projects": {"nodes": self.projects}}

    def project_milestones(self, project_id):
        if project_id != "proj-user-skills": return []
        return self.milestones

    def issue(self, identifier):
        if identifier != "TIA-375": raise AssertionError(identifier)
        return self.parent

    def create_issue(self, **kwargs):
        result = {"id": "created", **kwargs}
        self.mutations.append(("create", result))
        self.issues.append(issue("created", kwargs["title"], description=kwargs["description"]))
        return result

    def update_issue(self, identifier, **kwargs):
        result = {"id": identifier, **kwargs}
        self.mutations.append(("update", result))
        return result

    def find_issue_by_dedup_marker(self, *args):
        raise AssertionError("Unsafe closed-issue fallback")

    def find_issue_by_title(self, *args):
        raise AssertionError("Unsafe title fallback")


def ledger(root, name, text):
    path = root / name / "ledger.md"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    return path


class DailyBriefTests(unittest.TestCase):
    def test_render_has_populated_continuous_groups_and_only_open_todos(self):
        data = [issue("TIA-1", "Choose palette", labels={"nodes": [{"name": "Needs Decision"}]}),
                issue("TIA-2", "Implement", "In Progress", "started", project={"name": "Personal project"}),
                issue("TIA-3", "Ship", priority=2),
                issue("TIA-4", "[Daily Learn 2026-11-15] optics"),
                issue("TIA-5", "Needs Decision", "Done", "completed", completedAt="2026-11-14T01:00:00+08:00"),
                issue("TIA-6", "[Daily Learn 2026-11-15] finished", "Done", "completed"),
                issue("TIA-7", TITLE, "In Progress", "started"),
                issue("TIA-8", "Renamed brief", priority=1, description=MARKER),
                issue("TIA-9", "Blocked", "Canceled", "unstarted"),
                issue("TIA-10", "Blocked", "Done", "unstarted"),
                issue("TIA-11", "[Daily Learn 2026-11-14] old"),
                issue("TIA-12", "Earlier brief", description="<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-14 -->", labels={"nodes": [{"name": "Blocked"}]})]
        markdown, todos = daily_brief.render_brief(TODAY, data, [], [], [])
        self.assertTrue(markdown.startswith(MARKER))
        self.assertEqual(markdown.splitlines()[1], "## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）")
        self.assertIn("## 📊 昨日完成 (11-14 · 1 项 · workflow-hub 四大方向)", markdown)
        self.assertEqual([row["number"] for row in todos], list(range(1, len(todos) + 1)))
        original = [row for row in todos if row["group"] != "recommended"]
        self.assertEqual([re.search(r"TIA-\d+", row["text"])[0] for row in original], ["TIA-1", "TIA-2", "TIA-3", "TIA-4"])
        self.assertIn("[Personal project] https://linear.app/test/TIA-2", todos[1]["text"])
        self.assertIn("[TIA-5](https://linear.app/test/TIA-5) | Needs Decision |", markdown)
        self.assertEqual([row["group"] for row in original], ["needs", "active", "planned", "planned"])
        self.assertEqual(todos[-1]["group"], "recommended")
        self.assertIn("- [ ] **[2] Implement**", markdown)
        self.assertIn("- [ ] **[3] Ship**", markdown)
        self.assertEqual(sum(line.startswith("### ") for line in markdown.splitlines()), 4)
        self.assertNotIn("## 🤖", markdown)
        self.assertNotIn("# " + TITLE, markdown)

    def test_explicit_demo_decision_clauses_not_generic_narrative(self):
        data = [issue("TIA-1", "Gallery", description="Demo gallery awaiting Owner selection: v01 or v02"),
                issue("TIA-2", "Demo 待选 v01/v02"), issue("TIA-3", "选型：待拍板"),
                issue("TIA-4", "Waiting", state="Needs Decision"), issue("TIA-5", "Review", state="In Review"),
                issue("TIA-6", "Demo selected; gallery implementation finished"),
                issue("TIA-7", "Narrative", description="We once built a Demo gallery. Now write a story about selection."),
                issue("TIA-8", "Backlog pick", state="Backlog", priority=1)]
        markdown, todos = daily_brief.render_brief(TODAY, data, [], [], [])
        self.assertEqual(len(todos), 6)
        needs = str([row for row in todos if row["group"] == "needs"])
        for identifier in ("TIA-1", "TIA-2", "TIA-3", "TIA-4"): self.assertIn(identifier, needs)
        self.assertEqual([row["group"] for row in todos], ["needs"] * 4 + ["active", "planned"])
        self.assertNotIn("TIA-6", str(todos)); self.assertNotIn("TIA-7", str(todos))

    def test_plan_a_single_line_budgets_table_count_and_boilerplate_removal(self):
        long_title = "**这是一个非常长的标题用于验证严格压缩而不是输出多行背景说明**\n第二段 | 更多背景"
        data = [issue("TIA-1", long_title, "Blocked", description="Long explanation\nwith paragraphs", priority=1),
                issue("TIA-2", long_title, "Done", "completed", completedAt="2026-11-14", project={"name": "Learning | Stream"})]
        commits = [{"repo": "user-skills", "sha": "abc", "subject": "A long commit result. Multiple sentences\nwith | pipe"}]
        prs = [{"repo": "LeePepe/user-skills", "title": long_title, "url": "https://github.com/LeePepe/user-skills/pull/148", "additions": 100, "deletions": 30, "changed_files": 5}]
        events = [{"goal": "personal-ledger", "id": "E1", "EVT": "DONE", "ledger": "file:///tmp/ledger.md", "note": long_title}]
        active = [{"goal": "standalone", "task": "T1", "title": long_title, "state": "in_review", "stage": "Reviewer", "group": "active", "ledger": "file:///tmp/standalone/ledger.md"}]
        markdown, todos = daily_brief.render_brief(TODAY, data, commits, events, [], prs, active)
        lines = markdown.splitlines()
        self.assertEqual(lines[0], MARKER)
        self.assertEqual(lines[1], "## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）")
        self.assertLess(lines.index("---"), lines.index("## 📊 昨日完成 (11-14 · 4 项 · workflow-hub 四大方向)"))
        self.assertEqual([line for line in lines if line.startswith("## ")], [lines[1], "## 📊 昨日完成 (11-14 · 4 项 · workflow-hub 四大方向)"])
        todo_lines = [line for line in lines if line.startswith("- [ ]")]
        self.assertEqual(len(todo_lines), len(todos))
        for number, line in enumerate(todo_lines, 1):
            match = re.fullmatch(r"- \[ \] \*\*\[(\d+)\] (.+)\*\* · (.+) · (.+)", line)
            self.assertIsNotNone(match)
            self.assertEqual(int(match[1]), number); self.assertLessEqual(len(daily_brief._plain(match[2])), 25)
            self.assertLessEqual(len(re.findall(r"\]\(", match[3])), 1)
            action = match[3].removeprefix("源自昨日 ")
            self.assertTrue(action.startswith("[") or action.startswith("`"))
        self.assertIn("| 项目 | 单据 / PR | 一句话结果 |", markdown)
        rows = [line for line in lines if line.startswith("| **")]
        self.assertEqual(len(rows), 4)
        for row in rows:
            cells = row.strip("|").split("|")
            self.assertEqual(len(cells), 3)
            result = cells[2].strip()
            self.assertLessEqual(len(daily_brief._plain(result)), 20); self.assertNotIn("\n", result)
        self.assertIn("additions=100, deletions=30, changed_files=5", markdown)
        self.assertIn("T1: in_review/Reviewer", markdown)
        self.assertFalse(any(line.startswith(">") for line in lines))
        self.assertEqual(sum(line.startswith("### ") for line in lines), 4)
        self.assertNotIn("## 🤖", markdown); self.assertNotIn("统计口径", markdown)
        self.assertNotIn("Long explanation", markdown)

    def test_empty_plan_a_has_honest_zero_rows_and_no_placeholder_actions(self):
        markdown, todos = daily_brief.render_brief(TODAY, [], [], [], [])
        self.assertFalse(todos)
        self.assertIn("## 📊 昨日完成 (11-14 · 0 项 · workflow-hub 四大方向)", markdown)
        self.assertEqual(markdown.splitlines().count("_无_"), 4)
        self.assertNotIn("| 项目 |", markdown)
        self.assertNotIn("- [ ]", markdown)
        self.assertNotIn("| **", markdown)

    def test_markdown_sink_rejects_malformed_links_budgets_numbers_and_counts(self):
        valid, _ = daily_brief.render_brief(TODAY, [issue("TIA-1", "Plan", priority=1)], [], [], [])
        self.assertEqual(daily_brief.validate_markdown(valid), [])
        malformed = valid.replace("[1] Plan", "[2] " + "x" * 26)
        malformed = malformed.replace("[TIA-1](https://linear.app/test/TIA-1)", "`[TIA-1](https://linear.app/test/TIA-1)`")
        malformed = malformed.replace("0 项", "1 项")
        errors = daily_brief.validate_markdown(malformed)
        self.assertTrue(any("numbering" in error for error in errors))
        self.assertTrue(any("25 visible" in error for error in errors))
        self.assertTrue(any("code spans" in error or "clickable" in error for error in errors))
        self.assertTrue(any("total" in error for error in errors))
        fenced = valid + "\n```markdown\n[TIA-9](https://linear.app/tianpeili/issue/TIA-9)\n```\n"
        self.assertTrue(any("fenced code" in error for error in daily_brief.validate_markdown(fenced)))

    def test_markdown_sink_accepts_real_stage_bearing_render(self):
        data = [issue("TIA-446", "[Goal] 新增每日 05:00 Linear「", state="Blocked", url="https://linear.app/tianpeili/issue/TIA-446/goal")]
        active = [{"identifier": "TIA-446", "goal": "linear-daily-brief", "task": "T6", "state": "in_progress", "stage": "Writer", "group": "needs"}]
        markdown, _ = daily_brief.render_brief(TODAY, data, [], [], [], ledger_active=active)
        self.assertEqual(daily_brief.validate_markdown(markdown), [])

    def test_four_layers_classify_mixed_sources_and_generic_projects_by_intent(self):
        data = [issue("TIA-1", "Ship app", "Done", "completed", completedAt="2026-11-14", project={"name": "VoxPocket"}),
                issue("TIA-2", "[Daily Learn 2026-11-14] optics", "Done", "completed", completedAt="2026-11-14", project={"name": "Agent Workflow"})]
        commits = [{"repo": "shared-ci", "sha": "abc", "subject": "Fix verification"},
                   {"repo": "user-skills", "sha": "def", "subject": "Tighten linear-board"}]
        prs = [{"repo": "Other/leepepe.github.io", "title": "Blog Demos", "url": "https://github.com/Other/leepepe.github.io/pull/10"}]
        events = [{"goal": "vitalstride-gallery", "note": "Ship product", "id": "E1", "ledger": "file:///tmp/product/ledger.md"},
                  {"goal": "retro-loop-upgrade", "note": "Digest evidence", "id": "E2", "ledger": "file:///tmp/story/ledger.md"},
                  {"goal": "unknown-handoff", "note": "Validated", "id": "E3", "ledger": "file:///tmp/work/ledger.md"}]
        markdown, _ = daily_brief.render_brief(TODAY, data, commits, events, [], prs)
        headings = [line for line in markdown.splitlines() if line.startswith("### ")]
        self.assertEqual(headings, ["### 1. Products & Apps（产品与应用 · 2 项）", "### 2. Shared Foundations（共享底座 · 1 项）",
                                    "### 3. Engineering Workflows（工程工作流与技能 · 2 项）", "### 4. Knowledge & Narrative（知识沉淀与叙事 · 3 项）"])
        sections = re.split(r"### \d\. ", markdown)[1:]
        for section, projects in zip(sections, (("VoxPocket", "vitalstride-gallery"), ("shared-ci",), ("user-skills", "unknown-handoff"), ("Agent Workflow", "leepepe.github.io", "retro-loop-upgrade"))):
            for project in projects: self.assertIn(f"| **{project}** |", section)
        # All documented concrete destinations and hyphenated handoff slugs map consistently.
        for names, expected in ((("Financial", "AIDash", "WebIQSearch", "Nas", "NasControl"), 0),
                                (("shared-design-system", "DesignSystem", "shared-telemetry", "raven", "agent-herdr"), 1),
                                (("m2-dispatcher", "personal-m2-assistant", "team-lead", "my-designer", "design-studio", "code-review", "repo-kit", "skill-contract"), 2),
                                (("knowledge-digest", "story-craft", "save-tool", "Knowledge"), 3)):
            for name in names:
                with self.subTest(name=name):
                    rendered, _ = daily_brief.render_brief(TODAY, [], [], [{"goal": name + "-upgrade", "note": "Validated", "id": "E1"}], [])
                    self.assertIn(f"| **{name}-upgrade** |", re.split(r"### \d\. ", rendered)[expected + 1])
        explicit, _ = daily_brief.render_brief(TODAY, [], [{"repo": "shared-design-system", "sha": "xyz", "subject": "Support VoxPocket"}], [], [])
        self.assertIn("shared-design-system", explicit.split("### 2.")[1].split("### 3.")[0])

    def test_clickable_refs_in_todos_and_all_completed_sources_preserve_canonical_urls(self):
        canonical = "https://linear.app/custom/issue/TIA-1/goal"
        data = [issue("TIA-1", "TIA-1 fix #21", "In Progress", "started", url=canonical, project={"name": "VoxPocket"}),
                issue("TIA-2", "TIA-3 fix #22", "Done", "completed", completedAt="2026-11-14", url="", project={"name": "VoxPocket"})]
        active = [{"identifier": "TIA-1", "goal": "voxpocket", "task": "T1", "state": "in_review", "stage": "Reviewer", "group": "active"},
                  {"identifier": "TIA-9", "goal": "standalone", "title": "TIA-9 choose", "state": "blocked", "group": "needs", "ledger": "file:///tmp/ledger.md"}]
        commits = [{"repo": "VoxPocket", "sha": "abc", "subject": "TIA-4 #23 fix"},
                   {"repo": "user-skills", "sha": "def", "subject": "Long result beyond budget TIA-40 #230; see https://github.com/Other/Financial/pull/231"}]
        prs = [{"repo": "Other/shared-ci", "number": 24, "title": "TIA-5 #25 fix", "url": ""}]
        events = [{"identifier": "TIA-6", "goal": "delivery", "note": "TIA-7 merged #26", "ref": "https://github.com/Other/Financial/pull/26"}]
        markdown, todos = daily_brief.render_brief(TODAY, data, commits, events, [], prs, active)
        self.assertIn(f"[TIA-1]({canonical}) · `T1: in_review/Reviewer`", markdown)
        for number in (2, 3, 4, 5, 6, 7, 9, 40):
            self.assertIn(f"[TIA-{number}](https://linear.app/tianpeili/issue/TIA-{number})", markdown)
        for repo, numbers in (("LeePepe/VoxPocket", (21, 22, 23)), ("LeePepe/user-skills", (230,)), ("Other/shared-ci", (24, 25)), ("Other/Financial", (26, 231))):
            for number in numbers: self.assertIn(f"[#{number}](https://github.com/{repo}/pull/{number})", markdown)
        unlinked = re.sub(r"\[[^\]]+\]\([^)]*\)", "LINK", markdown)
        self.assertIsNone(re.search(r"\bTIA-\d+\b|(?<!\w)#\d+\b", unlinked))
        self.assertEqual(sum(row["group"] != "recommended" for row in todos), 2)
        self.assertIn("TIA-1", todos[1]["text"])
        for line in markdown.splitlines():
            if line.startswith("| **"): self.assertLessEqual(len(daily_brief._plain(line.strip("|").split("|")[2])), 20)
            if line.startswith("- [ ]"):
                summary = re.search(r"\*\*\[\d+\] (.*?)\*\*", line)[1]
                self.assertLessEqual(len(daily_brief._plain(summary)), 25)

    def test_ledger_repo_metadata_reaches_layer_and_pr_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger(root, "generic-delivery", "# Goal: Publish\n- Repo: Other/shared-ci\n## Tasks\n| ID | Title | Status |\n|---|---|---|\n| T1 | Repair #8 | in_progress |\n## Events\n- 2026-11-14 | EVT=PR_MERGED | id=E1 | note=Done #7\n")
            events, active, warnings = daily_brief.collect_handoff(root, TODAY)
            markdown, _ = daily_brief.render_brief(TODAY, [], [], events, warnings, ledger_active=active)
        self.assertFalse(warnings)
        self.assertIn("[#7](https://github.com/Other/shared-ci/pull/7)", markdown)
        self.assertIn("[#8](https://github.com/Other/shared-ci/pull/8)", markdown)
        self.assertIn("generic-delivery", markdown.split("### 2.")[1].split("### 3.")[0])

    def test_adjacent_and_explicit_pr_refs_link_to_their_qualified_repositories_beyond_truncation(self):
        commits = [{"repo": "user-skills", "sha": "abc",
                    "subject": "Fix PR#42 with TIA-7 and Other/Financial#27 after a long result " + "x" * 80}]
        markdown, _ = daily_brief.render_brief(TODAY, [], commits, [], [])
        self.assertIn("[#42](https://github.com/LeePepe/user-skills/pull/42)", markdown)
        self.assertIn("[#27](https://github.com/Other/Financial/pull/27)", markdown)
        self.assertNotIn("PR#42", markdown)
        self.assertNotIn("Other/Financial#27", markdown)
        self.assertEqual(daily_brief.validate_markdown(markdown), [])

    def test_truncation_does_not_change_issue_or_pr_identity_and_unknown_repo_is_not_invented(self):
        commits = [{"repo": "VoxPocket", "sha": "abc", "subject": "A" * 15 + " TIA-123456"},
                   {"repo": "user-skills", "sha": "def", "subject": "A" * 16 + " #123456"}]
        events = [{"goal": "unknown", "id": "E1", "note": "Done #99"}]
        markdown, _ = daily_brief.render_brief(TODAY, [], commits, events, [])
        self.assertIn("[TIA-123456](https://linear.app/tianpeili/issue/TIA-123456)", markdown)
        self.assertIn("[#123456](https://github.com/LeePepe/user-skills/pull/123456)", markdown)
        self.assertNotIn("#99", markdown); self.assertNotIn("LeePepe/unknown", markdown)
        self.assertNotRegex(markdown, r"\[(?:TIA-12?|#12?)\]\(")
        self.assertEqual(sum(line.startswith("| **") for line in markdown.splitlines()), 3)

    def test_todo_action_buckets_cap_recommendations_and_keep_bridge_global_selection(self):
        data = [issue("TIA-1", "Need owner", state="Blocked"), issue("TIA-2", "Active turn", "In Progress", "started"),
                issue("TIA-3", "Plan work", priority=1),
                issue("TIA-4", "Demo gallery", "Done", "completed", completedAt="2026-11-14", project={"name": "VoxPocket"}),
                issue("TIA-5", "Story card", "Done", "completed", completedAt="2026-11-14", project={"name": "Knowledge"})]
        commits = [{"repo": "user-skills", "sha": "abc", "subject": "Validate workflow"}]
        prs = [{"repo": "Other/shared-ci", "number": 8, "title": "Quality gates", "url": "https://github.com/Other/shared-ci/pull/8"}]
        markdown, todos = daily_brief.render_brief(TODAY, data, commits, [], [], prs)
        self.assertEqual([row["number"] for row in todos], [1, 2, 3, 4, 5, 6])
        self.assertEqual([row["group"] for row in todos], ["needs", "active", "planned"] + ["recommended"] * 3)
        buckets = [line for line in markdown.splitlines() if line.startswith("**")]
        self.assertEqual(buckets, [f"**{label}**" for label in daily_brief.TODO_BUCKETS.values()])
        self.assertEqual([daily_brief._plain(row["summary"]) for row in todos[3:]], ["拍板 VoxPocket 画廊并进实现", "验证 shared-ci 下游接入与门禁", "实测 user-skills 首轮端到端链路"])
        for row in todos[3:]: self.assertTrue(row["action"].startswith("源自昨日 ["))
        self.assertIn("[#8](https://github.com/Other/shared-ci/pull/8)", markdown)
        bridge_spec = importlib.util.spec_from_file_location("brief_bucket_bridge", ROOT / "skills/linear-board/scripts/linear-bot-bridge.py")
        bridge = importlib.util.module_from_spec(bridge_spec); bridge_spec.loader.exec_module(bridge)
        selected = bridge.brief_selection({"title": TITLE, "description": markdown}, {"body": "@bot 1, 6"})
        self.assertEqual([line.split("]", 1)[0] for line in selected.splitlines()[1:]], ["[1", "[6"])
        self.assertIn("实测 user-skills", selected); self.assertNotIn("拍板 VoxPocket", selected)
        checked = bridge.brief_selection({"title": TITLE, "description": markdown.replace("- [ ] **[6]", "- [x] **[6]")})
        self.assertIn("[6]", checked); self.assertNotIn("[1]", checked)

    def test_next_hop_layer_defaults_and_distinct_multi_item_milestones(self):
        for project, title, expected in (("VoxPocket", "Feature done", "验证 VoxPocket 本地安装/构建读回"),
                                         ("shared-design-system", "Tokens done", "验证 shared-design-system 下游接入与门禁"),
                                         ("linear-board", "Bridge done", "实测 linear-board 首轮端到端链路"),
                                         ("Knowledge", "Optics card", "提炼 Knowledge 复盘/技术博客")):
            with self.subTest(project=project):
                completed = issue("TIA-10", title, "Done", "completed", completedAt="2026-11-14", project={"name": project})
                _, todos = daily_brief.render_brief(TODAY, [completed], [], [], [])
                self.assertEqual(len(todos), 1)
                self.assertEqual(daily_brief._plain(todos[0]["summary"]), expected[:25])
        completed = issue("TIA-10", "Tokens done", "Done", "completed", completedAt="2026-11-14", project={"name": "shared-ci"})
        duplicate = {"goal": "shared-ci-release", "identifier": "TIA-10", "ref": "https://github.com/LeePepe/shared-ci/pull/8", "note": "Delivered"}
        _, todos = daily_brief.render_brief(TODAY, [completed], [], [duplicate], [])
        self.assertEqual(len(todos), 1)
        self.assertTrue(todos[0]["summary"].startswith("验证 shared-ci"))
        commits = [{"repo": "shared-ci", "sha": "first", "subject": "Gate one"}, {"repo": "shared-ci", "sha": "second", "subject": "Gate two"}]
        _, todos = daily_brief.render_brief(TODAY, [], commits, [], [])
        self.assertEqual(len(todos), 1); self.assertEqual(todos[0]["summary"], "提炼 shared-ci 复盘/技术博客")
        self.assertIn("/commit/first", todos[0]["action"])
        _, reversed_todos = daily_brief.render_brief(TODAY, [], list(reversed(commits)), [], [])
        self.assertEqual(reversed_todos[0]["action"], todos[0]["action"])

    def test_recommendations_exclude_covered_issues_topics_non_yesterday_and_unusable_sources(self):
        data = [issue("TIA-1", "Active app", "In Progress", "started", project={"name": "VoxPocket"}),
                issue("TIA-2", "Demo gallery", "Done", "completed", completedAt="2026-11-14", project={"name": "VoxPocket"}),
                issue("TIA-3", "Skill planned", priority=1, project={"name": "linear-board"}),
                issue("TIA-4", "Bridge done", "Done", "completed", completedAt="2026-11-14", project={"name": "linear-board"}),
                issue("TIA-5", TITLE, "Done", "completed", completedAt="2026-11-14"),
                issue("TIA-6", "Work Business", "Done", "completed", completedAt="2026-11-14"),
                issue("TIA-7", "Future", "Done", "completed", completedAt="2026-11-15")]
        events = [{"goal": "voxpocket-gallery", "id": "E1", "ledger": "file:///tmp/gallery/ledger.md", "note": "Demo done"},
                  {"goal": "other-area", "identifier": "TIA-1", "note": "Covered issue"},
                  {"goal": "unknown", "id": "E2", "note": "No usable destination"}]
        commits = [{"repo": "shared-ci", "sha": "old", "subject": "Older", "timestamp": "2026-11-13"}]
        markdown, todos = daily_brief.render_brief(TODAY, data, commits, events, [])
        self.assertEqual([row["group"] for row in todos], ["active", "planned"])
        self.assertNotIn("**💡 昨日完结顺推（Recommended Next-Hop）**", markdown)
        self.assertNotIn("**🚨 待你拍板（Needs Decision）**", markdown)

    def test_jev_drops_terminal_low_actionability_and_null_case_uses_layer_mapping(self):
        completed = issue("TIA-10", "清理归档过期单据", "Done", "completed", completedAt="2026-11-14", project={"name": "user-skills"})
        runner = mock.Mock(return_value={"has_actionable_next_hop": {"noul": 0.49}})
        markdown, todos = daily_brief.render_brief(TODAY, [completed], [], [], [], jev_runner=runner)
        self.assertFalse(todos); self.assertNotIn("**💡", markdown); runner.assert_called_once()
        completed = issue("TIA-11", "Library shipped", "Done", "completed", completedAt="2026-11-14", project={"name": "shared-ci"})
        runner.return_value = {"has_actionable_next_hop": {"noul": 0.50}, "next_hop_kind": {"choice": "gallery_pick"}, "next_hop_kind_match": {"noul": 0.49}, "next_hop_priority": {"score": 0.8}}
        _, todos = daily_brief.render_brief(TODAY, [completed], [], [], [], jev_runner=runner)
        self.assertEqual(todos[0]["summary"], "验证 shared-ci 下游接入与门禁")
        self.assertEqual(todos[0]["decision_source"], "jev_truth_table")
        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["agent_role"], "linear_board"); self.assertEqual(kwargs["timeout_sec"], 1.5)
        self.assertTrue(kwargs["falcon_only"])
        self.assertEqual(kwargs["questions"]["next_hop_kind"]["type"], "choice")
        self.assertEqual(kwargs["questions"]["next_hop_kind_match"]["type"], "noul")
        self.assertEqual(set(kwargs["questions"]), {"has_actionable_next_hop", "next_hop_kind", "next_hop_kind_match", "next_hop_priority"})

    def test_jev_truth_table_maps_all_kinds_and_ranks_before_top_three_cap(self):
        completed = issue("TIA-10", "Evidence ready", "Done", "completed", completedAt="2026-11-14", project={"name": "linear-board"})
        for kind, expected in (("gallery_pick", "拍板 linear-board 画廊并进实现"), ("downstream_adopt", "验证 linear-board 下游接入与门禁"),
                               ("live_e2e_verify", "实测 linear-board 首轮端到端链路"), ("story_or_retro", "提炼 linear-board 复盘/技术博客")):
            with self.subTest(kind=kind):
                response = {"answers": {"has_actionable_next_hop": {"noul": 0.75}, "next_hop_kind": {"choice": kind}, "next_hop_kind_match": {"noul": 0.70}, "next_hop_priority": {"score": 0.9}}}
                _, todos = daily_brief.render_brief(TODAY, [completed], [], [], [], jev_runner=mock.Mock(return_value=response))
                self.assertEqual(todos[0]["summary"], expected); self.assertEqual(todos[0]["next_hop_kind"], kind)
        data = [issue(f"TIA-{n}", "Feature done", "Done", "completed", completedAt="2026-11-14", project={"name": project})
                for n, project in enumerate(("VoxPocket", "shared-ci", "linear-board", "Knowledge"), 1)]
        priorities = {"VoxPocket": 0.1, "shared-ci": 0.3, "linear-board": 0.9, "Knowledge": 0.7}
        def judge(**kwargs):
            return {"has_actionable_next_hop": {"noul": 0.8}, "next_hop_kind": {"choice": "live_e2e_verify"}, "next_hop_kind_match": {"noul": 0.9}, "next_hop_priority": {"score": priorities[kwargs["state"]["area"]]}}
        _, todos = daily_brief.render_brief(TODAY, data, [], [], [], jev_runner=judge)
        self.assertEqual([row["next_hop_priority"] for row in todos], [0.9, 0.7, 0.3])
        self.assertEqual([row["number"] for row in todos], [1, 2, 3])
        self.assertTrue(todos[0]["summary"].startswith("实测 linear-board"))

    def test_jev_offline_invalid_and_exception_fallback_is_deterministic_and_drops_pure_cleanup(self):
        data = [issue("TIA-1", "清理归档过期单据", "Done", "completed", completedAt="2026-11-14", project={"name": "user-skills"}),
                issue("TIA-2", "Demo gallery", "Done", "completed", completedAt="2026-11-14", project={"name": "VoxPocket"})]
        for response in (None, {"fallback_reason": "Falcon offline"}, {"answers": []}, {"has_actionable_next_hop": {"noul": float("nan")}}):
            with self.subTest(response=response):
                _, todos = daily_brief.render_brief(TODAY, data, [], [], [], jev_runner=mock.Mock(return_value=response))
                self.assertEqual(len(todos), 1); self.assertEqual(todos[0]["summary"], "拍板 VoxPocket 画廊并进实现")
                self.assertEqual(todos[0]["decision_source"], "deterministic_fallback")
        _, todos = daily_brief.render_brief(TODAY, data, [], [], [], jev_runner=mock.Mock(side_effect=TimeoutError("offline")))
        self.assertEqual(len(todos), 1)
        # Mixed useful delivery and archival noise in one area still has a concrete fallback.
        commits = [{"repo": "user-skills", "sha": "a", "subject": "Cleanup stale tickets"}, {"repo": "user-skills", "sha": "b", "subject": "Ship bridge"}]
        _, todos = daily_brief.render_brief(TODAY, [], commits, [], [], jev_runner=mock.Mock(return_value={"fallback_reason": "offline"}))
        self.assertEqual(len(todos), 1)

    def test_resolved_event_without_task_does_not_resurrect_blocked_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger(root, "personal", "## Tasks\n| ID | Title | Status |\n| --- | --- | --- |\n| T1 | Await approval | blocked |\n## Events\n- 2026-11-15T01:00:00+08:00 | EVT=NEED_GO | id=B1 | task=T1\n- 2026-11-15T02:00:00+08:00 | EVT=ACK | id=B1\n")
            _, active, warnings = daily_brief.collect_handoff(root, TODAY)
        self.assertFalse(active); self.assertFalse(warnings)

    def test_generate_uses_issue_title_not_first_visible_plan_a_heading(self):
        client = FakeClient([issue("TIA-1", priority=1)])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(daily_brief, "_proposal_lock", return_value=contextlib.nullcontext()):
            result = daily_brief.generate(TODAY, dev_root=Path(tmp), handoff_root=Path(tmp), client=client, propose_linear=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], TITLE)
        self.assertEqual(client.mutations[0][1]["title"], TITLE)
        self.assertTrue(client.mutations[0][1]["description"].startswith(MARKER + "\n## 🎯 今日 Todo"))

    def test_cst_boundary_and_naive_timestamps_are_host_independent(self):
        self.addCleanup(time.tzset)
        for tz in ("UTC", "America/Los_Angeles"):
            with self.subTest(tz=tz), mock.patch.dict(os.environ, {"TZ": tz}):
                time.tzset()
                self.assertTrue(daily_brief._date_in("2026-11-13T16:00:00Z", YESTERDAY))
                self.assertFalse(daily_brief._date_in("2026-11-13T15:59:59Z", YESTERDAY))
                self.assertFalse(daily_brief._date_in("2026-11-14T16:00:00Z", YESTERDAY))
                self.assertTrue(daily_brief._date_in("2026-11-14", YESTERDAY))
                self.assertTrue(daily_brief._date_in("2026-11-14T23:59:59", YESTERDAY))
                self.assertEqual(daily_brief._timestamp("2026-11-14").isoformat(), "2026-11-14T00:00:00+08:00")

    def test_completion_boundary_and_fallback_not_cancellation(self):
        data = [issue("TIA-1", state="Done", state_type="completed", completedAt="2026-11-13T16:00:00Z", updatedAt="2026-11-15T01:00:00+08:00"),
                issue("TIA-2", state="Done", state_type="completed", updatedAt="2026-11-14T15:59:59Z"),
                issue("TIA-3", state="Done", state_type="completed", completedAt="2026-11-14T16:00:00Z"),
                issue("TIA-4", state="Canceled", state_type="canceled", completedAt="2026-11-14"),
                issue("TIA-5", state="In Progress", state_type="started", updatedAt="2026-11-14"),
                issue("TIA-6", state="Canceled", state_type="unstarted", completedAt="2026-11-14"),
                issue("TIA-7", state="Done", state_type="unstarted", updatedAt="2026-11-14")]
        self.assertEqual([daily_brief._issue_is_completed(row, YESTERDAY) for row in data], [True, True, False, False, False, False, True])

    def test_scope_personal_root_null_project_and_english_allowed(self):
        root = issue("root", "[Root] Personal learning", project=None)
        client = FakeClient([root, issue("child", "probing and describing ideas", parent=root)])
        rows, warnings = daily_brief.collect_linear(client, TODAY)
        self.assertEqual([row["id"] for row in rows], ["child"])
        self.assertFalse(warnings); self.assertFalse(client.scope_calls)
        self.assertEqual(client.list_calls, [("TIA", None)])

    def test_deep_ancestor_lookup_and_cache_excludes_work_grandparent(self):
        personal = issue("personal-root", "[Root] Personal")
        work = issue("work-root", "[Root] Work Business")
        client = FakeClient([issue("a", parent={"id": "middle"}), issue("b", parent={"id": "middle"})],
                            {"middle": issue("middle", parent={"id": "work-root"}), "work-root": work})
        rows, warnings = daily_brief.collect_linear(client, TODAY)
        self.assertFalse(rows); self.assertFalse(warnings)
        self.assertEqual(client.scope_calls, ["middle", "work-root"])
        client = FakeClient([issue("a", parent={"id": "middle"})], {"middle": issue("middle", parent={"id": "personal-root"}), "personal-root": personal})
        rows, warnings = daily_brief.collect_linear(client, TODAY)
        self.assertEqual(len(rows), 1); self.assertFalse(warnings)

    def test_scope_cycles_and_lookup_failures_fail_closed_with_sanitized_warning(self):
        for scopes in ({"b": issue("b", parent={"id": "a"})}, {"b": RuntimeError("secret-token server response")}, {"b": {"id": "b", "title": "Personal"}}):
            client = FakeClient([issue("a", parent={"id": "b"})], scopes)
            rows, warnings = daily_brief.collect_linear(client, TODAY)
            self.assertFalse(rows); self.assertTrue(warnings)
            self.assertNotIn("secret-token", str(warnings))
        self.assertTrue(daily_brief._excluded_issue(issue("a", parent={"id": "a"})))
        warnings = []
        self.assertTrue(daily_brief._excluded_issue(issue("a", parent={"title": "Unknown ancestor"}), warnings=warnings))
        self.assertTrue(warnings)

    def test_scope_uses_cached_complete_parent_even_without_client(self):
        root = issue("r", "[Root] Personal")
        client = FakeClient(scopes={"r": RuntimeError("offline")})
        self.assertFalse(daily_brief._excluded_issue(issue("a", parent={"id": "r"}), client, {"r": root}))
        self.assertFalse(client.scope_calls)

    def test_ledger_work_headers_prefixes_and_personal_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("bingDemo", "sapphireUI", "flightbook2", "webiqTools", "devsyncM1", "work-goal", "_watch"):
                ledger(root, name, "- 2026-11-14 | EVT=DONE | note=excluded\n")
            for index, header in enumerate(("- Project: Bing", "- Repo: /work/Workplace/project", "- Linear: Work Business", "* Project: Workplace", "+ Repo: LeePepe/sapphireUI", "# Goal: bingDemo UI", "- Repo: LeePepe/BingDemo")):
                ledger(root, f"neutral{index}", header + "\n- 2026-11-14 | EVT=DONE | note=excluded\n")
            ledger(root, "personal", "# Goal: Personal narrative\n- Project: Personal project\nDescription mentions Bing, but is personal.\n- 2026-11-14 | EVT=DONE | note=personal delivery\n")
            rows, warnings = daily_brief.collect_ledgers(root, YESTERDAY)
        self.assertEqual([row["goal"] for row in rows], ["personal"]); self.assertFalse(warnings)

    def test_ledger_only_event_timestamp_not_date_in_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger(root, "personal", "- 2026-11-13T15:59:00Z | EVT=DONE | note=2026-11-14\n- not-a-date | EVT=DONE | note=2026-11-14\n- narrative on 2026-11-14 | EVT=DONE\n- 2026-11-13T16:00:00Z | EVT=DONE | note=start\n- 2026-11-14T16:00:00Z | EVT=DONE | note=end\n- 2026-11-14 | EVT=DONE | note=CST date\n")
            rows, warnings = daily_brief.collect_ledgers(root, YESTERDAY)
        self.assertEqual([row["note"] for row in rows], ["start", "CST date"]); self.assertFalse(warnings)

    def test_blocker_reconciliation_namespaced_and_matching_resolution_only(self):
        events = [{"goal": goal, "EVT": "NEED_GO", "id": identifier, "task": task} for goal in ("alpha", "beta", "finished") for identifier, task in (("B1", "T1"), ("E1", "T2"))]
        events += [{"goal": "alpha", "EVT": "ACK", "id": "B1", "task": "T2"},
                   {"goal": "beta", "EVT": "REVIEW_PASS", "task": "T2"},
                   {"goal": "finished", "EVT": "GOAL_DONE", "id": "G1"}]
        active = daily_brief._active_events(events)
        self.assertEqual([(row["goal"], row["id"]) for row in active], [("alpha", "E1"), ("beta", "B1")])
        self.assertFalse(daily_brief._active_events(active + [{"goal": "alpha", "EVT": "RESOLVED", "id": "E1"}, {"goal": "beta", "EVT": "DONE", "task": "T1"}]))

    def test_active_tasks_stages_bound_goal_enrichment_and_completed_goal(self):
        tasks = "## Tasks\n| ID | Title | Status | Stage | Note | PR/head |\n| --- | --- | --- | --- | --- | --- |\n| T1 | Build feature | in_progress | Writer | implementing | receipts/writer.md |\n| T2 | Validate | in_review | Reviewer | checking | https://github.com/LeePepe/user-skills/pull/148 |\n| T3 | Later | ready | Writer | pending | |\n| T4 | Complete | done | Writer | finished | |\n## Events\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger(root, "alpha", "# Goal: Palette\n- Linear: TIA-10\n" + tasks + "- 2026-11-15T01:00:00+08:00 | EVT=NEED_OWNER_DECISION | id=B1 | task=T1 | note=Choose v01 or v02 | ref=design.md\n")
            ledger(root, "beta", tasks + "- 2026-11-15T01:00:00+08:00 | EVT=NEED_GO | id=B1 | task=T1 | note=Approve API\n- 2026-11-15T02:00:00+08:00 | EVT=RESOLVED | id=B1 | task=T1\n")
            ledger(root, "finished", tasks + "- 2026-11-14 | EVT=NEED_GO | id=B1 | task=T1\n- 2026-11-14T23:00:00+08:00 | EVT=GOAL_DONE | id=G1\n")
            data = [issue("TIA-10", "Palette", "In Progress", "started")]
            client = FakeClient(data)
            cache = {}; rows, _ = daily_brief.collect_linear(client, TODAY, scope_cache=cache)
            rollup, active, warnings = daily_brief.collect_handoff(root, TODAY, client=client, scope_cache=cache)
            markdown, todos = daily_brief.render_brief(TODAY, rows, [], rollup, warnings, ledger_active=active)
        self.assertFalse(warnings); self.assertFalse(client.scope_calls)
        original = [row for row in todos if row["group"] != "recommended"]
        self.assertEqual(len(original), 2)
        self.assertEqual(sum("TIA-10" in row["text"] for row in original), 1)
        self.assertIn("Choose v01 or v02", todos[0]["text"])
        self.assertIn("M1: in_review / Reviewer", todos[0]["text"])
        self.assertIn("ledger.md", todos[0]["text"])
        self.assertIn("beta", todos[1]["text"])
        self.assertNotIn("finished", str(original)); self.assertNotIn("Later", str(todos)); self.assertNotIn("Complete", str(todos))
        self.assertEqual(todos[-1]["group"], "recommended")
        self.assertIn("| **finished** |", markdown.split("## 📊 昨日完成")[1])
        self.assertNotIn("| **Handoff** |", markdown)
        self.assertIn("T2: in_review/Reviewer", markdown)

    def test_latest_task_event_and_goal_state_override_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = "## Tasks\n| ID | Title | Status | Stage |\n| --- | --- | --- | --- |\n| T1 | Repair | in_progress | Writer |\n| T2 | Review | planned | Writer |\n## Events\n"
            ledger(root, "active", tasks + "- 2026-11-15T01:00:00+08:00 | EVT=DONE | task=T1\n- 2026-11-15T02:00:00+08:00 | EVT=TASK_STATE | task=T2 | state=in_review | stage=Reviewer\n")
            ledger(root, "complete", tasks + "- 2026-11-15 | EVT=GOAL_STATE | state=done\n")
            _, active, warnings = daily_brief.collect_handoff(root, TODAY)
        self.assertEqual([(row["goal"], row["task"], row["state"], row["stage"]) for row in active], [("active", "T2", "in_review", "Reviewer")])
        self.assertFalse(warnings)

    def test_bound_ledger_uses_personal_scope_and_lookup_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = "- Linear: TIA-10\n## Tasks\n| ID | Title | Status |\n| --- | --- | --- |\n| T1 | Active | in_progress |\n"
            ledger(root, "bound", text)
            work = issue("TIA-10", parent={"id": "work"})
            client = FakeClient(scopes={"TIA-10": work, "work": issue("work", "[Root] Work Business")})
            rollup, active, warnings = daily_brief.collect_handoff(root, TODAY, client=client)
            self.assertFalse(rollup); self.assertFalse(active); self.assertFalse(warnings)
            client = FakeClient(scopes={"TIA-10": RuntimeError("secret-scope-error")})
            _, active, warnings = daily_brief.collect_handoff(root, TODAY, client=client)
            self.assertFalse(active); self.assertTrue(warnings); self.assertNotIn("secret", str(warnings))

    def test_paginated_pr_collection_filters_cst_and_fetches_only_matched_metrics(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "--paginate" in command:
                return json.dumps([[{"number": 1, "title": "start", "html_url": "https://github.com/LeePepe/user-skills/pull/1", "merged_at": "2026-11-13T11:00:00-05:00"}, {"number": 2, "merged_at": "2026-11-13T15:59:59Z"}],
                                   [{"number": 3, "merged_at": "2026-11-14T16:00:00Z"}, {"number": 4, "title": "late", "html_url": "pr4", "merged_at": "2026-11-15T00:59:59+09:00"}, {"number": 5, "merged_at": None}]])
            return json.dumps({"additions": 10, "deletions": 3, "changed_files": 2})
        rows, warnings = daily_brief.collect_prs(("LeePepe/user-skills",), YESTERDAY, runner=run)
        self.assertFalse(warnings)
        self.assertEqual([row["title"] for row in rows], ["start", "late"])
        self.assertEqual([call[0][-1] for call in calls[1:]], ["repos/LeePepe/user-skills/pulls/1", "repos/LeePepe/user-skills/pulls/4"])
        self.assertTrue(all(call[1]["timeout"] == 15 for call in calls))
        markdown, _ = daily_brief.render_brief(TODAY, [], [{"repo": "user-skills", "sha": "abc", "subject": "commit"}], [], [], rows)
        self.assertEqual(markdown.count("| **user-skills** |"), 3)
        self.assertNotIn("**LeePepe/user-skills**", markdown)
        self.assertIn("additions=10, deletions=3, changed_files=2", markdown)

    def test_source_failures_and_missing_pr_metrics_are_explicit_sanitized(self):
        def run(command, **kwargs):
            if "--paginate" in command: return json.dumps([[{"number": 1, "merged_at": "2026-11-14"}]])
            raise subprocess.TimeoutExpired(command, 15, output="secret-server-error")
        rows, warnings = daily_brief.collect_prs(("LeePepe/user-skills",), YESTERDAY, runner=run)
        self.assertEqual(len(rows), 1); self.assertIsNone(rows[0]["additions"])
        self.assertIn("PR metrics unavailable", str(warnings)); self.assertNotIn("secret", str(warnings))
        rows, warnings = daily_brief.collect_prs(("LeePepe/user-skills",), YESTERDAY, runner=lambda *a, **kw: "{bad json")
        self.assertFalse(rows); self.assertTrue(warnings)
        rows, warnings = daily_brief.collect_git(Path("/missing-brief-repositories"), YESTERDAY)
        self.assertFalse(rows); self.assertEqual(len(warnings), len(daily_brief.PERSONAL_REPOS))

    def test_git_exact_cst_half_open_committer_bounds_and_bounded_runner(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return "a\tbefore\tbefore\t2026-11-13T15:59:59Z\nb\tstart\tstart\t2026-11-13T16:00:00Z\nc\tlate\tlate\t2026-11-14T15:59:59Z\nd\tend\tend\t2026-11-14T16:00:00Z\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "user-skills" / ".git").mkdir(parents=True)
            with mock.patch.object(daily_brief, "PERSONAL_REPOS", ("user-skills",)):
                rows, warnings = daily_brief.collect_git(root, YESTERDAY, runner=run)
        self.assertEqual([row["sha"] for row in rows], ["start", "late"]); self.assertFalse(warnings)
        command, kwargs = calls[0]
        self.assertIn("--all", command); self.assertIn("--since-as-filter", command)
        self.assertEqual(command[command.index("--since-as-filter") + 1], "2026-11-14 00:00 +0800")
        self.assertEqual(command[command.index("--until") + 1], "2026-11-15 00:00 +0800")
        self.assertIn("%cI", command[-1]); self.assertNotIn("%aI", command[-1]); self.assertEqual(kwargs["timeout"], 15)

    def test_real_git_author_date_does_not_control_rollup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / "user-skills"; repo.mkdir()
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "init", "--quiet", str(repo)], env=env, check=True, capture_output=True)
            for subject, stamp in (("before", "2026-11-13T15:59:59Z"), ("start", "2026-11-13T16:00:00Z"), ("late", "2026-11-14T15:59:59Z"), ("end", "2026-11-14T16:00:00Z")):
                commit_env = {**env, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": stamp}
                subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", subject], env=commit_env, check=True, capture_output=True)
            with mock.patch.object(daily_brief, "PERSONAL_REPOS", ("user-skills",)):
                rows, warnings = daily_brief.collect_git(root, YESTERDAY)
        self.assertEqual([row["subject"] for row in rows], ["late", "start"]); self.assertFalse(warnings)

    def test_canonical_paginated_linear_collection_includes_closed_and_personal_children(self):
        class Paged(linear_sync.GraphQLClient):
            def __init__(self): self.requests = []
            def execute(self, query, variables):
                self.requests.append(variables)
                if variables["after"] is None:
                    return {"issues": {"nodes": [issue("done", state="Done", state_type="completed"), issue("root", "[Root] Personal")], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}
                return {"issues": {"nodes": [issue("child", parent={"id": "root"})], "pageInfo": {"hasNextPage": False}}}
        client = Paged()
        rows, warnings = daily_brief.collect_linear(client, TODAY)
        self.assertEqual([row["id"] for row in rows], ["done", "child"]); self.assertFalse(warnings)
        self.assertEqual([call["after"] for call in client.requests], [None, "next"])

    def test_propose_creates_then_updates_idempotently_with_correct_parent_owner(self):
        client = FakeClient()
        first = daily_brief.propose(client, TITLE, MARKER + "\nbody")
        second = daily_brief.propose(client, TITLE, MARKER + "\nupdated")
        self.assertEqual([kind for kind, _ in client.mutations], ["create", "update"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["team_id"], "tia"); self.assertEqual(first["state_id"], "todo")
        for row in (first, second):
            self.assertIsNone(row["parent_id"])
            self.assertEqual(row["project_id"], "proj-user-skills")
            self.assertEqual(row["project_milestone_id"], "ms-skills-workflow")
            self.assertEqual(row["assignee_id"], "0680d5e4-720a-4724-a1d4-b9660c0d1495")
        self.assertTrue(second["clear_parent"])
        self.assertNotIn("state_id", second)
        self.assertEqual(client.list_calls, [("TIA", None), ("TIA", None)])
        override = daily_brief.propose(client, TITLE, MARKER + "\nexplicit", "TIA-375")
        self.assertEqual(override["parent_id"], "parent")
        self.assertNotIn("clear_parent", override)

    def test_propose_auto_closes_and_unassigns_prior_daily_briefs(self):
        prior = issue(
            "prior-brief",
            "[Daily Brief 2026-11-14] 昨日汇总 (11-13) 与今日 Todo (11-14)",
            "Todo",
            "unstarted",
            description="<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-14 -->\nbody",
            assignee={"id": daily_brief.OWNER_ID},
        )
        client = FakeClient([prior])
        client.states.append({"id": "done", "name": "Done", "type": "completed"})
        created = daily_brief.propose(client, TITLE, MARKER + "\nbody")
        self.assertEqual(created["id"], "created")
        self.assertEqual(created["assignee_id"], daily_brief.OWNER_ID)
        self.assertEqual(
            client.mutations[0],
            ("update", {"id": "prior-brief", "assignee_id": None, "clear_assignee": True, "state_id": "done"}),
        )

    def test_exact_comment_not_prose_and_marker_preferred_over_title(self):
        prose = issue("TIA-446", "Implement generator", description="Mentions daily-brief-2026-11-15 in prose")
        title_only = issue("fallback", TITLE)
        marked = issue("marked", "Renamed", description=MARKER)
        client = FakeClient([prose, title_only, marked])
        result = daily_brief.propose(client, TITLE, MARKER)
        self.assertEqual(result["id"], "marked")
        self.assertEqual(client.mutations[0][0], "update")
        client = FakeClient([prose])
        self.assertEqual(daily_brief.propose(client, TITLE, MARKER)["id"], "created")

    def test_duplicate_preference_title_fallback_and_state_preservation(self):
        for state, state_type in (("In Progress", "started"), ("In Review", "started"), ("Done", "completed")):
            with self.subTest(state=state):
                canceled = issue("canceled", TITLE, "Canceled", "canceled", description=MARKER)
                active = issue("existing", TITLE, state, state_type, description=MARKER)
                client = FakeClient([canceled, active])
                result = daily_brief.propose(client, TITLE, MARKER)
                self.assertEqual(result["id"], "existing"); self.assertNotIn("state_id", result)
        client = FakeClient([issue("old", TITLE, "Canceled", "canceled"), issue("fallback", TITLE, "Done", "completed")])
        result = daily_brief.propose(client, TITLE, MARKER)
        self.assertEqual(result["id"], "fallback"); self.assertNotIn("state_id", result)
        client = FakeClient([issue("canceled-marker", "Old title", "Canceled", "unstarted", description=MARKER), issue("title-active", TITLE)])
        result = daily_brief.propose(client, TITLE, MARKER)
        self.assertEqual(result["id"], "canceled-marker"); self.assertEqual(result["state_id"], "todo")

    def test_wrong_or_unverifiable_parent_and_missing_todo_do_not_mutate(self):
        for team in ({"key": "OTHER", "id": "other"}, {}, {"id": "other"}):
            client = FakeClient(); client.parent["team"] = team
            with self.assertRaises(ValueError): daily_brief.propose(client, TITLE, MARKER, "TIA-375")
            self.assertFalse(client.mutations)
        for existing in ([], [issue("old", TITLE, "Canceled", "canceled", description=MARKER)]):
            client = FakeClient(existing); client.states = []
            with self.assertRaises(ValueError): daily_brief.propose(client, TITLE, MARKER)
            self.assertFalse(client.mutations)
        client = FakeClient([issue("done", TITLE, "Done", "completed", description=MARKER)]); client.states = []
        self.assertNotIn("state_id", daily_brief.propose(client, TITLE, MARKER))

    def test_candidate_enumeration_failure_never_uses_unsafe_fallback(self):
        client = FakeClient(); client.read_error = RuntimeError("secret-api-token")
        with self.assertRaises(RuntimeError): daily_brief.propose(client, TITLE, MARKER)
        self.assertFalse(client.mutations)

    def test_generate_publication_no_client_no_result_and_errors_fail_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for client, proposal in ((None, None), (FakeClient(), None), (FakeClient(), RuntimeError("secret-token"))):
                with self.subTest(client=client), mock.patch.object(daily_brief, "_proposal_lock", return_value=contextlib.nullcontext()), mock.patch.object(daily_brief, "propose", side_effect=proposal if isinstance(proposal, Exception) else None, return_value=proposal):
                    result = daily_brief.generate(TODAY, dev_root=root, handoff_root=root, client=client, propose_linear=True)
                self.assertFalse(result["ok"]); self.assertIn("publication failed", str(result["warnings"]))
                self.assertIn("Publication warning", result["markdown"]); self.assertNotIn("secret", json.dumps(result))

    def test_main_missing_credentials_publication_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"LINEAR_DAILY_BRIEF_DEV_ROOT": tmp, "LINEAR_DAILY_BRIEF_HANDOFF_ROOT": tmp}), mock.patch.object(daily_brief, "_load_linear_client", return_value=None), contextlib.redirect_stdout(io.StringIO()) as output:
            code = daily_brief.main(["--date", "2026-11-15", "--propose-linear", "--json"])
        self.assertEqual(code, 1); self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_loader_dry_run_passes_no_refresh_and_reads_without_writes(self):
        client = FakeClient([issue("TIA-1", priority=1)])
        module = mock.Mock()
        module.load_api_key.return_value = "fixture-key"
        module.GraphQLClient.return_value = client
        with mock.patch.object(daily_brief.importlib.util, "module_from_spec", return_value=module), mock.patch.object(daily_brief.importlib.util, "spec_from_file_location") as spec:
            def execute_module(module):
                self.assertTrue(daily_brief.sys.dont_write_bytecode)
            spec.return_value.loader.exec_module.side_effect = execute_module
            with mock.patch.object(daily_brief.sys, "dont_write_bytecode", False):
                loaded = daily_brief._load_linear_client(dry_run=True)
                self.assertFalse(daily_brief.sys.dont_write_bytecode)
        module.GraphQLClient.assert_called_once_with("fixture-key", allow_refresh=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"LINEAR_DAILY_BRIEF_LOCK": str(root / "must-not-create" / "lock")}), mock.patch.object(daily_brief, "_proposal_lock", side_effect=AssertionError("Dry-run lock")), mock.patch.object(daily_brief, "propose", side_effect=AssertionError("Dry-run publication")):
                result = daily_brief.generate(TODAY, dev_root=root, handoff_root=root, client=loaded, propose_linear=True, dry_run=True)
            self.assertFalse(list(root.iterdir()))
        self.assertTrue(result["ok"]); self.assertIsNone(result["linear_issue"])
        self.assertEqual(len(result["todo_items"]), 1)
        self.assertEqual(client.list_calls, [("TIA", None)]); self.assertFalse(client.mutations)

    def test_email_action_pipeline_extracts_workiq_target_links_stages_plan_and_enters_daily_brief_needs_decision(self):
        pipeline_spec = importlib.util.spec_from_file_location(
            "email_action_pipeline", ROOT / "skills/linear-board/scripts/email-action-pipeline.py"
        )
        pipeline = importlib.util.module_from_spec(pipeline_spec)
        assert pipeline_spec.loader
        pipeline_spec.loader.exec_module(pipeline)

        raw_applink = (
            "https://outlook.office.com/mail/MBX%3A14bf50cb-b422-4ef7-a0ac-c0886461cb6e%40"
            "72f988bf-86f1-41af-91ab-2d7cd011db47/applink/read/"
            "AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg0A6LMpo9Of7EymTgIos9RPlAAPt2c6RAAA/"
            "?ds=Tianpei.Li%40microsoft.com&ai=OID%3Afbc97a98-90ba-4a99-aa1f-864201feccdb%40"
            "72f988bf-86f1-41af-91ab-2d7cd011db47&as=Tianpei.Li%40microsoft.com&au=tianpli"
        )
        workiq_payload = {
            "isError": False,
            "response": (
                "1. **[[EXTERNAL] The uploaded build for 微软必应 has one or more issues.]("
                + raw_applink
                + "&EntityRepresentationId=e955e389-8be2-47f4-a86c-c9035419f49c)**\n"
                "   - **Sender:** App Store Connect `<no_reply@email.apple.com>`\n"
                "   - **Received:** Nov 17, **16:08**\n"
                "   - **What they want:** Correct **ITMS-90683** in a subsequent build by adding "
                "`NSLocationAlwaysAndWhenInUseUsageDescription` to the **BingChina.app Info.plist**.\n"
                "   - **Urgency:** Next-delivery fix.\n"
                "   - **Context:** It applies to 微软必应 version/build **34.6.441116001**. "
                f"[3]({raw_applink}#0a4909)\n"
                "2. **[Ignite week is here!](https://outlook.office.com/mail/MBX/applink/read/AAkALgFYI1234567890/)**\n"
                "   - **Sender:** Viva Engage\n"
                "   - **What they want:** **No action.** FYI announcement only. "
                "[4](https://outlook.office.com/mail/MBX/applink/read/AAkALgFYI1234567890/#763596)\n"
            ),
            "artifacts": [{
                "artifactId": "art-1",
                "parts": [
                    {"text": ""},
                    {"data": {
                        "0a4909": {"targetLink": raw_applink, "isCitedInResponse": True},
                        "763596": {"targetLink": "https://outlook.office.com/mail/MBX/applink/read/AAkALgFYI1234567890/", "isCitedInResponse": True},
                    }},
                ],
            }],
        }
        list_shaped_payload = {
            "response": workiq_payload["response"],
            "artifacts": [{
                "parts": [{"data": [
                    {"citationId": "0a4909", "targetLink": raw_applink, "isCitedInResponse": True},
                    {"citationId": "763596", "targetLink": "https://outlook.office.com/mail/MBX/applink/read/AAkALgFYI1234567890/"},
                ]}],
            }],
        }
        for payload in (workiq_payload, list_shaped_payload):
            emails = pipeline.extract_emails_from_workiq(payload)
            self.assertEqual(len(emails), 2)
            self.assertEqual(
                emails[0]["email_item_id"],
                "AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg0A6LMpo9Of7EymTgIos9RPlAAPt2c6RAAA",
            )
            self.assertEqual(emails[0]["target_link"], raw_applink)
            self.assertNotIn("#", emails[0]["target_link"])
            self.assertNotIn("EntityRepresentationId", emails[0]["target_link"])

        router_calls = []

        def fake_router(argv, _timeout):
            router_calls.append(argv)
            self.assertIn("--plan", argv)
            self.assertNotIn("--apply", argv)
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({
                    "schema_version": 1,
                    "plan_id": "m2p-itms90683",
                    "targets": [{
                        "topic_id": "sapphire-china-bugs",
                        "target_repo": "bing-growth-sapphire-app",
                        "m2_status": "EXISTING_M2",
                        "herdr_target": "m2-sapphire-china-bugs",
                        "session_id": "m2-sapphire-china-bugs",
                        "sub_prompt": argv[argv.index("--message") + 1],
                        "wave_order": 1,
                    }],
                }),
                "",
            )

        with tempfile.TemporaryDirectory() as tmp:
            plans_dir = Path(tmp) / "email-action-plans"
            client = FakeClient()
            client.labels = [{"id": "lbl-needs", "name": "Needs Decision"}]
            orig_meta = client.team_metadata
            client.team_metadata = lambda key: {**orig_meta(key), "labels": {"nodes": client.labels}}

            first_run = pipeline.ingest_workiq_payload(
                workiq_payload,
                plans_dir=plans_dir,
                client=client,
                propose_linear=True,
                runner=fake_router,
            )
            self.assertTrue(first_run["ok"])
            self.assertEqual(first_run["actionable_count"], 1)
            self.assertEqual(first_run["skipped_count"], 1)
            item = first_run["actionable"][0]
            self.assertEqual(item["slug"], "bingchina-itms-90683")
            self.assertTrue(Path(item["plan_file"]).is_file())
            self.assertEqual(Path(item["plan_file"]).stat().st_mode & 0o777, 0o600)
            self.assertFalse(item["linear"]["deduplicated"])
            self.assertEqual(client.mutations[0][0], "create")
            self.assertEqual(client.mutations[0][1]["label_ids"], ["lbl-needs"])
            self.assertEqual(client.mutations[0][1]["assignee_id"], daily_brief.OWNER_ID)

            second_run = pipeline.ingest_workiq_payload(
                workiq_payload,
                plans_dir=plans_dir,
                client=client,
                propose_linear=True,
                runner=fake_router,
            )
            self.assertTrue(second_run["actionable"][0]["linear"]["deduplicated"])
            self.assertEqual([m[0] for m in client.mutations], ["create", "update"])

            created_issue = issue(
                "TIA-562",
                client.mutations[0][1]["title"],
                "Todo",
                "unstarted",
                description=client.mutations[0][1]["description"],
                project={"name": "Work Business"},
                labels={"nodes": [{"name": "Needs Decision"}]},
            )
            prose_issue = issue(
                "TIA-561",
                "[Workflow] Bing doc with placeholder",
                "In Progress",
                "started",
                project={"name": "Work Business"},
                description='Prose mentions `<!-- GTT-EMAIL-ACTION:v1 {"email_item_id": "...", "plan_file": "..."} -->`',
            )
            eligible, warnings = daily_brief.collect_linear(FakeClient([created_issue, prose_issue]), TODAY)
            self.assertEqual([row["identifier"] for row in eligible], ["TIA-562"])
            self.assertFalse(warnings)

            markdown, todos = daily_brief.render_brief(TODAY, eligible, [], [], [])
            self.assertEqual(daily_brief.validate_markdown(markdown), [])
            self.assertEqual(len(todos), 1)
            self.assertEqual(todos[0]["group"], "needs")
            self.assertIn(f"[📧 打开原邮件]({raw_applink})", markdown)
            self.assertIn("[TIA-562](https://linear.app/test/TIA-562)", markdown)


if __name__ == "__main__":
    unittest.main()
