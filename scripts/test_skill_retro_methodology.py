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

            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)

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
                conn.execute(
                    "CREATE TABLE script_call (session_id TEXT, skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT)"
                )
                # Counter-Case R3-F2: Insert duplicate out-of-order warehouse rows for `sess-healthy-1`
                # (later pass row at t+8s inserted BEFORE earlier fail row at t+2s).
                # Deep-mode chronological deduplication must NOT treat the out-of-order fail row as a new terminal failure!
                conn.executemany(
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("sess-healthy-1", "healthy-skill", "skills/healthy-skill/scripts/check_tool.py", 0, 50.0, (now + timedelta(seconds=8)).isoformat()),
                        ("sess-healthy-1", "healthy-skill", "skills/healthy-skill/scripts/check_tool.py", 1, 50.0, (now + timedelta(seconds=2)).isoformat()),
                    ],
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
                # Goal-level REVIEW_FAIL with empty task_id on `g-conv`:
                # - for `healthy-skill` (which converged in g-conv/T1): exempted
                # - for `noisy-skill` (which did NOT converge in g-conv): MUST NOT be exempted! (Counter-Case R2-F4)
                conn.executemany(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                    [
                        ("g-conv", "T1", "REVIEW_FAIL", "[IMPL_BUG] skills/healthy-skill/SKILL.md R1 finding resolved in R2", day_str),
                        ("g-conv", "", "REVIEW_FAIL", "[IMPL_BUG] skills/healthy-skill/SKILL.md goal-level R1 note", day_str),
                        ("g-conv", "", "REVIEW_FAIL", "[SPEC_GAP] skills/noisy-skill/SKILL.md unresolved goal-level escape", day_str),
                    ],
                )
                conn.commit()

            # Session 3 (Counter-Case R3-F1): partial/truncated sampled session `sess-partial-log`
            # where `events.jsonl` only has `skill.invoked` + `edit` (omitting script calls), while `usage.jsonl`
            # has 3 terminal `lint_noisy.py exit=1` calls for `sess-partial-log`.
            # Canonical `probe --fast` must merge those usage/warehouse calls into `sampled_obs["sess-partial-log"]`
            # rather than dropping them!
            s3_dir = sessions_dir / "sess-partial-log"
            s3_dir.mkdir(parents=True, exist_ok=True)
            s3_events = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "noisy-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=20)).isoformat(),
                    "data": {"toolCallId": "ep-1", "toolName": "edit", "arguments": {"path": "src/other.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=21)).isoformat(),
                    "data": {"toolCallId": "ep-1", "result": {"content": "ok"}},
                },
            ]
            (s3_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in s3_events) + "\n", encoding="utf-8")

            # Overlapping usage.jsonl with the same `sess-healthy-1` exit=1 -> exit=0 sequence (Counter-Case R2-F1)
            # PLUS 3 terminal `exit=1` calls for `sess-partial-log` (Counter-Case R3-F1)
            usage_jsonl = root / "usage.jsonl"
            usage_rows = [
                {
                    "kind": "script_call",
                    "session_id": "sess-healthy-1",
                    "ts": (now + timedelta(seconds=2)).isoformat(),
                    "skill": "healthy-skill",
                    "script_path": "skills/healthy-skill/scripts/check_tool.py",
                    "exit_code": 1,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-healthy-1",
                    "ts": (now + timedelta(seconds=8)).isoformat(),
                    "skill": "healthy-skill",
                    "script_path": "skills/healthy-skill/scripts/check_tool.py",
                    "exit_code": 0,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-partial-log",
                    "ts": (now + timedelta(seconds=5)).isoformat(),
                    "skill": "noisy-skill",
                    "script_path": "skills/noisy-skill/scripts/lint_noisy.py",
                    "exit_code": 1,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-partial-log",
                    "ts": (now + timedelta(seconds=7)).isoformat(),
                    "skill": "noisy-skill",
                    "script_path": "skills/noisy-skill/scripts/lint_noisy.py",
                    "exit_code": 1,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-partial-log",
                    "ts": (now + timedelta(seconds=9)).isoformat(),
                    "skill": "noisy-skill",
                    "script_path": "skills/noisy-skill/scripts/lint_noisy.py",
                    "exit_code": 1,
                },
            ]
            usage_jsonl.write_text("\n".join(json.dumps(r) for r in usage_rows) + "\n", encoding="utf-8")

            # Deep probe with custom sources (including overlapping usage.jsonl)
            probe_res = SKILL_RETRO.probe_skills(
                warehouse_db=warehouse_db,
                copilot_sessions_dir=sessions_dir,
                usage_jsonl=usage_jsonl,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            by_sk = {s["skill"]: s for s in probe_res["skills"]}

            # Positive Case: `healthy-skill` has progressive_catch_count=1 and healthy_r1_to_r2_convergences=1,
            # 0 directional drifts, 0 escapes, 0 domain_failures, and 0 penalty!
            h_sig = by_sk["healthy-skill"]["signals"]
            self.assertEqual(h_sig["progressive_catch_count"], 1)
            self.assertEqual(h_sig["healthy_r1_to_r2_convergences"], 1)
            self.assertEqual(h_sig["l0_directional_drifts"], 0)
            self.assertEqual(h_sig["skill_escapes"], 0)
            self.assertEqual(h_sig["domain_failures"], 0)
            self.assertEqual(h_sig["wasted_preread_rate"], 0.0)
            self.assertEqual(by_sk["healthy-skill"]["priority_score"], 0.0)

            # Counter-Case: `noisy-skill` has r3+ drift, wasted_preread_sessions=1 (out of 2 sessions -> 0.5),
            # unwired drawer (`unwired-rules.md`), and its goal-level REVIEW_FAIL on `g-conv` was NOT suppressed
            # by `healthy-skill`'s convergence (skill_escapes=2, domain_failures=4 including `sess-partial-log`)!
            n_sig = by_sk["noisy-skill"]["signals"]
            self.assertEqual(n_sig["l0_directional_drifts"], 1)
            self.assertEqual(n_sig["skill_escapes"], 2)
            self.assertEqual(n_sig["domain_failures"], 4)
            self.assertEqual(n_sig["wasted_preread_sessions"], 1)
            self.assertEqual(n_sig["wasted_preread_rate"], 0.5)
            self.assertGreaterEqual(n_sig["l1_unwired_drawers"], 1)
            self.assertGreater(by_sk["noisy-skill"]["priority_score"], 0.0)

            # Canonical Fast-Path (`fast=True` without explicit source overrides, with real overlapping usage_jsonl)
            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", warehouse_db),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", usage_jsonl),
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
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["domain_failures"], 0)
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["l1_unwired_drawers"], 0)
                self.assertEqual(fast_by_sk["healthy-skill"]["signals"]["l0_directional_drifts"], 0)
                self.assertEqual(fast_by_sk["healthy-skill"]["priority_score"], 0.0)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["wasted_preread_sessions"], 1)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["wasted_preread_rate"], 0.5)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["l0_directional_drifts"], 1)
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["skill_escapes"], 2)
                # `sess-noisy-1` + `sess-partial-log` (merged from usage_jsonl!) + 2 escapes = 4 domain_failures
                self.assertEqual(fast_by_sk["noisy-skill"]["signals"]["domain_failures"], 4)

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
            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)

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

            # Session C (Counter-Case R2-F3): same validator (`lint.py`) recovers once (`exit=1 -> read -> exit=0`),
            # then fails 3 times in a terminal blind spin loop (`exit=1, exit=1, exit=1`).
            # Must record BOTH the earlier positive catch AND the terminal unresolved failure!
            sc_dir = sessions_dir / "sess-same-validator-terminal-fail"
            sc_dir.mkdir(parents=True, exist_ok=True)
            sc_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "validator-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {"toolCallId": "c1", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "c1", "shellExecution": {"exitCode": 1}, "result": {"content": "fail 1"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {"toolCallId": "c2", "toolName": "view", "arguments": {"path": "skills/validator-skill/references/fix-lint.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "c2", "result": {"content": "doc"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=6)).isoformat(),
                    "data": {"toolCallId": "c3", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=7)).isoformat(),
                    "data": {"toolCallId": "c3", "shellExecution": {"exitCode": 0}, "result": {"content": "pass"}},
                },
            ]
            for spin_i in range(1, 4):
                sc_evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (now + timedelta(seconds=10 + spin_i * 2)).isoformat(),
                            "data": {"toolCallId": f"cs-{spin_i}", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (now + timedelta(seconds=11 + spin_i * 2)).isoformat(),
                            "data": {"toolCallId": f"cs-{spin_i}", "shellExecution": {"exitCode": 1}, "result": {"content": "terminal fail"}},
                        },
                    ]
                )
            (sc_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in sc_evs) + "\n", encoding="utf-8")

            # Session D (Counter-Case R4-F2): distant pass after 8 unrelated turns (>6 global timeline steps).
            # Eventual pass clears terminal `domain_failures`, MUST NOT count as bounded 1-2 turn `progressive_catch_count`!
            sd_dir = sessions_dir / "sess-distant-pass"
            sd_dir.mkdir(parents=True, exist_ok=True)
            sd_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "validator-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "data": {"toolCallId": "d-fail", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=3)).isoformat(),
                    "data": {"toolCallId": "d-fail", "shellExecution": {"exitCode": 1}, "result": {"content": "fail"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=4)).isoformat(),
                    "data": {"toolCallId": "d-read", "toolName": "view", "arguments": {"path": "skills/validator-skill/references/fix-lint.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {"toolCallId": "d-read", "result": {"content": "doc"}},
                },
            ]
            for gap_i in range(1, 9):
                sd_evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (now + timedelta(seconds=6 + gap_i * 2)).isoformat(),
                            "data": {"toolCallId": f"d-gap-{gap_i}", "toolName": "edit", "arguments": {"path": f"src/f{gap_i}.py"}},
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (now + timedelta(seconds=7 + gap_i * 2)).isoformat(),
                            "data": {"toolCallId": f"d-gap-{gap_i}", "result": {"content": "ok"}},
                        },
                    ]
                )
            sd_evs.extend(
                [
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now + timedelta(seconds=50)).isoformat(),
                        "data": {"toolCallId": "d-pass", "toolName": "bash", "arguments": {"command": "python3 skills/validator-skill/scripts/lint.py"}},
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now + timedelta(seconds=51)).isoformat(),
                        "data": {"toolCallId": "d-pass", "shellExecution": {"exitCode": 0}, "result": {"content": "eventual pass"}},
                    },
                ]
            )
            (sd_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in sd_evs) + "\n", encoding="utf-8")

            probe_v = SKILL_RETRO.probe_skills(
                skill="validator-skill",
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            vsig = probe_v["skills"][0]["signals"]
            # Session B's lint.py recovery + Session C's early lint.py recovery = 2 progressive catches (Session D's distant pass is excluded!)
            self.assertEqual(vsig["progressive_catch_count"], 2)
            # Session A's lint.py + Session B's sec_check.py + Session C's terminal lint.py failure = 3 unresolved domain_failures (Session D's eventual pass cleared terminal failure!)
            self.assertEqual(vsig["domain_failures"], 3)
            # `unwired-sec.md` is flagged as unwired because unresolved domain_failures > 0
            self.assertEqual(vsig["l1_unwired_drawers"], 1)
            # Session B's trailing read after all actions is caught as wasted_preread_sessions=1 across 4 sessions
            self.assertEqual(vsig["wasted_preread_sessions"], 1)
            self.assertEqual(vsig["wasted_preread_rate"], 0.25)

    def test_merged_partial_trace_unified_chronological_timeline(self) -> None:
        """Verify R4-F1 positive & counter cases on canonical fast path when calls come from usage.jsonl/warehouse.db."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            sessions_dir = root / "sessions"
            usage_jsonl = root / "usage.jsonl"
            handoff_root.mkdir(parents=True, exist_ok=True)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            eval_doc = json.dumps(
                {
                    "schema": "skill-eval-cases/v1",
                    "version": 1,
                    "updated": datetime.now(CST).date().isoformat(),
                    "active_cases": [
                        {"id": "EV-1", "title": "p", "cmd": "true", "expect_exit": 0},
                        {"id": "EV-2", "title": "n", "negative_cmd": "false", "expect_fail_exit": 1},
                    ],
                }
            )
            _write_skill_pkg(
                skills_root,
                "post-fail-3drawer-skill",
                skill_md_extra="See `references/r1.md`, `references/r2.md`, `references/r3.md`.",
                refs={"r1.md": "# R1\n", "r2.md": "# R2\n", "r3.md": "# R3\n", "eval-cases.json": eval_doc},
                scripts={"check.py": "#!/usr/bin/env python3\n# see references/r1.md references/r2.md references/r3.md\n"},
            )
            _write_skill_pkg(
                skills_root,
                "trailing-read-skill",
                skill_md_extra="See `references/r1.md`.",
                refs={"r1.md": "# R1\n", "eval-cases.json": eval_doc},
                scripts={"check.py": "#!/usr/bin/env python3\n# see references/r1.md\n"},
            )

            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)

            # 1) Positive Case R4-F1a: Partial trace has 3 on-demand drawer reads at t+4s, t+5s, t+6s,
            #    while `usage.jsonl` has `check.py exit=1` at t+2s and `check.py exit=0` at t+8s.
            #    Chronologically: fail(t+2) -> read r1,r2,r3(t+4..6) -> pass(t+8).
            #    Must record progressive_catch_count=1, wasted_preread_sessions=0, priority_score=0.0!
            s_pos = sessions_dir / "sess-post-fail-3reads"
            s_pos.mkdir(parents=True, exist_ok=True)
            pos_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "post-fail-3drawer-skill"}},
            ]
            for idx_r, r_name in enumerate(("r1.md", "r2.md", "r3.md"), 4):
                pos_evs.extend(
                    [
                        {
                            "type": "tool.execution_start",
                            "timestamp": (now + timedelta(seconds=idx_r)).isoformat(),
                            "data": {"toolCallId": f"pr-{idx_r}", "toolName": "view", "arguments": {"path": f"skills/post-fail-3drawer-skill/references/{r_name}"}},
                        },
                        {
                            "type": "tool.execution_complete",
                            "timestamp": (now + timedelta(seconds=idx_r)).isoformat(),
                            "data": {"toolCallId": f"pr-{idx_r}", "result": {"content": "doc"}},
                        },
                    ]
                )
            (s_pos / "events.jsonl").write_text("\n".join(json.dumps(e) for e in pos_evs) + "\n", encoding="utf-8")

            # 2) Counter-Case R4-F1b: Sampled trace ends with a drawer read at t+20s; its only real action
            #    is a usage-only `check.py exit=0` call EARLIER at t+5s.
            #    Chronologically: pass(t+5) -> read r1(t+20) -> END (0 downstream actions after read).
            #    Must record wasted_preread_sessions=1, wasted_preread_rate=1.0!
            s_neg = sessions_dir / "sess-trailing-read-after-usage-call"
            s_neg.mkdir(parents=True, exist_ok=True)
            neg_evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "trailing-read-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=20)).isoformat(),
                    "data": {"toolCallId": "nr-1", "toolName": "view", "arguments": {"path": "skills/trailing-read-skill/references/r1.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=21)).isoformat(),
                    "data": {"toolCallId": "nr-1", "result": {"content": "doc"}},
                },
            ]
            (s_neg / "events.jsonl").write_text("\n".join(json.dumps(e) for e in neg_evs) + "\n", encoding="utf-8")

            usage_rows = [
                {
                    "kind": "script_call",
                    "session_id": "sess-post-fail-3reads",
                    "ts": (now + timedelta(seconds=2)).isoformat(),
                    "skill": "post-fail-3drawer-skill",
                    "script_path": "skills/post-fail-3drawer-skill/scripts/check.py",
                    "exit_code": 1,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-post-fail-3reads",
                    "ts": (now + timedelta(seconds=8)).isoformat(),
                    "skill": "post-fail-3drawer-skill",
                    "script_path": "skills/post-fail-3drawer-skill/scripts/check.py",
                    "exit_code": 0,
                },
                {
                    "kind": "script_call",
                    "session_id": "sess-trailing-read-after-usage-call",
                    "ts": (now + timedelta(seconds=5)).isoformat(),
                    "skill": "trailing-read-skill",
                    "script_path": "skills/trailing-read-skill/scripts/check.py",
                    "exit_code": 0,
                },
            ]
            usage_jsonl.write_text("\n".join(json.dumps(r) for r in usage_rows) + "\n", encoding="utf-8")

            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", usage_jsonl),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", root / "none.db"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", root / "none-prop.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", root / "none-roi.json"),
            ):
                fast_out = SKILL_RETRO.probe_skills(
                    handoff_root=handoff_root,
                    skills_root=skills_root,
                    dev_root=dev_root,
                    fast=True,
                )
                by_sk = {s["skill"]: s for s in fast_out["skills"]}
                pos_s = by_sk["post-fail-3drawer-skill"]
                self.assertEqual(pos_s["signals"]["progressive_catch_count"], 1)
                self.assertEqual(pos_s["signals"]["wasted_preread_sessions"], 0)
                self.assertEqual(pos_s["signals"]["wasted_preread_rate"], 0.0)
                self.assertEqual(pos_s["priority_score"], 0.0)

                neg_s = by_sk["trailing-read-skill"]
                self.assertEqual(neg_s["signals"]["wasted_preread_sessions"], 1)
                self.assertEqual(neg_s["signals"]["wasted_preread_rate"], 1.0)
                self.assertGreater(neg_s["priority_score"], 0.0)

    def test_subsecond_fail_read_pass_fail_preserves_terminal_failure(self) -> None:
        """Verify R5-F1: sub-second fail(t+0.0s) -> read(t+0.1s) -> pass(t+0.3s) -> fail(t+0.7s) retains BOTH catch=1 and domain_failures=1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            dev_root = root / "dev"
            handoff_root = dev_root / "handoff"
            sessions_dir = root / "sessions"
            handoff_root.mkdir(parents=True, exist_ok=True)
            sessions_dir.mkdir(parents=True, exist_ok=True)

            eval_doc = json.dumps(
                {
                    "schema": "skill-eval-cases/v1",
                    "version": 1,
                    "updated": datetime.now(CST).date().isoformat(),
                    "active_cases": [
                        {"id": "EV-1", "title": "p", "cmd": "true", "expect_exit": 0},
                        {"id": "EV-2", "title": "n", "negative_cmd": "false", "expect_fail_exit": 1},
                    ],
                }
            )
            _write_skill_pkg(
                skills_root,
                "rapid-retry-skill",
                skill_md_extra="See `references/fix.md` and `references/unwired.md`.",
                refs={"fix.md": "# Fix\n", "unwired.md": "# Unwired\n", "eval-cases.json": eval_doc},
                scripts={"check.py": "#!/usr/bin/env python3\n# see references/fix.md\n"},
            )

            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)
            s_dir = sessions_dir / "sess-rapid-fail-pass-fail"
            s_dir.mkdir(parents=True, exist_ok=True)
            evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": now.isoformat(), "data": {"name": "rapid-retry-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(milliseconds=0)).isoformat(),
                    "data": {"toolCallId": "r-c1", "toolName": "bash", "arguments": {"command": "python3 skills/rapid-retry-skill/scripts/check.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(milliseconds=50)).isoformat(),
                    "data": {"toolCallId": "r-c1", "shellExecution": {"exitCode": 1}, "result": {"content": "fail 1"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(milliseconds=100)).isoformat(),
                    "data": {"toolCallId": "r-v1", "toolName": "view", "arguments": {"path": "skills/rapid-retry-skill/references/fix.md"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(milliseconds=150)).isoformat(),
                    "data": {"toolCallId": "r-v1", "result": {"content": "doc"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(milliseconds=300)).isoformat(),
                    "data": {"toolCallId": "r-c2", "toolName": "bash", "arguments": {"command": "python3 skills/rapid-retry-skill/scripts/check.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(milliseconds=350)).isoformat(),
                    "data": {"toolCallId": "r-c2", "shellExecution": {"exitCode": 0}, "result": {"content": "pass"}},
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(milliseconds=700)).isoformat(),
                    "data": {"toolCallId": "r-c3", "toolName": "bash", "arguments": {"command": "python3 skills/rapid-retry-skill/scripts/check.py"}},
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(milliseconds=750)).isoformat(),
                    "data": {"toolCallId": "r-c3", "shellExecution": {"exitCode": 1}, "result": {"content": "terminal fail"}},
                },
            ]
            (s_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

            # 1) Deep mode
            deep_out = SKILL_RETRO.probe_skills(
                skill="rapid-retry-skill",
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
            )
            d_sig = deep_out["skills"][0]["signals"]
            self.assertEqual(d_sig["progressive_catch_count"], 1)
            self.assertEqual(d_sig["domain_failures"], 1)
            self.assertEqual(d_sig["l1_unwired_drawers"], 1)
            self.assertGreater(deep_out["skills"][0]["priority_score"], 0.0)

            # 2) Canonical sampled-fast mode
            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", root / "none.db"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", root / "none.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", root / "none-prop.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", root / "none-roi.json"),
            ):
                fast_out = SKILL_RETRO.probe_skills(
                    skill="rapid-retry-skill",
                    handoff_root=handoff_root,
                    skills_root=skills_root,
                    dev_root=dev_root,
                    fast=True,
                )
                f_sig = fast_out["skills"][0]["signals"]
                self.assertEqual(f_sig["progressive_catch_count"], 1)
                self.assertEqual(f_sig["domain_failures"], 1)
                self.assertEqual(f_sig["l1_unwired_drawers"], 1)
                self.assertGreater(fast_out["skills"][0]["priority_score"], 0.0)

            # 3) Mixed-source R6-F1a (`usage_jsonl` terminal failure across intervening pass) &
            #    R6-F1b (`warehouse.db` terminal failure across intervening pass):
            #    `events.jsonl` has fail(t+0.0s) -> read(t+0.1s) -> pass(t+0.3s), while the final
            #    fail(t+0.7s) exists ONLY in `usage.jsonl` (for `usage-terminal-skill`) or ONLY in
            #    `warehouse.db` (for `wh-terminal-skill`).
            for mix_sk in ("usage-terminal-skill", "wh-terminal-skill"):
                _write_skill_pkg(
                    skills_root,
                    mix_sk,
                    skill_md_extra="See `references/fix.md` and `references/unwired.md`.",
                    refs={"fix.md": "# Fix\n", "unwired.md": "# Unwired\n", "eval-cases.json": eval_doc},
                    scripts={"check.py": "#!/usr/bin/env python3\n# see references/fix.md\n"},
                )
                ms_dir = sessions_dir / f"sess-{mix_sk}"
                ms_dir.mkdir(parents=True, exist_ok=True)
                ms_evs = [
                    {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                    {"type": "skill.invoked", "timestamp": now.isoformat(), "data": {"name": mix_sk}},
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now + timedelta(milliseconds=0)).isoformat(),
                        "data": {"toolCallId": "mc1", "toolName": "bash", "arguments": {"command": f"python3 skills/{mix_sk}/scripts/check.py"}},
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now + timedelta(milliseconds=50)).isoformat(),
                        "data": {"toolCallId": "mc1", "shellExecution": {"exitCode": 1}, "result": {"content": "fail 1"}},
                    },
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now + timedelta(milliseconds=100)).isoformat(),
                        "data": {"toolCallId": "mv1", "toolName": "view", "arguments": {"path": f"skills/{mix_sk}/references/fix.md"}},
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now + timedelta(milliseconds=150)).isoformat(),
                        "data": {"toolCallId": "mv1", "result": {"content": "doc"}},
                    },
                    {
                        "type": "tool.execution_start",
                        "timestamp": (now + timedelta(milliseconds=300)).isoformat(),
                        "data": {"toolCallId": "mc2", "toolName": "bash", "arguments": {"command": f"python3 skills/{mix_sk}/scripts/check.py"}},
                    },
                    {
                        "type": "tool.execution_complete",
                        "timestamp": (now + timedelta(milliseconds=350)).isoformat(),
                        "data": {"toolCallId": "mc2", "shellExecution": {"exitCode": 0}, "result": {"content": "pass"}},
                    },
                ]
                (ms_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ms_evs) + "\n", encoding="utf-8")

            mix_usage = root / "mix-usage.jsonl"
            mix_usage.write_text(
                json.dumps(
                    {
                        "kind": "script_call",
                        "session_id": "sess-usage-terminal-skill",
                        "ts": (now + timedelta(milliseconds=700)).isoformat(),
                        "skill": "usage-terminal-skill",
                        "script_path": "skills/usage-terminal-skill/scripts/check.py",
                        "exit_code": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            mix_wh = root / "mix-warehouse.db"
            with contextlib.closing(sqlite3.connect(mix_wh)) as conn:
                conn.execute(
                    "CREATE TABLE script_call (session_id TEXT, skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT)"
                )
                conn.execute(
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "sess-wh-terminal-skill",
                        "wh-terminal-skill",
                        "skills/wh-terminal-skill/scripts/check.py",
                        1,
                        25.0,
                        (now + timedelta(milliseconds=700)).isoformat(),
                    ),
                )
                conn.commit()

            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", mix_wh),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", mix_usage),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_DB", root / "none-cp.db"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", root / "none-prop.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", root / "none-roi.json"),
            ):
                for mix_sk in ("usage-terminal-skill", "wh-terminal-skill"):
                    deep_mix = SKILL_RETRO.probe_skills(
                        skill=mix_sk,
                        handoff_root=handoff_root,
                        skills_root=skills_root,
                        dev_root=dev_root,
                        fast=False,
                    )
                    dm_sig = deep_mix["skills"][0]["signals"]
                    self.assertEqual(dm_sig["progressive_catch_count"], 1, mix_sk)
                    self.assertEqual(dm_sig["domain_failures"], 1, mix_sk)
                    self.assertEqual(dm_sig["l1_unwired_drawers"], 1, mix_sk)
                    self.assertGreater(deep_mix["skills"][0]["priority_score"], 0.0, mix_sk)

                    fast_mix = SKILL_RETRO.probe_skills(
                        skill=mix_sk,
                        handoff_root=handoff_root,
                        skills_root=skills_root,
                        dev_root=dev_root,
                        fast=True,
                    )
                    fm_sig = fast_mix["skills"][0]["signals"]
                    self.assertEqual(fm_sig["progressive_catch_count"], 1, mix_sk)
                    self.assertEqual(fm_sig["domain_failures"], 1, mix_sk)
                    self.assertEqual(fm_sig["l1_unwired_drawers"], 1, mix_sk)
                    self.assertGreater(fast_mix["skills"][0]["priority_score"], 0.0, mix_sk)

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

            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)
            day_str = now.date().isoformat()
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                conn.execute(
                    "CREATE TABLE script_call (session_id TEXT, skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT)"
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
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("s-1", "codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 120.0, now.isoformat()),
                        ("s-2", "codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 240.0, now.isoformat()),
                        ("s-3", "codebase-design", "skills/codebase-design/scripts/audit_design.py", 0, 480.0, now.isoformat()),
                    ],
                )
                conn.execute(
                    "INSERT INTO fact_gtt_event VALUES (?, ?, ?, ?, ?)",
                    ("g-arch", "T1", "REVIEW_FAIL", "[IMPL_BUG] skills/codebase-design/SKILL.md seam leak", day_str),
                )
                conn.commit()

            _write_skill_pkg(
                skills_root,
                "out-of-order-wh-skill",
                scripts={"check.py": "#!/usr/bin/env python3\n"},
            )
            with contextlib.closing(sqlite3.connect(warehouse_db)) as conn:
                # Counter-Case R2-F2: Insert LATER pass row first and EARLIER fail row second (out-of-order ingestion).
                # Chronologically (by ts): t+2s fail (exit=1) -> t+10s pass (exit=0) -> recovered!
                # And for `s-unres`: insert EARLIER pass row second and LATER fail row first -> chronologically terminal fail!
                conn.executemany(
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("s-rec", "out-of-order-wh-skill", "skills/out-of-order-wh-skill/scripts/check.py", 0, 50.0, (now + timedelta(seconds=10)).isoformat()),
                        ("s-rec", "out-of-order-wh-skill", "skills/out-of-order-wh-skill/scripts/check.py", 1, 50.0, (now + timedelta(seconds=2)).isoformat()),
                        ("s-unres", "out-of-order-wh-skill", "skills/out-of-order-wh-skill/scripts/check.py", 1, 50.0, (now + timedelta(seconds=20)).isoformat()),
                        ("s-unres", "out-of-order-wh-skill", "skills/out-of-order-wh-skill/scripts/check.py", 0, 50.0, (now + timedelta(seconds=5)).isoformat()),
                    ],
                )
                conn.commit()

            inv = SKILL_RETRO._discover_skill_inventory(skills_root, dev_root)
            fast_sig = SKILL_RETRO._fast_telemetry_signals(
                inv=inv,
                warehouse_db=warehouse_db,
                copilot_sessions_dir=root / "nonexistent-sessions",
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

            ooo_sig = fast_sig["out-of-order-wh-skill"]
            self.assertEqual(ooo_sig["progressive_catch_count"], 1)
            self.assertEqual(ooo_sig["domain_failures"], 1)

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

            # 3) ACI Output Bloat (>20KB or "Output too large to read at once") + R7-F1 earlier telemetry mirror
            #    Use fractional timestamps (`+2.2s`, `+4.2s`) in `events.jsonl` while `usage.jsonl` / `warehouse.db`
            #    have whole-second earlier mirrors (`+2.0s`, `+4.0s`) with no output_chars, proving coalescing
            #    preserves `output_chars` & `aci_output_bloat` and counts each logical bloated call once!
            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)
            s_dir = sessions_dir / "sess-bloat-1"
            s_dir.mkdir(parents=True, exist_ok=True)
            huge_output = "X" * 25_000
            evs = [
                {"type": "session.start", "timestamp": now.isoformat(), "data": {}},
                {"type": "skill.invoked", "timestamp": (now + timedelta(seconds=1)).isoformat(), "data": {"name": "drifted-cli-skill"}},
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=2, milliseconds=200)).isoformat(),
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
                    "timestamp": (now + timedelta(seconds=4, milliseconds=200)).isoformat(),
                    "data": {
                        "toolCallId": "b-spill",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/drifted-cli-skill/scripts/tool.py check --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "data": {
                        "toolCallId": "b-spill",
                        "shellExecution": {"exitCode": 0},
                        "result": {"content": "Output too large to read at once (48 KB). Saved to temp file."},
                    },
                },
                {
                    "type": "tool.execution_start",
                    "timestamp": (now + timedelta(seconds=6)).isoformat(),
                    "data": {
                        "toolCallId": "b-compact",
                        "toolName": "bash",
                        "arguments": {"command": "python3 skills/clean-cli-skill/scripts/tool.py check --strict"},
                    },
                },
                {
                    "type": "tool.execution_complete",
                    "timestamp": (now + timedelta(seconds=7)).isoformat(),
                    "data": {"toolCallId": "b-compact", "shellExecution": {"exitCode": 0}, "result": {"content": "ok"}},
                },
            ]
            (s_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

            bloat_usage = root / "bloat-usage.jsonl"
            bloat_usage.write_text(
                json.dumps(
                    {
                        "kind": "script_call",
                        "session_id": "sess-bloat-1",
                        "ts": (now + timedelta(seconds=2)).isoformat(),
                        "skill": "drifted-cli-skill",
                        "script_path": "skills/drifted-cli-skill/scripts/tool.py",
                        "exit_code": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            bloat_wh = root / "bloat-warehouse.db"
            with contextlib.closing(sqlite3.connect(bloat_wh)) as conn:
                conn.execute(
                    "CREATE TABLE script_call (session_id TEXT, skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT)"
                )
                conn.execute(
                    "INSERT INTO script_call VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "sess-bloat-1",
                        "drifted-cli-skill",
                        "skills/drifted-cli-skill/scripts/tool.py",
                        0,
                        150.0,
                        (now + timedelta(seconds=4)).isoformat(),
                    ),
                )
                conn.commit()

            probe_out = SKILL_RETRO.probe_skills(
                warehouse_db=bloat_wh,
                usage_jsonl=bloat_usage,
                copilot_sessions_dir=sessions_dir,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                fast=False,
                summary=True,
            )
            by_sk = {s["skill"]: s for s in probe_out["skills"]}
            self.assertEqual(by_sk["drifted-cli-skill"]["signals"]["aci_output_bloat_count"], 2)
            self.assertGreaterEqual(by_sk["drifted-cli-skill"]["signals"]["internal_doc_cli_drift_count"], 3)
            self.assertGreaterEqual(by_sk["drifted-cli-skill"]["signals"]["internal_broken_refs_count"], 2)
            self.assertEqual(by_sk["clean-cli-skill"]["signals"]["aci_output_bloat_count"], 0)

            # Verify canonical fast-path (`fast=True` without explicit source overrides) also preserves ACI output bloat across earlier mirrors
            with (
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_COPILOT_SESSIONS_DIR", sessions_dir),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_WAREHOUSE_DB", bloat_wh),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILL_TELEMETRY_USAGE_JSONL", bloat_usage),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_PROPOSALS_PATH", root / "none-prop.jsonl"),
                unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH", root / "none-roi.json"),
            ):
                fast_bloat = SKILL_RETRO.probe_skills(
                    handoff_root=handoff_root,
                    skills_root=skills_root,
                    dev_root=dev_root,
                    fast=True,
                    summary=True,
                )
                fast_bloat_by_sk = {s["skill"]: s for s in fast_bloat["skills"]}
                self.assertEqual(fast_bloat_by_sk["drifted-cli-skill"]["signals"]["aci_output_bloat_count"], 2)

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

            now = datetime.now(CST).replace(microsecond=0) - timedelta(minutes=10)
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

            # Counter-Case F6: `verify_grounding=True` without `--repo-root` (hermetically patched to `skills_root`),
            # with invalid repo_root, or when `git cat-file` raises SubprocessError/TimeoutError must fail closed!
            with unittest.mock.patch.object(SKILL_RETRO, "DEFAULT_SKILLS_ROOT", skills_root):
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

            with unittest.mock.patch.object(
                SKILL_RETRO.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["git", "cat-file"], timeout=2.5),
            ):
                rollup_git_err = SKILL_RETRO.rollup_m1_retro_receipts(
                    handoff_root=handoff_root,
                    skills=["gap-skill"],
                    repo_root=repo_dir,
                    verify_grounding=False,
                )
            self.assertFalse(rollup_git_err["receipt_grounded"])
            self.assertTrue(
                any("git_verification_error" in err for err in rollup_git_err["grounding_issues"])
            )


if __name__ == "__main__":
    unittest.main()
