import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "skills" / "goal-task" / "scripts" / "herdr-dispatch.sh"
PROFILES = ROOT / "skills" / "goal-task" / "references" / "runtime-profiles.yaml"


class GatewayDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.herdr_source = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane": {"pane_id": "p99"}}}))
elif args[:2] == ["agent", "start"]:
    with open(os.environ["TEST_CAPTURE"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
    print(json.dumps({"ok": True}))
elif args[:2] == ["agent", "get"]:
    print(json.dumps({"interactive_ready": True, "agent_status": "working"}))
elif args[:2] == ["agent", "prompt"]:
    print(json.dumps({"ok": True}))
"""

    def run_dispatch(
        self, role, kind="copilot", extra=(), agent_args=(), name="goal-tk1-writer-r2",
        online="0", mock_log=True,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            herdr = tmp / "herdr"
            herdr.write_text(self.herdr_source, encoding="utf-8")
            herdr.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{tmp}:{os.environ['PATH']}",
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "p1",
                "HERDR_MOCK_MAGPIE_ONLINE": online,
                "HERDR_PROMPT_IDLE_GRACE": "0",
                **({"MOCK_HERDR_LOG": str(tmp / "calls")} if mock_log else {}),
                "TEST_CAPTURE": str(tmp / "calls"),
            }
            result = subprocess.run(
                [
                    str(DISPATCH), "--name", name, "--kind", kind, "--cwd", str(tmp),
                    "--prompt", "test", "--role", role, *extra, *(("--", *agent_args) if agent_args else ()),
                ],
                env=env, capture_output=True, text=True,
            )
            result.mock_log = (tmp / "calls").read_text(encoding="utf-8") if (tmp / "calls").exists() else ""
            return result

    def test_gateway_groups_and_cross_vendor_mapping(self):
        text = PROFILES.read_text(encoding="utf-8")
        for group in ("group/steering", "group/reviewing", "group/coding", "group/coding-escalated"):
            self.assertIn(group, text)
        self.assertIn("alt_by_excluded_vendor: {google: \"group/reviewing-openai\"", text)

    def test_writer_online_and_offline(self):
        online = self.run_dispatch("writer", online="1")
        self.assertEqual(online.returncode, 0, online.stderr)
        self.assertEqual(json.loads(online.stdout)["resolved_model"], "magpie/group/coding")
        offline = self.run_dispatch("writer", online="0")
        self.assertEqual(offline.returncode, 0, offline.stderr)
        self.assertEqual(json.loads(offline.stdout)["resolved_model"], "gpt-5.6-luna")

    def test_pr_monitor_fallback(self):
        result = self.run_dispatch("pr_monitor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["resolved_model"], "mai-code-2-flash")

    def test_reviewer_exclusion(self):
        google = self.run_dispatch("code_reviewer", extra=("--exclude-vendor", "openai"))
        self.assertEqual(json.loads(google.stdout)["logical_group"], "group/reviewing")
        self.assertEqual(json.loads(google.stdout)["resolved_model"], "gemini-4-argon")
        openai = self.run_dispatch("code_reviewer", extra=("--exclude-vendor", "google"))
        self.assertEqual(json.loads(openai.stdout)["logical_group"], "group/reviewing-openai")
        self.assertEqual(json.loads(openai.stdout)["resolved_model"], "gpt-5.6-luna")

    def test_round_one_escalation_is_rejected(self):
        result = self.run_dispatch("writer_escalated", name="goal-tk1-writer-r1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("strictly reserved", result.stderr)
        result = self.run_dispatch("writer_escalated")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_caller_allow_tool_is_preserved(self):
        result = self.run_dispatch("writer", agent_args=("--allow-tool=url",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--allow-tool=url", result.mock_log)

    def test_add_dir_pairs_are_filtered_as_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp) / "valid"
            valid.mkdir()
            result = self.run_dispatch(
                "writer", agent_args=("--add-dir", str(valid), "--add-dir", str(valid / "missing")),
                mock_log=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(valid), result.mock_log)
            self.assertNotIn(str(valid / "missing"), result.mock_log)


if __name__ == "__main__":
    unittest.main()
