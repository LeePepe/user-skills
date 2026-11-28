"""Bounded handoff-loss and m2-router parameter-trap detection."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

HANDOFF_INSTRUCTION_DROP = "HANDOFF_INSTRUCTION_DROP"
M2_ROUTER_PARAM_TRAP = "M2_ROUTER_PARAM_TRAP"
_DISPATCH_ROLES = {"m2", "m1", "writer", "team-lead", "m3"}
_OWNER_SHORT = re.compile(r"^(?:做|批准|继续|派发|先聊聊|先讨论|/status)\b", re.I)
_POINTER = re.compile(r"(?:/|\.md\b|\.py\b|\.swift\b|\.json\b|handoff/)", re.I)
_DISCOVERY = re.compile(r"(?:\bgrep\b|\bglob\b|\bfind\b|session_store_sql|\brg\b)", re.I)
_KNOWN_DOMAINS = ("my-designer", "design-studio", "skill-retro", "agent-retro", "retro-loop")


def _data(event: dict[str, Any]) -> dict[str, Any]:
    nested = event.get("data")
    return nested if isinstance(nested, dict) else event


def _field(event: dict[str, Any], *names: str) -> Any:
    data = _data(event)
    for name in names:
        if name in data:
            return data[name]
        if name in event:
            return event[name]
    return None


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or _data(event).get("eventType") or "").lower()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value or "")


def _arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = _field(event, "arguments", "args", "toolArguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"command": value}
    return {}


def _command(event: dict[str, Any]) -> str:
    args = _arguments(event)
    return str(args.get("command") or args.get("cmd") or _field(event, "command") or "")


def _tool_name(event: dict[str, Any]) -> str:
    return str(_field(event, "toolName", "tool_name", "name") or "").lower()


def _event_blob(event: dict[str, Any]) -> str:
    args = _arguments(event)
    result = _field(event, "result", "toolResult", "resultContent", "output")
    return " ".join(
        part for part in (
            _event_type(event),
            _text(_field(event, "content", "message", "assistantContent", "userContent")),
            _command(event),
            _text(args),
            _text(result),
        ) if part
    )


def _session_id(event: dict[str, Any], fallback: str) -> str:
    return str(_field(event, "sessionId", "session_id", "conversationId") or fallback)


def _role(events: list[dict[str, Any]]) -> str:
    explicit = next((str(_field(event, "role") or "").lower() for event in events if _field(event, "role")), "")
    aliases = {
        "m2-dispatcher": "m2",
        "dispatcher": "m2",
        "personal-m2-assistant": "m3",
        "team-lead": "m1",
        "steering": "m1",
        "coding": "writer",
        "reviewer": "reviewer",
    }
    if explicit in aliases:
        return aliases[explicit]
    if explicit in _DISPATCH_ROLES:
        return explicit
    text = " ".join(_text(_data(event)) for event in events).lower()
    for role in ("m2", "m1", "writer", "team-lead", "m3"):
        if role in text:
            return role
    return ""


def _first_user_prompt(events: list[dict[str, Any]]) -> str:
    for event in events:
        if _event_type(event) not in {"user.message", "owner.message"}:
            continue
        value = _field(event, "content", "message", "userContent")
        if value:
            return _text(value).strip()
    return ""


def _prompt_terms(prompt: str) -> list[str]:
    compact = re.sub(r"\s+", "", prompt).lower()
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", prompt.lower())
    return [term for term in dict.fromkeys(terms) if len(term) >= 2 or term.isdigit() or term in compact]


def _searches_prompt(event: dict[str, Any], prompt: str) -> bool:
    command = _command(event)
    tool = _tool_name(event)
    if not (_DISCOVERY.search(command) or tool in {"grep", "glob", "find", "session_store_sql"}):
        return False
    haystack = _event_blob(event).lower()
    compact_prompt = re.sub(r"\s+", "", prompt).lower()
    compact_haystack = re.sub(r"\s+", "", haystack)
    if compact_prompt and compact_prompt in compact_haystack:
        return True
    terms = _prompt_terms(prompt)
    return sum(1 for term in terms if term in haystack) >= 2


def _instruction_drop(text: str) -> bool:
    if not re.search(r"\binstruction\b|\bprompt\b|\bdetail\b|\bbody\b", text, re.I):
        return False
    message = re.search(r"(?:--message(?:=|\s+)|[\"']message[\"']\s*:\s*[\"'])([^\"'\n]{1,99})", text, re.I)
    return bool(message) and not re.search(r"\bsub_prompt\b", text, re.I)


def detect_handoff_signals(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic, redaction-free signal records for one or more sessions."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if isinstance(event, dict):
            groups[_session_id(event, "unknown")].append(event)
    signals: list[dict[str, Any]] = []
    for sid, sequence in groups.items():
        role = _role(sequence)
        prompt = _first_user_prompt(sequence)
        if (
            role in _DISPATCH_ROLES
            and 0 < len(prompt) < 100
            and not _POINTER.search(prompt)
            and not _OWNER_SHORT.search(prompt.strip())
            and not re.match(r"^\s*(?:先聊聊|先讨论)\b", prompt)
        ):
            early_tools = []
            for event in sequence:
                if _event_type(event).startswith("tool."):
                    early_tools.append(event)
                if _tool_name(event) in {"edit", "create", "apply_patch", "write", "patch"}:
                    break
                if "spec.md" in _event_blob(event).lower() and _tool_name(event) in {"view", "read", "cat"}:
                    break
                if len(early_tools) >= 8:
                    break
            discovery = next((event for event in early_tools if _searches_prompt(event, prompt)), None)
            if discovery is not None:
                signals.append({
                    "code": HANDOFF_INSTRUCTION_DROP,
                    "session_id": sid,
                    "upstream_skill": "personal-m2-assistant" if role in {"m2", "m3"} else "team-lead",
                    "root_cause": "short dispatched prompt caused early reverse-discovery waste",
                    "evidence": [
                        f"{sid}: prompt length={len(prompt)} lacks path/spec pointer",
                        f"{sid}: {_tool_name(discovery) or 'tool'} rediscovered prompt terms in first 8 tool events",
                    ],
                })
        router_text = " ".join(_event_blob(event) for event in sequence)
        if "m2-router.py" not in router_text and "m2-router" not in router_text:
            continue
        known_domain = next((domain for domain in _KNOWN_DOMAINS if domain in router_text), None)
        marker = bool(re.search(
            r"new-topic-unmapped-domain|[\"']space[\"']\s*:\s*[\"']unspecified|"
            r"[\"']target_repo[\"']\s*:\s*[\"']unspecified",
            router_text,
            re.I,
        ))
        if known_domain and (marker or any(_instruction_drop(_event_blob(event)) for event in sequence)):
            reason = "unmapped domain created an unspecified space" if marker else "target instruction was reduced to top-level message"
            signals.append({
                "code": M2_ROUTER_PARAM_TRAP,
                "session_id": sid,
                "upstream_skill": "personal-m2-assistant",
                "root_cause": reason,
                "evidence": [f"{sid}: m2-router.py trace contains {known_domain} and parameter trap"],
            })
    return signals
