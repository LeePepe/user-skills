from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

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
        jev_path = ROUTER_FILE.resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
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
