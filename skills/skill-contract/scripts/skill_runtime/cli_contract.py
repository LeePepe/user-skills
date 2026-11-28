"""Small CLI contracts shared by progressive skill scripts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REFERENCE = "skills/skill-contract/references/valves-and-metabolism.md"
_HOME_PATH = re.compile(r"/" + "Users/" + r"[^/\\s]+")
_SECRET = re.compile(r"(?i)\b(?:ghp_[A-Za-z0-9_\-]+|lin_api_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]+)\b")
_BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+\-/]+=*")

HERDR_ORIGIN_SCHEMA = "herdr-origin:v1"
HERDR_ORIGIN_KINDS = ("agent_dispatch", "human_routed")
HERDR_ORIGIN_ROLES = ("m2", "m1", "writer", "reviewer", "explorer", "pr_monitor", "designer")
HERDR_SESSION_ENV_VARS = ("HERDR_SPAWN_ORIGIN", "HERDR_AGENT_ROLE", "HERDR_PARENT_PANE")
HERDR_ORIGIN_COMMENT_RE = re.compile(r"<!--\s*herdr-origin:v1\s+([^>]*?)\s*-->", re.IGNORECASE | re.DOTALL)


def normalize_herdr_agent_role(role: str, name: str = "") -> str:
    raw_role = str(role or "").strip().casefold().replace(" ", "_")
    raw_name = str(name or "").strip().casefold()
    if raw_role in {"m2", "m2-dispatcher", "m2_dispatcher"} or (not raw_role and raw_name.startswith("m2-")):
        return "m2"
    if raw_role in {"tl", "team-lead", "team_lead", "steering", "m1", "m1_tl", "m1-tl", "arbitration", "planner"} or "-goal-tl-" in raw_name:
        return "m1"
    if raw_role in {"reviewer", "code-reviewer", "code_reviewer", "rev", "planner_reviewer"} or "-reviewer-" in raw_name:
        return "reviewer"
    if raw_role in {"writer", "writer_escalated", "writer-escalated", "coding-escalated", "mechanical"} or "-writer-" in raw_name:
        return "writer"
    if raw_role in {"explorer", "research_primary", "research_co", "research_arbiter"} or "-explorer-" in raw_name:
        return "explorer"
    if raw_role in {"pr_monitor", "pr-monitor", "prmon"} or "-pr-monitor-" in raw_name:
        return "pr_monitor"
    if raw_role == "designer":
        return "designer"
    return "writer"


def _herdr_origin_token(value: Any, default: str) -> str:
    token = re.sub(r"[\s>]+", "-", str(value or "").strip())
    return token or default


def format_herdr_origin_tag(*, kind: str, role: str = "", sender: str = "", goal: str = "", topic: str = "") -> str:
    normalized_kind = str(kind or "").strip().casefold()
    if normalized_kind == "human_routed":
        return f"<!-- {HERDR_ORIGIN_SCHEMA} kind=human_routed sender={_herdr_origin_token(sender, 'm3')} topic={_herdr_origin_token(topic, 'general')} -->"
    if normalized_kind == "agent_dispatch":
        return f"<!-- {HERDR_ORIGIN_SCHEMA} kind=agent_dispatch role={_herdr_origin_token(role, 'writer')} sender={_herdr_origin_token(sender, 'cli')} goal={_herdr_origin_token(goal, 'default')} -->"
    raise ValueError(f"unsupported herdr origin kind: {kind}")


def append_herdr_origin_tag(prompt: str, **kwargs: Any) -> str:
    text = str(prompt or "")
    if HERDR_ORIGIN_COMMENT_RE.search(text):
        return text
    return f"{text.rstrip()}\n\n{format_herdr_origin_tag(**kwargs)}"


def parse_herdr_origin_tag(text: Any) -> dict[str, str] | None:
    matches = list(HERDR_ORIGIN_COMMENT_RE.finditer(str(text or "")))
    if not matches:
        return None
    attrs = {key: value for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_-]*)=([^\s>]+)", matches[-1].group(1))}
    attrs["schema"] = HERDR_ORIGIN_SCHEMA
    return attrs


def strip_herdr_origin_comments(text: Any) -> str:
    return HERDR_ORIGIN_COMMENT_RE.sub("", str(text or "")).strip()


def split_routed_prompt_sections(text: Any) -> dict[str, Any]:
    raw = str(text or "")
    origin = parse_herdr_origin_tag(raw)
    clean = strip_herdr_origin_comments(raw)
    match = re.match(r"^\s*\[RAG/context hint\]\s*\n(.*?)\n\s*\[Dispatched instruction\]\s*\n(.*)$", clean, re.IGNORECASE | re.DOTALL)
    if match:
        hint, instruction = match.groups()
    else:
        hint, instruction = "", clean
    return {"rag_context_hint": hint.strip(), "dispatched_instruction": instruction.strip(), "origin": origin}


def extract_dispatched_instruction(text: Any) -> str:
    return str(split_routed_prompt_sections(text)["dispatched_instruction"])


class SelfDisclosingArgumentParser(argparse.ArgumentParser):
    """Bound parser errors to three actionable lines."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {message}\n")
        sys.stderr.write("Try --help or an unscoped command (audit/eval default to --all).\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)


def redact_text(value: str) -> str:
    value = _HOME_PATH.sub("~", str(value))
    value = _SECRET.sub("<redacted>", value)
    return _BEARER.sub(r"\1<redacted>", value)


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    return value


def _encoded(value: Any) -> str:
    return json.dumps(redact_payload(value), ensure_ascii=False, separators=(",", ":"))


def compact_json_dumps(payload: Any, *, max_bytes: int = 15_500, summary: bool = False, preserve_keys: tuple[str, ...] | list[str] = ()) -> str:
    """Serialize redacted JSON without exceeding the caller's byte budget."""
    if max_bytes < 64:
        raise ValueError("max_bytes must be at least 64")
    text = _encoded(payload)
    if not summary and len(text.encode("utf-8")) <= max_bytes:
        return text
    if isinstance(payload, dict):
        keep = {key: redact_payload(payload[key]) for key in preserve_keys if key in payload}
        compact = {"summary": True, **keep}
        text = _encoded(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        for key in list(compact):
            if key not in preserve_keys and key != "summary":
                compact.pop(key, None)
        text = _encoded(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
    marker = {"summary": True, "truncated": True}
    for key in preserve_keys:
        if isinstance(payload, dict) and key in payload:
            marker[key] = redact_payload(payload[key])
    text = _encoded(marker)
    while len(text.encode("utf-8")) > max_bytes and len(marker) > 2:
        marker.pop(next(iter(k for k in marker if k not in {"summary", "truncated"})))
        text = _encoded(marker)
    return text


def is_valid_skill_slug(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(name)))
