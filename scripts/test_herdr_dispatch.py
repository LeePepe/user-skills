import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh"


class HerdrDispatchTests(unittest.TestCase):
    def test_workspace_tab_flags_are_documented(self):
        result = subprocess.run([str(DISPATCH), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--workspace <workspace-id>", result.stderr)
        self.assertIn("--tab-label <label>", result.stderr)

    def test_workspace_mode_does_not_require_herdr_environment(self):
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
elif args[:2] == ['tab', 'create']:
    print(json.dumps({'result': {'tab': {'workspace_id': 'w1', 'tab_id': 't9'}, 'root_pane': {'pane_id': 'w1:p9'}}}))
elif args[:2] == ['agent', 'start']:
    print('{}')
elif args[:2] == ['agent', 'get']:
    print(json.dumps({'agent_status': 'idle', 'interactive_ready': True}))
elif args[:2] == ['agent', 'prompt']:
    print('{}')
else:
    print('{}')
""")
            fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{temp}:{os.environ['PATH']}", HERDR_TEST_LOG=str(log), HERDR_PROMPT_IDLE_GRACE="0")
            result = subprocess.run([
                str(DISPATCH), "--name", "m2-test-r1", "--kind", "copilot", "--cwd", temp,
                "--prompt", "hello", "--workspace", "w1", "--tab-label", "Custom Tab",
            ], capture_output=True, text=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("w2J:p1", result.stderr)
            receipt = __import__("json").loads(result.stdout)
            self.assertEqual(receipt["workspace_id"], "w1")
            self.assertEqual(receipt["tab_id"], "t9")
            calls = [__import__("json").loads(line) for line in log.read_text().splitlines()]
            tab = next(call for call in calls if call[:2] == ["tab", "create"])
            self.assertEqual(tab, ["tab", "create", "--workspace", "w1", "--cwd", temp, "--label", "Custom Tab", "--no-focus"])
            start = next(call for call in calls if call[:2] == ["agent", "start"])
            self.assertIn("w1:p9", start)


if __name__ == "__main__":
    unittest.main()
