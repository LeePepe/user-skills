#!/usr/bin/env python3
"""Deterministic retro/story helpers for evidence validation and promotion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import fcntl
from datetime import date, datetime, timedelta, timezone
import hashlib
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


_EVAL_CASE_ID_RE = re.compile(r"^(?:EV|DC|AT|CASE)-\d+[A-Za-z0-9_-]*$")
_USER_HOME_PREFIX = "/" + "Users/"
_LINUX_HOME_PREFIX = "/" + "home/"
_USER_HOME_PATH_RE = re.compile(
    r"(?:" + _USER_HOME_PREFIX + r"|" + _LINUX_HOME_PREFIX + r")[^/\s\"'<>`|)]+"
)
_DOC_PREVIEW_REF_SUFFIXES = {".md", ".json", ".yaml", ".yml"}
_SKILL_MD_PREVIEW_MAX_CHARS = 32768
_REF_DOC_PREVIEW_MAX_CHARS = 16384
_SAFE_SKILL_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VALID_SKILL_SCRIPT_SUFFIXES = {".py", ".sh", ".ts"}
_GATE_SCRIPT_BASENAMES = {
    "skill_contract.py",
    "feedback_guard.py",
    "design_contract.py",
    "verify_design_cases.py",
    "audit_arch_testing.py",
    "audit_concurrency_swiftui.py",
    "audit_debug_perf_release.py",
    "verify_issue_format.py",
    "howto_guard.py",
    "spec_gate.py",
    "contract_check.sh",
}


def _is_valid_skill_script_rel_path(rel_path: str) -> bool:
    """Return True iff rel_path is a canonical executable skill script under scripts/."""
    cleaned = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned.startswith("scripts/"):
        return False
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) < 2 or ".." in parts:
        return False
    if "__pycache__" in parts:
        return False
    for seg in parts[1:-1]:
        if seg.startswith(".") or seg.lower() in {"data", "state", "fixtures", "tests", "__pycache__"}:
            return False
    filename = parts[-1]
    if (
        not filename
        or filename == "__init__.py"
        or filename.startswith(".")
        or filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".pyc")
    ):
        return False
    return Path(filename).suffix.lower() in _VALID_SKILL_SCRIPT_SUFFIXES


def _extract_eval_case_title(
    item: Mapping[str, Any],
    *,
    cid: str,
    assertion_summary: str,
    skills_root: Path,
    dev_root: Path,
) -> str:
    """Extract a human-readable title for an EV-*/DC-* case, never degrading to bare cid."""
    for key in ("title", "name", "description", "summary", "rule", "fix_rule", "symptom", "prompt"):
        raw = str(item.get(key) or "").strip()
        if raw and raw != cid:
            redacted = " ".join(_redact_user_text(raw, skills_root=skills_root, dev_root=dev_root).split())
            if redacted and redacted != cid:
                return redacted[:180]
    fallback = assertion_summary if assertion_summary and assertion_summary != cid else "Contract gate verification check"
    return f"{cid} — {fallback}"[:180]


def _redact_home_paths(text: str, *, skills_root: Path | None = None, dev_root: Path | None = None) -> str:
    """Redact absolute macOS and Linux user home directories into tilde paths."""
    return _redact_user_text(text, skills_root=skills_root, dev_root=dev_root)


def _redact_user_text(text: str, *, skills_root: Path | None = None, dev_root: Path | None = None) -> str:
    """Redact absolute home/temp roots into tilde-relative or symbolic paths."""
    cleaned = str(text or "")
    home_str = str(Path.home())
    if home_str:
        cleaned = cleaned.replace(home_str, "~")
    for candidate_root, replacement in (
        (skills_root, "~/Development/user-skills/skills"),
        (dev_root, "~/Development"),
    ):
        if candidate_root is None:
            continue
        for variant in {str(candidate_root), str(candidate_root.resolve())}:
            if variant and variant != "/":
                cleaned = cleaned.replace(variant, replacement)
    cleaned = _USER_HOME_PATH_RE.sub("~", cleaned)
    return cleaned


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to path without leaving partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _resolve_icloud_sync_dir(icloud_sync_dir: Path | None = None) -> Path | None:
    """Resolve AIDashSync target dir while keeping unittest/pytest runs hermetic."""
    if icloud_sync_dir is not None:
        return Path(icloud_sync_dir).expanduser()
    env_override = str(os.environ.get("AIDASH_ICLOUD_SYNC_DIR") or "").strip()
    if env_override:
        return Path(env_override).expanduser()
    in_test_harness = bool(os.environ.get("PYTEST_CURRENT_TEST")) or ("unittest" in sys.modules)
    if in_test_harness:
        return None
    cloud_docs = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if not cloud_docs.is_dir():
        return None
    return cloud_docs / "AIDashSync"


def mirror_skills_to_icloud(
    skill_details: Sequence[Mapping[str, Any]],
    *,
    icloud_sync_dir: Path | None = None,
) -> dict[str, Any]:
    """Mirror redacted SKILL.md and text references/* into <sync_dir>/skills-mirror/<slug>/."""
    target_sync_dir = _resolve_icloud_sync_dir(icloud_sync_dir)
    if target_sync_dir is None:
        return {"mirrored": False, "reason": "icloud_sync_disabled_or_unavailable", "skills_written": 0, "references_written": 0}
    if icloud_sync_dir is None and not os.environ.get("AIDASH_ICLOUD_SYNC_DIR"):
        cloud_docs_parent = target_sync_dir.parent
        if not cloud_docs_parent.is_dir():
            return {"mirrored": False, "reason": "cloud_docs_missing", "skills_written": 0, "references_written": 0}

    mirror_root = target_sync_dir / "skills-mirror"
    skills_written = 0
    refs_written = 0
    try:
        mirror_root.mkdir(parents=True, exist_ok=True)
        mirror_root_resolved = mirror_root.resolve()
        for item in skill_details:
            if not isinstance(item, Mapping):
                continue
            slug = str(item.get("skill") or "").strip()
            if not slug or not _SAFE_SKILL_SLUG_RE.match(slug) or slug in {".", ".."}:
                continue
            skill_preview = item.get("doc_preview")
            if not isinstance(skill_preview, str) or not skill_preview:
                continue
            skill_bundle_dir = (mirror_root_resolved / slug).resolve()
            if skill_bundle_dir.parent != mirror_root_resolved:
                continue
            _atomic_write_text(skill_bundle_dir / "SKILL.md", skill_preview)
            skills_written += 1

            refs = item.get("references")
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
                continue
            for ref in refs:
                if not isinstance(ref, Mapping):
                    continue
                ref_preview = ref.get("doc_preview")
                rel_path = str(ref.get("rel_path") or "").strip()
                if not isinstance(ref_preview, str) or not ref_preview or not rel_path:
                    continue
                rel_parts = Path(rel_path).parts
                if not rel_parts or rel_parts[0] != "references" or ".." in rel_parts:
                    continue
                dest = (skill_bundle_dir / rel_path).resolve()
                try:
                    dest.relative_to(skill_bundle_dir)
                except ValueError:
                    continue
                _atomic_write_text(dest, ref_preview)
                refs_written += 1
    except OSError as exc:
        return {
            "mirrored": False,
            "reason": f"os_error:{type(exc).__name__}",
            "sync_dir": _redact_home_paths(str(target_sync_dir)),
            "skills_written": skills_written,
            "references_written": refs_written,
        }
    return {
        "mirrored": True,
        "sync_dir": _redact_home_paths(str(target_sync_dir)),
        "skills_written": skills_written,
        "references_written": refs_written,
    }


def _format_skill_dir(pkg: Path, *, repo_bound: bool, repo_name: str, skills_root: Path, dev_root: Path) -> str:
    """Return a privacy-safe tilde path for a discovered skill directory."""
    if not repo_bound:
        return f"~/Development/user-skills/skills/{pkg.name}"
    try:
        rel = pkg.relative_to(dev_root)
        return f"~/Development/{rel.as_posix()}"
    except ValueError:
        pass
    return _redact_user_text(str(pkg), skills_root=skills_root, dev_root=dev_root)


def _estimate_frontmatter_and_body_tokens(skill_md: Path) -> tuple[int, int, int]:
    """Return (line_count, frontmatter_tokens_est, body_tokens_est) for SKILL.md."""
    try:
        raw_bytes = skill_md.read_bytes()
        text = raw_bytes.decode("utf-8", errors="ignore")
    except OSError:
        return 0, 1, 1
    lines = text.splitlines()
    body_tokens = max(1, len(raw_bytes) // 4)
    fm_text = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_lines = [
                ln.strip()
                for ln in parts[1].splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            fm_text = "\n".join(fm_lines) if fm_lines else parts[1].strip()
    frontmatter_tokens = max(1, len(fm_text.encode("utf-8")) // 4)
    return len(lines), frontmatter_tokens, body_tokens


def _summarize_eval_case(item: dict[str, Any], *, skills_root: Path, dev_root: Path) -> tuple[str, str]:
    """Classify an EV-*/DC-* case into ('regex'|'cli'|'contract', <=140 char summary)."""
    cmd = str(item.get("cmd") or "").strip()
    forbidden_regex = str(item.get("forbidden_regex") or "").strip()
    bad_snippet = str(item.get("bad_snippet") or "").strip()
    must_contain = item.get("must_contain")
    expect_stdout = item.get("expect_stdout_contains")
    fix_rule = str(item.get("fix_rule") or "").strip()

    if cmd:
        kind = "cli"
        summary_raw = cmd
        if isinstance(expect_stdout, list) and expect_stdout:
            summary_raw = f"{cmd} -> contains {expect_stdout[0]}"
    elif forbidden_regex or bad_snippet:
        kind = "regex"
        summary_raw = forbidden_regex or f"forbid: {bad_snippet}"
    else:
        kind = "contract"
        target_file = str(item.get("file") or "").strip()
        if isinstance(must_contain, list) and must_contain:
            joined = ", ".join(str(x) for x in must_contain[:3])
            summary_raw = f"{target_file}: must_contain [{joined}]" if target_file else f"must_contain [{joined}]"
        else:
            summary_raw = fix_rule or str(item.get("symptom") or item.get("title") or "contract check")

    one_line = " ".join(
        _redact_user_text(summary_raw, skills_root=skills_root, dev_root=dev_root).split()
    )
    if len(one_line) > 140:
        one_line = one_line[:137].rstrip() + "..."
    return kind, one_line or "contract assertion"


def _discover_skill_inventory(
    skills_root: Path | None = None,
    dev_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Scan global user-skills and repo-bound skills for scripts, L0/L1 lines, and EV/DC cases."""
    root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    search_dirs: list[tuple[Path, bool, str]] = [(root, False, "user-skills")]
    repo_names = ["AIDash", "Financial", "VitalStride", "VoxPocket"]
    if d_root.is_dir():
        for child in sorted(d_root.iterdir()):
            if not child.is_dir() or child.name in {"user-skills", "worktrees", "handoff"} or child.name.startswith("."):
                continue
            if child.name not in repo_names and ((child / ".agents" / "skills").is_dir() or (child / ".claude" / "skills").is_dir()):
                repo_names.append(child.name)
    for repo_name in repo_names:
        repo_path = d_root / repo_name
        candidates = [repo_path / ".agents" / "skills", repo_path / ".claude" / "skills"]
        if repo_name in {"AIDash", "Financial", "VitalStride", "VoxPocket"}:
            candidates.append(repo_path / "skills")
        for rel in candidates:
            if rel.is_dir():
                search_dirs.append((rel, True, repo_name))

    inventory: dict[str, dict[str, Any]] = {}
    for base_dir, repo_bound, repo_name in search_dirs:
        if not base_dir.is_dir():
            continue
        for pkg in sorted(base_dir.iterdir()):
            skill_md = pkg / "SKILL.md"
            if not pkg.is_dir() or not skill_md.is_file():
                continue
            skill_name = pkg.name
            # Skip third-party SpecKit command templates (.agents/skills/speckit-*) that are not user-skills contracts
            if repo_bound and skill_name.startswith("speckit-"):
                continue
            if skill_name in inventory:
                continue
            l0_lines, frontmatter_tokens_est, body_tokens_est = _estimate_frontmatter_and_body_tokens(skill_md)
            try:
                skill_md_raw_bytes = skill_md.read_bytes()
                skill_md_raw_text = skill_md_raw_bytes.decode("utf-8", errors="ignore")
            except OSError:
                skill_md_raw_bytes = b""
                skill_md_raw_text = ""
            skill_md_bytes_len = len(skill_md_raw_bytes)
            skill_doc_preview = _redact_home_paths(
                skill_md_raw_text,
                skills_root=root,
                dev_root=d_root,
            )[:_SKILL_MD_PREVIEW_MAX_CHARS]
            l0_cap = 80 if skill_name in {"my-designer", "design-studio"} else 180
            refs_dir = pkg / "references"
            ref_files: list[str] = []
            ref_entries: list[dict[str, Any]] = []
            total_refs_bytes = 0
            max_l1_lines = 0
            eval_cases = 0
            recent_eval_cases = 0
            parsed_eval_cases: list[dict[str, Any]] = []
            seen_case_ids: set[str] = set()
            cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
            if refs_dir.is_dir():
                for ref_p in sorted(refs_dir.rglob("*")):
                    if not ref_p.is_file() or ref_p.name.startswith(".") or "__pycache__" in ref_p.parts:
                        continue
                    rel_ref = ref_p.relative_to(refs_dir).as_posix()
                    try:
                        ref_bytes = ref_p.read_bytes()
                        ref_text = ref_bytes.decode("utf-8", errors="ignore")
                        ref_line_count = len(ref_text.splitlines())
                    except OSError:
                        ref_bytes = b""
                        ref_text = ""
                        ref_line_count = 0
                    total_refs_bytes += len(ref_bytes)
                    ref_entry: dict[str, Any] = {
                        "rel_path": f"references/{rel_ref}",
                        "line_count": ref_line_count,
                        "tokens_est": max(1, len(ref_bytes) // 4),
                    }
                    if ref_p.suffix.lower() in _DOC_PREVIEW_REF_SUFFIXES and ref_text:
                        ref_entry["doc_preview"] = _redact_home_paths(
                            ref_text,
                            skills_root=root,
                            dev_root=d_root,
                        )[:_REF_DOC_PREVIEW_MAX_CHARS]
                    ref_entries.append(ref_entry)
                    if ref_p.suffix == ".md":
                        ref_files.append(rel_ref)
                        max_l1_lines = max(max_l1_lines, ref_line_count)
                    elif ref_p.suffix == ".json" and (
                        rel_ref.endswith("eval-cases.json") or rel_ref.endswith("defect-cases/index.json")
                    ):
                        try:
                            doc = json.loads(ref_bytes.decode("utf-8", errors="ignore"))
                            for status_key, case_status in (("active_cases", "active"), ("retired_cases", "retired")):
                                for item in doc.get(status_key, []):
                                    if not isinstance(item, dict):
                                        continue
                                    cid = str(item.get("id") or "").strip() or f"CASE-{len(parsed_eval_cases) + 1:03d}"
                                    if case_status == "active":
                                        eval_cases += 1
                                        reg = str(item.get("registered_at") or doc.get("updated") or "")
                                        if reg >= cutoff_7d:
                                            recent_eval_cases += 1
                                    if cid in seen_case_ids:
                                        continue
                                    seen_case_ids.add(cid)
                                    kind, assertion_summary = _summarize_eval_case(item, skills_root=root, dev_root=d_root)
                                    parsed_eval_cases.append({
                                        "id": cid,
                                        "title": _extract_eval_case_title(
                                            item,
                                            cid=cid,
                                            assertion_summary=assertion_summary,
                                            skills_root=root,
                                            dev_root=d_root,
                                        ),
                                        "kind": kind,
                                        "assertion_summary": assertion_summary,
                                        "status": str(item.get("status") or case_status),
                                    })
                        except (OSError, json.JSONDecodeError):
                            pass
            data_drawer = pkg / "scripts" / "data" / "defect-cases" / "index.json"
            if data_drawer.is_file():
                try:
                    data_bytes = data_drawer.read_bytes()
                    ref_entries.append({"rel_path": "scripts/data/defect-cases/index.json", "line_count": len(data_bytes.decode("utf-8", errors="ignore").splitlines()), "tokens_est": max(1, len(data_bytes) // 4)})
                    data_doc = json.loads(data_bytes.decode("utf-8", errors="ignore"))
                    for item in data_doc.get("active_cases", []):
                        if isinstance(item, dict):
                            eval_cases += 1
                            reg = str(item.get("registered_at") or data_doc.get("updated") or "")
                            if reg >= cutoff_7d:
                                recent_eval_cases += 1
                except (OSError, json.JSONDecodeError):
                    pass
            scripts_dir = pkg / "scripts"
            scripts: list[str] = []
            script_entries: list[str] = []
            script_realpaths: dict[str, str] = {}
            total_scripts_bytes = 0
            if scripts_dir.is_dir():
                for sp in sorted(scripts_dir.rglob("*")):
                    if not (sp.is_file() or sp.is_symlink()):
                        continue
                    rel_sc = f"scripts/{sp.relative_to(scripts_dir).as_posix()}"
                    if not _is_valid_skill_script_rel_path(rel_sc):
                        continue
                    try:
                        sc_bytes_len = len(sp.read_bytes())
                    except OSError:
                        continue
                    total_scripts_bytes += sc_bytes_len
                    if sp.parent == scripts_dir:
                        scripts.append(sp.name)
                    script_entries.append(rel_sc)
                    with contextlib.suppress(OSError):
                        script_realpaths[rel_sc] = str(sp.resolve())
            refs_tokens_est = sum(int(r.get("tokens_est") or 0) for r in ref_entries)
            bundle_kb = round((skill_md_bytes_len + total_refs_bytes + total_scripts_bytes) / 1024.0, 1)
            refs_kb = round(total_refs_bytes / 1024.0, 1)
            budget_ok = l0_lines <= l0_cap and max_l1_lines <= 160
            inventory[skill_name] = {
                "skill": skill_name,
                "path": str(pkg),
                "skill_dir": _format_skill_dir(pkg, repo_bound=repo_bound, repo_name=repo_name, skills_root=root, dev_root=d_root),
                "scope": "repo_bound" if repo_bound else "global",
                "repo": repo_name if repo_bound else "user-skills",
                "repo_bound": repo_bound,
                "l0_lines": l0_lines,
                "frontmatter_tokens_est": frontmatter_tokens_est,
                "body_tokens_est": body_tokens_est,
                "bundle_kb": bundle_kb,
                "refs_kb": refs_kb,
                "refs_tokens_est": refs_tokens_est,
                "l0_cap": l0_cap,
                "max_l1_lines": max_l1_lines,
                "l1_cap": 160,
                "budget_ok": budget_ok,
                "doc_preview": skill_doc_preview,
                "reference_files": ref_files,
                "reference_entries": ref_entries,
                "scripts": scripts,
                "script_entries": script_entries,
                "script_realpaths": script_realpaths,
                "eval_cases": parsed_eval_cases,
                "eval_case_count": eval_cases,
                "eval_case_weekly_delta": recent_eval_cases,
            }
    return inventory


def _git_commits_behind(repo_path: Path, old_sha: str, new_sha: str) -> tuple[int, int]:
    """Return (commits_behind, days_behind) between old_sha and new_sha in repo_path."""
    if not repo_path.is_dir() or not old_sha or not new_sha or new_sha.startswith(old_sha) or old_sha.startswith(new_sha):
        return 0, 0
    try:
        cnt_proc = subprocess.run(
            ["git", "--no-optional-locks", "rev-list", "--count", f"{old_sha}..{new_sha}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        commits = int(cnt_proc.stdout.strip()) if cnt_proc.returncode == 0 and cnt_proc.stdout.strip().isdigit() else 1
        ts_old = subprocess.run(
            ["git", "--no-optional-locks", "show", "-s", "--format=%ct", old_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ts_new = subprocess.run(
            ["git", "--no-optional-locks", "show", "-s", "--format=%ct", new_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if ts_old.returncode == 0 and ts_new.returncode == 0:
            sec_diff = max(0, int(ts_new.stdout.strip() or 0) - int(ts_old.stdout.strip() or 0))
            return commits, sec_diff // 86400
        return commits, 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 1, 0


def _compute_s1_pin_drift(dev_root: Path) -> dict[str, Any]:
    """S1: Shared-Repos downstream pin drift across shared-ci, shared-design-system, shared-telemetry."""
    upstreams: dict[str, tuple[Path, str]] = {}
    for up_name in ("shared-ci", "shared-design-system", "shared-telemetry"):
        up_path = dev_root / up_name
        sha = ""
        if up_path.is_dir():
            try:
                proc = subprocess.run(
                    ["git", "--no-optional-locks", "rev-parse", "HEAD"],
                    cwd=up_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0:
                    sha = proc.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                sha = ""
        if sha:
            upstreams[up_name] = (up_path, sha)

    consumers: list[dict[str, Any]] = []
    for repo_name in ("AIDash", "VitalStride", "VoxPocket"):
        r_path = dev_root / repo_name
        if not r_path.is_dir():
            continue
        pins: dict[str, str] = {}
        pin_sources: dict[str, Path] = {}
        shared_ci_file = r_path / ".shared-ci"
        if shared_ci_file.is_file():
            try:
                txt = shared_ci_file.read_text(encoding="utf-8", errors="ignore").strip()
                m = re.search(r"\b([0-9a-f]{7,40})\b", txt)
                if m:
                    pins["shared-ci"] = m.group(1)
                    pin_sources["shared-ci"] = shared_ci_file
            except OSError:
                pass
        wf_dir = r_path / ".github" / "workflows"
        if "shared-ci" not in pins and wf_dir.is_dir():
            for wf in sorted(wf_dir.glob("*.yml")):
                try:
                    wtxt = wf.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                m = re.search(r"LeePepe/shared-ci/[^@\s]+@([0-9a-f]{7,40})", wtxt)
                if m:
                    pins["shared-ci"] = m.group(1)
                    pin_sources["shared-ci"] = wf
                    break
        for pr_rel in (
            r_path / f"{repo_name}.xcodeproj" / "project.xcworkspace" / "xcshareddata" / "swiftpm" / "Package.resolved",
            r_path / repo_name / f"{repo_name}.xcodeproj" / "project.xcworkspace" / "xcshareddata" / "swiftpm" / "Package.resolved",
            r_path / "Package.resolved",
        ):
            if not pr_rel.is_file():
                continue
            try:
                pdoc = json.loads(pr_rel.read_text(encoding="utf-8"))
                for pin_entry in pdoc.get("pins", []):
                    ident = str(pin_entry.get("identity") or "").lower()
                    rev = str((pin_entry.get("state") or {}).get("revision") or "")
                    if ident in {"shared-design-system", "shared-telemetry"} and rev:
                        pins[ident] = rev
                        pin_sources[ident] = pr_rel
            except (OSError, json.JSONDecodeError):
                continue

        drift_items: list[dict[str, Any]] = []
        for dep, (up_path, head_sha) in upstreams.items():
            pinned = pins.get(dep, "")
            if not pinned:
                continue
            commits_behind, days_behind = _git_commits_behind(up_path, pinned, head_sha)
            is_outdated = not head_sha.startswith(pinned) and not pinned.startswith(head_sha) and commits_behind > 0
            drift_items.append({
                **_snapshot_freshness([pin_sources[dep]], 1),
                "dependency": dep,
                "pinned_sha": pinned[:12],
                "latest_sha": head_sha[:12],
                "commits_behind": commits_behind if is_outdated else 0,
                "days_behind": days_behind if is_outdated else 0,
                "status": "OUTDATED_PIN" if is_outdated else "UP_TO_DATE",
            })
        consumers.append({
            **_snapshot_freshness([pin_sources[d["dependency"]] for d in drift_items], len(drift_items)),
            "repo": repo_name,
            "pins": pins,
            "drift": drift_items,
            "outdated_count": sum(1 for d in drift_items if d["status"] == "OUTDATED_PIN"),
        })
    total_outdated = sum(c["outdated_count"] for c in consumers)
    stamps = [_observed_at(row["source_as_of"]) for row in consumers if row["source_as_of"]]
    metadata = _freshness(stamps, bool(consumers))
    metadata.update(window_cst="snapshot", window_start=min(stamps).isoformat() if stamps else None,
                    window_end=max(stamps).isoformat() if stamps else None)
    return {
        **metadata,
        "upstreams": {k: v[1][:12] for k, v in upstreams.items()},
        "consumers": consumers,
        "outdated_pin_count": total_outdated,
        "all_up_to_date": total_outdated == 0 if any(c["drift"] for c in consumers) else None,
        "sample_size": sum(len(c["drift"]) for c in consumers),
    }


def _compute_s11_s12_context_and_tools(
    warehouse_db: Path,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
    dsh_sessions: Path | None = None,
    telemetry_shards_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Token allocation and per-tool burn are proxies, never measured savings."""
    mp, cp = magpie_usage or DEFAULT_MAGPIE_USAGE, copilot_db or DEFAULT_COPILOT_DB
    slice_tokens = dict.fromkeys(("System & AGENTS.md 指令链", "Skills 目录与 SKILL.md", "Tools Schema 定义",
                                 "Tool Results 工具返回", "User/Assistant 对话与思考链"), 0)
    total_in, total_cache_read = 0, 0
    context_stamps, cache_stamps = [], []
    tool_calls: dict[str, list[tuple[int, str, datetime]]] = {}
    rows = []
    if warehouse_db.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)) as conn:
            rows = _timed_rows(conn, "fact_turn")
    seen_turn_sigs: set[tuple[str, str, str]] = set()
    for row in rows:
        sid_s = str(row.get("session_id") or "")
        tid_s = str(row.get("turn_uuid") or "")
        sk_list = _json_list(row.get("skill_names"))
        sk_s = str(row.get("attribution_skill") or (sk_list[0] if sk_list else "team-lead"))
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, sk_s))
        in_t, out_t, cache = (int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
        total_in += in_t
        total_cache_read += cache
        stamp = row["_observed_at"]
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = json.loads(row.get("tool_calls") or "[]")
        skills = json.loads(row.get("skill_names") or "[]")
        skill = str(row.get("attribution_skill") or (skills[0] if skills else "team-lead"))
        has_skill, has_tool = bool(row.get("attribution_skill") or skills), bool(tools)
        shares = (0.16 if has_skill and has_tool else 0.20 if has_tool else 0.28,
                  0.18 if has_skill else 0.04, 0.14 if has_tool else 0.18,
                  0.36 if has_tool else 0.10, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens["User/Assistant 对话与思考链"] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {"bash", "execute_code", "terminal", "run_command"}:
                name = "bash"
            elif name in {"read", "view", "read_file"}:
                name = "read/view"
            elif name in {"edit", "apply_patch", "write", "create"}:
                name = "edit/write"
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    shard_rows = _read_telemetry_shards(telemetry_shards_dir, days=7)
    for s_row in shard_rows:
        if str(s_row.get("kind") or "") != "skill_turn":
            continue
        sid_s = str(s_row.get("session_id") or "")
        tid_s = str(s_row.get("turn_id") or "")
        skill = str(s_row.get("skill") or "team-lead")
        if (sid_s or tid_s) and (sid_s, tid_s, skill) in seen_turn_sigs:
            continue
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, skill))
        in_t = int(s_row.get("input_tokens") or s_row.get("in") or 0)
        out_t = int(s_row.get("output_tokens") or s_row.get("out") or 0)
        cache = int(s_row.get("cache_read") or 0)
        total_in += in_t
        total_cache_read += cache
        stamp = s_row["_observed_at"]
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = _json_list(s_row.get("tool_calls"))
        has_tool = bool(tools)
        shares = (0.16 if has_tool else 0.28, 0.18, 0.14 if has_tool else 0.18, 0.36 if has_tool else 0.10, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens["User/Assistant 对话与思考链"] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {"bash", "execute_code", "terminal", "run_command"}:
                name = "bash"
            elif name in {"read", "view", "read_file"}:
                name = "read/view"
            elif name in {"edit", "apply_patch", "write", "create"}:
                name = "edit/write"
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    if not cache_stamps:
        usage = _usage_records(mp, telemetry_shards_dir=telemetry_shards_dir)
        for row in usage:
            in_t, cache = int(row.get("in") or 0), int(row.get("cache_read") or 0)
            if in_t + cache > 0:
                total_in += in_t
                total_cache_read += cache
                cache_stamps.append(row["_observed_at"])
    if not cache_stamps and cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            for row in _timed_rows(conn, "assistant_usage_events"):
                in_t, cache = int(row.get("input_tokens") or 0), int(row.get("cache_read_tokens") or 0)
                if in_t + cache > 0:
                    total_in += in_t
                    total_cache_read += cache
                    cache_stamps.append(row["_observed_at"])
    # File sizes have no observation timestamps or measured token composition.
    # Do not synthesize slices from an unbounded DSH byte-count fallback.
    shards_present = bool(telemetry_shards_dir and telemetry_shards_dir.is_dir() and list(telemetry_shards_dir.glob("*-skill-usage.jsonl")))
    present = warehouse_db.is_file() or mp.is_file() or cp.is_file() or shards_present
    context_meta = _freshness(context_stamps, present)
    cache_meta = _freshness(cache_stamps, present)
    total_ctx = sum(slice_tokens.values())
    s11 = {**_freshness(context_stamps or cache_stamps, present), "estimated": True,
           "estimation_method": "fixed-share allocation of observed turn tokens; no measured compression saving",
           "slices": [{"slice_key": key, "tokens": value if total_ctx else None,
                       "share_pct": round(100 * value / total_ctx, 1) if total_ctx else None,
                       "estimated": True, **context_meta} for key, value in slice_tokens.items()],
           "total_context_tokens": total_ctx or None,
           "kv_cache_hit_pct": round(100 * total_cache_read / (total_in + total_cache_read), 1) if total_in + total_cache_read else None,
           "kv_cache_freshness": cache_meta, "compressed_saved_tokens": None}
    grand_total = sum(tokens for calls in tool_calls.values() for tokens, _, _ in calls)
    s12 = []
    for tool, calls in tool_calls.items():
        tokens = sorted(item[0] for item in calls)
        skill_counts: dict[str, int] = {}
        for tok, skill, _ in calls:
            skill_counts[skill] = skill_counts.get(skill, 0) + tok
        s12.append({"tool_name": tool, "call_count": len(calls), "total_tokens": sum(tokens),
                    "p95_tokens": tokens[max(0, (len(tokens) * 95 + 99) // 100 - 1)],
                    "bloated_call_count": sum(tok > 10000 for tok in tokens),
                    "share_pct": round(100 * sum(tokens) / grand_total, 1) if grand_total else None,
                    "dominant_skill": max(skill_counts, key=skill_counts.get), "estimated": True,
                    "estimation_method": "turn context * 0.36 / tool count, not measured tool result tokens",
                    **_freshness([item[2] for item in calls])})
    s12.sort(key=lambda row: (-row["total_tokens"], row["tool_name"]))
    return s11, s12, _build_tool_context_burn_candidates(s12)


def _percent_label(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "unknown"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _extract_skill_names(value: Any, known_skills: set[str]) -> set[str]:
    text = str(value or "")
    found: set[str] = set()
    for match in _SKILL_NAME_RE.finditer(text):
        name = match.group("ctx") or match.group("path") or match.group("call")
        if name:
            found.add("team-lead" if name == "goal-target-task" else name)
    for pattern in (_REF_PATH_RE, _SCRIPT_PATH_RE):
        found.update(match.group("skill") for match in pattern.finditer(text))
    for name in known_skills:
        if re.search(rf"(?<![A-Za-z0-9_-])/{re.escape(name)}(?![A-Za-z0-9_-])", text):
            found.add(name)
        if re.search(rf"(?<![A-Za-z0-9_-])skills/{re.escape(name)}(?:/|\b)", text):
            found.add(name)
    return {"team-lead" if name == "goal-target-task" else name for name in found}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1))
    return ordered[index]


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "pass", "caught"}


def _normalize_skill_rel_path(raw_path: str, *, prefix: str) -> str:
    """Normalize a warehouse reference_path or script_path to 'references/...' or 'scripts/...'."""
    cleaned = str(raw_path or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("legacy:"):
        return ""
    marker = f"/{prefix}/"
    idx = cleaned.find(marker)
    if idx != -1:
        return f"{prefix}/{cleaned[idx + len(marker):].lstrip('/')}"
    if cleaned.startswith(f"{prefix}/"):
        return cleaned
    return f"{prefix}/{cleaned.lstrip('/')}"


def _read_l2_channel_counts(wh: Path, start30: datetime, end30: datetime) -> dict[str, dict[str, int]]:
    """Read optional 30d per-skill call counts from sibling L2 clean DBs when present."""
    counts: dict[str, dict[str, int]] = {}
    clean_dir = wh.parent.parent / "L2_normalize" / "clean"
    if not clean_dir.is_dir():
        return counts
    day_from = start30.date().isoformat()
    day_to = (end30 - timedelta(days=1)).date().isoformat()
    specs = (
        ("codex", clean_dir / "codex_prompts.db", "SELECT skill, COUNT(*) FROM skill_use WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("hermes", clean_dir / "hermes_tools.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("copilot", clean_dir / "copilot_cli.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("claude", clean_dir / "claude_prompts.db", "SELECT slash_skill, COUNT(*) FROM prompt WHERE slash_skill IS NOT NULL AND slash_skill != '' AND day BETWEEN ? AND ? GROUP BY slash_skill"),
    )
    for channel, db_path, sql in specs:
        if not db_path.is_file():
            continue
        try:
            with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                for raw_sk, raw_n in conn.execute(sql, (day_from, day_to)):
                    sk = str(raw_sk or "").strip().lstrip("/")
                    if not sk or sk == "(unrecorded)":
                        continue
                    norm = "team-lead" if sk in {"goal-target-task", "goal-task"} else sk
                    bucket = counts.setdefault(norm, {"claude": 0, "copilot": 0, "codex": 0, "hermes": 0, "gtt": 0})
                    bucket[channel] = max(bucket[channel], int(raw_n or 0))
        except sqlite3.Error:
            continue
    return counts


def _collect_skill_details(
    *,
    inventory: Mapping[str, Mapping[str, Any]],
    skill_rows: Sequence[Mapping[str, Any]],
    per_skill_channels: Mapping[str, Mapping[str, Any]],
    l2_channels: Mapping[str, Mapping[str, Any]],
    per_skill_cache: Mapping[str, Mapping[str, Any]],
    s11_context: Mapping[str, Any],
    per_skill_refs: Mapping[str, Mapping[str, Any]],
    per_skill_scripts: Mapping[str, Mapping[str, Any]],
    per_skill_eval_cases: Mapping[str, Mapping[str, Any]],
    warehouse_present: bool,
) -> list[dict[str, Any]]:
    """Build per-skill inspector details with redacted doc_preview and references."""
    row_by_skill = {r["skill"]: r for r in skill_rows}
    global_eval_catalog: dict[str, dict[str, Any]] = {}
    for inv_m in inventory.values():
        for c_entry in inv_m.get("eval_cases", []):
            cid = str(c_entry.get("id") or "").strip()
            if cid and cid not in global_eval_catalog:
                global_eval_catalog[cid] = dict(c_entry)

    all_detail_skills = sorted(
        set(inventory.keys()) | {sk for sk, row_d in row_by_skill.items() if int(row_d.get("loads_30d") or 0) > 0},
        key=lambda sk: (
            0 if sk in inventory else 1,
            -(row_by_skill.get(sk, {}).get("loads_30d") or 0),
            0 if (inventory.get(sk, {}).get("scope") or "global") == "global" else 1,
            sk,
        ),
    )
    skill_details: list[dict[str, Any]] = []
    for sk in all_detail_skills:
        is_ghost = sk not in inventory
        inv_meta = inventory.get(sk, {})
        row_data = row_by_skill.get(sk, {})
        scope = str(inv_meta.get("scope") or "global")
        repo_label = str(inv_meta.get("repo") or "user-skills")
        skill_dir_str = str(inv_meta.get("skill_dir") or f"~/Development/user-skills/skills/{sk}")
        skill_md_lines = int(inv_meta.get("l0_lines") or 0)
        fm_tok_est = int(inv_meta.get("frontmatter_tokens_est") or 1)
        body_tok_est = int(inv_meta.get("body_tokens_est") or max(1, skill_md_lines * 12))

        loads_7d_val = int(row_data.get("loads_7d") or 0)
        loads_30d_val = int(row_data.get("loads_30d") or 0)
        needs_opt = bool(row_data.get("needs_optimization"))
        if is_ghost:
            status_label = "ghost"
        elif loads_30d_val == 0:
            status_label = "zombie_30d"
        elif needs_opt:
            status_label = "needs_optimization"
        else:
            status_label = "active"

        obs_ch = per_skill_channels.get(sk, {})
        l2_ch = l2_channels.get(sk, {})
        source_breakdown = {
            ch_key: max(int(obs_ch.get(ch_key) or 0), int(l2_ch.get(ch_key) or 0))
            for ch_key in ("claude", "copilot", "codex", "hermes", "gtt")
        }

        cache_b = per_skill_cache.get(sk, {})
        tot_in_sk = int(cache_b.get("in") or 0)
        tot_cr_sk = int(cache_b.get("cache_read") or 0)
        sk_kv_cache_pct = (
            round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1)
            if (tot_in_sk + tot_cr_sk) > 0
            else s11_context.get("kv_cache_hit_pct")
        )

        # Merge physical references + observed warehouse reference_hits
        phys_refs = {entry["rel_path"]: dict(entry) for entry in inv_meta.get("reference_entries", [])}
        obs_refs = per_skill_refs.get(sk, {})
        merged_ref_paths = sorted(set(phys_refs.keys()) | set(obs_refs.keys()))
        ref_items: list[dict[str, Any]] = []
        for rel_p in merged_ref_paths:
            p_info = phys_refs.get(rel_p)
            o_info = obs_refs.get(rel_p)
            hits_val: int | None
            sessions_val: int | None
            if warehouse_present:
                hits_val = int(o_info["hits"]) if o_info else 0
                sessions_val = len(o_info["sessions"]) if o_info else 0
            else:
                hits_val = None
                sessions_val = None
            obs_bytes = int(o_info["max_bytes"]) if o_info else 0
            tok_est = int(p_info["tokens_est"]) if p_info else max(1, obs_bytes // 4)
            ln_cnt = int(p_info["line_count"]) if p_info else max(1, obs_bytes // 48) if obs_bytes else 0
            ref_item: dict[str, Any] = {
                "rel_path": rel_p,
                "line_count": ln_cnt,
                "tokens_est": tok_est,
                "hits_30d": hits_val,
                "sessions_30d": sessions_val,
                "is_unhit": bool(hits_val == 0),
            }
            if p_info and isinstance(p_info.get("doc_preview"), str) and p_info["doc_preview"]:
                ref_item["doc_preview"] = p_info["doc_preview"]
            ref_items.append(ref_item)

        unhit_refs_count = sum(1 for r in ref_items if r["is_unhit"])
        unhit_refs_ratio = round(unhit_refs_count / len(ref_items), 4) if ref_items else 0.0
        refs_tokens_est = (
            int(inv_meta["refs_tokens_est"])
            if "refs_tokens_est" in inv_meta
            else sum(int(r.get("tokens_est") or 0) for r in ref_items)
        )
        refs_kb = (
            float(inv_meta["refs_kb"])
            if "refs_kb" in inv_meta
            else round((refs_tokens_est * 4) / 1024.0, 1)
        )
        bundle_kb = (
            float(inv_meta["bundle_kb"])
            if "bundle_kb" in inv_meta
            else round(((body_tok_est + refs_tokens_est) * 4) / 1024.0, 1)
        )

        # Merge physical scripts + observed warehouse script_call (strictly filtered)
        phys_scripts = {sc for sc in inv_meta.get("script_entries", []) if _is_valid_skill_script_rel_path(sc)}
        obs_scripts = {
            sc: info
            for sc, info in per_skill_scripts.get(sk, {}).items()
            if _is_valid_skill_script_rel_path(sc)
        }
        merged_script_paths = sorted(phys_scripts | set(obs_scripts.keys()))
        script_items: list[dict[str, Any]] = []
        for rel_sc in merged_script_paths:
            o_sc = obs_scripts.get(rel_sc)
            if warehouse_present:
                calls_val: int | None = int(o_sc["calls"]) if o_sc else 0
            else:
                calls_val = None
            if o_sc and int(o_sc["calls"]) > 0:
                graded = int(o_sc.get("graded_calls") or 0)
                succ_rate = round(int(o_sc["successes"]) / graded, 4) if graded > 0 else None
                durs = [float(v) for v in o_sc.get("durations", [])]
                p95_val = round(float(_p95(durs)), 2) if durs else None
            else:
                succ_rate = None
                p95_val = None
            script_items.append({
                "rel_path": rel_sc,
                "calls_30d": calls_val,
                "success_rate": succ_rate,
                "p95_ms": p95_val,
                "is_unpracticed": bool(calls_val == 0),
            })

        gate_script_calls_30d = sum(
            int(s.get("calls_30d") or 0)
            for s in script_items
            if Path(str(s["rel_path"])).name in _GATE_SCRIPT_BASENAMES
        )

        # Merge parsed eval_cases + observed per-skill eval case events
        parsed_cases = [dict(c) for c in inv_meta.get("eval_cases", [])]
        obs_cases = per_skill_eval_cases.get(sk, {})
        known_case_ids = {c["id"] for c in parsed_cases}
        eval_case_items: list[dict[str, Any]] = []
        for idx_c, c_entry in enumerate(parsed_cases):
            cid = c_entry["id"]
            c_obs = obs_cases.get(cid, {})
            int_30d = int(c_obs.get("interceptions_30d") or 0)
            if idx_c == 0 and int_30d == 0 and gate_script_calls_30d > 0 and c_entry.get("status", "active") == "active":
                int_30d = gate_script_calls_30d
            raw_title = str(c_entry.get("title") or "").strip()
            safe_title = (
                raw_title
                if raw_title and raw_title != cid
                else f"{cid} — {c_entry.get('assertion_summary') or 'Contract gate verification check'}"
            )
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": c_entry["kind"],
                "assertion_summary": c_entry["assertion_summary"],
                "status": c_entry.get("status", "active"),
                "interceptions_30d": int_30d,
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })
        for cid, c_obs in sorted(obs_cases.items()):
            if cid in known_case_ids or not _EVAL_CASE_ID_RE.match(cid):
                continue
            global_match = global_eval_catalog.get(cid)
            if global_match:
                m_title = str(global_match.get("title") or "").strip()
                m_summary = str(global_match.get("assertion_summary") or f"Observed gate event for {cid}")
                m_kind = str(global_match.get("kind") or "contract")
                safe_title = m_title if m_title and m_title != cid else f"{cid} — {m_summary}"
            else:
                m_summary = f"Observed gate verification event for {cid}"
                m_kind = "contract"
                safe_title = f"{cid} — Gate contract verification"
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": m_kind,
                "assertion_summary": m_summary,
                "status": "active",
                "interceptions_30d": int(c_obs.get("interceptions_30d") or 0),
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })

        total_eval_interceptions = max(
            int(row_data.get("eval_interceptions") or 0),
            sum(int(c.get("interceptions_30d") or 0) for c in eval_case_items),
        )

        detail_meta = {
            key: row_data[key]
            for key in ("window_cst", "window_start", "window_end", "source_as_of", "sample_size", "coverage_status")
            if key in row_data
        }
        if not detail_meta:
            detail_meta = _freshness([], warehouse_present, 30)

        skill_details.append({
            **detail_meta,
            "skill": sk,
            "is_ghost": is_ghost,
            "scope": scope,
            "repo": repo_label,
            "skill_dir": skill_dir_str,
            "skill_md_path": "SKILL.md",
            "skill_md_lines": skill_md_lines,
            "frontmatter_tokens_est": fm_tok_est,
            "body_tokens_est": body_tok_est,
            "bundle_kb": bundle_kb,
            "refs_kb": refs_kb,
            "refs_tokens_est": refs_tokens_est,
            "doc_preview": str(inv_meta.get("doc_preview") or ""),
            "loads_7d": loads_7d_val,
            "loads_30d": loads_30d_val,
            "empty_load_rate": row_data.get("empty_load_rate"),
            "unpracticed_rate": row_data.get("unpracticed_rate"),
            "unhit_refs_count": unhit_refs_count,
            "unhit_refs_ratio": unhit_refs_ratio,
            "roi_score": row_data.get("roi_score"),
            "r1_pass_rate": row_data.get("r1_pass_rate"),
            "avg_context_tokens": row_data.get("avg_context_tokens"),
            "kv_cache_hit_pct": sk_kv_cache_pct,
            "edit_churn": int(row_data.get("edit_churn_count") or 0),
            "retry_churn": int(row_data.get("retry_commands") or 0),
            "impl_bug_escapes": int(row_data.get("impl_bug_escapes") or 0),
            "spec_gap_escapes": int(row_data.get("spec_gap_escapes") or 0),
            "eval_interceptions": total_eval_interceptions,
            "eval_escapes": int(row_data.get("eval_escapes") or 0),
            "status_label": status_label,
            "source_breakdown": source_breakdown,
            "references": ref_items,
            "scripts": script_items,
            "eval_cases": eval_case_items,
        })
    return skill_details


def compute_skill_roi_diagnostics(
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
    """Compute S1-S4, S6-S10 Skill ROI diagnostics, and S11-S12 Context & Tool Burn."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    cp = copilot_db or DEFAULT_COPILOT_DB
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    resolved_sync = _resolve_icloud_sync_dir(icloud_sync_dir)
    shards_dir = (
        telemetry_shards_dir
        if telemetry_shards_dir is not None
        else (
            resolved_sync / "telemetry-shards"
            if resolved_sync is not None and (icloud_sync_dir is not None or (warehouse_db is None and copilot_db is None))
            else None
        )
    )
    effective_roi_path = icloud_roi_path or (
        resolved_sync / "jobs" / "aidata-digest" / "skills-roi.json" if resolved_sync is not None else None
    )
    should_mirror_roi = (
        mirror_icloud_roi
        if mirror_icloud_roi is not None
        else (icloud_roi_path is not None or icloud_sync_dir is not None or (resolved_sync is not None and warehouse_db is None and copilot_db is None))
    )

    inventory = _discover_skill_inventory(s_root, d_root)
    known_skills = set(inventory.keys())
    script_to_skill: dict[str, str] = {}
    script_basename_to_targets: dict[str, list[tuple[str, str]]] = {}
    script_realpath_to_targets: dict[str, list[tuple[str, str]]] = {}
    for s_name, meta in inventory.items():
        for sc in meta.get("scripts", []):
            script_to_skill[sc] = s_name
        for rel_sc in meta.get("script_entries", []):
            if not _is_valid_skill_script_rel_path(rel_sc):
                continue
            bname = Path(rel_sc).name
            script_basename_to_targets.setdefault(bname, []).append((s_name, rel_sc))
        for rel_sc, resolved_p in (meta.get("script_realpaths") or {}).items():
            if resolved_p and _is_valid_skill_script_rel_path(rel_sc):
                script_realpath_to_targets.setdefault(resolved_p, []).append((s_name, rel_sc))
    basename_regex = (
        re.compile(
            r"(?<![A-Za-z0-9_./-])("
            + "|".join(re.escape(bn) for bn in sorted(script_basename_to_targets.keys(), key=len, reverse=True))
            + r")(?![A-Za-z0-9_.-])"
        )
        if script_basename_to_targets
        else None
    )

    wall_clock_date = _cst_today_str()
    historical_dates: list[datetime] = []
    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            for table in ("fact_turn", "fact_gtt_task_run", "skill_day", "hermes_skill_day"):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                column = next((name for name in ("cst_day", "day", "date", "occurred_at", "timestamp", "started_at", "created_at") if name in columns), None)
                if column:
                    for row in conn.execute(f'SELECT "{column}" FROM "{table}"'):
                        stamp = _observed_at(row[0])
                        if stamp and stamp < _window_bounds()[1]:
                            historical_dates.append(stamp)
    anchor_as_of = max(historical_dates).date().isoformat() if historical_dates else None
    staleness_days = (date.fromisoformat(wall_clock_date) - date.fromisoformat(anchor_as_of)).days if anchor_as_of else None
    window_anchor = {"anchor_date": anchor_as_of, "source_as_of": anchor_as_of, "wall_clock_date": wall_clock_date,
                     "staleness_days": staleness_days, "coverage_status": "missing" if staleness_days is None else "fresh" if staleness_days <= 2 else "stale",
                     "window_days_7d": 7, "window_days_30d": 30, "basis": "historical_source_watermark; not the live metric window"}

    # Per-skill accumulators
    stats: dict[str, dict[str, Any]] = {
        s_name: {
            "skill": s_name,
            "loads_7d": 0,
            "loads_30d": 0,
            "script_execs": 0,
            "script_failures": 0,
            "script_touch_sessions": 0,
            "script_durations_ms": [],
            "has_scripts": bool(meta.get("scripts")),
            "reference_hit_count": 0,
            "reference_bytes": 0,
            "empty_load_sessions": 0,
            "eval_interceptions": 0,
            "eval_escapes": 0,
            "scripts": list(meta.get("scripts", [])),
            "l0_only_sessions": 0,
            "l1_selective_sessions": 0,
            "l1_greedy_sessions": 0,
            "total_tokens": 0,
            "token_session_count": 0,
            "session_count": 0,
            "edit_churn_count": 0,
            "retry_commands": 0,
            "r1_total": 0,
            "r1_pass": 0,
            "rework_rounds": 0,
            "impl_bug_escapes": 0,
            "spec_gap_escapes": 0,
        }
        for s_name, meta in inventory.items()
    }

    def _ensure_skill(name: str) -> dict[str, Any]:
        norm = "team-lead" if name == "goal-target-task" else name
        if norm not in stats:
            stats[norm] = {
                "skill": norm,
                "loads_7d": 0,
                "loads_30d": 0,
                "script_execs": 0,
                "script_failures": 0,
                "script_touch_sessions": 0,
                "script_durations_ms": [],
                "has_scripts": False,
                "reference_hit_count": 0,
                "reference_bytes": 0,
                "empty_load_sessions": 0,
                "eval_interceptions": 0,
                "eval_escapes": 0,
                "scripts": [],
                "l0_only_sessions": 0,
                "l1_selective_sessions": 0,
                "l1_greedy_sessions": 0,
                "total_tokens": 0,
                "token_session_count": 0,
                "session_count": 0,
                "edit_churn_count": 0,
                "retry_commands": 0,
                "r1_total": 0,
                "r1_pass": 0,
                "rework_rounds": 0,
                "impl_bug_escapes": 0,
                "spec_gap_escapes": 0,
            }
        return stats[norm]

    populations: dict[str, dict[str, list[datetime]]] = {}
    observed: list[datetime] = []
    def observe(skill: str, kind: str, stamp: datetime) -> dict[str, Any]:
        entry = _ensure_skill(skill)
        populations.setdefault(entry["skill"], {}).setdefault(kind, []).append(stamp)
        return entry

    s2_gate = {"total_runs_7d": 0, "true_positive_catches_7d": 0, "config_or_rerun_noise_7d": 0,
               "classified_sample_size": 0, "gate_catch_rate": None, "noise_rate": None,
               **_freshness([], wh.is_file())}
    eval_total = 0
    eval_interceptions = 0
    eval_escapes = 0
    eval_by_case: dict[str, dict[str, int]] = {}
    per_skill_eval_cases: dict[str, dict[str, dict[str, int]]] = {}
    per_skill_refs: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_scripts: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_channels: dict[str, dict[str, int]] = {}
    per_skill_cache: dict[str, dict[str, int]] = {}
    session_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    turn_skill_links: dict[str, set[str]] = {}
    start7, _ = _window_bounds()
    start30, end30 = _window_bounds(30)
    shard_records_30d = _read_telemetry_shards(shards_dir, days=30)
    shards_present = bool(shards_dir and shards_dir.is_dir() and list(shards_dir.glob("*-skill-usage.jsonl")))
    warehouse_present = wh.is_file() or cp.is_file() or shards_present
    l2_channels = _read_l2_channel_counts(wh, start30, end30)
    seen_turn_sigs_30d: set[tuple[str, str, str]] = set()
    seen_ref_sigs_30d: set[tuple[str, str, str]] = set()
    seen_script_sigs_30d: set[tuple[str, str, str]] = set()

    def _norm_skill(name: str) -> str:
        return "team-lead" if name in {"goal-target-task", "goal-task"} else name

    def _bump_channel(skill: str, channel: str, delta: int = 1) -> None:
        sk = _norm_skill(skill)
        bucket = per_skill_channels.setdefault(sk, {"claude": 0, "copilot": 0, "codex": 0, "hermes": 0, "gtt": 0})
        bucket[channel] = bucket.get(channel, 0) + max(0, int(delta))

    def session_for(source: str, sid: str, skill: str, stamp: datetime, *, reference_source: bool = False) -> dict[str, Any]:
        skill = _norm_skill(skill)
        key = (source, sid or f"row-{len(session_records)}", skill)
        record = session_records.setdefault(key, {
            "source": source, "sid": sid, "skill": skill, "stamp": stamp, "loaded": True,
            "refs": {}, "reference_source": reference_source, "read_proxy": 0,
            "scripts": [], "edits": 0, "tokens": 0, "token_seen": False,
        })
        record["stamp"] = max(record["stamp"], stamp)
        record["reference_source"] = record["reference_source"] or reference_source
        return record

    def add_reference(source: str, sid: str, skill: str, path: str, stamp: datetime, byte_len: Any = 0) -> None:
        norm_sk = _norm_skill(skill)
        record = session_for(source, sid, norm_sk, stamp, reference_source=True)
        ref_path = str(path or "").strip()
        if ref_path:
            rel_norm = _normalize_skill_rel_path(ref_path, prefix="references") or ref_path
            if sid:
                seen_ref_sigs_30d.add((sid, norm_sk, rel_norm))
            try:
                size = max(0, int(byte_len or 0))
            except (TypeError, ValueError):
                size = 0
            record["refs"].setdefault(ref_path, size)
            rel_ref = _normalize_skill_rel_path(ref_path, prefix="references")
            if rel_ref:
                ref_bucket = per_skill_refs.setdefault(norm_sk, {}).setdefault(
                    rel_ref, {"hits": 0, "sessions": set(), "max_bytes": 0}
                )
                ref_bucket["hits"] += 1
                ref_bucket["sessions"].add(sid or f"{source}:{stamp.isoformat()}")
                ref_bucket["max_bytes"] = max(ref_bucket["max_bytes"], size)
        observed.append(stamp)
        observe(norm_sk, "usage", stamp)

    def add_script(source: str, sid: str, skill: str, path: str, stamp: datetime, exit_code: Any = 0, duration_ms: Any = None) -> None:
        norm_sk = _norm_skill(skill)
        raw_path_str = str(path or "").strip()
        is_legacy_proxy = raw_path_str == "legacy:bash"
        rel_script = "" if is_legacy_proxy else _normalize_skill_rel_path(raw_path_str, prefix="scripts")
        if not is_legacy_proxy and not _is_valid_skill_script_rel_path(rel_script):
            return

        has_exit = exit_code not in (None, "")
        try:
            code = int(exit_code) if has_exit else 0
        except (TypeError, ValueError):
            code = 0
            has_exit = False
        has_dur = duration_ms not in (None, "")
        try:
            duration = max(0.0, float(duration_ms)) if has_dur else 0.0
        except (TypeError, ValueError):
            duration = 0.0
            has_dur = False

        def _record_single_script(target_sk: str, target_rel: str, *, is_proxy: bool = False) -> None:
            record = session_for(source, sid, target_sk, stamp, reference_source=source == "copilot")
            sig_path = "legacy:bash" if is_proxy else target_rel
            call = (sig_path, stamp.isoformat(), code, duration)
            if sid and sig_path:
                seen_script_sigs_30d.add((sid, target_sk, sig_path))
            if call not in record["scripts"]:
                record["scripts"].append(call)
                if not is_proxy and target_rel:
                    sc_bucket = per_skill_scripts.setdefault(target_sk, {}).setdefault(
                        target_rel, {"calls": 0, "successes": 0, "graded_calls": 0, "durations": []}
                    )
                    sc_bucket["calls"] += 1
                    if has_exit or source == "warehouse":
                        sc_bucket["graded_calls"] += 1
                        if code == 0:
                            sc_bucket["successes"] += 1
                    if has_dur:
                        sc_bucket["durations"].append(duration)
            entry = _ensure_skill(target_sk)
            entry["has_scripts"] = True
            if not is_proxy and target_rel and target_rel not in entry["scripts"]:
                entry["scripts"].append(target_rel)
            observed.append(stamp)
            observe(target_sk, "usage", stamp)

        if is_legacy_proxy:
            _record_single_script(norm_sk, "", is_proxy=True)
            return

        targets: list[tuple[str, str]] = []
        inv_entry = inventory.get(norm_sk)
        if inv_entry and rel_script in set(inv_entry.get("script_entries", [])):
            targets.append((norm_sk, rel_script))
            rp = (inv_entry.get("script_realpaths") or {}).get(rel_script)
            if rp:
                for sym_sk, sym_rel in script_realpath_to_targets.get(rp, []):
                    if (sym_sk, sym_rel) not in targets:
                        targets.append((sym_sk, sym_rel))
        else:
            bname = Path(rel_script).name
            by_name = script_basename_to_targets.get(bname, [])
            if any(t_sk == norm_sk for t_sk, _ in by_name):
                for t_sk, t_rel in by_name:
                    if t_sk == norm_sk and (t_sk, t_rel) not in targets:
                        targets.append((t_sk, t_rel))
            elif len(by_name) == 1 and norm_sk not in known_skills:
                targets.append(by_name[0])
            else:
                targets.append((norm_sk, rel_script))

        for t_sk, t_rel in targets:
            _record_single_script(t_sk, t_rel)

    def _attribute_basename_scripts_in_text(
        source: str,
        sid: str,
        text: str,
        active_skills: set[str],
        stamp: datetime,
        exit_code: Any = None,
        duration_ms: Any = None,
    ) -> set[str]:
        """Match bare script basenames (e.g. goal-queue.py) when full skills/<sk>/scripts/ prefix is omitted."""
        credited: set[str] = set()
        if not basename_regex or not text:
            return credited
        explicit_basenames = {Path(m.group("script")).name for m in _SCRIPT_PATH_RE.finditer(text)}
        for m in basename_regex.finditer(text):
            bname = m.group(1)
            if bname in explicit_basenames:
                continue
            candidates = script_basename_to_targets.get(bname, [])
            if not candidates:
                continue
            overlapping = [(c_sk, c_rel) for c_sk, c_rel in candidates if c_sk in active_skills]
            chosen = overlapping if overlapping else (candidates if len(candidates) == 1 else [])
            for c_sk, c_rel in chosen:
                add_script(source, sid, c_sk, f"skills/{c_sk}/{c_rel}", stamp, exit_code, duration_ms)
                credited.add(c_sk)
        return credited

    goal_skills_cache: dict[str, set[str]] = {}

    def _handoff_skills_for_goal(goal: str) -> set[str]:
        if not goal:
            return set()
        if goal in goal_skills_cache:
            return goal_skills_cache[goal]
        handoff = d_root / "handoff" / goal
        text_parts: list[str] = []
        if handoff.is_dir():
            spec_p = handoff / "spec.md"
            if spec_p.is_file():
                try:
                    text_parts.append(spec_p.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
            briefs_dir = handoff / "briefs"
            if briefs_dir.is_dir():
                for path in sorted(briefs_dir.glob("*.md")):
                    if path.is_file():
                        try:
                            text_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                        except OSError:
                            pass
        extracted = _extract_skill_names("\n".join(text_parts), known_skills) if text_parts else set()
        goal_skills_cache[goal] = extracted
        return extracted

    def task_skills(row: dict[str, Any], *, keys_to_skills: dict[str, set[str]]) -> set[str]:
        raw_explicit = {str(item).strip() for item in _json_list(row.get("skills_used")) if str(item).strip()}
        if not raw_explicit and row.get("skills_used"):
            raw_explicit = _extract_skill_names(row.get("skills_used"), known_skills)
        normalized_explicit = {"team-lead" if item in {"goal-target-task", "goal-task"} else item for item in raw_explicit}
        explicit = normalized_explicit & known_skills if known_skills else normalized_explicit
        if explicit:
            return explicit
        goal = str(row.get("goal_id") or "")
        inferred = set(_handoff_skills_for_goal(goal))
        for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
            if key:
                inferred.update(keys_to_skills.get(str(key).strip().lower(), set()))
        if inferred:
            return inferred
        goal_lower = goal.lower()
        inferred = {"team-lead"}
        for skill, hints in (("my-designer", ("design", "ui", "feel", "glass", "hallmark")),
                             ("design-studio", ("ingest", "palette", "motion")),
                             ("retro-loop", ("retro", "telemetry", "roi", "aidash")),
                             ("knowledge-digest", ("learn", "digest", "blog", "vault")),
                             ("skill-contract", ("skill", "contract")),
                             ("m2-dispatcher", ("m2", "herdr", "dispatch"))):
            if any(hint in goal_lower for hint in hints):
                inferred.add(skill)
        return inferred

    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            ci_rows = _timed_rows(conn, "fact_ci_job_run")
            catches = sum(row.get("failure_category") in {"true_positive_gate", "build_or_test_failure"} for row in ci_rows)
            noise = sum(str(row.get("failure_category") or "").startswith("config_drift_") or row.get("failure_category") == "runner_capacity_or_sim" for row in ci_rows)
            classified = catches + noise
            s2_gate.update(total_runs_7d=len(ci_rows), true_positive_catches_7d=catches,
                           config_or_rerun_noise_7d=noise, classified_sample_size=classified,
                           gate_catch_rate=round(catches / classified, 4) if classified else None,
                           noise_rate=round(noise / len(ci_rows), 4) if ci_rows else None)
            s2_gate.update(_freshness([r["_observed_at"] for r in ci_rows], True))
            if not classified:
                s2_gate["coverage_status"] = "unknown"
            observed.extend(row["_observed_at"] for row in ci_rows)
            turn_rows = _timed_rows(conn, "fact_turn", 30)
            wh_session_tokens: dict[str, tuple[int, int, int]] = {}
            if _table_columns(conn, "fact_request"):
                with contextlib.suppress(sqlite3.Error):
                    req_start_ms = int(start30.timestamp() * 1000)
                    req_end_ms = int(end30.timestamp() * 1000)
                    for r_sid, r_in, r_out, r_cr in conn.execute(
                        "SELECT session_uuid, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cache_read), 0) "
                        "FROM fact_request WHERE session_uuid IS NOT NULL AND session_uuid != '' AND ts >= ? AND ts < ? GROUP BY session_uuid",
                        (req_start_ms, req_end_ms),
                    ):
                        wh_session_tokens[str(r_sid)] = (int(r_in or 0), int(r_out or 0), int(r_cr or 0))
            ref_table = next((name for name in ("reference_hits", "fact_skill_reference_hit") if _table_columns(conn, name)), None)
            script_table = next((name for name in ("script_call", "fact_skill_script_call") if _table_columns(conn, name)), None)
            for number, row in enumerate(turn_rows):
                stamp = row["_observed_at"]
                skills = {str(item) for item in _json_list(row.get("skill_names")) if str(item).strip()}
                if row.get("attribution_skill"):
                    skills.add(str(row["attribution_skill"]))
                sid = str(row.get("session_id") or row.get("turn_uuid") or f"turn-{number}")
                keys = [row.get(key) for key in ("goal_id", "git_branch", "repo")]
                for key in keys:
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
                tools = _json_list(row.get("tool_calls"))
                norm_skills_in_turn = {_norm_skill(s) for s in skills}
                for skill in skills:
                    norm_sk = _norm_skill(skill)
                    seen_turn_sigs_30d.add((sid, str(row.get("turn_uuid") or f"turn-{number}"), norm_sk))
                    _bump_channel(norm_sk, "claude")
                    cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
                    cache_bucket["in"] += int(row.get("input_tokens") or 0)
                    cache_bucket["cache_read"] += int(row.get("cache_read") or 0)
                    record = session_for("warehouse", sid, skill, stamp, reference_source=bool(ref_table))
                    record["read_proxy"] += sum(str(tool).lower() in {"read", "view", "read_file"} for tool in tools)
                    record["edits"] += sum(str(tool).lower() in {"edit", "write", "multiedit", "apply_patch", "create"} for tool in tools)
                    record["tokens"] += sum(int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
                    record["token_seen"] = record["token_seen"] or any(row.get(key) is not None for key in ("input_tokens", "output_tokens", "cache_read"))
                    if (not script_table and any(str(tool).lower() in {"bash", "execute_code", "terminal"} for tool in tools)
                            and not any(_SCRIPT_PATH_RE.search(str(tool)) for tool in tools) and _ensure_skill(skill)["has_scripts"]):
                        # Legacy fixtures predate script_call; retain their Bash proxy without overriding observed calls.
                        add_script("warehouse", sid, skill, "legacy:bash", stamp)
                    observe(skill, "usage", stamp)
                for tool in tools:
                    text = str(tool.get("command") or tool.get("path") or tool) if isinstance(tool, dict) else str(tool)
                    for match in _REF_PATH_RE.finditer(text):
                        add_reference("warehouse", sid, match.group("skill"), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp)
                    for match in _SCRIPT_PATH_RE.finditer(text):
                        add_script("warehouse", sid, match.group("skill"), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp)
                    _attribute_basename_scripts_in_text("warehouse", sid, text, norm_skills_in_turn, stamp)
                if skills:
                    observed.append(stamp)
            for (src_key, sid_key, sk_key), rec_val in session_records.items():
                if src_key == "warehouse" and not rec_val["token_seen"] and sid_key in wh_session_tokens:
                    r_in, r_out, r_cr = wh_session_tokens[sid_key]
                    if r_in + r_out + r_cr > 0:
                        rec_val["tokens"] = r_in + r_out + r_cr
                        rec_val["token_seen"] = True
                        cache_b = per_skill_cache.setdefault(sk_key, {"in": 0, "cache_read": 0})
                        cache_b["in"] += r_in
                        cache_b["cache_read"] += r_cr
            for source_table in ("copilot_cli", "codex_prompts", "skill_day", "hermes_skill_day"):
                if not _table_columns(conn, source_table):
                    continue
                for row in _timed_rows(conn, source_table, 30):
                    if source_table in {"skill_day", "hermes_skill_day"}:
                        sk = str(row.get("skill") or "").strip()
                        cnt = int(row.get("calls") or row.get("n") or 1)
                        if sk:
                            _bump_channel(sk, "hermes" if source_table == "hermes_skill_day" else "copilot", cnt)
                        continue
                    blob = " ".join(str(row.get(key) or "") for key in ("skill_names", "attribution_skill", "user_message", "assistant_response", "prompt"))
                    skills = {str(item) for item in _json_list(row.get("skill_names")) if str(item).strip()}
                    skills.update(_extract_skill_names(blob, known_skills))
                    if row.get("attribution_skill"):
                        skills.add(str(row["attribution_skill"]))
                    ch_name = "codex" if source_table == "codex_prompts" else "copilot"
                    for sk in skills:
                        _bump_channel(sk, ch_name)
                    for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
                        if key:
                            turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
            if ref_table:
                for row in _timed_rows(conn, ref_table, 30):
                    skill = str(row.get("skill") or "").strip()
                    path = str(row.get("reference_path") or row.get("ref_path") or "").strip()
                    if skill and path:
                        src_col = str(row.get("source") or "").lower()
                        _bump_channel(skill, "codex" if "codex" in src_col else "copilot")
                        add_reference("warehouse", str(row.get("session_id") or row.get("turn_uuid") or ""), skill, path, row["_observed_at"], row.get("byte_len"))
            if script_table:
                for row in _timed_rows(conn, script_table, 30):
                    skill = str(row.get("skill") or "").strip()
                    path = str(row.get("script_path") or row.get("path") or "").strip()
                    if skill and path:
                        src_col = str(row.get("source") or "").lower()
                        _bump_channel(skill, "codex" if "codex" in src_col else "copilot")
                        add_script("warehouse", str(row.get("session_id") or row.get("turn_uuid") or ""), skill, path, row["_observed_at"], row.get("exit_code"), row.get("duration_ms"))
            gtt_task_groups: dict[tuple[str, str], dict[str, Any]] = {}
            for idx_tr, row in enumerate(_timed_rows(conn, "fact_gtt_task_run", 30)):
                g_id = str(row.get("goal_id") or "")
                t_id = str(row.get("task_id") or f"task-{idx_tr}")
                role = str(row.get("role_name") or "").strip().lower()
                stamp = row["_observed_at"]
                row_skills = task_skills(row, keys_to_skills=turn_skill_links)
                for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(row_skills)
                grp = gtt_task_groups.setdefault(
                    (g_id, t_id),
                    {
                        "skills": set(),
                        "max_round": 1,
                        "latest_stamp": stamp,
                        "has_reviewer": False,
                        "r1_failed": False,
                        "any_failed": False,
                        "rework_rounds": 0,
                    },
                )
                grp["skills"].update(_norm_skill(s) for s in row_skills)
                grp["latest_stamp"] = max(grp["latest_stamp"], stamp)
                r_no = max(1, int(row.get("round_no") or 1))
                grp["max_round"] = max(grp["max_round"], r_no)
                verdict_up = str(row.get("v5_verdict") or "").strip().upper()
                status_low = str(row.get("status") or "").strip().lower()
                is_reviewer_role = role in {"reviewer", "code_reviewer", "planner_reviewer"}
                if is_reviewer_role:
                    grp["has_reviewer"] = True
                    if r_no == 1:
                        if verdict_up not in {"PASS", "CONCERNS", "WAIVED"} or status_low == "failed":
                            grp["r1_failed"] = True
                    elif r_no >= 2:
                        grp["rework_rounds"] += 1
                else:
                    if status_low == "failed" or verdict_up == "FAIL":
                        grp["any_failed"] = True
                    if r_no >= 2:
                        grp["rework_rounds"] = max(grp["rework_rounds"], r_no - 1)
                for skill in row_skills:
                    _bump_channel(skill, "gtt")

            for grp in gtt_task_groups.values():
                stamp = grp["latest_stamp"]
                r1_passed = (
                    (not grp["r1_failed"] and grp["max_round"] == 1)
                    if grp["has_reviewer"]
                    else (grp["max_round"] == 1 and not grp["any_failed"])
                )
                for skill in grp["skills"]:
                    entry = observe(skill, "reviews", stamp)
                    entry["r1_total"] += 1
                    if r1_passed:
                        entry["r1_pass"] += 1
                    elif grp["r1_failed"] or grp["any_failed"]:
                        entry["impl_bug_escapes"] += 1
                    if grp["rework_rounds"] > 0:
                        entry["rework_rounds"] += grp["rework_rounds"]
                observed.append(stamp)
            for row in _timed_rows(conn, "fact_gtt_event", 30):
                event = row.get("evt_type")
                skills = task_skills(row, keys_to_skills=turn_skill_links)
                has_eval = any(row.get(key) not in (None, "") for key in ("eval_case", "interception", "escape"))
                is_7d = row["_observed_at"] >= start7
                if has_eval:
                    case = str(row.get("eval_case") or "unclassified")
                    int_flag = int(_flag(row.get("interception")))
                    esc_flag = int(_flag(row.get("escape")))
                    for skill in skills:
                        norm_sk = _norm_skill(skill)
                        _bump_channel(norm_sk, "gtt")
                        case_bucket = per_skill_eval_cases.setdefault(norm_sk, {}).setdefault(
                            case, {"interceptions_30d": 0, "escapes_30d": 0}
                        )
                        case_bucket["interceptions_30d"] += int_flag
                        case_bucket["escapes_30d"] += esc_flag
                    if is_7d:
                        bucket = eval_by_case.setdefault(case, {"observations": 0, "interceptions": 0, "escapes": 0})
                        bucket["observations"] += 1
                        eval_total += 1
                        if int_flag:
                            eval_interceptions += 1
                            bucket["interceptions"] += 1
                        if esc_flag:
                            eval_escapes += 1
                            bucket["escapes"] += 1
                        for skill in skills:
                            entry = observe(skill, "events", row["_observed_at"])
                            entry["eval_interceptions"] += int_flag
                            entry["eval_escapes"] += esc_flag
                if is_7d and event in {"REVIEW_FAIL", "RULING", "CHANGE_REQUEST"}:
                    note = str(row.get("note_preview") or "").upper()
                    for skill in skills:
                        entry = observe(skill, "events", row["_observed_at"])
                        entry["spec_gap_escapes" if "SPEC_GAP" in note or event in {"RULING", "CHANGE_REQUEST"} else "impl_bug_escapes"] += 1
                if is_7d and (event or has_eval):
                    observed.append(row["_observed_at"])

    if cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            files = _timed_rows(conn, "session_files", 30)
            turns = _timed_rows(conn, "turns", 30)
            cp_session_usage: dict[str, tuple[int, int, int, int]] = {}
            if _table_columns(conn, "assistant_usage_events"):
                for u_row in _timed_rows(conn, "assistant_usage_events", 30):
                    u_sid = str(u_row.get("session_id") or "")
                    if not u_sid:
                        continue
                    prev_in, prev_out, prev_cr, prev_cnt = cp_session_usage.get(u_sid, (0, 0, 0, 0))
                    cp_session_usage[u_sid] = (
                        prev_in + int(u_row.get("input_tokens") or 0),
                        prev_out + int(u_row.get("output_tokens") or 0),
                        prev_cr + int(u_row.get("cache_read_tokens") or 0),
                        prev_cnt + 1,
                    )
            pending_edits: dict[str, int] = {}
            for row in files + turns:
                sid = str(row.get("session_id") or "")
                stamp = row["_observed_at"]
                text = str(row.get("file_path") or "") if "file_path" in row else f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                matched = _extract_skill_names(text, known_skills)
                ref_matches = list(_REF_PATH_RE.finditer(text))
                script_matches = list(_SCRIPT_PATH_RE.finditer(text))
                matched.update(match.group("skill") for match in ref_matches)
                matched.update(match.group("skill") for match in script_matches)
                matched_norm = {_norm_skill(s) for s in matched}
                if "file_path" not in row:
                    matched_norm.update(
                        _attribute_basename_scripts_in_text(
                            "copilot",
                            sid,
                            text,
                            matched_norm,
                            stamp,
                            row.get("exit_code"),
                            row.get("duration_ms"),
                        )
                    )
                for skill in matched_norm:
                    norm_sk = _norm_skill(skill)
                    if sid:
                        seen_turn_sigs_30d.add((sid, f"turn-{row.get('turn_index', '')}", norm_sk))
                    _bump_channel(norm_sk, "copilot")
                    record = session_for("copilot", sid, norm_sk, stamp, reference_source=True)
                    if "input_tokens" in row:
                        in_tok = int(row.get("input_tokens") or 0)
                        cr_tok = int(row.get("cache_read") or 0)
                        cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
                        cache_bucket["in"] += in_tok
                        cache_bucket["cache_read"] += cr_tok
                        record["tokens"] += sum(int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
                        record["token_seen"] = True
                    observe(norm_sk, "usage", stamp)
                for match in ref_matches:
                    add_reference("copilot", sid, match.group("skill"), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp, row.get("byte_len"))
                for match in script_matches:
                    add_script("copilot", sid, match.group("skill"), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp, row.get("exit_code"), row.get("duration_ms"))
                tool_name = str(row.get("tool_name") or "").lower()
                if tool_name in {"edit", "write", "multiedit", "apply_patch", "create"}:
                    if matched_norm:
                        for skill in matched_norm:
                            session_for("copilot", sid, skill, stamp, reference_source=True)["edits"] += 1
                    else:
                        pending_edits[sid] = pending_edits.get(sid, 0) + 1
                observed.append(stamp)
            for (source, sid, skill), record in session_records.items():
                if source == "copilot" and pending_edits.get(sid):
                    record["edits"] += pending_edits[sid]
                if source == "copilot" and not record["token_seen"] and sid in cp_session_usage:
                    u_in, u_out, u_cr, u_cnt = cp_session_usage[sid]
                    if u_in + u_out + u_cr > 0:
                        avg_evt_tokens = max(1, (u_in + u_out + u_cr) // max(1, u_cnt))
                        record["tokens"] = avg_evt_tokens
                        record["token_seen"] = True
                        cache_b = per_skill_cache.setdefault(skill, {"in": 0, "cache_read": 0})
                        cache_b["in"] += u_in
                        cache_b["cache_read"] += u_cr
            for record in session_records.values():
                if record["source"] == "copilot" and record["edits"] >= 4:
                    observe(record["skill"], "churn", record["stamp"])["edit_churn_count"] += record["edits"]

    # Ingest deduplicated multi-device iCloud telemetry shards (*-skill-usage.jsonl)
    for s_row in shard_records_30d:
        kind = str(s_row.get("kind") or "")
        if kind not in {"skill_turn", "script_call", "reference_hit"}:
            continue
        raw_sk = str(s_row.get("skill") or "").strip()
        if not raw_sk:
            continue
        norm_sk = _norm_skill(raw_sk)
        sid = str(s_row.get("session_id") or "")
        tid = str(s_row.get("turn_id") or s_row.get("call_uid") or s_row.get("hit_uid") or "")
        host_tag = str(s_row.get("host") or "shard").strip()
        shard_src = f"shard:{host_tag}"
        stamp = s_row["_observed_at"]
        ch = str(s_row.get("channel") or "copilot").strip().lower()
        if ch not in {"claude", "copilot", "codex", "hermes", "gtt"}:
            ch = "copilot"

        if kind == "skill_turn":
            if (sid or tid) and (sid, tid, norm_sk) in seen_turn_sigs_30d:
                continue
            if sid or tid:
                seen_turn_sigs_30d.add((sid, tid, norm_sk))
            _bump_channel(norm_sk, ch)
            in_tok = int(s_row.get("input_tokens") or s_row.get("in") or 0)
            out_tok = int(s_row.get("output_tokens") or s_row.get("out") or 0)
            cr_tok = int(s_row.get("cache_read") or 0)
            cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
            cache_bucket["in"] += in_tok
            cache_bucket["cache_read"] += cr_tok
            record = session_for(shard_src, sid or tid, norm_sk, stamp, reference_source=True)
            tools = _json_list(s_row.get("tool_calls"))
            record["read_proxy"] += sum(str(t).lower() in {"read", "view", "read_file"} for t in tools)
            record["edits"] += sum(str(t).lower() in {"edit", "write", "multiedit", "apply_patch", "create"} for t in tools)
            if in_tok + out_tok + cr_tok > 0:
                record["tokens"] += in_tok + out_tok + cr_tok
                record["token_seen"] = True
            observed.append(stamp)
            observe(norm_sk, "usage", stamp)
        elif kind == "reference_hit":
            ref_p = str(s_row.get("reference_path") or "").strip()
            rel_norm = _normalize_skill_rel_path(ref_p, prefix="references") or ref_p
            if sid and (sid, norm_sk, rel_norm) in seen_ref_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_reference(shard_src, sid or tid, norm_sk, ref_p, stamp, s_row.get("byte_len"))
        elif kind == "script_call":
            sc_p = str(s_row.get("script_path") or "").strip()
            rel_sc = _normalize_skill_rel_path(sc_p, prefix="scripts") or sc_p
            if sid and (sid, norm_sk, rel_sc) in seen_script_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_script(shard_src, sid or tid, norm_sk, sc_p, stamp, s_row.get("exit_code"), s_row.get("duration_ms"))

    for record in session_records.values():
        if not record["loaded"]:
            continue
        skill = record["skill"]
        entry = _ensure_skill(skill)
        entry["session_count"] += 1
        entry["loads_30d"] += 1
        entry["loads_7d"] += record["stamp"] >= start7
        entry["total_tokens"] += record["tokens"]
        entry["token_session_count"] += int(record["token_seen"])
        refs = record["refs"]
        ref_count = len(refs) if record["reference_source"] else record["read_proxy"]
        entry["reference_hit_count"] += len(refs)
        entry["reference_bytes"] += sum(refs.values())
        entry["l0_only_sessions" if ref_count == 0 else "l1_selective_sessions" if ref_count <= 2 else "l1_greedy_sessions"] += 1
        if record["scripts"]:
            entry["script_touch_sessions"] += 1
            entry["script_execs"] += len(record["scripts"])
            entry["script_failures"] += sum(call[2] != 0 for call in record["scripts"])
            entry["script_durations_ms"].extend(call[3] for call in record["scripts"])
        if not refs and not record["scripts"] and record["edits"] == 0:
            entry["empty_load_sessions"] += 1
        if record["edits"] >= 4:
            entry["edit_churn_count"] += record["edits"]

    s2_gate["eval_effect"] = {
        "interceptions_7d": eval_interceptions,
        "escapes_7d": eval_escapes,
        "interception_rate": round(eval_interceptions / (eval_interceptions + eval_escapes), 4) if eval_interceptions + eval_escapes else None,
        "by_eval_case": eval_by_case,
    }
    source_meta = _freshness(observed, warehouse_present, 30)
    source_as_of = source_meta["source_as_of"]
    start7, end7 = _window_bounds()
    root_stamps = [stamp for stamp in observed if start7 <= stamp < end7]
    root_meta = _freshness(root_stamps, warehouse_present)

    # 3. Synthesize S1, S2, S3, S4, S6, S7, S8, S9, S10
    s1_pins = _compute_s1_pin_drift(d_root)

    # S3: Skill Health & Eval Growth
    budget_passed = sum(1 for m in inventory.values() if m["budget_ok"])
    top_l0 = sorted(inventory.values(), key=lambda m: (-m["l0_lines"], m["skill"]))[:3]
    top_l1 = sorted(inventory.values(), key=lambda m: (-m["max_l1_lines"], m["skill"]))[:3]
    total_eval_cases = sum(m["eval_case_count"] for m in inventory.values())
    weekly_eval_delta = sum(m["eval_case_weekly_delta"] for m in inventory.values())
    s3_health = {
        "total_skills": len(inventory),
        "audit_pass_count": budget_passed,
        "audit_pass_rate": round(budget_passed / len(inventory), 4) if inventory else None,
        "top3_l0_watermarks": [
            {"skill": m["skill"], "l0_lines": m["l0_lines"], "l0_cap": m["l0_cap"]}
            for m in top_l0
        ],
        "top3_l1_watermarks": [
            {"skill": m["skill"], "max_l1_lines": m["max_l1_lines"], "l1_cap": m["l1_cap"]}
            for m in top_l1
        ],
        "eval_case_total": total_eval_cases,
        "eval_case_weekly_delta": weekly_eval_delta,
    }

    s11_context, s12_tools, burn_proposals = _compute_s11_s12_context_and_tools(
        wh,
        magpie_usage=magpie_usage,
        copilot_db=cp,
        dsh_sessions=dsh_sessions,
        telemetry_shards_dir=shards_dir,
    )

    for l2_sk, l2_map in l2_channels.items():
        if sum(int(v or 0) for v in l2_map.values()) > 0:
            _ensure_skill(l2_sk)

    # Build per-skill ROI table for S4, S6, S7, S8, S9, S10
    skill_rows: list[dict[str, Any]] = []
    idle_skills_30d: list[str] = []
    for s_name, st in sorted(stats.items()):
        l2_ch_map = l2_channels.get(s_name, {})
        l2_sum_30d = sum(int(v or 0) for v in l2_ch_map.values())
        loads = max(st["loads_30d"], st["loads_7d"], st["session_count"], l2_sum_30d)
        if loads == 0 and s_name in known_skills:
            idle_skills_30d.append(s_name)

        meta_inv = inventory.get(s_name, {})
        l0_lines = int(meta_inv.get("l0_lines") or 0)
        max_l1_lines = int(meta_inv.get("max_l1_lines") or 0)
        fm_tok_est = int(meta_inv.get("frontmatter_tokens_est") or 1)
        body_tok_est = int(meta_inv.get("body_tokens_est") or max(1, l0_lines * 12))

        r1_tot = st["r1_total"]
        if r1_tot > 0:
            r1_pass_rate: float | None = round(st["r1_pass"] / r1_tot, 4)
        elif loads > 0:
            clean_loads = max(
                0,
                loads
                - st["impl_bug_escapes"]
                - st["spec_gap_escapes"]
                - min(loads, st["script_failures"])
                - (1 if st["edit_churn_count"] >= 4 else 0),
            )
            r1_pass_rate = round(max(0.05, clean_loads / loads), 4)
        else:
            r1_pass_rate = None

        obs_script_calls_30d = sum(
            int(sc_info.get("calls") or 0)
            for sc_path, sc_info in per_skill_scripts.get(s_name, {}).items()
            if _is_valid_skill_script_rel_path(sc_path)
        )
        script_execs = max(st["script_execs"], obs_script_calls_30d)
        has_valid_scripts = bool(meta_inv.get("script_entries")) or bool(st["has_scripts"])
        if has_valid_scripts and loads > 0:
            practiced_sessions = min(loads, max(st["script_touch_sessions"], min(loads, script_execs)))
            script_ratio: float | None = round(practiced_sessions / loads, 4)
            read_only_unpracticed_rate: float | None = round(max(0.0, 1.0 - script_ratio), 4)
        elif not has_valid_scripts and loads > 0:
            script_ratio = None
            read_only_unpracticed_rate = 0.0
        else:
            script_ratio = None
            read_only_unpracticed_rate = None

        empty_load_rate = round(min(1.0, st["empty_load_sessions"] / loads), 4) if loads > 0 else None
        script_durations = [float(value) for value in st["script_durations_ms"]]
        script_avg_ms = round(sum(script_durations) / len(script_durations), 2) if script_durations else None
        script_p95_ms = _p95(script_durations)

        f_tot = max(1, st["l0_only_sessions"] + st["l1_selective_sessions"] + st["l1_greedy_sessions"])
        l0_rate = round(st["l0_only_sessions"] / f_tot, 4) if loads > 0 else None
        l1_sel_rate = round(st["l1_selective_sessions"] / f_tot, 4) if loads > 0 else None
        l1_greedy_rate = round(st["l1_greedy_sessions"] / f_tot, 4) if loads > 0 else None

        if st["token_session_count"] > 0 and st["total_tokens"] > 0:
            avg_tokens: int | None = max(1, int(st["total_tokens"] / st["token_session_count"]))
        elif loads > 0:
            avg_hit_ref_tokens = (
                int((st["reference_bytes"] / max(1, st["reference_hit_count"])) // 4)
                if st["reference_hit_count"] > 0 and st["reference_bytes"] > 0
                else 0
            )
            script_proxy_tokens = 320 if script_execs > 0 else 0
            avg_tokens = max(1, fm_tok_est + body_tok_est + avg_hit_ref_tokens + script_proxy_tokens + 1200)
        else:
            avg_tokens = None

        cache_b = per_skill_cache.get(s_name, {})
        tot_in_sk = int(cache_b.get("in") or 0)
        tot_cr_sk = int(cache_b.get("cache_read") or 0)
        sk_kv_cache_pct = (
            round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1)
            if (tot_in_sk + tot_cr_sk) > 0
            else s11_context.get("kv_cache_hit_pct")
        )
        cache_bonus = ((float(sk_kv_cache_pct) / 100.0) * 0.25) if sk_kv_cache_pct is not None else 0.0
        script_bonus = min(0.2, 0.05 * min(4, script_execs)) if has_valid_scripts and script_execs > 0 else 0.0

        # S10 formula: Skill ROI = (R1_Pass_Rate + Bonuses) / Normalized(Tokens + Churn + Uncalled_Script_Penalty)
        norm_tokens = min(2.0, max(0.2, avg_tokens / 10000.0)) if avg_tokens is not None else None
        norm_churn = min(2.0, st["edit_churn_count"] * 0.15 + st["retry_commands"] * 0.1)
        uncalled_penalty = (read_only_unpracticed_rate or 0.0) * 0.6 if has_valid_scripts and loads > 0 else 0.0
        escape_penalty = (st["impl_bug_escapes"] * 0.2) + (st["spec_gap_escapes"] * 0.15)
        denom = max(0.25, 0.5 + norm_tokens + norm_churn + uncalled_penalty + escape_penalty) if norm_tokens is not None else None
        roi_score = (
            round((r1_pass_rate + cache_bonus + script_bonus) / denom, 4)
            if loads > 0 and denom is not None and r1_pass_rate is not None
            else (round(r1_pass_rate / denom, 4) if denom is not None and r1_pass_rate is not None else None)
        )

        rework_rounds = int(st.get("rework_rounds") or 0)
        repair_denom = max(1, r1_tot, loads)
        repair_numer = st["impl_bug_escapes"] + st["spec_gap_escapes"] + rework_rounds + (1 if st["edit_churn_count"] >= 4 else 0)
        post_invocation_repair_loop_rate = round(min(1.0, repair_numer / repair_denom), 4) if (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0) else None

        diagnoses: list[str] = []
        if has_valid_scripts and loads > 0 and (read_only_unpracticed_rate or 0.0) >= 0.5:
            diagnoses.append(f"光读不练率 {(read_only_unpracticed_rate or 0.0)*100:.0f}%（脚本未充分触发）")
        if st["impl_bug_escapes"] > 0:
            diagnoses.append(f"[IMPL_BUG] 漏网 {st['impl_bug_escapes']} 次（建议 Valve 1 下沉脚本检查）")
        if st["spec_gap_escapes"] > 0:
            diagnoses.append(f"[SPEC_GAP] 盲区 {st['spec_gap_escapes']} 次（建议 add-case --id auto 补齐抽屉）")
        if st["edit_churn_count"] >= 4:
            diagnoses.append(f"挂载后文件反复擦写 {st['edit_churn_count']} 次")
        if l1_greedy_rate is not None and l1_greedy_rate > 0.25:
            diagnoses.append(f"L1 全量暴读违规率 {l1_greedy_rate*100:.0f}%")
        if (post_invocation_repair_loop_rate or 0) > 0.25 and not diagnoses:
            diagnoses.append(f"调用后返工率 {post_invocation_repair_loop_rate*100:.0f}% 超阈值")

        if (post_invocation_repair_loop_rate or 0) > 0.25 or st["impl_bug_escapes"] > 0 or st["spec_gap_escapes"] > 0 or st["edit_churn_count"] >= 4:
            diagnostic_status = "high_repair_loop"
        elif loads == 0 and observed and (l0_lines > 120 or max_l1_lines > 140 or not meta_inv.get("budget_ok", True)):
            diagnostic_status = "zero_trigger_bloat"
            diagnoses.append(f"30d 零触发且 L0/L1 体积偏大 (L0={l0_lines}, max_L1={max_l1_lines})")
        elif loads == 0 and s_name in known_skills:
            diagnostic_status = "dormant"
        elif diagnoses:
            diagnostic_status = "warn"
        else:
            diagnostic_status = "healthy" if loads > 0 else "unknown"

        kinds = populations.get(s_name, {})
        stamps = [stamp for values in kinds.values() for stamp in values]
        row_meta = _freshness(stamps, warehouse_present, 30)
        skill_rows.append({
            **row_meta, "estimated": True,
            "estimation_method": "session/tool and goal-name skill attribution proxies",
            "r1_freshness": _freshness(kinds.get("reviews", []), wh.is_file()),
            "skill": s_name,
            "loads_7d": st["loads_7d"],
            "loads_30d": loads,
            "r1_pass_rate": r1_pass_rate,
            "r1_total": r1_tot,
            "rework_rounds": rework_rounds,
            "post_invocation_repair_loop_rate": post_invocation_repair_loop_rate,
            "has_scripts": has_valid_scripts,
            "script_execs": script_execs,
            "script_failures": st["script_failures"],
            "script_success_rate": round(max(0, script_execs - st["script_failures"]) / script_execs, 4) if script_execs else None,
            "script_touch_sessions": st["script_touch_sessions"],
            "script_touch_rate": script_ratio,
            "unpracticed_rate": read_only_unpracticed_rate,
            "script_avg_ms": script_avg_ms,
            "script_p95_ms": script_p95_ms,
            "reference_hit_count": st["reference_hit_count"],
            "reference_bytes": st["reference_bytes"],
            "empty_load_sessions": st["empty_load_sessions"],
            "empty_load_rate": empty_load_rate,
            "eval_interceptions": st["eval_interceptions"],
            "eval_escapes": st["eval_escapes"],
            "l0_only_rate": l0_rate,
            "l1_selective_rate": l1_sel_rate,
            "l1_greedy_violation_rate": l1_greedy_rate,
            "l0_only_sessions": st["l0_only_sessions"],
            "l1_selective_sessions": st["l1_selective_sessions"],
            "l1_greedy_sessions": st["l1_greedy_sessions"],
            "avg_context_tokens": avg_tokens,
            "edit_churn_count": st["edit_churn_count"],
            "retry_commands": st["retry_commands"],
            "impl_bug_escapes": st["impl_bug_escapes"],
            "spec_gap_escapes": st["spec_gap_escapes"],
            "roi_score": roi_score,
            "diagnosis": "；".join(diagnoses) if diagnoses else ("健康达标" if loads > 0 else "unknown / 观测不足"),
            "diagnostic_status": diagnostic_status,
            "needs_optimization": bool(diagnoses) and (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0 or diagnostic_status == "zero_trigger_bloat"),
        })

    active_rows = [r for r in skill_rows if r["loads_30d"] > 0 or r["r1_total"] > 0 or r["edit_churn_count"] > 0]
    actionable_rows = [r for r in skill_rows if r["needs_optimization"]]
    low_roi_candidates = sorted(
        actionable_rows,
        key=lambda r: (r["roi_score"] is None, r["roi_score"] if r["roi_score"] is not None else float("inf"), -(r["post_invocation_repair_loop_rate"] or 0), -r["impl_bug_escapes"], -(r["unpracticed_rate"] or 0.0)),
    )[:3]

    recommendations: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for cand in low_roi_candidates:
        sk = cand["skill"]
        roi_label = f"{cand['roi_score']:.2f}" if cand["roi_score"] is not None else "unknown"
        r1_label = f"{cand['r1_pass_rate'] * 100:.1f}%" if cand["r1_pass_rate"] is not None else "unknown"
        rec_item = {
            "id": f"skill-roi-{sk}",
            "ref": f"retro://skill-roi/{sk}",
            "legacy_ref": f"skill-roi://{sk}",
            "legacy_refs": [f"skill-roi://{sk}"],
            "skill": sk,
            "metric_id": f"skill-roi-{sk}",
            "diagnostic_status": cand["diagnostic_status"],
            "post_invocation_repair_loop_rate": cand["post_invocation_repair_loop_rate"],
            "action": f"Run skill_contract.py add-case --skill {sk} --id auto and sink repeated repair checks into scripts/" if cand["diagnostic_status"] == "high_repair_loop" else f"Audit {sk} L0/L1 budget and wire deterministic script triggers",
            "scope": "personal",
            "title": f"[Skill ROI] 优化 {sk}（ROI={cand['roi_score']:.2f}，R1通过={cand['r1_pass_rate']*100:.0f}%）：{cand['diagnosis'][:36]}" if cand["roi_score"] is not None and cand["r1_pass_rate"] is not None else f"[Skill ROI] 补齐 {sk} 的观测覆盖：{cand['diagnosis'][:36]}",
            "detail": (
                f"skill={sk} | roi_score={roi_label} | "
                f"r1_pass_rate={r1_label} | "
                f"unpracticed_rate={_percent_label(cand['unpracticed_rate'])} | "
                f"edit_churn={cand['edit_churn_count']} | "
                f"impl_bug={cand['impl_bug_escapes']} | spec_gap={cand['spec_gap_escapes']} | "
                f"diagnosis={cand['diagnosis']}"
            ),
            "priority": "P1",
            **{key: cand[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                         "sample_size", "coverage_status", "estimated", "estimation_method")},
        }
        recommendations.append(rec_item)
        proposals.append({**rec_item, "ref": rec_item["legacy_ref"], "priority": "high"})

    for bp in burn_proposals[:2]:
        bp_enriched = dict(bp)
        bp_enriched.setdefault("scope", "personal")
        bp_enriched.setdefault("action", "Add --head-limit / view_range or sink filtering into deterministic script")
        proposals.append(bp_enriched)
        if not any(r["id"] == bp_enriched["id"] for r in recommendations):
            recommendations.append(bp_enriched)

    total_sample_size = max(len(root_stamps), int(s11_context["sample_size"] or 0))
    cov_status = "healthy" if total_sample_size > 0 else ("unknown" if warehouse_present else "missing")
    observed_as_of = [value for value in (root_meta["source_as_of"], s11_context["source_as_of"]) if value]
    source_as_of = max(observed_as_of) if observed_as_of else None
    inventory_paths = [path for meta in inventory.values() for path in
                       [Path(meta["path"]) / "SKILL.md", *(Path(meta["path"]) / "references").rglob("*.md"),
                        *(Path(meta["path"]) / "references").rglob("*.json")]]
    s3_health.update(_snapshot_freshness(inventory_paths, len(inventory)))
    for key in ("top3_l0_watermarks", "top3_l1_watermarks"):
        for row in s3_health[key]:
            row.update(_snapshot_freshness([Path(inventory[row["skill"]]["path"]) / "SKILL.md"], 1))

    for p_item in proposals:
        p_item.setdefault("window_cst", "7d")
        p_item.setdefault("source_as_of", source_as_of)
        p_item.setdefault("sample_size", total_sample_size)
        p_item.setdefault("coverage_status", cov_status)
        p_item.setdefault("window_start", source_meta["window_start"])
        p_item.setdefault("window_end", source_meta["window_end"])

    skill_details = _collect_skill_details(
        inventory=inventory,
        skill_rows=skill_rows,
        per_skill_channels=per_skill_channels,
        l2_channels=l2_channels,
        per_skill_cache=per_skill_cache,
        s11_context=s11_context,
        per_skill_refs=per_skill_refs,
        per_skill_scripts=per_skill_scripts,
        per_skill_eval_cases=per_skill_eval_cases,
        warehouse_present=warehouse_present,
    )

    try:
        mirror_skills_to_icloud(skill_details, icloud_sync_dir=icloud_sync_dir)
    except Exception:
        pass
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    auto_proposed = _queue_recommendations(proposals, prop_path) if auto_propose else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0
    report_payload = {
        "ok": True,
        "generated_at": generated_at,
        "query_id": "retro/skill_roi_diagnostics",
        "wall_clock_date": wall_clock_date,
        "staleness_days": staleness_days,
        "window_anchor": window_anchor,
        "window_cst": "7d",
        "window_start": root_meta["window_start"] or s11_context["window_start"],
        "window_end": root_meta["window_end"] or s11_context["window_end"],
        "source_as_of": source_as_of,
        "sample_size": total_sample_size,
        "coverage_status": cov_status,
        "skill_details": skill_details,
        "S1_pin_drift": s1_pins,
        "S2_gate_catch": s2_gate,
        "S3_skill_health": s3_health,
        "S4_usage_and_r1": {
            **source_meta,
            "active_skills_count": len(active_rows),
            "idle_skills_30d": idle_skills_30d,
            "empty_load_sessions": sum(int(r.get("empty_load_sessions") or 0) for r in active_rows),
            "empty_load_rate": round(
                sum(int(r.get("empty_load_sessions") or 0) for r in active_rows)
                / max(1, sum(int(r.get("loads_30d") or 0) for r in active_rows)), 4
            ) if active_rows else None,
            "top_used_skills": sorted(skill_rows, key=lambda r: (-r["loads_30d"], r["skill"]))[:8],
        },
        "S6_load_vs_script": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "loads_30d": r["loads_30d"],
                "script_execs": r["script_execs"],
                "script_touch_sessions": r["script_touch_sessions"],
                "script_touch_rate": r["script_touch_rate"],
                "unpracticed_rate": r["unpracticed_rate"],
                "script_success_rate": r["script_success_rate"],
                "script_avg_ms": r["script_avg_ms"],
                "script_p95_ms": r["script_p95_ms"],
            }
            for r in active_rows
            if r["has_scripts"]
        ],
        "S7_disclosure_funnel": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "l0_only_rate": r["l0_only_rate"],
                "l1_selective_rate": r["l1_selective_rate"],
                "l1_greedy_violation_rate": r["l1_greedy_violation_rate"],
                "l0_only_sessions": r["l0_only_sessions"],
                "l1_selective_sessions": r["l1_selective_sessions"],
                "l1_greedy_sessions": r["l1_greedy_sessions"],
                "reference_hit_count": r["reference_hit_count"],
                "reference_bytes": r["reference_bytes"],
                "avg_context_tokens": r["avg_context_tokens"],
            }
            for r in active_rows
        ],
        "S8_post_skill_churn": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('churn',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "edit_churn_count": r["edit_churn_count"],
                "retry_commands": r["retry_commands"],
            }
            for r in active_rows
            if r["edit_churn_count"] > 0 or r["retry_commands"] > 0
        ],
        "S9_escape_attribution": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('reviews', 'events') for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 7),
                "skill": r["skill"],
                "impl_bug_escapes": r["impl_bug_escapes"],
                "spec_gap_escapes": r["spec_gap_escapes"],
                "eval_interceptions": r["eval_interceptions"],
                "eval_escapes": r["eval_escapes"],
            }
            for r in active_rows
            if r["impl_bug_escapes"] > 0 or r["spec_gap_escapes"] > 0 or r["eval_interceptions"] > 0 or r["eval_escapes"] > 0
        ],
        "S10_skill_roi_ranking": sorted(active_rows, key=lambda r: (r["roi_score"] is None, r["roi_score"] if r["roi_score"] is not None else float("inf"), r["skill"])),
        "S11_context_composition": s11_context,
        "S12_tool_context_burn": s12_tools,
        "recommendations": recommendations,
        "low_roi_proposals": proposals,
        "auto_proposed": auto_proposed,
        "proposals_written": proposals_written,
        "proposals_skipped": proposals_skipped,
    }
    if should_mirror_roi:
        _mirror_skills_roi_snapshot_to_icloud(report_payload, icloud_roi_path=effective_roi_path)
    return report_payload


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
    """Alias for compute_skill_roi_diagnostics()."""
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


def _mirror_skills_roi_snapshot_to_icloud(
    report: dict[str, Any],
    *,
    icloud_roi_path: Path | None = None,
    local_snapshot_path: Path | None = None,
) -> Path | None:
    target = icloud_roi_path or DEFAULT_ICLOUD_SKILLS_ROI_PATH
    sync_root = target.parents[2] if len(target.parents) >= 3 else target.parent
    if icloud_roi_path is None and not sync_root.is_dir():
        return None
    local_snap = local_snapshot_path or DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH
    daily: list[dict[str, Any]] = []
    for candidate in (local_snap, target):
        if candidate.is_file():
            try:
                existing = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(existing, dict) and isinstance(existing.get("skills_daily"), list):
                    daily = existing["skills_daily"]
                    break
            except (OSError, json.JSONDecodeError):
                continue
    snapshot_payload: dict[str, Any] = {
        "schema_version": 1,
        "window_cst": report.get("window_cst") or "7d",
        "window_start": report.get("window_start"),
        "window_end": report.get("window_end"),
        "source_as_of": report.get("source_as_of"),
        "sample_size": int(report.get("sample_size") or 0),
        "coverage_status": report.get("coverage_status") or "unknown",
        "diagnostics": report,
        "skills_daily": daily,
    }
    if isinstance(report.get("skill_details"), list):
        snapshot_payload["skill_details"] = report["skill_details"]
    try:
        _write_json(target, snapshot_payload)
        return target
    except OSError:
        return None


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
