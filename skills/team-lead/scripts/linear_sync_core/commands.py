"""Command handlers for idea, ensure-goal, sync-ledger, issue CRUD, and comments."""
from __future__ import annotations

from ._imports import *

def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    state = issue.get("state") or {}
    project = issue.get("project") or {}
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "state": state.get("name") or state.get("type"),
        "project": project.get("name") if project else None,
        "priority": issue.get("priority"),
        "url": issue.get("url"),
        "updatedAt": issue.get("updatedAt"),
    }


def _issue_detail(issue: dict[str, Any]) -> dict[str, Any]:
    result = _issue_summary(issue)
    result.update({
        "description": issue.get("description"),
        "labels": [node.get("name") for node in (issue.get("labels") or {}).get("nodes", [])],
        "comments": (issue.get("comments") or {}).get("nodes", []),
        "history": (issue.get("history") or {}).get("nodes", []),
    })
    return result


def cmd_list_issues(args: argparse.Namespace) -> dict[str, Any]:
    client = _client_or_fail()
    issues = client.list_issues(
        args.team, state=args.state, project=args.project, query=args.query, limit=args.limit,
    )
    return {"ok": True, "command": "list-issues", "team": args.team, "issues": [_issue_summary(issue) for issue in issues]}


def cmd_get_issue(args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, "command": "get-issue", "issue": _issue_detail(_client_or_fail().issue(args.id))}


def _update_mutation(issue: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "UpdateIssue", "graphql_operation_name": "UpdateIssue",
        "variables": {"id": str(issue.get("id") or issue.get("identifier")), "input": input_data},
    }


def _normalize_optional_id(value: str | None) -> tuple[str | None, bool]:
    if value is None: return None, False
    if value.strip().casefold() in {"", "null", "none"}: return None, True
    return value, False


