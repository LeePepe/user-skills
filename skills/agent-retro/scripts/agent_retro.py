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
import shutil
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = ROOT / "skills" / "agent-retro"
SF_DATALESS = 0x40000000
MAX_EVENTS = 150_000
MAX_EVENTS_PER_SESSION = 4_000
MAX_BYTES = 2_000_000
MAX_OUTPUT = 16_000
DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
DEFAULT_AUTOPILOT_TITLE = "Daily Agent Trajectory Retro Autopilot (Auto-Fix + M3 Issue)"
DEFAULT_TRIGGER_LABEL = "Daily CST 04:40 Agent Trajectory Retro Diagnostic"
DEFAULT_CRON = "40 4 * * *"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_COLLECT_OUTPUT = Path("/tmp/multica-agent-retro-collect.json")


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
        event["_retro_skill"] = str(value)
        return event["_retro_skill"]
    path = str(_arguments(event).get("path") or _field(event, "path") or "")
    match = re.search(r"skills/([^/]+)/SKILL\.md", path)
    event["_retro_skill"] = match.group(1) if match else None
    return event["_retro_skill"]


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


def _metric_scan(events: list[dict[str, Any]], handoffs: list[dict[str, Any]], source: dict[str, Any],
                 profile_sessions: set[str] | None = None) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    boundary_metrics = {"R1_m3_prompt_prelock", "R2_m2_direct_code_edit", "R3_m1_inline_overwork",
                        "R5_reviewer_same_vendor", "H4_compaction_and_handoff_loss", "E4_unfaithful_receipt_and_cancelled_noise",
                        "F1_permission_stall_count", "F2_owner_abort_rate", "F3_mid_flight_owner_correction"}
    def allowed(metric: str, sid: str) -> bool:
        return profile_sessions is None or sid in profile_sessions or metric in boundary_metrics
    groups = _session_groups(events)
    indexed_events = [(event, _event_blob(event), _goal_key(event)) for event in events]
    event_blobs = {id(event): blob for event, blob, _ in indexed_events}
    handoff_names = {record["name"].lower() for record in handoffs}
    scope_lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event, blob, goal in indexed_events:
        if goal:
            scope_lookup[f"goal:{goal.lower()}"].append(event)
        for name in handoff_names:
            if name in blob:
                scope_lookup[name].append(event)
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
        text = " ".join(_value_text(_field(e, "content", "message", "assistantContent", "userContent")) for e in seq).lower()
        if any(token in text for token in ("prompt lock-in", "hot-append", "pre-baked", "prebaked")) and role in ("m3", "unknown"):
            observations.append(_observation("R1_m3_prompt_prelock", "role", 1, [f"{sid}: prompt/pre-lock wording"], sessions=1))
        if role == "m2":
            direct = [e for e in tools if _tool_name(e) in {"edit", "create", "apply_patch", "write"} and _is_code_path(str(_arguments(e).get("path") or _field(e, "path") or ""))]
            if direct:
                observations.append(_observation("R2_m2_direct_code_edit", "role", len(direct), [f"{sid}: {_tool_name(e)} {_arguments(e).get('path', '')}" for e in direct], sessions=1))
        subagent_events = [e for e in seq if _event_type(e) in {"subagent.started", "subagent.completed"}]
        writer_spawned = any(
            "writer" in _text(_data(e)).lower()
            or "group/coding" in str(_field(e, "model", "selectedModel") or "").lower()
            or ("herdr-dispatch.sh" in _command(e) and "--role writer" in _command(e))
            for e in subagent_events + seq
        )
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
        parent_models = [str(_field(e, "selectedModel", "model", "usageModel") or "") for e in seq
                         if _event_type(e) == "session.start" or _field(e, "selectedModel", "model", "usageModel")]
        parent_models.extend(peer_models)
        for goal_id in {_goal_key(event) for event in seq if _goal_key(event)}:
            peer_vendors.update(vendor for vendor in goal_models.get(goal_id, set()) if vendor != "unknown")
        if role == "reviewer" and reviewer_vendor != "unknown" and reviewer_vendor in peer_vendors:
            observations.append(_observation("R5_reviewer_same_vendor", "role", 1, [f"{sid}: reviewer and Writer/TL share {reviewer_vendor}"], skill="code-review", sessions=1))
        reviewer_subagents = [
            e for e in subagent_events
            if str(_field(e, "agentName") or "").lower() in {"code-review", "reviewer"}
            or "reviewer" in _text(_field(e, "agentDisplayName", "agentDescription")).lower()
            or "code-review" in _text(_field(e, "agentDisplayName", "agentDescription")).lower()
        ]
        for reviewer in reviewer_subagents:
            reviewer_model = str(_field(reviewer, "model", "selectedModel", "usageModel") or "")
            same_route = any(_same_model_route(reviewer_model, peer) for peer in parent_models if peer)
            if same_route:
                observations.append(_observation(
                    "R5_reviewer_same_vendor", "role", 1,
                    [f"{sid}: reviewer subagent shares model route/vendor"],
                    skill="code-review", sessions=1,
                ))
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
        bloat = [e for e in tools if len(_result_content(e).encode("utf-8")) > 20_000
                 or "Output too large to read at once" in _result_content(e)]
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
        record_name = record["name"].lower()
        related = [event for event, blob, goal in indexed_events if record_name in blob or goal == record["name"]]
        related_sample = related[:200]
        h4_evidence: list[str] = []
        if any("compaction" in _event_type(e) or "context.compacted" in _event_type(e)
               or "context compaction" in event_blobs.get(id(e), "") for e in related_sample):
            h4_evidence.append(f"{record['name']}: context compaction")
        rereads: Counter[str] = Counter()
        for event in related_sample:
            if _tool_name(event) in {"view", "read", "cat"}:
                args = _arguments(event)
                path = str(args.get("path") or _field(event, "path") or "")
                if path and not any(args.get(key) for key in ("view_range", "viewRange", "range", "startLine", "lineRange")):
                    rereads[path] += 1
        h4_evidence.extend(f"{record['name']}: full-file reread {path}" for path, count in rereads.items() if count >= 3)
        related_text = " ".join(event_blobs.get(id(e), "") for e in related_sample)
        promises = bool(re.search(r"delegate|spawn a writer|run (?:the )?verification|verify", related_text))
        mutated = any(_tool_name(e) in {"edit", "create", "apply_patch", "write", "patch"} for e in related)
        verified = any(_exit_code(e) == 0 and re.search(r"verify|pytest|unittest|swiftlint", _command(e), re.I) for e in related)
        if promises and (mutated or not verified):
            h4_evidence.append(f"{record['name']}: reasoning-action mismatch")
        if spec_words and len(spec_words - set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", brief_text))) > 8:
            h4_evidence.append(f"{record['name']}: spec constraints absent from brief")
        if h4_evidence:
            observations.append(_observation("H4_compaction_and_handoff_loss", "handoff", len(h4_evidence), h4_evidence, skill=_first_fault_skill(related_text or record["spec"], _role_for(related_sample)), sessions=1))
        for name, receipt in record["receipts"]:
            scoped_events = _scoped_events(record, events, indexed_events, scope_lookup)
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


def _aggregate_observations(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("metric", "")), str(row.get("upstream_skill", row.get("skill", "unknown"))),
               str(row.get("status", "actionable")))
        current = grouped.get(key)
        if current is None:
            current = dict(row)
            current["evidence"] = []
            grouped[key] = current
        current["count"] = int(current.get("count", 0)) + int(row.get("count", 0) or 0)
        current["scenario_sessions"] = int(current.get("scenario_sessions", 0)) + int(row.get("scenario_sessions", 0) or 0)
        for evidence in row.get("evidence", []):
            if evidence not in current["evidence"] and len(current["evidence"]) < 3:
                current["evidence"].append(evidence)
    return sorted(grouped.values(), key=lambda row: (-int(row.get("count", 0)), str(row.get("metric", ""))))[:limit]


