#!/usr/bin/env python3
"""Deterministic supervision checks for concurrent Goal handoffs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from jev_client import jev_decide  # noqa: E402


ROLE_TTL_SECONDS = {
    "explorer": 600,
    "pr_monitor": 600,
    "reviewer": 900,
    "designer": 900,
    "writer": 1800,
    "team_lead": 2400,
}
VALID_MODES = {"off", "observe", "auto"}
EVENT_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))"
    r".*?\bEVT=(?P<event>[A-Z0-9_]+)"
)
UNRESOLVED_EVENT_TYPES = {
    "NEED_GO": "decision",
    "NEED_OWNER_DECISION": "decision",
    "TASK_BLOCKED": "blocked",
    "INPUT_BLOCKED": "blocked",
    "REVIEW_FAIL": "review",
    "HANDBACK": "handback",
}


def parse_time(value: str) -> dt.datetime:
    value = value.strip().replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", value):
        value = f"{value[:-2]}:{value[-2:]}"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def json_output(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True))
    else:
        print(json.dumps(value, indent=2, sort_keys=True))


def agent_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get("result", payload)
        if isinstance(value, dict):
            value = value.get("agents", [])
    else:
        value = payload
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def agent_name(agent: dict[str, Any]) -> str:
    return str(agent.get("name") or agent.get("agent_name") or "")


def agent_status(agent: dict[str, Any]) -> str:
    return str(agent.get("agent_status") or agent.get("status") or "").lower()


def role_for(name: str) -> str:
    patterns = (
        ("team_lead", ("-goal-tl-", "-team-lead-")),
        ("pr_monitor", ("-prmon-", "-pr-monitor-")),
        ("reviewer", ("-rev-", "-reviewer-")),
        ("designer", ("-des-", "-designer-")),
        ("explorer", ("-exp-", "-explorer-")),
        ("writer", ("-writer-",)),
    )
    for role, markers in patterns:
        if any(marker in name for marker in markers):
            return role
    return "reviewer"


def goal_events(ledger: Path) -> list[tuple[dt.datetime, str]]:
    events: list[tuple[dt.datetime, str]] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        match = EVENT_RE.search(line)
        if match:
            events.append((parse_time(match.group("timestamp")), match.group("event")))
    return events


def receipt_exists(goal: Path, name: str) -> bool:
    receipts = goal / "receipts"
    if not receipts.is_dir():
        return False
    role = role_for(name)
    return any(
        path.is_file() and (name in path.name or role in path.name)
        for path in receipts.glob("*")
    )


def load_agents(path: Path | None) -> list[dict[str, Any]]:
    if path:
        return agent_items(json.loads(path.read_text(encoding="utf-8")))
    try:
        result = subprocess.run(
            ["herdr", "agent", "list"], capture_output=True, text=True, check=True
        )
        return agent_items(json.loads(result.stdout))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return []


TL_MARKERS = ("-goal-tl-", "-team-lead-")
_CWD_KEYS = ("cwd", "workdir", "work_dir", "working_directory", "pane_cwd")


def agent_cwd(agent: dict[str, Any]) -> str:
    for key in _CWD_KEYS:
        value = agent.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    pane = agent.get("pane")
    if isinstance(pane, dict):
        for key in _CWD_KEYS:
            value = pane.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def ledger_tl_name(text: str) -> str:
    for line in text.splitlines():
        if "TL:" in line:
            token = line.split("TL:", 1)[1].strip().split()
            return token[0] if token else ""
    return ""


def ledger_agent_names(text: str) -> set[str]:
    names: set[str] = set()
    tl_name = ledger_tl_name(text)
    if tl_name:
        names.add(tl_name)
    for match in re.finditer(r"\b(?:from|to)=([a-z][a-z0-9_-]{0,31})\b", text):
        token = match.group(1)
        if "-" in token:
            names.add(token)
    return names


def tl_stem(name: str) -> str:
    for marker in TL_MARKERS:
        index = name.find(marker)
        if index > 0:
            return name[:index]
    return ""


def hyphen_prefixed(name: str, prefix: str) -> bool:
    return bool(prefix) and (name == prefix or name.startswith(prefix + "-"))


def stem_matches_goal(stem: str, goal_name: str) -> bool:
    """True when `stem` is the goal folder or a same-shape truncation of its last segment.

    `good-des` matches folder `good-designer`. Dropping a whole segment (`good`)
    does not, and a last-segment prefix shorter than 3 characters does not.
    """
    if not stem or not goal_name:
        return False
    if stem == goal_name:
        return True
    stem_parts = stem.split("-")
    goal_parts = goal_name.split("-")
    if len(stem_parts) != len(goal_parts) or stem_parts[:-1] != goal_parts[:-1]:
        return False
    last_stem, last_goal = stem_parts[-1], goal_parts[-1]
    return len(last_stem) >= 3 and last_goal.startswith(last_stem)


def cwd_within(cwd: str, goal: Path) -> bool:
    if not cwd:
        return False
    try:
        resolved_cwd = Path(cwd).expanduser().resolve()
        resolved_goal = goal.expanduser().resolve()
    except OSError:
        return False
    return resolved_cwd == resolved_goal or resolved_goal in resolved_cwd.parents


def unique_name(hits: list[str]) -> str:
    if len(hits) == 1:
        return hits[0]
    return ""


def choose_goal_name(name: str, cwd: str, goals: list[dict[str, Any]]) -> str:
    """Bind one agent to one goal without using the folder name as the only key."""
    cwd_hits = [goal["name"] for goal in goals if cwd_within(cwd, goal["path"])]
    if len(cwd_hits) == 1:
        return cwd_hits[0]
    if len(cwd_hits) > 1:
        return max(cwd_hits, key=len)
    if not name:
        return ""
    exact = [
        goal["name"]
        for goal in goals
        if name == goal["tl_name"] or name in goal["ledger_names"]
    ]
    chosen = unique_name(exact)
    if chosen:
        return chosen
    stem_hits = [
        goal["name"]
        for goal in goals
        if any(hyphen_prefixed(name, stem) for stem in goal["stems"])
    ]
    chosen = unique_name(stem_hits)
    if chosen:
        return chosen
    prefix_hits = [goal["name"] for goal in goals if goal["name"] and name.startswith(goal["name"])]
    if prefix_hits:
        return max(prefix_hits, key=len)
    return ""


def associate_agent_to_goal(name: str, cwd: str, goals: list[dict[str, Any]]) -> str:
    """Use the supervisor's normal name/cwd association for bridge events."""
    return choose_goal_name(name, cwd, goals)


