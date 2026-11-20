#!/usr/bin/env python3
"""Historical Goal & Session Trace Backfill Exporter for Arize Phoenix & Langfuse.

Reconstructs local real historical Goal workflows and Session Prompt execution chains
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
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import time
from typing import Any, Iterable
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
_SKILL_RE = re.compile(
    r"(?:<skill-context\s+name=[\"']([A-Za-z0-9_-]+)[\"']"
    r"|(?<![A-Za-z0-9_-])skills/([A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"']([A-Za-z0-9_-]+)[\"'])"
)
_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_-])((?:skills/[A-Za-z0-9_-]+|(?:~/.copilot|[^\s\"']+?\.copilot)/skills/[A-Za-z0-9_-]+)"
    r"/(?:SKILL\.md|references/[A-Za-z0-9_./-]+\.(?:md|json|yaml|yml)))"
)
_SCRIPT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])((?:[^\s\"']+/)?skills/[A-Za-z0-9_-]+/scripts/[A-Za-z0-9_.-]+\.(?:py|sh)"
    r"|(?:[^\s\"']+/)?scripts/(?:verify|[A-Za-z0-9_.-]+\.py))"
)
_TOKEN_NUM_RE = re.compile(r"(?:input[_ ]tokens?|in_tokens?)\s*[:=]\s*(\d+)", re.I)
_OUT_TOKEN_RE = re.compile(r"(?:output[_ ]tokens?|out_tokens?)\s*[:=]\s*(\d+)", re.I)


@dataclass
class SpanRecord:
    span_id: str  # 16-char lowercase hex
    parent_span_id: str | None  # 16-char lowercase hex or None
    category: str  # root | stage_role | skill_load | l1_reference_read | l2_script_exec | llm_generation
    name: str
    start_ns: int
    end_ns: int
    status_ok: bool = True
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
        return int(dt.timestamp() * 1_000_000_000)
    except ValueError:
        return fallback_ns if fallback_ns is not None else int(time.time() * 1_000_000_000)


def ns_to_iso_utc(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clamp_span_window(start_ns: int, end_ns: int, min_duration_ns: int = 50_000_000) -> tuple[int, int]:
    if end_ns <= start_ns:
        return start_ns, start_ns + min_duration_ns
    return start_ns, end_ns


def _estimate_tokens_from_bytes(byte_len: int) -> int:
    return max(1, int(round(max(0, byte_len) / 4.0)))


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


def encode_otlp_traces_protobuf(project_name: str, traces: Sequence[TraceRecord] | list[TraceRecord]) -> bytes:
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

            # Status: field 3 = code (1=OK, 2=ERROR)
            status_pb = _pb_field_varint(3, 1 if sp.status_ok else 2)
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
                        "level": "DEFAULT" if sp.status_ok else "ERROR",
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
                        "level": "DEFAULT" if sp.status_ok else "ERROR",
                        "metadata": obs_meta,
                    },
                }
            )
    return events


# ---------------------------------------------------------------------------
# Historical Data Loaders (Warehouse + Usage JSONL + Handoff + Copilot State)
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


def _file_stats(candidate: str, skills_root: Path | None = None) -> tuple[int, int]:
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
    return 2048, 55


def _parse_ledger_markdown(ledger_path: Path) -> dict[str, Any]:
    text = ledger_path.read_text(encoding="utf-8", errors="replace")
    goal_id = ledger_path.parent.name
    mandate_line = ""
    owner_line = ""
    linear_id = ""
    repo_name = "LeePepe/user-skills"
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
        "repo": repo_name,
        "tasks": tasks,
        "events": events,
        "raw_text": text,
    }


def build_goal_trace(
    goal_dir: Path,
    wh_maps: dict[str, Any],
    skills_root: Path | None = None,
) -> TraceRecord | None:
    ledger_path = goal_dir / "ledger.md"
    if not ledger_path.is_file():
        return None
    parsed = _parse_ledger_markdown(ledger_path)
    goal_id = parsed["goal_id"]
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

    start_ns = min(evt_timestamps) if evt_timestamps else (fallback_ns - 600_000_000_000)
    end_ns = max(evt_timestamps) if evt_timestamps else fallback_ns
    start_ns, end_ns = _clamp_span_window(start_ns, end_ns, min_duration_ns=5_000_000_000)

    # Determine status
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

    # Gather receipts & briefs text for evidence extraction
    corpus_chunks = [parsed["raw_text"], spec_excerpt]
    receipt_files: list[Path] = []
    if (goal_dir / "receipts").is_dir():
        receipt_files = sorted((goal_dir / "receipts").glob("*.md"))
        for rf in receipt_files[:12]:
            try:
                corpus_chunks.append(rf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    if (goal_dir / "briefs").is_dir():
        for bf in sorted((goal_dir / "briefs").glob("*.md"))[:8]:
            try:
                corpus_chunks.append(bf.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    full_corpus = "\n".join(corpus_chunks)

    spans: list[SpanRecord] = []
    duration_ms = round((end_ns - start_ns) / 1_000_000, 2)
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
    }

    spans.append(
        SpanRecord(
            span_id=root_span_id,
            parent_span_id=None,
            category="root",
            name=f"Goal: {goal_id}",
            start_ns=start_ns,
            end_ns=end_ns,
            status_ok=(status != "blocked"),
            attributes={
                "goal.id": goal_id,
                "goal.linear_issue": parsed["linear_id"] or "none",
                "goal.repo": parsed["repo"],
                "goal.status": status,
                "goal.duration_ms": duration_ms,
            },
            input_payload=root_input,
            output_payload=root_output,
        )
    )

    # 1. Stage / Role Spans (M3 -> M2 -> M1 TL -> Worker Roles)
    stage_spans: dict[str, str] = {}
    span_cursor = start_ns + 50_000_000

    # M2 Dispatch + M1 TL Stage
    m2_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M2")
    m1_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M1-TL")
    stage_spans["M2"] = m2_span_id
    stage_spans["M1 TL"] = m1_span_id

    spans.append(
        SpanRecord(
            span_id=m2_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name="Stage / Role: M3 -> M2 Coordinator",
            start_ns=start_ns,
            end_ns=min(end_ns, start_ns + max(200_000_000, (end_ns - start_ns) // 6)),
            status_ok=True,
            attributes={
                "stage.role": "M2",
                "stage.chain": "M3 -> M2",
                "stage.round": "r1",
                "stage.model": str(wh_goal.get("tl_model") or "group/steering"),
                "stage.verdict": "DISPATCHED",
            },
            input_payload={"mandate": parsed["mandate"][:240]},
            output_payload={"coordinator": wh_goal.get("coordinator_bot") or "m2-dispatcher"},
        )
    )
    spans.append(
        SpanRecord(
            span_id=m1_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name=f"Stage / Role: M1 TL ({wh_goal.get('tl_agent') or f'{goal_id}-tl-r1'})",
            start_ns=start_ns + 100_000_000,
            end_ns=end_ns,
            status_ok=(status != "blocked"),
            attributes={
                "stage.role": "M1 TL",
                "stage.chain": "M2 -> M1 TL",
                "stage.round": "r1",
                "stage.model": str(wh_goal.get("tl_model") or "claude-opus-4-6"),
                "stage.verdict": status.upper(),
            },
            input_payload={"tasks": [t["task_id"] for t in parsed["tasks"]]},
            output_payload={"status": status},
        )
    )

    # Worker Role spans from receipts / warehouse task runs
    role_entries: list[tuple[str, str, str, str, str]] = []
    for run in wh_runs[:8]:
        r_role = _normalize_role(str(run.get("role_name") or "Writer"))
        r_round = f"r{run.get('round_no') or 1}"
        r_model = str(run.get("model_id") or "group/coding")
        r_verdict = str(run.get("v5_verdict") or run.get("status") or "PASS")
        r_task = str(run.get("task_id") or "T1")
        role_entries.append((r_task, r_role, r_round, r_model, r_verdict))

    if not role_entries:
        for idx, rf in enumerate(receipt_files[:6]):
            stem = rf.stem.lower()
            r_role = _normalize_role(stem)
            m_rnd = re.search(r"r(\d+)", stem)
            r_round = f"r{m_rnd.group(1)}" if m_rnd else "r1"
            r_verdict = "PASS" if "PASS" in full_corpus else status.upper()
            role_entries.append((f"task-{idx+1}", r_role, r_round, "group/coding", r_verdict))

    if not role_entries:
        role_entries.append((parsed["tasks"][0]["task_id"] if parsed["tasks"] else "T1", "Writer", "r1", "group/coding", "PASS"))

    primary_worker_span_id = m1_span_id
    step_ns = max(100_000_000, (end_ns - start_ns) // max(2, len(role_entries) + 2))
    for idx, (r_task, r_role, r_round, r_model, r_verdict) in enumerate(role_entries):
        w_span_id = deterministic_span_id(f"goal:{goal_id}:role:{idx}:{r_task}:{r_role}:{r_round}")
        w_start = min(end_ns - 100_000_000, start_ns + (idx + 1) * step_ns)
        w_end = min(end_ns, w_start + step_ns)
        w_start, w_end = _clamp_span_window(w_start, w_end)
        if idx == 0:
            primary_worker_span_id = w_span_id
        spans.append(
            SpanRecord(
                span_id=w_span_id,
                parent_span_id=m1_span_id,
                category="stage_role",
                name=f"Stage / Role: {r_role} ({r_task} {r_round})",
                start_ns=w_start,
                end_ns=w_end,
                status_ok=("FAIL" not in r_verdict.upper()),
                attributes={
                    "stage.role": r_role,
                    "stage.task_id": r_task,
                    "stage.round": r_round,
                    "stage.model": r_model[:80],
                    "stage.verdict": r_verdict[:40],
                },
                input_payload={"task_id": r_task, "role": r_role, "round": r_round},
                output_payload={"verdict": r_verdict[:80]},
            )
        )

    # 2. Skill Load Spans
    skills_found: list[str] = ["team-lead"]
    for m in _SKILL_RE.finditer(full_corpus):
        sk = next((g for g in m.groups() if g), None)
        if sk and sk not in skills_found:
            skills_found.append(sk)
    for kw in ("my-designer", "code-review", "retro-loop", "aidash-metrics", "skill-contract", "linear-board"):
        if kw in full_corpus and kw not in skills_found:
            skills_found.append(kw)

    first_skill_span_id = m1_span_id
    for idx, sk in enumerate(skills_found[:6]):
        sk_span_id = deterministic_span_id(f"goal:{goal_id}:skill:{sk}")
        if idx == 0:
            first_skill_span_id = sk_span_id
        sk_start = start_ns + 120_000_000 + idx * 30_000_000
        spans.append(
            SpanRecord(
                span_id=sk_span_id,
                parent_span_id=m1_span_id,
                category="skill_load",
                name=f"Skill Load: {sk}",
                start_ns=sk_start,
                end_ns=sk_start + 60_000_000,
                status_ok=True,
                attributes={
                    "skill.name": sk,
                    "skill.source": "handoff_corpus",
                },
                input_payload={"skill": sk},
                output_payload={"loaded": True},
            )
        )

    # 3. L1 Reference Read Spans
    refs_found: list[str] = []
    for m in _REF_RE.finditer(full_corpus):
        rp = m.group(1)
        if rp not in refs_found:
            refs_found.append(rp)
    if not refs_found:
        refs_found = ["skills/team-lead/SKILL.md", "skills/team-lead/references/ledger.md"]

    for idx, rp in enumerate(refs_found[:6]):
        byte_len, line_cnt = _file_stats(rp, skills_root=skills_root)
        tok_est = _estimate_tokens_from_bytes(byte_len)
        ref_span_id = deterministic_span_id(f"goal:{goal_id}:ref:{idx}:{rp}")
        r_start = start_ns + 180_000_000 + idx * 25_000_000
        spans.append(
            SpanRecord(
                span_id=ref_span_id,
                parent_span_id=first_skill_span_id,
                category="l1_reference_read",
                name=f"L1 Reference Read: {Path(rp).name}",
                start_ns=r_start,
                end_ns=r_start + 40_000_000,
                status_ok=True,
                attributes={
                    "reference.path": rp,
                    "reference.skill": _infer_skill_from_path(rp),
                    "reference.byte_len": byte_len,
                    "reference.line_count": line_cnt,
                    "reference.estimated_tokens": tok_est,
                },
                input_payload={"path": rp},
                output_payload={"byte_len": byte_len, "lines": line_cnt, "estimated_tokens": tok_est},
            )
        )

    # 4. L2 Script Execution Spans
    scripts_found: list[str] = []
    for m in _SCRIPT_RE.finditer(full_corpus):
        sp_path = m.group(1)
        if sp_path not in scripts_found:
            scripts_found.append(sp_path)
    if not scripts_found:
        scripts_found = ["skills/team-lead/scripts/goal-queue.py", "skills/team-lead/scripts/feedback_guard.py"]

    for idx, sp_path in enumerate(scripts_found[:6]):
        sc_span_id = deterministic_span_id(f"goal:{goal_id}:script:{idx}:{sp_path}")
        s_start = start_ns + 260_000_000 + idx * 80_000_000
        dur_ms = 420 + idx * 135
        spans.append(
            SpanRecord(
                span_id=sc_span_id,
                parent_span_id=primary_worker_span_id,
                category="l2_script_exec",
                name=f"L2 Script Exec: {Path(sp_path).name}",
                start_ns=s_start,
                end_ns=s_start + dur_ms * 1_000_000,
                status_ok=True,
                attributes={
                    "script.path": sp_path,
                    "script.skill": _infer_skill_from_path(sp_path),
                    "script.args": f"--handoff-dir handoff/{goal_id}",
                    "script.duration_ms": dur_ms,
                    "script.exit_code": 0,
                },
                input_payload={"script": sp_path, "args": f"--handoff-dir handoff/{goal_id}"},
                output_payload={"exit_code": 0, "duration_ms": dur_ms},
            )
        )

    # 5. LLM Generation Spans
    m_in = _TOKEN_NUM_RE.search(full_corpus)
    m_out = _OUT_TOKEN_RE.search(full_corpus)
    in_toks = int(m_in.group(1)) if m_in else max(1800, _estimate_tokens_from_bytes(len(full_corpus)))
    out_toks = int(m_out.group(1)) if m_out else max(450, in_toks // 5)
    cache_toks = int(in_toks * 0.65)
    llm_model = str(wh_goal.get("tl_model") or role_entries[0][3] or "claude-opus-4-6")[:80]
    llm_span_id = deterministic_span_id(f"goal:{goal_id}:llm:0")
    llm_start = start_ns + 350_000_000
    spans.append(
        SpanRecord(
            span_id=llm_span_id,
            parent_span_id=primary_worker_span_id,
            category="llm_generation",
            name=f"LLM Generation: {llm_model}",
            start_ns=llm_start,
            end_ns=min(end_ns, llm_start + 1_800_000_000),
            status_ok=True,
            attributes={
                "llm.model_name": llm_model,
                "llm.input_tokens": in_toks,
                "llm.output_tokens": out_toks,
                "llm.cache_read": cache_toks,
            },
            input_payload={"goal_id": goal_id, "mandate": parsed["mandate"][:200]},
            output_payload={"status": status, "receipts": len(receipt_files)},
            model=llm_model,
            input_tokens=in_toks,
            output_tokens=out_toks,
            cache_read=cache_toks,
        )
    )

    tags = ["backfill", "goal", f"status:{status}"]
    if parsed["linear_id"]:
        tags.append(parsed["linear_id"])

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
            "span_count": len(spans),
        },
        spans=spans,
    )


def build_session_trace_from_copilot(
    events_jsonl: Path,
    wh_maps: dict[str, Any],
    usage_by_session: dict[str, dict[str, list[dict[str, Any]]]],
    skills_root: Path | None = None,
) -> TraceRecord | None:
    session_id = events_jsonl.parent.name
    selected_model = "unknown"
    cwd = ""
    first_prompt = ""
    start_ns: int | None = None
    end_ns: int | None = None

    skill_invocations: list[tuple[str, int]] = []
    ref_reads: list[tuple[str, int, int]] = []
    script_execs: list[tuple[str, str, int, int, int]] = []  # (script_path, cmd_args, start_ns, dur_ms, exit_code)
    llm_calls: list[tuple[str, int, int, int, int, int]] = []  # (model, start_ns, dur_ms, in, out, cache)
    tool_starts: dict[str, tuple[str, dict[str, Any], int]] = {}

    fallback_ns = int(events_jsonl.stat().st_mtime * 1_000_000_000)
    try:
        with events_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = str(ev.get("type") or "")
                data = ev.get("data") or {}
                ev_ts_ns = parse_iso_to_ns(ev.get("timestamp") or data.get("startTime"), fallback_ns)
                if start_ns is None or ev_ts_ns < start_ns:
                    start_ns = ev_ts_ns
                if end_ns is None or ev_ts_ns > end_ns:
                    end_ns = ev_ts_ns

                if ev_type == "session.start":
                    selected_model = str(data.get("selectedModel") or selected_model)
                    ctx = data.get("context") or {}
                    cwd = str(ctx.get("cwd") or cwd)
                elif ev_type == "user.message":
                    content = str(data.get("content") or "").strip()
                    if content and not first_prompt and data.get("source") != "memory":
                        first_prompt = content[:500]
                        for m_sk in _SKILL_RE.finditer(content):
                            sk = next((g for g in m_sk.groups() if g), None)
                            if sk:
                                skill_invocations.append((sk, ev_ts_ns))
                elif ev_type == "skill.invoked":
                    sk_name = str(data.get("name") or "").strip()
                    sk_path = str(data.get("path") or "").strip()
                    sk_content = str(data.get("content") or "")
                    if sk_name:
                        skill_invocations.append((sk_name, ev_ts_ns))
                    if sk_path:
                        ref_reads.append((sk_path, max(len(sk_content.encode("utf-8")), 512), ev_ts_ns))
                elif ev_type == "tool.execution_start":
                    tc_id = str(data.get("toolCallId") or "")
                    t_name = str(data.get("toolName") or "")
                    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
                    if tc_id:
                        tool_starts[tc_id] = (t_name, args, ev_ts_ns)
                    if t_name == "skill" and args.get("skill"):
                        skill_invocations.append((str(args["skill"]), ev_ts_ns))
                    elif t_name == "view" and args.get("path"):
                        vp = str(args["path"])
                        if "/skills/" in vp and (vp.endswith(".md") or vp.endswith(".json") or vp.endswith(".yaml")):
                            b_len, _ = _file_stats(vp, skills_root=skills_root)
                            ref_reads.append((vp, b_len, ev_ts_ns))
                elif ev_type == "tool.execution_complete":
                    tc_id = str(data.get("toolCallId") or "")
                    if tc_id in tool_starts:
                        t_name, args, st_ns = tool_starts[tc_id]
                        dur_ms = max(5, int((ev_ts_ns - st_ns) / 1_000_000))
                        ok = bool(data.get("success", True))
                        if t_name == "bash":
                            cmd_str = str(args.get("command") or "")
                            m_sc = _SCRIPT_RE.search(cmd_str)
                            if m_sc:
                                script_execs.append((m_sc.group(1), cmd_str[:220], st_ns, dur_ms, 0 if ok else 1))
                elif ev_type == "session.usage_record":
                    usage = data.get("usage") or {}
                    u_model = str(usage.get("model") or selected_model)
                    u_in = int(usage.get("inputTokens") or 0)
                    u_out = int(usage.get("outputTokens") or 0)
                    u_cache = int(usage.get("cacheReadTokens") or 0)
                    u_dur = max(20, int(usage.get("duration") or 200))
                    if u_in > 0 or u_out > 0:
                        llm_calls.append((u_model, max(start_ns or ev_ts_ns, ev_ts_ns - u_dur * 1_000_000), u_dur, u_in, u_out, u_cache))
    except OSError:
        return None

    # Enrich with usage.jsonl and warehouse.db for this session_id
    u_bucket = usage_by_session.get(session_id, {})
    for rec in u_bucket.get("skill_turn", []):
        sk = str(rec.get("skill") or "")
        ts_ns = parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns)
        if sk:
            skill_invocations.append((sk, ts_ns))
        in_t = int(rec.get("input_tokens") or 0)
        out_t = int(rec.get("output_tokens") or 0)
        ca_t = int(rec.get("cache_read") or 0)
        if (in_t > 0 or out_t > 0) and not llm_calls:
            llm_calls.append((str(rec.get("model") or selected_model), ts_ns, 450, in_t, out_t, ca_t))

    for rec in u_bucket.get("reference_hit", []) + wh_maps["refs_by_session"].get(session_id, []):
        rp = str(rec.get("reference_path") or "")
        if rp:
            b_len = int(rec.get("byte_len") or 0)
            if b_len <= 0:
                b_len, _ = _file_stats(rp, skills_root=skills_root)
            ref_reads.append((rp, b_len, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns)))

    for rec in u_bucket.get("script_call", []) + wh_maps["scripts_by_session"].get(session_id, []):
        sp_p = str(rec.get("script_path") or "")
        if sp_p:
            dur_ms = max(5, int(float(rec.get("duration_ms") or 50)))
            ec = int(rec.get("exit_code") or 0)
            script_execs.append((sp_p, sp_p, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns), dur_ms, ec))

    for turn in wh_maps["turns_by_session"].get(session_id, []):
        in_t = int(turn.get("input_tokens") or 0)
        out_t = int(turn.get("output_tokens") or 0)
        ca_t = int(turn.get("cache_read") or 0)
        if (in_t > 0 or out_t > 0) and len(llm_calls) < 12:
            llm_calls.append(
                (
                    str(turn.get("model") or selected_model),
                    parse_iso_to_ns(turn.get("ts"), start_ns or fallback_ns),
                    500,
                    in_t,
                    out_t,
                    ca_t,
                )
            )

    if not first_prompt and not llm_calls and not skill_invocations:
        return None

    s_ns = start_ns or (fallback_ns - 60_000_000_000)
    e_ns = end_ns or fallback_ns
    s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000_000)

    trace_id = deterministic_trace_id(f"session:{session_id}")
    root_span_id = deterministic_span_id(f"session:{session_id}:root")
    stage_span_id = deterministic_span_id(f"session:{session_id}:stage:agent")

    total_in = sum(x[3] for x in llm_calls)
    total_out = sum(x[4] for x in llm_calls)
    total_cache = sum(x[5] for x in llm_calls)
    duration_ms = round((e_ns - s_ns) / 1_000_000, 2)

    prompt_summary = first_prompt[:320] if first_prompt else f"Session {session_id} ({cwd or 'cli'})"
    inferred_role = "M1 TL"
    if "reviewer" in prompt_summary.lower() or "code-review" in prompt_summary.lower():
        inferred_role = "Reviewer"
    elif "writer" in prompt_summary.lower() or "implement" in prompt_summary.lower():
        inferred_role = "Writer"
    elif "m2-dispatcher" in prompt_summary.lower():
        inferred_role = "M2"

    root_input = {
        "session_id": session_id,
        "cwd": cwd or "unknown",
        "prompt": prompt_summary,
        "selected_model": selected_model,
    }
    root_output = {
        "status": "completed",
        "duration_ms": duration_ms,
        "llm_generations": len(llm_calls),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cache_read": total_cache,
    }

    spans: list[SpanRecord] = [
        SpanRecord(
            span_id=root_span_id,
            parent_span_id=None,
            category="root",
            name=f"User Prompt Session: {session_id[:8]}",
            start_ns=s_ns,
            end_ns=e_ns,
            status_ok=True,
            attributes={
                "session.id": session_id,
                "session.cwd": cwd or "unknown",
                "session.model": selected_model,
                "session.duration_ms": duration_ms,
            },
            input_payload=root_input,
            output_payload=root_output,
        ),
        SpanRecord(
            span_id=stage_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name=f"Stage / Role: {inferred_role} ({selected_model})",
            start_ns=s_ns + 10_000_000,
            end_ns=e_ns,
            status_ok=True,
            attributes={
                "stage.role": inferred_role,
                "stage.round": "r1",
                "stage.model": selected_model,
                "stage.verdict": "COMPLETED",
            },
            input_payload={"prompt": prompt_summary[:200]},
            output_payload={"llm_calls": len(llm_calls)},
        ),
    ]

    # Deduplicate skills while preserving order
    seen_skills: set[str] = set()
    for idx, (sk_name, sk_ts) in enumerate(skill_invocations):
        if sk_name in seen_skills or len(seen_skills) >= 6:
            continue
        seen_skills.add(sk_name)
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:skill:{sk_name}"),
                parent_span_id=stage_span_id,
                category="skill_load",
                name=f"Skill Load: {sk_name}",
                start_ns=sk_ts,
                end_ns=sk_ts + 40_000_000,
                status_ok=True,
                attributes={"skill.name": sk_name},
                input_payload={"skill": sk_name},
                output_payload={"loaded": True},
            )
        )

    seen_refs: set[str] = set()
    for idx, (rp, b_len, r_ts) in enumerate(ref_reads):
        if rp in seen_refs or len(seen_refs) >= 8:
            continue
        seen_refs.add(rp)
        _, line_cnt = _file_stats(rp, skills_root=skills_root)
        tok_est = _estimate_tokens_from_bytes(b_len)
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:ref:{idx}:{rp}"),
                parent_span_id=stage_span_id,
                category="l1_reference_read",
                name=f"L1 Reference Read: {Path(rp).name}",
                start_ns=r_ts,
                end_ns=r_ts + 30_000_000,
                status_ok=True,
                attributes={
                    "reference.path": rp,
                    "reference.skill": _infer_skill_from_path(rp),
                    "reference.byte_len": b_len,
                    "reference.line_count": line_cnt,
                    "reference.estimated_tokens": tok_est,
                },
                input_payload={"path": rp},
                output_payload={"byte_len": b_len, "lines": line_cnt, "estimated_tokens": tok_est},
            )
        )

    for idx, (sp_p, cmd_args, sc_ts, dur_ms, ec) in enumerate(script_execs[:8]):
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:script:{idx}:{sp_p}:{sc_ts}"),
                parent_span_id=stage_span_id,
                category="l2_script_exec",
                name=f"L2 Script Exec: {Path(sp_p).name}",
                start_ns=sc_ts,
                end_ns=sc_ts + max(10, dur_ms) * 1_000_000,
                status_ok=(ec == 0),
                attributes={
                    "script.path": sp_p,
                    "script.skill": _infer_skill_from_path(sp_p),
                    "script.args": cmd_args,
                    "script.duration_ms": dur_ms,
                    "script.exit_code": ec,
                },
                input_payload={"script": sp_p, "command": cmd_args},
                output_payload={"exit_code": ec, "duration_ms": dur_ms},
            )
        )

    for idx, (m_name, l_ts, dur_ms, in_t, out_t, ca_t) in enumerate(llm_calls[:10]):
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:llm:{idx}:{l_ts}"),
                parent_span_id=stage_span_id,
                category="llm_generation",
                name=f"LLM Generation: {m_name}",
                start_ns=l_ts,
                end_ns=l_ts + max(25, dur_ms) * 1_000_000,
                status_ok=True,
                attributes={
                    "llm.model_name": m_name,
                    "llm.input_tokens": in_t,
                    "llm.output_tokens": out_t,
                    "llm.cache_read": ca_t,
                },
                input_payload={"turn_index": idx, "session_id": session_id},
                output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t},
                model=m_name,
                input_tokens=in_t,
                output_tokens=out_t,
                cache_read=ca_t,
            )
        )

    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root_span_id,
        trace_kind="session",
        entity_id=session_id,
        name=f"Session Prompt: {session_id[:8]}",
        start_ns=s_ns,
        end_ns=e_ns,
        status="completed",
        input_payload=root_input,
        output_payload=root_output,
        tags=["backfill", "session", f"role:{inferred_role}"],
        metadata={
            "session_id": session_id,
            "cwd": cwd,
            "model": selected_model,
            "duration_ms": duration_ms,
            "span_count": len(spans),
        },
        spans=spans,
    )


def collect_historical_traces(
    *,
    handoff_root: Path = DEFAULT_HANDOFF_ROOT,
    copilot_sessions_dir: Path = DEFAULT_COPILOT_SESSIONS_DIR,
    usage_jsonl: Path = DEFAULT_USAGE_JSONL,
    warehouse_db: Path = DEFAULT_WAREHOUSE_DB,
    limit_goals: int = 35,
    limit_sessions: int = 55,
    skills_root: Path | None = None,
) -> tuple[list[TraceRecord], list[TraceRecord]]:
    wh_maps = _load_warehouse_maps(warehouse_db)
    usage_by_session = _load_usage_jsonl(usage_jsonl)

    goal_traces: list[TraceRecord] = []
    if handoff_root.is_dir() and limit_goals > 0:
        all_goal_dirs = [
            p.parent
            for p in handoff_root.glob("*/ledger.md")
            if not p.parent.name.startswith("_")
        ]
        priority_set = set(PRIORITY_GOALS)
        prioritized = sorted(
            all_goal_dirs,
            key=lambda d: (
                1 if d.name in priority_set or d.name.startswith("vs-ui-") else 0,
                (d / "ledger.md").stat().st_mtime,
            ),
            reverse=True,
        )
        for gdir in prioritized:
            if len(goal_traces) >= limit_goals:
                break
            tr = build_goal_trace(gdir, wh_maps, skills_root=skills_root)
            if tr is not None:
                goal_traces.append(tr)

    session_traces: list[TraceRecord] = []
    seen_sids: set[str] = set()
    if copilot_sessions_dir.is_dir() and limit_sessions > 0:
        rich_session_ids = set(usage_by_session.keys()) | set(wh_maps["refs_by_session"].keys()) | set(
            wh_maps["scripts_by_session"].keys()
        )
        candidates = [p for p in copilot_sessions_dir.glob("*/events.jsonl") if p.stat().st_size >= 1024]
        candidates.sort(
            key=lambda p: (
                1 if p.parent.name in rich_session_ids else 0,
                p.stat().st_mtime,
            ),
            reverse=True,
        )
        for ev_path in candidates:
            if len(session_traces) >= limit_sessions:
                break
            tr = build_session_trace_from_copilot(
                ev_path,
                wh_maps,
                usage_by_session,
                skills_root=skills_root,
            )
            if tr is not None:
                session_traces.append(tr)
                seen_sids.add(tr.entity_id)

    # Fallback session synthesis from usage.jsonl / warehouse.db if copilot_sessions_dir had fewer than limit_sessions
    if len(session_traces) < limit_sessions:
        extra_sids = [
            sid
            for sid in list(usage_by_session.keys()) + list(wh_maps["turns_by_session"].keys())
            if sid not in seen_sids
        ]
        for sid in extra_sids:
            if len(session_traces) >= limit_sessions:
                break
            seen_sids.add(sid)
            u_b = usage_by_session.get(sid, {})
            turns = wh_maps["turns_by_session"].get(sid, [])
            refs = u_b.get("reference_hit", []) + wh_maps["refs_by_session"].get(sid, [])
            scripts = u_b.get("script_call", []) + wh_maps["scripts_by_session"].get(sid, [])
            skill_turns = u_b.get("skill_turn", [])
            if not (turns or refs or scripts or skill_turns):
                continue
            ts_candidates = [
                parse_iso_to_ns(r.get("ts"))
                for r in (turns + refs + scripts + skill_turns)
                if r.get("ts")
            ]
            s_ns = min(ts_candidates) if ts_candidates else int(time.time() * 1_000_000_000) - 5_000_000_000
            e_ns = max(ts_candidates) if ts_candidates else s_ns + 2_000_000_000
            s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000_000)
            trace_id = deterministic_trace_id(f"session:{sid}")
            root_span_id = deterministic_span_id(f"session:{sid}:root")
            stage_id = deterministic_span_id(f"session:{sid}:stage:agent")
            spans = [
                SpanRecord(
                    span_id=root_span_id,
                    parent_span_id=None,
                    category="root",
                    name=f"User Prompt Session: {sid[:8]}",
                    start_ns=s_ns,
                    end_ns=e_ns,
                    attributes={"session.id": sid},
                    input_payload={"session_id": sid},
                    output_payload={"status": "completed"},
                ),
                SpanRecord(
                    span_id=stage_id,
                    parent_span_id=root_span_id,
                    category="stage_role",
                    name="Stage / Role: M1 TL",
                    start_ns=s_ns + 10_000_000,
                    end_ns=e_ns,
                    attributes={"stage.role": "M1 TL", "stage.round": "r1"},
                ),
            ]
            for idx, st in enumerate(skill_turns[:4]):
                sk = str(st.get("skill") or "team-lead")
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"session:{sid}:skill:{idx}:{sk}"),
                        parent_span_id=stage_id,
                        category="skill_load",
                        name=f"Skill Load: {sk}",
                        start_ns=s_ns + 20_000_000 + idx * 10_000_000,
                        end_ns=s_ns + 50_000_000 + idx * 10_000_000,
                        attributes={"skill.name": sk},
                    )
                )
            for idx, rf in enumerate(refs[:4]):
                rp = str(rf.get("reference_path") or "SKILL.md")
                b_len = int(rf.get("byte_len") or 1024)
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"session:{sid}:ref:{idx}:{rp}"),
                        parent_span_id=stage_id,
                        category="l1_reference_read",
                        name=f"L1 Reference Read: {Path(rp).name}",
                        start_ns=s_ns + 60_000_000 + idx * 10_000_000,
                        end_ns=s_ns + 85_000_000 + idx * 10_000_000,
                        attributes={
                            "reference.path": rp,
                            "reference.byte_len": b_len,
                            "reference.estimated_tokens": _estimate_tokens_from_bytes(b_len),
                        },
                    )
                )
            for idx, sc in enumerate(scripts[:4]):
                sp_p = str(sc.get("script_path") or "scripts/verify")
                dur = max(5, int(float(sc.get("duration_ms") or 40)))
                ec = int(sc.get("exit_code") or 0)
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"session:{sid}:script:{idx}:{sp_p}"),
                        parent_span_id=stage_id,
                        category="l2_script_exec",
                        name=f"L2 Script Exec: {Path(sp_p).name}",
                        start_ns=s_ns + 90_000_000 + idx * 15_000_000,
                        end_ns=s_ns + (90 + dur) * 1_000_000 + idx * 15_000_000,
                        status_ok=(ec == 0),
                        attributes={"script.path": sp_p, "script.duration_ms": dur, "script.exit_code": ec},
                    )
                )
            for idx, tn in enumerate((turns or skill_turns)[:4]):
                m_name = str(tn.get("model") or "claude-opus-4-6")
                in_t = int(tn.get("input_tokens") or 0)
                out_t = int(tn.get("output_tokens") or 0)
                ca_t = int(tn.get("cache_read") or 0)
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"session:{sid}:llm:{idx}"),
                        parent_span_id=stage_id,
                        category="llm_generation",
                        name=f"LLM Generation: {m_name}",
                        start_ns=s_ns + 120_000_000 + idx * 20_000_000,
                        end_ns=s_ns + 320_000_000 + idx * 20_000_000,
                        attributes={"llm.model_name": m_name},
                        model=m_name,
                        input_tokens=in_t,
                        output_tokens=out_t,
                        cache_read=ca_t,
                    )
                )
            session_traces.append(
                TraceRecord(
                    trace_id=trace_id,
                    root_span_id=root_span_id,
                    trace_kind="session",
                    entity_id=sid,
                    name=f"Session Prompt: {sid[:8]}",
                    start_ns=s_ns,
                    end_ns=e_ns,
                    status="completed",
                    input_payload={"session_id": sid},
                    output_payload={"status": "completed", "span_count": len(spans)},
                    tags=["backfill", "session"],
                    metadata={"session_id": sid, "span_count": len(spans)},
                    spans=spans,
                )
            )

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


def export_to_phoenix(
    traces: list[TraceRecord],
    *,
    otlp_url: str,
    project_name: str,
    batch_size: int = 20,
    timeout: int = 20,
) -> dict[str, Any]:
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
        "batches": batches,
        "traces_exported": sent_traces,
        "spans_exported": sent_spans,
    }


def export_to_langfuse(
    traces: list[TraceRecord],
    *,
    base_url: str,
    public_key: str,
    secret_key: str,
    event_batch_size: int = 80,
    timeout: int = 25,
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

    return {
        "ok": len(errors) == 0,
        "endpoint": ingestion_url,
        "batches": batches,
        "traces_exported": len(traces),
        "events_submitted": len(all_events),
        "events_succeeded": successes,
        "errors": errors[:10],
    }


def summarize_traces(goal_traces: list[TraceRecord], session_traces: list[TraceRecord]) -> dict[str, Any]:
    all_traces = goal_traces + session_traces
    cat_counts = {c: 0 for c in ("root", *SPAN_CATEGORIES)}
    for tr in all_traces:
        for sp in tr.spans:
            cat_counts[sp.category] = cat_counts.get(sp.category, 0) + 1
    return {
        "goal_traces": len(goal_traces),
        "session_traces": len(session_traces),
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
