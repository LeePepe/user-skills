import contextlib
import copy
import hashlib
import hmac
import http.client
import importlib.util
import io
import json
import multiprocessing
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "skills/linear-board/scripts/linear-bot-bridge.py"
spec = importlib.util.spec_from_file_location("bridge", PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
SYNC = bridge._sync_module()


def owner_comment(body="@bot inspect it", cid="owner", parent=None, created="2"):
    return {"id": cid, "body": body, "user": {"id": bridge.OWNER_USER_ID},
            "parent": {"id": parent} if parent else None, "createdAt": created}


def personal_issue(daily=False, assigned=False, comments=None, iid="i1"):
    return {"id": iid, "identifier": "TIA-1", "title": "[Daily Learn 2026-11-14]" if daily else "Personal investigation",
            "description": "- [ ] [first-topic](learning-topic://first-topic) First\n- [x] [second-topic](<learning-topic://second-topic>) Second" if daily else "Explore a local protocol",
            "updatedAt": "2026-11-14T01:00:00Z", "project": {"name": "Personal project"}, "team": {"key": "TIA"},
            "parent": None, "state": {"type": "started"}, "assignee": {"id": bridge.BOT_ACTOR_ID if assigned else bridge.OWNER_USER_ID},
            "delegate": None, "comments": {"nodes": [owner_comment()] if comments is None else comments}}


class FakeClient:
    def __init__(self, *issues):
        self.issues = {i["id"]: i for i in issues}
        self.comments, self.updates, self.lists, self.lookups = [], [], [], []
        self.fail_comment = None
        self.fail_owner = 0
        self.scopes = {}
        self.incremental_empty = False

    def list_issues(self, *_args, **kwargs):
        self.lists.append(kwargs)
        return [] if self.incremental_empty and kwargs.get("updated_after") else list(self.issues.values())

    def issue(self, iid):
        self.lookups.append(iid)
        return self.issues[iid]

    def issue_scope(self, iid):
        self.lookups.append(iid)
        return copy.deepcopy(self.scopes[iid])

    def create_comment(self, issue_id, body, parent_id=None):
        kind = "ack" if body.startswith(bridge.ACK) else "result"
        if self.fail_comment == kind:
            self.fail_comment = None
            raise OSError("temporary comment failure")
        cid = f"bridge-{len(self.comments)}"
        self.comments.append((issue_id, body, parent_id))
        self.issues[issue_id]["comments"]["nodes"].append({"id": cid, "body": body, "parent": {"id": parent_id},
                                                        "user": {"id": bridge.BOT_ACTOR_ID}, "createdAt": "9"})
        return cid

    def team_metadata(self, _team):
        return {"states": {"nodes": [{"id": "progress", "name": "In Progress"}]}}

    def update_issue(self, issue_id, **kwargs):
        self.updates.append((issue_id, kwargs))
        if kwargs.get("assignee_id") and self.fail_owner:
            self.fail_owner -= 1
            raise OSError("temporary Owner update failure")
        issue = self.issues[issue_id]
        if kwargs.get("assignee_id"): issue["assignee"] = {"id": kwargs["assignee_id"]}
        if kwargs.get("clear_delegate"): issue["delegate"] = None
        return issue


class DailyBriefSelectionTests(unittest.TestCase):
    def brief(self):
        issue = personal_issue(comments=[])
        issue["title"] = "[Daily Brief 2026-11-15] 昨日汇总与今日 Todo"
        issue["description"] = (
            "## Yesterday\n- unrelated-delivery\n"
            "- [ ] **[1]** TIA-10 VoxPocket: choose demo\n"
            "- [x] **[2]** TIA-11 user-skills: fix daily brief\n"
            "- [ ] **[3]** TIA-12 Financial: inspect ledger\n")
        return issue

    def test_numbered_comment_routes_only_selected_sources(self):
        issue = self.brief()
        comment = owner_comment("@bot 执行 1, 3")
        directive, slugs = bridge._directive(issue, comment, [])
        self.assertEqual(slugs, [])
        self.assertIn("TIA-10 VoxPocket", directive)
        self.assertIn("TIA-12 Financial", directive)
        self.assertNotIn("TIA-11", directive)
        context = bridge.thread_context(issue, comment)
        self.assertIn(directive, context)
        self.assertNotIn("unrelated-delivery", context)
        self.assertNotIn("TIA-11", context)
        issue["description"] = issue["description"].replace("[1]", r"\[1\]")
        self.assertIn("TIA-10", bridge.brief_selection(issue, comment))

    def test_assignment_uses_checked_boxes_or_latest_owner_selection(self):
        issue = self.brief()
        directive, _ = bridge._directive(issue, {"body": "assigned to bot"}, [])
        self.assertIn("TIA-11 user-skills", directive)
        self.assertNotIn("TIA-10", directive)
        directive, _ = bridge._directive(issue, {"body": "assigned to bot"}, [owner_comment("执行 3")])
        self.assertIn("TIA-12 Financial", directive)
        self.assertNotIn("TIA-11", directive)

    def test_plan_a_compact_lines_support_comments_and_checked_boxes(self):
        issue = self.brief()
        issue["description"] = (
            "<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-15 -->\n"
            "## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）\n\n"
            "- [ ] **[1] VoxPocket 三分类方案拍板** · [打开画廊](https://example.test/demos/v02/) · `TIA-420`\n"
            "- [x] **[2] AIDash 双卡 R2 观察** · [打开画廊](https://example.test/demos/r2/) · `TIA-432`\n"
            "- [ ] **[3] Financial 补录期初锚点** · `finance accounts --anchor` · `TIA-430`\n\n"
            "---\n## 📊 昨日完成 (11-14 · 1 项)\n"
            "| 项目 | 单据 / PR | 一句话结果 |\n|---|---|---|\n"
            "| VoxPocket | `TIA-410` | 昨日已交付 |\n")
        for body in ("@bot 1, 3", "@bot 执行 1, 3"):
            directive, slugs = bridge._directive(issue, owner_comment(body), [])
            self.assertEqual(slugs, [])
            self.assertIn("TIA-420", directive)
            self.assertIn("finance accounts --anchor", directive)
            self.assertNotIn("TIA-432", directive)
            self.assertNotIn("TIA-410", directive)
        checked = bridge.brief_selection(issue)
        self.assertIn("TIA-432", checked)
        self.assertNotIn("TIA-420", checked)

    def test_resolved_selection_survives_real_dispatch_payload(self):
        for assignment in (False, True):
            with self.subTest(assignment=assignment), tempfile.TemporaryDirectory() as tmp:
                issue = self.brief()
                issue["description"] = issue["description"].replace("fix daily brief", "fix daily brief at 5:00, limit 100")
                issue["comments"] = {"nodes": [] if assignment else [owner_comment("@bot 执行 2")]}
                if assignment:
                    issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
                client, state, runtime = FakeClient(issue), bridge.State(Path(tmp) / "state.json"), Runtime()
                pending = bridge._new_turn(issue, state)
                self.assertIsNotNone(pending)
                with mock.patch.object(bridge, "_run", side_effect=runtime.run):
                    bridge._advance_turn(client, state, issue, pending)
                plan_call = next(call for call in runtime.calls if "--plan" in call)
                payload = plan_call[plan_call.index("--message") + 1]
                self.assertIn("TIA-11 user-skills: fix daily brief at 5:00, limit 100", payload)
                self.assertNotIn("Owner decision required", payload)
                self.assertNotIn("TIA-10", payload)
                self.assertNotIn("TIA-12", payload)

    def test_invalid_selection_returns_owner_question_without_dispatch_or_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = self.brief()
            issue["state"] = {"name": "Todo", "type": "unstarted"}
            issue["comments"] = {"nodes": [owner_comment("@bot 执行 1, 9")]}
            client, state = FakeClient(issue), bridge.State(Path(tmp) / "state.json")
            pending = bridge._new_turn(issue, state)
            with mock.patch.object(bridge, "_run") as run:
                result = bridge._advance_turn(client, state, issue, pending)
            run.assert_not_called()
            self.assertEqual(result["result_kind"], "owner-decision")
            self.assertEqual(result["phase"], "complete")
            self.assertEqual(len(client.comments), 1)
            self.assertIn("Owner decision required", client.comments[0][1])
            self.assertFalse(any("state_id" in kwargs for _, kwargs in client.updates))
            self.assertEqual(client.updates[-1][1], {"assignee_id": bridge.OWNER_USER_ID, "clear_delegate": True})

    def test_owner_direct_comments_trigger_without_mention_for_daily_brief_and_daily_learn(self):
        issue = self.brief()
        issue["comments"] = {"nodes": [owner_comment("1, 2", "brief-owner")]}
        with tempfile.TemporaryDirectory() as tmp:
            pending = bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json"))
        self.assertIsNotNone(pending)
        self.assertIn("TIA-10", pending["directive"])
        self.assertIn("TIA-11", pending["directive"])
        learning = personal_issue(daily=True, comments=[owner_comment("1, 3", "learn-owner")])
        with tempfile.TemporaryDirectory() as tmp:
            pending = bridge._new_turn(learning, bridge.State(Path(tmp) / "state.json"))
        self.assertEqual(pending["slugs"], ["first-topic"])

    def test_all_and_invalid_selection_never_fall_back_to_whole_issue(self):
        issue = self.brief()
        selected = bridge.brief_selection(issue, owner_comment("@bot 全部"))
        for identifier in ("TIA-10", "TIA-11", "TIA-12"):
            self.assertIn(identifier, selected)
        invalid = bridge.brief_selection(issue, owner_comment("@bot 执行 1, 9"))
        self.assertIn("Owner decision required", invalid)
        self.assertNotIn("TIA-10", invalid)
        issue["description"] = issue["description"].replace("[x]", "[ ]")
        self.assertIn("Owner decision required", bridge.brief_selection(issue))
        learning = personal_issue(daily=True)
        self.assertEqual(bridge._directive(learning, owner_comment("@bot 执行 2"), [])[1], ["second-topic"])


class OwnerIngressTests(unittest.TestCase):
    def test_owner_top_level_comment_triggers_for_real_api_shaped_bot_created_issue(self):
        issue = personal_issue(comments=[owner_comment("inspect", "owner-created", created="2")])
        transport = mock.Mock(return_value={"data": {"issue": {"creator": {"id": bridge.BOT_ACTOR_ID, "app": True}}}})
        client = SYNC.GraphQLClient("test", transport)
        with tempfile.TemporaryDirectory() as tmp:
            state = bridge.State(Path(tmp) / "state.json")
            pending = bridge._new_turn(issue, state, client)
            self.assertIsNotNone(bridge._new_turn(issue, state, client))
        self.assertIsNotNone(pending)
        transport.assert_called_once()
        query, variables = transport.call_args.args
        self.assertEqual(" ".join(query.split()),
                         "query BotIssueProvenance($id: String!) { issue(id: $id) { creator { id app } } }")
        self.assertEqual(variables, {"id": issue["id"]})
        self.assertIn("owner-created", pending["trigger_ids"])

    def test_bot_metadata_history_does_not_make_human_issue_bot_created(self):
        for embedded_creator in (False, True):
            with self.subTest(embedded_creator=embedded_creator), tempfile.TemporaryDirectory() as tmp:
                creator = {"id": bridge.OWNER_USER_ID, "app": False}
                issue = personal_issue(comments=[owner_comment("inspect")])
                issue["history"] = {"nodes": [{"actor": {"id": bridge.BOT_ACTOR_ID, "app": True},
                                               "botActor": {"id": bridge.BOT_ACTOR_ID},
                                               "toStateId": "progress", "addedLabelIds": ["synced"]}]}
                if embedded_creator: issue["creator"] = creator
                transport = mock.Mock(return_value={"data": {"issue": {"creator": creator}}})
                client = SYNC.GraphQLClient("test", transport)
                self.assertIsNone(bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json"), client))
                self.assertEqual(transport.call_count, 0 if embedded_creator else 1)

    def test_known_creator_identity_requires_bot_id_not_app_flag(self):
        for field in ("creator", "createdBy", "creatorId"):
            for actor_id in (bridge.BOT_ACTOR_ID, bridge.OWNER_USER_ID, "unrelated-app"):
                with self.subTest(field=field, actor_id=actor_id), tempfile.TemporaryDirectory() as tmp:
                    issue = personal_issue(comments=[owner_comment("inspect")])
                    issue[field] = actor_id if field == "creatorId" else {"id": actor_id, "app": True}
                    client = mock.Mock(spec=["execute"])
                    pending = bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json"), client)
                    self.assertEqual(pending is not None, actor_id == bridge.BOT_ACTOR_ID)
                    client.execute.assert_not_called()

    def test_scans_without_eligible_owner_comment_do_not_query_creator(self):
        bot = {"id": "bot-root", "body": "Prior response", "user": {"id": bridge.BOT_ACTOR_ID}, "createdAt": "1"}
        other = {"id": "other", "body": "inspect", "user": {"id": "someone"}, "createdAt": "2"}
        cases = [
            ("empty", [], [], False),
            ("processed", [owner_comment("inspect")], ["owner"], False),
            ("bot-only", [bot], [], False),
            ("non-owner", [other], [], False),
            ("processed-owner-new-non-owner", [owner_comment("inspect"), other], ["owner"], False),
            ("mention", [owner_comment("@bot inspect")], [], True),
            ("daily-learn", [owner_comment("都做")], [], True),
            ("daily-brief", [owner_comment("1, 2")], [], True),
            ("prior-bot", [bot, owner_comment("inspect")], [], True),
            ("bot-parent", [bot, owner_comment("inspect", parent="bot-root")], [], True),
            ("bot-root", [bot, owner_comment("earlier", "reply", "bot-root"),
                          owner_comment("inspect", parent="reply")], ["reply"], True),
            ("ordinary-reply", [owner_comment("inspect", parent="human-root")], [], False),
            ("delegated", [], [], True),
        ]
        for name, comments, processed, triggered in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                issue = personal_issue(comments=comments, daily=name == "daily-learn")
                if name == "daily-brief": issue["title"] = "[Daily Brief 2026-11-15]"
                if name == "delegated": issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
                client, state = FakeClient(issue), bridge.State(Path(tmp) / "state.json")
                client.execute = mock.Mock()
                state.data["processed_comment_ids"] = processed
                report = bridge.process_once(client, state, dry_run=True)
                self.assertTrue(report["ok"])
                self.assertEqual(bool(report["triggers"]), triggered)
                client.execute.assert_not_called()
                self.assertFalse(client.comments or client.updates)

    def test_creator_lookup_failure_or_unknown_identity_preserves_explicit_ingress(self):
        responses = [{"data": {"issue": None}}, {"data": {"issue": {"creator": None}}},
                     {"data": {"issue": {"creator": {"app": True}}}},
                     {"data": {"issue": {"creator": {"id": "unrelated-app", "app": True}}}},
                     {"errors": [{"message": "creator unavailable"}]}, OSError("offline")]
        for response in responses:
            for ingress in ("plain", "mention", "delegated"):
                with self.subTest(response=response, ingress=ingress), tempfile.TemporaryDirectory() as tmp:
                    issue = personal_issue(comments=[owner_comment("@bot inspect" if ingress == "mention" else "inspect")])
                    if ingress == "delegated": issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
                    transport = mock.Mock(side_effect=response) if isinstance(response, Exception) else mock.Mock(return_value=response)
                    client = SYNC.GraphQLClient("test", transport)
                    pending = bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json"), client)
                    self.assertEqual(pending is not None, ingress != "plain")
                    if ingress == "mention": transport.assert_not_called()
                    else: transport.assert_called_once()

    def test_paginated_bot_comments_enable_owner_ingress_without_creator_query(self):
        issue = personal_issue(comments=[owner_comment("inspect")])
        issue["comments"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next-comment"}
        def transport(query, variables):
            if "ListIssues" in query:
                return {"data": {"issues": {"nodes": [issue], "pageInfo": {"hasNextPage": False}}}}
            if "IssueComments" in query:
                return {"data": {"issue": {"comments": {"nodes": [
                    {"id": "old-bot", "body": "Prior response", "user": {"id": bridge.BOT_ACTOR_ID}, "createdAt": "1"}],
                    "pageInfo": {"hasNextPage": False}}}}}
            self.fail(f"Unexpected creator query: {query}")
        requests = mock.Mock(side_effect=transport)
        client = SYNC.GraphQLClient("test", requests)
        with tempfile.TemporaryDirectory() as tmp:
            report = bridge.process_once(client, bridge.State(Path(tmp) / "state.json"), dry_run=True)
        self.assertTrue(report["ok"])
        self.assertEqual([turn["directive"] for turn in report["triggers"]], ["inspect"])
        self.assertEqual(requests.call_count, 2)
        self.assertEqual(requests.call_args.args[1], {"id": issue["id"], "after": "next-comment"})

    def test_owner_top_level_comment_triggers_after_bot_participation_without_mention(self):
        bot = {"id": "bot-root", "body": bridge.ACK, "user": {"id": bridge.BOT_ACTOR_ID}, "createdAt": "1"}
        issue = personal_issue(comments=[bot, owner_comment("执行 1", "owner-followup", created="2")])
        with tempfile.TemporaryDirectory() as tmp:
            pending = bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json"))
        self.assertIsNotNone(pending)
        self.assertEqual(pending["directive"], "执行 1")
        self.assertIn("owner-followup", pending["trigger_ids"])

    def test_non_owner_top_level_comment_does_not_get_no_mention_bypass(self):
        issue = personal_issue(daily=True, comments=[{"id": "other", "body": "1", "user": {"id": "someone"}, "createdAt": "2"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bridge._new_turn(issue, bridge.State(Path(tmp) / "state.json")))


class OAuthAgentScopeTests(unittest.TestCase):
    def test_agent_capabilities_require_explicit_login_and_admin_approval(self):
        for enabled in (False, True):
            with self.subTest(agent_scopes=enabled), mock.patch.object(SYNC, "_credential_record", return_value={}), \
                    mock.patch("http.server.HTTPServer") as server, mock.patch("subprocess.run") as opened, \
                    mock.patch.object(SYNC, "save_credentials") as saved, mock.patch.object(SYNC, "urlopen") as exchanged:
                server.return_value.handle_request.side_effect = RuntimeError("approval gate")
                with self.assertRaisesRegex(RuntimeError, "approval gate"):
                    SYNC._oauth_interactive_login("client", "secret", agent_scopes=enabled)
                query = parse_qs(urlsplit(opened.call_args.args[0][1]).query)
                self.assertEqual(query["actor"], ["app"])
                scopes = set(query["scope"][0].split(","))
                self.assertEqual("app:assignable" in scopes, enabled)
                self.assertEqual("app:mentionable" in scopes, enabled)
                self.assertTrue({"read", "write", "issues:create", "comments:create"} <= scopes)
                saved.assert_not_called()
                exchanged.assert_not_called()
        for flags, options in (([], {}), (["--agent-scopes"], {"agent_scopes": True})):
            with self.subTest(flags=flags), mock.patch.object(SYNC, "_oauth_interactive_login", return_value={"ok": True}) as login, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SYNC.main(["auth", "oauth-login", *flags]), 0)
                login.assert_called_once_with("", "", 8765, **options)


class Runtime:
    """Only the subprocess boundary is faked; envelopes follow the real CLIs."""
    def __init__(self, counter=None):
        self.calls, self.prompts = [], []
        self.dispatches = 0
        self.counter = counter
        self.finished, self.busy, self.blocked = True, False, False
        self.token = ""
        self.sequence = 3710
        self.text = "# Actual M2 result\n\nA verified explanation, not a plan/apply receipt."
        self.watch_ok, self.settled, self.attention = True, True, False
        self.apply_crash, self.no_receipt = False, False
        self.on_send = None
        self.stale_output = False
        self.echo_prompt = False

    def result(self, value, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(value) if not isinstance(value, str) else value, "")

    def output(self):
        if self.echo_prompt and self.prompts: return self.prompts[-1]
        if self.stale_output or not self.token or not self.finished: return "# Previous turn output"
        begin, end = bridge._output_markers(self.token)
        return f"{begin}\n{self.text}\n{end}"

    def sent(self, prompt):
        self.dispatches += 1
        if self.counter is not None:
            with self.counter.get_lock(): self.counter.value += 1
        self.prompts.append(prompt)
        self.token = re.search(r"Bridge turn token: ([a-f0-9]+)", prompt).group(1)
        self.sequence += 2
        if self.on_send: self.on_send()

    def run(self, argv, _timeout):
        self.calls.append(argv)
        if argv[:3] == ["herdr", "agent", "list"]:
            assert "--json" not in argv
            return self.result({"id": "cli:agent:list", "result": {"agents": [{"name": "m2-learning-and-blog-d35b62-r1", "agent_status": "idle"}]}})
        if argv[:3] == ["herdr", "agent", "get"]:
            status = "working" if self.busy or (self.token and not self.finished) else "blocked" if self.token and self.blocked else "idle"
            return self.result({"id": "cli:agent:get", "result": {"type": "agent_info", "agent": {
                "name": argv[3], "agent_status": status, "state_change_seq": self.sequence, "revision": 2}}})
        if argv[:3] == ["herdr", "agent", "read"]: return self.result(self.output())
        if argv[:3] == ["herdr", "agent", "prompt"]:
            self.sent(argv[4])
            return self.result({"id": "cli:agent:prompt", "error": {"code": "timeout"}}, 1) if not self.finished else self.result({"result": {"status": "idle"}})
        if "--plan" in argv:
            message = argv[argv.index("--message") + 1]
            return self.result({"schema_version": 1, "plan_id": "m2p-fixture", "message": message, "targets": [{
                "topic_id": "personal", "target_repo": "Personal", "m2_status": "EXISTING_M2", "transport": "herdr",
                "herdr_target": "m2-personal", "session_id": "m2-personal", "sub_prompt": message, "wave_order": 1}]})
        if "--apply" in argv:
            plan = json.loads(Path(argv[argv.index("--plan-file") + 1]).read_text())
            self.sent(plan["targets"][0]["sub_prompt"])
            receipt = {"ok": True, "approved": True, "plan_id": plan["plan_id"], "receipts": [{
                "ok": True, "method": "herdr.agent.prompt", "target": "m2-personal", "topic_id": "personal", "run_seq": 1, "slot_id": "A"}]}
            if not self.no_receipt: Path(argv[argv.index("--apply-receipt-file") + 1]).write_text(json.dumps(receipt))
            if self.apply_crash: raise Crash("interrupted after apply")
            return self.result({"ok": False}, 2) if self.no_receipt else self.result(receipt)
        if "--watch" in argv:
            status = "blocked" if self.blocked else "idle" if self.finished else "working"
            return self.result({"ok": self.watch_ok, "all_settled": self.settled and self.finished,
                                "needs_owner_attention": self.attention or self.blocked,
                                "summary_markdown": "### M2 Watch Summary\n- All Settled: true",
                                "completed_rollup_display": "A", "targets": [{"target": "m2-personal", "status": status,
                                    "visible_summary": self.output(), "needs_owner_attention": self.blocked,
                                    "state_change_seq": self.sequence}]})
        raise AssertionError(f"unexpected process boundary: {argv}")


class Crash(BaseException):
    pass


def concurrent_scan(path, counter, ready):
    client = FakeClient(personal_issue())
    runtime = Runtime(counter)
    state = bridge.State(Path(path))
    ready.wait(5)
    with mock.patch.object(bridge, "_run", side_effect=runtime.run):
        bridge.process_once(client, state)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.state = bridge.State(self.path)
        self.runtime = Runtime()
        self.run = mock.patch.object(bridge, "_run", side_effect=self.runtime.run).start()
        self.addCleanup(mock.patch.stopall)
        self.vault = Path(self.tmp.name) / "vault"
        mock.patch.object(bridge, "VAULT", self.vault).start()

    def restart(self):
        state = bridge.State(self.path)
        for item in state.data["retry_issues"].values(): item["retry_at"] = 0
        state.save()
        self.state = state

    def scan(self, client):
        return bridge.process_once(client, self.state)

    def assert_handoff(self, client, text=None):
        self.assertEqual(self.runtime.dispatches, 1)
        self.assertEqual(len(client.comments), 2)
        result_body = client.comments[-1][1]
        if bridge.is_daily(client.issues[client.comments[-1][0]]):
            self.assertIn("Daily Learn blog receipt:", result_body)
            self.assertNotIn(self.runtime.text, result_body)
        else:
            self.assertIn(text or self.runtime.text, result_body)
        self.assertIn("@ltp19941128", result_body)
        self.assertEqual(client.updates[-1][1], {"assignee_id": bridge.OWNER_USER_ID, "clear_delegate": True})
        self.assertFalse(self.state.data["pending"])

    def test_router_real_watch_schema_full_context_and_verbatim_directive(self):
        root = {"id": "bot-root", "body": bridge.ACK, "user": {"id": bridge.BOT_ACTOR_ID}, "createdAt": "0"}
        earlier = owner_comment("prior question", "earlier", "bot-root", "1")
        trigger = owner_comment("continue with $(unsafe); inspect protocol 2", parent="earlier")
        issue = personal_issue(assigned=True, comments=[root, earlier, trigger])
        client = FakeClient(issue)
        report = self.scan(client)
        self.assertTrue(report["triggers"][0]["ok"])
        self.assert_handoff(client)
        prompt = self.runtime.prompts[0]
        for value in (issue["title"], issue["description"], "prior question", trigger["body"]): self.assertIn(value, prompt)
        self.assertIn("Current directive:\n" + trigger["body"], prompt)
        self.assertEqual([x[2] for x in client.comments], ["bot-root", "bot-root"])
        self.assertEqual(sum("--apply" in c for c in self.runtime.calls), 1)
        self.assertEqual(sum("--watch" in c for c in self.runtime.calls), 1)
        self.assertFalse(list(self.path.parent.glob("*.jobs/*/*")))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.restart(); self.scan(client)
        self.assertEqual(self.runtime.dispatches, 1)
        self.assertEqual(len(client.comments), 2)

    def test_topic_formats_checked_numbers_and_all_resolve_real_slugs(self):
        digest_spec = importlib.util.spec_from_file_location("bridge_digest_fixture", ROOT / "skills/knowledge-digest/scripts/digest_vault.py")
        digest = importlib.util.module_from_spec(digest_spec); digest_spec.loader.exec_module(digest)
        topics = [{"topic_key": slug, "title": slug, "learning_brief": {"why_now": "now", "preview_questions": ["one", "two", "three"], "recommended_command": f"/learn {slug}"}}
                  for slug in ("first-topic", "second-topic", "third-topic")]
        generated = digest._daily_digest_markdown("2026-11-14", topics)
        legacy = "\n".join(f"{bullet} [{'x' if i == 2 else ' '}] **\\[{i}\\] Title** (`{topic['topic_key']}`)" for i, (bullet, topic) in enumerate(zip("-*+", topics), 1))
        for text in (generated, generated.replace("(learning-topic://", "(<learning-topic://").replace(") ", ">) "), legacy):
            with self.subTest(format=text[:70]):
                issue = personal_issue(daily=True); issue["description"] = text
                self.assertEqual(bridge.topic_selection(issue, {"body": "@bot 2"}), ["second-topic"])
                for selector in ("@bot都做", "全部做吧", "@bot ALL"):
                    self.assertEqual(bridge.topic_selection(issue, {"body": selector}), [t["topic_key"] for t in topics])
                self.assertEqual(bridge.topic_selection(issue, {"body": "@bot 99"}), [])
        self.assertEqual(bridge.topic_selection(personal_issue(daily=True)), ["second-topic"])
        self.assertEqual(bridge.topic_selection({"description": "- [ ] not a topic"}, {"body": "@bot 1"}), [])

    def test_assignment_latest_owner_selection_consumed_and_fresh_selected_card_only(self):
        issue = personal_issue(daily=True, assigned=True, comments=[owner_comment("1", "old", created="1"), owner_comment("2", "selection", created="3"),
                            {"id": "other", "body": "1", "user": {"id": "someone"}, "createdAt": "4"}])
        issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
        for name in ("concepts/second-topic.md", "concepts/first-topic.md", "raw/second-topic.md", "concepts/second-topic-unrelated.md", "thoughts/index.md"):
            path = self.vault / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("stale " + name)
            os.utime(path, (1, 1))
        def write_cards():
            for name in ("concepts/second-topic.md", "concepts/first-topic.md", "raw/second-topic.md", "concepts/second-topic-unrelated.md", "thoughts/index.md"):
                (self.vault / name).write_text("# Fresh selected card" if name == "concepts/second-topic.md" else "DO NOT POST " + name)
        self.runtime.on_send = write_cards
        client = FakeClient(issue); self.scan(client)
        self.assert_handoff(client)
        self.assertNotIn("DO NOT POST", client.comments[-1][1])
        self.assertTrue(self.runtime.prompts[0].startswith("/learn second-topic\n"))
        self.assertIn("selection", self.state.data["processed_comment_ids"])
        self.assertEqual(issue["assignee"]["id"], bridge.OWNER_USER_ID)
        self.assertIsNone(issue["delegate"])
        self.restart(); self.scan(client)
        self.assertEqual(self.runtime.dispatches, 1)

    def test_daily_learn_requests_blog_publication_and_posts_compact_verified_receipt(self):
        issue = personal_issue(daily=True, comments=[owner_comment("2", "learn-one")])
        self.runtime.text = ("- [Second Topic](https://leepepe.github.io/learn/second-topic/) — Inline summary.\n"
                             "# Full Card Body Sentinel\nprivate body")
        def write_card():
            card = self.vault / "concepts" / "second-topic.md"
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_text("---\ntitle: Second Topic\ninsight: One-line card summary\n---\n# Full Card Body Sentinel")
        self.runtime.on_send = write_card
        client = FakeClient(issue)
        self.scan(client)
        body = client.comments[-1][1]
        self.assertIn("[Second Topic](https://leepepe.github.io/learn/second-topic/)", body)
        self.assertIn("Inline summary", body)
        self.assertNotIn("Full Card Body Sentinel", body)
        self.assertNotIn("private body", body)
        self.assertIn("publish", self.runtime.prompts[0].casefold())
        self.assertIn("leepepe.github.io", self.runtime.prompts[0])

    def test_legacy_daily_result_comment_retry_compacts_saved_full_card_output(self):
        issue = personal_issue(daily=True, comments=[owner_comment("2", "legacy-owner")])
        client = FakeClient(issue)
        pending = bridge._new_turn(issue, self.state)
        pending.update(phase="result-comment", result_kind="completed",
                       output="# Full Card Body Sentinel\nhttps://leepepe.github.io/learn/second-topic/\nprivate body",
                       root_id=None)
        result = bridge._advance_turn(client, self.state, issue, pending)
        body = client.comments[-1][1]
        self.assertTrue(result["ok"])
        self.assertIn("Blog publication pending Owner readback", body)
        self.assertNotIn("Full Card Body Sentinel", body)
        self.assertNotIn("private body", body)

    def test_inline_receipt_associates_multiple_articles_without_body_fallback(self):
        output = ("- [First](https://leepepe.github.io/learn/first/) — First summary.\n"
                  "- [Second](https://leepepe.github.io/learn/second/) — Second summary.\n"
                  "private body")
        receipt = bridge._compact_daily_receipt(output, [], ["first", "second"])
        self.assertIn("[First](https://leepepe.github.io/learn/first/) — First summary.", receipt)
        self.assertIn("[Second](https://leepepe.github.io/learn/second/) — Second summary.", receipt)
        self.assertNotIn("private body", receipt)

    def test_idle_owner_decision_response_preserves_decision_result_kind(self):
        issue = personal_issue(daily=True, comments=[owner_comment("2", "decision-owner")])
        self.runtime.text = "Owner decision required: publication is unsafe; confirm a public-safe rewrite."
        client = FakeClient(issue)
        report = self.scan(client)
        self.assertEqual(report["triggers"][0]["result_kind"], "owner-decision")
        self.assertIn("Owner decision required", client.comments[-1][1])
        self.assertIn("publication is unsafe", client.comments[-1][1])

    def test_busy_learning_target_defers_and_previous_completion_never_counts(self):
        client = FakeClient(personal_issue(daily=True, assigned=True, comments=[]))
        self.runtime.busy = True
        self.scan(client)
        self.assertEqual(self.runtime.dispatches, 0)
        self.assertEqual(self.state.data["pending"]["i1"]["phase"], "dispatch")
        self.runtime.sequence += 2  # Previous task completes; not our result.
        self.runtime.busy = False
        self.runtime.finished = False
        self.restart(); self.scan(client)
        self.assertEqual(self.runtime.dispatches, 1)
        self.assertEqual(len(client.comments), 1)
        pending = self.state.data["pending"]["i1"]
        self.assertEqual(pending["handle"]["baseline"]["agent"]["state_change_seq"], 3712)
        self.runtime.finished = True; self.runtime.stale_output = True
        self.restart(); self.scan(client)
        self.assertEqual(len(client.comments), 1)
        self.runtime.stale_output = False
        self.restart(); self.scan(client)
        self.assert_handoff(client)

    def test_known_rejected_prompt_and_missing_binary_retry_without_permanent_pending(self):
        for failure in ("agent_blocked", "missing-binary"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                self.path = Path(tmp) / "state.json"; self.state = bridge.State(self.path)
                self.runtime = Runtime()
                first = True
                def run(argv, timeout):
                    nonlocal first
                    if argv[:3] == ["herdr", "agent", "prompt"] and first:
                        first = False
                        if failure == "missing-binary": raise FileNotFoundError("herdr")
                        return self.runtime.result({"error": {"code": "agent_blocked"}}, 1)
                    return self.runtime.run(argv, timeout)
                self.run.side_effect = run
                client = FakeClient(personal_issue(daily=True))
                self.scan(client)
                self.assertEqual(self.state.data["pending"]["i1"]["phase"], "dispatch")
                self.assertEqual(self.runtime.dispatches, 0)
                self.restart(); self.scan(client)
                self.assert_handoff(client)

    def test_router_timeout_restart_only_watches_saved_handle_and_queues_followup(self):
        issue = personal_issue(); client = FakeClient(issue)
        self.runtime.finished = False
        self.scan(client)
        saved = copy.deepcopy(self.state.data["pending"]["i1"])
        issue["comments"]["nodes"].append(owner_comment("@bot later instruction", "later", created="8"))
        self.runtime.finished = True
        client.incremental_empty = True
        self.restart(); self.scan(client)
        self.assert_handoff(client)
        self.assertNotIn("later instruction", self.runtime.prompts[0])
        self.assertEqual(saved["directive"], "@bot inspect it")
        self.assertEqual(sum("--apply" in c for c in self.runtime.calls), 1)
        self.assertIn("i1", client.lookups)
        self.assertNotIn("later", self.state.data["processed_comment_ids"])
        self.restart(); self.scan(client)  # Follow-up is fetched even behind the incremental cursor.
        self.assertEqual(self.runtime.dispatches, 2)
        self.assertIn("later instruction", self.runtime.prompts[-1])

    def test_router_rejects_stale_unsettled_failed_watch_and_apply_is_not_completion(self):
        client = FakeClient(personal_issue())
        self.runtime.stale_output = True
        self.scan(client)
        self.assertEqual(len(client.comments), 1)
        self.runtime.stale_output = False; self.runtime.echo_prompt = True
        self.restart(); self.scan(client)
        self.assertEqual(len(client.comments), 1)
        self.runtime.echo_prompt = False
        for prop, value in (("stale_output", False), ("watch_ok", False), ("settled", False)):
            setattr(self.runtime, prop, value)
        self.restart(); self.scan(client)
        self.assertEqual(len(client.comments), 1)
        self.runtime.watch_ok = self.runtime.settled = True
        self.restart(); self.scan(client)
        self.assert_handoff(client)

    def test_blocked_actual_decision_is_owner_handoff_not_success_claim(self):
        client = FakeClient(personal_issue())
        self.runtime.blocked = True
        self.runtime.text = "Which personal repository should I use?"
        report = self.scan(client)
        self.assert_handoff(client)
        self.assertEqual(report["triggers"][0]["result_kind"], "owner-decision")
        self.assertIn("task is not completed", client.comments[-1][1])
        self.assertNotIn("completed without", client.comments[-1][1])

    def test_restarts_at_each_side_effect_boundary_reconcile_without_duplicates(self):
        cases = ("ack-posted", "wait-receipt", "output-saved", "result-posted", "owner-updated")
        for case in cases:
            with self.subTest(boundary=case), tempfile.TemporaryDirectory() as tmp:
                self.path = Path(tmp) / "state.json"; self.state = bridge.State(self.path)
                self.runtime = Runtime(); self.run.side_effect = self.runtime.run
                client = FakeClient(personal_issue())
                save = self.state.save
                crashed = False
                def crash_save():
                    nonlocal crashed
                    p = self.state.data["pending"].get("i1", {})
                    match = ((case == "ack-posted" and p.get("phase") == "ack" and p.get("ack_id"))
                             or (case == "wait-receipt" and p.get("phase") == "wait")
                             or (case == "output-saved" and p.get("phase") == "result-comment")
                             or (case == "result-posted" and p.get("phase") == "owner-reassign")
                             or (case == "owner-updated" and p.get("phase") == "complete"))
                    if match and not crashed:
                        crashed = True
                        if case == "output-saved": save()
                        raise Crash(case)
                    save()
                with mock.patch.object(self.state, "save", side_effect=crash_save):
                    with self.assertRaises(Crash): self.scan(client)
                self.restart(); self.scan(client)
                self.assert_handoff(client)
                self.assertEqual(self.state.data["processed_comment_ids"], ["owner"])

    def test_transient_comments_and_owner_reassign_retry_only_missing_side_effect(self):
        for failure in ("ack", "result", "owner"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                self.path = Path(tmp) / "state.json"; self.state = bridge.State(self.path)
                self.runtime = Runtime(); self.run.side_effect = self.runtime.run
                client = FakeClient(personal_issue())
                if failure == "owner": client.fail_owner = 1
                else: client.fail_comment = failure
                self.scan(client)
                before = len(self.runtime.calls)
                self.restart(); self.scan(client)
                self.assert_handoff(client)
                if failure == "owner":
                    self.assertEqual(len(self.runtime.calls), before)
                    self.assertEqual(len(client.updates), 3)  # Progress, failed Owner, Owner retry.

    def test_apply_crash_with_receipt_resumes_without_reapply(self):
        self.runtime.apply_crash = True
        client = FakeClient(personal_issue())
        with self.assertRaises(Crash): self.scan(client)
        self.assertEqual(self.state.data["pending"]["i1"]["phase"], "dispatching")
        self.runtime.apply_crash = False
        self.restart(); self.scan(client)
        self.assert_handoff(client)

    def test_ambiguous_dispatch_never_silently_duplicates_and_can_recover_late_receipt(self):
        client = FakeClient(personal_issue())
        self.runtime.no_receipt = True
        report = self.scan(client)
        self.assertEqual(report["triggers"][0]["phase"], "ambiguous")
        self.assertIn("no verified receipt", report["triggers"][0]["diagnostic"])
        self.restart(); self.scan(client)
        self.assertEqual(self.runtime.dispatches, 1)
        self.assertEqual(len(client.comments), 1)
        pending = self.state.data["pending"]["i1"]
        receipt = {"ok": True, "approved": True, "plan_id": "m2p-fixture", "receipts": [{"ok": True, "method": "herdr.agent.prompt", "target": "m2-personal"}]}
        bridge._router_files(self.state, pending)[1].write_text(json.dumps(receipt))
        self.restart(); self.scan(client)
        self.assert_handoff(client)

    def test_crash_before_prompt_is_explicit_pending_not_duplicate_or_fake_completion(self):
        client = FakeClient(personal_issue(daily=True))
        save = self.state.save
        def interrupt():
            save()
            if (self.state.data["pending"].get("i1") or {}).get("phase") == "dispatching": raise Crash()
        with mock.patch.object(self.state, "save", side_effect=interrupt):
            with self.assertRaises(Crash): self.scan(client)
        self.restart(); report = self.scan(client)
        self.assertEqual(report["triggers"][0]["phase"], "ambiguous")
        self.assertEqual(self.runtime.dispatches, 0)
        self.assertEqual(len(client.comments), 1)

    def test_scope_title_team_project_deep_ancestry_and_lookup_failure_before_dispatch(self):
        for field in ("title", "team", "project", "ancestry", "unavailable"):
            with self.subTest(field=field):
                issue = personal_issue(iid=field)
                client = FakeClient(issue)
                if field == "title": issue[field] = "[bInG] investigate"
                elif field in {"team", "project"}: issue[field] = {"name": "WORKPLACE"}
                else:
                    issue["parent"] = {"id": "parent"}
                    client.scopes = {"parent": {"id": "parent", "title": "normal", "project": None, "team": None, "parent": {"id": "grandparent"}},
                                     "grandparent": {"id": "grandparent", "title": "[Work Business] root", "project": None, "team": None, "parent": None}}
                    if field == "unavailable": client.scopes.pop("grandparent")
                report = self.scan(client)
                self.assertEqual(self.runtime.dispatches, 0)
                self.assertFalse(client.comments)
                if field == "unavailable": self.assertFalse(report["ok"])
        issue = personal_issue(iid="allowed")
        issue["title"] = "Climbing and tubing"
        issue["description"] = "Study a Bing search protocol as personal technology learning."
        self.scan(FakeClient(issue))
        self.assertEqual(self.runtime.dispatches, 1)

    def test_self_filters_terminal_states_and_delegation_cycles(self):
        bots = [{"user": {"id": bridge.BOT_ACTOR_ID}}, {"user": {"app": True}}, {"botActor": {"id": "app"}},
                {"body": "<!-- BOT-BRIDGE:result:old -->"}, {"body": bridge.ACK}]
        issues = []
        for i, bot in enumerate(bots):
            c = {"id": str(i), "body": "@bot self", **bot}
            issues.append(personal_issue(comments=[c], iid=str(i)))
        for status in ("completed", "canceled", "cancelled", "Done"):
            issue = personal_issue(assigned=True, iid=status); issue["state"] = {"type": status}; issues.append(issue)
        self.scan(FakeClient(*issues))
        self.assertEqual(self.runtime.dispatches, 0)
        issue = personal_issue(comments=[]); issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
        client = FakeClient(issue)
        self.scan(client); self.scan(client)  # Own handoff is not a new trigger.
        self.assertEqual(self.runtime.dispatches, 1)
        issue["delegate"] = {"id": bridge.BOT_ACTOR_ID}
        self.scan(client)
        self.assertEqual(self.runtime.dispatches, 2)

    def test_dry_run_cli_and_flag_create_no_state_locks_transport_or_mutations(self):
        client = FakeClient(personal_issue(assigned=True))
        before = copy.deepcopy(self.state.data)
        self.assertTrue(bridge.process_once(client, self.state, dry_run=True)["dry_run"])
        sync = mock.Mock()
        sync.GraphQLClient.return_value = client
        with mock.patch.object(bridge, "_sync_module", return_value=sync), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bridge.main(["--once", "--json", "--dry-run", "--state-file", str(self.path)]), 0)
        self.assertTrue(json.loads(output.getvalue())["dry_run"])
        sync.GraphQLClient.assert_called_once_with(sync.load_api_key.return_value, allow_refresh=False)
        self.assertEqual(self.state.data, before)
        self.assertFalse(list(self.path.parent.iterdir()))
        self.assertFalse(client.comments or client.updates)
        self.run.assert_not_called()

    def test_per_issue_failure_backoff_continues_other_issues_and_recovers(self):
        bad = personal_issue(iid="bad", comments=[owner_comment(cid="bad-owner")]); bad["parent"] = {"id": "not-found"}
        client = FakeClient(bad, personal_issue(iid="good"))
        self.assertFalse(self.scan(client)["ok"])
        self.assertEqual(self.runtime.dispatches, 1)
        failures = len(client.lookups)
        self.scan(client)
        self.assertEqual(len(client.lookups), failures)
        client.scopes["not-found"] = {"id": "not-found", "title": "personal", "project": None, "team": None, "parent": None}
        self.restart(); self.scan(client)
        self.assertEqual(self.runtime.dispatches, 2)
        self.assertNotIn("bad", self.state.data["retry_issues"])
        self.assertTrue(client.lists[1]["updated_after"])
        self.assertIsNone(client.lists[0]["limit"])

    def test_concurrent_same_and_separate_state_instances_reload_under_lock(self):
        for separate in (False, True):
            with self.subTest(separate=separate), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "state.json"
                state1 = bridge.State(path); state2 = bridge.State(path) if separate else state1
                client = FakeClient(personal_issue()); runtime = Runtime(); self.run.side_effect = runtime.run
                errors = []
                def scan(state):
                    try: bridge.process_once(client, state)
                    except Exception as exc: errors.append(exc)
                threads = [threading.Thread(target=scan, args=(s,)) for s in (state1, state2)]
                for t in threads: t.start()
                for t in threads: t.join(5); self.assertFalse(t.is_alive())
                self.assertFalse(errors)
                self.assertEqual(runtime.dispatches, 1)
                self.assertEqual(len(client.comments), 2)

    def test_cross_process_lock_reloads_stale_preexisting_state(self):
        ctx = multiprocessing.get_context("fork")
        counter, ready = ctx.Value("i", 0), ctx.Event()
        workers = [ctx.Process(target=concurrent_scan, args=(str(self.path), counter, ready)) for _ in range(2)]
        for worker in workers: worker.start()
        ready.set()
        for worker in workers:
            worker.join(8)
            if worker.is_alive(): worker.terminate(); worker.join(); self.fail("scan lock deadlocked")
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(counter.value, 1)

    def test_canonical_pagination_startup_older_issue_and_incremental_filter(self):
        requests = []
        def transport(query, variables):
            requests.append((query, variables))
            if "TeamMetadata" in query:
                return {"data": {"teams": {"nodes": [{"id": "tia", "states": {"nodes": [{"id": "progress", "name": "In Progress"}]}}]}}}
            if "CreateComment" in query:
                return {"data": {"commentCreate": {"success": True, "comment": {"id": "posted"}}}}
            if "UpdateIssue" in query:
                return {"data": {"issueUpdate": {"success": True, "issue": {"id": "old"}}}}
            if "IssueComments" in query:
                return {"data": {"issue": {"comments": {"nodes": [owner_comment()], "pageInfo": {"hasNextPage": False}}}}}
            if "ListIssues" in query:
                if variables["after"] is None:
                    issue = personal_issue(iid="recent", comments=[])
                    return {"data": {"issues": {"nodes": [issue], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}}
                old = personal_issue(iid="old", comments=[])
                old["comments"]["pageInfo"] = {"hasNextPage": True, "endCursor": "comments-next"}
                return {"data": {"issues": {"nodes": [old], "pageInfo": {"hasNextPage": False}}}}
            raise AssertionError(query)
        client = SYNC.GraphQLClient("test", transport)
        self.scan(client)
        self.assertEqual(self.runtime.dispatches, 1)
        self.assertEqual([v["after"] for q, v in requests if "ListIssues" in q], [None, "next"])
        self.assertEqual(sum("IssueComments" in q for q, v in requests), 1)
        requests.clear()
        client.list_issues("TIA", updated_after="2026-11-14T01:00:00Z", limit=1)
        self.assertEqual(requests[0][1]["filter"]["updatedAt"], {"gte": "2026-11-14T01:00:00Z"})

    def test_dry_run_client_never_refreshes_credentials_on_unauthorized(self):
        from urllib.error import HTTPError
        client = SYNC.GraphQLClient("test", allow_refresh=False)
        error = HTTPError("https://test", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        with error, mock.patch.object(SYNC, "urlopen", side_effect=error), mock.patch.object(SYNC, "refresh_oauth_credentials") as refresh:
            with self.assertRaises(SYNC.LinearSyncError): client.issue_scope("i1")
        refresh.assert_not_called()


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.client = FakeClient()
        with mock.patch.dict(os.environ, {"LINEAR_WEBHOOK_SECRET": "secret"}):
            self.server = bridge.webhook_server(0, self.client, bridge.State(Path(self.tmp.name) / "state.json", dry_run=True))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.port = self.server.server_address[1]

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def post(self, payload=None, *, body=None, headers=None):
        body = body if body is not None else json.dumps(payload if payload is not None else {"webhookTimestamp": time.time() * 1000}).encode()
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            conn.request("POST", "/", body, headers={"Linear-Signature": sig, **(headers or {})})
            response = conn.getresponse(); response.read(); return response.status
        finally: conn.close()

    def test_real_http_codes_signature_length_timestamp_and_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.post(headers={"Linear-Signature": "bad"}), 401)
        self.assertEqual(self.post(headers={"Content-Length": "garbage"}), 400)
        self.assertEqual(self.post(headers={"Content-Length": "1000001"}), 413)
        self.assertEqual(self.post({"webhookTimestamp": (time.time() - 301) * 1000}), 408)
        self.assertEqual(self.post({}), 400)
        self.assertEqual(self.post([]), 400)
        self.assertEqual(self.post({"webhookTimestamp": "NaN"}), 400)
        self.assertEqual(self.post(body=b"not-json"), 400)
        self.assertEqual(self.post(), 202)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_signed_replay_concurrency_and_bounded_scan_coalescing(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def list_issues(*args, **kwargs):
            calls.append(1); entered.set(); release.wait(2); return []
        self.client.list_issues = list_issues
        body = json.dumps({"webhookTimestamp": time.time() * 1000, "id": "same"}).encode()
        self.assertEqual(self.post(body=body), 202)
        self.assertTrue(entered.wait(2))
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(self.post(body=body, headers={"Linear-Delivery": str(time.time_ns())}))) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join(2)
        self.assertEqual(sorted(codes), [409] * 4)
        for i in range(5): self.assertEqual(self.post({"webhookTimestamp": time.time() * 1000, "id": i}), 202)
        self.assertEqual(len(calls), 1)
        release.set()
        deadline = time.monotonic() + 2
        while len(calls) < 2 and time.monotonic() < deadline: time.sleep(0.01)
        self.assertEqual(len(calls), 2)

    def test_slow_body_timeout_does_not_block_other_requests(self):
        with mock.patch.object(bridge, "WEBHOOK_TIMEOUT", 0.1):
            conn = socket.create_connection(("127.0.0.1", self.port), timeout=2)
            try:
                conn.sendall(b"POST / HTTP/1.0\r\nContent-Length: 10\r\n\r\nx")
                self.assertEqual(self.post(), 202)
                self.assertIn(b"408", conn.recv(4096).split(b"\r\n")[0])
            finally: conn.close()


if __name__ == "__main__":
    unittest.main()
