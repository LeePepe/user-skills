import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("daily_brief", Path(__file__).parents[1] / "skills/linear-board/scripts/daily-brief.py")
daily_brief = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(daily_brief)


class DailyBriefTests(unittest.TestCase):
    def test_render_has_contract_and_sequential_groups(self):
        markdown, todos = daily_brief.render_brief(date(2026, 11, 15), [], [], [], [])
        self.assertTrue(markdown.startswith("<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-15 -->"))
        self.assertIn("昨日交付汇总（Yesterday's Rollup · 2026-11-14）", markdown)
        self.assertIn("今日 Todo 与待拍板清单（Today's Actionable Todos · 2026-11-15）", markdown)
        self.assertIn("## 🤖 @bot 一键点单指令栏", markdown)
        self.assertEqual([item["number"] for item in todos], list(range(1, len(todos) + 1)))
        populated = [{"identifier": "TIA-1", "title": "Needs Decision", "state": {"name": "Blocked", "type": "backlog"}, "labels": {"nodes": []}}]
        markdown, todos = daily_brief.render_brief(date(2026, 11, 15), populated, [], [], [])
        self.assertIn("- [ ] **[1]**", markdown)
        self.assertIn("TIA-1 Needs Decision", markdown)

    def test_scope_and_ledger_filtering(self):
        self.assertTrue(daily_brief._excluded_issue({"title": "[Root] Work Business goal"}))
        self.assertFalse(daily_brief._excluded_issue({"title": "Personal goal", "project": {"name": "Personal project"}}))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "personal").mkdir()
            (root / "work").mkdir()
            line = "- 2026-11-14 | EVT=DONE | delivered"
            (root / "personal" / "ledger.md").write_text(line)
            (root / "work" / "ledger.md").write_text(line)
            events, warnings = daily_brief.collect_ledgers(root, date(2026, 11, 14))
        self.assertEqual(len(events), 1)
        self.assertEqual(warnings, [])

    def test_cst_boundary_and_scope(self):
        self.assertTrue(daily_brief._date_in("2026-11-13T16:30:00Z", date(2026, 11, 14)))
        self.assertFalse(daily_brief._date_in("2026-11-14T16:00:00Z", date(2026, 11, 14)))
        root_child = {"title": "Personal child", "parent": {"title": "[Root]"}}
        self.assertFalse(daily_brief._excluded_issue(root_child))
        self.assertFalse(daily_brief._excluded_issue({"title": "probing ideas", "project": None}))
        self.assertTrue(daily_brief._excluded_issue({"title": "Personal", "parent": {"title": "Bing roadmap"}}))

    def test_ledger_timestamp_and_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); goal = root / "personal-goal"; goal.mkdir()
            (goal / "ledger.md").write_text("- 2026-11-13T15:59:00Z | EVT=DONE | note=2026-11-14\n- 2026-11-14T01:00:00+08:00 | EVT=GOAL_DONE | id=x\n")
            events, warnings = daily_brief.collect_ledgers(root, date(2026, 11, 14))
        self.assertEqual(len(events), 1); self.assertFalse(warnings)
        self.assertEqual(daily_brief._active_events([{"EVT": "NEED_GO", "id": "x"}, {"EVT": "RESOLVED", "id": "x"}]), [])

    def test_dry_run_does_not_propose(self):
        with mock.patch.object(daily_brief, "propose") as propose:
            result = daily_brief.generate(date(2026, 11, 15), dev_root=Path("/missing"), handoff_root=Path("/missing"), client=object(), propose_linear=True, dry_run=True)
        propose.assert_not_called()
        self.assertIsNone(result["linear_issue"])
        self.assertEqual(result["date"], "2026-11-15")

    def test_propose_updates_existing_issue_preserving_state(self):
        class Fake:
            def team_metadata(self, key):
                return {"id": "team", "states": {"nodes": [{"id": "todo", "name": "Todo"}]}}
            def find_issue_by_dedup_marker(self, team, marker):
                return {"id": "issue", "state": {"type": "started"}}
            def find_issue_by_title(self, team, title):
                return None
            def issue(self, identifier):
                return {"id": "parent"}
            def update_issue(self, issue_id, **kwargs):
                kwargs["issue_id"] = issue_id
                return kwargs
        result = daily_brief.propose(Fake(), "[Daily Brief 2026-11-15] title", "body", "TIA-375")
        self.assertEqual(result["parent_id"], "parent")
        self.assertEqual(result["assignee_id"], daily_brief.OWNER_ID)


if __name__ == "__main__":
    unittest.main()
