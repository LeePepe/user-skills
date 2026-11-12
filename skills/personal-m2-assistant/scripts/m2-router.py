#!/usr/bin/env python3
"""Unified M2 ingress: classify, preview, and owner-gated dispatch.

The router deliberately separates planning from delivery.  ``--plan`` only reads
session discovery sources and Jev; ``--apply`` requires an explicit approval flag
and sends only the targets recorded in the plan.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
ACTIVE_STATUSES = {"working", "running", "idle", "blocked", "needs_input", "ready"}
TERMINAL_STATUSES = {"done", "cancelled", "canceled", "stopped", "archived", "exited", "dead"}
ROUTES = {"ROUTE_SINGLE", "ROUTE_CROSS_SESSION", "CREATE_NEW_M2"}
NEW_UNMAPPED_DOMAIN = "NEW_UNMAPPED_DOMAIN"
MAPPED_DOMAIN = "MAPPED_DOMAIN"
NEW_DOMAIN_CANDIDATE = "NEW_DOMAIN_CANDIDATE"
MAGPIE_BASE_URL = "http://127.0.0.1:3425"
MAGPIE_DEFAULT_MODEL = "group/steering"
DEFAULT_DOMAIN_MAP_PATH = Path(__file__).resolve().parents[1] / "references" / "m2-domain-map.json"
HERDR_BACKGROUND_RE = re.compile(r"(?:^|[-_])(?:goal-tl|writer|reviewer|explorer|pr-monitor)(?:[-_]|$)|/worktrees(?:/|$)", re.I)
HERDR_INTERACTIVE_AGENTS = {"copilot", "claude", "codex", "pi", "cindy"}


class RouterError(RuntimeError):
    """A user-actionable planning or dispatch error."""


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _json_value(value: str) -> Any:
    path = Path(value).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _parse_json_output(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some CLI wrappers print a short preamble before their JSON result.
        for marker in ("{", "["):
            index = raw.find(marker)
            if index >= 0:
                try:
                    return json.loads(raw[index:])
                except json.JSONDecodeError:
                    continue
    return None


def _repo_from(value: dict[str, Any]) -> str:
    for key in ("target_repo", "repo", "repository", "repository_name", "repo_root", "repositoryPath", "project", "goal"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    cwd = _text(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree"))
    if cwd:
        return Path(cwd.rstrip("/")).name
    return ""


def _session_id(value: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "id", "herdr_target", "herdrTarget", "name", "pane_id"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    return ""


def _herdr_agent_name(value: dict[str, Any]) -> str:
    agent = value.get("agent") or value.get("agent_name") or value.get("agentName") or value.get("provider")
    if isinstance(agent, dict):
        agent = agent.get("name") or agent.get("type") or agent.get("id")
    return _text(agent).lower()


def _herdr_background(value: dict[str, Any]) -> bool:
    haystack = " ".join(_text(value.get(key)) for key in (
        "name", "title", "role", "goal", "cwd", "foreground_cwd", "worktree", "repo_root"
    ))
    return bool(HERDR_BACKGROUND_RE.search(haystack))


def _herdr_interactive_candidate(value: dict[str, Any]) -> bool:
    if _herdr_background(value):
        return False
    if value.get("active") is False or value.get("is_active") is False or value.get("isActive") is False:
        return False
    explicit = value.get("is_m2", value.get("isM2"))
    if explicit is True or _looks_like_m2(value, "herdr"):
        return True
    if explicit is False:
        return False
    agent = _herdr_agent_name(value)
    cwd = _text(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree") or value.get("repo_root"))
    if not agent or not cwd:
        return False
    return agent in HERDR_INTERACTIVE_AGENTS or bool(re.search(r"copilot|claude|codex|pi|cindy", agent, re.I))


def _looks_like_m2(value: dict[str, Any], source: str) -> bool:
    explicit = value.get("is_m2", value.get("isM2"))
    if explicit is not None:
        return bool(explicit)
    if source == "dsh":
        return value.get("isDshSession", value.get("is_dsh_session", True)) is not False
    haystack = " ".join(_text(value.get(key)) for key in (
        "name", "title", "role", "agent", "goal", "target_repo", "repo"
    )).lower()
    return bool(re.search(r"(?:^|[-_ /])m2(?:[-_ /]|$)|dispatcher|omni-hub|omnihub", haystack))


def normalize_session(value: dict[str, Any], source: str, *, allow_interactive_herdr: bool = False) -> dict[str, Any] | None:
    """Normalize Herdr/dsh records without retaining provider-specific secrets."""
    if not isinstance(value, dict):
        return None
    if source == "herdr" and allow_interactive_herdr:
        if not _herdr_interactive_candidate(value):
            return None
    elif not _looks_like_m2(value, source):
        return None
    status = _text(value.get("agent_status") or value.get("status"), "idle").lower()
    if status in TERMINAL_STATUSES:
        return None
    sid = _session_id(value)
    if not sid:
        return None
    repo = _repo_from(value)
    return {
        "session_id": sid,
        "topic_id": _text(value.get("topic_id") or value.get("topicId")),
        "target_repo": repo,
        "title": _text(value.get("title") or value.get("terminal_title_stripped") or value.get("name"), sid),
        "status": status,
        "cwd": _text(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree")),
        "workspace_id": _text(value.get("workspace_id") or value.get("workspaceId")),
        "pane_id": _text(value.get("pane_id") or value.get("paneId")),
        "herdr_target": _text(value.get("herdr_target") or value.get("herdrTarget") or value.get("name"), sid),
        "source": source,
        "summary": _text(value.get("summary") or value.get("contextSummary") or value.get("lastAssistantText"))[:800],
        "base_url": _text(value.get("base_url") or value.get("dsh_base_url")),
    }


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("agents", "panes", "sessions", "dshSessions", "items"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    result = payload.get("result")
    return _records(result) if result is not None else []


def _run_command(args: list[str], *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _merge_herdr_records(pane_records: list[dict[str, Any]], agent_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join pane and agent inventories so either public command can supply identity."""
    merged: dict[str, dict[str, Any]] = {}
    for record in pane_records + agent_records:
        if not isinstance(record, dict):
            continue
        key = _text(record.get("pane_id") or record.get("paneId") or record.get("id") or record.get("name"))
        if not key:
            key = f"record-{len(merged)}"
        current = merged.setdefault(key, {})
        current.update({field: value for field, value in record.items() if value not in (None, "")})
    return list(merged.values())


