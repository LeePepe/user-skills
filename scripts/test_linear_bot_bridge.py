import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "skills/linear-board/scripts/linear-bot-bridge.py"
spec = importlib.util.spec_from_file_location("bridge", PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
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
