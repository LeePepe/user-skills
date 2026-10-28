import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/goal-task/scripts/gtt-telemetry-export.py"
spec = importlib.util.spec_from_file_location("gtt_telemetry_export", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(exporter)


class GttTelemetryExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ROOT / "tests" / ".gtt-export-fixture"
        shutil.rmtree(self.fixture, ignore_errors=True)
        (self.fixture / "goal-a" / "receipts").mkdir(parents=True)
        (self.fixture / "goal-a" / "ledger.md").write_text(
            "owner: " + "/" + "Users/alice\nkey=ghp_secret123\nurl=https://example.test/x?token=abc\n"
            "mail alice@example.com\n",
            encoding="utf-8",
        )
        (self.fixture / "goal-a" / "goal.md").write_text("# Goal A\n", encoding="utf-8")
        (self.fixture / "goal-a" / "queue.json").write_text("{}\n", encoding="utf-8")
        (self.fixture / "goal-a" / "receipts" / "r1.md").write_text("receipt\n", encoding="utf-8")
        self.output = self.fixture / "bundle.jsonl"
        self.state = self.fixture / "state.json"

    def tearDown(self):
        shutil.rmtree(self.fixture, ignore_errors=True)

    def records(self):
        return [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]

    def test_full_export_redacts_and_normalizes(self):
        self.assertEqual(
            4,
            exporter.export([self.fixture], "Mac Mini", self.output, None, False),
        )
        records = self.records()
        self.assertEqual({"ledger", "goal", "queue", "receipt"}, {r["record_type"] for r in records})
        ledger = next(r for r in records if r["record_type"] == "ledger")
        self.assertIn("owner: ~", ledger["content"])
        self.assertNotIn("ghp_secret123", ledger["content"])
        self.assertNotIn("token=abc", ledger["content"])
        self.assertIn("token=[REDACTED]", ledger["content"])
        self.assertNotIn("alice@example.com", ledger["content"])
        self.assertEqual("mac-mini", ledger["machine_id"])

    def test_incremental_watermark_skips_then_emits_change(self):
        self.assertEqual(4, exporter.export([self.fixture], "m", self.output, self.state, True))
        self.assertEqual(0, exporter.export([self.fixture], "m", self.output, self.state, True))
        (self.fixture / "goal-a" / "goal.md").write_text("# Changed\n", encoding="utf-8")
        self.assertEqual(1, exporter.export([self.fixture], "m", self.output, self.state, True))
        self.assertEqual(5, len(self.records()))

    def test_symlink_is_refused(self):
        outside = self.fixture.parent / "outside-gtt.md"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            (self.fixture / "goal-a" / "receipts" / "bad.md").symlink_to(outside)
            with self.assertRaises(ValueError):
                exporter.export([self.fixture], "m", self.output, None, False)
        finally:
            outside.unlink(missing_ok=True)

    def test_incremental_symlink_output_is_refused(self):
        target = self.fixture / "target.jsonl"
        target.write_text("", encoding="utf-8")
        self.output.symlink_to(target)
        with self.assertRaises(ValueError):
            exporter.export([self.fixture], "m", self.output, self.state, True)

    def test_envelopes_collect_bot_tl_prmon_and_custom_offsets(self):
        (self.fixture / "goal-a" / "pr-dag.json").write_text('{"wave": 1}\n', encoding="utf-8")
        (self.fixture / "goal-a" / "ledger.md").write_text(
            "TL_PROFILE=worker | id=B1 | note=Allow path access\n", encoding="utf-8"
        )
        watch = self.fixture / "_watch" / "prmon"
        watch.mkdir(parents=True)
        history = watch / "heartbeat-history.jsonl"
        history.write_text('{"checked": 1}\n', encoding="utf-8")
        custom_root = self.fixture / "custom"
        custom_root.mkdir()
        custom = custom_root / "events.jsonl"
        custom.write_text('{"event": "a"}\n', encoding="utf-8")
        descriptors = self.fixture / "collectors.d"
        descriptors.mkdir()
        (descriptors / "custom.json").write_text(json.dumps({
            "collector": "custom", "root": str(custom_root), "glob": "*.jsonl",
            "record_type": "event", "mode": "jsonl_offset"
        }), encoding="utf-8")
        sync = self.fixture / "sync"
        count = exporter.export([self.fixture], "m", self.output, None, True, sync, descriptors)
        self.assertGreaterEqual(count, 4)
        records = [json.loads(line) for line in (sync / "outbox.jsonl").read_text().splitlines()]
        self.assertIn(("bot_tl", "pr_dag"), {(r["collector"], r["record_type"]) for r in records})
        self.assertIn(("prmon", "prmon_heartbeat"), {(r["collector"], r["record_type"]) for r in records})
        self.assertIn(("custom", "event"), {(r["collector"], r["record_type"]) for r in records})
        cursor = json.loads((sync / "cursors.json").read_text())
        custom_key = next(key for key in cursor if "events.jsonl" in key)
        self.assertEqual(custom.stat().st_size, cursor[custom_key]["offset"])
        with custom.open("a", encoding="utf-8") as handle:
            handle.write('{"event": "b"}\n')
        self.assertEqual(1, exporter.export([self.fixture], "m", self.output, None, True, sync, descriptors))

    def test_device_is_stable_and_transport_failure_preserves_offset(self):
        sync = self.fixture / "sync"
        exporter.export([self.fixture], "m", self.output, None, True, sync)
        device = json.loads((sync / "device.json").read_text())
        self.assertEqual(0, json.loads((sync / "outbox.state.json").read_text()).get("offset", 0))
        with mock.patch.object(exporter.subprocess, "run", side_effect=OSError("az unavailable")):
            with self.assertRaises(ValueError):
                exporter.export([self.fixture], "m", self.output, None, True, sync, azure_account="acct")
        state = json.loads((sync / "outbox.state.json").read_text())
        self.assertEqual(0, state.get("offset", 0))
        self.assertEqual(device["device_id"], json.loads((sync / "device.json").read_text())["device_id"])

    def test_config_supplies_transport_defaults(self):
        sync = self.fixture / "sync"
        (sync).mkdir()
        (sync / "config.json").write_text(json.dumps({
            "azure_account": "test-account",
            "azure_container": "test-container",
            "azure_table": "test-table",
            "azure_prefix": "test-prefix",
        }), encoding="utf-8")
        calls = []
        with mock.patch.object(exporter.subprocess, "run", side_effect=lambda command, check: calls.append(command)):
            exporter.export([self.fixture], "m", self.output, None, True, sync)
            exporter.export([self.fixture], "m", self.output, None, True, sync)
        self.assertTrue(any("test-account" in command for call in calls for command in call))
        blob = next(call for call in calls if call[:4] == ["az", "storage", "blob", "upload"])
        self.assertIn("test-container", blob)
        self.assertTrue(any("test-prefix/" in command for command in blob))

    def test_legacy_state_waits_for_transport_success(self):
        sync = self.fixture / "sync"
        legacy = self.fixture / "legacy-state.json"
        legacy.write_text('{"old": "cursor"}\n', encoding="utf-8")
        with mock.patch.object(exporter.subprocess, "run", side_effect=OSError("rsync unavailable")):
            with self.assertRaises(ValueError):
                exporter.export([self.fixture], "m", self.output, legacy, True, sync, rsync_to="remote:")
        self.assertEqual('{"old": "cursor"}\n', legacy.read_text(encoding="utf-8"))
        self.assertTrue((sync / "cursors.json").is_file())

    def test_notify_is_nonblocking_when_lock_is_held(self):
        sync = self.fixture / "sync"
        sync.mkdir()
        lock = (sync / "sync.lock").open("a+")
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(0, exporter.notify(sync))
            self.assertTrue((sync / "notify.signal").exists())
        finally:
            lock.close()


if __name__ == "__main__":
    unittest.main()
