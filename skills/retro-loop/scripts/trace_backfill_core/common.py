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
_VERDICT_LINE_RE = re.compile(r"\bV5\s*Verdict\s*[:=]\s*(PASS|CONCERNS|WAIVED|FAIL)\b", re.I)
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


def _normalize_role(raw_role: str) -> str:
    lower = raw_role.lower()
    if "designer" in lower:
        return "Designer"
    if "reviewer" in lower or "review" in lower:
        return "Reviewer"
    if "anticheat" in lower or "anti-cheat" in lower:
        return "AntiCheat"
    if "verifier" in lower or "verify" in lower:
        return "Verifier"
    if "writer" in lower or "coder" in lower or "worker" in lower:
        return "Writer"
    if "m2" in lower or "coordinator" in lower or "dispatcher" in lower:
        return "M2"
    if "m3" in lower or "owner" in lower:
        return "M3"
    return "M1 TL"


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
    events: list[dict[str, Any]] = [
        {
            "id": deterministic_trace_id(f"lf-evt:trace:{trace.trace_id}"),
            "timestamp": ns_to_iso_utc(trace.start_ns),
            "type": "trace-create",
            "body": {
                "id": trace.trace_id,
                "timestamp": ns_to_iso_utc(trace.start_ns),
                "name": trace.name,
                "sessionId": trace.entity_id if trace.trace_kind == "session" else f"goal:{trace.entity_id}",
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
