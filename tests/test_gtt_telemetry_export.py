import importlib.util
import json
from pathlib import Path
import shutil
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/goal-task/scripts/gtt-telemetry-export.py"
MIRROR = ROOT / "skills/goal-target-task/scripts/gtt-telemetry-export.py"
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
            "owner: /Users/alice\nkey=ghp_secret123\nurl=https://example.test/x?token=abc\n"
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

    def test_mirror_is_byte_for_byte_identical(self):
        self.assertEqual(SCRIPT.read_bytes(), MIRROR.read_bytes())


if __name__ == "__main__":
    unittest.main()
