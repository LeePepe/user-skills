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
            drawer_skill = next((re.search(r"skills/([^/]+)/", _text(_arguments(e))).group(1) for e in drawer_reads if re.search(r"skills/([^/]+)/", _text(_arguments(e)))), None)
            observations.append(_observation("I1_cold_start_greedy_drawer_read", "invoke", len(drawer_reads), [f"{sid}: {len(drawer_reads)} reference reads before L2"], skill=drawer_skill, sessions=1))
        # I2: explicit skill invocation without any script call for its package.
        invoked = {s for e in seq if (s := _event_skill(e))}
        called_scripts = " ".join(_command(e) for e in tools)
        for skill in invoked:
            if _skill_requires_l2(skill) and f"skills/{skill}/scripts/" not in called_scripts and allowed("I2_mandatory_l2_cli_skip", sid):
                observations.append(_observation("I2_mandatory_l2_cli_skip", "invoke", 1, [f"{sid}: {skill} invoked without its script"], skill=skill, sessions=1))
        # I3, I4 and F signals.
        exit2 = [e for e in tools if _exit_code(e) == 2]
        bloat = [e for e in tools if len(_result_content(e).encode("utf-8")) > 20_000
                 or "Output too large to read at once" in _result_content(e)]
        if (exit2 or bloat) and allowed("I3_l2_exit2_and_output_bloat", sid):
            evidence = [f"{sid}: exit=2" for _ in exit2[:2]] + [f"{sid}: tool output >20KB" for _ in bloat[:2]]
            target_skill = next((re.search(r"skills/([^/]+)/scripts/", _command(e)).group(1) for e in exit2 + bloat if re.search(r"skills/([^/]+)/scripts/", _command(e))), None)
            observations.append(_observation("I3_l2_exit2_and_output_bloat", "invoke", len(exit2) + len(bloat), evidence, skill=target_skill, sessions=1))
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
            drawer_skill = next((re.search(r"skills/([^/]+)/", _text(_arguments(e))).group(1) for e in tools if _tool_name(e) in {"view", "read", "cat"} and "references/" in _text(_arguments(e))), None)
            observations.append(_observation("E1_blind_spin_loop_vs_progressive_catch", "exec", progressive, [f"{sid}: exit=1 -> drawer -> exit=0"], skill=drawer_skill, sessions=1, healthy=True))
        if spin >= 3 and allowed("E1_blind_spin_loop_vs_progressive_catch", sid):
            target_skill = next((re.search(r"skills/([^/]+)/scripts/", _command(e)).group(1) for e in tools if re.search(r"skills/([^/]+)/scripts/", _command(e))), None)
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