def discover_herdr(
    *,
    payload: Any = None,
    pane_payload: Any = None,
    agent_payload: Any = None,
    herdr_bin: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
) -> tuple[list[dict[str, Any]], str | None]:
    """Discover interactive repo-root M2 panes from both public Herdr inventories."""
    pane_records: list[dict[str, Any]] = []
    agent_records: list[dict[str, Any]] = []
    if payload is not None:
        if isinstance(payload, dict) and ("panes" in payload or "agents" in payload):
            pane_records = _records(payload.get("panes"))
            agent_records = _records(payload.get("agents"))
            if not pane_records and not agent_records:
                pane_records = _records(payload)
        else:
            agent_records = _records(payload)
    if pane_payload is not None:
        pane_records = _records(pane_payload)
    if agent_payload is not None:
        agent_records = _records(agent_payload)
    if pane_payload is None and agent_payload is None and payload is None:
        binary = herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr")
        if not binary:
            return [], None
        try:
            pane_result = runner([binary, "pane", "list"], timeout=3.0)
            agent_result = runner([binary, "agent", "list"], timeout=3.0)
        except (OSError, subprocess.SubprocessError) as error:
            return [], f"herdr discovery failed: {error}"
        failures = [result for result in (pane_result, agent_result) if result.returncode != 0]
        if len(failures) == 2:
            return [], f"herdr discovery exited {failures[0].returncode}/{failures[1].returncode}"
        pane_records = _records(_parse_json_output(pane_result.stdout)) if pane_result.returncode == 0 else []
        agent_records = _records(_parse_json_output(agent_result.stdout)) if agent_result.returncode == 0 else []
    records = _merge_herdr_records(pane_records, agent_records)
    sessions = [normalize_session(item, "herdr", allow_interactive_herdr=True) for item in records]
    return [item for item in sessions if item], None


