"""Issue synchronization, file-locked ledger binding, and keep_2_days_and_cap_150 archiving."""
from __future__ import annotations

from ._imports import *

def _aggregate_state(team: dict[str, Any], tasks: list[dict[str, str]], events: list[dict[str, str]], header: dict[str, str]) -> dict[str, Any] | None:
    event_names = {event.get("EVT", "").upper() for event in events}
    header_status = header.get("status", "").casefold()
    statuses = [_normalize_task_status(task.get("status", "")) for task in tasks]
    if "GOAL_DONE" in event_names or (statuses and all(s in {"done", "cancelled"} for s in statuses) and any(s == "done" for s in statuses)):
        return _state(team, "Done")
    if header_status == "cancelled" or "GOAL_CANCELLED" in event_names or "CANCELLED" in event_names:
        return _state(team, "Canceled")
    if any(status in {"in_progress", "changes_requested", "blocked"} for status in statuses):
        return _state(team, "In Progress")
    if any(status == "in_review" for status in statuses) and all(status in {"in_review", "done", "cancelled"} for status in statuses):
        return _state(team, "In Review", "In Progress")
    if any(status in {"done", "in_review"} for status in statuses):
        return _state(team, "In Progress")
    if statuses and all(status in {"planned", "ready"} for status in statuses):
        return _state(team, "Todo")
    return _state(team, "Todo", "In Progress")


def _issue_comment_bodies(issue: dict[str, Any]) -> list[str]:
    return [str(node.get("body", "")) for node in issue.get("comments", {}).get("nodes", [])]


def _comment_mutation(issue_id: str, body: str, create_as_user: str | None) -> dict[str, Any]:
    input_data: dict[str, Any] = {"issueId": issue_id, "body": body}
    if create_as_user and _oauth_app_enabled():
        input_data["createAsUser"] = create_as_user
    return {
        "operation": "CreateComment",
        "graphql_operation_name": "CreateComment",
        "variables": {"input": input_data},
    }


def _read_ledger(handoff_dir: Path) -> str:
    ledger_path = handoff_dir / "ledger.md"
    try:
        return ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LinearSyncError(f"Unable to read ledger: {ledger_path}: {exc}") from exc


def _ledger_tl(text: str) -> str | None:
    match = re.search(r"(?mi)^\s*-\s*TL:\s*(\S.*?)\s*$", text)
    return match.group(1).strip() if match and match.group(1).strip() else None


def _resolve_goal_route(
    client: GraphQLClient,
    team: dict[str, Any],
    goal_name: str,
    ledger_text: str,
    handoff_dir: Path,
) -> dict[str, str] | None:
    topic = _goal_topic(goal_name, ledger_text, handoff_dir)
    if not topic:
        return None
    project_name = DOMAIN_PROJECT_NAMES.get(topic.get("group", ""), "")
    project_id = _project_id(team, project_name)
    if not project_id:
        return None
    milestone_name = _root_name(topic)
    milestones = client.project_milestones(project_id)
    milestone = next((item for item in milestones if str(item.get("name", "")).casefold() == milestone_name.casefold()), None)
    if not milestone or not milestone.get("id"):
        return None
    return {
        "topic_id": topic.get("topic_id", ""),
        "project_id": str(project_id),
        "milestone_id": str(milestone["id"]),
    }


