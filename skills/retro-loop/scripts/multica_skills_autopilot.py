#!/usr/bin/env python3
"""Multica Skills ROI Autopilot Foundation.

Implements the deterministic Two-Stage Skills ROI Autopilot flow:
1. `collect`: Deterministically runs `compute_skill_roi_diagnostics()` over existing `aidata`
   (read-only `warehouse.db`, `~/.copilot/session-store.db`, `user-skills` inventory) with zero
   LLM tokens, extracting top low-ROI / high-repair-loop / high-unpracticed-rate skills and
   actionable remediation commands into a JSON + Markdown snapshot.
2. `file-issue`: Creates (or deduplicates) a Priority 3 (`--priority medium`) Actionable Issue
   on NAS Multica explicitly assigned to Owner (`lee@multica.local` / `1a895cfa-3ed5-45f5-aaf1-a24b10b502fc`),
   using `--status backlog` (or `--status todo`) so no runaway agent loop starts on assignment.
3. `setup-autopilot`: Idempotently creates or updates the NAS Multica Autopilot (`--mode run_only`,
   agent `M2-Dispatcher`, subscriber Owner) and configures its daily schedule trigger.
4. `run-once`: Executes `collect` -> optional Agent analysis refinement (or deterministic fallback) -> `file-issue`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_PATH = SCRIPT_DIR / "retro_story_engine.py"
ISSUE_BRIEF_SCRIPT_PATH = SCRIPT_DIR.parents[1] / "issue-brief" / "scripts" / "verify_issue_format.py"

DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_WEB_URL = "http://192.168.31.17:13000"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_OWNER_EMAIL = "lee@multica.local"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
DEFAULT_AUTOPILOT_TITLE = "Daily Skills ROI Diagnostic Autopilot (P3 -> Owner)"
DEFAULT_CRON = "20 8 * * *"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_COLLECT_OUTPUT = Path("/tmp/multica-skills-autopilot-collect.json")
DEFAULT_ANALYSIS_OUTPUT = Path("/tmp/multica-skills-autopilot-analysis.md")
DEFAULT_ISSUE_STATUS = "backlog"
DEFAULT_ISSUE_PRIORITY = "medium"

CST = timezone(timedelta(hours=8))


def _load_retro_engine() -> Any:
    spec = importlib.util.spec_from_file_location("retro_story_engine_autopilot", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load retro_story_engine from {ENGINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_issue_brief() -> Any:
    spec = importlib.util.spec_from_file_location("issue_brief_autopilot", ISSUE_BRIEF_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load verify_issue_format from {ISSUE_BRIEF_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_multica_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_bin = os.environ.get("MULTICA_BIN")
    if env_bin:
        return env_bin
    if Path("/opt/homebrew/bin/multica").is_file():
        return "/opt/homebrew/bin/multica"
    found = shutil.which("multica")
    return found or "/opt/homebrew/bin/multica"


def _resolve_script_display_path() -> str:
    canonical = Path.home() / "Development" / "user-skills" / "skills" / "retro-loop" / "scripts" / "multica_skills_autopilot.py"
    if canonical.is_file():
        return str(canonical)
    return str(Path(__file__).resolve())


def build_autopilot_prompt(
    *,
    script_path: str | None = None,
    collect_path: str = str(DEFAULT_COLLECT_OUTPUT),
    analysis_path: str = str(DEFAULT_ANALYSIS_OUTPUT),
    owner_id: str = DEFAULT_OWNER_ID,
    owner_email: str = DEFAULT_OWNER_EMAIL,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> str:
    resolved_script = script_path or (
        "$HOME/Development/user-skills/skills/retro-loop/scripts/multica_skills_autopilot.py"
    )
    script_expr = f'SCRIPT="{resolved_script}"'
    return (
        "# Daily Skills ROI Diagnostic Autopilot (`collect -> analyze -> P3 Actionable Issue`)\n\n"
        "Follow this strict 3-step execution protocol without modifying any business code or `aidata` schemas:\n\n"
        "1. **Stage 1 — Deterministic Script Collection (0 LLM Guesswork)**:\n"
        "   Run the deterministic collector over existing `aidata` (`warehouse.db` + `session-store.db` + `user-skills`):\n"
        "   ```bash\n"
        f"   {script_expr}\n"
        f'   python3 "$SCRIPT" collect --output "{collect_path}"\n'
        "   ```\n\n"
        "2. **Stage 2 — Agent Analysis & Root-Cause Synthesis**:\n"
        f"   Read `{collect_path}` and inspect `top_findings`, `S6_unpracticed_hotspots`, `S7_greedy_hotspots`, "
        "`S9_escape_hotspots`, `S3_skill_health`, `eval_effect`, and `S12_tool_context_burn`.\n"
        f"   Write a concise, evidence-backed Markdown root-cause & P3 action plan to `{analysis_path}` covering:\n"
        "   - Top 3 low-ROI / high-repair-loop / high-unpracticed-rate skills with exact numbers (`roi_score`, `r1_pass_rate`, "
        "`unpracticed_rate`, `impl_bug_escapes`, `spec_gap_escapes`);\n"
        "   - Concrete Physical Lock / script-sinking / eval-case actions (`skill_contract.py add-case --id auto`, L0/L1 budget trim, script wiring);\n"
        "   - Exact verification commands.\n\n"
        "3. **Stage 3 — File Priority 3 (`medium`) Actionable Issue Assigned to Owner**:\n"
        f"   Create (or deduplicate for today) the Priority 3 (`--priority medium`) Actionable Issue assigned directly to Owner `{owner_email}` (`{owner_id}`):\n"
        "   ```bash\n"
        f"   {script_expr}\n"
        f'   python3 "$SCRIPT" file-issue \\\n'
        f'     --input "{collect_path}" \\\n'
        f'     --analysis-file "{analysis_path}" \\\n'
        f'     --priority medium \\\n'
        f'     --status backlog \\\n'
        f'     --assignee-id "{owner_id}" \\\n'
        f'     --server-url "{server_url}" \\\n'
        f'     --workspace-id "{workspace_id}"\n'
        "   ```\n"
    )


def _pct(val: float | None) -> str:
    if val is None:
        return "unknown"
    return f"{val * 100:.1f}%"


def _remediation_commands_for_skill(row: dict[str, Any]) -> list[str]:
    sk = str(row.get("skill") or "").strip()
    if not sk:
        return []
    cmds: list[str] = []
    status = str(row.get("diagnostic_status") or "")
    impl_escapes = int(row.get("impl_bug_escapes") or 0)
    spec_escapes = int(row.get("spec_gap_escapes") or 0)
    unpracticed = row.get("unpracticed_rate")
    if status == "high_repair_loop" or impl_escapes > 0 or spec_escapes > 0:
        cmds.append(
            f"python3 skills/skill-contract/scripts/skill_contract.py add-case --skill {sk} --id auto "
            f'--title "Prevent recurring {sk} escape" --file skills/{sk}/SKILL.md --must-contain "{sk}"'
        )
    if unpracticed is not None and float(unpracticed) >= 0.5:
        cmds.append(
            f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict"
        )
    if not cmds:
        cmds.append(f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict")
    return cmds


def collect_skills_roi_snapshot(
    *,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    top_n: int = 5,
    diagnostics_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if diagnostics_fn is None:
        engine = _load_retro_engine()
        diagnostics_fn = engine.compute_skill_roi_diagnostics

    raw = diagnostics_fn(
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        skills_root=skills_root,
        dev_root=dev_root,
        magpie_usage=magpie_usage,
        dsh_sessions=dsh_sessions,
        auto_propose=False,
    )

    s10_rows: list[dict[str, Any]] = list(raw.get("S10_skill_roi_ranking") or [])
    actionable = [r for r in s10_rows if r.get("needs_optimization")]
    ranked_candidates = sorted(
        actionable if actionable else s10_rows,
        key=lambda r: (
            not bool(r.get("needs_optimization")),
            r.get("roi_score") is None,
            r.get("roi_score") if r.get("roi_score") is not None else float("inf"),
            -(float(r.get("post_invocation_repair_loop_rate") or 0.0)),
            -int(r.get("impl_bug_escapes") or 0),
            -int(r.get("spec_gap_escapes") or 0),
            -(float(r.get("unpracticed_rate") or 0.0)),
            str(r.get("skill") or ""),
        ),
    )[: max(1, top_n)]

    top_findings: list[dict[str, Any]] = []
    for idx, row in enumerate(ranked_candidates, 1):
        top_findings.append(
            {
                "rank": idx,
                "skill": row.get("skill"),
                "diagnostic_status": row.get("diagnostic_status"),
                "roi_score": row.get("roi_score"),
                "r1_pass_rate": row.get("r1_pass_rate"),
                "r1_total": row.get("r1_total"),
                "loads_30d": row.get("loads_30d"),
                "loads_7d": row.get("loads_7d"),
                "script_execs": row.get("script_execs"),
                "script_p95_ms": row.get("script_p95_ms"),
                "script_success_rate": row.get("script_success_rate"),
                "unpracticed_rate": row.get("unpracticed_rate"),
                "empty_load_rate": row.get("empty_load_rate"),
                "empty_load_sessions": row.get("empty_load_sessions"),
                "reference_hit_count": row.get("reference_hit_count"),
                "reference_bytes": row.get("reference_bytes"),
                "eval_interceptions": row.get("eval_interceptions"),
                "eval_escapes": row.get("eval_escapes"),
                "post_invocation_repair_loop_rate": row.get("post_invocation_repair_loop_rate"),
                "impl_bug_escapes": row.get("impl_bug_escapes"),
                "spec_gap_escapes": row.get("spec_gap_escapes"),
                "edit_churn_count": row.get("edit_churn_count"),
                "l1_greedy_violation_rate": row.get("l1_greedy_violation_rate"),
                "diagnosis": row.get("diagnosis"),
                "source_as_of": row.get("source_as_of"),
                "sample_size": row.get("sample_size"),
                "remediation_commands": _remediation_commands_for_skill(row),
            }
        )

    s6_hotspots = sorted(
        [r for r in (raw.get("S6_load_vs_script") or []) if (r.get("unpracticed_rate") or 0.0) >= 0.5],
        key=lambda r: (-(float(r.get("unpracticed_rate") or 0.0)), -int(r.get("loads_30d") or 0), str(r.get("skill") or "")),
    )[:5]
    s7_hotspots = sorted(
        [r for r in (raw.get("S7_disclosure_funnel") or []) if (r.get("l1_greedy_violation_rate") or 0.0) > 0.0],
        key=lambda r: (-(float(r.get("l1_greedy_violation_rate") or 0.0)), str(r.get("skill") or "")),
    )[:5]
    s9_hotspots = sorted(
        list(raw.get("S9_escape_attribution") or []),
        key=lambda r: (-(int(r.get("impl_bug_escapes") or 0) + int(r.get("spec_gap_escapes") or 0)), str(r.get("skill") or "")),
    )[:5]
    s3_health = dict(raw.get("S3_skill_health") or {})
    s1_pins = dict(raw.get("S1_pin_drift") or {})
    s12_burn = list(raw.get("S12_tool_context_burn") or [])[:5]
    s4_usage = dict(raw.get("S4_usage_and_r1") or {})
    s2_gate = dict(raw.get("S2_gate_catch") or {})
    eval_effect = dict(s2_gate.get("eval_effect") or {})
    observed_p95 = [float(row["script_p95_ms"]) for row in s10_rows if row.get("script_p95_ms") is not None]
    observed_reference_hits = sum(int(row.get("reference_hit_count") or 0) for row in s10_rows)

    now_cst = datetime.now(CST)
    collected_date = str(raw.get("wall_clock_date") or now_cst.date().isoformat())
    dedup_key = f"skills-roi-autopilot-{collected_date}"

    snapshot: dict[str, Any] = {
        "schema": "multica-skills-autopilot-collect/v1",
        "ok": True,
        "collected_at": now_cst.isoformat(timespec="seconds"),
        "collected_date": collected_date,
        "dedup_key": dedup_key,
        "target_priority": {
            "linear_priority_number": 3,
            "multica_priority": DEFAULT_ISSUE_PRIORITY,
            "label": "P3 / Priority 3 (medium)",
        },
        "target_owner": {
            "assignee_id": DEFAULT_OWNER_ID,
            "email": DEFAULT_OWNER_EMAIL,
        },
        "summary": {
            "window_cst": raw.get("window_cst", "7d"),
            "source_as_of": raw.get("source_as_of"),
            "sample_size": raw.get("sample_size"),
            "coverage_status": raw.get("coverage_status"),
            "window_anchor": raw.get("window_anchor"),
            "active_skills_count": s4_usage.get("active_skills_count", 0),
            "idle_skills_30d_count": len(s4_usage.get("idle_skills_30d") or []),
            "idle_skills_30d": list(s4_usage.get("idle_skills_30d") or [])[:10],
            "actionable_skills_count": len(actionable),
            "total_skills_audited": s3_health.get("total_skills", 0),
            "audit_pass_count": s3_health.get("audit_pass_count", 0),
            "audit_pass_rate": s3_health.get("audit_pass_rate"),
            "outdated_pin_count": s1_pins.get("outdated_pin_count", 0),
            "empty_load_rate": s4_usage.get("empty_load_rate"),
            "script_p95_ms": max(observed_p95) if observed_p95 else None,
            "reference_hit_count": observed_reference_hits,
            "eval_effect": eval_effect,
        },
        "top_findings": top_findings,
        "S6_unpracticed_hotspots": s6_hotspots,
        "S7_greedy_hotspots": s7_hotspots,
        "S9_escape_hotspots": s9_hotspots,
        "eval_effect": eval_effect,
        "S3_skill_health": {
            "total_skills": s3_health.get("total_skills"),
            "audit_pass_count": s3_health.get("audit_pass_count"),
            "audit_pass_rate": s3_health.get("audit_pass_rate"),
            "top3_l0_watermarks": s3_health.get("top3_l0_watermarks", []),
            "top3_l1_watermarks": s3_health.get("top3_l1_watermarks", []),
            "eval_case_total": s3_health.get("eval_case_total"),
            "eval_case_weekly_delta": s3_health.get("eval_case_weekly_delta"),
        },
        "S1_pin_drift_summary": {
            "outdated_pin_count": s1_pins.get("outdated_pin_count", 0),
            "all_up_to_date": s1_pins.get("all_up_to_date", True),
        },
        "S12_tool_context_burn": s12_burn,
        "recommendations": list(raw.get("recommendations") or []),
    }
    snapshot["default_issue_title"] = build_issue_title(snapshot)
    snapshot["markdown_brief"] = render_issue_markdown(snapshot)
    return snapshot


def build_issue_title(snapshot: dict[str, Any]) -> str:
    collected_date = str(snapshot.get("collected_date") or datetime.now(CST).date().isoformat())
    findings = snapshot.get("top_findings") or []
    skill_names = [str(item.get("skill")) for item in findings[:3] if item.get("skill")]
    focus = ", ".join(skill_names) if skill_names else "workspace skills"
    return f"[P3 / Priority 3][Skills ROI] {collected_date} 技能效能诊断与可执行优化清单 ({focus})"


def _plain_chinese_reason_for_skill(row: dict[str, Any]) -> str:
    sk = str(row.get("skill") or "skill").strip()
    unp = row.get("unpracticed_rate")
    r1_rate = row.get("r1_pass_rate")
    loads_30d = int(row.get("loads_30d") or 0)
    impl_esc = int(row.get("impl_bug_escapes") or 0)
    spec_esc = int(row.get("spec_gap_escapes") or 0)
    reasons: list[str] = []
    if impl_esc > 0 or spec_esc > 0:
        reasons.append(f"`{sk}` 近期实现缺陷 {impl_esc} 次、规格盲区 {spec_esc} 次，需将高频漏检下沉为确定性脚本")
    if isinstance(unp, (int, float)) and float(unp) >= 0.4:
        reasons.append(f"`{sk}` 30天加载 {loads_30d} 次中未调脚本率 {_pct(float(unp))}，需改为强制首选脚本入口")
    if not reasons:
        r1_txt = f"首轮通过率 {_pct(float(r1_rate))}" if isinstance(r1_rate, (int, float)) else f"30天调用 {loads_30d} 次"
        reasons.append(f"`{sk}`（{r1_txt}）需补齐可执行抽屉断言以收敛返工损耗")
    return "；".join(reasons[:2]) + "。"


def _build_ai_evidence_markdown(snapshot: dict[str, Any], *, agent_analysis: str | None = None) -> str:
    summary = snapshot.get("summary") or {}
    findings = snapshot.get("top_findings") or []
    s6_rows = snapshot.get("S6_unpracticed_hotspots") or []
    s9_rows = snapshot.get("S9_escape_hotspots") or []
    s3_health = snapshot.get("S3_skill_health") or {}
    s12_rows = snapshot.get("S12_tool_context_burn") or []
    dedup_key = str(snapshot.get("dedup_key") or "")

    lines: list[str] = [
        f"## [P3 / Priority 3] Skills ROI Actionable Diagnostic ({snapshot.get('collected_date')})",
        "",
        f"- **Dedup Marker**: `{dedup_key}`",
        f"- **Priority**: Priority 3 (`medium`)",
        f"- **Assigned Owner**: `{DEFAULT_OWNER_EMAIL}` (`{DEFAULT_OWNER_ID}`)",
        f"- **Observation Window**: `{summary.get('window_cst', '7d')}` (30d usage & 7d review freshness) · "
        f"`source_as_of={summary.get('source_as_of')}` · `sample_size={summary.get('sample_size')}` · "
        f"`coverage={summary.get('coverage_status')}`",
        f"- **Portfolio Health**: Active `{summary.get('active_skills_count')}` / Audited `{summary.get('total_skills_audited')}` "
        f"(Audit Pass `{_pct(summary.get('audit_pass_rate'))}`, Actionable `{summary.get('actionable_skills_count')}`, "
        f"Outdated Pins `{summary.get('outdated_pin_count')}`)",
        f"- **Gap Metrics**: Empty-load `{_pct(summary.get('empty_load_rate'))}` · Script p95 `{summary.get('script_p95_ms')} ms` · "
        f"Reference hits `{summary.get('reference_hit_count', 0)}` · Eval interceptions/escapes `"
        f"{(summary.get('eval_effect') or {}).get('interceptions_7d', 0)} / {(summary.get('eval_effect') or {}).get('escapes_7d', 0)}`",
        "",
    ]

    if agent_analysis and agent_analysis.strip():
        lines.extend(
            [
                "## Agent Root-Cause Analysis & Optimization Plan",
                "",
                agent_analysis.strip(),
                "",
            ]
        )

    lines.extend(
        [
            "## Deterministic `aidata` Evidence (`S10` / `S6` / `S9` / `S3`)",
            "",
            "| Rank | Skill | Status | ROI (`S10`) | R1 Pass | Unpracticed (`S6`) | Escapes (`IMPL` / `SPEC`) | Key Diagnosis |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )

    if findings:
        for item in findings:
            roi_txt = f"{item['roi_score']:.4f}" if isinstance(item.get("roi_score"), (int, float)) else "unknown"
            r1_txt = _pct(item.get("r1_pass_rate"))
            unp_txt = _pct(item.get("unpracticed_rate"))
            esc_txt = f"{item.get('impl_bug_escapes', 0)} / {item.get('spec_gap_escapes', 0)}"
            diag_txt = str(item.get("diagnosis") or "").replace("|", "\\|")
            lines.append(
                f"| {item.get('rank')} | `{item.get('skill')}` | `{item.get('diagnostic_status')}` | "
                f"{roi_txt} | {r1_txt} | {unp_txt} | {esc_txt} | {diag_txt} |"
            )
    else:
        lines.append("| — | — | `healthy` | — | — | — | 0 / 0 | No low-ROI anomalies detected |")

    lines.extend(["", "### Remediation Details & Script Wiring", ""])
    if findings:
        for item in findings[:3]:
            sk = item.get("skill")
            diag = item.get("diagnosis")
            cmds = item.get("remediation_commands") or []
            cmd_inline = f" — Run `{cmds[0]}`" if cmds else ""
            lines.append(f"- **`{sk}`** (`{item.get('diagnostic_status')}`): {diag}{cmd_inline}")
    if s3_health.get("top3_l0_watermarks"):
        over_cap = [
            w for w in s3_health["top3_l0_watermarks"]
            if int(w.get("l0_lines") or 0) > int(w.get("l0_cap") or 180)
        ]
        if over_cap:
            over_desc = ", ".join(f"`{w['skill']}` ({w['l0_lines']}/{w['l0_cap']} lines)" for w in over_cap)
            lines.append(
                f"- **L0 Budget Trim (`S3_skill_health`)**: Progressive-disclosure split for {over_desc} "
                "and verify via `python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict`."
            )
    if s6_rows:
        top_unp = ", ".join(
            f"`{r['skill']}` ({_pct(r.get('unpracticed_rate'))}, p95={r.get('script_p95_ms')}ms)"
            for r in s6_rows[:3]
        )
        lines.append(
            f"- **Script Touch Wiring (`S6_load_vs_script`)**: Convert prose-only instructions into deterministic CLI invocations for {top_unp}."
        )
    if s9_rows:
        top_esc = ", ".join(
            f"`{r['skill']}` (IMPL={r.get('impl_bug_escapes', 0)}, SPEC={r.get('spec_gap_escapes', 0)})"
            for r in s9_rows[:3]
        )
        lines.append(
            f"- **Escape Sink (`S9_escape_attribution`)**: Sink recurring review escapes into Valve 1 scripts & `eval-cases.json` for {top_esc}."
        )
    eval_effect = summary.get("eval_effect") or {}
    if eval_effect.get("interceptions_7d") or eval_effect.get("escapes_7d"):
        lines.append(
            f"- **Eval Gate Effect (`S2_gate_catch`)**: Review `{eval_effect.get('interceptions_7d', 0)}` interceptions versus "
            f"`{eval_effect.get('escapes_7d', 0)}` escapes across eval cases."
        )
    if summary.get("empty_load_rate") is not None:
        lines.append(
            f"- **Empty-load reduction (`B1`)**: Reduce loads with no references, scripts, or edits from "
            f"`{_pct(summary.get('empty_load_rate'))}`."
        )
    if s12_rows:
        top_burn = ", ".join(f"`{r['tool_name']}` (p95={r.get('p95_tokens')} tok)" for r in s12_rows[:2])
        lines.append(f"- **Tool Context Burn (`S12`)**: Bound output size on {top_burn}.")

    lines.extend(
        [
            "",
            "### Verification Commands",
            "",
            "```bash",
            "python3 skills/retro-loop/scripts/multica_skills_autopilot.py collect --output /tmp/multica-skills-autopilot-collect.json",
            "python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict",
            "./scripts/verify",
            "```",
        ]
    )
    return "\n".join(lines)


def build_issue_markdown(snapshot: dict[str, Any], *, agent_analysis: str | None = None) -> str:
    """Build Dual-Zone compliant Multica issue body via `issue-brief` contract."""
    issue_brief = _load_issue_brief()
    summary = snapshot.get("summary") or {}
    findings = snapshot.get("top_findings") or []
    dedup_key = str(snapshot.get("dedup_key") or f"skills-roi-autopilot-{snapshot.get('collected_date') or 'today'}")
    actionable_count = int(summary.get("actionable_skills_count") or len(findings))

    skill_names = [f"`{item.get('skill')}`" for item in findings[:3] if item.get("skill")]
    if skill_names:
        focus_txt = "、".join(skill_names)
        tldr = (
            f"今日技能巡检共发现 {actionable_count} 个待治理技能，返工与空读损耗主要集中在 "
            f"{focus_txt}，勾选对应项后即可由后台总控自动加锁修复。"
        )
    else:
        tldr = "今日技能效能巡检整体健康，未发现需要紧急返工治理的低效技能。"

    human_items: list[dict[str, Any]] = []
    for item in findings[:3]:
        sk = str(item.get("skill") or "").strip()
        if not sk:
            continue
        human_items.append(
            {
                "what": f"为 `{sk}` 补齐确定性防呆门禁",
                "why": _plain_chinese_reason_for_skill(item).rstrip("。"),
            }
        )
    if not human_items:
        human_items.append(
            {
                "what": "保持现有技能结构门禁与回归巡检",
                "why": "当前无高频返工热点，仅需维持日常零成本自动巡检",
            }
        )

    ai_context_md = _build_ai_evidence_markdown(snapshot, agent_analysis=agent_analysis)
    rendered = issue_brief.render_dual_zone_issue(
        tldr=tldr,
        items=human_items,
        cursor=dedup_key,
        extra_comments=[f"<!-- multica-skills-autopilot:{dedup_key} -->"],
        ai_context_md=ai_context_md,
    )
    verification = issue_brief.verify_dual_zone_issue(rendered)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone issue verification failed: {verification['violations']}")
    return rendered


def render_issue_markdown(snapshot: dict[str, Any], *, agent_analysis: str | None = None) -> str:
    return build_issue_markdown(snapshot, agent_analysis=agent_analysis)


def _default_multica_runner(
    args: list[str],
    *,
    stdin_text: str | None = None,
    multica_bin: str | None = None,
    server_url: str | None = None,
    workspace_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = _resolve_multica_bin(multica_bin)
    cmd = [binary]
    if server_url:
        cmd.extend(["--server-url", server_url])
    if workspace_id:
        cmd.extend(["--workspace-id", workspace_id])
    cmd.extend(args)
    env = dict(os.environ)
    if server_url:
        env["MULTICA_SERVER_URL"] = server_url
    if workspace_id:
        env["MULTICA_WORKSPACE_ID"] = workspace_id
    return subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _parse_json_stdout(proc: subprocess.CompletedProcess[str], context: str) -> Any:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"multica {context} failed (exit {proc.returncode}): {detail}")
    text = (proc.stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"multica {context} returned invalid JSON: {text[:400]}") from exc


def file_actionable_issue(
    snapshot: dict[str, Any],
    *,
    analysis_markdown: str | None = None,
    title: str | None = None,
    priority: str = DEFAULT_ISSUE_PRIORITY,
    status: str = DEFAULT_ISSUE_STATUS,
    assignee_id: str = DEFAULT_OWNER_ID,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    allow_duplicate: bool = False,
    refresh_existing: bool = False,
    update_issue_id: str | None = None,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    issue_title = (title or snapshot.get("default_issue_title") or build_issue_title(snapshot)).strip()
    if "[P3" not in issue_title and "Priority 3" not in issue_title:
        issue_title = f"[P3 / Priority 3] {issue_title}"
    description = build_issue_markdown(snapshot, agent_analysis=analysis_markdown)
    verification = _load_issue_brief().verify_dual_zone_issue(description)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone issue verification failed before Multica publish: {verification['violations']}")
    dedup_key = str(snapshot.get("dedup_key") or "")
    dedup_marker = f"multica-skills-autopilot:{dedup_key}" if dedup_key else ""
    cursor_marker = f"multica-cursor:{dedup_key}" if dedup_key else ""

    if dry_run:
        return {
            "ok": True,
            "action": "dry_run",
            "dedup_key": dedup_key,
            "title": issue_title,
            "priority": priority,
            "status": status,
            "assignee_id": assignee_id,
            "description": description,
            "dual_zone_verification": verification,
        }

    if update_issue_id:
        upd_proc = runner(
            [
                "issue",
                "update",
                update_issue_id,
                "--title",
                issue_title,
                "--description-stdin",
                "--priority",
                priority,
                "--assignee-id",
                assignee_id,
                "--status",
                status,
                "--no-start",
                "--output",
                "json",
            ],
            stdin_text=description,
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        updated = _parse_json_stdout(upd_proc, "issue update")
        return {
            "ok": True,
            "action": "updated_existing",
            "dedup_key": dedup_key,
            "priority": priority,
            "status": status,
            "assignee_id": assignee_id,
            "issue": updated,
        }

    if not allow_duplicate:
        list_proc = runner(
            [
                "issue",
                "list",
                "--assignee-id",
                assignee_id,
                "--limit",
                "50",
                "--output",
                "json",
            ],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        listed = _parse_json_stdout(list_proc, "issue list")
        issues = listed.get("issues", []) if isinstance(listed, dict) else (listed if isinstance(listed, list) else [])
        for existing in issues:
            if not isinstance(existing, dict):
                continue
            ex_status = str(existing.get("status") or existing.get("status_category") or "").lower()
            if ex_status in {"done", "cancelled", "canceled"}:
                continue
            ex_title = str(existing.get("title") or "")
            ex_desc = str(existing.get("description") or "")
            if ex_title == issue_title or (dedup_marker and dedup_marker in ex_desc) or (cursor_marker and cursor_marker in ex_desc):
                if refresh_existing and (existing.get("id") or existing.get("identifier")):
                    target_id = str(existing.get("id") or existing.get("identifier"))
                    upd_proc = runner(
                        [
                            "issue",
                            "update",
                            target_id,
                            "--title",
                            issue_title,
                            "--description-stdin",
                            "--priority",
                            priority,
                            "--no-start",
                            "--output",
                            "json",
                        ],
                        stdin_text=description,
                        multica_bin=multica_bin,
                        server_url=server_url,
                        workspace_id=workspace_id,
                    )
                    updated = _parse_json_stdout(upd_proc, "issue update")
                    return {
                        "ok": True,
                        "action": "refreshed_existing",
                        "dedup_key": dedup_key,
                        "issue": updated,
                    }
                return {
                    "ok": True,
                    "action": "deduplicated_existing",
                    "dedup_key": dedup_key,
                    "issue": existing,
                }

    create_args = [
        "issue",
        "create",
        "--title",
        issue_title,
        "--description-stdin",
        "--priority",
        priority,
        "--assignee-id",
        assignee_id,
        "--status",
        status,
        "--output",
        "json",
    ]
    if allow_duplicate:
        create_args.append("--allow-duplicate")

    create_proc = runner(
        create_args,
        stdin_text=description,
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    created = _parse_json_stdout(create_proc, "issue create")
    return {
        "ok": True,
        "action": "created",
        "dedup_key": dedup_key,
        "priority": priority,
        "status": status,
        "assignee_id": assignee_id,
        "issue": created,
    }


def setup_multica_autopilot(
    *,
    title: str = DEFAULT_AUTOPILOT_TITLE,
    agent_id: str = DEFAULT_AGENT_ID,
    subscriber_id: str = DEFAULT_OWNER_ID,
    mode: str = "run_only",
    cron_expr: str = DEFAULT_CRON,
    tz_name: str = DEFAULT_TIMEZONE,
    trigger_label: str = "Daily CST Morning Skills ROI Diagnostic",
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    collect_output: str = str(DEFAULT_COLLECT_OUTPUT),
    analysis_output: str = str(DEFAULT_ANALYSIS_OUTPUT),
    trigger_now: bool = False,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    prompt = build_autopilot_prompt(
        collect_path=collect_output,
        analysis_path=analysis_output,
        owner_id=subscriber_id,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    if dry_run:
        return {
            "ok": True,
            "action": "dry_run",
            "title": title,
            "mode": mode,
            "agent_id": agent_id,
            "subscriber_id": subscriber_id,
            "cron": cron_expr,
            "timezone": tz_name,
            "prompt": prompt,
        }

    list_proc = runner(
        ["autopilot", "list", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    listed = _parse_json_stdout(list_proc, "autopilot list")
    autopilots = listed.get("autopilots", []) if isinstance(listed, dict) else (listed if isinstance(listed, list) else [])
    existing = next((ap for ap in autopilots if isinstance(ap, dict) and ap.get("title") == title), None)

    if existing is not None:
        autopilot_id = str(existing["id"])
        upd_proc = runner(
            [
                "autopilot",
                "update",
                autopilot_id,
                "--title",
                title,
                "--agent",
                agent_id,
                "--mode",
                mode,
                "--status",
                "active",
                "--description",
                prompt,
                "--subscriber",
                subscriber_id,
                "--output",
                "json",
            ],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        autopilot_obj = _parse_json_stdout(upd_proc, "autopilot update")
        autopilot_action = "updated"
    else:
        create_proc = runner(
            [
                "autopilot",
                "create",
                "--title",
                title,
                "--agent",
                agent_id,
                "--mode",
                mode,
                "--description",
                prompt,
                "--subscriber",
                subscriber_id,
                "--output",
                "json",
            ],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        autopilot_obj = _parse_json_stdout(create_proc, "autopilot create")
        autopilot_id = str(autopilot_obj.get("id") or (autopilot_obj.get("autopilot") or {}).get("id") or "")
        if not autopilot_id:
            raise RuntimeError(f"Could not extract autopilot id from create response: {autopilot_obj}")
        autopilot_action = "created"

    trig_list_proc = runner(
        ["autopilot", "trigger-list", autopilot_id, "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    trig_listed = _parse_json_stdout(trig_list_proc, "autopilot trigger-list")
    triggers = (
        trig_listed.get("triggers", [])
        if isinstance(trig_listed, dict)
        else (trig_listed if isinstance(trig_listed, list) else [])
    )
    schedule_trig = next(
        (t for t in triggers if isinstance(t, dict) and str(t.get("kind") or "schedule") == "schedule"),
        None,
    )

    if schedule_trig is not None:
        trig_id = str(schedule_trig["id"])
        same_schedule = (
            str(schedule_trig.get("cron") or schedule_trig.get("cron_expression") or "") == cron_expr
            and str(schedule_trig.get("timezone") or "") == tz_name
        )
        if same_schedule:
            trigger_obj = schedule_trig
            trigger_action = "unchanged"
        else:
            trig_upd_proc = runner(
                [
                    "autopilot",
                    "trigger-update",
                    trig_id,
                    "--cron",
                    cron_expr,
                    "--timezone",
                    tz_name,
                    "--label",
                    trigger_label,
                    "--output",
                    "json",
                ],
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            trigger_obj = _parse_json_stdout(trig_upd_proc, "autopilot trigger-update")
            trigger_action = "updated"
    else:
        trig_add_proc = runner(
            [
                "autopilot",
                "trigger-add",
                autopilot_id,
                "--kind",
                "schedule",
                "--cron",
                cron_expr,
                "--timezone",
                tz_name,
                "--label",
                trigger_label,
                "--output",
                "json",
            ],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        trigger_obj = _parse_json_stdout(trig_add_proc, "autopilot trigger-add")
        trigger_action = "created"

    triggered_run = None
    if trigger_now:
        trig_now_proc = runner(
            ["autopilot", "trigger", autopilot_id, "--output", "json"],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        triggered_run = _parse_json_stdout(trig_now_proc, "autopilot trigger")

    return {
        "ok": True,
        "autopilot_action": autopilot_action,
        "autopilot_id": autopilot_id,
        "autopilot": autopilot_obj,
        "trigger_action": trigger_action,
        "trigger": trigger_obj,
        "triggered_run": triggered_run,
    }


def synthesize_deterministic_analysis(snapshot: dict[str, Any]) -> str:
    """Synthesize structured root-cause analysis when no external Agent markdown is supplied."""
    findings = snapshot.get("top_findings") or []
    s3_health = snapshot.get("S3_skill_health") or {}
    summary = snapshot.get("summary") or {}
    bullets: list[str] = [
        f"基于现有 `aidata` 样本（`sample_size={summary.get('sample_size')}`, `source_as_of={summary.get('source_as_of')}`），"
        f"当前共有 **{summary.get('actionable_skills_count', 0)}** 个技能处于需治理状态（总技能包 `{summary.get('total_skills_audited')}`，"
        f"四阀门审计通过率 `{_pct(summary.get('audit_pass_rate'))}`）：",
        "",
    ]
    for item in findings[:3]:
        sk = item.get("skill")
        roi_val = f"{item['roi_score']:.4f}" if isinstance(item.get("roi_score"), (int, float)) else "unknown (缺 R1/Token 交叉样本)"
        bullets.append(
            f"{item.get('rank')}. **`{sk}` (`{item.get('diagnostic_status')}`, ROI={roi_val})**："
            f"{item.get('diagnosis')}。30d 触发 `{item.get('loads_30d')}` 次，脚本触达 `{item.get('script_execs')}` 次"
            f"（光读不练率 `{_pct(item.get('unpracticed_rate'))}`），`[IMPL_BUG]` 漏网 `{item.get('impl_bug_escapes')}` 次、"
            f"`[SPEC_GAP]` 盲区 `{item.get('spec_gap_escapes')}` 次。"
            f"建议优先将高频返工规则下沉至确定性脚本并用 `skill_contract.py add-case --skill {sk} --id auto` 固化回归断言。"
        )
    over_l0 = [
        w for w in (s3_health.get("top3_l0_watermarks") or [])
        if int(w.get("l0_lines") or 0) > int(w.get("l0_cap") or 180)
    ]
    if over_l0:
        names = "、".join(f"`{w['skill']}` ({w['l0_lines']} 行)" for w in over_l0)
        bullets.append(
            f"- **结构门禁水位（`S3_skill_health`）**：{names} 超出 180 行 L0 预算，建议按渐进式披露拆入 `references/` 抽屉以降低冷启动上下文开销。"
        )
    return "\n".join(bullets)


def run_once_pipeline(
    *,
    collect_output: Path = DEFAULT_COLLECT_OUTPUT,
    analysis_file: Path | None = None,
    analysis_cmd: str | None = None,
    priority: str = DEFAULT_ISSUE_PRIORITY,
    status: str = DEFAULT_ISSUE_STATUS,
    assignee_id: str = DEFAULT_OWNER_ID,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    allow_duplicate: bool = False,
    refresh_existing: bool = False,
    update_issue_id: str | None = None,
    dry_run: bool = False,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    diagnostics_fn: Callable[..., dict[str, Any]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    snapshot = collect_skills_roi_snapshot(
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        skills_root=skills_root,
        dev_root=dev_root,
        magpie_usage=magpie_usage,
        dsh_sessions=dsh_sessions,
        diagnostics_fn=diagnostics_fn,
    )
    collect_output.parent.mkdir(parents=True, exist_ok=True)
    collect_output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    analysis_md: str | None = None
    if analysis_cmd:
        env = dict(os.environ)
        env["SKILLS_ROI_COLLECT_JSON"] = str(collect_output)
        proc = subprocess.run(analysis_cmd, shell=True, capture_output=True, text=True, env=env, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"analysis-cmd failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
        if analysis_file and analysis_file.is_file():
            analysis_md = analysis_file.read_text(encoding="utf-8")
        else:
            analysis_md = proc.stdout.strip()
    elif analysis_file and analysis_file.is_file():
        analysis_md = analysis_file.read_text(encoding="utf-8")
    else:
        analysis_md = synthesize_deterministic_analysis(snapshot)

    issue_result = file_actionable_issue(
        snapshot,
        analysis_markdown=analysis_md,
        priority=priority,
        status=status,
        assignee_id=assignee_id,
        server_url=server_url,
        workspace_id=workspace_id,
        allow_duplicate=allow_duplicate,
        refresh_existing=refresh_existing,
        update_issue_id=update_issue_id,
        dry_run=dry_run,
        runner=runner,
        multica_bin=multica_bin,
    )
    return {
        "ok": True,
        "collect_output": str(collect_output),
        "collected_date": snapshot.get("collected_date"),
        "top_findings_count": len(snapshot.get("top_findings") or []),
        "issue_result": issue_result,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multica_skills_autopilot.py",
        description="Two-stage NAS Multica Skills ROI Autopilot (collect -> agent analyze -> file P3 issue to Owner).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_collect = sub.add_parser("collect", help="Deterministically collect existing aidata Skills ROI snapshot.")
    p_collect.add_argument("--warehouse-db", type=Path, default=None)
    p_collect.add_argument("--copilot-db", type=Path, default=None)
    p_collect.add_argument("--skills-root", type=Path, default=None)
    p_collect.add_argument("--dev-root", type=Path, default=None)
    p_collect.add_argument("--magpie-usage", type=Path, default=None)
    p_collect.add_argument("--dsh-sessions", type=Path, default=None)
    p_collect.add_argument("--top-n", type=int, default=5)
    p_collect.add_argument("--output", type=Path, default=None, help="Optional JSON output file path.")
    p_collect.add_argument("--markdown-output", type=Path, default=None, help="Optional rendered Markdown output path.")

    p_issue = sub.add_parser("file-issue", help="Create or deduplicate a Priority 3 (medium) Actionable Issue assigned to Owner.")
    p_issue.add_argument("--input", type=Path, default=DEFAULT_COLLECT_OUTPUT, help="Collected snapshot JSON path.")
    p_issue.add_argument("--analysis-file", type=Path, default=None, help="Optional Agent-refined Markdown analysis path.")
    p_issue.add_argument("--analysis-text", default="", help="Optional inline Agent analysis Markdown.")
    p_issue.add_argument("--title", default=None, help="Override issue title.")
    p_issue.add_argument("--priority", default=DEFAULT_ISSUE_PRIORITY, choices=("urgent", "high", "medium", "low", "none"))
    p_issue.add_argument("--status", default=DEFAULT_ISSUE_STATUS, help="Initial Multica issue status (default: backlog).")
    p_issue.add_argument("--assignee-id", default=DEFAULT_OWNER_ID, help="Owner user UUID on Multica.")
    p_issue.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_issue.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_issue.add_argument("--multica-bin", default=None)
    p_issue.add_argument("--allow-duplicate", action="store_true")
    p_issue.add_argument("--refresh-existing", action="store_true", help="Update matching open issue body in place.")
    p_issue.add_argument("--update-issue-id", default=None, help="Explicit Multica issue ID/key (e.g. TIA-1) to update in place.")
    p_issue.add_argument("--dry-run", action="store_true")

    p_setup = sub.add_parser("setup-autopilot", help="Idempotently configure NAS Multica Autopilot & daily schedule trigger.")
    p_setup.add_argument("--title", default=DEFAULT_AUTOPILOT_TITLE)
    p_setup.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    p_setup.add_argument("--subscriber-id", default=DEFAULT_OWNER_ID)
    p_setup.add_argument("--mode", default="run_only", choices=("run_only", "create_issue"))
    p_setup.add_argument("--cron", default=DEFAULT_CRON)
    p_setup.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    p_setup.add_argument("--trigger-label", default="Daily CST Morning Skills ROI Diagnostic")
    p_setup.add_argument("--collect-output", default=str(DEFAULT_COLLECT_OUTPUT))
    p_setup.add_argument("--analysis-output", default=str(DEFAULT_ANALYSIS_OUTPUT))
    p_setup.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_setup.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_setup.add_argument("--multica-bin", default=None)
    p_setup.add_argument("--trigger-now", action="store_true", help="Immediately trigger one autopilot run after setup.")
    p_setup.add_argument("--dry-run", action="store_true")

    p_run = sub.add_parser("run-once", help="Collect existing aidata Skills ROI, attach analysis, and file P3 issue to Owner.")
    p_run.add_argument("--output", type=Path, default=DEFAULT_COLLECT_OUTPUT)
    p_run.add_argument("--analysis-file", type=Path, default=None)
    p_run.add_argument("--analysis-cmd", default=None, help="Optional shell command to generate Agent analysis before filing.")
    p_run.add_argument("--priority", default=DEFAULT_ISSUE_PRIORITY, choices=("urgent", "high", "medium", "low", "none"))
    p_run.add_argument("--status", default=DEFAULT_ISSUE_STATUS)
    p_run.add_argument("--assignee-id", default=DEFAULT_OWNER_ID)
    p_run.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_run.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_run.add_argument("--multica-bin", default=None)
    p_run.add_argument("--warehouse-db", type=Path, default=None)
    p_run.add_argument("--copilot-db", type=Path, default=None)
    p_run.add_argument("--skills-root", type=Path, default=None)
    p_run.add_argument("--dev-root", type=Path, default=None)
    p_run.add_argument("--magpie-usage", type=Path, default=None)
    p_run.add_argument("--dsh-sessions", type=Path, default=None)
    p_run.add_argument("--allow-duplicate", action="store_true")
    p_run.add_argument("--refresh-existing", action="store_true", help="Update matching open issue body in place.")
    p_run.add_argument("--update-issue-id", default=None, help="Explicit Multica issue ID/key (e.g. TIA-1) to update in place.")
    p_run.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if ns.subcommand == "collect":
        snapshot = collect_skills_roi_snapshot(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
            top_n=ns.top_n,
        )
        if ns.output is not None:
            ns.output.parent.mkdir(parents=True, exist_ok=True)
            ns.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if ns.markdown_output is not None:
            ns.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            ns.markdown_output.write_text(str(snapshot.get("markdown_brief") or ""), encoding="utf-8")
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand == "file-issue":
        if not ns.input.is_file():
            raise SystemExit(f"Collected snapshot file not found: {ns.input}")
        snapshot = json.loads(ns.input.read_text(encoding="utf-8"))
        analysis_md: str | None = None
        if ns.analysis_file is not None:
            if not ns.analysis_file.is_file():
                raise SystemExit(f"Analysis file not found: {ns.analysis_file}")
            analysis_md = ns.analysis_file.read_text(encoding="utf-8")
        elif ns.analysis_text.strip():
            analysis_md = ns.analysis_text
        else:
            analysis_md = synthesize_deterministic_analysis(snapshot)

        result = file_actionable_issue(
            snapshot,
            analysis_markdown=analysis_md,
            title=ns.title,
            priority=ns.priority,
            status=ns.status,
            assignee_id=ns.assignee_id,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
            allow_duplicate=ns.allow_duplicate,
            refresh_existing=ns.refresh_existing,
            update_issue_id=ns.update_issue_id,
            dry_run=ns.dry_run,
            multica_bin=ns.multica_bin,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand == "setup-autopilot":
        result = setup_multica_autopilot(
            title=ns.title,
            agent_id=ns.agent_id,
            subscriber_id=ns.subscriber_id,
            mode=ns.mode,
            cron_expr=ns.cron,
            tz_name=ns.timezone,
            trigger_label=ns.trigger_label,
            collect_output=ns.collect_output,
            analysis_output=ns.analysis_output,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
            trigger_now=ns.trigger_now,
            dry_run=ns.dry_run,
            multica_bin=ns.multica_bin,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand == "run-once":
        result = run_once_pipeline(
            collect_output=ns.output,
            analysis_file=ns.analysis_file,
            analysis_cmd=ns.analysis_cmd,
            priority=ns.priority,
            status=ns.status,
            assignee_id=ns.assignee_id,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
            allow_duplicate=ns.allow_duplicate,
            refresh_existing=ns.refresh_existing,
            update_issue_id=ns.update_issue_id,
            dry_run=ns.dry_run,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
            multica_bin=ns.multica_bin,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    raise SystemExit(f"Unsupported subcommand: {ns.subcommand}")


if __name__ == "__main__":
    raise SystemExit(main())