def bind_live_tl(name: str, cwd: str, goals: list[dict[str, Any]]) -> str:
    """Attach a shortened `<stem>-goal-tl-*` agent to the Goal folder it abbreviates."""
    stem = tl_stem(name)
    if not stem:
        return ""
    exact_stem = [goal["name"] for goal in goals if goal["name"] == stem]
    if len(exact_stem) == 1:
        return exact_stem[0]
    truncated = [goal["name"] for goal in goals if stem_matches_goal(stem, goal["name"])]
    chosen = unique_name(truncated)
    if chosen:
        return chosen
    return choose_goal_name(name, cwd, goals)


def goal_metadata(root: Path) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for ledger in sorted(root.glob("*/ledger.md")):
        events = goal_events(ledger)
        if any(event == "GOAL_DONE" for _, event in events):
            continue
        text = ledger.read_text(encoding="utf-8")
        tl_name = ledger_tl_name(text)
        stems: set[str] = set()
        recorded_stem = tl_stem(tl_name)
        if recorded_stem:
            stems.add(recorded_stem)
        latest = (
            max(timestamp for timestamp, _ in events)
            if events
            else dt.datetime.fromtimestamp(ledger.stat().st_mtime, tz=dt.timezone.utc)
        )
        metadata.append(
            {
                "path": ledger.parent,
                "name": ledger.parent.name,
                "tl_name": tl_name,
                "ledger_names": ledger_agent_names(text),
                "stems": stems,
                "events": events,
                "latest": latest,
            }
        )
    return metadata


def nested_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    pending = [payload]
    while pending:
        current = pending.pop(0)
        values.append(current)
        for key in ("params", "result", "data", "agent", "pane"):
            value = current.get(key)
            if isinstance(value, dict):
                pending.append(value)
    return values


