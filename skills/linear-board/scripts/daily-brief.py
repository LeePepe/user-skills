#!/usr/bin/env python3
"""Render and optionally publish the personal Linear daily brief."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OWNER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
DEFAULT_PARENT = "TIA-375"
PERSONAL_REPOS = ("user-skills", "VoxPocket", "AIDash", "shared-design-system", "Financial", "leepepe.github.io")
EXCLUDED_TERMS = ("work business", "bing", "workplace", "[root]")
EXCLUDED_LEDGER_PATHS = ("work", "sapphire", "flightbook", "webiq")
EVENT_RE = re.compile(r"(?:^|\|)\s*(EVT=(?:DONE|PR_MERGED|GOAL_DONE))\b", re.I)


def parse_date(value: str) -> date:
    if value == "today":
        return datetime.now(timezone(timedelta(hours=8))).date()
    return date.fromisoformat(value)


def _excluded_issue(issue: dict[str, Any]) -> bool:
    parts = [str(issue.get("title", "")), str(issue.get("project", {}).get("name", ""))]
    parent = issue.get("parent") or {}
    while parent:
        parts.extend((str(parent.get("title", "")), str(parent.get("project", {}).get("name", ""))))
        parent = parent.get("parent") or {}
    text = " ".join(parts).casefold()
    return any(term in text for term in EXCLUDED_TERMS)


def _date_in(value: str, target: date) -> bool:
    return bool(value) and value[:10] == target.isoformat()


def collect_linear(client: Any, target: date) -> tuple[list[dict[str, Any]], list[str]]:
    if client is None:
        return [], ["Linear source not queried (no client supplied)"]
    try:
        issues = client.list_issues("TIA", limit=None)
    except Exception:
        return [], ["Linear source unavailable; no issue data was fabricated"]
    result = []
    for issue in issues:
        if _excluded_issue(issue):
            continue
        state = issue.get("state") or {}
        if str(state.get("type", "")).casefold() == "canceled":
            continue
        if _date_in(str(issue.get("completedAt", "")), target) or (_date_in(str(issue.get("updatedAt", "")), target) and str(state.get("type", "")).casefold() == "completed"):
            result.append(issue)
    return result, []


def collect_git(dev_root: Path, target: date) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    for name in PERSONAL_REPOS:
        repo = dev_root / name
        if not (repo / ".git").exists():
            continue
        try:
            out = subprocess.check_output(["git", "-C", str(repo), "log", "--all", "--since", f"{target} 00:00 +0800", "--until", f"{target + timedelta(days=1)} 00:00 +0800", "--format=%h%x09%s%x09%an"], text=True, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            warnings.append(f"Git source unavailable: {name}")
            continue
        for line in out.splitlines():
            sha, subject, author = (line.split("\t", 2) + ["", "", ""])[:3]
            rows.append({"repo": name, "sha": sha, "subject": subject, "author": author})
    return rows, warnings


def collect_ledgers(handoff_root: Path, target: date) -> tuple[list[str], list[str]]:
    events: list[str] = []
    warnings: list[str] = []
    if not handoff_root.exists():
        return [], ["Handoff ledger source unavailable"]
    for ledger in sorted(handoff_root.glob("*/ledger.md")):
        relative = str(ledger.parent).casefold()
        if any(term in relative.split("/") for term in EXCLUDED_LEDGER_PATHS):
            continue
        try:
            lines = ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            warnings.append(f"Ledger unreadable: {ledger.parent.name}")
            continue
        for line in lines:
            if target.isoformat() in line and EVENT_RE.search(line):
                events.append(f"{ledger.parent.name}: {line.strip()}")
    return events, warnings


def _item_text(text: str, source: str) -> str:
    return f"{text} _(source: {source})_"


def render_brief(target: date, linear: list[dict[str, Any]], commits: list[dict[str, str]], events: list[str], warnings: list[str]) -> tuple[str, list[dict[str, Any]]]:
    yesterday = target - timedelta(days=1)
    marker = f"<!-- GTT-LINEAR-DEDUP:daily-brief-{target.isoformat()} -->"
    title = f"[Daily Brief {target.isoformat()}] 昨日汇总 ({yesterday:%m-%d}) 与今日 Todo ({target:%m-%d})"
    lines = [marker, f"# {title}", "", f"## 📊 昨日交付汇总（Yesterday's Rollup · {yesterday.isoformat()}）", ""]
    rollup = []
    for issue in linear:
        rollup.append(_item_text(f"{issue.get('identifier', 'Linear')} {issue.get('title', '')}", "Linear TIA"))
    for commit in commits:
        rollup.append(_item_text(f"{commit['repo']} `{commit['sha']}` {commit['subject']}", "Git"))
    rollup.extend(_item_text(event, "handoff ledger") for event in events)
    lines.extend([f"- {item}" for item in rollup] or ["- No verified personal deliveries found for this date."])
    if warnings:
        lines.extend(["", "**Source warnings:**"] + [f"- {warning}" for warning in warnings])
    lines += ["", f"## 🎯 今日 Todo 与待拍板清单（Today's Actionable Todos · {target.isoformat()}）", ""]
    todo_items: list[dict[str, Any]] = []
    groups = [("### 🚨 待你拍板 / 选型 (Needs Owner Decision)", []), ("### 🔨 在途推进中 (In Progress Goals)", []), ("### 📌 今日计划待办 & 每日学习 (Planned Todos & Daily Learn)", [])]
    for issue in linear:
        state = str((issue.get("state") or {}).get("type", "")).casefold()
        if state in {"started", "inprogress", "in_review"}:
            groups[1][1].append(_item_text(f"{issue.get('identifier', 'Linear')} {issue.get('title', '')}", "Linear TIA"))
    groups[2][1].append(_item_text(f"Review today's goals and verify source warnings ({target.isoformat()})", "daily brief"))
    number = 1
    for heading, items in groups:
        lines += [heading]
        if not items:
            lines.append("- None verified.")
        for item in items:
            lines.append(f"- [ ] **[{number}]** {item}")
            todo_items.append({"number": number, "text": item})
            number += 1
        lines.append("")
    lines += ["## 🤖 @bot 一键点单指令栏", "", "Reply with `@bot 执行 1, 2` (or check `- [x]` and assign/delegate/mention `@bot`). `linear-bot-bridge` wakes the matching Herdr M2 session, writes the full Markdown result back in comments, and reassigns the Issue to Owner (`@ltp19941128`)."]
    return "\n".join(lines).rstrip() + "\n", todo_items


def _load_linear_client() -> Any:
    path = Path(__file__).resolve().parents[2] / "team-lead/scripts/linear-sync.py"
    spec = importlib.util.spec_from_file_location("linear_sync", path)
    if not spec or not spec.loader:
        return None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    key = module.load_api_key()
    return module.GraphQLClient(key, allow_refresh=True) if key else None


def propose(client: Any, title: str, markdown: str, parent: str) -> dict[str, Any] | None:
    if client is None:
        return None
    team = client.team_metadata("TIA")
    marker = f"daily-brief-{title.split()[2]}"
    existing = client.find_issue_by_dedup_marker(team["id"], marker) or client.find_issue_by_title("TIA", title)
    state = next((s for s in team.get("states", {}).get("nodes", []) if str(s.get("name", "")).casefold() == "todo"), None)
    parent_issue = client.issue(parent)
    if existing:
        return client.update_issue(existing["id"], title=title, description=markdown, parent_id=parent_issue["id"], assignee_id=OWNER_ID)
    return client.create_issue(team_id=team["id"], title=title, description=markdown, state_id=(state or {}).get("id"), parent_id=parent_issue["id"], assignee_id=OWNER_ID)


def generate(target: date, *, dev_root: Path, handoff_root: Path, client: Any = None, propose_linear: bool = False, dry_run: bool = False, parent: str = DEFAULT_PARENT) -> dict[str, Any]:
    linear, w1 = collect_linear(client, target)
    commits, w2 = collect_git(dev_root, target - timedelta(days=1))
    events, w3 = collect_ledgers(handoff_root, target - timedelta(days=1))
    markdown, todos = render_brief(target, linear, commits, events, w1 + w2 + w3)
    issue = None if dry_run or not propose_linear else propose(client, markdown.splitlines()[1][2:], markdown, parent)
    return {"ok": True, "date": target.isoformat(), "yesterday": (target - timedelta(days=1)).isoformat(), "title": markdown.splitlines()[1][2:], "dedup_key": f"daily-brief-{target.isoformat()}", "markdown": markdown, "rollup_counts": {"linear": len(linear), "commits": len(commits), "events": len(events)}, "todo_items": todos, "linear_issue": issue}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="today")
    parser.add_argument("--propose-linear", action="store_true")
    parser.add_argument("--parent-issue", default=DEFAULT_PARENT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    client = _load_linear_client() if args.propose_linear and not args.dry_run else None
    result = generate(parse_date(args.date), dev_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_DEV_ROOT", "~/Development")).expanduser(), handoff_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_HANDOFF_ROOT", "~/Development/handoff")).expanduser(), client=client, propose_linear=args.propose_linear, dry_run=args.dry_run, parent=args.parent_issue)
    print(json.dumps(result, ensure_ascii=False) if args.json else result["markdown"], end="" if args.json else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
