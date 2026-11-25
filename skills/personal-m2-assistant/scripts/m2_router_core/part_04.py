def run_gc(
    records: Iterable[dict[str, Any]],
    *,
    approve: bool = False,
    state_file: str | Path | None = None,
    handoff_root: str | Path | None = None,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    candidates = discover_gc_candidates(records, handoff_root=handoff_root)
    receipts = []
    if approve:
        for candidate in candidates:
            try:
                receipts.append({**candidate, **_close_herdr_pane(candidate, runner=runner)})
            except RouterError as error:
                receipts.append({**candidate, "ok": False, "error": str(error)})
    state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True) if approve else None
    pruned = _prune_closed_slots(state_path, receipts)
    result: dict[str, Any] = {"ok": True, "approved": approve, "dry_run": not approve, "gc_candidates": candidates, "closed_receipts": receipts, "pruned_state_slots": pruned}
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=bool(approve),
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync:
        board_state = _with_locked_supervision_state(state_path, lambda s: s)
        board_rollup = _compute_board_rollup(board_state)
        cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
        result["linear_sync"] = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    elif approve or sync_linear or no_sync_linear:
        result["linear_sync"] = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    return result

def _default_preview_path(plan_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", _text(plan_id, "m2p-plan")).strip("-") or "m2p-plan"
    return Path(f"/tmp/m2-plan-{safe_id}.md")

def render_preview_markdown(plan: dict[str, Any]) -> str:
    plan_id = _text(plan.get("plan_id"), "m2p-unknown")
    route = _text((plan.get("decision") or {}).get("route"), "ROUTE_SINGLE")
    default_label = _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL)
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    lines = [
        f"# M2 Dispatch Plan · {plan_id} ({route})",
        "",
        f"- **Message**: {_text(plan.get('message'))}",
        f"- **Route**: `{route}`",
        f"- **Default Model / Context**: `{default_label}` (`{_text(plan.get('default_model'), DEFAULT_M2_MODEL)}` / `{_text(plan.get('default_context'), DEFAULT_M2_CONTEXT)}`)",
        "- **Owner Gate**: Owner approval required before dispatch.",
    ]
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"- **Quota Protection**: `quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE}` "
            "(Grok Bot 省流防烧额度模式：仅执行单次派发 Fire-and-Forget，自动取消后台 `--watch` 监督与定时 Routine 轮询)"
        )
    lines.extend([
        "",
        "## Target Dispatch Table",
        "",
        "| Wave | Topic | Domain Status | Target Repo | Space | M2 Status | Transport | Model / Context | CWD |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for target in plan.get("targets", []):
        model_label = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        cwd = _expand_cwd(target.get("cwd"))
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.append(
            f"| {target.get('wave_order', 1)} | `{_text(target.get('topic_id'))}` | "
            f"`{_text(target.get('domain_status'))}` | `{_text(target.get('target_repo'), 'unspecified')}` | "
            f"`{_text(target.get('space'), 'M2')} ({ws_badge})` | "
            f"`{_text(target.get('m2_status'))}` | `{_text(target.get('transport'), 'auto')}` | "
            f"`{model_label}` | `{cwd}` |"
        )
    lines.extend(["", "## Target Instructions"])
    for index, target in enumerate(plan.get("targets", []), start=1):
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.extend([
            "",
            f"### {index}. Wave {target.get('wave_order', 1)} · `{_text(target.get('topic_id'))}` → `{_text(target.get('target_repo'), 'unspecified')}` ({_text(target.get('m2_status'))})",
            f"- **Model / Context**: `{_text(target.get('model_label'), DEFAULT_M2_MODEL_LABEL)}` (`{_text(target.get('model'), DEFAULT_M2_MODEL)}` / `{_text(target.get('context'), DEFAULT_M2_CONTEXT)}`)",
            f"- **Space**: `{_text(target.get('space'), 'M2')} ({ws_badge})`",
            f"- **CWD**: `{_expand_cwd(target.get('cwd'))}`",
            f"- **Transport**: `{_text(target.get('transport'), 'auto')}`",
            "",
            "#### `sub_prompt`",
            "```text",
            _text(target.get("sub_prompt")),
            "```",
        ])
        hint = _text(target.get("rag_context_hint"))
        if hint:
            lines.extend([
                "",
                "#### `rag_context_hint`",
                "```text",
                hint,
                "```",
            ])
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            lines.extend([
                "",
                "#### `recent_session_context`",
                f"- **Source**: `{_text(recent_ctx.get('source'), 'session')}` · **Last Activity**: `{_text(recent_ctx.get('last_session_activity_at') or target.get('last_session_activity_at'), '—')}`",
            ])
            for t_idx, turn in enumerate(recent_ctx.get("turns") or [], start=1):
                if isinstance(turn, dict):
                    u_m = _text(turn.get("user_message"))
                    a_m = _text(turn.get("assistant_message"))
                    ts_m = _text(turn.get("timestamp"))
                    ts_part = f" (`{ts_m}`)" if ts_m else ""
                    if u_m and a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}` → Assistant `{a_m}`")
                    elif u_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}`")
                    elif a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: Assistant `{a_m}`")
            if not recent_ctx.get("turns") and _text(recent_ctx.get("summary")):
                lines.append(f"- **Summary**: {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "## Context Eviction", "", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"- completion=`{eviction.get('completion_evidence')}`; is_same_goal_continuation=`{eviction.get('is_same_goal_continuation')}`; prior_context_value=`{eviction.get('prior_context_value')}`"])
    stale = plan.get("stale_m2_candidates", [])
    if stale:
        lines.extend(["", "## Stale / Duplicate M2 Cleanup Candidates", ""])
        for candidate in stale:
            lines.append(
                f"- `{_text(candidate.get('topic_id'))}`: `{_text(candidate.get('session_id'))}` "
                f"`{_text(candidate.get('pane_id'))}` [{_text(candidate.get('reason'))}] {_text(candidate.get('title'))}"
            )
    candidates = plan.get("new_domain_candidates", [])
    if candidates:
        lines.extend(["", "## New Domain Candidates", ""])
        for candidate in candidates:
            lines.append(
                f"- `{_text(candidate.get('topic_id'))}`: {_text(candidate.get('title'))} → "
                f"`{_text(candidate.get('target_repo'))}` (`{_text(candidate.get('group'))}`, cwd `{_expand_cwd(candidate.get('cwd'))}`)"
            )
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "## Cross-Device Active & Pending (Linear `TIA-463`)", "", *cross_lines[1:]])
    return "\n".join(lines) + "\n"

