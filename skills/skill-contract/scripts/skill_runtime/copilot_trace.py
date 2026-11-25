"""Read and pair canonical Copilot event traces without loading noise."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

_NOISE = {"hook.start", "hook.end", "session.usage_checkpoint", "skill.context_delivered_ref"}
_EXIT = re.compile(r"<shellId: [^\n>]*completed with exit code (\d+)", re.I)


def _event(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = str(raw.get("type") or raw.get("event") or raw.get("name") or "")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    merged = dict(data)
    merged.update({key: value for key, value in raw.items() if key not in {"data", "type", "event", "name"}})
    return kind, merged


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("content") or value.get("text") or "")
    return ""


def _pair_events(path: Path, lines: list[dict[str, Any]]) -> dict[str, Any]:
    session: dict[str, Any] = {"path": str(path), "events": [], "tool_calls": [], "assistant_turns": [], "skills": [], "subagents": [], "reasoning_chars": 0, "output_tokens": 0}
    starts: dict[str, dict[str, Any]] = {}
    for raw in lines:
        kind, data = _event(raw)
        if not kind or kind in _NOISE:
            continue
        session["events"].append(raw)
        if kind == "session.start":
            session.update({key: data[key] for key in ("cwd", "selectedModel", "sessionId") if key in data})
        if kind in {"assistant.message", "assistant.turn", "assistant.response"} or "reasoningText" in data:
            content = _content(data.get("content") or data.get("text"))
            reasoning = str(data.get("reasoningText") or "")
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else data
            tokens = usage.get("output_tokens", usage.get("outputTokens", 0)) if isinstance(usage, dict) else 0
            try:
                tokens = int(tokens or 0)
            except (TypeError, ValueError):
                tokens = 0
            session["reasoning_chars"] += len(reasoning)
            session["output_tokens"] += tokens
            session["assistant_turns"].append({"content": content, "reasoning_chars": len(reasoning), "output_tokens": tokens})
        if kind in {"session.usage_record", "usage"}:
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else data
            if isinstance(usage, dict):
                try:
                    session["output_tokens"] += int(usage.get("output_tokens", usage.get("outputTokens", 0)) or 0)
                except (TypeError, ValueError):
                    pass
        if kind == "tool.execution_start":
            call_id = str(data.get("toolCallId") or data.get("tool_call_id") or "")
            if call_id:
                starts[call_id] = {"toolCallId": call_id, "toolName": data.get("toolName") or data.get("name"), "arguments": data.get("arguments")}
        if kind == "tool.execution_complete":
            call_id = str(data.get("toolCallId") or data.get("tool_call_id") or "")
            call = dict(starts.pop(call_id, {}))
            call.update({"toolCallId": call_id, "content": _content(data.get("content") or data.get("result"))})
            if "toolName" not in call:
                call["toolName"] = data.get("toolName") or data.get("name")
            if "arguments" not in call:
                call["arguments"] = data.get("arguments")
            if "exitCode" not in data:
                match = _EXIT.search(call["content"])
                if match:
                    call["exitCode"] = int(match.group(1))
            if "exitCode" in data:
                call["exitCode"] = data["exitCode"]
            if "Output too large to read at once" in call["content"] or len(call["content"]) > 20_000:
                call["aci_output_bloat"] = True
            session["tool_calls"].append(call)
        if kind in {"skill.invoked", "skill.invoked_ref"}:
            session["skills"].append({key: data[key] for key in ("skill", "skillName", "reference", "path") if key in data})
        if kind == "subagent.completed":
            session["subagents"].append({key: data[key] for key in ("agentName", "model", "totalTokens", "durationMs") if key in data})
    for call in starts.values():
        call["incomplete"] = True
        session["tool_calls"].append(call)
    return session


def read_copilot_sessions(root: str | Path, days: int = 7, max_sessions: int = 100, fast: bool = False) -> list[dict[str, Any]]:
    """Return recent sessions ordered newest-first; ``fast`` skips malformed lines."""
    base = Path(root).expanduser()
    cutoff = datetime.now(timezone.utc).timestamp() - max(0, int(days)) * 86400
    paths = [path for path in base.rglob("events.jsonl") if path.is_file() and path.stat().st_mtime >= cutoff]
    paths.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    sessions: list[dict[str, Any]] = []
    for path in paths[:max(0, int(max_sessions))]:
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    if isinstance(raw, dict):
                        rows.append(raw)
                except json.JSONDecodeError:
                    if not fast:
                        continue
        except OSError:
            continue
        sessions.append(_pair_events(path, rows))
    return sessions
