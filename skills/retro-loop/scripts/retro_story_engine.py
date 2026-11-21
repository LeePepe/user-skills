#!/usr/bin/env python3
"""Deterministic retro/story helpers for evidence validation and promotion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import fcntl
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence
from urllib.request import Request, urlopen

STATES = ("draft", "brief_review", "scripting", "script_review", "production", "final_review", "ready")
_RANK = {state: index for index, state in enumerate(STATES)}
DEFAULT_AIDATA_DIR = Path.home() / "Development" / "AIDash" / "aidata"
DEFAULT_PROPOSALS_PATH = DEFAULT_AIDATA_DIR / "L5_apps" / "digest" / "state" / "proposals.jsonl"
DEFAULT_WAREHOUSE_DB = DEFAULT_AIDATA_DIR / "L3_merge" / "warehouse.db"
DEFAULT_RETRO_QUERIES_DIR = DEFAULT_AIDATA_DIR / "L4_serve" / "queries" / "retro"
DEFAULT_MAGPIE_USAGE = Path.home() / ".config" / "magpie" / "usage.jsonl"
DEFAULT_COPILOT_DB = Path.home() / ".copilot" / "session-store.db"
DEFAULT_COPILOT_SESSIONS_DIR = Path.home() / ".copilot" / "session-state"
DEFAULT_SKILL_TELEMETRY_USAGE_JSONL = Path.home() / ".local" / "share" / "skill-telemetry" / "usage.jsonl"
DEFAULT_HANDOFF_ROOT = Path.home() / "Development" / "handoff"
DEFAULT_DSH_SESSIONS = Path.home() / ".dsh" / "sessions"
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEV_ROOT = Path.home() / "Development"
DEFAULT_ICLOUD_SYNC_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"
DEFAULT_ICLOUD_TELEMETRY_SHARDS_DIR = DEFAULT_ICLOUD_SYNC_DIR / "telemetry-shards"
DEFAULT_ICLOUD_SKILLS_ROI_PATH = DEFAULT_ICLOUD_SYNC_DIR / "jobs" / "aidata-digest" / "skills-roi.json"
DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH = (
    Path.home() / "Library" / "Application Support" / "AIDash" / "jobs" / "aidata-digest" / "skills-roi.json"
)
_SKILL_NAME_RE = re.compile(
    r"(?:<skill-context\s+name=[\"'](?P<ctx>[A-Za-z0-9_-]+)[\"']"
    r"|(?<![A-Za-z0-9_-])(?:/)?skills/(?P<path>[A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"'](?P<call>[A-Za-z0-9_-]+)[\"'])"
)
_REF_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/references/(?P<ref>[A-Za-z0-9_./-]+)"
)
_SCRIPT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/scripts/(?P<script>[A-Za-z0-9_.-]+\.(?:py|sh|ts))"
)


_PRIORITY_MAP = {
    "p0": "high",
    "p1": "high",
    "p2": "medium",
    "p3": "low",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_CST_TZ = timezone(timedelta(hours=8))


def _cst_today_str() -> str:
    return datetime.now(_CST_TZ).date().isoformat()


def _normalize_priority(raw_priority: Any, default: str = "high") -> str:
    norm = str(raw_priority or default).strip().lower()
    return _PRIORITY_MAP.get(norm, default)


class OptimisticLockError(RuntimeError):

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def init_state(state_path: Path, record_id: str, source_goal: str, evidence_sha: str) -> dict[str, Any]:
    state = {
        "version": 1,
        "record_id": record_id,
        "source_goal": source_goal,
        "evidence_sha": evidence_sha,
        "state": "draft",
        "stale_assets": False,
        "approvals": {"brief_review": None, "script_review": None, "final_review": None},
    }
    _write_json(state_path, state)
    return state


def transition_state(
    state_path: Path,
    target_state: str,
    expected_version: int,
    actor: str = "tl",
    evidence_sha: str | None = None,
) -> dict[str, Any]:
    current = _read_state(state_path)
    if current.get("version") != expected_version:
        raise OptimisticLockError(409, f"expected version {expected_version}, got {current.get('version')}")
    if target_state not in _RANK:
        raise ValueError(f"unknown state: {target_state}")
    current_state = current["state"]
    if evidence_sha is not None and evidence_sha != current.get("evidence_sha") and _RANK[current_state] >= _RANK["scripting"]:
        current["evidence_sha"] = evidence_sha
        current["stale_assets"] = True
        current["approvals"]["script_review"] = None
        current["approvals"]["final_review"] = None
        current["state"] = "scripting"
    else:
        if target_state != current_state and target_state != "scripting":
            allowed = target_state == "draft" or _RANK[target_state] == _RANK[current_state] + 1
            if not allowed:
                raise ValueError(f"illegal transition: {current_state} -> {target_state}")
        current["state"] = target_state
        if evidence_sha is not None:
            current["evidence_sha"] = evidence_sha
        for approval_state in ("brief_review", "script_review", "final_review"):
            if _RANK[target_state] >= _RANK[approval_state]:
                current["approvals"][approval_state] = {
                    "actor": actor,
                    "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
    current["version"] += 1
    _write_json(state_path, current)
    return current


def validate_hourly_report(report: dict[str, Any], manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    hours = report.get("hours", [])
    citations = report.get("citations", {})
    errors: list[str] = []
    if len(hours) != 24 or [item.get("hour") for item in hours] != list(range(24)):
        errors.append("hours must contain ordered 0..23 entries")
    manifest_ids = {
        record.get("evidence_id")
        for bucket in (manifest or {}).get("buckets", [])
        for record in bucket.get("records", [])
    }
    for item in hours:
        hour = item.get("hour")
        ids = item.get("evidenceIds", [])
        active = bool(item.get("summary")) and item.get("summary") != "无采集数据。"
        if not active and (item.get("summary") != "无采集数据。" or ids):
            errors.append(f"hour {hour}: empty-hour contract violated")
        if active:
            if not 1 <= len(ids) <= 3:
                errors.append(f"hour {hour}: active hours need 1..3 evidence IDs")
            for evidence_id in ids:
                if not isinstance(evidence_id, str) or not evidence_id.startswith(f"H{int(hour):02d}-"):
                    errors.append(f"hour {hour}: invalid evidence ID")
                if evidence_id not in citations and evidence_id not in manifest_ids:
                    errors.append(f"hour {hour}: unknown evidence ID {evidence_id}")
    return {"ok": not errors, "errors": errors}


def _resolve_falcon_source_key(role: str = "retro-loop") -> str:
    for name in ("FALCON_SOURCE_KEY", "FALCON_API_KEY", "JEV_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    try:
        data = json.loads((Path.home() / ".config" / "falcon" / "source-keys.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if isinstance(data, dict):
        for key in (role, "shared-ci", "team-lead", "goal-task"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _ask_jev(question: dict[str, Any], endpoint: str) -> dict[str, Any]:
    q_type = str(question.get("type", "noul")).lower()
    q_spec: dict[str, Any] = {"type": q_type, "instructions": f"Evaluate hour {question.get('hour', 0)} claim."}
    if q_type == "choice":
        choices = question.get("choices") or ["supports", "contradicts", "unrelated"]
        q_spec["criteria"] = {str(c): str(c) for c in choices}
    payload = {
        "model": "microsoft-decision-1",
        "state": {"hour": question.get("hour"), "claim": question.get("claim", "")},
        "questions": {"q": q_spec},
    }
    source_key = _resolve_falcon_source_key("retro-loop")
    headers = {"Content-Type": "application/json", "X-Falcon-Agent": "retro-loop"}
    if source_key:
        headers["Authorization"] = f"Bearer {source_key}"
        headers["X-Falcon-Source-Key"] = source_key
    request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=2) as response:
        raw = json.loads(response.read().decode())
    if isinstance(raw, dict) and isinstance(raw.get("answers"), dict) and isinstance(raw["answers"].get("q"), dict):
        return raw["answers"]["q"]
    return raw if isinstance(raw, dict) else {}


def verify_citations_with_jev(
    report: dict[str, Any],
    manifest: dict[str, Any],
    endpoint: str = "http://127.0.0.1:19823/v1/systemone",
    sender: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    active = [item for item in report.get("hours", []) if item.get("evidenceIds")]
    questions = []
    for item in active:
        questions.extend((
            {"hour": item["hour"], "type": "Choice", "choices": ["supports", "contradicts", "unrelated"], "claim": item.get("summary", "")},
            {"hour": item["hour"], "type": "Noul", "claim": item.get("summary", "")},
        ))
    def ask(question: dict[str, Any]) -> dict[str, Any]:
        try:
            return (sender or (lambda value: _ask_jev(value, endpoint)))(question)
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}
    with ThreadPoolExecutor(max_workers=max(1, len(questions))) as pool:
        answers = list(pool.map(ask, questions))
    by_hour: dict[int, list[dict[str, Any]]] = {}
    for question, answer in zip(questions, answers):
        by_hour.setdefault(question["hour"], []).append(answer)
    results = []
    for hour, hour_answers in sorted(by_hour.items()):
        choice = next((answer for answer in hour_answers if answer.get("type") == "Choice" or "choice" in answer), {})
        noul = next((answer for answer in hour_answers if answer.get("type") == "Noul" or "noul" in answer), {})
        choice_value = choice.get("choice", choice.get("label"))
        confidence = float(choice.get("confidence", 0))
        noul_score = float(noul.get("noul", noul.get("score", 1)))
        status = "PASS" if choice_value == "supports" and confidence >= 0.70 and noul_score < 0.35 else (
            "FAIL" if choice_value in {"contradicts", "unrelated"} else "WARN")
        results.append({"hour": hour, "status": status, "choice": choice_value, "confidence": confidence, "noul": noul_score})
    return {"status": "PASS" if results and all(item["status"] == "PASS" for item in results) else
            "FAIL" if any(item["status"] == "FAIL" for item in results) else "WARN", "results": results}


def compose_prompt(category: str, intent: dict[str, Any], sources: dict[str, Any], slots_path: Path | None = None) -> str:
    path = slots_path or Path(__file__).resolve().parents[2] / "story-craft" / "references" / "prompt-slots.json"
    slots = json.loads(path.read_text(encoding="utf-8"))
    for key in slots["intent_required_keys"]:
        if key not in intent:
            raise ValueError(f"missing intent key: {key}")
    for key in slots["sources_required_keys"]:
        if key not in sources:
            raise ValueError(f"missing source key: {key}")
    lines = [str(value) for value in slots["global"].values()]
    category_slots = slots["categories"].get(category, {})
    if category_slots.get("mode") == "override":
        lines = [value for key, value in slots["global"].items() if key == "privacy_redlines"] + list(category_slots.get("slots", {}).values())
    else:
        lines.extend(str(value) for value in category_slots.get("slots", {}).values())
    lines.extend(f"{key}: {value}" for key, value in intent.items())
    lines.extend(f"{key}: {value}" for key, value in sources.items())
    return "\n".join(lines)


_APP_REPAIR_TARGETS: dict[str, dict[str, str]] = {
    "AIDash": {
        "layer": "Packages/AIDashUI",
        "verify_cmd": "scripts/context/run AIDashUI --mode local",
    },
    "VitalStride": {
        "layer": "Packages/VitalStrideCore",
        "verify_cmd": "swift test",
    },
    "VoxPocket": {
        "layer": "Packages/VoxPocketCore",
        "verify_cmd": "swift test",
    },
    "Financial": {
        "layer": "backend/services",
        "verify_cmd": "pytest -q",
    },
}


def _recommend_repair_target(app_name: str, stack_top_symbol: str) -> tuple[str, str]:
    base = _APP_REPAIR_TARGETS.get(
        app_name,
        {"layer": f"{app_name}/Core", "verify_cmd": "./scripts/verify"},
    )
    sym_lower = (stack_top_symbol or "").lower()
    if app_name == "AIDash":
        if any(k in sym_lower for k in ("core", "cloudkit", "xpc", "model", "payload")):
            return "Packages/AIDashCore", "scripts/context/run AIDashCore --mode local"
        if any(k in sym_lower for k in ("aidata", "digest", "inbox", "bridge")):
            return "aidata/L5_apps", "scripts/context/run AidataL5 --mode local"
    return base["layer"], base["verify_cmd"]


def _build_app_telemetry_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        app_name = str(row.get("app_name") or "").strip()
        if not app_name:
            continue
        new_crashes = int(row.get("new_crash_signature_count") or 0)
        is_regressed = int(row.get("is_perf_regressed") or 0)
        if new_crashes <= 0 and is_regressed <= 0:
            continue
        stack_top_symbol = str(
            row.get("stack_top_symbol")
            or row.get("top_crash_symbol")
            or row.get("perf_stack_top_symbol")
            or "unknown_symbol"
        ).strip()
        signature = str(
            row.get("signature")
            or row.get("top_new_signature")
            or row.get("perf_signature")
            or f"{app_name}.anomaly"
        ).strip()
        layer, verify_cmd = _recommend_repair_target(app_name, stack_top_symbol)
        slug_sig = "".join(ch.lower() if ch.isalnum() else "-" for ch in signature).strip("-")[:24] or "sig"

        if new_crashes > 0:
            crash_free = float(row.get("crash_free_rate_7d") or 0.0) * 100.0
            candidates.append({
                "id": f"telemetry-crash-{app_name.lower()}-{slug_sig}",
                "title": f"[Crash] {app_name} 新增闪退签名 ({new_crashes} 项): {signature[:40]}",
                "detail": (
                    f"app_name={app_name} | stack_top_symbol={stack_top_symbol} | "
                    f"signature={signature} | crash_free_7d={crash_free:.2f}% | "
                    f"recommended_layer={layer} | verify_cmd={verify_cmd}"
                ),
                "priority": "high",
                "app_name": app_name,
                "event_kind": "crash",
                "signature": signature,
                "stack_top_symbol": stack_top_symbol,
                "recommended_layer": layer,
                "verify_cmd": verify_cmd,
            })
        if is_regressed > 0:
            reg_pct = float(row.get("p95_regression_pct") or 0.0)
            p95_cur = float(row.get("p95_metric_ms_7d") or 0.0)
            p95_prev = float(row.get("p95_metric_ms_prev_7d") or 0.0)
            candidates.append({
                "id": f"telemetry-perf-{app_name.lower()}-{slug_sig}",
                "title": f"[Perf] {app_name} P95 耗时退化 +{reg_pct:.1f}% ({p95_prev:.0f}ms -> {p95_cur:.0f}ms)",
                "detail": (
                    f"app_name={app_name} | stack_top_symbol={stack_top_symbol} | "
                    f"signature={signature} | p95_7d={p95_cur:.1f}ms (prev={p95_prev:.1f}ms, +{reg_pct:.1f}%) | "
                    f"recommended_layer={layer} | verify_cmd={verify_cmd}"
                ),
                "priority": "high",
                "app_name": app_name,
                "event_kind": "perf",
                "signature": signature,
                "stack_top_symbol": stack_top_symbol,
                "recommended_layer": layer,
                "verify_cmd": verify_cmd,
            })
    return candidates


def _build_tool_context_burn_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        t_name = str(row.get("tool_name") or "").strip()
        bloated = int(row.get("bloated_call_count") or 0)
        if not t_name or bloated <= 0:
            continue
        p95_tok = int(row.get("p95_tokens") or 0)
        total_tok = int(row.get("total_tokens") or 0)
        share = float(row.get("share_pct") or 0.0)
        dom_skill = str(row.get("dominant_skill") or "team-lead").strip()
        slug_tool = "".join(ch if ch.isalnum() else "-" for ch in t_name.lower()).strip("-") or "tool"
        candidates.append({
            "id": f"tool-burn-{dom_skill}-{slug_tool}",
            "ref": f"skill-roi://{dom_skill}",
            "title": (
                f"[Context Burn · estimated] {t_name} ({dom_skill}) 产出 {bloated} 次 >10k tok 膨胀结果："
                "建议加 --head-limit / view_range 或脚本下沉过滤"
            ),
            "detail": (
                f"tool_name={t_name} | dominant_skill={dom_skill} | "
                f"bloated_calls_gt_10k={bloated} | p95_tokens={p95_tok} | "
                f"total_tokens={total_tok} ({share:.1f}%) | "
                "remediation=Add --head-limit, view_range, or sink filtering into deterministic script"
            ),
            "priority": "high",
            **{key: row[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                         "sample_size", "coverage_status", "estimated", "estimation_method") if key in row},
        })
    return candidates


def discover_aidata_signals(
    warehouse_db: Path | None = None,
    queries_dir: Path | None = None,
    proposals_path: Path | None = None,
    auto_propose: bool = True,
) -> dict[str, Any]:
    """Run aidata L4 retro queries against warehouse.db to discover anomalies."""
    db_path = warehouse_db or DEFAULT_WAREHOUSE_DB
    q_dir = queries_dir or DEFAULT_RETRO_QUERIES_DIR
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    findings: list[dict[str, Any]] = []
    project_loop_a_proposals: list[dict[str, Any]] = []
    if not db_path.is_file() or not q_dir.is_dir():
        return {
            "ok": True,
            "degraded": True,
            "findings": findings,
            "project_loop_a_proposals": project_loop_a_proposals,
        }

    with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        for sql_file in sorted(q_dir.glob("*.sql")):
            query_name = f"retro/{sql_file.stem}"
            sql_text = "\n".join(
                line for line in sql_file.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("--")
            ).strip()
            if not sql_text:
                continue
            try:
                cur = conn.execute(sql_text)
                cols = [d[0] for d in (cur.description or [])]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()[:10]]
                if rows:
                    findings.append({"query": query_name, "count": len(rows), "sample": rows[:3]})
                    if query_name == "retro/app_crash_and_perf":
                        for cand in _build_app_telemetry_candidates(rows):
                            if auto_propose and prop_path is not None:
                                routed = route_retro_candidate(
                                    cand, mode="observe", proposals_path=prop_path, deduplicate_pending=True
                                )
                                project_loop_a_proposals.append(routed["proposal"])
                            else:
                                project_loop_a_proposals.append(cand)
                    elif query_name == "retro/tool_context_burn":
                        for cand in _build_tool_context_burn_candidates(rows):
                            if auto_propose and prop_path is not None:
                                routed = route_retro_candidate(
                                    cand, mode="observe", proposals_path=prop_path, deduplicate_pending=True
                                )
                                project_loop_a_proposals.append(routed["proposal"])
                            else:
                                project_loop_a_proposals.append(cand)
            except sqlite3.Error:
                continue
    return {
        "ok": True,
        "degraded": False,
        "findings": findings,
        "project_loop_a_proposals": project_loop_a_proposals,
    }



CST = timezone(timedelta(hours=8))


def _window_bounds(days: int = 7) -> tuple[datetime, datetime]:
    today = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1), today + timedelta(days=1)


def _observed_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        text = str(value)
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=CST if len(text) == 10 else timezone.utc)
        return stamp.astimezone(CST)
    except (ValueError, TypeError):
        return None


def _timed_rows(conn: sqlite3.Connection, table: str, days: int = 7) -> list[dict[str, Any]]:
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    time_column = next((name for name in ("observed_at", "completed_at", "ended_at", "started_at",
                        "timestamp", "first_seen_at", "ts", "created_at", "cst_day") if name in columns), None)
    if time_column is None:
        return []
    start, end = _window_bounds(days)
    rows = []
    if time_column == "cst_day":
        predicate = f'"{time_column}" >= ? AND "{time_column}" < ?'
        bounds = (start.date().isoformat(), end.date().isoformat())
    else:
        # Bare dates are CST days; offset/naive timestamps are parsed as UTC by SQLite.
        observed_jd = (f'CASE WHEN length("{time_column}") = 10 '
                       f'THEN julianday("{time_column}", \'-8 hours\') ELSE julianday("{time_column}") END')
        predicate = f'{observed_jd} >= julianday(?) AND {observed_jd} < julianday(?)'
        bounds = (start.isoformat(), end.isoformat())
    for values in conn.execute(f'SELECT * FROM "{table}" WHERE {predicate}', bounds):
        record = dict(zip(columns, values))
        stamp = _observed_at(record[time_column])
        if stamp and start <= stamp < end:
            record["_observed_at"] = stamp
            rows.append(record)
    return rows


def _freshness(stamps: list[datetime], present: bool = True, days: int = 7) -> dict[str, Any]:
    start, end = _window_bounds(days)
    return {"window_cst": f"{days}d", "window_start": start.isoformat() if stamps else None,
            "window_end": end.isoformat() if stamps else None,
            "source_as_of": max(stamps).isoformat() if stamps else None,
            "sample_size": len(stamps),
            "coverage_status": "healthy" if stamps else ("unknown" if present else "missing")}


def _snapshot_freshness(paths: list[Path], sample_size: int) -> dict[str, Any]:
    stamps = [datetime.fromtimestamp(path.stat().st_mtime, CST) for path in paths if path.is_file()]
    return {"window_cst": "snapshot", "window_start": min(stamps).isoformat() if stamps else None,
            "window_end": max(stamps).isoformat() if stamps else None,
            "source_as_of": max(stamps).isoformat() if stamps else None,
            "sample_size": sample_size, "coverage_status": "healthy" if sample_size and stamps else "unknown",
            "freshness_basis": "source_file_mtime"}


def _event_fingerprint(rec: dict[str, Any]) -> str:
    explicit = str(rec.get("event_id") or "").strip()
    if explicit:
        return explicit
    host = str(rec.get("host") or "local").strip().lower()
    kind = str(rec.get("kind") or rec.get("event_type") or "usage").strip()
    sid = str(rec.get("session_id") or rec.get("route_id") or "").strip()
    turn_id = str(rec.get("turn_id") or rec.get("call_uid") or rec.get("hit_uid") or "").strip()
    ts = str(rec.get("ts") or rec.get("t") or rec.get("timestamp") or rec.get("created_at") or "").strip()
    skill = str(rec.get("skill") or "").strip()
    tool_or_path = str(
        rec.get("script_path")
        or rec.get("reference_path")
        or rec.get("tool")
        or rec.get("req")
        or rec.get("model")
        or ""
    ).strip()
    tokens_sig = f"{rec.get('in', rec.get('input_tokens', 0))}:{rec.get('out', rec.get('output_tokens', 0))}:{rec.get('cache_read', 0)}"
    raw_key = f"{host}|{kind}|{sid}|{turn_id}|{ts}|{skill}|{tool_or_path}|{tokens_sig}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


def _read_telemetry_shards(shards_dir: Path | None, days: int = 30) -> list[dict[str, Any]]:
    if shards_dir is None or not shards_dir.is_dir():
        return []
    start, end = _window_bounds(days)
    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []
    for shard_file in sorted(shards_dir.glob("*-skill-usage.jsonl")):
        if not shard_file.is_file():
            continue
        default_host = shard_file.name[: -len("-skill-usage.jsonl")]
        try:
            with shard_file.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    rec.setdefault("host", default_host)
                    stamp = next(
                        (_observed_at(rec[key]) for key in ("ts", "t", "timestamp", "created_at", "observed_at") if rec.get(key)),
                        None,
                    )
                    if not stamp or not (start <= stamp < end):
                        continue
                    fp = _event_fingerprint(rec)
                    if fp in seen_ids:
                        continue
                    seen_ids.add(fp)
                    enriched = dict(rec)
                    enriched["event_id"] = fp
                    enriched["_observed_at"] = stamp
                    merged.append(enriched)
        except OSError:
            continue
    return merged



def _percent_label(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "unknown"


def _load_skill_retro_module() -> Any:
    path = Path(__file__).resolve().parents[2] / "skill-retro" / "scripts" / "skill_retro.py"
    spec = importlib.util.spec_from_file_location("standalone_skill_retro_mod", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load skill_retro from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.datetime = datetime
    module._cst_today_str = _cst_today_str
    return module


def _discover_skill_inventory(skills_root: Path | None = None, dev_root: Path | None = None) -> dict[str, dict[str, Any]]:
    return _load_skill_retro_module()._discover_skill_inventory(skills_root, dev_root)


def mirror_skills_to_icloud(skill_details: Sequence[Mapping[str, Any]], *, icloud_sync_dir: Path | None = None) -> dict[str, Any]:
    return _load_skill_retro_module().mirror_skills_to_icloud(skill_details, icloud_sync_dir=icloud_sync_dir)


def compute_skill_roi_diagnostics(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _load_skill_retro_module().compute_skill_roi_diagnostics(*args, **kwargs)


def compute_skill_roi(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
    icloud_sync_dir: Path | None = None,
    mirror_icloud_roi: bool | None = None,
    icloud_roi_path: Path | None = None,
) -> dict[str, Any]:
    report = compute_skill_roi_diagnostics(
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        skills_root=skills_root,
        dev_root=dev_root,
        magpie_usage=magpie_usage,
        dsh_sessions=dsh_sessions,
        telemetry_shards_dir=telemetry_shards_dir,
        auto_propose=auto_propose,
        proposals_path=proposals_path,
        icloud_sync_dir=icloud_sync_dir,
        mirror_icloud_roi=mirror_icloud_roi,
        icloud_roi_path=icloud_roi_path,
    )
    report.setdefault(
        "generated_at",
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    return report


def compute_skill_retro(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _load_skill_retro_module().compute_skill_retro(*args, **kwargs)


def _usage_records(path: Path, *, telemetry_shards_dir: Path | None = None) -> list[dict[str, Any]]:
    start, end = _window_bounds()
    records: list[dict[str, Any]] = []
    seen_route_sigs: set[tuple[str, str, int, int]] = set()
    seen_fps: set[str] = set()
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid usage JSON at {path}:{number}") from exc
                if not isinstance(rec, dict):
                    raise ValueError(f"invalid usage record at {path}:{number}")
                stamp = next((_observed_at(rec[key]) for key in ("t", "ts", "timestamp", "created_at", "observed_at")
                              if rec.get(key)), None)
                if stamp and start <= stamp < end:
                    rec["_observed_at"] = stamp
                    fp = _event_fingerprint(rec)
                    seen_fps.add(fp)
                    route_sig = (
                        str(rec.get("route_id") or ""),
                        str(rec.get("req") or rec.get("model") or ""),
                        int(rec.get("in") or 0),
                        int(rec.get("out") or 0),
                    )
                    if route_sig[0]:
                        seen_route_sigs.add(route_sig)
                    records.append(rec)
    for s_rec in _read_telemetry_shards(telemetry_shards_dir, days=7):
        kind = str(s_rec.get("kind") or "")
        if kind and kind not in {"gateway_usage", "usage"}:
            continue
        fp = str(s_rec.get("event_id") or _event_fingerprint(s_rec))
        if fp in seen_fps:
            continue
        route_sig = (
            str(s_rec.get("route_id") or ""),
            str(s_rec.get("req") or s_rec.get("model") or ""),
            int(s_rec.get("in") or 0),
            int(s_rec.get("out") or 0),
        )
        if route_sig[0] and route_sig in seen_route_sigs:
            continue
        seen_fps.add(fp)
        if route_sig[0]:
            seen_route_sigs.add(route_sig)
        records.append(s_rec)
    return records


def compute_agent_efficiency(
    warehouse_db: Path | None = None,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    """Compute M1-M5 from timestamped observations in seven natural CST days."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    mp = magpie_usage or DEFAULT_MAGPIE_USAGE
    cp = copilot_db or DEFAULT_COPILOT_DB
    shards_dir = (
        telemetry_shards_dir
        if telemetry_shards_dir is not None
        else (
            DEFAULT_ICLOUD_TELEMETRY_SHARDS_DIR
            if warehouse_db is None and magpie_usage is None and copilot_db is None
            else None
        )
    )
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    review_rows, event_rows, edit_rows, ci_rows = [], [], [], []
    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            review_rows = [r for r in _timed_rows(conn, "fact_gtt_task_run")
                           if r.get("role_name") in {"reviewer", "code_reviewer", "planner_reviewer"}]
            event_rows = [r for r in _timed_rows(conn, "fact_gtt_event") if r.get("evt_type") == "TASK_BLOCKED"]
            ci_rows = _timed_rows(conn, "fact_ci_job_run")
    grouped_reviews: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in review_rows:
        grouped_reviews.setdefault((row["goal_id"], row["task_id"]), []).append(row)
    reviews = [{"goal_id": goal, "task_id": task,
                "max_round": max(int(r.get("round_no") or 1) for r in rows),
                "r1_fail": any(int(r.get("round_no") or 1) == 1 and r.get("v5_verdict") == "FAIL" for r in rows),
                "_observed_at": max(r["_observed_at"] for r in rows)}
               for (goal, task), rows in grouped_reviews.items()]
    failures = sum(r["r1_fail"] or r["max_round"] >= 2 for r in reviews)
    rate = round(failures / len(reviews), 4) if reviews else None
    m1 = {"reviewed_tasks": len(reviews), "r1_fail_tasks": failures, "r1_fail_rate": rate,
          "floor_max_r1_fail_rate": 0.15,
          "multi_round_review_tasks": [{k: r[k] for k in ("goal_id", "task_id", "max_round")} for r in reviews if r["max_round"] >= 2][:10],
          "round_3_plus_tasks": [{k: r[k] for k in ("goal_id", "task_id", "max_round")} for r in reviews if r["max_round"] >= 3],
          "breached": bool(rate is not None and (rate > 0.15 or any(r["max_round"] >= 3 for r in reviews))),
          **_freshness([r["_observed_at"] for r in reviews], wh.is_file())}

    if cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            edit_rows = [r for r in _timed_rows(conn, "session_files")
                         if r.get("tool_name") in {"edit", "apply_patch", "create"}]
    edits: dict[tuple[str, str], int] = {}
    edit_stamps: dict[tuple[str, str], list[datetime]] = {}
    for row in edit_rows:
        key = (str(row.get("session_id") or ""), str(row.get("file_path") or ""))
        edits[key] = edits.get(key, 0) + 1
        edit_stamps.setdefault(key, []).append(row["_observed_at"])
    churn = [{"session_id": sid[:8], "file_path": path, "edits": count, **_freshness(edit_stamps[(sid, path)])}
             for (sid, path), count in sorted(edits.items(), key=lambda pair: (-pair[1], pair[0])) if count >= 5]
    m2 = {"high_churn_files": churn[:8], "max_edits_single_file": max(edits.values(), default=0),
          "floor_max_edits_per_session": 5, "breached": bool(churn),
          **_freshness([r["_observed_at"] for r in edit_rows], cp.is_file())}

    shards_eff_present = bool(shards_dir and shards_dir.is_dir() and list(shards_dir.glob("*-skill-usage.jsonl")))
    records = _usage_records(mp, telemetry_shards_dir=shards_dir)
    ttfts, retries, gateway_4xx = [], 0, 0
    group_tokens: dict[str, dict[str, int]] = {}
    cache_stamps, token_stamps = [], []
    steering_tiny_outputs, steering_reqs = 0, 0
    for rec in records:
        status = int(rec.get("status") or 200)
        gateway_4xx += status in (400, 401, 403, 422, 429)
        retries += int(rec.get("tries") or 1) > 1 or int(rec.get("retried_ms") or 0) > 0
        ttft = int(rec.get("ttft_ms") or 0)
        if ttft > 0:
            ttfts.append(ttft)
        req_model = str(rec.get("req") or rec.get("model") or "other")
        group = req_model if req_model.startswith("group/") else "direct"
        in_t, out_t, cr_t = (int(rec.get(key) or 0) for key in ("in", "out", "cache_read"))
        if in_t + cr_t > 0:
            cache_stamps.append(rec["_observed_at"])
        if in_t + out_t + cr_t > 0:
            token_stamps.append(rec["_observed_at"])
            bucket = group_tokens.setdefault(group, {"in": 0, "out": 0, "cache_read": 0})
            bucket["in"] += in_t
            bucket["out"] += out_t
            bucket["cache_read"] += cr_t
            if "steering" in group:
                steering_reqs += 1
                steering_tiny_outputs += 0 < out_t < 80
    ttfts.sort()
    total_reqs = len(records)
    p90 = ttfts[min(len(ttfts) - 1, max(0, (len(ttfts) * 90 + 99) // 100 - 1))] if ttfts else None
    retry_rate = round(retries / total_reqs, 4) if total_reqs else None
    m3 = {"sampled_requests": total_reqs, "p90_ttft_ms": p90, "ttft_sample_size": len(ttfts),
          "floor_max_p90_ttft_ms": 3000, "retry_rate": retry_rate, "gateway_4xx_count": gateway_4xx,
          "blocked_events_7d": len(event_rows),
          "blocked_events_freshness": _freshness([r["_observed_at"] for r in event_rows], wh.is_file()),
          "breached": bool((total_reqs and ((p90 or 0) > 3000 or retry_rate > 0.05 or gateway_4xx)) or len(event_rows) > 3),
          **_freshness([r["_observed_at"] for r in records + event_rows], mp.is_file() or wh.is_file() or shards_eff_present)}
    total_in = sum(b["in"] for b in group_tokens.values())
    total_cr = sum(b["cache_read"] for b in group_tokens.values())
    overall_cache_hit = round(total_cr / (total_in + total_cr), 4) if total_in + total_cr else None
    m4 = {"overall_cache_hit_rate": overall_cache_hit, "floor_min_cache_hit_rate": 0.75,
          "by_group": {g: round(b["cache_read"] / (b["in"] + b["cache_read"]), 4) if b["in"] + b["cache_read"] else None
                       for g, b in group_tokens.items()},
          "breached": overall_cache_hit is not None and overall_cache_hit < 0.75,
          **_freshness(cache_stamps, mp.is_file() or shards_eff_present)}
    total_tokens = sum(b["in"] + b["out"] + b["cache_read"] for b in group_tokens.values())
    steering_tokens = sum(b["in"] + b["out"] + b["cache_read"] for g, b in group_tokens.items() if "steering" in g)
    share = round(steering_tokens / total_tokens, 4) if total_tokens else None
    first_pass_pr_green_rate = round(sum(row.get("failure_category") in {None, "", "pass"} for row in ci_rows) / len(ci_rows), 4) if ci_rows else None
    m5 = {"steering_token_share": share, "floor_max_steering_share": 0.35,
          "steering_tiny_output_ratio": round(steering_tiny_outputs / steering_reqs, 4) if steering_reqs else None,
          "first_pass_pr_green_rate": first_pass_pr_green_rate,
          "first_pass_pr_green_freshness": _freshness([r["_observed_at"] for r in ci_rows], wh.is_file()),
          "breached": bool((share is not None and share > 0.35) or (first_pass_pr_green_rate is not None and first_pass_pr_green_rate < 0.80)),
          **_freshness(token_stamps + [r["_observed_at"] for r in ci_rows], mp.is_file() or wh.is_file() or shards_eff_present)}
    observed = [r["_observed_at"] for r in reviews + edit_rows + records + event_rows + ci_rows]
    freshness = _freshness(observed, wh.is_file() or cp.is_file() or mp.is_file() or shards_eff_present)
    eff_as_of, eff_cov, eff_sample = (freshness[k] for k in ("source_as_of", "coverage_status", "sample_size"))

    metric_ids = ["agent-efficiency-m1-review-pingpong", "agent-efficiency-m2-retry-tax", "agent-efficiency-m3-automated-share", "agent-efficiency-m4-steer-autonomy", "agent-efficiency-m5-first-pass-pr-green"]
    for metric, metric_id in zip((m1, m2, m3, m4, m5), metric_ids):
        metric["metric_id"] = metric_id
        metric["status"] = "fail" if metric["breached"] else "pass" if metric["sample_size"] else "unknown"
    recommendations: list[dict[str, Any]] = []

    if m1["breached"]:
        multi_str = ", ".join(f"{t['goal_id']}/{t['task_id']}(r{t['max_round']})" for t in m1["round_3_plus_tasks"][:4])
        recommendations.append({
            "id": "improve-m1-review-pingpong",
            "ref": "proposal://improve-m1-review-pingpong",
            "title": f"缩减审核返工拉锯：R1 打回率 {m1['r1_fail_rate']*100:.1f}%（{len(m1['round_3_plus_tasks'])} 项达 r3+）",
            "detail": (
                f"多轮审核拉锯任务：{multi_str or '见 multi_round_review_tasks'}。"
                f"建议将 Reviewer 打回的高频项前移至 Writer 提交前的内环 L1/G2 脚本预检。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={m1['reviewed_tasks']}"
            ),
            "priority": "high",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": m1["reviewed_tasks"],
            "coverage_status": eff_cov,
        })
    if m2["status"] == "fail":
        top_f = m2["high_churn_files"][0]
        recommendations.append({
            "id": "improve-m2-edit-churn",
            "ref": "proposal://improve-m2-edit-churn",
            "title": f"降低文件反复擦写：单会话最高连改 {top_f['edits']} 次 ({Path(top_f['file_path']).name})",
            "detail": (
                f"文件 {top_f['file_path']} 在单次会话中被反复 edit {top_f['edits']} 次，建议补充局部契约或拆分深模块。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={len(m2['high_churn_files'])}"
            ),
            "priority": "medium",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": len(m2["high_churn_files"]),
            "coverage_status": eff_cov,
        })
    if m3["breached"]:
        recommendations.append({
            "id": "improve-m3-gateway-flow",
            "ref": "proposal://improve-m3-gateway-flow",
            "title": f"治理网关阻塞与首字延迟：P90 TTFT {m3['p90_ttft_ms']}ms · 4xx 错误 {m3['gateway_4xx_count']} 次",
            "detail": (
                f"采样 {m3['sampled_requests']} 次请求中 P90 TTFT={m3['p90_ttft_ms']}ms (阈值 3000ms)，"
                f"重试率={_percent_label(m3['retry_rate'])}，4xx={m3['gateway_4xx_count']} 次，近 7d TASK_BLOCKED={m3['blocked_events_7d']} 次。"
                f"建议收紧路由超时回退与配额预检锁。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={m3['sampled_requests']}"
            ),
            "priority": "high",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": m3["sampled_requests"],
            "coverage_status": eff_cov,
        })
    if m4["breached"]:
        recommendations.append({
            "id": "improve-m4-cache-efficiency",
            "ref": "proposal://improve-m4-cache-efficiency",
            "title": f"提升 KV Cache 命中率：当前 {m4['overall_cache_hit_rate']*100:.1f}% 低于 75% 基准线",
            "detail": (
                f"整体缓存命中率 {m4['overall_cache_hit_rate']*100:.1f}% 偏低，建议固定 System/AGENTS.md 前缀顺序并减少跨轮动态注入抖动。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={total_reqs}"
            ),
            "priority": "medium",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": total_reqs,
            "coverage_status": eff_cov,
        })
    if m5["breached"]:
        recommendations.append({
            "id": "improve-m5-steering-token-share",
            "ref": "proposal://improve-m5-steering-token-share",
            "title": f"收敛 Steering 主控 Token 占比：当前 {_percent_label(m5['steering_token_share'])} 超出 35% 预算",
            "detail": (
                f"Steering 消耗占比达 {_percent_label(m5['steering_token_share'])}（短输出占比 {_percent_label(m5['steering_tiny_output_ratio'])}），"
                f"建议将 >3 文件实现与大日志探查下沉至 Writer / Layer-0 脚本。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={total_reqs}"
            ),
            "priority": "medium",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": total_reqs,
            "coverage_status": eff_cov,
        })

    new_ids = ["reduce-m1-ping-pong", "reduce-m2-retry-tax", "improve-m3-automated-share", "improve-m4-steer-autonomy", "improve-m5-first-pass-pr-green"]
    for proposal in recommendations:
        index = int(proposal["id"].split("-")[1][1:]) - 1
        proposal["legacy_ids"] = [proposal["id"]]
        proposal["legacy_refs"] = [proposal["ref"]]
        proposal["id"] = new_ids[index]
        proposal["ref"] = f"retro://agent-efficiency-m{index + 1}"
        proposal["metric_id"] = metric_ids[index]
        proposal["priority"] = "P1"
        proposal["scope"] = "personal"
        proposal["action"] = "Move repeated findings into deterministic pre-review checks and preserve autonomous session routing."
        metric = {"m1": m1, "m2": m2, "m3": m3, "m4": m4, "m5": m5}[proposal["id"].split("-")[1]]
        proposal.update({key: metric[key] for key in freshness})
        proposal["detail"] = proposal["detail"].split(" | window_cst=")[0] + (
            f" | window_cst=7d | source_as_of={metric['source_as_of']} | sample_size={metric['sample_size']}")

    auto_proposed = _queue_recommendations(recommendations, prop_path) if auto_propose else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(recommendations) - proposals_written if auto_propose else 0
    return {
        "ok": True,
        **freshness,
        "metrics": {
            "M1_review_ping_pong": m1,
            "M2_edit_churn": m2,
            "M3_flow_and_blockers": m3,
            "M4_cache_efficiency": m4,
            "M5_steering_vs_coding_split": m5,
        },
        "recommendations": recommendations,
        "auto_proposed": auto_proposed,
        "proposals_written": proposals_written,
        "proposals_skipped": proposals_skipped,
    }


@contextlib.contextmanager
def _proposal_lock(path: Path):
    sidecar = path.with_name(path.name + ".lock")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _proposal_history(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal history JSON at {path}:{number}") from exc
            if not isinstance(rec, dict) or any(not str(rec.get(k) or "").strip() for k in ("id", "ts", "agent", "title")):
                raise ValueError(f"incomplete proposal history record at {path}:{number}")
            if _observed_at(rec["ts"]) is None:
                raise ValueError(f"invalid proposal timestamp at {path}:{number}")
            records[str(rec["id"])] = rec
    return records


def _queue_recommendations(candidates: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    queued = []
    for candidate in candidates:
        result = route_retro_candidate(candidate, proposals_path=path, deduplicate_pending=True)
        if result["decision"] != "already_pending":
            queued.append(result)
    return queued


def route_retro_candidate(
    candidate: dict[str, Any],
    mode: str = "observe",
    starred: bool = False,
    proposals_path: Path | None = None,
    linear_runner: Callable[..., Any] | None = None,
    deduplicate_pending: bool = False,
) -> dict[str, Any]:
    cid = str(candidate.get("id") or "").strip()
    if not cid:
        raise ValueError("candidate id is required")
    title = str(candidate.get("title") or cid).strip()
    detail = str(candidate.get("detail") or title).strip()
    ref = str(candidate.get("ref") or f"retro://{cid}").strip()
    scope = str(candidate.get("scope") or "personal").strip() or "personal"
    norm_prio = _normalize_priority(candidate.get("priority"), default="high")
    proposal = {
        "id": cid,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "agent": str(candidate.get("agent") or "retro-loop").strip() or "retro-loop",
        "title": title,
        "detail": detail,
        "priority": norm_prio,
        "status": "pending",
        "ref": ref,
        "scope": scope,
    }
    for extra_key in (
        "layer",
        "category",
        "skill",
        "plain_reason",
        "scope_triage",
        "metric_id",
        "action",
        "legacy_ids",
        "legacy_refs",
        "legacy_ref",
        "diagnostic_status",
        "post_invocation_repair_loop_rate",
        "app_name",
        "event_kind",
        "signature",
        "stack_top_symbol",
        "recommended_layer",
        "verify_cmd",
        "window_cst",
        "window_start",
        "window_end",
        "estimated",
        "estimation_method",
        "source_as_of",
        "sample_size",
        "coverage_status",
        "scope",
    ):
        if extra_key in candidate:
            proposal[extra_key] = candidate[extra_key]
    proposal.setdefault("scope", "personal")
    if any(not str(proposal[key] or "").strip() for key in ("id", "ts", "agent", "title")):
        raise ValueError("proposal requires nonempty id/ts/agent/title")
    if mode == "observe" and not starred:
        if proposals_path is None:
            raise ValueError("proposals_path is required for observe mode")
        with _proposal_lock(proposals_path):
            history = _proposal_history(proposals_path)
            ids = {proposal["id"], *candidate.get("legacy_ids", [])}
            refs = {proposal["ref"], *candidate.get("legacy_refs", []), candidate.get("legacy_ref", "")}
            refs.discard("")
            prior = next((row for row in history.values() if (row["id"] in ids or row.get("ref") in refs or ids.intersection(row.get("legacy_ids", [])) or refs.intersection(row.get("legacy_refs", []))) and row.get("status") in {"pending", "approved", "dismissed", "transferred"}), None)
            if deduplicate_pending and prior:
                return {"decision": "already_pending", "promoted": False, "proposal": prior}
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(proposal, ensure_ascii=False) + "\n")
        return {"decision": "observe (queued_to_aidash)", "promoted": False, "proposal": proposal}
    if linear_runner is None:
        raise ValueError("linear_runner is required for promotion")
    linear_runner(proposal)
    proposal["status"] = "approved"
    return {"decision": "promoted_to_linear", "promoted": True, "proposal": proposal}


route_retro_Candidate = route_retro_candidate


def main(argv: list[str] | None = None) -> int:
    import sys
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "discover":
        p = argparse.ArgumentParser(prog="retro_story_engine.py discover")
        p.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
        p.add_argument("--queries-dir", type=Path, default=DEFAULT_RETRO_QUERIES_DIR)
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        p.add_argument("--no-auto-propose", action="store_true")
        ns = p.parse_args(args_list[1:])
        print(
            json.dumps(
                discover_aidata_signals(
                    ns.warehouse_db,
                    ns.queries_dir,
                    proposals_path=ns.proposals_path,
                    auto_propose=not ns.no_auto_propose,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args_list and args_list[0] == "agent-efficiency":
        p = argparse.ArgumentParser(prog="retro_story_engine.py agent-efficiency")
        p.add_argument("--warehouse-db", type=Path, default=None)
        p.add_argument("--magpie-usage", type=Path, default=None)
        p.add_argument("--copilot-db", type=Path, default=None)
        p.add_argument("--telemetry-shards-dir", type=Path, default=None)
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        report = compute_agent_efficiency(
            ns.warehouse_db,
            ns.magpie_usage,
            ns.copilot_db,
            telemetry_shards_dir=ns.telemetry_shards_dir,
        )
        if ns.auto_propose:
            queued = _queue_recommendations(report.get("recommendations", []), ns.proposals_path)
            report.update(auto_proposed=queued, proposals_written=len(queued), proposals_skipped=len(report.get("recommendations", [])) - len(queued))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args_list and args_list[0] == "skill-roi":
        p = argparse.ArgumentParser(prog="retro_story_engine.py skill-roi")
        p.add_argument("--warehouse-db", type=Path, default=None)
        p.add_argument("--copilot-db", type=Path, default=None)
        p.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
        p.add_argument("--dev-root", type=Path, default=DEFAULT_DEV_ROOT)
        p.add_argument("--magpie-usage", type=Path, default=None)
        p.add_argument("--dsh-sessions", type=Path, default=DEFAULT_DSH_SESSIONS)
        p.add_argument("--icloud-sync-dir", type=Path, default=None)
        p.add_argument("--telemetry-shards-dir", type=Path, default=None)
        p.add_argument("--icloud-roi-path", type=Path, default=None)
        p.add_argument("--no-icloud-mirror", action="store_true")
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        report = compute_skill_roi(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            icloud_sync_dir=ns.icloud_sync_dir,
            mirror_icloud_roi=False if ns.no_icloud_mirror else None,
            icloud_roi_path=ns.icloud_roi_path,
        )
        if ns.auto_propose:
            queued = _queue_recommendations(report.get("low_roi_proposals", []), ns.proposals_path)
            report.update(auto_proposed=queued, proposals_written=len(queued), proposals_skipped=len(report.get("low_roi_proposals", [])) - len(queued))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args_list and args_list[0] in {"skill-retro", "probe", "triage", "diagnose", "emit-briefs", "emit-relay"}:
        return _load_skill_retro_module().main(args_list)
    if args_list and args_list[0] == "propose":
        p = argparse.ArgumentParser(prog="retro_story_engine.py propose")
        p.add_argument("--id", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--detail", default="")
        p.add_argument("--priority", default="high", choices=("high", "medium", "low", "P0", "P1", "P2", "P3"))
        p.add_argument("--ref", default="")
        p.add_argument("--scope", default="personal")
        p.add_argument("--mode", default="observe", choices=("observe", "auto"))
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        res = route_retro_candidate(
            {
                "id": ns.id,
                "title": ns.title,
                "detail": ns.detail,
                "priority": ns.priority,
                "ref": ns.ref,
                "scope": ns.scope,
            },
            mode=ns.mode,
            proposals_path=ns.proposals_path,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("state_path", type=Path)
    parser.add_argument("target_state", choices=STATES)
    parser.add_argument("expected_version", type=int)
    args = parser.parse_args(args_list)
    try:
        print(json.dumps(transition_state(args.state_path, args.target_state, args.expected_version)))
        return 0
    except OptimisticLockError as exc:
        print(json.dumps({"code": exc.code, "error": str(exc)}))
        raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
