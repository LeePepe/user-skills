from __future__ import annotations

from . import base as _base
from . import contracts as _contracts
globals().update({k: v for module in (_base, _contracts) for k, v in vars(module).items() if not k.startswith("__")})


def _parent_session_id(event: dict[str, Any]) -> str:
    value = _field(event, "parentSessionId", "parent_session_id", "parentConversationId", "parent_session")
    if value:
        return str(value)
    parent = _field(event, "parent")
    return str(parent.get("sessionId") or parent.get("session_id") or "") if isinstance(parent, dict) else ""


def _full_read_paths(events: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for event in _tool_events(events):
        tool = _tool_name(event)
        args = _arguments(event)
        path = str(args.get("path") or _field(event, "path") or "").strip()
        ranged = any(args.get(key) for key in ("view_range", "viewRange", "range", "startLine", "lineRange"))
        if path and tool in {"view", "read", "cat"} and not ranged:
            paths.add(str(Path(path)))
    return paths


def _inherited_steering_dispatch(events: list[dict[str, Any]]) -> list[str]:
    parent_model = next((str(_field(event, "selectedModel", "model", "usageModel") or "").lower()
                         for event in events if _event_type(event) == "session.start"), "")
    if not parent_model:
        parent_model = next((str(_field(event, "selectedModel", "model", "usageModel") or "").lower()
                             for event in events if _field(event, "selectedModel", "model", "usageModel")), "")
    findings: list[str] = []
    for event in events:
        text = " ".join((_text(_data(event)), _command(event))).lower()
        dispatch = re.search(r"task\s*\(\s*general-purpose|--role\s+writer|writer\s+dispatch", text)
        if not dispatch:
            continue
        explicit = bool(re.search(r"(?:--model(?:=|\s+)|selected[_ ]?model\s*[:=]|model\s*[:=])", text))
        model = str(_field(event, "selectedModel", "model", "usageModel") or "").lower()
        inherited = "group/steering" in model or (not model and "group/steering" in parent_model)
        if inherited and not explicit:
            findings.append(f"{_session_id(event, 'unknown')}: {dispatch.group(0)} inherited group/steering; explicit coding model required")
    return findings


def _parent_child_duplicate_reads(groups: dict[str, list[dict[str, Any]]]) -> list[tuple[str, str, set[str]]]:
    duplicates: list[tuple[str, str, set[str]]] = []
    for child_sid, child_seq in groups.items():
        parent_ids = {_parent_session_id(event) for event in child_seq if _parent_session_id(event)}
        for parent_sid in parent_ids:
            if parent_sid not in groups or parent_sid == child_sid:
                continue
            shared = _full_read_paths(groups[parent_sid]) & _full_read_paths(child_seq)
            if len(shared) >= 3:
                duplicates.append((parent_sid, child_sid, shared))
    return duplicates


def _event_path(event: dict[str, Any]) -> str:
    args = _arguments(event)
    return str(args.get("path") or args.get("file") or _field(event, "path", "file") or "")


def _skill_from_tool_event(event: dict[str, Any], *, include_non_scripts: bool = True) -> str | None:
    candidates = " ".join(
        value for value in (
            _command(event),
            _event_path(event),
            _text(_arguments(event)),
        ) if value
    )
    match = re.search(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/scripts/", candidates)
    if match and is_valid_skill_slug(match.group(1)):
        return match.group(1)
    if include_non_scripts:
        match = re.search(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/", candidates)
        if match and is_valid_skill_slug(match.group(1)):
            return match.group(1)
    return None


def _first_invoked_skill(seq: list[dict[str, Any]]) -> str | None:
    for event in seq:
        skill = _event_skill(event)
        if skill and is_valid_skill_slug(skill):
            return skill
    return None


def _script_target(command: str) -> str | None:
    match = re.search(r"([A-Za-z0-9_./-]*scripts/[A-Za-z0-9_.-]+\.py)", command)
    return match.group(1) if match else None


def _is_healthy_small_output(event: dict[str, Any]) -> bool:
    out = _result_content(event)
    code = _exit_code(event)
    return (
        len(out.encode("utf-8")) <= 20_000
        and "Output too large to read at once" not in out
        and code in (None, 0)
    )


def _i3_recovered_progressively(tools: list[dict[str, Any]], index: int, failing: dict[str, Any], reason: str) -> bool:
    next_events = tools[index + 1:index + 9]
    fail_cmd = _command(failing)
    fail_path = _event_path(failing)
    fail_skill = _skill_from_tool_event(failing, include_non_scripts=True)
    fail_script = _script_target(fail_cmd)
    if reason == "exit=2":
        disclosed_at: int | None = None
        for offset, event in enumerate(next_events):
            cmd = _command(event)
            path = _event_path(event)
            tool = _tool_name(event)
            ev_skill = _skill_from_tool_event(event, include_non_scripts=True)
            same_target = (
                (fail_script and fail_script in cmd)
                or (fail_skill and ev_skill == fail_skill)
                or (not fail_script and not fail_skill and bool(cmd))
            )
            drawer = tool in {"view", "read", "cat"} and ("references/" in path or path.endswith("SKILL.md")) and (not fail_skill or ev_skill == fail_skill)
            help_or_summary = same_target and any(flag in cmd for flag in ("--help", "-h", "--summary"))
            if drawer or help_or_summary:
                disclosed_at = offset
                if help_or_summary and _exit_code(event) == 0:
                    return True
                break
        if disclosed_at is None:
            return False
        for event in next_events[disclosed_at + 1:]:
            cmd = _command(event)
            ev_skill = _skill_from_tool_event(event, include_non_scripts=True)
            same_target = (
                (fail_script and fail_script in cmd)
                or (fail_skill and ev_skill == fail_skill)
                or (not fail_script and not fail_skill and bool(cmd))
            )
            if same_target and _exit_code(event) == 0:
                return True
        return False
    # reason == "tool output >20KB"
    if fail_path and not fail_cmd:
        for event in next_events:
            args = _arguments(event)
            ranged = any(args.get(key) for key in ("view_range", "viewRange", "range", "startLine", "lineRange"))
            if ranged and _event_path(event) == fail_path and _is_healthy_small_output(event):
                return True
        return False
    for event in next_events:
        cmd = _command(event)
        ev_skill = _skill_from_tool_event(event, include_non_scripts=True)
        same_target = (
            (fail_script and fail_script in cmd)
            or (fail_skill and ev_skill == fail_skill)
            or (not fail_script and not fail_skill and bool(cmd))
        )
        narrowed_cmd = any(flag in cmd for flag in ("--summary", "--limit", "--head", "head "))
        if same_target and narrowed_cmd and _exit_code(event) == 0 and _is_healthy_small_output(event):
            return True
    return False


def _metric_scan(events: list[dict[str, Any]], handoffs: list[dict[str, Any]], source: dict[str, Any],
                 profile_sessions: set[str] | None = None) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    boundary_metrics = {"R1_m3_prompt_prelock", "R2_m2_direct_code_edit", "R3_m1_inline_overwork",
                        "R5_reviewer_same_vendor", "H4_compaction_and_handoff_loss", "E4_unfaithful_receipt_and_cancelled_noise",
                        "F1_permission_stall_count", "F2_owner_abort_rate", "F3_mid_flight_owner_correction"}
    def allowed(metric: str, sid: str) -> bool:
        return profile_sessions is None or sid in profile_sessions or metric in boundary_metrics
    groups = _session_groups(events)
    inherited = [finding for seq in groups.values() for finding in _inherited_steering_dispatch(seq)]
    if inherited:
        observations.append(_observation("R3_m1_inline_overwork", "role", len(inherited), inherited, skill="team-lead", sessions=len(inherited)))
    for parent_sid, child_sid, shared in _parent_child_duplicate_reads(groups):
        observations.append(_observation(
            "HZN1_context_carry_tax", "horizon", len(shared),
            [f"{child_sid}: duplicate full reads from parent {parent_sid}: {path}" for path in sorted(shared)[:5]],
            skill="agent-retro", sessions=1,
        ))
    indexed_events = [(event, _event_blob(event), _goal_key(event)) for event in events]
    event_blobs = {id(event): blob for event, blob, _ in indexed_events}
    handoff_names = {record["name"].lower() for record in handoffs}
    scope_lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event, blob, goal in indexed_events:
        if goal:
            scope_lookup[f"goal:{goal.lower()}"].append(event)
        for name in handoff_names:
            if name in blob:
                scope_lookup[name].append(event)
    goal_models: dict[str, set[str]] = defaultdict(set)
    for grouped in groups.values():
        goal_ids = {_goal_key(event) for event in grouped if _goal_key(event)}
        grouped_role = _role_for(grouped)
        if grouped_role in {"writer", "m1"}:
            for event in grouped:
                model = str(_field(event, "selectedModel", "model", "usageModel") or "")
                if model:
                    for goal_id in goal_ids:
                        goal_models[goal_id].add(_vendor(model))
    # Session-level role and invocation signals.
    for sid, seq in groups.items():
        role = _role_for(seq)
        skills = _skill_names(seq)
        tools = _tool_events(seq)
        mutation_tools = {"edit", "create", "apply_patch", "write", "patch"}
        mutation_events = [e for e in tools if _tool_name(e) in mutation_tools]
        files = set()
        for event in mutation_events:
            args = _arguments(event)
            path = str(args.get("path") or args.get("file") or _field(event, "path") or "")
            if path and _is_code_path(path):
                files.add(path)
        assistant_turns = sum(1 for e in seq if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response"})
        text = " ".join(_value_text(_field(e, "content", "message", "assistantContent", "userContent")) for e in seq).lower()
        if any(token in text for token in ("prompt lock-in", "hot-append", "pre-baked", "prebaked")) and role in ("m3", "unknown"):
            observations.append(_observation("R1_m3_prompt_prelock", "role", 1, [f"{sid}: prompt/pre-lock wording"], sessions=1))
        if role == "m2":
            direct = [e for e in tools if _tool_name(e) in {"edit", "create", "apply_patch", "write"} and _is_code_path(str(_arguments(e).get("path") or _field(e, "path") or ""))]
            if direct:
                observations.append(_observation("R2_m2_direct_code_edit", "role", len(direct), [f"{sid}: {_tool_name(e)} {_arguments(e).get('path', '')}" for e in direct], sessions=1))
        subagent_events = [e for e in seq if _event_type(e) in {"subagent.started", "subagent.completed"}]
        writer_spawned = any(
            "writer" in _text(_data(e)).lower()
            or "group/coding" in str(_field(e, "model", "selectedModel") or "").lower()
            or ("herdr-dispatch.sh" in _command(e) and "--role writer" in _command(e))
            for e in subagent_events + seq
        )
        if role == "m1" and (len(files) > 3 or (assistant_turns > 60 and not writer_spawned)):
            evidence = [f"{sid}: {len(files)} mutating source files"] if len(files) > 3 else [f"{sid}: {assistant_turns} assistant turns without Writer"]
            observations.append(_observation("R3_m1_inline_overwork", "role", max(len(files), assistant_turns), evidence, skill="team-lead", sessions=1))
        if len(skills) > 3 and allowed("R4_session_skill_pollution", sid):
            observations.append(_observation("R4_session_skill_pollution", "role", len(skills), [f"{sid}: {', '.join(sorted(skills)[:6])}"], sessions=1))
        reviewer_models = [str(_field(e, "selectedModel", "model", "usageModel", "reviewerModel", "reviewer_model") or "") for e in seq]
        peer_models = [str(_field(e, "writerModel", "writer_model", "tlModel", "tl_model", "leadModel") or "") for e in seq]
        peer_models.extend(str(_data(e).get("selectedModel") or "") for e in seq if str(_data(e).get("role", "")).lower() in {"writer", "m1", "team-lead", "tl"})
        reviewer_vendor = next((_vendor(m) for m in reviewer_models if _vendor(m) != "unknown"), "unknown")
        peer_vendors = {_vendor(m) for m in peer_models if _vendor(m) != "unknown"}
        parent_models = [str(_field(e, "selectedModel", "model", "usageModel") or "") for e in seq
                         if _event_type(e) == "session.start" or _field(e, "selectedModel", "model", "usageModel")]
        parent_models.extend(peer_models)
        for goal_id in {_goal_key(event) for event in seq if _goal_key(event)}:
            peer_vendors.update(vendor for vendor in goal_models.get(goal_id, set()) if vendor != "unknown")
        if role == "reviewer" and reviewer_vendor != "unknown" and reviewer_vendor in peer_vendors:
            observations.append(_observation("R5_reviewer_same_vendor", "role", 1, [f"{sid}: reviewer and Writer/TL share {reviewer_vendor}"], skill="code-review", sessions=1))
        reviewer_subagents = [
            e for e in subagent_events
            if str(_field(e, "agentName") or "").lower() in {"code-review", "reviewer"}
            or "reviewer" in _text(_field(e, "agentDisplayName", "agentDescription")).lower()
            or "code-review" in _text(_field(e, "agentDisplayName", "agentDescription")).lower()
        ]
        for reviewer in reviewer_subagents:
            reviewer_model = str(_field(reviewer, "model", "selectedModel", "usageModel") or "")
            same_route = any(_same_model_route(reviewer_model, peer) for peer in parent_models if peer)
            if same_route:
                observations.append(_observation(
                    "R5_reviewer_same_vendor", "role", 1,
                    [f"{sid}: reviewer subagent shares model route/vendor"],
                    skill="code-review", sessions=1,
                ))
        # I1: first two turns/events read three drawers before a script invocation.
        first = seq[:20]
        drawer_reads = [e for e in first if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e))]
        script_at = next((i for i, e in enumerate(first) if "scripts/" in _command(e)), 999)
        if len(drawer_reads) >= 3 and all(first.index(e) < script_at for e in drawer_reads[:3]) and allowed("I1_cold_start_greedy_drawer_read", sid):
            drawer_skill = next((m.group(1) for e in drawer_reads if (m := re.search(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/", _text(_arguments(e)))) and is_valid_skill_slug(m.group(1))), None)
            observations.append(_observation("I1_cold_start_greedy_drawer_read", "invoke", len(drawer_reads), [f"{sid}: {len(drawer_reads)} reference reads before L2"], skill=drawer_skill, sessions=1))
        # I2: explicit skill invocation without any script call for its package.
        invoked = {s for e in seq if (s := _event_skill(e))}
        called_scripts = " ".join(_command(e) for e in tools)
        for skill in invoked:
            if _skill_requires_l2(skill) and f"skills/{skill}/scripts/" not in called_scripts and allowed("I2_mandatory_l2_cli_skip", sid):
                observations.append(_observation("I2_mandatory_l2_cli_skip", "invoke", 1, [f"{sid}: {skill} invoked without its script"], skill=skill, sessions=1))
        # I3, I4 and F signals.
        i3_hits: list[tuple[dict[str, Any], str, int]] = []
        for index, event in enumerate(tools):
            if _exit_code(event) == 2:
                i3_hits.append((event, "exit=2", index))
            if len(_result_content(event).encode("utf-8")) > 20_000 or "Output too large to read at once" in _result_content(event):
                i3_hits.append((event, "tool output >20KB", index))
        if i3_hits and allowed("I3_l2_exit2_and_output_bloat", sid):
            first_invoked = _first_invoked_skill(seq)
            actionable_hits: list[tuple[dict[str, Any], str]] = []
            recovered_hits: list[tuple[dict[str, Any], str]] = []
            for event, reason, index in i3_hits:
                if _i3_recovered_progressively(tools, index, event, reason):
                    recovered_hits.append((event, reason))
                else:
                    actionable_hits.append((event, reason))
            if actionable_hits:
                evidence = [f"{sid}: {reason}" for _, reason in actionable_hits[:4]]
                target_skill = None
                for event, _ in actionable_hits:
                    target_skill = _skill_from_tool_event(event)
                    if target_skill:
                        break
                observations.append(_observation(
                    "I3_l2_exit2_and_output_bloat", "invoke", len(actionable_hits), evidence,
                    skill=target_skill or first_invoked or "team-lead", sessions=1,
                    session_ids=[sid],
                ))
            if recovered_hits:
                evidence = [f"{sid}: {reason} -> narrowed/disclosed -> exit=0" for _, reason in recovered_hits[:3]]
                target_skill = None
                for event, _ in recovered_hits:
                    target_skill = _skill_from_tool_event(event)
                    if target_skill:
                        break
                observations.append(_observation(
                    "I3_l2_exit2_and_output_bloat", "invoke", len(recovered_hits), evidence,
                    skill=target_skill or first_invoked or "team-lead", sessions=1,
                    healthy=True, session_ids=[sid],
                ))
        polls = [e for e in tools if re.search(r"(?:herdr\s+pane\s+read|\bsleep\b|gh\s+pr\s+view)", _command(e))]
        if len(polls) >= 3 and allowed("I4_manual_poll_loop", sid):
            observations.append(_observation("I4_manual_poll_loop", "invoke", len(polls), [f"{sid}: {len(polls)} manual polls"], sessions=1))
        permissions = [e for e in seq if "permission.requested" in _event_type(e)]
        if permissions:
            observations.append(_observation("F1_permission_stall_count", "friction", len(permissions), [f"{sid}: permission requests"], sessions=1))
        if any(x in _event_type(e) for e in seq for x in ("abort", "session.error")):
            observations.append(_observation("F2_owner_abort_rate", "friction", 1, [f"{sid}: abort/session.error"], sessions=1))
        user_messages = [e for e in seq if _event_type(e) in {"user.message", "owner.message"}]
        if user_messages and len(seq) > 4:
            observations.append(_observation("F3_mid_flight_owner_correction", "friction", len(user_messages), [f"{sid}: owner message during execution"], sessions=1))
        # Execution and receipt grounding.
        failed = []
        progressive = 0
        spin = 0
        previous = None
        fail_run = None
        for index, event in enumerate(tools):
            code, command = _exit_code(event), _command(event)
            if code == 1:
                failed.append(event)
                fail_run = (index, command)
                if previous == command:
                    spin += 1
                else:
                    spin = 1
                previous = command
            elif code == 0 and fail_run:
                failed_index = fail_run[0]
                between = tools[failed_index + 1:index]
                read_drawer = any(_tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e)) for e in between)
                if read_drawer:
                    progressive += 1
                    spin = 0
                fail_run = None
                previous = None
            elif code not in (None, 1):
                previous = None
        if progressive:
            drawer_skill = next((m.group(1) for e in tools if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e)) and (m := re.search(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/", _text(_arguments(e)))) and is_valid_skill_slug(m.group(1))), None)
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", progressive, [f"{sid}: exit=1 -> drawer -> exit=0"], skill=drawer_skill, sessions=1, healthy=True))
        if spin >= 3 and allowed("E1_blind_spin_loop_vs_progressive_catch", sid):
            target_skill = next((m.group(1) for e in tools if (m := re.search(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/scripts/", _command(e))) and is_valid_skill_slug(m.group(1))), None)
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", spin, [f"{sid}: {spin} identical failed retries"], skill=target_skill, sessions=1))
        rounds = len(re.findall(r"(?:change request|changes requested|review fail|r[0-9]+\s+fail)", text))
        if rounds >= 3 and allowed("E2_r3_plus_divergence", sid):
            observations.append(_observation("E2_r3_plus_divergence", "exec", rounds, [f"{sid}: {rounds} review/reset signals"], skill=_first_fault_skill(text, role), sessions=1))
        ci_failure = any("PR_CHECK_FAILED" in _text(e) or ("ci" in _event_type(e) and "fail" in _text(e).lower()) for e in seq)
        if re.search(r"verif(?:y|ication)|pytest|swiftlint|scripts/verify", text) and ci_failure and allowed("E3_gate_escape_and_uncrystallized_spec_gap", sid):
            observations.append(_observation("E3_gate_escape_and_uncrystallized_spec_gap", "exec", 1, [f"{sid}: local verification followed by CI failure"], skill=_first_fault_skill(text, role, ci=True), sessions=1))
    # Cross-agent glue is actionable only when it recurs across sessions.
    glue: dict[str, set[str]] = defaultdict(set)
    for sid, seq in groups.items():
        for event in _tool_events(seq):
            command = _command(event)
            if re.search(r"herdr\s+agent\s+list|herdr\s+pane\s+read|python3\s+-c.*(?:receipt|ledger)|gh\s+pr\s+view", command, re.I):
                normalized = re.sub(r"\s+", " ", command).strip()
                glue[normalized].add(sid)
    repeated_glue = [(command, ids) for command, ids in glue.items() if len(ids) >= 5]
    if repeated_glue:
        total = sum(len(ids) for _, ids in repeated_glue)
        owner = "m2-dispatcher" if any("herdr" in command.lower() for command, _ in repeated_glue) else "team-lead"
        observations.append(_observation("I5_cross_agent_wild_glue", "invoke", total,
                                         [f"{len(ids)} sessions repeat manual glue: {command[:120]}" for command, ids in repeated_glue[:3]],
                                         skill=owner, sessions=len(set().union(*(ids for _, ids in repeated_glue)))))
    contract = _cross_skill_contract_check()
    if contract["findings"]:
        observations.append(_observation("H5_cross_skill_contract_drift", "handoff", len(contract["findings"]), contract["findings"], skill="agent-retro", sessions=1))
    # Cross-session / handoff signals.
    active = {sid for sid, seq in groups.items() if _role_for(seq) != "unknown"}
    for record in handoffs:
        ledger = record["ledger"].lower()
        if re.search(r"\b(?:in_progress|active|planning)\b", ledger) and not any(record["name"] in sid for sid in active):
            observations.append(_observation("H1_zombie_drifted_goals", "handoff", 1, [f"{record['name']}: active ledger without matching live session"], sessions=1))
        if "input_blocked" in ledger and re.search(r"initial|0.?diff|zero.?diff", ledger):
            observations.append(_observation("H2_initial_sha_false_lock", "handoff", 1, [f"{record['name']}: initial zero-diff INPUT_BLOCKED"], sessions=1))
        if re.search(r"\b(?:done|completed|pr_merged)\b", ledger) and not record["receipts"]:
            observations.append(_observation("H3_r10_receipt_gap_or_dirty_schema", "handoff", 1, [f"{record['name']}: completed ledger has no receipt"], sessions=1))
        spec_words = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", record["spec"].lower()))
        brief_text = " ".join(text for _, text in record["briefs"]).lower()
        record_name = record["name"].lower()
        related = [event for event, blob, goal in indexed_events if record_name in blob or goal == record["name"]]
        related_sample = related[:200]
        h4_evidence: list[str] = []
        if any("compaction" in _event_type(e) or "context.compacted" in _event_type(e)
               or "context compaction" in event_blobs.get(id(e), "") for e in related_sample):
            h4_evidence.append(f"{record['name']}: context compaction")
        rereads: Counter[str] = Counter()
        for event in related_sample:
            if _tool_name(event) in {"view", "read", "cat"}:
                args = _arguments(event)
                path = str(args.get("path") or _field(event, "path") or "")
                if path and not any(args.get(key) for key in ("view_range", "viewRange", "range", "startLine", "lineRange")):
                    rereads[path] += 1
        h4_evidence.extend(f"{record['name']}: full-file reread {path}" for path, count in rereads.items() if count >= 3)
        related_text = " ".join(event_blobs.get(id(e), "") for e in related_sample)
        promises = bool(re.search(r"delegate|spawn a writer|run (?:the )?verification|verify", related_text))
        mutated = any(_tool_name(e) in {"edit", "create", "apply_patch", "write", "patch"} for e in related)
        verified = any(_exit_code(e) == 0 and re.search(r"verify|pytest|unittest|swiftlint", _command(e), re.I) for e in related)
        if promises and (mutated or not verified):
            h4_evidence.append(f"{record['name']}: reasoning-action mismatch")
        if spec_words and len(spec_words - set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", brief_text))) > 8:
            h4_evidence.append(f"{record['name']}: spec constraints absent from brief")
        if h4_evidence:
            observations.append(_observation("H4_compaction_and_handoff_loss", "handoff", len(h4_evidence), h4_evidence, skill=_first_fault_skill(related_text or record["spec"], _role_for(related_sample)), sessions=1))
        for name, receipt in record["receipts"]:
            scoped_events = _scoped_events(record, events, indexed_events, scope_lookup)
            if re.search(r"(?:pytest|unittest|swiftlint|scripts/verify|PR_MERGED|merge)", receipt, re.I):
                verified_commands = {_command(e).strip() for e in scoped_events if _exit_code(e) == 0 and _command(e).strip()}
                claims: list[str] = []
                for line in receipt.splitlines():
                    match = re.search(r"(?:`|^\s*[-*]?\s*)(python3\s+[^`\n]+|pytest\s+[^`\n]+|unittest\s+[^`\n]+|swiftlint\s+[^`\n]+|(?:\./)?scripts/verify(?:\s+[^`\n]+)?)", line, re.I)
                    if match:
                        claim = re.sub(r"\s+", " ", match.group(1).strip())
                        claims.append(re.split(r"\s+(?:—|--|:)?\s+(?:PASS|OK|exit=0)\b", claim, maxsplit=1, flags=re.I)[0].strip())
                missing = [claim for claim in claims if claim not in verified_commands]
                merge_claimed = bool(re.search(r"\bPR_MERGED\b|merge commit", receipt, re.I))
                merge_grounded = any(("merged" in _event_type(e) or "pr_merged" in _text(e).lower() or ("git merge" in _command(e) and _exit_code(e) == 0)) for e in scoped_events)
                cancelled_false_failure = bool(re.search(r"CANCELLED", receipt, re.I) and re.search(r"PR_CHECK_FAILED", receipt, re.I))
                ledger_cancelled = bool(re.search(r"CANCELLED", record["ledger"], re.I) and re.search(r"PR_CHECK_FAILED", record["ledger"], re.I))
                if missing or (merge_claimed and not merge_grounded) or cancelled_false_failure or ledger_cancelled:
                    observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", len(missing) or 1, [f"{record['name']}/{name}: scoped receipt lacks exact evidence"], skill="team-lead", sessions=1))
    # CI cancellation is not a failure; remove false positive evidence from raw scan by only emitting when explicit failure.
    for e in events:
        blob = _text(e).upper()
        if "CANCELLED" in blob and "PR_CHECK_FAILED" in blob:
            observations.append(_observation("E4_unfaithful_receipt_and_cancelled_noise", "exec", 1, ["CANCELLED run was classified as PR_CHECK_FAILED"], sessions=1))
    return observations


_QUALITY_TIER = "soft_advisory_exit_0"
_QUALITY_REMEDIATION = (
    "Add Shared-Token Adversarial Twin (good_snippet sharing tokens with bad_snippet in valid AST/branch scope) "
    "in eval-cases.json during periodic retro+eval."
)
_QUALITY_MUTATIONS = {"edit", "create", "apply_patch", "write", "patch"}
_QUALITY_EDGE_WORDS = re.compile(
    r"regex|re\.compile|negation|boundary|false[- ]positive|switch|ternary|prefix", re.I
)
_QUALITY_SCRIPT = re.compile(
    r"(?:^|/)(?:[^/]*_guard|verify_[^/]+|classify_[^/]+|semiotics_gates)\.py$|"
    r"(?:^|/)skills/[^/]+/scripts/[^/]+\.py$", re.I
)


def _quality_added_text(event: dict[str, Any]) -> str:
    args = _arguments(event)
    values: list[Any] = []
    for key in ("new_str", "newStr", "file_text", "fileText", "patch", "diff", "content"):
        if key in args:
            values.append(args[key])
    return _text(values).lower()


def _quality_mutation_text(event: dict[str, Any]) -> str:
    args = _arguments(event)
    values: list[Any] = [args.get(key) for key in ("old_str", "oldStr") if key in args]
    return (_quality_added_text(event) + " " + _text(values) + " " + _text(args)).lower()


def _quality_target(event: dict[str, Any]) -> str:
    args = _arguments(event)
    return str(args.get("path") or args.get("file") or _field(event, "path", "file") or "").strip()


def _quality_goal_ids(seq: list[dict[str, Any]]) -> list[str]:
    values = {_goal_key(event) for event in seq if _goal_key(event)}
    return sorted(str(value) for value in values if value)


def _quality_skill(text: str, fallback: str = "team-lead") -> str:
    candidates = re.findall(r"skills/([A-Za-z0-9][A-Za-z0-9_-]*)/", text)
    candidates += re.findall(r"(?:agent|skill)-retro-([A-Za-z0-9][A-Za-z0-9_-]*)", text, re.I)
    candidates += re.findall(r"(?:^|[^A-Za-z])us-([A-Za-z0-9][A-Za-z0-9_-]*)-", text, re.I)
    for candidate in candidates:
        if is_valid_skill_slug(candidate, require_repo_dir=True):
            return candidate
    return fallback if is_valid_skill_slug(fallback, require_repo_dir=True) else "team-lead"


def _quality_negative_probe(event: dict[str, Any]) -> bool:
    if _tool_name(event) not in {"bash", "shell", "run_command", "terminal"}:
        return False
    command = _command(event).lower()
    return bool(
        _exit_code(event) == 1
        or re.search(r"--(?:bad-)?snippet|counter(?:example)?|negative|--bad(?:\s|$)", command)
    )


def _quality_item(kind: str, subkind: str, *, skill: str, count: int,
                  session_ids: Iterable[str], goal_ids: Iterable[str], evidence: Iterable[str]) -> dict[str, Any]:
    valid_skill = skill if is_valid_skill_slug(skill, require_repo_dir=True) else "team-lead"
    return {
        "kind": kind, "subkind": subkind, "tier": _QUALITY_TIER,
        "related_metric": "E2_r3_plus_divergence", "skill": valid_skill,
        "upstream_skill": valid_skill, "count": int(count),
        "session_ids": sorted({str(value) for value in session_ids if str(value)}),
        "goal_ids": sorted({str(value) for value in goal_ids if str(value)}),
        "evidence": [_redact(str(value)) for value in list(evidence)[:5]],
        "remediation": _QUALITY_REMEDIATION,
    }


def _session_quality_rows(events: list[dict[str, Any]], handoffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find session-quality smells without changing the existing 27 metrics."""
    groups = _session_groups(events)
    rows: list[dict[str, Any]] = []

    for sid, seq in groups.items():
        tools = _tool_events(seq)
        mutations = [event for event in tools if _tool_name(event) in _QUALITY_MUTATIONS]
        goals = _quality_goal_ids(seq)
        text = " ".join(_event_blob(event) for event in seq)
        skill_text = " ".join(_quality_target(event) + " " + _text(_data(event)) for event in seq)
        skill = _quality_skill(skill_text, _first_fault_skill(text, _role_for(seq)))

        regex_mutations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in mutations:
            target = _quality_target(event)
            if _QUALITY_SCRIPT.search(target) and re.search(r"re\.compile|regex", _quality_mutation_text(event), re.I):
                regex_mutations[target].append(event)
        regex_churn = False
        for target, changed in sorted(regex_mutations.items()):
            if len(changed) >= 3:
                regex_churn = True
                rows.append(_quality_item(
                    "session_false_positive_churn", "multi_round_regex_boundary_rework", skill=skill,
                    count=len(changed), session_ids=[sid], goal_ids=goals,
                    evidence=[f"{sid}: {len(changed)} regex mutations in {target}"],
                ))
        review_fail_count = len(re.findall(r"(?:EVT=)?REVIEW_FAIL|review(?:er)? fail|changes requested", text, re.I))
        if review_fail_count >= 2 and _QUALITY_EDGE_WORDS.search(text) and _QUALITY_SCRIPT.search(skill_text) and not regex_churn:
            rows.append(_quality_item(
                "session_false_positive_churn", "multi_round_regex_boundary_rework", skill=skill,
                count=review_fail_count, session_ids=[sid], goal_ids=goals,
                evidence=[f"{sid}: {review_fail_count} review-fail rounds cite regex/boundary false-positive edges"],
            ))

        waiver_events = [event for event in mutations if "design-contract-allow" in _quality_added_text(event)]
        if waiver_events:
            rows.append(_quality_item(
                "session_false_positive_churn", "in_session_waiver_bypass_churn", skill=skill,
                count=len(waiver_events), session_ids=[sid], goal_ids=goals,
                evidence=[f"{sid}: waiver bypass inserted in {_quality_target(waiver_events[0])}"],
            ))
        validator_failures = [event for event in tools if _exit_code(event) == 1 and re.search(
            r"skills/[^/]+/scripts/|(?:_guard|verify_|classify_|design_contract)\.py", _command(event) + " " + _quality_target(event), re.I
        )]
        relaxations = [event for event in mutations if re.search(
            r"skills/[^/]+/(?:scripts|references)/|(?:_guard|verify_|classify_|design_contract)\.py", _quality_target(event), re.I
        ) and re.search(r"re\.compile|regex", _quality_mutation_text(event), re.I)]
        failure_positions = [tools.index(event) for event in validator_failures]
        relaxation_positions = [tools.index(event) for event in relaxations]
        if len(failure_positions) >= 2 and relaxation_positions and max(relaxation_positions) > failure_positions[1]:
            rows.append(_quality_item(
                "session_false_positive_churn", "in_session_waiver_bypass_churn", skill=skill,
                count=len(validator_failures), session_ids=[sid], goal_ids=goals,
                evidence=[f"{sid}: {len(validator_failures)} validator failures followed by regex relaxation"],
            ))

        negative_probe = any(_quality_negative_probe(event) for event in tools)
        role = _role_for(seq)
        writer = role == "writer" or "writer" in text
        for event in mutations:
            target = _quality_target(event)
            added = _quality_added_text(event)
            test_target = bool(re.search(r"(?:^|/)scripts/test_[^/]+\.py$|(?:^|/)test_[^/]+\.py$", target, re.I))
            prose_echo = "assertin(" in added and ".read_text(" in added and re.search(r"\.md|skill\.md|references/", added)
            vacuous = bool(re.search(r"text\(\s*['\"]clean['\"]\s*\)|text\(\s*['\"]clean['\"]", added, re.I))
            if writer and test_target and (prose_echo or vacuous) and not negative_probe:
                rows.append(_quality_item(
                    "session_fake_or_unprobed_test", "prose_echo_or_vacuous_test_mutation", skill=skill,
                    count=1, session_ids=[sid], goal_ids=goals,
                    evidence=[f"{sid}: test mutation lacks a negative/counterexample CLI probe"],
                ))
                break

        fake_command = [event for event in tools if re.search(r"echo\s+[^|;&\n]*\b(?:pytest|unittest)\b", _command(event), re.I)]
        zero_pass = [event for event in tools if re.search(
            r"[\"']?ok[\"']?\s*[:=]\s*(?:true|True)", _result_content(event)
        ) and re.search(r"(?:files|node_count)[\"']?\s*[:=]\s*0", _result_content(event))]
        if fake_command or zero_pass:
            evidence = [f"{sid}: spoofed {'echo command' if fake_command else 'zero-file pass'}"]
            rows.append(_quality_item(
                "session_fake_or_unprobed_test", "fake_command_or_zero_file_pass", skill=skill,
                count=len(fake_command) + len(zero_pass), session_ids=[sid], goal_ids=goals, evidence=evidence,
            ))

        reviewer_pass = role == "reviewer" or bool(re.search(r"EVT=REVIEW_PASS|V5\s+Verdict:\s*PASS|review(?:er)? pass", text, re.I))
        code_change = any(
            _is_code_path(candidate) and (_QUALITY_SCRIPT.search(candidate) or re.search(r"\.(?:py|sh|ts|swift)\b|gates?|scripts/", candidate, re.I))
            for event in tools
            for candidate in (_quality_target(event), _command(event))
            if candidate
        )
        if reviewer_pass and code_change and not negative_probe:
            rows.append(_quality_item(
                "session_fake_or_unprobed_test", "reviewer_pass_without_adversarial_probe", skill=skill,
                count=1, session_ids=[sid], goal_ids=goals,
                evidence=[f"{sid}: reviewer pass without --snippet or counterexample probe"],
            ))

    for record in handoffs:
        source = " ".join((record.get("ledger", ""), record.get("spec", ""), *(text for _, text in record.get("receipts", []))))
        if len(re.findall(r"(?:EVT=)?REVIEW_FAIL|review(?:er)? fail|changes requested", source, re.I)) >= 2 and _QUALITY_EDGE_WORDS.search(source) and _QUALITY_SCRIPT.search(source):
            related = [sid for sid, seq in groups.items() if record["name"].lower() in _event_blob(seq[0]) or any(record["name"].lower() in _event_blob(event) for event in seq)]
            skill = _quality_skill(source + " " + record["name"], _first_fault_skill(source, "m1"))
            rows.append(_quality_item(
                "session_false_positive_churn", "multi_round_regex_boundary_rework", skill=skill,
                count=2, session_ids=related, goal_ids=[record["name"]],
                evidence=[f"{record['name']}: review-fail rounds cite regex/boundary false-positive edges"],
            ))
        if re.search(r"EVT=REVIEW_PASS|V5\s+Verdict:\s*PASS", source, re.I) and re.search(r"(?:\.py|\.sh|\.ts|\.swift|gates?|scripts/)", source, re.I):
            related = [sid for sid, seq in groups.items() if record["name"].lower() in " ".join(_event_blob(event) for event in seq)]
            if not any(_quality_negative_probe(event) for sid in related for event in _tool_events(groups[sid])):
                skill = _quality_skill(source + " " + record["name"], "team-lead")
                rows.append(_quality_item(
                    "session_fake_or_unprobed_test", "reviewer_pass_without_adversarial_probe", skill=skill,
                    count=1, session_ids=related, goal_ids=[record["name"]],
                    evidence=[f"{record['name']}: reviewer receipt lacks an adversarial probe"],
                ))
    return rows
