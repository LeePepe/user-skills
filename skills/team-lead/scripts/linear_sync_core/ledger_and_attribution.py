"""Domain routing, handoff ledger parsing, and R10 execution/cost attribution."""
from __future__ import annotations

from ._imports import *

def _state(team: dict[str, Any], name: str, fallback: str | None = None) -> dict[str, Any] | None:
    states = team.get("states", {}).get("nodes", [])
    wanted = name.casefold()
    for state in states:
        if str(state.get("name", "")).casefold() == wanted:
            return state
    if fallback:
        return _state(team, fallback)
    return None


def _label(team: dict[str, Any], name: str) -> dict[str, Any] | None:
    wanted = name.casefold()
    for label in team.get("labels", {}).get("nodes", []):
        if str(label.get("name", "")).casefold() == wanted:
            return label
    return None


def _infer_project_name(explicit_project: str | None, text: str, repo_text: str = "") -> str:
    if explicit_project and explicit_project.strip():
        return explicit_project.strip()
    ledger_project = re.search(r"(?mi)^\s*-\s*Project:\s*(\S.*?)\s*$", text)
    if ledger_project:
        return ledger_project.group(1).strip()
    cleaned_text = re.sub(r"(?i)(?:contract\s*=\s*|合同(?:版本|sha)\s*:\s*)team-lead\S*", "", text)
    combined = f"{cleaned_text} {repo_text}".casefold()
    if any(keyword in combined for keyword in (
        "bing", "sapphire", "flightbook", "oneauth", "serp", "fastlane",
        "dsat", "cer", "msn", "san sa",
    )):
        return "Bing"
    if any(keyword in combined for keyword in (
        "user-skills", "workflow", "team-lead", "linear-board", "bot-tl",
        "retro-loop", "story-craft", "herdr", "launchd", "cron", "6dq",
        "shared-ci", "skill",
    )):
        return "Agent Workflow"
    if any(keyword in combined for keyword in (
        "vitalstride", "voxpocket", "aidash", "localis", "financial",
        "fin-", "workout", "activeworkout", "webiq", "strata",
    )):
        return "Personal project"
    return "Agent Workflow"


def _project_id(team: dict[str, Any], project_name: str | None) -> str | None:
    if not project_name:
        return None
    wanted = project_name.casefold()
    for project in (team.get("projects") or {}).get("nodes") or []:
        if str(project.get("name", "")).casefold() == wanted:
            project_id = project.get("id")
            return str(project_id) if project_id else None
    return None


DOMAIN_MAP_PATH = Path(__file__).resolve().parents[3] / "personal-m2-assistant" / "references" / "m2-domain-map.json"
DOMAIN_PROJECT_NAMES = {
    "work-business": "Work Business",
    "user-skills": "user-skills",
    "personal-core-gateways": "Personal Core & Gateways",
    "personal-products-home": "Personal Products & Home",
}
EXTRA_TOPIC = {"topic_id": "financial", "group": "personal-products-home", "title": "Financial"}
ROOT_NAME_OVERRIDES = {
    "sapphire-china-bugs": "China Release",
    "sapphire-notification": "Sapphire Notification",
    "sapphire-smartbanner": "Sapphire SmartBanner",
}