def _fetch_json(url: str, *, token: str = "", timeout: float = 3.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_dsh(
    *,
    payload: Any = None,
    state_url: str = "",
    token: str = "",
    fetcher: Callable[..., Any] = _fetch_json,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read dsh's public omni-hub state, never its private session storage."""
    if payload is None and state_url:
        try:
            payload = fetcher(state_url, token=token, timeout=3.0)
        except (OSError, ValueError, urllib.error.URLError) as error:
            return [], f"dsh discovery failed: {error}"
    if payload is None:
        return [], None
    sessions = [normalize_session(item, "dsh") for item in _records(payload)]
    return [item for item in sessions if item], None


def discover_sessions(
    *,
    herdr_payload: Any = None,
    herdr_pane_payload: Any = None,
    herdr_agent_payload: Any = None,
    dsh_payload: Any = None,
    herdr_bin: str | None = None,
    dsh_state_url: str = "",
    dsh_token: str = "",
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    fetcher: Callable[..., Any] = _fetch_json,
    include_herdr: bool = True,
    include_dsh: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    if include_herdr:
        found, warning = discover_herdr(payload=herdr_payload, pane_payload=herdr_pane_payload, agent_payload=herdr_agent_payload, herdr_bin=herdr_bin, runner=runner)
        sessions.extend(found)
        if warning:
            warnings.append(warning)
    if include_dsh:
        found, warning = discover_dsh(payload=dsh_payload, state_url=dsh_state_url, token=dsh_token, fetcher=fetcher)
        sessions.extend(found)
        if warning:
            warnings.append(warning)
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for session in sessions:
        deduped[(session["source"], session["session_id"])] = session
    return list(deduped.values()), warnings


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
            item.setdefault("target_repo", "")
            item.setdefault("cwd", "")
            item.setdefault("keywords", [])
            item.setdefault("aliases", [])
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


def _topic_for_target(target: dict[str, Any], message: str, domain_map: dict[str, Any], jev: dict[str, Any]) -> dict[str, Any] | None:
    by_id = _topic_by_id(domain_map)
    explicit = _text(target.get("topic_id"))
    if explicit in by_id:
        return by_id[explicit]
    primary = jev.get("primary_topic")
    if isinstance(primary, dict):
        primary = primary.get("choice")
    if len(jev.get("targets", [])) <= 1 and _text(primary) in by_id:
        return by_id[_text(primary)]
    repo = _clean_target_name(target.get("target_repo"))
    repo_topics = [topic for topic in domain_map.get("topics", []) if repo and _clean_target_name(topic.get("target_repo")).casefold() == repo.casefold()]
    if len(repo_topics) == 1:
        return repo_topics[0]
    pool = repo_topics if repo_topics else domain_map.get("topics", [])
    target_text = " ".join(_text(target.get(key)) for key in ("target_repo", "title", "sub_prompt", "rag_context_hint"))
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
        en_words = [
            w for w in re.findall(r"[a-z0-9]{2,}", hint_text.casefold())
            if w != slug and w not in {"m2", "session", "new", "create", "build", "fix", "update", "inspect"}
        ]
        if en_words:
            suffix = "-".join(en_words[:2])[:16].rstrip("-")
        else:
            suffix = hashlib.sha256(hint_text.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[:20].rstrip('-')}-{suffix}"
    return f"new-topic-{slug[:40].rstrip('-')}"


def _proposed_domain(target: dict[str, Any], message: str, domain_map: dict[str, Any]) -> dict[str, Any]:
    topic_id = _new_topic_id(target, message)
    title = _text(target.get("proposed_domain", {}).get("title") if isinstance(target.get("proposed_domain"), dict) else "")
    title = title or _text(target.get("title")) or topic_id.replace("-", " ").title()
    repo = _text(target.get("target_repo"), "unspecified")
    cwd = _text(target.get("cwd")) or f"~/Development/{repo}"
    keywords = target.get("keywords") if isinstance(target.get("keywords"), list) else []
    if not keywords:
        keywords = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", message)[:8]]
    return {"topic_id": topic_id, "group": _text(target.get("group"), "new-domain"), "title": title, "target_repo": repo, "cwd": cwd, "keywords": sorted(set(keywords)), "aliases": list(target.get("aliases") or [])}


def _matching_topic_sessions(
    target: dict[str, Any],
    topic: dict[str, Any] | None,
    sessions: list[dict[str, Any]],
    domain_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    repo = _clean_target_name(target.get("target_repo"))
    candidates = [session for session in sessions if repo and _clean_target_name(session.get("target_repo")).casefold() == repo.casefold()]
    effective_topic_id = _text((topic or {}).get("topic_id") or target.get("topic_id")).casefold()
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
        effective_topic_id = _text((topic or {}).get("topic_id") or target.get("topic_id")).casefold()
        if not topic and effective_topic_id in {"", NEW_UNMAPPED_DOMAIN.lower()}:
            repo = _clean_target_name(target.get("target_repo"))
            fallback = next(
                (
                    session for session in sessions
                    if session["session_id"] == session_id
                    and (not repo or _clean_target_name(session.get("target_repo")).casefold() == repo.casefold())
                ),
                None,
            )
            if fallback:
                return fallback
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        active = [session for session in candidates if _text(session.get("status"), "idle").casefold() != "idle"]
        return active[0] if active else candidates[0]
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
        result = module.jev_decide(state, questions, agent_role="team-lead", goal_id=goal_id, mock_response=None if mock is None else mock)
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
        else:
            proposal = _proposed_domain(target, message, domain_map)
            target["topic_id"] = proposal["topic_id"]
            target["domain_status"] = NEW_DOMAIN_CANDIDATE
            target["proposed_domain"] = proposal
            candidates.append(proposal)
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
    request_input = {"user_message": message, "active_m2_sessions": sessions}
    system = (
        "You are the unified M2 ingress semantic decomposer. Return only strict JSON, with no markdown or prose. "
        "Rewrite each target's sub_prompt as a complete executable instruction for that M2. "
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
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
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
    route, targets, metadata = _extract_jev_plan(semantic, sessions, message, domain_map, jev)
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
        if session.get("source") != "herdr" or _text(session.get("status"), "idle").casefold() != "idle":
            continue
        pane_id = _text(session.get("pane_id"))
        if not pane_id or pane_id == current_pane or pane_id == "w1:p1":
            continue
        is_primary_idle = session.get("session_id") == primary_id or not idle_primary_assigned
        reason = "idle_existing_m2" if is_primary_idle else "duplicate_m2"
        idle_primary_assigned = True
        candidates.append({"session_id": session.get("session_id", ""), "pane_id": pane_id, "title": session.get("title", ""), "status": session.get("status", "idle"), "target_repo": session.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": reason})
    return candidates


def build_plan(
    message: str,
    sessions: list[dict[str, Any]],
    warnings: Iterable[str] = (),
    *,
    domain_map: dict[str, Any] | None = None,
    domain_map_file: str | Path | None = None,
    mock_magpie: Any = None,
    targets_json: Any = None,
    magpie_url: str = "",
    magpie_model: str = "",
    mock_jev: Any = None,
    transport: str = "auto",
) -> dict[str, Any]:
    message = _text(message)
    if not message:
        raise RouterError("message is required")
    plan_id = f"m2p-{uuid.uuid4().hex[:10]}"
    loaded_domain_map = domain_map or load_domain_map(domain_map_file)
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
    )
    stale_candidates: list[dict[str, Any]] = []
    for target in targets:
        if transport != "auto" and target["m2_status"] == "EXISTING_M2":
            target["transport"] = transport
        elif transport != "auto":
            target["transport"] = transport
        target.setdefault("context_pass", {"enabled": bool(target.get("rag_context_hint")), "hint": target.get("rag_context_hint")})
        target["cleanup_candidates"] = _stale_candidates(target, sessions, loaded_domain_map)
        stale_candidates.extend(target["cleanup_candidates"])
    unique_stale = {item["session_id"]: item for item in stale_candidates}
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "message": message,
        "decision": {"route": route, **decision},
        "domain_map_file": loaded_domain_map.get("path", str(domain_map_file or DEFAULT_DOMAIN_MAP_PATH)),
        "discovery": {"sessions": sessions, "warnings": list(warnings)},
        "waves": sorted({target["wave_order"] for target in targets}),
        "targets": targets,
        "new_domain_candidates": decision.get("new_domain_candidates", []),
        "stale_m2_candidates": list(unique_stale.values()),
        "approval": {"required": True, "approved": False},
    }


def preview_text(plan: dict[str, Any]) -> str:
    lines = [f"Plan {plan['plan_id']} · {plan['decision']['route']}", "Owner approval required before dispatch.", "", "wave | topic_id | domain status | target repo | M2 status | transport | content"]
    lines.append("-----|----------|---------------|-------------|-----------|-----------|--------")
    for target in plan.get("targets", []):
        marker = " [NEW_DOMAIN_CANDIDATE]" if target.get("domain_status") == NEW_DOMAIN_CANDIDATE else ""
        lines.append(f"{target.get('wave_order', 1)} | {target.get('topic_id', '')} | {target.get('domain_status', '')}{marker} | {target.get('target_repo', 'unspecified')} | {target.get('m2_status')} | {target.get('transport', 'auto')} | {target.get('sub_prompt', '')}")
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
    created = False
    if target.get("m2_status") == "NEW_M2":
        created_body = poster(base + "/m2-omni-hub/api/create-session", {"topic": target.get("target_repo", "New M2"), "cwd": target.get("cwd") or os.environ.get("DSH_M2_CWD", "")}, token=token)
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
        return {"ok": True, "method": "dsh.session-sink", "target": session_id, "created": created, "receipt": receipt}
    except Exception:
        if created:
            raise
        receipt = poster(base + "/m2-omni-hub/api/dispatch", {"message": prompt, "targetSessionIds": [session_id], "forceMode": "single"}, token=token)
        return {"ok": True, "method": "dsh.omni-hub.dispatch", "target": session_id, "created": False, "receipt": receipt}


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


def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, index: int = 1, used_names: set[str] | None = None) -> dict[str, Any]:
    binary = _herdr_binary(target)
    if target.get("m2_status") == "EXISTING_M2":
        agent = _text(target.get("herdr_target") or target.get("session_id"))
        if not agent:
            raise RouterError("Herdr target has no agent name")
        result = runner([binary, "agent", "prompt", agent, prompt], timeout=10.0)
        if result.returncode != 0:
            raise RouterError(f"Herdr prompt failed for {agent}: {result.stderr.strip()}")
        return {"ok": True, "method": "herdr.agent.prompt", "target": agent, "created": False, "stdout": result.stdout.strip()}

    name = _new_herdr_agent_name(target, index=index, used_names=used_names)
    dispatch = _text(target.get("dispatch_script") or os.environ.get("M2_ROUTER_HERDR_DISPATCH"))
    if not dispatch:
        dispatch = str(Path.home() / "Development" / "user-skills" / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh")
    args = [dispatch, "--name", name, "--kind", _text(target.get("kind"), "copilot"), "--cwd", _text(target.get("cwd"), str(Path.home() / "Development")), "--prompt", prompt]
    pane = _text(target.get("source_pane") or os.environ.get("HERDR_PANE_ID"))
    if pane:
        args.extend(["--pane", pane, "--direction", "right"])
    else:
        args.append("--new-workspace")
    result = runner(args, timeout=30.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr new M2 failed for {name}: {result.stderr.strip()}")
    parsed = _parse_json_output(result.stdout)
    reused = bool(isinstance(parsed, dict) and parsed.get("reused_existing"))
    if reused and _text(parsed.get("agent_status")).casefold() == "working":
        prompt_res = runner([binary, "agent", "prompt", name, prompt], timeout=10.0)
        if prompt_res.returncode != 0:
            raise RouterError(f"Herdr prompt failed for reused working agent {name}: {prompt_res.stderr.strip()}")
    return {"ok": True, "method": "herdr-dispatch", "target": name, "created": not reused, "receipt": parsed or result.stdout.strip()}


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
        topic = {key: candidate.get(key) for key in ("topic_id", "group", "title", "target_repo", "cwd", "keywords", "aliases")}
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
    if live_status != "idle":
        return False, f"pane_not_idle:{live_status or 'unknown'}"
    return True, "ok"


def _close_herdr_pane(
    candidate: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    dry_run: bool = False,
) -> dict[str, Any]:
    pane_id = _text(candidate.get("pane_id"))
    if not pane_id or pane_id == _text(os.environ.get("HERDR_PANE_ID")) or pane_id == "w1:p1":
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
        candidate_dry_run = dry_run or bool(dry_run_sessions and session_id in dry_run_sessions)
        receipts.append({"session_id": session_id, "reason": candidate.get("reason"), **_close_herdr_pane(candidate, runner=runner, dry_run=candidate_dry_run)})
    return receipts


def apply_plan(plan: dict[str, Any], *, approve: bool = False, register_new_domains: bool = False, cleanup_duplicates: bool = False, replace_stale_m2: bool = False, domain_map_file: str | Path | None = None, transport: str = "auto", runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
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
    cleanup_mode = "replace" if replace_stale_m2 else "duplicates" if cleanup_duplicates else "none"
    cleanup_receipts = _cleanup_stale(plan, cleanup_mode, runner=runner, dry_run=is_stdout_only, dry_run_sessions=stdout_candidate_sessions)
    closed_sessions = {item.get("session_id") for item in cleanup_receipts if item.get("ok") and not item.get("skipped") and not item.get("simulated")}
    used_herdr_names: set[str] = set()
    receipts: list[dict[str, Any]] = []
    target_seq = 0
    for wave in sorted({int(item.get("wave_order", 1)) for item in targets}):
        for target in [item for item in targets if int(item.get("wave_order", 1)) == wave]:
            target_seq += 1
            dispatch_target = dict(target)
            initial_transport = _resolve_target_transport(dispatch_target, plan, transport)
            target_is_stdout = is_stdout_only or initial_transport == "stdout"
            if replace_stale_m2 and target.get("cleanup_candidates"):
                candidate_ids = {_text(c.get("session_id")) for c in target.get("cleanup_candidates", []) if isinstance(c, dict)}
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
                receipt = _apply_herdr(dispatch_target, plan, prompt, runner=runner, index=target_seq, used_names=used_herdr_names)
            elif selected == "dsh":
                receipt = _apply_dsh(dispatch_target, plan, prompt, poster=poster)
            else:
                raise RouterError(f"unsupported transport: {selected}")
            receipts.append({"wave_order": wave, "target_repo": target.get("target_repo"), **receipt})
    return {"ok": True, "plan_id": plan.get("plan_id"), "approved": True, "registered_new_domains": registered, "cleanup_receipts": cleanup_receipts, "receipts": receipts}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and owner-gated dispatch for a unified M2 ingress")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="build and print a dispatch preview")
    mode.add_argument("--apply", action="store_true", help="apply a previously saved plan")
    parser.add_argument("--message")
    parser.add_argument("--message-file")
    parser.add_argument("--plan-file")
    parser.add_argument("--sessions-json", help="combined discovery fixture")
    parser.add_argument("--herdr-json", help="combined Herdr discovery fixture")
    parser.add_argument("--herdr-pane-json", "--pane-json", dest="herdr_pane_json", help="Herdr pane list fixture")
    parser.add_argument("--herdr-agent-json", "--agent-json", dest="herdr_agent_json", help="Herdr agent list fixture")
    parser.add_argument("--dsh-state-json")
    parser.add_argument("--dsh-state-url", default=os.environ.get("DSH_M2_STATE_URL", ""))
    parser.add_argument("--herdr-bin")
    parser.add_argument("--dsh-token", default=os.environ.get("DSH_M2_TOKEN", ""))
    parser.add_argument("--transport", choices=("auto", "herdr", "dsh", "stdout"), default="auto")
    parser.add_argument("--magpie-url", default=os.environ.get("MAGPIE_BASE_URL", MAGPIE_BASE_URL))
    parser.add_argument("--magpie-model", default=os.environ.get("MAGPIE_MODEL", MAGPIE_DEFAULT_MODEL))
    parser.add_argument("--domain-map-file", default=str(DEFAULT_DOMAIN_MAP_PATH))
    parser.add_argument("--register-new-domains", action="store_true", help="register approved new domain candidates during --apply")
    parser.add_argument("--cleanup-duplicates", action="store_true", help="close safe duplicate idle Herdr panes during --apply")
    parser.add_argument("--replace-stale-m2", action="store_true", help="close safe idle stale panes and spawn a fresh M2 during --apply")
    parser.add_argument("--targets-json", help="target list/object supplied by an outer CLI model")
    parser.add_argument("--mock-magpie-json", help="offline Magpie response fixture")
    parser.add_argument("--mock-jev-json", help="legacy Jev fixture alias")
    parser.add_argument("--approve", "--yes", action="store_true", help="explicitly approve dispatch")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--no-herdr", action="store_true")
    parser.add_argument("--no-dsh", action="store_true")
    return parser


def _message(args: argparse.Namespace) -> str:
    if args.message is not None:
        return args.message
    if args.message_file:
        return Path(args.message_file).expanduser().read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.plan:
            payload = _json_value(args.sessions_json) if args.sessions_json else None
            herdr_payload = _json_value(args.herdr_json) if args.herdr_json else payload
            herdr_pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            herdr_agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else None
            dsh_payload = _json_value(args.dsh_state_json) if args.dsh_state_json else payload
            sessions, warnings = discover_sessions(herdr_payload=herdr_payload, herdr_pane_payload=herdr_pane_payload, herdr_agent_payload=herdr_agent_payload, dsh_payload=dsh_payload, herdr_bin=args.herdr_bin, dsh_state_url=args.dsh_state_url, dsh_token=args.dsh_token, include_herdr=not args.no_herdr, include_dsh=not args.no_dsh)
            plan = build_plan(
                _message(args),
                sessions,
                warnings,
                domain_map_file=args.domain_map_file,
                mock_magpie=_json_value(args.mock_magpie_json) if args.mock_magpie_json else None,
                targets_json=_json_value(args.targets_json) if args.targets_json else None,
                magpie_url=args.magpie_url,
                magpie_model=args.magpie_model,
                mock_jev=_json_value(args.mock_jev_json) if args.mock_jev_json else None,
                transport=args.transport,
            )
            if args.dsh_state_url:
                plan["discovery"]["dsh_state_url"] = args.dsh_state_url
            output: Any = plan if args.json else preview_text(plan)
        else:
            if not args.plan_file:
                raise RouterError("--apply requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            output = apply_plan(plan, approve=args.approve, register_new_domains=args.register_new_domains, cleanup_duplicates=args.cleanup_duplicates, replace_stale_m2=args.replace_stale_m2, domain_map_file=args.domain_map_file, transport=args.transport)
        print(json.dumps(output, ensure_ascii=False, indent=None if args.json else 2, sort_keys=True) if isinstance(output, dict) else output)
        return 0
    except (OSError, ValueError, RouterError, json.JSONDecodeError) as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"m2-router: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
