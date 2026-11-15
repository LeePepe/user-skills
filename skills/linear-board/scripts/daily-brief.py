#!/usr/bin/env python3
"""Render and optionally publish the personal Linear daily brief."""
from __future__ import annotations
import argparse, importlib.util, json, os, re, subprocess, sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

CST = timezone(timedelta(hours=8))
OWNER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
DEFAULT_PARENT = "TIA-375"
PERSONAL_REPOS = ("user-skills", "VoxPocket", "AIDash", "shared-design-system", "Financial", "leepepe.github.io")
EXCLUDED_TERMS = ("work business", "bing", "workplace")
LEDGER_TOKENS = ("work", "sapphire", "flightbook", "webiq", "bing", "devsync")
BLOCKING = {"NEED_GO", "NEED_OWNER_DECISION", "TASK_BLOCKED", "INPUT_BLOCKED", "REVIEW_FAIL"}
CLEARING = {"ACK", "RESOLVED", "DONE", "REVIEW_PASS", "PR_MERGED", "GOAL_DONE"}
EVENT_RE = re.compile(r"^\s*[-*]\s+(?P<stamp>\S+)\s+\|\s+(?P<body>.*)$")

def parse_date(value: str) -> date:
    return datetime.now(CST).date() if value == "today" else date.fromisoformat(value)

def _timestamp(value: str) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(CST)
    except (TypeError, ValueError): return None

def _date_in(value: str, target: date) -> bool:
    parsed = _timestamp(value)
    return parsed is not None and parsed.date() == target

def _excluded_text(values: list[str], root_self: bool = False) -> bool:
    text = " ".join(values)
    if root_self and re.search(r"(?<!\w)\[root\](?!\w)", text, re.I): return True
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(x) for x in EXCLUDED_TERMS) + r")(?!\w)"
    return bool(re.search(pattern, text, re.I))

def _excluded_issue(issue: dict[str, Any], client: Any = None, cache: dict[str, Any] | None = None) -> bool:
    cache = {} if cache is None else cache
    current = issue
    seen: set[str] = set()
    first = True
    while isinstance(current, dict):
        title = str(current.get("title") or "")
        project = current.get("project") or {}
        team = current.get("team") or {}
        if _excluded_text([title, str(project.get("name") or ""), str(team.get("name") or "")], root_self=first): return True
        first = False
        parent = current.get("parent") or {}
        parent_id = str(parent.get("id") or "")
        if not parent_id:
            if not isinstance(parent, dict) or not parent.get("title"): break
            current = parent; first = False; continue
        if parent_id in seen: break
        seen.add(parent_id)
        if client is not None and (not parent.get("parent") or not parent.get("team") or not parent.get("project")):
            if parent_id not in cache:
                try: cache[parent_id] = client.issue_scope(parent_id)
                except Exception: return True
            parent = cache[parent_id]
        current = parent
    return False

def _issue_is_completed(issue: dict[str, Any], target: date) -> bool:
    state = str((issue.get("state") or {}).get("type") or "").casefold()
    if state in {"canceled", "cancelled"}: return False
    completed = str(issue.get("completedAt") or "")
    if completed: return _date_in(completed, target)
    return state == "completed" and _date_in(str(issue.get("updatedAt") or ""), target)

def collect_linear(client: Any, target: date, *, scope_cache: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    if client is None: return [], ["Linear source unavailable; no issue data was fabricated"]
    try: issues = client.list_issues("TIA", limit=None)
    except Exception: return [], ["Linear source unavailable; no issue data was fabricated"]
    cache = scope_cache if scope_cache is not None else {}
    eligible = [i for i in issues if isinstance(i, dict) and not _excluded_issue(i, client, cache)]
    return eligible, []

def _run(command: list[str], *, timeout: int = 15, runner: Callable[..., Any] = subprocess.check_output) -> str:
    return str(runner(command, text=True, stderr=subprocess.DEVNULL, timeout=timeout))

def collect_git(dev_root: Path, target: date, *, runner: Callable[..., Any] = subprocess.check_output) -> tuple[list[dict[str, str]], list[str]]:
    rows, warnings = [], []
    start, end = f"{target} 00:00 +0800", f"{target + timedelta(days=1)} 00:00 +0800"
    for name in PERSONAL_REPOS:
        repo = dev_root / name
        if not (repo / ".git").exists(): continue
        try: out = _run(["git", "-C", str(repo), "log", "--all", "--since-as-filter", start, "--until", end, "--format=%H%x09%h%x09%s%x09%aI"], runner=runner)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired): warnings.append(f"Git source unavailable: {name}"); continue
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) == 4 and _date_in(parts[3], target): rows.append({"repo": name, "sha": parts[1], "subject": parts[2], "timestamp": parts[3]})
    return rows, warnings

