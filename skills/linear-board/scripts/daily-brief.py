#!/usr/bin/env python3
"""Render and optionally publish the personal Linear daily brief."""
from __future__ import annotations
import argparse, importlib.util, json, math, os, re, subprocess, sys
from contextlib import contextmanager
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CST = timezone(timedelta(hours=8))
OWNER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
MULTICA_DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
MULTICA_DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
MULTICA_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
MULTICA_OWNER_EMAIL = "lee@multica.local"
DEFAULT_PARENT = ""
DEFAULT_PROJECT = "user-skills"
DEFAULT_MILESTONE = "Skills Workflow"
PERSONAL_REPOS = ("user-skills", "VoxPocket", "AIDash", "shared-design-system", "Financial", "leepepe.github.io")
EXCLUDED_TERMS = ("work business", "bing", "workplace")
LEDGER_TOKENS = ("work", "sapphire", "flightbook", "webiq", "bing", "devsync")
BLOCKING = {"NEED_GO", "NEED_OWNER_DECISION", "TASK_BLOCKED", "INPUT_BLOCKED", "REVIEW_FAIL"}
EVENT_RE = re.compile(r"^\s*[-*]\s+(?P<stamp>\S+)\s+\|\s+(?P<body>.*)$")
EMAIL_ACTION_MARKER_RE = re.compile(r"(?m)^[ \t]*<!--\s*GTT-EMAIL-ACTION:v1\s+(\{[^\n]*?\})\s*-->[ \t]*$")

