from .common import *

# ---------------------------------------------------------------------------
# Historical Data Loaders (Warehouse + Usage JSONL + Copilot Session State)
# ---------------------------------------------------------------------------


def _load_warehouse_maps(warehouse_db: Path) -> dict[str, Any]:
    maps: dict[str, Any] = {
        "goals": {},
        "goal_events": {},
        "goal_runs": {},
        "turns_by_session": {},
        "refs_by_session": {},
        "scripts_by_session": {},
        "dim_sessions": {},
    }
    if not warehouse_db.is_file():
        return maps
    try:
        conn = sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return maps

    with conn:
        for table_query, handler in (
            ("SELECT * FROM dim_goal", lambda r: maps["goals"].setdefault(r["goal_id"], dict(r))),
            (
                "SELECT * FROM fact_gtt_event ORDER BY line_no ASC",
                lambda r: maps["goal_events"].setdefault(r["goal_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM fact_gtt_task_run ORDER BY round_no ASC",
                lambda r: maps["goal_runs"].setdefault(r["goal_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM fact_turn ORDER BY ts ASC",
                lambda r: maps["turns_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM reference_hits ORDER BY ts ASC",
                lambda r: maps["refs_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM script_call ORDER BY ts ASC",
                lambda r: maps["scripts_by_session"].setdefault(r["session_id"], []).append(dict(r)),
            ),
            (
                "SELECT * FROM dim_session",
                lambda r: maps["dim_sessions"].setdefault(r["session_id"], dict(r)),
            ),
        ):
            try:
                for row in conn.execute(table_query):
                    handler(row)
            except sqlite3.Error:
                continue
    conn.close()
    return maps


def _load_usage_jsonl(usage_jsonl: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    by_session: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if not usage_jsonl.is_file():
        return by_session
    try:
        with usage_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = str(rec.get("session_id") or "").strip()
                kind = str(rec.get("kind") or "").strip()
                if not sid or not kind:
                    continue
                bucket = by_session.setdefault(sid, {"skill_turn": [], "reference_hit": [], "script_call": []})
                if kind in bucket:
                    bucket[kind].append(rec)
    except OSError:
        pass
    return by_session


def _extract_process_exit_code(data: dict[str, Any], result_text: str) -> int | None:
    shell_exec = data.get("shellExecution")
    if isinstance(shell_exec, dict) and shell_exec.get("exitCode") is not None:
        try:
            return int(shell_exec["exitCode"])
        except (TypeError, ValueError):
            pass
    m_done = _SHELL_COMPLETE_RE.search(result_text)
    if m_done:
        try:
            return int(m_done.group(2))
        except ValueError:
            pass
    if _SHELL_START_RE.search(result_text) or "is still running" in result_text:
        return None
    return None


def parse_copilot_session_activity(
    events_jsonl: Path,
    wh_maps: dict[str, Any],
    usage_by_session: dict[str, dict[str, list[dict[str, Any]]]],
    skills_root: Path | None = None,
) -> ParsedSessionActivity | None:
    session_id = events_jsonl.parent.name
    selected_model = "unknown"
    cwd = ""
    first_prompt = ""
    start_ns: int | None = None
    end_ns: int | None = None
    referenced_goals: set[str] = set()

    skill_invocations: list[tuple[str, int, str]] = []
    ref_reads: list[tuple[str, int | None, int, str]] = []
    # Pending bash scripts that moved to background/async keyed by shellId
    pending_async_shells: dict[str, tuple[str, str, int, str]] = {}  # shell_id -> (script_path, cmd_args, start_ns, tc_id)
    script_execs: list[tuple[str, str, int, int, int | None, str]] = []
    llm_calls: list[tuple[str, int, int, int, int, int, str]] = []
    tool_starts: dict[str, tuple[str, dict[str, Any], int]] = {}
    timestamped_tool_starts: set[str] = set()
    measured_durations_ms: dict[str, int] = {}
    tool_transport_success: dict[str, bool] = {}

    fallback_ns = int(events_jsonl.stat().st_mtime * 1_000_000_000)
    try:
        with events_jsonl.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = str(ev.get("type") or "")
                data = ev.get("data") or {}
                ev_id = str(ev.get("id") or f"line-{line_no}")
                ev_ts_ns = parse_iso_to_ns(ev.get("timestamp") or data.get("startTime"), fallback_ns)
                if start_ns is None or ev_ts_ns < start_ns:
                    start_ns = ev_ts_ns
                if end_ns is None or ev_ts_ns > end_ns:
                    end_ns = ev_ts_ns

                if ev_type == "session.start":
                    selected_model = str(data.get("selectedModel") or selected_model)
                    ctx = data.get("context") or {}
                    cwd = str(ctx.get("cwd") or cwd)
                    for m_goal in _HANDOFF_GOAL_REF_RE.finditer(cwd + "/"):
                        if not m_goal.group(1).startswith("_"):
                            referenced_goals.add(m_goal.group(1))
                elif ev_type == "user.message":
                    content = str(data.get("content") or "").strip()
                    if content and data.get("source") != "memory":
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(content):
                            if not m_goal.group(1).startswith("_"):
                                referenced_goals.add(m_goal.group(1))
                        if not first_prompt:
                            first_prompt = content[:500]
                        context_match = _SKILL_CONTEXT_RE.fullmatch(content)
                        if context_match:
                            skill_invocations.append(
                                (context_match.group(1), ev_ts_ns, f"copilot:skill-context:{ev_id}")
                            )
                elif ev_type == "skill.invoked":
                    sk_name = str(data.get("name") or "").strip()
                    sk_path = str(data.get("path") or "").strip()
                    sk_content = str(data.get("content") or "")
                    if sk_name:
                        skill_invocations.append((sk_name, ev_ts_ns, f"copilot:skill.invoked:{ev_id}"))
                    if sk_path:
                        content_bytes = len(sk_content.encode("utf-8")) if sk_content else None
                        if content_bytes is None:
                            content_bytes, _ = _file_stats(sk_path, skills_root=skills_root)
                        ref_reads.append((sk_path, content_bytes, ev_ts_ns, f"copilot:skill.invoked:{ev_id}"))
                elif ev_type == "tool.execution_start":
                    tc_id = str(data.get("toolCallId") or "")
                    t_name = str(data.get("toolName") or "")
                    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
                    if tc_id:
                        tool_starts[tc_id] = (t_name, args, ev_ts_ns)
                        if ev.get("timestamp"):
                            timestamped_tool_starts.add(tc_id)
                    if t_name == "skill" and args.get("skill"):
                        skill_invocations.append((str(args["skill"]), ev_ts_ns, f"copilot:tool:{tc_id or ev_id}"))
                    elif t_name == "bash" and args.get("command"):
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(str(args["command"])):
                            if not m_goal.group(1).startswith("_"):
                                referenced_goals.add(m_goal.group(1))
                    elif t_name == "view" and args.get("path"):
                        vp = str(args["path"])
                        for m_goal in _HANDOFF_GOAL_REF_RE.finditer(vp):
                            if not m_goal.group(1).startswith("_"):
                                referenced_goals.add(m_goal.group(1))
                        if (vp.startswith("skills/") or "/skills/" in vp) and vp.endswith((".md", ".json", ".yaml", ".yml")):
                            b_len, _ = _file_stats(vp, skills_root=skills_root)
                            ref_reads.append((vp, b_len, ev_ts_ns, f"copilot:view:{tc_id or ev_id}"))
                elif ev_type == "tool.execution_complete":
                    tc_id = str(data.get("toolCallId") or "")
                    res_obj = data.get("result")
                    res_text = str(res_obj.get("content") or "") if isinstance(res_obj, dict) else ""
                    if tc_id in tool_starts:
                        t_name, args, st_ns = tool_starts[tc_id]
                        dur_ms = max(1, int((ev_ts_ns - st_ns) / 1_000_000))
                        has_completion_timestamp = bool(ev.get("timestamp")) and tc_id in timestamped_tool_starts
                        if t_name in ("view", "skill") and has_completion_timestamp:
                            source_id = f"copilot:{'view' if t_name == 'view' else 'tool'}:{tc_id}"
                            measured_durations_ms[source_id] = dur_ms
                        if t_name == "bash":
                            cmd_str = str(args.get("command") or "")
                            m_sc = _SCRIPT_RE.search(cmd_str)
                            if m_sc:
                                sp_path = m_sc.group(1)
                                ec = _extract_process_exit_code(data, res_text)
                                m_bg = _SHELL_START_RE.search(res_text)
                                if ec is None and m_bg:
                                    pending_async_shells[m_bg.group(1)] = (sp_path, cmd_str[:260], st_ns, tc_id)
                                else:
                                    source_id = f"copilot:bash:{tc_id}"
                                    script_execs.append((sp_path, cmd_str[:260], st_ns, dur_ms, ec, source_id))
                                    if has_completion_timestamp:
                                        measured_durations_ms[source_id] = dur_ms
                                    if isinstance(data.get("success"), bool):
                                        tool_transport_success[source_id] = data["success"]
                        elif t_name in ("read_bash", "stop_bash"):
                            sh_id = str(args.get("shellId") or "")
                            m_done = _SHELL_COMPLETE_RE.search(res_text)
                            m_stop = _SHELL_STOPPED_RE.search(res_text)
                            target_sh = m_done.group(1) if m_done else (m_stop.group(1) if m_stop else sh_id)
                            if target_sh and target_sh in pending_async_shells:
                                if m_done:
                                    sp_path, cmd_args, bg_start_ns, orig_tc = pending_async_shells.pop(target_sh)
                                    bg_dur = max(1, int((ev_ts_ns - bg_start_ns) / 1_000_000))
                                    if ev.get("timestamp") and orig_tc in timestamped_tool_starts:
                                        measured_durations_ms[f"copilot:async_bash:{orig_tc}"] = bg_dur
                                    script_execs.append(
                                        (
                                            sp_path,
                                            cmd_args,
                                            bg_start_ns,
                                            bg_dur,
                                            int(m_done.group(2)),
                                            f"copilot:async_bash:{orig_tc}",
                                        )
                                    )
                                elif m_stop:
                                    sp_path, cmd_args, bg_start_ns, orig_tc = pending_async_shells.pop(target_sh)
                                    bg_dur = max(1, int((ev_ts_ns - bg_start_ns) / 1_000_000))
                                    if ev.get("timestamp") and orig_tc in timestamped_tool_starts:
                                        measured_durations_ms[f"copilot:stopped_bash:{orig_tc}"] = bg_dur
                                    script_execs.append(
                                        (
                                            sp_path,
                                            cmd_args,
                                            bg_start_ns,
                                            bg_dur,
                                            None,
                                            f"copilot:stopped_bash:{orig_tc}",
                                        )
                                    )
                elif ev_type == "session.usage_record":
                    usage = data.get("usage") or {}
                    u_model = str(usage.get("model") or selected_model)
                    u_in = int(usage.get("inputTokens") or 0)
                    u_out = int(usage.get("outputTokens") or 0)
                    u_cache = int(usage.get("cacheReadTokens") or 0)
                    u_dur = max(1, int(usage.get("duration") or 1))
                    if u_in > 0 or u_out > 0 or u_cache > 0:
                        call_start = max(start_ns or ev_ts_ns, ev_ts_ns - u_dur * 1_000_000)
                        source_id = f"copilot:usage:{ev_id}"
                        llm_calls.append((u_model, call_start, u_dur, u_in, u_out, u_cache, source_id))
                        if usage.get("duration") is not None:
                            measured_durations_ms[source_id] = u_dur
    except OSError:
        return None

    # Any async bash command that never completed stays exit_code=None (unknown)
    for sh_id, (sp_path, cmd_args, bg_start_ns, orig_tc) in pending_async_shells.items():
        bg_dur = max(1, int(((end_ns or fallback_ns) - bg_start_ns) / 1_000_000))
        script_execs.append((sp_path, cmd_args, bg_start_ns, bg_dur, None, f"copilot:pending_bash:{orig_tc}"))

    # Enrich with usage.jsonl and warehouse.db records for this exact session_id
    u_bucket = usage_by_session.get(session_id, {})
    had_copilot_llm_calls = bool(llm_calls)
    for idx, rec in enumerate(u_bucket.get("skill_turn", [])):
        sk = str(rec.get("skill") or "").strip()
        ts_ns = parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns)
        src_id = str(rec.get("event_id") or f"usage:skill_turn:{idx}")
        if sk:
            skill_invocations.append((sk, ts_ns, src_id))
        if not had_copilot_llm_calls:
            in_t = int(rec.get("input_tokens") or 0)
            out_t = int(rec.get("output_tokens") or 0)
            ca_t = int(rec.get("cache_read") or 0)
            if in_t > 0 or out_t > 0 or ca_t > 0:
                llm_calls.append((str(rec.get("model") or selected_model), ts_ns, 1, in_t, out_t, ca_t, src_id))

    for idx, rec in enumerate(u_bucket.get("reference_hit", []) + wh_maps["refs_by_session"].get(session_id, [])):
        rp = str(rec.get("reference_path") or "").strip()
        if rp:
            raw_b = rec.get("byte_len")
            b_len = int(raw_b) if raw_b is not None and int(raw_b) > 0 else None
            if b_len is None:
                b_len, _ = _file_stats(rp, skills_root=skills_root)
            src_id = str(rec.get("hit_uid") or rec.get("event_id") or f"ref:{idx}")
            ref_reads.append((rp, b_len, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns), src_id))

    for idx, rec in enumerate(u_bucket.get("script_call", []) + wh_maps["scripts_by_session"].get(session_id, [])):
        sp_p = str(rec.get("script_path") or "").strip()
        if sp_p:
            dur_ms = max(1, int(float(rec.get("duration_ms") or 1)))
            raw_ec = rec.get("exit_code")
            ec = int(raw_ec) if raw_ec is not None else None
            src_id = str(rec.get("call_uid") or rec.get("event_id") or f"script:{idx}")
            if rec.get("duration_ms") is not None:
                measured_durations_ms[src_id] = dur_ms
            script_execs.append(
                (sp_p, sp_p, parse_iso_to_ns(rec.get("ts"), start_ns or fallback_ns), dur_ms, ec, src_id)
            )

    if not llm_calls:
        for idx, turn in enumerate(wh_maps["turns_by_session"].get(session_id, [])):
            in_t = int(turn.get("input_tokens") or 0)
            out_t = int(turn.get("output_tokens") or 0)
            ca_t = int(turn.get("cache_read") or 0)
            if in_t > 0 or out_t > 0 or ca_t > 0:
                src_id = str(turn.get("turn_uuid") or f"wh_turn:{idx}")
                llm_calls.append(
                    (
                        str(turn.get("model") or selected_model),
                        parse_iso_to_ns(turn.get("ts"), start_ns or fallback_ns),
                        1,
                        in_t,
                        out_t,
                        ca_t,
                        src_id,
                    )
                )

    if not (first_prompt or llm_calls or skill_invocations or ref_reads or script_execs):
        return None

    s_ns = start_ns or fallback_ns
    e_ns = end_ns or fallback_ns
    s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000)

    return ParsedSessionActivity(
        session_id=session_id,
        source_path=events_jsonl.name,
        selected_model=selected_model,
        cwd=cwd,
        first_prompt=first_prompt,
        start_ns=s_ns,
        end_ns=e_ns,
        referenced_goals=referenced_goals,
        skill_invocations=skill_invocations,
        ref_reads=ref_reads,
        script_execs=script_execs,
        llm_calls=llm_calls,
        measured_durations_ms=measured_durations_ms,
        tool_transport_success=tool_transport_success,
    )


def _parse_ledger_markdown(ledger_path: Path) -> dict[str, Any]:
    text = ledger_path.read_text(encoding="utf-8", errors="replace")
    goal_id = ledger_path.parent.name
    mandate_line = ""
    owner_line = ""
    linear_id = ""
    repo_name = ""
    events: list[dict[str, str]] = []
    tasks: list[dict[str, str]] = []

    m_lin = _LINEAR_ISSUE_RE.search(text)
    if m_lin:
        linear_id = m_lin.group(1)
    m_repo = _REPO_RE.search(text)
    if m_repo:
        repo_name = m_repo.group(1) or m_repo.group(0)

    in_tasks = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 目标:") or stripped.startswith("- Mandate:"):
            mandate_line = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- Owner:"):
            owner_line = stripped
        elif stripped.startswith("## Tasks"):
            in_tasks = True
            continue
        elif stripped.startswith("## "):
            in_tasks = False
        elif in_tasks and stripped.startswith("|") and not stripped.startswith("|---") and "ID" not in stripped[:12]:
            cols = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cols) >= 5:
                tasks.append(
                    {
                        "task_id": cols[0],
                        "title": cols[2],
                        "assignee": cols[3],
                        "status": cols[4],
                        "pr": cols[5] if len(cols) > 5 else "",
                    }
                )
        m_evt = _EVT_LINE_RE.match(stripped)
        if m_evt:
            ts_str, rest = m_evt.group(1), m_evt.group(2)
            fields: dict[str, str] = {"ts": ts_str}
            for chunk in rest.split("|"):
                chunk = chunk.strip()
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    fields[k.strip()] = v.strip()
            events.append(fields)

    return {
        "goal_id": goal_id,
        "mandate": mandate_line or f"Goal {goal_id}",
        "owner_line": owner_line,
        "linear_id": linear_id,
        "repo": repo_name or "unknown",
        "tasks": tasks,
        "events": events,
        "raw_text": text,
    }
