from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _stale_candidates(target: dict[str, Any], sessions: list[dict[str, Any]], domain_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Find only safe-to-close idle Herdr panes for the target topic/repo."""
    topic = _topic_by_id(domain_map).get(_text(target.get("topic_id")))
    matches = _matching_topic_sessions(target, topic, sessions, domain_map)
    if not matches:
        return []
    current_pane = _text(os.environ.get("HERDR_PANE_ID"))
    primary_id = _text(target.get("session_id")) if target.get("m2_status") == "EXISTING_M2" else ""
    candidates: list[dict[str, Any]] = []
    idle_primary_assigned = False
    for session in matches:
        if session.get("source") != "herdr" or _text(session.get("status"), "idle").casefold() not in {"idle", "done"}:
            continue
        pane_id = _text(session.get("pane_id"))
        if not pane_id or pane_id == current_pane or _gc_protected(session):
            continue
        is_primary_idle = session.get("session_id") == primary_id or not idle_primary_assigned
        reason = "idle_existing_m2" if is_primary_idle else "duplicate_m2"
        idle_primary_assigned = True
        candidates.append({"session_id": session.get("session_id", ""), "pane_id": pane_id, "title": session.get("title", ""), "status": session.get("status", "idle"), "target_repo": session.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": reason})
    return candidates

def _read_goal_state(session: dict[str, Any], *, handoff_root: str | Path | None = None) -> tuple[bool, str]:
    """Determine effective current completion from structured ledger state."""
    root = Path(handoff_root or os.environ.get("M2_HANDOFF_ROOT") or "~/Development/handoff").expanduser()
    explicit = _text(session.get("handoff_dir"))
    text = "\n".join(_text(session.get(k)) for k in ("target", "title", "summary", "visible_summary", "terminal_title", "linear_issue"))
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit) / "ledger.md")
    if _text(session.get("goal_id")):
        candidates.append(root / _text(session["goal_id"]) / "ledger.md")
    for match in re.finditer(r"(?:~/|/)[^\s`\"'<>|()]*handoff/([^/\s`\"'<>|()]+)", text):
        candidates.append(Path(match.group(0)).expanduser() / "ledger.md")
    if root.is_dir():
        candidates.extend(sorted(root.glob("*/ledger.md")))
    ranked: list[tuple[int, Path, str]] = []
    for path in dict.fromkeys(candidates):
        try:
            ledger = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        score = 100 if explicit and path.parent == Path(explicit).expanduser() else 0
        if path.parent.name and path.parent.name.casefold() in text.casefold():
            score += 30
        linear = re.search(r"\bTIA-\d+\b", ledger, re.I)
        if linear and linear.group(0).casefold() in text.casefold():
            score += 20
        if score or explicit or _text(session.get("goal_id")):
            ranked.append((score, path, ledger))
    for _, path, ledger in sorted(ranked, key=lambda item: item[0], reverse=True)[:1]:
        header = re.search(r"^\s*-?\s*(?:status|状态)\s*[:：]\s*([^\n|]+)", ledger, re.I | re.M)
        header_status = _text(header.group(1)).casefold() if header else ""
        tasks: list[str] = []
        in_tasks = False
        for line in ledger.splitlines():
            if line.startswith("## "):
                in_tasks = line.strip().casefold() == "## tasks"
            if in_tasks and line.startswith("|"):
                cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 5 and cells[4] in {"planned", "ready", "in_progress", "done", "blocked", "in_review", "changes_requested"}:
                    tasks.append(cells[4])
        events = [(idx, line) for idx, line in enumerate(ledger.splitlines()) if "EVT=" in line]
        done_idx = max((idx for idx, line in events if re.search(r"EVT=GOAL_DONE", line, re.I)), default=-1)
        later_active = any(idx > done_idx and re.search(r"EVT=(?:READY|IN_PROGRESS|IN_REVIEW|DISPATCHED|INPUT_BLOCKED)|NEED_OWNER_DECISION", line, re.I) for idx, line in events)
        active_status = header_status in {"in_progress", "planned", "ready", "blocked", "in_review", "changes_requested", "reopened", "needs_input"} or bool(re.search(r"need_owner_decision|in review", ledger, re.I))
        active_tasks = any(status != "done" for status in tasks)
        if later_active or active_status or active_tasks:
            return False, f"active_or_reopened:{path.parent.name}"
        if done_idx >= 0 or header_status in {"done", "completed", "complete"}:
            return True, f"ledger:{path.parent.name}"
        if re.search(r"\b(?:Done|Completed)\b", text, re.I):
            return True, f"linear_done:{path.parent.name}"
    if re.search(r"\b(?:Done|Completed)\b", text, re.I) and not re.search(r"in_progress|in review|blocked|needs_input", text, re.I):
        return True, "explicit_done_evidence"
    if session.get("goal_done") is True:
        return True, "session.goal_done"
    return False, "not_effectively_completed"

def _answer_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("noul", "choice", "value", "score", "answer"):
            if key in value:
                return value[key]
    return value

def _evaluate_m2_context_continuity(
    target: dict[str, Any], session: dict[str, Any], message: str, *,
    domain_map: dict[str, Any] | None = None, mock_jev: Any = None,
    handoff_root: str | Path | None = None, visible_text: str = "",
) -> dict[str, Any]:
    """Strict safety truth table: effective completion AND both unrelatedness answers."""
    old_done, completion_evidence = _read_goal_state(session, handoff_root=handoff_root)
    semantic: dict[str, Any] = {}
    if isinstance(mock_jev, dict):
        semantic = _decision_answers(mock_jev)
    relevant = any(key in semantic for key in ("is_same_goal_continuation", "prior_context_value", "prior_context_value_match"))
    if not relevant and mock_jev is None:
        try:
            jev_path = ROUTER_FILE.resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
            spec = importlib.util.spec_from_file_location("m2_router_continuity_jev", jev_path)
            module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
            questions = {
                "is_same_goal_continuation": {"type": "noul", "instructions": "Is this a direct continuation, follow-up, or feedback?"},
                "prior_context_value": {"type": "choice", "criteria": {"essential":"essential", "helpful":"helpful", "noise_or_irrelevant":"noise_or_irrelevant"}, "instructions": "How valuable is old context?"},
                "prior_context_value_match": {"type": "noul", "instructions": "Is prior context essential or helpful?"},
            }
            result = module.jev_decide({"old_title": session.get("title", ""), "old_summary": session.get("summary", ""), "new_message": message, "topic_id": target.get("topic_id", "")}, questions, agent_role="personal-m2-assistant", timeout_sec=1.5)
            semantic = _decision_answers(result) if isinstance(result, dict) else {}
        except (OSError, ImportError, TypeError, AssertionError):
            semantic = {}
    raw_same = _answer_value(semantic.get("is_same_goal_continuation"))
    try:
        same_score = float(raw_same) if raw_same is not None else None
    except (TypeError, ValueError):
        same_score = None
    value = _text(_answer_value(semantic.get("prior_context_value"))).lower() or None
    match = _answer_value(semantic.get("prior_context_value_match"))
    valid = same_score is not None and 0 <= same_score <= 1 and value in {"essential", "helpful", "noise_or_irrelevant"}
    match_conflict = False
    if match is not None:
        if isinstance(match, bool):
            match_conflict = match
        elif isinstance(match, (int, float)) and not isinstance(match, bool):
            match_conflict = not (0 <= float(match) < 0.35)
        elif _text(match).casefold() in {"true", "yes", "essential", "helpful"}:
            match_conflict = True
        elif _text(match) and _text(match).casefold() not in {"false", "no", "noise_or_irrelevant"}:
            match_conflict = True
    unrelated = bool(valid and same_score < 0.35 and value == "noise_or_irrelevant" and not match_conflict and not _has_active_visible_tail(visible_text) and not _gc_protected(session))
    replace = bool(old_done and unrelated)
    return {
        "m2_action": "REPLACE_STALE_M2" if replace else "REUSE_EXISTING_M2",
        "m2_status": "NEW_M2" if replace else "EXISTING_M2",
        "old_goal_done": old_done,
        "completion_evidence": completion_evidence,
        "is_same_goal_continuation": same_score,
        "prior_context_value": value,
        "prior_context_value_match": match,
        "semantic_evidence_valid": valid,
        "semantic_unrelated": unrelated,
        "eviction_reason": "completed_goal_and_strict_jev_unrelatedness" if replace else "conservative_reuse",
        "stale_session_id": _text(session.get("session_id")),
        "stale_pane_id": _text(session.get("pane_id")),
    }

def _gc_protected(rec: dict[str, Any]) -> bool:
    pane = _text(rec.get("pane_id") or rec.get("paneId"))
    status = _text(rec.get("agent_status") or rec.get("status")).lower()
    hay = " ".join(_text(rec.get(k)) for k in ("workspace_label", "workspaceLabel", "space", "label", "title", "name"))
    return pane in {_text(os.environ.get("HERDR_PANE_ID")), "w1:p1", "w2J:p1"} or re.search(r"Cindy|大总管", hay, re.I) is not None or status in {"working", "running", "blocked", "needs_input"} or _has_active_visible_tail(_text(rec.get("visible") or rec.get("visible_text")))

def discover_gc_candidates(records: Iterable[dict[str, Any]], *, handoff_root: str | Path | None = None) -> list[dict[str, Any]]:
    records = [dict(r) for r in records if isinstance(r, dict)]
    candidates: list[dict[str, Any]] = []
    for rec in records:
        if _gc_protected(rec):
            continue
        pane = _text(rec.get("pane_id") or rec.get("paneId"))
        if not pane:
            continue
        status = _text(rec.get("agent_status") or rec.get("status"), "idle").lower()
        background = _herdr_background(rec)
        done, evidence = _read_goal_state(rec, handoff_root=handoff_root)
        workspace_ids = {_text(rec.get("workspace_id") or rec.get("workspaceId")), _text(rec.get("workspace_label") or rec.get("workspaceLabel"))}
        prefix = pane.split(":", 1)[0]
        active_child = any(
            other is not rec and (
                (workspace_ids - {""}) & {_text(other.get("workspace_id") or other.get("workspaceId")), _text(other.get("workspace_label") or other.get("workspaceLabel"))}
                or _text(other.get("pane_id") or other.get("paneId")).split(":", 1)[0] == prefix
            ) and (
                _text(other.get("agent_status") or other.get("status")).lower() in {"working", "running", "blocked", "needs_input"}
                or _has_active_visible_tail(_text(other.get("visible") or other.get("visible_text")))
            ) for other in records
        )
        if background and (status == "done" or done) and status in {"idle", "done", "exited", "stopped"} and not active_child:
            candidates.append({"category":"orphan_m1", "reason":"orphan_completed_m1", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "completion_evidence":evidence})
        elif _looks_like_m2(rec, "herdr") and status in {"idle", "done"}:
            if done:
                candidates.append({"category":"completed_goal_m2", "reason":"completed_goal_m2", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "completion_evidence":evidence, "topic_id":_text(rec.get("topic_id"))})
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if _looks_like_m2(rec, "herdr") and not _gc_protected(rec) and _text(rec.get("agent_status") or rec.get("status"), "idle").lower() in {"idle", "done"}:
            space = _text(rec.get("workspace_label") or rec.get("workspaceLabel") or rec.get("space") or rec.get("target_repo") or rec.get("repo"), "M2").casefold()
            topic = _text(rec.get("topic_id") or rec.get("topicId") or rec.get("target_repo") or rec.get("repo"), "unknown").casefold()
            groups.setdefault(f"{space}|{topic}", []).append(rec)
    for key, group in groups.items():
        classified = {c.get("pane_id"): c for c in candidates if c.get("pane_id")}
        eligible = [rec for rec in group if classified.get(_text(rec.get("pane_id") or rec.get("paneId")), {}).get("reason") != "completed_goal_m2"]
        for rec in eligible[1:]:
            pane = _text(rec.get("pane_id") or rec.get("paneId"))
            if pane and not any(c.get("pane_id") == pane for c in candidates):
                candidates.append({"category":"duplicate_m2", "reason":"duplicate_m2", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "topic_id":_text(rec.get("topic_id")), "space":key})
    return candidates

def _prune_closed_slots(state_path: Path | None, receipts: list[dict[str, Any]]) -> list[str]:
    if state_path is None:
        return []
    closed = [r for r in receipts if r.get("ok") and not r.get("skipped") and not r.get("simulated")]
    if not closed:
        return []
    pruned: list[str] = []
    def prune(state: dict[str, Any]) -> dict[str, Any]:
        kept = []
        for slot in state.get("slots", []):
            match = next((r for r in closed if _text(r.get("pane_id")) == _text(slot.get("pane_id"))), None)
            if match and (not _text(match.get("session_id")) or not _text(slot.get("session_id")) or _text(match.get("session_id")) == _text(slot.get("session_id"))):
                pruned.append(_text(slot.get("slot_id") or slot.get("pane_id")))
            else:
                kept.append(slot)
        state["slots"] = kept
        return state
    _with_locked_supervision_state(state_path, prune)
    return pruned

def run_gc(
    records: Iterable[dict[str, Any]],
    *,
    approve: bool = False,
    state_file: str | Path | None = None,
    handoff_root: str | Path | None = None,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    candidates = discover_gc_candidates(records, handoff_root=handoff_root)
    receipts = []
    if approve:
        for candidate in candidates:
            try:
                receipts.append({**candidate, **_close_herdr_pane(candidate, runner=runner)})
            except RouterError as error:
                receipts.append({**candidate, "ok": False, "error": str(error)})
    state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True) if approve else None
    pruned = _prune_closed_slots(state_path, receipts)
    result: dict[str, Any] = {"ok": True, "approved": approve, "dry_run": not approve, "gc_candidates": candidates, "closed_receipts": receipts, "pruned_state_slots": pruned}
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=bool(approve),
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync:
        board_state = _with_locked_supervision_state(state_path, lambda s: s)
        board_rollup = _compute_board_rollup(board_state)
        cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
        result["linear_sync"] = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    elif approve or sync_linear or no_sync_linear:
        result["linear_sync"] = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    return result