def _extract_email_action(issue: dict[str, Any]) -> dict[str, Any] | None:
    text = str(issue.get("description") or "")
    if "GTT-EMAIL-ACTION:v1" not in text: return None
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(?:```|~~~)", line): in_fence = not in_fence; continue
        if in_fence: continue
        match = EMAIL_ACTION_MARKER_RE.match(line)
        if not match: continue
        try: payload = json.loads(match.group(1))
        except ValueError: continue
        if not isinstance(payload, dict): continue
        item_id, plan_file = str(payload.get("email_item_id") or "").strip(), str(payload.get("plan_file") or "").strip()
        if item_id and plan_file and item_id != "..." and plan_file != "...":
            for key in ("email_url", "target_link"):
                raw_url = str(payload.get(key) or "").strip()
                url_match = re.fullmatch(r"\[[^\]]*\]\(<?(https?://[^>\s)]+)>?\)", raw_url)
                if url_match: payload[key] = url_match.group(1).strip()
            return payload
    return None

def parse_date(value: str) -> date:
    return datetime.now(CST).date() if value == "today" else date.fromisoformat(value)

def _timestamp(value: str) -> datetime | None:
    if not value: return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=CST)
        return parsed.astimezone(CST)
    except (AttributeError, TypeError, ValueError): return None

def _date_in(value: str, target: date) -> bool:
    parsed = _timestamp(value)
    return parsed is not None and parsed.date() == target

def _excluded_text(values: list[str], root_self: bool = False) -> bool:
    text = " ".join(values)
    if root_self and re.search(r"(?<!\w)\[root\](?!\w)", text, re.I): return True
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(x) for x in EXCLUDED_TERMS) + r")(?!\w)"
    return bool(re.search(pattern, text, re.I))

def _scope_complete(issue: Any) -> bool:
    return isinstance(issue, dict) and all(k in issue for k in ("title", "parent", "team", "project"))

def _scope_lookup(identifier: str, client: Any, cache: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    cached = cache.get(identifier)
    if _scope_complete(cached): return cached
    try:
        scope = client.issue_scope(identifier) if client is not None else None
        if not _scope_complete(scope): raise ValueError("Incomplete scope")
        for key in (identifier, scope.get("id"), scope.get("identifier")):
            if key: cache[str(key)] = scope
        return scope
    except Exception:
        warnings.append("Linear scope lookup unavailable or incomplete; affected items excluded")
        return None

def _excluded_issue(issue: dict[str, Any], client: Any = None, cache: dict[str, Any] | None = None, warnings: list[str] | None = None) -> bool:
    if isinstance(issue, dict) and _extract_email_action(issue) is not None: return False
    cache = {} if cache is None else cache
    warnings = [] if warnings is None else warnings
    current = issue
    seen = {str(issue.get("id") or "")} - {""}
    first = True
    while isinstance(current, dict):
        project, team = current.get("project") or {}, current.get("team") or {}
        if _excluded_text([str(current.get("title") or ""), str(project.get("name") or ""), str(team.get("name") or "")], root_self=first): return True
        first = False
        parent = current.get("parent")
        if parent is None: return False
        if not isinstance(parent, dict):
            warnings.append("Linear ancestor scope incomplete; affected items excluded")
            return True
        parent_id = str(parent.get("id") or parent.get("identifier") or "")
        if not parent_id or parent_id in seen:
            warnings.append("Linear ancestor scope incomplete or cyclic; affected items excluded")
            return True
        seen.add(parent_id)
        if not _scope_complete(parent):
            parent = _scope_lookup(parent_id, client, cache, warnings)
            if parent is None: return True
        current = parent
    warnings.append("Linear ancestor scope incomplete; affected items excluded")
    return True

def _issue_is_completed(issue: dict[str, Any], target: date) -> bool:
    state = str((issue.get("state") or {}).get("type") or "").casefold()
    name = str((issue.get("state") or {}).get("name") or "").casefold()
    if state in {"canceled", "cancelled"} or name in {"canceled", "cancelled"}: return False
    completed = str(issue.get("completedAt") or "")
    if completed: return _date_in(completed, target)
    return (state == "completed" or name == "done") and _date_in(str(issue.get("updatedAt") or ""), target)

def collect_linear(client: Any, target: date, *, scope_cache: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    if client is None: return [], ["Linear source unavailable; no issue data was fabricated"]
    try: issues = client.list_issues("TIA", limit=None)
    except Exception: return [], ["Linear source unavailable; no issue data was fabricated"]
    cache = scope_cache if scope_cache is not None else {}
    warnings: list[str] = []
    for issue in issues:
        if _scope_complete(issue):
            for key in (issue.get("id"), issue.get("identifier")):
                if key: cache[str(key)] = issue
    eligible = [i for i in issues if isinstance(i, dict) and not _excluded_issue(i, client, cache, warnings)]
    return eligible, warnings

def _run(command: list[str], *, timeout: int = 15, runner: Callable[..., Any] = subprocess.check_output) -> str:
    return str(runner(command, text=True, stderr=subprocess.DEVNULL, timeout=timeout))

def collect_git(dev_root: Path, target: date, *, runner: Callable[..., Any] = subprocess.check_output) -> tuple[list[dict[str, str]], list[str]]:
    rows, warnings = [], []
    start, end = f"{target} 00:00 +0800", f"{target + timedelta(days=1)} 00:00 +0800"
    for name in PERSONAL_REPOS:
        repo = dev_root / name
        if not (repo / ".git").exists():
            warnings.append(f"Git source unavailable: {name}")
            continue
        try: out = _run(["git", "-C", str(repo), "log", "--all", "--since-as-filter", start, "--until", end, "--format=%H%x09%h%x09%s%x09%cI"], runner=runner)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired): warnings.append(f"Git source unavailable: {name}"); continue
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4 and _date_in(parts[3], target): rows.append({"repo": name, "sha": parts[1], "subject": parts[2], "timestamp": parts[3]})
    return rows, warnings

def _repo_name(repo: str) -> str:
    return repo.rstrip("/").rsplit("/", 1)[-1]

def collect_prs(repos: tuple[str, ...], target: date, *, runner: Callable[..., Any] = subprocess.check_output) -> tuple[list[dict[str, Any]], list[str]]:
    rows, warnings = [], []
    for repo in repos:
        try:
            raw = _run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls?state=closed&per_page=100"], runner=runner)
            data = json.loads(raw)
            if isinstance(data, list) and data and all(isinstance(page, list) for page in data): data = [item for page in data for item in page]
            if not isinstance(data, list) or any(not isinstance(pr, dict) for pr in data): raise ValueError("Invalid PR pages")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError): warnings.append(f"PR source unavailable: {repo}"); continue
        seen = set()
        for pr in data:
            if not _date_in(pr.get("merged_at"), target): continue
            number = pr.get("number")
            if number in seen: continue
            seen.add(number)
            detail = {}
            try:
                if not isinstance(number, int): raise ValueError("Missing PR number")
                detail = json.loads(_run(["gh", "api", f"repos/{repo}/pulls/{number}"], runner=runner))
                if not isinstance(detail, dict) or any(detail.get(k) is None for k in ("additions", "deletions", "changed_files")): raise ValueError("Missing PR metrics")
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
                warnings.append(f"PR metrics unavailable: {repo}")
                detail = {}
            rows.append({"repo": repo, "number": number, "title": pr.get("title", ""), "url": pr.get("html_url", ""), **{k: detail.get(k) for k in ("additions", "deletions", "changed_files")}})
    return rows, warnings

def _ledger_header(lines: list[str]) -> dict[str, str]:
    header = {}
    for line in lines:
        if line.startswith("## "): break
        goal = re.match(r"^#\s*Goal:\s*(.*)$", line, re.I)
        if goal: header["goal"] = goal[1].strip()
        match = re.match(r"^\s*(?:[-*+]\s*)?(project|repo|linear(?: issue)?|goal|status|state|stage|m1)\s*:\s*(.*)$", line, re.I)
        if match: header[match[1].casefold()] = match[2].strip()
    return header

def _ledger_excluded(path: Path, lines: list[str]) -> bool:
    name = path.parent.name.casefold()
    if name.startswith("_watch"): return True
    if re.match(r"^work(?:[-_]|$)", name) or any(name.startswith(token) for token in LEDGER_TOKENS if token != "work"): return True
    header = _ledger_header(lines)
    return any(_excluded_text([value]) or re.search(r"(?i)(?<!\w)(?:bing|sapphire|flightbook|webiq|devsync)\w*", value)
               for key, value in header.items() if key in {"project", "repo", "linear", "linear issue", "goal"})

def _ledger_tasks(lines: list[str]) -> list[dict[str, str]]:
    tasks, columns = [], {}
    in_tasks = False
    aliases = {"id": "task", "task id": "task", "task": "task", "title": "title", "标题": "title",
               "status": "state", "状态": "state", "state": "state", "stage": "stage", "m1": "stage",
               "owner": "owner", "writer": "owner", "note": "note", "notes": "note",
               "pr": "ref", "pr/head": "ref", "receipts": "receipts", "ref": "ref"}
    for line in lines:
        if line.strip() == "## Tasks": in_tasks = True; continue
        if in_tasks and line.startswith("## "): break
        if not in_tasks or not line.lstrip().startswith("|"): continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if all(not cell or set(cell) <= {"-", ":"} for cell in cells): continue
        if cells[0].casefold() in {"id", "task", "task id"}:
            columns = {aliases[cell.casefold()]: index for index, cell in enumerate(cells) if cell.casefold() in aliases}
            continue
        if not columns: continue
        row = {key: cells[index] for key, index in columns.items() if index < len(cells)}
        if row.get("task"):
            row["state"] = row.get("state", "").casefold().replace(" ", "_")
            tasks.append(row)
    return tasks

def _ledger_snapshot(path: Path, target: date, client: Any, cache: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    try: lines = path.read_text(encoding="utf-8").splitlines()
    except OSError: return None, ["Handoff ledger unreadable; affected goal excluded"]
    if _ledger_excluded(path, lines): return None, []
    header, warnings = _ledger_header(lines), []
    bound = re.search(r"\bTIA-\d+\b", header.get("linear issue", header.get("linear", "")))
    identifier = bound[0] if bound else ""
    if identifier:
        scope = _scope_lookup(identifier, client, cache, warnings)
        if scope is None or _excluded_issue(scope, client, cache, warnings): return None, warnings
    title = next((line.split(":", 1)[1].strip() for line in lines if re.match(r"^#\s*Goal:", line)), path.parent.name)
    events = []
    for line in lines:
        match = EVENT_RE.match(line)
        if not match: continue
        stamp = _timestamp(match["stamp"])
        if stamp is None or stamp.date() > target: continue
        fields = {k.strip(): v.strip() for k, v in (part.split("=", 1) for part in match["body"].split(" | ") if "=" in part)}
        if "EVT" not in fields: continue
        if not fields.get("note"):
            fields["note"] = " · ".join(part.strip() for part in match["body"].split(" | ") if "=" not in part)
        fields.update({"timestamp": stamp.isoformat(), "line": line.strip(), "goal": path.parent.name,
                       "title": title, "identifier": identifier, "ledger": path.as_uri(), "repo": header.get("repo", ""), "project": header.get("project", "")})
        events.append(fields)
    events.sort(key=lambda event: _timestamp(event["timestamp"]))
    return {"goal": path.parent.name, "title": title, "identifier": identifier, "ledger": path.as_uri(),
            "header": header, "repo": header.get("repo", ""), "project": header.get("project", ""), "tasks": _ledger_tasks(lines), "events": events}, warnings

def _parse_ledger(path: Path, target: date) -> tuple[list[dict[str, str]], list[str]]:
    snapshot, warnings = _ledger_snapshot(path, target, None, {})
    return (snapshot["events"] if snapshot else []), warnings

def _active_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    active: dict[tuple[str, str, str], dict[str, str]] = {}
    for index, event in enumerate(events):
        typ, goal = event.get("EVT", "").upper(), event.get("goal", "")
        if typ in BLOCKING:
            key = (goal, event.get("id", ""), event.get("task") or ("" if event.get("id") else str(index)))
            active[key] = event
        elif typ == "GOAL_DONE":
            active = {key: item for key, item in active.items() if key[0] != goal}
        elif typ in {"ACK", "RESOLVED"} and event.get("id"):
            active = {key: item for key, item in active.items() if not (key[0] == goal and item.get("id") == event["id"])}
        elif typ in {"DONE", "TASK_DONE", "REVIEW_PASS", "PR_MERGED"} and event.get("task"):
            active = {key: item for key, item in active.items() if not (key[0] == goal and item.get("task") == event["task"])}
    return list(active.values())

def collect_handoff(handoff_root: Path, target: date, *, client: Any = None, scope_cache: dict[str, Any] | None = None) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    rollup, active, warnings = [], [], []
    cache = {} if scope_cache is None else scope_cache
    if not handoff_root.exists(): return [], [], ["Handoff ledger source unavailable"]
    stage_events = {"TASK_STARTED": "in_progress", "IN_PROGRESS": "in_progress", "REVIEW_STARTED": "in_review",
                    "IN_REVIEW": "in_review", "TASK_BLOCKED": "blocked", "REVIEW_FAIL": "blocked",
                    "TASK_PLANNED": "planned", "TASK_READY": "ready", "TASK_DONE": "done",
                    "DONE": "done", "REVIEW_PASS": "done", "PR_MERGED": "done"}
    for ledger in sorted(handoff_root.glob("*/ledger.md")):
        snapshot, warns = _ledger_snapshot(ledger.resolve(), target, client, cache)
        warnings.extend(warns)
        if snapshot is None: continue
        events = snapshot["events"]
        rollup.extend(event for event in events if _date_in(event["timestamp"], target - timedelta(days=1)) and event["EVT"].upper() in {"DONE", "PR_MERGED", "GOAL_DONE"})
        goal_events = [event for event in events if event["EVT"].upper().startswith("GOAL_") or (not event.get("task") and (event.get("state") or event.get("status")))]
        latest = goal_events[-1] if goal_events else {}
        inferred_state = {"GOAL_STARTED": "in_progress", "GOAL_IN_PROGRESS": "in_progress", "GOAL_IN_REVIEW": "in_review", "GOAL_DONE": "done"}.get(latest.get("EVT", "").upper(), snapshot["header"].get("state", snapshot["header"].get("status", "")))
        goal_state = latest.get("state", latest.get("status", inferred_state)).casefold()
        if latest.get("EVT", "").upper() == "GOAL_DONE" or goal_state in {"done", "completed", "canceled", "cancelled"}: continue
        blockers = _active_events(events)
        for task in snapshot["tasks"]:
            task_events = [event for event in events if event.get("task") == task["task"]]
            stage = task["state"]
            for event in task_events:
                stage = event.get("state", event.get("status", stage_events.get(event["EVT"].upper(), stage))).casefold().replace(" ", "_")
            context = {key: str(snapshot[key]) for key in ("goal", "title", "identifier", "ledger", "repo", "project")}
            context.update(task); context["state"] = stage
            for field in ("stage", "note", "ref", "pr"):
                newest = next((event[field] for event in reversed(task_events) if event.get(field)), "")
                if newest: context[field] = newest
            context["stage"] = context.get("stage") or snapshot["header"].get("m1", snapshot["header"].get("stage", ""))
            if stage in {"in_progress", "in_review"} and not any(event.get("task") == task["task"] for event in blockers): active.append({**context, "group": "active"})
            elif stage == "blocked" and not any(event.get("task") == task["task"] for event in blockers):
                # Do not resurrect a resolved event from an older blocked task snapshot.
                if not any(event["EVT"].upper() in BLOCKING for event in task_events):
                    active.append({**context, "EVT": "TASK_BLOCKED", "group": "needs"})
        for event in blockers:
            task = next((task for task in snapshot["tasks"] if task["task"] == event.get("task")), {})
            context = {**task, **event, "state": "blocked", "group": "needs"}
            context["stage"] = event.get("stage") or task.get("stage") or snapshot["header"].get("m1", snapshot["header"].get("stage", ""))
            if task.get("title"): context["title"] = f"{snapshot['title']} / {task['title']}"
            active.append(context)
    return rollup, active, warnings

def collect_ledgers(handoff_root: Path, target: date) -> tuple[list[dict[str, str]], list[str]]:
    rollup, _, warnings = collect_handoff(handoff_root, target + timedelta(days=1))
    return rollup, warnings

def _closed_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state") or {}
    return str(state.get("type") or "").casefold() in {"completed", "canceled", "cancelled"} or str(state.get("name") or "").casefold() in {"done", "canceled", "cancelled"}

def _brief_issue(issue: dict[str, Any]) -> bool:
    return bool(re.match(r"\[Daily Brief\b", str(issue.get("title") or ""), re.I) or re.search(r"<!-- GTT-LINEAR-DEDUP:daily-brief-\d{4}-\d{2}-\d{2} -->", str(issue.get("description") or "")))

def _needs_decision(issue: dict[str, Any], labels: str, state: str) -> bool:
    if _extract_email_action(issue) is not None and state not in {"in progress", "inprogress", "started"}: return True
    title = str(issue.get("title") or "")
    if re.search(r"(?i)\b(blocked|needs decision|owner decision)\b|待拍板|待选型", f"{title} {labels} {state}"): return True
    clauses = f"{title}\n{issue.get('description') or ''}"
    return bool(re.search(r"(?i)(?:demo|gallery|选型)[^\n.!。]{0,100}(?:awaiting|waiting for|pending|待拍板|待选|待你选择)|(?:awaiting|waiting for|pending|待拍板|待选)[^\n.!。]{0,100}(?:demo|gallery|选型)", clauses))

def _ledger_context(item: dict[str, str]) -> str:
    stage = " / ".join(value for value in (item.get("state", ""), item.get("stage", "")) if value)
    details = " · ".join(str(item[key]) for key in ("task", "title", "EVT", "note", "ref", "pr", "receipts", "owner") if item.get(key))
    if not details: details = item.get("line", "")
    return f"{item.get('goal', 'goal')}: {details}" + (f" [M1: {stage}]" if stage else "") + f" {item.get('ledger', '')}"

def _item(text: str, source: str) -> str: return f"{text} _(source: {source})_"
def _issue_context(issue: dict[str, Any]) -> str:
    project = (issue.get("project") or {}).get("name") or "personal"
    return f"{issue.get('identifier', 'Linear')} {issue.get('title', '')} [{project}] {issue.get('url', '')}".strip()

def _plain(value: Any) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"[*`<>|]", "", text)).strip()

