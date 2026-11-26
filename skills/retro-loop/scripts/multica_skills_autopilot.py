#!/usr/bin/env python3
"""Multica Skill Progressive Retro Autopilot (`multica_skills_autopilot.py`).

Implements the deterministic 4-Layer Progressive Skill Retro (`skill-retro`) Autopilot flow:
1. `collect`: Deterministically runs `retro_story_engine.py skill-retro` over existing telemetry
   (`warehouse.db`, `~/.copilot/session-state/*/events.jsonl`, `~/.local/share/skill-telemetry/usage.jsonl`,
   `~/.copilot/session-store.db`, `user-skills` inventory) with zero LLM tokens, extracting
   L0 trigger/drift guardrails, L1 context-aware drawer metabolism, L2 script efficacy/roadblocks/speed,
   and L3 wild pattern crystallization + Dual-Flywheel cohort gating into a JSON + Dual-Zone Markdown snapshot.
2. `file-issue`: Creates (or deduplicates) a Priority 3 (`--priority medium`) Actionable Issue
   on NAS Multica explicitly assigned to Owner (`lee@multica.local` / `1a895cfa-3ed5-45f5-aaf1-a24b10b502fc`),
   using `--status backlog` (or `--status todo`) so no runaway agent loop starts on assignment.
3. `setup-autopilot`: Idempotently updates in-place (matching `4e06bf2a` or legacy titles so zero
   duplicate autopilots are created) or creates the NAS Multica Autopilot (`--mode run_only`,
   agent `M2-Dispatcher`, subscriber Owner) and configures its `20 4 * * *` (`Asia/Shanghai`) schedule trigger.
4. `run-once`: Executes `collect` -> optional Agent analysis refinement (or deterministic synthesis) -> `file-issue`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_PATH = SCRIPT_DIR / "retro_story_engine.py"
SKILL_RETRO_SCRIPT_PATH = SCRIPT_DIR.parents[1] / "skill-retro" / "scripts" / "skill_retro.py"
ISSUE_BRIEF_SCRIPT_PATH = SCRIPT_DIR.parents[1] / "issue-brief" / "scripts" / "verify_issue_format.py"

DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_WEB_URL = "http://192.168.31.17:13000"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_OWNER_EMAIL = "lee@multica.local"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
CANONICAL_SKILL_RETRO_AUTOPILOT_ID_PREFIX = "4e06bf2a"
DEFAULT_AUTOPILOT_TITLE = "Daily Skill Progressive Retro Autopilot (Auto-Fix + Daily Brief Digest)"
DEFAULT_TRIGGER_LABEL = "Daily CST 04:20 Skill Progressive Retro Diagnostic"
DEFAULT_CRON = "20 4 * * *"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_COLLECT_OUTPUT = Path("/tmp/multica-skills-autopilot-collect.json")
DEFAULT_ANALYSIS_OUTPUT = Path("/tmp/multica-skills-autopilot-analysis.md")
DEFAULT_ISSUE_STATUS = "backlog"
DEFAULT_ISSUE_PRIORITY = "medium"

CST = timezone(timedelta(hours=8))


def _is_safe_local_file(path: Path | None, *, timeout_sec: float = 0.5) -> bool:
    """Delegate non-blocking `SF_DATALESS` / `Mobile Documents` / 0.5s open check to `skill_retro.py`."""
    if path is None:
        return False
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if int(getattr(st, "st_flags", 0) or 0) & 0x40000000:
        return False
    raw = str(path)
    if (
        ("Mobile Documents" in raw or "com~apple~CloudDocs" in raw or "/Volumes/" in raw)
        and getattr(st, "st_blocks", None) is not None
        and int(st.st_blocks) == 0
        and int(getattr(st, "st_size", 0) or 0) > 0
    ):
        return False
    return path.is_file()


def _probe_readable_dir(path: Path | None) -> bool:
    """Fast non-blocking directory probe; returns False immediately on macOS TCC / external volume EINTR/OSError."""
    if path is None:
        return False
    try:
        if not path.is_dir():
            return False
        with os.scandir(path) as it:
            next(it, None)
        return True
    except OSError:
        return False


def _default_skill_retro_watermark_path() -> Path:
    override = os.environ.get("MULTICA_SKILL_RETRO_WATERMARK_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "multica-skills-autopilot" / "skill-retro-watermark.json"


def _compute_cohort_watermark(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _load_skill_retro_watermark(path: Path | None = None) -> dict[str, Any]:
    target = path or _default_skill_retro_watermark_path()
    if not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_skill_retro_watermark(state: dict[str, Any], path: Path | None = None) -> None:
    target = path or _default_skill_retro_watermark_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass


def _sanitize_human_zone_label(text: str) -> str:
    cleaned = re.sub(
        r"\[Skill Retro\s+L[0-3]\]\s*",
        "",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_-])[0-9a-f]{7,40}(?![A-Za-z0-9_-])", "", cleaned)
    # Neutralize embedded shell/CLI command snippets so Human Zone never trips CLI_COMMAND_RE;
    # exact command strings remain preserved in `title`, `plain_reason`, `action`, and `<details>`.
    cleaned = re.sub(
        r"`[^`\n]*(?:python3?|pytest|bash|sh|curl|gh|multica|git|\.\/scripts\/|skills\/[A-Za-z0-9_-]+\/scripts\/)[^`\n]*`",
        "该高频探针模式",
        cleaned,
    )
    cleaned = re.sub(
        r"(?:(?<![A-Za-z0-9_/-])(?:python3?|pytest|bash|sh|curl|gh|multica|git)\s+\S+(?:\s+--?[A-Za-z0-9_-]+|\s+\S+\.py\b)(?:\s+--?[A-Za-z0-9_=.-]+)*)"
        r"|(?:\./scripts/[A-Za-z0-9_./-]+)"
        r"|(?:\bskills/[A-Za-z0-9_-]+/scripts/[A-Za-z0-9_.-]+\.py\b)",
        "脚本入口",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ·—-：:")
    return cleaned


_PSEUDO_SKILLS = frozenset({"skills", "skill", "scripts", "references", "tests", "main", "*"})


def _resolve_proposal_skill(prop: Mapping[str, Any]) -> str:
    raw = str(prop.get("skill") or "").strip()
    if raw == "repo-gate":
        return "repo-kit"
    if raw and raw.lower() not in _PSEUDO_SKILLS:
        return raw
    rule_id = str(prop.get("rule_id") or "").strip().upper()
    hints = " ".join(str(prop.get(k) or "") for k in ("id", "ref", "metric_id")).lower()
    if rule_id.startswith("RG-") or "repo-kit" in hints or "repo-gate" in hints:
        return "repo-kit"
    return ""


def _proposal_action_cell(prop: Mapping[str, Any], sk: str) -> str:
    rule_id = str(prop.get("rule_id") or "").strip()
    cat = str(prop.get("category") or prop.get("diagnostic_status") or "contract").strip()
    title = str(prop.get("title") or prop.get("diagnosis") or "").strip().replace("|", "\\|")
    action = str(prop.get("action") or prop.get("plain_reason") or "").strip().replace("|", "\\|")
    if not title:
        title = f"[{rule_id}] `{sk}` (`{cat}`)" if rule_id else f"Audit `{sk}` (`{cat}`)"
    if not action or action == title:
        action = f"Run `{sk}` remediation & contract verification"
    return f"{title} — {action}"


def _remediation_commands_for_proposal(prop: Mapping[str, Any]) -> list[str]:
    sk = _resolve_proposal_skill(prop)
    cmds: list[str] = []
    if sk:
        cmds.append(
            f"python3 skills/skill-retro/scripts/skill_retro.py emit-briefs --skill {sk} "
            f"--emit-briefs ~/Development/handoff/skill-retro-{sk} --json"
        )
        cmds.append(f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict")
    else:
        cmds.append("python3 skills/skill-retro/scripts/skill_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json")
        cmds.append("python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict")
    return cmds


def _summarize_skill_retro_report(raw_retro: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_retro, Mapping) or not raw_retro.get("ok"):
        return {
            "enabled": False,
            "schema": None,
            "source_as_of": None,
            "sample_size": 0,
            "coverage_status": "unknown",
            "slow_flywheel_ready": False,
            "high_signal_proposals_count": 0,
            "observation_pool_count": 0,
            "proposals_written": 0,
            "summary": {},
            "proposals": [],
            "observation_pool": [],
            "layer0_direction_and_triggers": {},
            "layer1_drawer_metabolism": {},
            "layer2_script_efficacy_and_speed": {},
            "layer3_wild_patterns_and_eval": {},
            "cohort_watermark": "",
        }
    retro_summary = dict(raw_retro.get("summary") or {})
    raw_proposals = [p for p in (raw_retro.get("proposals") or []) if isinstance(p, Mapping)]
    proposals: list[dict[str, Any]] = []
    for prop in raw_proposals:
        sk = _resolve_proposal_skill(prop)
        if not sk or sk.lower() in _PSEUDO_SKILLS:
            continue
        rule_id = str(prop.get("rule_id") or "").strip().upper()
        cat = str(prop.get("category") or "").strip()
        layer = str(prop.get("layer") or ("L2" if (rule_id.startswith("RG-") or sk == "repo-kit") else "L0"))
        raw_title = str(prop.get("title") or "").strip() or (
            f"[Repo Gate {rule_id}] `{sk}` ({cat or 'gate_drift'})" if rule_id else f"[{layer}] `{sk}` ({cat or 'retro'})"
        )
        raw_act = str(prop.get("action") or "").strip() or (
            f"Apply `{sk}` remediation for `{rule_id or cat or layer}` and run `./scripts/verify`."
        )
        raw_reason = str(prop.get("plain_reason") or raw_act).strip()
        human_what = _sanitize_human_zone_label(raw_title) or f"治理 `{sk}` ({rule_id or cat or layer})"
        if "该高频探针模式" in human_what and cat == "wild_pattern_crystallization":
            human_what = f"为 `{sk}` 柔性沉淀高频手工探针脚本"
        human_why = _sanitize_human_zone_label(raw_reason).rstrip("。") or f"`{sk}` 观测到高信号摩擦或漏检样本"
        proposals.append(
            {
                "id": prop.get("id"),
                "ref": prop.get("ref"),
                "metric_id": prop.get("metric_id"),
                "rule_id": rule_id or prop.get("rule_id"),
                "skill": sk,
                "layer": layer,
                "category": cat,
                "priority": prop.get("priority") or "P2",
                "title": raw_title,
                "plain_reason": raw_reason,
                "action": raw_act,
                "detail": prop.get("detail"),
                "scope_triage": dict(prop.get("scope_triage")) if isinstance(prop.get("scope_triage"), Mapping) else {},
                "human_what": human_what[:46],
                "human_why": human_why[:64],
                "remediation_commands": _remediation_commands_for_proposal({**prop, "skill": sk}),
            }
        )
        if len(proposals) >= 6:
            break
    obs_pool = [
        {
            "id": item.get("id"),
            "skill": sk_obs,
            "layer": item.get("layer"),
            "category": item.get("category"),
            "title": item.get("title"),
        }
        for item in (raw_retro.get("observation_pool") or [])
        if isinstance(item, Mapping) and (sk_obs := _resolve_proposal_skill(item))
    ][:6]
    cohort_sig = _compute_cohort_watermark(
        {
            "proposals": [
                {
                    "id": p.get("id"),
                    "skill": p.get("skill"),
                    "layer": p.get("layer"),
                    "category": p.get("category"),
                    "priority": p.get("priority"),
                }
                for p in proposals
            ],
            "slow_flywheel_ready": bool(retro_summary.get("slow_flywheel_ready")),
        }
    )
    layer0 = dict(raw_retro.get("layer0_direction_and_triggers") or {})
    layer1 = dict(raw_retro.get("layer1_drawer_metabolism") or {})
    layer2 = dict(raw_retro.get("layer2_script_efficacy_and_speed") or {})
    layer3 = dict(raw_retro.get("layer3_wild_patterns_and_eval") or {})
    sources_ingested = dict(raw_retro.get("sources_ingested") or {})
    return {
        "enabled": True,
        "schema": raw_retro.get("schema") or "skill-retro-diagnostic/v1",
        "window_cst": raw_retro.get("window_cst") or "30d",
        "window_start": raw_retro.get("window_start"),
        "window_end": raw_retro.get("window_end"),
        "source_as_of": raw_retro.get("source_as_of"),
        "sample_size": int(raw_retro.get("sample_size") or 0),
        "coverage_status": raw_retro.get("coverage_status") or "unknown",
        "sources_ingested": sources_ingested,
        "slow_flywheel_ready": bool(retro_summary.get("slow_flywheel_ready")),
        "high_signal_proposals_count": len(proposals),
        "observation_pool_count": int(retro_summary.get("observation_pool_count") or len(obs_pool)),
        "proposals_written": int(raw_retro.get("proposals_written") or 0),
        "auto_proposed_ids": list(raw_retro.get("auto_proposed") or []),
        "summary": retro_summary,
        "proposals": proposals,
        "observation_pool": obs_pool,
        "layer0_direction_and_triggers": {
            "trigger_misses": list(layer0.get("trigger_misses") or [])[:5],
            "zero_action_loads": list(layer0.get("zero_action_loads") or [])[:5],
            "healthy_convergence_count": int(layer0.get("healthy_convergence_count") or 0),
            "directional_drift_count": int(layer0.get("directional_drift_count") or 0),
        },
        "layer1_drawer_metabolism": {
            "hot_drawers": list(layer1.get("hot_drawers") or [])[:5],
            "on_demand_correction_drawers": list(layer1.get("on_demand_correction_drawers") or [])[:5],
            "unwired_disconnected_drawers": list(layer1.get("unwired_disconnected_drawers") or [])[:5],
            "true_zero_hit_deadwood": list(layer1.get("true_zero_hit_deadwood") or [])[:5],
        },
        "layer2_script_efficacy_and_speed": {
            "true_catchers": list(layer2.get("true_catchers") or [])[:5],
            "ceremonial_rubber_stamps": list(layer2.get("ceremonial_rubber_stamps") or [])[:5],
            "rigid_roadblocks": list(layer2.get("rigid_roadblocks") or [])[:5],
            "latency_sinks": list(layer2.get("latency_sinks") or [])[:5],
        },
        "layer3_wild_patterns_and_eval": {
            "wild_patterns": list(layer3.get("wild_patterns") or [])[:5],
            "taste_and_diversity_summary": dict(layer3.get("taste_and_diversity_summary") or {}),
        },
        "cohort_watermark": cohort_sig,
    }


def _invoke_pure_skill_retro_cli(
    *,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    proposals_path: Path | None = None,
    skill: str | None = None,
    days: int = 30,
    auto_propose: bool = False,
    no_roi_mirror: bool = True,
) -> dict[str, Any]:
    if SKILL_RETRO_SCRIPT_PATH.is_file():
        spec = importlib.util.spec_from_file_location("autopilot_skill_retro_mod", SKILL_RETRO_SCRIPT_PATH)
        if spec is not None and spec.loader is not None:
            mod = sys.modules.get("autopilot_skill_retro_mod")
            if mod is None or getattr(mod, "__file__", None) != str(SKILL_RETRO_SCRIPT_PATH):
                mod = importlib.util.module_from_spec(spec)
                sys.modules["autopilot_skill_retro_mod"] = mod
                spec.loader.exec_module(mod)
            safe_wh = warehouse_db if (warehouse_db is None or _is_safe_local_file(warehouse_db)) else None
            safe_cp = copilot_db if (copilot_db is None or _is_safe_local_file(copilot_db)) else None
            safe_sess = (
                copilot_sessions_dir
                if (copilot_sessions_dir is None or _probe_readable_dir(copilot_sessions_dir))
                else None
            )
            safe_usage = usage_jsonl if (usage_jsonl is None or _is_safe_local_file(usage_jsonl)) else None
            safe_handoff = handoff_root if (handoff_root is None or _probe_readable_dir(handoff_root)) else None
            safe_dev = dev_root if (dev_root is None or _probe_readable_dir(dev_root)) else None
            res = mod.compute_skill_retro(
                warehouse_db=safe_wh,
                copilot_db=safe_cp,
                copilot_sessions_dir=safe_sess,
                usage_jsonl=safe_usage,
                handoff_root=safe_handoff,
                skills_root=skills_root,
                dev_root=safe_dev,
                proposals_path=proposals_path,
                skill=skill,
                days=days,
                auto_propose=auto_propose,
                mirror_roi_snapshot=False if no_roi_mirror else None,
            )
            return res if isinstance(res, dict) else {}

    cli_path = SKILL_RETRO_SCRIPT_PATH if SKILL_RETRO_SCRIPT_PATH.is_file() else ENGINE_PATH
    subcmd = "diagnose" if cli_path == SKILL_RETRO_SCRIPT_PATH else "skill-retro"
    cmd = [sys.executable, str(cli_path), subcmd, "--days", str(days), "--json"]
    if skill:
        cmd.extend(["--skill", skill])
    if no_roi_mirror:
        cmd.append("--no-roi-mirror")
    if auto_propose:
        cmd.append("--auto-propose")
    flag_pairs: tuple[tuple[str, Path | None], ...] = (
        ("--warehouse-db", warehouse_db if (warehouse_db is None or _is_safe_local_file(warehouse_db)) else None),
        ("--copilot-db", copilot_db if (copilot_db is None or _is_safe_local_file(copilot_db)) else None),
        ("--copilot-sessions-dir", copilot_sessions_dir if (copilot_sessions_dir is None or _probe_readable_dir(copilot_sessions_dir)) else None),
        ("--usage-jsonl", usage_jsonl if (usage_jsonl is None or _is_safe_local_file(usage_jsonl)) else None),
        ("--handoff-root", handoff_root if (handoff_root is None or _probe_readable_dir(handoff_root)) else None),
        ("--skills-root", skills_root),
        ("--dev-root", dev_root if (dev_root is None or _probe_readable_dir(dev_root)) else None),
        ("--proposals-path", proposals_path),
    )
    for flag, val in flag_pairs:
        if val is not None:
            cmd.extend([flag, str(val)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"skill_retro.py failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
    parsed = json.loads(proc.stdout or "{}")
    return parsed if isinstance(parsed, dict) else {}


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


def build_autopilot_prompt(
    *,
    loop_runner_path: str | None = None,
    script_path: str | None = None,
    loop_id: str = "skill-retro-diagnostic",
    collect_path: str = "/tmp/multica-loop-skills-roi-diagnostic-collect.json",
    analysis_path: str = str(DEFAULT_ANALYSIS_OUTPUT),
    owner_id: str = DEFAULT_OWNER_ID,
    owner_email: str = DEFAULT_OWNER_EMAIL,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> str:
    resolved_runner = loop_runner_path or (
        "$HOME/Development/user-skills/skills/team-lead/scripts/multica_loop_runner.py"
    )
    resolved_script = script_path or (
        "$HOME/Development/user-skills/skills/retro-loop/scripts/multica_skills_autopilot.py"
    )
    skill_retro_cli = "$HOME/Development/user-skills/skills/skill-retro/scripts/skill_retro.py"
    return (
        "# Daily Skill Progressive Retro Autopilot (Auto-Fix + Daily Brief Digest)\n\n"
        f"- **Loop ID**: `{loop_id}` (compatible alias: `skills-roi-diagnostic`)\n"
        f"- **Primary Engine**: `skills/skill-retro/scripts/skill_retro.py` (`{skill_retro_cli}`, compatible alias `retro_story_engine.py skill-retro`)\n"
        "- **Orchestration Topology**: `1 M2 Coordinator -> 1 Cheap Probe Worker -> K (0..3) Independent Parallel M1 TLs (deep reasoning per skill) -> Rollup to Single M2 -> Daily Summary Issue`\n"
        "- **Execution Architecture**: `Layer 0 Deterministic Script Precheck -> Layer 1 Jev Fast-Thinking -> Layer 2 Wake-on-Actionable (auto_fix_and_digest_only)`\n\n"
        "## Execution Protocol (`1 M2 -> Probe Worker -> 0..3 M1 TLs -> M2 Rollup`)\n\n"
        "1. **Stage 1+2 · Scheduled Tick & Cheap Probe Worker Triage (`04:20` CST, `0..3` Honest Cap)**:\n"
        "   ```bash\n"
        f'   python3 "{resolved_runner}" run-loop --loop "{loop_id}" --json\n'
        f'   python3 "{skill_retro_cli}" triage --pick-top 3 --use-jev --emit-briefs "$HOME/Development/handoff" --json\n'
        "   ```\n"
        "   A single M2 spawns **1 Cheap Probe Worker** (`--role explorer` / `--role mechanical`, `mai-code-2-flash` / `worker` tier + Jev) to probe and triage `K` (`0..3` max cap, never a forced quota) disjoint skills. If `K == 0`, report `HEALTHY_NO_ISSUES` (`0` M1 TLs spawned); if `K = 1..3`, emit `K` per-skill M1 Goal specs (`~/Development/handoff/skill-retro-<skill>/spec.md`) + `m2_dispatch_plan`.\n\n"
        "2. **Stage 3 · `K (1..3)` Parallel M1 TL Deep Reasoning (`1 Skill = 1 Independent M1 TL`)**:\n"
        "   For each selected skill, the **single M2** spawns **1 independent M1 TL** in parallel (`herdr-dispatch.sh --new-workspace --role team_lead --name skill-retro-<skill>-goal-tl-r1 --kind claude`) in `~/Development/worktrees/skill-retro-<skill>`:\n"
        "   - If M1 TL's deep reasoning finds the signal is benign/healthy, **M1 TL directly writes `HEALTHY_NO_ISSUES` (`record-m1-receipt --skill <skill> --status HEALTHY_NO_ISSUES`) and returns to M2 without spawning any Writer or Reviewer**.\n"
        "   - If M1 TL confirms a real issue, M1 TL runs `Writer -> Reviewer`, Positive + Counter-Case Replay, and `./scripts/verify`, merges its PR, and writes its R10 receipt (`FIXED_AND_MERGED` or `CROSS_SKILL_RELAY`).\n\n"
        "3. **Final Rollup to the Single M2 (`skill_retro.py rollup` -> `[Skill Retro YYYY-MM-DD]` Issue)**:\n"
        f"   All `K` M1 TLs report back to the **same single M2**, which runs `python3 \"{skill_retro_cli}\" rollup --handoff-root \"$HOME/Development/handoff\" --markdown-output \"{analysis_path}\" --json` (and `{collect_path}` / `python3 \"{resolved_script}\" collect --output \"{DEFAULT_COLLECT_OUTPUT}\"`) to publish the single Dual-Zone (`issue-brief` verified) `[Skill Retro YYYY-MM-DD]` summary issue assigned to Owner `{owner_email}` (`{owner_id}`):\n"
        "   ```bash\n"
        f'   python3 "{resolved_script}" file-issue \\\n'
        f'     --input "{DEFAULT_COLLECT_OUTPUT}" \\\n'
        f'     --analysis-file "{analysis_path}" \\\n'
        f'     --priority medium \\\n'
        f'     --status backlog \\\n'
        f'     --assignee-id "{owner_id}" \\\n'
        f'     --server-url "{server_url}" \\\n'
        f'     --workspace-id "{workspace_id}"\n'
        "   ```\n"
    )


def _derive_top_findings_from_skill_retro(skill_retro: Mapping[str, Any], *, top_n: int = 5) -> list[dict[str, Any]]:
    proposals = [p for p in (skill_retro.get("proposals") or []) if isinstance(p, Mapping)]
    findings: list[dict[str, Any]] = []
    for idx, prop in enumerate(proposals[: max(1, top_n)], 1):
        findings.append(
            {
                "rank": idx,
                "id": prop.get("id"),
                "skill": prop.get("skill"),
                "layer": prop.get("layer") or "L0",
                "category": prop.get("category"),
                "diagnostic_status": prop.get("category") or "skill_retro_proposal",
                "priority": prop.get("priority") or "P2",
                "title": prop.get("title"),
                "diagnosis": prop.get("plain_reason") or prop.get("title"),
                "action": prop.get("action"),
                "human_what": prop.get("human_what"),
                "human_why": prop.get("human_why"),
                "scope_triage": prop.get("scope_triage") or {},
                "source_as_of": skill_retro.get("source_as_of"),
                "sample_size": skill_retro.get("sample_size"),
                "remediation_commands": list(prop.get("remediation_commands") or _remediation_commands_for_proposal(prop)),
            }
        )
    return findings


def collect_skill_retro_snapshot(
    *,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    proposals_path: Path | None = None,
    skill: str | None = None,
    days: int = 30,
    top_n: int = 5,
    skill_retro_auto_propose: bool = False,
    skill_retro_watermark_path: Path | None = None,
    persist_cohort_watermark: bool = True,
    skill_retro_fn: Callable[..., dict[str, Any]] | None = None,
    **_unused_legacy_kwargs: Any,
) -> dict[str, Any]:
    """Collect a pure 4-Layer Progressive Skill Retro (`skill-retro`) snapshot."""
    no_roi_mirror = any(
        val is not None
        for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, proposals_path)
    )
    source_kwargs = {
        "warehouse_db": warehouse_db,
        "copilot_db": copilot_db,
        "copilot_sessions_dir": copilot_sessions_dir,
        "usage_jsonl": usage_jsonl,
        "handoff_root": handoff_root,
        "skills_root": skills_root,
        "dev_root": dev_root,
        "proposals_path": proposals_path,
        "skill": skill,
        "days": days,
    }
    if skill_retro_fn is not None:
        raw_skill_retro = skill_retro_fn(**source_kwargs, auto_propose=False)
    else:
        raw_skill_retro = _invoke_pure_skill_retro_cli(**source_kwargs, auto_propose=False, no_roi_mirror=no_roi_mirror)

    skill_retro = _summarize_skill_retro_report(raw_skill_retro)
    retro_proposals = list(skill_retro.get("proposals") or [])
    top_findings = _derive_top_findings_from_skill_retro(skill_retro, top_n=top_n)
    actionable_skills = sorted({str(p.get("skill")) for p in retro_proposals if p.get("skill")})

    now_cst = datetime.now(CST)
    collected_date = str(raw_skill_retro.get("wall_clock_date") or now_cst.date().isoformat())
    dedup_key = f"skill-retro-autopilot-{collected_date}"

    cohort_watermark = str(skill_retro.get("cohort_watermark") or _compute_cohort_watermark({"proposals": [], "slow_flywheel_ready": False}))
    prev_wm_state = _load_skill_retro_watermark(skill_retro_watermark_path) if skill_retro_watermark_path is not None else {}
    prev_cohort_watermark = str(prev_wm_state.get("cohort_watermark") or "")
    prev_proposed_watermark = str(prev_wm_state.get("proposed_cohort_watermark") or prev_cohort_watermark)
    has_cohort_delta = bool(retro_proposals and cohort_watermark != prev_cohort_watermark)
    has_propose_delta = bool(retro_proposals and (skill_retro_watermark_path is None or cohort_watermark != prev_proposed_watermark))

    if skill_retro_auto_propose and has_propose_delta:
        if skill_retro_fn is not None:
            proposed_res = skill_retro_fn(**source_kwargs, auto_propose=True)
        else:
            proposed_res = _invoke_pure_skill_retro_cli(**source_kwargs, auto_propose=True, no_roi_mirror=no_roi_mirror)
        if isinstance(proposed_res, Mapping):
            skill_retro["proposals_written"] = int(proposed_res.get("proposals_written") or 0)
            skill_retro["auto_proposed_ids"] = list(proposed_res.get("auto_proposed") or [])
        if skill_retro_watermark_path is not None:
            updated_state = dict(prev_wm_state)
            updated_state["proposed_cohort_watermark"] = cohort_watermark
            updated_state["proposed_at"] = now_cst.isoformat(timespec="seconds")
            _save_skill_retro_watermark(updated_state, skill_retro_watermark_path)
            prev_wm_state = updated_state

    if skill_retro_watermark_path is not None and persist_cohort_watermark and has_cohort_delta:
        updated_state = dict(prev_wm_state)
        updated_state.update(
            {
                "cohort_watermark": cohort_watermark,
                "collected_date": collected_date,
                "updated_at": now_cst.isoformat(timespec="seconds"),
                "skill_retro_proposals_count": len(retro_proposals),
            }
        )
        _save_skill_retro_watermark(updated_state, skill_retro_watermark_path)

    retro_summary = dict(skill_retro.get("summary") or {})
    sources_ingested = dict(skill_retro.get("sources_ingested") or {})

    snapshot: dict[str, Any] = {
        "schema": "multica-skill-retro-collect/v2",
        "engine": "skill-retro",
        "ok": True,
        "collected_at": now_cst.isoformat(timespec="seconds"),
        "collected_date": collected_date,
        "dedup_key": dedup_key,
        "cohort_gate": {
            "cohort_watermark": cohort_watermark,
            "previous_cohort_watermark": prev_cohort_watermark,
            "has_delta": has_cohort_delta if skill_retro_watermark_path is not None else bool(len(retro_proposals) > 0),
            "token_cost": 0,
            "slow_flywheel_ready": bool(skill_retro.get("slow_flywheel_ready")),
            "session_sample_size": skill_retro.get("sample_size", 0),
        },
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
            "window_cst": skill_retro.get("window_cst", f"{days}d"),
            "source_as_of": skill_retro.get("source_as_of"),
            "sample_size": skill_retro.get("sample_size", 0),
            "coverage_status": skill_retro.get("coverage_status", "unknown"),
            "skills_in_inventory": int(sources_ingested.get("skills_in_inventory") or 0),
            "trace_spans_ingested": int(sources_ingested.get("trace_spans_ingested") or 0),
            "actionable_skills_count": len(actionable_skills),
            "skill_retro_proposals_count": len(retro_proposals),
            "observation_pool_count": skill_retro.get("observation_pool_count", 0),
            "skill_retro_slow_flywheel_ready": bool(skill_retro.get("slow_flywheel_ready")),
            "trigger_miss_skills": int(retro_summary.get("trigger_miss_skills") or 0),
            "zero_action_load_skills": int(retro_summary.get("zero_action_load_skills") or 0),
            "directional_drifts": int(retro_summary.get("directional_drifts") or 0),
            "unwired_drawers_count": int(retro_summary.get("unwired_drawers_count") or 0),
            "true_deadwood_drawers_count": int(retro_summary.get("true_deadwood_drawers_count") or 0),
            "true_catcher_scripts_count": int(retro_summary.get("true_catcher_scripts_count") or 0),
            "ceremonial_scripts_count": int(retro_summary.get("ceremonial_scripts_count") or 0),
            "rigid_roadblock_scripts_count": int(retro_summary.get("rigid_roadblock_scripts_count") or 0),
            "latency_sink_scripts_count": int(retro_summary.get("latency_sink_scripts_count") or 0),
            "wild_patterns_count": int(retro_summary.get("wild_patterns_count") or 0),
        },
        "top_findings": top_findings,
        "skill_retro": skill_retro,
        "recommendations": retro_proposals,
    }
    snapshot["default_issue_title"] = build_issue_title(snapshot)
    snapshot["markdown_brief"] = render_issue_markdown(snapshot)
    return snapshot


# Thin backward-compatible alias routed exclusively to pure `skill-retro`
collect_skills_roi_snapshot = collect_skill_retro_snapshot


def build_issue_title(snapshot: Mapping[str, Any]) -> str:
    collected_date = str(snapshot.get("collected_date") or datetime.now(CST).date().isoformat())
    skill_retro = snapshot.get("skill_retro") if isinstance(snapshot.get("skill_retro"), Mapping) else {}
    proposals = list(skill_retro.get("proposals") or snapshot.get("top_findings") or [])
    focus_skills: list[str] = []
    for item in proposals:
        if not isinstance(item, Mapping):
            continue
        sk = _resolve_proposal_skill(item)
        if sk and sk not in focus_skills:
            focus_skills.append(sk)
        if len(focus_skills) >= 3:
            break
    focus = ", ".join(focus_skills) if focus_skills else "workspace skills"
    return f"[P3 / Priority 3][Skill Retro] {collected_date} 技能四层渐进式自演进诊断 ({focus})"


def _build_ai_evidence_markdown(snapshot: Mapping[str, Any], *, agent_analysis: str | None = None) -> str:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), Mapping) else {}
    skill_retro = snapshot.get("skill_retro") if isinstance(snapshot.get("skill_retro"), Mapping) else {}
    retro_sum = skill_retro.get("summary") if isinstance(skill_retro.get("summary"), Mapping) else summary
    retro_proposals = list(skill_retro.get("proposals") or snapshot.get("top_findings") or [])
    obs_pool = list(skill_retro.get("observation_pool") or [])
    dedup_key = str(snapshot.get("dedup_key") or "")

    lines: list[str] = [
        f"## [P3 / Priority 3] 4-Layer Progressive Skill Retro Diagnostic ({snapshot.get('collected_date')})",
        "",
        f"- **Dedup Marker**: `{dedup_key}`",
        "- **Primary Engine**: `skills/skill-retro/scripts/skill_retro.py` (`retro_story_engine.py skill-retro` / `skill-retro-diagnostic/v1`)",
        "- **Orchestration Topology**: `1 M2 -> 1 Cheap Probe Worker -> K (0..3) Independent M1 TLs -> Rollup to Single M2`",
        "- **Priority**: Priority 3 (`medium`)",
        f"- **Assigned Owner**: `{DEFAULT_OWNER_EMAIL}` (`{DEFAULT_OWNER_ID}`)",
        f"- **Observation Cohort**: `{summary.get('window_cst', '30d')}` · "
        f"`sessions={summary.get('sample_size', 0)}` · "
        f"`slow_flywheel_ready={summary.get('skill_retro_slow_flywheel_ready', False)}` · "
        f"`source_as_of={summary.get('source_as_of')}` · "
        f"`coverage={summary.get('coverage_status')}`",
        f"- **Layer Signals**: L0 trigger_misses=`{retro_sum.get('trigger_miss_skills', 0)}` / "
        f"zero_action=`{retro_sum.get('zero_action_load_skills', 0)}` / drifts=`{retro_sum.get('directional_drifts', 0)}` · "
        f"L1 unwired_drawers=`{retro_sum.get('unwired_drawers_count', 0)}` / deadwood=`{retro_sum.get('true_deadwood_drawers_count', 0)}` · "
        f"L2 true_catchers=`{retro_sum.get('true_catcher_scripts_count', 0)}` / roadblocks=`{retro_sum.get('rigid_roadblock_scripts_count', 0)}` / "
        f"latency_sinks=`{retro_sum.get('latency_sink_scripts_count', 0)}` · "
        f"L3 wild_patterns=`{retro_sum.get('wild_patterns_count', 0)}`",
        "",
    ]

    if agent_analysis and agent_analysis.strip():
        lines.extend(
            [
                "## Agent Root-Cause Synthesis & Evolution Plan",
                "",
                agent_analysis.strip(),
                "",
            ]
        )

    lines.extend(
        [
            "## Deterministic 4-Layer Skill Retro Proposals (`L0`–`L3`)",
            "",
            "| Priority | Layer | Skill | Category | Proposal & Recommended Action |",
            "|---|---|---|---|---|",
        ]
    )

    valid_proposals = [
        (p, sk)
        for p in retro_proposals
        if isinstance(p, Mapping) and (sk := _resolve_proposal_skill(p))
    ][:6]
    if valid_proposals:
        for prop, sk in valid_proposals:
            layer = prop.get("layer") or ("L2" if sk == "repo-kit" else "L0")
            lines.append(
                f"| `{prop.get('priority', 'P2')}` | `{layer}` | `{sk}` | "
                f"`{prop.get('category') or prop.get('diagnostic_status')}` | {_proposal_action_cell(prop, sk)} |"
            )
    else:
        lines.append("| — | — | `healthy` | `clean` | No high-signal L0–L3 friction or drift anomalies detected |")

    if obs_pool:
        lines.extend(["", "### Dual-Flywheel Observation Pool (Held Below Cohort Gate)", ""])
        for item in obs_pool[:5]:
            lines.append(
                f"- **`[{item.get('layer', 'L1')}] {item.get('skill')}`** (`{item.get('category')}`): {item.get('title')}"
            )

    lines.extend(
        [
            "",
            "### Verification & Brief Emission Commands",
            "",
            "```bash",
            "python3 skills/retro-loop/scripts/multica_skills_autopilot.py collect --output /tmp/multica-skills-autopilot-collect.json",
            "python3 skills/skill-retro/scripts/skill_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json",
            "python3 skills/skill-retro/scripts/skill_retro.py rollup --handoff-root ~/Development/handoff --json",
            "python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict",
            "./scripts/verify",
            "```",
        ]
    )
    return "\n".join(lines)


def build_issue_markdown(snapshot: Mapping[str, Any], *, agent_analysis: str | None = None) -> str:
    """Build Dual-Zone compliant Multica issue body via `issue-brief` contract from pure `skill-retro` output."""
    issue_brief = _load_issue_brief()
    skill_retro = snapshot.get("skill_retro") if isinstance(snapshot.get("skill_retro"), Mapping) else {}
    retro_proposals = [
        {**p, "skill": sk}
        for p in (skill_retro.get("proposals") or snapshot.get("top_findings") or [])
        if isinstance(p, Mapping) and (sk := _resolve_proposal_skill(p))
    ]
    dedup_key = str(snapshot.get("dedup_key") or f"skill-retro-autopilot-{snapshot.get('collected_date') or 'today'}")
    retro_count = len(retro_proposals)

    focus_skills: list[str] = []
    for prop in retro_proposals:
        sk_clean = str(prop.get("skill") or "").strip()
        if sk_clean and f"`{sk_clean}`" not in focus_skills:
            focus_skills.append(f"`{sk_clean}`")
        if len(focus_skills) >= 3:
            break

    if focus_skills and retro_count:
        focus_txt = "、".join(focus_skills)
        tldr = (
            f"今日技能四层渐进式自演进巡检共发现 {retro_count} 项高信号改进提案，"
            f"主要集中在 {focus_txt}，勾选对应项后即可由后台总控自动派发演进。"
        )
    else:
        tldr = "今日技能四层渐进式自演进巡检整体健康，未发现需要紧急治理的触发、抽屉或脚本路障。"

    human_items: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for prop in retro_proposals[:5]:
        if not isinstance(prop, Mapping):
            continue
        what_txt = _sanitize_human_zone_label(str(prop.get("human_what") or prop.get("title") or "")).strip()[:46]
        why_txt = _sanitize_human_zone_label(str(prop.get("human_why") or prop.get("plain_reason") or prop.get("action") or "")).rstrip("。").strip()[:64]
        if not what_txt or what_txt.casefold() in seen_targets:
            continue
        seen_targets.add(what_txt.casefold())
        human_items.append({"what": what_txt, "why": why_txt or "补齐四层技能自演进防呆指引"})

    if not human_items:
        human_items.append(
            {
                "what": "保持现有技能四层契约门禁与零成本巡检",
                "why": "当前无高信号路障或断链抽屉，维持日常自动观测即可",
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


def render_issue_markdown(snapshot: Mapping[str, Any], *, agent_analysis: str | None = None) -> str:
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
    update_existing: bool = False,
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
                content_differs = bool(ex_desc.strip() and ex_desc.strip() != description.strip()) or (
                    bool(ex_title.strip()) and ex_title.strip() != issue_title.strip()
                )
                should_update_in_place = bool(refresh_existing or update_existing or content_differs)
                if should_update_in_place and (existing.get("id") or existing.get("identifier")):
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
                        "action": "refreshed_existing" if refresh_existing else "updated_existing",
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


def _matches_skill_retro_autopilot(ap: Mapping[str, Any], title: str) -> bool:
    ap_id = str(ap.get("id") or "")
    ap_title = str(ap.get("title") or "")
    ap_desc = str(ap.get("description") or "")
    return (
        ap_title == title
        or (CANONICAL_SKILL_RETRO_AUTOPILOT_ID_PREFIX and ap_id.startswith(CANONICAL_SKILL_RETRO_AUTOPILOT_ID_PREFIX))
        or "Skill Progressive Retro" in ap_title
        or "Skills ROI" in ap_title
        or "`skill-retro-diagnostic`" in ap_desc
        or "`skills-roi-diagnostic`" in ap_desc
    )


def setup_multica_autopilot(
    *,
    title: str = DEFAULT_AUTOPILOT_TITLE,
    agent_id: str = DEFAULT_AGENT_ID,
    subscriber_id: str = DEFAULT_OWNER_ID,
    mode: str = "run_only",
    cron_expr: str = DEFAULT_CRON,
    tz_name: str = DEFAULT_TIMEZONE,
    trigger_label: str = DEFAULT_TRIGGER_LABEL,
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
            "trigger_label": trigger_label,
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
    existing = next((ap for ap in autopilots if isinstance(ap, dict) and _matches_skill_retro_autopilot(ap, title)), None)

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
        existing_label = str(schedule_trig.get("label") or "")
        same_schedule = (
            str(schedule_trig.get("cron") or schedule_trig.get("cron_expression") or "") == cron_expr
            and str(schedule_trig.get("timezone") or "") == tz_name
            and (not existing_label or existing_label == trigger_label)
        )
        if same_schedule:
            trigger_obj = schedule_trig
            trigger_action = "unchanged"
        else:
            trig_upd_proc = runner(
                [
                    "autopilot",
                    "trigger-update",
                    autopilot_id,
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


def synthesize_deterministic_analysis(snapshot: Mapping[str, Any]) -> str:
    """Synthesize structured 4-Layer `skill-retro` root-cause analysis when no external Agent markdown is supplied."""
    skill_retro = snapshot.get("skill_retro") if isinstance(snapshot.get("skill_retro"), Mapping) else {}
    proposals = [
        (p, sk)
        for p in (skill_retro.get("proposals") or snapshot.get("top_findings") or [])
        if isinstance(p, Mapping) and (sk := _resolve_proposal_skill(p))
    ]
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), Mapping) else {}
    bullets: list[str] = [
        f"基于 4-Layer Progressive Skill Retro（`sessions={summary.get('sample_size', 0)}`, "
        f"`slow_flywheel_ready={summary.get('skill_retro_slow_flywheel_ready', False)}`, "
        f"`source_as_of={summary.get('source_as_of')}`），当前共识别 **{len(proposals)}** 项高信号改进提案"
        f"（观测池 `{summary.get('observation_pool_count', 0)}` 项）：",
        "",
    ]
    for idx, (prop, sk) in enumerate(proposals[:5], 1):
        layer = prop.get("layer") or ("L2" if sk == "repo-kit" else "L0")
        cat = prop.get("category") or prop.get("diagnostic_status")
        prio = prop.get("priority") or "P2"
        reason = prop.get("plain_reason") or prop.get("diagnosis") or prop.get("title") or f"`{sk}` (`{cat}`)"
        action = prop.get("action") or f"Run `{sk}` remediation & `./scripts/verify`"
        bullets.append(
            f"{idx}. **`[{prio}][{layer}] {sk}` (`{cat}`)**：{reason}。建议动作：{action}"
        )
    if not proposals:
        bullets.append("- 当前周期内 L0 触发守卫、L1 抽屉代谢、L2 脚本路障与耗时、L3 手工探针沉淀均无新增高信号异常。")
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
    update_existing: bool = False,
    update_issue_id: str | None = None,
    dry_run: bool = False,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    proposals_path: Path | None = None,
    skill: str | None = None,
    days: int = 30,
    skill_retro_auto_propose: bool = False,
    skill_retro_watermark_path: Path | None = None,
    skip_if_no_cohort_delta: bool = False,
    skill_retro_fn: Callable[..., dict[str, Any]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
    **_unused_legacy_kwargs: Any,
) -> dict[str, Any]:
    snapshot = collect_skill_retro_snapshot(
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        copilot_sessions_dir=copilot_sessions_dir,
        usage_jsonl=usage_jsonl,
        handoff_root=handoff_root,
        skills_root=skills_root,
        dev_root=dev_root,
        proposals_path=proposals_path,
        skill=skill,
        days=days,
        skill_retro_auto_propose=(skill_retro_auto_propose and not dry_run),
        skill_retro_watermark_path=(None if dry_run else skill_retro_watermark_path),
        persist_cohort_watermark=False,
        skill_retro_fn=skill_retro_fn,
    )
    collect_output.parent.mkdir(parents=True, exist_ok=True)
    collect_output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cohort_gate = snapshot.get("cohort_gate") if isinstance(snapshot.get("cohort_gate"), dict) else {}
    if skip_if_no_cohort_delta and not cohort_gate.get("has_delta", True):
        return {
            "ok": True,
            "status": "SKIPPED_NO_DELTA",
            "token_cost": 0,
            "collect_output": str(collect_output),
            "collected_date": snapshot.get("collected_date"),
            "cohort_gate": cohort_gate,
            "top_findings_count": len(snapshot.get("top_findings") or []),
            "skill_retro_proposals_count": len((snapshot.get("skill_retro") or {}).get("proposals") or []),
        }

    analysis_md: str | None = None
    if analysis_cmd:
        env = dict(os.environ)
        env["SKILL_RETRO_COLLECT_JSON"] = str(collect_output)
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
        update_existing=update_existing,
        update_issue_id=update_issue_id,
        dry_run=dry_run,
        runner=runner,
        multica_bin=multica_bin,
    )
    if not dry_run and skill_retro_watermark_path is not None and cohort_gate.get("cohort_watermark"):
        wm_state = _load_skill_retro_watermark(skill_retro_watermark_path)
        wm_state.update(
            {
                "cohort_watermark": str(cohort_gate["cohort_watermark"]),
                "collected_date": snapshot.get("collected_date"),
                "delivered_at": datetime.now(CST).isoformat(timespec="seconds"),
                "skill_retro_proposals_count": len((snapshot.get("skill_retro") or {}).get("proposals") or []),
            }
        )
        _save_skill_retro_watermark(wm_state, skill_retro_watermark_path)
    return {
        "ok": True,
        "collect_output": str(collect_output),
        "collected_date": snapshot.get("collected_date"),
        "top_findings_count": len(snapshot.get("top_findings") or []),
        "skill_retro_proposals_count": len((snapshot.get("skill_retro") or {}).get("proposals") or []),
        "issue_result": issue_result,
    }


def _add_retro_source_args(subparser: argparse.ArgumentParser) -> None:
    for flag in (
        "--warehouse-db",
        "--copilot-db",
        "--copilot-sessions-dir",
        "--usage-jsonl",
        "--handoff-root",
        "--skills-root",
        "--dev-root",
        "--proposals-path",
    ):
        subparser.add_argument(flag, type=Path, default=None)
    subparser.add_argument("--skill", default=None, help="Optional single skill filter.")
    subparser.add_argument("--days", type=int, default=30, help="Observation window in days (default: 30).")
    subparser.add_argument("--skill-retro-auto-propose", action="store_true", help="Queue gated high-signal skill-retro proposals to proposals.jsonl.")
    subparser.add_argument("--watermark-file", type=Path, default=None, help="Optional cohort watermark file for zero-token no-delta gating.")


def _add_issue_publish_args(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--priority", default=DEFAULT_ISSUE_PRIORITY, choices=("urgent", "high", "medium", "low", "none"))
    subparser.add_argument("--status", default=DEFAULT_ISSUE_STATUS, help="Initial Multica issue status (default: backlog).")
    subparser.add_argument("--assignee-id", default=DEFAULT_OWNER_ID, help="Owner user UUID on Multica.")
    subparser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    subparser.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    subparser.add_argument("--multica-bin", default=None)
    subparser.add_argument("--allow-duplicate", action="store_true")
    subparser.add_argument("--refresh-existing", action="store_true", help="Update matching open issue body in place.")
    subparser.add_argument("--update-existing", action="store_true", help="Update matching same-day issue in place via multica issue update.")
    subparser.add_argument("--update-issue-id", default=None, help="Explicit Multica issue ID/key (e.g. TIA-1) to update in place.")
    subparser.add_argument("--dry-run", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multica_skills_autopilot.py",
        description="Pure 4-Layer Skill Progressive Retro NAS Multica Autopilot (collect -> analyze -> file P3 issue / sync autopilot).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_collect = sub.add_parser("collect", help="Deterministically collect pure 4-layer skill-retro snapshot.")
    _add_retro_source_args(p_collect)
    p_collect.add_argument("--top-n", type=int, default=5)
    p_collect.add_argument("--output", type=Path, default=None, help="Optional JSON output file path.")
    p_collect.add_argument("--markdown-output", type=Path, default=None, help="Optional rendered Markdown output path.")

    p_issue = sub.add_parser("file-issue", help="Create or deduplicate a Priority 3 (medium) Actionable Issue assigned to Owner.")
    p_issue.add_argument("--input", type=Path, default=DEFAULT_COLLECT_OUTPUT, help="Collected snapshot JSON path.")
    p_issue.add_argument("--analysis-file", type=Path, default=None, help="Optional Agent-refined Markdown analysis path.")
    p_issue.add_argument("--analysis-text", default="", help="Optional inline Agent analysis Markdown.")
    p_issue.add_argument("--title", default=None, help="Override issue title.")
    _add_issue_publish_args(p_issue)

    p_setup = sub.add_parser("setup-autopilot", help="Idempotently configure NAS Multica Autopilot & daily schedule trigger.")
    p_setup.add_argument("--title", default=DEFAULT_AUTOPILOT_TITLE)
    p_setup.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    p_setup.add_argument("--subscriber-id", default=DEFAULT_OWNER_ID)
    p_setup.add_argument("--mode", default="run_only", choices=("run_only", "create_issue"))
    p_setup.add_argument("--cron", default=DEFAULT_CRON)
    p_setup.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    p_setup.add_argument("--trigger-label", default=DEFAULT_TRIGGER_LABEL)
    p_setup.add_argument("--collect-output", default="/tmp/multica-loop-skills-roi-diagnostic-collect.json")
    p_setup.add_argument("--analysis-output", default=str(DEFAULT_ANALYSIS_OUTPUT))
    p_setup.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_setup.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_setup.add_argument("--multica-bin", default=None)
    p_setup.add_argument("--trigger-now", action="store_true", help="Immediately trigger one autopilot run after setup.")
    p_setup.add_argument("--dry-run", action="store_true")

    p_run = sub.add_parser("run-once", help="Collect pure 4-layer skill-retro snapshot, attach analysis, and file P3 issue to Owner.")
    p_run.add_argument("--output", type=Path, default=DEFAULT_COLLECT_OUTPUT)
    p_run.add_argument("--analysis-file", type=Path, default=None)
    p_run.add_argument("--analysis-cmd", default=None, help="Optional shell command to generate Agent analysis before filing.")
    _add_retro_source_args(p_run)
    p_run.add_argument("--skip-if-no-delta", action="store_true", help="Skip filing issue at 0 token cost when cohort watermark is unchanged.")
    _add_issue_publish_args(p_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if ns.subcommand == "collect":
        snapshot = collect_skill_retro_snapshot(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            proposals_path=ns.proposals_path,
            skill=ns.skill,
            days=ns.days,
            top_n=ns.top_n,
            skill_retro_auto_propose=bool(ns.skill_retro_auto_propose),
            skill_retro_watermark_path=ns.watermark_file,
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
            update_existing=ns.update_existing,
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
            update_existing=ns.update_existing,
            update_issue_id=ns.update_issue_id,
            dry_run=ns.dry_run,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            proposals_path=ns.proposals_path,
            skill=ns.skill,
            days=ns.days,
            skill_retro_auto_propose=bool(ns.skill_retro_auto_propose),
            skill_retro_watermark_path=ns.watermark_file,
            skip_if_no_cohort_delta=bool(ns.skip_if_no_delta),
            multica_bin=ns.multica_bin,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    raise SystemExit(f"Unsupported subcommand: {ns.subcommand}")


if __name__ == "__main__":
    raise SystemExit(main())
