import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "skills/linear-board/scripts/linear-bot-bridge.py"
spec = importlib.util.spec_from_file_location("bridge", PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeClient:
    def __init__(self, issue):
        self.issue = issue
        self.comments = []
        self.updates = []
    def list_issues(self, *_args, **_kwargs): return [self.issue]
    def create_comment(self, issue_id, body, parent_id=None):
        self.comments.append((issue_id, body, parent_id)); return f"c{len(self.comments)}"
    def team_metadata(self, _team): return {"states": {"nodes": [{"id": "progress", "name": "In Progress"}]}}
    def update_issue(self, issue_id, **kwargs): self.updates.append((issue_id, kwargs)); return self.issue


class BridgeTests(unittest.TestCase):
    def test_process_assignment_and_reply_root_coalesces(self):
        root = {"id": "bot-root", "body": "🤖 已接单", "user": {"id": bridge.BOT_ACTOR_ID}, "parent": {}}
        issue = {"id": "i1", "identifier": "TIA-1", "title": "Personal", "description": "do this", "updatedAt": "x", "project": {"name": "Personal project"}, "state": {"type": "started"}, "assignee": {"id": bridge.BOT_ACTOR_ID}, "delegate": {}, "comments": {"nodes": [root, {"id": "owner", "body": "@bot inspect it", "user": {"id": bridge.OWNER_USER_ID}, "parent": {"id": "bot-root"}, "createdAt": "2"}]}}
        client = FakeClient(issue)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bridge, "dispatch", return_value=(True, "actual M2 summary", {"argv": ["mock"]})):
            state = bridge.State(Path(tmp) / "state.json")
            report = bridge.process_once(client, state)
            self.assertTrue(report["triggers"][0]["ok"])
            self.assertEqual(len(client.comments), 2)
            self.assertEqual(client.comments[0][2], "bot-root")
            self.assertIn("actual M2 summary", client.comments[1][1])
            self.assertEqual(client.updates[-1][1]["assignee_id"], bridge.OWNER_USER_ID)
            self.assertTrue(client.updates[-1][1]["clear_delegate"])

    def test_assignment_removal_readd_and_repeat_are_watermarked(self):
        issue = {"id": "i2", "identifier": "TIA-2", "title": "Personal", "description": "do", "project": {"name": "Personal"}, "state": {"type": "started"}, "assignee": {"id": bridge.BOT_ACTOR_ID}, "delegate": {}, "comments": {"nodes": []}}
        client = FakeClient(issue)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bridge, "dispatch", return_value=(True, "summary", {})):
            state = bridge.State(Path(tmp) / "state.json"); bridge.process_once(client, state)
            issue["assignee"] = {}; bridge.process_once(client, state)
            issue["assignee"] = {"id": bridge.BOT_ACTOR_ID}; report = bridge.process_once(client, state)
            self.assertEqual(len([x for x in report["triggers"] if x.get("ok")]), 1)

    def test_dispatch_uses_real_herdr_list_and_fresh_completion(self):
        result = lambda out, code=0: mock.Mock(stdout=out, stderr="", returncode=code)
        with mock.patch.object(bridge, "_run", side_effect=[result(json.dumps({"result": {"agents": [{"name": "m2-learning-and-blog-x", "agent_status": "idle"}]}})), result(json.dumps({"result": {"agent": {"completion_seq": 4}}})), result("", 0), result(json.dumps({"result": {"agent": {"completion_seq": 5, "agent_status": "idle"}}})), result("# Fresh card", 0)]) as run:
            ok, output, detail = bridge.dispatch({"title": "[Daily Learn]", "description": ""}, "topic", False, 0, ["topic"])
            self.assertTrue(ok); self.assertEqual(output, "# Fresh card")
            self.assertIn("--wait", run.call_args_list[2].args[0]); self.assertNotIn("--json", run.call_args_list[0].args[0])

    def test_scope_and_bot_filters(self):
        self.assertTrue(bridge.excluded_scope({"project": {"name": "Work Business"}}))
        self.assertFalse(bridge.excluded_scope({"project": {"name": "Personal project"}}))
        self.assertTrue(bridge.is_bot_comment({"user": {"id": bridge.BOT_ACTOR_ID}, "body": "x"}))
        self.assertTrue(bridge.is_bot_comment({"body": "<!-- BOT-BRIDGE:ack -->"}))

    def test_topic_and_context(self):
        issue = {"title": "[Daily Learn]", "description": "- [x] first\n- [ ] second", "comments": {"nodes": [{"id": "c1", "body": "old"}]}}
        self.assertEqual(bridge.topic_selection(issue, {"body": "@bot 2"}), ["2"])
        self.assertEqual(bridge.topic_selection(issue, {"body": "@bot 都做"}), ["all"])
        context = bridge.thread_context(issue, {"body": "@bot continue"})
        self.assertIn("Issue: [Daily Learn]", context)
        self.assertIn("old", context)
        self.assertIn("@bot continue", context)

    def test_signature_and_durable_dry_run(self):
        body = b'{"action":"update"}'
        import hmac, hashlib
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(bridge.verify_signature("secret", body, "sha256=" + sig))
        self.assertFalse(bridge.verify_signature("secret", body, "sha256=bad"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = bridge.State(path, dry_run=True)
            state.data["processed_comment_ids"].append("c1")
            state.save()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