def collect_prs(repos: tuple[str, ...], target: date, *, runner: Callable[..., Any] = subprocess.check_output) -> tuple[list[dict[str, Any]], list[str]]:
    rows, warnings = [], []
    start = datetime.combine(target, datetime.min.time(), CST).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end = datetime.combine(target + timedelta(days=1), datetime.min.time(), CST).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for repo in repos:
        try:
            raw = _run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls?state=closed&per_page=100"], runner=runner)
            data = json.loads(raw)
            if isinstance(data, list) and data and all(isinstance(page, list) for page in data): data = [item for page in data for item in page]
            if isinstance(data, dict): data = data.get("items", [])
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError): warnings.append(f"PR source unavailable: {repo}"); continue
        for pr in data or []:
            merged = pr.get("merged_at")
            if merged and start <= merged < end:
                rows.append({"repo": repo, "title": pr.get("title", ""), "url": pr.get("html_url", ""), "additions": pr.get("additions"), "deletions": pr.get("deletions"), "changed_files": pr.get("changed_files")})
    return rows, warnings

def _ledger_excluded(path: Path, lines: list[str]) -> bool:
    name = path.parent.name.casefold()
    if any(re.match(rf"^{re.escape(token)}(?:[-_]|$)", name) for token in LEDGER_TOKENS): return True
    header = "\n".join(lines[:40])
    return bool(re.search(r"(?im)^\s*(?:project|repo|linear)\s*:\s*.*\b(?:work business|bing|sapphire|flightbook|webiq|devsync)\b", header))

def _parse_ledger(path: Path, target: date) -> tuple[list[dict[str, str]], list[str]]:
    try: lines = path.read_text(encoding="utf-8").splitlines()
    except OSError: return [], [f"Ledger unreadable: {path.parent.name}"]
    if _ledger_excluded(path, lines): return [], []
    events = []
    for line in lines:
        match = EVENT_RE.match(line)
        if not match: continue
        stamp = _timestamp(match.group("stamp")); body = match.group("body")
        fields = {k.strip(): v.strip() for k, v in (part.split("=", 1) for part in body.split(" | ") if "=" in part)}
        if stamp is None: continue
        fields.update({"timestamp": match.group("stamp"), "line": line.strip(), "goal": path.parent.name})
        events.append(fields)
    return events, []

def _active_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    active: dict[str, dict[str, str]] = {}; unkeyed: list[dict[str, str]] = []
    for event in events:
        typ = event.get("EVT", "").upper(); key = event.get("id") or event.get("task") or event.get("goal", "")
        if typ in BLOCKING: active[key] = event
        elif typ in CLEARING:
            active.pop(key, None)
            for k in list(active):
                if event.get("task") and active[k].get("task") == event["task"]: active.pop(k)
    return list(active.values())

def collect_ledgers(handoff_root: Path, target: date) -> tuple[list[str], list[str]]:
    events, warnings = [], []
    if not handoff_root.exists(): return [], ["Handoff ledger source unavailable"]
    for ledger in sorted(handoff_root.glob("*/ledger.md")):
        parsed, warns = _parse_ledger(ledger, target); warnings.extend(warns)
        for event in parsed:
            if _date_in(event.get("timestamp", ""), target) and event.get("EVT", "").upper() in {"DONE", "PR_MERGED", "GOAL_DONE"}:
                events.append(f"{event['goal']}: {event['line']}")
    return events, warnings

def _item(text: str, source: str) -> str: return f"{text} _(source: {source})_"
def _issue_context(issue: dict[str, Any]) -> str:
    project = (issue.get("project") or {}).get("name") or "personal"
    return f"{issue.get('identifier', 'Linear')} {issue.get('title', '')} [{project}] {issue.get('url', '')}".strip()

