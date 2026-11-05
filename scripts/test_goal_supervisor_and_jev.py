import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "goal-task" / "scripts"
SUPERVISOR = SCRIPTS / "goal-supervisor.py"
ROUTER = SCRIPTS / "jev-skill-router.py"
COMPACT = SCRIPTS / "jev-compact.py"


def load_supervisor():
    spec = importlib.util.spec_from_file_location("goal_supervisor", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoalSupervisorAndJevTests(unittest.TestCase):
    def test_mode_resolution_and_watchdog(self):
        supervisor = load_supervisor()
        self.assertEqual(supervisor.resolve_supervisor_mode("off"), "off")
        self.assertEqual(
            supervisor.evaluate_stuck_loop("writer", "writer", "error\nerror\nerror"),
            {
                "type": "STUCK_LOOP", "goal_id": "", "agent": "writer", "role": "writer",
                "repeating_prob": 1.0, "progress_prob": 0.0,
            },
        )
        boundary = supervisor.evaluate_stuck_loop(
            "writer", "writer", "", jev_override={"violating_role_boundary": {"noul": 0.9}}
        )
        self.assertEqual(boundary["type"], "BOUNDARY_VIOLATION")

    def test_scan_modes_and_status_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            goal = root / "goal-a"
            (goal / "receipts").mkdir(parents=True)
            (goal / "ledger.md").write_text(
                "# Goal: goal-a\n- Owner: owner | 协调者 bot: flower\n"
                "- TL: goal-a-goal-tl-r1\n"
                "2026-11-05T00:00:00Z | EVT=NEED_GO | id=E1 | note=Approve change\n",
                encoding="utf-8",
            )
            agents = root / "agents.json"
            agents.write_text(json.dumps({"agents": [{
                "name": "goal-a-writer-r1", "agent_status": "blocked", "cwd": str(goal)
            }]}), encoding="utf-8")
            command = [str(SUPERVISOR), "scan", "--handoff-root", str(root),
                       "--herdr-json", str(agents), "--mode", "observe", "--json"]
            result = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
            self.assertEqual(result["mode"], "observe")
            self.assertEqual(result["anomalies"][0]["decision"], "observe (would accept)")
            self.assertFalse(list((goal / "alerts").glob("*")) if (goal / "alerts").exists() else [])
            auto = subprocess.run(
                [str(SUPERVISOR), "scan", "--handoff-root", str(root),
                 "--herdr-json", str(agents), "--mode", "observe", "--write-alerts", "--json"],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(auto.stdout)["mode"], "observe")
            self.assertFalse((goal / "alerts").exists())
            check = subprocess.run(
                [str(SUPERVISOR), "status", "--handoff-root", str(root),
                 "--herdr-json", str(agents), "--check", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(check.returncode, 1)
            status = json.loads(check.stdout)
            self.assertEqual(status["needs_a_person"][0]["action"], "decision")
            self.assertEqual(status["needs_a_person"][0]["summary"], "Approve change")
            observed = json.loads(subprocess.run(
                [str(SUPERVISOR), "observe", "--handoff-dir", str(goal),
                 "--herdr-json", str(agents), "--json"],
                capture_output=True, text=True, check=True,
            ).stdout)
            self.assertEqual(observed["goal_id"], "goal-a")

    def test_notify_bridge_off_short_circuits(self):
        result = subprocess.run(
            [str(SUPERVISOR), "notify-bridge", "--mode", "off", "--json"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "agent": "", "alert_path": "", "decision": "off (skipped)",
            "goal": "", "mode": "off", "status": "", "would_accept": False,
            "written": False,
        })

    def test_status_review_when_queue_drained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            goal = root / "goal-a"
            goal.mkdir()
            (goal / "ledger.md").write_text(
                "2026-11-05T00:00:00Z | EVT=START | id=E1\n",
                encoding="utf-8",
            )
            (goal / "queue.json").write_text(
                json.dumps({"tasks": [{"task_id": "tk1", "status": "done"}]}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(SUPERVISOR), "status", "--handoff-root", str(root), "--json"],
                capture_output=True, text=True, check=True,
            )
            status = json.loads(result.stdout)
            self.assertEqual(status["needs_a_person"][0]["action"], "review")

    def test_compact_uses_per_block_mock_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archive"
            blocks = [
                {"id": "keep", "turn": 1, "text": "ordinary output"},
                {"id": "drop", "turn": 2, "text": "active.py:12"},
            ]
            mock = {
                "keep": {
                    "q_contains_active_anchor": {"noul": 1.0},
                    "q_superseded_by_later_edit": {"noul": 0.0},
                },
                "drop": {
                    "q_contains_active_anchor": {"noul": 0.0},
                    "q_superseded_by_later_edit": {"noul": 1.0},
                },
            }
            result = subprocess.run(
                [str(COMPACT), "--input-json", json.dumps(blocks),
                 "--archive-dir", str(archive), "--keep-recent", "0",
                 "--mock-jev-json", json.dumps(mock), "--json"],
                capture_output=True, text=True, check=True,
            )
            compacted = json.loads(result.stdout)
            self.assertEqual(compacted[0]["action"], "keep")
            self.assertEqual(compacted[1]["action"], "drop")

    def test_router_truth_table_and_offline_fallback(self):
        def run(mock, title="Chart task", paths="Sources/Chart.swift"):
            result = subprocess.run(
                [str(ROUTER), "--title", title, "--paths", paths,
                 "--mock-jev-json", json.dumps(mock), "--json"],
                capture_output=True, text=True, check=True,
            )
            return json.loads(result.stdout)

        self.assertEqual(run({"needs_specialist_skill": {"noul": 0.2}})["selected_skills"], [])
        selected = run({
            "needs_specialist_skill": {"noul": 0.9},
            "primary_skill": {"choice": "guide-swiftui-charts", "confidence": 0.8},
            "companion_my-designer": {"noul": 0.9},
            "companion_motion-studio": {"noul": 0.9},
        })
        self.assertEqual(len(selected["selected_skills"]), 2)
        offline = subprocess.run(
            [str(ROUTER), "--title", "Chart task", "--paths", "Sources/Chart.swift", "--json"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(offline.stdout)["decision_source"], "deterministic_fallback")

    def test_compact_lossless_keep_and_archive_drop(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archive"
            blocks = [
                {"id": "keep", "turn": 1, "tool": "bash", "text": "src/main.py:41\n5d14085"},
                {"id": "drop", "turn": 2, "tool": "bash", "text": "ordinary output"},
                {"id": "recent", "turn": 3, "tool": "bash", "text": "latest"},
            ]
            result = subprocess.run(
                [str(COMPACT), "--input-json", json.dumps(blocks), "--archive-dir", str(archive),
                 "--keep-recent", "1", "--json"],
                capture_output=True, text=True, check=True,
            )
            compacted = json.loads(result.stdout)
            self.assertEqual(compacted[0]["text"], blocks[0]["text"])
            self.assertEqual(compacted[0]["action"], "keep")
            self.assertEqual(compacted[1]["action"], "drop")
            self.assertTrue((archive / "turn-2-drop.log").read_text() == blocks[1]["text"])
            self.assertEqual(compacted[2]["action"], "keep_recent")


if __name__ == "__main__":
    unittest.main()
