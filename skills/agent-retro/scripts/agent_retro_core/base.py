#!/usr/bin/env python3
"""Bounded, offline agent trajectory retro for the standalone agent-retro skill.

The reader deliberately accepts both Copilot's nested ``data`` event shape and
flat fixtures. It never sends traces to a network endpoint and it treats
missing sources as unknown rather than healthy.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = ROOT / "skills" / "agent-retro"
SF_DATALESS = 0x40000000
MAX_EVENTS = 150_000
MAX_EVENTS_PER_SESSION = 4_000
MAX_BYTES = 2_000_000
MAX_OUTPUT = 16_000
_TARGET_WATERMARK_CACHE: dict[str, dict[str, Any]] | None = None
_CROSS_SKILL_CACHE: dict[str, Any] | None = None
DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
DEFAULT_AUTOPILOT_TITLE = "Daily Agent Trajectory Retro Autopilot (Auto-Fix + M3 Issue)"
DEFAULT_TRIGGER_LABEL = "Daily CST 04:40 Agent Trajectory Retro Diagnostic"
DEFAULT_CRON = "40 4 * * *"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_COLLECT_OUTPUT = Path("/tmp/multica-agent-retro-collect.json")
CST = timezone(timedelta(hours=8))


def _default_agent_retro_watermark_path() -> Path:
    override = os.environ.get("AGENT_RETRO_WATERMARK_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "multica-agent-retro-autopilot" / "agent-retro-watermark.json"


HOOK_NOISE = ("hook.start", "hook.end", "session.usage_checkpoint", "skill.context_delivered_ref")
METRIC_ORDER = (
    ("role", "R1_m3_prompt_prelock"), ("role", "R2_m2_direct_code_edit"),
    ("role", "R3_m1_inline_overwork"), ("role", "R4_session_skill_pollution"),
    ("role", "R5_reviewer_same_vendor"), ("handoff", "H1_zombie_drifted_goals"),
    ("handoff", "H2_initial_sha_false_lock"), ("handoff", "H3_r10_receipt_gap_or_dirty_schema"),
    ("handoff", "H4_compaction_and_handoff_loss"), ("handoff", "H5_cross_skill_contract_drift"),
    ("invoke", "I1_cold_start_greedy_drawer_read"),
    ("invoke", "I2_mandatory_l2_cli_skip"), ("invoke", "I3_l2_exit2_and_output_bloat"),
    ("invoke", "I4_manual_poll_loop"), ("invoke", "I5_cross_agent_wild_glue"),
    ("exec", "E1_blind_spin_loop_vs_progressive_catch"),
    ("exec", "E2_r3_plus_divergence"), ("exec", "E3_gate_escape_and_uncrystallized_spec_gap"),
    ("exec", "E4_unfaithful_receipt_and_cancelled_noise"), ("friction", "F1_permission_stall_count"),
    ("friction", "F2_owner_abort_rate"), ("friction", "F3_mid_flight_owner_correction"),
    ("horizon", "HZN1_context_carry_tax"), ("horizon", "HZN2_wakeup_lag_and_missed_parallelism"),
    ("horizon", "HZN3_rework_detour_and_premature_full_verify"),
    ("horizon", "HZN4_context_fatigue_inflection"), ("horizon", "HZN5_steering_overkill_mechanical_span"),
)
UPSTREAM = {
    "R1_m3_prompt_prelock": "personal-m2-assistant", "R2_m2_direct_code_edit": "m2-dispatcher",
    "R3_m1_inline_overwork": "team-lead", "R4_session_skill_pollution": "m2-dispatcher",
    "R5_reviewer_same_vendor": "code-review", "H1_zombie_drifted_goals": "m2-dispatcher",
    "H2_initial_sha_false_lock": "team-lead", "H3_r10_receipt_gap_or_dirty_schema": "team-lead",
    "H4_compaction_and_handoff_loss": "m2-dispatcher", "H5_cross_skill_contract_drift": "agent-retro",
    "I1_cold_start_greedy_drawer_read": "agent-retro",
    "I2_mandatory_l2_cli_skip": "agent-retro", "I3_l2_exit2_and_output_bloat": "agent-retro",
    "I4_manual_poll_loop": "team-lead", "I5_cross_agent_wild_glue": "m2-dispatcher",
    "E1_blind_spin_loop_vs_progressive_catch": "agent-retro",
    "E2_r3_plus_divergence": "team-lead", "E3_gate_escape_and_uncrystallized_spec_gap": "repo-kit",
    "E4_unfaithful_receipt_and_cancelled_noise": "team-lead", "F1_permission_stall_count": "team-lead",
    "F2_owner_abort_rate": "m2-dispatcher", "F3_mid_flight_owner_correction": "personal-m2-assistant",
    "HZN1_context_carry_tax": "agent-retro", "HZN2_wakeup_lag_and_missed_parallelism": "m2-dispatcher",
    "HZN3_rework_detour_and_premature_full_verify": "agent-retro", "HZN4_context_fatigue_inflection": "team-lead",
    "HZN5_steering_overkill_mechanical_span": "team-lead",
}
ROLE_WORDS = {"m2": ("m2", "dispatcher", "personal-m2"), "m1": ("m1", "steering", "team-lead"),
              "writer": ("writer", "coding"), "reviewer": ("reviewer", "code-review"), "m3": ("m3", "personal-m2-assistant")}
_SKILL_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def is_valid_skill_slug(value: Any, *, require_repo_dir: bool = False) -> bool:
    """Reject glob/placeholder tokens before a skill name reaches probe or triage."""
    candidate = str(value or "").strip()
    if not _SKILL_SLUG.fullmatch(candidate):
        return False
    return not require_repo_dir or (ROOT / "skills" / candidate).is_dir()


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


def _value_text(value: Any) -> str:
    return value if isinstance(value, str) else _text(value)


def _event_blob(event: dict[str, Any]) -> str:
    parts = [_event_type(event)]
    for name in ("content", "message", "assistantContent", "userContent", "role",
                 "agentName", "agentDisplayName", "agentDescription", "status"):
        value = _field(event, name)
        if value:
            parts.append(_value_text(value))
    if _event_type(event).startswith("tool."):
        command = _command(event)
        if command:
            parts.append(command)
        path = str(_arguments(event).get("path") or _field(event, "path") or "")
        if path:
            parts.append(path)
    return " ".join(parts).lower()


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
    cached = event.get("_retro_arguments")
    if isinstance(cached, dict):
        return cached
    value = _field(event, "arguments", "args", "toolArguments")
    if isinstance(value, dict):
        event["_retro_arguments"] = value
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                event["_retro_arguments"] = parsed
                return parsed
        except json.JSONDecodeError:
            parsed = {"command": value}
            event["_retro_arguments"] = parsed
            return parsed
    event["_retro_arguments"] = {}
    return event["_retro_arguments"]


def _tool_name(event: dict[str, Any]) -> str:
    if "_retro_tool_name" not in event:
        event["_retro_tool_name"] = str(_field(event, "toolName", "tool_name", "name") or "").lower()
    return event["_retro_tool_name"]


def _command(event: dict[str, Any]) -> str:
    if "_retro_command" not in event:
        args = _arguments(event)
        event["_retro_command"] = str(args.get("command") or args.get("cmd") or _field(event, "command") or "")
    return event["_retro_command"]


def _exit_code(event: dict[str, Any]) -> int | None:
    if "_retro_exit_code" in event:
        return event["_retro_exit_code"]
    shell = _field(event, "shellExecution")
    if isinstance(shell, dict):
        value = shell.get("exitCode")
    else:
        value = _field(event, "exitCode", "exit_code")
    try:
        if value is not None:
            code = int(value)
            event["_retro_exit_code"] = code
            return code
    except (TypeError, ValueError):
        pass
    content = _result_content(event)
    match = re.search(r"(?:<shellId:\s*[^>\n]*\s+)?completed with exit code\s+(\d+)", content, re.I)
    if match:
        code = int(match.group(1))
        event["_retro_exit_code"] = code
        return code
    success = _field(event, "success")
    if success is True:
        event["_retro_exit_code"] = 0
        return 0
    if success is False:
        event["_retro_exit_code"] = 1
        return 1
    event["_retro_exit_code"] = None
    return None


def _result_content(event: dict[str, Any]) -> str:
    if "_retro_result_content" in event:
        return event["_retro_result_content"]
    result = _field(event, "result", "toolResult")
    if isinstance(result, dict):
        content = _text(result.get("content") or result.get("output") or result.get("stdout") or "")
    else:
        content = _text(_field(event, "resultContent", "content", "output") or result or "")
    event["_retro_result_content"] = content
    return content


def _tool_call_id(event: dict[str, Any]) -> str:
    return str(_field(event, "toolCallId", "tool_call_id", "callId", "call_id") or "")


def _is_hook_noise(event: dict[str, Any]) -> bool:
    return _event_type(event) in HOOK_NOISE


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


def _merge_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Copilot's split tool start/complete records without double-counting."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("_retro_session", "unknown"))].append(event)
    merged: list[dict[str, Any]] = []
    for sequence in grouped.values():
        output: list[dict[str, Any]] = []
        pending: dict[str, int] = {}
        for event in sequence:
            event_type = _event_type(event)
            call_id = _tool_call_id(event)
            if event_type == "tool.execution_start" and call_id:
                pending[call_id] = len(output)
                output.append(event)
                continue
            if event_type == "tool.execution_complete" and call_id in pending:
                start_index = pending.pop(call_id)
                start = output[start_index]
                start_data = _data(start)
                complete_data = _data(event)
                combined = dict(event)
                combined_data = dict(start_data)
                combined_data.update(complete_data)
                for key in ("toolName", "tool_name", "arguments", "args", "toolArguments"):
                    if key not in combined_data and key in start_data:
                        combined_data[key] = start_data[key]
                combined["data"] = combined_data
                combined["_retro_session"] = event.get("_retro_session", start.get("_retro_session"))
                output[start_index] = combined
                continue
            output.append(event)
        merged.extend(output)
    return merged


def _read_events(root: Path, days: int, *, fast: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _external_path_safe(root):
        return [], {"files_seen": 0, "events_read": 0, "truncated": False, "available": False, "blocked_dataless": True}
    cutoff = _now() - timedelta(days=max(0, days))
    events: list[dict[str, Any]] = []
    candidates: list[tuple[float, Path]] = []
    if root.is_dir():
        for path in root.glob("*/events.jsonl"):
            if not _external_path_safe(path):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, path))
    in_window = [item for item in candidates if item[0] >= cutoff.timestamp()]
    source_files = in_window if len(in_window) >= 10 else candidates
    max_sessions = 50 if fast else 80
    source_files.sort(key=lambda item: item[0], reverse=True)
    files = [path for _, path in source_files[:max_sessions]]
    truncated = False
    bounded_sessions = 0
    oversized_lines = 0
    for path in files:
        if len(events) >= MAX_EVENTS:
            truncated = True
            break
        session_events = 0
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if len(events) >= MAX_EVENTS:
                        truncated = True
                        break
                    if session_events >= MAX_EVENTS_PER_SESSION:
                        bounded_sessions += 1
                        break
                    if any(f'"type":"{noise}"' in line.replace(" ", "") or f'"type": "{noise}"' in line for noise in HOOK_NOISE):
                        continue
                    if len(line) > MAX_BYTES:
                        oversized_lines += 1
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if _is_hook_noise(event):
                        continue
                    timestamp = _parse_time(event.get("timestamp") or _field(event, "timestamp", "createdAt"))
                    if timestamp and timestamp < cutoff:
                        continue
                    event["_retro_session"] = path.parent.name
                    events.append(event)
                    session_events += 1
        except OSError:
            continue
    events = _merge_tool_events(events)
    events.sort(key=lambda e: _parse_time(e.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
    return events, {"files_seen": len(candidates), "sessions_scanned": len(files), "events_read": len(events),
                    "bounded_sessions": bounded_sessions, "oversized_lines": oversized_lines,
                    "truncated": truncated, "available": bool(candidates)}


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
    worktrees = Path(os.environ.get("AGENT_RETRO_WORKTREES", "~/Development/worktrees")).expanduser()
    ranked: list[tuple[float, Path]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or not _external_path_safe(directory):
            continue
        ledger_path = directory / "ledger.md"
        try:
            mtime = ledger_path.stat().st_mtime if ledger_path.exists() else directory.stat().st_mtime
        except OSError:
            continue
        ranked.append((mtime, directory))
    ranked.sort(key=lambda item: item[0], reverse=True)
    dirs = [directory for _, directory in ranked[:128]]
    for directory in dirs:
        ledger_path = directory / "ledger.md"
        ledger = _safe_text(ledger_path)
        spec = _safe_text(directory / "spec.md")
        briefs = [p for p in (directory / "briefs").glob("*.md")][:32] if (directory / "briefs").is_dir() else []
        receipts = [p for p in (directory / "receipts").glob("*.md")][:64] if (directory / "receipts").is_dir() else []
        try:
            ledger_mtime = ledger_path.stat().st_mtime if ledger_path.exists() else directory.stat().st_mtime
        except OSError:
            ledger_mtime = 0.0
        recent = _now().timestamp() - ledger_mtime <= 7200
        matching_worktree = _external_path_safe(worktrees / directory.name) and (worktrees / directory.name).is_dir()
        active = (
            bool(re.search(r"\b(?:in_progress|active)\b", ledger, re.I))
            and not re.search(r"EVT=GOAL_DONE|\bstatus:\s*(?:done|completed)\b", ledger, re.I)
            and (recent or matching_worktree)
        )
        records.append({"path": directory, "name": directory.name, "ledger": ledger, "spec": spec,
                        "ledger_mtime": ledger_mtime, "active_lease": active,
                        "briefs": [(p.name, _safe_text(p)) for p in briefs],
                        "receipts": [(p.name, _safe_text(p)) for p in receipts]})
    return records, {"directories": len(dirs), "available": True}


def _role_for(events: list[dict[str, Any]]) -> str:
    explicit = next((str(_field(event, "role")).lower() for event in events if _field(event, "role")), "")
    explicit_map = {
        "team-lead": "m1", "m1": "m1", "steering": "m1",
        "m2-dispatcher": "m2", "m2": "m2", "dispatcher": "m2",
        "personal-m2-assistant": "m3", "m3": "m3",
        "code-review": "reviewer", "reviewer": "reviewer",
        "writer": "writer",
    }
    if explicit in explicit_map:
        return explicit_map[explicit]
    text = " ".join(_text(_data(e).get(k, "")) for e in events for k in
                    ("role", "agentName", "agentDisplayName", "agentDescription", "selectedModel", "model"))
    low = text.lower()
    if "code-review" in low or "reviewer" in low:
        return "reviewer"
    skills = {_event_skill(event) for event in events}
    skills.discard(None)
    if "personal-m2-assistant" in skills:
        return "m3"
    if "m2-dispatcher" in skills and "team-lead" not in skills:
        return "m2"
    cwd = next((str(_data(event).get("context", {}).get("cwd", ""))
                for event in events if isinstance(_data(event).get("context"), dict)), "")
    if "/worktrees/" in cwd and "team-lead" in skills:
        return "m1"
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
    if "_retro_skill" in event:
        return event["_retro_skill"]
    value = _field(event, "skill", "skillName", "name")
    if _event_type(event) in {"skill.invoked", "skill.invoked_ref"} and value:
        candidate = str(value)
        event["_retro_skill"] = candidate if is_valid_skill_slug(candidate, require_repo_dir=True) else None
        return event["_retro_skill"]
    path = str(_arguments(event).get("path") or _field(event, "path") or "")
    match = re.search(r"skills/([^/]+)/SKILL\.md", path)
    candidate = match.group(1) if match else ""
    event["_retro_skill"] = candidate if is_valid_skill_slug(candidate, require_repo_dir=True) else None
    return event["_retro_skill"]


def _skill_names(events: list[dict[str, Any]]) -> set[str]:
    names = {name for e in events if (name := _event_skill(e)) and is_valid_skill_slug(name)}
    for e in events:
        text = _text(_field(e, "content", "message", "assistantContent", "userContent"))
        names.update(name for name in re.findall(r"skills/([^/]+)/", text) if is_valid_skill_slug(name, require_repo_dir=True))
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
    if "_retro_goal" in event:
        return event["_retro_goal"]
    value = _field(event, "goalId", "goal_id", "taskId", "task_id", "goal", "task")
    if value:
        event["_retro_goal"] = str(value)
        return event["_retro_goal"]
    text = _text(_field(event, "content", "message", "assistantContent", "userContent"))
    match = re.search(r"(?:goal|task)[_ -]?(?:id)?\s*[:=]\s*([A-Za-z0-9._-]+)", text, re.I)
    event["_retro_goal"] = match.group(1) if match else None
    return event["_retro_goal"]


def _vendor(model: str) -> str:
    low = model.lower()
    for family, words in {"openai": ("openai", "gpt", "o1", "o3"), "google": ("google", "gemini", "argon"),
                          "anthropic": ("anthropic", "claude"), "microsoft": ("microsoft", "copilot", "mai-")}.items():
        if any(word in low for word in words):
            return family
    return "unknown"


def _routing_group(model: str) -> str:
    low = model.lower().strip()
    match = re.search(r"(?:^|/)(group/[^/@\s]+)", low)
    return match.group(1) if match else ""


def _same_model_route(first: str, second: str) -> bool:
    if not first or not second:
        return False
    first_group, second_group = _routing_group(first), _routing_group(second)
    if first_group and second_group and first_group == second_group:
        return True
    first_vendor, second_vendor = _vendor(first), _vendor(second)
    return first_vendor != "unknown" and first_vendor == second_vendor


def _outcome_gate(groups: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], set[str]]:
    gate: dict[str, Any] = {}
    eligible: set[str] = set()
    for sid, seq in groups.items():
        blob = " ".join(_event_blob(e) for e in seq)
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


def _scoped_events(record: dict[str, Any], events: list[dict[str, Any]],
                   indexed: list[tuple[dict[str, Any], str, str | None]] | None = None,
                   scope_lookup: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    tokens = {record["name"].lower()}
    for source in (record.get("ledger", ""), record.get("spec", "")):
        tokens.update(x.lower() for x in re.findall(r"(?:goal|task)[_ -]?(?:id)?\s*[:=]\s*([A-Za-z0-9._-]+)", source, re.I))
    tokens.discard("")
    if scope_lookup is not None:
        selected: list[dict[str, Any]] = []
        seen: set[int] = set()
        for token in tokens:
            for event in scope_lookup.get(token, []) + scope_lookup.get(f"goal:{token}", []):
                marker = id(event)
                if marker not in seen:
                    selected.append(event)
                    seen.add(marker)
        return selected
    scoped = []
    candidates = indexed or [(event, _text(event).lower(), _goal_key(event)) for event in events]
    for event, blob, goal in candidates:
        if (goal and goal.lower() in tokens) or any(token in blob for token in tokens if len(token) >= 4):
            scoped.append(event)
    return scoped
