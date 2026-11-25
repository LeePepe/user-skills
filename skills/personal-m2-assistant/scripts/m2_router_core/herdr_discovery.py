from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _herdr_agent_name(value: dict[str, Any]) -> str:
    agent = value.get("agent") or value.get("agent_name") or value.get("agentName") or value.get("provider")
    if isinstance(agent, dict):
        agent = agent.get("name") or agent.get("type") or agent.get("id")
    return _text(agent).lower()

def _herdr_background(value: dict[str, Any]) -> bool:
    haystack = " ".join(_text(value.get(key)) for key in (
        "name", "title", "role", "goal", "cwd", "foreground_cwd", "worktree", "repo_root"
    ))
    return bool(HERDR_BACKGROUND_RE.search(haystack))

def _herdr_interactive_candidate(value: dict[str, Any]) -> bool:
    if _herdr_background(value):
        return False
    if value.get("active") is False or value.get("is_active") is False or value.get("isActive") is False:
        return False
    explicit = value.get("is_m2", value.get("isM2"))
    if explicit is True or _looks_like_m2(value, "herdr"):
        return True
    if explicit is False:
        return False
    agent = _herdr_agent_name(value)
    cwd = _text(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree") or value.get("repo_root"))
    if not agent or not cwd:
        return False
    return agent in HERDR_INTERACTIVE_AGENTS or bool(re.search(r"copilot|claude|codex|pi|cindy", agent, re.I))

def _looks_like_m2(value: dict[str, Any], source: str) -> bool:
    explicit = value.get("is_m2", value.get("isM2"))
    if explicit is not None:
        return bool(explicit)
    if source == "dsh":
        return value.get("isDshSession", value.get("is_dsh_session", True)) is not False
    haystack = " ".join(_text(value.get(key)) for key in (
        "name", "title", "role", "agent", "goal", "target_repo", "repo"
    )).lower()
    return bool(re.search(r"(?:^|[-_ /])m2(?:[-_ /]|$)|dispatcher|omni-hub|omnihub", haystack))

def normalize_session(value: dict[str, Any], source: str, *, allow_interactive_herdr: bool = False) -> dict[str, Any] | None:
    """Normalize Herdr/dsh records without retaining provider-specific secrets."""
    if not isinstance(value, dict):
        return None
    if source == "herdr" and allow_interactive_herdr:
        if not _herdr_interactive_candidate(value):
            return None
    elif not _looks_like_m2(value, source):
        return None
    status = _text(value.get("agent_status") or value.get("status"), "idle").lower()
    pane_id = _text(value.get("pane_id") or value.get("paneId"))
    if status in (HERDR_TERMINAL_STATUSES if source == "herdr" and allow_interactive_herdr else TERMINAL_STATUSES):
        return None
    if source == "herdr" and allow_interactive_herdr and status == "done" and not pane_id:
        return None
    sid = _session_id(value)
    if not sid:
        return None
    repo = _repo_from(value)
    agent_sid = _agent_session_id(value)
    raw_agent_session = value.get("agent_session") if isinstance(value.get("agent_session"), dict) else ({"value": agent_sid} if agent_sid else None)
    norm: dict[str, Any] = {
        "session_id": sid,
        "topic_id": _text(value.get("topic_id") or value.get("topicId")),
        "target_repo": repo,
        "title": _text(value.get("title") or value.get("terminal_title_stripped") or value.get("name"), sid),
        "status": status,
        "cwd": _expand_cwd(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree")),
        "workspace_id": _text(value.get("workspace_id") or value.get("workspaceId")),
        "workspace_label": _text(value.get("workspace_label") or value.get("workspaceLabel")),
        "pane_id": pane_id,
        "herdr_target": _text(value.get("herdr_target") or value.get("herdrTarget") or value.get("name"), sid),
        "source": source,
        "summary": _text(value.get("summary") or value.get("contextSummary") or value.get("lastAssistantText"))[:800],
        "base_url": _text(value.get("base_url") or value.get("dsh_base_url")),
    }
    if raw_agent_session:
        norm["agent_session"] = dict(raw_agent_session)
    if agent_sid:
        norm["agent_session_id"] = agent_sid
    if _text(value.get("last_session_activity_at")):
        norm["last_session_activity_at"] = _text(value.get("last_session_activity_at"))
    if isinstance(value.get("recent_session_context"), dict):
        norm["recent_session_context"] = dict(value["recent_session_context"])
    if value.get("_live_herdr"):
        norm["_live_herdr"] = True
    if value.get("_from_issue_cache"):
        norm["_from_issue_cache"] = True
    return norm

def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("agents", "panes", "sessions", "dshSessions", "items"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    result = payload.get("result")
    return _records(result) if result is not None else []

def _workspace_inventory(payload: Any) -> list[dict[str, Any]] | None:
    """Distinguish a valid, possibly empty inventory from non-workspace output."""
    if isinstance(payload, dict):
        if payload.get("ok") is False or payload.get("error"):
            return None
        if "workspaces" in payload:
            payload = payload["workspaces"]
        elif isinstance(payload.get("result"), dict):
            return _workspace_inventory(payload["result"])
    if not isinstance(payload, list):
        return None
    normalized = []
    session_fields = {
        "pane_id", "paneId", "session_id", "sessionId", "agent", "agent_name", "agentName",
        "herdr_target", "herdrTarget", "isDshSession", "is_dsh_session", "foreground_cwd", "target_repo",
    }
    for item in payload:
        if not isinstance(item, dict) or session_fields.intersection(item):
            return None
        if "cwd" in item and "label" not in item:
            return None
        workspace_id = _text(item.get("workspace_id") or item.get("workspaceId") or item.get("id"))
        label = _text(item.get("label") or item.get("name") or item.get("title"))
        if not workspace_id or not any(field in item for field in ("label", "name", "title")):
            return None
        normalized.append({
            "workspace_id": workspace_id,
            "label": label,
            "active_tab_id": _text(item.get("active_tab_id") or item.get("activeTabId")),
            "tab_count": item.get("tab_count", item.get("tabCount", 0)),
            "pane_count": item.get("pane_count", item.get("paneCount", 0)),
            "agent_status": _text(item.get("agent_status") or item.get("agentStatus") or item.get("status")),
        })
    return normalized

def _workspace_records(payload: Any) -> list[dict[str, Any]]:
    return _workspace_inventory(payload) or []

def _workspace_map(workspaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_text(item.get("workspace_id")): item for item in workspaces if _text(item.get("workspace_id"))}

def resolve_target_space(target: dict[str, Any], topic: dict[str, Any] | None = None, workspaces: list[dict[str, Any]] | None = None, domain_map: dict[str, Any] | None = None) -> dict[str, Any]:
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    source = topic if topic is not None else proposed
    space = _text((source or {}).get("space") or target.get("space") or target.get("target_repo"), "M2")
    aliases = list((source or {}).get("space_aliases") or target.get("space_aliases") or [])
    aliases = [_text(value) for value in aliases if _text(value)]
    target_id = ""
    inventory = workspaces or []
    labels = [space, *aliases]
    for wanted in labels:
        wanted_key = wanted.casefold()
        if wanted_key == "大总管" and space.casefold() != "大总管":
            continue
        match = next(
            (
                item for item in inventory
                if _text(item.get("label")).casefold() == wanted_key
                and _text(item.get("workspace_id"))
            ),
            None,
        )
        if match:
            target_id = _text(match.get("workspace_id"))
            break
    target["space"] = space
    target["space_aliases"] = aliases
    target["target_workspace_id"] = target_id
    return target

def _merge_herdr_records(pane_records: list[dict[str, Any]], agent_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join pane and agent inventories so either public command can supply identity."""
    merged: dict[str, dict[str, Any]] = {}
    for record in pane_records + agent_records:
        if not isinstance(record, dict):
            continue
        key = _text(record.get("pane_id") or record.get("paneId") or record.get("id") or record.get("name"))
        if not key:
            key = f"record-{len(merged)}"
        current = merged.setdefault(key, {})
        current.update({field: value for field, value in record.items() if value not in (None, "")})
    return list(merged.values())

def discover_herdr(
    *,
    payload: Any = None,
    pane_payload: Any = None,
    agent_payload: Any = None,
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
    herdr_bin: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
) -> tuple[list[dict[str, Any]], str | None]:
    """Discover interactive repo-root M2 panes from both public Herdr inventories."""
    pane_records: list[dict[str, Any]] = []
    agent_records: list[dict[str, Any]] = []
    if payload is not None:
        if isinstance(payload, dict) and ("panes" in payload or "agents" in payload):
            pane_records = _records(payload.get("panes"))
            agent_records = _records(payload.get("agents"))
            if not pane_records and not agent_records:
                pane_records = _records(payload)
        else:
            agent_records = _records(payload)
    if pane_payload is not None:
        pane_records = _records(pane_payload)
    if agent_payload is not None:
        agent_records = _records(agent_payload)
    if workspace_payload is None:
        workspace_payload = herdr_workspace_payload
    workspace_inventory = _workspace_inventory(workspace_payload)
    if isinstance(payload, dict) and workspace_inventory is None:
        workspace_inventory = _workspace_inventory(payload)
    workspace_records = workspace_inventory or []
    used_live_herdr = False
    if pane_payload is None and agent_payload is None and payload is None:
        used_live_herdr = True
        binary = herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr")
        if not binary:
            return DiscoverySessions(workspaces=workspace_records), None
        try:
            pane_result = runner([binary, "pane", "list"], timeout=3.0)
            agent_result = runner([binary, "agent", "list"], timeout=3.0)
            try:
                workspace_result = runner([binary, "workspace", "list"], timeout=3.0)
                live_inventory = _workspace_inventory(_parse_json_output(workspace_result.stdout)) if workspace_result.returncode == 0 else None
                if workspace_inventory is None and live_inventory is not None:
                    workspace_records = live_inventory
            except (OSError, subprocess.SubprocessError):
                pass
        except (OSError, subprocess.SubprocessError) as error:
            return [], f"herdr discovery failed: {error}"
        failures = [result for result in (pane_result, agent_result) if result.returncode != 0]
        if len(failures) == 2:
            return [], f"herdr discovery exited {failures[0].returncode}/{failures[1].returncode}"
        pane_records = _records(_parse_json_output(pane_result.stdout)) if pane_result.returncode == 0 else []
        agent_records = _records(_parse_json_output(agent_result.stdout)) if agent_result.returncode == 0 else []
    workspace_by_id = _workspace_map(workspace_records)
    records = _merge_herdr_records(pane_records, agent_records)
    for item in records:
        if used_live_herdr:
            item["_live_herdr"] = True
        ws = _text(item.get("workspace_id") or item.get("workspaceId"))
        if ws and ws in workspace_by_id:
            item["workspace_label"] = workspace_by_id[ws].get("label", "")
    sessions = [normalize_session(item, "herdr", allow_interactive_herdr=True) for item in records]
    return DiscoverySessions(
        (item for item in sessions if item),
        workspaces=workspace_records,
        discovery_source="herdr_live" if used_live_herdr else "herdr_fixture",
    ), None

def discover_from_issue_cache(
    *,
    state_file: str | Path | None = None,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    cross_device_state: dict[str, Any] | None = None,
    domain_map: dict[str, Any] | None = None,
    domain_map_file: str | Path | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
) -> tuple[DiscoverySessions, str | None]:
    """Read stored spaces (workspaces) and M2 agents (sessions) from Linear Issue TIA-463 and local cache snapshots without invoking herdr."""
    host = _resolve_local_hostname(local_hostname)
    state_path = _resolve_supervision_state_path(state_file, persist_by_default=True)
    cache_path = _resolve_cross_device_cache_path(state_file)
    try:
        loaded_domain_map = domain_map or load_domain_map(domain_map_file)
    except RouterError:
        loaded_domain_map = {}
    topics_by_id = _topic_by_id(loaded_domain_map) if isinstance(loaded_domain_map, dict) else {}

    hub_state: dict[str, Any] = {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}
    warning: str | None = None
    explicit_issue_fixture = hub_issue_payload is not None or cross_device_state is not None
    if isinstance(hub_issue_payload, dict):
        if isinstance(hub_issue_payload.get("devices"), dict):
            hub_state = copy.deepcopy(hub_issue_payload)
        else:
            remote_issue = dict(
                hub_issue_payload.get("issue")
                if isinstance(hub_issue_payload.get("issue"), dict)
                else hub_issue_payload
            )
            parsed_hub = parse_cross_device_hub_description(_text(remote_issue.get("description")))
            hub_state = copy.deepcopy(parsed_hub.get("state") or hub_state)
            if state_file is not None:
                _save_cross_device_cache(cache_path, hub_state)
    elif isinstance(cross_device_state, dict):
        hub_state = copy.deepcopy(cross_device_state)
    else:
        hub_state = _load_cross_device_cache(cache_path)

    local_sup_state: dict[str, Any] = {}
    if (not explicit_issue_fixture or state_file is not None) and state_path and state_path.is_file():
        try:
            loaded_sup = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded_sup, dict):
                local_sup_state = loaded_sup
        except (OSError, ValueError):
            local_sup_state = {}

    devices_map = hub_state.get("devices") if isinstance(hub_state.get("devices"), dict) else {}
    host_shard: dict[str, Any] = {}
    for dev_key, dev_val in devices_map.items():
        if isinstance(dev_val, dict) and str(dev_key).casefold() == host.casefold():
            host_shard = dict(dev_val)
            break

    has_cached_local = bool(
        host_shard.get("spaces")
        or host_shard.get("workspaces")
        or host_shard.get("agents")
        or host_shard.get("sessions")
        or host_shard.get("in_flight")
        or local_sup_state.get("spaces")
        or local_sup_state.get("workspaces")
        or local_sup_state.get("agents")
        or local_sup_state.get("slots")
    )
    can_fetch_linear = bool(
        not no_sync_linear
        and (
            sync_linear
            or linear_runner is not None
            or (
                not has_cached_local
                and not _is_isolated_test_state_path(state_path)
                and _text(os.environ.get("GTT_LINEAR_SYNC")) != "0"
            )
        )
    )
    if can_fetch_linear and hub_issue_payload is None and cross_device_state is None:
        issue_id = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
        exec_runner = linear_runner or _run_command
        linear_script = _text(os.environ.get("M2_LINEAR_SYNC_SCRIPT"), str(LINEAR_SYNC_SCRIPT_PATH))
        try:
            get_res = exec_runner([sys.executable, linear_script, "get-issue", "--id", issue_id], timeout=10.0)
            parsed_get = _parse_json_output(getattr(get_res, "stdout", "")) if getattr(get_res, "returncode", 1) == 0 else None
            if isinstance(parsed_get, dict) and isinstance(parsed_get.get("issue"), dict):
                parsed_hub = parse_cross_device_hub_description(_text(parsed_get["issue"].get("description")))
                if isinstance(parsed_hub.get("state"), dict):
                    hub_state = parsed_hub["state"]
                    _save_cross_device_cache(cache_path, hub_state)
                    devices_map = hub_state.get("devices") if isinstance(hub_state.get("devices"), dict) else {}
                    for dev_key, dev_val in devices_map.items():
                        if isinstance(dev_val, dict) and str(dev_key).casefold() == host.casefold():
                            host_shard = dict(dev_val)
                            break
        except (OSError, subprocess.SubprocessError) as error:
            warning = f"issue cache linear fetch failed: {error}"

    raw_spaces: list[dict[str, Any]] = []
    for sp_list in (
        host_shard.get("spaces"),
        host_shard.get("workspaces"),
        local_sup_state.get("spaces"),
        local_sup_state.get("workspaces"),
    ):
        if isinstance(sp_list, list):
            for sp in _workspace_records(sp_list):
                raw_spaces.append(sp)
    workspace_by_id: dict[str, dict[str, Any]] = {}
    workspace_records: list[dict[str, Any]] = []
    for sp in raw_spaces:
        ws_id = _text(sp.get("workspace_id"))
        if ws_id and ws_id not in workspace_by_id:
            workspace_by_id[ws_id] = sp
            workspace_records.append(sp)

    merged_candidates: dict[str, dict[str, Any]] = {}
    in_flight_ids: set[str] = set()
    for item in (host_shard.get("in_flight") or []):
        if isinstance(item, dict):
            key = _text(item.get("pane_id") or item.get("session_id") or item.get("slot_id"))
            if key:
                in_flight_ids.add(key)

    candidate_lists: list[tuple[str, list[Any]]] = [
        ("in_flight", list(host_shard.get("in_flight") or [])),
        ("slots", list(local_sup_state.get("slots") or [])),
        ("sup_agents", list(local_sup_state.get("agents") or local_sup_state.get("sessions") or [])),
        ("shard_agents", list(host_shard.get("agents") or host_shard.get("sessions") or [])),
    ]
    for source_kind, items in candidate_lists:
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            if _text(raw_item.get("method")).lower() == "stdout":
                continue
            pane_id = _text(raw_item.get("pane_id") or raw_item.get("paneId"))
            sid = _text(
                raw_item.get("session_id")
                or raw_item.get("target")
                or raw_item.get("herdr_target")
                or raw_item.get("name")
                or raw_item.get("slot_id")
            )
            key = pane_id or sid
            if not key:
                continue
            cur = merged_candidates.setdefault(key, {})
            for field, val in raw_item.items():
                if val not in (None, ""):
                    cur[field] = val
            if source_kind == "in_flight" and "status" not in cur and "agent_status" not in cur:
                cur["status"] = "working"

    sessions: list[dict[str, Any]] = []
    for key, rec in merged_candidates.items():
        pane_id = _text(rec.get("pane_id") or rec.get("paneId"))
        sid = _text(
            rec.get("session_id")
            or rec.get("target")
            or rec.get("herdr_target")
            or rec.get("name")
            or rec.get("slot_id")
        )
        topic_id = _text(rec.get("topic_id") or rec.get("topicId"))
        topic_meta = topics_by_id.get(topic_id, {})
        ws_id = _text(
            rec.get("workspace_id")
            or rec.get("workspaceId")
            or (pane_id.split(":", 1)[0] if ":" in pane_id else "")
        )
        ws_label = _text(
            rec.get("workspace_label")
            or rec.get("workspaceLabel")
            or (workspace_by_id.get(ws_id, {}).get("label") if ws_id else "")
            or (topic_meta.get("space") if ws_id and topic_meta else "")
        )
        status = _text(rec.get("agent_status") or rec.get("status"), "working" if key in in_flight_ids else "idle").lower()
        target_repo = _text(rec.get("target_repo") or rec.get("repo") or topic_meta.get("target_repo"))
        cwd = _expand_cwd(rec.get("cwd") or rec.get("foreground_cwd") or topic_meta.get("cwd"))
        title = _text(rec.get("title") or rec.get("terminal_title") or topic_meta.get("title") or sid)
        summary = _text(rec.get("summary") or rec.get("m1_progress") or rec.get("visible_summary"))
        enriched = {
            **rec,
            "session_id": sid,
            "herdr_target": _text(rec.get("herdr_target") or rec.get("target") or sid),
            "pane_id": pane_id,
            "workspace_id": ws_id,
            "workspace_label": ws_label,
            "topic_id": topic_id,
            "target_repo": target_repo,
            "title": title,
            "status": status,
            "agent_status": status,
            "cwd": cwd,
            "summary": summary,
            "is_m2": True,
        }
        norm = normalize_session(enriched, _text(rec.get("source"), "herdr"), allow_interactive_herdr=True)
        if norm is not None:
            for extra_key in ("linear_issue", "parent_linear_issue", "handoff_dir", "goal_id", "m1_progress"):
                if _text(rec.get(extra_key)):
                    norm[extra_key] = _text(rec.get(extra_key))
            if rec.get("issue") and "linear_issue" not in norm:
                norm["linear_issue"] = _text(rec.get("issue"))
            norm["_from_issue_cache"] = True
            sessions.append(norm)
            if ws_id and ws_label and ws_id not in workspace_by_id:
                synthesized_ws = {
                    "workspace_id": ws_id,
                    "label": ws_label,
                    "active_tab_id": "",
                    "tab_count": 1,
                    "pane_count": 1,
                    "agent_status": status,
                }
                workspace_by_id[ws_id] = synthesized_ws
                workspace_records.append(synthesized_ws)

    return DiscoverySessions(sessions, workspaces=workspace_records, discovery_source="issue_cache"), warning

def _fetch_json(url: str, *, token: str = "", timeout: float = 3.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def discover_dsh(
    *,
    payload: Any = None,
    state_url: str = "",
    token: str = "",
    fetcher: Callable[..., Any] = _fetch_json,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read dsh's public omni-hub state, never its private session storage."""
    if payload is None and state_url:
        try:
            payload = fetcher(state_url, token=token, timeout=3.0)
        except (OSError, ValueError, urllib.error.URLError) as error:
            return [], f"dsh discovery failed: {error}"
    if payload is None:
        return [], None
    sessions = [normalize_session(item, "dsh") for item in _records(payload)]
    return [item for item in sessions if item], None

def discover_sessions(
    *,
    herdr_payload: Any = None,
    herdr_pane_payload: Any = None,
    herdr_agent_payload: Any = None,
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
    dsh_payload: Any = None,
    herdr_bin: str | None = None,
    dsh_state_url: str = "",
    dsh_token: str = "",
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    fetcher: Callable[..., Any] = _fetch_json,
    include_herdr: bool = True,
    include_dsh: bool = True,
    prefer_issue_cache: bool = False,
    state_file: str | Path | None = None,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    cross_device_state: dict[str, Any] | None = None,
    domain_map: dict[str, Any] | None = None,
    domain_map_file: str | Path | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    discovered_workspaces: list[dict[str, Any]] = []
    discovery_source = ""
    has_explicit_herdr_fixture = any(
        val is not None
        for val in (herdr_payload, herdr_pane_payload, herdr_agent_payload, workspace_payload, herdr_workspace_payload)
    )
    if include_herdr:
        if prefer_issue_cache and not has_explicit_herdr_fixture:
            found, warning = discover_from_issue_cache(
                state_file=state_file,
                hub_issue=hub_issue,
                local_hostname=local_hostname,
                linear_runner=linear_runner,
                hub_issue_payload=hub_issue_payload,
                cross_device_state=cross_device_state,
                domain_map=domain_map,
                domain_map_file=domain_map_file,
                sync_linear=sync_linear,
                no_sync_linear=no_sync_linear,
            )
        else:
            found, warning = discover_herdr(payload=herdr_payload, pane_payload=herdr_pane_payload, agent_payload=herdr_agent_payload, workspace_payload=workspace_payload, herdr_workspace_payload=herdr_workspace_payload, herdr_bin=herdr_bin, runner=runner)
        sessions.extend(found)
        discovered_workspaces = list(getattr(found, "workspaces", []))
        discovery_source = _text(getattr(found, "discovery_source", ""))
        if warning:
            warnings.append(warning)
    if include_dsh:
        found, warning = discover_dsh(payload=dsh_payload, state_url=dsh_state_url, token=dsh_token, fetcher=fetcher)
        sessions.extend(found)
        if warning:
            warnings.append(warning)
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for session in sessions:
        deduped[(session["source"], session["session_id"])] = session
    workspace_inventory = _workspace_inventory(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    if workspace_inventory is None:
        workspace_inventory = discovered_workspaces
    return DiscoverySessions(deduped.values(), workspaces=workspace_inventory, discovery_source=discovery_source), warnings