def cmd_update_issue(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(args.mode, subcommand="update-issue")
    assignee_value, clear_assignee = _normalize_optional_id(args.assignee)
    delegate_value, clear_delegate = _normalize_optional_id(args.delegate)
    if mode == "off":
        return {"ok": True, "command": "update-issue", "mode": "off", "decision": "off (skipped)",
                "skipped": skipped or "mode_off", "planned_mutations": []}
    requested = {
        key: value for key, value in {
            "title": args.title, "description": args.description, "priority": args.priority,
            "assigneeId": assignee_value, "delegateId": delegate_value,
        }.items() if value is not None
    }
    if mode == "observe":
        if args.state:
            requested["stateId"] = args.state
        if args.project:
            requested["projectId"] = args.project
        if args.add_label:
            requested["labelIds"] = list(args.add_label)
        issue = {"id": args.id, "identifier": args.id}
    elif args.state or args.project or args.add_label:
        client = _client_or_fail()
        issue = client.issue(args.id)
        team = client.team(str((issue.get("team") or {}).get("key") or "TIA"))
        if args.state:
            state = _state(team, args.state)
            if not state:
                raise LinearSyncError(f"Linear state '{args.state}' was not found")
            requested["stateId"] = str(state["id"])
            if args.assignee is None and str(state.get("name") or state.get("type") or "").casefold() in {"done", "completed", "canceled", "cancelled"}:
                clear_assignee = True
        if args.project:
            project_id = _project_id(team, args.project)
            if not project_id:
                raise LinearSyncError(f"Linear project '{args.project}' was not found")
            requested["projectId"] = project_id
        if args.add_label:
            labels = [str(node["id"]) for node in (issue.get("labels") or {}).get("nodes", []) if node.get("id")]
            for name in args.add_label:
                label = _label(team, name)
                if not label:
                    raise LinearSyncError(f"Linear label '{name}' was not found")
                if str(label["id"]) not in labels:
                    labels.append(str(label["id"]))
            requested["labelIds"] = labels
    else:
        issue = {"id": args.id, "identifier": args.id}
    if not requested and not (clear_assignee or clear_delegate):
        raise LinearSyncError("update-issue requires at least one update option")
    if clear_assignee: requested["assigneeId"] = None
    if clear_delegate: requested["delegateId"] = None
    mutation = _update_mutation(issue, requested)
    if mode == "observe":
        return {"ok": True, "command": "update-issue", "mode": mode,
                "decision": "observe (would accept)", "would_accept": True,
                "planned_mutations": [mutation]}
    client = locals().get("client") or _client_or_fail()
    updated = client.update_issue(
        str(issue.get("id") or issue["identifier"]),
        title=requested.get("title"), description=requested.get("description"),
        state_id=requested.get("stateId"), project_id=requested.get("projectId"),
        priority=requested.get("priority"), label_ids=requested.get("labelIds"),
        assignee_id=requested.get("assigneeId"), delegate_id=requested.get("delegateId"),
        clear_assignee=clear_assignee, clear_delegate=clear_delegate,
    )
    return {"ok": True, "command": "update-issue", "mode": mode, "decision": "accept",
            "issue": _issue_detail(updated)}


def cmd_comment(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(args.mode, subcommand="comment")
    mutation = _comment_mutation(args.id, args.body, args.create_as_user)
    if mode == "off":
        return {"ok": True, "command": "comment", "mode": "off", "decision": "off (skipped)",
                "skipped": skipped or "mode_off", "planned_mutations": []}
    if mode == "observe":
        return {"ok": True, "command": "comment", "mode": mode, "decision": "observe (would accept)",
                "would_accept": True, "planned_mutations": [mutation]}
    _client_or_fail().create_comment(args.id, args.body, args.create_as_user)
    return {"ok": True, "command": "comment", "mode": mode, "decision": "accept",
            "identifier": args.id}


def cmd_idea(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="idea", source=getattr(args, "source", ""))
    if mode == "off":
        return {"schema_version": 1, "ok": True, "command": "idea", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    client = None if snapshot else (_client_or_fail() if mode == "auto" or load_api_key() else None)
    if snapshot:
        team = snapshot[0]
    elif client:
        try:
            team = client.team(args.team)
        except (LinearSyncError, AssertionError):
            if mode != "observe":
                raise
            team = _synthetic_team()
            client = None
    else:
        team = _synthetic_team()
    project_name = _infer_project_name(
        getattr(args, "project", ""),
        f"{args.title} {args.description}",
        getattr(args, "repo", ""),
    )
    project_id = _project_id(team, project_name)
    target_state = _state(team, args.state, "Backlog")
    label = _label(team, args.label) if args.label else None
    description = args.description or ""
    if getattr(args, "dedup_key", ""):
        marker = f"<!-- GTT-LINEAR-DEDUP:{args.dedup_key} -->"
        description += f"\n\n{marker}"
        existing = None
        if snapshot:
            snapshot_issue = snapshot[1]
            candidates = snapshot_issue.get("issues", []) if isinstance(snapshot_issue, dict) and isinstance(snapshot_issue.get("issues"), list) else (
                snapshot_issue if isinstance(snapshot_issue, list) else [snapshot_issue]
            )
            existing = next(
                (issue for issue in candidates if isinstance(issue, dict)
                 and marker in str(issue.get("description") or "") and not _is_closed_issue(issue)),
                None,
            )
        elif client:
            try:
                existing = client.find_issue_by_dedup_marker(str(team["id"]), marker)
            except (LinearSyncError, AssertionError):
                existing = None
        if existing:
            return {
                "ok": True, "mode": mode, "decision": "no-op (duplicate)",
                "would_accept": False, "duplicate_of": existing.get("identifier"),
                "identifier": existing.get("identifier"), "url": existing.get("url"),
                "state": (existing.get("state") or {}).get("name"),
                "project": (existing.get("project") or {}).get("name"),
                "planned_mutations": [],
            }
    mutation = {"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
        "teamId": str(team["id"]), "title": args.title, "description": description,
        **({"stateId": str(target_state["id"])} if target_state else {}),
        **({"labelIds": [str(label["id"])]} if label else {}),
        **({"projectId": project_id} if project_id else {}),
    }}}
    if mode == "observe":
        return {"ok": True, "mode": mode, "decision": "observe (would accept)",
                "would_accept": True, "diff": {"has_changes": True, "issue_create": True, "fields": ["title", "description"]},
                "planned_mutations": [mutation]}
    if client is None:
        raise LinearSyncError("LINEAR_API_KEY is not configured")
    issue = client.create_issue(team_id=str(team["id"]), title=args.title, description=description,
                                state_id=str(target_state["id"]) if target_state else None,
                                label_ids=[str(label["id"])] if label else None, project_id=project_id)
    return {
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "id": issue.get("id"),
        "project": (issue.get("project") or {}).get("name") or (project_name if project_id else None),
    }


def cmd_ensure_goal(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = _resolve_handoff_dir_arg(args)
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="ensure-goal", handoff_dir=handoff_dir)
    if mode == "off":
        return {"schema_version": 1, "ok": True, "command": "ensure-goal", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    create_as_user = getattr(args, "create_as_user", False)
    if mode == "observe":
        if not handoff_dir.is_dir():
            raise LinearSyncError(f"Missing handoff directory: {handoff_dir}")
        team, issue = snapshot or (_synthetic_team(), {})
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = args.issue or _bound_issue(ledger_text)
        issue = issue or _virtual_issue(identifier)
        if identifier and not issue.get("identifier"):
            issue["identifier"] = identifier
        goal_name, _, _, _ = _load_handoff(handoff_dir)
        planned: list[dict[str, Any]] = []
        local_effects: list[dict[str, Any]] = []
        if args.issue and _bound_issue(ledger_text) != args.issue:
            local_effects.append({"operation": "BindIssueLine", "identifier": args.issue, "path": str(handoff_dir / "ledger.md")})
        if not identifier:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            planned.append({"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
                "teamId": str(team["id"]), "title": f"[Goal] {goal_name}", "description": "",
                **({"stateId": str(todo["id"])} if todo else {}),
                **({"projectId": _project_id(team, project_name)} if _project_id(team, project_name) else {}),
                **({"createAsUser": actor_name} if actor_name and _oauth_app_enabled() else {}),
            }}})
            local_effects.append({"operation": "BindIssueLine", "identifier": "<created issue>", "path": str(handoff_dir / "ledger.md")})
            identifier = ""
        result = _sync_issue(
            client, issue, team, handoff_dir, project_name=getattr(args, "project", ""),
            mode="observe", create_as_user=actor_name,
        )
        planned = planned + result.get("planned_mutations", [])
        result["planned_mutations"] = planned
        result["diff"]["local_effects"] = local_effects
        if getattr(args, "record_observe", False):
            receipts = handoff_dir / "receipts"
            receipts.mkdir(parents=True, exist_ok=True)
            with (receipts / "linear-observe.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"command": "ensure-goal", "result": result["diff"]}) + "\n")
        return {
            "ok": True, "mode": mode,
            "decision": "observe (would accept)" if planned or local_effects else "observe (no-op)",
            "would_accept": bool(planned or local_effects), "identifier": identifier or None,
            "url": issue.get("url"), "state": (issue.get("state") or {}).get("name"),
            "project": (issue.get("project") or {}).get("name"),
            "diff": result["diff"], "planned_mutations": planned,
        }
    client = _client_or_fail()
    handoff_dir.mkdir(parents=True, exist_ok=True)
    with _queue_lock(handoff_dir):
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = args.issue or _bound_issue(ledger_text)
        team = client.team(args.team)
        if identifier:
            issue = client.issue(identifier)
            if args.issue and _bound_issue(ledger_text) != identifier:
                _bind_issue_line(handoff_dir, identifier, str(issue.get("url") or ""))
        else:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            goal_name, _, _, _ = _load_handoff(handoff_dir)
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            issue = client.create_issue(
                team_id=str(team["id"]),
                title=f"[Goal] {goal_name}",
                description="",
                state_id=str(todo["id"]) if todo else None,
                project_id=_project_id(team, project_name),
                create_as_user=actor_name,
            )
            _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
            identifier = str(issue["identifier"])
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            project_name=getattr(args, "project", ""),
            create_as_user=actor_name,
        )
    return {
        "ok": True,
        "mode": mode,
        "decision": "accept" if issue.get("_mutations_executed") else "no-op",
        "would_accept": bool(issue.get("_mutations_executed")),
        "identifier": identifier,
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }


