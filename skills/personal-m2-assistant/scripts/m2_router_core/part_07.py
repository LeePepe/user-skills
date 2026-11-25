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

def apply_plan(plan: dict[str, Any], *, approve: bool = False, register_new_domains: bool = False, cleanup_duplicates: bool = False, replace_stale_m2: bool = False, domain_map_file: str | Path | None = None, plan_file: str | Path | None = None, apply_receipt_file: str | Path | None = None, state_file: str | Path | None = None, reset_state: bool = False, handoff_root: str | Path | None = None, agent_records: list[dict[str, Any]] | None = None, transport: str = "auto", sync_linear: bool = False, no_sync_linear: bool = False, hub_issue: str | None = None, local_hostname: str | None = None, linear_runner: CommandRunner | None = None, hub_issue_payload: dict[str, Any] | None = None, caller_bot: str = "", no_supervision: bool = False, no_routine: bool = False, spawn_background_sync: bool = False, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise RouterError("unsupported plan schema")
    if not approve:
        raise RouterError("owner approval required: rerun with --approve")
    targets = plan.get("targets", [])
    resolved_initial_transports = [_resolve_target_transport(dict(item), plan, transport) for item in targets]
    is_stdout_only = transport == "stdout" or (bool(resolved_initial_transports) and all(item == "stdout" for item in resolved_initial_transports))
    stdout_candidate_sessions: set[str] = set()
    stdout_new_topics: set[str] = set()
    for item, eff in zip(targets, resolved_initial_transports):
        if eff == "stdout":
            if _text(item.get("domain_status")) == NEW_DOMAIN_CANDIDATE and _text(item.get("topic_id")):
                stdout_new_topics.add(_text(item.get("topic_id")))
            for cand in item.get("cleanup_candidates", []):
                if isinstance(cand, dict) and _text(cand.get("session_id")):
                    stdout_candidate_sessions.add(_text(cand.get("session_id")))
    reg_plan = plan
    if register_new_domains and not is_stdout_only and stdout_new_topics:
        reg_plan = dict(plan)
        reg_plan["new_domain_candidates"] = [
            c for c in plan.get("new_domain_candidates", [])
            if isinstance(c, dict) and _text(c.get("topic_id")) not in stdout_new_topics
        ]
    registered = _register_new_domains(reg_plan, domain_map_file, dry_run=is_stdout_only) if register_new_domains else []
    auto_replace = any(t.get("m2_action") == "REPLACE_STALE_M2" for t in targets)
    cleanup_mode = "replace" if replace_stale_m2 else "replace_context" if auto_replace else "duplicates" if cleanup_duplicates else "none"
    cleanup_receipts = _cleanup_stale(plan, cleanup_mode, runner=runner, dry_run=is_stdout_only, dry_run_sessions=stdout_candidate_sessions)
    cleanup_state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True)
    pruned_cleanup_slots = _prune_closed_slots(cleanup_state_path, cleanup_receipts) if not is_stdout_only else []
    closed_sessions = {item.get("session_id") for item in cleanup_receipts if item.get("ok") and not item.get("skipped") and not item.get("simulated")}
    used_herdr_names: set[str] = set()
    created_workspaces: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    dispatched_targets: list[dict[str, Any]] = []
    target_seq = 0
    for wave in sorted({int(item.get("wave_order", 1)) for item in targets}):
        for target in [item for item in targets if int(item.get("wave_order", 1)) == wave]:
            target_seq += 1
            dispatch_target = dict(target)
            initial_transport = _resolve_target_transport(dispatch_target, plan, transport)
            target_is_stdout = is_stdout_only or initial_transport == "stdout"
            if (replace_stale_m2 or target.get("m2_action") == "REPLACE_STALE_M2") and target.get("cleanup_candidates"):
                candidate_ids = {_text(c.get("session_id")) for c in target.get("cleanup_candidates", []) if isinstance(c, dict)}
                if not target_is_stdout and candidate_ids and not candidate_ids.issubset(closed_sessions):
                    failures = [item for item in cleanup_receipts if _text(item.get("session_id")) in candidate_ids and (item.get("skipped") or not item.get("ok"))]
                    raise RouterError(f"stale pane close failed; refusing replacement spawn: {failures}")
                if not candidate_ids or candidate_ids.issubset(closed_sessions) or target_is_stdout:
                    dispatch_target.update({
                        "m2_status": "NEW_M2",
                        "session_id": "",
                        "source": "herdr",
                        "transport": "stdout" if target_is_stdout else "herdr",
                    })
            prompt = _text(dispatch_target.get("sub_prompt"), plan.get("message", ""))
            hint = dispatch_target.get("rag_context_hint")
            if hint:
                prompt = f"[RAG/context hint]\n{hint}\n\n[Dispatched instruction]\n{prompt}"
            selected = "stdout" if target_is_stdout else _resolve_target_transport(dispatch_target, plan, transport)
            if selected == "stdout":
                receipt = {"ok": True, "method": "stdout", "target": dispatch_target.get("target_repo", "unspecified"), "created": False, "would_create": dispatch_target.get("m2_status") == "NEW_M2", "prompt": prompt}
            elif selected == "herdr":
                receipt = _apply_herdr(dispatch_target, plan, prompt, runner=runner, index=target_seq, used_names=used_herdr_names, created_workspaces=created_workspaces, domain_map_file=domain_map_file)
            elif selected == "dsh":
                receipt = _apply_dsh(dispatch_target, plan, prompt, poster=poster)
            else:
                raise RouterError(f"unsupported transport: {selected}")
            pane_id_val = _text(
                (receipt.get("receipt") or {}).get("pane_id")
                if isinstance(receipt.get("receipt"), dict)
                else dispatch_target.get("pane_id")
            )
            session_id_val = _text(receipt.get("target")) if selected == "dsh" else _text(dispatch_target.get("session_id") or receipt.get("target"))
            rec_entry: dict[str, Any] = {
                "wave_order": wave,
                "topic_id": _text(target.get("topic_id")),
                "target_repo": target.get("target_repo"),
                "pane_id": pane_id_val,
                "session_id": session_id_val,
                **receipt,
            }
            if _text(target.get("slot_id")):
                rec_entry["slot_id"] = _text(target.get("slot_id"))
            if _text(target.get("task_label")):
                rec_entry["task_label"] = _text(target.get("task_label"))
            receipts.append(rec_entry)
            dispatched_targets.append(dispatch_target)
    plan_id = _text(plan.get("plan_id"), "m2p-plan")
    safe_plan_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", plan_id).strip("-") or "m2p-plan"
    resolved_plan_file = _text(plan_file or plan.get("plan_file"), f"/tmp/m2-plan-{safe_plan_id}.json")
    resolved_receipt_path = Path(apply_receipt_file).expanduser() if apply_receipt_file else Path(f"/tmp/m2-apply-{safe_plan_id}.json")
    resolved_state_path = _resolve_supervision_state_path(
        state_file,
        plan_file=plan_file,
        apply_receipt_file=apply_receipt_file,
        safe_plan_id=safe_plan_id,
        persist_by_default=True,
    )
    assert resolved_state_path is not None
    binary = _text(os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    explicit_sync_requested = bool(sync_linear or linear_runner is not None or hub_issue_payload is not None)
    live_records = agent_records if agent_records is not None else (
        _live_agent_records(binary, runner=runner)
        if explicit_sync_requested and "herdr" in resolved_initial_transports and not is_stdout_only
        else []
    )

    def _register_dispatched_slots(state: dict[str, Any]) -> dict[str, Any]:
        slots = [dict(s) for s in (state.get("slots") or []) if isinstance(s, dict)]
        events = [dict(e) for e in (state.get("events") or []) if isinstance(e, dict)]
        used_slot_ids = {_text(s.get("slot_id")).casefold() for s in slots if _text(s.get("slot_id"))}
        for idx, (rec, tgt) in enumerate(zip(receipts, dispatched_targets)):
            match_idx = _match_existing_slot_index(slots, rec, tgt)
            if match_idx is not None:
                existing = dict(slots[match_idx])
                history = [dict(h) for h in (existing.get("history") or []) if isinstance(h, dict)]
                history.append({
                    "run_seq": int(existing.get("run_seq") or 1),
                    "run_label": _text(existing.get("run_label") or existing.get("slot_id")),
                    "plan_id": _text(existing.get("plan_id")),
                    "status": _text(existing.get("status")),
                    "visible_summary": _text(existing.get("visible_summary")),
                })
                run_seq = int(existing.get("run_seq") or 1) + 1
                slot_id = _text(existing.get("slot_id"))
                base_label = _text(existing.get("base_label") or slot_id)
                explicit_label = _text(tgt.get("task_label") or rec.get("task_label"))
                run_label = explicit_label if explicit_label else f"{base_label}{run_seq}"
                existing.update({
                    "slot_id": slot_id,
                    "base_label": base_label,
                    "run_seq": run_seq,
                    "run_label": run_label,
                    "plan_id": plan_id,
                    "wave_order": int(rec.get("wave_order") or 1),
                    "topic_id": _text(rec.get("topic_id") or existing.get("topic_id")),
                    "target_repo": _text(rec.get("target_repo") or existing.get("target_repo"), "unspecified"),
                    "target": _text(rec.get("target") or existing.get("target")),
                    "pane_id": _text(rec.get("pane_id") or existing.get("pane_id")),
                    "session_id": _text(rec.get("session_id") or existing.get("session_id")),
                    "status": "running",
                    "raw_status": "running",
                    "needs_owner_attention": False,
                    "blocked_reason": "",
                    "auto_unblocked": [],
                    "visible_summary": "",
                    "history": history,
                })
                slots[match_idx] = existing
                rec["slot_id"] = slot_id
                rec["run_seq"] = run_seq
                rec["run_label"] = run_label
            else:
                explicit_slot = _text(tgt.get("slot_id") or rec.get("slot_id"))
                explicit_label = _text(tgt.get("task_label") or rec.get("task_label"))
                raw_slot = (
                    explicit_slot
                    or explicit_label
                    or _text(rec.get("topic_id"))
                    or _text(rec.get("target"))
                    or _text(rec.get("target_repo"))
                    or f"m2-{idx + 1}"
                )
                slot_id = raw_slot
                suffix = 2
                while slot_id.casefold() in used_slot_ids:
                    slot_id = f"{raw_slot}-{suffix}"
                    suffix += 1
                used_slot_ids.add(slot_id.casefold())
                base_label = explicit_slot or explicit_label or slot_id
                run_label = explicit_label or base_label
                run_seq = 1
                slots.append({
                    "slot_id": slot_id,
                    "base_label": base_label,
                    "run_seq": run_seq,
                    "run_label": run_label,
                    "plan_id": plan_id,
                    "wave_order": int(rec.get("wave_order") or 1),
                    "topic_id": _text(rec.get("topic_id")),
                    "target_repo": _text(rec.get("target_repo"), "unspecified"),
                    "target": _text(rec.get("target")),
                    "pane_id": _text(rec.get("pane_id")),
                    "session_id": _text(rec.get("session_id")),
                    "status": "running",
                    "raw_status": "running",
                    "needs_owner_attention": False,
                    "blocked_reason": "",
                    "auto_unblocked": [],
                    "visible_summary": "",
                    "history": [],
                })
                rec["slot_id"] = slot_id
                rec["run_seq"] = run_seq
                rec["run_label"] = run_label
            slot = slots[_match_existing_slot_index(slots, rec, tgt)]
            for key in (*ROLLUP_METADATA_KEYS, "handoff_in_progress", "goal_tl"):
                slot.pop(key, None)
            slot.update(_slot_metadata(tgt))
        events.append({
            "seq": len(events) + 1,
            "type": "apply",
            "plan_id": plan_id,
            "dispatched_labels": [_text(r.get("run_label")) for r in receipts],
        })
        state = _discover_and_enrich_global_m2_slots(
            {"schema_version": 1, "slots": slots, "events": events}, agent_records=live_records,
            runner=runner, herdr_bin=binary, handoff_root=handoff_root,
        )
        state["slots"] = _compute_board_rollup(state)["slots"]
        return state

    board_state = _with_locked_supervision_state(resolved_state_path, _register_dispatched_slots, reset=reset_state)
    board_rollup = _compute_board_rollup(board_state)
    cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
    host = _resolve_local_hostname(local_hostname)
    existing_cache = _load_cross_device_cache(cache_path)
    existing_devices = existing_cache.setdefault("devices", {})
    now_ts = _cst_now_iso()
    existing_devices[host] = _build_local_device_shard(
        board_rollup,
        previous_shard=existing_devices.get(host),
        now_iso=now_ts,
    )
    existing_cache["schema"] = M2_SYNC_SCHEMA
    existing_cache["updated_at"] = now_ts
    _save_cross_device_cache(cache_path, existing_cache)

    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=explicit_sync_requested,
        state_path=resolved_state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    linear_sync_result: dict[str, Any]
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    else:
        linear_sync_result = {
            "ok": True if not no_sync_linear else False,
            "updated": False,
            "deferred_to_background_task": not no_sync_linear and not is_stdout_only,
            "skipped_reason": sync_reason if no_sync_linear else "deferred_to_post_apply_background_task",
        }
    _attach_cross_device_to_rollup(
        board_rollup,
        linear_sync_result.get("state") if isinstance(linear_sync_result.get("state"), dict) else existing_cache,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )

    handoff_watch_arg = f" --handoff-root {shlex.quote(str(handoff_root))}" if handoff_root else ""
    effective_hub_issue = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
    host_watch_arg = f" --local-hostname {shlex.quote(str(local_hostname))}" if local_hostname else ""
    quota_protection = _resolve_quota_protection(
        plan=plan,
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=live_records if live_records else None,
        herdr_bin=binary,
        runner=runner,
        probe_herdr=False,
    )
    grok_supervision_disabled = bool(quota_protection.get("active"))
    post_apply_sync_cmd = None if (is_stdout_only or no_sync_linear) else (
        f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
        f"--sync-herdr-to-issue --plan-file {shlex.quote(resolved_plan_file)} "
        f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
        f"--state-file {shlex.quote(str(resolved_state_path))} "
        f"--hub-issue {shlex.quote(effective_hub_issue)}{host_watch_arg} --json{handoff_watch_arg}"
    )
    post_apply_sync_task = None if post_apply_sync_cmd is None else {
        "tool": "bash",
        "mode": "async",
        "detach": True,
        "shellId": f"sync-m2-issue-{safe_plan_id}",
        "description": f"Scan live Herdr spaces/agents in background and write back to Issue {effective_hub_issue}",
        "command": post_apply_sync_cmd,
    }
    background_sync_pid: int | None = None
    if (
        post_apply_sync_cmd is not None
        and (spawn_background_sync or _text(os.environ.get("M2_SPAWN_POST_APPLY_SYNC")) == "1")
        and runner is _run_command
    ):
        try:
            bg_args = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--sync-herdr-to-issue",
                "--plan-file", str(resolved_plan_file),
                "--apply-receipt-file", str(resolved_receipt_path),
                "--state-file", str(resolved_state_path),
                "--hub-issue", effective_hub_issue,
                "--json",
            ]
            if local_hostname:
                bg_args.extend(["--local-hostname", str(local_hostname)])
            if handoff_root:
                bg_args.extend(["--handoff-root", str(handoff_root)])
            proc = subprocess.Popen(
                bg_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            background_sync_pid = proc.pid
        except OSError:
            background_sync_pid = None
    watchdog_tasks: list[dict[str, Any]] = []
    for rec in receipts:
        slot_id = _text(rec.get("slot_id"), "m2")
        run_seq = int(rec.get("run_seq") or 1)
        run_label = _text(rec.get("run_label"), slot_id)
        safe_slot = re.sub(r"[^a-zA-Z0-9_.-]+", "-", slot_id).strip("-") or "m2"
        if grok_supervision_disabled:
            rec["watchdog_cmd"] = None
            rec["watchdog_copilot_cli"] = None
            continue
        target_watch_cmd = (
            f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
            f"--watch --plan-file {shlex.quote(resolved_plan_file)} "
            f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
            f"--state-file {shlex.quote(str(resolved_state_path))} "
            f"--watch-target {shlex.quote(slot_id)} "
            f"--interval 15 --max-wait 2400 --json{handoff_watch_arg}"
        )
        target_cli = {
            "tool": "bash",
            "mode": "async",
            "detach": True,
            "shellId": f"watch-m2-{safe_plan_id}-{safe_slot}-r{run_seq}",
            "description": f"Watch M2 {run_label} ({slot_id}) until quiescent and update cumulative rollup",
            "command": target_watch_cmd,
        }
        rec["watchdog_cmd"] = target_watch_cmd
        rec["watchdog_copilot_cli"] = target_cli
        watchdog_tasks.append({
            "slot_id": slot_id,
            "run_seq": run_seq,
            "run_label": run_label,
            "topic_id": _text(rec.get("topic_id")),
            "target_repo": _text(rec.get("target_repo")),
            "target": _text(rec.get("target")),
            "pane_id": _text(rec.get("pane_id")),
            "session_id": _text(rec.get("session_id")),
            "watchdog_cmd": target_watch_cmd,
            "watchdog_copilot_cli": target_cli,
        })

    watch_cmd = None if grok_supervision_disabled else (
        f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
        f"--watch --plan-file {shlex.quote(resolved_plan_file)} "
        f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
        f"--state-file {shlex.quote(str(resolved_state_path))} "
        f"--interval 15 --max-wait 2400 --json{handoff_watch_arg}"
    )
    watch_cli = None if grok_supervision_disabled else {
        "tool": "bash",
        "mode": "async",
        "detach": True,
        "shellId": f"watch-m2-{plan_id}",
        "description": f"Watch dispatched target M2 sessions ({plan_id}) until quiescent",
        "command": watch_cmd,
    }
    payload: dict[str, Any] = {
        "ok": True,
        "plan_id": plan.get("plan_id"),
        "approved": True,
        "apply_receipt_path": str(resolved_receipt_path),
        "supervision_state_path": str(resolved_state_path),
        "registered_new_domains": registered,
        "cleanup_receipts": cleanup_receipts,
        "pruned_cleanup_slots": pruned_cleanup_slots,
        "receipts": receipts,
        "post_apply_sync_cmd": post_apply_sync_cmd,
        "post_apply_sync_task": post_apply_sync_task,
        **({"post_apply_sync_pid": background_sync_pid} if background_sync_pid is not None else {}),
        "watchdog_tasks": watchdog_tasks,
        "unified_rollup_markdown": board_rollup["unified_rollup_markdown"],
        "in_flight_details": board_rollup["in_flight_details"],
        "wave_dependency_chain": board_rollup["wave_dependency_chain"],
        "local_hostname": board_rollup.get("local_hostname"),
        "remote_devices": board_rollup.get("remote_devices", {}),
        "cross_device_in_flight": board_rollup.get("cross_device_in_flight", []),
        "cross_device_pending_decisions": board_rollup.get("cross_device_pending_decisions", []),
        "linear_sync": linear_sync_result,
        "board_rollup": board_rollup,
        "watchdog_cmd": watch_cmd,
        "watchdog_copilot_cli": watch_cli,
    }
    if grok_supervision_disabled:
        payload["quota_protection_mode"] = GROK_QUOTA_PROTECTION_MODE
        payload["quota_protection"] = quota_protection
    try:
        resolved_receipt_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        payload["apply_receipt_write_warning"] = str(error)
    return payload

def _safe_roots_for_target(target_cwd: str = "", *, include_target_cwd: bool = True) -> list[str]:
    baseline_roots = [_expand_cwd(root).rstrip("/") for root in SAFE_BASELINE_ROOTS if _expand_cwd(root)]
    if not include_target_cwd:
        return baseline_roots
    expanded_target = _expand_cwd(target_cwd).rstrip("/")
    if expanded_target and _path_is_under_safe_roots(expanded_target, baseline_roots):
        if expanded_target not in baseline_roots:
            baseline_roots.append(expanded_target)
    return baseline_roots

def _lexical_within(path_str: str, roots: list[str]) -> bool:
    norm = os.path.abspath(path_str).rstrip("/")
    for root in roots:
        norm_root = os.path.abspath(root).rstrip("/")
        if norm == norm_root or norm.startswith(norm_root + "/"):
            return True
    return False

def _resolved_within(path_obj: Path, resolved_roots: list[str]) -> bool:
    try:
        resolved_str = str(path_obj.resolve(strict=False)).rstrip("/")
    except OSError:
        return False
    for r_root in resolved_roots:
        if resolved_str == r_root or resolved_str.startswith(r_root + "/"):
            return True
    return False

_FORBIDDEN_SINGLE_PATH_CHARS = ("*", "?", "[", "]", "$", "`", ";", "|", "&", "<", ">", '"', "'")

def _strip_matching_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"', "`"}:
        return stripped[1:-1].strip()
    return stripped

def _is_clean_single_path_token(token: str, *, allow_relative: bool = False) -> bool:
    if not token or "\n" in token or "\r" in token or "\x00" in token:
        return False
    if any(ch in token for ch in _FORBIDDEN_SINGLE_PATH_CHARS):
        return False
    if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", token):
        return False
    if allow_relative:
        return True
    return token.startswith("/") or token.startswith("~/") or token == "~"

def _split_symlink_arrow_path(raw_text: str) -> tuple[str, str] | None:
    """Parse '<symlink_path> -> <target_path>' into (source_path, resolved_target_path) or None."""
    candidate = _strip_matching_quotes(_text(raw_text).rstrip("?").strip())
    if "->" not in candidate and "→" not in candidate:
        return None
    parts = re.split(r"\s*(?:->|→)\s*", candidate)
    if len(parts) != 2:
        return None
    src = _strip_matching_quotes(parts[0].strip())
    dst = _strip_matching_quotes(parts[1].rstrip("?").strip())
    if not _is_clean_single_path_token(src, allow_relative=False):
        return None
    src_parts = [p for p in re.split(r"[/\\]+", src) if p]
    if ".." in src_parts or "." in src_parts:
        return None
    if not _is_clean_single_path_token(dst, allow_relative=True):
        return None
    if dst.startswith("/") or dst.startswith("~/") or dst == "~":
        dst_parts = [p for p in re.split(r"[/\\]+", dst) if p]
        if ".." in dst_parts or "." in dst_parts:
            return None
        resolved_dst = dst
    else:
        expanded_src = _expand_cwd(src)
        if not expanded_src or not os.path.isabs(expanded_src):
            return None
        resolved_dst = os.path.normpath(os.path.join(os.path.dirname(expanded_src), dst))
    return src, resolved_dst

def _is_forbidden_redline_path(expanded_path: str) -> bool:
    norm_abs = os.path.abspath(expanded_path).rstrip("/") or "/"
    home_obj = Path.home().resolve(strict=False)
    home_abs = str(home_obj).rstrip("/")
    users_root = str(home_obj.parent).rstrip("/")
    if norm_abs in {"/", "/Volumes", "/System", "/Library", "/etc", "/var", "/private", home_abs, users_root}:
        return True
    if str(Path(norm_abs).parent).rstrip("/") == users_root:
        return True
    return False
