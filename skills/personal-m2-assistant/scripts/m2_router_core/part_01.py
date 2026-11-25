"""Unified M2 ingress: classify, preview, and owner-gated dispatch.

The router deliberately separates planning from delivery.  ``--plan`` only reads
session discovery sources and Jev; ``--apply`` requires an explicit approval flag
and sends only the targets recorded in the plan.
"""

from __future__ import annotations

import argparse

import copy

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

import hashlib

import importlib.util

import json

import os

import re

import shlex

import shutil

import socket

import subprocess

import sys

import time

import urllib.error

import urllib.request

import uuid

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1

M2_SYNC_HUB_MARKER = "<!-- GTT-M2-SYNC-HUB:v1 -->"

M2_SYNC_STATE_START = "<!-- M2-SYNC-STATE-START -->"

M2_SYNC_STATE_END = "<!-- M2-SYNC-STATE-END -->"

M2_SYNC_SCHEMA = "m2-cross-device-sync/v1"

DEFAULT_M2_SYNC_HUB_ISSUE = "TIA-463"

LINEAR_SYNC_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "linear-sync.py"

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

DEFAULT_HOOK_SCRIPT_PATH = Path(__file__).resolve().parent / "m2-board-hook.sh"

HERDR_BACKGROUND_RE = re.compile(r"(?:^|[-_])(?:goal-tl|writer|reviewer|explorer|pr-monitor)(?:[-_]|$)|/worktrees(?:/|$)", re.I)

HERDR_INTERACTIVE_AGENTS = {"copilot", "claude", "codex", "pi", "cindy"}

READONLY_PACKAGE_MANAGER_ROOTS = (
    "/opt/homebrew/bin",
    "/opt/homebrew/Cellar",
    "/opt/homebrew/opt",
    "/opt/homebrew/lib",
    "/opt/homebrew/share",
    "/opt/homebrew/libexec",
    "/usr/local/Cellar",
    "/usr/local/opt",
    "/usr/local/bin",
)

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
    "~/.claude",
    "~/.local",
    "~/.config/pew",
    "~/Library/Application Support/AIDash",
    "/opt/homebrew/lib/node_modules/@nocoo/pew",
    *READONLY_PACKAGE_MANAGER_ROOTS,
)

WHITELISTED_LINEAR_MCP_TOOLS = {
    "save_issue",
    "create_issue",
    "update_issue",
    "save_comment",
    "create_comment",
    "update_comment",
    "save_project",
    "create_project",
    "update_project",
    "save_milestone",
    "create_milestone",
    "update_milestone",
    "save_document",
    "create_document",
    "update_document",
    "save_status_update",
    "create_status_update",
    "update_status_update",
    "save_issue_label",
    "create_issue_label",
    "save_project_label",
    "create_project_label",
    "mark_notification",
    "whoami",
    "extract_images",
}

WHITELISTED_READONLY_MCP_SERVERS = {
    "ado",
    "ado-edge",
    "github",
    "github-mcp-server",
}

WHITELISTED_ADO_READONLY_MCP_TOOLS = {
    "wit_work_item",
    "wit_query",
    "wit_backlog",
    "repo_pull_request",
    "repo_pull_request_org",
    "repo_pull_request_thread",
    "repo_repository",
    "repo_branch",
    "repo_file",
    "repo_search_commits",
    "pipelines_build",
    "pipelines_build_log",
    "pipelines_definition",
    "pipelines_run",
    "pipelines_artifact",
    "core_list_projects",
    "core_list_project_teams",
    "core_get_identity_ids",
    "advsec_get_alerts",
    "advsec_get_alert_details",
    "testplan",
    "testplan_show_test_results_from_build_id",
    "wiki",
    "work",
    "search_code",
    "search_wiki",
    "search_workitem",
}

