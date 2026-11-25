from __future__ import annotations

import contextlib
from io import StringIO
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/skill-contract/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from skill_runtime.cli_contract import SelfDisclosingArgumentParser, compact_json_dumps, is_valid_skill_slug, redact_payload, redact_text
from skill_runtime.copilot_trace import read_copilot_sessions
from skill_runtime.fs_guard import atomic_write_json, get_cached, is_safe_local_dir, is_safe_local_file, put_cached, safe_read_text
from skill_runtime.git_version import resolve_skill_version, verify_commit_exists
import skill_contract


class SharedSkillRuntimeTests(unittest.TestCase):
    def test_fs_guard_atomic_write_read_and_cache_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "facts.json"
            self.assertTrue(is_safe_local_dir(root))
            atomic_write_json(target, {"ok": True})
            self.assertTrue(is_safe_local_file(target))
            self.assertEqual(json.loads(safe_read_text(target) or "{}"), {"ok": True})
            self.assertIsNone(safe_read_text(root / "missing"))
            mobile = root / "Mobile Documents" / "cloud.txt"
            mobile.parent.mkdir()
            mobile.write_text("cloud", encoding="utf-8")
            self.assertFalse(is_safe_local_file(mobile))
            target_link = root / "facts-link.json"
            target_link.symlink_to(target)
            self.assertFalse(is_safe_local_file(target_link))
            dir_link = root / "dir-link"
            dir_link.symlink_to(root, target_is_directory=True)
            self.assertFalse(is_safe_local_dir(dir_link))
            dataless = mock.Mock(st_flags=0x40000000, st_mode=stat.S_IFREG)
            with mock.patch("skill_runtime.fs_guard.os.lstat", return_value=dataless):
                self.assertFalse(is_safe_local_file(target))
            put_cached(target, {"cached": 1}, "facts")
            self.assertEqual(get_cached(target, "facts"), {"cached": 1})
            target.write_text('{"ok": false}\n', encoding="utf-8")
            self.assertIsNone(get_cached(target, "facts"))
            self.assertFalse(is_safe_local_file(Path("/Volumes/remote/facts.json")))

    def test_cli_redaction_slug_and_bounded_json(self) -> None:
        text = redact_text("/" + "Users/alice/project ghp_secret lin_api_token sk-secret Bearer abc.def")
        self.assertNotIn("alice", text)
        self.assertNotIn("ghp_secret", text)
        self.assertNotIn("abc.def", text)
        self.assertEqual(redact_payload({"nested": ["/" + "Users/bob"]}), {"nested": ["~"]})
        encoded = compact_json_dumps({"keep": "x", "large": "y" * 50_000}, max_bytes=500, preserve_keys=("keep",))
        self.assertLessEqual(len(encoded.encode()), 500)
        self.assertIn('"keep":"x"', encoded)
        self.assertTrue(is_valid_skill_slug("skill_runtime-2"))
        self.assertFalse(is_valid_skill_slug("Skill Runtime"))

    def test_parser_error_is_three_lines_and_referenced(self) -> None:
        parser = SelfDisclosingArgumentParser(prog="runtime")
        stderr = StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(stderr):
            parser.error("bad flag")
        self.assertEqual(raised.exception.code, 2)
        lines = stderr.getvalue().splitlines()
        self.assertLessEqual(len(lines), 3)
        self.assertIn("Reference: skills/skill-contract/references/valves-and-metabolism.md", lines[-1])

    def test_copilot_trace_pairs_nested_and_flat_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            events = Path(raw) / "session" / "events.jsonl"
            events.parent.mkdir()
            rows = [
                {"type": "hook.start", "data": {}},
                {"type": "session.start", "data": {"cwd": "/tmp/work", "selectedModel": "test-model"}},
                {"type": "tool.execution_start", "data": {"toolCallId": "call-1", "toolName": "bash", "arguments": {"command": "echo"}}},
                {"type": "tool.execution_complete", "data": {"toolCallId": "call-1", "content": "<shellId: x completed with exit code 7>"}},
                {"type": "tool.execution_start", "data": {"toolCallId": "call-2", "toolName": "read", "arguments": {}}},
                {"type": "tool.execution_complete", "data": {"toolCallId": "call-2", "content": "z" * 20_001}},
                {"type": "tool.execution_start", "data": {"toolCallId": "call-3", "toolName": "read", "arguments": {}}},
                {"type": "tool.execution_complete", "data": {"toolCallId": "call-3", "content": "Output too large to read at once"}},
                {"type": "assistant.message", "content": "visible", "reasoningText": "think", "output_tokens": 4},
                {"type": "assistant.message", "content": "", "reasoningText": "tool-only reasoning"},
                {"type": "session.usage_record", "usage": {"output_tokens": 3}},
                {"type": "skill.invoked", "skillName": "skill-contract"},
                {"type": "subagent.completed", "agentName": "writer", "model": "m", "totalTokens": 9, "durationMs": 2},
            ]
            events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            sessions = read_copilot_sessions(raw, days=1, max_sessions=2)
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session["selectedModel"], "test-model")
            self.assertEqual(session["tool_calls"][0]["exitCode"], 7)
            self.assertEqual(session["tool_calls"][0]["arguments"], {"command": "echo"})
            self.assertTrue(session["tool_calls"][1]["aci_output_bloat"])
            self.assertTrue(session["tool_calls"][2]["aci_output_bloat"])
            self.assertEqual(session["assistant_turns"][1]["content"], "")
            self.assertEqual(session["assistant_turns"][1]["reasoning_chars"], len("tool-only reasoning"))
            self.assertEqual(session["reasoning_chars"], 5 + len("tool-only reasoning"))
            self.assertEqual(session["output_tokens"], 7)
            self.assertEqual(session["skills"][0]["skillName"], "skill-contract")
            self.assertTrue(session["subagents"])

    def test_hygiene_facts_reports_legacy_monoliths_as_soft_advisory(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "skills/skill-contract/scripts/skill_contract.py"), "hygiene-facts", "--no-check-diff", "--json"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = json.loads(result.stdout)
        self.assertEqual(facts["gate_tier"], "soft_advisory_exit_0")
        self.assertTrue(facts["legacy_monoliths"])
        self.assertTrue(all(item["lines"] <= item["cap"] for item in facts["legacy_monoliths"]))

    def test_duplicate_fork_ignores_init_and_relative_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            one = repo / "skills" / "one"
            two = repo / "skills" / "two"
            for package in (one, two):
                (package / "scripts").mkdir(parents=True)
                (package / "SKILL.md").write_text("---\ndescription: test\n---\n", encoding="utf-8")
                (package / "scripts" / "__init__.py").write_text(f"name = {package.name!r}\n", encoding="utf-8")
            (one / "scripts" / "skill_runtime").mkdir()
            (one / "scripts" / "skill_runtime" / "runtime.py").write_text("value = 1\n", encoding="utf-8")
            (two / "scripts" / "skill_runtime").symlink_to("../../one/scripts/skill_runtime", target_is_directory=True)
            result = subprocess.run(
                [sys.executable, str(ROOT / "skills/skill-contract/scripts/skill_contract.py"), "audit", "--repo", str(repo), "--all", "--strict", "--no-cache"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertNotIn("duplicate-script-fork", result.stdout)

    def test_git_version_and_commit_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / "skills/demo").mkdir(parents=True)
            (repo / "skills/demo/SKILL.md").write_text("---\ndescription: demo\n---\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "demo"], cwd=repo, check=True)
            info = resolve_skill_version(repo, "demo")
            self.assertRegex(info.version, r"^v1@")
            self.assertGreater(info.commits, 0)
            self.assertTrue(verify_commit_exists(repo, "HEAD"))
            self.assertFalse(verify_commit_exists(repo, "not-a-sha"))

    def test_modularity_valve_positive_and_counter_cases(self) -> None:
        self.assertNotIn("skills/skill-contract/scripts/skill_contract.py", skill_contract.LEGACY_SCRIPT_LINE_CAPS)
        self.assertFalse(skill_contract.audit_package(ROOT / "skills/skill-contract"))
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "skills" / "demo"
            (package / "scripts").mkdir(parents=True)
            (package / "SKILL.md").write_text("---\ndescription: demo\n---\n", encoding="utf-8")
            (package / "scripts" / "new.py").write_text("pass\n" * 1001, encoding="utf-8")
            codes = {item["code"] for item in skill_contract.audit_package(package)}
            self.assertIn("script-lines", codes)
            key = "skills/demo/scripts/legacy.py"
            skill_contract.LEGACY_SCRIPT_LINE_CAPS[key] = 2
            try:
                (package / "scripts" / "legacy.py").write_text("pass\n" * 3, encoding="utf-8")
                codes = {item["code"] for item in skill_contract.audit_package(package)}
                self.assertIn("script-monolith-growth", codes)
            finally:
                skill_contract.LEGACY_SCRIPT_LINE_CAPS.pop(key, None)


if __name__ == "__main__":
    unittest.main()
