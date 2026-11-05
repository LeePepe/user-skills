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
SCRIPT = ROOT / "skills/goal-task/scripts/linear-sync.py"
QUEUE_SCRIPT = ROOT / "skills/goal-task/scripts/goal-queue.py"
TELEMETRY_SCRIPT = ROOT / "skills/goal-task/scripts/gtt-telemetry-export.py"


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
        ):
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
        self.assertEqual(linear_sync._infer_project_name(None, "workflow-hub goal-task"), "Agent Workflow")
        self.assertEqual(linear_sync._infer_project_name(None, "Sapphire DSAT"), "Bing")
        self.assertEqual(linear_sync._infer_project_name(None, "VitalStride health app"), "Personal project")
        self.assertEqual(linear_sync._infer_project_name(" Custom ", "Bing"), "Custom")

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
