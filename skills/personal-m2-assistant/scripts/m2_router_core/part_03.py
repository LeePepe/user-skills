def _matching_topic_sessions(
    target: dict[str, Any],
    topic: dict[str, Any] | None,
    sessions: list[dict[str, Any]],
    domain_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    repo = _clean_target_name(target.get("target_repo"))
    mapped_space = _text((topic or {}).get("space") or target.get("space") or target.get("target_repo"))
    aliases = [_text(value) for value in ((topic or {}).get("space_aliases") or target.get("space_aliases") or []) if _text(value)]
    allowed_spaces = {value.casefold() for value in [mapped_space, *aliases]}
    if mapped_space.casefold() != "大总管":
        allowed_spaces.discard("大总管")
    target_workspace_id = _text(target.get("target_workspace_id"))
    effective_topic_id = _text((topic or {}).get("topic_id") or target.get("topic_id")).casefold()
    explicit_unmapped = not topic and effective_topic_id in {"", NEW_UNMAPPED_DOMAIN.lower()}
    session_id = _text(target.get("session_id") or target.get("sessionId"))
    candidates = []
    for session in sessions:
        repo_match = bool(repo) and _clean_target_name(session.get("target_repo")).casefold() == repo.casefold()
        label = _text(session.get("workspace_label")).casefold()
        ws_id = _text(session.get("workspace_id"))
        space_match = bool(label and label in allowed_spaces)
        workspace_match = bool(target_workspace_id and ws_id == target_workspace_id)
        if label and label not in allowed_spaces:
            continue
        if target_workspace_id and ws_id and ws_id != target_workspace_id and not space_match:
            continue
        if repo_match or space_match or workspace_match or (explicit_unmapped and not repo and session_id == session["session_id"]):
            candidates.append(session)
    if effective_topic_id in {"", NEW_UNMAPPED_DOMAIN.lower()}:
        return candidates
    compatible = [session for session in candidates if not _text(session.get("topic_id")) or _text(session.get("topic_id")).casefold() == effective_topic_id]
    exact = [session for session in compatible if _text(session.get("topic_id")).casefold() == effective_topic_id]
    if exact:
        return exact
    if not topic:
        return compatible
    active_map = domain_map or load_domain_map()
    repo_topics = [
        item for item in active_map.get("topics", [])
        if repo and _clean_target_name(item.get("target_repo")).casefold() == repo.casefold()
    ]
    repo_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", repo.casefold()))
    topic_values = [topic.get("topic_id"), topic.get("title"), *(topic.get("keywords") or []), *(topic.get("aliases") or [])]
    topic_terms = [
        term
        for term in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", " ".join(_text(value) for value in topic_values).casefold())
        if len(term) > 1 and term not in repo_tokens and term not in {"app", "ios", "macos", "web", "m2"}
    ]
    scored: list[tuple[int, dict[str, Any]]] = []
    for session in compatible:
        haystack = " ".join(_text(session.get(key)) for key in ("title", "summary")).casefold()
        score = sum(1 for term in set(topic_terms) if term in haystack)
        if score > 0:
            scored.append((score, session))
    if scored:
        best = max(score for score, _ in scored)
        return [session for score, session in scored if score == best]
    return compatible if len(compatible) == 1 and len(repo_topics) <= 1 else []

def _target_session(
    target: dict[str, Any],
    topic: dict[str, Any] | None,
    sessions: list[dict[str, Any]],
    domain_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    session_id = _text(target.get("session_id") or target.get("sessionId"))
    candidates = _matching_topic_sessions(target, topic, sessions, domain_map)
    if session_id:
        match = next((session for session in candidates if session["session_id"] == session_id), None)
        if match:
            return match
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        active = [session for session in candidates if _text(session.get("status"), "idle").casefold() in {"working", "running"}]
        if active:
            return active[0]
        quiescent = [session for session in candidates if _text(session.get("status"), "idle").casefold() in {"idle", "done"}]
        return quiescent[0] if quiescent else candidates[0]
    return None

def _jev_decision(message: str, sessions: list[dict[str, Any]], domain_map: dict[str, Any], *, mock: Any = None, goal_id: str = "") -> dict[str, Any] | None:
    if mock is not None and not (isinstance(mock, dict) and ("targets" in mock or "m2_targets" in mock)):
        return mock if isinstance(mock, dict) else None
    try:
        jev_path = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
        spec = importlib.util.spec_from_file_location("m2_router_jev_client", jev_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        topic_ids = [topic["topic_id"] for topic in domain_map.get("topics", [])] + [NEW_UNMAPPED_DOMAIN]
        state = {"user_message": message, "active_m2_sessions": sessions, "domain_map": domain_map.get("topics", []), "routing_contract": {"target_fields": ["topic_id", "domain_status", "proposed_domain", "target_repo", "sub_prompt", "wave_order", "rag_context_hint"]}}
        questions = {
            "route_mode": {"type": "choice", "instructions": "Choose the route mode.", "criteria": {route: route for route in sorted(ROUTES)}},
            "primary_topic": {"type": "choice", "instructions": "Choose the primary business topic, or NEW_UNMAPPED_DOMAIN.", "criteria": {topic_id: topic_id for topic_id in topic_ids}},
            "has_unmapped_domain": {"type": "noul", "instructions": "Is any requested business topic missing from the domain map?"},
        }
        result = module.jev_decide(state, questions, agent_role="personal-m2-assistant", goal_id=goal_id, mock_response=None if mock is None else mock)
        return result if isinstance(result, dict) else None
    except (OSError, ImportError, TypeError, AssertionError):
        return None

def _target_value(decision: dict[str, Any]) -> Any:
    answers = decision.get("answers") if isinstance(decision.get("answers"), dict) else decision
    for key in ("targets", "m2_targets", "dispatch_plan", "target_m2s", "routes"):
        if key in answers:
            value = answers[key]
            if isinstance(value, dict) and "noul" in value:
                value = value["noul"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    return []
            return value if isinstance(value, list) else []
    return []

def _decision_answers(decision: dict[str, Any]) -> dict[str, Any]:
    answers = decision.get("answers")
    return answers if isinstance(answers, dict) else decision

def _score(message: str, session: dict[str, Any]) -> float:
    haystack = " ".join(session.get(key, "") for key in ("target_repo", "title", "summary", "cwd")).lower()
    words = re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", message.lower())
    return sum(1.0 for word in set(words) if word in haystack)

def _route_from_message(message: str, sessions: list[dict[str, Any]]) -> str:
    if re.search(r"(?:新建|新开|另开|创建)\s*(?:一个\s*)?(?:m2|session|会话)|create\s+(?:a\s+)?new\s+m2", message, re.I):
        return "CREATE_NEW_M2"
    if re.search(r"跨\s*(?:m2|session|会话)|多个\s*(?:m2|session|会话)|同时|分别|同步到|转发给|加到|先让|再让|first|then|after that", message, re.I):
        return "ROUTE_CROSS_SESSION"
    return "ROUTE_SINGLE" if sessions else "CREATE_NEW_M2"

def _split_ordered_clauses(message: str) -> list[tuple[int, str]]:
    """Split common Chinese/English sequential instructions into soft waves."""
    connector = r"(?:(?:然后|再让|再由|随后|之后)|(?:then|next|after that|and then)\b)"
    parts = re.split(rf"\s*(?:，|,|;|；)\s*(?={connector})|\s+(?={connector})", message, flags=re.I)
    clauses: list[tuple[int, str]] = []
    wave = 1
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if re.match(rf"^{connector}", text, re.I):
            wave += 1
        text = re.sub(r"^(?:先|首先|first|initially)\s*", "", text, flags=re.I)
        clauses.append((wave, text))
    return clauses or [(1, message)]

def _clean_target_name(value: Any) -> str:
    return _text(value).strip(" `[](){}<>\"'。，,;；:：")

def _session_target_aliases(sessions: list[dict[str, Any]], domain_map: dict[str, Any] | None = None) -> tuple[dict[str, str], list[str]]:
    aliases: dict[str, str] = {}
    for session in sessions:
        canonical = _clean_target_name(session.get("target_repo"))
        if not canonical:
            continue
        values = (canonical, session.get("title"), session.get("herdr_target"))
        cwd = _text(session.get("cwd"))
        if cwd:
            values += (Path(cwd.rstrip("/")).name,)
        for value in values:
            alias = _clean_target_name(value)
            if alias:
                aliases.setdefault(alias.casefold(), canonical)
    for topic in (domain_map or {}).get("topics", []):
        canonical = _clean_target_name(topic.get("target_repo"))
        if not canonical:
            continue
        for value in (canonical, topic.get("topic_id"), topic.get("title"), *(topic.get("aliases") or [])):
            alias = _clean_target_name(value)
            if alias and len(alias) > 1:
                aliases.setdefault(alias.casefold(), canonical)
    return aliases, sorted(aliases, key=len, reverse=True)

def _deterministic_targets(message: str, sessions: list[dict[str, Any]], domain_map: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Extract known or explicitly named repos and soft waves without Jev targets."""
    clauses = _split_ordered_clauses(message)
    alias_map, known_aliases = _session_target_aliases(sessions, domain_map)
    actions = r"(?:更新|同步|接入|处理|完成|检查|执行|负责|修复|实现|推进|to(?:\s|$))"
    targets: list[dict[str, Any]] = []
    for wave, clause in clauses:
        names: list[str] = []
        marker = re.search(
            rf"(?:先让|再让|然后让|随后让|之后让|让|由|请|叫|let|ask|tell|have)\s*(.+?)(?=\s*{actions}|$)",
            clause,
            re.I,
        )
        candidate_text = marker.group(1) if marker else ""
        candidates = [
            _clean_target_name(part)
            for part in re.split(r"\s*(?:和|及|以及|、|and|&)\s*", candidate_text, flags=re.I)
            if _clean_target_name(part)
        ]
        names.extend(candidates)

        known_matches: list[tuple[int, str]] = []
        for alias in known_aliases:
            match = re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", clause, re.I)
            if match:
                known_matches.append((match.start(), alias_map[alias.casefold()]))
        names.extend(canonical for _, canonical in sorted(known_matches, key=lambda item: item[0]))

        canonical_names: list[str] = []
        for name in names:
            canonical = alias_map.get(name.casefold(), name)
            if re.search(r"[A-Za-z0-9]", canonical) and canonical not in canonical_names:
                canonical_names.append(canonical)
        if not canonical_names and domain_map:
            matched_topic = _topic_for_target({"sub_prompt": clause}, clause, domain_map, {})
            if matched_topic and _text(matched_topic.get("target_repo")):
                targets.append({
                    "topic_id": matched_topic["topic_id"],
                    "target_repo": matched_topic["target_repo"],
                    "sub_prompt": clause,
                    "wave_order": wave,
                })
                continue
        for name in canonical_names:
            targets.append({"target_repo": name, "sub_prompt": clause, "wave_order": wave})
    return targets

def _extract_jev_plan(
    decision: dict[str, Any],
    sessions: list[dict[str, Any]],
    message: str,
    domain_map: dict[str, Any],
    jev_decision: dict[str, Any] | None = None,
    workspaces: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    answers = _decision_answers(decision)
    jev_answers = _decision_answers(jev_decision or {})
    route_value = jev_answers.get("route_mode") or answers.get("route_mode") or answers.get("route") or decision.get("route")
    route = _text(route_value.get("choice") if isinstance(route_value, dict) else route_value)
    if route not in ROUTES:
        route = _route_from_message(message, sessions)

    raw_targets = _target_value(decision)
    used_deterministic_fallback = False
    if not raw_targets:
        raw_targets = _deterministic_targets(message, sessions, domain_map)
        used_deterministic_fallback = bool(raw_targets)
    if len(raw_targets) > 1 or _route_from_message(message, sessions) == "ROUTE_CROSS_SESSION":
        route = "ROUTE_CROSS_SESSION"
    if not raw_targets:
        primary = answers.get("primary_target") or jev_answers.get("primary_topic")
        if isinstance(primary, dict):
            primary = primary.get("choice")
        if primary and primary not in {"NEW_SESSION", NEW_UNMAPPED_DOMAIN}:
            raw_targets = [{"topic_id": primary, "sub_prompt": message}]
        elif route == "CREATE_NEW_M2":
            raw_targets = [{"topic_id": NEW_UNMAPPED_DOMAIN, "sub_prompt": message}]

    jev_primary = jev_answers.get("primary_topic")
    if isinstance(jev_primary, dict):
        jev_primary = jev_primary.get("choice")
    jev_unmapped = jev_answers.get("has_unmapped_domain")
    if isinstance(jev_unmapped, dict):
        jev_unmapped = jev_unmapped.get("noul", jev_unmapped.get("choice"))
    jev_unmapped = bool(jev_unmapped) or _text(jev_primary) == NEW_UNMAPPED_DOMAIN

    targets: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_targets):
        if isinstance(raw, str):
            raw = {"target_repo": raw}
        if not isinstance(raw, dict):
            continue
        target = dict(raw)
        target.setdefault("sub_prompt", message)
        target.setdefault("wave_order", target.get("order", target.get("wave", index + 1)))
        target.setdefault("rag_context_hint", target.get("context_pass") or target.get("rag_hint"))
        topic = _topic_for_target(target, message, domain_map, {"primary_topic": jev_primary, "targets": raw_targets})
        explicit_topic = _text(target.get("topic_id"))
        has_explicit_target_hint = bool(explicit_topic and explicit_topic != NEW_UNMAPPED_DOMAIN) or bool(_clean_target_name(target.get("target_repo")) and _clean_target_name(target.get("target_repo")) != "unspecified")
        is_new = (_text(jev_primary) == NEW_UNMAPPED_DOMAIN or (jev_unmapped and not explicit_topic and len(raw_targets) == 1)) and not (used_deterministic_fallback and topic is not None and not has_explicit_target_hint)
        if explicit_topic == NEW_UNMAPPED_DOMAIN:
            topic = None
            is_new = True
        if topic and not is_new:
            target["topic_id"] = topic["topic_id"]
            target["domain_status"] = MAPPED_DOMAIN
            target.setdefault("group", topic.get("group"))
            target.setdefault("title", topic.get("title"))
            target.setdefault("target_repo", topic.get("target_repo"))
            target.setdefault("cwd", topic.get("cwd"))
            target["space"] = _text(topic.get("space")) or _text(topic.get("target_repo"))
            target["space_aliases"] = list(topic.get("space_aliases") or [])
        else:
            proposal = _proposed_domain(target, message, domain_map)
            target["topic_id"] = proposal["topic_id"]
            target["domain_status"] = NEW_DOMAIN_CANDIDATE
            target["proposed_domain"] = proposal
            candidates.append(proposal)
        resolve_target_space(target, topic, workspaces, domain_map)
        if isinstance(target.get("proposed_domain"), dict):
            target["proposed_domain"]["space"] = _text(target["proposed_domain"].get("space")) or target["target_repo"]
        match = _target_session(target, topic, sessions, domain_map)
        session_id = _text(target.get("session_id") or target.get("sessionId"))
        if match:
            explicit_target_transport = _text(target.get("transport"))
            target.update({
                "session_id": match["session_id"],
                "target_repo": target.get("target_repo") or match["target_repo"],
                "source": match["source"],
                "transport": explicit_target_transport if explicit_target_transport and explicit_target_transport != "auto" else match["source"],
                "cwd": target.get("cwd") or match["cwd"],
                "herdr_target": match["herdr_target"],
                "pane_id": match["pane_id"],
                "workspace_id": match["workspace_id"],
                "title": target.get("title") or match["title"],
                "base_url": match.get("base_url", ""),
                "m2_status": "EXISTING_M2",
            })
            if match.get("agent_session"):
                target.setdefault("agent_session", dict(match["agent_session"]))
            if _text(match.get("agent_session_id")):
                target.setdefault("agent_session_id", _text(match["agent_session_id"]))
            if isinstance(match.get("recent_session_context"), dict):
                target["recent_session_context"] = dict(match["recent_session_context"])
                if _text(match["recent_session_context"].get("last_session_activity_at")):
                    target["last_session_activity_at"] = _text(match["recent_session_context"].get("last_session_activity_at"))
            elif _text(match.get("last_session_activity_at")):
                target["last_session_activity_at"] = _text(match.get("last_session_activity_at"))
        else:
            target["session_id"] = session_id
            target["target_repo"] = _repo_from(target) or "unspecified"
            target["m2_status"] = "NEW_M2"
            target.setdefault("transport", "auto")
        try:
            target["wave_order"] = max(1, int(target["wave_order"]))
        except (TypeError, ValueError):
            target["wave_order"] = index + 1
        target["sub_prompt"] = _text(target.get("sub_prompt"), message)
        target["rag_context_hint"] = target.get("rag_context_hint")
        targets.append(target)

    if len(targets) > 1:
        route = "ROUTE_CROSS_SESSION"
    if route == "ROUTE_CROSS_SESSION" and not targets:
        ranked = sorted(sessions, key=lambda item: _score(message, item), reverse=True)
        targets = [{"session_id": item["session_id"], "target_repo": item["target_repo"], "sub_prompt": message, "wave_order": index + 1, "rag_context_hint": None, "m2_status": "EXISTING_M2", "source": item["source"], "transport": item["source"], "herdr_target": item["herdr_target"], "cwd": item["cwd"], "pane_id": item["pane_id"], "workspace_id": item["workspace_id"], "title": item["title"], "topic_id": _new_topic_id({}, message), "domain_status": NEW_DOMAIN_CANDIDATE, "proposed_domain": _proposed_domain({}, message, domain_map)} for index, item in enumerate(ranked[:3])]
        if not targets:
            targets = [{"target_repo": "unspecified", "sub_prompt": message, "wave_order": 1, "rag_context_hint": None, "m2_status": "NEW_M2", "transport": "auto", "topic_id": _new_topic_id({}, message), "domain_status": NEW_DOMAIN_CANDIDATE, "proposed_domain": _proposed_domain({}, message, domain_map)}]
    if not targets:
        targets = [{"target_repo": "unspecified", "sub_prompt": message, "wave_order": 1, "rag_context_hint": None, "m2_status": "NEW_M2", "transport": "auto", "topic_id": _new_topic_id({}, message), "domain_status": NEW_DOMAIN_CANDIDATE, "proposed_domain": _proposed_domain({}, message, domain_map)}]
    targets.sort(key=lambda item: (item["wave_order"], item.get("target_repo", "")))
    unique_candidates = {item["topic_id"]: item for item in candidates}
    return route, targets, {"source": "deterministic_fallback" if used_deterministic_fallback or not decision else "jev", "model": _text(decision.get("model"), "microsoft-decision-1"), "confidence": route_value.get("confidence", decision.get("confidence", 0.0)) if isinstance(route_value, dict) else decision.get("confidence", 0.0), "reason": _text(decision.get("reason")), "primary_topic": _text(jev_primary), "has_unmapped_domain": jev_unmapped, "new_domain_candidates": list(unique_candidates.values())}

def _magpie_token() -> str:
    """Read an optional local gateway token without including it in plans or logs."""
    for key in ("MAGPIE_API_KEY", "MAGPIE_TOKEN", "MAGPIE_AUTH_TOKEN"):
        token = _text(os.environ.get(key))
        if token:
            return token
    config_root = Path.home() / ".config" / "magpie"
    for candidate in (config_root / "token", config_root / "api_key", config_root / "auth_token"):
        try:
            if candidate.is_file():
                token = candidate.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except OSError:
            continue
    for candidate in (config_root / "config.json", config_root / "settings.json"):
        try:
            if candidate.is_file():
                value = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    for key in ("api_key", "apiKey", "token", "auth_token", "authToken"):
                        token = _text(value.get(key))
                        if token:
                            return token
        except (OSError, ValueError):
            continue
    return ""

def _magpie_decision(value: Any) -> dict[str, Any] | None:
    """Extract the strict JSON routing object from a mock or OpenAI response."""
    if isinstance(value, str):
        value = _parse_json_output(value)
    if not isinstance(value, dict):
        return None
    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            content = message.get("content") if isinstance(message, dict) else first.get("text")
            parsed = _magpie_decision(content)
            if parsed:
                return parsed
    if "content" in value and not ("route" in value or "targets" in value):
        parsed = _magpie_decision(value.get("content"))
        if parsed:
            return parsed
    if isinstance(value.get("route"), str) or isinstance(value.get("targets"), (list, dict)) or isinstance(value.get("answers"), dict):
        return value
    return None

def _request_magpie(message: str, sessions: list[dict[str, Any]], *, base_url: str, model: str, token: str = "") -> dict[str, Any]:
    recent_contexts = {
        _text(s.get("session_id") or s.get("pane_id")): s["recent_session_context"]
        for s in sessions
        if isinstance(s, dict) and isinstance(s.get("recent_session_context"), dict) and (s["recent_session_context"].get("turns") or s["recent_session_context"].get("summary"))
    }
    request_input: dict[str, Any] = {"user_message": message, "active_m2_sessions": sessions}
    if recent_contexts:
        request_input["recent_session_context"] = recent_contexts
    system = (
        "You are the unified M2 ingress semantic decomposer. Return only strict JSON, with no markdown or prose. "
        "Rewrite each target's sub_prompt as a complete executable instruction for that M2. "
        "When binding an existing session (EXISTING_M2) that includes recent_session_context "
        "(recent user.message and assistant.message turns 2, 3 from direct sub-pane chat), "
        "you MUST generate an incremental sub_prompt (step 4) that continues directly on top of the latest turns in recent_session_context, "
        "and NEVER overwrite or regress to stale pre-private-chat state. "
        "M3 only routes and forwards the user's follow-up instruction by business topic faithfully to the target M2: "
        "NEVER pre-package or rewrite a follow-up instruction as a Hot-Append merged into the current Goal or split Goals at the M3 layer; "
        "leave Goal boundary judgment (same-page tweak Hot-Append vs. splitting a distinct new Feature into a separate second Goal handoff/<goal-2>/spec.md + separate TL/Task/PR) strictly to the receiving M2-Dispatcher. "
        "Bind session_id only when it matches an active session; otherwise use an empty string. "
        "Use route ROUTE_SINGLE, ROUTE_CROSS_SESSION, or CREATE_NEW_M2. "
        "Return {route, reason, targets:[{target_repo, session_id, sub_prompt, wave_order, rag_context_hint}]}."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(request_input, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    response = _post_json(base_url.rstrip("/") + "/v1/chat/completions", payload, token=token, timeout=5.0)
    decision = _magpie_decision(response)
    if decision is None:
        raise RouterError("Magpie returned invalid routing JSON")
    decision.setdefault("model", model)
    return decision

def _targets_decision(value: Any, message: str, model: str = "targets-json") -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("targets"), list):
        decision = dict(value)
    elif isinstance(value, list):
        decision = {"targets": value}
    else:
        raise RouterError("--targets-json must contain a target list or routing object")
    decision.setdefault("route", _route_from_message(message, []))
    decision.setdefault("reason", "targets supplied by outer CLI model")
    decision.setdefault("model", model)
    return decision

def classify(
    message: str,
    sessions: list[dict[str, Any]],
    *,
    domain_map: dict[str, Any],
    mock_magpie: Any = None,
    targets_json: Any = None,
    magpie_url: str = "",
    magpie_model: str = "",
    mock: Any = None,
    goal_id: str = "",
    workspaces: list[dict[str, Any]] | None = None,
    runner: CommandRunner = _run_command,
    herdr_bin: str | None = None,
    session_state_dir: str | Path | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    for session in sessions:
        if isinstance(session, dict) and not isinstance(session.get("recent_session_context"), dict):
            ctx = read_recent_session_context(
                session,
                runner=runner,
                herdr_bin=herdr_bin,
                session_state_dir=session_state_dir,
            )
            if ctx:
                session["recent_session_context"] = ctx
                if _text(ctx.get("last_session_activity_at")):
                    session["last_session_activity_at"] = _text(ctx.get("last_session_activity_at"))
    jev = _jev_decision(message, sessions, domain_map, mock=mock, goal_id=goal_id)
    decision: dict[str, Any] | None = None
    source = "magpie"
    if targets_json is not None:
        decision = _targets_decision(targets_json, message)
        source = "targets-json"
    elif mock_magpie is not None:
        decision = _magpie_decision(mock_magpie)
        source = "mock-magpie"
    elif mock is not None:
        decision = _magpie_decision(mock)
        source = "mock-magpie"
    else:
        try:
            decision = _request_magpie(
                message,
                sessions,
                base_url=_text(magpie_url, os.environ.get("MAGPIE_BASE_URL", MAGPIE_BASE_URL)),
                model=_text(magpie_model, os.environ.get("MAGPIE_MODEL", MAGPIE_DEFAULT_MODEL)),
                token=_magpie_token(),
            )
        except (OSError, ValueError, RouterError, urllib.error.URLError, TimeoutError):
            decision = None
            source = "offline-fallback"
    semantic = decision or (jev if _target_value(jev or {}) else {})
    route, targets, metadata = _extract_jev_plan(semantic, sessions, message, domain_map, jev, workspaces)
    if semantic and metadata.get("source") == "deterministic_fallback" and source == "mock-magpie":
        metadata["source"] = "deterministic_fallback"
    else:
        metadata["source"] = source if semantic else "offline-fallback"
    metadata["model"] = _text((decision or {}).get("model"), _text(magpie_model, os.environ.get("MAGPIE_MODEL", MAGPIE_DEFAULT_MODEL)))
    metadata["jev"] = jev or {}
    return route, targets, metadata

def _stale_candidates(target: dict[str, Any], sessions: list[dict[str, Any]], domain_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Find only safe-to-close idle Herdr panes for the target topic/repo."""
    topic = _topic_by_id(domain_map).get(_text(target.get("topic_id")))
    matches = _matching_topic_sessions(target, topic, sessions, domain_map)
    if not matches:
        return []
    current_pane = _text(os.environ.get("HERDR_PANE_ID"))
    primary_id = _text(target.get("session_id")) if target.get("m2_status") == "EXISTING_M2" else ""
    candidates: list[dict[str, Any]] = []
    idle_primary_assigned = False
    for session in matches:
        if session.get("source") != "herdr" or _text(session.get("status"), "idle").casefold() not in {"idle", "done"}:
            continue
        pane_id = _text(session.get("pane_id"))
        if not pane_id or pane_id == current_pane or _gc_protected(session):
            continue
        is_primary_idle = session.get("session_id") == primary_id or not idle_primary_assigned
        reason = "idle_existing_m2" if is_primary_idle else "duplicate_m2"
        idle_primary_assigned = True
        candidates.append({"session_id": session.get("session_id", ""), "pane_id": pane_id, "title": session.get("title", ""), "status": session.get("status", "idle"), "target_repo": session.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": reason})
    return candidates

def _read_goal_state(session: dict[str, Any], *, handoff_root: str | Path | None = None) -> tuple[bool, str]:
    """Determine effective current completion from structured ledger state."""
    root = Path(handoff_root or os.environ.get("M2_HANDOFF_ROOT") or "~/Development/handoff").expanduser()
    explicit = _text(session.get("handoff_dir"))
    text = "\n".join(_text(session.get(k)) for k in ("target", "title", "summary", "visible_summary", "terminal_title", "linear_issue"))
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit) / "ledger.md")
    if _text(session.get("goal_id")):
        candidates.append(root / _text(session["goal_id"]) / "ledger.md")
    for match in re.finditer(r"(?:~/|/)[^\s`\"'<>|()]*handoff/([^/\s`\"'<>|()]+)", text):
        candidates.append(Path(match.group(0)).expanduser() / "ledger.md")
    if root.is_dir():
        candidates.extend(sorted(root.glob("*/ledger.md")))
    ranked: list[tuple[int, Path, str]] = []
    for path in dict.fromkeys(candidates):
        try:
            ledger = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        score = 100 if explicit and path.parent == Path(explicit).expanduser() else 0
        if path.parent.name and path.parent.name.casefold() in text.casefold():
            score += 30
        linear = re.search(r"\bTIA-\d+\b", ledger, re.I)
        if linear and linear.group(0).casefold() in text.casefold():
            score += 20
        if score or explicit or _text(session.get("goal_id")):
            ranked.append((score, path, ledger))
    for _, path, ledger in sorted(ranked, key=lambda item: item[0], reverse=True)[:1]:
        header = re.search(r"^\s*-?\s*(?:status|状态)\s*[:：]\s*([^\n|]+)", ledger, re.I | re.M)
        header_status = _text(header.group(1)).casefold() if header else ""
        tasks: list[str] = []
        in_tasks = False
        for line in ledger.splitlines():
            if line.startswith("## "):
                in_tasks = line.strip().casefold() == "## tasks"
            if in_tasks and line.startswith("|"):
                cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 5 and cells[4] in {"planned", "ready", "in_progress", "done", "blocked", "in_review", "changes_requested"}:
                    tasks.append(cells[4])
        events = [(idx, line) for idx, line in enumerate(ledger.splitlines()) if "EVT=" in line]
        done_idx = max((idx for idx, line in events if re.search(r"EVT=GOAL_DONE", line, re.I)), default=-1)
        later_active = any(idx > done_idx and re.search(r"EVT=(?:READY|IN_PROGRESS|IN_REVIEW|DISPATCHED|INPUT_BLOCKED)|NEED_OWNER_DECISION", line, re.I) for idx, line in events)
        active_status = header_status in {"in_progress", "planned", "ready", "blocked", "in_review", "changes_requested", "reopened", "needs_input"} or bool(re.search(r"need_owner_decision|in review", ledger, re.I))
        active_tasks = any(status != "done" for status in tasks)
        if later_active or active_status or active_tasks:
            return False, f"active_or_reopened:{path.parent.name}"
        if done_idx >= 0 or header_status in {"done", "completed", "complete"}:
            return True, f"ledger:{path.parent.name}"
        if re.search(r"\b(?:Done|Completed)\b", text, re.I):
            return True, f"linear_done:{path.parent.name}"
    if re.search(r"\b(?:Done|Completed)\b", text, re.I) and not re.search(r"in_progress|in review|blocked|needs_input", text, re.I):
        return True, "explicit_done_evidence"
    if session.get("goal_done") is True:
        return True, "session.goal_done"
    return False, "not_effectively_completed"

def _answer_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("noul", "choice", "value", "score", "answer"):
            if key in value:
                return value[key]
    return value

def _evaluate_m2_context_continuity(
    target: dict[str, Any], session: dict[str, Any], message: str, *,
    domain_map: dict[str, Any] | None = None, mock_jev: Any = None,
    handoff_root: str | Path | None = None, visible_text: str = "",
) -> dict[str, Any]:
    """Strict safety truth table: effective completion AND both unrelatedness answers."""
    old_done, completion_evidence = _read_goal_state(session, handoff_root=handoff_root)
    semantic: dict[str, Any] = {}
    if isinstance(mock_jev, dict):
        semantic = _decision_answers(mock_jev)
    relevant = any(key in semantic for key in ("is_same_goal_continuation", "prior_context_value", "prior_context_value_match"))
    if not relevant and mock_jev is None:
        try:
            jev_path = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
            spec = importlib.util.spec_from_file_location("m2_router_continuity_jev", jev_path)
            module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
            questions = {
                "is_same_goal_continuation": {"type": "noul", "instructions": "Is this a direct continuation, follow-up, or feedback?"},
                "prior_context_value": {"type": "choice", "criteria": {"essential":"essential", "helpful":"helpful", "noise_or_irrelevant":"noise_or_irrelevant"}, "instructions": "How valuable is old context?"},
                "prior_context_value_match": {"type": "noul", "instructions": "Is prior context essential or helpful?"},
            }
            result = module.jev_decide({"old_title": session.get("title", ""), "old_summary": session.get("summary", ""), "new_message": message, "topic_id": target.get("topic_id", "")}, questions, agent_role="personal-m2-assistant", timeout_sec=1.5)
            semantic = _decision_answers(result) if isinstance(result, dict) else {}
        except (OSError, ImportError, TypeError, AssertionError):
            semantic = {}
    raw_same = _answer_value(semantic.get("is_same_goal_continuation"))
    try:
        same_score = float(raw_same) if raw_same is not None else None
    except (TypeError, ValueError):
        same_score = None
    value = _text(_answer_value(semantic.get("prior_context_value"))).lower() or None
    match = _answer_value(semantic.get("prior_context_value_match"))
    valid = same_score is not None and 0 <= same_score <= 1 and value in {"essential", "helpful", "noise_or_irrelevant"}
    match_conflict = False
    if match is not None:
        if isinstance(match, bool):
            match_conflict = match
        elif isinstance(match, (int, float)) and not isinstance(match, bool):
            match_conflict = not (0 <= float(match) < 0.35)
        elif _text(match).casefold() in {"true", "yes", "essential", "helpful"}:
            match_conflict = True
        elif _text(match) and _text(match).casefold() not in {"false", "no", "noise_or_irrelevant"}:
            match_conflict = True
    unrelated = bool(valid and same_score < 0.35 and value == "noise_or_irrelevant" and not match_conflict and not _has_active_visible_tail(visible_text) and not _gc_protected(session))
    replace = bool(old_done and unrelated)
    return {
        "m2_action": "REPLACE_STALE_M2" if replace else "REUSE_EXISTING_M2",
        "m2_status": "NEW_M2" if replace else "EXISTING_M2",
        "old_goal_done": old_done,
        "completion_evidence": completion_evidence,
        "is_same_goal_continuation": same_score,
        "prior_context_value": value,
        "prior_context_value_match": match,
        "semantic_evidence_valid": valid,
        "semantic_unrelated": unrelated,
        "eviction_reason": "completed_goal_and_strict_jev_unrelatedness" if replace else "conservative_reuse",
        "stale_session_id": _text(session.get("session_id")),
        "stale_pane_id": _text(session.get("pane_id")),
    }

def _gc_protected(rec: dict[str, Any]) -> bool:
    pane = _text(rec.get("pane_id") or rec.get("paneId"))
    status = _text(rec.get("agent_status") or rec.get("status")).lower()
    hay = " ".join(_text(rec.get(k)) for k in ("workspace_label", "workspaceLabel", "space", "label", "title", "name"))
    return pane in {_text(os.environ.get("HERDR_PANE_ID")), "w1:p1", "w2J:p1"} or re.search(r"Cindy|大总管", hay, re.I) is not None or status in {"working", "running", "blocked", "needs_input"} or _has_active_visible_tail(_text(rec.get("visible") or rec.get("visible_text")))

def discover_gc_candidates(records: Iterable[dict[str, Any]], *, handoff_root: str | Path | None = None) -> list[dict[str, Any]]:
    records = [dict(r) for r in records if isinstance(r, dict)]
    candidates: list[dict[str, Any]] = []
    for rec in records:
        if _gc_protected(rec):
            continue
        pane = _text(rec.get("pane_id") or rec.get("paneId"))
        if not pane:
            continue
        status = _text(rec.get("agent_status") or rec.get("status"), "idle").lower()
        background = _herdr_background(rec)
        done, evidence = _read_goal_state(rec, handoff_root=handoff_root)
        workspace_ids = {_text(rec.get("workspace_id") or rec.get("workspaceId")), _text(rec.get("workspace_label") or rec.get("workspaceLabel"))}
        prefix = pane.split(":", 1)[0]
        active_child = any(
            other is not rec and (
                (workspace_ids - {""}) & {_text(other.get("workspace_id") or other.get("workspaceId")), _text(other.get("workspace_label") or other.get("workspaceLabel"))}
                or _text(other.get("pane_id") or other.get("paneId")).split(":", 1)[0] == prefix
            ) and (
                _text(other.get("agent_status") or other.get("status")).lower() in {"working", "running", "blocked", "needs_input"}
                or _has_active_visible_tail(_text(other.get("visible") or other.get("visible_text")))
            ) for other in records
        )
        if background and (status == "done" or done) and status in {"idle", "done", "exited", "stopped"} and not active_child:
            candidates.append({"category":"orphan_m1", "reason":"orphan_completed_m1", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "completion_evidence":evidence})
        elif _looks_like_m2(rec, "herdr") and status in {"idle", "done"}:
            if done:
                candidates.append({"category":"completed_goal_m2", "reason":"completed_goal_m2", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "completion_evidence":evidence, "topic_id":_text(rec.get("topic_id"))})
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if _looks_like_m2(rec, "herdr") and not _gc_protected(rec) and _text(rec.get("agent_status") or rec.get("status"), "idle").lower() in {"idle", "done"}:
            space = _text(rec.get("workspace_label") or rec.get("workspaceLabel") or rec.get("space") or rec.get("target_repo") or rec.get("repo"), "M2").casefold()
            topic = _text(rec.get("topic_id") or rec.get("topicId") or rec.get("target_repo") or rec.get("repo"), "unknown").casefold()
            groups.setdefault(f"{space}|{topic}", []).append(rec)
    for key, group in groups.items():
        classified = {c.get("pane_id"): c for c in candidates if c.get("pane_id")}
        eligible = [rec for rec in group if classified.get(_text(rec.get("pane_id") or rec.get("paneId")), {}).get("reason") != "completed_goal_m2"]
        for rec in eligible[1:]:
            pane = _text(rec.get("pane_id") or rec.get("paneId"))
            if pane and not any(c.get("pane_id") == pane for c in candidates):
                candidates.append({"category":"duplicate_m2", "reason":"duplicate_m2", "pane_id":pane, "session_id":_session_id(rec), "title":_text(rec.get("title") or rec.get("name")), "topic_id":_text(rec.get("topic_id")), "space":key})
    return candidates

def _prune_closed_slots(state_path: Path | None, receipts: list[dict[str, Any]]) -> list[str]:
    if state_path is None:
        return []
    closed = [r for r in receipts if r.get("ok") and not r.get("skipped") and not r.get("simulated")]
    if not closed:
        return []
    pruned: list[str] = []
    def prune(state: dict[str, Any]) -> dict[str, Any]:
        kept = []
        for slot in state.get("slots", []):
            match = next((r for r in closed if _text(r.get("pane_id")) == _text(slot.get("pane_id"))), None)
            if match and (not _text(match.get("session_id")) or not _text(slot.get("session_id")) or _text(match.get("session_id")) == _text(slot.get("session_id"))):
                pruned.append(_text(slot.get("slot_id") or slot.get("pane_id")))
            else:
                kept.append(slot)
        state["slots"] = kept
        return state
    _with_locked_supervision_state(state_path, prune)
    return pruned