def _sync_issue(
    client: GraphQLClient,
    issue: dict[str, Any],
    team: dict[str, Any],
    handoff_dir: Path,
    *,
    event_note: str | None = None,
    project_name: str | None = None,
    mode: str = "auto",
    create_as_user: str | None = None,
) -> dict[str, Any]:
    ledger_text = _read_ledger(handoff_dir)
    goal_name, tasks, events, header = _load_handoff(handoff_dir)
    attribution = _collect_task_attribution(handoff_dir, ledger_text, tasks)
    inferred_project = _infer_project_name(project_name, ledger_text, str(handoff_dir))
    project_id = _project_id(team, inferred_project)
    goal_route = None
    if mode != "observe" and str(issue.get("title") or "").startswith("[Goal]"):
        goal_route = _resolve_goal_route(client, team, goal_name, ledger_text, handoff_dir)
        if goal_route:
            project_id = goal_route["project_id"]
            inferred_project = next(
                (value for value in DOMAIN_PROJECT_NAMES.values() if _project_id(team, value) == project_id),
                inferred_project,
            )
    current_project_id = str((issue.get("project") or {}).get("id") or "")
    current_milestone_id = str((issue.get("projectMilestone") or {}).get("id") or "")
    current_parent_id = str((issue.get("parent") or {}).get("id") or "")
    explicit_project = bool(project_name and project_name.strip())
    description = _updated_description(
        issue.get("description"),
        _description_block(goal_name, ledger_text, tasks, handoff_dir),
    )
    target_state = _aggregate_state(team, tasks, events, header)
    labels = [str(node["id"]) for node in issue.get("labels", {}).get("nodes", []) if node.get("id")]
    blocking = _active_blocking_events(events, tasks)
    blocked_labels = [
        lbl for lbl in (
            _label(team, "Needs Decision"),
            _label(team, "Blocked"),
            _label(team, "NEED_GO"),
        ) if lbl and lbl.get("id")
    ]
    if blocking and blocked_labels:
        primary_blocked_id = str(blocked_labels[0]["id"])
        if primary_blocked_id not in labels:
            labels.append(primary_blocked_id)
    elif not blocking and blocked_labels:
        blocked_ids = {str(lbl["id"]) for lbl in blocked_labels}
        labels = [label_id for label_id in labels if label_id not in blocked_ids]
    target_project_id = project_id if project_id and (explicit_project or not current_project_id or goal_route) else None
    target_milestone_id = goal_route.get("milestone_id") if goal_route and goal_route.get("milestone_id") else None
    target_parent_id = None
    clear_goal_parent = goal_route is not None
    current_assignee_id = str((issue.get("assignee") or {}).get("id") or "")
    target_state_name = str((target_state or {}).get("name") or "").casefold()
    is_container = bool(re.match(r"^\s*\[(?:epic|root|m2-sync)\]", str(issue.get("title") or ""), re.I)) or "<!-- GTT-M2-SYNC-HUB:v1 -->" in str(issue.get("description") or "")
    needs_owner_ball = not is_container and (
        target_state_name == "in review"
        or any(str(ev.get("EVT") or "").upper() in {"NEED_OWNER_DECISION", "NEED_GO"} for ev in blocking)
    )
    target_assignee_id = OWNER_USER_ID if needs_owner_ball else None
    clear_goal_assignee = not needs_owner_ball and bool(current_assignee_id)
    assignee_changed = (needs_owner_ball and current_assignee_id != OWNER_USER_ID) or clear_goal_assignee
    current_state_id = str((issue.get("state") or {}).get("id") or "")
    current_labels = [str(node["id"]) for node in issue.get("labels", {}).get("nodes", []) if node.get("id")]
    state_changed = bool(target_state and str(target_state.get("id") or "") != current_state_id)
    labels_changed = set(labels) != set(current_labels)
    project_changed = bool(target_project_id and target_project_id != current_project_id)
    milestone_changed = bool(target_milestone_id and target_milestone_id != current_milestone_id)
    parent_changed = clear_goal_parent and bool(current_parent_id)
    description_changed = description != str(issue.get("description") or "")
    has_field_changes = description_changed or state_changed or labels_changed or project_changed or milestone_changed or parent_changed or assignee_changed
    planned_mutations: list[dict[str, Any]] = []
    if has_field_changes:
        planned_mutations.append({"operation": "UpdateIssue", "graphql_operation_name": "UpdateIssue", "variables": {
            "id": str(issue["id"]), "input": {
                "description": description,
                "stateId": str(target_state["id"]) if target_state else None,
                "labelIds": labels,
                **({"projectId": target_project_id} if target_project_id else {}),
                **({"projectMilestoneId": target_milestone_id} if target_milestone_id else {}),
                **({"parentId": target_parent_id} if clear_goal_parent else {}),
                **({"assigneeId": target_assignee_id} if assignee_changed else {}),
            },
        }})
    if mode == "auto" and has_field_changes:
        issue = client.update_issue(
            str(issue["id"]), description=description,
            state_id=str(target_state["id"]) if target_state else None,
            label_ids=labels, project_id=target_project_id,
            project_milestone_id=target_milestone_id, parent_id=target_parent_id,
            clear_parent=clear_goal_parent,
            assignee_id=target_assignee_id if needs_owner_ball else None,
            clear_assignee=clear_goal_assignee,
        )
    existing_comments = _issue_comment_bodies(issue)
    for event in blocking:
        event_id = event.get("id", "")
        marker = f"<!-- GTT-LINEAR-EVENT:{event_id} -->"
        if event_id and not any(marker in body for body in existing_comments):
            body = f"{marker}\n**{event.get('EVT')}** `{event_id}`"
            if event.get("task"):
                body += f" · Task `{event['task']}`"
            if event.get("note"):
                body += f"\n{event['note']}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), body, create_as_user)
            existing_comments.append(body)
    task_ids = {t["task_id"] for t in tasks}
    for event in events:
        event_type = event.get("EVT", "").upper()
        task_id = event.get("task", "")
        if event_type not in {"DONE", "GOAL_DONE"}:
            continue
        if event_type != "GOAL_DONE" and task_id not in task_ids:
            continue
        meta = attribution.get(task_id) if task_id else None
        if event_type != "GOAL_DONE" and not _has_meaningful_attribution(meta) and not PR_RE.search(
            f"{event.get('ref', '')} {event.get('note', '')}"
        ):
            continue
        event_id = event.get("id", "")
        marker = f"<!-- GTT-LINEAR-MILESTONE:{event_id} -->"
        if event_id and not any(marker in body for body in existing_comments):
            body = f"{marker}\n**{event_type}**"
            if task_id:
                body += f" · Task `{task_id}`"
                if _has_meaningful_attribution(meta):
                    body += f"\nRun attribution: {meta['runs']} run(s), {meta['tokens']}, {meta['cost']}"
                    if meta.get("receipts"):
                        body += f" · receipt `{meta['receipts'][0]}`"
            if event.get("ref"):
                body += f"\n{event['ref']}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), body, create_as_user)
            existing_comments.append(body)
    if event_note:
        marker = f"<!-- GTT-LINEAR-NOTE:{event_note} -->"
        if not any(marker in body for body in existing_comments):
            note_body = f"{marker}\n{event_note}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), note_body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), note_body, create_as_user)
            existing_comments.append(marker)
    if mode == "observe":
        return {
            **issue,
            "diff": {"has_changes": has_field_changes, "issue_create": False,
                     "fields": [key for key, changed in (("description", description_changed), ("state", state_changed), ("labels", labels_changed), ("project", project_changed), ("milestone", milestone_changed), ("parent", parent_changed), ("assignee", assignee_changed)) if changed],
                     "comments_to_create": [m["variables"]["input"]["body"] for m in planned_mutations if m["operation"] == "CreateComment"],
                     "local_effects": []},
            "planned_mutations": planned_mutations,
        }
    issue["_mutations_executed"] = len(planned_mutations) if mode == "auto" else 0
    return issue


