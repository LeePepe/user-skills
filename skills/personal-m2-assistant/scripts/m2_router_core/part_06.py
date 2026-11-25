def parse_cross_device_hub_description(description: str) -> dict[str, Any]:
    text = str(description or "")
    state_obj: dict[str, Any] = {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}
    if M2_SYNC_STATE_START in text and M2_SYNC_STATE_END in text:
        between = text.split(M2_SYNC_STATE_START, 1)[1].split(M2_SYNC_STATE_END, 1)[0]
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", between, re.S)
        raw_json = code_match.group(1) if code_match else between.strip()
        try:
            loaded = json.loads(raw_json)
            if isinstance(loaded, dict):
                devices_raw = loaded.get("devices")
                state_obj = {
                    "schema": _text(loaded.get("schema"), M2_SYNC_SCHEMA),
                    "updated_at": _text(loaded.get("updated_at")),
                    "devices": {
                        str(k): dict(v)
                        for k, v in (devices_raw.items() if isinstance(devices_raw, dict) else [])
                        if isinstance(v, dict)
                    },
                }
        except (ValueError, TypeError):
            pass

    gtt_suffix = ""
    gtt_start = text.find("<!-- GTT-LINEAR-SYNC:START -->")
    if gtt_start >= 0:
        gtt_suffix = text[gtt_start:].strip()

    body_before_details = text.split("<details>", 1)[0] if "<details>" in text else (text[:gtt_start] if gtt_start >= 0 else text)
    pending_by_host: dict[str, list[str]] = {}
    in_flight_rows_by_host: dict[str, list[str]] = {}
    backlog_lines_by_host: dict[str, list[str]] = {}
    completed_rows_by_host: dict[str, list[str]] = {}
    line_scopes: dict[str, str] = {}

    def _extract_host_from_bullet(line: str) -> str:
        host_m = re.search(r"\(`([^`]+)`\)\s*$", line)
        if not host_m:
            host_m = re.search(r"\[Host:\s*([^\]]+)\]", line)
        return host_m.group(1).strip() if host_m else ""

    def _collect_table_rows(lines_iter: Iterable[str], target_map: dict[str, list[str]], scope_hint: str) -> None:
        for raw_line in lines_iter:
            line = raw_line.strip()
            if not line.startswith("|") or line.count("|") < 3:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0]
            if not first or all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            if "设备" in first or "Host" in first:
                continue
            host_name = first.strip("` ").strip()
            if host_name and host_name not in {"—", "-"}:
                if line not in target_map.setdefault(host_name, []):
                    target_map[host_name].append(line)
                if scope_hint:
                    line_scopes[line] = scope_hint

    current_part_scope = ""
    current_sub_kind = ""
    sub_buffer: list[str] = []

    def _flush_sub_buffer() -> None:
        nonlocal sub_buffer
        if not current_sub_kind or not sub_buffer:
            sub_buffer = []
            return
        if current_sub_kind == "pending":
            for raw_line in sub_buffer:
                line = raw_line.strip()
                if not line.startswith("-"):
                    continue
                host_key = _extract_host_from_bullet(line)
                if host_key:
                    if line not in pending_by_host.setdefault(host_key, []):
                        pending_by_host[host_key].append(line)
                    if current_part_scope:
                        line_scopes[line] = current_part_scope
        elif current_sub_kind == "backlog":
            for raw_line in sub_buffer:
                line = raw_line.strip()
                if not line.startswith("-"):
                    continue
                host_key = _extract_host_from_bullet(line)
                if host_key:
                    if line not in backlog_lines_by_host.setdefault(host_key, []):
                        backlog_lines_by_host[host_key].append(line)
                    if current_part_scope:
                        line_scopes[line] = current_part_scope
        elif current_sub_kind == "in_flight":
            _collect_table_rows(sub_buffer, in_flight_rows_by_host, current_part_scope)
        elif current_sub_kind == "completed":
            _collect_table_rows(sub_buffer, completed_rows_by_host, current_part_scope)
        sub_buffer = []

    for raw_line in body_before_details.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            _flush_sub_buffer()
            current_sub_kind = ""
            low_h2 = stripped.casefold()
            if "non-work" in low_h2 or "非工作" in stripped:
                current_part_scope = "non-work"
            elif re.search(r"\bwork\b", low_h2) or "工作业务" in stripped:
                current_part_scope = "work"
            continue
        if stripped.startswith("### "):
            _flush_sub_buffer()
            low_h3 = stripped.casefold()
            if "non-work" in low_h3 or "非工作" in stripped:
                current_part_scope = "non-work"
            elif re.search(r"\bwork\b", low_h3) or "工作业务" in stripped:
                current_part_scope = "work"
            if "🚨" in stripped:
                current_sub_kind = "pending"
            elif "⏳" in stripped:
                current_sub_kind = "in_flight"
            elif "🗂" in stripped or "backlog" in low_h3:
                current_sub_kind = "backlog"
            elif "✅" in stripped:
                current_sub_kind = "completed"
            else:
                current_sub_kind = ""
            continue
        if stripped == "---":
            _flush_sub_buffer()
            current_sub_kind = ""
            continue
        if current_sub_kind:
            sub_buffer.append(raw_line)
    _flush_sub_buffer()

    return {
        "state": state_obj,
        "pending_lines_by_host": pending_by_host,
        "in_flight_rows_by_host": in_flight_rows_by_host,
        "backlog_lines_by_host": backlog_lines_by_host,
        "completed_rows_by_host": completed_rows_by_host,
        "line_scopes": line_scopes,
        "gtt_suffix": gtt_suffix,
    }

