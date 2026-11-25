from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/m2-dispatcher/scripts/m2_preflight.py"
REFERENCE = "skills/m2-dispatcher/references/pr-dag-auto-heal.md"


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def delta_output(result: subprocess.CompletedProcess[str], as_json: bool) -> dict:
    if as_json:
        return json.loads(result.stdout)
    return {key: json.loads(value) for key, value in (line.split("=", 1) for line in result.stdout.splitlines())}


class M2DispatcherRetroTests(unittest.TestCase):
    def test_skill_runtime_symlink_and_line_budget(self) -> None:
        runtime = ROOT / "skills/m2-dispatcher/scripts/skill_runtime"
        self.assertTrue(runtime.is_symlink())
        self.assertEqual(runtime.readlink().as_posix(), "../../skill-contract/scripts/skill_runtime")
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertLessEqual(len(SCRIPT.read_text(encoding="utf-8").splitlines()), 350)
        plugin = json.loads((ROOT / "skills/m2-dispatcher/.claude-plugin/plugin.json").read_text())
        self.assertEqual(plugin["version"], "1.1.0")

    def test_ledger_delta_incremental_watermark_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "goal"
            handoff.mkdir()
            ledger = handoff / "ledger.md"
            ledger.write_text(
                "## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n| T1 | ready | writer | — |\n"
                "\n## Events\n- 2026-01-01T00:00:00+08:00 | EVT=READY | id=E0\n",
                encoding="utf-8",
            )
            first = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_doc = json.loads(first.stdout)
            self.assertTrue(first_doc["has_delta"])
            self.assertEqual(first_doc["tasks"][0]["task_id"], "T1")
            self.assertTrue((handoff / ".m2-ledger-watermark.json").is_file())
            second = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json")
            self.assertFalse(json.loads(second.stdout)["has_delta"])
            ledger.write_text(ledger.read_text() .replace("| ready |", "| in_progress |") + "- 2026-01-01T00:00:01+08:00 | EVT=STARTED | id=E1\n", encoding="utf-8")
            third = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json")
            third_doc = json.loads(third.stdout)
            self.assertEqual(len(third_doc["new_events"]), 1)
            self.assertIn("EVT=STARTED", third_doc["new_events"][0])
            explicit = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--since-line", "0", "--tail", "1", "--json")
            self.assertEqual(len(json.loads(explicit.stdout)["new_events"]), 1)
            huge = handoff / "huge.md"
            ledger.write_text("## Events\n" + "".join(f"- 2026-01-01T00:00:{i:02d}+08:00 | EVT=E | id=E{i}\n" for i in range(120)), encoding="utf-8")
            bounded = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--no-save", "--tail", "20", "--json")
            self.assertLess(len(bounded.stdout.encode()), 4096)
            self.assertTrue(huge.parent.is_dir())

    def test_text_delta_redacts_tasks_and_utf8_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "goal"
            handoff.mkdir()
            personal_path, token = "/" + "Users/alice/secret", "ghp_" + "SyntheticToken1234567890ABCD"
            (handoff / "ledger.md").write_text("## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n| T1 | ready | writer | — |\n\n## Events\n- 2026-01-01 | EVT=NOTE | id=E1 | note=" + personal_path + " " + token + " 中文" + "很长" * 1400 + "\n", encoding="utf-8")
            result = run_cli("ledger-delta", "--handoff-dir", str(handoff))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("tasks=", result.stdout)
            self.assertNotIn(personal_path, result.stdout)
            self.assertNotIn("ghp_", result.stdout)
            self.assertIn("~", result.stdout)
            self.assertIn("<redacted>", result.stdout)
            self.assertLess(len(result.stdout.encode("utf-8")), 4096)

    def test_supersede_repairs_queue_and_rejects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "old-goal"
            handoff.mkdir()
            ledger = handoff / "ledger.md"
            ledger.write_text("# Goal\n- status: active\n\n## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n| T1 | in_progress | writer | — |\n\n## Events\n", encoding="utf-8")
            queue = handoff / "queue.json"
            queue.write_text(json.dumps({"tasks": [{"task_id": "T1", "status": "in_progress"}]}), encoding="utf-8")
            applied = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--apply", "--json")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            next_wave = subprocess.run([sys.executable, str(ROOT / "skills/team-lead/scripts/goal-queue.py"), "next-wave", "--handoff-dir", str(handoff), "--json"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(next_wave.returncode, 0, next_wave.stderr)
            self.assertTrue(json.loads(next_wave.stdout)["drained"])
            queue.write_text(json.dumps({"status": "in_progress", "tasks": [{"task_id": "T1", "status": "in_progress"}]}), encoding="utf-8")
            retry = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--apply", "--json")
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(queue.read_text())["status"], "cancelled")
            self.assertEqual(ledger.read_text().count("EVT=GOAL_SUPERSEDED"), 1)
            before = ledger.read_text()
            queue.write_text("{broken", encoding="utf-8")
            corrupt = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "newer", "--apply")
            self.assertEqual(corrupt.returncode, 1)
            self.assertEqual(ledger.read_text(), before)

    def test_supersede_preserves_titles_and_historical_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "old-goal"
            handoff.mkdir()
            event = "- 2026-01-01T00:00:00+08:00 | EVT=RULING | id=E0 | note=status: active"
            ledger = handoff / "ledger.md"
            ledger.write_text("## Tasks\n| ID | Status | Title | Owner | PR |\n|---|---|---|---|---|\n| T1 | active | Build active planning view | writer | — |\n\n## Events\n" + event + "\n", encoding="utf-8")
            result = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            after = ledger.read_text()
            self.assertIn("Build active planning view", after)
            self.assertIn(event, after)

    def test_symlinked_handoff_root_cannot_authorize_repo_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            other_repo = Path(tmp) / "other-repo"
            (other_repo / ".git").mkdir(parents=True)
            (other_repo / "src.py").write_text("x = 1\n", encoding="utf-8")
            (cwd / "handoff").symlink_to(ROOT, target_is_directory=True)
            for path in ("skills/m2-dispatcher/SKILL.md", "handoff/skills/m2-dispatcher/SKILL.md"):
                result = run_cli("check-write-path", "--path", path, "--json", cwd=cwd)
                self.assertEqual(result.returncode, 1, (path, result.stdout, result.stderr))
                self.assertFalse(json.loads(result.stdout)["allowed"])
            (cwd / "handoff").unlink()
            (cwd / "handoff").symlink_to(other_repo, target_is_directory=True)
            ext = run_cli("check-write-path", "--path", "handoff/src.py", "--json", cwd=cwd)
            self.assertEqual(ext.returncode, 1, (ext.stdout, ext.stderr))
            self.assertFalse(json.loads(ext.stdout)["allowed"])

    def test_exact_event_ids_and_overflow_continue_from_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "goal"
            handoff.mkdir()
            ledger = handoff / "ledger.md"
            ledger.write_text("## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n| T1 | ready | writer | — |\n\n## Events\n" + "\n".join(f"- 2026-01-01 | EVT=E | id=E{i}" for i in range(2)) + "\n", encoding="utf-8")
            self.assertEqual(run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json").returncode, 0)
            ledger.write_text(ledger.read_text() + "\n".join(f"- 2026-01-01 | EVT=E | id=E{i}" for i in range(2, 11)) + "\n", encoding="utf-8")
            delta = json.loads(run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json").stdout)
            self.assertEqual([event.split("id=")[1] for event in delta["new_events"]], [f"E{i}" for i in range(2, 11)])
            ledger.write_text(ledger.read_text() + "\n".join(f"- 2026-01-01 | EVT=E | id=E{i} | note={'中文' * 100}" for i in range(11, 36)) + "\n", encoding="utf-8")
            first = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--tail", "25", "--json")
            first_doc = json.loads(first.stdout)
            self.assertLess(len(first.stdout.encode("utf-8")), 4096)
            self.assertTrue(first_doc["tasks"] and first_doc["new_events"] and first_doc["truncated"])
            second = json.loads(run_cli("ledger-delta", "--handoff-dir", str(handoff), "--tail", "25", "--json").stdout)
            self.assertTrue(second["new_events"])

    def test_many_tasks_prioritize_active_and_page_without_consuming_events(self) -> None:
        for busy in (False, True):
            with self.subTest(busy=busy), tempfile.TemporaryDirectory() as tmp:
                handoff = Path(tmp) / "goal"
                handoff.mkdir()
                rows = "".join(f"| T{i} | {'BLOCKED' if i == 45 else 'done'} | writer | — |\n" for i in range(1, 46))
                events = "".join(f"- now | EVT=NOTE | id=E{i} | note={'load' * 550}\n" for i in range(8)) if busy else ""
                (handoff / "ledger.md").write_text("## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n" + rows + "\n## Events\n" + events, encoding="utf-8")
                first = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--json")
                self.assertEqual(first.returncode, 0, first.stderr)
                doc = json.loads(first.stdout)
                self.assertEqual(doc["tasks"][0]["task_id"], "T45")
                self.assertGreaterEqual(len(doc["tasks"]), 8)
                watermark = handoff / ".m2-ledger-watermark.json"
                before = watermark.read_bytes() if watermark.exists() else None
                seen = []
                for _ in range(10):
                    self.assertLessEqual(len(first.stdout.encode("utf-8")), 3851)
                    self.assertEqual(doc["total_tasks"], 45)
                    self.assertEqual(doc["task_counts"], {"done": 44, "blocked": 1})
                    self.assertEqual(doc["omitted_tasks"], 45 - len(doc["tasks"]))
                    self.assertEqual(doc["tasks_truncated"], len(doc["tasks"]) < 45)
                    if doc["tasks_truncated"]:
                        self.assertTrue(doc["truncated"])
                    seen.extend(task["task_id"] for task in doc["tasks"])
                    if doc["next_task_offset"] is None:
                        break
                    first = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--task-offset", str(doc["next_task_offset"]), "--no-save", "--json")
                    self.assertEqual(first.returncode, 0, first.stderr)
                    doc = json.loads(first.stdout)
                else:
                    self.fail("task paging did not terminate")
                self.assertEqual(len(seen), 45)
                self.assertEqual(set(seen), {f"T{i}" for i in range(1, 46)})
                self.assertEqual(watermark.read_bytes() if watermark.exists() else None, before)

    def test_long_event_continuation_is_lossless_in_both_formats(self) -> None:
        for as_json in (False, True):
            with self.subTest(as_json=as_json), tempfile.TemporaryDirectory() as tmp:
                handoff = Path(tmp) / "goal"
                handoff.mkdir()
                token = "ghp_" + "SyntheticToken1234567890ABCD"
                events = ["- now | EVT=NOTE | id=E0 | note=" + "中文" * 1250 + " " + token + " TAIL_0",
                          "- now | EVT=NOTE | id=E1 | note=" + "'\"" * 2200 + "TAIL_1"]
                (handoff / "ledger.md").write_text("## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n| T1 | ready | writer | — |\n\n## Events\n" + "\n".join(events) + "\n", encoding="utf-8")
                delivered, saw_clip, expected_replay = [], False, None
                for _ in range(20):
                    flags = ("--json",) if as_json else ()
                    result = run_cli("ledger-delta", "--handoff-dir", str(handoff), *flags)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertLessEqual(len(result.stdout.encode("utf-8")), 3851)
                    self.assertNotIn("ghp_", result.stdout)
                    doc = delta_output(result, as_json)
                    if expected_replay is not None:
                        self.assertEqual(doc, expected_replay)
                    if not doc["has_delta"]:
                        self.assertEqual(doc["remaining_events"], 0)
                        self.assertFalse(doc["truncated"])
                        break
                    self.assertTrue(doc["tasks"])
                    delivered.extend(doc["new_events"])
                    if doc["event_clipped"]:
                        saw_clip = True
                        self.assertTrue(doc["truncated"])
                        self.assertGreater(doc["remaining_events"], 0)
                        watermark = handoff / ".m2-ledger-watermark.json"
                        saved = watermark.read_bytes()
                        cursor = json.loads(saved)
                        self.assertGreater(cursor["event_char_offset"], 0)
                        self.assertEqual(cursor["current_event_id"], doc["current_event_id"])
                        expected_index = int(cursor["current_event_id"][1:])
                        self.assertEqual(cursor["last_event_idx"], expected_index)
                        self.assertEqual(cursor["last_event_id"], "E0" if expected_index else "")
                        replay = run_cli("ledger-delta", "--handoff-dir", str(handoff), "--no-save", *flags)
                        replay_doc = delta_output(replay, as_json)
                        self.assertEqual(watermark.read_bytes(), saved)
                    else:
                        replay_doc = None
                    expected_replay = replay_doc
                else:
                    self.fail("event continuation did not drain")
                self.assertTrue(saw_clip)
                self.assertEqual("".join(delivered), "".join(events).replace(token, "<redacted>"))
                cursor = json.loads((handoff / ".m2-ledger-watermark.json").read_text())
                self.assertEqual(cursor["last_event_id"], "E1")
                self.assertEqual(cursor["last_event_idx"], 2)
                self.assertEqual(cursor["event_char_offset"], 0)

    def test_quote_heavy_text_batches_deliver_every_tail_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "goal"
            handoff.mkdir()
            events = [f"- now | EVT=NOTE | id=E{i} | note=" + "'" * 500 + '"' + f"TAIL_MARKER_{i}" for i in range(8)]
            (handoff / "ledger.md").write_text("## Events\n" + "\n".join(events) + "\n", encoding="utf-8")
            delivered = []
            for _ in range(10):
                result = run_cli("ledger-delta", "--handoff-dir", str(handoff))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(len(result.stdout.encode("utf-8")), 4096)
                doc = delta_output(result, False)
                if not doc["has_delta"]:
                    self.assertIn("has_delta=false", result.stdout)
                    break
                self.assertFalse(doc["event_clipped"])
                for event in doc["new_events"]:
                    self.assertIn(event, events)
                delivered.extend(doc["new_events"])
            else:
                self.fail("quote-heavy text continuation did not drain")
            self.assertEqual(delivered, events)
            self.assertEqual(sum(f"TAIL_MARKER_{i}" in event for i in range(8) for event in delivered), 8)

    def test_supersede_goal_dry_run_apply_and_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "old-goal"
            handoff.mkdir()
            ledger = handoff / "ledger.md"
            ledger.write_text(
                "# Goal\n- status: active\n\n## Tasks\n| ID | Status | Owner | PR |\n|---|---|---|---|\n"
                "| T1 | in_progress | writer | — |\n| T2 | planned | writer | — |\n| T3 | ready | writer | — |\n| T4 | done | writer | — |\n\n## Events\n",
                encoding="utf-8",
            )
            queue = handoff / "queue.json"
            queue.write_text(json.dumps({"tasks": [{"task_id": "T1", "status": "in_progress"}, {"task_id": "T4", "status": "done"}]}), encoding="utf-8")
            before_ledger, before_queue = ledger.read_text(), queue.read_text()
            dry = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--json")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(ledger.read_text(), before_ledger)
            self.assertEqual(queue.read_text(), before_queue)
            applied = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--apply", "--json")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            after = ledger.read_text()
            self.assertNotRegex(after, r"\b(?:in_progress|active|planning)\b")
            self.assertEqual(after.count("EVT=GOAL_SUPERSEDED"), 1)
            self.assertEqual(after.count("EVT=GOAL_DONE"), 1)
            self.assertEqual(json.loads(queue.read_text())["tasks"][0]["status"], "cancelled")
            repeat = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", "new-goal", "--apply", "--json")
            repeat_doc = json.loads(repeat.stdout)
            self.assertTrue(repeat_doc["already_superseded"])
            self.assertEqual(repeat_doc["events_appended"], [])
            for args in (("bad/slug",), ("old-goal",),):
                result = run_cli("supersede-goal", "--old-handoff", str(handoff), "--new-goal", args[0])
                self.assertEqual(result.returncode, 1)
                self.assertIn(REFERENCE, result.stderr)
            missing = run_cli("supersede-goal", "--old-handoff", str(handoff / "missing"), "--new-goal", "newer")
            self.assertEqual(missing.returncode, 1)
            self.assertIn(REFERENCE, missing.stderr)

    def test_check_write_path_positive_and_counter_cases(self) -> None:
        for path in ("handoff/retro-x/spec.md", "~/Development/handoff/retro-x/ledger.md", "/tmp/probe.json", ".copilot/session-state/s1/notes.md"):
            result = run_cli("check-write-path", "--path", path, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["allowed"])
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "escape"
            link.symlink_to(ROOT / "skills/m2-dispatcher/SKILL.md")
            paths = ["skills/m2-dispatcher/SKILL.md", "scripts/test_verify_skills.py", "handoff/x/../../skills/m2-dispatcher/SKILL.md", str(link)]
            for path in paths:
                result = run_cli("check-write-path", "--path", path, "--json")
                self.assertEqual(result.returncode, 1, (path, result.stdout, result.stderr))
                self.assertFalse(json.loads(result.stdout)["allowed"])
                self.assertIn(REFERENCE, result.stderr + result.stdout)

    def test_cli_exit2_self_disclosing_contract(self) -> None:
        for args in (("--unknown-flag",), ("ledger-delta", "--json")):
            result = run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertLessEqual(len(result.stderr.splitlines()), 3)
            self.assertIn(REFERENCE, result.stderr)

    def test_eval_drawer_counter_case_ratio_and_internal_reality(self) -> None:
        eval_result = subprocess.run([sys.executable, "skills/skill-contract/scripts/skill_contract.py", "eval", "--skill", "m2-dispatcher", "--strict", "--json"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(eval_result.returncode, 0, eval_result.stdout + eval_result.stderr)
        audit_result = subprocess.run([sys.executable, "skills/skill-contract/scripts/skill_contract.py", "audit", "--skills", "m2-dispatcher", "--strict", "--json"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(audit_result.returncode, 0, audit_result.stdout + audit_result.stderr)
        drawer = json.loads((ROOT / "skills/m2-dispatcher/references/eval-cases.json").read_text())
        self.assertTrue(8 <= len(drawer["active_cases"]) <= 12)
        sys.path.insert(0, str(ROOT / "skills/skill-retro/scripts"))
        from skill_retro_core.reality_check import _is_eval_counter_case, internal_reality_check
        ratio = sum(_is_eval_counter_case(case) for case in drawer["active_cases"]) / len(drawer["active_cases"])
        self.assertGreaterEqual(ratio, 0.33)
        self.assertTrue(internal_reality_check(ROOT / "skills/m2-dispatcher")["ok"])


if __name__ == "__main__":
    unittest.main()