def _domain_topics() -> list[dict[str, str]]:
    try:
        data = json.loads(DOMAIN_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    topics: list[dict[str, str]] = []
    for group in data.get("groups", []) if isinstance(data, dict) else []:
        if not isinstance(group, dict):
            continue
        for topic in group.get("topics", []) if isinstance(group.get("topics"), list) else []:
            if isinstance(topic, dict) and topic.get("topic_id"):
                topics.append({
                    "topic_id": str(topic["topic_id"]),
                    "group": str(topic.get("group") or ""),
                    "title": str(topic.get("title") or topic["topic_id"]),
                    "keywords": " ".join(str(x) for x in topic.get("keywords", [])),
                    "aliases": " ".join(str(x) for x in topic.get("aliases", [])),
                })
    topics.append(EXTRA_TOPIC)
    return topics


def _goal_topic(goal_name: str, ledger_text: str, handoff_dir: Path | None = None) -> dict[str, str] | None:
    goal_key = str(goal_name or "").strip().casefold()
    topics = {topic["topic_id"]: topic for topic in _domain_topics()}
    exact = {
        "project-root-tracker": "skills-workflow",
        "ios-snapshot-skill": "skills-repo-ci",
        "devsync1030": "sapphire-china-bugs",
    }
    if goal_key in exact:
        return topics.get(exact[goal_key])
    text = f"{goal_name} {ledger_text} {handoff_dir or ''}".casefold()
    explicit = (
        ("sapphire-china-bugs", ("devsync", "china release", "bing cn", "bingchina", "ab114", "manageapps")),
        ("sapphire-notification", ("notification", "push", "mai notification")),
        ("sapphire-smartbanner", ("smartbanner", "universal link", "universal_link", "upsell")),
        ("aidash-aidata", ("aidash", "aidata")),
        ("dsh-plugins", ("dsh", "deepseek harness", "session sink", "omni hub")),
        ("gateway-herdr-jev", ("herdr", "magpie", "falcon", "gateway", "copilot-bg-watch")),
        ("financial", ("financial", "fin-", "fin product")),
        ("voxpocket", ("voxpocket", "vox-pocket")),
        ("vitalstride", ("vitalstride", "workout")),
        ("helix", ("helix",)),
        ("davici", ("davici",)),
        ("nas-smart-home", ("nas", "home assistant", "node-red", "mijia")),
        ("learning-and-blog", ("learning", "blog", "learn-", "knowledge vault")),
        ("skills-design", ("designer", "design", "visual", "studio")),
        ("skills-research-knowledge", ("research", "knowledge", "digest")),
        ("skills-retro-story", ("retro", "story-craft", "story maintenance")),
        ("skills-repo-ci", ("repo", "skill-contract", "snapshot", "ci", "precommit", "test")),
    )
    for topic_id, markers in explicit:
        if any(marker in text for marker in markers):
            return topics.get(topic_id) or {"topic_id": topic_id, "group": "", "title": topic_id}
    if any(marker in text for marker in ("workflow", "m2", "linear", "team-lead", "dispatcher", "skills")):
        return topics.get("skills-workflow")
    return None


def _root_name(topic: dict[str, str]) -> str:
    return ROOT_NAME_OVERRIDES.get(topic.get("topic_id", ""), topic.get("title", topic.get("topic_id", "")))


def _parse_events(text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for match in EVENT_RE.finditer(text):
        fields: dict[str, str] = {}
        for field in match.group(1).split(" | "):
            if "=" in field:
                key, value = field.split("=", 1)
                fields[key.strip()] = value.strip()
        if fields.get("EVT"):
            events.append(fields)
    return events


def _normalize_task_status(raw_status: str) -> str:
    token = str(raw_status or "").split(",", 1)[0].split(":", 1)[0].strip().casefold()
    return token


def _active_blocking_events(
    events: list[dict[str, str]],
    tasks: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    blocking_types = {
        "TASK_BLOCKED",
        "INPUT_BLOCKED",
        "REVIEW_FAIL",
        "NEED_GO",
        "NEED_OWNER_DECISION",
    }
    clearing_types = {"RESOLVED", "ACK", "DONE", "REVIEW_PASS", "PR_MERGED", "GOAL_DONE"}
    active: dict[str, dict[str, str]] = {}
    unkeyed: list[dict[str, str]] = []
    for event in events:
        event_type = event.get("EVT", "").upper()
        event_id = event.get("id", "")
        task_id = event.get("task", "")
        if event_type in blocking_types:
            if event_id:
                active[event_id] = event
            else:
                unkeyed.append(event)
        elif event_type in clearing_types:
            if event_id:
                active.pop(event_id, None)
            if task_id:
                for k in [k for k, v in active.items() if v.get("task") == task_id]:
                    active.pop(k, None)
                unkeyed = [v for v in unkeyed if v.get("task") != task_id]
    if tasks is not None:
        resolved_tasks = {
            str(t.get("task_id", ""))
            for t in tasks
            if _normalize_task_status(t.get("status", "")) in {"done", "cancelled", "in_review"}
        }
        if resolved_tasks:
            active = {k: v for k, v in active.items() if v.get("task") not in resolved_tasks}
            unkeyed = [v for v in unkeyed if v.get("task") not in resolved_tasks]
        active_tasks = {v.get("task") for v in [*active.values(), *unkeyed] if v.get("task")}
        for t in tasks:
            if _normalize_task_status(t.get("status", "")) == "blocked" and t.get("task_id") not in active_tasks:
                unkeyed.append({"EVT": "TASK_BLOCKED", "task": str(t.get("task_id", "")), "note": str(t.get("status", ""))})
    return [*active.values(), *unkeyed]


def _parse_tasks(ledger_text: str, queue_data: dict[str, Any] | None) -> list[dict[str, str]]:
    queue_by_id = {
        str(task.get("task_id")): task
        for task in (queue_data or {}).get("tasks", [])
        if task.get("task_id")
    }
    tasks: list[dict[str, str]] = []
    in_table = False
    headers: dict[str, int] | None = None
    aliases = {
        "id": {"id", "task", "task id"},
        "title": {"title", "标题"},
        "status": {"status", "状态"},
        "pr": {"pr", "pr/head", "pr / head"},
        "owner": {"owner", "负责", "写者"},
        "receipts": {"receipts", "验收证据／回执"},
    }
    for line in ledger_text.splitlines():
        if line.strip() == "## Tasks":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not line.startswith("|") or line.count("|") < 2:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        normalized = [re.sub(r"\s+", " ", c.casefold()) for c in cells]
        if normalized and all(set(c) <= {"-", ":"} for c in normalized):
            continue
        if normalized and any(normalized[0] == value for value in aliases["id"]):
            in_table = True
            headers = {}
            for index, cell in enumerate(normalized):
                for key, names in aliases.items():
                    if cell in names:
                        headers[key] = index
                        break
            continue
        if not in_table:
            continue
        if headers:
            get = lambda key: cells[headers[key]] if headers.get(key, 999) < len(cells) else ""
            task_id, title, status, pr = get("id"), get("title"), get("status"), get("pr")
        else:
            task_id, _, title, _, status, pr = (cells + [""] * 6)[:6]
        if not re.match(r"^[A-Za-z0-9_.-]+$", task_id):
            continue
        queued = queue_by_id.get(task_id, {})
        tasks.append(
            {
                "task_id": task_id,
                "title": str(queued.get("title") or title),
                "status": str(queued.get("status") or status),
                "pr": str(queued.get("pr") or pr or ""),
            }
        )
    return tasks


def _load_handoff(handoff_dir: Path) -> tuple[str, list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    ledger_path = handoff_dir / "ledger.md"
    if not ledger_path.is_file():
        raise LinearSyncError(f"Missing ledger: {ledger_path}")
    ledger_text = _read_ledger(handoff_dir)
    queue_data: dict[str, Any] | None = None
    queue_path = handoff_dir / "queue.json"
    if queue_path.is_file():
        try:
            loaded = json.loads(queue_path.read_text(encoding="utf-8"))
            queue_data = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            queue_data = None
    goal_match = re.search(r"(?m)^#\s*Goal:\s*(.+?)\s*$", ledger_text)
    goal_name = goal_match.group(1).strip() if goal_match else handoff_dir.name
    header: dict[str, str] = {}
    for line in ledger_text.splitlines():
        if line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            header[key.strip().casefold()] = value.strip()
    return goal_name, _parse_tasks(ledger_text, queue_data), _parse_events(ledger_text), header


def _has_meaningful_attribution(meta: dict[str, Any] | None) -> bool:
    if not meta:
        return False
    if any(meta.get(key) not in (None, "", "—") for key in ("tokens", "cost")):
        return True
    try:
        return float(meta.get("duration_sec", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _infer_github_repo(goal_name: str, ledger_text: str, handoff_dir: Path | None = None) -> str | None:
    repo_match = re.search(r"\brepo=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", ledger_text)
    if repo_match:
        return repo_match.group(1)
    combined = f"{goal_name} {ledger_text} {handoff_dir or ''}".casefold()
    if "financial" in combined or "fin-" in combined:
        return "LeePepe/financial"
    if "vitalstride" in combined or "workout" in combined:
        return "LeePepe/VitalStride"
    if "voxpocket" in combined or "voxkit" in combined:
        return "LeePepe/VoxPocket"
    if "aidash" in combined:
        return "LeePepe/AIDash"
    if "user-skills" in combined or "linear-sync" in combined or "workflow-hub" in combined:
        return "LeePepe/user-skills"
    return None


def _format_pr_ref(raw_pr: str, repo: str | None) -> str:
    text = str(raw_pr or "").strip()
    if not text or text in {"—", "-"}:
        return ""
    if "http://" in text or "https://" in text:
        return text
    if repo:
        def _repl(m: re.Match[str]) -> str:
            num = m.group(1)
            return f"[PR #{num}](https://github.com/{repo}/pull/{num})"
        linked = re.sub(r"(?:PR\s*#?|#)(\d+)", _repl, text, flags=re.IGNORECASE)
        if linked != text:
            return linked
    return text


def _build_status_bar(tasks: list[dict[str, str]]) -> str:
    if not tasks:
        return ""
    total = len(tasks)
    done_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "done"]
    review_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "in_review"]
    progress_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) in {"in_progress", "changes_requested"}]
    blocked_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "blocked"]
    planned_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) in {"planned", "ready"}]
    parts = [f"`{len(done_list)}/{total} 已完成`"]
    if review_list:
        ids = ", ".join(t["task_id"] for t in review_list[:3])
        parts.append(f"`{len(review_list)} 审查中 ({ids})`")
    if progress_list:
        ids = ", ".join(t["task_id"] for t in progress_list[:3])
        parts.append(f"`{len(progress_list)} 进行中 ({ids})`")
    if blocked_list:
        ids = ", ".join(t["task_id"] for t in blocked_list[:3])
        parts.append(f"`🚨 {len(blocked_list)} 阻塞 ({ids})`")
    if planned_list:
        nxt = planned_list[0]
        short_title = re.sub(r"\s*[（(].*?[)）]", "", nxt["title"]).strip() or nxt["title"][:32]
        parts.append(f"`下一步：{nxt['task_id']} {short_title}`")
    return "**📊 当前状态**：" + " · ".join(parts)