def _build_local_device_shard(
    board_rollup: dict[str, Any],
    *,
    previous_shard: dict[str, Any] | None = None,
    now_iso: str = "",
    spaces: list[dict[str, Any]] | None = None,
    agents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timestamp = now_iso or _cst_now_iso()
    prev = previous_shard if isinstance(previous_shard, dict) else {}
    try:
        domain_map = load_domain_map()
    except RouterError:
        domain_map = {}
    topics_by_id = _topic_by_id(domain_map) if isinstance(domain_map, dict) else {}
    slots = [dict(s) for s in (board_rollup.get("slots") or []) if isinstance(s, dict)]
    in_flight_items: list[dict[str, Any]] = []
    pending_decisions: list[str] = []
    recent_completed: list[Any] = []
    active_issues: set[str] = set()
    active_running_topics: set[str] = set()

    for slot in slots:
        status = _text(slot.get("status")).lower()
        retained = bool(slot.get("retained"))
        slot_id = _text(slot.get("slot_id") or slot.get("run_label"))
        topic_id = _text(slot.get("topic_id") or slot.get("target_repo") or slot_id)
        session_id = _text(slot.get("target") or slot.get("session_id") or slot_id)
        pane_id = _text(slot.get("pane_id"))
        issue = _text(slot.get("linear_issue"))
        summary = _text(slot.get("m1_progress") or slot.get("visible_summary") or slot.get("sub_prompt"), "—").replace("\n", " / ")[:280]
        scope = _classify_hub_scope(slot, domain_map=domain_map)
        if status in {"working", "running", "blocked", "needs_input", "ready", "unknown"} or retained:
            entry: dict[str, Any] = {
                "slot_id": slot_id,
                "topic_id": topic_id,
                "session_id": session_id,
                "pane_id": pane_id,
                "issue": issue,
                "summary": summary,
                "scope": scope,
            }
            if retained:
                entry["retained"] = True
            in_flight_items.append(entry)
            if issue:
                active_issues.add(issue)
            if status in {"working", "running"} and topic_id:
                active_running_topics.add(topic_id.casefold())
            if bool(slot.get("needs_owner_attention")) or status in {"blocked", "needs_input"}:
                decision_key = issue or slot_id
                if decision_key and decision_key not in pending_decisions:
                    pending_decisions.append(decision_key)
        elif status in {"idle", "done"}:
            comp_entry: dict[str, Any] = {
                "slot_id": slot_id,
                "topic_id": topic_id,
                "target_repo": _text(slot.get("target_repo") or topic_id),
                "issue": issue or slot_id,
                "summary": summary,
                "scope": scope,
            }
            recent_completed.append(comp_entry)

    # Preserve prior recent_completed items from this host that aren't currently back in-flight
    seen_completed_keys: set[str] = {
        _text(item.get("issue") if isinstance(item, dict) else item)
        for item in recent_completed
        if _text(item.get("issue") if isinstance(item, dict) else item)
    }
    for old_comp in (prev.get("recent_completed") or []):
        key = _text(old_comp.get("issue") if isinstance(old_comp, dict) else old_comp)
        if key and key not in seen_completed_keys and key not in active_issues:
            recent_completed.append(old_comp)
            seen_completed_keys.add(key)

    raw_backlog = board_rollup.get("backlog") if "backlog" in board_rollup else prev.get("backlog")
    backlog_items: list[dict[str, Any]] = []
    for raw_b in (raw_backlog or []):
        if isinstance(raw_b, dict):
            b_topic = _text(raw_b.get("topic_id") or raw_b.get("id"))
            if b_topic and b_topic.casefold() in active_running_topics:
                continue
            b_copy = dict(raw_b)
            b_copy["scope"] = _classify_hub_scope(b_copy, domain_map=domain_map)
            backlog_items.append(b_copy)

    # Build stored spaces and agents for fast --plan Issue-cache discovery
    raw_spaces = (
        spaces
        if spaces is not None
        else (
            board_rollup.get("spaces")
            if isinstance(board_rollup.get("spaces"), list)
            else (
                board_rollup.get("workspaces")
                if isinstance(board_rollup.get("workspaces"), list)
                else prev.get("spaces")
            )
        )
    )
    resolved_spaces: list[dict[str, Any]] = _workspace_records(raw_spaces) if isinstance(raw_spaces, list) else []
    space_by_id = {_text(sp.get("workspace_id")): sp for sp in resolved_spaces if _text(sp.get("workspace_id"))}

    raw_agents_source = (
        agents
        if agents is not None
        else (
            board_rollup.get("agents")
            if isinstance(board_rollup.get("agents"), list)
            else prev.get("agents")
        )
    )
    agent_by_key: dict[str, dict[str, Any]] = {}
    if isinstance(raw_agents_source, list):
        for ag in raw_agents_source:
            if not isinstance(ag, dict):
                continue
            ag_pane = _text(ag.get("pane_id") or ag.get("paneId"))
            ag_sid = _text(ag.get("session_id") or ag.get("herdr_target") or ag.get("target") or ag.get("name"))
            ag_key = ag_pane or ag_sid
            if ag_key:
                clean_ag = {
                    k: v
                    for k, v in ag.items()
                    if k not in {"_live_herdr", "_from_issue_cache", "recent_session_context", "last_session_activity_at"}
                }
                agent_by_key[ag_key] = clean_ag

    for slot in slots:
        pane_id = _text(slot.get("pane_id"))
        session_id = _text(slot.get("target") or slot.get("session_id") or slot.get("slot_id"))
        key = pane_id or session_id
        if not key:
            continue
        topic_id = _text(slot.get("topic_id"))
        topic_meta = topics_by_id.get(topic_id, {})
        ws_id = _text(slot.get("workspace_id") or (pane_id.split(":", 1)[0] if ":" in pane_id else ""))
        ws_label = _text(
            slot.get("workspace_label")
            or (space_by_id.get(ws_id, {}).get("label") if ws_id else "")
            or (topic_meta.get("space") if ws_id and topic_meta else "")
        )
        status = _text(slot.get("status"), "idle").lower()
        target_repo = _text(slot.get("target_repo") or topic_meta.get("target_repo"))
        cwd = _expand_cwd(slot.get("cwd") or topic_meta.get("cwd"))
        title = _text(slot.get("terminal_title") or slot.get("title") or topic_meta.get("title") or session_id)
        summary = _text(slot.get("m1_progress") or slot.get("visible_summary") or slot.get("sub_prompt"))[:280]
        existing_ag = dict(agent_by_key.get(key) or {})
        existing_ag.update({
            "session_id": session_id,
            "herdr_target": _text(slot.get("target") or existing_ag.get("herdr_target") or session_id),
            "pane_id": pane_id or _text(existing_ag.get("pane_id")),
            "workspace_id": ws_id or _text(existing_ag.get("workspace_id")),
            "workspace_label": ws_label or _text(existing_ag.get("workspace_label")),
            "topic_id": topic_id or _text(existing_ag.get("topic_id")),
            "target_repo": target_repo or _text(existing_ag.get("target_repo")),
            "title": title,
            "status": status if status in ACTIVE_STATUSES else "idle",
            "cwd": cwd or _text(existing_ag.get("cwd")),
            "source": _text(slot.get("source") or existing_ag.get("source"), "herdr"),
            "summary": summary,
            "is_m2": True,
        })
        if _text(slot.get("agent_session_id")):
            existing_ag["agent_session_id"] = _text(slot.get("agent_session_id"))
        if _text(slot.get("linear_issue")):
            existing_ag["linear_issue"] = _text(slot.get("linear_issue"))
        agent_by_key[key] = existing_ag
        if ws_id and ws_label and ws_id not in space_by_id:
            synth_ws = {
                "workspace_id": ws_id,
                "label": ws_label,
                "active_tab_id": "",
                "tab_count": 1,
                "pane_count": 1,
                "agent_status": status,
            }
            space_by_id[ws_id] = synth_ws
            resolved_spaces.append(synth_ws)

    shard_out: dict[str, Any] = {
        "updated_at": timestamp,
        "spaces": resolved_spaces,
        "agents": list(agent_by_key.values()),
        "in_flight": in_flight_items,
        "pending_decisions": pending_decisions,
        "recent_completed": recent_completed[:12],
    }
    if backlog_items:
        shard_out["backlog"] = backlog_items
    return shard_out

def render_cross_device_hub_markdown(
    state: dict[str, Any],
    *,
    preserved_sections: dict[str, Any] | None = None,
    local_hostname: str = "",
) -> str:
    preserved = preserved_sections or {}
    pending_by_host: dict[str, list[str]] = dict(preserved.get("pending_lines_by_host") or {})
    in_flight_by_host: dict[str, list[str]] = dict(preserved.get("in_flight_rows_by_host") or {})
    backlog_by_host: dict[str, list[str]] = dict(preserved.get("backlog_lines_by_host") or {})
    completed_by_host: dict[str, list[str]] = dict(preserved.get("completed_rows_by_host") or {})
    line_scopes: dict[str, str] = dict(preserved.get("line_scopes") or {})
    gtt_suffix = _text(preserved.get("gtt_suffix"))
    try:
        domain_map = load_domain_map()
    except RouterError:
        domain_map = {}
    devices: dict[str, dict[str, Any]] = {
        str(k): dict(v)
        for k, v in ((state.get("devices") or {}).items() if isinstance(state.get("devices"), dict) else [])
        if isinstance(v, dict)
    }

    def _ordered_hosts() -> list[str]:
        keys = list(devices.keys())
        for extra_map in (pending_by_host, in_flight_by_host, backlog_by_host, completed_by_host):
            for k in extra_map:
                if k not in keys:
                    keys.append(k)
        return keys

    hosts = _ordered_hosts()
    pending_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    in_flight_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    backlog_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    completed_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}

    def _append_unique(bucket: dict[str, list[str]], scope: str, line: str) -> None:
        key = "work" if scope == "work" else "non-work"
        if line not in bucket[key]:
            bucket[key].append(line)

    def _scope_for_host_issue(shard: dict[str, Any], issue_or_text: str, rich_line: str = "") -> str:
        hinted = line_scopes.get(rich_line, "")
        if hinted:
            return hinted
        needle = _text(issue_or_text)
        if needle:
            for coll_key in ("in_flight", "backlog", "recent_completed"):
                for entry in (shard.get(coll_key) or []):
                    if isinstance(entry, dict) and needle in {_text(entry.get("issue")), _text(entry.get("topic_id")), _text(entry.get("slot_id"))}:
                        return _classify_hub_scope(entry, rich_line=rich_line, domain_map=domain_map)
        return _classify_hub_scope(issue_or_text, rich_line=rich_line, domain_map=domain_map)

    # Pending decisions (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_pending = shard.get("pending_decisions") or []
        rich_lines = pending_by_host.get(host) or []
        all_pending_seen: list[str] = []
        if host != local_hostname and rich_lines and shard_pending:
            for line in rich_lines:
                sc = _scope_for_host_issue(shard, "", line)
                _append_unique(pending_by_scope, sc, line)
                all_pending_seen.append(line)
            for item in shard_pending:
                item_str = _text(item.get("issue") or item.get("summary") if isinstance(item, dict) else item)
                if item_str and not any(item_str in existing for existing in all_pending_seen):
                    line = f"- [ ] {_format_linear_issue_link(item_str)} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map) if isinstance(item, dict) else _scope_for_host_issue(shard, item_str, line)
                    _append_unique(pending_by_scope, sc, line)
                    all_pending_seen.append(line)
        else:
            for item in shard_pending:
                if isinstance(item, dict):
                    issue_ref = _format_linear_issue_link(_text(item.get("issue")))
                    desc = _text(item.get("summary") or item.get("title"))
                    line = f"- [ ] {issue_ref}{(' · ' + desc) if desc else ''} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map)
                else:
                    raw_item = _text(item)
                    matched_rich = next((rl for rl in rich_lines if raw_item and raw_item in rl), "")
                    if matched_rich:
                        line = matched_rich
                    elif re.match(r"^TIA-\d+$", raw_item, re.I):
                        line = f"- [ ] {_format_linear_issue_link(raw_item)} · 待 Owner 拍板 (`{host}`)"
                    else:
                        line = f"- [ ] {raw_item} (`{host}`)"
                    sc = _scope_for_host_issue(shard, raw_item, line)
                _append_unique(pending_by_scope, sc, line)

    # In-Flight & Retained by Device (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_in_flight = shard.get("in_flight") or []
        rich_rows = in_flight_by_host.get(host) or []
        if host != local_hostname and rich_rows and shard_in_flight:
            for r in rich_rows:
                cells = [c.strip("` ").strip() for c in r.strip("|").split("|")]
                row_topic = cells[1] if len(cells) > 1 else ""
                matched_entry = next(
                    (it for it in shard_in_flight if isinstance(it, dict) and _text(it.get("topic_id") or it.get("slot_id")) == row_topic),
                    None,
                )
                sc = _classify_hub_scope(matched_entry or row_topic, rich_line=r, domain_map=domain_map, hint_scope=line_scopes.get(r, ""))
                _append_unique(in_flight_by_scope, sc, r)
        else:
            hb = _format_cst_heartbeat(_text(shard.get("updated_at") or state.get("updated_at")))
            for item in shard_in_flight:
                if not isinstance(item, dict):
                    continue
                topic = _text(item.get("topic_id") or item.get("slot_id"), "—")
                session_id = _text(item.get("session_id") or item.get("target"), "—")
                pane_id = _text(item.get("pane_id"))
                session_cell = f"`{session_id}` (`{pane_id}`)" if pane_id else f"`{session_id}`"
                issue_cell = _format_linear_issue_link(_text(item.get("issue")))
                summary_cell = _text(item.get("summary"), "—").replace("|", "\\|").replace("\n", "&#10;")
                item_hb = _format_cst_heartbeat(_text(item.get("updated_at") or shard.get("updated_at") or state.get("updated_at"))) or hb
                row_str = f"| `{host}` | `{topic}` | {session_cell} | {issue_cell} | {summary_cell} | `{item_hb}` |"
                sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map)
                _append_unique(in_flight_by_scope, sc, row_str)

    # Deferred Backlog by Device (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_backlog = shard.get("backlog") or []
        rich_backlog = backlog_by_host.get(host) or []
        if host != local_hostname and rich_backlog and not shard_backlog:
            for line in rich_backlog:
                sc = _classify_hub_scope(None, rich_line=line, domain_map=domain_map, hint_scope=line_scopes.get(line, ""))
                _append_unique(backlog_by_scope, sc, line)
        else:
            for item in shard_backlog:
                if isinstance(item, dict):
                    b_topic = _text(item.get("topic_id") or item.get("id") or item.get("title"), "backlog")
                    b_summary = _text(item.get("summary") or item.get("intent") or item.get("title"), "暂缓执行")
                    b_issue = _text(item.get("issue"))
                    issue_part = f" · {_format_linear_issue_link(b_issue)}" if b_issue else ""
                    line = f"- ⏸️ `{b_topic}`{issue_part} · {b_summary} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map)
                else:
                    raw_b = _text(item)
                    line = f"- ⏸️ {raw_b} (`{host}`)"
                    sc = _classify_hub_scope(raw_b, rich_line=line, domain_map=domain_map)
                _append_unique(backlog_by_scope, sc, line)

    # Recent Completed Rollup (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_completed = shard.get("recent_completed") or []
        rich_rows = completed_by_host.get(host) or []
        seen_comp_rows: list[str] = []
        if host != local_hostname and rich_rows:
            for r in rich_rows:
                sc = _classify_hub_scope(None, rich_line=r, domain_map=domain_map, hint_scope=line_scopes.get(r, ""))
                _append_unique(completed_by_scope, sc, r)
                seen_comp_rows.append(r)
            for item in shard_completed:
                item_key = _text(item.get("issue") if isinstance(item, dict) else item)
                if item_key and not any(item_key in existing for existing in seen_comp_rows):
                    if isinstance(item, dict):
                        topic = _text(item.get("topic_id") or item.get("target_repo"), "—")
                        issue_cell = _format_linear_issue_link(item_key)
                        summary_cell = _text(item.get("summary"), "已完成").replace("|", "\\|")
                        row_str = f"| `{host}` | `{topic}` | {issue_cell} | {summary_cell} |"
                        sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map)
                    else:
                        row_str = f"| `{host}` | `—` | {_format_linear_issue_link(item_key)} | 已完成 |"
                        sc = _scope_for_host_issue(shard, item_key, row_str)
                    _append_unique(completed_by_scope, sc, row_str)
                    seen_comp_rows.append(row_str)
        else:
            for item in shard_completed:
                if isinstance(item, dict):
                    item_key = _text(item.get("issue") or item.get("slot_id"))
                    matched_rich = next((rr for rr in rich_rows if item_key and item_key in rr), "")
                    if matched_rich and item.get("summary") in {"", "—"}:
                        row_str = matched_rich
                    else:
                        topic = _text(item.get("topic_id") or item.get("target_repo") or item.get("slot_id"), "—")
                        issue_cell = _format_linear_issue_link(item_key) if re.match(r"^TIA-\d+$", item_key, re.I) else (f"`{item_key}`" if item_key else "—")
                        pr_ref = _text(item.get("pr"))
                        if pr_ref:
                            issue_cell = f"{issue_cell} / {pr_ref}"
                        summary_cell = _text(item.get("summary"), "已完成").replace("|", "\\|")
                        row_str = f"| `{host}` | `{topic}` | {issue_cell} | {summary_cell} |"
                    sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map, hint_scope=line_scopes.get(row_str, ""))
                else:
                    raw_val = _text(item)
                    matched_rich = next((rr for rr in rich_rows if raw_val and raw_val in rr), "")
                    row_str = matched_rich or f"| `{host}` | `—` | {_format_linear_issue_link(raw_val)} | 已完成 |"
                    sc = _scope_for_host_issue(shard, raw_val, row_str)
                _append_unique(completed_by_scope, sc, row_str)

    canonical_state = {
        "schema": M2_SYNC_SCHEMA,
        "updated_at": _text(state.get("updated_at")) or _cst_now_iso(),
        "devices": devices,
    }
    state_json_str = json.dumps(canonical_state, ensure_ascii=False, indent=2)

    work_pending = pending_by_scope["work"] or ["- 暂无 Work 阻塞或待拍板事项"]
    work_in_flight = in_flight_by_scope["work"] or ["| `—` | `—` | `—` | — | 当前无 Work 在途或常驻 M2 | `—` |"]
    work_backlog = backlog_by_scope["work"] or ["- 暂无 Work 暂缓 Backlog"]
    work_completed = completed_by_scope["work"] or ["| `—` | `—` | — | 暂无 Work 完成记录 |"]

    non_work_pending = pending_by_scope["non-work"] or ["- 暂无 Non-Work 阻塞或待拍板事项"]
    non_work_in_flight = in_flight_by_scope["non-work"] or ["| `—` | `—` | `—` | — | 当前无 Non-Work 在途 M2 | `—` |"]
    non_work_backlog = backlog_by_scope["non-work"] or ["- 暂无 Non-Work 暂缓 Backlog"]
    non_work_completed = completed_by_scope["non-work"] or ["| `—` | `—` | — | 暂无 Non-Work 完成记录 |"]

    lines = [
        M2_SYNC_HUB_MARKER,
        "",
        "## 🌐 跨设备 `/personal-m2-assistant` 监督与推进总账本",
        "",
        "> **用途**：作为 `leedeMac-Mini`、`MacBook-Pro` 与移动端 Linear 之间的唯一跨设备 M2 监督同步中枢，在同一个 Issue 内拆分为 **Part 1 · Work（工作业务）** 与 **Part 2 · Non-Work（非工作 / 个人与基建）** 两部分。任一设备执行 `--apply` / `--watch` / `--status` / `--gc` 时按设备分片（`devices.<hostname>`）自动对账更新；直接在本单评论指令即可通过 `linear-bot-bridge` 跨设备触发推进。",
        "",
        "---",
        "",
        "## 💼 Part 1 · Work（工作业务 M2）",
        "",
        "### 1.1 🚨 Work 待拍板 / 跨设备待选型事项",
        "",
        *work_pending,
        "",
        "### 1.2 ⏳ Work 各设备在途与常驻监督（In-Flight & Retained）",
        "",
        "| 设备 (`Host`) | M2 话题 (`Topic`) | 会话 / Pane | 关联单据 | 当前推进阶段 (`M1 Stage`) | 最近心跳 (`CST`) |",
        "| -- | -- | -- | -- | -- | -- |",
        *work_in_flight,
        "",
        "### 1.3 🗂️ Work 暂缓 Backlog（Deferred · 不自动派发）",
        "",
        *work_backlog,
        "",
        "### 1.4 ✅ Work 最近已完成（Recent Completed）",
        "",
        "| 设备 (`Host`) | 话题 / 仓库 | 单据 / PR | 一句话结果 |",
        "| -- | -- | -- | -- |",
        *work_completed,
        "",
        "---",
        "",
        "## 🛠️ Part 2 · Non-Work（非工作 / 个人与基建 M2）",
        "",
        "### 2.1 🚨 Non-Work 待拍板 / 跨设备待选型事项",
        "",
        *non_work_pending,
        "",
        "### 2.2 ⏳ Non-Work 各设备在途与继续推进（In-Flight & Continuing）",
        "",
        "| 设备 (`Host`) | M2 话题 (`Topic`) | 会话 / Pane | 关联单据 | 当前推进阶段 (`M1 Stage`) | 最近心跳 (`CST`) |",
        "| -- | -- | -- | -- | -- | -- |",
        *non_work_in_flight,
        "",
        "### 2.3 🗂️ Non-Work 暂缓 Backlog（Deferred · 不自动派发）",
        "",
        *non_work_backlog,
        "",
        "### 2.4 ✅ Non-Work 最近已完成（Recent Completed）",
        "",
        "| 设备 (`Host`) | 话题 / 仓库 | 单据 / PR | 一句话结果 |",
        "| -- | -- | -- | -- |",
        *non_work_completed,
        "",
        "---",
        "",
        "<details>",
        "<summary>🤖 Machine-Readable Cross-Device State (M2-SYNC-STATE v1)</summary>",
        "",
        M2_SYNC_STATE_START,
        "",
        "```json",
        state_json_str,
        "```",
        "",
        M2_SYNC_STATE_END,
        "</details>",
    ]
    if gtt_suffix:
        lines.extend(["", gtt_suffix])
    return "\n".join(lines).rstrip() + "\n"

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
