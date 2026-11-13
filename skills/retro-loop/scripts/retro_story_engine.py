#!/usr/bin/env python3
"""Deterministic retro/story helpers for evidence validation and promotion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import fcntl
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from typing import Any, Callable
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
_SKILL_NAME_RE = re.compile(
    r"(?:<skill-context\s+name=[\"'](?P<ctx>[A-Za-z0-9_-]+)[\"']"
    r"|/skills/(?P<path>[A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"'](?P<call>[A-Za-z0-9_-]+)[\"'])"
)
_REF_PATH_RE = re.compile(r"/skills/(?P<skill>[A-Za-z0-9_-]+)/references/(?P<ref>[A-Za-z0-9_./-]+)")
_SCRIPT_PATH_RE = re.compile(
    r"/skills/(?P<skill>[A-Za-z0-9_-]+)/scripts/(?P<script>[A-Za-z0-9_.-]+\.(?:py|sh))"
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


def _discover_skill_inventory(
    skills_root: Path | None = None,
    dev_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Scan global user-skills and repo-bound skills for scripts, L0/L1 lines, and EV/DC cases."""
    root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    search_dirs: list[tuple[Path, bool]] = [(root, False)]
    for repo_name in ("AIDash", "VitalStride", "VoxPocket"):
        repo_path = d_root / repo_name
        for rel in (repo_path / ".agents" / "skills", repo_path / "skills"):
            if rel.is_dir():
                search_dirs.append((rel, True))

    inventory: dict[str, dict[str, Any]] = {}
    for base_dir, repo_bound in search_dirs:
        if not base_dir.is_dir():
            continue
        for pkg in sorted(base_dir.iterdir()):
            skill_md = pkg / "SKILL.md"
            if not pkg.is_dir() or not skill_md.is_file():
                continue
            skill_name = pkg.name
            if skill_name in inventory:
                continue
            try:
                l0_lines = len(skill_md.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                l0_lines = 0
            l0_cap = 80 if skill_name in {"my-designer", "design-studio"} else 180
            refs_dir = pkg / "references"
            ref_files: list[str] = []
            max_l1_lines = 0
            eval_cases = 0
            recent_eval_cases = 0
            cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
            if refs_dir.is_dir():
                for ref_p in sorted(refs_dir.rglob("*")):
                    if not ref_p.is_file():
                        continue
                    rel_ref = str(ref_p.relative_to(refs_dir))
                    if ref_p.suffix == ".md":
                        ref_files.append(rel_ref)
                        try:
                            max_l1_lines = max(
                                max_l1_lines,
                                len(ref_p.read_text(encoding="utf-8", errors="ignore").splitlines()),
                            )
                        except OSError:
                            pass
                    elif ref_p.suffix == ".json" and (
                        rel_ref.endswith("eval-cases.json") or rel_ref.endswith("defect-cases/index.json")
                    ):
                        try:
                            doc = json.loads(ref_p.read_text(encoding="utf-8"))
                            for item in doc.get("active_cases", []):
                                if isinstance(item, dict):
                                    eval_cases += 1
                                    reg = str(item.get("registered_at") or doc.get("updated") or "")
                                    if reg >= cutoff_7d:
                                        recent_eval_cases += 1
                        except (OSError, json.JSONDecodeError):
                            pass
            scripts_dir = pkg / "scripts"
            scripts: list[str] = []
            if scripts_dir.is_dir():
                scripts = [
                    sp.name
                    for sp in sorted(scripts_dir.iterdir())
                    if sp.is_file() and sp.suffix in {".py", ".sh", ".ts"} and not sp.name.startswith("test_")
                ]
            budget_ok = l0_lines <= l0_cap and max_l1_lines <= 160
            inventory[skill_name] = {
                "skill": skill_name,
                "path": str(pkg),
                "repo_bound": repo_bound,
                "l0_lines": l0_lines,
                "l0_cap": l0_cap,
                "max_l1_lines": max_l1_lines,
                "l1_cap": 160,
                "budget_ok": budget_ok,
                "reference_files": ref_files,
                "scripts": scripts,
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
    for row in rows:
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
    if not cache_stamps:
        usage = _usage_records(mp)
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
    present = warehouse_db.is_file() or mp.is_file() or cp.is_file()
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


def compute_skill_roi_diagnostics(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    """Compute S1-S4, S6-S10 Skill ROI diagnostics, and S11-S12 Context & Tool Burn."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    cp = copilot_db or DEFAULT_COPILOT_DB
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH

    inventory = _discover_skill_inventory(s_root, d_root)
    known_skills = set(inventory.keys())
    script_to_skill: dict[str, str] = {}
    for s_name, meta in inventory.items():
        for sc in meta.get("scripts", []):
            script_to_skill[sc] = s_name

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
            "has_scripts": bool(meta.get("scripts")),
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
                "has_scripts": False,
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
    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            ci_rows = _timed_rows(conn, "fact_ci_job_run")
            catches = sum(row.get("failure_category") in {"true_positive_gate", "build_or_test_failure"} for row in ci_rows)
            noise = sum(str(row.get("failure_category") or "").startswith("config_drift_") or
                        row.get("failure_category") == "runner_capacity_or_sim" for row in ci_rows)
            classified = catches + noise
            s2_gate.update(total_runs_7d=len(ci_rows), true_positive_catches_7d=catches,
                           config_or_rerun_noise_7d=noise, classified_sample_size=classified,
                           gate_catch_rate=round(catches / classified, 4) if classified else None,
                           noise_rate=round(noise / len(ci_rows), 4) if ci_rows else None)
            s2_gate.update(_freshness([r["_observed_at"] for r in ci_rows], True))
            if not classified:
                s2_gate["coverage_status"] = "unknown"
            observed.extend(row["_observed_at"] for row in ci_rows)
            sessions: dict[str, list[dict[str, Any]]] = {}
            start7, _ = _window_bounds()
            for row in _timed_rows(conn, "fact_turn", 30):
                stamp = row["_observed_at"]
                skills = set(json.loads(row.get("skill_names") or "[]"))
                if row.get("attribution_skill"):
                    skills.add(str(row["attribution_skill"]))
                row["_skills"] = skills
                for skill in skills:
                    entry = observe(skill, "usage", stamp)
                    entry["loads_30d"] += 1
                    entry["loads_7d"] += stamp >= start7
                if skills:
                    sessions.setdefault(str(row.get("session_id") or ""), []).append(row)
                    observed.append(stamp)
            for rows in sessions.values():
                skills = set().union(*(row["_skills"] for row in rows))
                tokens = sum(int(row.get(key) or 0) for row in rows for key in ("input_tokens", "output_tokens", "cache_read"))
                tools = [str(tool).lower() for row in rows for tool in json.loads(row.get("tool_calls") or "[]")]
                read_count = sum(tool in {"read", "view"} for tool in tools)
                for skill in skills:
                    entry = _ensure_skill(skill)
                    entry["session_count"] += 1
                    entry["total_tokens"] += tokens // max(1, len(skills))
                    entry["token_session_count"] += any(row.get(key) is not None for row in rows for key in ("input_tokens", "output_tokens", "cache_read"))
                    # Generic Bash usage is only a script-touch proxy, not an observed script invocation.
                    if any(tool in {"bash", "execute_code", "terminal"} for tool in tools) and entry["has_scripts"]:
                        entry["script_execs"] += 1
                    entry["l0_only_sessions" if read_count <= 1 else "l1_selective_sessions" if read_count <= 3 else "l1_greedy_sessions"] += 1
            for row in _timed_rows(conn, "fact_gtt_task_run"):
                if row.get("role_name") not in {"reviewer", "code_reviewer", "planner_reviewer"}:
                    continue
                goal = str(row.get("goal_id") or "").lower()
                inferred = {"team-lead"}
                for skill, hints in (("my-designer", ("design", "ui", "feel", "glass", "hallmark")),
                                     ("design-studio", ("ingest", "palette", "motion")),
                                     ("retro-loop", ("retro", "telemetry", "roi", "aidash")),
                                     ("knowledge-digest", ("learn", "digest", "blog", "vault")),
                                     ("skill-contract", ("skill", "contract")),
                                     ("m2-dispatcher", ("m2", "herdr", "dispatch"))):
                    if any(hint in goal for hint in hints):
                        inferred.add(skill)
                for skill in inferred:
                    entry = observe(skill, "reviews", row["_observed_at"])
                    if int(row.get("round_no") or 1) == 1:
                        entry["r1_total"] += 1
                        if str(row.get("v5_verdict") or "").upper() in {"PASS", "CONCERNS", "WAIVED"} and row.get("status") != "failed":
                            entry["r1_pass"] += 1
                        else:
                            entry["impl_bug_escapes"] += 1
                    elif int(row.get("round_no") or 1) >= 2:
                        entry["rework_rounds"] += 1
                observed.append(row["_observed_at"])
            for row in _timed_rows(conn, "fact_gtt_event"):
                event = row.get("evt_type")
                if event not in {"REVIEW_FAIL", "RULING", "CHANGE_REQUEST"}:
                    continue
                goal, note = str(row.get("goal_id") or "").lower(), str(row.get("note_preview") or "").upper()
                skill = "my-designer" if any(hint in goal for hint in ("design", "ui", "feel")) else "team-lead"
                entry = observe(skill, "events", row["_observed_at"])
                entry["spec_gap_escapes" if "SPEC_GAP" in note or event in {"RULING", "CHANGE_REQUEST"} else "impl_bug_escapes"] += 1
                observed.append(row["_observed_at"])

    if cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            files = _timed_rows(conn, "session_files", 30)
            turns = _timed_rows(conn, "turns", 30)
            sessions: dict[str, set[str]] = {}
            refs: dict[tuple[str, str], set[str]] = {}
            session_stamps: dict[str, datetime] = {}
            edits: dict[tuple[str, str], int] = {}
            start7, _ = _window_bounds()
            for row in files:
                sid, path = str(row.get("session_id") or ""), str(row.get("file_path") or "")
                stamp = row["_observed_at"]
                matched = {m.group("ctx") or m.group("path") or m.group("call") for m in _SKILL_NAME_RE.finditer(path)}
                matched.update(m.group("skill") for m in _REF_PATH_RE.finditer(path))
                matched.update(m.group("skill") for m in _SCRIPT_PATH_RE.finditer(path))
                for skill in matched & known_skills:
                    observe(skill, "usage", stamp)
                    sessions.setdefault(sid, set()).add(skill)
                session_stamps[sid] = max(session_stamps.get(sid, stamp), stamp)
                for match in _REF_PATH_RE.finditer(path):
                    refs.setdefault((sid, match.group("skill")), set()).add(match.group("ref"))
                for match in _SCRIPT_PATH_RE.finditer(path):
                    _ensure_skill(match.group("skill"))["script_execs"] += 1
                if row.get("tool_name") in {"edit", "apply_patch"}:
                    edits[(sid, path)] = edits.get((sid, path), 0) + 1
                observed.append(stamp)
            for row in turns:
                sid = str(row.get("session_id") or "")
                blob = f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                stamp = row["_observed_at"]
                matched = {m.group("ctx") or m.group("path") or m.group("call") for m in _SKILL_NAME_RE.finditer(blob)}
                for skill in matched:
                    entry = observe(skill, "usage", stamp)
                    entry["loads_7d"] += stamp >= start7
                    entry["loads_30d"] += 1
                    sessions.setdefault(sid, set()).add(entry["skill"])
                session_stamps[sid] = max(session_stamps.get(sid, stamp), stamp)
                observed.append(stamp)
            for (sid, _), count in edits.items():
                if count >= 4:
                    for skill in sessions.get(sid, {"team-lead"}):
                        observe(skill, "churn", session_stamps[sid])["edit_churn_count"] += count
            for sid, skills in sessions.items():
                for skill in skills:
                    entry = _ensure_skill(skill)
                    entry["session_count"] += 1
                    ref_count = len(refs.get((sid, skill), set()))
                    entry["l0_only_sessions" if ref_count == 0 else "l1_selective_sessions" if ref_count <= 2 else "l1_greedy_sessions"] += 1
    source_meta = _freshness(observed, wh.is_file() or cp.is_file(), 30)
    source_as_of = source_meta["source_as_of"]
    start7, end7 = _window_bounds()
    root_stamps = [stamp for stamp in observed if start7 <= stamp < end7]
    root_meta = _freshness(root_stamps, wh.is_file() or cp.is_file())

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

    # Build per-skill ROI table for S4, S6, S7, S8, S9, S10
    skill_rows: list[dict[str, Any]] = []
    idle_skills_30d: list[str] = []
    for s_name, st in sorted(stats.items()):
        loads = max(st["loads_30d"], st["loads_7d"], st["session_count"])
        if loads == 0 and s_name in known_skills:
            idle_skills_30d.append(s_name)
        r1_tot = st["r1_total"]
        r1_pass_rate = round(st["r1_pass"] / r1_tot, 4) if r1_tot > 0 else None
        script_execs = st["script_execs"]
        script_ratio = round(min(1.0, script_execs / loads), 4) if st["has_scripts"] and loads > 0 else None
        read_only_unpracticed_rate = round(1.0 - script_ratio, 4) if script_ratio is not None else None

        f_tot = max(1, st["l0_only_sessions"] + st["l1_selective_sessions"] + st["l1_greedy_sessions"])
        l0_rate = round(st["l0_only_sessions"] / f_tot, 4) if loads > 0 else None
        l1_sel_rate = round(st["l1_selective_sessions"] / f_tot, 4) if loads > 0 else None
        l1_greedy_rate = round(st["l1_greedy_sessions"] / f_tot, 4) if loads > 0 else None
        avg_tokens = int(st["total_tokens"] / st["token_session_count"]) if st["token_session_count"] > 0 else None

        # S10 formula: Skill ROI = R1_Pass_Rate / Normalized(Tokens + Churn + Uncalled_Script_Penalty)
        norm_tokens = min(2.0, max(0.2, avg_tokens / 10000.0)) if avg_tokens is not None else None
        norm_churn = min(2.0, st["edit_churn_count"] * 0.15 + st["retry_commands"] * 0.1)
        uncalled_penalty = read_only_unpracticed_rate * 0.6 if st["has_scripts"] and loads > 0 else 0.0
        escape_penalty = (st["impl_bug_escapes"] * 0.2) + (st["spec_gap_escapes"] * 0.15)
        denom = max(0.25, 0.5 + norm_tokens + norm_churn + uncalled_penalty + escape_penalty) if norm_tokens is not None else None
        roi_score = round(r1_pass_rate / denom, 4) if denom is not None and r1_pass_rate is not None else None

        rework_rounds = int(st.get("rework_rounds") or 0)
        repair_denom = max(1, r1_tot, loads)
        repair_numer = st["impl_bug_escapes"] + st["spec_gap_escapes"] + rework_rounds + (1 if st["edit_churn_count"] >= 4 else 0)
        post_invocation_repair_loop_rate = round(min(1.0, repair_numer / repair_denom), 4) if (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0) else None

        meta_inv = inventory.get(s_name, {})
        l0_lines = int(meta_inv.get("l0_lines") or 0)
        max_l1_lines = int(meta_inv.get("max_l1_lines") or 0)

        diagnoses: list[str] = []
        if st["has_scripts"] and loads > 0 and read_only_unpracticed_rate >= 0.5:
            diagnoses.append(f"光读不练率 {read_only_unpracticed_rate*100:.0f}%（脚本未充分触发）")
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
            diagnostic_status = "healthy" if loads and r1_tot else "unknown"

        kinds = populations.get(s_name, {})
        stamps = [stamp for values in kinds.values() for stamp in values]
        row_meta = _freshness(stamps, wh.is_file() or cp.is_file(), 30)
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
            "has_scripts": st["has_scripts"],
            "script_execs": script_execs,
            "script_failures": st["script_failures"],
            "script_touch_rate": script_ratio,
            "unpracticed_rate": read_only_unpracticed_rate,
            "l0_only_rate": l0_rate,
            "l1_selective_rate": l1_sel_rate,
            "l1_greedy_violation_rate": l1_greedy_rate,
            "avg_context_tokens": avg_tokens,
            "edit_churn_count": st["edit_churn_count"],
            "retry_commands": st["retry_commands"],
            "impl_bug_escapes": st["impl_bug_escapes"],
            "spec_gap_escapes": st["spec_gap_escapes"],
            "roi_score": roi_score,
            "diagnosis": "；".join(diagnoses) if diagnoses else ("健康达标" if loads and r1_tot else "unknown / 观测不足"),
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

    s11_context, s12_tools, burn_proposals = _compute_s11_s12_context_and_tools(
        wh,
        magpie_usage=magpie_usage,
        copilot_db=cp,
        dsh_sessions=dsh_sessions,
    )
    for bp in burn_proposals[:2]:
        bp_enriched = dict(bp)
        bp_enriched.setdefault("scope", "personal")
        bp_enriched.setdefault("action", "Add --head-limit / view_range or sink filtering into deterministic script")
        proposals.append(bp_enriched)
        if not any(r["id"] == bp_enriched["id"] for r in recommendations):
            recommendations.append(bp_enriched)

    total_sample_size = max(len(root_stamps), int(s11_context["sample_size"] or 0))
    cov_status = "healthy" if total_sample_size > 0 else ("unknown" if wh.is_file() or cp.is_file() else "missing")
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

    auto_proposed = _queue_recommendations(proposals, prop_path) if auto_propose else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0
    return {
        "ok": True,
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
        "S1_pin_drift": s1_pins,
        "S2_gate_catch": s2_gate,
        "S3_skill_health": s3_health,
        "S4_usage_and_r1": {
            **source_meta,
            "active_skills_count": len(active_rows),
            "idle_skills_30d": idle_skills_30d,
            "top_used_skills": sorted(skill_rows, key=lambda r: (-r["loads_30d"], r["skill"]))[:8],
        },
        "S6_load_vs_script": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             wh.is_file() or cp.is_file(), 30),
                "skill": r["skill"],
                "loads_30d": r["loads_30d"],
                "script_execs": r["script_execs"],
                "script_touch_rate": r["script_touch_rate"],
                "unpracticed_rate": r["unpracticed_rate"],
            }
            for r in active_rows
            if r["has_scripts"]
        ],
        "S7_disclosure_funnel": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             wh.is_file() or cp.is_file(), 30),
                "skill": r["skill"],
                "l0_only_rate": r["l0_only_rate"],
                "l1_selective_rate": r["l1_selective_rate"],
                "l1_greedy_violation_rate": r["l1_greedy_violation_rate"],
                "avg_context_tokens": r["avg_context_tokens"],
            }
            for r in active_rows
        ],
        "S8_post_skill_churn": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('churn',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             wh.is_file() or cp.is_file(), 30),
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
                             wh.is_file() or cp.is_file(), 7),
                "skill": r["skill"],
                "impl_bug_escapes": r["impl_bug_escapes"],
                "spec_gap_escapes": r["spec_gap_escapes"],
            }
            for r in active_rows
            if r["impl_bug_escapes"] > 0 or r["spec_gap_escapes"] > 0
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


def _usage_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    start, end = _window_bounds()
    records = []
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
            stamp = next((_observed_at(rec[key]) for key in ("ts", "timestamp", "created_at", "observed_at")
                          if rec.get(key)), None)
            if stamp and start <= stamp < end:
                rec["_observed_at"] = stamp
                records.append(rec)
    return records


def compute_agent_efficiency(
    warehouse_db: Path | None = None,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    """Compute M1-M5 from timestamped observations in seven natural CST days."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    mp = magpie_usage or DEFAULT_MAGPIE_USAGE
    cp = copilot_db or DEFAULT_COPILOT_DB
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

    records = _usage_records(mp)
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
          **_freshness([r["_observed_at"] for r in records + event_rows], mp.is_file() or wh.is_file())}
    total_in = sum(b["in"] for b in group_tokens.values())
    total_cr = sum(b["cache_read"] for b in group_tokens.values())
    overall_cache_hit = round(total_cr / (total_in + total_cr), 4) if total_in + total_cr else None
    m4 = {"overall_cache_hit_rate": overall_cache_hit, "floor_min_cache_hit_rate": 0.75,
          "by_group": {g: round(b["cache_read"] / (b["in"] + b["cache_read"]), 4) if b["in"] + b["cache_read"] else None
                       for g, b in group_tokens.items()},
          "breached": overall_cache_hit is not None and overall_cache_hit < 0.75,
          **_freshness(cache_stamps, mp.is_file())}
    total_tokens = sum(b["in"] + b["out"] + b["cache_read"] for b in group_tokens.values())
    steering_tokens = sum(b["in"] + b["out"] + b["cache_read"] for g, b in group_tokens.items() if "steering" in g)
    share = round(steering_tokens / total_tokens, 4) if total_tokens else None
    first_pass_pr_green_rate = round(sum(row.get("failure_category") in {None, "", "pass"} for row in ci_rows) / len(ci_rows), 4) if ci_rows else None
    m5 = {"steering_token_share": share, "floor_max_steering_share": 0.35,
          "steering_tiny_output_ratio": round(steering_tiny_outputs / steering_reqs, 4) if steering_reqs else None,
          "first_pass_pr_green_rate": first_pass_pr_green_rate,
          "first_pass_pr_green_freshness": _freshness([r["_observed_at"] for r in ci_rows], wh.is_file()),
          "breached": bool((share is not None and share > 0.35) or (first_pass_pr_green_rate is not None and first_pass_pr_green_rate < 0.80)),
          **_freshness(token_stamps + [r["_observed_at"] for r in ci_rows], mp.is_file() or wh.is_file())}
    observed = [r["_observed_at"] for r in reviews + edit_rows + records + event_rows + ci_rows]
    freshness = _freshness(observed, wh.is_file() or cp.is_file() or mp.is_file())
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
        p.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
        p.add_argument("--magpie-usage", type=Path, default=DEFAULT_MAGPIE_USAGE)
        p.add_argument("--copilot-db", type=Path, default=DEFAULT_COPILOT_DB)
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        report = compute_agent_efficiency(
            ns.warehouse_db,
            ns.magpie_usage,
            ns.copilot_db,
        )
        if ns.auto_propose:
            queued = _queue_recommendations(report.get("recommendations", []), ns.proposals_path)
            report.update(auto_proposed=queued, proposals_written=len(queued), proposals_skipped=len(report.get("recommendations", [])) - len(queued))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args_list and args_list[0] == "skill-roi":
        p = argparse.ArgumentParser(prog="retro_story_engine.py skill-roi")
        p.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
        p.add_argument("--copilot-db", type=Path, default=DEFAULT_COPILOT_DB)
        p.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
        p.add_argument("--dev-root", type=Path, default=DEFAULT_DEV_ROOT)
        p.add_argument("--magpie-usage", type=Path, default=DEFAULT_MAGPIE_USAGE)
        p.add_argument("--dsh-sessions", type=Path, default=DEFAULT_DSH_SESSIONS)
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        report = compute_skill_roi_diagnostics(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
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