def _short(value: Any, limit: int) -> str:
    return _plain(value)[:limit].strip()

def _code(value: Any) -> str:
    return f"`{_plain(value)}`"

def _link(url: Any, label: str) -> str:
    url = str(url or "")
    if not re.match(r"^(?:https?://|file:///)", url) or re.search(r"[\s<>]", url): return ""
    return f"[{_plain(label)}]({url.replace('|', '%7C').replace('(', '%28').replace(')', '%29')})"

LAYERS = (
    ("Products & Apps", "产品与应用", ("voxpocket", "aidash", "financial", "vitalstride", "webiqsearch", "nas", "nascontrol")),
    ("Shared Foundations", "共享底座", ("shared-design-system", "designsystem", "shared-ci", "shared-telemetry", "raven", "agent-herdr")),
    ("Engineering Workflows", "工程工作流与技能", ("user-skills", "linear-board", "m2-dispatcher", "personal-m2-assistant", "team-lead", "my-designer", "design-studio", "code-review", "repo-kit", "skill-contract", "workflow-hub")),
    ("Knowledge & Narrative", "知识沉淀与叙事", ("knowledge-digest", "daily learn", "knowledge", "retro-loop", "story-craft", "save-tool", "leepepe.github.io", "blog demos")),
)
REFERENCE_RE = re.compile(r"\bTIA-\d+\b|(?<![\w])(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|PR)?#\d+\b")
PR_URL_RE = re.compile(r"https://github\.com/([^/\s()]+/[^/\s()]+)/pull/(\d+)\b")
EXPLICIT_PR_RE = re.compile(r"^(?:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)|PR)?#(\d+)$", re.I)


def _pr_url(reference: str, repo: str = "") -> str:
    match = EXPLICIT_PR_RE.fullmatch(reference)
    if not match: return ""
    owner_repo, number = match.groups()
    destination = owner_repo or repo
    return f"https://github.com/{destination}/pull/{number}" if destination else ""


def _reference_label(reference: str) -> str:
    match = EXPLICIT_PR_RE.fullmatch(reference)
    return f"#{match[2]}" if match else reference


def _has_reference_link(text: str, reference: str) -> bool:
    label = _reference_label(reference)
    return bool(re.search(r"\[" + re.escape(reference) + r"\]\([^)]*\)", text)
                or (label != reference and re.search(r"\[" + re.escape(label) + r"\]\([^)]*\)", text)))


def _layer(project: str, item: dict[str, Any]) -> int:
    def match(text: str, tokens: tuple[str, ...]) -> bool:
        return any(re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text.casefold()) for token in tokens)
    # A concrete product/foundation/knowledge destination outranks incidental title mentions.
    destination = " ".join((project, str(item.get("repo") or ""), str(item.get("project") or "")))
    for index in (0, 1, 3):
        if match(destination, LAYERS[index][2]): return index
    context = " ".join(str(item.get(key) or "") for key in ("goal", "title", "subject", "note"))
    for index in (3, 0, 1, 2):
        if match(context, LAYERS[index][2]): return index
    if re.search(r"(?i)\b(?:demo gallery|ui gallery|gallery)\b", context): return 0
    return 2

def _reference_context(item: dict[str, Any], project: str = "") -> str:
    text = " ".join(str(item.get(key) or "") for key in ("url", "ref", "pr", "note", "title", "subject"))
    repo = str(item.get("repo") or "")
    if repo and not repo.startswith("/") and repo.count("/") == 1: return repo
    known = PERSONAL_REPOS + ("VitalStride", "WebIQSearch", "Nas", "NasControl", "shared-ci", "shared-telemetry", "raven", "agent-herdr")
    if repo and _repo_name(repo).casefold() in {token.casefold() for token in known}: return f"LeePepe/{_repo_name(repo)}"
    match = PR_URL_RE.search(text)
    if match: return match[1]
    name = _repo_name(project)
    if name.casefold() in {token.casefold() for token in known}: return f"LeePepe/{name}"
    context = " ".join((project, str(item.get("goal") or ""), str(item.get("title") or "")))
    matches = [token for token in known if re.search(r"(?<![a-z0-9])" + re.escape(token.casefold()) + r"(?![a-z0-9])", context.casefold())]
    return f"LeePepe/{matches[0]}" if len(matches) == 1 else ""

def _reference_urls(items: list[dict[str, Any]]) -> dict[str, str]:
    urls = {}
    for item in items:
        identifier = str(item.get("identifier") or "")
        if re.fullmatch(r"TIA-\d+", identifier) and _link(item.get("url"), identifier): urls[identifier] = item["url"]
        for value in item.values():
            if not isinstance(value, str): continue
            for label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", value):
                if re.fullmatch(r"TIA-\d+", label) and _link(url, label): urls.setdefault(label, url)
    return urls

def _linked_text(value: Any, issue_urls: dict[str, str], repo: str = "", *, limit: int | None = None) -> str:
    text = _plain(value)
    pr_urls = {f"#{match[2]}": match[0] for match in PR_URL_RE.finditer(str(value or ""))}
    for reference in REFERENCE_RE.findall(text):
        url = _pr_url(reference, repo)
        if url: pr_urls[reference] = url
    if limit is not None:
        # Do not turn a cut TIA-1234/#1234 token into a different ticket/PR.
        end = limit
        for match in REFERENCE_RE.finditer(text):
            if match.start() < end < match.end(): end = match.start(); break
        text = text[:end].strip()
    def replace(match: re.Match[str]) -> str:
        label = match[0]
        if label.startswith("TIA-"):
            return _link(issue_urls.get(label) or f"https://linear.app/tianpeili/issue/{label}", label)
        url = pr_urls.get(label) or _pr_url(label, repo)
        return _link(url, _reference_label(label)) or "PR"
    # URLs are destinations, not text tokens to rewrite inside a destination.
    return "".join(_link(part, "链接") if re.match(r"^(?:https?://|file:///)", part) else REFERENCE_RE.sub(replace, part)
                   for part in re.split(r"((?:https?://|file:///)[^\s]+)", text))

