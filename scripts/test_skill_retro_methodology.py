#!/usr/bin/env python3
"""Regression suite for `skill-retro` Methodology v2 (`T1-skill-retro-v2`).

Covers Positive Cases and Counter-Cases for:
  1. Outcome-First Gate & Runtime Progressive Disclosure Exemption (`progressive_catch_count`,
     `R1 -> R2` convergence exemption, `wasted_preread_rate`, `l1_unwired_drawers` false-positive fix)
  2. Direct `warehouse.db` Fast Probe (`p50_ms`, `p90_ms`, `skill_escapes`, `l2_leaky_rubber_stamps`,
     `rubber_stamp_escapes`, and expanded `_DOMAIN_HINTS`)
  3. S2 Internal Reality Check (`internal_reality_check` AST `argparse` flag/subcommand/script drift,
     missing `references/*.md`, malformed model IDs like `gemini:4- argon`) & ACI Output Bloat (`>20KB`, `--summary`)
  4. S4 Eval Counter-Case Ratio (`eval_counter_case_ratio >= 0.25`), `eval_escape_gap`, and
     Rollup Receipt-to-Trace Grounding (`receipt_grounded`, `grounding_issues`, git SHA verification)
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


def _load_skill_retro():
    path = ROOT / "skills" / "skill-retro" / "scripts" / "skill_retro.py"
    spec = importlib.util.spec_from_file_location("test_skill_retro_meth_v2_mod", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


SKILL_RETRO = _load_skill_retro()


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
        json.dumps({"name": name, "description": f"Synthetic {name}", "version": "1.1.0", "category": "meta"}) + "\n",
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


class SkillRetroMethodologyV2Tests(unittest.TestCase):
    def test_progressive_disclosure_exemption_and_unwired_drawer_false_positive_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            sessions_dir = root / "sessions"
            warehouse_db = root / "warehouse.db"
            handoff_root.mkdir(parents=True, exist_ok=True)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            eval_cases_doc = json.dumps(
                {
                    "schema": "skill-eval-cases/v1",
                    "version": 1,
                    "updated": datetime.now(CST).date().isoformat(),
                    "active_cases": [
                        {"id": "EV-001", "title": "pos", "cmd": "python3 -c 'print(1)'", "expect_exit": 0},
                        {"id": "EV-002", "title": "neg", "negative_cmd": "python3 -c 'raise SystemExit(2)'", "expect_fail_exit": 2},
                    ],
                }
            )

            # 1) `healthy-skill`: has an unlinked reference drawer in SKILL.md, 0 domain failures,
            #    R1->R2 convergence in warehouse.db, and exit=1 -> read drawer -> edit -> exit=0 in session.
            _write_skill_pkg(
                skills_root,
                "healthy-skill",
                skill_md_extra="See `references/on-demand-guide.md` and `references/extra-notes.md`.",
                refs={
                    "on-demand-guide.md": "# On-Demand Guide\n",
                    "extra-notes.md": "# Extra Notes\n",
                    "eval-cases.json": eval_cases_doc,
                },
                scripts={
                    "check_tool.py": (
                        "#!/usr/bin/env python3\n"
                        "import argparse\n"
                        "# Self-disclosing pointer: see references/on-demand-guide.md\n"
                        "p = argparse.ArgumentParser()\n"
                        "p.add_argument('--strict', action='store_true')\n"
                    )
                },
            )

            # 2) `noisy-skill`: has r3+ failure, cold-start greedy pre-read of 3 drawers before any action,
            #    and domain failure with an unwired drawer (0 reads, not linked in script).
            _write_skill_pkg(
                skills_root,
                "noisy-skill",
                skill_md_extra="See `references/d1.md`, `references/d2.md`, `references/d3.md`, `references/unwired-rules.md`.",
                refs={
                    "d1.md": "# Drawer 1\n",
                    "d2.md": "# Drawer 2\n",
                    "d3.md": "# Drawer 3\n",
                    "unwired-rules.md": "# Unwired Rules\n",
                    "eval-cases.json": eval_cases_doc,
                },
                scripts={
                    "lint_noisy.py": (
                        "#!/usr/bin/env python3\n"
                        "import argparse\n"
                        "p = argparse.ArgumentParser()\n"
                        "p.add_argument('--check', action='store_true')\n"
                    )
                },
            )

            now = datetime.now(CST).replace(microsecond=0)

            # Session 1 for `healthy-skill`: exit=1 -> read `on-demand-guide.md` -> edit -> exit=0
            s1_dir = sessions_dir / "sess-healthy-1"
            s1_dir.mkdir(parents=True, exist_ok=True)
            s1_events = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "healthy-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {
                        "toolCallId": "c1",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/healthy-skill/scripts/check_tool.py --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "c1", "shellExecution": {"exitCode": 1}, "result": {"content": "failed"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {
                        "toolCallId": "v1",
                        "toolName": "view",
                        "arguments": {"path": "skills/healthy-skill/references/on-demand-guide.md"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "v1", "result": {"content": "guide"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=6)).isoformat(),
                    "data": {"toolCallId": "e1", "toolName": "edit", "arguments": {"path": "src/app.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=7)).isoformat(),
                    "data": {"toolCallId": "e1", "result": {"content": "ok"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=8)).isoformat(),
                    "data": {
                        "toolCallId": "c2",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/healthy-skill/scripts/check_tool.py --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=9)).isoformat(),
                    "data": {"toolCallId": "c2", "shellExecution": {"exitCode": 0}, "result": {"content": "passed"}},
                },
            ]
            (s1_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in s1_events) + "\n", encoding="utf-8")

            # Session 2 for `noisy-skill`: cold-start greedy read of 3 drawers before any script/edit, then exit=1 without reading unwired-rules.md
            s2_dir = sessions_dir / "sess-noisy-1"
            s2_dir.mkdir(parents=True, exist_ok=True)
            s2_events = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "noisy-skill"}},
            ]
            for idx_d, d_name in enumerate(("d1.md", "d2.md", "d3.md"), 1):
                s2_events.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (now + timedelta(seconds=1 + idx_d)).isoformat(),
                            "data": {
                                "toolCallId": f"vd-{idx_d}",
                                "toolName": "view",
                                "arguments": {"path": f"skills/noisy-skill/references/{d_name}"},
                            },
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (now + timedelta(seconds=1 + idx_d)).isoformat(),
                            "data": {"toolCallId": f"vd-{idx_d}", "result": {"content": "drawer"}},
                        },
                    ]
                )
            s2_events.extend(
                [
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now + timedelta(seconds=10)).isoformat(),
                        "data": {
                            "toolCallId": "cn-1",
                            "toolName": "bash",
                            "arguments": {"command": "python3 skills/noisy-skill/scripts/lint_noisy.py --check"},
                        },
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now + timedelta(seconds=11)).isoformat(),
                        "data": {"toolCallId": "cn-1", "shellExecution": {"exitCode": 1}, "result": {"content": "error"}},
                    },
                ]
            )
            (s2_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in s2_events) + "\n", encoding="utf-8")

            day_str = now.date().isoformat()
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                conn.execute(
                    "CREATE TABLE fact_gtt_task_run (goal_id TEXT, task_id TEXT, role_name TEXT, round_no INTEGER, v5_verdict TEXT, skills_used TEXT, cst_day TEXT)"
                )
                conn.execute(
                    "CREATE TABLE fact_gtt_event (goal_id TEXT, task_id TEXT, evt_type TEXT, note_preview TEXT, cst_day TEXT)"
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_task_run VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("g-conv", "T1", "code_reviewer", 1, "FAIL", '["healthy-skill"]', day_str),
                        ("g-conv", "T1", "code_reviewer", 2, "PASS", '["healthy-skill"]', day_str),
                        ("g-r2only", "T_R2", "code_reviewer", 1, "FAIL", '["healthy-skill"]', day_str),
                        ("g-r2only", "T_R2", "code_reviewer", 2, "FAIL", '["healthy-skill"]', day_str),
                        ("g-drift", "T2", "code_reviewer", 1, "FAIL", '["noisy-skill"]', day_str),
                        ("g-drift", "T2", "code_reviewer", 2, "FAIL", '["noisy-skill"]', day_str),
                        ("g-drift", "T2", "code_reviewer", 3, "FAIL", '["noisy-skill"]', day_str),
                    ],
                )
                # Matching R1 REVIEW_FAIL event on a converged task (g-conv, T1) must NOT be penalized as an escape!
                conn.execute(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                    ("g-conv", "T1", "REVIEW_FAIL", "[IMPL_BUG] skills/healthy-skill/SKILL.md R1 finding resolved in R2", day_str),
                )
                conn.commit()

            # Deep probe with custom sources
            probe_res = SKILL_RETRO.probe_skills(
                warehouse_db=warehouse_db,
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            by_sk = {s["skill"]: s for s in probe_res["skills"]}

            # Positive Case: `healthy-skill` has progressive_catch_count=1 and healthy_r1_to_r2_convergences=1,
            # 0 directional drifts (even with R2-only failure and R1 REVIEW_FAIL on converged task), and 0 penalty!
            h_sig = by_sk["healthy-skill"]["signals"]
            self.assertEqual(h_sig["progressive_catch_count"], 1)
            self.assertEqual(h_sig["healthy_r1_to_r2_convergences"], 1)
            self.assertEqual(h_sig["l0_directional_drifts"], 0)
            self.assertEqual(h_sig["skill_escapes"], 0)
            self.assertEqual(h_sig["domain_failures"], 0)
            self.assertEqual(h_sig["wasted_preread_rate"], 0.0)
            self.assertEqual(by_sk["healthy-skill"]["priority_score"], 0.0)

            # Counter-Case: `noisy-skill` has r3+ drift, wasted_preread_rate=1.0, and unwired drawer (`unwired-rules.md`)
            n_sig = by_sk["noisy-skill"]["signals"]
            self.assertEqual(n_sig["l0_directional_drifts"], 1)
            self.assertEqual(n_sig["wasted_preread_sessions"], 1)
            self.assertEqual(n_sig["wasted_preread_rate"], 1.0)
            self.assertGreaterEqual(n_sig["l1_unwired_drawers"], 1)
            self.assertGreater(by_sk["noisy-skill"]["priority_score"], 0.0)

            # Canonical Fast-Path (`fast=True` without explicit source overrides) must also compute runtime signals
            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", warehouse_db),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", root / "none.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", root / "none-prop.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", root / "none-roi.json"),
            ):
                fast_canonical = SKILL_RETRO.probe_skills(
                    handoff_root=handoff_root,
                    skills_root=skills_root,
                    dev_root=dev_root,
                    fast=True,
                )
                fast_by_sk = {s["skill"]: s for s in fast_canonical["skills"]}
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["progressive_catch_count"], 1)
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["l1_unwired_drawers"], 0)
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["l0_directional_drifts"], 0)
                self.assertEqual(fast_by_sk["healthy-skill"]["priority_score"], 0.0)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["wasted_preread_sessions"], 1)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["wasted_preread_rate"], 1.0)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["l0_directional_drifts"], 1)

    def test_validator_specific_recovery_and_sequence_ordered_downstream_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            sessions_dir = root / "sessions"
            handoff_root.mkdir(parents=True, exist_ok=True)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            _write_skill_pkg(
                skills_root,
                "validator-skill",
                skill_md_extra="See `references/fix-lint.md` and `references/unwired-sec.md`.",
                refs={
                    "fix-lint.md": "# Fix Lint\n",
                    "unwired-sec.md": "# Unwired Security\n",
                },
                scripts={
                    "lint.py": "#!/usr/bin/env python3\n# see references/fix-lint.md\n",
                    "helper.py": "#!/usr/bin/env python3\n",
                    "sec_check.py": "#!/usr/bin/env python3\n",
                },
            )
            now = datetime.now(CST).replace(microsecond=0)

            # Session A (Counter-Case F3): `lint.py` fails (exit=1), reads drawer, then UNRELATED `helper.py` exits 0
            # while `lint.py` is never re-run -> MUST NOT count as progressive_catch_count, and failure remains unresolved!
            sa_dir = sessions_dir / "sess-unrelated-pass"
            sa_dir.mkdir(parents=True, exist_ok=True)
            sa_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "validator-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {"toolCallId": "a1", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "a1", "shellExecution": {"exitCode": 1}, "result": {"content": "lint err"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {"toolCallId": "a2", "toolName": "view", "arguments": {"path": "skills/validator-skill/references/fix-lint.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "a2", "result": {"content": "doc"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=6)).isoformat(),
                    "data": {"toolCallId": "a3", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/helper.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=7)).isoformat(),
                    "data": {"toolCallId": "a3", "shellExecution": {"exitCode": 0}, "result": {"content": "helper ok"}},
                },
            ]
            (sa_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in sa_evs) + "\n", encoding="utf-8")

            # Session B (Mixed recovery + unresolved validator + script->drawer->end zero downstream action F4):
            # First runs `lint.py` (exit=1) -> reads `fix-lint.md` -> re-runs `lint.py` (exit=0) [recovered],
            # then runs `sec_check.py` (exit=1) [unresolved], then reads `fix-lint.md` at the end of session with 0 actions after it!
            sb_dir = sessions_dir / "sess-mixed-and-trailing-read"
            sb_dir.mkdir(parents=True, exist_ok=True)
            sb_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "validator-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {"toolCallId": "b1", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "b1", "shellExecution": {"exitCode": 1}, "result": {"content": "fail"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {"toolCallId": "b2", "toolName": "view", "arguments": {"path": "skills/validator-skill/references/fix-lint.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "b2", "result": {"content": "doc"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=6)).isoformat(),
                    "data": {"toolCallId": "b3", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=7)).isoformat(),
                    "data": {"toolCallId": "b3", "shellExecution": {"exitCode": 0}, "result": {"content": "pass"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=8)).isoformat(),
                    "data": {"toolCallId": "b4", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/sec_check.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=9)).isoformat(),
                    "data": {"toolCallId": "b4", "shellExecution": {"exitCode": 1}, "result": {"content": "sec fail"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=10)).isoformat(),
                    "data": {"toolCallId": "b5", "toolName": "view", "arguments": {"path": "skills/validator-skill/references/fix-lint.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=11)).isoformat(),
                    "data": {"toolCallId": "b5", "result": {"content": "trailing read with 0 actions after"}},
                },
            ]
            (sb_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in sb_evs) + "\n", encoding="utf-8")

            probe_v = SKILL_RETRO.probe_skills(
                skill="validator-skill",
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            vsig = probe_v["skills"][0]["signals"]
            # Only Session B's lint.py recovery counts as progressive_catch_count (1, not 2)
            self.assertEqual(vsig["progressive_catch_count"], 1)
            # Both Session A's lint.py and Session B's sec_check.py remain unresolved domain_failures (2)
            self.assertEqual(vsig["domain_failures"], 2)
            # `unwired-sec.md` is flagged as unwired because unresolved domain_failures > 0
            self.assertEqual(vsig["l1_unwired_drawers"], 1)
            # Session B's trailing read after all actions is caught as wasted_preread_sessions=1
            self.assertEqual(vsig["wasted_preread_sessions"], 1)
            self.assertEqual(vsig["wasted_preread_rate"], 0.5)

    def test_direct_warehouse_db_fast_probe_and_domain_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            warehouse_db = root / "warehouse.db"
            handoff_root.mkdir(parents=True, exist_ok=True)

            _write_skill_pkg(
                skills_root,
                "codebase-design",
                skill_md_extra="See `references/guide.md`.",
                refs={"guide.md": "# Guide\n"},
                scripts={"audit_design.py": "#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\n"},
            )

            now = datetime.now(CST).replace(microsecond=0)
            day_str = now.date().isoformat()
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                conn.execute(
                    "CREATE TABLE script_call (skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT)"
                )
                conn.execute(
                    "CREATE TABLE reference_hits (skill TEXT, reference_path TEXT, ts TEXT)"
                )
                conn.execute(
                    "CREATE TABLE fact_gtt_task_run (goal_id TEXT, task_id TEXT, role_name TEXT, round_no INTEGER, v5_verdict TEXT, skills_used TEXT, cst_day TEXT)"
                )
                conn.execute(
                    "CREATE TABLE fact_gtt_event (goal_id TEXT, task_id TEXT, evt_type TEXT, note_preview TEXT, cst_day TEXT)"
                )
                conn.executemany(
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?)",
                    [
                        ("codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 120.0, now.isoformat()),
                        ("codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 240.0, now.isoformat()),
                        ("codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 480.0, now.isoformat()),
                    ],
                )
                conn.execute(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                    ("g-arch", "T1", "REVIEW_FAIL", "[IMPL_BUG] skills/codebase-design/SKILL.md seam leak", day_str),
                )
                conn.commit()

            inv = SKILL_RETRO._discover_skill_inventory(skills_root, dev_root)
            fast_sig = SKILL_RETRO._fast_telemetry_signals(
                inv=inv,
                warehouse_db=warehouse_db,
                usage_jsonl=root / "nonexistent-usage.jsonl",
                proposals_path=root / "nonexistent-proposals.jsonl",
                roi_snapshot_path=root / "nonexistent-roi.json",
            )
            cd_sig = fast_sig["codebase-design"]
            self.assertEqual(cd_sig["p50_ms"], 240.0)
            self.assertEqual(cd_sig["p90_ms"], 480.0)
            self.assertEqual(cd_sig["skill_escapes"], 1)
            self.assertEqual(cd_sig["l2_leaky_rubber_stamps"], 1)
            self.assertEqual(cd_sig["rubber_stamp_escapes"], 1)
            self.assertGreaterEqual(cd_sig["domain_failures"], 1)

            # Verify expanded _DOMAIN_HINTS covers newly added repo skills
            hint_skills = {sk for sk, _ in SKILL_RETRO._DOMAIN_HINTS}
            for expected_sk in ("my-designer", "design-studio", "skill-retro", "codebase-design", "team-lead", "issue-brief"):
                self.assertIn(expected_sk, hint_skills)

    def test_s2_internal_reality_check_and_aci_output_bloat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            sessions_dir = root / "sessions"
            handoff_root = dev_root / "handoff"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            handoff_root.mkdir(parents=True, exist_ok=True)

            # 1) Positive Case: clean skill with valid flags, generic placeholder `[--flag]`, and existing ref
            clean_pkg = _write_skill_pkg(
                skills_root,
                "clean-cli-skill",
                skill_md_extra=(
                    "Run `python3 skills/clean-cli-skill/scripts/tool.py check --strict [--flag]`.\n"
                    "See `references/valid-ref.md`. Model: `gemini-4-argon`.\n"
                ),
                refs={"valid-ref.md": "# Valid Ref\n"},
                scripts={
                    "tool.py": (
                        "#!/usr/bin/env python3\n"
                        "import argparse\n"
                        "p = argparse.ArgumentParser()\n"
                        "sub = p.add_subparsers(dest='cmd')\n"
                        "c = sub.add_parser('check')\n"
                        "c.add_argument('--strict', action='store_true')\n"
                    )
                },
            )
            clean_ir = SKILL_RETRO.internal_reality_check(clean_pkg)
            self.assertTrue(clean_ir["ok"])
            self.assertEqual(clean_ir["internal_doc_cli_drift_count"], 0)
            self.assertEqual(clean_ir["internal_broken_refs_count"], 0)

            # 2) Counter-Case: drifted skill with unknown `--nonexistent-flag`, unknown subcommand `bogus-sub`,
            #    missing `scripts/missing_script.py`, missing `references/ghost.md`, and malformed `gemini:4- argon`
            drifted_pkg = _write_skill_pkg(
                skills_root,
                "drifted-cli-skill",
                skill_md_extra=(
                    "Run `python3 skills/drifted-cli-skill/scripts/tool.py check --nonexistent-flag`.\n"
                    "Run `python3 skills/drifted-cli-skill/scripts/tool.py bogus-sub`.\n"
                    "Run `python3 skills/drifted-cli-skill/scripts/missing_script.py --json`.\n"
                    "See `references/ghost.md`. Bad model: `gemini:4- argon`.\n"
                ),
                refs={},
                scripts={
                    "tool.py": (
                        "#!/usr/bin/env python3\n"
                        "import argparse\n"
                        "EXAMPLE = '--ghost-constant-flag'\n"
                        "p = argparse.ArgumentParser()\n"
                        "sub = p.add_subparsers(dest='cmd')\n"
                        "c = sub.add_parser('check')\n"
                        "c.add_argument('--strict', action='store_true')\n"
                    )
                },
            )
            drifted_ir = SKILL_RETRO.internal_reality_check(drifted_pkg)
            self.assertFalse(drifted_ir["ok"])
            drift_tokens = {d["token"] for d in drifted_ir["internal_doc_cli_drift"]}
            self.assertIn("--nonexistent-flag", drift_tokens)
            self.assertIn("bogus-sub", drift_tokens)
            self.assertIn("scripts/missing_script.py", drift_tokens)

            # Counter-Case F5: an arbitrary string constant `EXAMPLE = '--ghost-constant-flag'` inside `tool.py`
            # must NOT mask `--ghost-constant-flag` when documented in SKILL.md!
            (drifted_pkg / "SKILL.md").write_text(
                (drifted_pkg / "SKILL.md").read_text(encoding="utf-8")
                + "\nRun `python3 skills/drifted-cli-skill/scripts/tool.py check --ghost-constant-flag`.\n",
                encoding="utf-8",
            )
            drifted_ir_const = SKILL_RETRO.internal_reality_check(drifted_pkg)
            self.assertIn(
                "--ghost-constant-flag",
                {d["token"] for d in drifted_ir_const["internal_doc_cli_drift"]},
            )

            broken_targets = {b["target"] for b in drifted_ir["internal_broken_refs"]}
            self.assertIn("references/ghost.md", broken_targets)
            self.assertIn("gemini:4- argon", broken_targets)

            # 3) ACI Output Bloat (>20KB or "Output too large to read at once")
            now = datetime.now(CST).replace(microsecond=0)
            s_dir = sessions_dir / "sess-bloat-1"
            s_dir.mkdir(parents=True, exist_ok=True)
            huge_output = "X" * 25_000
            evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "drifted-cli-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {
                        "toolCallId": "b-bloat",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/drifted-cli-skill/scripts/tool.py check --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "b-bloat", "shellExecution": {"exitCode": 0}, "result": {"content": huge_output}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {
                        "toolCallId": "b-compact",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/clean-cli-skill/scripts/tool.py check --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "b-compact", "shellExecution": {"exitCode": 0}, "result": {"content": "ok"}},
                },
            ]
            (s_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

            probe_out = SKILL_RETRO.probe_skills(
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
                summary=True,
            )
            by_sk = {s["skill"]: s for s in probe_out["skills"]}
            self.assertEqual(by_sk["drifted-cli-skill"]["signals"]["aci_output_bloat_count"], 1)
            self.assertGreaterEqual(by_sk["drifted-cli-skill"]["signals"]["internal_doc_cli_drift_count"], 3)
            self.assertGreaterEqual(by_sk["drifted-cli-skill"]["signals"]["internal_broken_refs_count"], 2)
            self.assertEqual(by_sk["clean-cli-skill"]["signals"]["aci_output_bloat_count"], 0)

    def test_s4_eval_counter_case_ratio_escape_gap_and_rollup_receipt_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir = root / "repo"
            skills_root = repo_dir / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            warehouse_db = root / "warehouse.db"
            handoff_root.mkdir(parents=True, exist_ok=True)

            subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)

            # Old registered_at (>7d ago) so eval_case_weekly_delta == 0
            old_date = (datetime.now(CST).date() - timedelta(days=20)).isoformat()
            eval_doc = {
                "schema": "skill-eval-cases/v1",
                "version": 1,
                "updated": old_date,
                "active_cases": [
                    {"id": "EV-001", "title": "pos1", "cmd": "true", "expect_exit": 0, "registered_at": old_date},
                    {"id": "EV-002", "title": "pos2", "cmd": "true", "expect_exit": 0, "registered_at": old_date},
                    {"id": "EV-003", "title": "pos3", "cmd": "true", "expect_exit": 0, "registered_at": old_date},
                    {"id": "EV-004", "title": "neg1", "forbidden_regex": "bad_pattern", "bad_snippet": "bad_pattern", "registered_at": old_date},
                ],
            }
            _write_skill_pkg(
                skills_root,
                "gap-skill",
                refs={"eval-cases.json": json.dumps(eval_doc)},
                scripts={"run.py": "#!/usr/bin/env python3\n"},
            )
            subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)
            real_sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            now = datetime.now(CST).replace(microsecond=0)
            day_str = now.date().isoformat()
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                conn.execute(
                    "CREATE TABLE fact_gtt_event (goal_id TEXT, task_id TEXT, evt_type TEXT, note_preview TEXT, cst_day TEXT)"
                )
                conn.executemany(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                    [
                        ("g1", "T1", "REVIEW_FAIL", "[IMPL_BUG] skills/gap-skill/SKILL.md escape 1", day_str),
                        ("g2", "T1", "REVIEW_FAIL", "[SPEC_GAP] skills/gap-skill/SKILL.md escape 2", day_str),
                    ],
                )
                conn.commit()

            probe_res = SKILL_RETRO.probe_skills(
                skill="gap-skill",
                warehouse_db=warehouse_db,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            g_entry = probe_res["skills"][0]
            self.assertEqual(g_entry["eval_case_count"], 4)
            self.assertEqual(g_entry["eval_counter_case_count"], 1)
            self.assertEqual(g_entry["eval_counter_case_ratio"], 0.25)
            # 2 skill_escapes + 2 domain_failures >= 3 while eval_case_weekly_delta == 0 -> eval_escape_gap = True
            self.assertTrue(g_entry["eval_escape_gap"])

            # Rollup Receipt Grounding:
            # 1) Grounded FIXED_AND_MERGED receipt with real git commit SHA and PR URL
            SKILL_RETRO.record_m1_retro_receipt(
                skill="gap-skill",
                status="FIXED_AND_MERGED",
                handoff_root=handoff_root,
                pr_url="https://github.com/LeePepe/user-skills/pull/300",
                pr_number=300,
                commit_sha=real_sha,
            )
            # 2) Counter-Case: Fabricated FIXED_AND_MERGED receipt with nonexistent commit SHA (`deadbeef999`)
            SKILL_RETRO.record_m1_retro_receipt(
                skill="fabricated-skill",
                status="FIXED_AND_MERGED",
                handoff_root=handoff_root,
                pr_url="https://github.com/LeePepe/user-skills/pull/301",
                pr_number=301,
                commit_sha="deadbeef999",
            )
            # 3) Counter-Case: Fabricated FIXED_AND_MERGED receipt missing commit_sha and pr_url
            SKILL_RETRO.record_m1_retro_receipt(
                skill="missing-sha-skill",
                status="FIXED_AND_MERGED",
                handoff_root=handoff_root,
            )

            rollup = SKILL_RETRO.rollup_m1_retro_receipts(
                handoff_root=handoff_root,
                repo_root=repo_dir,
                verify_grounding=True,
            )
            self.assertFalse(rollup["receipt_grounded"])
            self.assertEqual(rollup["counts"]["fixed_and_merged"], 1)
            self.assertEqual(rollup["counts"]["ungrounded_receipts"], 2)
            by_rec = {r["skill"]: r for r in rollup["m1_receipts"]}
            self.assertTrue(by_rec["gap-skill"]["receipt_grounded"])
            self.assertEqual(by_rec["gap-skill"]["effective_status"], "FIXED_AND_MERGED")

            self.assertFalse(by_rec["fabricated-skill"]["receipt_grounded"])
            self.assertEqual(by_rec["fabricated-skill"]["effective_status"], "UNGROUNDED_RECEIPT")
            self.assertTrue(any("commit_not_found_in_git" in err for err in by_rec["fabricated-skill"]["grounding_issues"]))

            self.assertFalse(by_rec["missing-sha-skill"]["receipt_grounded"])
            self.assertIn("missing_commit_or_merge_sha", by_rec["missing-sha-skill"]["grounding_issues"])
            self.assertIn("missing_pr_reference", by_rec["missing-sha-skill"]["grounding_issues"])

            # Counter-Case F6: `verify_grounding=True` without `--repo-root` (or with invalid repo_root)
            # must fail closed on a fabricated SHA rather than silently skipping git verification!
            rollup_no_repo_arg = SKILL_RETRO.rollup_m1_retro_receipts(
                handoff_root=handoff_root,
                skills=["fabricated-skill"],
                verify_grounding=True,
            )
            self.assertFalse(rollup_no_repo_arg["receipt_grounded"])
            self.assertEqual(rollup_no_repo_arg["counts"]["fixed_and_merged"], 0)
            self.assertEqual(rollup_no_repo_arg["counts"]["ungrounded_receipts"], 1)

            rollup_bad_repo = SKILL_RETRO.rollup_m1_retro_receipts(
                handoff_root=handoff_root,
                skills=["gap-skill"],
                repo_root=root / "nonexistent-repo-dir",
                verify_grounding=True,
            )
            self.assertFalse(rollup_bad_repo["receipt_grounded"])
            self.assertTrue(
                any("invalid_or_missing_repo_root" in err for err in rollup_bad_repo["grounding_issues"])
            )


if __name__ == "__main__":
    unittest.main()
