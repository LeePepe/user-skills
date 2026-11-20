import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh"


class HerdrDispatchTests(unittest.TestCase):
    def test_new_workspace_flags_are_documented_without_tab_creation(self):
        result = subprocess.run([str(DISPATCH), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--new-workspace", result.stderr)
        self.assertIn("--workspace-label <label>", result.stderr)
        self.assertNotIn("--tab-label <label>", result.stderr)

    def test_new_workspace_and_legacy_workspace_flags_always_create_new_space_never_tab(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "herdr"
            log = Path(temp) / "calls"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['HERDR_TEST_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
if args[:2] == ['agent', 'get']:
    get_count = sum(1 for line in open(os.environ['HERDR_TEST_LOG']) if 'agent", "get' in line)
    print('{}' if get_count == 1 else json.dumps({'agent_status': 'idle', 'interactive_ready': True}))
elif args[:2] == ['workspace', 'create']:
    print(json.dumps({'result': {'workspace': {'workspace_id': 'w-space'}, 'root_pane': {'pane_id': 'w-space:p1'}}}))
elif args[:2] == ['tab', 'create']:
    sys.exit('herdr tab create must never be called')
elif args[:2] == ['agent', 'start']:
    print('{}')
elif args[:2] == ['agent', 'prompt']:
    print('{}')
else:
    print('{}')
""")
            fake.chmod(0o755)
            for flag_args, expected_label in (
                (["--new-workspace", "--workspace-label", "info-email"], "info-email"),
                (["--workspace", "w1", "--tab-label", "Legacy Tab"], "Legacy Tab"),
            ):
                with self.subTest(flags=flag_args):
                    if log.exists():
                        log.unlink()
                    env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}", HERDR_TEST_LOG=str(log), MOCK_HERDR_LOG=str(log), HERDR_PROMPT_IDLE_GRACE="0")
                    result = subprocess.run([
                        str(DISPATCH), "--name", "m2-test-r1", "--kind", "copilot", "--cwd", temp,
                        "--prompt", "hello", *flag_args,
                    ], capture_output=True, text=True, env=env, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("w2J:p1", result.stderr)
                    receipt = __import__("json").loads(result.stdout)
                    self.assertEqual(receipt["workspace_id"], "w-space")
                    self.assertNotIn("tab_id", receipt)
                    calls = [__import__("json").loads(line) for line in log.read_text().splitlines()]
                    self.assertFalse(any(call[:2] == ["tab", "create"] for call in calls))
                    ws_create = next(call for call in calls if call[:2] == ["workspace", "create"])
                    self.assertEqual(ws_create, ["workspace", "create", "--cwd", temp, "--label", expected_label, "--no-focus"])
                    start = next(call for call in calls if call[:2] == ["agent", "start"])
                    self.assertIn("w-space:p1", start)
                    self.assertIn(temp, start)
                    self.assertIn(str(Path.home() / "Development" / "user-skills"), start)
                    self.assertIn(str(Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"), start)


if __name__ == "__main__":
    unittest.main()