def _source_refs(item: dict[str, Any], issue_urls: dict[str, str], repo: str) -> list[str]:
    text = " ".join(str(item.get(key) or "") for key in ("identifier", "task", "title", "subject", "note", "ref", "pr"))
    labels = list(dict.fromkeys(REFERENCE_RE.findall(text)))
    for match in PR_URL_RE.finditer(text):
        label = f"#{match[2]}"
        if label not in labels: labels.append(label)
    pr_urls = {f"#{match[2]}": match[0] for match in PR_URL_RE.finditer(text)}
    return [_link(pr_urls[label], label) if label in pr_urls else _linked_text(label, issue_urls, repo) for label in labels]

TODO_BUCKETS = {
    "needs": "🚨 待你拍板（Needs Decision）",
    "active": "🔨 在途跟进（In Progress）",
    "planned": "📌 今日计划（Planned & Learn）",
    "recommended": "💡 昨日完结顺推（Recommended Next-Hop）",
}

def _area(project: str, item: dict[str, Any]) -> str:
    generic = {"", "personal", "personal project", "agent workflow", "handoff"}
    layer = _layer(project, item)
    destination = _repo_name(str(item.get("repo") or project))
    context = " ".join(str(item.get(key) or "") for key in ("goal", "title", "subject"))
    if destination.casefold() not in generic | {"user-skills"}: context = destination + " " + context
    for token in LAYERS[layer][2]:
        if re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", context.casefold()):
            return {"voxpocket": "VoxPocket", "aidash": "AIDash", "financial": "Financial", "vitalstride": "VitalStride", "webiqsearch": "WebIQSearch", "nas": "Nas", "nascontrol": "NasControl"}.get(token, destination if destination.casefold() == token else token)
    return destination if destination.casefold() not in generic else _plain(item.get("goal") or item.get("title") or item.get("identifier") or "完成事项")