def event_type(event: dict[str, Any]) -> str:
    candidates: list[str] = []
    for payload in nested_values(event):
        for key in ("type", "event", "method"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
    if "pane.agent_status_changed" in candidates:
        return "pane.agent_status_changed"
    return candidates[0] if candidates else ""


def event_value(event: dict[str, Any], *keys: str) -> Any:
    for payload in nested_values(event):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return None


def event_agent_record(event: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for payload in nested_values(event):
        for key in (
            "name", "agent", "agent_name", "agent_status", "status",
            "cwd", "workdir", "work_dir", "working_directory", "pane_cwd",
            "pane_id", "workspace_id", "visible", "visible_text",
        ):
            if payload.get(key) not in (None, ""):
                record[key] = payload[key]
    pane = event_value(event, "pane")
    if isinstance(pane, str):
        record["pane_id"] = record.get("pane_id") or pane
    elif isinstance(pane, dict):
        record["pane_id"] = record.get("pane_id") or pane.get("id") or pane.get("pane_id")
        record["workspace_id"] = record.get("workspace_id") or pane.get("workspace_id")
        for key in _CWD_KEYS:
            if pane.get(key):
                record["cwd"] = pane[key]
    record["name"] = str(record.get("name") or record.get("agent_name") or record.get("agent") or "")
    record["agent_status"] = str(record.get("agent_status") or record.get("status") or "").lower()
    record["cwd"] = str(record.get("cwd") or "")
    record["pane_id"] = str(record.get("pane_id") or "")
    return record


def visible_text(event: dict[str, Any]) -> str:
    value = event_value(event, "visible_text", "visible", "prompt_excerpt", "excerpt")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else ""


def read_visible_from_herdr(agent_name_value: str) -> str:
    if not agent_name_value:
        return ""
    try:
        result = subprocess.run(
            ["herdr", "agent", "read", agent_name_value, "--source", "visible", "--lines", "40"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def coordinator_bot(goal: Path) -> str:
    try:
        text = (goal / "ledger.md").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r"协调者\s*bot\s*[:：]\s*([^\s|]+)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"coordinator[_ ]bot\s*[:：]\s*([^\s|]+)", text, re.IGNORECASE)
    return match.group(1) if match else "unknown"


def compact_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_blocked_alert(
    handoff_root: Path,
    goal_name: str,
    agent: dict[str, Any],
    trigger: str,
    visible: str,
    now: dt.datetime,
) -> str:
    goal = handoff_root / goal_name
    name = str(agent.get("name") or "unknown")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "unknown"
    timestamp = now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    pane_value = agent.get("pane_id") or agent.get("pane")
    pane = (
        str(pane_value.get("id") or pane_value.get("pane_id") or "unknown")
        if isinstance(pane_value, dict)
        else str(pane_value or "unknown")
    )
    workspace_value = agent.get("workspace_id")
    if not workspace_value and isinstance(pane_value, dict):
        workspace_value = pane_value.get("workspace_id")
    workspace = str(workspace_value or (pane.split(":", 1)[0] if ":" in pane else "unknown"))
    cwd = str(agent.get("cwd") or "")
    digest = hashlib.sha256(visible.encode("utf-8")).hexdigest()
    destination = goal / "alerts" / f"blocked-{safe_name}-{compact_timestamp(now)}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        f"# Blocked agent alert\n\n"
        f"- agent: `{name}`\n- goal: `{goal_name}`\n"
        f"- coordinator_bot: `{coordinator_bot(goal)}`\n- trigger: `{trigger}`\n"
        f"- pane: `{pane}`\n- workspace: `{workspace}`\n- cwd: `{cwd}`\n"
        f"- timestamp: `{timestamp}`\n- visible_sha256: `{digest}`\n\n"
        "## Visible prompt excerpt\n\n```text\n"
        f"{visible}\n```\n\n## Suggested commands\n\n"
        f"- `herdr agent get {name}`\n"
        f"- `herdr agent read {name} --source visible --lines 40`\n"
        f"- `herdr workspace close {workspace}`\n\n"
        "Notification only: the coordinator Bot must inspect this alert and make any "
        "manual/policy decision. 通知桥 ≠ 解卡；严禁 auto-Enter。"
        " GOAL_DONE 关窗与 blocked alerts 两条独立。\n",
        encoding="utf-8",
    )
    return str(destination)


def load_json_input(value: str) -> Any:
    path = Path(value).expanduser()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else json.loads(value)


def resolve_supervisor_mode(
    explicit_mode: str | None,
    write_alerts: bool = False,
    default: str = "observe",
) -> str:
    value = explicit_mode
    if value is None:
        value = os.environ.get("GTT_SUPERVISOR_MODE", "").strip().lower() or None
    if value is None:
        value = "auto" if write_alerts else default
    if value not in VALID_MODES:
        raise ValueError(f"invalid supervisor mode: {value}")
    return value


def _alert_target(root: Path, goal_name: str, agent: dict[str, Any], now: dt.datetime) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(agent.get("name") or "unknown")).strip("-") or "unknown"
    return str(root / goal_name / "alerts" / f"blocked-{name}-{compact_timestamp(now)}.md")


def _decorate_anomaly(
    anomaly: dict[str, Any],
    *,
    mode: str,
    write_alerts: bool,
    root: Path,
    now: dt.datetime,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(anomaly)
    kind = str(result.get("type", "")).lower()
    result.update({"strength": "inferred", "matched_rule": f"supervisor.{kind}"})
    goal_name = str(result.get("goal_id") or "")
    if mode == "observe":
        result.update(
            {
                "decision": "observe (would accept)",
                "would_accept": True,
                "alert_path": "",
                "proposed_action": {
                    "kind": "write_blocked_alert" if result.get("type") == "BLOCKED_AGENT" else "notify_tl",
                    "coordinator_bot": coordinator_bot(root / goal_name),
                },
            }
        )
        if agent is not None:
            result["proposed_action"]["target_path"] = _alert_target(root, goal_name, agent, now)
            result["proposed_action"]["suggested_command"] = (
                f"herdr agent read {agent.get('name', 'unknown')} --source visible --lines 40"
            )
        return result
    result.update({"decision": "accept", "would_accept": True})
    return result


def _number_from_answer(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("noul", "probability", "confidence", "score"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
    return 0.0


def evaluate_stuck_loop(
    agent_name_value: str,
    role: str,
    visible: str,
    *,
    jev_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    explicit = bool(re.search(r"\b(?:stuck|looping|repeating identical failure)\b", visible, re.I))
    repeated = any(lines[index] == lines[index - 1] == lines[index - 2] for index in range(2, len(lines)))
    failure_re = re.compile(r"(?:error|failed|failure|traceback|command not found|exit code [1-9])", re.I)
    repeated_failure = repeated and sum(bool(failure_re.search(line)) for line in lines[-5:]) >= 3
    if explicit or repeated_failure:
        return {
            "type": "STUCK_LOOP",
            "goal_id": "",
            "agent": agent_name_value,
            "role": role,
            "repeating_prob": 1.0,
            "progress_prob": 0.0,
        }
    answers = jev_override
    if answers is None:
        answers = jev_decide(
            visible,
            {
                "repeating_identical_failure": {"type": "noul"},
                "making_forward_progress": {"type": "noul"},
                "violating_role_boundary": {"type": "noul"},
            },
            agent_role=role,
        ) or {}
    repeat_prob = _number_from_answer(answers.get("repeating_identical_failure"))
    progress_prob = _number_from_answer(answers.get("making_forward_progress"))
    boundary_prob = _number_from_answer(answers.get("violating_role_boundary"))
    if boundary_prob >= 0.85:
        return {
            "type": "BOUNDARY_VIOLATION",
            "goal_id": "",
            "agent": agent_name_value,
            "role": role,
            "repeating_prob": repeat_prob,
            "progress_prob": progress_prob,
        }
    if repeat_prob >= 0.80 and progress_prob < 0.25:
        return {
            "type": "STUCK_LOOP",
            "goal_id": "",
            "agent": agent_name_value,
            "role": role,
            "repeating_prob": max(repeat_prob, 1.0 if repeated_failure or explicit else 0.0),
            "progress_prob": progress_prob,
        }
    return None


def blocked_event(event: dict[str, Any]) -> bool:
    statuses = [
        str(payload.get(key)).lower()
        for payload in nested_values(event)
        for key in ("status", "agent_status")
        if payload.get(key) not in (None, "")
    ]
    return event_type(event) == "pane.agent_status_changed" and "blocked" in statuses


def notify_bridge(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = Path(args.handoff_dir).expanduser() if args.handoff_dir else None
    goal_hint = handoff_dir.name if handoff_dir and (handoff_dir / "ledger.md").is_file() else ""
    root = Path(args.handoff_root or (handoff_dir.parent if goal_hint else handoff_dir) or "~/Development/handoff").expanduser()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    mode = resolve_supervisor_mode(getattr(args, "mode", None), default="auto")
    if mode == "off":
        return {
            "written": False, "mode": "off", "decision": "off (skipped)",
            "would_accept": False, "alert_path": "", "status": "", "goal": "", "agent": "",
        }
    events: list[dict[str, Any]] = []
    if args.event_json:
        payload = load_json_input(args.event_json)
        events.extend(payload if isinstance(payload, list) else [payload])
    if args.events_jsonl:
        for line in Path(args.events_jsonl).expanduser().read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                events.extend(payload if isinstance(payload, list) else [payload])
    if args.socket:
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "events.subscribe",
            "params": {"subscriptions": [{"type": "pane.agent_status_changed"}]},
        }
        deadline = time.monotonic() + max(0.0, args.timeout)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(max(0.1, args.timeout))
            connection.connect(args.socket)
            connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
            buffer = ""
            while len(events) < args.max_events and time.monotonic() < deadline:
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")
                lines = buffer.split("\n")
                buffer = lines.pop()
                for line in lines:
                    if line.strip():
                        payload = json.loads(line)
                        events.extend(payload if isinstance(payload, list) else [payload])
                        if len(events) >= args.max_events:
                            break
    agents = load_agents(Path(args.herdr_json).expanduser() if args.herdr_json else None)
    goals = goal_metadata(root)
    for item in agents:
        item["_goal"] = associate_agent_to_goal(agent_name(item), agent_cwd(item), goals)
    last_unmatched: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict) or not blocked_event(event):
            continue
        agent = event_agent_record(event)
        matching = next((item for item in agents if agent["name"] == agent_name(item)), None)
        if matching:
            merged = dict(matching)
            merged.update({key: value for key, value in agent.items() if value})
            agent = merged
        goal_name = associate_agent_to_goal(agent["name"], agent.get("cwd", ""), goals)
        if not goal_name:
            goal_name = next((str(item.get("_goal")) for item in agents if item.get("name") == agent["name"] and item.get("_goal")), "")
        if not goal_name and goal_hint:
            goal_name = goal_hint
        if not goal_name:
            last_unmatched = {
                "written": False,
                "alert_path": "",
                "status": "blocked",
                "goal": "",
                "agent": agent["name"],
                "mode": mode,
            }
            continue
        excerpt = args.visible_text if args.visible_text is not None else visible_text(event)
        if not excerpt and not args.herdr_json:
            excerpt = read_visible_from_herdr(agent["name"])
        if mode == "observe":
            return {
                "written": False,
                "mode": "observe",
                "decision": "observe (would accept)",
                "would_accept": True,
                "proposed_alert_path": _alert_target(root, goal_name, agent, now),
                "status": "blocked",
                "goal": goal_name,
                "agent": agent["name"],
            }
        path = write_blocked_alert(root, goal_name, agent, "pane.agent_status_changed", excerpt, now)
        return {
            "written": True, "mode": mode, "decision": "accept", "would_accept": True,
            "alert_path": path, "status": "blocked", "goal": goal_name, "agent": agent["name"],
        }
    if last_unmatched is not None:
        return last_unmatched
    return {"written": False, "mode": mode, "alert_path": "", "status": "", "goal": "", "agent": ""}


def scan(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root).expanduser()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    mode = resolve_supervisor_mode(
        getattr(args, "mode", None), getattr(args, "write_alerts", False)
    )
    transcripts: dict[str, Any] = {}
    if getattr(args, "transcripts_json", None):
        payload = load_json_input(args.transcripts_json)
        transcripts = payload if isinstance(payload, dict) else {}
    jev_response: dict[str, Any] = {}
    if getattr(args, "jev_response_json", None):
        payload = load_json_input(args.jev_response_json)
        jev_response = payload if isinstance(payload, dict) else {}
    agents = load_agents(Path(args.herdr_json) if args.herdr_json else None)
    goals_meta = goal_metadata(root)
    if mode == "off":
        return {
            "mode": mode,
            "goals": [
                {
                    "goal_id": item["name"],
                    "latest_event_ts": item["latest"].isoformat(),
                    "event_count": len(item["events"]),
                    "tl": item["tl_name"],
                    "agents": [],
                }
                for item in goals_meta
            ],
            "anomalies": [],
            "healthy": True,
        }

    # Record shortened live TL stems (`good-des-goal-tl-r1` → folder `good-designer`)
    # before binding siblings that share that stem (`good-des-*`).
    tl_binding: dict[str, str] = {}
    for item in agents:
        name = agent_name(item)
        chosen = bind_live_tl(name, agent_cwd(item), goals_meta)
        if not chosen:
            continue
        tl_binding[name] = chosen
        stem = tl_stem(name)
        if stem:
            for goal in goals_meta:
                if goal["name"] == chosen:
                    goal["stems"].add(stem)

    assigned: dict[str, list[dict[str, Any]]] = {goal["name"]: [] for goal in goals_meta}
    for item in agents:
        name = agent_name(item)
        if name in tl_binding:
            assigned[tl_binding[name]].append(item)
            continue
        chosen = choose_goal_name(name, agent_cwd(item), goals_meta)
        if chosen:
            assigned[chosen].append(item)

    goals: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for meta in goals_meta:
        goal = meta["path"]
        latest = meta["latest"]
        tl_name = meta["tl_name"]
        goal_agents = assigned[meta["name"]]
        goals.append(
            {
                "goal_id": goal.name,
                "latest_event_ts": latest.isoformat(),
                "event_count": len(meta["events"]),
                "tl": tl_name,
                "agents": [agent_name(item) for item in goal_agents],
            }
        )
        for item in goal_agents:
            name, status = agent_name(item), agent_status(item)
            role = role_for(name)
            if status == "blocked":
                anomaly: dict[str, Any] = {"type": "BLOCKED_AGENT", "goal_id": goal.name, "agent": name}
                if mode == "auto" and getattr(args, "write_alerts", False):
                    excerpt = str(item.get("visible") or item.get("visible_text") or "")
                    if not excerpt and not args.herdr_json:
                        excerpt = read_visible_from_herdr(name)
                    anomaly["alert_path"] = write_blocked_alert(
                        root, goal.name, item, "goal-supervisor.scan:BLOCKED_AGENT",
                        excerpt, now,
                    )
                anomalies.append(_decorate_anomaly(
                    anomaly, mode=mode, write_alerts=getattr(args, "write_alerts", False),
                    root=root, now=now, agent=item,
                ))
            elapsed = (now - latest).total_seconds()
            if status == "working" and elapsed > ROLE_TTL_SECONDS[role] and not receipt_exists(goal, name):
                anomalies.append(_decorate_anomaly(
                    {
                        "type": "ZOMBIE_LEASE", "goal_id": goal.name, "agent": name,
                        "role": role, "elapsed_sec": int(elapsed), "ttl_sec": ROLE_TTL_SECONDS[role],
                    },
                    mode=mode, write_alerts=getattr(args, "write_alerts", False),
                    root=root, now=now, agent=item,
                ))
            is_tl = role == "team_lead" or (bool(tl_name) and name == tl_name)
            if is_tl and status == "working":
                child_attention = any(
                    other is not item
                    and agent_name(other) not in {name, tl_name}
                    and agent_status(other) in {"blocked", "done", "idle"}
                    for other in goal_agents
                )
                if child_attention:
                    anomalies.append(_decorate_anomaly(
                        {"type": "ORPHAN_TL_WAIT", "goal_id": goal.name, "agent": name},
                        mode=mode, write_alerts=getattr(args, "write_alerts", False),
                        root=root, now=now, agent=item,
                    ))
            transcript = transcripts.get(name, {})
            if isinstance(transcript, dict):
                visible = str(transcript.get("visible") or transcript.get("visible_text") or "")
                override = transcript.get("jev") or jev_response.get(name) or jev_response
            else:
                visible = str(transcript)
                override = jev_response.get(name) or jev_response
            if not visible:
                visible = str(item.get("visible") or item.get("visible_text") or "")
            if not visible and not args.herdr_json:
                visible = read_visible_from_herdr(name)
            if status == "working" and (visible or override):
                stuck = evaluate_stuck_loop(name, role, visible, jev_override=override)
                if stuck:
                    stuck["goal_id"] = goal.name
                    anomalies.append(_decorate_anomaly(
                        stuck, mode=mode, write_alerts=getattr(args, "write_alerts", False),
                        root=root, now=now, agent=item,
                    ))
    return {"mode": mode, "goals": goals, "anomalies": anomalies, "healthy": not anomalies}


def check_pins(args: argparse.Namespace) -> dict[str, Any]:
    data = json.loads(Path(args.pins_file).read_text(encoding="utf-8"))
    results = []
    for consumer in data.get("consumers", []):
        outdated = [
            dependency
            for dependency, expected in data.get("upstreams", {}).items()
            if not str(consumer.get("pins", {}).get(dependency, "")).startswith(str(expected))
        ]
        results.append(
            {
                "repo": consumer.get("repo", ""),
                "status": "OUTDATED_PIN" if outdated else "UP_TO_DATE",
                "outdated": outdated,
            }
        )
    return {"consumers": results, "all_up_to_date": all(item["status"] == "UP_TO_DATE" for item in results)}


DEFAULT_OPERATOR_POLICY_TEMPLATE: dict[str, Any] = {
    "schema_version": 1,
    "policy_id": "dsh-operator-policy-v1",
    "communication": {
        "language": "zh-CN",
        "style": "concise_direct",
        "max_summary_lines": 15,
        "agent_signature": "GTT-TL",
        "mention_handle": "@tianpei",
    },
    "ticket_scope": {
        "auto_ruling_allowed_for_non_redline": True,
        "pause_on_redlines": [
            "core_goal_change",
            "security_privacy_data_loss",
            "ci_gate_or_ruleset_mutation",
            "destructive_operation",
        ],
        "max_inline_files": 3,
        "explorer_noise_file_threshold": 8,
        "foreground_command_max_seconds": 5,
        "out_of_scope_followup_action": "handback",
    },
    "delivery_requirements": {
        "commit_coauthor_trailer": "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        "require_cross_vendor_review": True,
        "require_r10_receipt": True,
        "auto_merge_when_checks_green": True,
        "clean_worktree_on_merge": True,
        "close_workspace_on_goal_done": True,
    },
}
OPERATOR_POLICY_SLOTS = ("communication", "ticket_scope", "delivery_requirements")


def _default_operator_policy_path() -> Path:
    return SCRIPT_DIR.parent / "references" / "operator-policy.json"


def check_operator_policy_compatibility(
    policy_path: Path | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = (policy_path or _default_operator_policy_path()).expanduser()
    base = template or DEFAULT_OPERATOR_POLICY_TEMPLATE
    if not target.is_file():
        return {
            "ok": False,
            "compatible": False,
            "policy_path": str(target),
            "missing_slots": list(OPERATOR_POLICY_SLOTS),
            "missing_keys": {slot: sorted(base[slot].keys()) for slot in OPERATOR_POLICY_SLOTS},
            "type_mismatches": [],
            "custom_overrides": {},
            "merged_policy": dict(base),
        }
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "compatible": False,
            "policy_path": str(target),
            "error": str(exc),
            "missing_slots": list(OPERATOR_POLICY_SLOTS),
            "missing_keys": {},
            "type_mismatches": [],
            "custom_overrides": {},
            "merged_policy": dict(base),
        }
    if not isinstance(loaded, dict):
        return {
            "ok": False,
            "compatible": False,
            "policy_path": str(target),
            "error": "operator-policy.json root must be a JSON object",
            "missing_slots": list(OPERATOR_POLICY_SLOTS),
            "missing_keys": {},
            "type_mismatches": [],
            "custom_overrides": {},
            "merged_policy": dict(base),
        }

    missing_slots: list[str] = []
    missing_keys: dict[str, list[str]] = {}
    type_mismatches: list[dict[str, str]] = []
    custom_overrides: dict[str, dict[str, Any]] = {}
    merged: dict[str, Any] = {
        "schema_version": loaded.get("schema_version", base["schema_version"]),
        "policy_id": loaded.get("policy_id", base["policy_id"]),
    }

    if not isinstance(merged["schema_version"], int) or isinstance(merged["schema_version"], bool):
        type_mismatches.append(
            {
                "path": "schema_version",
                "expected": "int",
                "actual": type(merged["schema_version"]).__name__,
            }
        )

    for slot in OPERATOR_POLICY_SLOTS:
        default_slot = base.get(slot, {})
        actual_slot = loaded.get(slot)
        if not isinstance(actual_slot, dict):
            missing_slots.append(slot)
            merged[slot] = dict(default_slot)
            continue
        merged_slot = dict(default_slot)
        slot_missing: list[str] = []
        slot_overrides: dict[str, Any] = {}
        for key, default_val in default_slot.items():
            if key not in actual_slot:
                slot_missing.append(key)
                continue
            actual_val = actual_slot[key]
            expected_is_bool = isinstance(default_val, bool)
            actual_is_bool = isinstance(actual_val, bool)
            if expected_is_bool != actual_is_bool or not isinstance(actual_val, type(default_val)):
                type_mismatches.append(
                    {
                        "path": f"{slot}.{key}",
                        "expected": type(default_val).__name__,
                        "actual": type(actual_val).__name__,
                    }
                )
                continue
            merged_slot[key] = actual_val
            if actual_val != default_val:
                slot_overrides[key] = actual_val
        for extra_key, extra_val in actual_slot.items():
            if extra_key not in default_slot:
                merged_slot[extra_key] = extra_val
                slot_overrides[extra_key] = extra_val
        if slot_missing:
            missing_keys[slot] = sorted(slot_missing)
        if slot_overrides:
            custom_overrides[slot] = slot_overrides
        merged[slot] = merged_slot

    compatible = not missing_slots and not type_mismatches
    return {
        "ok": compatible,
        "compatible": compatible,
        "policy_path": str(target),
        "schema_version": merged["schema_version"],
        "slots": list(OPERATOR_POLICY_SLOTS),
        "missing_slots": missing_slots,
        "missing_keys": missing_keys,
        "type_mismatches": type_mismatches,
        "custom_overrides": custom_overrides,
        "merged_policy": merged,
    }


def check_operator_policy(args: argparse.Namespace) -> dict[str, Any]:
    policy_path = Path(args.policy_file).expanduser() if getattr(args, "policy_file", None) else None
    template = load_json_input(args.template_file) if getattr(args, "template_file", None) else None
    return check_operator_policy_compatibility(policy_path=policy_path, template=template)


def telemetry(args: argparse.Namespace) -> dict[str, Any]:
    handoff = Path(args.handoff_dir)
    events = goal_events(handoff / "ledger.md")
    names = [event for _, event in events]
    duration = int((events[-1][0] - events[0][0]).total_seconds()) if len(events) > 1 else 0
    result = {
        "goal_id": handoff.name,
        "goal_done": "GOAL_DONE" in names,
        "total_duration_sec": duration,
        "event_count": len(events),
        "unblock_count": names.count("UNBLOCK"),
        "changes_requested_count": sum(name in {"CHANGES_REQUESTED", "REVIEW_FAIL"} for name in names),
        "pr_check_failed_count": names.count("PR_CHECK_FAILED"),
        "receipt_count": len(list((handoff / "receipts").glob("*.md"))),
    }
    result["summary_line"] = (
        f"{result['goal_id']}: {result['event_count']} events, "
        f"{result['total_duration_sec']}s, {result['receipt_count']} receipts, "
        f"goal_done={str(result['goal_done']).lower()}"
    )
    if args.write:
        destination = handoff / "receipts" / "contract-telemetry.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _ledger_attention(goal: Path, now: dt.datetime) -> list[dict[str, Any]]:
    ledger = goal / "ledger.md"
    if not ledger.is_file():
        return []
    lines = ledger.read_text(encoding="utf-8").splitlines()
    closed: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for line in lines:
        event_match = re.search(r"\bEVT=([A-Z0-9_]+)", line)
        if not event_match:
            continue
        event = event_match.group(1)
        event_id_match = re.search(r"\bid=([EB]\d+)\b", line)
        event_id = event_id_match.group(1) if event_id_match else ""
        if event in {"RESOLVED", "ACK"} and event_id:
            closed.add(event_id)
            continue
        if event not in UNRESOLVED_EVENT_TYPES or (event_id and event_id in closed):
            continue
        timestamp_match = EVENT_RE.search(line)
        timestamp = parse_time(timestamp_match.group("timestamp")) if timestamp_match else now
        task_match = re.search(r"\b(?:task|task_id)=([^\s|]+)", line)
        note_match = re.search(r"\bnote=([^|]+)", line)
        note = note_match.group(1).strip() if note_match else line.strip()
        parsed.append(
            {
                "event": event, "event_id": event_id, "task_id": task_match.group(1) if task_match else "",
                "timestamp": timestamp, "note": note,
            }
        )
    return [item for item in parsed if not item["event_id"] or item["event_id"] not in closed]


def _queue_tasks(goal: Path) -> list[dict[str, Any]]:
    queue = goal / "queue.json"
    if not queue.is_file():
        return []
    try:
        payload = json.loads(queue.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tasks = payload.get("tasks", payload) if isinstance(payload, dict) else payload
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


def _attention_item(
    action: str, goal: Path, task_id: str, waits_for: str, summary: str, age_sec: int
) -> dict[str, Any]:
    return {
        "action": action, "goal_id": goal.name, "task_id": task_id, "waits_for": waits_for,
        "summary": summary, "age_sec": max(0, int(age_sec)), "handoff_dir": str(goal),
    }


def status(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root).expanduser()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    mode = resolve_supervisor_mode(getattr(args, "mode", None), default="observe")
    agents = load_agents(Path(args.herdr_json).expanduser() if args.herdr_json else None)
    scan_result = scan(argparse.Namespace(
        handoff_root=str(root), herdr_json=args.herdr_json, now=args.now, write_alerts=False,
        mode=mode, transcripts_json=None, jev_response_json=None,
    ))
    needs: list[dict[str, Any]] = []
    goals = goal_metadata(root)
    for goal in goals:
        tl = goal["tl_name"] or "tl"
        events = _ledger_attention(goal["path"], now)
        for item in events:
            action = UNRESOLVED_EVENT_TYPES[item["event"]]
            waits_for = "owner" if action == "decision" else (tl if action in {"handback", "review"} else coordinator_bot(goal["path"]))
            needs.append(_attention_item(
                action, goal["path"], item["task_id"], waits_for, item["note"],
                int((now - item["timestamp"]).total_seconds()),
            ))
        for task in _queue_tasks(goal["path"]):
            task_status = str(task.get("status", ""))
            if task_status not in {"blocked", "handback"}:
                continue
            action = task_status
            needs.append(_attention_item(
                action, goal["path"], str(task.get("task_id") or task.get("id") or ""),
                tl if action == "handback" else coordinator_bot(goal["path"]),
                str(task.get("note") or task.get("title") or f"{action} task"),
                0,
            ))
        queue_tasks = _queue_tasks(goal["path"])
        if queue_tasks and all(str(task.get("status", "")) == "done" for task in queue_tasks):
            needs.append(_attention_item(
                "review", goal["path"], "", tl,
                "queue drained; review and record GOAL_DONE", 0,
            ))
    for anomaly in scan_result["anomalies"]:
        if anomaly.get("type") not in {"BLOCKED_AGENT", "ZOMBIE_LEASE", "ORPHAN_TL_WAIT", "STUCK_LOOP"}:
            continue
        goal = root / str(anomaly.get("goal_id", ""))
        action = "blocked" if anomaly["type"] == "BLOCKED_AGENT" else "stale"
        needs.append(_attention_item(
            action, goal, "", str(anomaly.get("agent") or "tl"),
            f"{anomaly['type'].lower()} for {anomaly.get('agent', 'unknown')}",
            int(anomaly.get("elapsed_sec", 0)),
        ))
    priority = {"decision": 0, "blocked": 1, "handback": 2, "review": 3, "stale": 4}
    needs.sort(key=lambda item: (priority.get(item["action"], 99), -item["age_sec"], item["goal_id"]))
    running_workers = sum(
        agent_status(item) == "working" and role_for(agent_name(item)) != "team_lead" for item in agents
    )
    queued = sum(
        sum(str(task.get("status")) in {"ready", "queued", "in_progress"} for task in _queue_tasks(goal["path"]))
        for goal in goals
    )
    handbacks = sum(
        sum(str(task.get("status")) == "handback" for task in _queue_tasks(goal["path"]))
        for goal in goals
    )
    system = {
        "active_goals_count": len(goals),
        "healthy_goals_count": max(0, len(goals) - len({item["goal_id"] for item in needs})),
        "running_workers": running_workers,
        "max_workers": int(args.max_workers),
        "human_slot_free": running_workers < int(args.max_workers) + 1,
        "queued_tasks": queued,
        "handback_tasks": handbacks,
        "blocked_agents": sum(item.get("type") == "BLOCKED_AGENT" for item in scan_result["anomalies"]),
        "zombie_leases": sum(item.get("type") == "ZOMBIE_LEASE" for item in scan_result["anomalies"]),
        "orphan_tl_waits": sum(item.get("type") == "ORPHAN_TL_WAIT" for item in scan_result["anomalies"]),
        "operator_policy_compatible": check_operator_policy_compatibility()["compatible"],
    }
    return {
        "command": "status", "mode": mode, "needs_a_person_count": len(needs),
        "needs_a_person": needs, "system": system, "healthy": not needs,
    }


def observe(args: argparse.Namespace) -> dict[str, Any]:
    goal = Path(args.handoff_dir).expanduser()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    agents = load_agents(Path(args.herdr_json).expanduser() if args.herdr_json else None)
    result = scan(argparse.Namespace(
        handoff_root=str(goal.parent), herdr_json=args.herdr_json, now=args.now,
        write_alerts=False, mode="observe", transcripts_json=None, jev_response_json=None,
    ))
    goal_agents = [item for item in agents if choose_goal_name(
        agent_name(item), agent_cwd(item), goal_metadata(goal.parent)
    ) == goal.name]
    queue = _queue_tasks(goal)
    counts: dict[str, int] = {}
    for task in queue:
        key = str(task.get("status", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    active_agents = []
    latest = max((timestamp for timestamp, _ in goal_events(goal / "ledger.md")), default=now)
    for item in goal_agents:
        name = agent_name(item)
        role = role_for(name)
        active_agents.append({
            "name": name, "role": role, "status": agent_status(item),
            "elapsed_sec": max(0, int((now - latest).total_seconds())),
            "ttl_sec": ROLE_TTL_SECONDS[role], "receipt_exists": receipt_exists(goal, name),
        })
    unresolved = _ledger_attention(goal, now)
    for item in unresolved:
        item["timestamp"] = item["timestamp"].isoformat()
    return {
        "goal_id": goal.name,
        "tl": ledger_tl_name((goal / "ledger.md").read_text(encoding="utf-8")) if (goal / "ledger.md").is_file() else "",
        "coordinator_bot": coordinator_bot(goal),
        "spec_sha": hashlib.sha256((goal / "spec.md").read_bytes()).hexdigest() if (goal / "spec.md").is_file() else "",
        "queue_summary": {"counts": counts, "mandate_valid": [bool(task.get("mandate")) for task in queue]},
        "active_agents": active_agents,
        "unresolved_events": unresolved,
        "anomalies": [item for item in result["anomalies"] if item.get("goal_id") == goal.name],
    }


def watch(args: argparse.Namespace) -> dict[str, Any]:
    import time

    handoff = Path(args.handoff_dir).expanduser()
    interval = max(1, int(args.interval))
    max_wait = max(interval, int(args.max_wait))
    started = time.monotonic()
    tl_seen_working = False
    polls = 0
    while True:
        ledger_path = handoff / "ledger.md"
        events = [event for _, event in goal_events(ledger_path)] if ledger_path.is_file() else []
        if "GOAL_DONE" in events:
            return {"goal_id": handoff.name, "reason": "GOAL_DONE", "events": len(events)}
        decision_items = [
            {**item, "timestamp": item["timestamp"].isoformat()}
            for item in _ledger_attention(handoff, dt.datetime.now(dt.timezone.utc))
            if UNRESOLVED_EVENT_TYPES.get(item["event"]) == "decision"
        ]
        if decision_items:
            return {
                "goal_id": handoff.name,
                "reason": "NEED_DECISION",
                "unresolved_events": decision_items,
                "events": len(events),
            }
        scan_args = argparse.Namespace(
            handoff_root=str(handoff.parent),
            herdr_json=args.herdr_json,
            now=None,
            json=True,
            write_alerts=getattr(args, "write_alerts", False),
            mode=getattr(args, "mode", None),
            transcripts_json=None,
            jev_response_json=None,
        )
        scan_result = scan(scan_args)
        goal_anomalies = [a for a in scan_result["anomalies"] if a.get("goal_id") == handoff.name]
        if goal_anomalies:
            return {"goal_id": handoff.name, "reason": "ANOMALY", "anomalies": goal_anomalies}

        agents = load_agents(Path(args.herdr_json).expanduser() if args.herdr_json else None)
        goals_meta = goal_metadata(handoff.parent)
        tl_name = ledger_tl_name(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else ""
        goal_agents: list[dict[str, Any]] = []
        for item in agents:
            name = agent_name(item)
            chosen = bind_live_tl(name, agent_cwd(item), goals_meta) or choose_goal_name(
                name, agent_cwd(item), goals_meta
            )
            if chosen == handoff.name or (tl_name and name == tl_name):
                goal_agents.append(item)

        tl_agent = next(
            (
                item
                for item in goal_agents
                if (tl_name and agent_name(item) == tl_name) or role_for(agent_name(item)) == "team_lead"
            ),
            None,
        )
        if tl_agent is not None:
            resolved_tl_name = agent_name(tl_agent) or tl_name
            tl_status = agent_status(tl_agent)
            if tl_status == "working":
                tl_seen_working = True
            elif tl_status in {"idle", "done"} and (tl_seen_working or polls >= 1):
                visible_working = False
                if not getattr(args, "herdr_json", None):
                    visible_working = bool(re.search(
                        r"(?:[○◎●]\s*Working\b|ctrl\+c\s+interrupt|esc\s+to\s+interrupt)",
                        read_visible_from_herdr(resolved_tl_name),
                        re.IGNORECASE,
                    ))
                if visible_working:
                    tl_seen_working = True
                    tl_status = "working"
                else:
                    other_busy = any(
                        item is not tl_agent and agent_status(item) in {"working", "blocked"}
                        for item in goal_agents
                    )
                    if not other_busy:
                        return {
                            "goal_id": handoff.name,
                            "reason": "TL_IDLE_OR_DONE",
                            "tl": resolved_tl_name,
                            "tl_status": tl_status,
                            "events": len(events),
                        }

        polls += 1
        if time.monotonic() - started >= max_wait:
            return {"goal_id": handoff.name, "reason": "WATCH_SLICE_TIMEOUT", "events": len(events)}
        time.sleep(interval)


def print_status_human(result: dict[str, Any]) -> None:
    print(f"needs a person ({result['needs_a_person_count']})")
    for item in result["needs_a_person"]:
        print(
            f"  {item['action']:<9} {item['goal_id']:<24} {item['task_id']:<12} "
            f"{item['waits_for']}: {item['summary']} ({item['age_sec']}s ago)"
        )
    system = result["system"]
    print("\nsystem")
    print(
        f"  goals     {system['active_goals_count']} active · "
        f"{system['healthy_goals_count']} healthy"
    )
    print(
        f"  workers   {system['running_workers']} running of {system['max_workers']} "
        f"(human slot: {'free' if system['human_slot_free'] else 'busy'})"
    )
    print(
        f"  queues    {system['queued_tasks']} queued · {system['handback_tasks']} handback"
    )
    print(
        f"  leases    {system['blocked_agents']} blocked · {system['zombie_leases']} zombie · "
        f"{system['orphan_tl_waits']} orphan_tl"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--handoff-root", default="~/Development/handoff")
    scan_parser.add_argument("--herdr-json")
    scan_parser.add_argument("--now")
    scan_parser.add_argument("--write-alerts", action="store_true")
    scan_parser.add_argument("--mode", choices=sorted(VALID_MODES))
    scan_parser.add_argument("--transcripts-json")
    scan_parser.add_argument("--jev-response-json")
    scan_parser.add_argument("--json", action="store_true")
    bridge_parser = subparsers.add_parser("notify-bridge")
    bridge_parser.add_argument("--handoff-root")
    bridge_parser.add_argument("--handoff-dir")
    bridge_parser.add_argument("--herdr-json")
    bridge_parser.add_argument("--event-json")
    bridge_parser.add_argument("--events-jsonl")
    bridge_parser.add_argument("--visible-text")
    bridge_parser.add_argument("--now")
    bridge_parser.add_argument("--socket")
    bridge_parser.add_argument("--max-events", type=int, default=1)
    bridge_parser.add_argument("--timeout", type=float, default=5.0)
    bridge_parser.add_argument("--mode", choices=sorted(VALID_MODES))
    bridge_parser.add_argument("--json", action="store_true")
    pins_parser = subparsers.add_parser("check-pins")
    pins_parser.add_argument("--pins-file", required=True)
    pins_parser.add_argument("--json", action="store_true")
    policy_parser = subparsers.add_parser("check-operator-policy")
    policy_parser.add_argument("--policy-file", default="")
    policy_parser.add_argument("--template-file", default="")
    policy_parser.add_argument("--json", action="store_true")
    telemetry_parser = subparsers.add_parser("telemetry")
    telemetry_parser.add_argument("--handoff-dir", required=True)
    telemetry_parser.add_argument("--write", action="store_true")
    telemetry_parser.add_argument("--json", action="store_true")
    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("--handoff-dir", required=True)
    watch_parser.add_argument("--interval", type=int, default=30)
    watch_parser.add_argument("--max-wait", type=int, default=2400)
    watch_parser.add_argument("--herdr-json")
    watch_parser.add_argument("--write-alerts", action="store_true")
    watch_parser.add_argument("--mode", choices=sorted(VALID_MODES))
    watch_parser.add_argument("--json", action="store_true")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--handoff-root", default="~/Development/handoff")
    status_parser.add_argument("--herdr-json")
    status_parser.add_argument("--now")
    status_parser.add_argument("--mode", choices=sorted(VALID_MODES))
    status_parser.add_argument("--max-workers", type=int, default=3)
    status_parser.add_argument("--check", action="store_true")
    status_parser.add_argument("--json", action="store_true")
    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--handoff-dir", required=True)
    observe_parser.add_argument("--herdr-json")
    observe_parser.add_argument("--now")
    observe_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "scan":
        result = scan(args)
    elif args.command == "notify-bridge":
        result = notify_bridge(args)
    elif args.command == "check-pins":
        result = check_pins(args)
    elif args.command == "check-operator-policy":
        result = check_operator_policy(args)
        json_output(result, args.json)
        if not result.get("compatible", False):
            raise SystemExit(1)
        return
    elif args.command == "watch":
        result = watch(args)
    elif args.command == "status":
        result = status(args)
        if args.json:
            json_output(result, True)
        else:
            print_status_human(result)
        if args.check and result["needs_a_person_count"] > 0:
            raise SystemExit(1)
        return
    elif args.command == "observe":
        result = observe(args)
    else:
        result = telemetry(args)
    json_output(result, args.json)


if __name__ == "__main__":
    main()
