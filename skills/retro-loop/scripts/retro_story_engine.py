#!/usr/bin/env python3
"""Deterministic retro/story helpers for evidence validation and promotion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
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


def discover_aidata_signals(
    warehouse_db: Path | None = None,
    queries_dir: Path | None = None,
) -> dict[str, Any]:
    """Run aidata L4 retro queries against warehouse.db to discover anomalies."""
    db_path = warehouse_db or DEFAULT_WAREHOUSE_DB
    q_dir = queries_dir or DEFAULT_RETRO_QUERIES_DIR
    findings: list[dict[str, Any]] = []
    if not db_path.is_file() or not q_dir.is_dir():
        return {"ok": True, "degraded": True, "findings": findings}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
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
            except sqlite3.Error:
                continue
    return {"ok": True, "degraded": False, "findings": findings}


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
        "r1_fail_rate": 0.0,
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
            m1["r1_fail_rate"] = round(m1["r1_fail_tasks"] / len(rows), 4) if rows else 0.0
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
        "p90_ttft_ms": p90_ttft,
        "floor_max_p90_ttft_ms": 3000,
        "retry_rate": retry_rate,
        "gateway_4xx_count": gateway_4xx,
        "blocked_events_7d": blocked_events_7d,
        "breached": p90_ttft > 3000 or retry_rate > 0.05 or gateway_4xx > 0,
    }

    total_in = sum(b["in"] for b in group_tokens.values())
    total_cr = sum(b["cache_read"] for b in group_tokens.values())
    overall_cache_hit = round(total_cr / (total_in + total_cr), 4) if (total_in + total_cr) > 0 else 0.0
    by_group_cache = {
        g: round(b["cache_read"] / (b["in"] + b["cache_read"]), 4) if (b["in"] + b["cache_read"]) > 0 else 0.0
        for g, b in group_tokens.items()
    }
    m4 = {
        "overall_cache_hit_rate": overall_cache_hit,
        "floor_min_cache_hit_rate": 0.75,
        "by_group": by_group_cache,
        "breached": (total_in + total_cr) > 0 and overall_cache_hit < 0.75,
    }

    total_all_tokens = sum(b["in"] + b["out"] + b["cache_read"] for b in group_tokens.values())
    steering_tokens = sum(
        b["in"] + b["out"] + b["cache_read"]
        for g, b in group_tokens.items() if "steering" in g
    )
    steering_share = round(steering_tokens / total_all_tokens, 4) if total_all_tokens > 0 else 0.0
    m5 = {
        "steering_token_share": steering_share,
        "floor_max_steering_share": 0.35,
        "steering_tiny_output_ratio": round(steering_tiny_outputs / steering_reqs, 4) if steering_reqs else 0.0,
        "breached": steering_share > 0.35,
    }

    recommendations: list[dict[str, str]] = []
    if m1["breached"]:
        multi_str = ", ".join(f"{t['goal_id']}/{t['task_id']}(r{t['max_round']})" for t in m1["round_3_plus_tasks"][:4])
        recommendations.append({
            "id": "improve-m1-review-pingpong",
            "title": f"缩减审核返工拉锯：R1 打回率 {m1['r1_fail_rate']*100:.1f}%（{len(m1['round_3_plus_tasks'])} 项达 r3+）",
            "detail": f"多轮审核拉锯任务：{multi_str or '见 multi_round_review_tasks'}。建议将 Reviewer 打回的高频项前移至 Writer 提交前的内环 L1/G2 脚本预检。",
            "priority": "high",
        })
    if m2["breached"]:
        top_f = m2["high_churn_files"][0]
        recommendations.append({
            "id": "improve-m2-edit-churn",
            "title": f"降低文件反复擦写：单会话最高连改 {top_f['edits']} 次 ({Path(top_f['file_path']).name})",
            "detail": f"文件 {top_f['file_path']} 在单次会话中被反复 edit {top_f['edits']} 次，建议补充局部契约或拆分深模块。",
            "priority": "medium",
        })

    return {
        "ok": True,
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
        ns = p.parse_args(args_list[1:])
        print(json.dumps(discover_aidata_signals(ns.warehouse_db, ns.queries_dir), ensure_ascii=False, indent=2))
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
            queued = []
            for rec in report.get("recommendations", []):
                queued.append(route_retro_candidate(rec, mode="observe", proposals_path=ns.proposals_path))
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
