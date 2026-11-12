#!/usr/bin/env python3
"""Unified M2 ingress: classify, preview, and owner-gated dispatch.

The router deliberately separates planning from delivery.  ``--plan`` only reads
session discovery sources and Jev; ``--apply`` requires an explicit approval flag
and sends only the targets recorded in the plan.
"""

from __future__ import annotations

import argparse
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
    parts = re.split(r"\s*(?:，|,|;|；)\s*(?=(?:然后|再让|再由|随后|之后|then|next|after that|and then)\b)|\s+(?=(?:然后|再让|再由|随后|之后|then|next|after that)\b)", message, flags=re.I)
    clauses: list[tuple[int, str]] = []
    wave = 1
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if re.match(r"^(?:然后|再让|再由|随后|之后|then|next|after that|and then)\b", text, re.I):
            wave += 1
        text = re.sub(r"^(?:先|首先|first|initially)\s*", "", text, flags=re.I)
        clauses.append((wave, text))
    return clauses or [(1, message)]


def _clean_target_name(value: Any) -> str:
    return _text(value).strip(" `[](){}<>\"'。，,;；:：")


def _session_target_aliases(sessions: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
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
    return aliases, sorted(aliases, key=len, reverse=True)


def _deterministic_targets(message: str, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract known or explicitly named repos and soft waves without Jev targets."""
    clauses = _split_ordered_clauses(message)
    alias_map, known_aliases = _session_target_aliases(sessions)
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
        for name in canonical_names:
            targets.append({"target_repo": name, "sub_prompt": clause, "wave_order": wave})
    return targets


def _extract_jev_plan(decision: dict[str, Any], sessions: list[dict[str, Any]], message: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    answers = _decision_answers(decision)
    route = _text(answers.get("route_mode") or answers.get("route") or decision.get("route"))
    if isinstance(answers.get("route_mode"), dict):
        route = _text(answers["route_mode"].get("choice"))
    if route not in ROUTES:
        route = _route_from_message(message, sessions)

    raw_targets = _target_value(decision)
    used_deterministic_fallback = False
    if not raw_targets:
        raw_targets = _deterministic_targets(message, sessions)
        used_deterministic_fallback = bool(raw_targets)
        if len(raw_targets) > 1 or _route_from_message(message, sessions) == "ROUTE_CROSS_SESSION":
            route = "ROUTE_CROSS_SESSION"
    if not raw_targets:
        primary = answers.get("primary_target")
        if isinstance(primary, dict):
            primary = primary.get("choice")
        if primary and primary != "NEW_SESSION":
            raw_targets = [{"session_id": primary, "sub_prompt": message}]
        elif route == "CREATE_NEW_M2":
            raw_targets = [{"sub_prompt": message}]

    targets: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_targets):
        if isinstance(raw, str):
            raw = {"target_repo": raw}
        if not isinstance(raw, dict):
            continue
        target = dict(raw)
        target.setdefault("sub_prompt", message)
        target.setdefault("wave_order", target.get("order", target.get("wave", index + 1)))
        target.setdefault("rag_context_hint", target.get("context_pass") or target.get("rag_hint"))
        session_id = _text(target.get("session_id") or target.get("sessionId"))
        match = next((s for s in sessions if session_id and s["session_id"] == session_id), None)
        if not match:
            repo = _repo_from(target)
            match = next((s for s in sessions if repo and s["target_repo"].lower() == repo.lower()), None)
        if match:
            target.update({"session_id": match["session_id"], "target_repo": target.get("target_repo") or match["target_repo"], "source": match["source"], "transport": match["source"], "cwd": match["cwd"], "herdr_target": match["herdr_target"], "pane_id": match["pane_id"], "workspace_id": match["workspace_id"], "title": match["title"], "base_url": match.get("base_url", ""), "m2_status": "EXISTING_M2"})
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

    if route == "ROUTE_SINGLE" and len(targets) > 1:
        targets = targets[:1]
    if route == "ROUTE_CROSS_SESSION" and not targets:
        ranked = sorted(sessions, key=lambda item: _score(message, item), reverse=True)
        targets = [{"session_id": item["session_id"], "target_repo": item["target_repo"], "sub_prompt": message, "wave_order": index + 1, "rag_context_hint": None, "m2_status": "EXISTING_M2", "source": item["source"], "transport": item["source"], "herdr_target": item["herdr_target"], "cwd": item["cwd"], "pane_id": item["pane_id"], "workspace_id": item["workspace_id"], "title": item["title"]} for index, item in enumerate(ranked[:3])]
        if not targets:
            targets = [{"target_repo": "unspecified", "sub_prompt": message, "wave_order": 1, "rag_context_hint": None, "m2_status": "NEW_M2", "transport": "auto"}]
    if not targets:
        targets = [{"target_repo": "unspecified", "sub_prompt": message, "wave_order": 1, "rag_context_hint": None, "m2_status": "NEW_M2", "transport": "auto"}]
    targets.sort(key=lambda item: (item["wave_order"], item.get("target_repo", "")))
    return route, targets, {"source": "deterministic_fallback" if used_deterministic_fallback or not decision else "jev", "model": _text(decision.get("model"), "microsoft-decision-1"), "confidence": answers.get("route_mode", {}).get("confidence", decision.get("confidence", 0.0)) if isinstance(answers.get("route_mode"), dict) else decision.get("confidence", 0.0), "reason": _text(decision.get("reason"))}


def classify(message: str, sessions: list[dict[str, Any]], *, mock: Any = None, goal_id: str = "") -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    decision: dict[str, Any] | None = None
    try:
        jev_path = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
        spec = importlib.util.spec_from_file_location("m2_router_jev_client", jev_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        state = {"user_message": message, "active_m2_sessions": sessions, "routing_contract": {"target_fields": ["target_repo", "m2_status", "sub_prompt", "wave_order", "rag_context_hint"]}}
        questions = {
            "route_mode": {"type": "choice", "instructions": "Choose ROUTE_SINGLE, ROUTE_CROSS_SESSION, or CREATE_NEW_M2.", "criteria": {route: route for route in sorted(ROUTES)}},
            "targets": {"type": "noul", "instructions": "Return a JSON list of target_repo, sub_prompt, wave_order, and rag_context_hint entries."},
        }
        decision = module.jev_decide(state, questions, agent_role="team-lead", goal_id=goal_id, mock_response=mock)
        if not isinstance(decision, dict):
            decision = None
    except (OSError, ImportError, TypeError, AssertionError):
        decision = None
    return _extract_jev_plan(decision or {}, sessions, message)


def build_plan(message: str, sessions: list[dict[str, Any]], warnings: Iterable[str] = (), *, mock_jev: Any = None, transport: str = "auto") -> dict[str, Any]:
    message = _text(message)
    if not message:
        raise RouterError("message is required")
    plan_id = f"m2p-{uuid.uuid4().hex[:10]}"
    route, targets, decision = classify(message, sessions, mock=mock_jev, goal_id=plan_id)
    for target in targets:
        if transport != "auto" and target["m2_status"] == "EXISTING_M2":
            target["transport"] = transport
        elif transport != "auto":
            target["transport"] = transport
        target.setdefault("context_pass", {"enabled": bool(target.get("rag_context_hint")), "hint": target.get("rag_context_hint")})
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "message": message,
        "decision": {"route": route, **decision},
        "discovery": {"sessions": sessions, "warnings": list(warnings)},
        "waves": sorted({target["wave_order"] for target in targets}),
        "targets": targets,
        "approval": {"required": True, "approved": False},
    }


def preview_text(plan: dict[str, Any]) -> str:
    lines = [f"Plan {plan['plan_id']} · {plan['decision']['route']}", "Owner approval required before dispatch.", "", "wave | target repo | M2 status | transport | content"]
    lines.append("-----|-------------|-----------|-----------|--------")
    for target in plan.get("targets", []):
        lines.append(f"{target.get('wave_order', 1)} | {target.get('target_repo', 'unspecified')} | {target.get('m2_status')} | {target.get('transport', 'auto')} | {target.get('sub_prompt', '')}")
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


def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command) -> dict[str, Any]:
    binary = _herdr_binary(target)
    if target.get("m2_status") == "EXISTING_M2":
        agent = _text(target.get("herdr_target") or target.get("session_id"))
        if not agent:
            raise RouterError("Herdr target has no agent name")
        result = runner([binary, "agent", "prompt", agent, prompt], timeout=10.0)
        if result.returncode != 0:
            raise RouterError(f"Herdr prompt failed for {agent}: {result.stderr.strip()}")
        return {"ok": True, "method": "herdr.agent.prompt", "target": agent, "created": False, "stdout": result.stdout.strip()}

    repo_slug = re.sub(r"[^a-z0-9_-]+", "-", _text(target.get("target_repo"), "m2").lower()).strip("-") or "m2"
    name = _text(target.get("agent_name"), f"m2-{repo_slug[:25]}-r1")[:32].rstrip("-")
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
    return {"ok": True, "method": "herdr-dispatch", "target": name, "created": True, "receipt": _parse_json_output(result.stdout) or result.stdout.strip()}


def apply_plan(plan: dict[str, Any], *, approve: bool = False, transport: str = "auto", runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise RouterError("unsupported plan schema")
    if not approve and not plan.get("approval", {}).get("approved"):
        raise RouterError("owner approval required: rerun with --approve")
    receipts: list[dict[str, Any]] = []
    for wave in sorted({int(item.get("wave_order", 1)) for item in plan.get("targets", [])}):
        for target in [item for item in plan.get("targets", []) if int(item.get("wave_order", 1)) == wave]:
            prompt = _text(target.get("sub_prompt"), plan.get("message", ""))
            hint = target.get("rag_context_hint")
            if hint:
                prompt = f"[RAG/context hint]\n{hint}\n\n[Dispatched instruction]\n{prompt}"
            selected = transport if transport != "auto" else _text(target.get("transport"), "auto")
            if selected == "auto":
                selected = "herdr" if target.get("source") == "herdr" else "dsh" if target.get("source") == "dsh" else "stdout"
            if selected == "stdout":
                receipt = {"ok": True, "method": "stdout", "target": target.get("target_repo", "unspecified"), "created": target.get("m2_status") == "NEW_M2", "prompt": prompt}
            elif selected == "herdr":
                receipt = _apply_herdr(target, plan, prompt, runner=runner)
            elif selected == "dsh":
                receipt = _apply_dsh(target, plan, prompt, poster=poster)
            else:
                raise RouterError(f"unsupported transport: {selected}")
            receipts.append({"wave_order": wave, "target_repo": target.get("target_repo"), **receipt})
    return {"ok": True, "plan_id": plan.get("plan_id"), "approved": True, "receipts": receipts}


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
    parser.add_argument("--mock-jev-json")
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
            plan = build_plan(_message(args), sessions, warnings, mock_jev=_json_value(args.mock_jev_json) if args.mock_jev_json else None, transport=args.transport)
            if args.dsh_state_url:
                plan["discovery"]["dsh_state_url"] = args.dsh_state_url
            output: Any = plan if args.json else preview_text(plan)
        else:
            if not args.plan_file:
                raise RouterError("--apply requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            output = apply_plan(plan, approve=args.approve, transport=args.transport)
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