def cmd_sync_ledger(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = _resolve_handoff_dir_arg(args)
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="sync-ledger", handoff_dir=handoff_dir, quiet=getattr(args, "quiet", False))
    if mode == "off":
        if skipped == "isolated_handoff_dir" and not getattr(args, "mode", None) and not os.environ.get("GTT_LINEAR_MODE"):
            return {"ok": True, "skipped": skipped}
        return {"schema_version": 1, "ok": True, "command": "sync-ledger", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    create_as_user = getattr(args, "create_as_user", False)
    if mode == "observe" and not snapshot:
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        team, issue = _synthetic_team(), {}
    elif snapshot:
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        team, issue = snapshot
    else:
        client = _client_or_fail()
    lock = contextlib.nullcontext() if mode == "observe" else _queue_lock(handoff_dir)
    with lock:
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = _bound_issue(ledger_text)
        if mode != "observe" and not snapshot:
            team = client.team("TIA")
        if not identifier:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            goal_name, _, _, _ = _load_handoff(handoff_dir)
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            create_mutation = {"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
                "teamId": str(team["id"]), "title": f"[Goal] {goal_name}", "description": "",
                **({"stateId": str(todo["id"])} if todo else {}),
                **({"projectId": _project_id(team, project_name)} if _project_id(team, project_name) else {}),
                **({"createAsUser": actor_name} if actor_name and _oauth_app_enabled() else {}),
            }}}
            if mode == "observe":
                issue = _virtual_issue()
                planned_create = create_mutation
            else:
                issue = client.create_issue(
                    team_id=str(team["id"]),
                    title=f"[Goal] {goal_name}",
                    description="",
                    state_id=str(todo["id"]) if todo else None,
                    project_id=_project_id(team, project_name),
                    create_as_user=actor_name,
                )
                _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
                identifier = str(issue["identifier"])
        elif not snapshot and mode != "observe":
            issue = client.issue(identifier)
        if (snapshot or mode == "observe") and not issue:
            issue = _virtual_issue(identifier or "TIA-OFFLINE")
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            event_note=args.event_note,
            project_name=getattr(args, "project", ""),
            mode=mode,
            create_as_user=actor_name,
        )
        if mode == "observe" and not identifier:
            issue.setdefault("planned_mutations", []).insert(0, planned_create)
            issue.setdefault("diff", {}).setdefault("local_effects", []).append(
                {"operation": "BindIssueLine", "identifier": "<created issue>", "path": str(handoff_dir / "ledger.md")}
            )
    if mode == "observe" and (handoff_dir.joinpath("receipts").is_dir() or getattr(args, "record_observe", False)):
        handoff_dir.joinpath("receipts").mkdir(parents=True, exist_ok=True)
        path = handoff_dir / "receipts" / "linear-observe.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"command": "sync-ledger", "result": issue.get("diff", {})}, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "mode": mode,
        "decision": "observe (would accept)" if mode == "observe" and (issue.get("planned_mutations") or issue.get("diff", {}).get("local_effects")) else ("observe (no-op)" if mode == "observe" else ("accept" if issue.get("_mutations_executed") else "no-op")),
        "would_accept": bool(issue.get("planned_mutations") or issue.get("diff", {}).get("local_effects")) if mode == "observe" else bool(issue.get("_mutations_executed")),
        **({"diff": issue.get("diff"), "planned_mutations": issue.get("planned_mutations", [])} if mode == "observe" else {}),
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }
