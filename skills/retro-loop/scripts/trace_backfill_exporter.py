#!/usr/bin/env python3
"""Historical Goal & Session Trace Backfill Exporter for Arize Phoenix & Langfuse.

Reconstructs local real historical Goal workflows and Session Prompt execution chains
from genuine recorded telemetry (never fabricating missing executions, timings, or token metrics)
into hierarchical Traces + 5 nested Child Span categories:
  1. Stage / Role Span (M3 -> M2 -> M1 TL -> Designer / Writer / Reviewer / Verifier / AntiCheat)
  2. Skill Load Span (e.g. team-lead, my-designer, retro-loop, deep-research)
  3. L1 Reference Read Span (SKILL.md / references/*.md, byte_len, lines, token estimate)
  4. L2 Script Execution Span (scripts/*.py|.sh, args, duration_ms, exit_code)
  5. LLM Generation Span (model, input_tokens, output_tokens, cache_read)

Exports idempotently with deterministic Trace IDs / Span IDs via:
  - OpenTelemetry OTLP HTTP (/v1/traces, protobuf) -> Arize Phoenix (:6006)
  - Langfuse Batch Ingestion API (/api/public/ingestion) -> Self-hosted Langfuse (:13100)
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import time
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_HANDOFF_ROOT = Path.home() / "Development" / "handoff"
DEFAULT_COPILOT_SESSIONS_DIR = Path.home() / ".copilot" / "session-state"
DEFAULT_USAGE_JSONL = Path.home() / ".local" / "share" / "skill-telemetry" / "usage.jsonl"
DEFAULT_WAREHOUSE_DB = Path.home() / "Development" / "AIDash" / "aidata" / "L3_merge" / "warehouse.db"
DEFAULT_OBS_CONFIG = Path.home() / ".local" / "share" / "agent-observability" / "config.json"

PRIORITY_GOALS = (
    "all-skills-audit-and-refactor",
    "financial-rich-interactive-40-sections",
    "aidata-real-anomalies-auto-heal",
    "vs-ui-l4-holistic-dashboard",
    "vs-ui-l1-steps-body-settings",
    "vs-ui-l1-heart-sleep-detail",
    "vs-ui-l2-workout-load-progression",
)

SPAN_CATEGORIES = (
    "stage_role",
    "skill_load",
    "l1_reference_read",
    "l2_script_exec",
    "llm_generation",
)

_LINEAR_ISSUE_RE = re.compile(r"\b(TIA-\d+)\b")
_REPO_RE = re.compile(r"\b(?:LeePepe/[A-Za-z0-9_.-]+|repo=([^\s;|]+))")
_EVT_LINE_RE = re.compile(r"^-\s*([0-9T:+-]+Z?)\s*\|\s*(.+)$")
_HANDOFF_GOAL_REF_RE = re.compile(r"handoff/([A-Za-z0-9_-]+)(?:/|[\s\"'])")
_SKILL_CONTEXT_RE = re.compile(
    r"^\s*<skill-context\s+name=[\"']([A-Za-z0-9_-]+)[\"']\s*>[\s\S]*?</skill-context>\s*$"
)
_RECEIPT_TOKENS_RE = re.compile(r"^-\s+(?:\*\*)?Tokens:(?:\*\*)?\s*(.+)$", re.M | re.I)
_SCRIPT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])((?:[^\s\"']+/)?skills/[A-Za-z0-9_-]+/scripts/[A-Za-z0-9_.-]+\.(?:py|sh)"
    r"|(?:[^\s\"']+/)?scripts/(?:verify|[A-Za-z0-9_.-]+\.py))"
)
_SHELL_START_RE = re.compile(r"<command (?:started in background )?with shellId:\s*([^\s>]+)")
_SHELL_COMPLETE_RE = re.compile(r"<shellId:\s*([^\s>]+)\s+completed with exit code\s+(-?\d+)>")
_SHELL_STOPPED_RE = re.compile(r"<command with id:\s*([^\s>]+)\s+stopped>")
_VERDICT_LINE_RE = re.compile(
    r"(?:\bV5\s*Verdict|\bVERIFIER_VERDICT|\bANTICHEAT_VERDICT|\bFinal\s*Verdict)\s*(?:\*\*|`)*\s*[:=]\s*(?:\*\*|`)*\s*"
    r"(PASS|CONCERNS|WAIVED|FAIL|CLEAN_NO_CHEATING_DETECTED|DONE|COMPLETED)\b",
    re.I,
)
_RECEIPT_FILE_REF_RE = re.compile(r"receipts/([A-Za-z0-9_.-]+\.md)")
_BRIEF_FILE_REF_RE = re.compile(r"briefs/([A-Za-z0-9_.-]+\.md)")
_WORKTREE_GOAL_RE = re.compile(r"/worktrees/([A-Za-z0-9_-]+)")
_TOKEN_NUM_RE = re.compile(r"\b(?:input[_ ]tokens?|in_tokens?)\s*[:=]\s*(\d+)\b", re.I)
_OUT_TOKEN_RE = re.compile(r"\b(?:output[_ ]tokens?|out_tokens?)\s*[:=]\s*(\d+)\b", re.I)
_CACHE_TOKEN_RE = re.compile(r"\b(?:cache[_ ]read(?:[_ ]tokens?)?)\s*[:=]\s*(\d+)\b", re.I)


@dataclass
class SpanRecord:
    span_id: str  # 16-char lowercase hex
    parent_span_id: str | None  # 16-char lowercase hex or None
    category: str  # root | stage_role | skill_load | l1_reference_read | l2_script_exec | llm_generation
    name: str
    start_ns: int
    end_ns: int
    status_ok: bool | None = True  # True=OK, False=ERROR, None=UNSET/UNKNOWN
    attributes: dict[str, str | int | float | bool] = field(default_factory=dict)
    input_payload: dict[str, Any] | str | None = None
    output_payload: dict[str, Any] | str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0


@dataclass
class TraceRecord:
    trace_id: str  # 32-char lowercase hex
    root_span_id: str  # 16-char lowercase hex
    trace_kind: str  # "goal" | "session"
    entity_id: str  # goal_id or session_id
    name: str
    start_ns: int
    end_ns: int
    status: str
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    tags: list[str]
    metadata: dict[str, Any]
    spans: list[SpanRecord] = field(default_factory=list)


@dataclass
class ParsedSessionActivity:
    session_id: str
    source_path: str
    selected_model: str
    cwd: str
    first_prompt: str
    start_ns: int
    end_ns: int
    referenced_goals: set[str]
    skill_invocations: list[tuple[str, int, str]]  # (skill_name, start_ns, source_id)
    ref_reads: list[tuple[str, int | None, int, str]]  # (ref_path, byte_len|None, start_ns, source_id)
    script_execs: list[tuple[str, str, int, int, int | None, str]]  # (script_path, args, start_ns, dur_ms, exit_code|None, source_id)
    llm_calls: list[tuple[str, int, int, int, int, int, str]]  # (model, start_ns, dur_ms, in, out, cache, source_id)
    measured_durations_ms: dict[str, int] = field(default_factory=dict)
    tool_transport_success: dict[str, bool] = field(default_factory=dict)
    primary_goal_id: str | None = None
    receipt_writes: set[str] = field(default_factory=set)
    receipt_mentions: set[str] = field(default_factory=set)
    brief_mentions: set[str] = field(default_factory=set)
    last_assistant_excerpt: str = ""


def _point_event_timing() -> dict[str, Any]:
    return {"timing.measured": False, "timing.provenance": "point_event_unmeasured"}


def _apply_activity_evidence(spans: list[SpanRecord], activity: ParsedSessionActivity) -> None:
    for span in spans:
        if span.category not in ("skill_load", "l1_reference_read", "l2_script_exec", "llm_generation"):
            continue
        source_id = str(span.attributes["evidence.source"])
        duration_ms = activity.measured_durations_ms.get(source_id)
        timing = ({"timing.measured": True, "timing.provenance": "measured"}
                  if duration_ms is not None else _point_event_timing())
        # A 1 ms display width is not an execution-duration measurement.
        span.end_ns = span.start_ns + (duration_ms if duration_ms is not None else 1) * 1_000_000
        span.attributes.update(timing)
        span.output_payload.update(timing)
        if span.category == "l2_script_exec" and duration_ms is None:
            span.attributes.pop("script.duration_ms", None)
            span.output_payload["duration_ms"] = None
        if source_id in activity.tool_transport_success:
            span.attributes["tool.transport_success"] = activity.tool_transport_success[source_id]


def deterministic_trace_id(seed: str) -> str:
    digest = hashlib.sha256(f"trace:{seed}".encode("utf-8")).hexdigest()[:32]
    return digest if int(digest, 16) != 0 else "1" * 32


def deterministic_span_id(seed: str) -> str:
    digest = hashlib.sha256(f"span:{seed}".encode("utf-8")).hexdigest()[:16]
    return digest if int(digest, 16) != 0 else "1" * 16


def parse_iso_to_ns(raw: str | int | float | None, fallback_ns: int | None = None) -> int:
    if raw is None or raw == "":
        return fallback_ns if fallback_ns is not None else int(time.time() * 1_000_000_000)
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1e16:
            return int(val)
        if val > 1e12:
            return int(val * 1_000_000)
        return int(val * 1_000_000_000)
    text = str(raw).strip()
    if not text:
        return fallback_ns if fallback_ns is not None else int(time.time() * 1_000_000_000)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch_delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (epoch_delta.days * 86400 + epoch_delta.seconds) * 1_000_000_000 + epoch_delta.microseconds * 1000
    except ValueError:
        return fallback_ns if fallback_ns is not None else int(time.time() * 1_000_000_000)


def ns_to_iso_utc(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clamp_span_window(start_ns: int, end_ns: int, min_duration_ns: int = 1_000_000) -> tuple[int, int]:
    if end_ns <= start_ns:
        return start_ns, start_ns + min_duration_ns
    return start_ns, end_ns


def _estimate_tokens_from_bytes(byte_len: int) -> int:
    if byte_len <= 0:
        return 0
    return max(1, int(round(byte_len / 4.0)))


def _infer_skill_from_path(path_str: str) -> str:
    m = re.search(r"skills/([A-Za-z0-9_-]+)/", path_str)
    if m:
        return m.group(1)
    return "repo-gate"


_NON_ROLE_ARTIFACT_TOKENS = frozenset(
    {
        "matrix",
        "baseline",
        "metrics",
        "inventory",
        "snapshot",
        "manifest",
        "index",
        "summary",
        "changelog",
        "readme",
        "telemetry",
    }
)


def _classify_receipt_role(raw_role: str) -> str | None:
    """Return canonical role name ONLY when raw_role represents an actual agent/role receipt.

    Non-role artifact filenames (such as `all-skills-before-after-matrix` or `baseline-metrics`)
    return None so they are never misclassified as `M1 TL` leaf stage_role spans.
    """
    cleaned = raw_role.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", lower) if tok]
    token_set = set(tokens)

    if "anticheat" in lower or ("anti" in token_set and "cheat" in token_set):
        return "AntiCheat"
    if "designer" in token_set or (tokens and tokens[0] == "des"):
        return "Designer"
    if "reviewer" in token_set or "review" in token_set or "code_reviewer" in lower or any(
        re.fullmatch(r"rev|rv\d*", tok) for tok in tokens
    ):
        return "Reviewer"
    if "verifier" in token_set or "verify" in token_set:
        return "Verifier"
    if "writer" in token_set or "coder" in token_set or "worker" in token_set:
        return "Writer"
    if "pr_monitor" in lower or ("pr" in token_set and "monitor" in token_set):
        return "Verifier"
    if "m3" in token_set or "owner" in token_set:
        return "M3"
    if "m2" in token_set or "coordinator" in token_set or "dispatcher" in token_set:
        return "M2"
    if (
        "m1" in token_set
        or "tl" in token_set
        or "steering" in token_set
        or "team_lead" in lower
        or lower.startswith(("goal-r10", "goal_done", "r10-final-goal", "r10-goal"))
    ):
        return "M1 TL"
    if token_set & _NON_ROLE_ARTIFACT_TOKENS:
        return None
    if any(re.fullmatch(r"(?:tk|t)\d+[a-z]*", tok) for tok in tokens) and any(
        re.fullmatch(r"r\d+", tok) for tok in tokens
    ):
        return "Writer"
    return None


def _normalize_role(raw_role: str) -> str | None:
    """Normalize a role string or receipt stem; returns None for non-role artifacts."""
    return _classify_receipt_role(raw_role)


def _extract_receipt_verdict(markdown_text: str) -> str:
    m_verd = _VERDICT_LINE_RE.search(markdown_text)
    if not m_verd:
        return "RECORDED"
    raw = m_verd.group(1).upper()
    if raw in ("CLEAN_NO_CHEATING_DETECTED", "DONE", "COMPLETED"):
        return "PASS"
    return raw


def _extract_receipt_excerpt(markdown_text: str, max_chars: int = 600) -> str:
    """Extract a concise, high-signal markdown summary/verdict excerpt up to max_chars."""
    lines: list[str] = []
    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == "---" or stripped.startswith("|---"):
            continue
        lines.append(stripped)
        if sum(len(x) + 1 for x in lines) >= max_chars + 120:
            break
    joined = "\n".join(lines).strip()
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 1].rstrip() + "…"


def _infer_session_role(activity: ParsedSessionActivity) -> str:
    lower_prompt = (activity.first_prompt or "").lower()
    lower_model = (activity.selected_model or "").lower()
    writes_lower = {w.lower() for w in activity.receipt_writes}

    # Explicit top-level M1 Steering / M2 Dispatcher prompts take precedence over subagent receipt mentions
    if "anti-cheat" in lower_prompt or "anticheat" in lower_prompt or any(
        "anticheat" in w or "anti-cheat" in w for w in writes_lower
    ):
        return "AntiCheat"
    if (
        "m2-skills-verifier" in lower_prompt
        or "functional & efficacy verification" in lower_prompt
        or any("verifier" in w for w in writes_lower)
    ):
        return "Verifier"
    if (
        lower_prompt.startswith("load /team-lead")
        or lower_prompt.startswith("load skill team-lead")
        or "steering" in lower_model
        or "m1 tl" in lower_prompt
        or "m1 team lead" in lower_prompt
        or "m1 steering" in lower_prompt
        or any(w.startswith(("goal-r10", "goal_done")) for w in writes_lower)
    ):
        return "M1 TL"
    if "m2-dispatcher" in lower_prompt or "load skill m2-dispatcher" in lower_prompt or "load /m2-dispatcher" in lower_prompt:
        return "M2"
    if (
        any("reviewer" in w or "review" in w for w in writes_lower)
        or "reviewing" in lower_model
        or "load /code-review" in lower_prompt
        or "load skills code-review" in lower_prompt
        or "independent v1-v5" in lower_prompt
        or "reviewer" in lower_prompt
    ):
        return "Reviewer"
    if "designer" in lower_model or "role: designer" in lower_prompt:
        return "Designer"
    if (
        any("writer" in w for w in writes_lower)
        or "coding" in lower_model
        or ("you do not dispatch subagents" in lower_prompt and "review" not in lower_prompt)
    ):
        return "Writer"
    if "team-lead" in lower_prompt:
        return "M1 TL"
    if "writer" in lower_prompt or "implement" in lower_prompt:
        return "Writer"
    return "M1 TL"


def _resolve_primary_goal_id(
    *,
    cwd: str,
    first_prompt: str,
    ordered_goals: Sequence[str],
    known_goals: set[str] | None = None,
) -> str | None:
    """Resolve the authoritative Goal ID that owns a Session Activity, if any."""
    candidates: list[str] = []

    for m_goal in _HANDOFF_GOAL_REF_RE.finditer((cwd or "") + "/"):
        g = m_goal.group(1)
        if not g.startswith("_"):
            candidates.append(g)

    m_wt = _WORKTREE_GOAL_RE.search(cwd or "")
    if m_wt:
        wt_name = m_wt.group(1)
        if known_goals:
            if wt_name in known_goals:
                candidates.append(wt_name)
            else:
                prefix_matches = sorted(
                    (g for g in known_goals if wt_name.startswith(g + "-")),
                    key=len,
                    reverse=True,
                )
                if prefix_matches:
                    candidates.append(prefix_matches[0])
        elif wt_name in ordered_goals:
            candidates.append(wt_name)

    for m_goal in _HANDOFF_GOAL_REF_RE.finditer(first_prompt or ""):
        g = m_goal.group(1)
        if not g.startswith("_"):
            candidates.append(g)

    m_named = re.search(r"\bGoal\s+`?([a-z0-9]+(?:-[a-z0-9]+)+)`?", first_prompt or "")
    if m_named:
        g_named = m_named.group(1)
        if not g_named.startswith("_") and (not known_goals or g_named in known_goals):
            candidates.append(g_named)

    candidates.extend(ordered_goals)

    for cand in candidates:
        if cand in ("merge", "goal", "example", "sample"):
            continue
        if known_goals and cand not in known_goals:
            continue
        return cand
    for cand in candidates:
        if cand not in ("merge", "goal", "example"):
            return cand
    return None


def _file_stats(candidate: str, skills_root: Path | None = None) -> tuple[int | None, int | None]:
    expanded = Path(candidate).expanduser()
    paths_to_try = [expanded]
    if skills_root is not None and not expanded.is_absolute():
        paths_to_try.append(skills_root.parent / candidate)
    for p in paths_to_try:
        if p.is_file():
            try:
                raw = p.read_bytes()
                return len(raw), max(1, raw.count(b"\n") + 1)
            except OSError:
                break
    return None, None


# ---------------------------------------------------------------------------
# Pure-Python OpenTelemetry OTLP/HTTP Protobuf Encoder (zero external deps)
# ---------------------------------------------------------------------------


def _pb_varint(value: int) -> bytes:
    n = value & ((1 << 64) - 1)
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pb_field_varint(field_num: int, value: int) -> bytes:
    return _pb_varint((field_num << 3) | 0) + _pb_varint(value)


def _pb_field_fixed64(field_num: int, value: int) -> bytes:
    return _pb_varint((field_num << 3) | 1) + int(value).to_bytes(8, "little", signed=False)


def _pb_field_bytes(field_num: int, payload: bytes | str) -> bytes:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return _pb_varint((field_num << 3) | 2) + _pb_varint(len(raw)) + raw


def _pb_kv(key: str, val: Any) -> bytes:
    if isinstance(val, bool):
        any_val = _pb_field_varint(2, 1 if val else 0)
    elif isinstance(val, int):
        any_val = _pb_field_varint(3, val)
    elif isinstance(val, float):
        any_val = _pb_varint((4 << 3) | 1) + struct.pack("<d", val)
    else:
        any_val = _pb_field_bytes(1, str(val))
    return _pb_field_bytes(1, key) + _pb_field_bytes(2, any_val)


def _openinference_kind(category: str) -> str:
    return {
        "root": "CHAIN",
        "stage_role": "AGENT",
        "skill_load": "CHAIN",
        "l1_reference_read": "RETRIEVER",
        "l2_script_exec": "TOOL",
        "llm_generation": "LLM",
    }.get(category, "CHAIN")


def encode_otlp_traces_protobuf(project_name: str, traces: Sequence[TraceRecord]) -> bytes:
    span_blobs: list[bytes] = []
    for tr in traces:
        for sp in tr.spans:
            start_ns, end_ns = _clamp_span_window(sp.start_ns, sp.end_ns)
            parts: list[bytes] = [
                _pb_field_bytes(1, bytes.fromhex(tr.trace_id)),
                _pb_field_bytes(2, bytes.fromhex(sp.span_id)),
            ]
            if sp.parent_span_id:
                parts.append(_pb_field_bytes(4, bytes.fromhex(sp.parent_span_id)))
            parts.extend(
                [
                    _pb_field_bytes(5, sp.name),
                    _pb_field_varint(6, 1),  # SPAN_KIND_INTERNAL
                    _pb_field_fixed64(7, start_ns),
                    _pb_field_fixed64(8, end_ns),
                ]
            )
            merged_attrs: dict[str, Any] = {
                "openinference.span.kind": _openinference_kind(sp.category),
                "multica.span.category": sp.category,
                "multica.trace.kind": tr.trace_kind,
                "multica.entity.id": tr.entity_id,
            }
            merged_attrs.update(sp.attributes)
            if sp.input_payload is not None:
                merged_attrs["input.value"] = (
                    sp.input_payload
                    if isinstance(sp.input_payload, str)
                    else json.dumps(sp.input_payload, ensure_ascii=False)
                )
            if sp.output_payload is not None:
                merged_attrs["output.value"] = (
                    sp.output_payload
                    if isinstance(sp.output_payload, str)
                    else json.dumps(sp.output_payload, ensure_ascii=False)
                )
            if sp.model:
                merged_attrs["llm.model_name"] = sp.model
            if sp.input_tokens or sp.output_tokens or sp.cache_read:
                merged_attrs["llm.token_count.prompt"] = int(sp.input_tokens)
                merged_attrs["llm.token_count.completion"] = int(sp.output_tokens)
                merged_attrs["llm.token_count.total"] = int(sp.input_tokens + sp.output_tokens)
                merged_attrs["llm.token_count.prompt_details.cache_read"] = int(sp.cache_read)

            for k, v in merged_attrs.items():
                if v is not None:
                    parts.append(_pb_field_bytes(9, _pb_kv(k, v)))

            # Status: field 3 = code (0=UNSET, 1=OK, 2=ERROR)
            status_code = 0 if sp.status_ok is None else (1 if sp.status_ok else 2)
            status_pb = _pb_field_varint(3, status_code)
            parts.append(_pb_field_bytes(15, status_pb))
            span_blobs.append(b"".join(parts))

    resource_pb = _pb_field_bytes(1, _pb_kv("service.name", project_name)) + _pb_field_bytes(
        1, _pb_kv("openinference.project.name", project_name)
    )
    scope_pb = _pb_field_bytes(1, "retro-loop.trace_backfill_exporter")
    scope_spans_pb = _pb_field_bytes(1, scope_pb) + b"".join(_pb_field_bytes(2, b) for b in span_blobs)
    resource_spans_pb = _pb_field_bytes(1, resource_pb) + _pb_field_bytes(2, scope_spans_pb)
    return _pb_field_bytes(1, resource_spans_pb)


# ---------------------------------------------------------------------------
# Langfuse Batch Ingestion Payload Builder
# ---------------------------------------------------------------------------


def build_langfuse_ingestion_events(trace: TraceRecord) -> list[dict[str, Any]]:
    session_id = str(
        trace.metadata.get("langfuse_session_id")
        or (f"goal:{trace.entity_id}" if trace.trace_kind == "goal" else trace.entity_id)
    )
    events: list[dict[str, Any]] = [
        {
            "id": deterministic_trace_id(f"lf-evt:trace:{trace.trace_id}"),
            "timestamp": ns_to_iso_utc(trace.start_ns),
            "type": "trace-create",
            "body": {
                "id": trace.trace_id,
                "timestamp": ns_to_iso_utc(trace.start_ns),
                "name": trace.name,
                "sessionId": session_id,
                "input": trace.input_payload,
                "output": trace.output_payload,
                "tags": trace.tags,
                "metadata": trace.metadata,
            },
        }
    ]

    for sp in trace.spans:
        if sp.span_id == trace.root_span_id:
            continue
        start_ns, end_ns = _clamp_span_window(sp.start_ns, sp.end_ns)
        parent_obs_id = None if sp.parent_span_id in (None, trace.root_span_id) else sp.parent_span_id
        obs_meta = dict(sp.attributes)
        obs_meta["category"] = sp.category
        level = "WARNING" if sp.status_ok is None else ("DEFAULT" if sp.status_ok else "ERROR")
        if sp.category == "llm_generation":
            obs_meta["cache_read"] = sp.cache_read
            events.append(
                {
                    "id": deterministic_trace_id(f"lf-evt:gen:{trace.trace_id}:{sp.span_id}"),
                    "timestamp": ns_to_iso_utc(start_ns),
                    "type": "generation-create",
                    "body": {
                        "id": sp.span_id,
                        "traceId": trace.trace_id,
                        "parentObservationId": parent_obs_id,
                        "name": sp.name,
                        "startTime": ns_to_iso_utc(start_ns),
                        "endTime": ns_to_iso_utc(end_ns),
                        "model": sp.model or str(sp.attributes.get("llm.model_name", "unknown")),
                        "input": sp.input_payload,
                        "output": sp.output_payload,
                        "usage": {
                            "input": int(sp.input_tokens),
                            "output": int(sp.output_tokens),
                            "total": int(sp.input_tokens + sp.output_tokens),
                            "unit": "TOKENS",
                        },
                        "level": level,
                        "metadata": obs_meta,
                    },
                }
            )
        else:
            events.append(
                {
                    "id": deterministic_trace_id(f"lf-evt:span:{trace.trace_id}:{sp.span_id}"),
                    "timestamp": ns_to_iso_utc(start_ns),
                    "type": "span-create",
                    "body": {
                        "id": sp.span_id,
                        "traceId": trace.trace_id,
                        "parentObservationId": parent_obs_id,
                        "name": sp.name,
                        "startTime": ns_to_iso_utc(start_ns),
                        "endTime": ns_to_iso_utc(end_ns),
                        "input": sp.input_payload,
                        "output": sp.output_payload,
                        "level": level,
                        "metadata": obs_meta,
                    },
                }
            )
    return events


# ---------------------------------------------------------------------------
# Historical Data Loaders (Warehouse + Usage JSONL + Copilot Session State)
# ---------------------------------------------------------------------------


def _load_warehouse_maps(warehouse_db: Path) -> dict[str, Any]:
    maps: dict[str, Any] = {
        "goals": {},
        "goal_events": {},
        "goal_runs": {},
        "turns_by_session": {},
        "refs_by_session": {},
        "scripts_by_session": {},
        "dim_sessions": {},
    }
    if not warehouse_db.is_file():
        return maps
    try:
        conn = sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return maps

    with conn:
        for table_query, handler in (
            ("SELECT * FROM dim_goal", lambda r: maps["goals"].setdefault(r["goal_id"], dict(r))),
            (
                "SELECT * FROM fact_gtt_event ORDER BY line_no ASC",
                lambda r: maps["goal_events"].setdefault(r["goal_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM fact_gtt_task_run ORDER BY round_no ASC",
                lambda r: maps["goal_runs"].setdefault(r["goal_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM fact_turn ORDER BY ts ASC",
                lambda r: maps["turns_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM reference_hits ORDER BY ts ASC",
                lambda r: maps["refs_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM script_call ORDER BY ts ASC",
                lambda r: maps["scripts_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM dim_session",
                lambda r: maps["dim_sessions"].setdefault(r["session_id"], dict(r)),
            ),
        ):
            try:
                for row in conn.execute(table_query):
                    handler(row)
            except sqlite3.Error:
                continue
    conn.close()
    return maps


def _load_usage_jsonl(usage_jsonl: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_session: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if not usage_jsonl.is_file():
        return by_session
    try:
        with usage_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(rec.get("session_id") or "").strip()
                kind = str(rec.get("kind") or "").strip()
                if not sid or not kind:
                    continue
                bucket = by_session.setdefault(sid, {"skill_turn": [], "reference_hit": [], "script_call": []})
                if kind in bucket:
                    bucket[kind].append(rec)
    except OSError:
        pass
    return by_session


def _extract_process_exit_code(data: dict[str, Any], result_text: str) -> int | None:
    shell_exec = data.get("shellExecution")
    if isinstance(shell_exec, dict) and shell_exec.get("exitCode") is not None:
        try:
            return int(shell_exec["exitCode"])
        except (TypeError, ValueError):
            pass
    m_done = _SHELL_COMPLETE_RE.search(result_text)
    if m_done:
        try:
            return int(m_done.group(2))
        except ValueError:
            pass
    if _SHELL_START_RE.search(result_text) or "is still running" in result_text:
        return None
    return None


def parse_copilot_session_activity(
    events_jsonl: Path,
    wh_maps: dict[str, Any],
    usage_by_session: dict[str, dict[str, list[dict[str, Any]]]],
    skills_root: Path | None = None,
    known_goals: set[str] | None = None,
) -> ParsedSessionActivity | None:
    session_id = events_jsonl.parent.name
    selected_model = "unknown"
    cwd = ""
    first_prompt = ""
    last_assistant_excerpt = ""
    start_ns: int | None = None
    end_ns: int | None = None
    referenced_goals: set[str] = set()
    ordered_goals: list[str] = []
    receipt_writes: set[str] = set()
    receipt_mentions: set[str] = set()
    brief_mentions: set[str] = set()

    def _record_goal_ref(goal_name: str) -> None:
        if goal_name and not goal_name.startswith("_") and goal_name not in ("merge", "goal", "example"):
            referenced_goals.add(goal_name)
            if goal_name not in ordered_goals:
                ordered_goals.append(goal_name)

    def _record_file_mentions(raw_text: str, *, is_write: bool = False) -> None:
        for m_rcpt in _RECEIPT_FILE_REF_RE.finditer(raw_text):
            r_name = m_rcpt.group(1)
            receipt_mentions.add(r_name)
            if is_write:
                receipt_writes.add(r_name)
        for m_br in _BRIEF_FILE_REF_RE.finditer(raw_text):
            brief_mentions.add(m_br.group(1))

    skill_invocations: list[tuple[str, int, str]] = []
    ref_reads: list[tuple[str, int | None, int, str]] = []
    # Pending bash scripts that moved to background/async keyed by shellId
    pending_async_shells: dict[str, tuple[str, str, int, str]] = {}  # shell_id -> (script_path, cmd_args, start_ns, tc_id)
    script_execs: list[tuple[str, str, int, int, int | None, str]] = []
    llm_calls: list[tuple[str, int, int, int, int, int, str]] = []
    tool_starts: dict[str, tuple[str, dict[str, Any], int]] = {}
    timestamped_tool_starts: set[str] = set()
    measured_durations_ms: dict[str, int] = {}
    tool_transport_success: dict[str, bool] = {}

    fallback_ns = int(events_jsonl.stat().st_mtime * 1_000_000_000)
    try:
        with events_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = str(ev.get("type") or "")
                data = ev.get("data") or {}
                ev_id = str(ev.get("id") or f"line-{line_no}")
                ev_ts_ns = parse_iso_to_ns(ev.get("timestamp") or data.get("startTime"), fallback_ns)
                if start_ns is None or ev_ts_ns < start_ns:
                    start_ns = ev_ts_ns
                if end_ns is None or ev_ts_ns > end_ns:
                    end_ns = ev_ts_ns

                if ev_type == "session.start":
                    selected_model = str(data.get("selectedModel") or selected_model)
                    ctx = data.get("context") or {}
                    cwd = str(ctx.get("cwd") or cwd)
                    for m_goal in _HANDOFF_GOAL_REF_RE.finditer(cwd + "/"):
                        _record_goal_ref(m_goal.group(1))
                elif ev_type == "user.message":
                    content = str(data.get("content") or "").strip()
                    if content and data.get("source") != "memory":
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(content):
                            _record_goal_ref(m_goal.group(1))
                        _record_file_mentions(content)
                        context_match = _SKILL_CONTEXT_RE.fullmatch(content)
                        if context_match:
                            skill_invocations.append(
                                (context_match.group(1), ev_ts_ns, f"copilot:skill-context:{ev_id}")
                            )
                        elif not first_prompt:
                            # Strip leading <skill-context> wrapper if embedded in user turn
                            cleaned_prompt = re.sub(
                                r"<skill-context\b[^>]*>[\s\S]*?</skill-context>",
                                "",
                                content,
                            )
                            cleaned_prompt = re.sub(
                                r"<system_reminder>[\s\S]*?</system_reminder>",
                                "",
                                cleaned_prompt,
                            )
                            cleaned_prompt = re.sub(
                                r"<current_datetime>[\s\S]*?</current_datetime>",
                                "",
                                cleaned_prompt,
                            ).strip()
                            first_prompt = (cleaned_prompt or content)[:500]
                elif ev_type == "assistant.message":
                    a_content = str(data.get("content") or "").strip()
                    if a_content:
                        last_assistant_excerpt = a_content[:400]
                elif ev_type == "skill.invoked":
                    sk_name = str(data.get("name") or "").strip()
                    sk_path = str(data.get("path") or "").strip()
                    sk_content = str(data.get("content") or "")
                    if sk_name:
                        skill_invocations.append((sk_name, ev_ts_ns, f"copilot:skill.invoked:{ev_id}"))
                    if sk_path:
                        content_bytes = len(sk_content.encode("utf-8")) if sk_content else None
                        if content_bytes is None:
                            content_bytes, _ = _file_stats(sk_path, skills_root=skills_root)
                        ref_reads.append((sk_path, content_bytes, ev_ts_ns, f"copilot:skill.invoked:{ev_id}"))
                elif ev_type == "tool.execution_start":
                    tc_id = str(data.get("toolCallId") or "")
                    t_name = str(data.get("toolName") or "")
                    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
                    if tc_id:
                        tool_starts[tc_id] = (t_name, args, ev_ts_ns)
                        if ev.get("timestamp"):
                            timestamped_tool_starts.add(tc_id)
                    if t_name == "skill" and args.get("skill"):
                        skill_invocations.append((str(args["skill"]), ev_ts_ns, f"copilot:tool:{tc_id or ev_id}"))
                    elif t_name in ("create", "edit", "apply_patch"):
                        arg_path = str(args.get("path") or args.get("patch") or "")
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(arg_path):
                            _record_goal_ref(m_goal.group(1))
                        _record_file_mentions(arg_path, is_write=True)
                    elif t_name == "bash" and args.get("command"):
                        cmd_text = str(args["command"])
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(cmd_text):
                            _record_goal_ref(m_goal.group(1))
                        is_bash_write = any(
                            op in cmd_text for op in (">", "tee ", "write_text", "cp ", "mv ", "cat <<")
                        )
                        _record_file_mentions(cmd_text, is_write=is_bash_write)
                    elif t_name == "view" and args.get("path"):
                        vp = str(args["path"])
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(vp):
                            _record_goal_ref(m_goal.group(1))
                        _record_file_mentions(vp)
                        if (vp.startswith("skills/") or "/skills/" in vp) and vp.endswith((".md", ".json", ".yaml", ".yml")):
                            b_len, _ = _file_stats(vp, skills_root=skills_root)
                            ref_reads.append((vp, b_len, ev_ts_ns, f"copilot:view:{tc_id or ev_id}"))
                elif ev_type == "tool.execution_complete":
                    tc_id = str(data.get("toolCallId") or "")
                    res_obj = data.get("result")
                    res_text = str(res_obj.get("content") or "") if isinstance(res_obj, dict) else ""
                    if tc_id in tool_starts:
                        t_name, args, st_ns = tool_starts[tc_id]
                        dur_ms = max(1, int((ev_ts_ns - st_ns) / 1_000_000))
                        has_completion_timestamp = bool(ev.get("timestamp")) and tc_id in timestamped_tool_starts
                        if t_name in ("view", "skill") and has_completion_timestamp:
                            source_id = f"copilot:{'view' if t_name == 'view' else 'tool'}:{tc_id}"
                            measured_durations_ms[source_id] = dur_ms
                        if t_name == "bash":
                            cmd_str = str(args.get("command") or "")
                            m_sc = _SCRIPT_RE.search(cmd_str)
                            if m_sc:
                                sp_path = m_sc.group(1)
                                ec = _extract_process_exit_code(data, res_text)
                                m_bg = _SHELL_START_RE.search(res_text)
                                if ec is None and m_bg:
                                    pending_async_shells[m_bg.group(1)] = (sp_path, cmd_str[:260], st_ns, tc_id)
                                else:
                                    source_id = f"copilot:bash:{tc_id}"
                                    script_execs.append((sp_path, cmd_str[:260], st_ns, dur_ms, ec, source_id))
                                    if has_completion_timestamp:
                                        measured_durations_ms[source_id] = dur_ms
                                    if isinstance(data.get("success"), bool):
                                        tool_transport_success[source_id] = data["success"]
                        elif t_name in ("read_bash", "stop_bash"):
                            sh_id = str(args.get("shellId") or "")
                            m_done = _SHELL_COMPLETE_RE.search(res_text)
                            m_stop = _SHELL_STOPPED_RE.search(res_text)
                            target_sh = m_done.group(1) if m_done else (m_stop.group(1) if m_stop else sh_id)
                            if target_sh and target_sh in pending_async_shells:
                                if m_done:
                                    sp_path, cmd_args, bg_start_ns, orig_tc = pending_async_shells.pop(target_sh)
                                    bg_dur = max(1, int((ev_ts_ns - bg_start_ns) / 1_000_000))
                                    if ev.get("timestamp") and orig_tc in timestamped_tool_starts:
                                        measured_durations_ms[f"copilot:async_bash:{orig_tc}"] = bg_dur
                                    script_execs.append(
                                        (
                                            sp_path,
                                            cmd_args,
                                            bg_start_ns,
                                            bg_dur,
                                            int(m_done.group(2)),
                                            f"copilot:async_bash:{orig_tc}",
                                        )
                                    )
                                elif m_stop:
                                    sp_path, cmd_args, bg_start_ns, orig_tc = pending_async_shells.pop(target_sh)
                                    bg_dur = max(1, int((ev_ts_ns - bg_start_ns) / 1_000_000))
                                    if ev.get("timestamp") and orig_tc in timestamped_tool_starts:
                                        measured_durations_ms[f"copilot:stopped_bash:{orig_tc}"] = bg_dur
                                    script_execs.append(
                                        (
                                            sp_path,
                                            cmd_args,
                                            bg_start_ns,
                                            bg_dur,
                                            None,
                                            f"copilot:stopped_bash:{orig_tc}",
                                        )
                                    )
                elif ev_type == "session.usage_record":
                    usage = data.get("usage") or {}
                    u_model = str(usage.get("model") or selected_model)
                    u_in = int(usage.get("inputTokens") or 0)
                    u_out = int(usage.get("outputTokens") or 0)
                    u_cache = int(usage.get("cacheReadTokens") or 0)
                    u_dur = max(1, int(usage.get("duration") or 1))
                    if u_in > 0 or u_out > 0 or u_cache > 0:
                        call_start = max(start_ns or ev_ts_ns, ev_ts_ns - u_dur * 1_000_000)
                        source_id = f"copilot:usage:{ev_id}"
                        llm_calls.append((u_model, call_start, u_dur, u_in, u_out, u_cache, source_id))
                        if usage.get("duration") is not None:
                            measured_durations_ms[source_id] = u_dur
    except OSError:
        return None

    # Any async bash command that never completed stays exit_code=None (unknown)
    for sh_id, (sp_path, cmd_args, bg_start_ns, orig_tc) in pending_async_shells.items():
        bg_dur = max(1, int(((end_ns or fallback_ns) - bg_start_ns) / 1_000_000))
        script_execs.append((sp_path, cmd_args, bg_start_ns, bg_dur, None, f"copilot:pending_bash:{orig_tc}"))

    # Enrich with usage.jsonl and warehouse.db records for this exact session_id
    u_bucket = usage_by_session.get(session_id, {})
    had_copilot_llm_calls = bool(llm_calls)
    for idx, rec in enumerate(u_bucket.get("skill_turn", [])):
        sk = str(rec.get("skill") or "").strip()
        ts_ns = parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns)
        src_id = str(rec.get("event_id") or f"usage:skill_turn:{idx}")
        if sk:
            skill_invocations.append((sk, ts_ns, src_id))
        if not had_copilot_llm_calls:
            in_t = int(rec.get("input_tokens") or 0)
            out_t = int(rec.get("output_tokens") or 0)
            ca_t = int(rec.get("cache_read") or 0)
            if in_t > 0 or out_t > 0 or ca_t > 0:
                llm_calls.append((str(rec.get("model") or selected_model), ts_ns, 1, in_t, out_t, ca_t, src_id))

    for idx, rec in enumerate(u_bucket.get("reference_hit", []) + wh_maps["refs_by_session"].get(session_id, [])):
        rp = str(rec.get("reference_path") or "").strip()
        if rp:
            raw_b = rec.get("byte_len")
            b_len = int(raw_b) if raw_b is not None and int(raw_b) > 0 else None
            if b_len is None:
                b_len, _ = _file_stats(rp, skills_root=skills_root)
            src_id = str(rec.get("hit_uid") or rec.get("event_id") or f"ref:{idx}")
            ref_reads.append((rp, b_len, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns), src_id))

    for idx, rec in enumerate(u_bucket.get("script_call", []) + wh_maps["scripts_by_session"].get(session_id, [])):
        sp_p = str(rec.get("script_path") or "").strip()
        if sp_p:
            dur_ms = max(1, int(float(rec.get("duration_ms") or 1)))
            raw_ec = rec.get("exit_code")
            ec = int(raw_ec) if raw_ec is not None else None
            src_id = str(rec.get("call_uid") or rec.get("event_id") or f"script:{idx}")
            if rec.get("duration_ms") is not None:
                measured_durations_ms[src_id] = dur_ms
            script_execs.append(
                (sp_p, sp_p, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns), dur_ms, ec, src_id)
            )

    if not llm_calls:
        for idx, turn in enumerate(wh_maps["turns_by_session"].get(session_id, [])):
            in_t = int(turn.get("input_tokens") or 0)
            out_t = int(turn.get("output_tokens") or 0)
            ca_t = int(turn.get("cache_read") or 0)
            if in_t > 0 or out_t > 0 or ca_t > 0:
                src_id = str(turn.get("turn_uuid") or f"wh_turn:{idx}")
                llm_calls.append(
                    (
                        str(turn.get("model") or selected_model),
                        parse_iso_to_ns(turn.get("ts"), start_ns or fallback_ns),
                        1,
                        in_t,
                        out_t,
                        ca_t,
                        src_id,
                    )
                )

    if not (first_prompt or llm_calls or skill_invocations or ref_reads or script_execs):
        return None

    s_ns = start_ns or fallback_ns
    e_ns = end_ns or fallback_ns
    s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000)

    primary_goal_id = _resolve_primary_goal_id(
        cwd=cwd,
        first_prompt=first_prompt,
        ordered_goals=ordered_goals,
        known_goals=known_goals,
    )
    if primary_goal_id:
        referenced_goals.add(primary_goal_id)

    return ParsedSessionActivity(
        session_id=session_id,
        source_path=events_jsonl.name,
        selected_model=selected_model,
        cwd=cwd,
        first_prompt=first_prompt,
        start_ns=s_ns,
        end_ns=e_ns,
        referenced_goals=referenced_goals,
        skill_invocations=skill_invocations,
        ref_reads=ref_reads,
        script_execs=script_execs,
        llm_calls=llm_calls,
        measured_durations_ms=measured_durations_ms,
        tool_transport_success=tool_transport_success,
        primary_goal_id=primary_goal_id,
        receipt_writes=receipt_writes,
        receipt_mentions=receipt_mentions,
        brief_mentions=brief_mentions,
        last_assistant_excerpt=last_assistant_excerpt,
    )


def _parse_ledger_markdown(ledger_path: Path) -> dict[str, Any]:
    text = ledger_path.read_text(encoding="utf-8", errors="replace")
    goal_id = ledger_path.parent.name
    mandate_line = ""
    owner_line = ""
    linear_id = ""
    repo_name = ""
    events: list[dict[str, str]] = []
    tasks: list[dict[str, str]] = []

    m_lin = _LINEAR_ISSUE_RE.search(text)
    if m_lin:
        linear_id = m_lin.group(1)
    m_repo = _REPO_RE.search(text)
    if m_repo:
        repo_name = m_repo.group(1) or m_repo.group(0)

    in_tasks = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 目标:") or stripped.startswith("- Mandate:"):
            mandate_line = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- Owner:"):
            owner_line = stripped
        elif stripped.startswith("## Tasks"):
            in_tasks = True
            continue
        elif stripped.startswith("## "):
            in_tasks = False
        elif in_tasks and stripped.startswith("|") and not stripped.startswith("|---") and "ID" not in stripped[:12]:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cols) >= 5:
                tasks.append(
                    {
                        "task_id": cols[0],
                        "title": cols[2],
                        "assignee": cols[3],
                        "status": cols[4],
                        "pr": cols[5] if len(cols) > 5 else "",
                    }
                )
        m_evt = _EVT_LINE_RE.match(stripped)
        if m_evt:
            ts_str, rest = m_evt.group(1), m_evt.group(2)
            fields: dict[str, str] = {"ts": ts_str}
            for chunk in rest.split("|"):
                chunk = chunk.strip()
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    fields[k.strip()] = v.strip()
            events.append(fields)

    return {
        "goal_id": goal_id,
        "mandate": mandate_line or f"Goal {goal_id}",
        "owner_line": owner_line,
        "linear_id": linear_id,
        "repo": repo_name or "unknown",
        "tasks": tasks,
        "events": events,
        "raw_text": text,
    }


def build_goal_trace(
    goal_dir: Path,
    wh_maps: dict[str, Any],
    linked_sessions: list[ParsedSessionActivity] | None = None,
    skills_root: Path | None = None,
) -> TraceRecord | None:
    """Reconstruct a Goal trace strictly from real recorded evidence (no fabricated spans/metrics)."""
    ledger_path = goal_dir / "ledger.md"
    if not ledger_path.is_file():
        return None
    parsed = _parse_ledger_markdown(ledger_path)
    goal_id = parsed["goal_id"]
    linked_sessions = linked_sessions or []

    spec_path = goal_dir / "spec.md"
    spec_excerpt = ""
    if spec_path.is_file():
        try:
            spec_excerpt = spec_path.read_text(encoding="utf-8", errors="replace")[:500].strip()
        except OSError:
            spec_excerpt = ""

    wh_goal = wh_maps["goals"].get(goal_id, {})
    wh_events = wh_maps["goal_events"].get(goal_id, [])
    wh_runs = wh_maps["goal_runs"].get(goal_id, [])

    fallback_ns = int(ledger_path.stat().st_mtime * 1_000_000_000)
    evt_timestamps = [parse_iso_to_ns(e.get("ts"), fallback_ns) for e in parsed["events"] if e.get("ts")]
    if wh_goal.get("created_at"):
        evt_timestamps.append(parse_iso_to_ns(wh_goal["created_at"], fallback_ns))
    if wh_goal.get("last_event_at"):
        evt_timestamps.append(parse_iso_to_ns(wh_goal["last_event_at"], fallback_ns))
    for sess in linked_sessions:
        evt_timestamps.append(sess.start_ns)
        evt_timestamps.append(sess.end_ns)

    start_ns = min(evt_timestamps) if evt_timestamps else fallback_ns
    end_ns = max(evt_timestamps) if evt_timestamps else fallback_ns
    start_ns, end_ns = _clamp_span_window(start_ns, end_ns, min_duration_ns=1_000_000)

    evt_types = {e.get("EVT", "") for e in parsed["events"]} | {e.get("evt_type", "") for e in wh_events}
    if "GOAL_DONE" in evt_types or "PR_MERGED" in evt_types or wh_goal.get("derived_actual_status") == "done":
        status = "done"
    elif "TASK_BLOCKED" in evt_types or wh_goal.get("derived_actual_status") == "blocked":
        status = "blocked"
    elif parsed["tasks"] and all(t["status"].lower() == "done" for t in parsed["tasks"]):
        status = "done"
    else:
        status = str(wh_goal.get("derived_actual_status") or "in_progress")

    trace_id = deterministic_trace_id(f"goal:{goal_id}")
    root_span_id = deterministic_span_id(f"goal:{goal_id}:root")

    # Read ONLY receipts (never briefs!) for executed work evidence when session logs are absent
    receipt_files = sorted((goal_dir / "receipts").glob("*.md")) if (goal_dir / "receipts").is_dir() else []
    receipt_texts: list[tuple[Path, str]] = []
    for rf in receipt_files:
        try:
            receipt_texts.append((rf, rf.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue

    duration_ms = round((end_ns - start_ns) / 1_000_000, 2)
    spans: list[SpanRecord] = []

    # Separate real role receipts vs non-role markdown artifacts (e.g., matrices/inventories)
    non_role_artifacts: list[dict[str, Any]] = []
    goal_r10_excerpt = ""
    role_receipt_records: list[dict[str, Any]] = []
    for rf, r_text in receipt_texts:
        r_role = _classify_receipt_role(rf.stem)
        excerpt = _extract_receipt_excerpt(r_text, max_chars=600)
        verdict = _extract_receipt_verdict(r_text)
        if r_role is None:
            non_role_artifacts.append(
                {
                    "filename": rf.name,
                    "verdict": verdict,
                    "excerpt": excerpt[:320],
                }
            )
            continue
        if r_role == "M1 TL" and rf.stem.lower().startswith(("goal-r10", "goal_done", "r10-final-goal", "r10-goal")):
            goal_r10_excerpt = excerpt
            continue
        m_rnd = re.search(r"r(\d+)", rf.stem, re.I)
        r_round = f"r{m_rnd.group(1)}" if m_rnd else "r1"
        rf_ns = int(rf.stat().st_mtime * 1_000_000_000)
        role_receipt_records.append(
            {
                "path": rf,
                "filename": rf.name,
                "stem": rf.stem,
                "role": r_role,
                "round": r_round,
                "verdict": verdict,
                "excerpt": excerpt,
                "mtime_ns": rf_ns,
            }
        )

    # 1. Stage / Role Spans from real warehouse runs, receipts, or ledger events
    m1_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M1-TL")
    tl_model = str(wh_goal.get("tl_model") or (linked_sessions[0].selected_model if linked_sessions else "unknown"))
    m1_output: dict[str, Any] = {"status": status}
    if goal_r10_excerpt:
        m1_output["receipt_excerpt"] = goal_r10_excerpt
    if non_role_artifacts:
        m1_output["artifacts"] = non_role_artifacts
    m1_stage_span = SpanRecord(
        span_id=m1_span_id,
        parent_span_id=root_span_id,
        category="stage_role",
        name=f"Stage / Role: M1 TL ({wh_goal.get('tl_agent') or f'{goal_id}-tl'})",
        start_ns=start_ns,
        end_ns=end_ns,
        status_ok=None if status == "in_progress" else (status != "blocked"),
        attributes={
            "stage.role": "M1 TL",
            "stage.chain": "M2 -> M1 TL",
            "stage.round": "r1",
            "stage.model": tl_model,
            "stage.verdict": status.upper(),
            "evidence.source": "ledger.md",
        },
        input_payload={"tasks": [t["task_id"] for t in parsed["tasks"]]},
        output_payload=m1_output,
    )
    spans.append(m1_stage_span)

    if wh_goal.get("coordinator_bot") or "coordinator" in parsed["owner_line"].lower() or "协调者" in parsed["owner_line"]:
        m2_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M2")
        spans.append(
            SpanRecord(
                span_id=m2_span_id,
                parent_span_id=root_span_id,
                category="stage_role",
                name=f"Stage / Role: M2 ({wh_goal.get('coordinator_bot') or 'coordinator'})",
                start_ns=start_ns,
                end_ns=start_ns + 1_000_000,
                status_ok=True,
                attributes={
                    "stage.role": "M2",
                    "stage.chain": "M3 -> M2",
                    "stage.round": "r1",
                    "evidence.source": "ledger.md:header",
                    **_point_event_timing(),
                },
                input_payload={"mandate": parsed["mandate"][:240]},
                output_payload={"coordinator": wh_goal.get("coordinator_bot") or "recorded", **_point_event_timing()},
            )
        )

    primary_worker_span_id = m1_span_id
    role_stage_spans: list[tuple[SpanRecord, dict[str, Any]]] = []
    receipt_excerpt_by_name = {
        rec["filename"]: rec["excerpt"] for rec in role_receipt_records
    }
    if wh_runs:
        for idx, run in enumerate(wh_runs):
            r_uid = str(run.get("receipt_uid") or "")
            uid_stem = Path(r_uid).stem if r_uid else ""
            r_role = _classify_receipt_role(str(run.get("role_name") or "")) or _classify_receipt_role(uid_stem)
            if r_role is None:
                continue
            r_round = f"r{run.get('round_no') or 1}"
            r_model = str(run.get("model_id") or "unknown")
            r_verdict = str(run.get("v5_verdict") or run.get("status") or "UNKNOWN")
            r_task = str(run.get("task_id") or "task")
            r_uid_key = r_uid or f"{idx}:{r_task}:{r_role}:{r_round}"
            rcpt_filename = Path(r_uid).name if r_uid.endswith(".md") else ""
            rcpt_excerpt = receipt_excerpt_by_name.get(rcpt_filename, "")
            w_span_id = deterministic_span_id(f"goal:{goal_id}:wh_run:{r_uid_key}")
            w_start = parse_iso_to_ns(run.get("started_at") or run.get("completed_at"), end_ns)
            run_measured = bool(run.get("started_at") and run.get("completed_at"))
            w_end = parse_iso_to_ns(run["completed_at"], w_start) if run_measured else w_start + 1_000_000
            w_start, w_end = _clamp_span_window(w_start, w_end)
            run_timing = (
                {"timing.measured": True, "timing.provenance": "measured"}
                if run_measured
                else _point_event_timing()
            )
            if primary_worker_span_id == m1_span_id:
                primary_worker_span_id = w_span_id
            v_up = r_verdict.upper()
            v_ok: bool | None = False if "FAIL" in v_up else (True if v_up in ("PASS", "DONE", "COMPLETED") else None)
            out_dict: dict[str, Any] = {"verdict": r_verdict[:80], **run_timing}
            if rcpt_excerpt:
                out_dict["receipt_excerpt"] = rcpt_excerpt
            sp_rec = SpanRecord(
                span_id=w_span_id,
                parent_span_id=m1_span_id,
                category="stage_role",
                name=f"Stage / Role: {r_role} ({r_task} {r_round})",
                start_ns=w_start,
                end_ns=w_end,
                status_ok=v_ok,
                attributes={
                    "stage.role": r_role,
                    "stage.task_id": r_task,
                    "stage.round": r_round,
                    "stage.model": r_model[:80],
                    "stage.verdict": r_verdict[:40],
                    "evidence.source": f"warehouse.fact_gtt_task_run:{r_uid_key}",
                    **run_timing,
                },
                input_payload={"task_id": r_task, "role": r_role, "round": r_round},
                output_payload=out_dict,
            )
            spans.append(sp_rec)
            role_stage_spans.append(
                (
                    sp_rec,
                    {
                        "role": r_role,
                        "filename": rcpt_filename,
                        "stem": uid_stem or f"{r_task} {r_round}",
                        "mtime_ns": w_end,
                        "matched": False,
                    },
                )
            )
    else:
        for rec in role_receipt_records:
            r_role = rec["role"]
            r_round = rec["round"]
            r_verdict = rec["verdict"]
            v_ok = False if r_verdict == "FAIL" else (True if r_verdict == "PASS" else None)
            rf_ns = rec["mtime_ns"]
            w_start, w_end = rf_ns, rf_ns + 1_000_000
            w_span_id = deterministic_span_id(f"goal:{goal_id}:receipt_role:{rec['filename']}")
            if primary_worker_span_id == m1_span_id:
                primary_worker_span_id = w_span_id
            out_dict = {"verdict": r_verdict, **_point_event_timing()}
            if rec["excerpt"]:
                out_dict["receipt_excerpt"] = rec["excerpt"]
            sp_rec = SpanRecord(
                span_id=w_span_id,
                parent_span_id=m1_span_id,
                category="stage_role",
                name=f"Stage / Role: {r_role} ({rec['stem']})",
                start_ns=w_start,
                end_ns=w_end,
                status_ok=v_ok,
                attributes={
                    "stage.role": r_role,
                    "stage.round": r_round,
                    "stage.verdict": r_verdict,
                    "stage.receipt": rec["filename"],
                    "evidence.source": f"receipts/{rec['filename']}:mtime",
                    **_point_event_timing(),
                },
                input_payload={"receipt": rec["filename"]},
                output_payload=out_dict,
            )
            spans.append(sp_rec)
            role_stage_spans.append(
                (
                    sp_rec,
                    {
                        "role": r_role,
                        "filename": rec["filename"],
                        "stem": rec["stem"],
                        "mtime_ns": rf_ns,
                        "matched": False,
                    },
                )
            )

    def _match_session_to_stage(sess: ParsedSessionActivity) -> SpanRecord | None:
        s_role = _infer_session_role(sess)
        # 1. M1 Steering TL session hangs directly under the Goal's M1 TL stage span
        if s_role == "M1 TL":
            return m1_stage_span
        # 2. Exact receipt write match where receipt role aligns with session role
        for sp_cand, meta in role_stage_spans:
            if meta["matched"] or not meta["filename"]:
                continue
            if meta["filename"] in sess.receipt_writes and meta["role"] == s_role:
                meta["matched"] = True
                return sp_cand
        # 3. Receipt filename mentioned in session prompt/events with matching role
        for sp_cand, meta in role_stage_spans:
            if meta["matched"] or not meta["filename"]:
                continue
            if meta["filename"] in sess.receipt_mentions and meta["role"] == s_role:
                meta["matched"] = True
                return sp_cand
        # 4. Role + closest timestamp window match among unmatched stage_role spans
        same_role_candidates = [
            (sp_cand, meta)
            for sp_cand, meta in role_stage_spans
            if not meta["matched"] and meta["role"] == s_role
        ]
        if same_role_candidates:
            best_sp, best_meta = min(
                same_role_candidates,
                key=lambda item: abs(int(item[1]["mtime_ns"]) - sess.end_ns),
            )
            best_meta["matched"] = True
            return best_sp
        return None

    # 2–5. Evidence-backed Child Spans from linked real sessions AND explicit receipt evidence
    seen_skills: set[str] = set()
    first_skill_span_id: str | None = None
    seen_refs: set[tuple[str, str]] = set()
    script_count = 0
    llm_count = 0
    total_in_tokens = 0
    total_out_tokens = 0
    total_cache_tokens = 0

    for sess in linked_sessions:
        session_span_start = len(spans)
        matched_stage = _match_session_to_stage(sess)
        s_role = _infer_session_role(sess)
        if matched_stage is not None:
            sess_stage_id = matched_stage.span_id
            if matched_stage is not m1_stage_span:
                matched_stage.start_ns, matched_stage.end_ns = _clamp_span_window(sess.start_ns, sess.end_ns)
                base_label = matched_stage.attributes.get("stage.receipt")
                stem_label = Path(str(base_label)).stem if base_label else matched_stage.name.split("(", 1)[-1].rstrip(")")
                matched_stage.name = (
                    f"Stage / Role: {matched_stage.attributes.get('stage.role', s_role)} "
                    f"({stem_label} · {sess.session_id[:8]})"
                )
                matched_stage.attributes["session.id"] = sess.session_id
                matched_stage.attributes["stage.model"] = sess.selected_model
                matched_stage.attributes["timing.measured"] = True
                matched_stage.attributes["timing.provenance"] = "measured"
                if isinstance(matched_stage.input_payload, dict):
                    matched_stage.input_payload["session_id"] = sess.session_id
                    matched_stage.input_payload["prompt"] = sess.first_prompt[:200]
                if isinstance(matched_stage.output_payload, dict):
                    matched_stage.output_payload["llm_generations"] = len(sess.llm_calls)
                    matched_stage.output_payload["timing.measured"] = True
                    matched_stage.output_payload["timing.provenance"] = "measured"
            else:
                matched_stage.attributes["session.id"] = sess.session_id
                if isinstance(matched_stage.output_payload, dict):
                    matched_stage.output_payload["tl_session_id"] = sess.session_id
                    matched_stage.output_payload["tl_llm_generations"] = len(sess.llm_calls)
        else:
            sess_stage_id = deterministic_span_id(f"goal:{goal_id}:session_stage:{sess.session_id}")
            spans.append(
                SpanRecord(
                    span_id=sess_stage_id,
                    parent_span_id=m1_span_id,
                    category="stage_role",
                    name=f"Stage / Role: {s_role} ({sess.session_id[:8]})",
                    start_ns=sess.start_ns,
                    end_ns=sess.end_ns,
                    status_ok=True,
                    attributes={
                        "stage.role": s_role,
                        "stage.model": sess.selected_model,
                        "session.id": sess.session_id,
                        "timing.measured": True,
                        "timing.provenance": "measured",
                        "evidence.source": f"session-state/{sess.session_id}/events.jsonl",
                    },
                    input_payload={"session_id": sess.session_id, "role": s_role, "prompt": sess.first_prompt[:200]},
                    output_payload={"llm_generations": len(sess.llm_calls), "timing.measured": True, "timing.provenance": "measured"},
                )
            )

        for sk_name, sk_ts, src_id in sess.skill_invocations:
            if sk_name in seen_skills:
                continue
            seen_skills.add(sk_name)
            sk_span_id = deterministic_span_id(f"goal:{goal_id}:skill:{sess.session_id}:{sk_name}")
            if first_skill_span_id is None:
                first_skill_span_id = sk_span_id
            spans.append(
                SpanRecord(
                    span_id=sk_span_id,
                    parent_span_id=sess_stage_id,
                    category="skill_load",
                    name=f"Skill Load: {sk_name}",
                    start_ns=sk_ts,
                    end_ns=sk_ts + 1_000_000,
                    status_ok=True,
                    attributes={
                        "skill.name": sk_name,
                        "session.id": sess.session_id,
                        "evidence.source": src_id,
                    },
                    input_payload={"skill": sk_name},
                    output_payload={"loaded": True},
                )
            )

        for rp, b_len, r_ts, src_id in sess.ref_reads:
            dedup_key = (sess.session_id, rp)
            if dedup_key in seen_refs:
                continue
            seen_refs.add(dedup_key)
            f_bytes, line_cnt = _file_stats(rp, skills_root=skills_root)
            actual_bytes = b_len if b_len is not None else f_bytes
            attrs: dict[str, str | int | float | bool] = {
                "reference.path": rp,
                "reference.skill": _infer_skill_from_path(rp),
                "session.id": sess.session_id,
                "evidence.source": src_id,
            }
            out_pay: dict[str, Any] = {"path": rp}
            if actual_bytes is not None:
                attrs["reference.byte_len"] = actual_bytes
                attrs["reference.estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
                out_pay["byte_len"] = actual_bytes
                out_pay["estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
            if line_cnt is not None:
                attrs["reference.line_count"] = line_cnt
                out_pay["lines"] = line_cnt
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:ref:{sess.session_id}:{src_id}:{rp}"),
                    parent_span_id=sess_stage_id,
                    category="l1_reference_read",
                    name=f"L1 Reference Read: {Path(rp).name}",
                    start_ns=r_ts,
                    end_ns=r_ts + 1_000_000,
                    status_ok=True,
                    attributes=attrs,
                    input_payload={"path": rp},
                    output_payload=out_pay,
                )
            )

        for idx_sc, (sp_p, cmd_args, sc_ts, dur_ms, ec, src_id) in enumerate(sess.script_execs):
            script_count += 1
            sc_ok: bool | None = None if ec is None else (ec == 0)
            sc_attrs: dict[str, str | int | float | bool] = {
                "script.path": sp_p,
                "script.skill": _infer_skill_from_path(sp_p),
                "script.args": cmd_args,
                "script.duration_ms": dur_ms,
                "session.id": sess.session_id,
                "evidence.source": src_id,
            }
            sc_out: dict[str, Any] = {"duration_ms": dur_ms}
            if ec is not None:
                sc_attrs["script.exit_code"] = ec
                sc_out["exit_code"] = ec
            else:
                sc_attrs["script.exit_status"] = "unknown"
                sc_out["exit_code"] = None
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:script:{sess.session_id}:{idx_sc}:{src_id}"),
                    parent_span_id=sess_stage_id,
                    category="l2_script_exec",
                    name=f"L2 Script Exec: {Path(sp_p).name}",
                    start_ns=sc_ts,
                    end_ns=sc_ts + max(1, dur_ms) * 1_000_000,
                    status_ok=sc_ok,
                    attributes=sc_attrs,
                    input_payload={"script": sp_p, "command": cmd_args},
                    output_payload=sc_out,
                )
            )

        for idx_llm, (m_name, l_ts, dur_ms, in_t, out_t, ca_t, src_id) in enumerate(sess.llm_calls):
            llm_count += 1
            total_in_tokens += in_t
            total_out_tokens += out_t
            total_cache_tokens += ca_t
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:llm:{sess.session_id}:{idx_llm}:{src_id}"),
                    parent_span_id=sess_stage_id,
                    category="llm_generation",
                    name=f"LLM Generation: {m_name}",
                    start_ns=l_ts,
                    end_ns=l_ts + max(1, dur_ms) * 1_000_000,
                    status_ok=True,
                    attributes={
                        "llm.model_name": m_name,
                        "llm.input_tokens": in_t,
                        "llm.output_tokens": out_t,
                        "llm.cache_read": ca_t,
                        "session.id": sess.session_id,
                        "evidence.source": src_id,
                    },
                    input_payload={"session_id": sess.session_id, "turn_index": idx_llm},
                    output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t},
                    model=m_name,
                    input_tokens=in_t,
                    output_tokens=out_t,
                    cache_read=ca_t,
                )
            )

        _apply_activity_evidence(spans[session_span_start:], sess)

    # Supplement ONLY from structured receipt token headers (never prose mentions).
    if not linked_sessions and receipt_texts:
        for rf, r_text in receipt_texts:
            rf_ns = int(rf.stat().st_mtime * 1_000_000_000)
            token_header = _RECEIPT_TOKENS_RE.search(r_text)
            if token_header is None:
                continue
            token_fields = token_header.group(1)
            m_in = _TOKEN_NUM_RE.search(token_fields)
            m_out = _OUT_TOKEN_RE.search(token_fields)
            m_ca = _CACHE_TOKEN_RE.search(token_fields)
            if m_in or m_out:
                in_t = int(m_in.group(1)) if m_in else 0
                out_t = int(m_out.group(1)) if m_out else 0
                ca_t = int(m_ca.group(1)) if m_ca else 0
                llm_count += 1
                total_in_tokens += in_t
                total_out_tokens += out_t
                total_cache_tokens += ca_t
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"goal:{goal_id}:rcpt_llm:{rf.name}"),
                        parent_span_id=primary_worker_span_id,
                        category="llm_generation",
                        name=f"LLM Generation: {tl_model}",
                        start_ns=rf_ns,
                        end_ns=rf_ns + 1_000_000,
                        status_ok=True,
                        attributes={
                            "llm.model_name": tl_model,
                            "llm.input_tokens": in_t,
                            "llm.output_tokens": out_t,
                            "llm.cache_read": ca_t,
                            "evidence.source": f"receipts/{rf.name}:Tokens",
                            **_point_event_timing(),
                        },
                        input_payload={"receipt": rf.name},
                        output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t,
                                        **_point_event_timing()},
                        model=tl_model,
                        input_tokens=in_t,
                        output_tokens=out_t,
                        cache_read=ca_t,
                    )
                )

    present_categories = sorted({sp.category for sp in spans if sp.category != "root"})
    missing_categories = [c for c in SPAN_CATEGORIES if c not in present_categories]

    root_input = {
        "goal_id": goal_id,
        "linear_issue": parsed["linear_id"] or "N/A",
        "repo": parsed["repo"],
        "mandate": parsed["mandate"][:400],
        "spec_summary": spec_excerpt[:300] if spec_excerpt else parsed["mandate"][:300],
    }
    root_output = {
        "status": status,
        "duration_ms": duration_ms,
        "task_count": len(parsed["tasks"]),
        "event_count": max(len(parsed["events"]), len(wh_events)),
        "receipt_count": len(receipt_files),
        "linked_sessions": len(linked_sessions),
        "llm_generations": llm_count,
        "total_input_tokens": total_in_tokens,
        "total_output_tokens": total_out_tokens,
        "total_cache_read": total_cache_tokens,
        "present_categories": present_categories,
        "missing_categories": missing_categories,
    }

    root_span = SpanRecord(
        span_id=root_span_id,
        parent_span_id=None,
        category="root",
        name=f"Goal: {goal_id}",
        start_ns=start_ns,
        end_ns=end_ns,
        status_ok=None if status == "in_progress" else (status != "blocked"),
        attributes={
            "goal.id": goal_id,
            "goal.linear_issue": parsed["linear_id"] or "none",
            "goal.repo": parsed["repo"],
            "goal.status": status,
            "goal.duration_ms": duration_ms,
            "goal.linked_sessions": len(linked_sessions),
            "goal.complete_5_categories": len(missing_categories) == 0,
        },
        input_payload=root_input,
        output_payload=root_output,
    )
    spans.insert(0, root_span)

    tags = ["backfill", "goal", f"status:{status}"]
    if parsed["linear_id"]:
        tags.append(parsed["linear_id"])
    if not missing_categories:
        tags.append("complete-5-cat")

    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root_span_id,
        trace_kind="goal",
        entity_id=goal_id,
        name=f"Goal: {goal_id}",
        start_ns=start_ns,
        end_ns=end_ns,
        status=status,
        input_payload=root_input,
        output_payload=root_output,
        tags=tags,
        metadata={
            "goal_id": goal_id,
            "linear_issue": parsed["linear_id"],
            "repo": parsed["repo"],
            "duration_ms": duration_ms,
            "linked_session_ids": [s.session_id for s in linked_sessions],
            "present_categories": present_categories,
            "missing_categories": missing_categories,
            "span_count": len(spans),
        },
        spans=spans,
    )


def build_session_trace_from_activity(
    activity: ParsedSessionActivity,
    skills_root: Path | None = None,
    handoff_root: Path | None = None,
) -> TraceRecord:
    """Build a complete Session Prompt trace exporting ALL recorded events without truncation."""
    session_id = activity.session_id
    s_ns, e_ns = _clamp_span_window(activity.start_ns, activity.end_ns, min_duration_ns=1_000_000)

    trace_id = deterministic_trace_id(f"session:{session_id}")
    root_span_id = deterministic_span_id(f"session:{session_id}:root")
    stage_span_id = deterministic_span_id(f"session:{session_id}:stage:agent")

    prompt_summary = activity.first_prompt[:320] if activity.first_prompt else f"Session {session_id}"
    inferred_role = _infer_session_role(activity)
    primary_goal_id = activity.primary_goal_id or (
        sorted(activity.referenced_goals)[0] if len(activity.referenced_goals) == 1 else None
    )
    langfuse_session_id = f"goal:{primary_goal_id}" if primary_goal_id else session_id

    receipt_targets = sorted(activity.receipt_writes or activity.receipt_mentions)
    brief_targets = sorted(activity.brief_mentions)
    receipt_excerpt = ""
    receipt_verdict = "COMPLETED"
    if primary_goal_id and handoff_root is not None:
        goal_rcpts_dir = handoff_root / primary_goal_id / "receipts"
        candidate_rcpt_names = sorted(activity.receipt_writes) + sorted(activity.receipt_mentions)
        if goal_rcpts_dir.is_dir():
            for rf_cand in sorted(goal_rcpts_dir.glob("*.md")):
                if _classify_receipt_role(rf_cand.stem) == inferred_role and rf_cand.name not in candidate_rcpt_names:
                    candidate_rcpt_names.append(rf_cand.name)
        for rcpt_name in candidate_rcpt_names:
            cand_role = _classify_receipt_role(Path(rcpt_name).stem)
            if cand_role != inferred_role:
                continue
            rcpt_path = goal_rcpts_dir / rcpt_name
            if rcpt_path.is_file():
                try:
                    r_txt = rcpt_path.read_text(encoding="utf-8", errors="replace")
                    receipt_excerpt = _extract_receipt_excerpt(r_txt, max_chars=600)
                    v_cand = _extract_receipt_verdict(r_txt)
                    if v_cand != "RECORDED":
                        receipt_verdict = v_cand
                    break
                except OSError:
                    continue

    spans: list[SpanRecord] = [
        SpanRecord(
            span_id=stage_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name=f"Stage / Role: {inferred_role} ({activity.selected_model})",
            start_ns=s_ns,
            end_ns=e_ns,
            status_ok=False if receipt_verdict == "FAIL" else True,
            attributes={
                "stage.role": inferred_role,
                "stage.round": "r1",
                "stage.model": activity.selected_model,
                "stage.verdict": receipt_verdict,
                "evidence.source": activity.source_path,
            },
            input_payload={"prompt": prompt_summary[:200], "role": inferred_role},
            output_payload={"llm_calls": len(activity.llm_calls), "verdict": receipt_verdict},
        )
    ]

    seen_skills: set[tuple[str, int]] = set()
    for idx, (sk_name, sk_ts, src_id) in enumerate(activity.skill_invocations):
        key = (sk_name, sk_ts)
        if key in seen_skills:
            continue
        seen_skills.add(key)
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:skill:{idx}:{src_id}:{sk_name}"),
                parent_span_id=stage_span_id,
                category="skill_load",
                name=f"Skill Load: {sk_name}",
                start_ns=sk_ts,
                end_ns=sk_ts + 1_000_000,
                status_ok=True,
                attributes={"skill.name": sk_name, "evidence.source": src_id},
                input_payload={"skill": sk_name},
                output_payload={"loaded": True},
            )
        )

    seen_refs: set[tuple[str, int]] = set()
    for idx, (rp, b_len, r_ts, src_id) in enumerate(activity.ref_reads):
        r_key = (rp, r_ts)
        if r_key in seen_refs:
            continue
        seen_refs.add(r_key)
        f_bytes, line_cnt = _file_stats(rp, skills_root=skills_root)
        actual_bytes = b_len if b_len is not None else f_bytes
        ref_attrs: dict[str, str | int | float | bool] = {
            "reference.path": rp,
            "reference.skill": _infer_skill_from_path(rp),
            "evidence.source": src_id,
        }
        out_pay: dict[str, Any] = {"path": rp}
        if actual_bytes is not None:
            ref_attrs["reference.byte_len"] = actual_bytes
            ref_attrs["reference.estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
            out_pay["byte_len"] = actual_bytes
            out_pay["estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
        if line_cnt is not None:
            ref_attrs["reference.line_count"] = line_cnt
            out_pay["lines"] = line_cnt
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:ref:{idx}:{src_id}:{rp}"),
                parent_span_id=stage_span_id,
                category="l1_reference_read",
                name=f"L1 Reference Read: {Path(rp).name}",
                start_ns=r_ts,
                end_ns=r_ts + 1_000_000,
                status_ok=True,
                attributes=ref_attrs,
                input_payload={"path": rp},
                output_payload=out_pay,
            )
        )

    for idx, (sp_p, cmd_args, sc_ts, dur_ms, ec, src_id) in enumerate(activity.script_execs):
        sc_ok: bool | None = None if ec is None else (ec == 0)
        sc_attrs: dict[str, str | int | float | bool] = {
            "script.path": sp_p,
            "script.skill": _infer_skill_from_path(sp_p),
            "script.args": cmd_args,
            "script.duration_ms": dur_ms,
            "evidence.source": src_id,
        }
        sc_out: dict[str, Any] = {"duration_ms": dur_ms}
        if ec is not None:
            sc_attrs["script.exit_code"] = ec
            sc_out["exit_code"] = ec
        else:
            sc_attrs["script.exit_status"] = "unknown"
            sc_out["exit_code"] = None
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:script:{idx}:{src_id}:{sp_p}"),
                parent_span_id=stage_span_id,
                category="l2_script_exec",
                name=f"L2 Script Exec: {Path(sp_p).name}",
                start_ns=sc_ts,
                end_ns=sc_ts + max(1, dur_ms) * 1_000_000,
                status_ok=sc_ok,
                attributes=sc_attrs,
                input_payload={"script": sp_p, "command": cmd_args},
                output_payload=sc_out,
            )
        )

    # Export EVERY recorded LLM generation (zero per-trace truncation) so root totals reconcile 100%
    exported_in = 0
    exported_out = 0
    exported_cache = 0
    for idx, (m_name, l_ts, dur_ms, in_t, out_t, ca_t, src_id) in enumerate(activity.llm_calls):
        exported_in += in_t
        exported_out += out_t
        exported_cache += ca_t
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:llm:{idx}:{src_id}:{l_ts}"),
                parent_span_id=stage_span_id,
                category="llm_generation",
                name=f"LLM Generation: {m_name}",
                start_ns=l_ts,
                end_ns=l_ts + max(1, dur_ms) * 1_000_000,
                status_ok=True,
                attributes={
                    "llm.model_name": m_name,
                    "llm.input_tokens": in_t,
                    "llm.output_tokens": out_t,
                    "llm.cache_read": ca_t,
                    "evidence.source": src_id,
                },
                input_payload={"turn_index": idx, "session_id": session_id},
                output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t},
                model=m_name,
                input_tokens=in_t,
                output_tokens=out_t,
                cache_read=ca_t,
            )
        )

    _apply_activity_evidence(spans, activity)

    duration_ms = round((e_ns - s_ns) / 1_000_000, 2)
    present_categories = sorted({sp.category for sp in spans})
    missing_categories = [c for c in SPAN_CATEGORIES if c not in present_categories]

    script_summaries = [
        {
            "script": Path(sp_p).name,
            "exit_code": ec,
            "duration_ms": dur_ms if src_id in activity.measured_durations_ms else None,
        }
        for sp_p, _args, _ts, dur_ms, ec, src_id in activity.script_execs[:8]
    ]
    summary_excerpt = receipt_excerpt or activity.last_assistant_excerpt

    root_input: dict[str, Any] = {
        "session_id": session_id,
        "role": inferred_role,
        "cwd": activity.cwd or "unknown",
        "prompt": prompt_summary,
        "selected_model": activity.selected_model,
    }
    if primary_goal_id:
        root_input["goal_id"] = primary_goal_id
    if brief_targets:
        root_input["brief_targets"] = brief_targets[:6]
    if receipt_targets:
        root_input["receipt_targets"] = receipt_targets[:6]

    root_output: dict[str, Any] = {
        "status": "completed",
        "role": inferred_role,
        "verdict": receipt_verdict,
        "duration_ms": duration_ms,
        "llm_generations": len(activity.llm_calls),
        "script_executions": len(activity.script_execs),
        "total_input_tokens": exported_in,
        "total_output_tokens": exported_out,
        "total_cache_read": exported_cache,
        "present_categories": present_categories,
        "missing_categories": missing_categories,
    }
    if summary_excerpt:
        root_output["receipt_excerpt"] = summary_excerpt
    if script_summaries:
        root_output["scripts"] = script_summaries

    root_attrs: dict[str, str | int | float | bool] = {
        "session.id": session_id,
        "session.role": inferred_role,
        "session.cwd": activity.cwd or "unknown",
        "session.model": activity.selected_model,
        "session.duration_ms": duration_ms,
        "session.complete_5_categories": len(missing_categories) == 0,
    }
    if primary_goal_id:
        root_attrs["goal.id"] = primary_goal_id

    root_span = SpanRecord(
        span_id=root_span_id,
        parent_span_id=None,
        category="root",
        name=f"User Prompt Session: {session_id[:8]} ({inferred_role})",
        start_ns=s_ns,
        end_ns=e_ns,
        status_ok=False if receipt_verdict == "FAIL" else True,
        attributes=root_attrs,
        input_payload=root_input,
        output_payload=root_output,
    )
    spans.insert(0, root_span)

    tags = ["backfill", "session", f"role:{inferred_role}"]
    if primary_goal_id:
        tags.append(f"goal:{primary_goal_id}")
    if not missing_categories:
        tags.append("complete-5-cat")

    meta_dict: dict[str, Any] = {
        "session_id": session_id,
        "langfuse_session_id": langfuse_session_id,
        "role": inferred_role,
        "cwd": activity.cwd,
        "model": activity.selected_model,
        "duration_ms": duration_ms,
        "present_categories": present_categories,
        "missing_categories": missing_categories,
        "span_count": len(spans),
    }
    if primary_goal_id:
        meta_dict["goal_id"] = primary_goal_id

    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root_span_id,
        trace_kind="session",
        entity_id=session_id,
        name=f"Session Prompt: {session_id[:8]} ({inferred_role})",
        start_ns=s_ns,
        end_ns=e_ns,
        status="completed",
        input_payload=root_input,
        output_payload=root_output,
        tags=tags,
        metadata=meta_dict,
        spans=spans,
    )


def build_session_trace_from_copilot(
    events_jsonl: Path,
    wh_maps: dict[str, Any],
    usage_by_session: dict[str, dict[str, list[dict[str, Any]]]],
    skills_root: Path | None = None,
    handoff_root: Path | None = None,
) -> TraceRecord | None:
    known_goals = (
        {p.parent.name for p in handoff_root.glob("*/ledger.md") if not p.parent.name.startswith("_")}
        if handoff_root is not None and handoff_root.is_dir()
        else None
    )
    activity = parse_copilot_session_activity(
        events_jsonl,
        wh_maps,
        usage_by_session,
        skills_root=skills_root,
        known_goals=known_goals,
    )
    if activity is None:
        return None
    return build_session_trace_from_activity(activity, skills_root=skills_root, handoff_root=handoff_root)


def _activity_category_count(act: ParsedSessionActivity) -> int:
    count = 1  # stage_role always present
    if act.skill_invocations:
        count += 1
    if act.ref_reads:
        count += 1
    if act.script_execs:
        count += 1
    if act.llm_calls:
        count += 1
    return count


def collect_historical_traces(
    *,
    handoff_root: Path = DEFAULT_HANDOFF_ROOT,
    copilot_sessions_dir: Path = DEFAULT_COPILOT_SESSIONS_DIR,
    usage_jsonl: Path = DEFAULT_USAGE_JSONL,
    warehouse_db: Path = DEFAULT_WAREHOUSE_DB,
    limit_goals: int = 35,
    limit_sessions: int = 55,
    max_sessions_per_goal: int = 6,
    skills_root: Path | None = None,
) -> tuple[list[TraceRecord], list[TraceRecord]]:
    wh_maps = _load_warehouse_maps(warehouse_db)
    usage_by_session = _load_usage_jsonl(usage_jsonl)

    all_goal_dirs: list[Path] = (
        [p.parent for p in handoff_root.glob("*/ledger.md") if not p.parent.name.startswith("_")]
        if handoff_root.is_dir()
        else []
    )
    known_goals: set[str] | None = {p.name for p in all_goal_dirs} if all_goal_dirs else None

    # Index Copilot sessions for both Session export AND real Goal-to-Session attribution
    parsed_activities: list[ParsedSessionActivity] = []
    goal_to_activities: dict[str, list[ParsedSessionActivity]] = {}

    if copilot_sessions_dir.is_dir():
        rich_session_ids = (
            set(usage_by_session.keys())
            | set(wh_maps["refs_by_session"].keys())
            | set(wh_maps["scripts_by_session"].keys())
        )
        ev_files = [p for p in copilot_sessions_dir.glob("*/events.jsonl") if p.stat().st_size >= 512]
        by_mtime = sorted(ev_files, key=lambda p: p.stat().st_mtime, reverse=True)
        by_rich = sorted(
            ev_files,
            key=lambda p: (1 if p.parent.name in rich_session_ids else 0, p.stat().st_mtime),
            reverse=True,
        )
        scan_cap = max(600, limit_sessions * 8, limit_goals * 12)
        half_cap = scan_cap // 2
        candidates_to_scan = list(dict.fromkeys(by_mtime[:half_cap] + by_rich[:scan_cap]))[:scan_cap]
        for ev_path in candidates_to_scan:
            act = parse_copilot_session_activity(
                ev_path,
                wh_maps,
                usage_by_session,
                skills_root=skills_root,
                known_goals=known_goals,
            )
            if act is None:
                continue
            parsed_activities.append(act)
            if act.primary_goal_id:
                goal_to_activities.setdefault(act.primary_goal_id, []).append(act)
            else:
                for g_name in act.referenced_goals:
                    goal_to_activities.setdefault(g_name, []).append(act)

    # 1. Build Goal Traces (prioritizing complete 5-category evidence + named spec Goals)
    goal_traces: list[TraceRecord] = []
    selected_goal_linked_acts: list[ParsedSessionActivity] = []
    if all_goal_dirs and limit_goals > 0:
        priority_set = set(PRIORITY_GOALS)

        def _goal_rank(gdir: Path) -> tuple[int, int, float]:
            g_acts = [
                a
                for a in goal_to_activities.get(gdir.name, [])
                if a.primary_goal_id == gdir.name or len(a.referenced_goals) <= 8
            ]
            has_all_5 = (
                any(a.skill_invocations for a in g_acts)
                and any(a.ref_reads for a in g_acts)
                and any(a.script_execs for a in g_acts)
                and any(a.llm_calls for a in g_acts)
            )
            is_named = 1 if (gdir.name in priority_set or gdir.name.startswith("vs-ui-")) else 0
            return (1 if has_all_5 else 0, is_named, (gdir / "ledger.md").stat().st_mtime)

        all_goal_dirs.sort(key=_goal_rank, reverse=True)
        for gdir in all_goal_dirs:
            if len(goal_traces) >= limit_goals:
                break
            linked = sorted(
                [
                    a
                    for a in goal_to_activities.get(gdir.name, [])
                    if a.primary_goal_id == gdir.name or len(a.referenced_goals) <= 8
                ],
                key=lambda a: (
                    a.primary_goal_id == gdir.name,
                    len(a.referenced_goals) == 1,
                    _activity_category_count(a),
                    a.end_ns,
                ),
                reverse=True,
            )[:max_sessions_per_goal]
            tr = build_goal_trace(gdir, wh_maps, linked_sessions=linked, skills_root=skills_root)
            if tr is not None:
                goal_traces.append(tr)
                selected_goal_linked_acts.extend(linked)

    # 2. Build Session Traces (prioritizing Goal-linked multi-trace relays + complete 5-category sessions)
    session_traces: list[TraceRecord] = []
    seen_sids: set[str] = set()
    if limit_sessions > 0:
        linked_priority_sids = {a.session_id for a in selected_goal_linked_acts}
        ordered_acts = sorted(
            parsed_activities,
            key=lambda a: (
                a.session_id in linked_priority_sids,
                _activity_category_count(a) == 5,
                _activity_category_count(a),
                a.end_ns,
            ),
            reverse=True,
        )
        for act in ordered_acts:
            if len(session_traces) >= limit_sessions:
                break
            if act.session_id in seen_sids:
                continue
            seen_sids.add(act.session_id)
            session_traces.append(
                build_session_trace_from_activity(
                    act,
                    skills_root=skills_root,
                    handoff_root=handoff_root if handoff_root.is_dir() else None,
                )
            )

    # 3. Fallback session reconstruction from usage.jsonl / warehouse.db (strictly deduplicated!)
    if len(session_traces) < limit_sessions:
        ordered_fallback_sids: list[str] = list(
            dict.fromkeys(
                list(usage_by_session.keys())
                + list(wh_maps["turns_by_session"].keys())
                + list(wh_maps["refs_by_session"].keys())
                + list(wh_maps["scripts_by_session"].keys())
            )
        )
        for sid in ordered_fallback_sids:
            if len(session_traces) >= limit_sessions:
                break
            if not sid or sid in seen_sids:
                continue
            u_b = usage_by_session.get(sid, {})
            turns = wh_maps["turns_by_session"].get(sid, [])
            refs = u_b.get("reference_hit", []) + wh_maps["refs_by_session"].get(sid, [])
            scripts = u_b.get("script_call", []) + wh_maps["scripts_by_session"].get(sid, [])
            skill_turns = u_b.get("skill_turn", [])
            if not (turns or refs or scripts or skill_turns):
                continue

            seen_sids.add(sid)
            ts_candidates = [
                parse_iso_to_ns(r.get("ts"))
                for r in (turns + refs + scripts + skill_turns)
                if r.get("ts")
            ]
            s_ns = min(ts_candidates) if ts_candidates else int(time.time() * 1_000_000_000)
            e_ns = max(ts_candidates) if ts_candidates else s_ns + 1_000_000
            s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000)

            fb_skills: list[tuple[str, int, str]] = []
            for idx, st in enumerate(skill_turns):
                sk = str(st.get("skill") or "").strip()
                if sk:
                    fb_skills.append(
                        (
                            sk,
                            parse_iso_to_ns(st.get("ts"), s_ns),
                            str(st.get("event_id") or f"usage:skill_turn:{idx}"),
                        )
                    )

            fb_refs: list[tuple[str, int | None, int, str]] = []
            for idx, rf in enumerate(refs):
                rp = str(rf.get("reference_path") or "").strip()
                if rp:
                    raw_b = rf.get("byte_len")
                    b_len = int(raw_b) if raw_b is not None and int(raw_b) > 0 else None
                    fb_refs.append(
                        (
                            rp,
                            b_len,
                            parse_iso_to_ns(rf.get("ts"), s_ns),
                            str(rf.get("hit_uid") or rf.get("event_id") or f"ref:{idx}"),
                        )
                    )

            fb_durations: dict[str, int] = {}
            fb_scripts: list[tuple[str, str, int, int, int | None, str]] = []
            for idx, sc in enumerate(scripts):
                sp_p = str(sc.get("script_path") or "").strip()
                if sp_p:
                    dur = max(1, int(float(sc.get("duration_ms") or 1)))
                    raw_ec = sc.get("exit_code")
                    ec = int(raw_ec) if raw_ec is not None else None
                    source_id = str(sc.get("call_uid") or sc.get("event_id") or f"script:{idx}")
                    if sc.get("duration_ms") is not None:
                        fb_durations[source_id] = dur
                    fb_scripts.append((sp_p, sp_p, parse_iso_to_ns(sc.get("ts"), s_ns), dur, ec, source_id))

            fb_llms: list[tuple[str, int, int, int, int, int, str]] = []
            token_skill_turns = [row for row in skill_turns if any(
                int(row.get(key) or 0) > 0 for key in ("input_tokens", "output_tokens", "cache_read")
            )]
            source_llm_rows = token_skill_turns if token_skill_turns else turns
            for idx, tn in enumerate(source_llm_rows):
                in_t = int(tn.get("input_tokens") or 0)
                out_t = int(tn.get("output_tokens") or 0)
                ca_t = int(tn.get("cache_read") or 0)
                if in_t > 0 or out_t > 0 or ca_t > 0:
                    m_name = str(tn.get("model") or "unknown")
                    fb_llms.append(
                        (
                            m_name,
                            parse_iso_to_ns(tn.get("ts"), s_ns),
                            1,
                            in_t,
                            out_t,
                            ca_t,
                            str(tn.get("turn_uuid") or tn.get("event_id") or f"llm:{idx}"),
                        )
                    )

            fb_act = ParsedSessionActivity(
                session_id=sid,
                source_path="telemetry_fallback",
                selected_model=fb_llms[0][0] if fb_llms else "unknown",
                cwd="telemetry_fallback",
                first_prompt="",
                start_ns=s_ns,
                end_ns=e_ns,
                referenced_goals=set(),
                skill_invocations=fb_skills,
                ref_reads=fb_refs,
                script_execs=fb_scripts,
                llm_calls=fb_llms,
                measured_durations_ms=fb_durations,
            )
            session_traces.append(build_session_trace_from_activity(fb_act, skills_root=skills_root))

    return goal_traces, session_traces


# ---------------------------------------------------------------------------
# Exporters (Phoenix OTLP HTTP + Langfuse Batch Ingestion)
# ---------------------------------------------------------------------------


def load_obs_config(config_path: Path = DEFAULT_OBS_CONFIG) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
    phoenix_cfg = cfg.get("phoenix") if isinstance(cfg.get("phoenix"), dict) else {}
    langfuse_cfg = cfg.get("langfuse") if isinstance(cfg.get("langfuse"), dict) else {}
    return {
        "phoenix_otlp_url": os.environ.get(
            "PHOENIX_OTLP_HTTP_URL",
            str(phoenix_cfg.get("otlp_http_endpoint") or "http://127.0.0.1:6006/v1/traces"),
        ),
        "phoenix_project": os.environ.get(
            "PHOENIX_PROJECT_NAME",
            str(phoenix_cfg.get("project_name") or "agent-observability"),
        ),
        "langfuse_url": os.environ.get(
            "LANGFUSE_HOST",
            str(langfuse_cfg.get("local_url") or "http://127.0.0.1:13100"),
        ).rstrip("/"),
        "langfuse_public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", str(langfuse_cfg.get("public_key") or "")),
        "langfuse_secret_key": os.environ.get("LANGFUSE_SECRET_KEY", str(langfuse_cfg.get("secret_key") or "")),
    }


def _clear_local_phoenix_project(otlp_url: str, project_name: str, timeout: int = 15) -> bool:
    """Clear stale spans in local Phoenix project before idempotent re-backfill via official GraphQL mutation."""
    if not any(host in otlp_url for host in ("127.0.0.1:6006", "localhost:6006")):
        return False
    base_url = otlp_url.split("/v1/traces", 1)[0].rstrip("/")
    gql_url = f"{base_url}/graphql"
    try:
        q_proj = {"query": "{ projects { edges { node { id name } } } }"}
        req_p = Request(
            gql_url,
            data=json.dumps(q_proj).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req_p, timeout=timeout) as resp_p:
            proj_data = json.loads(resp_p.read().decode("utf-8", errors="replace"))
        edges = ((proj_data.get("data") or {}).get("projects") or {}).get("edges") or []
        proj_id = next(
            (e["node"]["id"] for e in edges if (e.get("node") or {}).get("name") == project_name),
            None,
        )
        if not proj_id:
            return False
        mut = {
            "query": "mutation ClearProj($id: ID!) { clearProject(input: { id: $id }) { __typename } }",
            "variables": {"id": proj_id},
        }
        req_m = Request(
            gql_url,
            data=json.dumps(mut).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req_m, timeout=timeout) as resp_m:
            mut_res = json.loads(resp_m.read().decode("utf-8", errors="replace"))
        return not bool(mut_res.get("errors"))
    except (HTTPError, URLError, OSError, ValueError, KeyError):
        return False


def export_to_phoenix(
    traces: list[TraceRecord],
    *,
    otlp_url: str,
    project_name: str,
    batch_size: int = 20,
    timeout: int = 25,
    replace_local_project: bool = True,
) -> dict[str, Any]:
    cleared = _clear_local_phoenix_project(otlp_url, project_name, timeout=timeout) if replace_local_project else False
    sent_traces = 0
    sent_spans = 0
    batches = 0
    for idx in range(0, len(traces), batch_size):
        chunk = traces[idx : idx + batch_size]
        payload = encode_otlp_traces_protobuf(project_name, chunk)
        req = Request(
            otlp_url,
            data=payload,
            headers={"Content-Type": "application/x-protobuf"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Phoenix OTLP HTTP {resp.status}")
            resp.read()
        batches += 1
        sent_traces += len(chunk)
        sent_spans += sum(len(t.spans) for t in chunk)
    return {
        "ok": True,
        "endpoint": otlp_url,
        "project": project_name,
        "project_cleared": cleared,
        "batches": batches,
        "traces_exported": sent_traces,
        "spans_exported": sent_spans,
    }


def _prune_langfuse_orphan_sessions(
    *,
    db_container: str = "agent-obs-langfuse-db",
    db_user: str = "langfuse",
    db_name: str = "langfuse",
) -> int:
    """Remove 0-trace orphan rows in local self-hosted Langfuse `trace_sessions`."""
    sql = (
        "WITH deleted AS ("
        "DELETE FROM trace_sessions s "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM traces t WHERE t.session_id = s.id AND t.project_id = s.project_id"
        ") RETURNING 1"
        ") SELECT COUNT(*) FROM deleted;"
    )
    try:
        proc = subprocess.run(
            ["docker", "exec", db_container, "psql", "-U", db_user, "-d", db_name, "-t", "-A", "-c", sql],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            out = (proc.stdout or "").strip().splitlines()
            if out and out[-1].strip().isdigit():
                return int(out[-1].strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 0


def _delete_stale_langfuse_observations(
    traces: Sequence[TraceRecord],
    *,
    db_container: str = "agent-obs-langfuse-db",
    db_user: str = "langfuse",
    db_name: str = "langfuse",
) -> int:
    """Prune stale child observations on re-exported traces when local Langfuse DB is present."""
    if not traces:
        return 0
    total_deleted = 0
    for tr in traces:
        if not re.fullmatch(r"[0-9a-f]{32}", tr.trace_id):
            continue
        valid_ids = [
            sp.span_id
            for sp in tr.spans
            if sp.span_id != tr.root_span_id and re.fullmatch(r"[0-9a-f]{16}", sp.span_id)
        ]
        if valid_ids:
            quoted = ",".join(f"'{sid}'" for sid in valid_ids)
            where_expr = f"trace_id = '{tr.trace_id}' AND id NOT IN ({quoted})"
        else:
            where_expr = f"trace_id = '{tr.trace_id}'"
        sql = f"WITH deleted AS (DELETE FROM observations WHERE {where_expr} RETURNING 1) SELECT COUNT(*) FROM deleted;"
        try:
            proc = subprocess.run(
                ["docker", "exec", "-i", db_container, "psql", "-U", db_user, "-d", db_name, "-t", "-A"],
                input=sql,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                out = (proc.stdout or "").strip().splitlines()
                if out and out[-1].strip().isdigit():
                    total_deleted += int(out[-1].strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            break
    return total_deleted


def export_to_langfuse(
    traces: list[TraceRecord],
    *,
    base_url: str,
    public_key: str,
    secret_key: str,
    event_batch_size: int = 80,
    timeout: int = 25,
    prune_orphan_sessions: bool = True,
) -> dict[str, Any]:
    if not public_key or not secret_key:
        raise ValueError("Langfuse credentials missing; configure ~/.local/share/agent-observability/config.json")
    all_events: list[dict[str, Any]] = []
    for tr in traces:
        all_events.extend(build_langfuse_ingestion_events(tr))

    auth_token = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    ingestion_url = f"{base_url.rstrip('/')}/api/public/ingestion"
    successes = 0
    errors: list[Any] = []
    batches = 0

    for idx in range(0, len(all_events), event_batch_size):
        chunk = all_events[idx : idx + event_batch_size]
        body = json.dumps({"batch": chunk}, ensure_ascii=False).encode("utf-8")
        req = Request(
            ingestion_url,
            data=body,
            headers={
                "Authorization": f"Basic {auth_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            successes += len(parsed.get("successes", chunk))
            if parsed.get("errors"):
                errors.extend(parsed["errors"])
        batches += 1

    stale_obs_pruned = 0
    orphan_sessions_pruned = 0
    if prune_orphan_sessions and any(host in base_url for host in ("127.0.0.1:13100", "localhost:13100")):
        stale_obs_pruned = _delete_stale_langfuse_observations(traces)
        orphan_sessions_pruned = _prune_langfuse_orphan_sessions()

    return {
        "ok": len(errors) == 0,
        "endpoint": ingestion_url,
        "batches": batches,
        "traces_exported": len(traces),
        "events_submitted": len(all_events),
        "events_succeeded": successes,
        "stale_observations_pruned": stale_obs_pruned,
        "orphan_sessions_pruned": orphan_sessions_pruned,
        "errors": errors[:10],
    }


def summarize_traces(goal_traces: list[TraceRecord], session_traces: list[TraceRecord]) -> dict[str, Any]:
    all_traces = goal_traces + session_traces
    cat_counts = {c: 0 for c in ("root", *SPAN_CATEGORIES)}
    complete_goals = 0
    complete_sessions = 0
    for tr in goal_traces:
        cats = {sp.category for sp in tr.spans if sp.category != "root"}
        if all(c in cats for c in SPAN_CATEGORIES):
            complete_goals += 1
    for tr in session_traces:
        cats = {sp.category for sp in tr.spans if sp.category != "root"}
        if all(c in cats for c in SPAN_CATEGORIES):
            complete_sessions += 1
    for tr in all_traces:
        for sp in tr.spans:
            cat_counts[sp.category] = cat_counts.get(sp.category, 0) + 1
    return {
        "goal_traces": len(goal_traces),
        "goal_traces_complete_5_categories": complete_goals,
        "session_traces": len(session_traces),
        "session_traces_complete_5_categories": complete_sessions,
        "total_traces": len(all_traces),
        "total_spans": sum(len(t.spans) for t in all_traces),
        "span_categories": cat_counts,
        "sample_goals": [t.entity_id for t in goal_traces[:8]],
        "sample_sessions": [t.entity_id for t in session_traces[:5]],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill historical Goal & Session traces into Arize Phoenix and Langfuse."
    )
    parser.add_argument(
        "--target",
        choices=("both", "phoenix", "langfuse"),
        default="both",
        help="Observability target(s) to export traces into (default: both).",
    )
    parser.add_argument("--limit-goals", type=int, default=35, help="Maximum historical Goal traces to export.")
    parser.add_argument("--limit-sessions", type=int, default=55, help="Maximum Session Prompt traces to export.")
    parser.add_argument("--handoff-root", type=Path, default=DEFAULT_HANDOFF_ROOT)
    parser.add_argument("--copilot-sessions-dir", type=Path, default=DEFAULT_COPILOT_SESSIONS_DIR)
    parser.add_argument("--usage-jsonl", type=Path, default=DEFAULT_USAGE_JSONL)
    parser.add_argument("--warehouse-db", type=Path, default=DEFAULT_WAREHOUSE_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_OBS_CONFIG)
    parser.add_argument("--phoenix-project", default=None, help="Override Phoenix project name.")
    parser.add_argument("--dry-run", action="store_true", help="Build trace trees and validate spans without HTTP export.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.limit_goals < 0 or args.limit_sessions < 0:
        print(json.dumps({"ok": False, "error": "limits must be non-negative"}), file=sys.stderr)
        return 2

    skills_root = Path(__file__).resolve().parents[2]
    goal_traces, session_traces = collect_historical_traces(
        handoff_root=args.handoff_root.expanduser(),
        copilot_sessions_dir=args.copilot_sessions_dir.expanduser(),
        usage_jsonl=args.usage_jsonl.expanduser(),
        warehouse_db=args.warehouse_db.expanduser(),
        limit_goals=args.limit_goals,
        limit_sessions=args.limit_sessions,
        skills_root=skills_root,
    )
    all_traces = goal_traces + session_traces
    summary = summarize_traces(goal_traces, session_traces)
    cfg = load_obs_config(args.config.expanduser())
    project_name = args.phoenix_project or cfg["phoenix_project"]

    exports: dict[str, Any] = {}
    ok = True
    if not args.dry_run and all_traces:
        try:
            if args.target in ("both", "phoenix"):
                exports["phoenix"] = export_to_phoenix(
                    all_traces,
                    otlp_url=cfg["phoenix_otlp_url"],
                    project_name=project_name,
                )
                ok = ok and bool(exports["phoenix"].get("ok"))
            if args.target in ("both", "langfuse"):
                exports["langfuse"] = export_to_langfuse(
                    all_traces,
                    base_url=cfg["langfuse_url"],
                    public_key=cfg["langfuse_public_key"],
                    secret_key=cfg["langfuse_secret_key"],
                )
                ok = ok and bool(exports["langfuse"].get("ok"))
        except (HTTPError, URLError, ValueError, RuntimeError, OSError) as exc:
            ok = False
            exports["error"] = str(exc)

    result = {
        "ok": ok,
        "dry_run": args.dry_run,
        "target": args.target,
        "summary": summary,
        "exports": exports,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"[trace_backfill_exporter] ok={ok} dry_run={args.dry_run} target={args.target} "
            f"goals={summary['goal_traces']} sessions={summary['session_traces']} spans={summary['total_spans']}"
        )
        if not ok and "error" in exports:
            print(f"Error: {exports['error']}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
