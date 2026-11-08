import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh"
PROFILES = ROOT / "skills" / "team-lead" / "references" / "runtime-profiles.yaml"


class GatewayDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.herdr_source = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get("TEST_CAPTURE_ALL"):
    with open(os.environ["TEST_CAPTURE_ALL"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
if args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane": {"pane_id": "p99"}}}))
elif args[:2] == ["workspace", "create"]:
    print(json.dumps({
        "result": {
            "workspace": {"workspace_id": "w77"},
            "root_pane": {"pane_id": "w77:p1"},
        }
    }))
elif args[:2] == ["agent", "start"]:
    with open(os.environ["TEST_CAPTURE"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
    print(json.dumps({"ok": True}))
elif args[:2] == ["agent", "get"]:
    existing_status = os.environ.get("MOCK_EXISTING_TL_STATUS", "")
    if existing_status:
        print(json.dumps({
            "interactive_ready": True,
            "agent_status": existing_status,
            "pane_id": "w55:p1",
            "workspace_id": "w55",
        }))
    elif os.environ.get("MOCK_NO_EXISTING_TL") == "1" and not os.path.exists(os.environ["TEST_CAPTURE"]):
        sys.exit(1)
    else:
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

    def test_team_lead_bootstrap_without_herdr_env_and_idempotent_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            herdr = tmp / "herdr"
            herdr.write_text(self.herdr_source, encoding="utf-8")
            herdr.chmod(0o755)
            handoff_goal = tmp / "handoff" / "demo-goal"
            handoff_goal.mkdir(parents=True)
            all_calls = tmp / "all_calls"

            # 1. Fresh team_lead spawn without HERDR_ENV or HERDR_PANE_ID (simulates Grok Bot / Cindy / plain CLI)
            env = {
                **os.environ,
                "PATH": f"{tmp}:{os.environ['PATH']}",
                "HERDR_ENV": "",
                "HERDR_PANE_ID": "",
                "HERDR_MOCK_MAGPIE_ONLINE": "1",
                "HERDR_PROMPT_IDLE_GRACE": "0",
                "MOCK_HERDR_LOG": str(tmp / "calls"),
                "TEST_CAPTURE": str(tmp / "calls"),
                "TEST_CAPTURE_ALL": str(all_calls),
                "MOCK_NO_EXISTING_TL": "1",
            }
            fresh = subprocess.run(
                [
                    str(DISPATCH), "--name", "demo-goal-tl-r1", "--kind", "claude",
                    "--cwd", str(handoff_goal), "--prompt", "Load /team-lead", "--role", "team_lead",
                ],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(fresh.returncode, 0, fresh.stderr)
            payload = json.loads(fresh.stdout)
            self.assertEqual(payload["workspace_id"], "w77")
            self.assertEqual(payload["pane_id"], "w77:p1")
            self.assertEqual(payload["resolved_kind"], "claude")
            self.assertEqual(payload["logical_group"], "group/steering")
            self.assertEqual(payload["resolved_model"], "opus")
            self.assertEqual(payload["gateway_mode"], "direct_fallback")
            recorded = [json.loads(line) for line in all_calls.read_text(encoding="utf-8").splitlines()]
            ws_creates = [c for c in recorded if c[:2] == ["workspace", "create"]]
            self.assertEqual(len(ws_creates), 1)
            self.assertIn("--label", ws_creates[0])
            self.assertIn("demo", ws_creates[0])

            # 2. Quota-exhausted / locked Claude skips directly to copilot + Magpie group/steering
            all_calls.write_text("", encoding="utf-8")
            (tmp / "calls").write_text("", encoding="utf-8")
            capped_env = {**env, "HERDR_CLAUDE_QUOTA_STATE": "quota_capped:5 hours=85%>=80%"}
            skipped = subprocess.run(
                [
                    str(DISPATCH), "--name", "demo-goal-tl-r1", "--kind", "claude",
                    "--cwd", str(handoff_goal), "--prompt", "Load /team-lead", "--role", "team_lead",
                    "--", "--model", "opus", "--allowedTools", "Bash", "Read", "Write",
                ],
                env=capped_env, capture_output=True, text=True,
            )
            self.assertEqual(skipped.returncode, 0, skipped.stderr)
            skipped_payload = json.loads(skipped.stdout)
            self.assertEqual(skipped_payload["resolved_kind"], "copilot")
            self.assertTrue(skipped_payload["skipped_claude"])
            self.assertEqual(skipped_payload["skipped_claude_reason"], "quota_capped:5 hours=85%>=80%")
            self.assertEqual(skipped_payload["logical_group"], "group/steering")
            self.assertEqual(skipped_payload["resolved_model"], "magpie/group/steering")
            self.assertEqual(skipped_payload["gateway_mode"], "magpie")
            start_calls = (tmp / "calls").read_text(encoding="utf-8")
            self.assertIn('"--kind", "copilot"', start_calls)
            self.assertNotIn("--allowedTools", start_calls)
            self.assertIn("magpie/group/steering", start_calls)

            # 3. Idempotent reuse when <goal>-goal-tl-r1 is already idle
            all_calls.write_text("", encoding="utf-8")
            reuse_env = {**env, "MOCK_EXISTING_TL_STATUS": "idle"}
            reused = subprocess.run(
                [
                    str(DISPATCH), "--name", "demo-goal-tl-r1", "--kind", "claude",
                    "--cwd", str(handoff_goal), "--prompt", "Wake up for hot append", "--role", "team_lead",
                ],
                env=reuse_env, capture_output=True, text=True,
            )
            self.assertEqual(reused.returncode, 0, reused.stderr)
            reused_payload = json.loads(reused.stdout)
            self.assertTrue(reused_payload["reused_existing"])
            self.assertEqual(reused_payload["workspace_id"], "w55")
            self.assertEqual(reused_payload["pane_id"], "w55:p1")
            reuse_calls = [json.loads(line) for line in all_calls.read_text(encoding="utf-8").splitlines()]
            self.assertFalse(any(c[:2] == ["workspace", "create"] for c in reuse_calls))
            self.assertFalse(any(c[:2] == ["agent", "start"] for c in reuse_calls))
            self.assertTrue(any(c[:2] == ["agent", "prompt"] for c in reuse_calls))


if __name__ == "__main__":
    unittest.main()