WHITELISTED_CMD_BINARIES = {
    "gh",
    "git",
    "python3",
    "jq",
    "herdr",
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

GROK_QUOTA_PROTECTION_MODE = "grok_bot_no_supervision"

GROK_QUOTA_PROTECTION_REASON = "grok_bot_no_supervision_no_routine"

GROK_ENV_MODEL_VARS = (
    "M2_CALLER_BOT",
    "BOT_KIND",
    "GROK_BOT",
    "COPILOT_MODEL",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
    "DSH_MODEL",
    "HERDR_MODEL",
    "MODEL",
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

class RouterError(RuntimeError):
    """A user-actionable planning or dispatch error."""

class DiscoverySessions(list[dict[str, Any]]):
    """List-compatible session results carrying the workspace inventory."""

    def __init__(
        self,
        values: Iterable[dict[str, Any]] = (),
        *,
        workspaces: Iterable[dict[str, Any]] = (),
        discovery_source: str = "",
    ):
        super().__init__(values)
        self.workspaces = list(workspaces)
        self.discovery_source = discovery_source

def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default

_EXPLORATORY_DISCUSSION_RE = re.compile(
    r"(?:先讨论|讨论一下|先聊聊|看看有哪些|先调研|不改代码|先探索|先分析|before\s+(?:coding|implement)|discussion\s+only)",
    re.IGNORECASE,
)

def is_exploratory_discussion_message(message: Any) -> bool:
    """Return whether an Owner explicitly requests Phase-0 discussion only."""
    return bool(_EXPLORATORY_DISCUSSION_RE.search(_text(message)))

def _apply_phase0_discussion_lock(target: dict[str, Any], message: str) -> None:
    """Keep exploratory requests raw and prevent M3 from choosing a delivery posture."""
    target["phase0_discussion_only"] = True
    target["m2_action"] = "PHASE0_DISCUSSION_ONLY"
    target["sub_prompt"] = message
    target.pop("hot_append", None)
    target.pop("hot_append_assumption", None)

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

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

EVENTS_TAIL_MAX_BYTES = 64 * 1024

RECENT_SESSION_MAX_TURNS = 3

def _agent_session_id(value: dict[str, Any]) -> str:
    raw_as = value.get("agent_session") or value.get("agentSession")
    if isinstance(raw_as, dict):
        for key in ("value", "id", "session_id", "sessionId", "agent_session_id"):
            cand = _text(raw_as.get(key))
            if cand:
                return cand
    elif isinstance(raw_as, str) and _text(raw_as):
        return _text(raw_as)
    for key in ("agent_session_id", "agentSessionId", "copilot_session_id", "copilotSessionId"):
        cand = _text(value.get(key))
        if cand:
            return cand
    for key in ("session_id", "sessionId", "id"):
        cand = _text(value.get(key))
        if cand and _UUID_RE.match(cand):
            return cand
    return ""

def _parse_iso_epoch(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        fval = float(value)
        return fval / 1000.0 if fval > 1e11 else fval
    raw = _text(value)
    if not raw:
        return None
    if re.match(r"^\d+(?:\.\d+)?$", raw):
        fval = float(raw)
        return fval / 1000.0 if fval > 1e11 else fval
    norm = raw.replace("Z", "+00:00")
    norm = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", norm)
    try:
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.timestamp()
    except ValueError:
        return None

def _epoch_to_iso(epoch_val: Any) -> str:
    parsed = _parse_iso_epoch(epoch_val)
    if parsed is None:
        return ""
    cst = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(parsed, tz=cst).replace(microsecond=0).isoformat()

def _clean_single_line(text: Any, *, max_len: int = 320) -> str:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", _text(text))
    compact = re.sub(r"\s+", " ", raw).strip()
    if len(compact) > max_len:
        return compact[: max_len - 1].rstrip() + "…"
    return compact

def _extract_events_from_jsonl_lines(
    raw_lines: list[str],
) -> tuple[list[dict[str, Any]], str, float | None, str, float | None]:
    turns: list[dict[str, Any]] = []
    last_conv_ts = ""
    last_conv_epoch: float | None = None
    last_any_ts = ""
    last_any_epoch: float | None = None

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        etype = _text(obj.get("type"))
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        ts = _text(obj.get("timestamp"))
        if not ts and data.get("timestamp") is not None:
            ts = _epoch_to_iso(data.get("timestamp"))
        ts_epoch = _parse_iso_epoch(ts)
        if ts and (last_any_epoch is None or (ts_epoch is not None and ts_epoch >= last_any_epoch)):
            last_any_ts = ts
            last_any_epoch = ts_epoch
        if etype and not etype.startswith("hook.") and ts:
            if last_conv_epoch is None or (ts_epoch is not None and ts_epoch >= last_conv_epoch):
                last_conv_ts = ts
                last_conv_epoch = ts_epoch

        if etype == "user.message":
            u_text = _clean_single_line(
                data.get("content") or data.get("transformedContent") or data.get("text") or obj.get("content"),
                max_len=320,
            )
            if u_text:
                turns.append({
                    "user_message": u_text,
                    "assistant_message": "",
                    "user_timestamp": ts,
                    "assistant_timestamp": "",
                    "timestamp": ts,
                })
        elif etype == "assistant.message":
            a_text = _clean_single_line(
                data.get("content") or data.get("text") or obj.get("content"),
                max_len=320,
            )
            if a_text:
                if turns:
                    turns[-1]["assistant_message"] = a_text
                    turns[-1]["assistant_timestamp"] = ts
                    if ts:
                        turns[-1]["timestamp"] = ts
                else:
                    turns.append({
                        "user_message": "",
                        "assistant_message": a_text,
                        "user_timestamp": "",
                        "assistant_timestamp": ts,
                        "timestamp": ts,
                    })
    return turns, last_conv_ts, last_conv_epoch, last_any_ts, last_any_epoch

def _read_copilot_events_tail(
    session_uuid: str,
    *,
    session_state_dir: str | Path | None = None,
    max_bytes: int = EVENTS_TAIL_MAX_BYTES,
    max_turns: int = RECENT_SESSION_MAX_TURNS,
) -> dict[str, Any] | None:
    clean_uuid = _text(session_uuid)
    if not clean_uuid or "/" in clean_uuid or "\\" in clean_uuid or ".." in clean_uuid:
        return None
    base_dir = Path(
        session_state_dir
        or os.environ.get("COPILOT_SESSION_STATE_DIR")
        or (Path.home() / ".copilot" / "session-state")
    ).expanduser()
    events_path = base_dir / clean_uuid / "events.jsonl"
    if not events_path.is_file():
        return None
    window = max(1024, int(max_bytes))
    try:
        with events_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            offset = max(0, file_size - window)
            handle.seek(offset, os.SEEK_SET)
            chunk = handle.read()
    except OSError:
        return None

    initial_bytes_read = len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    raw_lines = text.splitlines()
    if offset > 0 and raw_lines:
        try:
            json.loads(raw_lines[0])
        except ValueError:
            raw_lines = raw_lines[1:]

    turns, last_conv_ts, _, last_any_ts, _ = _extract_events_from_jsonl_lines(raw_lines)
    # If the tail 64KB was filled with tool/hook events during an active turn, widen window up to 512KB to recover recent user/assistant messages.
    if not any(t.get("user_message") or t.get("assistant_message") for t in turns) and offset > 0:
        wider_offset = max(0, file_size - min(file_size, window * 8))
        try:
            with events_path.open("rb") as handle:
                handle.seek(wider_offset, os.SEEK_SET)
                wider_chunk = handle.read()
            wider_lines = wider_chunk.decode("utf-8", errors="replace").splitlines()
            if wider_offset > 0 and wider_lines:
                try:
                    json.loads(wider_lines[0])
                except ValueError:
                    wider_lines = wider_lines[1:]
            w_turns, w_conv_ts, _, w_any_ts, _ = _extract_events_from_jsonl_lines(wider_lines)
            if w_turns:
                turns = w_turns
            if not last_conv_ts and w_conv_ts:
                last_conv_ts = w_conv_ts
            if not last_any_ts and w_any_ts:
                last_any_ts = w_any_ts
        except OSError:
            pass

    recent_turns = [t for t in turns if t.get("user_message") or t.get("assistant_message")][-max_turns:]
    last_activity = last_conv_ts or last_any_ts
    if not last_activity:
        try:
            last_activity = _epoch_to_iso(events_path.stat().st_mtime)
        except OSError:
            last_activity = ""

    turn_summaries: list[str] = []
    for idx, t in enumerate(recent_turns, start=1):
        u_msg = _text(t.get("user_message"))
        a_msg = _text(t.get("assistant_message"))
        if u_msg and a_msg:
            turn_summaries.append(f"[{idx}] User: {u_msg} -> Assistant: {a_msg}")
        elif u_msg:
            turn_summaries.append(f"[{idx}] User: {u_msg}")
        elif a_msg:
            turn_summaries.append(f"[{idx}] Assistant: {a_msg}")

    user_messages = [_text(t["user_message"]) for t in recent_turns if _text(t.get("user_message"))]
    assistant_messages = [_text(t["assistant_message"]) for t in recent_turns if _text(t.get("assistant_message"))]
    return {
        "source": "copilot_events_jsonl",
        "agent_session_id": clean_uuid,
        "events_path": str(events_path),
        "bytes_read": initial_bytes_read,
        "last_session_activity_at": last_activity,
        "turns": recent_turns,
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "latest_user_message": user_messages[-1] if user_messages else "",
        "latest_assistant_message": assistant_messages[-1] if assistant_messages else "",
        "summary": " | ".join(turn_summaries),
    }

def _parse_pane_recent_unwrapped_turns(raw_text: str, *, max_turns: int = RECENT_SESSION_MAX_TURNS) -> list[dict[str, Any]]:
    cleaned = _strip_tui_box_chrome(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", _text(raw_text)))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines() if re.search(r"\w", ln)]
    if not lines:
        return []
    turns: list[dict[str, Any]] = []
    prompt_re = re.compile(r"^(?:[❯>]\s*(?!\d+\.)|(?:User|Prompt|You)\s*[:：]\s*)(.+)$", re.I)
    reply_re = re.compile(r"^(?:[●⏺✦]\s*|(?:Assistant|Copilot)\s*[:：]\s*)(.+)$", re.I)
    current_user = ""
    current_assistant: list[str] = []
    for ln in lines:
        if re.match(r"^(?:─+|━+|╻.*|╹.*)$", ln):
            continue
        pm = prompt_re.match(ln)
        if pm:
            u_cand = _clean_single_line(pm.group(1), max_len=320)
            if u_cand:
                if current_user or current_assistant:
                    turns.append({
                        "user_message": current_user,
                        "assistant_message": _clean_single_line(" ".join(current_assistant), max_len=320),
                        "timestamp": "",
                    })
                current_user = u_cand
                current_assistant = []
                continue
        rm = reply_re.match(ln)
        if rm:
            current_assistant.append(_clean_single_line(rm.group(1), max_len=320))
        elif current_user:
            current_assistant.append(_clean_single_line(ln, max_len=200))
    if current_user or current_assistant:
        turns.append({
            "user_message": current_user,
            "assistant_message": _clean_single_line(" ".join(current_assistant), max_len=320),
            "timestamp": "",
        })
    if not turns:
        tail_summary = _clean_single_line(" / ".join(lines[-6:]), max_len=320)
        if tail_summary:
            turns.append({"user_message": "", "assistant_message": tail_summary, "timestamp": ""})
    return [t for t in turns if t.get("user_message") or t.get("assistant_message")][-max_turns:]

def _run_command(args: list[str], *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

def _quota_protection_payload() -> dict[str, Any]:
    return {
        "active": True,
        "caller_bot": "grok",
        "reason": GROK_QUOTA_PROTECTION_REASON,
        "supervision_disabled": True,
        "routine_disabled": True,
    }

def _record_has_grok_model(record: dict[str, Any]) -> bool:
    for field in ("model", "resolved_model", "agent_model", "logical_group", "agent"):
        if "grok" in _text(record.get(field)).lower():
            return True
    return False

def is_grok_bot_caller(
    args: Any = None,
    *,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    herdr_bin: str | None = None,
    runner: CommandRunner = _run_command,
    probe_herdr: bool = True,
) -> bool:
    """Detect whether the caller is Grok Bot or explicitly requested no supervision/routine polling."""
    if (
        no_supervision
        or no_routine
        or bool(getattr(args, "no_supervision", False))
        or bool(getattr(args, "no_routine", False))
    ):
        return True
    effective_caller_bot = _text(caller_bot or getattr(args, "caller_bot", "")).lower()
    if "grok" in effective_caller_bot:
        return True
    grok_bot_env = _text(os.environ.get("GROK_BOT")).lower()
    if grok_bot_env in {"1", "true", "yes"} or "grok" in grok_bot_env:
        return True
    for env_var in GROK_ENV_MODEL_VARS:
        if "grok" in _text(os.environ.get(env_var)).lower():
            return True
    caller_pane = _text(os.environ.get("HERDR_PANE_ID"))
    if caller_pane:
        records: list[dict[str, Any]] = []
        if agent_records is not None:
            records = [dict(item) for item in agent_records if isinstance(item, dict)]
        elif probe_herdr and not bool(getattr(args, "no_herdr", False)):
            binary = _text(
                herdr_bin
                or getattr(args, "herdr_bin", "")
                or os.environ.get("HERDR_BIN")
                or shutil.which("herdr"),
                "herdr",
            )
            try:
                res = runner([binary, "agent", "list"], timeout=2.0)
                if getattr(res, "returncode", 1) == 0:
                    records = _records(_parse_json_output(getattr(res, "stdout", "")))
            except (OSError, subprocess.SubprocessError):
                records = []
        for rec in records:
            pane_val = _text(rec.get("pane_id") or rec.get("paneId") or rec.get("id"))
            if pane_val == caller_pane and _record_has_grok_model(rec):
                return True
    return False

def _resolve_quota_protection(
    args: Any = None,
    *,
    plan: dict[str, Any] | None = None,
    apply_receipt: dict[str, Any] | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    herdr_bin: str | None = None,
    runner: CommandRunner = _run_command,
    probe_herdr: bool = True,
) -> dict[str, Any]:
    plan_obj = plan if isinstance(plan, dict) else {}
    receipt_obj = apply_receipt if isinstance(apply_receipt, dict) else {}
    active = (
        _text(plan_obj.get("quota_protection_mode")) == GROK_QUOTA_PROTECTION_MODE
        or bool((plan_obj.get("quota_protection") or {}).get("active"))
        or _text(receipt_obj.get("quota_protection_mode")) == GROK_QUOTA_PROTECTION_MODE
        or bool((receipt_obj.get("quota_protection") or {}).get("active"))
        or is_grok_bot_caller(
            args,
            caller_bot=caller_bot,
            no_supervision=no_supervision,
            no_routine=no_routine,
            agent_records=agent_records,
            herdr_bin=herdr_bin,
            runner=runner,
            probe_herdr=probe_herdr,
        )
    )
    if active:
        return _quota_protection_payload()
    return {
        "active": False,
        "caller_bot": _text(caller_bot or getattr(args, "caller_bot", "")),
        "reason": "",
        "supervision_disabled": False,
        "routine_disabled": False,
    }

def _read_herdr_pane_recent_unwrapped(
    binary: str,
    pane_id: str,
    *,
    runner: CommandRunner = _run_command,
    lines: int = 50,
) -> str:
    clean_pane = _text(pane_id)
    if not clean_pane:
        return ""
    try:
        res = runner(
            [binary, "pane", "read", clean_pane, "--source", "recent-unwrapped", "--lines", str(lines)],
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(res, "returncode", 1) != 0:
        return ""
    stdout = _text(getattr(res, "stdout", ""))
    parsed = _parse_json_output(stdout)
    if isinstance(parsed, dict):
        for key in ("recent_unwrapped", "recent", "text", "content", "output", "visible"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val
        result = parsed.get("result")
        if isinstance(result, dict):
            for key in ("recent_unwrapped", "recent", "text", "content", "output", "visible"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    return stdout
