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
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
DEFAULT_M2_MODEL = "gemini-4-argon"
DEFAULT_M2_CONTEXT = "long_context"
DEFAULT_M2_MODEL_LABEL = "Gemini 4 Argon · 983K context"
ACTIVE_STATUSES = {"working", "running", "idle", "done", "blocked", "needs_input", "ready"}
HERDR_TERMINAL_STATUSES = {"cancelled", "canceled", "stopped", "archived", "exited", "dead"}
TERMINAL_STATUSES = {"done", *HERDR_TERMINAL_STATUSES}
ROUTES = {"ROUTE_SINGLE", "ROUTE_CROSS_SESSION", "CREATE_NEW_M2"}
NEW_UNMAPPED_DOMAIN = "NEW_UNMAPPED_DOMAIN"
MAPPED_DOMAIN = "MAPPED_DOMAIN"
NEW_DOMAIN_CANDIDATE = "NEW_DOMAIN_CANDIDATE"
MAGPIE_BASE_URL = "http://127.0.0.1:3425"
MAGPIE_DEFAULT_MODEL = "group/steering"
DEFAULT_DOMAIN_MAP_PATH = Path(__file__).resolve().parents[1] / "references" / "m2-domain-map.json"
HERDR_BACKGROUND_RE = re.compile(r"(?:^|[-_])(?:goal-tl|writer|reviewer|explorer|pr-monitor)(?:[-_]|$)|/worktrees(?:/|$)", re.I)
HERDR_INTERACTIVE_AGENTS = {"copilot", "claude", "codex", "pi", "cindy"}
SAFE_BASELINE_ROOTS = (
    "~/Development",
    "/Volumes/SSD",
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    "~/.copilot",
    "~/.agents",
    "~/.codex",
    "~/orca",
)
WHITELISTED_CMD_BINARIES = {
    "gh",
    "git",
    "python3",
    "jq",
    "herdr",
    "orca",
    "shasum",
    "date",
    "sleep",
    "env",
    "ls",
    "cat",
    "head",
    "tail",
    "rg",
    "grep",
    "find",
}
FORBIDDEN_GIT_SUBCOMMANDS = {"commit", "push", "merge", "rebase", "reset", "checkout"}
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class RouterError(RuntimeError):
    """A user-actionable planning or dispatch error."""


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _expand_cwd(raw_cwd: Any) -> str:
    text = _text(raw_cwd)
    if not text:
        return ""
    if text.startswith("~"):
        return str(Path(text).expanduser())
    return text


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
    pane_id = _text(value.get("pane_id") or value.get("paneId"))
    if status in (HERDR_TERMINAL_STATUSES if source == "herdr" and allow_interactive_herdr else TERMINAL_STATUSES):
        return None
    if source == "herdr" and allow_interactive_herdr and status == "done" and not pane_id:
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
        "cwd": _expand_cwd(value.get("foreground_cwd") or value.get("cwd") or value.get("worktree")),
        "workspace_id": _text(value.get("workspace_id") or value.get("workspaceId")),
        "workspace_label": _text(value.get("workspace_label") or value.get("workspaceLabel")),
        "pane_id": pane_id,
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


def _workspace_records(payload: Any) -> list[dict[str, Any]]:
    records = _records(payload)
    if isinstance(payload, dict) and isinstance(payload.get("workspaces"), list):
        records = [item for item in payload["workspaces"] if isinstance(item, dict)]
    elif isinstance(payload, dict) and isinstance(payload.get("result"), dict) and isinstance(payload["result"].get("workspaces"), list):
        records = [item for item in payload["result"]["workspaces"] if isinstance(item, dict)]
    normalized = []
    for item in records:
        workspace_id = _text(item.get("workspace_id") or item.get("workspaceId") or item.get("id"))
        label = _text(item.get("label") or item.get("name") or item.get("title"))
        if not workspace_id:
            continue
        normalized.append({
            "workspace_id": workspace_id,
            "label": label,
            "active_tab_id": _text(item.get("active_tab_id") or item.get("activeTabId")),
            "tab_count": item.get("tab_count", item.get("tabCount", 0)),
            "pane_count": item.get("pane_count", item.get("paneCount", 0)),
            "agent_status": _text(item.get("agent_status") or item.get("agentStatus") or item.get("status")),
        })
    return normalized


def _workspace_map(workspaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_text(item.get("workspace_id")): item for item in workspaces if _text(item.get("workspace_id"))}


