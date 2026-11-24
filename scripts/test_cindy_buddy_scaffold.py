import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/howto/scripts/cindy_buddy_scaffold.py"
FIXTURE_ROOT = ROOT / "scripts/.cindy-buddy-test-fixture"


def load_module():
    spec = importlib.util.spec_from_file_location("cindy_buddy_scaffold", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CindyBuddyScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        if FIXTURE_ROOT.exists():
            shutil.rmtree(FIXTURE_ROOT)
        FIXTURE_ROOT.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(FIXTURE_ROOT, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def scaffold(self, slug, role="read_only_review"):
        output = FIXTURE_ROOT / slug
        result = self.run_cli(
            "scaffold", "--slug", slug, "--display-name", slug.upper(),
            "--output-dir", str(output), "--tl-role", role, "--json",
        )
        return output, json.loads(result.stdout)

    def test_scaffold_generates_private_protocol_bundle_and_guarded_prompt(self):
        output, result = self.scaffold("review-a")
        self.assertTrue(result["ok"])
        state = output / "state/review-a"
        self.assertEqual((state.stat().st_mode & 0o777), 0o700)
        self.assertEqual((state / "items.jsonl").stat().st_mode & 0o777, 0o600)
        script = output / ".cindy/review-a-script.py"
        wake = output / ".cindy/wake-review-a-tl.sh"
        self.assertTrue(script.stat().st_mode & 0o100)
        wake_text = wake.read_text(encoding="utf-8")
        notify_text = (output / ".cindy/notify-review-a.py").read_text(encoding="utf-8")
        self.assertIn("READ-ONLY REVIEW ONLY", wake_text)
        self.assertIn("do not edit files, commit, push, merge", wake_text)
        self.assertIn("READ-ONLY REVIEW: do not edit files", notify_text)
        self.assertNotIn("worktree remediation", wake_text.lower())

    def test_protocol_less_start_completes_without_items_and_dispatches_pending_item(self):
        output, _ = self.scaffold("review-b")
        script = output / ".cindy/review-b-script.py"
        complete = subprocess.run(
            [sys.executable, str(script)], input='{"type":"start"}\n',
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(complete.stdout)["type"], "complete")
        items = output / "state/review-b/items.jsonl"
        items.write_text(json.dumps({"id": "42", "prompt": "inspect"}) + "\n", encoding="utf-8")
        dispatched = subprocess.run(
            [sys.executable, str(script)], input='{"type":"start"}\n',
            capture_output=True, text=True, check=True,
        )
        frame = json.loads(dispatched.stdout)
        self.assertEqual(frame["name"], "sessions.dispatch")
        self.assertEqual(frame["idempotency_key"], "review-b:42")
        self.assertIn("READ-ONLY REVIEW GUARD", frame["arguments"]["prompt"])
        ack = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps({"type": "call_result", "ok": True, "idempotency_key": "review-b:42"}) + "\n",
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(ack.stdout)["pending"], 0)

    def test_verify_isolation_rejects_overlapping_identity_or_paths(self):
        first, _ = self.scaffold("review-c")
        second, _ = self.scaffold("review-d", role="worktree_remediation")
        result = self.run_cli(
            "verify-isolation", "--config", str(first / "buddy.json"),
            "--config", str(second / "buddy.json"), "--json",
        )
        self.assertTrue(json.loads(result.stdout)["ok"])
        a = json.loads((first / "buddy.json").read_text(encoding="utf-8"))
        b = json.loads((second / "buddy.json").read_text(encoding="utf-8"))
        b["state_dir"] = a["state_dir"]
        (second / "overlap.json").write_text(json.dumps(b), encoding="utf-8")
        bad = subprocess.run(
            [sys.executable, str(SCRIPT), "verify-isolation", "--config", str(first / "buddy.json"),
             "--config", str(second / "overlap.json"), "--json"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(json.loads(bad.stdout)["ok"])

    def test_register_db_is_idempotent_and_backs_up_fixture(self):
        output, _ = self.scaffold("review-db")
        db = FIXTURE_ROOT / "cindy.sqlite"
        first = json.loads(self.run_cli("register-db", "--slug", "review-db", "--output-dir", str(output), "--db", str(db), "--now", "2026-11-24T10:01:00+00:00", "--json").stdout)
        second = json.loads(self.run_cli("register-db", "--slug", "review-db", "--config", str(output / "buddy.json"), "--db", str(db), "--now", "2026-11-24T10:01:00+00:00", "--json").stdout)
        self.assertTrue(first["ok"] and second["ok"])
        self.assertTrue(Path(second["backup"]).is_file())
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("select count(*) from cindy_buddies").fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from cindy_schedules").fetchone()[0], 1)
            self.assertEqual(conn.execute("select next_fire_at from cindy_schedules where slug='review-db'").fetchone()[0], first["next_fire_at"])


if __name__ == "__main__":
    unittest.main()
