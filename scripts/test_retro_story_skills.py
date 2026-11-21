#!/usr/bin/env python3
"""Unit tests for Multica Skill Progressive Retro Autopilot (`multica_skills_autopilot.py`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


def _load_autopilot_module():
    path = ROOT / "skills" / "retro-loop" / "scripts" / "multica_skills_autopilot.py"
    spec = importlib.util.spec_from_file_location("multica_skills_autopilot_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTOPILOT = _load_autopilot_module()


def _sample_skill_retro_report() -> dict:
    return {
        "ok": True,
        "schema": "skill-retro-diagnostic/v1",
        "wall_clock_date": "2026-11-21",
        "window_cst": "30d",
        "source_as_of": "2026-11-21T00:30:00+08:00",
        "sample_size": 64,
        "coverage_status": "healthy",
        "sources_ingested": {
            "sessions_observed": 64,
            "trace_spans_ingested": 180,
            "skills_in_inventory": 28,
        },
        "summary": {
            "trigger_miss_skills": 1,
            "zero_action_load_skills": 1,
            "healthy_r1_r2_convergences": 3,
            "directional_drifts": 1,
            "hot_drawers_count": 2,
            "on_demand_drawers_count": 5,
            "unwired_drawers_count": 1,
            "true_deadwood_drawers_count": 0,
            "true_catcher_scripts_count": 12,
            "ceremonial_scripts_count": 2,
            "rigid_roadblock_scripts_count": 1,
            "latency_sink_scripts_count": 1,
            "wild_patterns_count": 1,
            "high_signal_proposals_count": 2,
            "observation_pool_count": 1,
            "slow_flywheel_ready": True,
        },
        "layer0_direction_and_triggers": {
            "trigger_misses": [{"skill": "my-designer", "missed_sessions": 2, "domain_sessions": 6, "miss_rate": 0.33}],
            "zero_action_loads": [],
            "healthy_convergence_count": 3,
            "directional_drift_count": 1,
        },
        "layer1_drawer_metabolism": {
            "hot_drawers": [],
            "on_demand_correction_drawers": [],
            "unwired_disconnected_drawers": [
                {"skill": "my-designer", "drawer": "references/unwired-a11y.md", "domain_failure_count": 3, "scenario_sessions": 6}
            ],
            "true_zero_hit_deadwood": [],
        },
        "layer2_script_efficacy_and_speed": {
            "true_catchers": [],
            "ceremonial_rubber_stamps": [],
            "rigid_roadblocks": [
                {"skill": "team-lead", "script": "scripts/goal-supervisor.py", "exit_2_count": 3, "exit_2_sessions": 2}
            ],
            "latency_sinks": [],
        },
        "layer3_wild_patterns_and_eval": {
            "wild_patterns": [],
            "taste_and_diversity_summary": {"selection_receipt_count": 2},
        },
        "proposals": [
            {
                "id": "skill-retro-l2-roadblock-team-lead-goal-supervisor-py",
                "ref": "retro://skill-retro/l2-roadblock/team-lead/goal-supervisor.py",
                "metric_id": "skill-retro-l2-roadblock-team-lead",
                "skill": "team-lead",
                "layer": "L2",
                "category": "rigid_roadblock",
                "priority": "P1",
                "title": "[Skill Retro L2] 消除脚本参数路障 `team-lead/goal-supervisor.py`（exit=2 失败 3 次）",
                "plain_reason": "`goal-supervisor.py` 因参数过严触发 3 次 `exit=2`，需补兼容参数与报错自引导",
                "action": "Add compatible CLI flag aliases and self-disclosing usage on exit=2.",
                "detail": "layer=L2 | category=rigid_roadblock | skill=team-lead",
                "scope_triage": {"emit_proposal": True},
            },
            {
                "id": "skill-retro-l1-unwired-my-designer-unwired-a11y",
                "ref": "retro://skill-retro/l1-unwired/my-designer/unwired-a11y.md",
                "metric_id": "skill-retro-l1-unwired-my-designer",
                "skill": "my-designer",
                "layer": "L1",
                "category": "unwired_disconnected_drawer",
                "priority": "P1",
                "title": "[Skill Retro L1] 打通 `my-designer` 断连纠偏抽屉 `unwired-a11y.md`（报错 3 次但 0 读取）",
                "plain_reason": "`my-designer` 在 3 次失败中均未触发 `unwired-a11y.md` 读取，需在脚本报错中直链该抽屉",
                "action": "Link `references/unwired-a11y.md` directly in `verify_design_cases.py` stderr.",
                "detail": "layer=L1 | category=unwired_disconnected_drawer | skill=my-designer",
                "scope_triage": {"emit_proposal": True},
            },
        ],
        "observation_pool": [
            {
                "id": "obs-1",
                "skill": "aidash-metrics",
                "layer": "L1",
                "category": "dormant_scenario_drawer",
                "title": "Low-sample drawer held in observation pool",
            }
        ],
        "proposals_written": 2,
        "auto_proposed": [
            "skill-retro-l2-roadblock-team-lead-goal-supervisor-py",
            "skill-retro-l1-unwired-my-designer-unwired-a11y",
        ],
    }


class MulticaSkillsAutopilotTests(unittest.TestCase):
    def test_collect_snapshot_runs_pure_skill_retro_without_legacy_roi_ranking(self) -> None:
        snap = AUTOPILOT.collect_skill_retro_snapshot(
            skill_retro_fn=lambda **_kwargs: _sample_skill_retro_report(),
            top_n=3,
        )
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["schema"], "multica-skill-retro-collect/v2")
        self.assertEqual(snap["engine"], "skill-retro")
        self.assertEqual(snap["collected_date"], "2026-11-21")
        self.assertEqual(snap["dedup_key"], "skill-retro-autopilot-2026-11-21")
        self.assertEqual(snap["target_priority"]["multica_priority"], "medium")
        self.assertEqual(snap["target_priority"]["linear_priority_number"], 3)
        self.assertEqual(snap["target_owner"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
        self.assertIn("[P3 / Priority 3][Skill Retro]", snap["default_issue_title"])
        self.assertEqual(len(snap["top_findings"]), 2)
        self.assertEqual(snap["top_findings"][0]["skill"], "team-lead")
        self.assertEqual(snap["top_findings"][0]["layer"], "L2")
        self.assertTrue(snap["top_findings"][0]["remediation_commands"])
        # Legacy S-code tables must NOT be present as primary snapshot keys or markdown sections
        for legacy_key in ("S6_unpracticed_hotspots", "S7_greedy_hotspots", "S9_escape_hotspots", "S3_skill_health", "S12_tool_context_burn"):
            self.assertNotIn(legacy_key, snap)
        self.assertNotIn("ROI (`S10`)", snap["markdown_brief"])
        self.assertIn("4-Layer Progressive Skill Retro Diagnostic", snap["markdown_brief"])
        self.assertIn("multica-skills-autopilot:skill-retro-autopilot-2026-11-21", snap["markdown_brief"])
        self.assertIn("Priority 3 (`medium`)", snap["markdown_brief"])
        self.assertIn("lee@multica.local", snap["markdown_brief"])
        issue_brief = AUTOPILOT._load_issue_brief()
        dz_report = issue_brief.verify_dual_zone_issue(snap["markdown_brief"])
        self.assertTrue(dz_report["ok"], dz_report)
        self.assertLessEqual(dz_report["human_zone_lines"], 15)
        self.assertEqual(dz_report["cursor"], "skill-retro-autopilot-2026-11-21")

    def test_collect_snapshot_invokes_real_pure_skill_retro_cli_on_synthetic_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            warehouse = root / "warehouse.db"
            copilot = root / "session-store.db"
            sessions_dir = root / "sessions"
            sessions_dir.mkdir()
            usage_jsonl = root / "usage.jsonl"
            usage_jsonl.write_text("", encoding="utf-8")
            handoff_root = root / "handoff"
            handoff_root.mkdir()
            skills_root = root / "skills"
            dev_root = root / "dev"
            dev_root.mkdir()
            skill_dir = skills_root / "alpha"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "references").mkdir()
            (skill_dir / ".claude-plugin").mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha skill\n---\n# alpha\n", encoding="utf-8")
            (skill_dir / ".claude-plugin" / "plugin.json").write_text('{"name":"alpha","description":"Alpha","category":"workflow"}\n', encoding="utf-8")
            (skill_dir / "scripts" / "check.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            now_iso = (datetime.now(CST) - timedelta(hours=1)).replace(microsecond=0)
            for idx in (1, 2):
                sdir = sessions_dir / f"s-{idx}"
                sdir.mkdir()
                evs = [
                    {"type": "session.start", "timestamp": now_iso.isoformat(), "data": {"selectedModel": "gpt-5.4"}},
                    {"type": "skill.invoked", "timestamp": (now_iso + timedelta(seconds=1)).isoformat(), "data": {"name": "alpha", "path": "skills/alpha/SKILL.md"}},
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now_iso + timedelta(seconds=2)).isoformat(),
                        "data": {"toolCallId": f"c-{idx}", "toolName": "bash", "arguments": {"command": f"python3 {skills_root}/alpha/scripts/check.py --bad"}},
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now_iso + timedelta(seconds=3)).isoformat(),
                        "data": {"toolCallId": f"c-{idx}", "result": {"content": "<shellId: 1 completed with exit code 2>"}},
                    },
                ]
                (sdir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

            sqlite3.connect(warehouse).close()
            sqlite3.connect(copilot).close()

            report = _load_autopilot_module().collect_skills_roi_snapshot(
                warehouse_db=warehouse,
                copilot_db=copilot,
                copilot_sessions_dir=sessions_dir,
                usage_jsonl=usage_jsonl,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                top_n=5,
            )
            self.assertEqual(report["engine"], "skill-retro")
            self.assertEqual(report["summary"]["sample_size"], 2)
            self.assertGreaterEqual(report["summary"]["rigid_roadblock_scripts_count"], 1)
            row = next(item for item in report["top_findings"] if item["skill"] == "alpha")
            self.assertEqual(row["layer"], "L2")
            self.assertEqual(row["category"], "rigid_roadblock")

    def test_file_actionable_issue_creates_p3_assigned_to_owner_and_deduplicates(self) -> None:
        snap = AUTOPILOT.collect_skill_retro_snapshot(
            skill_retro_fn=lambda **_kwargs: _sample_skill_retro_report()
        )
        recorded_calls: list[tuple[list[str], str | None]] = []
        existing_issues: list[dict] = []

        def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
            recorded_calls.append((args, stdin_text))
            if args[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"issues": existing_issues}), "")
            if args[:2] == ["issue", "create"]:
                created = {
                    "id": "issue-uuid-001",
                    "identifier": "MUL-101",
                    "title": args[args.index("--title") + 1],
                    "priority": args[args.index("--priority") + 1],
                    "status": args[args.index("--status") + 1],
                    "assignee_id": args[args.index("--assignee-id") + 1],
                    "description": stdin_text or "",
                }
                existing_issues.append(created)
                return subprocess.CompletedProcess(args, 0, json.dumps(created), "")
            raise AssertionError(f"Unexpected command: {args}")

        first = AUTOPILOT.file_actionable_issue(
            snap,
            analysis_markdown="### Root Cause\n- Soften CLI roadblock and wire disconnected drawer.",
            runner=fake_runner,
        )
        self.assertEqual(first["action"], "created")
        self.assertEqual(first["issue"]["priority"], "medium")
        self.assertEqual(first["issue"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
        self.assertEqual(first["issue"]["status"], "backlog")
        self.assertIn("Agent Root-Cause Synthesis & Evolution Plan", first["issue"]["description"])

        second = AUTOPILOT.file_actionable_issue(
            snap,
            analysis_markdown="### Root Cause\n- Soften CLI roadblock and wire disconnected drawer.",
            runner=fake_runner,
        )
        self.assertEqual(second["action"], "deduplicated_existing")
        self.assertEqual(second["issue"]["id"], "issue-uuid-001")
        create_calls = [c for c, _ in recorded_calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(create_calls), 1)

    def test_setup_multica_autopilot_updates_legacy_4e06bf2a_in_place_without_duplicates(self) -> None:
        state: dict[str, list[dict]] = {
            "autopilots": [
                {
                    "id": "4e06bf2a-5842-4b3a-928f-fcde3bea7c41",
                    "title": "Daily Skills ROI Diagnostic Autopilot (Auto-Fix + Daily Brief Digest)",
                    "description": 'python3 "$HOME/Development/user-skills/skills/m2-dispatcher/scripts/multica_loop_runner.py" run-loop --loop "skills-roi-diagnostic" --json',
                    "mode": "run_only",
                    "agent_id": AUTOPILOT.DEFAULT_AGENT_ID,
                }
            ],
            "triggers": [
                {
                    "id": "ad0a2d4e-c902-433f-99cb-6bc318561454",
                    "kind": "schedule",
                    "cron_expression": "20 8 * * *",
                    "timezone": "Asia/Shanghai",
                    "label": "Daily CST Morning Skills ROI Diagnostic",
                }
            ],
        }

        def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
            del stdin_text
            if args[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"autopilots": state["autopilots"]}), "")
            if args[:2] == ["autopilot", "create"]:
                raise AssertionError("Must update 4e06bf2a in-place, never create a duplicate autopilot!")
            if args[:2] == ["autopilot", "update"]:
                ap = state["autopilots"][0]
                self.assertEqual(args[2], "4e06bf2a-5842-4b3a-928f-fcde3bea7c41")
                ap["title"] = args[args.index("--title") + 1]
                ap["description"] = args[args.index("--description") + 1]
                return subprocess.CompletedProcess(args, 0, json.dumps(ap), "")
            if args[:2] == ["autopilot", "trigger-list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"triggers": state["triggers"]}), "")
            if args[:2] == ["autopilot", "trigger-update"]:
                self.assertEqual(args[2], "4e06bf2a-5842-4b3a-928f-fcde3bea7c41")
                self.assertEqual(args[3], "ad0a2d4e-c902-433f-99cb-6bc318561454")
                trig = state["triggers"][0]
                trig["cron_expression"] = args[args.index("--cron") + 1]
                trig["timezone"] = args[args.index("--timezone") + 1]
                trig["label"] = args[args.index("--label") + 1]
                return subprocess.CompletedProcess(args, 0, json.dumps(trig), "")
            if args[:2] == ["autopilot", "trigger"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"run_id": "run-uuid-789"}), "")
            raise AssertionError(f"Unexpected command: {args}")

        updated = AUTOPILOT.setup_multica_autopilot(trigger_now=True, runner=fake_runner)
        self.assertEqual(updated["autopilot_action"], "updated")
        self.assertEqual(updated["autopilot_id"], "4e06bf2a-5842-4b3a-928f-fcde3bea7c41")
        self.assertEqual(updated["trigger_action"], "updated")
        self.assertEqual(len(state["autopilots"]), 1)
        self.assertIn("Skill Progressive Retro", state["autopilots"][0]["title"])
        self.assertIn("skills/team-lead/scripts/multica_loop_runner.py", state["autopilots"][0]["description"])
        self.assertNotIn("skills/m2-dispatcher/scripts/multica_loop_runner.py", state["autopilots"][0]["description"])
        self.assertEqual(state["triggers"][0]["cron_expression"], "20 4 * * *")

        second = AUTOPILOT.setup_multica_autopilot(trigger_now=False, runner=fake_runner)
        self.assertEqual(second["autopilot_action"], "updated")
        self.assertEqual(second["trigger_action"], "unchanged")
        self.assertIsNone(second["triggered_run"])

    def test_cli_collect_and_run_once_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "collect.json"
            analysis_md = Path(tmp) / "analysis.md"
            analysis_md.write_text("- Custom agent finding for `team-lead`.\n", encoding="utf-8")

            with mock.patch.object(
                AUTOPILOT,
                "_invoke_pure_skill_retro_cli",
                return_value=_sample_skill_retro_report(),
            ):
                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    rc = AUTOPILOT.main(["collect", "--output", str(out_json)])
                self.assertEqual(rc, 0)
                self.assertTrue(out_json.is_file())
                parsed = json.loads(out_json.read_text(encoding="utf-8"))
                self.assertEqual(parsed["engine"], "skill-retro")
                self.assertEqual(parsed["top_findings"][0]["skill"], "team-lead")
                self.assertTrue(parsed["skill_retro"]["enabled"])

                buf2 = io.StringIO()
                with mock.patch("sys.stdout", buf2):
                    rc2 = AUTOPILOT.main(
                        [
                            "run-once",
                            "--output",
                            str(out_json),
                            "--analysis-file",
                            str(analysis_md),
                            "--dry-run",
                        ]
                    )
                self.assertEqual(rc2, 0)
                run_res = json.loads(buf2.getvalue())
                self.assertTrue(run_res["ok"])
                self.assertEqual(run_res["issue_result"]["action"], "dry_run")
                self.assertEqual(run_res["issue_result"]["priority"], "medium")
                self.assertEqual(run_res["issue_result"]["assignee_id"], AUTOPILOT.DEFAULT_OWNER_ID)
                self.assertIn("Custom agent finding", run_res["issue_result"]["description"])

    def test_skill_retro_cli_wiring_and_cohort_watermark_skip_no_delta(self) -> None:
        sample_skill_retro = _sample_skill_retro_report()

        with tempfile.TemporaryDirectory() as tmp:
            wm_path = Path(tmp) / "skill-retro-watermark.json"
            collect_out = Path(tmp) / "collect.json"
            propose_calls: list[bool] = []
            multica_calls: list[list[str]] = []

            def fake_skill_retro(**kwargs):
                propose_calls.append(bool(kwargs.get("auto_propose")))
                return sample_skill_retro

            def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
                multica_calls.append(list(args))
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(args, 0, '{"issues": []}', "")
                if args[:2] == ["issue", "create"]:
                    return subprocess.CompletedProcess(args, 0, '{"id": "mul-sr-1", "identifier": "TIA-77"}', "")
                return subprocess.CompletedProcess(args, 0, "{}", "")

            first_run = AUTOPILOT.run_once_pipeline(
                collect_output=collect_out,
                skill_retro_auto_propose=True,
                skill_retro_watermark_path=wm_path,
                skip_if_no_cohort_delta=True,
                skill_retro_fn=fake_skill_retro,
                runner=fake_runner,
            )
            self.assertTrue(first_run["ok"])
            self.assertEqual(first_run["issue_result"]["action"], "created")
            self.assertEqual(propose_calls, [False, True])
            saved_snap = json.loads(collect_out.read_text(encoding="utf-8"))
            self.assertTrue(saved_snap["cohort_gate"]["has_delta"])
            self.assertTrue(saved_snap["cohort_gate"]["slow_flywheel_ready"])
            self.assertEqual(saved_snap["summary"]["skill_retro_proposals_count"], 2)
            self.assertIn("4-Layer Progressive Skill Retro Diagnostic", saved_snap["markdown_brief"])
            dz = AUTOPILOT._load_issue_brief().verify_dual_zone_issue(saved_snap["markdown_brief"])
            self.assertTrue(dz["ok"], dz)

            # Second scheduled tick with identical cohort/findings skips at 0 token cost without auto-proposing or calling Multica
            propose_calls.clear()
            multica_calls.clear()
            second_run = AUTOPILOT.run_once_pipeline(
                collect_output=collect_out,
                skill_retro_auto_propose=True,
                skill_retro_watermark_path=wm_path,
                skip_if_no_cohort_delta=True,
                skill_retro_fn=fake_skill_retro,
                runner=fake_runner,
            )
            self.assertEqual(second_run["status"], "SKIPPED_NO_DELTA")
            self.assertEqual(second_run["token_cost"], 0)
            self.assertEqual(propose_calls, [False])
            self.assertEqual(multica_calls, [])

    def test_l3_wild_pattern_cli_command_sanitization_and_failed_publish_watermark_retry(self) -> None:
        l3_retro = {
            "ok": True,
            "schema": "skill-retro-diagnostic/v1",
            "sample_size": 58,
            "summary": {"wild_patterns_count": 1, "slow_flywheel_ready": True},
            "proposals": [
                {
                    "id": "skill-retro-l3-wild-code-review-99aa11bb",
                    "skill": "code-review",
                    "layer": "L3",
                    "category": "wild_pattern_crystallization",
                    "priority": "P3",
                    "title": "[Skill Retro L3] 柔性沉淀高频手工探针 `git diff --stat --cached`（重复 7 次）",
                    "plain_reason": "跨 6 个会话重复手敲 `git diff --stat --cached` 共 7 次，适合沉淀为只采数不代拍板的柔性脚本",
                    "action": "Crystallize read-only fact collector subcommand under `skills/code-review/scripts/`.",
                    "detail": "layer=L3 | category=wild_pattern_crystallization | pattern=`git diff --stat --cached`",
                    "scope_triage": {"emit_proposal": True},
                }
            ],
            "observation_pool": [],
            "proposals_written": 1,
            "auto_proposed": ["skill-retro-l3-wild-code-review-99aa11bb"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            wm_path = Path(tmp) / "retry-wm.json"
            collect_out = Path(tmp) / "collect-l3.json"
            propose_calls: list[bool] = []
            fail_publish = True

            def fake_l3_retro(**kwargs):
                propose_calls.append(bool(kwargs.get("auto_propose")))
                return l3_retro

            def flaky_runner(args: list[str], *, stdin_text: str | None = None, **_kwargs) -> subprocess.CompletedProcess[str]:
                if fail_publish:
                    return subprocess.CompletedProcess(args, 1, "", "Multica server unavailable")
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(args, 0, '{"issues": []}', "")
                if args[:2] == ["issue", "create"]:
                    return subprocess.CompletedProcess(args, 0, '{"id": "mul-ok-2", "identifier": "TIA-88"}', "")
                return subprocess.CompletedProcess(args, 0, "{}", "")

            with self.assertRaises(RuntimeError):
                AUTOPILOT.run_once_pipeline(
                    collect_output=collect_out,
                    skill_retro_auto_propose=True,
                    skill_retro_watermark_path=wm_path,
                    skip_if_no_cohort_delta=True,
                    skill_retro_fn=fake_l3_retro,
                    runner=flaky_runner,
                )
            snap_l3 = json.loads(collect_out.read_text(encoding="utf-8"))
            dz_res = AUTOPILOT._load_issue_brief().verify_dual_zone_issue(snap_l3["markdown_brief"])
            self.assertTrue(dz_res["ok"], dz_res)
            self.assertIn("git diff --stat --cached", snap_l3["markdown_brief"].split("<details>", 1)[1])
            self.assertNotIn("git diff --stat", snap_l3["markdown_brief"].split("<details>", 1)[0])
            self.assertEqual(propose_calls, [False, True])

            fail_publish = False
            propose_calls.clear()
            recovered = AUTOPILOT.run_once_pipeline(
                collect_output=collect_out,
                skill_retro_auto_propose=True,
                skill_retro_watermark_path=wm_path,
                skip_if_no_cohort_delta=True,
                skill_retro_fn=fake_l3_retro,
                runner=flaky_runner,
            )
            self.assertEqual(recovered["issue_result"]["action"], "created")
            self.assertEqual(propose_calls, [False])


if __name__ == "__main__":
    unittest.main()
