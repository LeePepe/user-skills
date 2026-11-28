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

    def test_nonexistent_cwd_falls_back_to_existing_user_skills_for_new_and_inferred_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            user_skills = home / "Development" / "user-skills"
            user_skills.mkdir(parents=True)
            worktrees = home / "Development" / "worktrees"
            worktrees.mkdir(parents=True)
            handoff = home / "Development" / "handoff" / "skill-retro-foo"
            handoff.mkdir(parents=True)
            (handoff / "spec.md").write_text("# Goal\n", encoding="utf-8")
            (handoff / "ledger.md").write_text("Repo: LeePepe/user-skills\n", encoding="utf-8")
            fake = root / "herdr"
            log = root / "calls"
            fake.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['HERDR_TEST_LOG'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if os.environ.get('HERDR_ENV_SNAPSHOT'):
    with open(os.environ['HERDR_ENV_SNAPSHOT'], 'w') as f:
        json.dump({key: os.environ.get(key, '') for key in ('HERDR_SPAWN_ORIGIN', 'HERDR_AGENT_ROLE', 'HERDR_PARENT_PANE')}, f)
a = sys.argv[1:]
if a[:2] == ['workspace', 'create']:
    print(json.dumps({'result': {'workspace': {'workspace_id': 'w'}, 'root_pane': {'pane_id': 'w:p'}}}))
elif a[:2] == ['agent', 'get']:
    print('{}')
else:
    print('{}')
""")
            fake.chmod(0o755)
            env_snapshot = root / "env.json"
            env = dict(os.environ, HOME=str(home), PATH=f"{root}:{os.environ['PATH']}", MOCK_HERDR_LOG=str(log), HERDR_TEST_LOG=str(log), HERDR_ENV_SNAPSHOT=str(env_snapshot), HERDR_WORKTREES_ROOT=str(worktrees), HERDR_HANDOFF_ROOT=str(home / "Development" / "handoff"), HERDR_PROMPT_IDLE_GRACE="0", HERDR_PANE_ID="w-parent:p1")
            missing_worktree = worktrees / "skill-retro-foo"
            preserved_tag = "<!-- herdr-origin:v1 kind=human_routed sender=m3 topic=alpha -->"
            explicit = subprocess.run(
                [str(DISPATCH), "--new-workspace", "--role", "team_lead", "--name", "skill-retro-foo-goal-tl-r1", "--kind", "claude", "--cwd", str(missing_worktree), "--prompt", f"run\n\n{preserved_tag}"],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertIn(f"using existing fallback {user_skills}", explicit.stderr)
            explicit_calls = [__import__("json").loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(next(call for call in explicit_calls if call[:2] == ["workspace", "create"])[3], str(user_skills))
            self.assertEqual(__import__("json").loads(env_snapshot.read_text()), {
                "HERDR_SPAWN_ORIGIN": "agent_dispatch",
                "HERDR_AGENT_ROLE": "m1",
                "HERDR_PARENT_PANE": "w-parent:p1",
            })
            explicit_receipt = __import__("json").loads(explicit.stdout)
            self.assertEqual(explicit_receipt["spawn_origin"], "agent_dispatch")
            self.assertEqual(explicit_receipt["agent_role"], "m1")
            self.assertEqual(explicit_receipt["parent_pane"], "w-parent:p1")
            explicit_prompt = next(call for call in explicit_calls if call[:2] == ["agent", "prompt"])
            self.assertEqual(explicit_prompt[-1].count("herdr-origin:v1"), 1)
            self.assertIn(preserved_tag, explicit_prompt[-1])

            log.unlink()
            inferred = subprocess.run(
                [str(DISPATCH), "--new-workspace", "--role", "team_lead", "--name", "skill-retro-foo-goal-tl-r1", "--kind", "claude"],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertEqual(inferred.returncode, 0, inferred.stderr)
            inferred_calls = [__import__("json").loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(next(call for call in inferred_calls if call[:2] == ["workspace", "create"])[3], str(user_skills))
            inferred_prompt = next(call for call in inferred_calls if call[:2] == ["agent", "prompt"])
            self.assertEqual(inferred_prompt[-1].count("herdr-origin:v1"), 1)
            self.assertIn("<!-- herdr-origin:v1 kind=agent_dispatch role=m1", inferred_prompt[-1])

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
