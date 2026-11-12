#!/usr/bin/env python3
"""Deterministic retro/story helpers for evidence validation and promotion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
from datetime import date, datetime, timedelta, timezone
import json
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


def _ask_jev(question: dict[str, Any], endpoint: str) -> dict[str, Any]:
    request = Request(endpoint, data=json.dumps(question).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode())


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
                f"[Context Burn] {t_name} ({dom_skill}) 产出 {bloated} 次 >10k tok 膨胀结果："
                "建议加 --head-limit / view_range 或脚本下沉过滤"
            ),
            "detail": (
                f"tool_name={t_name} | dominant_skill={dom_skill} | "
                f"bloated_calls_gt_10k={bloated} | p95_tokens={p95_tok} | "
                f"total_tokens={total_tok} ({share:.1f}%) | "
                "remediation=Add --head-limit, view_range, or sink filtering into deterministic script"
            ),
            "priority": "high",
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

    existing_ids: set[str] = set()
    if prop_path and prop_path.is_file():
        for line in prop_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict) and rec.get("id"):
                    if str(rec.get("status", "pending")).lower() == "pending":
                        existing_ids.add(str(rec["id"]))
                    else:
                        existing_ids.discard(str(rec["id"]))
            except json.JSONDecodeError:
                continue

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
                            if auto_propose and prop_path is not None and cand["id"] not in existing_ids:
                                routed = route_retro_candidate(
                                    cand, mode="observe", proposals_path=prop_path
                                )
                                existing_ids.add(cand["id"])
                                project_loop_a_proposals.append(routed["proposal"])
                            else:
                                project_loop_a_proposals.append(cand)
                    elif query_name == "retro/tool_context_burn":
                        for cand in _build_tool_context_burn_candidates(rows):
                            if auto_propose and prop_path is not None and cand["id"] not in existing_ids:
                                routed = route_retro_candidate(
                                    cand, mode="observe", proposals_path=prop_path
                                )
                                existing_ids.add(cand["id"])
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
        shared_ci_file = r_path / ".shared-ci"
        if shared_ci_file.is_file():
            try:
                txt = shared_ci_file.read_text(encoding="utf-8", errors="ignore").strip()
                m = re.search(r"\b([0-9a-f]{7,40})\b", txt)
                if m:
                    pins["shared-ci"] = m.group(1)
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
                "dependency": dep,
                "pinned_sha": pinned[:12],
                "latest_sha": head_sha[:12],
                "commits_behind": commits_behind if is_outdated else 0,
                "days_behind": days_behind if is_outdated else 0,
                "status": "OUTDATED_PIN" if is_outdated else "UP_TO_DATE",
            })
        consumers.append({
            "repo": repo_name,
            "pins": pins,
            "drift": drift_items,
            "outdated_count": sum(1 for d in drift_items if d["status"] == "OUTDATED_PIN"),
        })
    total_outdated = sum(c["outdated_count"] for c in consumers)
    return {
        "upstreams": {k: v[1][:12] for k, v in upstreams.items()},
        "consumers": consumers,
        "outdated_pin_count": total_outdated,
        "all_up_to_date": total_outdated == 0,
    }


def _compute_s11_s12_context_and_tools(
    warehouse_db: Path,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
    dsh_sessions: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute S11 5-slice context composition & KV cache and S12 tool context burn (>10k alert)."""
    mp = magpie_usage or DEFAULT_MAGPIE_USAGE
    cp = copilot_db or DEFAULT_COPILOT_DB
    dsh_dir = dsh_slices_dir = dsh_sessions or DEFAULT_DSH_SESSIONS

    slice_tokens: dict[str, int] = {
        "System & AGENTS.md 指令链": 0,
        "Skills 目录与 SKILL.md": 0,
        "Tools Schema 定义": 0,
        "Tool Results 工具返回": 0,
        "User/Assistant 对话与思考链": 0,
    }
    total_in = 0
    total_cache_read = 0
    tool_calls_map: dict[str, list[tuple[int, str]]] = {}

    def _norm_tool(raw: str) -> str:
        r = raw.strip().lower()
        if r in {"bash", "execute_code", "terminal", "run_command"}:
            return "bash"
        if r in {"read", "view", "read_file"}:
            return "read/view"
        if r in {"grep", "search", "rg"}:
            return "grep"
        if r in {"edit", "apply_patch", "write", "create"}:
            return "edit/write"
        if "web" in r or "fetch" in r:
            return "web_fetch"
        if r.startswith(("mcp", "linear", "github")):
            return "mcp"
        return r or "other"

    if warehouse_db.is_file():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)) as conn:
                tables = {
                    r[0]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "fact_turn" in tables:
                    rows = conn.execute(
                        """
                        SELECT
                            COALESCE(input_tokens, 0),
                            COALESCE(output_tokens, 0),
                            COALESCE(cache_read, 0),
                            COALESCE(attribution_skill, ''),
                            COALESCE(skill_names, ''),
                            COALESCE(tool_calls, '')
                        FROM fact_turn
                        WHERE cst_day >= date('now', '-7 days')
                        """
                    ).fetchall()
                    for in_t, out_t, cr_t, attr_sk, sk_names, tc_json in rows:
                        in_i, out_i, cr_i = int(in_t or 0), int(out_t or 0), int(cr_t or 0)
                        total_in += in_i
                        total_cache_read += cr_i
                        ctx = in_i + cr_i
                        has_sk = bool(attr_sk or (sk_names and sk_names != "[]"))
                        t_list: list[str] = []
                        if tc_json:
                            try:
                                t_list = [str(x) for x in json.loads(tc_json) if x]
                            except json.JSONDecodeError:
                                t_list = []
                        has_tl = bool(t_list)
                        slice_tokens["System & AGENTS.md 指令链"] += int(
                            ctx * (0.16 if (has_sk and has_tl) else 0.20 if has_tl else 0.28)
                        )
                        slice_tokens["Skills 目录与 SKILL.md"] += int(ctx * (0.18 if has_sk else 0.04))
                        slice_tokens["Tools Schema 定义"] += int(ctx * (0.14 if has_tl else 0.18))
                        slice_tokens["Tool Results 工具返回"] += int(ctx * (0.36 if has_tl else 0.10))
                        slice_tokens["User/Assistant 对话与思考链"] += int(ctx * 0.16) + out_i

                        dom_sk = str(attr_sk or "").strip()
                        if not dom_sk and sk_names:
                            try:
                                parsed_sk = json.loads(sk_names)
                                if parsed_sk and isinstance(parsed_sk[0], str):
                                    dom_sk = parsed_sk[0].strip()
                            except json.JSONDecodeError:
                                pass
                        dom_sk = "team-lead" if dom_sk in {"", "goal-target-task"} else dom_sk
                        if t_list:
                            per_call = int(round((ctx * 0.36) / max(1, len(t_list))))
                            for raw_t in t_list:
                                tool_calls_map.setdefault(_norm_tool(raw_t), []).append((per_call, dom_sk))
        except sqlite3.Error:
            pass

    if mp.is_file() and total_in + total_cache_read == 0:
        try:
            for ln in mp.read_text(encoding="utf-8", errors="ignore").splitlines()[-2000:]:
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                total_in += int(rec.get("in") or 0)
                total_cache_read += int(rec.get("cache_read") or 0)
        except OSError:
            pass

    if cp.is_file():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
                cp_tables = {
                    r[0]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "assistant_usage_events" in cp_tables and total_in + total_cache_read == 0:
                    u_row = conn.execute(
                        "SELECT SUM(COALESCE(input_tokens, 0)), SUM(COALESCE(cache_read_tokens, 0)) FROM assistant_usage_events"
                    ).fetchone()
                    if u_row:
                        total_in += int(u_row[0] or 0)
                        total_cache_read += int(u_row[1] or 0)
        except sqlite3.Error:
            pass

    if dsh_slices_dir.is_dir() and sum(slice_tokens.values()) == 0:
        try:
            for jpath in list(dsh_slices_dir.rglob("*.jsonl"))[:20]:
                sz = jpath.stat().st_size
                est_tok = max(100, sz // 4)
                slice_tokens["System & AGENTS.md 指令链"] += int(est_tok * 0.18)
                slice_tokens["Skills 目录与 SKILL.md"] += int(est_tok * 0.14)
                slice_tokens["Tools Schema 定义"] += int(est_tok * 0.14)
                slice_tokens["Tool Results 工具返回"] += int(est_tok * 0.36)
                slice_tokens["User/Assistant 对话与思考链"] += int(est_tok * 0.18)
        except OSError:
            pass

    total_ctx = sum(slice_tokens.values())
    slices_out = [
        {
            "slice_key": k,
            "tokens": v,
            "share_pct": round(100.0 * v / total_ctx, 1) if total_ctx > 0 else 0.0,
        }
        for k, v in slice_tokens.items()
    ]
    kv_hit_pct = round(100.0 * total_cache_read / (total_in + total_cache_read), 1) if (total_in + total_cache_read) > 0 else 0.0
    s11_data = {
        "slices": slices_out,
        "total_context_tokens": total_ctx,
        "kv_cache_hit_pct": kv_hit_pct,
        "compressed_saved_tokens": int(round(total_cache_read * 0.9)),
    }

    grand_tool_tok = sum(tok for calls in tool_calls_map.values() for tok, _ in calls)
    s12_rows: list[dict[str, Any]] = []
    for t_name, calls in tool_calls_map.items():
        toks = sorted(tok for tok, _ in calls)
        p95_idx = min(len(toks) - 1, max(0, int((len(toks) * 95 + 99) // 100) - 1))
        p95_val = toks[p95_idx] if toks else 0
        tot_t = sum(toks)
        bloated = sum(1 for tok in toks if tok > 10000)
        sk_counts: dict[str, int] = {}
        for tok, sk in calls:
            sk_counts[sk] = sk_counts.get(sk, 0) + (100000 if tok > 10000 else 1)
        dom_sk = max(sk_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if sk_counts else "team-lead"
        s12_rows.append({
            "tool_name": t_name,
            "call_count": len(calls),
            "total_tokens": tot_t,
            "p95_tokens": p95_val,
            "bloated_call_count": bloated,
            "share_pct": round(100.0 * tot_t / grand_tool_tok, 1) if grand_tool_tok > 0 else 0.0,
            "dominant_skill": dom_sk,
        })
    s12_rows.sort(key=lambda r: (-r["total_tokens"], r["tool_name"]))
    burn_proposals = _build_tool_context_burn_candidates(s12_rows)
    return s11_data, s12_rows, burn_proposals


def compute_skill_roi_diagnostics(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
) -> dict[str, Any]:
    """Compute S1-S4, S6-S10 Skill ROI diagnostics, and S11-S12 Context & Tool Burn."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    cp = copilot_db or DEFAULT_COPILOT_DB
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT

    inventory = _discover_skill_inventory(s_root, d_root)
    known_skills = set(inventory.keys())
    script_to_skill: dict[str, str] = {}
    for s_name, meta in inventory.items():
        for sc in meta.get("scripts", []):
            script_to_skill[sc] = s_name

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
            "session_count": 0,
            "edit_churn_count": 0,
            "retry_commands": 0,
            "r1_total": 0,
            "r1_pass": 0,
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
                "session_count": 0,
                "edit_churn_count": 0,
                "retry_commands": 0,
                "r1_total": 0,
                "r1_pass": 0,
                "impl_bug_escapes": 0,
                "spec_gap_escapes": 0,
            }
        return stats[norm]

    s2_gate: dict[str, Any] = {
        "total_runs_7d": 0,
        "true_positive_catches_7d": 0,
        "config_or_rerun_noise_7d": 0,
        "gate_catch_rate": None,
        "noise_rate": None,
        "window_cst": "7d",
        "source_as_of": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        "sample_size": 0,
        "coverage_status": "unknown",
    }

    source_as_of = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    sample_turns = 0
    # 1. Read warehouse.db (fact_turn, fact_gtt_task_run, fact_gtt_event, fact_ci_*)
    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            tables = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "fact_turn" in tables:
                max_day_row = conn.execute("SELECT MAX(cst_day), COUNT(*) FROM fact_turn").fetchone()
                if max_day_row and max_day_row[0]:
                    source_as_of = str(max_day_row[0])
                if max_day_row and max_day_row[1]:
                    sample_turns = int(max_day_row[1] or 0)
            if "fact_ci_job_run" in tables:
                row_ci = conn.execute(
                    """
                    SELECT
                        COUNT(*),
                        SUM(CASE WHEN failure_category IN ('true_positive_gate', 'build_or_test_failure') THEN 1 ELSE 0 END),
                        SUM(CASE WHEN failure_category LIKE 'config_drift_%' OR failure_category = 'runner_capacity_or_sim' THEN 1 ELSE 0 END),
                        MAX(cst_day)
                    FROM fact_ci_job_run
                    WHERE cst_day >= date('now', '-7 days')
                    """
                ).fetchone()
                if row_ci and row_ci[0]:
                    total_j = int(row_ci[0] or 0)
                    catches = int(row_ci[1] or 0)
                    noise = int(row_ci[2] or 0)
                    ci_as_of = str(row_ci[3] or source_as_of)
                    s2_gate["total_runs_7d"] = total_j
                    s2_gate["true_positive_catches_7d"] = catches
                    s2_gate["config_or_rerun_noise_7d"] = noise
                    s2_gate["sample_size"] = total_j
                    s2_gate["source_as_of"] = ci_as_of
                    denom = catches + noise
                    if denom > 0:
                        s2_gate["gate_catch_rate"] = round(catches / denom, 4)
                        s2_gate["coverage_status"] = "healthy"
                    else:
                        s2_gate["gate_catch_rate"] = None
                        s2_gate["coverage_status"] = "unknown"
                    s2_gate["noise_rate"] = round(noise / total_j, 4) if total_j > 0 else None

            if "fact_turn" in tables:
                turn_rows = conn.execute(
                    """
                    SELECT
                        session_id,
                        attribution_skill,
                        skill_names,
                        tool_calls,
                        COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) + COALESCE(cache_read, 0) AS tokens,
                        cst_day >= date('now', '-7 days') AS is_7d,
                        cst_day >= date('now', '-30 days') AS is_30d
                    FROM fact_turn
                    WHERE (attribution_skill IS NOT NULL AND attribution_skill != '')
                       OR (skill_names IS NOT NULL AND skill_names != '')
                    """
                ).fetchall()
                session_skills: dict[str, set[str]] = {}
                session_tokens: dict[str, int] = {}
                session_bash_calls: dict[str, int] = {}
                session_read_calls: dict[str, int] = {}
                for sess_id, attr_sk, sk_json, tc_json, tok, is_7d, is_30d in turn_rows:
                    matched_skills: set[str] = set()
                    if attr_sk:
                        matched_skills.add(str(attr_sk).strip())
                    if sk_json:
                        try:
                            for item in json.loads(sk_json):
                                if isinstance(item, str) and item.strip():
                                    matched_skills.add(item.strip())
                        except json.JSONDecodeError:
                            pass
                    t_list: list[str] = []
                    if tc_json:
                        try:
                            t_list = [str(x) for x in json.loads(tc_json) if x]
                        except json.JSONDecodeError:
                            t_list = []
                    bash_cnt = sum(1 for t in t_list if t.lower() in {"bash", "execute_code", "terminal"})
                    read_cnt = sum(1 for t in t_list if t.lower() in {"read", "view"})
                    for sk in matched_skills:
                        entry = _ensure_skill(sk)
                        if is_7d:
                            entry["loads_7d"] += 1
                        if is_30d:
                            entry["loads_30d"] += 1
                        if sess_id:
                            session_skills.setdefault(str(sess_id), set()).add(entry["skill"])
                    if sess_id:
                        session_tokens[str(sess_id)] = session_tokens.get(str(sess_id), 0) + int(tok or 0)
                        session_bash_calls[str(sess_id)] = session_bash_calls.get(str(sess_id), 0) + bash_cnt
                        session_read_calls[str(sess_id)] = session_read_calls.get(str(sess_id), 0) + read_cnt

                for sess_id, sk_set in session_skills.items():
                    tok_share = int(session_tokens.get(sess_id, 0) / max(1, len(sk_set)))
                    b_cnt = session_bash_calls.get(sess_id, 0)
                    r_cnt = session_read_calls.get(sess_id, 0)
                    for sk in sk_set:
                        entry = _ensure_skill(sk)
                        entry["session_count"] += 1
                        entry["total_tokens"] += tok_share
                        if b_cnt > 0 and entry["has_scripts"]:
                            entry["script_execs"] += 1
                        if r_cnt <= 1:
                            entry["l0_only_sessions"] += 1
                        elif r_cnt <= 3:
                            entry["l1_selective_sessions"] += 1
                        else:
                            entry["l1_greedy_sessions"] += 1

            if "fact_gtt_task_run" in tables:
                run_rows = conn.execute(
                    """
                    SELECT goal_id, task_id, round_no, v5_verdict, status
                    FROM fact_gtt_task_run
                    WHERE role_name IN ('reviewer', 'code_reviewer', 'planner_reviewer')
                    """
                ).fetchall()
                for goal_id, _task_id, round_no, v5, status in run_rows:
                    g_lower = str(goal_id or "").lower()
                    inferred = ["team-lead"]
                    if any(k in g_lower for k in ("design", "ui", "feel", "glass", "hallmark")):
                        inferred.append("my-designer")
                    if any(k in g_lower for k in ("ingest", "palette", "motion")):
                        inferred.append("design-studio")
                    if any(k in g_lower for k in ("retro", "telemetry", "roi", "aidash")):
                        inferred.append("retro-loop")
                    if any(k in g_lower for k in ("learn", "digest", "blog", "vault")):
                        inferred.append("knowledge-digest")
                    if any(k in g_lower for k in ("skill", "contract")):
                        inferred.append("skill-contract")
                    if any(k in g_lower for k in ("m2", "herdr", "dispatch")):
                        inferred.append("m2-dispatcher")
                    for sk in inferred:
                        entry = _ensure_skill(sk)
                        if int(round_no or 1) == 1:
                            entry["r1_total"] += 1
                            if str(v5 or "").upper() in {"PASS", "CONCERNS", "WAIVED"} and str(status or "") != "failed":
                                entry["r1_pass"] += 1
                            else:
                                entry["impl_bug_escapes"] += 1

            if "fact_gtt_event" in tables:
                evt_rows = conn.execute(
                    """
                    SELECT goal_id, evt_type, COALESCE(note_preview, '')
                    FROM fact_gtt_event
                    WHERE evt_type IN ('REVIEW_FAIL', 'RULING', 'CHANGE_REQUEST')
                    """
                ).fetchall()
                for goal_id, evt_type, note in evt_rows:
                    note_up = str(note).upper()
                    g_lower = str(goal_id or "").lower()
                    target_sk = "my-designer" if any(k in g_lower for k in ("design", "ui", "feel")) else "team-lead"
                    entry = _ensure_skill(target_sk)
                    if "SPEC_GAP" in note_up or evt_type in {"RULING", "CHANGE_REQUEST"}:
                        entry["spec_gap_escapes"] += 1
                    elif "IMPL_BUG" in note_up or evt_type == "REVIEW_FAIL":
                        entry["impl_bug_escapes"] += 1

    # 2. Enrich from ~/.copilot/session-store.db if present
    if cp.is_file():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
                cp_tables = {
                    r[0]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                sess_to_skills: dict[str, set[str]] = {}
                sess_to_refs: dict[str, set[str]] = {}
                if "session_files" in cp_tables:
                    sf_rows = conn.execute(
                        "SELECT session_id, file_path, tool_name, count(*) FROM session_files GROUP BY session_id, file_path, tool_name"
                    ).fetchall()
                    for sid, fpath, tname, cnt in sf_rows:
                        fpath_s = str(fpath or "")
                        for m in _SKILL_NAME_RE.finditer(fpath_s):
                            sk_hit = m.group("ctx") or m.group("path") or m.group("call")
                            if sk_hit and sk_hit in known_skills:
                                sess_to_skills.setdefault(str(sid), set()).add(sk_hit)
                        for rm in _REF_PATH_RE.finditer(fpath_s):
                            sk_hit = rm.group("skill")
                            if sk_hit in known_skills:
                                sess_to_skills.setdefault(str(sid), set()).add(sk_hit)
                                sess_to_refs.setdefault(f"{sid}:{sk_hit}", set()).add(rm.group("ref"))
                        for sm in _SCRIPT_PATH_RE.finditer(fpath_s):
                            sk_hit = sm.group("skill")
                            if sk_hit in known_skills:
                                _ensure_skill(sk_hit)["script_execs"] += int(cnt or 1)

                    # Edit churn per session linked to skills
                    churn_rows = conn.execute(
                        """
                        SELECT session_id, file_path, count(*) as edit_cnt
                        FROM session_files
                        WHERE tool_name IN ('edit', 'apply_patch')
                        GROUP BY session_id, file_path
                        HAVING count(*) >= 4
                        """
                    ).fetchall()
                    for sid, _fp, edit_cnt in churn_rows:
                        for sk in sess_to_skills.get(str(sid), {"team-lead"}):
                            _ensure_skill(sk)["edit_churn_count"] += int(edit_cnt or 0)

                if "turns" in cp_tables:
                    t_rows = conn.execute(
                        """
                        SELECT session_id, substr(user_message, 1, 2000), substr(assistant_response, 1, 2000),
                               substr(timestamp, 1, 10) >= date('now', '-7 days') AS is_7d,
                               substr(timestamp, 1, 10) >= date('now', '-30 days') AS is_30d
                        FROM turns
                        WHERE user_message LIKE '%skill-context%' OR user_message LIKE '%/skills/%'
                           OR assistant_response LIKE '%/skills/%'
                        LIMIT 2000
                        """
                    ).fetchall()
                    for sid, u_msg, a_msg, is_7d, is_30d in t_rows:
                        blob = f"{u_msg or ''}\n{a_msg or ''}"
                        for m in _SKILL_NAME_RE.finditer(blob):
                            sk_hit = m.group("ctx") or m.group("path") or m.group("call")
                            if sk_hit:
                                entry = _ensure_skill(sk_hit)
                                if is_7d:
                                    entry["loads_7d"] += 1
                                if is_30d:
                                    entry["loads_30d"] += 1
                                sess_to_skills.setdefault(str(sid), set()).add(entry["skill"])
                        for sc_name, sc_skill in script_to_skill.items():
                            if sc_name in blob:
                                _ensure_skill(sc_skill)["script_execs"] += 1

                for sid, sk_set in sess_to_skills.items():
                    for sk in sk_set:
                        entry = _ensure_skill(sk)
                        entry["session_count"] += 1
                        refs_used = len(sess_to_refs.get(f"{sid}:{sk}", set()))
                        if refs_used == 0:
                            entry["l0_only_sessions"] += 1
                        elif refs_used <= 2:
                            entry["l1_selective_sessions"] += 1
                        else:
                            entry["l1_greedy_sessions"] += 1
        except sqlite3.Error:
            pass

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
        script_ratio = round(min(1.0, script_execs / max(1, loads)), 4) if st["has_scripts"] and loads > 0 else (1.0 if not st["has_scripts"] else 0.0)
        read_only_unpracticed_rate = round(1.0 - script_ratio, 4) if st["has_scripts"] and loads > 0 else 0.0

        f_tot = max(1, st["l0_only_sessions"] + st["l1_selective_sessions"] + st["l1_greedy_sessions"])
        l0_rate = round(st["l0_only_sessions"] / f_tot, 4) if loads > 0 else None
        l1_sel_rate = round(st["l1_selective_sessions"] / f_tot, 4) if loads > 0 else None
        l1_greedy_rate = round(st["l1_greedy_sessions"] / f_tot, 4) if loads > 0 else None
        avg_tokens = int(st["total_tokens"] / st["session_count"]) if st["session_count"] > 0 else None

        # S10 formula: Skill ROI = R1_Pass_Rate / Normalized(Tokens + Churn + Uncalled_Script_Penalty)
        norm_tokens = min(2.0, max(0.2, avg_tokens / 10000.0)) if avg_tokens is not None else None
        norm_churn = min(2.0, st["edit_churn_count"] * 0.15 + st["retry_commands"] * 0.1)
        uncalled_penalty = read_only_unpracticed_rate * 0.6 if st["has_scripts"] and loads > 0 else 0.0
        escape_penalty = (st["impl_bug_escapes"] * 0.2) + (st["spec_gap_escapes"] * 0.15)
        denom = max(0.25, 0.5 + norm_tokens + norm_churn + uncalled_penalty + escape_penalty) if norm_tokens is not None else None
        roi_score = round(r1_pass_rate / denom, 4) if denom is not None and r1_pass_rate is not None else None

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

        skill_rows.append({
            "skill": s_name,
            "loads_7d": st["loads_7d"],
            "loads_30d": loads,
            "r1_pass_rate": r1_pass_rate,
            "r1_total": r1_tot,
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
            "diagnosis": "；".join(diagnoses) if diagnoses else "健康达标",
            "needs_optimization": bool(diagnoses) and (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0),
        })

    active_rows = [r for r in skill_rows if r["loads_30d"] > 0 or r["r1_total"] > 0 or r["edit_churn_count"] > 0]
    low_roi_candidates = sorted(
        [r for r in active_rows if r["needs_optimization"]],
        key=lambda r: (r["roi_score"] is None, r["roi_score"] if r["roi_score"] is not None else float("inf"), -r["impl_bug_escapes"], -(r["unpracticed_rate"] or 0.0)),
    )[:2]

    proposals: list[dict[str, Any]] = []
    for cand in low_roi_candidates:
        sk = cand["skill"]
        roi_label = f"{cand['roi_score']:.2f}" if cand["roi_score"] is not None else "unknown"
        r1_label = f"{cand['r1_pass_rate'] * 100:.1f}%" if cand["r1_pass_rate"] is not None else "unknown"
        proposals.append({
            "id": f"skill-roi-{sk}",
            "ref": f"skill-roi://{sk}",
            "title": f"[Skill ROI] 优化 {sk}（ROI={cand['roi_score']:.2f}，R1通过={cand['r1_pass_rate']*100:.0f}%）：{cand['diagnosis'][:36]}" if cand["roi_score"] is not None and cand["r1_pass_rate"] is not None else f"[Skill ROI] 补齐 {sk} 的观测覆盖：{cand['diagnosis'][:36]}",
            "detail": (
                f"skill={sk} | roi_score={roi_label} | "
                f"r1_pass_rate={r1_label} | "
                f"unpracticed_rate={cand['unpracticed_rate']*100:.1f}% | "
                f"edit_churn={cand['edit_churn_count']} | "
                f"impl_bug={cand['impl_bug_escapes']} | spec_gap={cand['spec_gap_escapes']} | "
                f"diagnosis={cand['diagnosis']}"
            ),
            "priority": "high",
        })

    s11_context, s12_tools, burn_proposals = _compute_s11_s12_context_and_tools(
        wh,
        magpie_usage=magpie_usage,
        copilot_db=cp,
        dsh_sessions=dsh_sessions,
    )
    proposals.extend(burn_proposals[:2])

    total_sample_size = max(
        sample_turns,
        sum(r["loads_30d"] + r["r1_total"] for r in skill_rows),
        int(s2_gate["total_runs_7d"] or 0),
    )
    cov_status = "healthy" if total_sample_size > 0 else ("unknown" if wh.is_file() else "missing")

    for p_item in proposals:
        p_item.setdefault("window_cst", "7d")
        p_item.setdefault("source_as_of", source_as_of)
        p_item.setdefault("sample_size", total_sample_size)
        p_item.setdefault("coverage_status", cov_status)

    return {
        "ok": True,
        "window_cst": "7d",
        "source_as_of": source_as_of,
        "sample_size": total_sample_size,
        "coverage_status": cov_status,
        "S1_pin_drift": s1_pins,
        "S2_gate_catch": s2_gate,
        "S3_skill_health": s3_health,
        "S4_usage_and_r1": {
            "active_skills_count": len(active_rows),
            "idle_skills_30d": idle_skills_30d,
            "top_used_skills": sorted(skill_rows, key=lambda r: (-r["loads_30d"], r["skill"]))[:8],
        },
        "S6_load_vs_script": [
            {
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
                "skill": r["skill"],
                "edit_churn_count": r["edit_churn_count"],
                "retry_commands": r["retry_commands"],
            }
            for r in active_rows
            if r["edit_churn_count"] > 0 or r["retry_commands"] > 0
        ],
        "S9_escape_attribution": [
            {
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
        "low_roi_proposals": proposals,
    }


def compute_agent_efficiency(
    warehouse_db: Path | None = None,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
) -> dict[str, Any]:
    """Compute M1-M5 Agent Efficiency metrics & improvement gaps."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    mp = magpie_usage or DEFAULT_MAGPIE_USAGE
    cp = copilot_db or DEFAULT_COPILOT_DB

    # M1: Review Ping-Pong (审核与修复往返拉锯，重点监控 R1 FAIL 与 Round >= 2/3)
    m1: dict[str, Any] = {
        "reviewed_tasks": 0,
        "r1_fail_tasks": 0,
        "r1_fail_rate": None,
        "floor_max_r1_fail_rate": 0.15,
        "multi_round_review_tasks": [],
        "round_3_plus_tasks": [],
        "breached": False,
    }
    blocked_events_7d = 0
    if wh.is_file():
        with sqlite3.connect(f"file:{wh}?mode=ro", uri=True) as conn:
            rows = conn.execute("""
                SELECT goal_id, task_id, max(round_no) as max_round,
                       sum(case when round_no = 1 and v5_verdict = 'FAIL' then 1 else 0 end) as r1_fail
                FROM fact_gtt_task_run
                WHERE role_name IN ('reviewer', 'code_reviewer', 'planner_reviewer')
                GROUP BY goal_id, task_id
            """).fetchall()
            m1["reviewed_tasks"] = len(rows)
            m1["r1_fail_tasks"] = sum(1 for r in rows if (r[3] or 0) > 0 or (r[2] or 1) >= 2)
            m1["r1_fail_rate"] = round(m1["r1_fail_tasks"] / len(rows), 4) if rows else None
            m1["multi_round_review_tasks"] = [
                {"goal_id": r[0], "task_id": r[1], "max_round": r[2]}
                for r in rows if (r[2] or 1) >= 2
            ][:10]
            m1["round_3_plus_tasks"] = [
                {"goal_id": r[0], "task_id": r[1], "max_round": r[2]}
                for r in rows if (r[2] or 1) >= 3
            ]
            m1["breached"] = m1["r1_fail_rate"] > m1["floor_max_r1_fail_rate"] or len(m1["round_3_plus_tasks"]) > 0
            row_blk = conn.execute("""
                SELECT count(*) FROM fact_gtt_event
                WHERE evt_type = 'TASK_BLOCKED' AND cst_day >= date('now', '-7 days')
            """).fetchone()
            blocked_events_7d = int(row_blk[0] if row_blk else 0)

    # M2: File Edit Churn (从 ~/.copilot/session-store.db 统计同会话反复修改同一文件)
    m2: dict[str, Any] = {
        "high_churn_files": [],
        "max_edits_single_file": 0,
        "floor_max_edits_per_session": 5,
        "breached": False,
    }
    if cp.is_file():
        try:
            with sqlite3.connect(f"file:{cp}?mode=ro", uri=True) as conn:
                churn_rows = conn.execute("""
                    SELECT session_id, file_path, count(*) as edit_cnt
                    FROM session_files
                    WHERE tool_name = 'edit'
                    GROUP BY session_id, file_path
                    HAVING count(*) >= 5
                    ORDER BY edit_cnt DESC
                    LIMIT 8
                """).fetchall()
                m2["high_churn_files"] = [
                    {"session_id": r[0][:8], "file_path": r[1], "edits": r[2]}
                    for r in churn_rows
                ]
                m2["max_edits_single_file"] = churn_rows[0][2] if churn_rows else 0
                m2["breached"] = len(m2["high_churn_files"]) > 0
        except sqlite3.Error:
            pass

    # M3, M4, M5 from ~/.config/magpie/usage.jsonl (recent 5000 records)
    ttfts: list[int] = []
    retries = 0
    gateway_4xx = 0
    total_reqs = 0
    group_tokens: dict[str, dict[str, int]] = {}
    steering_tiny_outputs = 0
    steering_reqs = 0
    if mp.is_file():
        try:
            lines = mp.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]
            for ln in lines:
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                total_reqs += 1
                status_code = int(rec.get("status") or 200)
                if status_code in (400, 401, 403, 422, 429):
                    gateway_4xx += 1
                req_model = str(rec.get("req") or rec.get("model") or "other")
                grp = req_model if req_model.startswith("group/") else "direct"
                in_t = int(rec.get("in") or 0)
                out_t = int(rec.get("out") or 0)
                cr_t = int(rec.get("cache_read") or 0)
                bucket = group_tokens.setdefault(grp, {"in": 0, "out": 0, "cache_read": 0, "reqs": 0})
                bucket["in"] += in_t
                bucket["out"] += out_t
                bucket["cache_read"] += cr_t
                bucket["reqs"] += 1
                if int(rec.get("tries") or 1) > 1 or int(rec.get("retried_ms") or 0) > 0:
                    retries += 1
                ttft = int(rec.get("ttft_ms") or 0)
                if ttft > 0:
                    ttfts.append(ttft)
                if "steering" in grp:
                    steering_reqs += 1
                    if 0 < out_t < 80:
                        steering_tiny_outputs += 1
        except OSError:
            pass

    ttfts.sort()
    p90_ttft = ttfts[int(len(ttfts) * 0.9)] if ttfts else 0
    retry_rate = round(retries / total_reqs, 4) if total_reqs else 0.0
    m3 = {
        "sampled_requests": total_reqs,
        "p90_ttft_ms": p90_ttft if ttfts else None,
        "floor_max_p90_ttft_ms": 3000,
        "retry_rate": retry_rate if total_reqs else None,
        "gateway_4xx_count": gateway_4xx,
        "blocked_events_7d": blocked_events_7d,
        "breached": bool(total_reqs and (p90_ttft > 3000 or retry_rate > 0.05 or gateway_4xx > 0)),
    }

    total_in = sum(b["in"] for b in group_tokens.values())
    total_cr = sum(b["cache_read"] for b in group_tokens.values())
    overall_cache_hit = round(total_cr / (total_in + total_cr), 4) if (total_in + total_cr) > 0 else None
    by_group_cache = {
        g: round(b["cache_read"] / (b["in"] + b["cache_read"]), 4) if (b["in"] + b["cache_read"]) > 0 else None
        for g, b in group_tokens.items()
    }
    m4 = {
        "overall_cache_hit_rate": overall_cache_hit,
        "floor_min_cache_hit_rate": 0.75,
        "by_group": by_group_cache,
        "breached": bool(total_in + total_cr and overall_cache_hit is not None and overall_cache_hit < 0.75),
    }

    total_all_tokens = sum(b["in"] + b["out"] + b["cache_read"] for b in group_tokens.values())
    steering_tokens = sum(
        b["in"] + b["out"] + b["cache_read"]
        for g, b in group_tokens.items() if "steering" in g
    )
    steering_share = round(steering_tokens / total_all_tokens, 4) if total_all_tokens > 0 else None
    m5 = {
        "steering_token_share": steering_share,
        "floor_max_steering_share": 0.35,
        "steering_tiny_output_ratio": round(steering_tiny_outputs / steering_reqs, 4) if steering_reqs else None,
        "breached": bool(total_all_tokens and steering_share is not None and steering_share > 0.35),
    }

    recommendations: list[dict[str, Any]] = []
    eff_as_of = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    eff_sample = max(m1["reviewed_tasks"], total_reqs, len(m2["high_churn_files"]))
    eff_cov = "healthy" if eff_sample > 0 else ("unknown" if wh.is_file() or mp.is_file() else "missing")

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
    if m2["breached"]:
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
                f"重试率={m3['retry_rate']*100:.1f}%，4xx={m3['gateway_4xx_count']} 次，近 7d TASK_BLOCKED={m3['blocked_events_7d']} 次。"
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
            "title": f"收敛 Steering 主控 Token 占比：当前 {m5['steering_token_share']*100:.1f}% 超出 35% 预算",
            "detail": (
                f"Steering 消耗占比达 {m5['steering_token_share']*100:.1f}%（短输出占比 {m5['steering_tiny_output_ratio']*100:.1f}%），"
                f"建议将 >3 文件实现与大日志探查下沉至 Writer / Layer-0 脚本。"
                f" | window_cst=7d | source_as_of={eff_as_of} | sample_size={total_reqs}"
            ),
            "priority": "medium",
            "window_cst": "7d",
            "source_as_of": eff_as_of,
            "sample_size": total_reqs,
            "coverage_status": eff_cov,
        })

    return {
        "ok": True,
        "window_cst": "7d",
        "source_as_of": eff_as_of,
        "sample_size": eff_sample,
        "coverage_status": eff_cov,
        "metrics": {
            "M1_review_ping_pong": m1,
            "M2_edit_churn": m2,
            "M3_flow_and_blockers": m3,
            "M4_cache_efficiency": m4,
            "M5_steering_vs_coding_split": m5,
        },
        "recommendations": recommendations,
    }


def route_retro_candidate(
    candidate: dict[str, Any],
    mode: str = "observe",
    starred: bool = False,
    proposals_path: Path | None = None,
    linear_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    raw_prio = str(candidate.get("priority", "high")).strip().lower()
    norm_prio = raw_prio if raw_prio in {"high", "medium", "low"} else "high"
    proposal = {
        "id": candidate["id"], "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": "retro-loop", "title": candidate.get("title", ""), "detail": candidate.get("detail", ""),
        "priority": norm_prio, "status": "pending",
    }
    for extra_key in (
        "ref",
        "app_name",
        "event_kind",
        "signature",
        "stack_top_symbol",
        "recommended_layer",
        "verify_cmd",
        "window_cst",
        "source_as_of",
        "sample_size",
        "coverage_status",
        "scope",
    ):
        if candidate.get(extra_key) is not None:
            proposal[extra_key] = candidate[extra_key]
    proposal.setdefault("scope", "personal")
    if mode == "observe" and not starred:
        if proposals_path is None:
            raise ValueError("proposals_path is required for observe mode")
        proposals_path.parent.mkdir(parents=True, exist_ok=True)
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
        report = compute_agent_efficiency(ns.warehouse_db, ns.magpie_usage, ns.copilot_db)
        if ns.auto_propose:
            existing_pending: set[str] = set()
            if ns.proposals_path and ns.proposals_path.is_file():
                for line in ns.proposals_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec_obj = json.loads(line)
                        if isinstance(rec_obj, dict) and rec_obj.get("id"):
                            if str(rec_obj.get("status", "pending")).lower() == "pending":
                                existing_pending.add(str(rec_obj["id"]))
                            else:
                                existing_pending.discard(str(rec_obj["id"]))
                    except json.JSONDecodeError:
                        continue
            queued = []
            for rec in report.get("recommendations", []):
                if rec["id"] not in existing_pending:
                    queued.append(route_retro_candidate(rec, mode="observe", proposals_path=ns.proposals_path))
                    existing_pending.add(rec["id"])
            report["auto_proposed"] = queued
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args_list and args_list[0] == "skill-roi":
        p = argparse.ArgumentParser(prog="retro_story_engine.py skill-roi")
        p.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
        p.add_argument("--copilot-db", type=Path, default=DEFAULT_COPILOT_DB)
        p.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
        p.add_argument("--dev-root", type=Path, default=DEFAULT_DEV_ROOT)
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        report = compute_skill_roi_diagnostics(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
        )
        if ns.auto_propose:
            existing_pending: set[str] = set()
            if ns.proposals_path and ns.proposals_path.is_file():
                for line in ns.proposals_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec_obj = json.loads(line)
                        if isinstance(rec_obj, dict) and rec_obj.get("id"):
                            if str(rec_obj.get("status", "pending")).lower() == "pending":
                                existing_pending.add(str(rec_obj["id"]))
                            else:
                                existing_pending.discard(str(rec_obj["id"]))
                    except json.JSONDecodeError:
                        continue
            queued = []
            for rec in report.get("low_roi_proposals", []):
                if rec["id"] not in existing_pending:
                    queued.append(route_retro_candidate(rec, mode="observe", proposals_path=ns.proposals_path))
                    existing_pending.add(rec["id"])
            report["auto_proposed"] = queued
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args_list and args_list[0] == "propose":
        p = argparse.ArgumentParser(prog="retro_story_engine.py propose")
        p.add_argument("--id", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--detail", default="")
        p.add_argument("--priority", default="high", choices=("high", "medium", "low"))
        p.add_argument("--mode", default="observe", choices=("observe", "auto"))
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(args_list[1:])
        res = route_retro_candidate(
            {"id": ns.id, "title": ns.title, "detail": ns.detail, "priority": ns.priority},
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
