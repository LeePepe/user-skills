from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _substantive_hub_signature(description: str) -> tuple[dict[str, Any], str]:
    parsed = parse_cross_device_hub_description(description)
    state = copy.deepcopy(parsed.get("state") or {})
    state.pop("updated_at", None)
    devices = state.get("devices")
    if isinstance(devices, dict):
        for host_shard in devices.values():
            if isinstance(host_shard, dict):
                host_shard.pop("updated_at", None)
                for item in (host_shard.get("in_flight") or []):
                    if isinstance(item, dict):
                        item.pop("updated_at", None)
                for ag in (host_shard.get("agents") or []):
                    if isinstance(ag, dict):
                        ag.pop("updated_at", None)
                        ag.pop("last_session_activity_at", None)
    body = str(description or "").split("<details>", 1)[0]
    body_norm = re.sub(r"\|\s*`\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}`\s*\|", "| `<CST>` |", body).strip()
    return state, body_norm

def _load_cross_device_cache(cache_path: Path | None) -> dict[str, Any]:
    if cache_path is None or not cache_path.is_file():
        return {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("devices"), dict):
            return loaded
    except (OSError, ValueError):
        pass
    return {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}

def _save_cross_device_cache(cache_path: Path | None, state: dict[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + f".{os.getpid()}.tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(cache_path)
    except OSError:
        pass

def _extract_remote_devices_summary(
    cross_device_state: dict[str, Any] | None,
    *,
    local_hostname: str | None = None,
) -> dict[str, Any]:
    host = _resolve_local_hostname(local_hostname)
    devices = (cross_device_state or {}).get("devices") if isinstance(cross_device_state, dict) else {}
    if not isinstance(devices, dict):
        devices = {}
    remote_devices: dict[str, dict[str, Any]] = {}
    remote_in_flight: list[dict[str, Any]] = []
    remote_pending: list[dict[str, Any]] = []
    for other_host, shard in devices.items():
        if not isinstance(shard, dict) or str(other_host).casefold() == host.casefold():
            continue
        in_flight = [dict(x) for x in (shard.get("in_flight") or []) if isinstance(x, dict)]
        pending = list(shard.get("pending_decisions") or [])
        completed = list(shard.get("recent_completed") or [])
        if in_flight or pending or completed:
            remote_devices[str(other_host)] = {
                "updated_at": _text(shard.get("updated_at")),
                "in_flight": in_flight,
                "pending_decisions": pending,
                "recent_completed": completed,
            }
        for item in in_flight:
            remote_in_flight.append({"host": str(other_host), **item})
        if pending:
            remote_pending.append({"host": str(other_host), "decisions": pending})
    return {
        "local_hostname": host,
        "remote_devices": remote_devices,
        "cross_device_in_flight": remote_in_flight,
        "cross_device_pending_decisions": remote_pending,
    }

def _render_cross_device_rollup_lines(
    cross_device_summary: dict[str, Any],
) -> list[str]:
    remote_in_flight = cross_device_summary.get("cross_device_in_flight") or []
    remote_pending = cross_device_summary.get("cross_device_pending_decisions") or []
    if not remote_in_flight and not remote_pending:
        return []
    lines = ["#### 🌐 4. 跨设备在途与待拍板（Cross-Device M2 Sync Hub · `TIA-463`）"]
    for group in remote_pending:
        host = _text(group.get("host"))
        decisions = ", ".join(_text(d.get("issue") if isinstance(d, dict) else d) for d in (group.get("decisions") or []) if _text(d.get("issue") if isinstance(d, dict) else d))
        if decisions:
            lines.append(f"- 🚨 `[Host: {host}]` 待拍板 / 待选型: {decisions}")
    for item in remote_in_flight:
        host = _text(item.get("host"))
        topic = _text(item.get("topic_id") or item.get("slot_id"), "—")
        session_id = _text(item.get("session_id") or item.get("target"), "—")
        pane_id = _text(item.get("pane_id"))
        pane_str = f" (`{pane_id}`)" if pane_id else ""
        issue = _text(item.get("issue"), "Linear 未绑定")
        summary = _text(item.get("summary"), "—")
        lines.append(f"- ⏳ `[Host: {host}]` `{topic}` · `{session_id}`{pane_str} · {issue} · {summary}")
    return lines

def _attach_cross_device_to_rollup(
    board_rollup: dict[str, Any],
    cross_device_state: dict[str, Any] | None = None,
    *,
    local_hostname: str | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    state = cross_device_state if isinstance(cross_device_state, dict) else _load_cross_device_cache(cache_path)
    summary = _extract_remote_devices_summary(state, local_hostname=local_hostname)
    board_rollup["local_hostname"] = summary["local_hostname"]
    board_rollup["remote_devices"] = summary["remote_devices"]
    board_rollup["cross_device_in_flight"] = summary["cross_device_in_flight"]
    board_rollup["cross_device_pending_decisions"] = summary["cross_device_pending_decisions"]
    extra_lines = _render_cross_device_rollup_lines(summary)
    if extra_lines:
        base_md = _text(board_rollup.get("unified_rollup_markdown"))
        marker_header = "#### 🌐 4. 跨设备在途与待拍板"
        if marker_header in base_md:
            base_md = base_md.split(marker_header, 1)[0].rstrip()
        board_rollup["unified_rollup_markdown"] = base_md + "\n" + "\n".join(extra_lines)
    return board_rollup

def sync_cross_device_hub(
    board_rollup: dict[str, Any],
    *,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    cache_path: Path | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    now_iso: str = "",
    spaces: list[dict[str, Any]] | None = None,
    agents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issue_id = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
    host = _resolve_local_hostname(local_hostname)
    exec_runner = linear_runner or _run_command
    linear_script = _text(os.environ.get("M2_LINEAR_SYNC_SCRIPT"), str(LINEAR_SYNC_SCRIPT_PATH))
    remote_issue: dict[str, Any] = {}
    if isinstance(hub_issue_payload, dict):
        remote_issue = dict(hub_issue_payload.get("issue") if isinstance(hub_issue_payload.get("issue"), dict) else hub_issue_payload)
    else:
        try:
            get_res = exec_runner(
                [sys.executable, linear_script, "get-issue", "--id", issue_id],
                timeout=15.0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"ok": False, "updated": False, "hub_issue": issue_id, "local_hostname": host, "error": str(error)}
        parsed_get = _parse_json_output(getattr(get_res, "stdout", "")) if getattr(get_res, "returncode", 1) == 0 else None
        if isinstance(parsed_get, dict) and isinstance(parsed_get.get("issue"), dict):
            remote_issue = dict(parsed_get["issue"])
        else:
            # Fallback: search via list-issues for [M2-Sync]
            try:
                list_res = exec_runner(
                    [sys.executable, linear_script, "list-issues", "--team", "TIA", "--query", "M2-Sync", "--limit", "10"],
                    timeout=15.0,
                )
                parsed_list = _parse_json_output(getattr(list_res, "stdout", "")) if getattr(list_res, "returncode", 1) == 0 else None
                candidates = (parsed_list or {}).get("issues") if isinstance(parsed_list, dict) else []
                found_id = next(
                    (
                        _text(item.get("identifier"))
                        for item in (candidates or [])
                        if isinstance(item, dict) and "[m2-sync]" in _text(item.get("title")).casefold()
                    ),
                    "",
                )
                if found_id:
                    issue_id = found_id
                    retry_res = exec_runner(
                        [sys.executable, linear_script, "get-issue", "--id", issue_id],
                        timeout=15.0,
                    )
                    parsed_retry = _parse_json_output(getattr(retry_res, "stdout", "")) if getattr(retry_res, "returncode", 1) == 0 else None
                    if isinstance(parsed_retry, dict) and isinstance(parsed_retry.get("issue"), dict):
                        remote_issue = dict(parsed_retry["issue"])
            except (OSError, subprocess.SubprocessError):
                pass
        if not remote_issue:
            err_msg = _text(getattr(get_res, "stderr", "") or getattr(get_res, "stdout", ""), f"unable to fetch hub issue {issue_id}")
            return {"ok": False, "updated": False, "hub_issue": issue_id, "local_hostname": host, "error": err_msg}

    existing_description = str(remote_issue.get("description") or "")
    parsed_hub = parse_cross_device_hub_description(existing_description)
    merged_state = copy.deepcopy(parsed_hub["state"])
    devices = merged_state.setdefault("devices", {})
    timestamp = now_iso or _cst_now_iso()
    local_shard = _build_local_device_shard(
        board_rollup,
        previous_shard=devices.get(host),
        now_iso=timestamp,
        spaces=spaces,
        agents=agents,
    )
    devices[host] = local_shard
    merged_state["schema"] = M2_SYNC_SCHEMA
    merged_state["updated_at"] = timestamp

    rendered_description = render_cross_device_hub_markdown(
        merged_state,
        preserved_sections=parsed_hub,
        local_hostname=host,
    )
    _save_cross_device_cache(cache_path, merged_state)

    old_sig = _substantive_hub_signature(existing_description)
    new_sig = _substantive_hub_signature(rendered_description)
    if existing_description.strip() and old_sig == new_sig:
        return {
            "ok": True,
            "updated": False,
            "skipped_reason": "no_substantive_change",
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "description": existing_description,
        }

    if hub_issue_payload is not None and linear_runner is None:
        return {
            "ok": True,
            "updated": True,
            "simulated": True,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "description": rendered_description,
        }

    try:
        upd_res = exec_runner(
            [sys.executable, linear_script, "update-issue", "--id", _text(remote_issue.get("identifier"), issue_id), "--description", rendered_description],
            timeout=20.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "ok": False,
            "updated": False,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "error": str(error),
        }
    if getattr(upd_res, "returncode", 1) != 0:
        return {
            "ok": False,
            "updated": False,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "error": _text(getattr(upd_res, "stderr", "") or getattr(upd_res, "stdout", ""), "update-issue failed"),
        }
    return {
        "ok": True,
        "updated": True,
        "hub_issue": _text(remote_issue.get("identifier"), issue_id),
        "local_hostname": host,
        "state": merged_state,
        "description": rendered_description,
    }

def sync_herdr_inventory_to_issue(
    *,
    state_file: str | Path | None = None,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    handoff_root: str | Path | None = None,
    herdr_payload: Any = None,
    herdr_pane_payload: Any = None,
    herdr_agent_payload: Any = None,
    herdr_workspace_payload: Any = None,
    herdr_bin: str | None = None,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
    no_sync_linear: bool = False,
) -> dict[str, Any]:
    """Scan live Herdr spaces (workspaces) and agents (panes + agents), update local supervision state, and write back to Linear Issue TIA-463."""
    state_path = _resolve_supervision_state_path(
        state_file,
        plan_file=plan_file,
        apply_receipt_file=apply_receipt_file,
        persist_by_default=True,
    )
    cache_path = _resolve_cross_device_cache_path(
        state_file,
        plan_file=plan_file,
        apply_receipt_file=apply_receipt_file,
    )
    binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    discovered, warning = discover_herdr(
        payload=herdr_payload,
        pane_payload=herdr_pane_payload,
        agent_payload=herdr_agent_payload,
        workspace_payload=herdr_workspace_payload,
        herdr_bin=binary,
        runner=runner,
    )
    live_spaces = list(getattr(discovered, "workspaces", []))
    live_agents = [dict(item) for item in discovered if isinstance(item, dict)]

    has_fixture = any(v is not None for v in (herdr_payload, herdr_pane_payload, herdr_agent_payload))
    if has_fixture:
        pane_recs = _records(herdr_pane_payload)
        agent_recs = _records(herdr_agent_payload)
        if not pane_recs and not agent_recs and herdr_payload is not None:
            if isinstance(herdr_payload, dict) and ("panes" in herdr_payload or "agents" in herdr_payload):
                pane_recs = _records(herdr_payload.get("panes"))
                agent_recs = _records(herdr_payload.get("agents"))
            else:
                agent_recs = _records(herdr_payload)
        raw_records = _merge_herdr_records(pane_recs, agent_recs)
    else:
        raw_records = _live_agent_records(binary, runner=runner)

    settled_ids: list[str] = []

    def _update_state(state: dict[str, Any]) -> dict[str, Any]:
        _discover_and_enrich_global_m2_slots(
            state,
            agent_records=raw_records if raw_records else live_agents,
            runner=runner,
            herdr_bin=binary,
            handoff_root=handoff_root,
            allow_quiescent_settle=True,
            settled_slot_ids_out=settled_ids,
        )
        state["slots"] = _compute_board_rollup(state, just_settled_slots=settled_ids)["slots"]
        state["spaces"] = live_spaces
        state["agents"] = live_agents
        return state

    updated_state = _with_locked_supervision_state(state_path, _update_state)
    board_rollup = _compute_board_rollup(updated_state, just_settled_slots=settled_ids)
    board_rollup["spaces"] = live_spaces
    board_rollup["agents"] = live_agents

    host = _resolve_local_hostname(local_hostname)
    existing_cache = _load_cross_device_cache(cache_path)
    existing_devices = existing_cache.setdefault("devices", {})
    now_ts = _cst_now_iso()
    existing_devices[host] = _build_local_device_shard(
        board_rollup,
        previous_shard=existing_devices.get(host),
        now_iso=now_ts,
        spaces=live_spaces,
        agents=live_agents,
    )
    existing_cache["schema"] = M2_SYNC_SCHEMA
    existing_cache["updated_at"] = now_ts
    _save_cross_device_cache(cache_path, existing_cache)

    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=not no_sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=True,
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
            spaces=live_spaces,
            agents=live_agents,
        )
    else:
        linear_sync_result = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    _attach_cross_device_to_rollup(
        board_rollup,
        linear_sync_result.get("state") if isinstance(linear_sync_result.get("state"), dict) else existing_cache,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )
    return {
        "ok": True,
        "supervision_state_path": str(state_path),
        "cross_device_cache_path": str(cache_path),
        "spaces": live_spaces,
        "agents": live_agents,
        "spaces_count": len(live_spaces),
        "agents_count": len(live_agents),
        "discovery_warning": warning,
        "linear_sync": linear_sync_result,
        "board_rollup": board_rollup,
        "summary_markdown": board_rollup["unified_rollup_markdown"],
    }
