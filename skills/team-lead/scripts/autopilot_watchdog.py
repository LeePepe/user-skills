#!/usr/bin/env python3
"""Daily Autopilot Health Watchdog (`autopilot_watchdog.py`).

Checks ONLY the two designated daily scheduled Autopilots on their 3 basic vital metrics:
1. `skill-retro` (`4e06bf2a` · `Daily Skill Progressive Retro Autopilot`):
   - `ran_today`: Triggered and completed today (`CST` date match, `status == "completed"`).
   - `issue_materialized`: Today's `[Skill Retro]` issue (`skill-retro-autopilot-YYYY-MM-DD`) exists on Multica.
   - `non_empty_telemetry`: Issue body / collect snapshot reports `sessions > 0` and `coverage != "missing"`
     (catching the exact `sessions=0` / `coverage=missing` silent-fallback failure from `TIA-25`).
2. `daily-learn` (`8b4d6d04` · `Daily Knowledge Digest Autopilot`):
   - `ran_today`: Triggered and completed today (`CST` date match, `status == "completed"`).
   - `issue_materialized`: Today's `[Daily Learn YYYY-MM-DD]` issue exists on Multica / Linear.
   - `non_empty_candidates`: Issue contains actual learning topics/articles (`> 0` items, not an empty/fallback shell).

Behavior:
- If both `skill-retro` and `daily-learn` pass all 3 basic metrics: exit `0` silently at `$0` token cost (`STATUS=HEALTHY`).
- If either fails any basic metric:
  1) Package the exact failure evidence (which autopilot failed, which basic metric failed, latest run ID,
     session ID, and error/degraded snippet);
  2) Escalate directly to M2 (via `m2-router.py` / `herdr-dispatch.sh` or inside the Autopilot's M2-Dispatcher
     agent execution) to investigate the root cause in the failing session/script, apply the fix, and
     immediately rerun the failed autopilot (`multica autopilot trigger <id>`) + re-verify that the basic metrics pass.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
TEAM_LEAD_SCRIPTS_DIR = SCRIPT_PATH.parent
SKILLS_ROOT = SCRIPT_PATH.parents[2]
M2_ROUTER_SCRIPT_PATH = SKILLS_ROOT / "personal-m2-assistant" / "scripts" / "m2-router.py"
HERDR_DISPATCH_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "herdr-dispatch.sh"
ISSUE_BRIEF_SCRIPT_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
SKILL_RETRO_AUTOPILOT_SCRIPT_PATH = SKILLS_ROOT / "retro-loop" / "scripts" / "multica_skills_autopilot.py"

LOOP_ID = "autopilot-health-watchdog"
WATCHDOG_AUTOPILOT_TITLE = "Daily Autopilot Health Watchdog (skill-retro & daily-learn -> M2 Fix & Rerun)"
WATCHDOG_CRON = "10 5 * * *"
WATCHDOG_TRIGGER_LABEL = "Daily CST 05:10 Autopilot Health Watchdog"

SKILL_RETRO_TARGET_KEY = "skill-retro"
SKILL_RETRO_AUTOPILOT_ID_PREFIX = "4e06bf2a"
SKILL_RETRO_CANONICAL_ID = "4e06bf2a-5842-4b3a-928f-fcde3bea7c41"
SKILL_RETRO_TITLE = "Daily Skill Progressive Retro Autopilot"

DAILY_LEARN_TARGET_KEY = "daily-learn"
DAILY_LEARN_AUTOPILOT_ID_PREFIX = "8b4d6d04"
DAILY_LEARN_CANONICAL_ID = "8b4d6d04-cc45-4565-9351-a3e8b07488c1"
DAILY_LEARN_TITLE = "Daily Knowledge Digest Autopilot"

ALLOWED_TARGETS: tuple[str, ...] = (SKILL_RETRO_TARGET_KEY, DAILY_LEARN_TARGET_KEY)

DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_OWNER_EMAIL = "lee@multica.local"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
DEFAULT_TIMEZONE = "Asia/Shanghai"
CST = timezone(timedelta(hours=8))

SESSIONS_RE = re.compile(r"\bsessions\s*=\s*(\d+)\b", re.IGNORECASE)
SAMPLE_SIZE_RE = re.compile(r'"sample_size"\s*:\s*(\d+)')
COVERAGE_RE = re.compile(r"\bcoverage(?:_status)?\s*=\s*`?([A-Za-z0-9_-]+)`?", re.IGNORECASE)
COVERAGE_JSON_RE = re.compile(r'"coverage_status"\s*:\s*"([^"]+)"')
CHECKBOX_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
CONCEPT_PATH_RE = re.compile(r"(?:concepts/|/learn/)[A-Za-z0-9_./-]+")
EMPTY_CANDIDATE_FALLBACK_RE = re.compile(
    r"(?:暂无候选|暂无新候选|0\s*个候选|0\s*篇|无新增学习主题|empty\s+fallback|no\s+learning\s+topics)",
    re.IGNORECASE,
)
GENERIC_NOOP_RETRO_RE = re.compile(r"保持现有技能四层契约门禁与零成本巡检|维持日常自动观测即可")


@dataclass(frozen=True)
class TargetAutopilotSpec:
    target_key: str
    autopilot_id_prefix: str
    canonical_autopilot_id: str
    display_title: str
    metric_names: tuple[str, str, str]


TARGET_SPECS: dict[str, TargetAutopilotSpec] = {
    SKILL_RETRO_TARGET_KEY: TargetAutopilotSpec(
        target_key=SKILL_RETRO_TARGET_KEY,
        autopilot_id_prefix=SKILL_RETRO_AUTOPILOT_ID_PREFIX,
        canonical_autopilot_id=SKILL_RETRO_CANONICAL_ID,
        display_title=SKILL_RETRO_TITLE,
        metric_names=("ran_today", "issue_materialized", "non_empty_telemetry"),
    ),
    DAILY_LEARN_TARGET_KEY: TargetAutopilotSpec(
        target_key=DAILY_LEARN_TARGET_KEY,
        autopilot_id_prefix=DAILY_LEARN_AUTOPILOT_ID_PREFIX,
        canonical_autopilot_id=DAILY_LEARN_CANONICAL_ID,
        display_title=DAILY_LEARN_TITLE,
        metric_names=("ran_today", "issue_materialized", "non_empty_candidates"),
    ),
}


def _resolve_multica_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_bin = os.environ.get("MULTICA_BIN")
    if env_bin:
        return env_bin
    if Path("/opt/homebrew/bin/multica").is_file():
        return "/opt/homebrew/bin/multica"
    return shutil.which("multica") or "/opt/homebrew/bin/multica"


def _default_multica_runner(
    args: Sequence[str],
    *,
    stdin_text: str | None = None,
    multica_bin: str | None = None,
    server_url: str | None = None,
    workspace_id: str | None = None,
    timeout: float = 20.0,
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
    try:
        return subprocess.run(
            cmd,
            input=stdin_text,
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "multica command timed out")


def _parse_json_stdout(proc: subprocess.CompletedProcess[str], context: str) -> Any:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"{context} failed (exit {proc.returncode}): {detail}")
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"{context} returned invalid JSON: {raw[:200]}") from exc


def _compute_signature(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def parse_timestamp_to_cst_date(raw_ts: Any) -> str | None:
    text = str(raw_ts or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", text[:10]):
            return text[:10]
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST).date().isoformat()


def _resolve_autopilot_record(
    spec: TargetAutopilotSpec,
    autopilots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    for ap in autopilots:
        if not isinstance(ap, Mapping):
            continue
        ap_id = str(ap.get("id") or "")
        title = str(ap.get("title") or "")
        if ap_id.startswith(spec.autopilot_id_prefix) or spec.display_title in title:
            return dict(ap)
    return {
        "id": spec.canonical_autopilot_id,
        "title": spec.display_title,
        "status": "unknown",
    }


def _select_today_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    target_date_str: str,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        ts_dates = {
            parse_timestamp_to_cst_date(run.get("triggered_at")),
            parse_timestamp_to_cst_date(run.get("created_at")),
            parse_timestamp_to_cst_date(run.get("completed_at")),
        }
        output_text = ""
        res_obj = run.get("result")
        if isinstance(res_obj, Mapping):
            output_text = str(res_obj.get("output") or "")
        if target_date_str in ts_dates or target_date_str in output_text:
            matched.append(dict(run))
    return matched


def _match_skill_retro_issue(
    issues: Sequence[Mapping[str, Any]],
    *,
    target_date_str: str,
) -> dict[str, Any] | None:
    expected_marker = f"skill-retro-autopilot-{target_date_str}"
    expected_cursor = f"skill-retro-{target_date_str}"
    expected_title_frag = f"[Skill Retro] {target_date_str}"
    alt_title_frag = f"[Skill Retro {target_date_str}]"

    candidates: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        status = str(issue.get("status") or "").lower()
        if status in {"cancelled", "canceled"}:
            continue
        title = str(issue.get("title") or "")
        desc = str(issue.get("description") or "")
        if (
            expected_marker in desc
            or expected_cursor in desc
            or expected_title_frag in title
            or alt_title_frag in title
            or ("[Skill Retro]" in title and target_date_str in title)
        ):
            candidates.append(dict(issue))
    if not candidates:
        return None
    candidates.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return candidates[0]


def _match_daily_learn_issue(
    issues: Sequence[Mapping[str, Any]],
    *,
    target_date_str: str,
) -> dict[str, Any] | None:
    expected_title_prefix = f"[Daily Learn {target_date_str}]"
    expected_markers = (
        f"daily-learn-{target_date_str}",
        f"daily-knowledge-{target_date_str}",
    )
    candidates: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        status = str(issue.get("status") or "").lower()
        if status in {"cancelled", "canceled"}:
            continue
        title = str(issue.get("title") or "")
        desc = str(issue.get("description") or "")
        if expected_title_prefix in title or any(m in desc for m in expected_markers):
            candidates.append(dict(issue))
    if not candidates:
        return None
    candidates.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return candidates[0]


def _extract_skill_retro_telemetry(
    issue: Mapping[str, Any] | None,
    *,
    collect_snapshot: Mapping[str, Any] | None = None,
    latest_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    desc = str((issue or {}).get("description") or "")
    sessions: int | None = None
    coverage: str | None = None

    m_sess = SESSIONS_RE.search(desc)
    if m_sess:
        sessions = int(m_sess.group(1))
    else:
        m_sample = SAMPLE_SIZE_RE.search(desc)
        if m_sample:
            sessions = int(m_sample.group(1))

    m_cov = COVERAGE_RE.search(desc)
    if m_cov:
        coverage = m_cov.group(1).strip().lower()
    else:
        m_cov_json = COVERAGE_JSON_RE.search(desc)
        if m_cov_json:
            coverage = m_cov_json.group(1).strip().lower()

    if isinstance(collect_snapshot, Mapping):
        summary = collect_snapshot.get("summary") if isinstance(collect_snapshot.get("summary"), Mapping) else {}
        skill_retro = collect_snapshot.get("skill_retro") if isinstance(collect_snapshot.get("skill_retro"), Mapping) else {}
        snap_sessions = summary.get("sample_size", skill_retro.get("sample_size"))
        snap_cov = summary.get("coverage_status", skill_retro.get("coverage_status"))
        if sessions is None and snap_sessions is not None:
            try:
                sessions = int(snap_sessions)
            except (TypeError, ValueError):
                pass
        if not coverage and snap_cov is not None:
            coverage = str(snap_cov).strip().lower()

    run_output = ""
    if isinstance(latest_run, Mapping) and isinstance(latest_run.get("result"), Mapping):
        run_output = str(latest_run["result"].get("output") or "")
    if sessions is None and re.search(r"`?0`?\s+sessions|sessions\s*=\s*0", run_output, re.IGNORECASE):
        sessions = 0
    if not coverage and re.search(r"coverage\s*(?:recorded\s*as|=)\s*`?missing`?", run_output, re.IGNORECASE):
        coverage = "missing"

    resolved_sessions = int(sessions) if sessions is not None else 0
    resolved_coverage = str(coverage or "missing").lower()
    non_empty_ok = bool(
        issue is not None
        and sessions is not None
        and resolved_sessions > 0
        and resolved_coverage not in {"", "missing", "none", "null", "unknown"}
    )
    return {
        "ok": non_empty_ok,
        "sessions": resolved_sessions,
        "coverage": resolved_coverage,
        "raw_sessions_found": sessions is not None,
    }


def _extract_daily_learn_candidates(issue: Mapping[str, Any] | None) -> dict[str, Any]:
    if issue is None:
        return {
            "ok": False,
            "candidate_count": 0,
            "link_count": 0,
            "concept_ref_count": 0,
            "candidates": [],
        }
    desc = str(issue.get("description") or "")
    human_zone = desc.split("<details>", 1)[0]
    raw_items = [m.group(1).strip() for m in CHECKBOX_ITEM_RE.finditer(human_zone)]
    substantive_items: list[str] = []
    for item in raw_items:
        if EMPTY_CANDIDATE_FALLBACK_RE.search(item):
            continue
        if GENERIC_NOOP_RETRO_RE.search(item):
            continue
        substantive_items.append(item)

    links = MARKDOWN_LINK_RE.findall(desc)
    concept_refs = CONCEPT_PATH_RE.findall(desc)
    total_candidates = len(substantive_items)
    if total_candidates == 0 and (links or concept_refs) and not EMPTY_CANDIDATE_FALLBACK_RE.search(human_zone):
        total_candidates = max(len(links), len(concept_refs))

    return {
        "ok": total_candidates > 0,
        "candidate_count": total_candidates,
        "link_count": len(links),
        "concept_ref_count": len(concept_refs),
        "candidates": substantive_items[:8],
    }


def evaluate_skill_retro_vitals(
    *,
    target_date_str: str,
    autopilot: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
    collect_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spec = TARGET_SPECS[SKILL_RETRO_TARGET_KEY]
    today_runs = _select_today_runs(runs, target_date_str=target_date_str)
    completed_today_runs = [r for r in today_runs if str(r.get("status") or "").lower() == "completed"]
    latest_run = today_runs[0] if today_runs else (dict(runs[0]) if runs else None)
    ran_today_ok = len(completed_today_runs) > 0

    matched_issue = _match_skill_retro_issue(issues, target_date_str=target_date_str)
    issue_materialized_ok = matched_issue is not None

    telemetry_eval = _extract_skill_retro_telemetry(
        matched_issue,
        collect_snapshot=collect_snapshot,
        latest_run=latest_run,
    )
    non_empty_telemetry_ok = bool(telemetry_eval["ok"])

    metrics = {
        "ran_today": {
            "ok": ran_today_ok,
            "today_runs_count": len(today_runs),
            "completed_today_count": len(completed_today_runs),
            "latest_run_id": (latest_run or {}).get("id"),
            "latest_run_status": (latest_run or {}).get("status"),
            "latest_triggered_at": (latest_run or {}).get("triggered_at"),
        },
        "issue_materialized": {
            "ok": issue_materialized_ok,
            "expected_dedup_key": f"skill-retro-autopilot-{target_date_str}",
            "issue_id": (matched_issue or {}).get("id"),
            "issue_identifier": (matched_issue or {}).get("identifier"),
            "issue_title": (matched_issue or {}).get("title"),
        },
        "non_empty_telemetry": {
            "ok": non_empty_telemetry_ok,
            "sessions": telemetry_eval["sessions"],
            "coverage": telemetry_eval["coverage"],
            "issue_identifier": (matched_issue or {}).get("identifier"),
        },
    }
    failed_metrics = [name for name in spec.metric_names if not metrics[name]["ok"]]
    res_obj = (latest_run or {}).get("result") if isinstance((latest_run or {}).get("result"), Mapping) else {}
    output_snippet = str(
        res_obj.get("output")
        or (latest_run or {}).get("failure_reason")
        or (matched_issue or {}).get("description")
        or ""
    ).strip()[:400]
    session_id = str(res_obj.get("session_id") or "")
    work_dir = str(res_obj.get("work_dir") or "")

    return {
        "target": spec.target_key,
        "autopilot_id": str(autopilot.get("id") or spec.canonical_autopilot_id),
        "autopilot_title": str(autopilot.get("title") or spec.display_title),
        "date": target_date_str,
        "ok": len(failed_metrics) == 0,
        "failed_metrics": failed_metrics,
        "metrics": metrics,
        "evidence": {
            "autopilot_id": str(autopilot.get("id") or spec.canonical_autopilot_id),
            "target": spec.target_key,
            "failed_metrics": failed_metrics,
            "latest_run_id": (latest_run or {}).get("id"),
            "latest_run_status": (latest_run or {}).get("status"),
            "session_id": session_id or None,
            "work_dir": work_dir or None,
            "issue_id": (matched_issue or {}).get("id"),
            "issue_identifier": (matched_issue or {}).get("identifier"),
            "sessions": telemetry_eval["sessions"],
            "coverage": telemetry_eval["coverage"],
            "snippet": output_snippet,
        },
    }


def evaluate_daily_learn_vitals(
    *,
    target_date_str: str,
    autopilot: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    spec = TARGET_SPECS[DAILY_LEARN_TARGET_KEY]
    today_runs = _select_today_runs(runs, target_date_str=target_date_str)
    completed_today_runs = [r for r in today_runs if str(r.get("status") or "").lower() == "completed"]
    latest_run = today_runs[0] if today_runs else (dict(runs[0]) if runs else None)
    ran_today_ok = len(completed_today_runs) > 0

    matched_issue = _match_daily_learn_issue(issues, target_date_str=target_date_str)
    issue_materialized_ok = matched_issue is not None

    candidates_eval = _extract_daily_learn_candidates(matched_issue)
    non_empty_candidates_ok = bool(candidates_eval["ok"])

    metrics = {
        "ran_today": {
            "ok": ran_today_ok,
            "today_runs_count": len(today_runs),
            "completed_today_count": len(completed_today_runs),
            "latest_run_id": (latest_run or {}).get("id"),
            "latest_run_status": (latest_run or {}).get("status"),
            "latest_triggered_at": (latest_run or {}).get("triggered_at"),
        },
        "issue_materialized": {
            "ok": issue_materialized_ok,
            "expected_title_prefix": f"[Daily Learn {target_date_str}]",
            "issue_id": (matched_issue or {}).get("id"),
            "issue_identifier": (matched_issue or {}).get("identifier"),
            "issue_title": (matched_issue or {}).get("title"),
        },
        "non_empty_candidates": {
            "ok": non_empty_candidates_ok,
            "candidate_count": candidates_eval["candidate_count"],
            "link_count": candidates_eval["link_count"],
            "concept_ref_count": candidates_eval["concept_ref_count"],
            "issue_identifier": (matched_issue or {}).get("identifier"),
        },
    }
    failed_metrics = [name for name in spec.metric_names if not metrics[name]["ok"]]
    res_obj = (latest_run or {}).get("result") if isinstance((latest_run or {}).get("result"), Mapping) else {}
    output_snippet = str(
        res_obj.get("output")
        or (latest_run or {}).get("failure_reason")
        or (matched_issue or {}).get("description")
        or ""
    ).strip()[:400]
    session_id = str(res_obj.get("session_id") or "")
    work_dir = str(res_obj.get("work_dir") or "")

    return {
        "target": spec.target_key,
        "autopilot_id": str(autopilot.get("id") or spec.canonical_autopilot_id),
        "autopilot_title": str(autopilot.get("title") or spec.display_title),
        "date": target_date_str,
        "ok": len(failed_metrics) == 0,
        "failed_metrics": failed_metrics,
        "metrics": metrics,
        "evidence": {
            "autopilot_id": str(autopilot.get("id") or spec.canonical_autopilot_id),
            "target": spec.target_key,
            "failed_metrics": failed_metrics,
            "latest_run_id": (latest_run or {}).get("id"),
            "latest_run_status": (latest_run or {}).get("status"),
            "session_id": session_id or None,
            "work_dir": work_dir or None,
            "issue_id": (matched_issue or {}).get("id"),
            "issue_identifier": (matched_issue or {}).get("identifier"),
            "candidate_count": candidates_eval["candidate_count"],
            "snippet": output_snippet,
        },
    }


def _fetch_multica_state(
    *,
    server_url: str,
    workspace_id: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    multica_bin: str | None = None,
    collect_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    ap_proc = runner(
        ["autopilot", "list", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    ap_payload = _parse_json_stdout(ap_proc, "autopilot list")
    autopilots = ap_payload.get("autopilots", []) if isinstance(ap_payload, Mapping) else (ap_payload if isinstance(ap_payload, list) else [])

    skill_retro_ap = _resolve_autopilot_record(TARGET_SPECS[SKILL_RETRO_TARGET_KEY], autopilots)
    daily_learn_ap = _resolve_autopilot_record(TARGET_SPECS[DAILY_LEARN_TARGET_KEY], autopilots)

    sr_runs_proc = runner(
        ["autopilot", "runs", str(skill_retro_ap["id"]), "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    sr_runs_payload = _parse_json_stdout(sr_runs_proc, "autopilot runs skill-retro")
    sr_runs = sr_runs_payload.get("runs", []) if isinstance(sr_runs_payload, Mapping) else (sr_runs_payload if isinstance(sr_runs_payload, list) else [])

    dl_runs_proc = runner(
        ["autopilot", "runs", str(daily_learn_ap["id"]), "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    dl_runs_payload = _parse_json_stdout(dl_runs_proc, "autopilot runs daily-learn")
    dl_runs = dl_runs_payload.get("runs", []) if isinstance(dl_runs_payload, Mapping) else (dl_runs_payload if isinstance(dl_runs_payload, list) else [])

    issues_proc = runner(
        ["issue", "list", "--limit", "100", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    issues_payload = _parse_json_stdout(issues_proc, "issue list")
    raw_issues = issues_payload.get("issues", []) if isinstance(issues_payload, Mapping) else (issues_payload if isinstance(issues_payload, list) else [])

    # Hydrate full descriptions for matching Skill Retro / Daily Learn issues when `issue list` omits description
    hydrated_issues: list[dict[str, Any]] = []
    for item in raw_issues:
        if not isinstance(item, Mapping):
            continue
        item_dict = dict(item)
        title = str(item_dict.get("title") or "")
        if not item_dict.get("description") and ("[Skill Retro" in title or "[Daily Learn" in title):
            ident_or_id = str(item_dict.get("identifier") or item_dict.get("id") or "")
            if ident_or_id:
                get_proc = runner(
                    ["issue", "get", ident_or_id, "--output", "json"],
                    multica_bin=multica_bin,
                    server_url=server_url,
                    workspace_id=workspace_id,
                )
                if get_proc.returncode == 0 and (get_proc.stdout or "").strip():
                    try:
                        fetched = json.loads(get_proc.stdout)
                        if isinstance(fetched, Mapping):
                            item_dict = dict(fetched)
                    except ValueError:
                        pass
        hydrated_issues.append(item_dict)

    collect_snapshot: dict[str, Any] | None = None
    if collect_snapshot_path is not None and collect_snapshot_path.is_file():
        try:
            loaded = json.loads(collect_snapshot_path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping):
                collect_snapshot = dict(loaded)
        except (OSError, ValueError):
            collect_snapshot = None

    return {
        "autopilots": autopilots,
        "skill_retro_autopilot": skill_retro_ap,
        "daily_learn_autopilot": daily_learn_ap,
        "skill_retro_runs": sr_runs,
        "daily_learn_runs": dl_runs,
        "issues": hydrated_issues,
        "collect_snapshot": collect_snapshot,
    }


def build_m2_escalation_prompt(
    failure_evidence: Mapping[str, Any],
    *,
    target_date_str: str,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> str:
    target = str(failure_evidence.get("target") or "")
    ap_id = str(failure_evidence.get("autopilot_id") or "")
    failed_metrics = list(failure_evidence.get("failed_metrics") or [])
    run_id = failure_evidence.get("latest_run_id")
    session_id = failure_evidence.get("session_id")
    issue_ident = failure_evidence.get("issue_identifier")
    snippet = str(failure_evidence.get("snippet") or "")

    if target == SKILL_RETRO_TARGET_KEY:
        remediation_hint = (
            f"Inspect session `{session_id}` / run `{run_id}` and `{SKILL_RETRO_AUTOPILOT_SCRIPT_PATH}`. "
            f"Ensure real telemetry (`warehouse.db` / `session-store.db` / `~/.copilot/session-state`) is read so `sessions > 0` "
            f"and `coverage != 'missing'`, update/re-file today's `[Skill Retro]` issue (`skill-retro-autopilot-{target_date_str}`), "
            f"and trigger rerun `multica autopilot trigger {ap_id}`."
        )
    else:
        remediation_hint = (
            f"Inspect session `{session_id}` / run `{run_id}` for Daily Knowledge Digest (`{ap_id}`). "
            f"Fix the root cause preventing `[Daily Learn {target_date_str}]` issue materialization or non-empty learning candidates (`> 0` items), "
            f"trigger rerun `multica autopilot trigger {ap_id}`, and verify the `[Daily Learn {target_date_str}]` issue exists on Multica."
        )

    return (
        f"[Autopilot Health Watchdog Escalation · {target_date_str}] "
        f"Target `{target}` (`{ap_id}`) failed basic vital metrics {failed_metrics}. "
        f"Evidence: run_id=`{run_id}`, session_id=`{session_id}`, issue=`{issue_ident}`, "
        f"sessions=`{failure_evidence.get('sessions')}`, coverage=`{failure_evidence.get('coverage')}`, "
        f"candidate_count=`{failure_evidence.get('candidate_count')}`, snippet={snippet!r}. "
        f"Action required: {remediation_hint} "
        f"Finally re-verify with `python3 skills/team-lead/scripts/autopilot_watchdog.py check --date {target_date_str} --server-url {server_url} --workspace-id {workspace_id} --json`."
    )


def _default_m2_escalator(
    failure_evidence: Mapping[str, Any],
    *,
    target_date_str: str,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    prompt = build_m2_escalation_prompt(
        failure_evidence,
        target_date_str=target_date_str,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    target = str(failure_evidence.get("target") or "")
    ap_id = str(failure_evidence.get("autopilot_id") or "")
    if dry_run:
        return {
            "ok": True,
            "action": "dry_run_m2_escalation_and_rerun",
            "target": target,
            "autopilot_id": ap_id,
            "m2_prompt": prompt,
            "rerun_triggered": False,
        }

    actions_taken: list[str] = []
    remediation_detail: dict[str, Any] = {}

    # 1. Deterministic self-heal for skill-retro `sessions=0` / `coverage=missing` or missing issue before/with M2 escalation
    if target == SKILL_RETRO_TARGET_KEY and SKILL_RETRO_AUTOPILOT_SCRIPT_PATH.is_file():
        collect_out = Path(f"/tmp/multica-skills-autopilot-watchdog-{target_date_str}.json")
        run_once_cmd = [
            sys.executable,
            str(SKILL_RETRO_AUTOPILOT_SCRIPT_PATH),
            "run-once",
            "--collect-output",
            str(collect_out),
            "--server-url",
            server_url,
            "--workspace-id",
            workspace_id,
        ]
        proc = subprocess.run(run_once_cmd, capture_output=True, text=True, check=False, timeout=90.0)
        remediation_detail["skill_retro_run_once_exit"] = proc.returncode
        if proc.returncode == 0:
            actions_taken.append("skill_retro_deterministic_recollect_and_file_issue")

    # 2. Dispatch M2 investigation via m2-router.py --plan (and herdr-dispatch.sh when available)
    if M2_ROUTER_SCRIPT_PATH.is_file():
        plan_out = Path(f"/tmp/autopilot-watchdog-m2-plan-{target}-{target_date_str}.json")
        router_cmd = [
            sys.executable,
            str(M2_ROUTER_SCRIPT_PATH),
            "--message",
            prompt,
            "--plan",
            "--plan-file",
            str(plan_out),
            "--json",
        ]
        try:
            r_proc = subprocess.run(router_cmd, capture_output=True, text=True, check=False, timeout=20.0)
            remediation_detail["m2_router_plan_exit"] = r_proc.returncode
            remediation_detail["m2_router_plan_file"] = str(plan_out)
            if r_proc.returncode == 0:
                actions_taken.append("m2_router_plan_prepared")
        except subprocess.TimeoutExpired:
            remediation_detail["m2_router_plan_exit"] = 124

    # 3. Trigger rerun of the failed autopilot via `multica autopilot trigger <id>`
    trig_proc = runner(
        ["autopilot", "trigger", ap_id, "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    rerun_triggered = trig_proc.returncode == 0
    trigger_response: Any = {}
    if rerun_triggered and (trig_proc.stdout or "").strip():
        with ExceptionContext():
            trigger_response = json.loads(trig_proc.stdout)
        actions_taken.append("multica_autopilot_triggered")
    else:
        remediation_detail["trigger_stderr"] = (trig_proc.stderr or trig_proc.stdout or "").strip()[:300]

    return {
        "ok": rerun_triggered or bool(actions_taken),
        "action": "m2_escalated_and_rerun_triggered",
        "target": target,
        "autopilot_id": ap_id,
        "actions_taken": actions_taken,
        "m2_prompt": prompt,
        "rerun_triggered": rerun_triggered,
        "trigger_response": trigger_response,
        "remediation_detail": remediation_detail,
    }


class ExceptionContext:
    def __enter__(self) -> "ExceptionContext":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True


def run_watchdog(
    *,
    target_date: date | None = None,
    targets: Sequence[str] = ALLOWED_TARGETS,
    auto_escalate: bool = True,
    dry_run: bool = False,
    reverify_polls: int = 1,
    reverify_interval_sec: float = 0.0,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    collect_snapshot_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    escalator_fn: Callable[..., dict[str, Any]] | None = None,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    resolved_date = target_date or datetime.now(CST).date()
    date_str = resolved_date.isoformat()

    normalized_targets: list[str] = []
    for raw_t in targets:
        key = str(raw_t or "").strip().lower()
        if key in {"skills-roi-diagnostic", "skill-retro-diagnostic", "skill_retro"}:
            key = SKILL_RETRO_TARGET_KEY
        elif key in {"daily-learn-recommend", "daily_learn", "knowledge-digest"}:
            key = DAILY_LEARN_TARGET_KEY
        if key not in TARGET_SPECS:
            raise ValueError(f"Unsupported watchdog target '{raw_t}'; only {ALLOWED_TARGETS} are allowed")
        if key not in normalized_targets:
            normalized_targets.append(key)

    def _evaluate_once() -> dict[str, dict[str, Any]]:
        state = _fetch_multica_state(
            server_url=server_url,
            workspace_id=workspace_id,
            runner=runner,
            multica_bin=multica_bin,
            collect_snapshot_path=collect_snapshot_path,
        )
        results: dict[str, dict[str, Any]] = {}
        if SKILL_RETRO_TARGET_KEY in normalized_targets:
            results[SKILL_RETRO_TARGET_KEY] = evaluate_skill_retro_vitals(
                target_date_str=date_str,
                autopilot=state["skill_retro_autopilot"],
                runs=state["skill_retro_runs"],
                issues=state["issues"],
                collect_snapshot=state["collect_snapshot"],
            )
        if DAILY_LEARN_TARGET_KEY in normalized_targets:
            results[DAILY_LEARN_TARGET_KEY] = evaluate_daily_learn_vitals(
                target_date_str=date_str,
                autopilot=state["daily_learn_autopilot"],
                runs=state["daily_learn_runs"],
                issues=state["issues"],
            )
        return results

    initial_checks = _evaluate_once()
    initial_failures = [
        check["evidence"]
        for check in initial_checks.values()
        if not check["ok"]
    ]

    if not initial_failures:
        sig = _compute_signature({"date": date_str, "status": "HEALTHY", "targets": normalized_targets})
        return {
            "schema": "autopilot-health-watchdog/v1",
            "ok": True,
            "status": "HEALTHY",
            "token_cost": 0,
            "date": date_str,
            "checked_targets": normalized_targets,
            "checks": initial_checks,
            "initial_checks": initial_checks,
            "failures": [],
            "escalations": [],
            "reverified": False,
            "watermark": f"{LOOP_ID}:{date_str}:healthy:{sig}",
        }

    escalations: list[dict[str, Any]] = []
    final_checks = initial_checks
    reverified = False

    if auto_escalate:
        escalate = escalator_fn or _default_m2_escalator
        for failure in initial_failures:
            esc_res = escalate(
                failure,
                target_date_str=date_str,
                server_url=server_url,
                workspace_id=workspace_id,
                dry_run=dry_run,
                runner=runner,
                multica_bin=multica_bin,
            )
            escalations.append(esc_res)

        if not dry_run and reverify_polls > 0:
            reverified = True
            for attempt in range(max(1, reverify_polls)):
                if attempt > 0 and reverify_interval_sec > 0:
                    time.sleep(reverify_interval_sec)
                final_checks = _evaluate_once()
                if all(c["ok"] for c in final_checks.values()):
                    break

    remaining_failures = [
        check["evidence"]
        for check in final_checks.values()
        if not check["ok"]
    ]
    overall_ok = len(remaining_failures) == 0
    if overall_ok and initial_failures:
        status = "RECOVERED_AFTER_M2_RERUN"
    elif overall_ok:
        status = "HEALTHY"
    else:
        status = "ESCALATED_UNHEALTHY" if auto_escalate else "UNHEALTHY"

    sig = _compute_signature(
        {
            "date": date_str,
            "status": status,
            "initial_failures": [
                {"target": f.get("target"), "failed_metrics": f.get("failed_metrics"), "run_id": f.get("latest_run_id")}
                for f in initial_failures
            ],
            "remaining_failures": [
                {"target": f.get("target"), "failed_metrics": f.get("failed_metrics")}
                for f in remaining_failures
            ],
        }
    )
    return {
        "schema": "autopilot-health-watchdog/v1",
        "ok": overall_ok,
        "status": status,
        "token_cost": 0 if not initial_failures else None,
        "date": date_str,
        "checked_targets": normalized_targets,
        "checks": final_checks,
        "initial_checks": initial_checks,
        "failures": remaining_failures,
        "initial_failures": initial_failures,
        "escalations": escalations,
        "reverified": reverified,
        "watermark": f"{LOOP_ID}:{date_str}:{sig}",
    }


def run_loop_check(
    *,
    target_date: date | None = None,
    auto_escalate: bool = True,
    dry_run: bool = False,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    escalator_fn: Callable[..., dict[str, Any]] | None = None,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    """Collector entry point for `multica_loop_runner.py run-loop --loop autopilot-health-watchdog`."""
    resolved_date = target_date or datetime.now(CST).date()
    date_str = resolved_date.isoformat()
    report = run_watchdog(
        target_date=resolved_date,
        auto_escalate=auto_escalate,
        dry_run=dry_run,
        server_url=server_url,
        workspace_id=workspace_id,
        runner=runner,
        escalator_fn=escalator_fn,
        multica_bin=multica_bin,
    )

    initial_failures = list(report.get("initial_failures") or [])
    remaining_failures = list(report.get("failures") or [])
    has_unrecovered_failure = len(remaining_failures) > 0

    items: list[dict[str, str]] = []
    for fail in (remaining_failures or initial_failures):
        target = str(fail.get("target") or "autopilot")
        ap_id = str(fail.get("autopilot_id") or "")[:8]
        failed_m = ", ".join(str(m) for m in (fail.get("failed_metrics") or []))
        if target == SKILL_RETRO_TARGET_KEY:
            items.append(
                {
                    "what": f"修复并重跑 `skill-retro` (`{ap_id}`) 巡检任务（异常指标：`{failed_m}`）",
                    "why": f"检测到 `sessions={fail.get('sessions')}` / `coverage={fail.get('coverage')}` 或今日单据缺失，需恢复真实遥测覆盖",
                }
            )
        else:
            items.append(
                {
                    "what": f"修复并重跑 `daily-learn` (`{ap_id}`) 巡检任务（异常指标：`{failed_m}`）",
                    "why": f"检测到今日学习单缺失或候选主题为空（`candidates={fail.get('candidate_count')}`），需恢复双轨产出",
                }
            )

    if report["status"] == "HEALTHY":
        summary = "今日 `skill-retro` (`4e06bf2a`) 与 `daily-learn` (`8b4d6d04`) 三项核心生命体征全部健康通过。"
    elif report["status"] == "RECOVERED_AFTER_M2_RERUN":
        summary = (
            f"检测到 {len(initial_failures)} 项定时任务基本指标异常，已自动升级 M2 修复并重跑复验通过。"
        )
    else:
        summary = (
            f"检测到 {len(remaining_failures)} 项定时任务（`skill-retro` / `daily-learn`）基本生命体征未通过，已升级 M2 排障重跑。"
        )

    return {
        "loop_id": LOOP_ID,
        "date": date_str,
        "status": report["status"],
        "ok": report["ok"],
        "has_delta": has_unrecovered_failure,
        "delta_count": len(remaining_failures),
        "actionable": has_unrecovered_failure,
        "severity": "high" if has_unrecovered_failure else "low",
        "token_cost": 0 if report["status"] == "HEALTHY" else None,
        "watermark": report["watermark"],
        "cursor": f"{LOOP_ID}-{date_str}",
        "title": f"[P3 / Priority 3][Autopilot Watchdog] {date_str} 定时任务健康巡检与 M2 自愈报告",
        "summary": summary,
        "items": items,
        "jev": {
            "actionable": has_unrecovered_failure,
            "decision_source": "watchdog_deterministic_vitals",
        },
        "watchdog": report,
        "raw": report,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopilot_watchdog.py",
        description="Daily Autopilot Health Watchdog for skill-retro (4e06bf2a) and daily-learn (8b4d6d04)",
    )
    sub = parser.add_subparsers(dest="subcommand", required=False)

    def _add_common_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--date", default="today", help="Target CST date YYYY-MM-DD or 'today'")
        p.add_argument(
            "--target",
            dest="targets",
            action="append",
            default=None,
            help="Target autopilot to check (skill-retro, daily-learn). Defaults to both.",
        )
        p.add_argument("--no-escalate", action="store_true", help="Check only without triggering M2 escalation or rerun")
        p.add_argument("--dry-run", action="store_true", help="Plan M2 escalation without mutating live Multica state")
        p.add_argument("--reverify-polls", type=int, default=1, help="Number of re-verification polls after rerun")
        p.add_argument("--reverify-interval", type=float, default=0.0, help="Seconds between re-verification polls")
        p.add_argument("--collect-snapshot", type=Path, default=None, help="Optional path to skill-retro collect snapshot JSON")
        p.add_argument("--server-url", default=DEFAULT_SERVER_URL)
        p.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
        p.add_argument("--json", action="store_true", default=True)

    p_check = sub.add_parser("check", help="Check vital metrics and escalate/rerun on failure")
    _add_common_flags(p_check)

    p_loop = sub.add_parser("loop-check", help="Run collector payload for multica_loop_runner.py")
    _add_common_flags(p_loop)

    _add_common_flags(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)
    subcmd = ns.subcommand or "check"
    target = datetime.now(CST).date() if ns.date == "today" else date.fromisoformat(ns.date)
    selected_targets = tuple(ns.targets) if ns.targets else ALLOWED_TARGETS

    if subcmd == "loop-check":
        payload = run_loop_check(
            target_date=target,
            auto_escalate=not ns.no_escalate,
            dry_run=ns.dry_run,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1

    report = run_watchdog(
        target_date=target,
        targets=selected_targets,
        auto_escalate=not ns.no_escalate,
        dry_run=ns.dry_run,
        reverify_polls=ns.reverify_polls,
        reverify_interval_sec=ns.reverify_interval,
        server_url=ns.server_url,
        workspace_id=ns.workspace_id,
        collect_snapshot_path=ns.collect_snapshot,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