def _description_block(
    goal_name: str,
    ledger_text: str,
    tasks: list[dict[str, str]],
    handoff_dir: Path | None = None,
) -> str:
    header_lines: list[str] = []
    machine_keys = {
        "协调者 bot", "协调者", "tl", "机器", "machine", "合同 sha", "合同版本",
        "contract", "contract sha", "linear", "linear issue", "状态", "status", "project",
    }
    found_goal = False
    for line in ledger_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## Tasks") or stripped.startswith("## Events") or stripped.startswith("|"):
            break
        match = re.match(r"^\s*[-*]\s+(.*?)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key = match.group(1).strip().strip("*").strip()
        value = match.group(2).strip()
        if not value:
            continue
        if " | " in line and re.search(r"(?:EVT=|evt=|id=|ts=)", line):
            continue
        if re.match(r"^[-*]\s+(?:`?ts=|20\d\d-\d\d-\d\d)", line, re.IGNORECASE):
            continue
        if key.casefold() in machine_keys:
            continue
        found_goal = found_goal or key.casefold() in {"目标", "goal"}
        header_lines.append(f"- {key}: {value}")
    if not found_goal and tasks:
        titles = [str(task.get("title", "")) for task in tasks[:3]]
        header_lines.insert(0, f"- 目标: {'; '.join(titles) if len(tasks) > 1 else titles[0]}")
    summary = "\n".join(header_lines) or f"- Goal: {goal_name}"
    repo = _infer_github_repo(goal_name, ledger_text, handoff_dir)
    checklist: list[str] = []
    attribution = _collect_task_attribution(handoff_dir, ledger_text, tasks)
    status_badges = {
        "in_review": "👀 *In Review*",
        "in_progress": "🔨 *In Progress*",
        "changes_requested": "🔧 *Changes Requested*",
        "blocked": "🚨 *Blocked*",
        "planned": "⏳ *Planned*",
        "ready": "⏳ *Ready*",
    }
    for task in tasks:
        raw_status = task["status"]
        norm = _normalize_task_status(raw_status)
        checked = "x" if norm == "done" else " "
        pr_str = _format_pr_ref(task.get("pr", ""), repo)
        meta = attribution.get(task["task_id"])
        attr_str = ""
        if _has_meaningful_attribution(meta):
            attr_str = f"runs={meta['runs']}, duration={meta['duration_sec']}s, tokens={meta['tokens']}, cost={meta['cost']}"
        details = [x for x in (pr_str, attr_str) if x]
        detail_suffix = f" ({'; '.join(details)})" if details else ""
        if norm == "done":
            checklist.append(f"- [{checked}] **{task['task_id']}**: {task['title']}{detail_suffix}")
        else:
            badge = status_badges.get(norm, f"*{raw_status}*")
            if norm == "blocked" and ":" in raw_status:
                reason = raw_status.split(":", 1)[1].strip()
                badge = f"🚨 *Blocked: {reason}*"
            checklist.append(f"- [{checked}] **{task['task_id']}**: {task['title']} — {badge}{detail_suffix}")
    attribution_block = ""
    goal = attribution.get("__goal__")
    if _has_meaningful_attribution(goal):
        attribution_block = (
            "\n\n## Execution & Cost Attribution\n"
            f"- runs: {goal['runs']}; duration: {goal['duration_sec']}s; "
            f"tokens: {goal['tokens']}; cost: {goal['cost']}"
        )
    status_bar = _build_status_bar(tasks)
    status_section = f"{status_bar}\n\n" if status_bar else ""
    return (
        MARKER_START
        + "\n"
        + status_section
        + "## Tasks\n"
        + ("\n".join(checklist) if checklist else "- [ ] No tasks recorded")
        + "\n\n## Goal Summary\n"
        + summary
        + attribution_block
        + "\n"
        + MARKER_END
    )


def _format_tokens(value: int, lower_bound: bool = False) -> str:
    if not value:
        return "—"
    suffix = "K" if value >= 1000 else ""
    number = value / 1000 if suffix else value
    result = f"{number:.1f}{suffix}" if suffix else str(value)
    return f"≥{result}" if lower_bound else result


def _format_cost(value: float, lower_bound: bool = False) -> str:
    if value <= 0:
        return "—"
    result = f"${value:.2f}"
    return f"≥{result}" if lower_bound else result


def _model_prices() -> dict[str, dict[str, float]]:
    prices = {key: dict(value) for key, value in DEFAULT_MODEL_PRICES.items()}
    csv_path = os.environ.get("GTT_MODEL_PRICES_CSV", "").strip()
    if not csv_path:
        return prices
    try:
        import csv
        with Path(csv_path).expanduser().open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                model = str(row.get("model") or row.get("model_name") or "").strip()
                if not model:
                    continue
                prices[model] = {
                    key: float(row.get(key, 0) or 0)
                    for key in ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok")
                }
    except (OSError, ValueError, TypeError):
        pass
    return prices


def _parse_numeric(value: Any) -> float:
    if value in (None, "", "—", "-"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").lstrip("≥~$").rstrip("sS")
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*([kKmM])?$", text)
    if not match:
        return 0.0
    num = float(match.group(1))
    suffix = (match.group(2) or "").casefold()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num


def _run_cost(run: dict[str, Any], prices: dict[str, dict[str, float]]) -> float:
    if run.get("cost_usd") not in (None, ""):
        return _parse_numeric(run.get("cost_usd"))
    model = str(run.get("model") or "default")
    price = prices.get(model, prices["default"])
    return sum(
        _parse_numeric(run.get(token_key, 0)) / 1_000_000 * price[price_key]
        for token_key, price_key in (
            ("input_tokens", "input_per_mtok"),
            ("output_tokens", "output_per_mtok"),
            ("cache_read_tokens", "cache_read_per_mtok"),
            ("cache_write_tokens", "cache_write_per_mtok"),
        )
    )


def _metric_runs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("input_tokens", "output_tokens", "cost_usd", "model", "duration_sec")):
            return [value]
        return [item for item in value.values() if isinstance(item, dict)]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _receipt_field(text: str, field: str) -> str:
    match = re.search(
        rf"(?mi)^\s*-\s*\**{re.escape(field)}\**\s*:\s*`?([^`\r\n]+?)`?\s*$",
        text,
    )
    return match.group(1).strip() if match else ""


def _collect_task_attribution(
    handoff_dir: Path | None, ledger_text: str, tasks: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    task_ids = {task["task_id"] for task in tasks}
    prices = _model_prices()

    def item_for(task_id: str) -> dict[str, Any]:
        return metrics.setdefault(task_id, {
            "runs": 0, "metered": 0, "duration": 0.0, "tokens_raw": 0,
            "cost_raw": 0.0, "v5_verdict": "", "receipts": [],
        })

    def add_run(task_id: str, run: dict[str, Any], receipt: str | None = None) -> None:
        if task_id not in task_ids:
            return
        item = item_for(task_id)
        item["runs"] += 1
        token_count = sum(int(_parse_numeric(run.get(key, 0))) for key in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "tokens",
        ))
        if token_count:
            item["metered"] += 1
            item["tokens_raw"] += token_count
        item["duration"] += _parse_numeric(run.get("duration_sec", 0))
        item["cost_raw"] += _run_cost(run, prices)
        item["v5_verdict"] = str(run.get("v5_verdict") or item["v5_verdict"])
        if receipt and receipt not in item["receipts"]:
            item["receipts"].append(receipt)

    for event in _parse_events(ledger_text):
        task_id = event.get("task")
        if task_id in task_ids and ("tokens" in event or "duration_sec" in event):
            add_run(task_id, {"tokens": event.get("tokens", 0), "duration_sec": event.get("duration_sec", 0)})
    if handoff_dir and handoff_dir.is_dir():
        queue_path = handoff_dir / "queue.json"
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            for task in queue.get("tasks", []):
                task_id = str(task.get("task_id", ""))
                for run in _metric_runs(task.get("run_metrics")):
                    add_run(task_id, run)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        receipts = handoff_dir / "receipts"
        if receipts.is_dir():
            for path in receipts.glob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                match_task = re.search(r"(?mi)^\s*-\s*\**(?:Task|任务)\**\s*:\s*`?([A-Za-z0-9_.-]+)", text)
                if not match_task or match_task.group(1) not in task_ids:
                    continue
                task_id = match_task.group(1)
                run = {
                    "tokens": _receipt_field(text, "Tokens").replace(",", ""),
                    "duration_sec": _receipt_field(text, "Duration").split()[0] if _receipt_field(text, "Duration") else 0,
                    "cost_usd": _receipt_field(text, "Cost").replace("$", ""),
                    "model": _receipt_field(text, "Model"),
                    "v5_verdict": _receipt_field(text, "V5 Verdict"),
                }
                add_run(task_id, run, str(path.relative_to(handoff_dir)))
    if not metrics:
        return {}
    total_runs = sum(int(item["runs"]) for item in metrics.values())
    total_metered = sum(int(item["metered"]) for item in metrics.values())
    total_tokens = sum(int(item["tokens_raw"]) for item in metrics.values())
    total_cost = sum(float(item["cost_raw"]) for item in metrics.values())
    output: dict[str, dict[str, Any]] = {}
    for task_id, item in metrics.items():
        lower = item["runs"] > item["metered"] > 0
        output[task_id] = {
            "runs": item["runs"], "duration_sec": f"{item['duration']:.1f}",
            "tokens": _format_tokens(item["tokens_raw"], lower),
            "cost": _format_cost(item["cost_raw"], lower),
            "receipts": item["receipts"],
            "v5_verdict": item["v5_verdict"],
        }
    output["__goal__"] = {
        "runs": total_runs, "duration_sec": f"{sum(float(i['duration']) for i in metrics.values()):.1f}",
        "tokens": _format_tokens(total_tokens, total_runs > total_metered > 0),
        "cost": _format_cost(total_cost, total_runs > total_metered > 0),
    }
    return output


def _strip_duplicate_task_sections(prefix: str) -> str:
    lines = prefix.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        if not re.match(r"^##\s+Tasks(?:\s+Checklist.*)?\s*$", lines[index].rstrip("\r\n"), re.IGNORECASE):
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not re.match(r"^##\s+", lines[end]):
            end += 1
        body = [line.strip() for line in lines[index + 1:end] if line.strip()]
        if body and all(re.match(r"^-\s+\[[ xX]\]", line) for line in body):
            del lines[index:end]
            continue
        index = end
    return "".join(lines)


def _updated_description(existing: str | None, block: str) -> str:
    current = existing or ""
    start = current.find(MARKER_START)
    if start < 0:
        prefix = _strip_duplicate_task_sections(current).rstrip()
        return (prefix + "\n\n" + block) if prefix else block
    end = current.find(MARKER_END, start + len(MARKER_START))
    prefix = _strip_duplicate_task_sections(current[:start]).rstrip()
    if end < 0:
        return (prefix + "\n\n" + block) if prefix else block
    end += len(MARKER_END)
    suffix = current[end:]
    return ((prefix + "\n\n") if prefix else "") + block + suffix