def resolve_target_space(target: dict[str, Any], topic: dict[str, Any] | None = None, workspaces: list[dict[str, Any]] | None = None, domain_map: dict[str, Any] | None = None) -> dict[str, Any]:
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    source = topic if topic is not None else proposed
    space = _text((source or {}).get("space") or target.get("space") or target.get("target_repo"), "M2")
    aliases = list((source or {}).get("space_aliases") or target.get("space_aliases") or [])
    aliases = [_text(value) for value in aliases if _text(value)]
    target_id = "" if topic is not None else _text(target.get("target_workspace_id"))
    inventory = workspaces or []
    if not target_id:
        labels = {space.casefold()}
        for alias in aliases:
            labels.add(alias.casefold())
        for item in inventory:
            label = _text(item.get("label"))
            if label and label.casefold() in labels and (label.casefold() != "大总管" or space.casefold() == "大总管"):
                target_id = _text(item.get("workspace_id"))
                if label.casefold() == space.casefold():
                    break
    target["space"] = space
    target["space_aliases"] = aliases
    target["target_workspace_id"] = target_id
    return target


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
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
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
    if workspace_payload is None:
        workspace_payload = herdr_workspace_payload
    workspace_records = _workspace_records(workspace_payload) if workspace_payload is not None else []
    if isinstance(payload, dict) and not workspace_records:
        workspace_records = _workspace_records(payload)
    if pane_payload is None and agent_payload is None and payload is None:
        binary = herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr")
        if not binary:
            return [], None
        try:
            pane_result = runner([binary, "pane", "list"], timeout=3.0)
            agent_result = runner([binary, "agent", "list"], timeout=3.0)
            try:
                workspace_result = runner([binary, "workspace", "list"], timeout=3.0)
                workspace_records = _workspace_records(_parse_json_output(workspace_result.stdout)) if workspace_result.returncode == 0 else []
            except (OSError, subprocess.SubprocessError):
                workspace_records = []
        except (OSError, subprocess.SubprocessError) as error:
            return [], f"herdr discovery failed: {error}"
        failures = [result for result in (pane_result, agent_result) if result.returncode != 0]
        if len(failures) == 2:
            return [], f"herdr discovery exited {failures[0].returncode}/{failures[1].returncode}"
        pane_records = _records(_parse_json_output(pane_result.stdout)) if pane_result.returncode == 0 else []
        agent_records = _records(_parse_json_output(agent_result.stdout)) if agent_result.returncode == 0 else []
    workspace_by_id = _workspace_map(workspace_records)
    records = _merge_herdr_records(pane_records, agent_records)
    for item in records:
        ws = _text(item.get("workspace_id") or item.get("workspaceId"))
        if ws and ws in workspace_by_id:
            item["workspace_label"] = workspace_by_id[ws].get("label", "")
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
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
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
        found, warning = discover_herdr(payload=herdr_payload, pane_payload=herdr_pane_payload, agent_payload=herdr_agent_payload, workspace_payload=workspace_payload, herdr_workspace_payload=herdr_workspace_payload, herdr_bin=herdr_bin, runner=runner)
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
    candidates = [session for session in sessions if repo and _clean_target_name(session.get("target_repo")).casefold() == repo.casefold()]
    mapped_space = _text((topic or {}).get("space") or target.get("space") or target.get("target_repo"))
    aliases = {_text(value).casefold() for value in ((topic or {}).get("space_aliases") or target.get("space_aliases") or []) if _text(value)}
    allowed_spaces = {mapped_space.casefold(), *aliases}
    target_workspace_id = _text(target.get("target_workspace_id"))
    filtered = []
    for session in candidates:
        label = _text(session.get("workspace_label")).casefold()
        ws_id = _text(session.get("workspace_id"))
        if label and label not in allowed_spaces and (target_workspace_id and ws_id != target_workspace_id or not target_workspace_id):
            continue
        if target_workspace_id and ws_id and ws_id != target_workspace_id and label and label not in allowed_spaces:
            continue
        filtered.append(session)
    candidates = filtered
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
            target["space"] = _text(topic.get("space")) or _text(topic.get("target_repo"))
            target["space_aliases"] = list(topic.get("space_aliases") or [])
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
        if session.get("source") != "herdr" or _text(session.get("status"), "idle").casefold() not in {"idle", "done"}:
            continue
        pane_id = _text(session.get("pane_id"))
        if not pane_id or pane_id == current_pane or pane_id == "w1:p1":
            continue
        is_primary_idle = session.get("session_id") == primary_id or not idle_primary_assigned
        reason = "idle_existing_m2" if is_primary_idle else "duplicate_m2"
        idle_primary_assigned = True
        candidates.append({"session_id": session.get("session_id", ""), "pane_id": pane_id, "title": session.get("title", ""), "status": session.get("status", "idle"), "target_repo": session.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": reason})
    return candidates


def _default_preview_path(plan_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", _text(plan_id, "m2p-plan")).strip("-") or "m2p-plan"
    return Path(f"/tmp/m2-plan-{safe_id}.md")


def render_preview_markdown(plan: dict[str, Any]) -> str:
    plan_id = _text(plan.get("plan_id"), "m2p-unknown")
    route = _text((plan.get("decision") or {}).get("route"), "ROUTE_SINGLE")
    default_label = _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL)
    lines = [
        f"# M2 Dispatch Plan · {plan_id} ({route})",
        "",
        f"- **Message**: {_text(plan.get('message'))}",
        f"- **Route**: `{route}`",
        f"- **Default Model / Context**: `{default_label}` (`{_text(plan.get('default_model'), DEFAULT_M2_MODEL)}` / `{_text(plan.get('default_context'), DEFAULT_M2_CONTEXT)}`)",
        "- **Owner Gate**: Owner approval required before dispatch.",
        "",
        "## Target Dispatch Table",
        "",
        "| Wave | Topic | Domain Status | Target Repo | M2 Status | Transport | Model / Context | CWD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for target in plan.get("targets", []):
        model_label = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        cwd = _expand_cwd(target.get("cwd"))
        lines.append(
            f"| {target.get('wave_order', 1)} | `{_text(target.get('topic_id'))}` | "
            f"`{_text(target.get('domain_status'))}` | `{_text(target.get('target_repo'), 'unspecified')}` | "
            f"`{_text(target.get('m2_status'))}` | `{_text(target.get('transport'), 'auto')}` | "
            f"`{model_label}` | `{cwd}` |"
        )
    lines.extend(["", "## Target Instructions"])
    for index, target in enumerate(plan.get("targets", []), start=1):
        lines.extend([
            "",
            f"### {index}. Wave {target.get('wave_order', 1)} · `{_text(target.get('topic_id'))}` → `{_text(target.get('target_repo'), 'unspecified')}` ({_text(target.get('m2_status'))})",
            f"- **Model / Context**: `{_text(target.get('model_label'), DEFAULT_M2_MODEL_LABEL)}` (`{_text(target.get('model'), DEFAULT_M2_MODEL)}` / `{_text(target.get('context'), DEFAULT_M2_CONTEXT)}`)",
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


def open_preview_in_orca(
    preview_path: str | Path,
    *,
    orca_bin: str | None = None,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    resolved_path = str(Path(preview_path).expanduser())
    binary = _text(orca_bin or os.environ.get("ORCA_BIN") or shutil.which("orca"), "orca")
    command = [binary, "file", "open", resolved_path, "--focus"]
    try:
        result = runner(command, timeout=5.0)
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "ok": False,
            "path": resolved_path,
            "command": command,
            "returncode": -1,
            "warning": f"orca preview open failed: {error}",
        }
    returncode = int(getattr(result, "returncode", 1))
    if returncode != 0:
        stderr = _text(getattr(result, "stderr", "")) or _text(getattr(result, "stdout", ""))
        return {
            "ok": False,
            "path": resolved_path,
            "command": command,
            "returncode": returncode,
            "warning": stderr or f"orca exited {returncode}",
        }
    return {
        "ok": True,
        "path": resolved_path,
        "command": command,
        "returncode": 0,
        "stdout": _text(getattr(result, "stdout", "")),
    }


def build_plan(
    message: str,
    sessions: list[dict[str, Any]],
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
    write_preview_md: bool = True,
    open_preview: bool = False,
    orca_bin: str | None = None,
    runner: CommandRunner = _run_command,
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
    workspace_inventory = _workspace_records(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        resolve_target_space(target, topic, workspace_inventory, loaded_domain_map)
        if isinstance(target.get("proposed_domain"), dict):
            resolve_target_space(target, None, workspace_inventory, loaded_domain_map)
    stale_candidates: list[dict[str, Any]] = []
    for target in targets:
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
        target.setdefault("context_pass", {"enabled": bool(target.get("rag_context_hint")), "hint": target.get("rag_context_hint")})
        target["cleanup_candidates"] = _stale_candidates(target, sessions, loaded_domain_map)
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
        "discovery": {"sessions": sessions, "workspaces": workspace_inventory, "warnings": list(warnings)},
        "waves": sorted({target["wave_order"] for target in targets}),
        "targets": targets,
        "new_domain_candidates": decision.get("new_domain_candidates", []),
        "stale_m2_candidates": list(unique_stale.values()),
        "approval": {"required": True, "approved": False},
    }
    plan["preview_markdown"] = render_preview_markdown(plan)
    preview_path = Path(preview_md_file).expanduser() if preview_md_file else _default_preview_path(plan_id)
    if write_preview_md or preview_md_file is not None or open_preview:
        preview_path = write_preview_markdown(plan, preview_path)
    else:
        plan["preview_markdown_path"] = str(preview_path)
    if open_preview:
        plan["preview_open"] = open_preview_in_orca(preview_path, orca_bin=orca_bin, runner=runner)
    else:
        plan["preview_open"] = {"ok": False, "skipped": True, "path": str(preview_path)}
    return plan


def preview_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Plan {plan['plan_id']} · {plan['decision']['route']}",
        f"Default Model / Context: {_text(plan.get('default_model_label'), DEFAULT_M2_MODEL_LABEL)}",
    ]
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
        lines.append(f"{target.get('wave_order', 1)} | {target.get('topic_id', '')} | {target.get('space', 'M2')} ({target.get('target_workspace_id') or 'new-space'}) | {target.get('domain_status', '')}{marker} | {target.get('target_repo', 'unspecified')} | {target.get('m2_status')} | {target.get('transport', 'auto')} | {target.get('sub_prompt', '')}")
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


def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, index: int = 1, used_names: set[str] | None = None, created_workspaces: dict[str, str] | None = None) -> dict[str, Any]:
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
    mapped_space = _text(target.get("space") or (target.get("proposed_domain") or {}).get("space") or target.get("target_repo"), "M2")
    space_key = mapped_space.casefold()
    discovery_workspaces = (plan.get("discovery") or {}).get("workspaces") or []
    workspace_id = _text(target.get("target_workspace_id"))
    if not workspace_id and created_workspaces:
        workspace_id = _text(created_workspaces.get(space_key))
    if not workspace_id:
        for item in discovery_workspaces:
            label = _text(item.get("label"))
            if label.casefold() == space_key or label.casefold() in {_text(v).casefold() for v in target.get("space_aliases", [])}:
                workspace_id = _text(item.get("workspace_id"))
                if label.casefold() == space_key:
                    break
    if not workspace_id and "workspaces" not in (plan.get("discovery") or {}):
        try:
            ws_result = runner([binary, "workspace", "list"], timeout=3.0)
            if ws_result.returncode == 0:
                for item in _workspace_records(_parse_json_output(ws_result.stdout)):
                    if _text(item.get("label")).casefold() == space_key:
                        workspace_id = _text(item.get("workspace_id"))
                        break
        except (OSError, subprocess.SubprocessError):
            pass
    args = [dispatch, "--name", name, "--kind", _text(target.get("kind"), "copilot"), "--cwd", expanded_cwd, "--prompt", prompt]
    spawn_mode = "workspace_tab" if workspace_id else "new_workspace"
    if workspace_id:
        args.extend(["--workspace", workspace_id, "--tab-label", name])
    else:
        args.extend(["--new-workspace", "--workspace-label", mapped_space])
    args.extend(["--", "--model", model, "--context", context])
    result = runner(args, timeout=30.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr new M2 failed for {name}: {result.stderr.strip()}")
    parsed = _parse_json_output(result.stdout)
    reused = bool(isinstance(parsed, dict) and parsed.get("reused_existing"))
    resolved_ws = _text((parsed or {}).get("workspace_id")) if isinstance(parsed, dict) else ""
    if resolved_ws and created_workspaces is not None:
        created_workspaces[space_key] = resolved_ws
    if reused and _text((parsed or {}).get("agent_status")).casefold() == "working":
        prompt_res = runner([binary, "agent", "prompt", name, prompt], timeout=10.0)
        if prompt_res.returncode != 0:
            raise RouterError(f"Herdr prompt failed for reused working agent {name}: {prompt_res.stderr.strip()}")
    return {"ok": True, "method": "herdr-dispatch", "target": name, "created": not reused, "model": model, "context": context, "model_label": model_label, "cwd": expanded_cwd, "space": mapped_space, "workspace_id": resolved_ws or workspace_id, "spawn_mode": spawn_mode, "receipt": parsed or result.stdout.strip()}


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


def apply_plan(plan: dict[str, Any], *, approve: bool = False, register_new_domains: bool = False, cleanup_duplicates: bool = False, replace_stale_m2: bool = False, domain_map_file: str | Path | None = None, plan_file: str | Path | None = None, apply_receipt_file: str | Path | None = None, transport: str = "auto", runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
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
    created_workspaces: dict[str, str] = {}
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
                receipt = _apply_herdr(dispatch_target, plan, prompt, runner=runner, index=target_seq, used_names=used_herdr_names, created_workspaces=created_workspaces)
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
            receipts.append({
                "wave_order": wave,
                "topic_id": _text(target.get("topic_id")),
                "target_repo": target.get("target_repo"),
                "pane_id": pane_id_val,
                "session_id": session_id_val,
                **receipt,
            })
    plan_id = _text(plan.get("plan_id"), "m2p-plan")
    safe_plan_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", plan_id).strip("-") or "m2p-plan"
    resolved_plan_file = _text(plan_file or plan.get("plan_file"), f"/tmp/m2-plan-{safe_plan_id}.json")
    resolved_receipt_path = Path(apply_receipt_file).expanduser() if apply_receipt_file else Path(f"/tmp/m2-apply-{safe_plan_id}.json")
    watch_cmd = (
        f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
        f"--watch --plan-file {shlex.quote(resolved_plan_file)} "
        f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
        f"--interval 15 --max-wait 2400 --json"
    )
    payload: dict[str, Any] = {
        "ok": True,
        "plan_id": plan.get("plan_id"),
        "approved": True,
        "apply_receipt_path": str(resolved_receipt_path),
        "registered_new_domains": registered,
        "cleanup_receipts": cleanup_receipts,
        "receipts": receipts,
        "watchdog_cmd": watch_cmd,
        "watchdog_copilot_cli": {
            "tool": "bash",
            "mode": "async",
            "detach": True,
            "shellId": f"watch-m2-{plan_id}",
            "description": f"Watch dispatched target M2 sessions ({plan_id}) until quiescent",
            "command": watch_cmd,
        },
    }
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


def _path_is_under_safe_roots(candidate_path: str, safe_roots: list[str]) -> bool:
    raw = _text(candidate_path)
    if not raw or "\n" in raw or "\r" in raw or "\x00" in raw:
        return False
    if re.search(r"[*?\[\]{}$`<>|;]", raw):
        return False
    parts = [p for p in re.split(r"[/\\]+", raw) if p]
    if ".." in parts or "." in parts:
        return False
    expanded = _expand_cwd(raw)
    if not expanded or not os.path.isabs(expanded):
        return False

    expanded_roots = [os.path.abspath(_expand_cwd(r)).rstrip("/") for r in safe_roots if _expand_cwd(r)]
    if not expanded_roots:
        return False
    if not _lexical_within(expanded, expanded_roots):
        return False

    resolved_roots: list[str] = []
    for r in expanded_roots:
        try:
            resolved_roots.append(str(Path(r).resolve(strict=False)).rstrip("/"))
        except OSError:
            continue
    if not resolved_roots:
        return False

    cand_obj = Path(os.path.abspath(expanded))
    if not _resolved_within(cand_obj, resolved_roots):
        return False

    # Walk every prefix from deepest allowed lexical root down to candidate to catch symlinks escaping safe roots.
    matching_lex_roots = [
        r for r in expanded_roots
        if os.path.abspath(expanded).rstrip("/") == r or os.path.abspath(expanded).startswith(r + "/")
    ]
    base_lex = max(matching_lex_roots, key=len)
    base_path = Path(base_lex)
    try:
        rel_parts = cand_obj.relative_to(base_path).parts
    except ValueError:
        return False

    cur = base_path
    if cur.is_symlink() and not _resolved_within(cur, resolved_roots):
        return False
    for part in rel_parts:
        cur = cur / part
        try:
            is_link = cur.is_symlink()
            exists = cur.exists()
        except OSError:
            return False
        if is_link or exists:
            if not _resolved_within(cur, resolved_roots):
                return False
            if is_link:
                try:
                    link_target = os.readlink(cur)
                except OSError:
                    return False
                resolved_target = (cur.parent / link_target) if not os.path.isabs(link_target) else Path(link_target)
                if not _resolved_within(resolved_target, resolved_roots):
                    return False
    return True


def _extract_single_dialog_path(frame_text: str) -> str | None:
    """Extract the exact single path from a path/folder permission prompt, or None if ambiguous."""
    patterns = (
        re.compile(r"(?:Allow\s+(?:path|directory)\s+access\s+to|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|Confirm\s+folder\s+trust\s*:?|Trust\s+folder\s*:?|access\s+to)\s+([^\n?]+?)\??\s*$", re.I),
        re.compile(r"^(?:Path|Directory|Folder)\s*:\s*([^\n]+?)\s*$", re.I),
    )
    extracted: list[str] = []
    for raw_line in frame_text.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            continue
        for pat in patterns:
            m = pat.search(line)
            if m:
                candidate = m.group(1).strip()
                if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"', "`"}:
                    candidate = candidate[1:-1].strip()
                else:
                    candidate = candidate.rstrip("?").strip()
                    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"', "`"}:
                        candidate = candidate[1:-1].strip()
                if candidate and candidate not in extracted:
                    extracted.append(candidate)
                break
    if len(extracted) != 1:
        return None
    single = extracted[0]
    if not (single.startswith("/") or single.startswith("~/") or single == "~"):
        return None
    # Reject multiple unquoted path tokens in the same captured span
    if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", single):
        return None
    if any(ch in single for ch in ("*", "?", "[", "]", "$", "`", ";", "|", "&", "<", ">", '"', "'")):
        return None
    return single


_READONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "worktree",
    "branch",
}
_SAFE_GIT_BRANCH_FLAGS = {"--show-current", "-a", "--all", "-r", "--remotes", "--list"}
_FORBIDDEN_GIT_FLAGS = (
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--upload-pack",
    "--receive-pack",
    "--ext-diff",
    "--textconv",
    "--output",
    "--paginate",
    "-p",
)


def _is_safe_git_command(args: list[str]) -> bool:
    idx = 0
    while idx < len(args):
        tok = args[idx]
        if tok in {"--no-pager", "-P", "--no-optional-locks"}:
            idx += 1
            continue
        if tok == "-C":
            if idx + 1 >= len(args):
                return False
            if not _path_is_under_safe_roots(args[idx + 1], _safe_roots_for_target(include_target_cwd=False)):
                return False
            idx += 2
            continue
        if tok.startswith("-"):
            return False
        break
    if idx >= len(args):
        return False
    subcmd = args[idx]
    rest = args[idx + 1 :]
    if subcmd not in _READONLY_GIT_SUBCOMMANDS:
        return False
    for flag in rest:
        if any(flag == p or flag.startswith(p + "=") for p in _FORBIDDEN_GIT_FLAGS):
            return False
    if subcmd == "worktree":
        return rest == ["list"] or rest == ["list", "--porcelain"]
    if subcmd == "branch":
        if not rest:
            return False
        return all(tok in _SAFE_GIT_BRANCH_FLAGS for tok in rest)
    return True


def _is_safe_gh_command(args: list[str]) -> bool:
    if len(args) < 2:
        return False
    group, action = args[0], args[1]
    rest = args[2:]
    allowed = {
        "pr": {"view", "list", "status", "checks"},
        "run": {"view", "list"},
        "issue": {"view", "list"},
    }
    if action not in allowed.get(group, set()):
        return False
    for flag in rest:
        if flag in {"-w", "--web"} or flag.startswith("--output"):
            return False
    return True


def _is_safe_herdr_command(args: list[str]) -> bool:
    if len(args) < 2:
        return False
    group, action = args[0], args[1]
    if group == "agent" and action in {"list", "get", "read", "wait"}:
        return True
    if group == "pane" and action == "list" and len(args) == 2:
        return True
    if group == "workspace" and action == "list" and len(args) == 2:
        return True
    return False


def _is_safe_python_command(args: list[str]) -> bool:
    if len(args) == 1 and args[0] in {"--version", "-V", "--help", "-h"}:
        return True
    if len(args) >= 2 and args[0] == "-m" and args[1] == "unittest":
        for tok in args[2:]:
            if tok in {"-c", "-k"}:
                continue
            if tok.startswith("-"):
                if tok not in {"-v", "--verbose", "-q", "--quiet", "-f", "--failfast", "-b", "--buffer", "discover", "-s", "-p", "-t"}:
                    return False
            elif ".." in tok or tok.startswith("/") or tok.startswith("~"):
                return False
        return True
    return False


def _is_whitelisted_command_segment(segment: str) -> bool:
    tokens = shlex.split(segment.strip()) if segment.strip() else []
    if not tokens:
        return False
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        return False
    if "/" in tokens[0] and Path(tokens[0]).parent not in {Path("/bin"), Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin")}:
        return False
    binary = Path(tokens[0]).name
    if binary not in WHITELISTED_CMD_BINARIES:
        return False
    args = tokens[1:]
    if binary == "env":
        return len(args) == 0
    if binary == "git":
        return _is_safe_git_command(args)
    if binary == "gh":
        return _is_safe_gh_command(args)
    if binary == "herdr":
        return _is_safe_herdr_command(args)
    if binary == "orca":
        if len(args) != 4 or args[:2] != ["file", "open"] or "--focus" not in args[2:]:
            return False
        file_args = [a for a in args[2:] if a != "--focus"]
        if len(file_args) != 1 or file_args[0].startswith("-"):
            return False
        return _path_is_under_safe_roots(file_args[0], _safe_roots_for_target(include_target_cwd=False))
    if binary == "python3":
        return _is_safe_python_command(args)
    if binary == "find":
        forbidden_find = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
        return not any(tok.lower() in forbidden_find or tok.lower().startswith("-fprint") or tok.lower().startswith("-fls") for tok in args)
    if binary == "jq":
        return not any(tok in {"-f", "--from-file", "-L"} for tok in args)
    if binary in {"rg", "grep"}:
        return not any(tok == "--pre" or tok.startswith("--pre=") or tok == "--pre-glob" or tok.startswith("--pre-glob=") for tok in args)
    if binary == "sleep":
        return len(args) == 1 and bool(re.match(r"^\d+(?:\.\d+)?$", args[0]))
    if binary in {"date", "shasum", "ls", "cat", "head", "tail"}:
        return not any(tok in {"-s", "--set"} for tok in args)
    return False


def _extract_command_block_from_frame(frame_text: str) -> str | None:
    """Extract the full command block from an active command confirmation frame, or None if multiline/ambiguous."""
    lines = [ln.strip() for ln in frame_text.splitlines() if ln.strip()]
    cmd_lines: list[str] = []
    collecting = False
    for line in lines:
        if re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            collecting = False
            continue
        if re.match(
            r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
            r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command)\b",
            line,
            re.I,
        ):
            m_inline = re.match(
                r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)\s*:\s*(.+)$",
                line,
                re.I,
            )
            if m_inline:
                rest = m_inline.group(1).strip()
                if not re.match(r"^(?:Do\s+you|Allow|Confirm|Run\s+command|Execute\s+command)\b", rest, re.I):
                    cmd_lines.append(rest.strip("`"))
                    collecting = True
            continue
        m_cmd = re.match(r"^(?:Command|Run|Execute|Shell|bash|sh)\s*:\s*(.+)$", line, re.I)
        if m_cmd:
            cmd_lines.append(m_cmd.group(1).strip().strip("`"))
            collecting = True
            continue
        if line.startswith("$ "):
            cmd_lines.append(line[2:].strip().strip("`"))
            collecting = True
            continue
        if collecting:
            # Any extra non-choice line after Command: makes it a multiline command block -> reject
            cmd_lines.append(line)
    if len(cmd_lines) != 1:
        return None
    return cmd_lines[0]


def _is_whitelisted_command(cmd_text: str) -> bool:
    stripped = cmd_text.strip()
    if not stripped or "\n" in stripped or "\r" in stripped or "\\" in stripped:
        return False
    # Reject shell chaining, pipes, redirections, command/process substitutions, subshells, backgrounding, or globs
    if re.search(r"[`<>()$*{}\[\];|&]", stripped):
        return False
    try:
        return _is_whitelisted_command_segment(stripped)
    except ValueError:
        return False


_PROMPT_HEADER_RE = re.compile(
    r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|\bCurate\s+Memory\b|"
    r"\bstore_memory\b|\bvote_memory\b|Confirm\s+folder\s+trust|"
    r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|\bCMD_CONFIRM\b|"
    r"Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
    r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command|"
    r"\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)
_BUSINESS_DECISION_RE = re.compile(
    r"(?:\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)
_COMPLETION_BOUNDARY_RE = re.compile(
    r"^(?:Completed\.|Done\.|Finished\.|[✓✔✗✘]\s+|\bExit\s+code\b|---+$|═+$|━+$)",
    re.I,
)


def _extract_active_bottom_frame(visible_text: str) -> str:
    """Slice only the bottom-most active interactive frame after the last output/completion boundary."""
    lines = [ln.rstrip() for ln in _text(visible_text).splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-20:]
    last_boundary_idx = -1
    for idx, raw in enumerate(tail):
        stripped = raw.strip()
        if _COMPLETION_BOUNDARY_RE.search(stripped):
            last_boundary_idx = idx
    if last_boundary_idx >= 0 and last_boundary_idx + 1 < len(tail):
        tail = tail[last_boundary_idx + 1 :]
    # Also if there are multiple prompt headers separated by non-choice output, slice from the last header/question
    header_indices = [
        idx for idx, raw in enumerate(tail)
        if _PROMPT_HEADER_RE.search(raw) or (raw.strip().endswith("?") and not re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", raw.strip()))
    ]
    if len(header_indices) > 1:
        # Check if there are multiple distinct prompt headers -> if separated or conflicting, return full tail so ambiguity check rejects it
        pass
    return "\n".join(tail)


def _validate_apply_receipt_for_target(
    plan: dict[str, Any],
    apply_receipt: dict[str, Any] | None,
    *,
    target_ref: str = "",
    pane_id: str = "",
) -> bool:
    if not isinstance(apply_receipt, dict):
        return False
    if apply_receipt.get("ok") is not True or apply_receipt.get("approved") is not True:
        return False
    plan_id = _text(plan.get("plan_id"))
    receipt_plan_id = _text(apply_receipt.get("plan_id"))
    if not plan_id or receipt_plan_id != plan_id:
        return False
    receipts = apply_receipt.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        return False
    for rec in receipts:
        if not isinstance(rec, dict) or rec.get("ok") is not True:
            continue
        method = _text(rec.get("method"))
        if method == "stdout":
            continue
        rec_target = _text(rec.get("target"))
        rec_pane = _text(
            rec.get("pane_id")
            or ((rec.get("receipt") or {}).get("pane_id") if isinstance(rec.get("receipt"), dict) else "")
        )
        if target_ref and rec_target and target_ref == rec_target:
            return True
        if pane_id and rec_pane and pane_id == rec_pane:
            return True
    return False


def _classify_and_unblock_dialog(
    visible_text: str,
    *,
    target_ref: str,
    target_cwd: str = "",
    live_status: str = "blocked",
    auto_unblock_enabled: bool = False,
    herdr_bin: str = "herdr",
    runner: CommandRunner = _run_command,
    already_unblocked_signatures: set[str] | None = None,
) -> dict[str, Any]:
    """Classify the bottom-most active dialog frame and auto-unblock only when authorized."""
    text = _text(visible_text)
    if not text:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    if _text(live_status).lower() not in {"blocked", "needs_input"}:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    active_frame = _extract_active_bottom_frame(text)
    if not active_frame:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    # Business / Spec ask_user or custom decision questions must NEVER be auto-unblocked.
    if _BUSINESS_DECISION_RE.search(active_frame):
        return {
            "action": "owner_decision",
            "kind": "ask_user",
            "keys": [],
            "blocked_reason": "owner_decision_required",
            "auto_unblock_enabled": auto_unblock_enabled,
        }

    baseline_roots = _safe_roots_for_target(include_target_cwd=False)
    safe_roots = _safe_roots_for_target(target_cwd, include_target_cwd=True)

    is_cmd_prompt = bool(
        re.search(
            r"(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
            r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command)",
            active_frame,
            re.I,
        )
    )
    is_path_prompt = bool(
        re.search(r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|\baccess\s+to\b)", active_frame, re.I)
    )
    is_folder_trust_prompt = bool(
        re.search(
            r"(?:Confirm\s+folder\s+trust|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|Trust\s+folder|Yes,\s*I\s+trust)",
            active_frame,
            re.I,
        )
    )
    is_curate_memory_prompt = bool(
        re.search(r"(?:Curate\s+Memory|\bstore_memory\b|\bvote_memory\b)", active_frame, re.I)
        and re.search(r"(?:\?|[>❯●◉▶]|\bAllow\b|\bConfirm\b|\bYes\b|1[.)])", active_frame, re.I)
    )

    # Also detect any unrecognized question lines in active_frame alongside a dialog header
    other_questions = [
        ln.strip()
        for ln in active_frame.splitlines()
        if ln.strip().endswith("?")
        and not re.search(
            r"(?:Allow\s+(?:path|directory)\s+access|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|"
            r"Confirm\s+folder\s+trust|Trust\s+folder|Curate\s+Memory|store_memory|vote_memory|"
            r"CMD_CONFIRM|Do\s+you\s+want\s+to\s+run|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)",
            ln,
            re.I,
        )
    ]
    matched_kinds = sum(bool(flag) for flag in (is_cmd_prompt, is_path_prompt, is_folder_trust_prompt, is_curate_memory_prompt))
    if matched_kinds > 1 or (matched_kinds == 1 and other_questions):
        return {
            "action": "owner_decision",
            "kind": "ambiguous_dialog",
            "keys": [],
            "blocked_reason": "owner_decision_required",
            "auto_unblock_enabled": auto_unblock_enabled,
        }

    kind = ""
    keys: list[str] = []

    if is_cmd_prompt:
        cmd_block = _extract_command_block_from_frame(active_frame)
        if cmd_block is not None and _is_whitelisted_command(cmd_block):
            kind = "whitelisted_cmd_confirm"
            keys = ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "non_whitelisted_cmd",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_path_prompt:
        single_path = _extract_single_dialog_path(active_frame)
        if single_path is not None and _path_is_under_safe_roots(single_path, safe_roots):
            kind = "allow_path_access"
            keys = ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "unsafe_path_access",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_folder_trust_prompt:
        single_path = _extract_single_dialog_path(active_frame)
        has_any_path_like = bool(re.search(r"(?:^|[\s'\"`:])(?:~|/)[A-Za-z0-9_.-]", active_frame))
        if single_path is not None:
            if _path_is_under_safe_roots(single_path, baseline_roots):
                kind = "confirm_folder_trust"
                keys = ["enter"]
            else:
                return {
                    "action": "owner_decision",
                    "kind": "untrusted_folder",
                    "keys": [],
                    "blocked_reason": "owner_decision_required",
                    "auto_unblock_enabled": auto_unblock_enabled,
                }
        elif not has_any_path_like and target_cwd and _path_is_under_safe_roots(target_cwd, baseline_roots):
            kind = "confirm_folder_trust"
            keys = ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "untrusted_folder",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_curate_memory_prompt:
        kind = "curate_memory"
        keys = ["enter"]

    if not kind:
        if re.search(r"(?:\?|\bconfirm\b|\bapprove\b|\bchoose\b|\bselect\b|\[y/n\])", active_frame, re.I):
            return {
                "action": "owner_decision",
                "kind": "owner_prompt",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    if not auto_unblock_enabled:
        return {
            "action": "owner_decision",
            "kind": kind,
            "keys": [],
            "blocked_reason": "passive_watch_no_verified_apply_receipt",
            "auto_unblock_enabled": False,
        }

    signature = f"{kind}:{hashlib.sha256(active_frame.encode('utf-8')).hexdigest()[:12]}"
    if already_unblocked_signatures is not None and signature in already_unblocked_signatures:
        return {"action": "already_unblocked", "kind": kind, "keys": keys, "blocked_reason": "", "auto_unblock_enabled": True}

    cmd = [herdr_bin, "agent", "send-keys", target_ref, *keys]
    res = runner(cmd, timeout=5.0)
    ok = getattr(res, "returncode", 1) == 0
    if ok and already_unblocked_signatures is not None:
        already_unblocked_signatures.add(signature)
    return {
        "action": "auto_unblocked" if ok else "unblock_failed",
        "ok": ok,
        "kind": kind,
        "keys": keys,
        "command": cmd,
        "target": target_ref,
        "blocked_reason": "" if ok else "unblock_send_keys_failed",
        "auto_unblock_enabled": True,
    }


def _summarize_visible_lines(text: str, *, max_lines: int = 8) -> str:
    lines = [line.rstrip() for line in _text(text).splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])[:800]


def _read_herdr_visible(binary: str, target_ref: str, *, runner: CommandRunner = _run_command, lines: int = 40) -> str:
    if not target_ref:
        return ""
    try:
        res = runner([binary, "agent", "read", target_ref, "--source", "visible", "--lines", str(lines)], timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(res, "returncode", 1) != 0:
        return ""
    stdout = _text(getattr(res, "stdout", ""))
    parsed = _parse_json_output(stdout)
    if isinstance(parsed, dict):
        for key in ("visible", "visible_text", "text", "content", "output"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val
        result = parsed.get("result")
        if isinstance(result, dict):
            for key in ("visible", "visible_text", "text", "content", "output"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    return stdout


def watch_dispatched_m2(
    plan: dict[str, Any],
    *,
    apply_receipt: dict[str, Any] | None = None,
    interval: float = 15.0,
    max_wait: float = 2400.0,
    max_polls: int | None = None,
    herdr_bin: str | None = None,
    dsh_state_url: str = "",
    dsh_token: str = "",
    runner: CommandRunner = _run_command,
    fetcher: Callable[..., Any] = _fetch_json,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll dispatched target M2s until quiescent, auto-unblocking safe Allow dialogs only with verified apply_receipt."""
    binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    plan_targets = [dict(t) for t in (plan.get("targets") or []) if isinstance(t, dict)]
    receipt_items = [dict(r) for r in ((apply_receipt or {}).get("receipts") or []) if isinstance(r, dict)]
    has_apply_receipt = bool(receipt_items)
    receipt_header_valid = bool(
        isinstance(apply_receipt, dict)
        and apply_receipt.get("ok") is True
        and apply_receipt.get("approved") is True
        and _text(plan.get("plan_id"))
        and _text(apply_receipt.get("plan_id")) == _text(plan.get("plan_id"))
    )

    monitored: list[dict[str, Any]] = []
    if has_apply_receipt:
        used_plan_indices: set[int] = set()
        for idx, rec in enumerate(receipt_items):
            plan_match: dict[str, Any] = {}
            for p_idx, pt in enumerate(plan_targets):
                if p_idx in used_plan_indices:
                    continue
                if _text(rec.get("topic_id")) and _text(pt.get("topic_id")) == _text(rec.get("topic_id")) and int(rec.get("wave_order") or 1) == int(pt.get("wave_order") or 1):
                    plan_match = pt
                    used_plan_indices.add(p_idx)
                    break
            if not plan_match and idx < len(plan_targets) and idx not in used_plan_indices:
                plan_match = plan_targets[idx]
                used_plan_indices.add(idx)
            method = _text(rec.get("method"))
            transport = "stdout" if method == "stdout" else ("dsh" if method.startswith("dsh") else "herdr")
            target_ref = _text(rec.get("target") or rec.get("session_id"))
            pane_id = _text(
                rec.get("pane_id")
                or ((rec.get("receipt") or {}).get("pane_id") if isinstance(rec.get("receipt"), dict) else "")
            )
            target_unblock_enabled = bool(
                receipt_header_valid
                and rec.get("ok") is True
                and transport == "herdr"
                and (target_ref or pane_id)
                and _validate_apply_receipt_for_target(plan, apply_receipt, target_ref=target_ref, pane_id=pane_id)
            )
            monitored.append({
                "wave_order": int(rec.get("wave_order") or plan_match.get("wave_order") or 1),
                "topic_id": _text(rec.get("topic_id") or plan_match.get("topic_id")),
                "target_repo": _text(rec.get("target_repo") or plan_match.get("target_repo"), "unspecified"),
                "target": target_ref,
                "pane_id": pane_id,
                "session_id": _text(rec.get("session_id") or target_ref),
                "transport": transport,
                "strict_receipt_identity": True,
                "auto_unblock_enabled": target_unblock_enabled,
                "cwd": _expand_cwd(rec.get("cwd") or plan_match.get("cwd")),
                "status": "done" if transport == "stdout" else "unknown",
                "needs_owner_attention": False,
                "blocked_reason": "",
                "auto_unblocked": [],
                "visible_summary": _text(rec.get("prompt"))[:240] if transport == "stdout" else "",
                "seen_working": False,
                "initial_seq": None,
                "completion_seq": None,
                "state_change_seq": None,
                "unblocked_signatures": set(),
            })
    else:
        for idx, pt in enumerate(plan_targets, start=1):
            transport = _resolve_target_transport(pt, plan, _text(pt.get("transport"), "auto"))
            target_ref = _text(pt.get("herdr_target") or pt.get("session_id"))
            if not target_ref and transport == "herdr":
                target_ref = _new_herdr_agent_name(pt, index=idx)
            monitored.append({
                "wave_order": int(pt.get("wave_order") or 1),
                "topic_id": _text(pt.get("topic_id")),
                "target_repo": _text(pt.get("target_repo"), "unspecified"),
                "target": target_ref or _text(pt.get("target_repo"), "unspecified"),
                "pane_id": _text(pt.get("pane_id")),
                "session_id": _text(pt.get("session_id") or target_ref),
                "transport": transport,
                "strict_receipt_identity": False,
                "auto_unblock_enabled": False,
                "cwd": _expand_cwd(pt.get("cwd")),
                "status": "done" if transport == "stdout" else "unknown",
                "needs_owner_attention": False,
                "blocked_reason": "",
                "auto_unblocked": [],
                "visible_summary": "",
                "seen_working": False,
                "initial_seq": None,
                "completion_seq": None,
                "state_change_seq": None,
                "unblocked_signatures": set(),
            })

    effective_dsh_url = _text(dsh_state_url or (plan.get("discovery") or {}).get("dsh_state_url") or os.environ.get("DSH_M2_STATE_URL"))
    started = time.monotonic()
    polls = 0
    while True:
        needs_herdr = any(item["transport"] == "herdr" for item in monitored)
        needs_dsh = any(item["transport"] == "dsh" for item in monitored)
        herdr_merged: list[dict[str, Any]] = []
        if needs_herdr:
            try:
                pane_res = runner([binary, "pane", "list"], timeout=5.0)
                agent_res = runner([binary, "agent", "list"], timeout=5.0)
                pane_records = _records(_parse_json_output(getattr(pane_res, "stdout", ""))) if getattr(pane_res, "returncode", 1) == 0 else []
                agent_records = _records(_parse_json_output(getattr(agent_res, "stdout", ""))) if getattr(agent_res, "returncode", 1) == 0 else []
                herdr_merged = _merge_herdr_records(pane_records, agent_records)
            except (OSError, subprocess.SubprocessError):
                herdr_merged = []
        dsh_sessions: list[dict[str, Any]] = []
        if needs_dsh and effective_dsh_url:
            dsh_sessions, _ = discover_dsh(state_url=effective_dsh_url, token=dsh_token, fetcher=fetcher)

        all_settled = True
        for item in monitored:
            if item["transport"] == "stdout":
                continue
            if item["transport"] == "dsh":
                if item["strict_receipt_identity"]:
                    expected_sid = _text(item["session_id"] or item["target"])
                    match = next(
                        (s for s in dsh_sessions if expected_sid and _text(s.get("session_id")) == expected_sid),
                        None,
                    )
                else:
                    match = next(
                        (
                            s for s in dsh_sessions
                            if (_text(item["target"]) and _text(s.get("session_id")) == _text(item["target"]))
                        ),
                        None,
                    )
                if match is None:
                    item["status"] = "missing"
                    all_settled = False
                    continue
                status = _text(match.get("status"), "idle").lower()
                item["status"] = status
                item["visible_summary"] = _text(match.get("summary")) or item["visible_summary"]
                if status in {"working", "running"}:
                    item["seen_working"] = True
                    item["needs_owner_attention"] = False
                    item["blocked_reason"] = ""
                    all_settled = False
                elif status in {"blocked", "needs_input"}:
                    item["needs_owner_attention"] = True
                    item["blocked_reason"] = "owner_decision_required"
                elif status in {"idle", "done"}:
                    item["needs_owner_attention"] = False
                    item["blocked_reason"] = ""
                    if not item["seen_working"] and polls == 0 and (max_polls is None or max_polls > 1):
                        all_settled = False
                else:
                    all_settled = False
                continue

            # Herdr target: match strictly by applied target/pane identity (never by repo fallback)
            expected_target = _text(item["target"])
            expected_pane = _text(item["pane_id"])
            live = None
            if expected_target or expected_pane:
                live = next(
                    (
                        rec for rec in herdr_merged
                        if (expected_target and _text(rec.get("name") or rec.get("herdr_target") or rec.get("id")) == expected_target)
                        or (expected_pane and _text(rec.get("pane_id") or rec.get("paneId")) == expected_pane)
                        or (expected_target and _session_id(rec) == expected_target)
                    ),
                    None,
                )
            if live is None:
                item["status"] = "missing"
                all_settled = False
                continue
            item["pane_id"] = _text(live.get("pane_id") or live.get("paneId")) or item["pane_id"]
            status = _text(live.get("agent_status") or live.get("status"), "idle").lower()
            comp_seq = live.get("completion_seq", live.get("completionSeq"))
            state_seq = live.get("state_change_seq", live.get("stateChangeSeq"))
            seq_tuple = (comp_seq, state_seq)
            if item["initial_seq"] is None:
                item["initial_seq"] = seq_tuple
            item["completion_seq"] = comp_seq
            item["state_change_seq"] = state_seq
            seq_advanced = item["initial_seq"] != (None, None) and seq_tuple != item["initial_seq"]

            visible = _read_herdr_visible(binary, item["target"] or item["pane_id"], runner=runner, lines=40)
            if not visible:
                visible = _text(live.get("visible") or live.get("visible_text") or live.get("summary"))
            if visible:
                item["visible_summary"] = _summarize_visible_lines(visible)

            if status in {"working", "running"}:
                item["seen_working"] = True
                item["status"] = status
                item["needs_owner_attention"] = False
                item["blocked_reason"] = ""
                all_settled = False
                continue

            # S3 & S4: Only inspect/unblock dialogs when target's live status is blocked or needs_input
            if status in {"blocked", "needs_input"}:
                decision = _classify_and_unblock_dialog(
                    visible,
                    target_ref=item["target"] or item["pane_id"],
                    target_cwd=item["cwd"],
                    live_status=status,
                    auto_unblock_enabled=bool(item["auto_unblock_enabled"]),
                    herdr_bin=binary,
                    runner=runner,
                    already_unblocked_signatures=item["unblocked_signatures"],
                )
                if decision["action"] == "auto_unblocked":
                    item["auto_unblocked"].append({
                        "kind": decision["kind"],
                        "keys": decision["keys"],
                        "target": decision["target"],
                    })
                    item["seen_working"] = True
                    item["status"] = "working"
                    item["needs_owner_attention"] = False
                    item["blocked_reason"] = ""
                    all_settled = False
                    continue
                item["status"] = status
                item["needs_owner_attention"] = True
                item["blocked_reason"] = decision.get("blocked_reason") or "owner_decision_required"
                continue

            item["status"] = status
            item["needs_owner_attention"] = False
            item["blocked_reason"] = ""
            if status in {"idle", "done"}:
                if status == "idle" and not item["seen_working"] and not seq_advanced and polls == 0 and (max_polls is None or max_polls > 1) and (time.monotonic() - started < max_wait):
                    all_settled = False
            else:
                all_settled = False

        polls += 1
        if all_settled:
            break
        if max_polls is not None and polls >= max_polls:
            break
        if (time.monotonic() - started) >= max_wait:
            break
        sleep_fn(max(0.0, float(interval)))

    # F1: Any target still missing/unknown at deadline is unresolved and requires Owner attention
    target_reports: list[dict[str, Any]] = []
    for item in monitored:
        if item["status"] in {"missing", "unknown"}:
            item["status"] = "missing"
            item["needs_owner_attention"] = True
            item["blocked_reason"] = "target_missing_or_unreachable"
        target_reports.append({
            "wave_order": item["wave_order"],
            "topic_id": item["topic_id"],
            "target_repo": item["target_repo"],
            "target": item["target"],
            "pane_id": item["pane_id"],
            "status": item["status"],
            "auto_unblock_enabled": bool(item["auto_unblock_enabled"]),
            "needs_owner_attention": bool(item["needs_owner_attention"]),
            "blocked_reason": item["blocked_reason"],
            "auto_unblocked": list(item["auto_unblocked"]),
            "visible_summary": item["visible_summary"],
            "completion_seq": item["completion_seq"],
            "state_change_seq": item["state_change_seq"],
        })

    any_missing = any(t["status"] == "missing" for t in target_reports)
    any_owner_attention = any(t["needs_owner_attention"] for t in target_reports)
    settled = (not any_missing) and all(
        t["status"] in {"idle", "done"} or (t["status"] in {"blocked", "needs_input"} and t["needs_owner_attention"])
        for t in target_reports
    )
    overall_ok = settled and not any_missing
    any_auto_unblock = any(t["auto_unblock_enabled"] for t in target_reports)
    summary_lines = [
        f"### M2 Watch Summary · `{_text(plan.get('plan_id'), 'm2p-unknown')}`",
        f"- **All Settled**: `{settled}` · **Needs Owner Attention**: `{any_owner_attention}` · **Auto Unblock Enabled**: `{any_auto_unblock}` · **Polls**: `{polls}`",
    ]
    for t in target_reports:
        unblocked_kinds = ",".join(u["kind"] for u in t["auto_unblocked"]) or "none"
        attn = f" · **NEEDS OWNER ({t['blocked_reason']})**" if t["needs_owner_attention"] else ""
        summary_lines.append(
            f"- Wave {t['wave_order']} `{t['topic_id']}` (`{t['target_repo']}` → `{t['target']}`): "
            f"status=`{t['status']}` · auto_unblocked=`{unblocked_kinds}`{attn}"
        )
        if t["visible_summary"]:
            first_line = t["visible_summary"].splitlines()[-1].strip()
            summary_lines.append(f"  - Latest: `{first_line[:160]}`")
    return {
        "ok": overall_ok,
        "plan_id": _text(plan.get("plan_id")),
        "polls": polls,
        "auto_unblock_enabled": any_auto_unblock,
        "passive_only": not any_auto_unblock,
        "all_settled": settled,
        "needs_owner_attention": any_owner_attention,
        "targets": target_reports,
        "summary_markdown": "\n".join(summary_lines),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and owner-gated dispatch for a unified M2 ingress")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="build and print a dispatch preview")
    mode.add_argument("--apply", action="store_true", help="apply a previously saved plan")
    mode.add_argument("--watch", action="store_true", help="watch dispatched target M2 sessions until quiescent")
    parser.add_argument("--message")
    parser.add_argument("--message-file")
    parser.add_argument("--plan-file")
    parser.add_argument("--apply-receipt-file", help="optional apply receipt JSON file for --watch")
    parser.add_argument("--preview-md-file", help="optional markdown file path for --plan preview")
    parser.add_argument("--no-open-preview", action="store_true", help="do not invoke orca file open --focus during --plan")
    parser.add_argument("--orca-bin", help="optional path to orca binary")
    parser.add_argument("--interval", type=float, default=15.0, help="polling interval in seconds for --watch")
    parser.add_argument("--max-wait", type=float, default=2400.0, help="max wait in seconds for --watch")
    parser.add_argument("--max-polls", type=int, default=None, help="optional max poll iterations for --watch")
    parser.add_argument("--sessions-json", help="combined discovery fixture")
    parser.add_argument("--herdr-json", help="combined Herdr discovery fixture")
    parser.add_argument("--herdr-pane-json", "--pane-json", dest="herdr_pane_json", help="Herdr pane list fixture")
    parser.add_argument("--herdr-agent-json", "--agent-json", dest="herdr_agent_json", help="Herdr agent list fixture")
    parser.add_argument("--herdr-workspace-json", dest="herdr_workspace_json", help="Herdr workspace list fixture")
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
            herdr_workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            dsh_payload = _json_value(args.dsh_state_json) if args.dsh_state_json else payload
            sessions, warnings = discover_sessions(herdr_payload=herdr_payload, herdr_pane_payload=herdr_pane_payload, herdr_agent_payload=herdr_agent_payload, workspace_payload=herdr_workspace_payload, dsh_payload=dsh_payload, herdr_bin=args.herdr_bin, dsh_state_url=args.dsh_state_url, dsh_token=args.dsh_token, include_herdr=not args.no_herdr, include_dsh=not args.no_dsh)
            plan = build_plan(
                _message(args),
                sessions,
                warnings,
                domain_map_file=args.domain_map_file,
                workspace_payload=herdr_workspace_payload,
                mock_magpie=_json_value(args.mock_magpie_json) if args.mock_magpie_json else None,
                targets_json=_json_value(args.targets_json) if args.targets_json else None,
                magpie_url=args.magpie_url,
                magpie_model=args.magpie_model,
                mock_jev=_json_value(args.mock_jev_json) if args.mock_jev_json else None,
                transport=args.transport,
                preview_md_file=args.preview_md_file,
                write_preview_md=True,
                open_preview=not args.no_open_preview,
                orca_bin=args.orca_bin,
            )
            if args.dsh_state_url:
                plan["discovery"]["dsh_state_url"] = args.dsh_state_url
            output: Any = plan if args.json else preview_text(plan)
        elif args.apply:
            if not args.plan_file:
                raise RouterError("--apply requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            output = apply_plan(plan, approve=args.approve, register_new_domains=args.register_new_domains, cleanup_duplicates=args.cleanup_duplicates, replace_stale_m2=args.replace_stale_m2, domain_map_file=args.domain_map_file, plan_file=args.plan_file, apply_receipt_file=args.apply_receipt_file, transport=args.transport)
        else:
            if not args.plan_file:
                raise RouterError("--watch requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            apply_receipt = _json_value(args.apply_receipt_file) if args.apply_receipt_file else None
            if apply_receipt is not None and not isinstance(apply_receipt, dict):
                raise RouterError("apply receipt file must contain a JSON object")
            watch_result = watch_dispatched_m2(
                plan,
                apply_receipt=apply_receipt,
                interval=args.interval,
                max_wait=args.max_wait,
                max_polls=args.max_polls,
                herdr_bin=args.herdr_bin,
                dsh_state_url=args.dsh_state_url,
                dsh_token=args.dsh_token,
            )
            output = watch_result if args.json else watch_result["summary_markdown"]
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
