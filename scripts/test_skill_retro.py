#!/usr/bin/env python3
"""Focused unit tests for pure 4-Layer Progressive Skill Retro (`skill-retro`) & M1->Writer->Reviewer brief emission."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


def _load(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load("test_skill_retro_engine_mod", "skills/retro-loop/scripts/retro_story_engine.py")
FEEDBACK_GUARD = _load("test_skill_retro_feedback_guard_mod", "skills/team-lead/scripts/feedback_guard.py")


def _write_skill_pkg(
    skills_dir: Path,
    name: str,
    *,
    skill_md_extra: str = "",
    refs: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
) -> Path:
    pkg = skills_dir / name
    (pkg / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Synthetic skill {name}\n---\n# {name}\n{skill_md_extra}\n",
        encoding="utf-8",
    )
    (pkg / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "description": f"Synthetic {name}", "category": "workflow"}) + "\n",
        encoding="utf-8",
    )
    if refs:
        refs_dir = pkg / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        for rel, body in refs.items():
            target = refs_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
    if scripts:
        sc_dir = pkg / "scripts"
        sc_dir.mkdir(parents=True, exist_ok=True)
        for rel, body in scripts.items():
            target = sc_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
    return pkg


class SkillRetroDiagnosticTests(unittest.TestCase):
    def _build_synthetic_workspace(self, root: Path) -> dict[str, Path]:
        skills_root = root / "user-skills" / "skills"
        dev_root = root / "dev"
        dev_root.mkdir(parents=True, exist_ok=True)
        handoff_root = dev_root / "handoff"
        handoff_root.mkdir(parents=True, exist_ok=True)
        sessions_dir = root / "copilot-sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        usage_jsonl = root / "usage.jsonl"
        trace_export = root / "traces.jsonl"
        warehouse_db = root / "warehouse.db"
        copilot_db = root / "session-store.db"
        proposals_path = root / "proposals.jsonl"
        roi_snapshot_path = root / "skills-roi.json"

        # 1. Synthetic skills covering L0, L1, L2, L3 scenarios
        _write_skill_pkg(
            skills_root,
            "my-designer",
            skill_md_extra="See `references/hot-checklist.md`, `references/on-demand-fix.md`, and `references/unwired-a11y.md`.",
            refs={
                "hot-checklist.md": "# Hot Checklist\nRead frequently.\n",
                "on-demand-fix.md": "# On-Demand Fix\nRead after exit=1.\n",
                "unwired-a11y.md": "# Unwired A11y\nNever linked in script stderr.\n",
                "deadwood-legacy.md": "# Deadwood\n0 hits across >=5 UI sessions.\n",
            },
            scripts={
                "verify_design_cases.py": (
                    "#!/usr/bin/env python3\n"
                    "# Self-disclosing pointer: see references/on-demand-fix.md\n"
                ),
                "ceremonial_check.py": "#!/usr/bin/env python3\nprint('ok')\n",
            },
        )
        _write_skill_pkg(
            skills_root,
            "aidash-metrics",
            refs={"rare-migration.md": "# Rare Migration\nScenario occurred only once; must NOT be deadwood.\n"},
            scripts={"inspect_aidata_pipeline.py": "#!/usr/bin/env python3\n"},
        )
        _write_skill_pkg(
            skills_root,
            "team-lead",
            refs={"ledger.md": "# Ledger\n"},
            scripts={
                "ci-monitor.py": "#!/usr/bin/env python3\n",
                "goal-queue.py": "#!/usr/bin/env python3\n",
            },
        )

        now = datetime.now(CST).replace(microsecond=0)

        def write_copilot_session(sid: str, events: list[dict]) -> None:
            sdir = sessions_dir / sid
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        fake_user_home = "/" + "Users/alice"
        # 2. 6 UI sessions exercising L0, L1, L2, L3
        for idx in range(1, 5):
            t0 = now - timedelta(hours=10 - idx)
            evs: list[dict] = [
                {"type": "session.start", "timestamp": t0.isoformat(), "data": {"selectedModel": "gpt-5.4"}},
                {
                    "type": "skill.invoked",
                    "timestamp": (t0 + timedelta(seconds=1)).isoformat(),
                    "data": {"name": "my-designer", "path": "skills/my-designer/SKILL.md"},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                    "data": {
                        "toolCallId": f"view-hot-{idx}",
                        "toolName": "view",
                        "arguments": {"path": str(skills_root / "my-designer/references/hot-checklist.md")},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (t0 + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": f"view-hot-{idx}", "result": {"content": "ok"}},
                },
            ]
            if idx == 1:
                # fail (exit=1) -> read on-demand-fix.md -> edit -> pass (exit=0)
                evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=5)).isoformat(),
                            "data": {
                                "toolCallId": "bash-fail-1",
                                "toolName": "bash",
                                "arguments": {
                                    "command": "python3 skills/my-designer/scripts/verify_design_cases.py audit-code View.swift"
                                },
                            },
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=7)).isoformat(),
                            "data": {
                                "toolCallId": "bash-fail-1",
                                "shellExecution": {"exitCode": 1},
                                "result": {"content": "violation"},
                            },
                        },
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=9)).isoformat(),
                            "data": {
                                "toolCallId": "view-ondemand-1",
                                "toolName": "view",
                                "arguments": {"path": "skills/my-designer/references/on-demand-fix.md"},
                            },
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=10)).isoformat(),
                            "data": {"toolCallId": "view-ondemand-1", "result": {"content": "fix guide"}},
                        },
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=12)).isoformat(),
                            "data": {"toolCallId": "edit-1", "toolName": "edit", "arguments": {"path": "App/DashboardView.swift"}},
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=13)).isoformat(),
                            "data": {"toolCallId": "edit-1", "result": {"content": "updated"}},
                        },
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=15)).isoformat(),
                            "data": {
                                "toolCallId": "bash-pass-1",
                                "toolName": "bash",
                                "arguments": {
                                    "command": "python3 skills/my-designer/scripts/verify_design_cases.py audit-code View.swift"
                                },
                            },
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=17)).isoformat(),
                            "data": {
                                "toolCallId": "bash-pass-1",
                                "shellExecution": {"exitCode": 0},
                                "result": {"content": "ok"},
                            },
                        },
                    ]
                )
            if idx in (2, 3):
                # Rigid roadblock exit=2 with user path that must be redacted
                evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=6)).isoformat(),
                            "data": {
                                "toolCallId": f"bash-e2-{idx}",
                                "toolName": "bash",
                                "arguments": {
                                    "command": f"python3 skills/my-designer/scripts/verify_design_cases.py --unknown-flag {fake_user_home}/secret_dir"
                                },
                            },
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=7)).isoformat(),
                            "data": {
                                "toolCallId": f"bash-e2-{idx}",
                                "result": {"content": "unrecognized arguments\n<shellId: 1 completed with exit code 2>"},
                            },
                        },
                    ]
                )
            evs.extend(
                [
                    {
                        "type": "tool.execution_start",
                        "timestamp": (t0 + timedelta(seconds=20)).isoformat(),
                        "data": {
                            "toolCallId": f"bash-cer-{idx}",
                            "toolName": "bash",
                            "arguments": {"command": "python3 skills/my-designer/scripts/ceremonial_check.py"},
                        },
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (t0 + timedelta(seconds=21)).isoformat(),
                        "data": {"toolCallId": f"bash-cer-{idx}", "shellExecution": {"exitCode": 0}, "result": {"content": "ok"}},
                    },
                    {
                        "type": "tool.execution_start",
                        "timestamp": (t0 + timedelta(seconds=25)).isoformat(),
                        "data": {
                            "toolCallId": f"bash-wild-{idx}",
                            "toolName": "bash",
                            "arguments": {"command": "sqlite3 ~/Development/AIDash/aidata/L3_merge/warehouse.db 'SELECT count(*) FROM fact_turn'"},
                        },
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (t0 + timedelta(seconds=26)).isoformat(),
                        "data": {"toolCallId": f"bash-wild-{idx}", "shellExecution": {"exitCode": 0}, "result": {"content": "42"}},
                    },
                ]
            )
            write_copilot_session(f"sess-ui-{idx}", evs)

        for idx in (5, 6):
            t0 = now - timedelta(hours=3, minutes=idx * 10)
            evs = [
                {"type": "session.start", "timestamp": t0.isoformat(), "data": {"selectedModel": "gpt-5.4"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (t0 + timedelta(seconds=2)).isoformat(),
                    "data": {"toolCallId": f"edit-miss-{idx}", "toolName": "edit", "arguments": {"path": f"VitalStride/CompactStudioView{idx}.swift"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (t0 + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": f"edit-miss-{idx}", "result": {"content": "edited"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (t0 + timedelta(seconds=5)).isoformat(),
                    "data": {
                        "toolCallId": f"bash-wild-{idx}",
                        "toolName": "bash",
                        "arguments": {"command": "sqlite3 ~/Development/AIDash/aidata/L3_merge/warehouse.db 'SELECT goal_id FROM fact_turn'"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (t0 + timedelta(seconds=6)).isoformat(),
                    "data": {"toolCallId": f"bash-wild-{idx}", "shellExecution": {"exitCode": 0}, "result": {"content": "g1"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (t0 + timedelta(seconds=10)).isoformat(),
                    "data": {"toolCallId": f"verify-a-{idx}", "toolName": "bash", "arguments": {"command": "./scripts/verify --all"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (t0 + timedelta(seconds=205)).isoformat(),
                    "data": {"toolCallId": f"verify-a-{idx}", "shellExecution": {"exitCode": 0}, "result": {"content": "passed"}},
                },
            ]
            if idx == 5:
                evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (t0 + timedelta(seconds=210)).isoformat(),
                            "data": {"toolCallId": "verify-b-5", "toolName": "bash", "arguments": {"command": "./scripts/verify --all"}},
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (t0 + timedelta(seconds=400)).isoformat(),
                            "data": {"toolCallId": "verify-b-5", "shellExecution": {"exitCode": 0}, "result": {"content": "passed"}},
                        },
                    ]
                )
            write_copilot_session(f"sess-ui-{idx}", evs)

        usage_rows = [
            {"kind": "skill_turn", "session_id": "sess-empty-1", "ts": (now - timedelta(hours=2)).isoformat(), "skill": "team-lead", "tool_calls": []},
            {"kind": "skill_turn", "session_id": "sess-empty-2", "ts": (now - timedelta(hours=1)).isoformat(), "skill": "team-lead", "tool_calls": []},
            {"kind": "skill_turn", "session_id": "sess-aidata-1", "ts": (now - timedelta(hours=1)).isoformat(), "skill": "aidash-metrics", "tool_calls": ["Edit"]},
        ]
        usage_jsonl.write_text("\n".join(json.dumps(r) for r in usage_rows) + "\n", encoding="utf-8")

        trace_doc = {
            "trace_id": "a" * 32,
            "entity_id": "sess-trace-1",
            "start_ns": int((now - timedelta(minutes=40)).timestamp() * 1_000_000_000),
            "spans": [
                {
                    "category": "l2_script_exec",
                    "name": "script:ci-monitor.py",
                    "start_ns": int((now - timedelta(minutes=40)).timestamp() * 1_000_000_000),
                    "attributes": {
                        "script.path": "skills/team-lead/scripts/ci-monitor.py",
                        "script.duration_ms": 210000,
                        "script.exit_code": 0,
                        "timing.measured": True,
                    },
                    "input_payload": {"command": "python3 skills/team-lead/scripts/ci-monitor.py pr 123"},
                    "output_payload": {"duration_ms": 210000, "exit_code": 0, "timing.measured": True},
                }
            ],
        }
        trace_export.write_text(json.dumps(trace_doc) + "\n", encoding="utf-8")

        with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
            conn.execute(
                "CREATE TABLE fact_gtt_task_run (goal_id TEXT, task_id TEXT, role_name TEXT, round_no INTEGER, v5_verdict TEXT, skills_used TEXT, cst_day TEXT)"
            )
            conn.execute(
                "CREATE TABLE fact_gtt_event (goal_id TEXT, task_id TEXT, evt_type TEXT, note_preview TEXT, cst_day TEXT)"
            )
            day_str = now.date().isoformat()
            conn.executemany(
                "INSERT INTO fact_gtt_task_run VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("goal-healthy", "T1", "code_reviewer", 1, "FAIL", '["my-designer"]', day_str),
                    ("goal-healthy", "T1", "code_reviewer", 2, "PASS", '["my-designer"]', day_str),
                    ("goal-drift", "T2", "code_reviewer", 1, "FAIL", '["my-designer"]', day_str),
                    ("goal-drift", "T2", "code_reviewer", 2, "FAIL", '["my-designer"]', day_str),
                    ("goal-drift", "T2", "code_reviewer", 3, "FAIL", '["my-designer"]', day_str),
                ],
            )
            conn.execute(
                "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                ("goal-ui-escape", "T1", "REVIEW_FAIL", "[SPEC_GAP] skills/my-designer/SKILL.md hierarchy mismatch", day_str),
            )
            conn.commit()

        receipts_dir = handoff_root / "goal-ui-demo" / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        (receipts_dir / "preview-selection.json").write_text(
            json.dumps(
                {
                    "selectedVariantID": "v02",
                    "starredVariantIDs": ["v04"],
                    "rejectedVariantIDs": ["v03"],
                    "notesByVariant": {"v02": "Keep dense command cockpit hierarchy"},
                    "variants": {
                        "v01": {"title": "Calm Split"},
                        "v02": {"title": "Dense Cockpit"},
                        "v03": {"title": "Flat Cards"},
                        "v04": {"title": "Bold Spatial Canvas"},
                    },
                }
            ),
            encoding="utf-8",
        )

        return {
            "skills_root": skills_root,
            "dev_root": dev_root,
            "handoff_root": handoff_root,
            "sessions_dir": sessions_dir,
            "usage_jsonl": usage_jsonl,
            "trace_export": trace_export,
            "warehouse_db": warehouse_db,
            "copilot_db": copilot_db,
            "proposals_path": proposals_path,
            "roi_snapshot_path": roi_snapshot_path,
        }

    def test_four_layer_diagnosis_and_scope_triage_and_idempotent_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build_synthetic_workspace(Path(tmp))
            report = ENGINE.compute_skill_retro(
                warehouse_db=paths["warehouse_db"],
                copilot_db=paths["copilot_db"],
                copilot_sessions_dir=paths["sessions_dir"],
                usage_jsonl=paths["usage_jsonl"],
                trace_export_path=paths["trace_export"],
                handoff_root=paths["handoff_root"],
                skills_root=paths["skills_root"],
                dev_root=paths["dev_root"],
                auto_propose=True,
                proposals_path=paths["proposals_path"],
                local_roi_path=paths["roi_snapshot_path"],
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["schema"], "skill-retro-diagnostic/v1")

            # Layer 0: Trigger misses, 0-action loads, healthy R1->R2 vs directional drift
            l0 = report["layer0_direction_and_triggers"]
            miss_by_skill = {m["skill"]: m for m in l0["trigger_misses"]}
            self.assertEqual(miss_by_skill["my-designer"]["missed_sessions"], 2)
            empty_by_skill = {z["skill"]: z for z in l0["zero_action_loads"]}
            self.assertEqual(empty_by_skill["team-lead"]["zero_action_sessions"], 2)
            self.assertEqual(l0["healthy_convergence_count"], 1)
            self.assertGreaterEqual(l0["directional_drift_count"], 1)

            # Layer 1: Context-aware drawer metabolism (Counter-Case: rare aidash-metrics drawer is NOT deadwood)
            l1 = report["layer1_drawer_metabolism"]
            hot_paths = {(r["skill"], r["drawer"]) for r in l1["hot_drawers"]}
            ondemand_paths = {(r["skill"], r["drawer"]) for r in l1["on_demand_correction_drawers"]}
            unwired_paths = {(r["skill"], r["drawer"]) for r in l1["unwired_disconnected_drawers"]}
            deadwood_paths = {(r["skill"], r["drawer"]) for r in l1["true_zero_hit_deadwood"]}

            self.assertIn(("my-designer", "references/hot-checklist.md"), hot_paths)
            self.assertIn(("my-designer", "references/on-demand-fix.md"), ondemand_paths)
            self.assertIn(("my-designer", "references/unwired-a11y.md"), unwired_paths)
            self.assertIn(("my-designer", "references/deadwood-legacy.md"), deadwood_paths)
            self.assertNotIn(("aidash-metrics", "references/rare-migration.md"), deadwood_paths)
            self.assertGreaterEqual(l1["dormant_scenario_drawers_count"], 1)

            # Layer 2: True Catchers, Ceremonial Rubber-Stamps, Rigid Roadblocks (redacted), Latency Sinks
            l2 = report["layer2_script_efficacy_and_speed"]
            catchers = {(r["skill"], r["script"]) for r in l2["true_catchers"]}
            ceremonial = {(r["skill"], r["script"]): r for r in l2["ceremonial_rubber_stamps"]}
            roadblocks = {(r["skill"], r["script"]): r for r in l2["rigid_roadblocks"]}
            sinks = {(r["skill"], r["script"]): r for r in l2["latency_sinks"]}

            self.assertIn(("my-designer", "scripts/verify_design_cases.py"), catchers)
            self.assertGreater(ceremonial[("my-designer", "scripts/ceremonial_check.py")]["co_occurring_skill_escapes"], 0)

            rb = roadblocks[("my-designer", "scripts/verify_design_cases.py")]
            self.assertEqual(rb["exit_2_count"], 2)
            self.assertTrue(any("--unknown-flag" in s for s in rb["failing_command_samples"]))
            self.assertNotIn("/" + "Users/alice", json.dumps(rb))

            verify_sink = sinks[("repo-gate", "scripts/verify")]
            self.assertEqual(verify_sink["primary_bottleneck"], "serial_queuing")
            self.assertGreaterEqual(verify_sink["nested_rerun_calls"], 1)
            self.assertEqual(sinks[("team-lead", "scripts/ci-monitor.py")]["primary_bottleneck"], "external_wait")

            # Layer 3: Wild manual pattern >=5x, 2+1+1 taste harvest, Fast vs Slow flywheel guard
            l3 = report["layer3_wild_patterns_and_eval"]
            self.assertTrue(any("sqlite3 warehouse.db" in w["pattern_signature"] and w["occurrences"] >= 5 for w in l3["wild_patterns"]))
            self.assertEqual(l3["taste_and_eval_diversity"]["spectrum_2_1_1_compliant_goals"], 1)
            self.assertFalse(l3["dual_flywheel_guard"]["slow_flywheel_ready"])
            # Counter-Case: 1-call external_wait ci-monitor stays in observation_pool instead of spamming proposals
            self.assertTrue(any(p["category"] == "latency_sink" and p["skill"] == "team-lead" for p in report["observation_pool"]))

            # Proposals idempotency
            self.assertGreater(report["proposals_written"], 0)
            second = ENGINE.compute_skill_retro(
                warehouse_db=paths["warehouse_db"],
                copilot_db=paths["copilot_db"],
                copilot_sessions_dir=paths["sessions_dir"],
                usage_jsonl=paths["usage_jsonl"],
                trace_export_path=paths["trace_export"],
                handoff_root=paths["handoff_root"],
                skills_root=paths["skills_root"],
                dev_root=paths["dev_root"],
                auto_propose=True,
                proposals_path=paths["proposals_path"],
                mirror_roi_snapshot=False,
            )
            self.assertEqual(second["proposals_written"], 0)

    def test_emit_briefs_and_skill_filter_reuse_m1_writer_reviewer_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._build_synthetic_workspace(root)
            emit_handoff = root / "handoff-out"

            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "skills/retro-loop/scripts/retro_story_engine.py"),
                    "skill-retro",
                    "--skill",
                    "my-designer",
                    "--emit-briefs",
                    str(emit_handoff),
                    "--warehouse-db",
                    str(paths["warehouse_db"]),
                    "--copilot-db",
                    str(paths["copilot_db"]),
                    "--copilot-sessions-dir",
                    str(paths["sessions_dir"]),
                    "--usage-jsonl",
                    str(paths["usage_jsonl"]),
                    "--trace-export",
                    str(paths["trace_export"]),
                    "--handoff-root",
                    str(paths["handoff_root"]),
                    "--skills-root",
                    str(paths["skills_root"]),
                    "--dev-root",
                    str(paths["dev_root"]),
                    "--no-roi-mirror",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["skill_filter"], "my-designer")
            self.assertEqual(payload["sources_ingested"]["skills_in_inventory"], 1)

            emitted = payload["emitted_briefs"]
            self.assertEqual(emitted["role_slots_reused"], ["M1-TL", "writer", "reviewer"])
            self.assertEqual(emitted["new_agent_roles_created"], 0)
            self.assertEqual(emitted["count"], 1)

            brief_entry = emitted["briefs"][0]
            self.assertEqual(brief_entry["skill"], "my-designer")
            writer_brief_path = Path(brief_entry["writer_brief"])
            reviewer_brief_path = Path(brief_entry["reviewer_brief"])
            self.assertTrue(writer_brief_path.is_file())
            self.assertTrue(reviewer_brief_path.is_file())

            writer_text = writer_brief_path.read_text(encoding="utf-8")
            reviewer_text = reviewer_brief_path.read_text(encoding="utf-8")

            # Writer Brief must carry Owned Paths parseable by feedback_guard.py, exact exit=2 samples,
            # drawer vs scenario counts, latency breakdown, Scope Triage, and Self-Disclosing Error rules
            owned = FEEDBACK_GUARD.extract_owned_paths_from_brief(writer_text)
            self.assertIn("skills/my-designer/**", owned)
            self.assertIn("--unknown-flag", writer_text)
            self.assertNotIn("/" + "Users/alice", writer_text)
            self.assertIn("unwired-a11y.md", writer_text)
            self.assertIn("scenario_sessions=6", writer_text)
            self.assertIn("serial_queuing", writer_text)
            self.assertIn("Scope Triage", writer_text)
            self.assertIn("Self-Disclosing Error", writer_text)
            self.assertIn("Counter-Case Replay", writer_text)

            # Reviewer Brief must reuse standard `reviewer` slot and guard against over-correction & rigid hardcoding
            self.assertIn("Role Slot**: `reviewer`", reviewer_text)
            self.assertIn("zero new agent role types", reviewer_text)
            self.assertIn("Reject Global-Default Over-Correction", reviewer_text)
            self.assertIn("Reject Rigid Hardcoding", reviewer_text)
            self.assertIn("Positive + Counter-Case Replay", reviewer_text)
            self.assertIn("feedback_guard.py pre-review-check", reviewer_text)


if __name__ == "__main__":
    unittest.main()
