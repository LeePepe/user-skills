#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills/team-lead/scripts/linear-sync.py"
QUEUE_SCRIPT = ROOT / "skills/team-lead/scripts/goal-queue.py"
TELEMETRY_SCRIPT = ROOT / "skills/team-lead/scripts/gtt-telemetry-export.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


linear_sync = load_module(SCRIPT, "linear_sync_test")
goal_queue = load_module(QUEUE_SCRIPT, "goal_queue_test")
telemetry_export = load_module(TELEMETRY_SCRIPT, "telemetry_export_test")


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.issue = {
            "id": "issue-id",
            "identifier": "TIA-19",
            "url": "https://linear.app/tianpeili/issue/TIA-19",
            "title": "Goal",
            "description": "Owner notes",
            "priority": 2,
            "createdAt": "2026-11-01T10:00:00Z",
            "updatedAt": "2026-11-02T12:00:00Z",
            "state": {"id": "todo", "name": "Todo", "type": "unstarted"},
            "team": {"id": "team-id", "key": "TIA", "name": "Tianpeili"},
            "project": None,
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
                                "projects": {
                                    "nodes": [
                                        {"id": "agent-workflow", "name": "Agent Workflow"},
                                        {"id": "bing", "name": "Bing"},
                                        {"id": "personal", "name": "Personal project"},
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        if "ListIssues" in query:
            return {"data": {"issues": {"nodes": [self.issue]}}}
        if "issue(id: $id)" in query:
            return {"data": {"issue": self.issue}}
        if "IssueByIdentifierFallback" in query:
            return {"data": {"issues": {"nodes": [self.issue]}}}
        if "CreateIssue" in query:
            self.issue = {**self.issue, "identifier": "TIA-20", "url": "https://linear.app/tianpeili/issue/TIA-20"}
            return {"data": {"issueCreate": {"success": True, "issue": self.issue}}}
        if "UpdateIssue" in query:
            label_ids = variables["input"].get("labelIds", [])
            labels = {"nodes": [{"id": label_id, "name": label_id} for label_id in label_ids]}
            self.issue = {
                **self.issue,
                "description": variables["input"].get("description"),
                "state": {"id": variables["input"].get("stateId"), "name": "In Progress", "type": "started"},
                "project": (
                    {"id": variables["input"]["projectId"], "name": variables["input"]["projectId"]}
                    if variables["input"].get("projectId")
                    else self.issue.get("project")
                ),
                "labels": labels,
            }
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
        ), mock.patch.object(linear_sync, "_credential_path", return_value=self.work / "missing.json"), \
             mock.patch.object(linear_sync, "_clip_credential_path", return_value=self.work / "missing-clip.json"):
            self.assertEqual(linear_sync.load_api_key(), "from-file")

    def test_repo_root_tempdir_is_isolated_and_skipped_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            handoff_dir = Path(temp_dir) / "chain"
            handoff_dir.mkdir()
            self.assertTrue(linear_sync._is_isolated_handoff_dir(handoff_dir))
            args = type(
                "Args",
                (),
                {"handoff_dir": str(handoff_dir), "event_note": "", "quiet": True},
            )()
            with mock.patch.object(linear_sync, "_client_or_fail") as client:
                with mock.patch.dict(os.environ, {"GTT_LINEAR_SYNC": "0"}, clear=False):
                    self.assertEqual(
                        linear_sync.cmd_sync_ledger(args),
                        {"ok": True, "skipped": "isolated_handoff_dir"},
                    )
                client.assert_not_called()
                with mock.patch.dict(os.environ, {"GTT_LINEAR_SYNC": "1"}, clear=False):
                    with mock.patch.object(linear_sync, "_client_or_fail", side_effect=linear_sync.LinearSyncError("forced")):
                        with self.assertRaises(linear_sync.LinearSyncError):
                            linear_sync.cmd_sync_ledger(args)

    def test_canonical_handoff_path_is_not_isolated(self) -> None:
        self.assertFalse(
            linear_sync._is_isolated_handoff_dir(
                Path("/") / "Users" / "example" / "Development" / "handoff" / "my-real-goal"
            )
        )

    def test_infers_linear_project_from_domain_keywords(self) -> None:
        self.assertEqual(linear_sync._infer_project_name(None, "workflow-hub team-lead"), "Agent Workflow")
        self.assertEqual(linear_sync._infer_project_name(None, "Sapphire DSAT"), "Bing")
        self.assertEqual(linear_sync._infer_project_name(None, "VitalStride health app"), "Personal project")
        self.assertEqual(
            linear_sync._infer_project_name(None, "Financial bills\n- 合同版本: team-lead@fa947507a7d8"),
            "Personal project",
        )
        self.assertEqual(linear_sync._infer_project_name(" Custom ", "Bing"), "Custom")

    def test_domain_map_routes_goals_to_root_topics(self) -> None:
        self.assertEqual(linear_sync._goal_topic("project-root-tracker", "", None)["topic_id"], "skills-workflow")
        self.assertEqual(linear_sync._goal_topic("devsync1030", "", None)["topic_id"], "sapphire-china-bugs")
        self.assertEqual(linear_sync._goal_topic("aidash-header-flat-toolbar", "", None)["topic_id"], "aidash-aidata")
        self.assertEqual(linear_sync._goal_topic("fin-product-and-design-v2", "", None)["topic_id"], "financial")

    def test_goal_route_resolves_project_milestone_without_root_parent(self) -> None:
        class RouteClient:
            def project_milestones(self, project_id: str):
                self.project_id = project_id
                return [{"id": "milestone-id", "name": "Skills Workflow"}]

        client = RouteClient()
        team = {"key": "TIA", "projects": {"nodes": [{"id": "skills-project", "name": "user-skills"}]}}
        route = linear_sync._resolve_goal_route(client, team, "project-root-tracker", "", self.work)
        self.assertEqual(route, {
            "topic_id": "skills-workflow",
            "project_id": "skills-project",
            "milestone_id": "milestone-id",
        })

    def test_update_issue_can_clear_parent(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        client.update_issue("issue-id", clear_parent=True)
        update_calls = [call for call in transport.calls if "UpdateIssue" in call[0]]
        self.assertIsNone(update_calls[-1][1]["input"]["parentId"])

    def test_aggregate_state_keeps_in_progress_when_partially_completed(self) -> None:
        team = FakeTransport()("query TeamMetadata", {})["data"]["teams"]["nodes"][0]
        tasks = [
            {"task_id": "T0", "title": "Spec", "status": "done", "pr": "PR#55"},
            {"task_id": "T1", "title": "Frontend", "status": "in_review", "pr": "PR#56"},
            {"task_id": "T2", "title": "Backend", "status": "planned", "pr": ""},
        ]
        state = linear_sync._aggregate_state(team, tasks, [], {})
        self.assertEqual(state["name"], "In Progress")

    def test_parse_numeric_handles_k_suffix_and_currency(self) -> None:
        self.assertEqual(linear_sync._parse_numeric("33k"), 33000.0)
        self.assertEqual(linear_sync._parse_numeric("≥1.5M"), 1500000.0)
        self.assertEqual(linear_sync._parse_numeric("$0.42"), 0.42)
        self.assertEqual(linear_sync._parse_numeric("12.5s"), 12.5)

    def test_project_id_is_case_insensitive_and_missing_projects_are_safe(self) -> None:
        team = FakeTransport()("query TeamMetadata", {})["data"]["teams"]["nodes"][0]
        self.assertEqual(linear_sync._project_id(team, "agent workflow"), "agent-workflow")
        self.assertIsNone(linear_sync._project_id(team, "Unknown"))

    def test_sync_attaches_inferred_project(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        team = client.team("TIA")
        result = linear_sync._sync_issue(client, client.issue("TIA-19"), team, self.work)
        update_calls = [call for call in transport.calls if "UpdateIssue" in call[0]]
        self.assertEqual(update_calls[-1][1]["input"]["projectId"], "agent-workflow")
        self.assertEqual(result["project"]["id"], "agent-workflow")

    def test_create_issue_passes_project_id(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        client.create_issue(
            team_id="team-id",
            title="Workflow idea",
            description="",
            project_id="agent-workflow",
        )
        create_calls = [call for call in transport.calls if "CreateIssue" in call[0]]
        self.assertEqual(create_calls[-1][1]["input"]["projectId"], "agent-workflow")

    def test_board_read_commands_return_structured_issue_fields(self) -> None:
        transport = FakeTransport()
        transport.issue["labels"] = {"nodes": [{"id": "l1", "name": "Blocked"}, {"id": "l2", "name": ""}]}
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        with mock.patch.object(linear_sync, "_client_or_fail", return_value=client):
            listed = linear_sync.cmd_list_issues(linear_sync.build_parser().parse_args(["list-issues"]))
            detail = linear_sync.cmd_get_issue(linear_sync.build_parser().parse_args(["get-issue", "--id", "TIA-19"]))
            idea = linear_sync.cmd_idea(
                linear_sync.build_parser().parse_args(["idea", "--title", "Voice todo", "--mode", "auto"])
            )
        summary = listed["issues"][0]
        self.assertEqual(summary["id"], "issue-id")
        self.assertEqual(summary["identifier"], "TIA-19")
        self.assertEqual(summary["state"], "Todo")
        self.assertEqual(summary["stateType"], "unstarted")
        self.assertEqual(summary["createdAt"], "2026-11-01T10:00:00Z")
        self.assertEqual(summary["updatedAt"], "2026-11-02T12:00:00Z")
        self.assertEqual(summary["labels"], ["Blocked"])
        self.assertEqual(detail["issue"]["description"], "Owner notes")
        self.assertEqual(detail["issue"]["stateType"], "unstarted")
        self.assertEqual(detail["issue"]["createdAt"], "2026-11-01T10:00:00Z")
        self.assertEqual(detail["issue"]["labels"], ["Blocked"])
        self.assertIn("comments", detail["issue"])
        self.assertIn("history", detail["issue"])
        self.assertTrue(idea["ok"])
        self.assertEqual(idea["command"], "idea")
        self.assertEqual(idea["issue"]["identifier"], "TIA-20")
        self.assertEqual(idea["issue"]["id"], "issue-id")
        self.assertEqual(idea["issue"]["stateType"], "unstarted")
        self.assertEqual(idea["issue"]["createdAt"], "2026-11-01T10:00:00Z")
        self.assertEqual(idea["issue"]["labels"], ["Blocked"])
        list_query = next(q for q, _ in transport.calls if "ListIssues" in q)
        create_query = next(q for q, _ in transport.calls if "CreateIssue" in q)
        self.assertIn("createdAt", list_query)
        self.assertIn("createdAt", create_query)

    def test_update_and_comment_modes_are_side_effect_safe(self) -> None:
        update_args = linear_sync.build_parser().parse_args([
            "update-issue", "--id", "TIA-19", "--title", "New title", "--mode", "observe",
        ])
        comment_args = linear_sync.build_parser().parse_args([
            "comment", "--id", "TIA-19", "--body", "hello", "--mode", "off",
        ])
        with mock.patch.object(linear_sync, "_client_or_fail") as client:
            update = linear_sync.cmd_update_issue(update_args)
            comment = linear_sync.cmd_comment(comment_args)
        client.assert_not_called()
        self.assertEqual(update["planned_mutations"][0]["variables"]["input"]["title"], "New title")
        self.assertEqual(comment["decision"], "off (skipped)")

    def test_oauth_app_create_as_user_is_added_to_issue_and_comment_inputs(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("oauth", transport)
        with mock.patch.object(linear_sync, "_oauth_app_enabled", return_value=True):
            client.create_issue(team_id="team-id", title="Goal", description="", create_as_user="Bot")
            client.create_comment("issue-id", "hello", "Bot")
        create = next(values for query, values in transport.calls if "CreateIssue" in query)
        comment = next(values for query, values in transport.calls if "CreateComment" in query)
        self.assertEqual(create["input"]["createAsUser"], "Bot")
        self.assertEqual(comment["input"]["createAsUser"], "Bot")

    def test_oauth_app_detection_requires_bearer_token_or_exact_app_actor(self) -> None:
        credentials = self.work / "config" / "linear" / "credentials.json"
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(credentials)}, clear=False):
            linear_sync.save_credentials("Bearer lin_oauth_token")
            self.assertTrue(linear_sync._oauth_app_enabled())
            linear_sync.save_credentials("Bearer ")
            self.assertFalse(linear_sync._oauth_app_enabled())
            linear_sync.save_credentials("lin_api_token")
            self.assertFalse(linear_sync._oauth_app_enabled())
            linear_sync.save_credentials("lin_api_token", actor="app")
            self.assertTrue(linear_sync._oauth_app_enabled())
            linear_sync.save_credentials("lin_api_token", actor="application")
            self.assertFalse(linear_sync._oauth_app_enabled())

    def test_update_observe_plans_named_fields_without_network_calls(self) -> None:
        args = linear_sync.build_parser().parse_args([
            "update-issue", "--id", "TIA-19", "--state", "In Progress",
            "--project", "Agent Workflow", "--add-label", "Blocked", "--mode", "observe",
        ])
        with mock.patch.object(linear_sync, "_client_or_fail") as client:
            result = linear_sync.cmd_update_issue(args)
        client.assert_not_called()
        input_data = result["planned_mutations"][0]["variables"]["input"]
        self.assertEqual(input_data["stateId"], "In Progress")
        self.assertEqual(input_data["projectId"], "Agent Workflow")
        self.assertEqual(input_data["labelIds"], ["Blocked"])

    def test_observe_sync_comment_mutations_match_create_as_user_input(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("oauth", transport)
        team = client.team("TIA")
        with mock.patch.object(linear_sync, "_oauth_app_enabled", return_value=True):
            result = linear_sync._sync_issue(
                client, client.issue("TIA-19"), team, self.work,
                mode="observe", create_as_user="Bot",
            )
        comments = [
            mutation for mutation in result["planned_mutations"]
            if mutation["operation"] == "CreateComment"
        ]
        self.assertTrue(comments)
        for mutation in comments:
            self.assertEqual(mutation["variables"]["input"]["createAsUser"], "Bot")

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

    def test_issue_uses_direct_identifier_query_with_filtered_fallback(self) -> None:
        calls: list[str] = []

        def transport(query: str, variables: dict[str, object]) -> dict[str, object]:
            calls.append(query)
            if "issue(id: $id)" in query:
                return {"data": {"issue": None}}
            if "IssueByIdentifierFallback" in query:
                return {"data": {"issues": {"nodes": [FakeTransport().issue]}}}
            raise AssertionError(query)

        issue = linear_sync.GraphQLClient("not-a-secret", transport).issue("TIA-19")
        self.assertEqual(issue["identifier"], "TIA-19")
        self.assertIn("issue(id: $id)", calls[0])
        self.assertIn("filter: {identifier: {eq: $identifier}}", calls[1])

    def test_ensure_goal_creates_and_binds_issue(self) -> None:
        transport = FakeTransport()
        args = type("Args", (), {"handoff_dir": str(self.work), "issue": "", "team": "TIA"})()
        with mock.patch.object(linear_sync, "_client_or_fail", return_value=linear_sync.GraphQLClient("x", transport)):
            payload = linear_sync.cmd_ensure_goal(args)
        self.assertEqual(payload["identifier"], "TIA-20")
        self.assertEqual(payload["state"], "In Progress")
        self.assertIn("- Linear: TIA-20", (self.work / "ledger.md").read_text(encoding="utf-8"))

    def test_resolved_blocker_is_not_synced_or_labeled(self) -> None:
        ledger = (self.work / "ledger.md").read_text(encoding="utf-8")
        (self.work / "ledger.md").write_text(
            ledger
            + "- 2026-11-04T14:01:00+08:00 | EVT=RESOLVED | id=E7 | task=TK1 | note=fixed\n",
            encoding="utf-8",
        )
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        team = client.team("TIA")
        result = linear_sync._sync_issue(client, client.issue("TIA-19"), team, self.work)
        self.assertNotIn("blocked", [node["id"] for node in result["labels"]["nodes"]])
        self.assertEqual(
            [call for call in transport.calls if "CreateComment" in call[0]],
            [],
        )

    def test_missing_ledger_is_linear_sync_error(self) -> None:
        (self.work / "ledger.md").unlink()
        args = type("Args", (), {"handoff_dir": str(self.work), "issue": "", "team": "TIA"})()
        with mock.patch.object(
            linear_sync,
            "_client_or_fail",
            return_value=linear_sync.GraphQLClient("x", FakeTransport()),
        ):
            with self.assertRaises(linear_sync.LinearSyncError):
                linear_sync.cmd_ensure_goal(args)

    def test_sync_ledger_rechecks_binding_inside_queue_lock(self) -> None:
        (self.work / "ledger.md").write_text(
            "# Goal: demo\n- Owner: tianpei\n- Status: in_progress\n\n## Tasks\n"
            "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
            "|---|---|---|---|---|---|\n"
            "| TK1 | — | Build it | writer | in_progress | — |\n",
            encoding="utf-8",
        )

        class BindingLock:
            def __enter__(self) -> None:
                (self_path / "ledger.md").write_text(
                    "# Goal: demo\n- Owner: tianpei\n- Status: in_progress\n"
                    "- Linear: TIA-19\n\n## Tasks\n"
                    "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
                    "|---|---|---|---|---|---|\n"
                    "| TK1 | — | Build it | writer | in_progress | — |\n",
                    encoding="utf-8",
                )

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                return None

        self_path = self.work
        transport = FakeTransport()
        args = type("Args", (), {"handoff_dir": str(self.work), "event_note": ""})()
        with mock.patch.object(
            linear_sync,
            "_client_or_fail",
            return_value=linear_sync.GraphQLClient("x", transport),
        ), mock.patch.object(linear_sync, "_queue_lock", return_value=BindingLock()):
            payload = linear_sync.cmd_sync_ledger(args)
        self.assertEqual(payload["identifier"], "TIA-19")
        self.assertEqual(len([call for call in transport.calls if "CreateIssue" in call[0]]), 0)

    def test_queue_hook_is_disabled_for_isolated_tests(self) -> None:
        with mock.patch.dict(os.environ, {"GTT_LINEAR_SYNC": "0"}, clear=False), mock.patch.object(
            goal_queue.subprocess, "Popen"
        ) as popen:
            goal_queue._maybe_linear_sync(self.work)
        popen.assert_not_called()

    def test_queue_hook_skips_isolated_dirs_unless_explicitly_enabled(self) -> None:
        with mock.patch.object(goal_queue, "_linear_key_configured", return_value=True), mock.patch.object(
            goal_queue.subprocess, "Popen"
        ) as popen:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GTT_LINEAR_SYNC", None)
                goal_queue._maybe_linear_sync(Path("/private/tmp/synthetic-goal"))
            popen.assert_not_called()
            with mock.patch.dict(os.environ, {"GTT_LINEAR_SYNC": "1"}, clear=False):
                goal_queue._maybe_linear_sync(Path("/private/tmp/synthetic-goal"))
            popen.assert_called_once()

    def test_next_wave_only_syncs_when_marking_in_progress(self) -> None:
        with mock.patch.object(goal_queue, "cmd_next_wave", return_value={"ok": True}), mock.patch.object(
            goal_queue, "_maybe_notify"
        ), mock.patch.object(goal_queue, "_maybe_linear_sync") as sync:
            self.assertEqual(
                goal_queue.main(["next-wave", "--handoff-dir", str(self.work)]),
                0,
            )
            sync.assert_not_called()
            self.assertEqual(
                goal_queue.main(
                    ["next-wave", "--handoff-dir", str(self.work), "--mark-in-progress"]
                ),
                0,
            )
            sync.assert_called_once_with(Path(self.work))

    def test_help_parser_has_all_subcommands(self) -> None:
        parser = linear_sync.build_parser()
        self.assertEqual(parser.parse_args(["idea", "--title", "x"]).subcommand, "idea")
        self.assertEqual(parser.parse_args(["idea", "--title", "x", "--project", "Bing"]).project, "Bing")
        self.assertEqual(parser.parse_args(["ensure-goal", "--handoff-dir", str(self.work)]).subcommand, "ensure-goal")
        self.assertEqual(parser.parse_args(["ensure-goal", "--handoff-dir", str(self.work), "--project", "Bing"]).project, "Bing")
        self.assertTrue(parser.parse_args(["sync-ledger", "--handoff-dir", str(self.work), "--quiet"]).quiet)

    def test_mode_off_skips_without_client_or_network(self) -> None:
        args = linear_sync.build_parser().parse_args(
            ["idea", "--title", "offline", "--mode", "off"]
        )
        with mock.patch.object(linear_sync, "_client_or_fail") as client:
            payload = linear_sync.cmd_idea(args)
        client.assert_not_called()
        self.assertEqual(payload["decision"], "off (skipped)")
        self.assertEqual(payload["planned_mutations"], [])

    def test_observe_plans_mutations_from_offline_snapshot(self) -> None:
        snapshot = json.dumps({
            "team": {
                "id": "team-id",
                "states": {"nodes": [{"id": "triage", "name": "Triage"}]},
                "labels": {"nodes": []},
                "projects": {"nodes": []},
            },
            "issue": {},
        })
        args = linear_sync.build_parser().parse_args([
            "idea", "--title", "observed", "--mode", "observe",
            "--offline-snapshot", snapshot,
        ])
        payload = linear_sync.cmd_idea(args)
        self.assertEqual(payload["decision"], "observe (would accept)")
        self.assertTrue(payload["would_accept"])
        self.assertEqual(payload["planned_mutations"][0]["operation"], "CreateIssue")

    def test_observe_ensure_and_sync_are_offline_and_do_not_bind(self) -> None:
        before = (self.work / "ledger.md").read_bytes()
        ensure_args = linear_sync.build_parser().parse_args([
            "ensure-goal", "--handoff-dir", str(self.work), "--mode", "observe",
        ])
        sync_args = linear_sync.build_parser().parse_args([
            "sync-ledger", "--handoff-dir", str(self.work), "--mode", "observe",
            "--record-observe",
        ])
        with mock.patch.object(linear_sync, "_client_or_fail", side_effect=AssertionError("network")):
            ensure = linear_sync.cmd_ensure_goal(ensure_args)
            sync = linear_sync.cmd_sync_ledger(sync_args)
        self.assertEqual(ensure["mode"], "observe")
        self.assertTrue(ensure["planned_mutations"])
        self.assertTrue(sync["planned_mutations"])
        self.assertEqual(before, (self.work / "ledger.md").read_bytes())
        self.assertTrue((self.work / "receipts" / "linear-observe.jsonl").is_file())

    def test_observe_noop_and_auto_noop_when_issue_is_current(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("x", transport)
        team = client.team("TIA")
        first = linear_sync._sync_issue(client, client.issue("TIA-19"), team, self.work)
        first["comments"] = {"nodes": [{"body": "<!-- GTT-LINEAR-EVENT:E7 -->"}]}
        second = linear_sync._sync_issue(client, first, team, self.work)
        self.assertEqual(second["_mutations_executed"], 0)
        observed = linear_sync._sync_issue(
            linear_sync.GraphQLClient("offline", lambda q, v: {"data": {}}),
            first, team, self.work, mode="observe",
        )
        self.assertEqual(observed["diff"]["local_effects"], [])

    def test_idea_dedup_snapshot_returns_duplicate_noop(self) -> None:
        snapshot = json.dumps({
            "team": {"id": "team-id", "states": {"nodes": []}, "labels": {"nodes": []}, "projects": {"nodes": []}},
            "issue": {"identifier": "TIA-77", "url": "https://linear.app/TIA-77",
                      "description": "<!-- GTT-LINEAR-DEDUP:retro-x -->",
                      "state": {"name": "Backlog", "type": "backlog"}, "project": {"name": "Agent Workflow"}},
        })
        args = linear_sync.build_parser().parse_args([
            "idea", "--title", "retro", "--mode", "observe", "--dedup-key", "retro-x",
            "--offline-snapshot", snapshot,
        ])
        payload = linear_sync.cmd_idea(args)
        self.assertEqual(payload["decision"], "no-op (duplicate)")
        self.assertEqual(payload["duplicate_of"], "TIA-77")

    def test_description_filters_machine_metadata_events_and_empty_attribution(self) -> None:
        ledger = (
            "# Goal: demo\n"
            "- **目标**: Clean summary\n"
            "- Owner: tianpei\n"
            "- TL: bot\n"
            "- 状态: in_progress\n"
            "- 验收:\n"
            "- 2026-11-04T14:00:00+08:00 | EVT=DONE | id=E7\n"
            "| ID | 标题 |\n"
            "## Tasks\n"
        )
        block = linear_sync._description_block(
            "demo", ledger,
            [{"task_id": "TK1", "title": "Build it", "status": "in_progress", "pr": ""}],
            None,
        )
        self.assertIn("Owner: tianpei", block)
        self.assertNotIn("TL: bot", block)
        self.assertNotIn("in_progress |", block)
        self.assertNotIn("runs=", block)
        self.assertNotIn("Execution & Cost Attribution", block)

    def test_updated_description_removes_duplicate_task_checklist(self) -> None:
        existing = "Intro\n\n## Tasks Checklist\n- [ ] Old task\n\n## Details\nDetails"
        block = "<!-- GTT-LINEAR-SYNC:START -->\nnew\n<!-- GTT-LINEAR-SYNC:END -->"
        updated = linear_sync._updated_description(existing, block)
        self.assertNotIn("Old task", updated)
        self.assertEqual(updated.count("## Tasks"), 0)
        self.assertIn("Intro", updated)
        self.assertIn("new", updated)

    def test_attribution_supports_dict_metrics_bulleted_receipts_and_pricing(self) -> None:
        (self.work / "queue.json").write_text(json.dumps({
            "tasks": [{"task_id": "TK1", "run_metrics": {
                "terminal": {"model": "test-model", "input_tokens": 1_000_000, "output_tokens": 500_000, "duration_sec": 4}
            }}]
        }), encoding="utf-8")
        receipts = self.work / "receipts"
        receipts.mkdir()
        (receipts / "tk1.md").write_text(
            "- **Task**: `TK1`\n- **Tokens**: 100\n- **Duration**: 2 sec\n- **Model**: test-model\n- **V5 Verdict**: PASS\n",
            encoding="utf-8",
        )
        prices = self.work / "prices.csv"
        prices.write_text("model,input_per_mtok,output_per_mtok,cache_read_per_mtok,cache_write_per_mtok\n"
                          "test-model,2,4,0,0\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GTT_MODEL_PRICES_CSV": str(prices)}, clear=False):
            block = linear_sync._description_block(
                "demo", (self.work / "ledger.md").read_text(encoding="utf-8"),
                [{"task_id": "TK1", "title": "Build it", "status": "done", "pr": ""}], self.work,
            )
        self.assertIn("$4.00", block)
        self.assertIn("## Execution & Cost Attribution", block)

    def test_milestone_comment_has_cost_attribution(self) -> None:
        (self.work / "queue.json").write_text(json.dumps({
            "tasks": [{"task_id": "TK1", "run_metrics": [{"input_tokens": 100, "cost_usd": 0.02}]}]
        }), encoding="utf-8")
        (self.work / "ledger.md").write_text(
            (self.work / "ledger.md").read_text(encoding="utf-8")
            + "- 2026-11-04T14:02:00+08:00 | EVT=DONE | id=E8 | task=TK1 | ref=PR #8\n",
            encoding="utf-8",
        )
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("x", transport)
        linear_sync._sync_issue(client, client.issue("TIA-19"), client.team("TIA"), self.work)
        comments = [v["input"]["body"] for q, v in transport.calls if "CreateComment" in q]
        self.assertTrue(any("Run attribution" in body for body in comments))

    def test_symlinked_credential_path_is_rejected(self) -> None:
        target = self.work / "real.json"
        target.write_text(json.dumps({"headerValue": "secret"}), encoding="utf-8")
        link = self.work / "credentials.json"
        link.symlink_to(target)
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(link)}, clear=False):
            with self.assertRaises(linear_sync.LinearSyncError):
                linear_sync.load_credential_info()
        real_dir = self.work / "real-dir"
        real_dir.mkdir()
        parent_link = self.work / "linked-dir"
        parent_link.symlink_to(real_dir, target_is_directory=True)
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(parent_link / "credentials.json")}, clear=False):
            with self.assertRaises(linear_sync.LinearSyncError):
                linear_sync.load_credential_info()

    def test_auto_sync_skips_update_when_issue_is_already_current(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("not-a-secret", transport)
        team = client.team("TIA")
        first = linear_sync._sync_issue(client, client.issue("TIA-19"), team, self.work)
        transport.calls.clear()
        linear_sync._sync_issue(client, first, team, self.work)
        self.assertEqual(
            [call for call in transport.calls if "UpdateIssue" in call[0]],
            [],
        )

    def test_idea_dedup_key_is_embedded_in_description(self) -> None:
        args = linear_sync.build_parser().parse_args([
            "idea", "--title", "retro", "--mode", "observe",
            "--dedup-key", "retro-2026-11-05",
        ])
        payload = linear_sync.cmd_idea(args)
        description = payload["planned_mutations"][0]["variables"]["input"]["description"]
        self.assertIn("<!-- GTT-LINEAR-DEDUP:retro-2026-11-05 -->", description)
        self.assertEqual(
            linear_sync.resolve_mode(None, subcommand="idea", source="retro-loop"),
            ("observe", None),
        )

    def test_execution_cost_attribution_uses_lower_bound_and_section(self) -> None:
        queue = {
            "tasks": [{
                "task_id": "TK1",
                "title": "Build it",
                "status": "done",
                "run_metrics": [
                    {"input_tokens": 1000, "output_tokens": 500, "duration_sec": 12, "cost_usd": 0.10},
                    {"duration_sec": 8},
                ],
            }]
        }
        (self.work / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
        text = linear_sync._description_block(
            "demo",
            (self.work / "ledger.md").read_text(encoding="utf-8"),
            [{"task_id": "TK1", "title": "Build it", "status": "done", "pr": ""}],
            self.work,
        )
        self.assertIn("## Execution & Cost Attribution", text)
        self.assertIn("≥", text)
        self.assertIn("TK1", text)

    def test_auth_set_show_remove_enforces_private_file_mode(self) -> None:
        credentials = self.work / "config" / "linear" / "credentials.json"
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(credentials)}, clear=False), \
             mock.patch("sys.stdin", mock.Mock(isatty=lambda: False, readline=lambda: "lin_api_secret\n")):
            self.assertEqual(linear_sync.main(["auth", "set", "--key-stdin"]), 0)
            self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
            self.assertEqual(credentials.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(linear_sync.main(["auth", "show"]), 0)
            self.assertEqual(linear_sync.main(["auth", "remove"]), 0)
        self.assertFalse(credentials.exists())

    def test_oauth_app_credentials_preserve_actor(self) -> None:
        credentials = self.work / "config" / "linear" / "credentials.json"
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(credentials)}, clear=False):
            linear_sync.save_credentials(
                "Bearer oauth-token",
                actor="app",
                refresh_token="rt-1",
                client_id="cid-1",
                client_secret="csec-1",
            )
            rec = linear_sync._credential_record()
            self.assertEqual(rec["actor"], "app")
            self.assertEqual(rec["refreshToken"], "rt-1")
            self.assertEqual(rec["clientId"], "cid-1")
            self.assertEqual(rec["clientSecret"], "csec-1")

    def test_issue_history_query_excludes_invalid_fields_and_401_auto_refreshes(self) -> None:
        transport = FakeTransport()
        client = linear_sync.GraphQLClient("Bearer old", transport)
        client.issue("TIA-19")
        query_text = transport.calls[-1][0]
        self.assertNotIn("fieldName", query_text)
        self.assertNotIn("fromValue", query_text)
        self.assertNotIn("toValue", query_text)
        self.assertIn("fromState", query_text)
        self.assertIn("toState", query_text)

        credentials = self.work / "config" / "linear" / "credentials.json"
        with mock.patch.dict(os.environ, {"GTT_LINEAR_CREDENTIALS_FILE": str(credentials)}, clear=False):
            linear_sync.save_credentials(
                "Bearer expired",
                actor="app",
                refresh_token="rt-old",
                client_id="cid",
                client_secret="csec",
            )

            class FakeResponse:
                def __init__(self, body: dict[str, object]) -> None:
                    self._body = json.dumps(body).encode("utf-8")

                def read(self) -> bytes:
                    return self._body

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            call_count = {"n": 0}

            def fake_urlopen(req, timeout=15):
                call_count["n"] += 1
                if req.full_url == linear_sync.OAUTH_TOKEN_URL:
                    return FakeResponse({"access_token": "fresh-tok", "refresh_token": "rt-new", "expires_in": 86400})
                if call_count["n"] == 1:
                    import io
                    err = linear_sync.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
                    err.close()
                    raise err
                return FakeResponse({"data": {"viewer": {"id": "bot"}}})

            with mock.patch.object(linear_sync, "urlopen", side_effect=fake_urlopen):
                live_client = linear_sync.GraphQLClient("Bearer expired")
                data = live_client.execute("{ viewer { id } }")
                self.assertEqual(data["viewer"]["id"], "bot")
                self.assertEqual(live_client.api_key, "Bearer fresh-tok")
                self.assertEqual(linear_sync._credential_record()["refreshToken"], "rt-new")

    def test_parse_tasks_dynamic_headers_and_issue_re(self) -> None:
        ledger = (
            "- **Linear Issue**: TIA-85 (https://linear.app/tianpei/issue/TIA-85)\n\n"
            "| Task | 写者 | 状态 | PR/head | 验收证据／回执 |\n"
            "|---|---|---|---|---|\n"
            "| TK9 | writer | in_review | PR #9 | receipts/tk9.md |\n"
        )
        tasks = linear_sync._parse_tasks(ledger, None)
        self.assertEqual(tasks[0]["task_id"], "TK9")
        self.assertEqual(tasks[0]["status"], "in_review")
        self.assertEqual(linear_sync._bound_issue(ledger), "TIA-85")

    def test_telemetry_export_collects_contract_and_observe_records(self) -> None:
        receipts = self.work / "receipts"
        receipts.mkdir()
        (receipts / "contract-telemetry.json").write_text("{}", encoding="utf-8")
        (receipts / "linear-observe.jsonl").write_text("{}\n", encoding="utf-8")
        records = list(telemetry_export._goal_files(self.work.parent))
        record_types = {record_type for path, record_type, goal in records if goal == self.work.name}
        self.assertIn("contract_telemetry", record_types)
        self.assertIn("linear_observe", record_types)


if __name__ == "__main__":
    unittest.main()
