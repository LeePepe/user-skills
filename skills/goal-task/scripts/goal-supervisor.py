#!/usr/bin/env python3
"""Deterministic supervision checks for concurrent Goal handoffs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROLE_TTL_SECONDS = {
    "explorer": 600,
    "pr_monitor": 600,
    "reviewer": 900,
    "designer": 900,
    "writer": 1800,
    "team_lead": 2400,
}
EVENT_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))"
    r".*?\bEVT=(?P<event>[A-Z0-9_]+)"
)


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


def scan(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root).expanduser()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    agents = load_agents(Path(args.herdr_json) if args.herdr_json else None)
    goals: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for ledger in sorted(root.glob("*/ledger.md")):
        events = goal_events(ledger)
        if not events or any(event == "GOAL_DONE" for _, event in events):
            continue
        goal = ledger.parent
        latest = max(timestamp for timestamp, _ in events)
        prefix = goal.name
        goal_agents = [item for item in agents if agent_name(item).startswith(prefix)]
        tl_name = next(
            (
                line.split("TL:", 1)[1].strip().split()[0]
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if "TL:" in line
            ),
            "",
        )
        goals.append(
            {
                "goal_id": goal.name,
                "latest_event_ts": latest.isoformat(),
                "event_count": len(events),
                "tl": tl_name,
                "agents": [agent_name(item) for item in goal_agents],
            }
        )
        for item in goal_agents:
            name, status = agent_name(item), agent_status(item)
            role = role_for(name)
            if status == "blocked":
                anomalies.append({"type": "BLOCKED_AGENT", "goal_id": goal.name, "agent": name})
            elapsed = (now - latest).total_seconds()
            if status == "working" and elapsed > ROLE_TTL_SECONDS[role] and not receipt_exists(goal, name):
                anomalies.append(
                    {
                        "type": "ZOMBIE_LEASE",
                        "goal_id": goal.name,
                        "agent": name,
                        "role": role,
                        "elapsed_sec": int(elapsed),
                        "ttl_sec": ROLE_TTL_SECONDS[role],
                    }
                )
            if tl_name and name == tl_name and status == "working":
                child_attention = any(
                    other is not item
                    and agent_name(other) != tl_name
                    and agent_status(other) in {"blocked", "done", "idle"}
                    for other in goal_agents
                )
                if child_attention:
                    anomalies.append({"type": "ORPHAN_TL_WAIT", "goal_id": goal.name, "agent": name})
    return {"goals": goals, "anomalies": anomalies, "healthy": not anomalies}


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


def watch(args: argparse.Namespace) -> dict[str, Any]:
    import time

    handoff = Path(args.handoff_dir).expanduser()
    interval = max(5, int(args.interval))
    max_wait = max(interval, int(args.max_wait))
    started = time.monotonic()
    while True:
        events = [event for _, event in goal_events(handoff / "ledger.md")]
        if "GOAL_DONE" in events:
            return {"goal_id": handoff.name, "reason": "GOAL_DONE", "events": len(events)}
        scan_args = argparse.Namespace(
            handoff_root=str(handoff.parent),
            herdr_json=args.herdr_json,
            now=None,
            json=True,
        )
        scan_result = scan(scan_args)
        goal_anomalies = [a for a in scan_result["anomalies"] if a.get("goal_id") == handoff.name]
        if goal_anomalies:
            return {"goal_id": handoff.name, "reason": "ANOMALY", "anomalies": goal_anomalies}
        if time.monotonic() - started >= max_wait:
            return {"goal_id": handoff.name, "reason": "WATCH_SLICE_TIMEOUT", "events": len(events)}
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--handoff-root", default="~/Development/handoff")
    scan_parser.add_argument("--herdr-json")
    scan_parser.add_argument("--now")
    scan_parser.add_argument("--json", action="store_true")
    pins_parser = subparsers.add_parser("check-pins")
    pins_parser.add_argument("--pins-file", required=True)
    pins_parser.add_argument("--json", action="store_true")
    telemetry_parser = subparsers.add_parser("telemetry")
    telemetry_parser.add_argument("--handoff-dir", required=True)
    telemetry_parser.add_argument("--write", action="store_true")
    telemetry_parser.add_argument("--json", action="store_true")
    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("--handoff-dir", required=True)
    watch_parser.add_argument("--interval", type=int, default=30)
    watch_parser.add_argument("--max-wait", type=int, default=2400)
    watch_parser.add_argument("--herdr-json")
    watch_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "scan":
        result = scan(args)
    elif args.command == "check-pins":
        result = check_pins(args)
    elif args.command == "watch":
        result = watch(args)
    else:
        result = telemetry(args)
    json_output(result, args.json)


if __name__ == "__main__":
    main()