def _outcome_summary(gate: dict[str, Any]) -> dict[str, Any]:
    healthy = sum(value.get("status") == "OUTCOME_HEALTHY" for value in gate.values())
    at_risk = sum(value.get("status") == "OUTCOME_AT_RISK" for value in gate.values())
    unknown = sum(value.get("status") == "OUTCOME_UNKNOWN" for value in gate.values())
    entries = dict(list(sorted(gate.items()))[:15])
    return {"healthy_count": healthy, "at_risk_count": at_risk, "unknown_count": unknown, "entries": entries}


def _skill_attribution(observations: list[dict[str, Any]], watermark: dict[str, Any]) -> list[dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    post = watermark.get("post_fix", {}).get("by_skill", {})
    pre = watermark.get("pre_fix", {}).get("by_skill", {})
    physical_locks = {
        "agent-retro": "bounded trace reader and compact output gate",
        "team-lead": "feedback and lease gate",
        "m2-dispatcher": "owned-path and lease gate",
        "code-review": "cross-vendor reviewer routing gate",
        "personal-m2-assistant": "prompt boundary gate",
        "repo-kit": "CI contract gate",
        "my-designer": "spec/design contract gate",
    }
    for row in observations:
        skill = str(row.get("upstream_skill") or row.get("skill") or "unknown")
        entry = by_skill.setdefault(skill, {"skill": skill, "total_anomalies": 0, "metrics": [],
                                             "physical_lock": physical_locks.get(skill, "upstream skill lock"),
                                             "pre_fix_anomalies": int(pre.get(skill, {}).get("anomalies", 0)),
                                             "post_fix_anomalies": int(post.get(skill, {}).get("anomalies", 0))})
        entry["total_anomalies"] += int(row.get("count", 0) or 0)
        if row.get("metric") not in entry["metrics"]:
            entry["metrics"].append(row["metric"])
    return sorted(by_skill.values(), key=lambda row: (-row["total_anomalies"], row["skill"]))[:15]


def _scan(args: argparse.Namespace) -> dict[str, Any]:
    sessions, handoff_root, warehouse = _roots(args)
    events, event_source = _read_events(
        sessions, int(getattr(args, "days", 30) or 30),
        fast=bool(getattr(args, "fast", False) or getattr(args, "_probe_budget", False)),
    )
    handoffs, handoff_source = _handoffs(handoff_root)
    outcome_gate, profile_sessions = _outcome_gate(_session_groups(events))
    for record in handoffs:
        if re.search(r"\b(?:done|completed|pr_merged)\b", record["ledger"], re.I):
            outcome_gate[f"handoff:{record['name']}"] = {"status": "OUTCOME_HEALTHY" if record["receipts"] else "OUTCOME_AT_RISK", "faithful_receipts": bool(record["receipts"]), "goal": record["name"]}
    analysis_events = events
    all_session_groups = _session_groups(events)
    if len(all_session_groups) > 45:
        recent_session_ids = set(list(all_session_groups)[-45:])
        analysis_events = [event for event in events if _session_id(event, str(event.get("_retro_session", ""))) in recent_session_ids]
    observations = _metric_scan(analysis_events, handoffs, event_source, profile_sessions)
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
    watermark = _version_watermark(events, observations)
    compact_actionable = _aggregate_observations(actionable)
    compact_healthy = _aggregate_observations(
        [x for x in observations if x["status"] == "healthy_progressive_disclosure"], limit=20
    )
    compact_pool = [x for x in compact_actionable if x["count"] < 3][:20]
    outcome = _outcome_summary(outcome_gate) if getattr(args, "summary", False) or len(outcome_gate) > 15 else outcome_gate
    return {"schema": "agent-retro-probe/v1", "ok": True, "entry_mode": "fast" if getattr(args, "fast", False) else "bounded", "role_filter": role_filter or None,
            "source": {"events": event_source, "handoff": handoff_source, "warehouse": "available" if warehouse else "missing_safe_degradation"},
            "coverage": {"events": len(events), "sessions": len(_session_groups(events)), "observations": len(observations), "incomplete": bool(event_source["truncated"])},
            "outcome_gate": outcome, "active_leases": sorted(active_leases), "excluded_by_active_lease": [],
            "version_watermark": watermark, "skill_attribution": _skill_attribution(actionable, watermark),
            "metrics": metrics, "observations": compact_actionable,
            "healthy_exemptions": compact_healthy, "observation_pool": compact_pool,
            "k_cap": min(3, len({x['upstream_skill'] for x in actionable if x['upstream_skill'] not in active_leases}))}


def _compact_print(payload: Any, as_json: bool, summary: bool = False) -> None:
    payload = _redact_obj(payload)
    if as_json:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        if isinstance(payload, dict):
            text = "\n".join([f"agent-retro: {payload.get('schema', 'ok')}", f"observations={len(payload.get('observations', []))} k={payload.get('k_cap', 0)}", "unknown sources remain unknown; no issue was published."])
        else:
            text = _text(payload)
    if len(text) > MAX_OUTPUT and isinstance(payload, dict):
        reduced = {key: payload[key] for key in (
            "schema", "ok", "entry_mode", "role_filter", "source", "coverage", "outcome_gate",
            "active_leases", "excluded_by_active_lease", "version_watermark", "skill_attribution",
            "metrics", "observations", "healthy_exemptions", "observation_pool", "k_cap",
        ) if key in payload}
        for key in ("issue_verified", "dry_run", "published", "written", "config", "path", "action"):
            if key in payload:
                reduced[key] = payload[key]
        for size in (10, 5, 2, 0):
            reduced["observations"] = reduced.get("observations", [])[:size]
            reduced["healthy_exemptions"] = reduced.get("healthy_exemptions", [])[:size]
            reduced["observation_pool"] = reduced.get("observation_pool", [])[:size]
            text = json.dumps(reduced, ensure_ascii=False, separators=(",", ":")) if as_json else text[:MAX_OUTPUT - 40] + "\n... output compacted"
            if len(text) <= MAX_OUTPUT:
                break
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


def _owned_path_skills(source: str) -> set[str]:
    match = re.search(r"(?ims)^#{0,6}\s*owned paths\b(?P<body>.*?)(?=^#{1,6}\s|\Z)", source)
    if not match:
        return set()
    skills: set[str] = set()
    for line in match.group("body").splitlines():
        if not re.match(r"^\s*(?:[-*]|\d+\.)\s+", line):
            continue
        skills.update(re.findall(r"\bskills/([A-Za-z0-9_-]+)(?:/|\b)", line))
    return {skill for skill in skills if (ROOT / "skills" / skill).is_dir()}


def _lease_name_skill(name: str) -> str | None:
    match = re.fullmatch(r"(?:agent|skill)-retro-([A-Za-z0-9_-]+)", name)
    if not match:
        return None
    candidate = match.group(1)
    return candidate if (ROOT / "skills" / candidate).is_dir() else None


def _active_leases(handoffs: list[dict[str, Any]]) -> set[str]:
    leases: set[str] = set()
    for record in handoffs:
        if not record.get("active_lease"):
            continue
        if skill := _lease_name_skill(record["name"]):
            leases.add(skill)
        for source in (record.get("spec", ""), record.get("ledger", "")):
            leases.update(_owned_path_skills(source))
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
    if int(getattr(args, "days", 30) or 30) <= 3 and not getattr(args, "fast", False):
        args._probe_budget = True
    return _scan(args)


def _agent_retro_coverage(scan: Mapping[str, Any]) -> str:
    source = scan.get("source") if isinstance(scan.get("source"), Mapping) else scan
    event_source = source.get("events") if isinstance(source.get("events"), Mapping) else {}
    handoff_source = source.get("handoff") if isinstance(source.get("handoff"), Mapping) else {}
    if not event_source.get("available") and not handoff_source.get("available"):
        return "missing"
    coverage = scan.get("coverage") if isinstance(scan.get("coverage"), Mapping) else {}
    sessions = int(coverage.get("sessions", 0) or 0)
    return "healthy" if sessions > 0 and not coverage.get("incomplete") else "degraded"


def collect_agent_retro_snapshot(
    *,
    days: int = 3,
    handoff_root: Path | None = None,
    output_path: Path | None = None,
    watermark_path: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Collect a deterministic three-day snapshot without network or Multica side effects."""
    args = argparse.Namespace(
        days=max(1, int(days)), role=None, layer=None, fast=True,
        handoff_root=str(handoff_root) if handoff_root else None, handoff_dir=None,
    )
    scan = _scan(args)
    date_str = datetime.now(timezone.utc).date().isoformat()
    coverage = _agent_retro_coverage(scan)
    sessions = int((scan.get("coverage") or {}).get("sessions", 0) or 0)
    cohort = hashlib.sha256(json.dumps(
        {"observations": scan.get("observations", []), "sessions": sessions, "coverage": coverage},
        ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()[:16]
    snapshot = {
        "schema": "agent-retro-collect/v1",
        "ok": True,
        "date": date_str,
        "window_days": max(1, int(days)),
        "dedup_key": f"agent-retro-autopilot-{date_str}",
        "cursor": f"agent-retro-autopilot-{date_str}",
        "summary": {
            "sample_size": sessions,
            "sessions": sessions,
            "coverage_status": coverage,
            "coverage": coverage,
            "observation_count": len(scan.get("observations", [])),
        },
        "coverage": {"sessions": sessions, "status": coverage},
        "scan": scan,
        "watermark": cohort,
    }
    if persist:
        target = output_path or DEFAULT_COLLECT_OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        watermark = watermark_path or _default_agent_retro_watermark_path()
        watermark.parent.mkdir(parents=True, exist_ok=True)
        watermark.write_text(json.dumps({
            "schema": "agent-retro-watermark/v1", "cohort_watermark": cohort,
            "date": date_str, "sessions": sessions, "coverage": coverage,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot["output"] = str(target)
        snapshot["watermark_path"] = str(watermark)
    return snapshot


def command_collect(args: argparse.Namespace) -> dict[str, Any]:
    return collect_agent_retro_snapshot(
        days=int(getattr(args, "days", 3) or 3),
        handoff_root=Path(args.handoff_root).expanduser() if getattr(args, "handoff_root", None) else None,
        output_path=Path(args.output).expanduser() if getattr(args, "output", None) else DEFAULT_COLLECT_OUTPUT,
        watermark_path=Path(args.watermark_file).expanduser() if getattr(args, "watermark_file", None) else None,
        persist=True,
    )


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


def _default_multica_runner(
    command: list[str], *, stdin_text: str | None = None,
    multica_bin: str | None = None, server_url: str | None = None,
    workspace_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = multica_bin or os.environ.get("MULTICA_BIN") or shutil.which("multica") or "/opt/homebrew/bin/multica"
    cmd = [binary]
    if server_url:
        cmd += ["--server-url", server_url]
    if workspace_id:
        cmd += ["--workspace-id", workspace_id]
    cmd += command
    env = dict(os.environ)
    if server_url:
        env["MULTICA_SERVER_URL"] = server_url
    if workspace_id:
        env["MULTICA_WORKSPACE_ID"] = workspace_id
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, env=env, check=False)


def _parse_multica_json(proc: subprocess.CompletedProcess[str], context: str) -> Any:
    if proc.returncode != 0:
        raise RuntimeError(f"multica {context} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:300]}")
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"multica {context} returned invalid JSON: {raw[:300]}") from exc


def _telemetry_issue_markdown(markdown: str, *, day: str, sessions: int, coverage: str) -> str:
    marker = f"agent-retro-autopilot-{day}"
    result = markdown.replace(f"<!-- multica-cursor:agent-retro-{day} -->", f"<!-- multica-cursor:{marker} -->")
    telemetry = f"- **Telemetry**: `sessions={sessions}` · `coverage={coverage}`"
    if "</details>" in result and telemetry not in result:
        result = result.replace("</details>", telemetry + "\n</details>", 1)
    return result


def _apply_agent_retro_issue(
    *, title: str, description: str, assignee_id: str, priority: str, status: str,
    server_url: str, workspace_id: str, runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    listed_proc = runner(["issue", "list", "--assignee-id", assignee_id, "--limit", "50", "--output", "json"],
                         server_url=server_url, workspace_id=workspace_id)
    listed = _parse_multica_json(listed_proc, "issue list")
    issues = listed.get("issues", []) if isinstance(listed, Mapping) else (listed if isinstance(listed, list) else [])
    marker = re.search(r"agent-retro-autopilot-\d{4}-\d{2}-\d{2}", description)
    cursor = marker.group(0) if marker else ""
    existing = next((item for item in issues if isinstance(item, Mapping) and str(item.get("status") or "").lower() not in {"done", "cancelled", "canceled"}
                     and (str(item.get("title") or "") == title or (cursor and cursor in str(item.get("description") or "")))), None)
    if existing is not None and (existing.get("id") or existing.get("identifier")):
        target = str(existing.get("id") or existing.get("identifier"))
        proc = runner(["issue", "update", target, "--title", title, "--description-stdin", "--priority", priority,
                       "--status", status, "--assignee-id", assignee_id, "--no-start", "--output", "json"],
                      stdin_text=description, server_url=server_url, workspace_id=workspace_id)
        return {"action": "updated_existing", "issue": _parse_multica_json(proc, "issue update"), "dedup_key": cursor}
    proc = runner(["issue", "create", "--title", title, "--priority", priority, "--status", status,
                   "--assignee-id", assignee_id, "--description-stdin", "--output", "json"],
                  stdin_text=description, server_url=server_url, workspace_id=workspace_id)
    return {"action": "created", "issue": _parse_multica_json(proc, "issue create"), "dedup_key": cursor}


def _sync_linear_agent_retro(*, title: str, description: str, day: str) -> dict[str, Any]:
    """Use the existing Linear client only when explicitly requested by --sync-linear."""
    path = ROOT / "skills" / "linear-board" / "scripts" / "daily-brief.py"
    spec = importlib.util.spec_from_file_location("agent_retro_linear_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Linear client bridge unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module._load_linear_client(dry_run=False)
    if client is None:
        raise RuntimeError("Linear credentials unavailable for --sync-linear")
    team = client.team_metadata("TIA")
    marker = f"<!-- multica-cursor:agent-retro-autopilot-{day} -->"
    issues = client.list_issues("TIA", limit=None)
    existing = next((item for item in issues if marker in str(item.get("description") or "") or item.get("title") == title), None)
    kwargs = {"title": title, "description": description, "assignee_id": DEFAULT_OWNER_ID}
    if existing:
        return client.update_issue(existing["id"], **kwargs)
    states = team.get("states", {}).get("nodes", [])
    todo = next((state for state in states if str(state.get("name", "")).casefold() == "todo"), None)
    kwargs.update({"team_id": team["id"], "state_id": todo["id"] if todo else None, "parent_id": None})
    return client.create_issue(**kwargs)


def command_file_issue(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    roll = command_rollup(argparse.Namespace(handoff_root=str(root), date=args.date, issue_brief=True))
    day = str(roll["date"])
    payload = roll.get("payload", {}) if isinstance(roll.get("payload"), Mapping) else {}
    scan = payload.get("coverage", {}) if isinstance(payload.get("coverage"), Mapping) else {}
    sessions = int(scan.get("sessions", 0) or 0)
    coverage = _agent_retro_coverage(payload)
    markdown = _telemetry_issue_markdown(str(roll.get("issue_markdown") or ""), day=day, sessions=sessions, coverage=coverage)
    verification = _load_issue_renderer().verify_dual_zone_issue(markdown) if _load_issue_renderer() else {"ok": False, "violations": ["renderer unavailable"]}
    if not verification.get("ok"):
        raise ValueError(f"Dual-Zone issue verification failed before Multica publish: {verification.get('violations')}")
    dry_run = bool(getattr(args, "dry_run", False) or not getattr(args, "apply", False))
    server_url = getattr(args, "server", "") or DEFAULT_SERVER_URL
    workspace = getattr(args, "workspace", "") or DEFAULT_WORKSPACE_ID
    owner = getattr(args, "owner", "") or DEFAULT_OWNER_ID
    title = f"[Agent Retro {day}]"
    result: dict[str, Any] = {"schema": "agent-retro-file-issue/v1", "ok": True, "dry_run": dry_run,
                              "published": False, "server": server_url, "workspace": workspace,
                              "owner": owner, "agent": getattr(args, "agent", "agent-retro"),
                              "dedup_key": f"agent-retro-autopilot-{day}", "issue_markdown": markdown,
                              "dual_zone_verification": verification, "action": "dry_run"}
    if not dry_run:
        applied = _apply_agent_retro_issue(title=title, description=markdown, assignee_id=owner,
                                           priority="medium", status="backlog", server_url=server_url,
                                           workspace_id=workspace, runner=getattr(args, "runner", _default_multica_runner))
        result.update(applied, published=True, action=applied["action"])
        if getattr(args, "sync_linear", False):
            result["linear"] = _sync_linear_agent_retro(title=title, description=markdown, day=day)
    return result


def _agent_retro_prompt() -> str:
    return ("# Daily Agent Trajectory Retro Autopilot (Auto-Fix + M3 Issue)\n\n"
            "1 M2 -> 1 Cheap Probe Worker -> K (0..3) Independent Per-Skill M1 TLs -> "
            "Rollup to Single M2 -> M3 [Agent Retro YYYY-MM-DD] Multica & Linear Issue\n\n"
            "Run `python3 skills/agent-retro/scripts/agent_retro.py collect --days 3 --json`, "
            "then file the verified Dual-Zone issue with the `agent-retro-autopilot-YYYY-MM-DD` cursor.")


def _apply_agent_retro_autopilot(*, runner: Callable[..., subprocess.CompletedProcess[str]], server_url: str,
                                  workspace: str, receipt: Path | None = None) -> dict[str, Any]:
    prompt = _agent_retro_prompt()
    listed = _parse_multica_json(runner(["autopilot", "list", "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot list")
    autopilots = listed.get("autopilots", []) if isinstance(listed, Mapping) else (listed if isinstance(listed, list) else [])
    existing = next((ap for ap in autopilots if isinstance(ap, Mapping) and
                     ("Daily Agent Trajectory Retro" in str(ap.get("title") or "") or "agent-retro-diagnostic" in str(ap.get("description") or ""))), None)
    if existing:
        ap_id = str(existing.get("id"))
        ap_action = "updated"
        ap_obj = _parse_multica_json(runner(["autopilot", "update", ap_id, "--title", DEFAULT_AUTOPILOT_TITLE,
                                             "--agent", DEFAULT_AGENT_ID, "--mode", "run_only", "--status", "active",
                                             "--subscriber", DEFAULT_OWNER_ID, "--description", prompt, "--output", "json"],
                                            server_url=server_url, workspace_id=workspace), "autopilot update")
    else:
        ap_action = "created"
        ap_obj = _parse_multica_json(runner(["autopilot", "create", "--title", DEFAULT_AUTOPILOT_TITLE,
                                             "--agent", DEFAULT_AGENT_ID, "--mode", "run_only", "--subscriber", DEFAULT_OWNER_ID,
                                             "--description", prompt, "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot create")
        ap_id = str(ap_obj.get("id") or (ap_obj.get("autopilot") or {}).get("id") or "")
        if not ap_id:
            raise RuntimeError(f"Could not extract agent-retro autopilot id: {ap_obj}")
    trig_data = _parse_multica_json(runner(["autopilot", "trigger-list", ap_id, "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot trigger-list")
    triggers = trig_data.get("triggers", []) if isinstance(trig_data, Mapping) else (trig_data if isinstance(trig_data, list) else [])
    sched = next((t for t in triggers if isinstance(t, Mapping) and str(t.get("kind") or "schedule") == "schedule"), None)
    if sched:
        trig_id = str(sched.get("id"))
        trigger_action = "unchanged" if (str(sched.get("cron") or sched.get("cron_expression") or "") == DEFAULT_CRON and
                                          str(sched.get("timezone") or "") == DEFAULT_TIMEZONE and
                                          str(sched.get("label") or DEFAULT_TRIGGER_LABEL) == DEFAULT_TRIGGER_LABEL) else "updated"
        if trigger_action == "updated":
            trigger = _parse_multica_json(runner(["autopilot", "trigger-update", ap_id, trig_id, "--cron", DEFAULT_CRON,
                                                   "--timezone", DEFAULT_TIMEZONE, "--label", DEFAULT_TRIGGER_LABEL, "--output", "json"],
                                                  server_url=server_url, workspace_id=workspace), "autopilot trigger-update")
        else:
            trigger = sched
    else:
        trigger_action = "created"
        trigger = _parse_multica_json(runner(["autopilot", "trigger-add", ap_id, "--kind", "schedule", "--cron", DEFAULT_CRON,
                                               "--timezone", DEFAULT_TIMEZONE, "--label", DEFAULT_TRIGGER_LABEL, "--output", "json"],
                                              server_url=server_url, workspace_id=workspace), "autopilot trigger-add")
    result = {"ok": True, "autopilot_action": ap_action, "autopilot_id": ap_id, "autopilot": ap_obj,
              "trigger_action": trigger_action, "trigger": trigger, "prompt": prompt}
    if receipt:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["receipt"] = str(receipt)
    return result


def command_autopilot(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    config = {"name": DEFAULT_AUTOPILOT_TITLE, "schedule": {"cron": DEFAULT_CRON, "timezone": DEFAULT_TIMEZONE, "mode": "run_only"},
              "command": "python3 skills/agent-retro/scripts/agent_retro.py collect --days 3 --json", "skill": "agent-retro",
              "prompt": _agent_retro_prompt()}
    path = root / "agent-retro-autopilot.json"
    dry_run = bool(getattr(args, "dry_run", False) or not getattr(args, "apply", False))
    result = {"schema": "agent-retro-autopilot/v1", "ok": True, "dry_run": dry_run, "config": config,
              "path": str(path), "written": False, "skill_retro_schedule_untouched": True}
    if not dry_run:
        result.update(_apply_agent_retro_autopilot(runner=getattr(args, "runner", _default_multica_runner),
                                                   server_url=getattr(args, "server", "") or DEFAULT_SERVER_URL,
                                                   workspace=getattr(args, "workspace", "") or DEFAULT_WORKSPACE_ID,
                                                   receipt=Path(args.receipt).expanduser() if getattr(args, "receipt", None) else None))
        result["written"] = _safe_write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return result


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
    p = sub.add_parser("collect", help="collect a bounded telemetry snapshot"); _common(p); p.set_defaults(days=3); p.add_argument("--output", type=Path, default=DEFAULT_COLLECT_OUTPUT); p.add_argument("--watermark-file", type=Path, default=None)
    p = sub.add_parser("probe", help="fast bounded five-layer probe"); _common(p)
    p = sub.add_parser("triage", help="cheap worker triage"); _common(p); p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3); p.add_argument("--pick-one", action="store_true"); p.add_argument("--use-jev", action="store_true"); p.add_argument("--mock-jev-json"); p.add_argument("--emit-briefs")
    p = sub.add_parser("diagnose", help="deep attribution for one skill"); _common(p); p.add_argument("--skill", required=True)
    p = sub.add_parser("emit-briefs", help="write non-clobbering specs and briefs"); _common(p); p.add_argument("--skill"); p.add_argument("--pick-top", type=int, choices=(1, 2, 3), default=3); p.add_argument("--pick-one", action="store_true"); p.add_argument("--emit-briefs")
    p = sub.add_parser("rollup", help="aggregate receipts into a Dual-Zone issue"); _common(p); p.add_argument("--date"); p.add_argument("--issue-brief", action="store_true")
    p = sub.add_parser("file-issue", help="prepare or explicitly apply an issue publication"); _common(p); p.add_argument("--date"); p.add_argument("--server", "--server-url", dest="server", default=""); p.add_argument("--workspace", "--workspace-id", dest="workspace", default=""); p.add_argument("--owner", "--assignee-id", dest="owner", default=""); p.add_argument("--agent", default="agent-retro"); p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False); p.add_argument("--apply", action="store_true"); p.add_argument("--sync-linear", action="store_true")
    p = sub.add_parser("setup-autopilot", help="prepare the independent run_only schedule"); _common(p); p.add_argument("--server", "--server-url", dest="server", default=""); p.add_argument("--workspace", "--workspace-id", dest="workspace", default=""); p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False); p.add_argument("--apply", action="store_true"); p.add_argument("--receipt", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect": result = command_collect(args)
        elif args.command == "probe": result = command_probe(args)
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