def write_preview_markdown(plan: dict[str, Any], path: str | Path | None = None) -> Path:
    preview_path = Path(path).expanduser() if path else _default_preview_path(_text(plan.get("plan_id"), "m2p-plan"))
    markdown = _text(plan.get("preview_markdown"))
    if not markdown:
        markdown = render_preview_markdown(plan)
    else:
        markdown = markdown + ("\n" if not markdown.endswith("\n") else "")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(markdown, encoding="utf-8")
    plan["preview_markdown"] = markdown
    plan["preview_markdown_path"] = str(preview_path)
    return preview_path

def build_plan(
    message: str,
    sessions: list[dict[str, Any]] | None = None,
    warnings: Iterable[str] = (),
    *,
    domain_map: dict[str, Any] | None = None,
    domain_map_file: str | Path | None = None,
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
    mock_magpie: Any = None,
    targets_json: Any = None,
    magpie_url: str = "",
    magpie_model: str = "",
    mock_jev: Any = None,
    transport: str = "auto",
    preview_md_file: str | Path | None = None,
    write_preview_md: bool = False,
    state_file: str | Path | None = None,
    cross_device_state: dict[str, Any] | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
    herdr_bin: str | None = None,
    session_state_dir: str | Path | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    prefer_issue_cache: bool = False,
) -> dict[str, Any]:
    message = _text(message)
    if not message:
        raise RouterError("message is required")
    plan_id = f"m2p-{uuid.uuid4().hex[:10]}"
    loaded_domain_map = domain_map or load_domain_map(domain_map_file)
    warnings_list = list(warnings)
    if sessions is None or (prefer_issue_cache and len(sessions) == 0 and not getattr(sessions, "discovery_source", "")):
        cached_sessions, cache_warn = discover_from_issue_cache(
            state_file=state_file,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
            cross_device_state=cross_device_state,
            domain_map=loaded_domain_map,
            domain_map_file=domain_map_file,
            sync_linear=sync_linear,
            no_sync_linear=no_sync_linear,
        )
        sessions = cached_sessions
        if cache_warn:
            warnings_list.append(cache_warn)
    workspace_inventory = _workspace_inventory(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    if workspace_inventory is None:
        workspace_inventory = _workspace_records(getattr(sessions, "workspaces", []))
    discovery_source = _text(getattr(sessions, "discovery_source", "")) or ("issue_cache" if prefer_issue_cache else "sessions")
    route, targets, decision = classify(
        message,
        sessions,
        domain_map=loaded_domain_map,
        mock_magpie=mock_magpie,
        targets_json=targets_json,
        magpie_url=magpie_url,
        magpie_model=magpie_model,
        mock=mock_jev,
        goal_id=plan_id,
        workspaces=workspace_inventory,
        runner=runner,
        herdr_bin=herdr_bin,
        session_state_dir=session_state_dir,
    )
    phase0_discussion_only = is_exploratory_discussion_message(message)
    if phase0_discussion_only:
        for target in targets:
            _apply_phase0_discussion_lock(target, message)
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        resolve_target_space(target, topic, workspace_inventory, loaded_domain_map)
        if isinstance(target.get("proposed_domain"), dict):
            resolve_target_space(target, None, workspace_inventory, loaded_domain_map)
    stale_candidates: list[dict[str, Any]] = []
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        if transport != "auto" and target["m2_status"] == "EXISTING_M2":
            target["transport"] = transport
        elif transport != "auto":
            target["transport"] = transport
        raw_cwd = _text(target.get("cwd"))
        if not raw_cwd:
            repo_name = _clean_target_name(target.get("target_repo"))
            raw_cwd = f"~/Development/{repo_name}" if repo_name and repo_name != "unspecified" else "~/Development"
        target["cwd"] = _expand_cwd(raw_cwd)
        if isinstance(target.get("proposed_domain"), dict):
            target["proposed_domain"]["cwd"] = _expand_cwd(target["proposed_domain"].get("cwd") or target["cwd"])
        target["model"] = _text(target.get("model"), DEFAULT_M2_MODEL)
        target["context"] = _text(target.get("context"), DEFAULT_M2_CONTEXT)
        target["model_label"] = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        matched = _target_session(target, topic, sessions, loaded_domain_map) if target.get("m2_status") == "EXISTING_M2" else None
        if target.get("m2_status") == "EXISTING_M2":
            ctx_source = matched if isinstance(matched, dict) else target
            recent_ctx = target.get("recent_session_context") if isinstance(target.get("recent_session_context"), dict) else read_recent_session_context(
                ctx_source,
                runner=runner,
                herdr_bin=herdr_bin,
                session_state_dir=session_state_dir,
            )
            if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("last_session_activity_at")):
                target["recent_session_context"] = recent_ctx
                if _text(recent_ctx.get("last_session_activity_at")):
                    target["last_session_activity_at"] = _text(recent_ctx.get("last_session_activity_at"))
                if _text(recent_ctx.get("agent_session_id")):
                    target["agent_session_id"] = _text(recent_ctx.get("agent_session_id"))
        target.setdefault(
            "context_pass",
            {
                "enabled": bool(target.get("rag_context_hint") or target.get("recent_session_context")),
                "hint": target.get("rag_context_hint"),
                **(
                    {"recent_session_context": target["recent_session_context"]}
                    if isinstance(target.get("recent_session_context"), dict)
                    else {}
                ),
            },
        )
        target["cleanup_candidates"] = _stale_candidates(target, sessions, loaded_domain_map)
        if matched and _text(matched.get("status"), "idle").casefold() in {"idle", "done"}:
            continuity = _evaluate_m2_context_continuity(
                target, matched, message, domain_map=loaded_domain_map, mock_jev=mock_jev,
                handoff_root=os.environ.get("M2_HANDOFF_ROOT"),
                visible_text=_text(matched.get("visible") or matched.get("visible_text")),
            )
            target.update({"m2_action": continuity["m2_action"]})
            target["context_eviction"] = continuity
            if continuity["m2_action"] == "REPLACE_STALE_M2":
                candidate = {"session_id": matched.get("session_id", ""), "pane_id": matched.get("pane_id", ""), "title": matched.get("title", ""), "status": matched.get("status", "idle"), "target_repo": matched.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": "context_eviction", "eviction_reason": continuity["eviction_reason"], "old_goal_done": True, "is_same_goal_continuation": continuity["is_same_goal_continuation"], "prior_context_value": continuity["prior_context_value"]}
                target["cleanup_candidates"] = [candidate]
        stale_candidates.extend(target["cleanup_candidates"])
    unique_stale = {item["session_id"]: item for item in stale_candidates}
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "message": message,
        "default_model": DEFAULT_M2_MODEL,
        "default_context": DEFAULT_M2_CONTEXT,
        "default_model_label": DEFAULT_M2_MODEL_LABEL,
        "decision": {"route": route, **decision},
        "domain_map_file": loaded_domain_map.get("path", str(domain_map_file or DEFAULT_DOMAIN_MAP_PATH)),
        "discovery": {"source": discovery_source, "sessions": sessions, "workspaces": workspace_inventory, "warnings": warnings_list},
        "waves": sorted({target["wave_order"] for target in targets}),
        "targets": targets,
        "new_domain_candidates": decision.get("new_domain_candidates", []),
        "stale_m2_candidates": list(unique_stale.values()),
        "gc_candidates": discover_gc_candidates(list(sessions), handoff_root=os.environ.get("M2_HANDOFF_ROOT")),
        "approval": {"required": True, "approved": False},
        "phase0_discussion_only": phase0_discussion_only,
    }
    cache_path = _resolve_cross_device_cache_path(state_file)
    resolved_cross_state = cross_device_state
    should_sync, _ = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=False,
        state_path=_resolve_supervision_state_path(state_file, persist_by_default=True),
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync and resolved_cross_state is None:
        state_path = _resolve_supervision_state_path(state_file, persist_by_default=True)
        local_board = _compute_board_rollup(_with_locked_supervision_state(state_path, lambda s: s))
        sync_res = sync_cross_device_hub(
            local_board,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        plan["linear_sync"] = sync_res
        if isinstance(sync_res.get("state"), dict):
            resolved_cross_state = sync_res["state"]
    if resolved_cross_state is None:
        resolved_cross_state = _load_cross_device_cache(cache_path)
    cross_summary = _extract_remote_devices_summary(resolved_cross_state, local_hostname=local_hostname)
    plan["local_hostname"] = cross_summary["local_hostname"]
    plan["remote_devices"] = cross_summary["remote_devices"]
    plan["cross_device_in_flight"] = cross_summary["cross_device_in_flight"]
    plan["cross_device_pending_decisions"] = cross_summary["cross_device_pending_decisions"]
    if is_grok_bot_caller(
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=agent_records if agent_records is not None else (sessions if sessions else None),
        herdr_bin=herdr_bin,
        runner=runner,
        probe_herdr=not (prefer_issue_cache or discovery_source == "issue_cache"),
    ):
        plan["quota_protection_mode"] = GROK_QUOTA_PROTECTION_MODE
        plan["quota_protection"] = _quota_protection_payload()
    plan["preview_markdown"] = render_preview_markdown(plan)
    if write_preview_md or preview_md_file is not None:
        write_preview_markdown(plan, preview_md_file)
    return plan

def preview_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Plan {plan['plan_id']} · {plan['decision']['route']}",
        f"Default Model / Context: {_text(plan.get('default_model_label'), DEFAULT_M2_MODEL_LABEL)}",
    ]
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE} "
            "(Grok Bot fire-and-forget: background --watch supervision and routine polling disabled)"
        )
    preview_md_path = _text(plan.get("preview_markdown_path"))
    if preview_md_path:
        lines.append(f"Preview Markdown: {preview_md_path}")
    lines.extend([
        "Owner approval required before dispatch.",
        "",
        "wave | topic_id | space | domain status | target repo | M2 status | transport | content",
        "-----|----------|-------|---------------|-------------|-----------|-----------|--------",
    ])
    for target in plan.get("targets", []):
        marker = " [NEW_DOMAIN_CANDIDATE]" if target.get("domain_status") == NEW_DOMAIN_CANDIDATE else ""
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.append(f"{target.get('wave_order', 1)} | {target.get('topic_id', '')} | {target.get('space', 'M2')} ({ws_badge}) | {target.get('domain_status', '')}{marker} | {target.get('target_repo', 'unspecified')} | {target.get('m2_status')} | {target.get('transport', 'auto')} | {target.get('sub_prompt', '')}")
        hint = _text(target.get("rag_context_hint"))
        if hint:
            lines.append(f"  rag_context_hint: {hint}")
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            ts_label = _text(recent_ctx.get("last_session_activity_at") or target.get("last_session_activity_at"), "recent")
            lines.append(f"  recent_session_context ({ts_label}): {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"completion={eviction.get('completion_evidence')} · continuation={eviction.get('is_same_goal_continuation')} · prior_context_value={eviction.get('prior_context_value')}"])
    stale = plan.get("stale_m2_candidates", [])
    if stale:
        lines.extend(["", "Stale / Duplicate M2 Cleanup Candidates:"])
        for candidate in stale:
            lines.append(f"- {candidate.get('topic_id')}: {candidate.get('session_id')} {candidate.get('pane_id')} [{candidate.get('reason')}] {candidate.get('title')}")
        lines.append("Use --cleanup-duplicates or --replace-stale-m2 only after Owner approval.")
    candidates = plan.get("new_domain_candidates", [])
    if candidates:
        lines.extend(["", "New domain candidates (Owner decision required):"])
        for candidate in candidates:
            lines.append(f"- {candidate.get('topic_id')}: {candidate.get('title')} → {candidate.get('target_repo')} ({candidate.get('group')})")
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "Cross-Device Active & Pending (TIA-463):", *cross_lines[1:]])
    return "\n".join(lines)

