#!/usr/bin/env python3
"""Bounded, offline agent trajectory retro for the standalone agent-retro skill.

The reader deliberately accepts both Copilot's nested ``data`` event shape and
flat fixtures. It never sends traces to a network endpoint and it treats
missing sources as unknown rather than healthy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = ROOT / "skills" / "agent-retro"
SF_DATALESS = 0x40000000
MAX_EVENTS = 200
MAX_BYTES = 2_000_000
MAX_OUTPUT = 16_000
METRIC_ORDER = (
    ("role", "R1_m3_prompt_prelock"), ("role", "R2_m2_direct_code_edit"),
    ("role", "R3_m1_inline_overwork"), ("role", "R4_session_skill_pollution"),
    ("role", "R5_reviewer_same_vendor"), ("handoff", "H1_zombie_drifted_goals"),
    ("handoff", "H2_initial_sha_false_lock"), ("handoff", "H3_r10_receipt_gap_or_dirty_schema"),
    ("handoff", "H4_compaction_and_handoff_loss"), ("invoke", "I1_cold_start_greedy_drawer_read"),
    ("invoke", "I2_mandatory_l2_cli_skip"), ("invoke", "I3_l2_exit2_and_output_bloat"),
    ("invoke", "I4_manual_poll_loop"), ("exec", "E1_blind_spin_loop_vs_progressive_catch"),
    ("exec", "E2_r3_plus_divergence"), ("exec", "E3_gate_escape_and_uncrystallized_spec_gap"),
    ("exec", "E4_unfaithful_receipt_and_cancelled_noise"), ("friction", "F1_permission_stall_count"),
    ("friction", "F2_owner_abort_rate"), ("friction", "F3_mid_flight_owner_correction"),
)
UPSTREAM = {
    "R1_m3_prompt_prelock": "personal-m2-assistant", "R2_m2_direct_code_edit": "m2-dispatcher",
    "R3_m1_inline_overwork": "team-lead", "R4_session_skill_pollution": "m2-dispatcher",
    "R5_reviewer_same_vendor": "code-review", "H1_zombie_drifted_goals": "m2-dispatcher",
    "H2_initial_sha_false_lock": "team-lead", "H3_r10_receipt_gap_or_dirty_schema": "team-lead",
    "H4_compaction_and_handoff_loss": "m2-dispatcher", "I1_cold_start_greedy_drawer_read": "agent-retro",
    "I2_mandatory_l2_cli_skip": "agent-retro", "I3_l2_exit2_and_output_bloat": "agent-retro",
    "I4_manual_poll_loop": "team-lead", "E1_blind_spin_loop_vs_progressive_catch": "agent-retro",
    "E2_r3_plus_divergence": "team-lead", "E3_gate_escape_and_uncrystallized_spec_gap": "repo-kit",
    "E4_unfaithful_receipt_and_cancelled_noise": "team-lead", "F1_permission_stall_count": "team-lead",
    "F2_owner_abort_rate": "m2-dispatcher", "F3_mid_flight_owner_correction": "personal-m2-assistant",
}
ROLE_WORDS = {"m2": ("m2", "dispatcher", "personal-m2"), "m1": ("m1", "steering", "team-lead"),
              "writer": ("writer", "coding"), "reviewer": ("reviewer", "code-review"), "m3": ("m3", "personal-m2-assistant")}


def _redact(value: Any) -> str:
    text = _text(value)
    user_prefix = re.escape(os.sep + "Users" + os.sep)
    token_prefixes = ("ghp" + "_", "lin_api" + "_", "sk" + "-")
    bearer = "Bearer" + r"\s+"
    text = re.sub(user_prefix + r"[^/\s\"']+", "~", text)
    text = re.sub("(?:" + "|".join(re.escape(prefix) for prefix in token_prefixes) + r")[A-Za-z0-9._-]+", "[REDACTED_TOKEN]", text)
    text = re.sub(bearer + r"[A-Za-z0-9._~+/=-]+", "Bearer" + " [REDACTED_TOKEN]", text, flags=re.I)
    return text


def _redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return _redact(value) if isinstance(value, str) else value


def _external_path_safe(path: Path) -> bool:
    """Perform a zero-block dataless/externally-mounted guard before stat/open."""
    raw = str(path)
    if os.environ.get("SF_DATALESS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    volume_prefix = os.sep + "Volumes" + os.sep
    mobile_documents = "Mobile " + "Documents"
    if volume_prefix not in raw and mobile_documents not in raw:
        return True
    try:
        flags = int(getattr(os.lstat(path), "st_flags", 0))
        return not bool(flags & SF_DATALESS)
    except OSError:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _data(event: dict[str, Any]) -> dict[str, Any]:
    nested = event.get("data")
    return nested if isinstance(nested, dict) else event


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or _data(event).get("eventType") or "").lower()


def _field(event: dict[str, Any], *names: str) -> Any:
    data = _data(event)
    for name in names:
        if name in data:
            return data[name]
        if name in event:
            return event[name]
    return None


def _arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = _field(event, "arguments", "args", "toolArguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"command": value}
    return {}


def _tool_name(event: dict[str, Any]) -> str:
    return str(_field(event, "toolName", "tool_name", "name") or "").lower()


def _command(event: dict[str, Any]) -> str:
    args = _arguments(event)
    return str(args.get("command") or args.get("cmd") or _field(event, "command") or "")


def _exit_code(event: dict[str, Any]) -> int | None:
    shell = _field(event, "shellExecution")
    if isinstance(shell, dict):
        value = shell.get("exitCode")
    else:
        value = _field(event, "exitCode", "exit_code")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _result_content(event: dict[str, Any]) -> str:
    result = _field(event, "result", "toolResult")
    if isinstance(result, dict):
        return _text(result.get("content") or result.get("output") or result.get("stdout") or "")
    return _text(_field(event, "resultContent", "content", "output") or result or "")


def _session_id(event: dict[str, Any], fallback: str) -> str:
    return str(_field(event, "sessionId", "session_id", "conversationId") or fallback)


def _roots(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    session_root = Path(os.environ.get("AGENT_RETRO_SESSIONS", os.environ.get("COPILOT_SESSION_ROOT", "~/.copilot/session-state"))).expanduser()
    handoff_root = Path(os.environ.get("AGENT_RETRO_HANDOFF_ROOT", "~/Development/handoff")).expanduser()
    if getattr(args, "handoff_root", None):
        handoff_root = Path(args.handoff_root).expanduser()
    elif getattr(args, "handoff_dir", None):
        handoff_root = Path(args.handoff_dir).expanduser()
    warehouse = Path(os.environ.get("AGENT_RETRO_WAREHOUSE", "~/Development/AIDash/aidata/L3_merge/warehouse.db")).expanduser()
    return session_root, handoff_root, warehouse if _external_path_safe(warehouse) and warehouse.exists() else None


def _read_events(root: Path, days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _external_path_safe(root):
        return [], {"files_seen": 0, "events_read": 0, "truncated": False, "available": False, "blocked_dataless": True}
    cutoff = _now() - timedelta(days=max(0, days))
    events: list[dict[str, Any]] = []
    files = sorted(root.glob("*/events.jsonl")) if root.is_dir() else []
    truncated = False
    for path in files[:128]:
        if len(events) >= MAX_EVENTS:
            truncated = True
            break
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index >= MAX_EVENTS or len(events) >= MAX_EVENTS:
                        truncated = True
                        break
                    if len(line) > MAX_BYTES:
                        truncated = True
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    timestamp = _parse_time(event.get("timestamp") or _field(event, "timestamp", "createdAt"))
                    if timestamp and timestamp < cutoff:
                        continue
                    event["_retro_session"] = path.parent.name
                    events.append(event)
        except OSError:
            continue
    events.sort(key=lambda e: _parse_time(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
    return events, {"files_seen": len(files), "events_read": len(events), "truncated": truncated, "available": bool(files)}


def _safe_text(path: Path, limit: int = 200_000) -> str:
    if not _external_path_safe(path):
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _handoffs(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not _external_path_safe(root):
        return records, {"directories": 0, "available": False, "blocked_dataless": True}
    if not root.is_dir():
        return records, {"directories": 0, "available": False}
    dirs = [p for p in sorted(root.iterdir()) if p.is_dir()][:128]
    for directory in dirs:
        ledger = _safe_text(directory / "ledger.md")
        spec = _safe_text(directory / "spec.md")
        briefs = [p for p in (directory / "briefs").glob("*.md")][:32] if (directory / "briefs").is_dir() else []
        receipts = [p for p in (directory / "receipts").glob("*.md")][:64] if (directory / "receipts").is_dir() else []
        records.append({"path": directory, "name": directory.name, "ledger": ledger, "spec": spec,
                        "active_lease": bool(re.search(r"\b(?:in_progress|active)\b", ledger, re.I)),
                        "briefs": [(p.name, _safe_text(p)) for p in briefs],
                        "receipts": [(p.name, _safe_text(p)) for p in receipts]})
    return records, {"directories": len(dirs), "available": True}


def _role_for(events: list[dict[str, Any]]) -> str:
    text = " ".join(_text(_data(e).get(k, "")) for e in events for k in ("role", "agentName", "agentDescription", "selectedModel"))
    low = text.lower()
    for role, words in ROLE_WORDS.items():
        if any(word in low for word in words):
            return role
    return "unknown"


def _session_groups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[_session_id(event, str(event.get("_retro_session", "unknown")))].append(event)
    if "unknown" in groups and len(groups) == 2:
        known = next(key for key in groups if key != "unknown")
        groups[known].extend(groups.pop("unknown"))
    return groups


def _tool_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if _tool_name(e) or _event_type(e).startswith("tool.")]


def _event_skill(event: dict[str, Any]) -> str | None:
    value = _field(event, "skill", "skillName", "name")
    if _event_type(event) == "skill.invoked" and value:
        return str(value)
    path = str(_arguments(event).get("path") or _field(event, "path") or "")
    match = re.search(r"skills/([^/]+)/SKILL\.md", path)
    return match.group(1) if match else None


def _skill_names(events: list[dict[str, Any]]) -> set[str]:
    names = {name for e in events if (name := _event_skill(e))}
    for e in events:
        text = _text(_field(e, "content", "message", "assistantContent", "userContent"))
        names.update(re.findall(r"skills/([A-Za-z0-9_-]+)/", text))
    return names


def _skill_requires_l2(skill: str) -> bool:
    """Derive the mandatory script contract from the skill, never from session IDs."""
    path = ROOT / "skills" / skill / "SKILL.md"
    text = _safe_text(path, 80_000).lower()
    return bool(re.search(r"mandatory l2 cli|mandatory.*scripts/|必跑 l2", text))


def _observation(metric: str, layer: str, count: int, evidence: list[str], *, skill: str | None = None,
                 sessions: int = 1, healthy: bool = False, status: str = "actionable") -> dict[str, Any]:
    resolved_skill = skill or UPSTREAM.get(metric, "unknown")
    return {"metric": metric, "layer": layer, "count": count, "skill": resolved_skill,
            "upstream_skill": resolved_skill, "scenario_sessions": sessions,
            "status": "healthy_progressive_disclosure" if healthy else status,
            "evidence": [_redact(item) for item in evidence[:5]]}


def _is_code_path(path: str) -> bool:
    personal_root = os.sep + "Users" + os.sep
    handoff_marker = os.sep + "handoff" + os.sep
    return bool(path) and not (path.startswith(personal_root) and handoff_marker in path) and not path.startswith(("handoff/", "briefs/", "receipts/"))


def _first_fault_skill(text: str, role: str = "unknown", *, ci: bool = False) -> str:
    low = text.lower()
    if "spec_gap" in low or "spec gap" in low or "design.md" in low:
        return "my-designer"
    if ci or "pr_check_failed" in low or "remote ci" in low or "gate escape" in low:
        return "repo-kit"
    if "prompt lock" in low or "hot-append" in low or "pre-lock" in low:
        return "personal-m2-assistant"
    if "scope" in low or "handoff" in low or "zombie" in low:
        return "m2-dispatcher"
    if "brief" in low or "constraint" in low:
        return "team-lead"
    return {"m3": "personal-m2-assistant", "m2": "m2-dispatcher", "m1": "team-lead"}.get(role, "team-lead")


def _goal_key(event: dict[str, Any]) -> str | None:
    value = _field(event, "goalId", "goal_id", "taskId", "task_id", "goal", "task")
    if value:
        return str(value)
    text = _text(_field(event, "content", "message", "assistantContent", "userContent"))
    match = re.search(r"(?:goal|task)[_ -]?(?:id)?\s*[:=]\s*([A-Za-z0-9._-]+)", text, re.I)
    return match.group(1) if match else None


def _vendor(model: str) -> str:
    low = model.lower()
    for family, words in {"openai": ("openai", "gpt", "o1", "o3"), "google": ("google", "gemini", "argon"),
                          "anthropic": ("anthropic", "claude"), "microsoft": ("microsoft", "copilot", "mai-")}.items():
        if any(word in low for word in words):
            return family
    return "unknown"


def _outcome_gate(groups: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], set[str]]:
    gate: dict[str, Any] = {}
    eligible: set[str] = set()
    for sid, seq in groups.items():
        blob = " ".join(_text(_data(e)) for e in seq).lower()
        rounds = len(re.findall(r"(?:change request|changes requested|review fail|r[0-9]+\s+fail)", blob))
        turns = sum(1 for e in seq if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response"})
        first_tool = next((index for index, event in enumerate(seq) if _tool_name(event)), len(seq))
        interruptions = any(_event_type(e) in {"permission.requested", "owner.message", "abort", "session.error"} or (_event_type(e) == "user.message" and seq.index(e) > first_tool) for e in seq)
        completed = any(_event_type(e) in {"goal.completed", "task.done", "session.complete", "pr.merged", "goal.done"} or re.search(r"\b(?:DONE|PR_MERGED|GOAL_DONE)\b", _text(e), re.I) for e in seq)
        faithful = not any("ungrounded" in _text(e).lower() for e in seq)
        healthy = completed and rounds <= 2 and turns <= 60 and not interruptions and faithful
        status = "OUTCOME_HEALTHY" if healthy else ("OUTCOME_AT_RISK" if completed else "OUTCOME_UNKNOWN")
        if not healthy:
            eligible.add(sid)
        gate[sid] = {"status": status, "completed": completed, "review_rounds": rounds,
                     "assistant_turns": turns, "human_interrupts": interruptions, "faithful_receipts": faithful,
                     "p90_budget": {"turn_limit": 60, "within": turns <= 60}}
    return gate, eligible


def _scoped_events(record: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens = {record["name"].lower()}
    for source in (record.get("ledger", ""), record.get("spec", "")):
        tokens.update(x.lower() for x in re.findall(r"(?:goal|task)[_ -]?(?:id)?\s*[:=]\s*([A-Za-z0-9._-]+)", source, re.I))
    tokens.discard("")
    scoped = []
    for event in events:
        goal = _goal_key(event)
        blob = _text(event).lower()
        if (goal and goal.lower() in tokens) or any(token in blob for token in tokens if len(token) >= 4):
            scoped.append(event)
    return scoped


def _metric_scan(events: list[dict[str, Any]], handoffs: list[dict[str, Any]], source: dict[str, Any],
                 profile_sessions: set[str] | None = None) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    boundary_metrics = {"R1_m3_prompt_prelock", "R2_m2_direct_code_edit", "R3_m1_inline_overwork",
                        "R5_reviewer_same_vendor", "H4_compaction_and_handoff_loss", "E4_unfaithful_receipt_and_cancelled_noise",
                        "F1_permission_stall_count", "F2_owner_abort_rate", "F3_mid_flight_owner_correction"}
    def allowed(metric: str, sid: str) -> bool:
        return profile_sessions is None or sid in profile_sessions or metric in boundary_metrics
    groups = _session_groups(events)
    goal_models: dict[str, set[str]] = defaultdict(set)
    for grouped in groups.values():
        goal_ids = {_goal_key(event) for event in grouped if _goal_key(event)}
        grouped_role = _role_for(grouped)
        if grouped_role in {"writer", "m1"}:
            for event in grouped:
                model = str(_field(event, "selectedModel", "model", "usageModel") or "")
                if model:
                    for goal_id in goal_ids:
                        goal_models[goal_id].add(_vendor(model))
    # Session-level role and invocation signals.
    for sid, seq in groups.items():
        role = _role_for(seq)
        skills = _skill_names(seq)
        tools = _tool_events(seq)
        mutation_tools = {"edit", "create", "apply_patch", "write", "patch"}
        mutation_events = [e for e in tools if _tool_name(e) in mutation_tools]
        files = set()
        for event in mutation_events:
            args = _arguments(event)
            path = str(args.get("path") or args.get("file") or _field(event, "path") or "")
            if path and _is_code_path(path):
                files.add(path)
        assistant_turns = sum(1 for e in seq if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response"})
        text = " ".join(_text(_field(e, "content", "message", "assistantContent", "userContent")) for e in seq).lower()
        if any(token in text for token in ("prompt lock-in", "hot-append", "pre-baked", "prebaked")) and role in ("m3", "unknown"):
            observations.append(_observation("R1_m3_prompt_prelock", "role", 1, [f"{sid}: prompt/pre-lock wording"], sessions=1))
        if role == "m2":
            direct = [e for e in tools if _tool_name(e) in {"edit", "create", "apply_patch", "write"} and _is_code_path(str(_arguments(e).get("path") or _field(e, "path") or ""))]
            if direct:
                observations.append(_observation("R2_m2_direct_code_edit", "role", len(direct), [f"{sid}: {_tool_name(e)} {_arguments(e).get('path', '')}" for e in direct], sessions=1))
        writer_spawned = any("writer" in _text(_data(e)).lower() or _role_for([e]) == "writer" for e in seq)
        if role == "m1" and (len(files) > 3 or (assistant_turns > 60 and not writer_spawned)):
            evidence = [f"{sid}: {len(files)} mutating source files"] if len(files) > 3 else [f"{sid}: {assistant_turns} assistant turns without Writer"]
            observations.append(_observation("R3_m1_inline_overwork", "role", max(len(files), assistant_turns), evidence, skill="team-lead", sessions=1))
        if len(skills) > 3 and allowed("R4_session_skill_pollution", sid):
            observations.append(_observation("R4_session_skill_pollution", "role", len(skills), [f"{sid}: {', '.join(sorted(skills)[:6])}"], sessions=1))
        reviewer_models = [str(_field(e, "selectedModel", "model", "usageModel", "reviewerModel", "reviewer_model") or "") for e in seq]
        peer_models = [str(_field(e, "writerModel", "writer_model", "tlModel", "tl_model", "leadModel") or "") for e in seq]
        peer_models.extend(str(_data(e).get("selectedModel") or "") for e in seq if str(_data(e).get("role", "")).lower() in {"writer", "m1", "team-lead", "tl"})
        reviewer_vendor = next((_vendor(m) for m in reviewer_models if _vendor(m) != "unknown"), "unknown")
        peer_vendors = {_vendor(m) for m in peer_models if _vendor(m) != "unknown"}
        for goal_id in {_goal_key(event) for event in seq if _goal_key(event)}:
            peer_vendors.update(vendor for vendor in goal_models.get(goal_id, set()) if vendor != "unknown")
        if role == "reviewer" and reviewer_vendor != "unknown" and reviewer_vendor in peer_vendors:
            observations.append(_observation("R5_reviewer_same_vendor", "role", 1, [f"{sid}: reviewer and Writer/TL share {reviewer_vendor}"], skill="code-review", sessions=1))
        # I1: first two turns/events read three drawers before a script invocation.
        first = seq[:20]
        drawer_reads = [e for e in first if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e))]
        script_at = next((i for i, e in enumerate(first) if "scripts/" in _command(e)), 999)
        if len(drawer_reads) >= 3 and all(first.index(e) < script_at for e in drawer_reads[:3]) and allowed("I1_cold_start_greedy_drawer_read", sid):
            drawer_skill = next((re.search(r"skills/([^/]+)/", _text(_arguments(e))).group(1) for e in drawer_reads if re.search(r"skills/([^/]+)/", _text(_arguments(e)))), None)
            observations.append(_observation("I1_cold_start_greedy_drawer_read", "invoke", len(drawer_reads), [f"{sid}: {len(drawer_reads)} reference reads before L2"], skill=drawer_skill, sessions=1))
        # I2: explicit skill invocation without any script call for its package.
        invoked = {s for e in seq if (s := _event_skill(e))}
        called_scripts = " ".join(_command(e) for e in tools)
        for skill in invoked:
            if _skill_requires_l2(skill) and f"skills/{skill}/scripts/" not in called_scripts and allowed("I2_mandatory_l2_cli_skip", sid):
                observations.append(_observation("I2_mandatory_l2_cli_skip", "invoke", 1, [f"{sid}: {skill} invoked without its script"], skill=skill, sessions=1))
        # I3, I4 and F signals.
        exit2 = [e for e in tools if _exit_code(e) == 2]
        bloat = [e for e in tools if len(_result_content(e).encode("utf-8")) > 20_000]
        if (exit2 or bloat) and allowed("I3_l2_exit2_and_output_bloat", sid):
            evidence = [f"{sid}: exit=2" for _ in exit2[:2]] + [f"{sid}: tool output >20KB" for _ in bloat[:2]]
            target_skill = next((re.search(r"skills/([^/]+)/scripts/", _command(e)).group(1) for e in exit2 + bloat if re.search(r"skills/([^/]+)/scripts/", _command(e))), None)
            observations.append(_observation("I3_l2_exit2_and_output_bloat", "invoke", len(exit2) + len(bloat), evidence, skill=target_skill, sessions=1))
        polls = [e for e in tools if re.search(r"(?:herdr\s+pane\s+read|\bsleep\b|gh\s+pr\s+view)", _command(e))]
        if len(polls) >= 3 and allowed("I4_manual_poll_loop", sid):
            observations.append(_observation("I4_manual_poll_loop", "invoke", len(polls), [f"{sid}: {len(polls)} manual polls"], sessions=1))
        permissions = [e for e in seq if "permission.requested" in _event_type(e)]
        if permissions:
            observations.append(_observation("F1_permission_stall_count", "friction", len(permissions), [f"{sid}: permission requests"], sessions=1))
        if any(x in _event_type(e) for e in seq for x in ("abort", "session.error")):
            observations.append(_observation("F2_owner_abort_rate", "friction", 1, [f"{sid}: abort/session.error"], sessions=1))
        user_messages = [e for e in seq if _event_type(e) in {"user.message", "owner.message"}]
        if user_messages and len(seq) > 4:
            observations.append(_observation("F3_mid_flight_owner_correction", "friction", len(user_messages), [f"{sid}: owner message during execution"], sessions=1))
        # Execution and receipt grounding.
        failed = []
        progressive = 0
        spin = 0
        previous = None
        fail_run = None
        for index, event in enumerate(tools):
            code, command = _exit_code(event), _command(event)
            if code == 1:
                failed.append(event)
                fail_run = (index, command)
                if previous == command:
                    spin += 1
                else:
                    spin = 1
                previous = command
            elif code == 0 and fail_run:
                failed_index = fail_run[0]
                between = tools[failed_index + 1:index]
                read_drawer = any(_tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e)) for e in between)
                if read_drawer:
                    progressive += 1
                    spin = 0
                fail_run = None
                previous = None
            elif code not in (None, 1):
                previous = None
        if progressive:
            drawer_skill = next((re.search(r"skills/([^/]+)/", _text(_arguments(e))).group(1) for e in tools if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e))), None)
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", progressive, [f"{sid}: exit=1 -> drawer -> exit=0"], skill=drawer_skill, sessions=1, healthy=True))
        if spin >= 3 and allowed("E1_blind_spin_loop_vs_progressive_catch", sid):
            target_skill = next((re.search(r"skills/([^/]+)/scripts/", _command(e)).group(1) for e in tools if re.search(r"skills/([^/]+)/scripts/", _command(e))), None)
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", spin, [f"{sid}: {spin} identical failed retries"], skill=target_skill, sessions=1))
        rounds = len(re.findall(r"(?:change request|changes requested|review fail|r[0-9]+\s+fail)", text))
        if rounds >= 3 and allowed("E2_r3_plus_divergence", sid):
            observations.append(_observation("E2_r3_plus_divergence", "exec", rounds, [f"{sid}: {rounds} review/reset signals"], skill=_first_fault_skill(text, role), sessions=1))
        ci_failure = any("PR_CHECK_FAILED" in _text(e) or ("ci" in _event_type(e) and "fail" in _text(e).lower()) for e in seq)
        if re.search(r"verif(?:y|ication)|pytest|swiftlint|scripts/verify", text) and ci_failure and allowed("E3_gate_escape_and_uncrystallized_spec_gap", sid):
            observations.append(_observation("E3_gate_escape_and_uncrystallized_spec_gap", "exec", 1, [f"{sid}: local verification followed by CI failure"], skill=_first_fault_skill(text, role, ci=True), sessions=1))
    # Cross-session / handoff signals.
    active = {sid for sid, seq in groups.items() if _role_for(seq) != "unknown"}
    for record in handoffs:
        ledger = record["ledger"].lower()
        if re.search(r"\b(?:in_progress|active|planning)\b", ledger) and not any(record["name"] in sid for sid in active):
            observations.append(_observation("H1_zombie_drifted_goals", "handoff", 1, [f"{record['name']}: active ledger without matching live session"], sessions=1))
        if "input_blocked" in ledger and re.search(r"initial|0.?diff|zero.?diff", ledger):
            observations.append(_observation("H2_initial_sha_false_lock", "handoff", 1, [f"{record['name']}: initial zero-diff INPUT_BLOCKED"], sessions=1))
        if re.search(r"\b(?:done|completed|pr_merged)\b", ledger) and not record["receipts"]:
            observations.append(_observation("H3_r10_receipt_gap_or_dirty_schema", "handoff", 1, [f"{record['name']}: completed ledger has no receipt"], sessions=1))
        spec_words = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", record["spec"].lower()))
        brief_text = " ".join(text for _, text in record["briefs"]).lower()
        related = [e for e in events if record["name"].lower() in _text(e).lower() or _goal_key(e) == record["name"]]
        h4_evidence: list[str] = []
        if any("compaction" in _event_type(e) or "context.compacted" in _event_type(e) or "context compaction" in _text(e).lower() for e in related):
            h4_evidence.append(f"{record['name']}: context compaction")
        rereads: Counter[str] = Counter()
        for event in related:
            if _tool_name(event) in {"view", "read", "cat"}:
                args = _arguments(event)
                path = str(args.get("path") or _field(event, "path") or "")
                if path and not any(args.get(key) for key in ("view_range", "viewRange", "range", "startLine", "lineRange")):
                    rereads[path] += 1
        h4_evidence.extend(f"{record['name']}: full-file reread {path}" for path, count in rereads.items() if count >= 3)
        related_text = " ".join(_text(_data(e)) for e in related).lower()
        promises = bool(re.search(r"delegate|spawn a writer|run (?:the )?verification|verify", related_text))
        mutated = any(_tool_name(e) in {"edit", "create", "apply_patch", "write", "patch"} for e in related)
        verified = any(_exit_code(e) == 0 and re.search(r"verify|pytest|unittest|swiftlint", _command(e), re.I) for e in related)
        if promises and (mutated or not verified):
            h4_evidence.append(f"{record['name']}: reasoning-action mismatch")
        if spec_words and len(spec_words - set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", brief_text))) > 8:
            h4_evidence.append(f"{record['name']}: spec constraints absent from brief")
        if h4_evidence:
            observations.append(_observation("H4_compaction_and_handoff_loss", "handoff", len(h4_evidence), h4_evidence, skill=_first_fault_skill(related_text or record["spec"], _role_for(related)), sessions=1))
        scoped_events = _scoped_events(record, events)
        for name, receipt in record["receipts"]:
            if re.search(r"(?:pytest|unittest|swiftlint|scripts/verify|PR_MERGED|merge)", receipt, re.I):
                verified_commands = {_command(e).strip() for e in scoped_events if _exit_code(e) == 0 and _command(e).strip()}
                claims: list[str] = []
                for line in receipt.splitlines():
                    match = re.search(r"(?:`|^\s*[-*]?\s*)(python3\s+[^`\n]+|pytest\s+[^`\n]+|unittest\s+[^`\n]+|swiftlint\s+[^`\n]+|(?:\./)?scripts/verify(?:\s+[^`\n]+)?)", line, re.I)
                    if match:
                        claim = re.sub(r"\s+", " ", match.group(1).strip())
                        claims.append(re.split(r"\s+(?:—|--|:)?\s+(?:PASS|OK|exit=0)\b", claim, maxsplit=1, flags=re.I)[0].strip())
                missing = [claim for claim in claims if claim not in verified_commands]
                merge_claimed = bool(re.search(r"\bPR_MERGED\b|merge commit", receipt, re.I))
                merge_grounded = any(("merged" in _event_type(e) or "pr_merged" in _text(e).lower() or ("git merge" in _command(e) and _exit_code(e) == 0)) for e in scoped_events)
                cancelled_false_failure = bool(re.search(r"CANCELLED", receipt, re.I) and re.search(r"PR_CHECK_FAILED", receipt, re.I))
                ledger_cancelled = bool(re.search(r"CANCELLED", record["ledger"], re.I) and re.search(r"PR_CHECK_FAILED", record["ledger"], re.I))
                if missing or (merge_claimed and not merge_grounded) or cancelled_false_failure or ledger_cancelled:
                    observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", len(missing) or 1, [f"{record['name']}/{name}: scoped receipt lacks exact evidence"], skill="team-lead", sessions=1))
    # CI cancellation is not a failure; remove false positive evidence from raw scan by only emitting when explicit failure.
    for e in events:
        blob = _text(e).upper()
        if "CANCELLED" in blob and "PR_CHECK_FAILED" in blob:
            observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", 1, ["CANCELLED run was classified as PR_CHECK_FAILED"], sessions=1))
    return observations


def _version_watermark(events: list[dict[str, Any]] | None = None, observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip())
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD:skills/agent-retro"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        modified = int(subprocess.check_output(["git", "log", "-1", "--format=%ct", "--", "skills/agent-retro"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        count, sha, modified = 0, "unknown", 0
    current = f"v{count}@{sha}"
    sessions: dict[str, tuple[datetime | None, str]] = {}
    scenario_sessions: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"pre_fix": set(), "post_fix": set()})
    for event in events or []:
        sid = _session_id(event, str(event.get("_retro_session", "unknown")))
        timestamp = _parse_time(event.get("timestamp") or _field(event, "timestamp", "createdAt"))
        version = str(_field(event, "skillVersion", "skill_version", "version") or "")
        prior = sessions.get(sid)
        sessions[sid] = (min((prior[0], timestamp), key=lambda x: x or datetime.max.replace(tzinfo=timezone.utc)) if prior else timestamp, version or (prior[1] if prior else ""))
        skill = _event_skill(event)
        if skill:
            bucket = "pre_fix" if (version and version != current) or (timestamp and modified and timestamp.timestamp() < modified) else "post_fix"
            scenario_sessions[skill][bucket].add(sid)
    totals = {"pre_fix": {"anomalies": 0, "scenario_sessions": 0, "by_skill": {}},
              "post_fix": {"anomalies": 0, "scenario_sessions": 0, "by_skill": {}}}
    for skill, buckets in scenario_sessions.items():
        for bucket, ids in buckets.items():
            totals[bucket]["scenario_sessions"] += len(ids)
            totals[bucket]["by_skill"].setdefault(skill, {"anomalies": 0, "scenario_sessions": len(ids)})["scenario_sessions"] = len(ids)
    for item in observations or []:
        skill = item.get("upstream_skill") or item.get("skill") or "unknown"
        scope = str(item.get("evidence", [""])[0]).split(":", 1)[0]
        bucket = "pre_fix" if scope in {sid for sid, (ts, version) in sessions.items() if (version and version != current) or (ts and modified and ts.timestamp() < modified)} else "post_fix"
        totals[bucket]["anomalies"] += int(item.get("count", 0) or 0)
        row = totals[bucket]["by_skill"].setdefault(skill, {"anomalies": 0, "scenario_sessions": 0})
        row["anomalies"] += int(item.get("count", 0) or 0)
    return {"current": current, "last_modified_epoch": modified, "pre_fix": totals["pre_fix"], "post_fix": totals["post_fix"],
            "transition": f"v{max(0, count - 1)}@previous -> {current}"}


def _scan(args: argparse.Namespace) -> dict[str, Any]:
    sessions, handoff_root, warehouse = _roots(args)
    events, event_source = _read_events(sessions, int(getattr(args, "days", 30) or 30))
    handoffs, handoff_source = _handoffs(handoff_root)
    outcome_gate, profile_sessions = _outcome_gate(_session_groups(events))
    for record in handoffs:
        if re.search(r"\b(?:done|completed|pr_merged)\b", record["ledger"], re.I):
            outcome_gate[f"handoff:{record['name']}"] = {"status": "OUTCOME_HEALTHY" if record["receipts"] else "OUTCOME_AT_RISK", "faithful_receipts": bool(record["receipts"]), "goal": record["name"]}
    observations = _metric_scan(events, handoffs, event_source, profile_sessions)
    active_leases = _active_leases(handoffs)
    layers = set(filter(None, str(getattr(args, "layer", "") or "").split(",")))
    role_filter = str(getattr(args, "role", "") or "").lower()
    if layers:
        observations = [x for x in observations if x["layer"] in layers]
    if role_filter:
        role_skills = {
            "m3": {"personal-m2-assistant"}, "m2": {"m2-dispatcher", "personal-m2-assistant"},
            "m1": {"team-lead"}, "writer": {"team-lead", "agent-retro"},
            "reviewer": {"code-review", "team-lead"},
        }
        allowed = role_skills.get(role_filter, {role_filter})
        observations = [x for x in observations if x.get("upstream_skill") in allowed or role_filter in x.get("skill", "").lower()]
    metrics: list[dict[str, Any]] = []
    counts = Counter(x["metric"] for x in observations if x["status"] != "healthy_progressive_disclosure")
    for layer, metric in METRIC_ORDER:
        healthy = any(x["metric"] == metric and x["status"] == "healthy_progressive_disclosure" for x in observations)
        missing_coverage = (not event_source["available"] and not handoff_source["available"]) or event_source["truncated"]
        metrics.append({"metric": metric, "layer": layer, "count": counts.get(metric, 0), "status": "healthy_progressive_disclosure" if healthy and not counts.get(metric) else ("unknown" if missing_coverage and not counts.get(metric) else "observed")})
    actionable = [x for x in observations if x["status"] != "healthy_progressive_disclosure"]
    return {"schema": "agent-retro-probe/v1", "ok": True, "entry_mode": "fast" if getattr(args, "fast", False) else "bounded", "role_filter": role_filter or None,
            "source": {"events": event_source, "handoff": handoff_source, "warehouse": "available" if warehouse else "missing_safe_degradation"},
            "coverage": {"events": len(events), "sessions": len(_session_groups(events)), "observations": len(observations), "incomplete": bool(event_source["truncated"])},
            "outcome_gate": outcome_gate, "active_leases": sorted(active_leases), "excluded_by_active_lease": [],
            "version_watermark": _version_watermark(events, observations), "metrics": metrics, "observations": actionable[:100],
            "healthy_exemptions": [x for x in observations if x["status"] == "healthy_progressive_disclosure"],
            "observation_pool": [x for x in actionable if x["count"] < 3], "k_cap": min(3, len({x['upstream_skill'] for x in actionable if x['upstream_skill'] not in active_leases}))}


def _compact_print(payload: Any, as_json: bool, summary: bool = False) -> None:
    payload = _redact_obj(payload)
    if as_json:
        text = json.dumps(payload, ensure_ascii=False, indent=None if summary else 2)
    else:
        if isinstance(payload, dict):
            text = "\n".join([f"agent-retro: {payload.get('schema', 'ok')}", f"observations={len(payload.get('observations', []))} k={payload.get('k_cap', 0)}", "unknown sources remain unknown; no issue was published."])
        else:
            text = _text(payload)
    if len(text) > MAX_OUTPUT and isinstance(payload, dict):
        reduced = {"schema": payload.get("schema", "agent-retro/v1"), "ok": payload.get("ok", True),
                   "truncated_output": True, "coverage": payload.get("coverage"), "metrics": payload.get("metrics", []),
                   "observations": payload.get("observations", [])[:5], "k_cap": payload.get("k_cap", 0)}
        for key in ("issue_verified", "dry_run", "published", "written", "config", "path", "action"):
            if key in payload:
                reduced[key] = payload[key]
        text = json.dumps(reduced, ensure_ascii=False, separators=(",", ":")) if as_json else text[:MAX_OUTPUT - 40] + "\n... output truncated"
        if len(text) > MAX_OUTPUT and as_json:
            text = json.dumps({"schema": reduced["schema"], "ok": reduced["ok"], "truncated_output": True}, ensure_ascii=False, separators=(",", ":"))
    print(_redact(text))


def _load_issue_renderer():
    path = ROOT / "skills" / "issue-brief" / "scripts" / "verify_issue_format.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("agent_retro_issue_renderer", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError):
        return None
    return module


def _safe_write(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(_redact(text), encoding="utf-8")
    return True


def _active_leases(handoffs: list[dict[str, Any]]) -> set[str]:
    leases: set[str] = set()
    for record in handoffs:
        if not record.get("active_lease"):
            continue
        match = re.search(r"agent-retro-([A-Za-z0-9_-]+)", record["name"])
        if match:
            leases.add(match.group(1))
        for source in (record.get("spec", ""), record.get("ledger", "")):
            for skill in re.findall(r"skills/([A-Za-z0-9_-]+)/", source):
                leases.add(skill)
    worktrees = Path(os.environ.get("AGENT_RETRO_WORKTREES", "~/Development/worktrees")).expanduser()
    if _external_path_safe(worktrees) and worktrees.is_dir():
        for directory in worktrees.iterdir():
            match = re.match(r"agent-retro-([A-Za-z0-9_-]+)", directory.name)
            if match and directory.is_dir():
                leases.add(match.group(1))
    return leases


def _selected(scan: dict[str, Any], pick_top: int = 3, skill: str | None = None) -> list[dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    priority = {"E4_unfaithful_receipt_and_cancelled_noise": 100, "R2_m2_direct_code_edit": 95, "I3_l2_exit2_and_output_bloat": 90, "E1_blind_spin_loop_vs_progressive_catch": 80}
    active_leases = set(scan.get("active_leases", []))
    excluded = set(scan.get("excluded_by_active_lease", []))
    for item in scan.get("observations", []):
        if skill and item.get("upstream_skill") != skill and item.get("skill") != skill:
            continue
        candidate_skill = item.get("upstream_skill") or item.get("skill") or "unknown"
        if not skill and candidate_skill in active_leases:
            excluded.add(candidate_skill)
            continue
        if item.get("count", 0) < 3 and item["metric"] not in priority:
            continue
        key = item.get("upstream_skill") or item.get("skill") or "unknown"
        score = item.get("count", 0) + priority.get(item["metric"], 0)
        if key not in by_skill or score > by_skill[key].get("_score", -1):
            copy = dict(item); copy["_score"] = score; by_skill[key] = copy
    chosen = sorted(by_skill.values(), key=lambda x: (-x["_score"], x["upstream_skill"]))[:pick_top]
    for item in chosen:
        item.pop("_score", None)
    scan["excluded_by_active_lease"] = sorted(excluded)
    return chosen


def _brief_text(skill: str, item: dict[str, Any], root: Path) -> tuple[str, str, str]:
    owned = f"skills/{skill}/**\nscripts/test_{skill.replace('-', '_')}*.py"
    spec = (f"# Agent Retro remediation: {skill}\n\nGoal: address `{item['metric']}` using the first-fault evidence below.\n\n"
            f"## Owned Paths\n- {owned}\n\n## Interfaces\n- Consumes: events.jsonl, handoff receipts, optional aidata (read-only).\n"
            "- Produces: one physical lock, positive replay, counter-case replay, and R10 receipt.\n\n"
            f"## Acceptance\n- Preserve progressive disclosure exemption when applicable.\n- Attribute the first upstream skill, not downstream symptoms.\n- Run targeted tests and a strict skill-contract audit.\n\nEvidence: {json.dumps(item, ensure_ascii=False)}\n")
    brief = (f"# Writer brief — agent-retro remediation for {skill}\n\n## Owned Paths\n- {owned}\n- {root}/receipts/* (R10 only)\n\n## Required behavior\nImplement the smallest physical lock for `{item['metric']}`. Do not change unrelated skills, CI policy, or public secrets. Add positive and counter-case fixtures.\n\n## Verification\nRun the targeted unit test, `skill_contract.py audit --skill {skill} --strict`, and record exact exit codes in R10.\n")
    ledger = (f"# Goal: agent-retro-{skill}\n\nstatus: ready\n\n## Owned Paths\n- {owned}\n\n## Interfaces\n- Consumes: events.jsonl, handoff receipts, optional aidata.\n- Produces: physical lock, positive/counter replay, R10 receipt.\n\n## Acceptance\n- Exact targeted test and strict audit exit 0.\n- No live issue publication or schedule mutation during validation.\n")
    return spec, brief, ledger


def command_probe(args: argparse.Namespace) -> dict[str, Any]:
    return _scan(args)


def _mock_jev(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def command_triage(args: argparse.Namespace) -> dict[str, Any]:
    scan = _scan(args)
    selected = _selected(scan, 1 if args.pick_one else 3)
    mock = _mock_jev(args.mock_jev_json)
    root = Path(args.emit_briefs).expanduser() if args.emit_briefs else None
    emitted = []
    if root:
        for item in selected:
            skill = re.sub(r"[^A-Za-z0-9_-]+", "-", str(item.get("upstream_skill", "unknown"))).strip("-")
            target = root / f"agent-retro-{skill}"
            spec, brief, ledger = _brief_text(skill, item, target)
            emitted.append({"skill": skill, "spec": _safe_write(target / "spec.md", spec), "brief": _safe_write(target / "briefs" / "agent-retro.md", brief), "ledger": _safe_write(target / "ledger.md", ledger), "path": str(target)})
    return {"schema": "agent-retro-triage/v1", "ok": True, "picked": selected, "picked_count": len(selected), "healthy_no_issues": not selected,
            "excluded_by_active_lease": sorted(scan.get("excluded_by_active_lease", [])),
            "worker_role_slot": {"role": "explorer", "tier": "worker", "tokens": 0},
            "jev": {"used": bool(args.use_jev), "bounded_seconds": 1.5, "mock": bool(mock), "mock_payload": mock},
            "emitted": emitted, "m2_dispatch_plan": {"schema": "agent-retro-m2-dispatch-plan/v1", "m2_coordinator_count": 1,
                "k": len(selected), "skills": [x.get("upstream_skill") for x in selected], "honest_cap": 3}}


def command_diagnose(args: argparse.Namespace) -> dict[str, Any]:
    scan = _scan(args)
    matches = [x for x in scan["observations"] if x.get("upstream_skill") == args.skill or x.get("skill") == args.skill]
    return {"schema": "agent-retro-diagnose/v1", "ok": True, "skill": args.skill, "observations": matches,
            "first_fault": matches[0] if matches else None, "status": "actionable" if matches else "HEALTHY_NO_ISSUES",
            "positive_counter_replay": {"required": bool(matches), "positive": True, "counter": True}}


def command_emit(args: argparse.Namespace) -> dict[str, Any]:
    scan = _scan(args)
    selected = _selected(scan, 1 if args.pick_one else 3, args.skill)
    root = Path(args.emit_briefs or "~/Development/handoff").expanduser()
    emitted = []
    for item in selected:
        skill = str(item.get("upstream_skill", "unknown"))
        target = root / f"agent-retro-{skill}"
        spec, brief, ledger = _brief_text(skill, item, target)
        emitted.append({"skill": skill, "path": str(target), "spec_created": _safe_write(target / "spec.md", spec), "brief_created": _safe_write(target / "briefs" / "agent-retro.md", brief), "ledger_created": _safe_write(target / "ledger.md", ledger)})
    return {"schema": "agent-retro-emit-briefs/v1", "ok": True, "k": len(emitted), "emitted": emitted, "honest_cap": 3,
            "excluded_by_active_lease": sorted(scan.get("excluded_by_active_lease", []))}


def _receipts(root: Path) -> list[dict[str, Any]]:
    rows = []
    if not _external_path_safe(root) or not root.is_dir():
        return rows
    for directory in sorted(root.iterdir())[:128]:
        receipt_dir = directory / "receipts"
        if not receipt_dir.is_dir():
            continue
        for path in sorted(receipt_dir.glob("*.md"))[:16]:
            text = _safe_text(path, 20_000)
            rows.append({"path": _redact(str(path)), "status": next((s for s in ("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY") if s in text), "UNKNOWN"), "text": _redact(text[:1000])})
    return rows[:100]


def command_rollup(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    scan = _scan(argparse.Namespace(days=30, role=None, layer=None, fast=True, handoff_root=str(root), handoff_dir=None))
    receipts = _receipts(root)
    day = args.date or datetime.now().date().isoformat()
    payload = {"schema": "agent-retro-rollup/v1", "date": day, "observations": scan["observations"][:30], "metrics": scan["metrics"], "receipts": receipts[:10], "observation_pool": scan["observation_pool"][:20], "version_watermark": scan["version_watermark"]}
    markdown = None
    verified = None
    if args.issue_brief:
        renderer = _load_issue_renderer()
        items = [{"what": f"核对技能 {x['upstream_skill']} 的首错证据", "why": f"复现 {x['metric']} 的 {x['count']} 次可观察信号"} for x in _selected(scan, 3)]
        if not items:
            items = [{"what": "保留当前观测池并等待更多场景", "why": "当前没有达到派发阈值的可执行异常"}]
        if renderer:
            markdown = renderer.render_dual_zone_issue(tldr=f"本次轨迹复盘覆盖 {scan['coverage']['sessions']} 个会话，派发上限为 {scan['k_cap']} 个技能。", items=items, cursor=f"agent-retro-{day}", heading=f"[Agent Retro {day}]", ai_payload=payload)
            verified = renderer.verify_dual_zone_issue(markdown)
    return {"schema": "agent-retro-rollup/v1", "ok": True, "date": day, "receipts": len(receipts), "k": scan["k_cap"], "payload": payload, "issue_markdown": markdown, "issue_verified": verified}


def command_file_issue(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    roll = command_rollup(argparse.Namespace(handoff_root=str(root), date=args.date, issue_brief=True))
    dry_run = not args.apply
    return {"schema": "agent-retro-file-issue/v1", "ok": True, "dry_run": dry_run, "published": False if dry_run else False,
            "server": args.server, "workspace": args.workspace, "owner": args.owner, "agent": args.agent,
            "dedup_key": f"[Agent Retro {roll['date']}]", "issue_markdown": roll.get("issue_markdown"),
            "action": "would publish after explicit integration approval" if dry_run else "apply requested; network publisher intentionally not bundled"}


def command_autopilot(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    config = {"name": "Daily Agent Trajectory Retro Autopilot", "schedule": {"cron": "40 4 * * *", "timezone": "Asia/Shanghai", "mode": "run_only"},
              "command": "python3 skills/agent-retro/scripts/agent_retro.py rollup --issue-brief --json", "skill": "agent-retro"}
    path = root / "agent-retro-autopilot.json"
    written = False
    if args.apply:
        written = _safe_write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return {"schema": "agent-retro-autopilot/v1", "ok": True, "dry_run": not args.apply, "config": config, "path": str(path), "written": written, "skill_retro_schedule_untouched": True}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self._print_message(f"agent-retro: {message}\nSee skills/agent-retro/references/agent-retro-playbook.md\n", sys.stderr)
        raise SystemExit(2)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--role")
    parser.add_argument("--layer", help="comma-separated metric layers")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--dir", dest="handoff_dir")
    parser.add_argument("--handoff-dir", dest="handoff_dir")
    parser.add_argument("--handoff-root", dest="handoff_root")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="agent_retro.py", description="Bounded offline multi-agent trajectory retro")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="fast bounded five-layer probe"); _common(p)
    p = sub.add_parser("triage", help="cheap worker triage"); _common(p); p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3); p.add_argument("--pick-one", action="store_true"); p.add_argument("--use-jev", action="store_true"); p.add_argument("--mock-jev-json"); p.add_argument("--emit-briefs")
    p = sub.add_parser("diagnose", help="deep attribution for one skill"); _common(p); p.add_argument("--skill", required=True)
    p = sub.add_parser("emit-briefs", help="write non-clobbering specs and briefs"); _common(p); p.add_argument("--skill"); p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3); p.add_argument("--pick-one", action="store_true"); p.add_argument("--emit-briefs")
    p = sub.add_parser("rollup", help="aggregate receipts into a Dual-Zone issue"); _common(p); p.add_argument("--date"); p.add_argument("--issue-brief", action="store_true")
    p = sub.add_parser("file-issue", help="prepare or explicitly apply an issue publication"); _common(p); p.add_argument("--date"); p.add_argument("--server", default=""); p.add_argument("--workspace", default=""); p.add_argument("--owner", default=""); p.add_argument("--agent", default="agent-retro"); p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("setup-autopilot", help="prepare the independent run_only schedule"); _common(p); p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False); p.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe": result = command_probe(args)
        elif args.command == "triage": result = command_triage(args)
        elif args.command == "diagnose": result = command_diagnose(args)
        elif args.command == "emit-briefs": result = command_emit(args)
        elif args.command == "rollup": result = command_rollup(args)
        elif args.command == "file-issue": result = command_file_issue(args)
        else: result = command_autopilot(args)
        _compact_print(result, args.json, getattr(args, "summary", False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"agent-retro: {exc}\nSee skills/agent-retro/references/agent-retro-playbook.md", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
