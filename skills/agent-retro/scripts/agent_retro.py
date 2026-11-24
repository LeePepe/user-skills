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
MAX_EVENTS = 3000
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
    return session_root, handoff_root, warehouse if warehouse.exists() else None


def _read_events(root: Path, days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = _now() - timedelta(days=max(0, days))
    events: list[dict[str, Any]] = []
    files = sorted(root.glob("*/events.jsonl")) if root.is_dir() else []
    truncated = False
    for path in files[:128]:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index >= MAX_EVENTS:
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
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _handoffs(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not root.is_dir():
        return records, {"directories": 0, "available": False}
    dirs = [p for p in sorted(root.iterdir()) if p.is_dir()][:128]
    for directory in dirs:
        ledger = _safe_text(directory / "ledger.md")
        spec = _safe_text(directory / "spec.md")
        briefs = [p for p in (directory / "briefs").glob("*.md")][:32] if (directory / "briefs").is_dir() else []
        receipts = [p for p in (directory / "receipts").glob("*.md")][:64] if (directory / "receipts").is_dir() else []
        records.append({"path": directory, "name": directory.name, "ledger": ledger, "spec": spec,
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
    return {"metric": metric, "layer": layer, "count": count, "skill": skill or UPSTREAM.get(metric, "unknown"),
            "upstream_skill": UPSTREAM.get(metric, skill or "unknown"), "scenario_sessions": sessions,
            "status": "healthy_progressive_disclosure" if healthy else status,
            "evidence": evidence[:5]}


def _is_code_path(path: str) -> bool:
    personal_root = os.sep + "Users" + os.sep
    handoff_marker = os.sep + "handoff" + os.sep
    return bool(path) and not (path.startswith(personal_root) and handoff_marker in path) and not path.startswith(("handoff/", "briefs/", "receipts/"))


def _metric_scan(events: list[dict[str, Any]], handoffs: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    groups = _session_groups(events)
    # Session-level role and invocation signals.
    for sid, seq in groups.items():
        role = _role_for(seq)
        skills = _skill_names(seq)
        tools = _tool_events(seq)
        files = set()
        for event in tools:
            args = _arguments(event)
            path = str(args.get("path") or args.get("file") or _field(event, "path") or "")
            if path and _is_code_path(path):
                files.add(path)
        text = " ".join(_text(_field(e, "content", "message", "assistantContent", "userContent")) for e in seq).lower()
        if any(token in text for token in ("prompt lock-in", "hot-append", "pre-baked", "prebaked")) and role in ("m3", "unknown"):
            observations.append(_observation("R1_m3_prompt_prelock", "role", 1, [f"{sid}: prompt/pre-lock wording"], sessions=1))
        if role == "m2":
            direct = [e for e in tools if _tool_name(e) in {"edit", "create", "apply_patch", "write"} and _is_code_path(str(_arguments(e).get("path") or _field(e, "path") or ""))]
            if direct:
                observations.append(_observation("R2_m2_direct_code_edit", "role", len(direct), [f"{sid}: {_tool_name(e)} {_arguments(e).get('path', '')}" for e in direct], sessions=1))
        if role == "m1" and len(files) > 3 and not any("writer" in _text(_data(e)).lower() for e in seq):
            observations.append(_observation("R3_m1_inline_overwork", "role", len(files), [f"{sid}: {len(files)} code paths, no Writer"], sessions=1))
        if len(skills) > 3:
            observations.append(_observation("R4_session_skill_pollution", "role", len(skills), [f"{sid}: {', '.join(sorted(skills)[:6])}"], sessions=1))
        models = [str(_field(e, "selectedModel", "model", "usageModel") or "") for e in seq]
        if role == "reviewer" and len({m.split("/")[0] for m in models if m}) == 1 and models:
            observations.append(_observation("R5_reviewer_same_vendor", "role", 1, [f"{sid}: reviewer model family {models[0]}"], sessions=1))
        # I1: first two turns/events read three drawers before a script invocation.
        first = seq[:20]
        drawer_reads = [e for e in first if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e))]
        script_at = next((i for i, e in enumerate(first) if "scripts/" in _command(e)), 999)
        if len(drawer_reads) >= 3 and all(first.index(e) < script_at for e in drawer_reads[:3]):
            observations.append(_observation("I1_cold_start_greedy_drawer_read", "invoke", len(drawer_reads), [f"{sid}: {len(drawer_reads)} reference reads before L2"], sessions=1))
        # I2: explicit skill invocation without any script call for its package.
        invoked = {s for e in seq if (s := _event_skill(e))}
        called_scripts = " ".join(_command(e) for e in tools)
        for skill in invoked:
            if _skill_requires_l2(skill) and f"skills/{skill}/scripts/" not in called_scripts:
                observations.append(_observation("I2_mandatory_l2_cli_skip", "invoke", 1, [f"{sid}: {skill} invoked without its script"], skill=skill, sessions=1))
        # I3, I4 and F signals.
        exit2 = [e for e in tools if _exit_code(e) == 2]
        bloat = [e for e in tools if len(_result_content(e).encode("utf-8")) > 20_000]
        if exit2 or bloat:
            evidence = [f"{sid}: exit=2" for _ in exit2[:2]] + [f"{sid}: tool output >20KB" for _ in bloat[:2]]
            observations.append(_observation("I3_l2_exit2_and_output_bloat", "invoke", len(exit2) + len(bloat), evidence, sessions=1))
        polls = [e for e in tools if re.search(r"(?:herdr\s+pane\s+read|\bsleep\b|gh\s+pr\s+view)", _command(e))]
        if len(polls) >= 3:
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
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", progressive, [f"{sid}: exit=1 -> drawer -> exit=0"], sessions=1, healthy=True))
        if spin >= 3:
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", spin, [f"{sid}: {spin} identical failed retries"], sessions=1))
        rounds = len(re.findall(r"(?:change request|changes requested|review fail|R[0-9]+\s+FAIL)", text))
        if rounds >= 3:
            observations.append(_observation("E2_r3_plus_divergence", "exec", rounds, [f"{sid}: {rounds} review/reset signals"], sessions=1))
        if re.search(r"verify|pytest|swiftlint|scripts/verify", text) and any("PR_CHECK_FAILED" in _text(e) or "ci" in _event_type(e) and "fail" in _text(e).lower() for e in seq):
            observations.append(_observation("E3_gate_escape_and_uncrystallized_spec_gap", "exec", 1, [f"{sid}: local verification followed by CI failure"], sessions=1))
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
        if spec_words and len(spec_words - set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", brief_text))) > 8:
            observations.append(_observation("H4_compaction_and_handoff_loss", "handoff", 1, [f"{record['name']}: spec constraints absent from brief"], sessions=1))
        for name, receipt in record["receipts"]:
            if re.search(r"(?:pytest|swiftlint|scripts/verify|PR_MERGED)", receipt, re.I):
                verified_commands = {_command(e).strip() for e in events if _exit_code(e) == 0 and _command(e).strip()}
                claims: list[str] = []
                for line in receipt.splitlines():
                    match = re.search(r"(?:`|^\s*[-*]?\s*)(python3\s+[^`\n]+|pytest\s+[^`\n]+|swiftlint\s+[^`\n]+|(?:\./)?scripts/verify(?:\s+[^`\n]+)?)", line, re.I)
                    if match:
                        claims.append(re.sub(r"\s+", " ", match.group(1).strip()))
                missing = [claim for claim in claims if claim not in verified_commands]
                merge_claimed = bool(re.search(r"\bPR_MERGED\b", receipt))
                merge_grounded = any("merge" in _event_type(e) or "PR_MERGED" in _text(e) for e in events)
                if missing or (merge_claimed and not merge_grounded):
                    observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", len(missing) or 1, [f"{record['name']}/{name}: receipt lacks exact exit=0 trace"], sessions=1))
    # CI cancellation is not a failure; remove false positive evidence from raw scan by only emitting when explicit failure.
    for e in events:
        blob = _text(e).upper()
        if "CANCELLED" in blob and "PR_CHECK_FAILED" in blob:
            observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", 1, ["CANCELLED run was classified as PR_CHECK_FAILED"], sessions=1))
    return observations


def _version_watermark() -> dict[str, Any]:
    try:
        count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip())
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError, ValueError):
        count, sha = 0, "unknown"
    return {"current": f"v{count}@{sha}", "pre_fix": 0, "post_fix": 0, "transition": f"v{max(0, count - 1)}@previous -> v{count}@{sha}"}


def _scan(args: argparse.Namespace) -> dict[str, Any]:
    sessions, handoff_root, warehouse = _roots(args)
    events, event_source = _read_events(sessions, int(getattr(args, "days", 30) or 30))
    handoffs, handoff_source = _handoffs(handoff_root)
    observations = _metric_scan(events, handoffs, event_source)
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
            "version_watermark": _version_watermark(), "metrics": metrics, "observations": actionable[:100],
            "healthy_exemptions": [x for x in observations if x["status"] == "healthy_progressive_disclosure"],
            "observation_pool": [x for x in actionable if x["count"] < 3], "k_cap": min(3, len({x['upstream_skill'] for x in actionable}))}


def _compact_print(payload: Any, as_json: bool, summary: bool = False) -> None:
    if as_json:
        text = json.dumps(payload, ensure_ascii=False, indent=None if summary else 2)
    else:
        if isinstance(payload, dict):
            text = "\n".join([f"agent-retro: {payload.get('schema', 'ok')}", f"observations={len(payload.get('observations', []))} k={payload.get('k_cap', 0)}", "unknown sources remain unknown; no issue was published."])
        else:
            text = _text(payload)
    if len(text) > MAX_OUTPUT and isinstance(payload, dict):
        payload = dict(payload)
        payload["observations"] = payload.get("observations", [])[:10]
        payload["truncated_output"] = True
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if as_json else text[:MAX_OUTPUT - 40] + "\n... output truncated"
    print(text)


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
    path.write_text(text, encoding="utf-8")
    return True


def _selected(scan: dict[str, Any], pick_top: int = 3, skill: str | None = None) -> list[dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    priority = {"E4_unfaithful_receipt_and_cancelled_noise": 100, "R2_m2_direct_code_edit": 95, "I3_l2_exit2_and_output_bloat": 90, "E1_blind_spin_loop_vs_progressive_catch": 80}
    for item in scan.get("observations", []):
        if skill and item.get("upstream_skill") != skill and item.get("skill") != skill:
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
    return {"schema": "agent-retro-emit-briefs/v1", "ok": True, "k": len(emitted), "emitted": emitted, "honest_cap": 3}


def _receipts(root: Path) -> list[dict[str, Any]]:
    rows = []
    if not root.is_dir():
        return rows
    for path in root.glob("**/receipts/*.md"):
        text = _safe_text(path, 100_000)
        rows.append({"path": str(path), "status": next((s for s in ("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY") if s in text), "UNKNOWN"), "text": text[:2000]})
    return rows[:100]


def command_rollup(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    scan = _scan(argparse.Namespace(days=30, role=None, layer=None, fast=True, handoff_root=str(root), handoff_dir=None))
    receipts = _receipts(root)
    day = args.date or datetime.now().date().isoformat()
    payload = {"schema": "agent-retro-rollup/v1", "date": day, "observations": scan["observations"], "metrics": scan["metrics"], "receipts": receipts, "observation_pool": scan["observation_pool"], "version_watermark": scan["version_watermark"]}
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
    p = sub.add_parser("file-issue", help="prepare or explicitly apply an issue publication"); _common(p); p.add_argument("--date"); p.add_argument("--server", default=""); p.add_argument("--workspace", default=""); p.add_argument("--owner", default=""); p.add_argument("--agent", default="agent-retro"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("setup-autopilot", help="prepare the independent run_only schedule"); _common(p); p.add_argument("--apply", action="store_true")
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
