import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "team-lead" / "scripts"
SUPERVISOR = SCRIPTS / "goal-supervisor.py"
ROUTER = SCRIPTS / "jev-skill-router.py"
COMPACT = SCRIPTS / "jev-compact.py"
JEV_CLIENT = SCRIPTS / "jev_client.py"


def load_supervisor():
    spec = importlib.util.spec_from_file_location("goal_supervisor", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jev_client():
    spec = importlib.util.spec_from_file_location("jev_client", JEV_CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoalSupervisorAndJevTests(unittest.TestCase):
    def test_jev_resolves_skill_keyed_falcon_source_key(self):
        jev_client = load_jev_client()
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            falcon_dir = home / ".config" / "falcon"
            falcon_dir.mkdir(parents=True)
            (falcon_dir / "source-keys.json").write_text(
                json.dumps({"team-lead": "flk_goal_task"}),
                encoding="utf-8",
            )
            with patch.object(jev_client.Path, "home", return_value=home), patch.dict(
                jev_client.os.environ,
                {
                    "FALCON_API_KEY": "",
                    "FALCON_SOURCE_KEY": "",
                    "JEV_API_KEY": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    jev_client._resolve_auth_token("http://127.0.0.1:19823"),
                    ("flk_goal_task", "flk_goal_task"),
                )
            (falcon_dir / "source-keys.json").write_text(
                json.dumps({"goal-task": "flk_legacy_goal_task", "deep-research": "flk_research"}),
                encoding="utf-8",
            )
            with patch.object(jev_client.Path, "home", return_value=home), patch.dict(
                jev_client.os.environ,
                {
                    "FALCON_API_KEY": "",
                    "FALCON_SOURCE_KEY": "",
                    "JEV_API_KEY": "",
                },
                clear=False,
            ):
                self.assertEqual(
                    jev_client._resolve_auth_token("http://127.0.0.1:19823", agent_role="team-lead"),
                    ("flk_legacy_goal_task", "flk_legacy_goal_task"),
                )
                self.assertEqual(
                    jev_client._resolve_auth_token("http://127.0.0.1:19823", agent_role="personal-m2-assistant"),
                    ("flk_legacy_goal_task", "flk_legacy_goal_task"),
                )
                self.assertEqual(
                    jev_client._resolve_auth_token("http://127.0.0.1:19823", agent_role="knowledge-digest"),
                    ("flk_research", "flk_research"),
                )

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

    def test_empty_events_ledger_tracked_and_watch_wakes_on_tl_idle_or_done(self):
        supervisor = load_supervisor()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            goal = root / "fresh-goal"
            goal.mkdir(parents=True)
            (goal / "ledger.md").write_text(
                "# Goal: fresh-goal\n- Owner: tianpei\n- TL: fresh-goal-goal-tl-r1\n",
                encoding="utf-8",
            )
            meta = supervisor.goal_metadata(root)
            self.assertEqual([item["name"] for item in meta], ["fresh-goal"])
            self.assertEqual(meta[0]["tl_name"], "fresh-goal-goal-tl-r1")

            agents_file = root / "agents.json"
            calls = []
            def fake_load_agents(_path):
                calls.append(1)
                if len(calls) == 1:
                    return [{"name": "fresh-goal-goal-tl-r1", "agent_status": "working", "cwd": str(goal)}]
                return [{"name": "fresh-goal-goal-tl-r1", "agent_status": "done", "cwd": str(goal)}]

            with patch.object(supervisor, "load_agents", side_effect=fake_load_agents), patch.object(
                supervisor.time, "sleep", return_value=None
            ):
                args = supervisor.argparse.Namespace(
                    handoff_dir=str(goal),
                    interval=1,
                    max_wait=30,
                    herdr_json=str(agents_file),
                    write_alerts=False,
                    mode="observe",
                    json=True,
                )
                watch_res = supervisor.watch(args)
            self.assertEqual(watch_res["reason"], "TL_IDLE_OR_DONE")
            self.assertEqual(watch_res["tl"], "fresh-goal-goal-tl-r1")
            self.assertEqual(watch_res["tl_status"], "done")
            self.assertEqual(watch_res["events"], 0)

    def test_watch_wakes_for_unresolved_decision_and_ignores_closed_event(self):
        supervisor = load_supervisor()
        with tempfile.TemporaryDirectory() as temp:
            goal = Path(temp) / "decision-goal"
            goal.mkdir(parents=True)
            ledger = goal / "ledger.md"
            ledger.write_text(
                "# Goal: decision-goal\n- TL: decision-goal-goal-tl-r1\n"
                "2026-11-12T00:00:00Z | EVT=NEED_GO | id=E7 | note=Approve change\n",
                encoding="utf-8",
            )
            args = supervisor.argparse.Namespace(
                handoff_dir=str(goal), interval=1, max_wait=1, herdr_json=None,
                write_alerts=False, mode="observe", json=True,
            )
            open_result = supervisor.watch(args)
            self.assertEqual(open_result["reason"], "NEED_DECISION")
            self.assertEqual(open_result["unresolved_events"][0]["event_id"], "E7")

            for close_event in ("RESOLVED", "ACK"):
                with self.subTest(close_event=close_event):
                    ledger.write_text(
                        "# Goal: decision-goal\n- TL: decision-goal-goal-tl-r1\n"
                        "2026-11-12T00:00:00Z | EVT=NEED_GO | id=E7 | note=Approve change\n"
                        f"2026-11-12T00:01:00Z | EVT={close_event} | id=E7\n",
                        encoding="utf-8",
                    )
                    with patch.object(supervisor, "scan", return_value={"anomalies": []}), patch.object(
                        supervisor, "load_agents", return_value=[]
                    ), patch.object(supervisor.time, "monotonic", side_effect=[0, 2]), patch.object(
                        supervisor.time, "sleep", return_value=None
                    ):
                        closed_result = supervisor.watch(args)
                    self.assertEqual(closed_result["reason"], "WATCH_SLICE_TIMEOUT")

    def test_watch_ignores_idle_status_while_visible_copilot_is_working(self):
        supervisor = load_supervisor()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            goal = root / "copilot-goal"
            goal.mkdir(parents=True)
            (goal / "ledger.md").write_text(
                "# Goal: copilot-goal\n- Owner: tianpei\n- TL: copilot-goal-goal-tl-r1\n",
                encoding="utf-8",
            )
            calls = []

            def fake_load_agents(_path):
                calls.append(1)
                status = "idle" if len(calls) <= 2 else "done"
                return [{
                    "name": "copilot-goal-goal-tl-r1",
                    "agent_status": status,
                    "cwd": str(goal),
                }]

            visible_calls = []
            def fake_read_visible(_name):
                visible_calls.append(1)
                return "○ Working · ctrl+c interrupt" if len(visible_calls) == 1 else ""

            with patch.object(supervisor, "scan", return_value={"anomalies": []}), patch.object(
                supervisor, "load_agents", side_effect=fake_load_agents
            ), patch.object(supervisor, "read_visible_from_herdr", side_effect=fake_read_visible), patch.object(
                supervisor.time, "sleep", return_value=None
            ):
                args = supervisor.argparse.Namespace(
                    handoff_dir=str(goal),
                    interval=1,
                    max_wait=30,
                    herdr_json=None,
                    write_alerts=False,
                    mode="observe",
                    json=True,
                )
                watch_res = supervisor.watch(args)

            self.assertEqual(watch_res["reason"], "TL_IDLE_OR_DONE")
            self.assertEqual(watch_res["tl_status"], "done")
            self.assertGreaterEqual(len(calls), 3)
            self.assertGreaterEqual(len(visible_calls), 2)

    def test_operator_policy_compatibility_and_default_upgrade_diff(self):
        supervisor = load_supervisor()
        default_res = supervisor.check_operator_policy_compatibility()
        self.assertTrue(default_res["ok"])
        self.assertTrue(default_res["compatible"])
        self.assertEqual(
            default_res["slots"],
            ["communication", "ticket_scope", "delivery_requirements"],
        )
        self.assertEqual(default_res["missing_slots"], [])
        self.assertEqual(default_res["type_mismatches"], [])

        cli_res = subprocess.run(
            [str(SUPERVISOR), "check-operator-policy", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        cli_payload = json.loads(cli_res.stdout)
        self.assertTrue(cli_payload["compatible"])

        with tempfile.TemporaryDirectory() as temp:
            custom_policy_path = Path(temp) / "operator-policy.json"
            # Simulate user personal overrides + an upgraded system template with a new default key
            custom_policy = {
                "schema_version": 1,
                "policy_id": "personal-policy",
                "communication": {
                    "language": "en-US",
                    "style": "concise_direct",
                    "max_summary_lines": 10,
                    "agent_signature": "Custom-TL",
                    "mention_handle": "@lee",
                },
                "ticket_scope": {
                    "auto_ruling_allowed_for_non_redline": True,
                    "pause_on_redlines": ["core_goal_change"],
                    "max_inline_files": 2,
                    "explorer_noise_file_threshold": 6,
                },
                "delivery_requirements": {
                    "commit_coauthor_trailer": "Co-authored-by: Custom <custom@example.com>",
                    "require_cross_vendor_review": True,
                    "require_r10_receipt": True,
                    "auto_merge_when_checks_green": False,
                    "clean_worktree_on_merge": True,
                    "close_workspace_on_goal_done": True,
                },
            }
            custom_policy_path.write_text(json.dumps(custom_policy), encoding="utf-8")
            diff_res = supervisor.check_operator_policy_compatibility(policy_path=custom_policy_path)
            self.assertTrue(diff_res["compatible"])
            self.assertIn("foreground_command_max_seconds", diff_res["missing_keys"]["ticket_scope"])
            self.assertEqual(diff_res["custom_overrides"]["communication"]["language"], "en-US")
            self.assertEqual(
                diff_res["merged_policy"]["ticket_scope"]["foreground_command_max_seconds"],
                5,
            )
            self.assertEqual(diff_res["merged_policy"]["communication"]["language"], "en-US")

            # Incompatible policy with missing slot and type mismatch fails check-operator-policy
            broken_policy_path = Path(temp) / "broken-policy.json"
            broken_policy_path.write_text(
                json.dumps({
                    "schema_version": "bad",
                    "communication": {"language": "zh-CN", "max_summary_lines": "fifteen"},
                    "ticket_scope": {},
                }),
                encoding="utf-8",
            )
            broken_cli = subprocess.run(
                [str(SUPERVISOR), "check-operator-policy", "--policy-file", str(broken_policy_path), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(broken_cli.returncode, 1)
            broken_payload = json.loads(broken_cli.stdout)
            self.assertFalse(broken_payload["compatible"])
            self.assertIn("delivery_requirements", broken_payload["missing_slots"])
            self.assertTrue(any(m["path"] == "communication.max_summary_lines" for m in broken_payload["type_mismatches"]))


if __name__ == "__main__":
    unittest.main()