def _completed_sources(yesterday: date, linear: list[dict[str, Any]], commits: list[dict[str, Any]], events: list[dict[str, Any]], prs: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    sources = [((item.get("project") or {}).get("name") or "Personal", item, "Linear")
               for item in linear if _issue_is_completed(item, yesterday) and not _brief_issue(item) and not _excluded_text([str(item.get("title") or ""), str((item.get("project") or {}).get("name") or "")], root_self=True)]
    sources += [(_repo_name(item["repo"]), item, "Git") for item in commits if not item.get("timestamp") or _date_in(item["timestamp"], yesterday)]
    sources += [(_repo_name(item["repo"]), item, "PR") for item in prs if not item.get("merged_at") or _date_in(item["merged_at"], yesterday)]
    sources += [(item.get("goal", "Handoff"), item, "ledger") for item in events
                if (not item.get("timestamp") or _date_in(item["timestamp"], yesterday)) and item.get("EVT", "DONE").upper() in {"DONE", "PR_MERGED", "GOAL_DONE"}]
    return sources

@lru_cache(maxsize=1)
def _load_jev_runner() -> Callable[..., Any]:
    path = Path(__file__).resolve().parents[2] / "team-lead/scripts/jev_client.py"
    spec = importlib.util.spec_from_file_location("daily_brief_jev", path)
    if not spec or not spec.loader: raise ImportError("Canonical Jev client unavailable")
    module = importlib.util.module_from_spec(spec)
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally: sys.dont_write_bytecode = old_flag
    return module.jev_decide

def _terminal_next_hop(text: str) -> bool:
    return bool(re.search(r"(?i)(?:清理(?:归档)?|归档|archive|archival|cleanup|clean up|close stale|cancel stale).{0,60}(?:过期|旧单|obsolete|stale|tickets?|issues?|单据|工单)|(?:过期|obsolete|stale).{0,30}(?:归档|cleanup|archive)|(?:fix typo|修正错别字|纯治理)", text))

def _probability(value: Any, key: str) -> float | None:
    if isinstance(value, dict): value = value.get(key, value.get("value"))
    if isinstance(value, bool): return None
    try: score = float(value)
    except (ValueError, TypeError, OverflowError): return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None

def _next_hop_decision(item: dict[str, Any], jev_runner: Callable[..., Any] | None) -> tuple[str, float, str] | None:
    if item["gallery"]: fallback_kind = "gallery_pick"
    elif item["layer"] == 3 or len(item["identities"]) >= 2: fallback_kind = "story_or_retro"
    elif item["layer"] == 1: fallback_kind = "downstream_adopt"
    else: fallback_kind = "live_e2e_verify"
    fallback_priority = 1.0 if item["gallery"] else 0.8 - item["layer"] * 0.1
    fallback = None if item["terminal"] else (fallback_kind, fallback_priority, "deterministic_fallback")
    questions = {
        "has_actionable_next_hop": {"type": "noul", "instructions": "Does this completion have a concrete non-trivial follow-up needed today?"},
        "next_hop_kind": {"type": "choice", "instructions": "Classify the observable follow-up context.", "criteria": {
            "gallery_pick": "A completed gallery awaits Owner selection.",
            "downstream_adopt": "A shared foundation change needs downstream adoption verification.",
            "live_e2e_verify": "A delivered app, bridge, scheduled job needs live readback.",
            "story_or_retro": "A completed milestone has reusable narrative evidence."}},
        "next_hop_kind_match": {"type": "noul", "instructions": "Does this completion match a supplied follow-up category?"},
        "next_hop_priority": {"type": "score", "instructions": "Score observable follow-up urgency from 0.0 (none) to 1.0 (highest)."},
    }
    try:
        runner = jev_runner if jev_runner is not None else _load_jev_runner()
        response = runner(state={"area": item["area"], "layer": LAYERS[item["layer"]][0], "gallery": item["gallery"],
                                 "completed_items": len(item["identities"]), "source_context": [_plain(part)[:500] for part in item["parts"]]},
                          questions=questions, agent_role="linear_board", goal_id="linear-daily-brief", timeout_sec=1.5, falcon_only=True)
    except Exception: return fallback
    if not isinstance(response, dict) or response.get("fallback_reason"): return fallback
    answers = response.get("answers", response)
    if not isinstance(answers, dict) or answers.get("fallback_reason"): return fallback
    actionable = _probability(answers.get("has_actionable_next_hop"), "noul")
    if actionable is None: return fallback
    if actionable < 0.50: return None
    matched = _probability(answers.get("next_hop_kind_match"), "noul")
    kind = answers.get("next_hop_kind")
    if isinstance(kind, dict): kind = kind.get("choice")
    if matched is None or matched < 0.50 or not isinstance(kind, str) or kind not in questions["next_hop_kind"]["criteria"]: kind = fallback_kind
    priority = _probability(answers.get("next_hop_priority"), "score")
    return kind, fallback_priority if priority is None else priority, "jev_truth_table"

def _recommendations(yesterday: date, linear: list[dict[str, Any]], commits: list[dict[str, Any]], events: list[dict[str, Any]], prs: list[dict[str, Any]], entries: dict[str, dict[str, Any]], issue_urls: dict[str, str], jev_runner: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    covered_ids = set(REFERENCE_RE.findall(" ".join(str(entry["ref"]) + " " + " ".join(entry["parts"]) for entry in entries.values())))
    covered_topics = {topic for entry in entries.values() for topic in entry["topics"]}
    areas: dict[str, dict[str, Any]] = {}
    for project, item, source in _completed_sources(yesterday, linear, commits, events, prs):
        area = _area(project, item); key = area.casefold()
        labels = _source_refs(item, issue_urls, _reference_context(item, project))
        identifiers = set(re.findall(r"\bTIA-\d+\b", " ".join(labels)))
        if key in covered_topics or identifiers & covered_ids: continue
        repo = _reference_context(item, project)
        pr_match = PR_URL_RE.search(" ".join(str(item.get(k) or "") for k in ("url", "ref", "pr")))
        identifier = str(item.get("identifier") or next(iter(sorted(identifiers)), ""))
        if source == "PR" or pr_match:
            number = str(item.get("number") or (pr_match[2] if pr_match else ""))
            url = pr_match[0] if pr_match else (f"https://github.com/{repo}/pull/{number}" if repo and number else "")
            action = _link(url, f"#{number}" if number else "PR"); identity = identifier or url; priority = 0
        elif identifier:
            action = _linked_text(identifier, issue_urls); identity = identifier; priority = 1
        elif source == "Git" and repo and item.get("sha"):
            action = _link(f"https://github.com/{repo}/commit/{item['sha']}", str(item["sha"])); identity = f"{repo}:{item['sha']}"; priority = 3
        else:
            action = _link(item.get("ledger"), "记录"); identity = str(item.get("task") or item.get("id") or item.get("goal")); priority = 2
        if not action: continue
        layer = _layer(project, item)
        gallery = layer == 0 and bool(re.search(r"(?i)\b(?:demo|gallery)\b|画廊|原型", " ".join(str(item.get(k) or "") for k in ("goal", "title", "subject", "note"))))
        source_text = " ".join(str(item.get(k) or "") for k in ("title", "subject", "note")) or str(item.get("goal") or "")
        candidate = {"area": area, "layer": layer, "gallery": gallery, "terminal": _terminal_next_hop(source_text), "identities": {identity}, "priority": priority,
                     "action": action, "ref": identifier or " · ".join(labels) or area, "repo": repo,
                     "parts": [_item(_issue_context(item) if source == "Linear" else _ledger_context(item) if source == "ledger" else f"{area}: {item.get('title') or item.get('subject') or ''} {action}", source)]}
        if key not in areas: areas[key] = candidate; continue
        current = areas[key]
        current["gallery"] |= gallery; current["terminal"] &= candidate["terminal"]
        current["identities"].add(identity); current["parts"].extend(candidate["parts"])
        if (priority, action) < (current["priority"], current["action"]):
            for field in ("priority", "action", "ref", "repo"): current[field] = candidate[field]
    eligible = []
    for item in areas.values():
        decision = _next_hop_decision(item, jev_runner)
        if decision is None: continue
        item["next_hop_kind"], item["next_hop_priority"], item["decision_source"] = decision
        eligible.append(item)
    result = []
    for item in sorted(eligible, key=lambda row: (-row["next_hop_priority"], not row["gallery"], row["layer"], row["area"].casefold()))[:3]:
        area = item["area"]
        if item["next_hop_kind"] == "gallery_pick": summary = f"拍板 {area} 画廊并进实现"
        elif item["next_hop_kind"] == "story_or_retro": summary = f"提炼 {area} 复盘/技术博客"
        elif item["next_hop_kind"] == "downstream_adopt": summary = f"验证 {area} 下游接入与门禁"
        elif item["layer"] == 0: summary = f"验证 {area} 本地安装/构建读回"
        else: summary = f"实测 {area} 首轮端到端链路"
        result.append({**item, "group": "recommended", "summary": summary, "action": "源自昨日 " + item["action"], "stages": [], "topics": [area.casefold()],
                       "parts": [summary, *item["parts"]]})
    return result

def _brief_title(target: date) -> str:
    yesterday = target - timedelta(days=1)
    return f"[Daily Brief {target.isoformat()}] 昨日汇总 ({yesterday:%m-%d}) 与今日 Todo ({target:%m-%d})"

def render_brief(target: date, linear: list[dict[str, Any]], commits: list[dict[str, str]], events: list[dict[str, str]], warnings: list[str], prs: list[dict[str, Any]] | None = None, ledger_active: list[dict[str, str]] | None = None, *, jev_runner: Callable[..., Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    yesterday = target - timedelta(days=1)
    lines = [f"<!-- GTT-LINEAR-DEDUP:daily-brief-{target.isoformat()} -->", "## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）", ""]
    entries: dict[str, dict[str, Any]] = {}
    by_identifier = {str(issue.get("identifier")): issue for issue in linear if issue.get("identifier")}
    issue_urls = _reference_urls([*linear, *commits, *events, *(prs or []), *(ledger_active or [])])
    for index, issue in enumerate(linear):
        if _closed_issue(issue) or _brief_issue(issue): continue
        state = str((issue.get("state") or {}).get("name") or (issue.get("state") or {}).get("type") or "").casefold()
        raw_labels = issue.get("labels") or {}
        label_nodes = raw_labels.get("nodes", []) if isinstance(raw_labels, dict) else raw_labels
        labels = " ".join(str(x.get("name", "")) for x in label_nodes)
        group = None
        if _needs_decision(issue, labels, state): group = "needs"
        elif state in {"in progress", "in review", "started", "inprogress", "in_review"}: group = "active"
        elif (state in {"todo", "backlog"} and issue.get("priority") in {1, 2}) or re.match(rf"\[Daily Learn {re.escape(target.isoformat())}\]", str(issue.get("title", ""))): group = "planned"
        if group:
            key = str(issue.get("identifier") or issue.get("id") or f"linear-{index}")
            email_meta = _extract_email_action(issue)
            email_url = str((email_meta or {}).get("email_url") or (email_meta or {}).get("target_link") or "").split("#", 1)[0].strip()
            action_link = _link(email_url, "📧 打开原邮件") if email_url else _link(issue.get("url") or issue_urls.get(key) or (f"https://linear.app/tianpeili/issue/{key}" if re.fullmatch(r"TIA-\d+", key) else ""), "打开")
            summary_text = str((email_meta or {}).get("brief_summary") or issue.get("title") or key)
            entries[key] = {"group": group, "parts": [_item(_issue_context(issue), "Linear TIA")],
                            "summary": summary_text, "action": action_link,
                            "ref": key, "repo": _reference_context(issue, (issue.get("project") or {}).get("name", "")),
                            "topics": [_area((issue.get("project") or {}).get("name", ""), issue).casefold()], "stages": []}
    rank = {"needs": 0, "active": 1, "planned": 2}
    for item in ledger_active or []:
        identifier = item.get("identifier", "")
        bound_issue = by_identifier.get(identifier)
        if bound_issue and (_closed_issue(bound_issue) or _brief_issue(bound_issue)): continue
        group = item.get("group", "needs")
        if group not in rank: continue
        key = identifier or f"ledger:{item.get('goal', '')}"
        if key not in entries:
            parts = [_item(_issue_context(bound_issue), "Linear TIA")] if bound_issue else []
            entries[key] = {"group": group, "parts": parts, "summary": (bound_issue or {}).get("title") or item.get("title") or item.get("goal"),
                            "action": _link((bound_issue or {}).get("url") or item.get("ledger"), "打开"), "ref": identifier or item.get("goal", "goal"),
                            "repo": _reference_context(item, item.get("goal", "")), "topics": [], "stages": []}
        entry = entries[key]
        entry["topics"].append(_area(item.get("goal", ""), item).casefold())
        if not entry["repo"]: entry["repo"] = _reference_context(item, item.get("goal", ""))
        if rank[group] < rank[entry["group"]]: entry["group"] = group
        entry["parts"].append(_item(_ledger_context(item), "handoff ledger"))
        stage = "/".join(value for value in (item.get("state"), item.get("stage")) if value)
        if stage: entry["stages"].append(f"{item.get('task') or item.get('goal')}: {stage}")
    for index, item in enumerate(_recommendations(yesterday, linear, commits, events, prs or [], entries, issue_urls, jev_runner)):
        entries[f"recommended:{index}"] = item
    todos = []
    for group, heading in TODO_BUCKETS.items():
        if not any(entry["group"] == group for entry in entries.values()): continue
        if lines[-1]: lines.append("")
        lines += [f"**{heading}**", ""]
        for entry in entries.values():
            if entry["group"] != group: continue
            number = len(todos) + 1
            summary = _linked_text(entry["summary"], issue_urls, entry["repo"], limit=25) or "待办"
            refs = " / ".join([entry["ref"], *dict.fromkeys(entry["stages"])])
            reference = _linked_text(entry["ref"], issue_urls, entry["repo"])
            if not REFERENCE_RE.search(entry["ref"]): reference = _code(entry["ref"])
            for stage in dict.fromkeys(entry["stages"]):
                reference += " · " + (_linked_text(stage, issue_urls, entry["repo"]) if REFERENCE_RE.search(stage) else _code(stage))
            action = entry["action"] or _code(f"@bot {number}")
            lines.append(f"- [ ] **[{number}] {summary}** · {action} · {reference}")
            todos.append({"number": number, "text": " · ".join(dict.fromkeys(entry["parts"])), "summary": summary,
                          "action": action, "ref": refs, "group": group, "stages": list(dict.fromkeys(entry["stages"])),
                          **{key: entry[key] for key in ("next_hop_kind", "next_hop_priority", "decision_source") if key in entry}})
    if not todos: lines.append("- 无已核实待办。")
    rollup: list[list[tuple[str, str, str]]] = [[] for _ in LAYERS]
    def add_row(project: str, item: dict[str, Any], primary: str, result: Any) -> None:
        repo = _reference_context(item, project)
        refs = list(dict.fromkeys([primary, *_source_refs(item, issue_urls, repo)]))
        rollup[_layer(project, item)].append((_linked_text(project, issue_urls, repo), " · ".join(ref for ref in refs if ref), _linked_text(result, issue_urls, repo, limit=20)))
    for issue in linear:
        if not _issue_is_completed(issue, yesterday): continue
        project = (issue.get("project") or {}).get("name") or "Personal"
        identifier = str(issue.get("identifier") or issue.get("id") or "Linear")
        ref = _linked_text(identifier, issue_urls) if REFERENCE_RE.search(identifier) else _link(issue.get("url"), identifier) or _code(identifier)
        add_row(project, issue, ref, issue.get("title"))
    for commit in commits:
        add_row(_repo_name(commit["repo"]), commit, _code(commit["sha"]), commit["subject"])
    for pr in prs or []:
        repo = _reference_context(pr, pr["repo"])
        number = str(pr.get("number") or str(pr.get("url", "")).rstrip("/").rsplit("/", 1)[-1])
        ref = _link(pr.get("url"), f"#{number}" if number.isdigit() else "PR") or (_linked_text(f"#{number}", issue_urls, repo) if number.isdigit() else "PR")
        metrics = ", ".join(f"{k}={pr[k]}" for k in ("additions", "deletions", "changed_files") if pr.get(k) is not None)
        add_row(_repo_name(pr["repo"]), pr, ref, pr.get("title"))
        if metrics:
            layer = rollup[_layer(_repo_name(pr["repo"]), pr)]
            project, refs, result = layer[-1]
            layer[-1] = (project, refs + f" · {_code(metrics)}", result)
    for event in events:
        ref = str(event.get("identifier") or event.get("task") or event.get("id") or event.get("EVT", "event"))
        repo = _reference_context(event, event.get("goal", ""))
        link = _linked_text(ref, issue_urls, repo) if REFERENCE_RE.search(ref) else _link(event.get("ref") or event.get("pr") or event.get("ledger"), ref) or _code(ref)
        result = event.get("note") or event.get("title") or event.get("EVT", "完成")
        add_row(event.get("goal", "Handoff"), event, link, result)
    count = sum(len(rows) for rows in rollup)
    lines += ["", "---", "", f"## 📊 昨日完成 ({yesterday:%m-%d} · {count} 项 · workflow-hub 四大方向)"]
    for index, ((name, translation, _), rows) in enumerate(zip(LAYERS, rollup), 1):
        lines += ["", f"### {index}. {name}（{translation} · {len(rows)} 项）", ""]
        if not rows: lines.append("_无_"); continue
        lines += ["| 项目 | 单据 / PR | 一句话结果 |", "|---|---|---|"]
        for project, ref, result in rows: lines.append(f"| **{project}** | {ref} | {result} |")
    if warnings: lines += ["", "**Source warnings:**", *[f"- {_linked_text(warning, issue_urls)}" for warning in sorted(set(warnings))]]
    return "\n".join(lines).rstrip() + "\n", todos

def _load_linear_module(*, dry_run: bool = False) -> Any:
    path = Path(__file__).resolve().parents[2] / "team-lead/scripts/linear-sync.py"; spec = importlib.util.spec_from_file_location("linear_sync", path)
    if not spec or not spec.loader: return None
    module = importlib.util.module_from_spec(spec)
    old_bytecode_flag = sys.dont_write_bytecode
    try:
        if dry_run: sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode_flag
    return module

def _load_linear_client(*, dry_run: bool = False) -> Any:
    module = _load_linear_module(dry_run=dry_run)
    if module is None: return None
    key = module.load_api_key()
    return module.GraphQLClient(key, allow_refresh=not dry_run) if key else None

@contextmanager
def _proposal_lock():
    import fcntl
    path = Path(os.environ.get("LINEAR_DAILY_BRIEF_LOCK", "~/.cache/linear-daily-brief/propose.lock")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def _canceled_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state") or {}
    return any(str(state.get(key) or "").casefold() in {"canceled", "cancelled"} for key in ("type", "name"))

def _prior_daily_brief_date(issue: dict[str, Any], current_date: str) -> bool:
    if not current_date: return False
    text = f"{issue.get('title') or ''} {issue.get('description') or ''}"
    match = re.search(r"<!--\s*GTT-LINEAR-DEDUP:daily-brief-(\d{4}-\d{2}-\d{2})\s*-->", text) or re.search(r"\[Daily Brief\s+(\d{4}-\d{2}-\d{2})\]", text, re.I)
    return bool(match and match.group(1) < current_date)

def propose(client: Any, title: str, markdown: str, parent: str | None = DEFAULT_PARENT, dedup_key: str | None = None) -> dict[str, Any] | None:
    if client is None: return None
    team = client.team_metadata("TIA")
    match = re.search(r"\d{4}-\d{2}-\d{2}", title)
    current_date = match[0] if match else ""
    marker = dedup_key or (f"daily-brief-{current_date}" if current_date else "")
    if not re.fullmatch(r"daily-brief-\d{4}-\d{2}-\d{2}", marker): raise ValueError("Invalid Daily Brief marker")
    if not current_date: current_date = marker.removeprefix("daily-brief-")
    exact_comment = f"<!-- GTT-LINEAR-DEDUP:{marker} -->"
    # Enumeration includes closed issues; narrower marker helpers can create duplicates.
    issues = client.list_issues("TIA", limit=None)
    marker_matches = [issue for issue in issues if exact_comment in str(issue.get("description") or "")]
    candidates = marker_matches or [issue for issue in issues if issue.get("title") == title]
    existing = min(candidates, key=_canceled_issue) if candidates else None
    states = team.get("states", {}).get("nodes", [])
    todo = next((s for s in states if str(s.get("name", "")).casefold() == "todo" and s.get("id")), None)
    done = next((s for s in states if (str(s.get("name", "")).casefold() == "done" or str(s.get("type", "")).casefold() == "completed") and s.get("id")), None)
    if (not existing or _canceled_issue(existing)) and todo is None: raise ValueError("TIA Todo state unavailable")
    for old in issues:
        if existing and old.get("id") == existing.get("id"): continue
        if not _prior_daily_brief_date(old, current_date) or _canceled_issue(old): continue
        old_state = old.get("state") or {}
        is_done = str(old_state.get("name") or "").casefold() == "done" or str(old_state.get("type") or "").casefold() == "completed"
        has_assignee = bool((old.get("assignee") or {}).get("id"))
        if (done and not is_done) or has_assignee:
            retire_kwargs: dict[str, Any] = {"assignee_id": None, "clear_assignee": True}
            if done and not is_done: retire_kwargs["state_id"] = str(done["id"])
            client.update_issue(old["id"], **retire_kwargs)
    kwargs: dict[str, Any] = {"title": title, "description": markdown, "assignee_id": OWNER_ID}
    parent_key = str(parent or "").strip()
    if parent_key:
        parent_issue = client.issue(parent_key)
        parent_team = parent_issue.get("team") or {}
        if not parent_issue.get("id") or not (parent_team.get("key") == "TIA" or (not parent_team.get("key") and parent_team.get("id") == team["id"])):
            raise ValueError("Daily Brief parent is not verified in team TIA")
        kwargs["parent_id"] = parent_issue["id"]
    else:
        kwargs["parent_id"] = None
        projects = (team.get("projects") or {}).get("nodes") or []
        project = next((p for p in projects if str(p.get("name", "")).casefold() == DEFAULT_PROJECT.casefold() and p.get("id")), None)
        if project:
            kwargs["project_id"] = str(project["id"])
            if hasattr(client, "project_milestones"):
                ms = next((m for m in (client.project_milestones(str(project["id"])) or []) if str(m.get("name", "")).casefold() == DEFAULT_MILESTONE.casefold() and m.get("id")), None)
                if ms: kwargs["project_milestone_id"] = str(ms["id"])
    if existing:
        if _canceled_issue(existing): kwargs["state_id"] = todo["id"]
        if not parent_key: kwargs["clear_parent"] = True
        return client.update_issue(existing["id"], **kwargs)
    kwargs["team_id"] = team["id"]; kwargs["state_id"] = todo["id"]
    return client.create_issue(**kwargs)

@lru_cache(maxsize=1)
def _load_issue_brief_module() -> Any:
    path = Path(__file__).resolve().parents[2] / "issue-brief/scripts/verify_issue_format.py"
    spec = importlib.util.spec_from_file_location("daily_brief_issue_brief", path)
    if not spec or not spec.loader: raise ImportError("Canonical verify_issue_format unavailable")
    module = importlib.util.module_from_spec(spec)
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally: sys.dont_write_bytecode = old_flag
    return module

def _sanitize_human_zone_text(text: str) -> str:
    cleaned = re.sub(r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])", "", text)
    cleaned = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_-])[0-9a-f]{7,40}(?![A-Za-z0-9_-])", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" ·—-")

def render_dual_zone_brief(target: date, plan_a_markdown: str, todos: list[dict[str, Any]], *, rollup_counts: dict[str, int] | None = None) -> str:
    """Render a strict Dual-Zone issue body wrapping Plan A daily brief context inside <details>."""
    issue_brief = _load_issue_brief_module()
    yesterday = target - timedelta(days=1)
    completed_total = sum((rollup_counts or {}).values()) if rollup_counts else sum(1 for ln in plan_a_markdown.splitlines() if ln.startswith("| **"))
    todo_total = len(todos)
    bucket_label_map = {
        "needs": "待你拍板阻塞项，确认后才能继续推进",
        "active": "在途跟进任务，保持进度与质量验收",
        "planned": "今日高优计划与推荐学习事项",
        "recommended": "基于昨日完结成果顺推的下一步动作",
    }
    if todo_total:
        tldr = f"昨日共完成 {completed_total} 项产出，今日聚焦 {todo_total} 项重点待办（展示前 {min(todo_total, 8)} 项），勾选或留言即可派发执行。"
    else:
        tldr = f"昨日共完成 {completed_total} 项产出，今日暂无阻塞或待拍板事项。"

    items: list[dict[str, Any]] = []
    for row in todos[:8]:
        summary = _sanitize_human_zone_text(str(row.get("summary") or "待办事项")) or "待办事项"
        action = str(row.get("action") or "").strip()
        ref = str(row.get("ref") or "").strip()
        link_tokens = [tok for tok in (action, ref) if tok and "](" in tok]
        if link_tokens and "](" not in summary:
            what = f"{summary}（{link_tokens[0]}）"
        else:
            what = summary
        why = bucket_label_map.get(str(row.get("group") or ""), "今日重点跟进事项")
        items.append({"what": what, "why": why})
    if not items:
        items.append({"what": "保持常规巡检与节奏", "why": "当前无阻塞或高优待办事项"})

    stripped_plan_a = re.sub(r"\A<!--\s*GTT-LINEAR-DEDUP:[^\n]*-->\n?", "", plan_a_markdown).strip()
    dedup_key = f"daily-brief-{target.isoformat()}"
    rendered = issue_brief.render_dual_zone_issue(
        tldr=tldr,
        items=items,
        cursor=dedup_key,
        extra_comments=[f"<!-- GTT-LINEAR-DEDUP:{dedup_key} -->"],
        ai_context_md=stripped_plan_a or f"- `date`: `{target.isoformat()}`",
    )
    verification = issue_brief.verify_dual_zone_issue(rendered)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone daily brief verification failed: {verification['violations']}")
    return rendered


@lru_cache(maxsize=1)
def _load_multica_loop_runner() -> Any:
    path = Path(__file__).resolve().parents[2] / "team-lead/scripts/multica_loop_runner.py"
    spec = importlib.util.spec_from_file_location("daily_brief_multica_loop", path)
    if not spec or not spec.loader:
        raise ImportError("Canonical multica_loop_runner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_flag
    return module


def publish_multica_brief(
    target: date,
    *,
    title: str,
    dual_zone_markdown: str,
    server_url: str = MULTICA_DEFAULT_SERVER_URL,
    workspace_id: str = MULTICA_DEFAULT_WORKSPACE_ID,
    assignee_id: str = MULTICA_OWNER_ID,
    priority: str = "medium",
    status: str = "todo",
    dry_run: bool = False,
    multica_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    loop_mod = _load_multica_loop_runner()
    dedup_key = f"daily-brief-{target.isoformat()}"
    kwargs: dict[str, Any] = {
        "title": title,
        "description": dual_zone_markdown,
        "cursor": dedup_key,
        "assignee_id": assignee_id,
        "priority": priority,
        "status": status,
        "retire_prior_prefix": "[Daily Brief",
        "current_date": target.isoformat(),
        "server_url": server_url,
        "workspace_id": workspace_id,
        "dry_run": dry_run,
    }
    if multica_runner is not None:
        kwargs["runner"] = multica_runner
    return loop_mod.upsert_multica_dual_zone_issue(**kwargs)


def generate(target: date, *, dev_root: Path, handoff_root: Path, client: Any = None, propose_linear: bool = False, publish_multica: bool = False, dry_run: bool = False, parent: str | None = DEFAULT_PARENT, dual_zone: bool = False, multica_server_url: str = MULTICA_DEFAULT_SERVER_URL, multica_workspace_id: str = MULTICA_DEFAULT_WORKSPACE_ID, multica_assignee_id: str = MULTICA_OWNER_ID, multica_status: str = "todo", multica_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None, runner: Callable[..., Any] = subprocess.check_output, jev_runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    yesterday = target - timedelta(days=1); scope_cache: dict[str, Any] = {}
    linear, w1 = collect_linear(client, target, scope_cache=scope_cache)
    commits, w2 = collect_git(dev_root, yesterday, runner=runner)
    available_repos = tuple(f"LeePepe/{r}" for r in PERSONAL_REPOS if (dev_root / r).exists())
    prs, w4 = collect_prs(available_repos, yesterday, runner=runner)
    events, ledger_active, w3 = collect_handoff(handoff_root, target, client=client, scope_cache=scope_cache)
    plan_a_markdown, todos = render_brief(target, linear, commits, events, w1 + w2 + w3 + w4, prs, ledger_active, jev_runner=jev_runner)
    rollup_counts = {"linear": sum(_issue_is_completed(i, yesterday) for i in linear), "commits": len(commits), "prs": len(prs), "events": len(events)}
    use_dual_zone = bool(dual_zone or (publish_multica and not propose_linear))
    dual_zone_markdown = render_dual_zone_brief(target, plan_a_markdown, todos, rollup_counts=rollup_counts) if (use_dual_zone or publish_multica) else ""
    markdown = dual_zone_markdown if use_dual_zone else plan_a_markdown
    issue = None; multica_result = None; ok = True; warnings = w1 + w2 + w3 + w4; archive_result = None
    if propose_linear and not dry_run:
        try:
            if client is None: raise ValueError("Linear client unavailable")
            with _proposal_lock(): issue = propose(client, _brief_title(target), markdown, parent, f"daily-brief-{target.isoformat()}")
            if not isinstance(issue, dict) or not issue.get("id"): raise ValueError("Publication not confirmed")
        except Exception:
            ok = False
            warning = "Linear publication failed; brief was not published"
            warnings.append(warning)
            markdown += f"\n**Publication warning:** {warning}\n"
        else:
            try:
                if hasattr(client, "archive_issue"):
                    sync_mod = _load_linear_module(dry_run=False)
                    if sync_mod is not None and hasattr(sync_mod, "archive_completed_issues"):
                        archive_result = sync_mod.archive_completed_issues(
                            client, team_key="TIA", retention_days=2, max_active_issues=150, mode="auto"
                        )
            except Exception:
                pass
    if publish_multica:
        try:
            with _proposal_lock():
                multica_result = publish_multica_brief(
                    target,
                    title=_brief_title(target),
                    dual_zone_markdown=dual_zone_markdown,
                    server_url=multica_server_url,
                    workspace_id=multica_workspace_id,
                    assignee_id=multica_assignee_id,
                    status=multica_status,
                    dry_run=dry_run,
                    multica_runner=multica_runner,
                )
            if not dry_run and (not isinstance(multica_result, dict) or not multica_result.get("ok")):
                raise ValueError("Multica publication not confirmed")
        except Exception:
            ok = False
            warning = "Multica publication failed; brief was not published to Multica"
            warnings.append(warning)
    payload = {"ok": ok, "date": target.isoformat(), "yesterday": yesterday.isoformat(), "title": _brief_title(target), "dedup_key": f"daily-brief-{target.isoformat()}", "markdown": markdown, "rollup_counts": rollup_counts, "todo_items": todos, "linear_issue": issue, "warnings": sorted(set(warnings))}
    if publish_multica: payload["multica_issue"] = multica_result
    if use_dual_zone: payload["plan_a_markdown"] = plan_a_markdown
    elif dual_zone_markdown: payload["dual_zone_markdown"] = dual_zone_markdown
    if archive_result is not None: payload["archive_completed"] = archive_result
    return payload

def validate_markdown(markdown: str) -> list[str]:
    """Validate the published Plan A sink without network, credentials, or Jev."""
    errors: list[str] = []
    lines = markdown.splitlines()
    if not lines or not re.fullmatch(r"<!-- GTT-LINEAR-DEDUP:daily-brief-\d{4}-\d{2}-\d{2} -->", lines[0] if lines else ""):
        errors.append("line 1 must be a dated GTT-LINEAR-DEDUP marker")
    todo_heading = "## 🎯 今日 Todo（直接勾 `[x]` 或评论 `@bot 1, 2`）"
    completed_match = next((match for line in lines if (match := re.fullmatch(r"## 📊 昨日完成 \((\d{2}-\d{2}) · (\d+) 项 · workflow-hub 四大方向\)", line))), None)
    if todo_heading not in lines:
        errors.append("missing Todo heading")
    if completed_match is None:
        errors.append("missing completed heading with date and total")
    elif lines.index(todo_heading) > lines.index(completed_match.group(0)):
        errors.append("Todo section must precede completed section")

    bucket_indexes = {label: index for index, label in enumerate(TODO_BUCKETS.values())}
    seen_buckets: list[int] = []
    todo_numbers: list[int] = []
    recommendation_count = 0
    current_bucket = ""
    todo_pattern = re.compile(r"^- \[[ x]\] \*\*\[(\d+)\] (.+?)\*\* · (.+?) · (.+)$")
    for line in lines:
        if line.startswith("**") and line.endswith("**"):
            label = line[2:-2]
            if label in bucket_indexes:
                current_bucket = label
                seen_buckets.append(bucket_indexes[label])
        match = todo_pattern.fullmatch(line)
        if not match: continue
        if current_bucket == TODO_BUCKETS["recommended"]: recommendation_count += 1
        number, summary, action, reference = int(match[1]), match[2], match[3], match[4]
        todo_numbers.append(number)
        if len(_plain(summary)) > 25: errors.append(f"Todo [{number}] summary exceeds 25 visible characters")
        if len(re.findall(r"\]\(", action)) > 1: errors.append(f"Todo [{number}] has multiple action links")
        if number > 0 and not re.search(r"\]\(", action) and not action.startswith("`"):
            errors.append(f"Todo [{number}] has no direct link or command")
        if number > 0 and not re.search(r"\]\(", reference) and REFERENCE_RE.search(reference):
            errors.append(f"Todo [{number}] reference is not clickable")
        if number > 0 and REFERENCE_RE.search(line):
            for token in REFERENCE_RE.findall(line):
                if not _has_reference_link(line, token): errors.append(f"Todo [{number}] reference {token} is not clickable")
    if seen_buckets != sorted(set(seen_buckets)):
        errors.append("Todo buckets must be nonempty and remain in fixed order")
    if todo_numbers and todo_numbers != list(range(1, len(todo_numbers) + 1)):
        errors.append("Todo numbering must be continuous from [1]")
    if recommendation_count > 3: errors.append("recommended Todo bucket exceeds 3 items")

    if completed_match:
        expected_total = int(completed_match.group(2))
        heading_indexes = []
        for number, (name, _, _) in enumerate(LAYERS, 1):
            prefix = f"### {number}. {name}（"
            matches = [i for i, line in enumerate(lines) if line.startswith(prefix)]
            if len(matches) != 1:
                errors.append(f"completed layer {number} heading missing or duplicated")
                continue
            heading_indexes.append(matches[0])
            start = matches[0] + 1
            end = next((i for i in range(start, len(lines)) if lines[i].startswith("### ") or lines[i].startswith("## ")), len(lines))
            section = lines[start:end]
            table_rows = [line for line in section if line.startswith("| **")]
            if not table_rows and "_无_" not in section: errors.append(f"completed layer {number} must contain a table or _无_")
            for row in table_rows:
                cells = row.strip("|").split("|")
                if len(cells) != 3: errors.append(f"completed layer {number} has malformed table row")
                elif len(_plain(cells[2].strip())) > 20: errors.append(f"completed layer {number} result exceeds 20 visible characters")
                for token in REFERENCE_RE.findall(row):
                    if not _has_reference_link(row, token): errors.append(f"completed reference {token} is not clickable")
        if heading_indexes != sorted(heading_indexes): errors.append("completed layers must remain in fixed order")
        if sum(1 for line in lines if line.startswith("| **")) != expected_total:
            errors.append("completed heading total does not match table rows")
    # Code spans and fenced blocks are literal text: link-looking references inside
    # them are not clickable. Inspect line-local spans to avoid crossing newlines.
    in_fence = False
    fence_has_ref = False
    for line in lines:
        fence = re.match(r"^\s*(```|~~~)", line)
        if fence:
            if in_fence:
                if fence_has_ref: errors.append("issue/PR links hidden in fenced code are not clickable")
                in_fence = False; fence_has_ref = False
            else:
                in_fence = True; fence_has_ref = bool(re.search(r"\[(?:TIA-\d+|#\d+)\]\([^)]*\)", line))
            continue
        if in_fence:
            fence_has_ref |= bool(re.search(r"\[(?:TIA-\d+|#\d+)\]\([^)]*\)", line))
            continue
        spans = re.findall(r"`([^`]*)`", line)
        if any(re.search(r"\[(?:TIA-\d+|#\d+)\]\([^)]*\)", span) for span in spans):
            errors.append("issue/PR links hidden in code spans are not clickable")
    if in_fence and fence_has_ref: errors.append("issue/PR links hidden in fenced code are not clickable")
    # Catch bare references anywhere in the rendered contract, including source rows.
    links_removed = re.sub(r"\[([^]]+)\]\([^)]*\)", "", markdown)
    if REFERENCE_RE.search(links_removed): errors.append("all displayed TIA/# references must be clickable")
    return list(dict.fromkeys(errors))


def _read_verification_input(path: str) -> str:
    if path == "-": return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="today")
    parser.add_argument("--propose-linear", action="store_true")
    parser.add_argument("--publish-multica", action="store_true", help="Publish Dual-Zone Daily Brief to NAS Multica")
    parser.add_argument("--target", choices=("linear", "multica", "both"), default=None, help="Target board(s) to publish")
    parser.add_argument("--multica-server-url", default=MULTICA_DEFAULT_SERVER_URL)
    parser.add_argument("--multica-workspace-id", default=MULTICA_DEFAULT_WORKSPACE_ID)
    parser.add_argument("--multica-assignee-id", default=MULTICA_OWNER_ID)
    parser.add_argument("--multica-status", default="todo")
    parser.add_argument("--parent-issue", default=DEFAULT_PARENT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dual-zone", action="store_true", help="Render Daily Brief using Dual-Zone issue-brief contract")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-markdown", metavar="PATH")
    args = parser.parse_args(argv)
    if args.verify_markdown is not None:
        try:
            raw_md = _read_verification_input(args.verify_markdown)
            if args.dual_zone:
                dz_report = _load_issue_brief_module().verify_dual_zone_issue(raw_md)
                errors = [] if dz_report["ok"] else [v["message"] for v in dz_report["violations"]]
            else:
                errors = validate_markdown(raw_md)
        except (OSError, UnicodeError) as exc: errors = [f"cannot read Markdown input: {type(exc).__name__}"]
        if errors:
            print("Markdown verification failed: " + "; ".join(errors), file=sys.stderr); return 1
        print("Markdown verification passed"); return 0
    propose_linear = bool(args.propose_linear or args.target in {"linear", "both"})
    publish_multica = bool(args.publish_multica or args.target in {"multica", "both"})
    dual_zone = bool(args.dual_zone or (publish_multica and not propose_linear))
    try: client = _load_linear_client(dry_run=args.dry_run)
    except Exception: client = None
    result = generate(
        parse_date(args.date),
        dev_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_DEV_ROOT", "~/Development")).expanduser(),
        handoff_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_HANDOFF_ROOT", "~/Development/handoff")).expanduser(),
        client=client,
        propose_linear=propose_linear,
        publish_multica=publish_multica,
        dry_run=args.dry_run,
        parent=args.parent_issue,
        dual_zone=dual_zone,
        multica_server_url=args.multica_server_url,
        multica_workspace_id=args.multica_workspace_id,
        multica_assignee_id=args.multica_assignee_id,
        multica_status=args.multica_status,
    )
    print(json.dumps(result, ensure_ascii=False) if args.json else result["markdown"], end="" if args.json else ""); return 0 if result["ok"] else 1
if __name__ == "__main__": raise SystemExit(main())