def render_brief(target: date, linear: list[dict[str, Any]], commits: list[dict[str, str]], events: list[str], warnings: list[str], prs: list[dict[str, Any]] | None = None, ledger_active: list[dict[str, str]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    yesterday = target - timedelta(days=1); marker = f"<!-- GTT-LINEAR-DEDUP:daily-brief-{target.isoformat()} -->"
    title = f"[Daily Brief {target.isoformat()}] 昨日汇总 ({yesterday:%m-%d}) 与今日 Todo ({target:%m-%d})"
    lines = [marker, f"# {title}", "", f"## 📊 昨日交付汇总（Yesterday's Rollup · {yesterday.isoformat()}）", ""]
    groups: dict[str, list[str]] = {}
    for issue in linear:
        if not _issue_is_completed(issue, yesterday): continue
        key = (issue.get("project") or {}).get("name") or "Personal / Unassigned"; groups.setdefault(key, []).append(_item(_issue_context(issue), "Linear TIA"))
    for commit in commits: groups.setdefault(commit["repo"], []).append(_item(f"{commit['repo']} `{commit['sha']}` {commit['subject']}", "Git"))
    for pr in prs or []:
        metrics = ", ".join(f"{k}={pr[k]}" for k in ("additions", "deletions", "changed_files") if pr.get(k) is not None)
        groups.setdefault(pr["repo"], []).append(_item(f"{pr['title']} {pr['url']}" + (f" ({metrics})" if metrics else ""), "GitHub PR"))
    for event in events: groups.setdefault("Handoff", []).append(_item(event, "handoff ledger"))
    if groups:
        for group, items in groups.items(): lines += [f"### {group}"] + [f"- {x}" for x in items] + [""]
    else: lines.append("- No verified personal deliveries found for this date.\n")
    if warnings: lines += ["**Source warnings:**"] + [f"- {w}" for w in sorted(set(warnings))] + [""]
    lines += [f"## 🎯 今日 Todo 与待拍板清单（Today's Actionable Todos · {target.isoformat()}）", ""]
    needs, active, planned = [], [], []
    for issue in linear:
        if str((issue.get("state") or {}).get("type") or "").casefold() in {"canceled", "cancelled"}: continue
        state = str((issue.get("state") or {}).get("name") or (issue.get("state") or {}).get("type") or "").casefold()
        raw_labels = issue.get("labels") or {}
        label_nodes = raw_labels.get("nodes", []) if isinstance(raw_labels, dict) else raw_labels
        labels = " ".join(str(x.get("name", "")) for x in label_nodes)
        text = f"{issue.get('title', '')} {labels}"; context = _item(_issue_context(issue), "Linear TIA")
        if re.search(r"(?i)\b(blocked|needs decision|owner decision)\b", text) or state in {"blocked", "input blocked"}: needs.append(context)
        elif state in {"in progress", "in review", "started", "inprogress", "in_review"}: active.append(context)
        elif (state in {"todo", "backlog"} and int(issue.get("priority") or 0) in {1, 2}) or re.search(rf"\[Daily Learn {re.escape(target.isoformat())}\]", str(issue.get("title", ""))): planned.append(context)
    for event in ledger_active or []: needs.append(_item(f"{event.get('goal', 'goal')}: {event.get('EVT', '')} {event.get('task', '')}", "handoff ledger"))
    groups = [("### 🚨 待你拍板 / 选型 (Needs Owner Decision)", needs), ("### 🔨 在途推进中 (In Progress Goals)", active), ("### 📌 今日计划待办 & 每日学习 (Planned Todos & Daily Learn)", planned)]
    todos = []; number = 1
    for heading, items in groups:
        lines += [heading]
        if not items: lines.append("- None verified.")
        for item in items:
            lines.append(f"- [ ] **[{number}]** {item}"); todos.append({"number": number, "text": item}); number += 1
        lines.append("")
    lines += ["## 🤖 @bot 一键点单指令栏", "", "Reply with `@bot 执行 1, 2`; textual comments are pollable immediately. Native Delegate/mention requires workspace-admin OAuth grants `app:assignable`/`app:mentionable`. The bridge routes numbered lines with source identifiers and reassigns verified results to the Owner."]
    return "\n".join(lines).rstrip() + "\n", todos

def _load_linear_client(*, dry_run: bool = False) -> Any:
    path = Path(__file__).resolve().parents[2] / "team-lead/scripts/linear-sync.py"; spec = importlib.util.spec_from_file_location("linear_sync", path)
    if not spec or not spec.loader: return None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); key = module.load_api_key()
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

def propose(client: Any, title: str, markdown: str, parent: str = DEFAULT_PARENT, dedup_key: str | None = None) -> dict[str, Any] | None:
    if client is None: return None
    team = client.team_metadata("TIA"); marker = dedup_key or f"daily-brief-{re.search(r'\d{4}-\d{2}-\d{2}', title).group(0)}"
    existing = None
    try:
        for issue in client.list_issues("TIA", limit=None):
            if marker in str(issue.get("description") or "") or issue.get("title") == title:
                existing = issue; break
    except Exception:
        existing = client.find_issue_by_dedup_marker(team["id"], marker) or client.find_issue_by_title("TIA", title)
    todo = next((s for s in team.get("states", {}).get("nodes", []) if str(s.get("name", "")).casefold() == "todo"), None)
    parent_issue = client.issue(parent)
    parent_team = (parent_issue.get("team") or {}).get("key")
    if parent_team and parent_team != "TIA": raise ValueError("Daily Brief parent is not in team TIA")
    kwargs = {"title": title, "description": markdown, "parent_id": parent_issue["id"], "assignee_id": OWNER_ID}
    if existing:
        if str((existing.get("state") or {}).get("type", "")).casefold() in {"canceled", "cancelled"}: kwargs["state_id"] = (todo or {}).get("id")
        return client.update_issue(existing["id"], **kwargs)
    kwargs["team_id"] = team["id"]; kwargs["state_id"] = (todo or {}).get("id"); return client.create_issue(**kwargs)

def generate(target: date, *, dev_root: Path, handoff_root: Path, client: Any = None, propose_linear: bool = False, dry_run: bool = False, parent: str = DEFAULT_PARENT, runner: Callable[..., Any] = subprocess.check_output) -> dict[str, Any]:
    yesterday = target - timedelta(days=1); scope_cache: dict[str, Any] = {}
    linear, w1 = collect_linear(client, target, scope_cache=scope_cache)
    commits, w2 = collect_git(dev_root, yesterday, runner=runner)
    available_repos = tuple(f"LeePepe/{r}" for r in PERSONAL_REPOS if (dev_root / r).exists())
    prs, w4 = collect_prs(available_repos, yesterday, runner=runner)
    events, w3 = collect_ledgers(handoff_root, yesterday)
    all_ledger_events: list[dict[str, str]] = []
    for ledger in sorted(handoff_root.glob("*/ledger.md")) if handoff_root.exists() else []:
        parsed, _ = _parse_ledger(ledger, target); all_ledger_events.extend(parsed)
    markdown, todos = render_brief(target, linear, commits, events, w1 + w2 + w3 + w4, prs, _active_events(all_ledger_events))
    issue = None; ok = True; warnings = w1 + w2 + w3 + w4
    if propose_linear and not dry_run:
        try:
            with _proposal_lock(): issue = propose(client, markdown.splitlines()[1][2:], markdown, parent, f"daily-brief-{target.isoformat()}")
        except Exception: ok = False; warnings.append("Linear publication failed; brief was not published")
    return {"ok": ok, "date": target.isoformat(), "yesterday": yesterday.isoformat(), "title": markdown.splitlines()[1][2:], "dedup_key": f"daily-brief-{target.isoformat()}", "markdown": markdown, "rollup_counts": {"linear": sum(_issue_is_completed(i, yesterday) for i in linear), "commits": len(commits), "prs": len(prs), "events": len(events)}, "todo_items": todos, "linear_issue": issue, "warnings": sorted(set(warnings))}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--date", default="today"); parser.add_argument("--propose-linear", action="store_true"); parser.add_argument("--parent-issue", default=DEFAULT_PARENT); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--json", action="store_true"); args = parser.parse_args(argv)
    client = _load_linear_client(dry_run=args.dry_run)
    result = generate(parse_date(args.date), dev_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_DEV_ROOT", "~/Development")).expanduser(), handoff_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_HANDOFF_ROOT", "~/Development/handoff")).expanduser(), client=client, propose_linear=args.propose_linear, dry_run=args.dry_run, parent=args.parent_issue)
    print(json.dumps(result, ensure_ascii=False) if args.json else result["markdown"], end="" if args.json else ""); return 0 if result["ok"] else 1
if __name__ == "__main__": raise SystemExit(main())