def _bind_issue_line(handoff_dir: Path, identifier: str, url: str) -> None:
    path = handoff_dir / "ledger.md"
    text = _read_ledger(handoff_dir)
    binding = f"- Linear: {identifier}" + (f" ({url})" if url else "")
    if ISSUE_RE.search(text):
        text = ISSUE_RE.sub(binding, text, count=1)
    else:
        lines = text.splitlines()
        insert_at = next((index for index, line in enumerate(lines) if line.startswith("## Tasks")), len(lines))
        lines[insert_at:insert_at] = [binding, ""]
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write_text(path, text, 0o600)


@contextlib.contextmanager
def _queue_lock(handoff_dir: Path) -> Iterator[None]:
    lock_path = handoff_dir / ".queue.lock"
    if lock_path.is_symlink():
        raise LinearSyncError(f"Refusing symlinked lock: {lock_path}")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _bound_issue(text: str) -> str | None:
    match = ISSUE_RE.search(text)
    return match.group(1) if match else None


def _client_or_fail() -> GraphQLClient:
    key = load_api_key()
    if not key:
        raise LinearSyncError("LINEAR_API_KEY is not configured")
    return GraphQLClient(key)


def _offline_snapshot(value: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not value:
        return None
    try:
        path = Path(value)
        raw = path.read_text(encoding="utf-8") if path.is_file() else value
        data = json.loads(raw)
        return data.get("team", {}), data.get("issue", {})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LinearSyncError(f"invalid offline snapshot: {exc}") from exc


def _synthetic_team() -> dict[str, Any]:
    return {"id": "offline-team", "key": "TIA", "name": "TIA", "states": {"nodes": [
        {"id": "todo", "name": "Todo"}, {"id": "progress", "name": "In Progress"},
        {"id": "backlog", "name": "Backlog"}, {"id": "done", "name": "Done"},
    ]}, "labels": {"nodes": []}, "projects": {"nodes": []}}


def _virtual_issue(identifier: str = "") -> dict[str, Any]:
    return {
        "id": "offline-issue",
        "identifier": identifier,
        "url": "",
        "description": "",
        "state": {},
        "labels": {"nodes": []},
        "comments": {"nodes": []},
        "project": None,
    }


def _is_closed_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state") or {}
    return str(state.get("type") or state.get("name") or "").casefold() in {
        "completed", "canceled", "cancelled", "done",
    }


def _is_usage_limit_error(message: str) -> bool:
    lowered = str(message or "").casefold()
    return (
        "usage limit exceeded" in lowered
        or "exceeded the free issue limit" in lowered
        or "free issue limit" in lowered
    )


def _parse_iso_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _issue_number_key(issue: dict[str, Any]) -> int:
    ident = str(issue.get("identifier") or "")
    match = re.search(r"-(\d+)$", ident)
    return int(match.group(1)) if match else 0


def _goal_slug_from_title(title: str) -> str | None:
    match = re.match(r"^\s*\[goal\]\s+([^\s:—–\-]+(?:\-[^\s:—–\-]+)*)", str(title or ""), re.IGNORECASE)
    if not match:
        return None
    slug = match.group(1).strip().casefold()
    return slug if slug else None


def _is_whitelisted_from_archive(
    issue: dict[str, Any],
    *,
    now: datetime,
    retention_days: int,
) -> bool:
    state = issue.get("state") or {}
    state_type = str(state.get("type") or "").strip().casefold()
    state_name = str(state.get("name") or "").strip().casefold()
    is_canceled = state_type in {"canceled", "cancelled"} or state_name in {"canceled", "cancelled", "duplicate"}
    is_completed = state_type == "completed" or state_name == "done"
    if not (is_canceled or is_completed):
        return True
    if state_type in {"triage", "backlog", "unstarted", "started"} and not (is_canceled or is_completed):
        return True
    if state_name in {"triage", "backlog", "todo", "in progress", "in review"}:
        return True
    identifier = str(issue.get("identifier") or "").strip().upper()
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    if (
        identifier == "TIA-463"
        or "[m2-sync]" in title.casefold()
        or "<!-- gtt-m2-sync-hub:v1 -->" in description.casefold()
    ):
        return True
    if re.search(r"\[(?:epic|root)\]", title, re.IGNORECASE):
        return True
    is_daily = bool(
        re.search(r"\[daily\s+(?:brief|learn)", title, re.IGNORECASE)
        or "gtt-linear-dedup:daily-brief-" in description.casefold()
    )
    if is_daily:
        cutoff = now - timedelta(days=retention_days)
        ts = (
            _parse_iso_timestamp(issue.get("completedAt"))
            or _parse_iso_timestamp(issue.get("updatedAt"))
            or _parse_iso_timestamp(issue.get("createdAt"))
        )
        if ts is not None and ts >= cutoff:
            return True
        date_match = (
            re.search(r"\[daily\s+(?:brief|learn)\s+(\d{4}-\d{2}-\d{2})\]", title, re.IGNORECASE)
            or re.search(r"gtt-linear-dedup:daily-brief-(\d{4}-\d{2}-\d{2})", description, re.IGNORECASE)
        )
        if date_match:
            try:
                issue_date = datetime.fromisoformat(f"{date_match.group(1)}T23:59:59+08:00").astimezone(timezone.utc)
                if issue_date >= cutoff:
                    return True
            except ValueError:
                pass
    return False


def _duplicate_goal_issue_ids(issues: list[dict[str, Any]]) -> set[str]:
    by_slug: dict[str, list[dict[str, Any]]] = {}
    all_titles_lower = [
        (
            str(item.get("id") or item.get("identifier") or ""),
            str(item.get("title") or "").casefold(),
            item,
        )
        for item in issues
        if isinstance(item, dict) and not item.get("archivedAt")
    ]
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("archivedAt"):
            continue
        slug = _goal_slug_from_title(str(issue.get("title") or ""))
        if slug:
            by_slug.setdefault(slug, []).append(issue)
    duplicates: set[str] = set()
    for slug, group in by_slug.items():
        if len(group) > 1:
            def primary_rank(item: dict[str, Any]) -> tuple[int, float, int]:
                st = item.get("state") or {}
                st_type = str(st.get("type") or "").casefold()
                st_name = str(st.get("name") or "").casefold()
                is_open = int(
                    st_type not in {"completed", "canceled", "cancelled"}
                    and st_name not in {"done", "canceled", "cancelled", "duplicate"}
                )
                ts = (
                    _parse_iso_timestamp(item.get("updatedAt"))
                    or _parse_iso_timestamp(item.get("completedAt"))
                    or _parse_iso_timestamp(item.get("createdAt"))
                )
                return (is_open, ts.timestamp() if ts else 0.0, _issue_number_key(item))

            primary = max(group, key=primary_rank)
            primary_key = str(primary.get("id") or primary.get("identifier") or "")
            for item in group:
                item_key = str(item.get("id") or item.get("identifier") or "")
                if item_key != primary_key:
                    duplicates.add(item_key)
        else:
            only = group[0]
            only_key = str(only.get("id") or only.get("identifier") or "")
            if len(slug) >= 6:
                for other_key, other_title, _ in all_titles_lower:
                    if other_key != only_key and slug in other_title:
                        duplicates.add(only_key)
                        break
    return duplicates


def archive_completed_issues(
    client: GraphQLClient | None,
    *,
    team_key: str = "TIA",
    retention_days: int = 2,
    max_active_issues: int = 150,
    mode: str = "auto",
    issues_override: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if mode == "off":
        return {
            "ok": True,
            "command": "archive-completed",
            "mode": "off",
            "decision": "off (skipped)",
            "strategy": "keep_2_days_and_cap_150",
            "team": team_key,
            "retention_days": retention_days,
            "max_active_issues": max_active_issues,
            "total_before": 0,
            "archived_count": 0,
            "active_after": 0,
            "protected_count": 0,
            "archived": [],
            "planned_mutations": [],
        }
    if issues_override is not None:
        raw_issues = list(issues_override)
    else:
        if client is None:
            raise LinearSyncError("LINEAR_API_KEY is not configured")
        raw_issues = client.list_issues(team_key, limit=None)
    active_issues = [
        issue for issue in raw_issues
        if isinstance(issue, dict) and not issue.get("archivedAt")
    ]
    total_before = len(active_issues)
    duplicate_goal_ids = _duplicate_goal_issue_ids(active_issues)
    cutoff = current_now - timedelta(days=retention_days)
    to_archive: list[tuple[dict[str, Any], str]] = []
    remaining_done: list[dict[str, Any]] = []
    protected_count = 0

    for issue in active_issues:
        if _is_whitelisted_from_archive(issue, now=current_now, retention_days=retention_days):
            protected_count += 1
            continue
        state = issue.get("state") or {}
        state_type = str(state.get("type") or "").strip().casefold()
        state_name = str(state.get("name") or "").strip().casefold()
        title = str(issue.get("title") or "")
        issue_key = str(issue.get("id") or issue.get("identifier") or "")
        is_canceled = state_type in {"canceled", "cancelled"} or state_name in {"canceled", "cancelled", "duplicate"}
        is_pr_aux = bool(re.search(r"\[pr\s+(?:action|decision)\]", title, re.IGNORECASE))
        is_dup_goal = issue_key in duplicate_goal_ids

        if is_canceled or is_pr_aux or is_dup_goal:
            to_archive.append((issue, "immediate"))
            continue

        completed_ts = (
            _parse_iso_timestamp(issue.get("completedAt"))
            or _parse_iso_timestamp(issue.get("updatedAt"))
            or _parse_iso_timestamp(issue.get("createdAt"))
        )
        if completed_ts is not None and completed_ts < cutoff:
            to_archive.append((issue, "retention_expired"))
        else:
            remaining_done.append(issue)

    projected_active = total_before - len(to_archive)
    if projected_active > max_active_issues and remaining_done:
        def oldest_sort_key(item: dict[str, Any]) -> tuple[float, int, str]:
            ts = (
                _parse_iso_timestamp(item.get("completedAt"))
                or _parse_iso_timestamp(item.get("updatedAt"))
                or _parse_iso_timestamp(item.get("createdAt"))
            )
            return (
                ts.timestamp() if ts is not None else float("inf"),
                _issue_number_key(item),
                str(item.get("identifier") or item.get("id") or ""),
            )

        remaining_done.sort(key=oldest_sort_key)
        excess = projected_active - max_active_issues
        for issue in remaining_done[:excess]:
            to_archive.append((issue, "cap_trim"))

    archived_records: list[dict[str, Any]] = []
    planned_mutations: list[dict[str, Any]] = []
    for issue, reason in to_archive:
        issue_id = str(issue.get("id") or issue.get("identifier") or "")
        state = issue.get("state") or {}
        mutation = {
            "operation": "ArchiveIssue",
            "graphql_operation_name": "ArchiveIssue",
            "variables": {"id": issue_id},
        }
        planned_mutations.append(mutation)
        if mode == "auto":
            if client is not None:
                client.archive_issue(issue_id)
        archived_records.append({
            "identifier": issue.get("identifier"),
            "id": issue_id,
            "title": issue.get("title"),
            "reason": reason,
            "state": state.get("name") or state.get("type"),
            "completedAt": issue.get("completedAt"),
            "updatedAt": issue.get("updatedAt"),
        })

    active_after = total_before - len(archived_records)
    return {
        "ok": True,
        "command": "archive-completed",
        "mode": mode,
        "decision": "observe (would accept)" if mode == "observe" and planned_mutations else ("observe (no-op)" if mode == "observe" else ("accept" if archived_records else "no-op")),
        "would_accept": bool(planned_mutations),
        "strategy": "keep_2_days_and_cap_150",
        "team": team_key,
        "retention_days": retention_days,
        "max_active_issues": max_active_issues,
        "total_before": total_before,
        "archived_count": len(archived_records),
        "active_after": active_after,
        "protected_count": protected_count,
        "archived": archived_records,
        "planned_mutations": planned_mutations,
    }


def cmd_archive_completed(args: argparse.Namespace) -> dict[str, Any]:
    mode, _ = resolve_mode(getattr(args, "mode", None), subcommand="archive-completed")
    snapshot_arg = getattr(args, "offline_snapshot", "")
    issues_override: list[dict[str, Any]] | None = None
    if snapshot_arg:
        try:
            path = Path(snapshot_arg)
            raw = path.read_text(encoding="utf-8") if path.is_file() else snapshot_arg
            data = json.loads(raw)
            if isinstance(data, list):
                issues_override = data
            elif isinstance(data, dict):
                if isinstance(data.get("issues"), list):
                    issues_override = data["issues"]
                elif isinstance(data.get("issue"), list):
                    issues_override = data["issue"]
                elif isinstance(data.get("issue"), dict) and isinstance(data["issue"].get("issues"), list):
                    issues_override = data["issue"]["issues"]
                else:
                    issues_override = []
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise LinearSyncError(f"invalid offline snapshot: {exc}") from exc
    client = None if (mode == "off" or issues_override is not None) else _client_or_fail()
    return archive_completed_issues(
        client,
        team_key=getattr(args, "team", "TIA"),
        retention_days=int(getattr(args, "retention_days", 2)),
        max_active_issues=int(getattr(args, "max_active_issues", 150)),
        mode=mode,
        issues_override=issues_override,
    )
