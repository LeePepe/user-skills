from __future__ import annotations

from ._imports import *

def load_domain_map(path: str | Path | None = None) -> dict[str, Any]:
    """Load and flatten the checked-in topic map without mutating it."""
    map_path = Path(path).expanduser() if path else DEFAULT_DOMAIN_MAP_PATH
    try:
        value = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RouterError(f"unable to load domain map {map_path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
        raise RouterError(f"invalid domain map {map_path}: expected groups")
    topics: list[dict[str, Any]] = []
    for group in value["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("topics"), list):
            continue
        for topic in group["topics"]:
            if not isinstance(topic, dict) or not _text(topic.get("topic_id")):
                continue
            item = dict(topic)
            item.setdefault("group", group.get("group", "unclassified"))
            item.setdefault("title", item["topic_id"])
            item["cwd"] = _expand_cwd(item.get("cwd"))
            item["target_repo"] = _text(item.get("target_repo")) or _repo_from(item)
            item.setdefault("keywords", [])
            item.setdefault("aliases", [])
            item["space"] = _text(item.get("space")) or item["target_repo"]
            item["space_aliases"] = list(item.get("space_aliases") or [])
            topics.append(item)
    if len(topics) < 23:
        raise RouterError(f"invalid domain map {map_path}: expected at least 23 topics, found {len(topics)}")
    return {"schema_version": value.get("schema_version", 1), "groups": value["groups"], "topics": topics, "path": str(map_path)}

def _topic_by_id(domain_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {topic["topic_id"]: topic for topic in domain_map.get("topics", [])}

def _topic_text(topic: dict[str, Any]) -> str:
    values = [topic.get("topic_id"), topic.get("title"), topic.get("target_repo"), topic.get("cwd")]
    values.extend(topic.get("keywords") or [])
    values.extend(topic.get("aliases") or [])
    return " ".join(_text(value) for value in values if _text(value)).casefold()

def _topic_match_score(text: str, topic: dict[str, Any]) -> int:
    haystack = text.casefold()
    score = 0
    for value in [topic.get("topic_id"), topic.get("title"), topic.get("target_repo"), *(topic.get("keywords") or []), *(topic.get("aliases") or [])]:
        candidate = _text(value).casefold()
        if candidate and candidate in haystack:
            score += 4 if candidate == _text(topic.get("target_repo")).casefold() else 2
    return score

def _normalise_hint(value: Any) -> str:
    return re.sub(r"[-_\s]+", " ", _text(value)).strip().casefold()

def _target_hint_values(target: dict[str, Any]) -> list[str]:
    keys = (
        "topic_id",
        "domain_key",
        "domain",
        "topic",
        "space_name",
        "space",
        "skill",
        "skill_name",
        "name",
        "target_repo",
        "title",
        "instruction",
        "instructions",
        "prompt",
        "detail",
        "details",
        "body",
        "sub_prompt",
        "rag_context_hint",
    )
    return [_text(target.get(key)) for key in keys if _text(target.get(key))]

def _topic_exact_match(topics: list[dict[str, Any]], values: list[str]) -> dict[str, Any] | None:
    wanted = {_normalise_hint(value) for value in values if _normalise_hint(value)}
    for topic in topics:
        candidates = [
            topic.get("space"),
            *(topic.get("space_aliases") or []),
        ]
        if any(_normalise_hint(value) in wanted for value in candidates):
            return topic
    return None

def _topic_keyword_match(topics: list[dict[str, Any]], values: list[str]) -> dict[str, Any] | None:
    wanted = {_normalise_hint(value) for value in values if _normalise_hint(value)}
    for topic in topics:
        candidates = [
            topic.get("title"),
            *(topic.get("keywords") or []),
            *(topic.get("aliases") or []),
        ]
        if any(_normalise_hint(value) in wanted for value in candidates):
            return topic
    return None

def _topic_has_explicit_hint(target: dict[str, Any], topic: dict[str, Any] | None, domain_map: dict[str, Any]) -> bool:
    if not topic:
        return False
    values = [
        _text(target.get(key))
        for key in ("topic_id", "domain_key", "domain", "topic", "space_name", "space", "skill", "skill_name", "name")
        if _text(target.get(key))
    ]
    topic_values = [
        topic.get("topic_id"),
        topic.get("title"),
        topic.get("space"),
        *(topic.get("space_aliases") or []),
        *(topic.get("keywords") or []),
        *(topic.get("aliases") or []),
    ]
    wanted = {_normalise_hint(value) for value in values}
    if any(_normalise_hint(value) in wanted for value in topic_values):
        return True
    repo = _normalise_hint(target.get("target_repo"))
    if not repo or repo != _normalise_hint(topic.get("target_repo")):
        return False
    return sum(_normalise_hint(item.get("target_repo")) == repo for item in domain_map.get("topics", [])) == 1

def _topic_for_target(target: dict[str, Any], message: str, domain_map: dict[str, Any], jev: dict[str, Any]) -> dict[str, Any] | None:
    by_id = _topic_by_id(domain_map)
    topics = list(domain_map.get("topics", []))
    explicit_hints = [
        _text(target.get(key))
        for key in ("topic_id", "domain_key", "domain", "topic", "space_name", "space", "skill", "skill_name", "name")
        if _text(target.get(key))
    ]
    hints = _target_hint_values(target)
    normalised_ids = {_normalise_hint(topic_id): topic_id for topic_id in by_id}
    for hint in explicit_hints:
        topic_id = normalised_ids.get(_normalise_hint(hint))
        if topic_id:
            return by_id[topic_id]
    explicit_space = _topic_exact_match(topics, explicit_hints)
    if explicit_space:
        return explicit_space
    explicit_keyword = _topic_keyword_match(topics, explicit_hints)
    if explicit_keyword:
        return explicit_keyword
    primary = jev.get("primary_topic")
    if isinstance(primary, dict):
        primary = primary.get("choice")
    primary_id = normalised_ids.get(_normalise_hint(primary))
    if len(jev.get("targets", [])) <= 1 and primary_id:
        return by_id[primary_id]
    repo = _clean_target_name(target.get("target_repo"))
    repo_topics = [topic for topic in topics if repo and _clean_target_name(topic.get("target_repo")).casefold() == repo.casefold()]
    if len(repo_topics) == 1:
        return repo_topics[0]
    pool = repo_topics if repo_topics else topics
    target_text = " ".join(hints)
    text = target_text if len(jev.get("targets", [])) > 1 else (target_text + " " + message)
    scored = sorted(((_topic_match_score(text, topic), topic) for topic in pool), key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None

def _new_topic_id(target: dict[str, Any], message: str) -> str:
    explicit = _clean_target_name(target.get("topic_id"))
    if explicit and explicit not in {NEW_UNMAPPED_DOMAIN}:
        return explicit
    repo_seed = _clean_target_name(target.get("target_repo"))
    seed = repo_seed or _clean_target_name(message.split()[0] if message.split() else "domain")
    slug = re.sub(r"[^a-z0-9]+", "-", seed.casefold()).strip("-") or "unmapped-domain"
    prop = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    hint_text = " ".join(_text(x) for x in (prop.get("title"), target.get("title"), target.get("sub_prompt"), message) if _text(x)).strip()
    if repo_seed and hint_text and hint_text.casefold() != repo_seed.casefold():
        digest = hashlib.sha256(hint_text.casefold().encode("utf-8")).hexdigest()[:6]
        en_words = [
            w for w in re.findall(r"[a-z0-9]{2,}", hint_text.casefold())
            if w != slug and w not in {"m2", "session", "new", "create", "build", "fix", "update", "inspect", "implement", "the", "for", "and", "with"}
        ]
        if en_words:
            readable = "-".join(en_words[:2])[:10].rstrip("-")
            suffix = f"{readable}-{digest}"
        else:
            suffix = digest
        slug = f"{slug[:18].rstrip('-')}-{suffix}"
    return f"new-topic-{slug[:40].rstrip('-')}"

def _proposed_domain(target: dict[str, Any], message: str, domain_map: dict[str, Any]) -> dict[str, Any]:
    topic_id = _new_topic_id(target, message)
    title = _text(target.get("proposed_domain", {}).get("title") if isinstance(target.get("proposed_domain"), dict) else "")
    title = title or _text(target.get("title")) or topic_id.replace("-", " ").title()
    repo = _text(target.get("target_repo"), "unspecified")
    cwd = _expand_cwd(_text(target.get("cwd")) or f"~/Development/{repo}")
    keywords = target.get("keywords") if isinstance(target.get("keywords"), list) else []
    if not keywords:
        keywords = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", message)[:8]]
    return {"topic_id": topic_id, "group": _text(target.get("group"), "new-domain"), "title": title, "target_repo": repo, "cwd": cwd, "space": _text(target.get("space"), repo), "space_aliases": list(target.get("space_aliases") or []), "keywords": sorted(set(keywords)), "aliases": list(target.get("aliases") or [])}

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