def _post_json(url: str, payload: dict[str, Any], *, token: str = "", timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RouterError(f"non-object response from {url}")
    if body.get("ok") is False:
        raise RouterError(_text(body.get("message") or body.get("error"), f"request failed: {url}"))
    return body

def _url_base(target: dict[str, Any], plan: dict[str, Any]) -> str:
    explicit = _text(target.get("dsh_base_url") or target.get("base_url") or os.environ.get("DSH_M2_BASE_URL"))
    if explicit:
        return explicit.rstrip("/")
    state_url = _text(plan.get("discovery", {}).get("dsh_state_url"))
    return re.sub(r"/m2-omni-hub/api/state/?$", "", state_url).rstrip("/")

def _apply_dsh(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
    base = _url_base(target, plan)
    if not base:
        raise RouterError(f"no dsh endpoint for {target.get('target_repo', 'target')}")
    token = _text(target.get("dsh_token") or os.environ.get("DSH_M2_TOKEN"))
    session_id = _text(target.get("session_id"))
    expanded_cwd = _expand_cwd(target.get("cwd") or os.environ.get("DSH_M2_CWD", ""))
    created = False
    if target.get("m2_status") == "NEW_M2":
        created_body = poster(base + "/m2-omni-hub/api/create-session", {"topic": target.get("target_repo", "New M2"), "cwd": expanded_cwd}, token=token)
        session = created_body.get("session") if isinstance(created_body.get("session"), dict) else created_body
        session_id = _text(session.get("sessionId") or session.get("id"))
        if not session_id:
            raise RouterError("dsh create-session did not return a session id")
        created = True
    if not session_id:
        raise RouterError("dsh target has no session id")
    delivery = {"sessionStrategy": "pinned", "sessionKey": f"m2-router:{target.get('target_repo', session_id)}", "sessionId": session_id, "title": target.get("title") or f"[M2] {target.get('target_repo', 'M2')}", "markdown": prompt, "deliveryKey": f"{plan['plan_id']}:{target.get('target_repo', session_id)}"}
    try:
        receipt = poster(base + "/dsh-session-sink/api/deliver", delivery, token=token)
        return {"ok": True, "method": "dsh.session-sink", "target": session_id, "created": created, "cwd": expanded_cwd, "receipt": receipt}
    except Exception:
        if created:
            raise
        receipt = poster(base + "/m2-omni-hub/api/dispatch", {"message": prompt, "targetSessionIds": [session_id], "forceMode": "single"}, token=token)
        return {"ok": True, "method": "dsh.omni-hub.dispatch", "target": session_id, "created": False, "cwd": expanded_cwd, "receipt": receipt}

def _herdr_binary(target: dict[str, Any]) -> str:
    return _text(target.get("herdr_bin") or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")

def _new_herdr_agent_name(target: dict[str, Any], *, index: int = 1, used_names: set[str] | None = None) -> str:
    explicit = _text(target.get("agent_name"))
    if explicit:
        base_name = explicit[:32].rstrip("-")
    else:
        raw_topic = _text(target.get("topic_id")).lower()
        if raw_topic.startswith("new-topic-"):
            raw_topic = raw_topic[len("new-topic-"):]
        topic_slug = re.sub(r"[^a-z0-9_-]+", "-", raw_topic).strip("-")
        repo_slug = re.sub(r"[^a-z0-9_-]+", "-", _text(target.get("target_repo"), "m2").lower()).strip("-") or "m2"
        if topic_slug and topic_slug not in {NEW_UNMAPPED_DOMAIN.lower(), "unmapped-domain"} and topic_slug != repo_slug:
            full_slug = topic_slug if repo_slug in topic_slug else f"{topic_slug}-{repo_slug}"
        else:
            full_slug = repo_slug
        if len(full_slug) > 25:
            digest = hashlib.sha256(f"{repo_slug}:{topic_slug or full_slug}".encode("utf-8")).hexdigest()[:6]
            slug = f"{full_slug[:18].rstrip('-')}-{digest}"
        else:
            slug = full_slug
        base_name = f"m2-{slug[:25]}-r1"[:32].rstrip("-")
    if used_names is not None and base_name in used_names:
        stem = re.sub(r"-r\d+$", "", base_name)[:24].rstrip("-")
        base_name = f"{stem}-{index}-r1"[:32].rstrip("-")
    if used_names is not None:
        used_names.add(base_name)
    return base_name

def _resolve_target_transport(target: dict[str, Any], plan: dict[str, Any], requested: str) -> str:
    selected = requested if requested != "auto" else _text(target.get("transport"), "auto")
    if selected != "auto":
        return selected
    source = _text(target.get("source"))
    if source in {"herdr", "dsh"}:
        return source
    discovered_sources = {_text(item.get("source")) for item in (plan.get("discovery") or {}).get("sessions", []) if isinstance(item, dict)}
    if "herdr" in discovered_sources:
        return "herdr"
    if "dsh" in discovered_sources or _url_base(target, plan):
        return "dsh"
    if _text(target.get("dispatch_script") or os.environ.get("M2_ROUTER_HERDR_DISPATCH") or os.environ.get("HERDR_PANE_ID") or os.environ.get("HERDR_BIN") or shutil.which("herdr")):
        return "herdr"
    raise RouterError(f"cannot resolve delivery transport for NEW_M2 target {target.get('target_repo', 'unspecified')}; specify --transport herdr|dsh|stdout")

def _authoritative_apply_space(target: dict[str, Any], plan: dict[str, Any], domain_map_file: str | Path | None = None) -> tuple[str, list[str]]:
    topic_id = _text(target.get("topic_id"))
    if topic_id and topic_id != NEW_UNMAPPED_DOMAIN:
        try:
            topic = _topic_by_id(load_domain_map(domain_map_file or plan.get("domain_map_file"))).get(topic_id)
        except RouterError:
            topic = None
        if topic:
            return _text(topic.get("space")) or _text(topic.get("target_repo"), "M2"), list(topic.get("space_aliases") or [])
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    return (
        _text(proposed.get("space") or target.get("space") or target.get("target_repo"), "M2"),
        list(proposed.get("space_aliases") or target.get("space_aliases") or []),
    )

def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, index: int = 1, used_names: set[str] | None = None, created_workspaces: dict[str, str] | None = None, domain_map_file: str | Path | None = None) -> dict[str, Any]:
    binary = _herdr_binary(target)
    model = _text(target.get("model"), _text(plan.get("default_model"), DEFAULT_M2_MODEL))
    context = _text(target.get("context"), _text(plan.get("default_context"), DEFAULT_M2_CONTEXT))
    model_label = _text(target.get("model_label"), _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL))
    expanded_cwd = _expand_cwd(_text(target.get("cwd")) or str(Path.home() / "Development"))
    if target.get("m2_status") == "EXISTING_M2":
        agent = _text(target.get("herdr_target") or target.get("session_id"))
        if not agent:
            raise RouterError("Herdr target has no agent name")
        result = runner([binary, "agent", "prompt", agent, prompt], timeout=10.0)
        if result.returncode != 0:
            raise RouterError(f"Herdr prompt failed for {agent}: {result.stderr.strip()}")
        return {"ok": True, "method": "herdr.agent.prompt", "target": agent, "created": False, "model": model, "context": context, "model_label": model_label, "cwd": expanded_cwd, "stdout": result.stdout.strip()}

    name = _new_herdr_agent_name(target, index=index, used_names=used_names)
    dispatch = _text(target.get("dispatch_script") or os.environ.get("M2_ROUTER_HERDR_DISPATCH")) or str(Path.home() / "Development" / "user-skills" / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh")
    mapped_space, _ = _authoritative_apply_space(target, plan, domain_map_file)
    spawn_mode = "new_workspace"
    args = [
        dispatch,
        "--name",
        name,
        "--kind",
        _text(target.get("kind"), "copilot"),
        "--cwd",
        expanded_cwd,
        "--prompt",
        prompt,
        "--new-workspace",
        "--workspace-label",
        mapped_space,
        "--",
        "--model",
        model,
        "--context",
        context,
    ]
    result = runner(args, timeout=30.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr new M2 failed for {name}: {result.stderr.strip()}")
    parsed = _parse_json_output(result.stdout)
    reused = bool(isinstance(parsed, dict) and parsed.get("reused_existing"))
    resolved_ws = _text((parsed or {}).get("workspace_id")) if isinstance(parsed, dict) else ""
    if resolved_ws and created_workspaces is not None:
        created_workspaces[name] = resolved_ws
    if reused and _text((parsed or {}).get("agent_status")).casefold() == "working":
        prompt_res = runner([binary, "agent", "prompt", name, prompt], timeout=10.0)
        if prompt_res.returncode != 0:
            raise RouterError(f"Herdr prompt failed for reused working agent {name}: {prompt_res.stderr.strip()}")
    return {"ok": True, "method": "herdr-dispatch", "target": name, "created": not reused, "model": model, "context": context, "model_label": model_label, "cwd": expanded_cwd, "space": mapped_space, "workspace_id": resolved_ws, "spawn_mode": spawn_mode, "receipt": parsed or result.stdout.strip()}

def _register_new_domains(plan: dict[str, Any], domain_map_file: str | Path | None = None, *, dry_run: bool = False) -> list[str]:
    candidates = plan.get("new_domain_candidates", [])
    if not candidates:
        return []
    path = Path(domain_map_file or plan.get("domain_map_file") or DEFAULT_DOMAIN_MAP_PATH).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RouterError(f"unable to update domain map {path}: {error}") from error
    groups = value.setdefault("groups", [])
    registered: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not _text(candidate.get("topic_id")):
            continue
        topic_id = candidate["topic_id"]
        if any(isinstance(group, dict) and any(isinstance(topic, dict) and topic.get("topic_id") == topic_id for topic in group.get("topics", [])) for group in groups):
            continue
        group_id = _text(candidate.get("group"), "new-domain")
        group = next((item for item in groups if isinstance(item, dict) and item.get("group") == group_id), None)
        if group is None:
            group = {"group": group_id, "title": group_id.replace("-", " ").title(), "topics": []}
            groups.append(group)
        topic = {key: candidate.get(key) for key in ("topic_id", "group", "title", "target_repo", "cwd", "space", "space_aliases", "keywords", "aliases")}
        group.setdefault("topics", []).append(topic)
        registered.append(topic_id)
    if registered and not dry_run:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return registered

def _revalidate_herdr_candidate(
    candidate: dict[str, Any],
    *,
    binary: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[bool, str]:
    pane_id = _text(candidate.get("pane_id"))
    session_id = _text(candidate.get("session_id"))
    try:
        pane_res = runner([binary, "pane", "list"], timeout=3.0)
        agent_res = runner([binary, "agent", "list"], timeout=3.0)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"revalidation_failed:{error}"
    pane_records = _records(_parse_json_output(pane_res.stdout)) if getattr(pane_res, "returncode", 1) == 0 else []
    agent_records = _records(_parse_json_output(agent_res.stdout)) if getattr(agent_res, "returncode", 1) == 0 else []
    if not pane_records and not agent_records:
        return False, "revalidation_inventory_unavailable"
    merged = _merge_herdr_records(pane_records, agent_records)
    live = next(
        (
            rec for rec in merged
            if _text(rec.get("pane_id") or rec.get("paneId")) == pane_id
            or (session_id and _session_id(rec) == session_id)
        ),
        None,
    )
    if live is None:
        return False, "candidate_missing"
    live_pane = _text(live.get("pane_id") or live.get("paneId"))
    if not live_pane or live_pane != pane_id:
        return False, "pane_identity_changed"
    live_sid = _session_id(live)
    if session_id and live_sid and live_sid not in {session_id, pane_id}:
        return False, "session_identity_changed"
    live_status = _text(live.get("agent_status") or live.get("status")).casefold()
    if live_status not in {"idle", "done"}:
        return False, f"pane_not_idle:{live_status or 'unknown'}"
    if _gc_protected(live) or pane_id in {"w2J:p1", "w1:p1"}:
        return False, "protected_or_active_tail"
    visible = _text(live.get("visible") or live.get("visible_text"))
    if not visible:
        visible = _read_herdr_visible(binary, pane_id, runner=runner)
    if not visible or _has_active_visible_tail(visible):
        return False, "visible_tail_unavailable_or_active"
    return True, "ok"

def _close_herdr_pane(
    candidate: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    dry_run: bool = False,
) -> dict[str, Any]:
    pane_id = _text(candidate.get("pane_id"))
    if not pane_id or pane_id == _text(os.environ.get("HERDR_PANE_ID")) or pane_id in {"w1:p1", "w2J:p1"} or re.search(r"Cindy|大总管", " ".join(_text(candidate.get(k)) for k in ("workspace_label", "workspaceLabel", "space", "label", "title", "name")), re.I):
        return {"ok": False, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": True, "reason": "protected_pane"}
    if dry_run:
        return {"ok": True, "method": "stdout", "pane_id": pane_id, "skipped": False, "simulated": True}
    binary = _text(os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    eligible, reason = _revalidate_herdr_candidate(candidate, binary=binary, runner=runner)
    if not eligible:
        return {"ok": False, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": True, "reason": reason}
    result = runner([binary, "pane", "close", pane_id], timeout=10.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr pane close failed for {pane_id}: {result.stderr.strip()}")
    return {"ok": True, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": False, "stdout": result.stdout.strip()}

def _cleanup_stale(
    plan: dict[str, Any],
    mode: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    dry_run: bool = False,
    dry_run_sessions: set[str] | None = None,
) -> list[dict[str, Any]]:
    if mode == "none":
        return []
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in plan.get("stale_m2_candidates", []):
        session_id = _text(candidate.get("session_id"))
        if session_id in seen:
            continue
        seen.add(session_id)
        if mode == "duplicates" and candidate.get("reason") != "duplicate_m2":
            continue
        if mode == "replace_context" and candidate.get("reason") != "context_eviction":
            continue
        candidate_dry_run = dry_run or bool(dry_run_sessions and session_id in dry_run_sessions)
        receipts.append({"session_id": session_id, "reason": candidate.get("reason"), **_close_herdr_pane(candidate, runner=runner, dry_run=candidate_dry_run)})
    return receipts

def _resolve_supervision_state_path(
    state_file: str | Path | None = None,
    *,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    apply_receipt: dict[str, Any] | None = None,
    safe_plan_id: str = "m2p-plan",
    persist_by_default: bool = False,
) -> Path | None:
    if state_file:
        return Path(state_file).expanduser()
    if isinstance(apply_receipt, dict) and _text(apply_receipt.get("supervision_state_path")):
        return Path(_text(apply_receipt.get("supervision_state_path"))).expanduser()
    env_state = _text(os.environ.get("M2_SUPERVISION_STATE_FILE"))
    if env_state:
        return Path(env_state).expanduser()
    if apply_receipt_file or plan_file:
        anchor = Path(apply_receipt_file or plan_file).expanduser()
        if anchor.parent in {Path("/tmp"), Path("/private/tmp")}:
            return Path("/tmp/m2-supervision-state.json")
        return anchor.parent / "m2-supervision-state.json"
    if persist_by_default:
        return Path("/tmp/m2-supervision-state.json")
    return None
