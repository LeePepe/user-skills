#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills/goal-task/scripts/linear-sync.py"
QUEUE_SCRIPT = ROOT / "skills/goal-task/scripts/goal-queue.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


linear_sync = load_module(SCRIPT, "linear_sync_test")
goal_queue = load_module(QUEUE_SCRIPT, "goal_queue_test")


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.issue = {
            "id": "issue-id",
            "identifier": "TIA-19",
            "url": "https://linear.app/tianpeili/issue/TIA-19",
            "title": "Goal",
            "description": "Owner notes",
            "state": {"id": "todo", "name": "Todo", "type": "unstarted"},
            "team": {"id": "team-id", "key": "TIA", "name": "Tianpeili"},
            "labels": {"nodes": []},
            "comments": {"nodes": []},
        }

    def __call__(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        self.calls.append((query, variables))
        if "TeamMetadata" in query:
            return {
                "data": {
                    "teams": {
                        "nodes": [
                            {
                                "id": "team-id",
                                "key": "TIA",
                                "name": "Tianpeili",
                                "states": {
                                    "nodes": [
                                        {"id": "todo", "name": "Todo", "type": "unstarted"},
                                        {"id": "progress", "name": "In Progress", "type": "started"},
                                        {"id": "review", "name": "In Review", "type": "started"},
                                        {"id": "done", "name": "Done", "type": "completed"},
                                        {"id": "cancelled", "name": "Canceled", "type": "canceled"},
                                        {"id": "backlog", "name": "Backlog", "type": "backlog"},
                                    ]
                                },
                                "labels": {"nodes": [{"id": "blocked", "name": "Blocked"}]},
                            }
                        ]
                    }
                }
            }
        if "IssueByIdentifier" in query:
            return {"data": {"issues": {"nodes": [self.issue]}}}
        if "CreateIssue" in query:
            self.issue = {**self.issue, "identifier": "TIA-20", "url": "https://linear.app/tianpeili/issue/TIA-20"}
            return {"data": {"issueCreate": {"success": True, "issue": self.issue}}}
        if "UpdateIssue" in query:
            self.issue = {**self.issue, "description": variables["input"].get("description"), "state": {"id": variables["input"].get("stateId"), "name": "In Progress", "type": "started"}}
            return {"data": {"issueUpdate": {"success": True, "issue": self.issue}}}
        if "CreateComment" in query:
            return {"data": {"commentCreate": {"success": True, "comment": {"id": "comment-id"}}}}
        raise AssertionError(f"Unexpected query: {query}")


class LinearSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = ROOT / ".linear-sync-test"
        if self.work.exists():
            shutil.rmtree(self.work)
        self.work.mkdir()
        (self.work / ".queue.lock").touch()
        (self.work / "ledger.md").write_text(
            "# Goal: demo\n"
            "- Owner: tianpei\n"
            "- Status: in_progress\n\n"
            "## Tasks\n"
            "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
            "|---|---|---|---|---|---|\n"
            "| TK1 | — | Build it | writer | in_progress | — |\n"
            "## Events\n"
            "- 2026-11-04T14:00:00+08:00 | EVT=TASK_BLOCKED | id=E7 | task=TK1 | note=missing input\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def test_key_falls_back_to_zsh_file(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            linear_sync, "_read_key_file", side_effect=[None, "from-file"]
        ):
            self.assertEqual(linear_sync.load_api_key(), "from-file")

    def test_sync_updates_description_state_and_deduplicates_blocker(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        team = client.team("TIA")
        issue = client.issue("TIA-19")
        result = linear_sync._sync_issue(client, issue, team, self.work)
        self.assertIn("Owner: tianpei", result["description"])
        self.assertIn("## Tasks", result["description"])
        self.assertEqual(result["state"]["id"], "progress")
        comment_calls = [call for call in transport.calls if "CreateComment" in call[0]]
        self.assertEqual(len(comment_calls), 1)
        result["comments"] = {"nodes": [{"id": "c", "body": "<!-- GTT-LINEAR-EVENT:E7 -->"}]}
        linear_sync._sync_issue(client, result, team, self.work)
        comment_calls = [call for call in transport.calls if "CreateComment" in call[0]]
        self.assertEqual(len(comment_calls), 1)

    def test_ensure_goal_creates_and_binds_issue(self) -> None:
        transport = FakeTransport()
        args = type("Args", (), {"handoff_dir": str(self.work), "issue": "", "team": "TIA"})()
        with mock.patch.object(linear_sync, "_client_or_fail", return_value=linear_sync.GraphQLClient("x", transport)):
            payload = linear_sync.cmd_ensure_goal(args)
        self.assertEqual(payload["identifier"], "TIA-20")
        self.assertIn("- Linear: TIA-20", (self.work / "ledger.md").read_text(encoding="utf-8"))

    def test_queue_hook_is_disabled_for_isolated_tests(self) -> None:
        with mock.patch.dict(os.environ, {"GTT_LINEAR_SYNC": "0"}, clear=False), mock.patch.object(
            goal_queue.subprocess, "Popen"
        ) as popen:
            goal_queue._maybe_linear_sync(self.work)
        popen.assert_not_called()

    def test_help_parser_has_all_subcommands(self) -> None:
        parser = linear_sync.build_parser()
        self.assertEqual(parser.parse_args(["idea", "--title", "x"]).subcommand, "idea")
        self.assertEqual(parser.parse_args(["ensure-goal", "--handoff-dir", str(self.work)]).subcommand, "ensure-goal")
        self.assertTrue(parser.parse_args(["sync-ledger", "--handoff-dir", str(self.work), "--quiet"]).quiet)


if __name__ == "__main__":
    unittest.main()
