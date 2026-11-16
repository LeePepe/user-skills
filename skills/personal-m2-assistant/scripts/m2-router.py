#!/usr/bin/env python3
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
    fcntl = None  # type: ignore[assignment]
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
)
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

    def __init__(self, values: Iterable[dict[str, Any]] = (), *, workspaces: Iterable[dict[str, Any]] = ()):
        super().__init__(values)
        self.workspaces = list(workspaces)


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


def read_recent_session_context(
    session: dict[str, Any],
    *,
    runner: CommandRunner = _run_command,
    herdr_bin: str | None = None,
    session_state_dir: str | Path | None = None,
    allow_pane_fallback: bool = True,
) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    existing = session.get("recent_session_context")
    if isinstance(existing, dict) and (existing.get("turns") or existing.get("summary")):
        return existing

    agent_sid = _agent_session_id(session)
    if agent_sid:
        events_ctx = _read_copilot_events_tail(agent_sid, session_state_dir=session_state_dir)
        if events_ctx and (events_ctx.get("turns") or events_ctx.get("last_session_activity_at")):
            if _text(session.get("pane_id") or session.get("paneId")):
                events_ctx["pane_id"] = _text(session.get("pane_id") or session.get("paneId"))
            return events_ctx

    pane_id = _text(session.get("pane_id") or session.get("paneId"))
    if not allow_pane_fallback or not pane_id:
        return None

    raw_pane_text = _text(session.get("recent_unwrapped") or session.get("pane_recent_unwrapped"))
    should_call_herdr = bool(
        raw_pane_text
        or runner is not _run_command
        or herdr_bin
        or session.get("_live_herdr")
        or session.get("agent_session") is not None
        or _text(os.environ.get("M2_ENABLE_PANE_READ_FALLBACK")) == "1"
    )
    if not raw_pane_text and should_call_herdr:
        binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
        raw_pane_text = _read_herdr_pane_recent_unwrapped(binary, pane_id, runner=runner, lines=50)
    if not raw_pane_text:
        return None

    turns = _parse_pane_recent_unwrapped_turns(raw_pane_text, max_turns=RECENT_SESSION_MAX_TURNS)
    if not turns:
        return None
    last_activity = _text(session.get("last_session_activity_at") or session.get("updated_at"))
    for idx, t in enumerate(turns):
        if last_activity and not _text(t.get("timestamp")):
            t["timestamp"] = last_activity
    turn_summaries: list[str] = []
    for idx, t in enumerate(turns, start=1):
        u_msg = _text(t.get("user_message"))
        a_msg = _text(t.get("assistant_message"))
        if u_msg and a_msg:
            turn_summaries.append(f"[{idx}] User: {u_msg} -> Assistant: {a_msg}")
        elif u_msg:
            turn_summaries.append(f"[{idx}] User: {u_msg}")
        elif a_msg:
            turn_summaries.append(f"[{idx}] Assistant: {a_msg}")
    user_messages = [_text(t["user_message"]) for t in turns if _text(t.get("user_message"))]
    assistant_messages = [_text(t["assistant_message"]) for t in turns if _text(t.get("assistant_message"))]
    return {
        "source": "herdr_pane_read",
        "agent_session_id": agent_sid,
        "pane_id": pane_id,
        "last_session_activity_at": last_activity,
        "turns": turns,
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "latest_user_message": user_messages[-1] if user_messages else "",
        "latest_assistant_message": assistant_messages[-1] if assistant_messages else "",
        "summary": " | ".join(turn_summaries),
    }


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
    agent_sid = _agent_session_id(value)
    raw_agent_session = value.get("agent_session") if isinstance(value.get("agent_session"), dict) else ({"value": agent_sid} if agent_sid else None)
    norm: dict[str, Any] = {
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
    if raw_agent_session:
        norm["agent_session"] = dict(raw_agent_session)
    if agent_sid:
        norm["agent_session_id"] = agent_sid
    if _text(value.get("last_session_activity_at")):
        norm["last_session_activity_at"] = _text(value.get("last_session_activity_at"))
    if isinstance(value.get("recent_session_context"), dict):
        norm["recent_session_context"] = dict(value["recent_session_context"])
    if value.get("_live_herdr"):
        norm["_live_herdr"] = True
    return norm


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


def _workspace_inventory(payload: Any) -> list[dict[str, Any]] | None:
    """Distinguish a valid, possibly empty inventory from non-workspace output."""
    if isinstance(payload, dict):
        if payload.get("ok") is False or payload.get("error"):
            return None
        if "workspaces" in payload:
            payload = payload["workspaces"]
        elif isinstance(payload.get("result"), dict):
            return _workspace_inventory(payload["result"])
    if not isinstance(payload, list):
        return None
    normalized = []
    session_fields = {
        "pane_id", "paneId", "session_id", "sessionId", "agent", "agent_name", "agentName",
        "herdr_target", "herdrTarget", "isDshSession", "is_dsh_session", "foreground_cwd", "target_repo",
    }
    for item in payload:
        if not isinstance(item, dict) or session_fields.intersection(item):
            return None
        if "cwd" in item and "label" not in item:
            return None
        workspace_id = _text(item.get("workspace_id") or item.get("workspaceId") or item.get("id"))
        label = _text(item.get("label") or item.get("name") or item.get("title"))
        if not workspace_id or not any(field in item for field in ("label", "name", "title")):
            return None
        normalized.append({
            "workspace_id": workspace_id,
            "label": label,
            "active_tab_id": _text(item.get("active_tab_id") or item.get("activeTabId")),
            "tab_count": item.get("tab_count", item.get("tabCount", 0)),
            "pane_count": item.get("pane_count", item.get("paneCount", 0)),
            "agent_status": _text(item.get("agent_status") or item.get("agentStatus") or item.get("status")),
        })
    return normalized


def _workspace_records(payload: Any) -> list[dict[str, Any]]:
    return _workspace_inventory(payload) or []


def _workspace_map(workspaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_text(item.get("workspace_id")): item for item in workspaces if _text(item.get("workspace_id"))}


def resolve_target_space(target: dict[str, Any], topic: dict[str, Any] | None = None, workspaces: list[dict[str, Any]] | None = None, domain_map: dict[str, Any] | None = None) -> dict[str, Any]:
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    source = topic if topic is not None else proposed
    space = _text((source or {}).get("space") or target.get("space") or target.get("target_repo"), "M2")
    aliases = list((source or {}).get("space_aliases") or target.get("space_aliases") or [])
    aliases = [_text(value) for value in aliases if _text(value)]
    target_id = ""
    inventory = workspaces or []
    labels = [space, *aliases]
    for wanted in labels:
        wanted_key = wanted.casefold()
        if wanted_key == "大总管" and space.casefold() != "大总管":
            continue
        match = next(
            (
                item for item in inventory
                if _text(item.get("label")).casefold() == wanted_key
                and _text(item.get("workspace_id"))
            ),
            None,
        )
        if match:
            target_id = _text(match.get("workspace_id"))
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
    workspace_inventory = _workspace_inventory(workspace_payload)
    if isinstance(payload, dict) and workspace_inventory is None:
        workspace_inventory = _workspace_inventory(payload)
    workspace_records = workspace_inventory or []
    used_live_herdr = False
    if pane_payload is None and agent_payload is None and payload is None:
        used_live_herdr = True
        binary = herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr")
        if not binary:
            return DiscoverySessions(workspaces=workspace_records), None
        try:
            pane_result = runner([binary, "pane", "list"], timeout=3.0)
            agent_result = runner([binary, "agent", "list"], timeout=3.0)
            try:
                workspace_result = runner([binary, "workspace", "list"], timeout=3.0)
                live_inventory = _workspace_inventory(_parse_json_output(workspace_result.stdout)) if workspace_result.returncode == 0 else None
                if workspace_inventory is None and live_inventory is not None:
                    workspace_records = live_inventory
            except (OSError, subprocess.SubprocessError):
                pass
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
        if used_live_herdr:
            item["_live_herdr"] = True
        ws = _text(item.get("workspace_id") or item.get("workspaceId"))
        if ws and ws in workspace_by_id:
            item["workspace_label"] = workspace_by_id[ws].get("label", "")
    sessions = [normalize_session(item, "herdr", allow_interactive_herdr=True) for item in records]
    return DiscoverySessions((item for item in sessions if item), workspaces=workspace_records), None


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
    discovered_workspaces: list[dict[str, Any]] = []
    if include_herdr:
        found, warning = discover_herdr(payload=herdr_payload, pane_payload=herdr_pane_payload, agent_payload=herdr_agent_payload, workspace_payload=workspace_payload, herdr_workspace_payload=herdr_workspace_payload, herdr_bin=herdr_bin, runner=runner)
        sessions.extend(found)
        discovered_workspaces = list(getattr(found, "workspaces", []))
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
    workspace_inventory = _workspace_inventory(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    if workspace_inventory is None:
        workspace_inventory = discovered_workspaces
    return DiscoverySessions(deduped.values(), workspaces=workspace_inventory), warnings


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


def run_gc(
    records: Iterable[dict[str, Any]],
    *,
    approve: bool = False,
    state_file: str | Path | None = None,
    handoff_root: str | Path | None = None,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    candidates = discover_gc_candidates(records, handoff_root=handoff_root)
    receipts = []
    if approve:
        for candidate in candidates:
            try:
                receipts.append({**candidate, **_close_herdr_pane(candidate, runner=runner)})
            except RouterError as error:
                receipts.append({**candidate, "ok": False, "error": str(error)})
    state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True) if approve else None
    pruned = _prune_closed_slots(state_path, receipts)
    result: dict[str, Any] = {"ok": True, "approved": approve, "dry_run": not approve, "gc_candidates": candidates, "closed_receipts": receipts, "pruned_state_slots": pruned}
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=bool(approve),
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync:
        board_state = _with_locked_supervision_state(state_path, lambda s: s)
        board_rollup = _compute_board_rollup(board_state)
        cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
        result["linear_sync"] = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    elif approve or sync_linear or no_sync_linear:
        result["linear_sync"] = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    return result

def _default_preview_path(plan_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", _text(plan_id, "m2p-plan")).strip("-") or "m2p-plan"
    return Path(f"/tmp/m2-plan-{safe_id}.md")


def render_preview_markdown(plan: dict[str, Any]) -> str:
    plan_id = _text(plan.get("plan_id"), "m2p-unknown")
    route = _text((plan.get("decision") or {}).get("route"), "ROUTE_SINGLE")
    default_label = _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL)
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    lines = [
        f"# M2 Dispatch Plan · {plan_id} ({route})",
        "",
        f"- **Message**: {_text(plan.get('message'))}",
        f"- **Route**: `{route}`",
        f"- **Default Model / Context**: `{default_label}` (`{_text(plan.get('default_model'), DEFAULT_M2_MODEL)}` / `{_text(plan.get('default_context'), DEFAULT_M2_CONTEXT)}`)",
        "- **Owner Gate**: Owner approval required before dispatch.",
    ]
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"- **Quota Protection**: `quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE}` "
            "(Grok Bot 省流防烧额度模式：仅执行单次派发 Fire-and-Forget，自动取消后台 `--watch` 监督与定时 Routine 轮询)"
        )
    lines.extend([
        "",
        "## Target Dispatch Table",
        "",
        "| Wave | Topic | Domain Status | Target Repo | Space | M2 Status | Transport | Model / Context | CWD |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for target in plan.get("targets", []):
        model_label = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        cwd = _expand_cwd(target.get("cwd"))
        lines.append(
            f"| {target.get('wave_order', 1)} | `{_text(target.get('topic_id'))}` | "
            f"`{_text(target.get('domain_status'))}` | `{_text(target.get('target_repo'), 'unspecified')}` | "
            f"`{_text(target.get('space'), 'M2')} ({_text(target.get('target_workspace_id')) or 'new-space'})` | "
            f"`{_text(target.get('m2_status'))}` | `{_text(target.get('transport'), 'auto')}` | "
            f"`{model_label}` | `{cwd}` |"
        )
    lines.extend(["", "## Target Instructions"])
    for index, target in enumerate(plan.get("targets", []), start=1):
        lines.extend([
            "",
            f"### {index}. Wave {target.get('wave_order', 1)} · `{_text(target.get('topic_id'))}` → `{_text(target.get('target_repo'), 'unspecified')}` ({_text(target.get('m2_status'))})",
            f"- **Model / Context**: `{_text(target.get('model_label'), DEFAULT_M2_MODEL_LABEL)}` (`{_text(target.get('model'), DEFAULT_M2_MODEL)}` / `{_text(target.get('context'), DEFAULT_M2_CONTEXT)}`)",
            f"- **Space**: `{_text(target.get('space'), 'M2')} ({_text(target.get('target_workspace_id')) or 'new-space'})`",
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
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            lines.extend([
                "",
                "#### `recent_session_context`",
                f"- **Source**: `{_text(recent_ctx.get('source'), 'session')}` · **Last Activity**: `{_text(recent_ctx.get('last_session_activity_at') or target.get('last_session_activity_at'), '—')}`",
            ])
            for t_idx, turn in enumerate(recent_ctx.get("turns") or [], start=1):
                if isinstance(turn, dict):
                    u_m = _text(turn.get("user_message"))
                    a_m = _text(turn.get("assistant_message"))
                    ts_m = _text(turn.get("timestamp"))
                    ts_part = f" (`{ts_m}`)" if ts_m else ""
                    if u_m and a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}` → Assistant `{a_m}`")
                    elif u_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}`")
                    elif a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: Assistant `{a_m}`")
            if not recent_ctx.get("turns") and _text(recent_ctx.get("summary")):
                lines.append(f"- **Summary**: {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "## Context Eviction", "", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"- completion=`{eviction.get('completion_evidence')}`; is_same_goal_continuation=`{eviction.get('is_same_goal_continuation')}`; prior_context_value=`{eviction.get('prior_context_value')}`"])
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
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "## Cross-Device Active & Pending (Linear `TIA-463`)", "", *cross_lines[1:]])
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
    write_preview_md: bool = False,
    state_file: str | Path | None = None,
    cross_device_state: dict[str, Any] | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
    herdr_bin: str | None = None,
    session_state_dir: str | Path | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message = _text(message)
    if not message:
        raise RouterError("message is required")
    plan_id = f"m2p-{uuid.uuid4().hex[:10]}"
    loaded_domain_map = domain_map or load_domain_map(domain_map_file)
    workspace_inventory = _workspace_inventory(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    if workspace_inventory is None:
        workspace_inventory = _workspace_records(getattr(sessions, "workspaces", []))
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
        workspaces=workspace_inventory,
        runner=runner,
        herdr_bin=herdr_bin,
        session_state_dir=session_state_dir,
    )
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        resolve_target_space(target, topic, workspace_inventory, loaded_domain_map)
        if isinstance(target.get("proposed_domain"), dict):
            resolve_target_space(target, None, workspace_inventory, loaded_domain_map)
    stale_candidates: list[dict[str, Any]] = []
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
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
        matched = _target_session(target, topic, sessions, loaded_domain_map) if target.get("m2_status") == "EXISTING_M2" else None
        if target.get("m2_status") == "EXISTING_M2":
            ctx_source = matched if isinstance(matched, dict) else target
            recent_ctx = target.get("recent_session_context") if isinstance(target.get("recent_session_context"), dict) else read_recent_session_context(
                ctx_source,
                runner=runner,
                herdr_bin=herdr_bin,
                session_state_dir=session_state_dir,
            )
            if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("last_session_activity_at")):
                target["recent_session_context"] = recent_ctx
                if _text(recent_ctx.get("last_session_activity_at")):
                    target["last_session_activity_at"] = _text(recent_ctx.get("last_session_activity_at"))
                if _text(recent_ctx.get("agent_session_id")):
                    target["agent_session_id"] = _text(recent_ctx.get("agent_session_id"))
        target.setdefault(
            "context_pass",
            {
                "enabled": bool(target.get("rag_context_hint") or target.get("recent_session_context")),
                "hint": target.get("rag_context_hint"),
                **(
                    {"recent_session_context": target["recent_session_context"]}
                    if isinstance(target.get("recent_session_context"), dict)
                    else {}
                ),
            },
        )
        target["cleanup_candidates"] = _stale_candidates(target, sessions, loaded_domain_map)
        if matched and _text(matched.get("status"), "idle").casefold() in {"idle", "done"}:
            continuity = _evaluate_m2_context_continuity(
                target, matched, message, domain_map=loaded_domain_map, mock_jev=mock_jev,
                handoff_root=os.environ.get("M2_HANDOFF_ROOT"),
                visible_text=_text(matched.get("visible") or matched.get("visible_text")),
            )
            target.update({"m2_action": continuity["m2_action"]})
            target["context_eviction"] = continuity
            if continuity["m2_action"] == "REPLACE_STALE_M2":
                candidate = {"session_id": matched.get("session_id", ""), "pane_id": matched.get("pane_id", ""), "title": matched.get("title", ""), "status": matched.get("status", "idle"), "target_repo": matched.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": "context_eviction", "eviction_reason": continuity["eviction_reason"], "old_goal_done": True, "is_same_goal_continuation": continuity["is_same_goal_continuation"], "prior_context_value": continuity["prior_context_value"]}
                target["cleanup_candidates"] = [candidate]
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
        "gc_candidates": discover_gc_candidates(list(sessions), handoff_root=os.environ.get("M2_HANDOFF_ROOT")),
        "approval": {"required": True, "approved": False},
    }
    cache_path = _resolve_cross_device_cache_path(state_file)
    resolved_cross_state = cross_device_state
    should_sync, _ = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=False,
        state_path=_resolve_supervision_state_path(state_file, persist_by_default=True),
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync and resolved_cross_state is None:
        state_path = _resolve_supervision_state_path(state_file, persist_by_default=True)
        local_board = _compute_board_rollup(_with_locked_supervision_state(state_path, lambda s: s))
        sync_res = sync_cross_device_hub(
            local_board,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        plan["linear_sync"] = sync_res
        if isinstance(sync_res.get("state"), dict):
            resolved_cross_state = sync_res["state"]
    if resolved_cross_state is None:
        resolved_cross_state = _load_cross_device_cache(cache_path)
    cross_summary = _extract_remote_devices_summary(resolved_cross_state, local_hostname=local_hostname)
    plan["local_hostname"] = cross_summary["local_hostname"]
    plan["remote_devices"] = cross_summary["remote_devices"]
    plan["cross_device_in_flight"] = cross_summary["cross_device_in_flight"]
    plan["cross_device_pending_decisions"] = cross_summary["cross_device_pending_decisions"]
    if is_grok_bot_caller(
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=agent_records if agent_records is not None else (sessions if sessions else None),
        herdr_bin=herdr_bin,
        runner=runner,
    ):
        plan["quota_protection_mode"] = GROK_QUOTA_PROTECTION_MODE
        plan["quota_protection"] = _quota_protection_payload()
    plan["preview_markdown"] = render_preview_markdown(plan)
    if write_preview_md or preview_md_file is not None:
        write_preview_markdown(plan, preview_md_file)
    return plan


def preview_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Plan {plan['plan_id']} · {plan['decision']['route']}",
        f"Default Model / Context: {_text(plan.get('default_model_label'), DEFAULT_M2_MODEL_LABEL)}",
    ]
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE} "
            "(Grok Bot fire-and-forget: background --watch supervision and routine polling disabled)"
        )
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
        hint = _text(target.get("rag_context_hint"))
        if hint:
            lines.append(f"  rag_context_hint: {hint}")
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            ts_label = _text(recent_ctx.get("last_session_activity_at") or target.get("last_session_activity_at"), "recent")
            lines.append(f"  recent_session_context ({ts_label}): {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"completion={eviction.get('completion_evidence')} · continuation={eviction.get('is_same_goal_continuation')} · prior_context_value={eviction.get('prior_context_value')}"])
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
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "Cross-Device Active & Pending (TIA-463):", *cross_lines[1:]])
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


def _apply_workspace_id(
    mapped_space: str,
    aliases: list[Any],
    plan: dict[str, Any],
    *,
    binary: str,
    runner: CommandRunner,
    created_workspaces: dict[str, str] | None,
) -> str:
    space_key = mapped_space.casefold()
    if created_workspaces and _text(created_workspaces.get(space_key)):
        return _text(created_workspaces[space_key])

    discovery = _workspace_records((plan.get("discovery") or {}).get("workspaces"))
    live_inventory: list[dict[str, Any]] | None = None
    try:
        result = runner([binary, "workspace", "list"], timeout=3.0)
        if getattr(result, "returncode", 1) == 0:
            live_inventory = _workspace_inventory(_parse_json_output(result.stdout))
    except (OSError, subprocess.SubprocessError):
        live_inventory = None
    inventory = live_inventory if live_inventory is not None else discovery
    labels = [mapped_space, *aliases]
    for wanted in labels:
        wanted_key = _text(wanted).casefold()
        if wanted_key == "大总管" and space_key != "大总管":
            continue
        match = next(
            (
                item for item in inventory
                if _text(item.get("label")).casefold() == wanted_key
                and _text(item.get("workspace_id"))
            ),
            None,
        )
        if match:
            return _text(match.get("workspace_id"))
    return ""


def _authoritative_apply_space(target: dict[str, Any], plan: dict[str, Any], domain_map_file: str | Path | None = None) -> tuple[str, list[str]]:
    topic_id = _text(target.get("topic_id"))
    if topic_id and topic_id != NEW_UNMAPPED_DOMAIN:
        try:
            topic = _topic_by_id(load_domain_map(domain_map_file or plan.get("domain_map_file"))).get(topic_id)
        except RouterError:
            topic = None
        if topic:
            return _text(topic.get("space")) or _text(topic.get("target_repo"), "M2"), list(topic.get("space_aliases") or [])
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    return (
        _text(proposed.get("space") or target.get("space") or target.get("target_repo"), "M2"),
        list(proposed.get("space_aliases") or target.get("space_aliases") or []),
    )


def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, index: int = 1, used_names: set[str] | None = None, created_workspaces: dict[str, str] | None = None, domain_map_file: str | Path | None = None) -> dict[str, Any]:
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
    mapped_space, space_aliases = _authoritative_apply_space(target, plan, domain_map_file)
    space_key = mapped_space.casefold()
    workspace_id = _apply_workspace_id(
        mapped_space,
        space_aliases,
        plan,
        binary=binary,
        runner=runner,
        created_workspaces=created_workspaces,
    )
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
    if _gc_protected(live) or pane_id in {"w2J:p1", "w1:p1"}:
        return False, "protected_or_active_tail"
    visible = _text(live.get("visible") or live.get("visible_text"))
    if not visible:
        visible = _read_herdr_visible(binary, pane_id, runner=runner)
    if not visible or _has_active_visible_tail(visible):
        return False, "visible_tail_unavailable_or_active"
    return True, "ok"


def _close_herdr_pane(
    candidate: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    dry_run: bool = False,
) -> dict[str, Any]:
    pane_id = _text(candidate.get("pane_id"))
    if not pane_id or pane_id == _text(os.environ.get("HERDR_PANE_ID")) or pane_id in {"w1:p1", "w2J:p1"} or re.search(r"Cindy|大总管", " ".join(_text(candidate.get(k)) for k in ("workspace_label", "workspaceLabel", "space", "label", "title", "name")), re.I):
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
        if mode == "replace_context" and candidate.get("reason") != "context_eviction":
            continue
        candidate_dry_run = dry_run or bool(dry_run_sessions and session_id in dry_run_sessions)
        receipts.append({"session_id": session_id, "reason": candidate.get("reason"), **_close_herdr_pane(candidate, runner=runner, dry_run=candidate_dry_run)})
    return receipts


def _resolve_supervision_state_path(
    state_file: str | Path | None = None,
    *,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    apply_receipt: dict[str, Any] | None = None,
    safe_plan_id: str = "m2p-plan",
    persist_by_default: bool = False,
) -> Path | None:
    if state_file:
        return Path(state_file).expanduser()
    if isinstance(apply_receipt, dict) and _text(apply_receipt.get("supervision_state_path")):
        return Path(_text(apply_receipt.get("supervision_state_path"))).expanduser()
    env_state = _text(os.environ.get("M2_SUPERVISION_STATE_FILE"))
    if env_state:
        return Path(env_state).expanduser()
    if apply_receipt_file or plan_file:
        anchor = Path(apply_receipt_file or plan_file).expanduser()
        if anchor.parent in {Path("/tmp"), Path("/private/tmp")}:
            return Path("/tmp/m2-supervision-state.json")
        return anchor.parent / "m2-supervision-state.json"
    if persist_by_default:
        return Path("/tmp/m2-supervision-state.json")
    return None


def _with_locked_supervision_state(
    state_path: Path | None,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    reset: bool = False,
) -> dict[str, Any]:
    if state_path is None:
        initial: dict[str, Any] = {"schema_version": 1, "slots": [], "events": []}
        return mutator(initial)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            current: dict[str, Any] = {"schema_version": 1, "slots": [], "events": []}
            if not reset and state_path.is_file():
                try:
                    loaded = json.loads(state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        current = {
                            "schema_version": 1,
                            "slots": [dict(s) for s in (loaded.get("slots") or []) if isinstance(s, dict)],
                            "events": [dict(e) for e in (loaded.get("events") or []) if isinstance(e, dict)],
                        }
                        if "backlog" in loaded and isinstance(loaded.get("backlog"), list):
                            current["backlog"] = [dict(b) for b in loaded["backlog"] if isinstance(b, dict)]
                except (OSError, ValueError):
                    pass
            updated = mutator(current)
            temp_path = state_path.with_suffix(state_path.suffix + f".{os.getpid()}.tmp")
            temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(state_path)
            return updated
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _match_existing_slot_index(
    slots: list[dict[str, Any]],
    rec: dict[str, Any],
    target: dict[str, Any],
) -> int | None:
    explicit_slot = _text(target.get("slot_id") or rec.get("slot_id"))
    if explicit_slot:
        for idx, slot in enumerate(slots):
            if _text(slot.get("slot_id")).casefold() == explicit_slot.casefold():
                return idx
        return None
    explicit_label = _text(target.get("task_label") or rec.get("task_label"))
    if explicit_label:
        for idx, slot in enumerate(slots):
            if _text(slot.get("slot_id")).casefold() == explicit_label.casefold() or _text(slot.get("base_label")).casefold() == explicit_label.casefold():
                return idx
        m_prefix = re.match(r"^(.+?)\d+$", explicit_label)
        if m_prefix:
            base_cand = m_prefix.group(1).strip()
            for idx, slot in enumerate(slots):
                if _text(slot.get("slot_id")).casefold() == base_cand.casefold() or _text(slot.get("base_label")).casefold() == base_cand.casefold():
                    return idx
        return None
    topic_id = _text(rec.get("topic_id") or target.get("topic_id"))
    has_named_topic = bool(topic_id and topic_id not in {"unspecified", NEW_UNMAPPED_DOMAIN} and not topic_id.startswith("new-topic-unspecified"))
    pane_id = _text(rec.get("pane_id") or target.get("pane_id"))
    if pane_id:
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("pane_id")) == pane_id:
                return idx
    is_stdout = _text(rec.get("method") or target.get("transport")).lower() == "stdout"
    session_id = _text(rec.get("session_id") or target.get("session_id"))
    if session_id and session_id.casefold() != "unspecified" and not (is_stdout and session_id == _text(rec.get("target_repo"))):
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("session_id")) == session_id or _text(slot.get("target")) == session_id:
                return idx
    target_ref = _text(rec.get("target") or target.get("herdr_target"))
    if target_ref and target_ref.casefold() != "unspecified" and not (is_stdout and target_ref == _text(rec.get("target_repo"))):
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("target")) == target_ref or _text(slot.get("session_id")) == target_ref:
                return idx
    if has_named_topic:
        for idx, slot in enumerate(slots):
            if _text(slot.get("topic_id")).casefold() == topic_id.casefold() or _text(slot.get("slot_id")).casefold() == topic_id.casefold():
                return idx
    return None


ROLLUP_METADATA_KEYS = (
    "linear_issue", "parent_linear_issue", "handoff_dir", "goal_id", "depends_on",
    "upstream_waves", "rag_context_hint", "sub_prompt", "terminal_title", "cwd",
    "m1_progress", "m1_stage_summary", "_handoff_discovered",
    "agent_session", "agent_session_id", "recent_session_context",
    "last_session_activity_at", "ledger_updated_at", "active_summary_source",
)


def _has_active_visible_tail(visible_text: str, *, max_lines: int = 8) -> bool:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", visible_text)
    raw_lines = text.splitlines()
    nonempty_lines = [line for line in raw_lines if line.strip()]
    tail = "\n".join(raw_lines[-max_lines:] + nonempty_lines[-max_lines:])
    return bool(re.search(r"background\s+/tasks\b|ctrl\+c(?:\s|$)|\b(?:Working|Thinking)\b|\bRunning\s|\bWaiting for\b", tail, re.I))


def _slot_metadata(source: dict[str, Any]) -> dict[str, Any]:
    metadata = {key: source[key] for key in ROLLUP_METADATA_KEYS if key in source}
    agent_sid = _agent_session_id(source)
    if agent_sid and "agent_session_id" not in metadata:
        metadata["agent_session_id"] = agent_sid
    if (source.get("handoff_dir") or source.get("goal_id")) and "_handoff_discovered" not in source:
        metadata["_handoff_discovered"] = False
    return metadata


_LEDGER_TS_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b"
)


def _extract_ledger_latest_timestamp(ledger_text: str, ledger_path: Path) -> tuple[str, float | None]:
    best_ts = ""
    best_epoch: float | None = None
    for match in _LEDGER_TS_RE.finditer(ledger_text):
        cand_ts = match.group(1).replace(" ", "T")
        cand_epoch = _parse_iso_epoch(cand_ts)
        if cand_epoch is not None and (best_epoch is None or cand_epoch >= best_epoch):
            best_epoch = cand_epoch
            best_ts = cand_ts
    if best_epoch is not None:
        return best_ts, best_epoch
    try:
        mtime = ledger_path.stat().st_mtime
        return _epoch_to_iso(mtime), mtime
    except OSError:
        return "", None


def _enrich_slot_metadata(
    slot: dict[str, Any],
    *,
    handoff_root: str | Path | None = None,
    scan_default: bool = False,
    session_state_dir: str | Path | None = None,
) -> dict[str, Any]:
    agent_sid = _agent_session_id(slot)
    if agent_sid:
        slot["agent_session_id"] = agent_sid
        events_ctx = _read_copilot_events_tail(agent_sid, session_state_dir=session_state_dir)
        if events_ctx:
            slot["recent_session_context"] = events_ctx
            if _text(events_ctx.get("last_session_activity_at")):
                slot["last_session_activity_at"] = _text(events_ctx.get("last_session_activity_at"))
            session_turn_summary = _text(
                events_ctx.get("latest_assistant_message")
                or events_ctx.get("summary")
                or events_ctx.get("latest_user_message")
            )
            if session_turn_summary and not _text(slot.get("visible_summary")):
                slot["visible_summary"] = session_turn_summary

    if slot.get("retained") and slot.get("m1_progress") and not slot.get("handoff_dir"):
        return slot
    text = "\n".join(_text(slot.get(key)) for key in (
        "target", "sub_prompt", "terminal_title", "visible_summary", "rag_context_hint", "goal_id", "linear_issue",
    ))
    parent = re.search(r"Parent(?:\s+Issue)?\s*[:=：]?\s*(TIA-\d+)", text, re.I)
    if parent:
        slot.setdefault("parent_linear_issue", parent.group(1).upper())
    issues = re.findall(r"\bTIA-\d+\b", text, re.I)
    primary = next((issue.upper() for issue in issues if issue.upper() != slot.get("parent_linear_issue")), "")
    if primary:
        slot.setdefault("linear_issue", primary)
    root_value = handoff_root or os.environ.get("M2_HANDOFF_ROOT")
    root = Path(root_value or "~/Development/handoff").expanduser()
    explicit = "" if slot.get("_handoff_discovered") else _text(slot.get("handoff_dir"))
    if not explicit and slot.get("goal_id") and not slot.get("_handoff_discovered"):
        explicit = str(root / _text(slot["goal_id"]))
    candidates = [Path(explicit).expanduser() / "ledger.md"] if explicit else []
    if not explicit:
        candidates.extend(Path(match.group(0)).expanduser() / "ledger.md" for match in re.finditer(r"(?:~/|/)[^\s`\"'<>|()]*handoff/([^/\s`\"'<>|()]+)", text))
        if slot.get("handoff_dir"):
            candidates.append(Path(slot["handoff_dir"]) / "ledger.md")
        if (root_value or scan_default) and root.is_dir():
            candidates.extend(sorted(root.glob("*/ledger.md")))
    ranked = []
    for ledger_path in dict.fromkeys(candidates):
        try:
            ledger_text = ledger_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        linear = re.search(r"^\s*-?\s*Linear\s*[:：]\s*[^\n]*?\b(TIA-\d+)\b", ledger_text, re.M | re.I)
        tl = re.search(r"^\s*-?\s*TL\s*[:：]\s*([^\s/|]+)", ledger_text, re.M)
        goal_names = [ledger_path.parent.name]
        if tl and "-goal-tl" in tl.group(1):
            goal_names.append(tl.group(1).split("-goal-tl", 1)[0])
        goal_pattern = r"(?<!\w)(?:" + "|".join(re.escape(name) for name in goal_names) + r")(?!\w)"
        score = 0
        for key, weight in (("visible_summary", 100), ("sub_prompt", 80), ("terminal_title", 40), ("rag_context_hint", 20)):
            context = _text(slot.get(key))
            if re.search(goal_pattern, context) or (tl and tl.group(1) in context):
                score += weight
        if linear and linear.group(1).upper() == slot.get("linear_issue"):
            score += 10
        # Never associate by Parent Issue or by a shared repository alone.
        if not explicit and not score:
            continue
        ranked.append((score, ledger_path, ledger_text, linear, tl))
    for _, ledger_path, ledger_text, linear, tl in sorted(ranked, key=lambda candidate: candidate[0], reverse=True)[:1]:
        slot["_handoff_discovered"] = not bool(explicit)
        slot["handoff_dir"] = str(ledger_path.parent)
        slot["goal_id"] = ledger_path.parent.name
        if linear:
            slot["linear_issue"] = linear.group(1).upper()
        parent = re.search(r"Parent(?:\s+Issue)?\s*[:=：]?\s*(TIA-\d+)", ledger_text, re.I)
        if parent:
            slot["parent_linear_issue"] = parent.group(1).upper()
        tasks = []
        in_tasks = False
        for line in ledger_text.splitlines():
            if line.startswith("## "):
                in_tasks = line.strip().casefold() == "## tasks"
            if in_tasks and line.startswith("|"):
                cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 5 and cells[4] in {"planned", "ready", "in_progress", "done", "blocked", "in_review", "changes_requested"}:
                    tasks.append(f"{cells[0]} ({cells[4]}, {cells[3]})")
        stages = list(dict.fromkeys(re.findall(r"Stage\s+\d+\s+[^\n|()<>]+?(?=\s*(?:->|→|\||\)|$))", ledger_text, re.I | re.M)))
        stage_summary = " -> ".join(stage.strip() for stage in stages)
        if re.search(r"(?:two|2)[- ]stage[- ][^\n]*designer[^\n]*(?:->|→|-)\s*coding", ledger_text, re.I):
            stage_summary = "Stage 1 Designer -> Stage 2 Coding"
        progress = ([f"TL: {tl.group(1)}"] if tl else []) + tasks
        if stage_summary:
            progress.append(stage_summary)
        events = [line.strip("- ") for line in ledger_text.splitlines() if "EVT=" in line]
        if events:
            progress.append(events[-1][:300])
        ledger_progress = " | ".join(progress)
        slot["ledger_m1_progress"] = ledger_progress
        ledger_ts, ledger_epoch = _extract_ledger_latest_timestamp(ledger_text, ledger_path)
        if ledger_ts:
            slot["ledger_updated_at"] = ledger_ts
        session_ts = _text(
            slot.get("last_session_activity_at")
            or ((slot.get("recent_session_context") or {}).get("last_session_activity_at") if isinstance(slot.get("recent_session_context"), dict) else "")
        )
        session_epoch = _parse_iso_epoch(session_ts)
        recent_ctx = slot.get("recent_session_context") if isinstance(slot.get("recent_session_context"), dict) else {}
        session_newer = bool(
            session_epoch is not None
            and (ledger_epoch is None or session_epoch > ledger_epoch)
        )
        if session_newer:
            newer_summary = _text(
                recent_ctx.get("latest_assistant_message")
                or recent_ctx.get("summary")
                or slot.get("visible_summary")
            )
            slot["active_summary_source"] = "session"
            if newer_summary:
                slot["visible_summary"] = newer_summary
                slot["m1_progress"] = newer_summary
            else:
                slot["m1_progress"] = ledger_progress
            slot["m1_stage_summary"] = ""
            slot["handoff_in_progress"] = False
            slot["goal_tl"] = tl.group(1) if tl else ""
        else:
            slot["active_summary_source"] = "ledger"
            slot["m1_progress"] = ledger_progress
            slot["m1_stage_summary"] = stage_summary
            slot["handoff_in_progress"] = any("(in_progress," in task for task in tasks) and "EVT=GOAL_DONE" not in ledger_text
            slot["goal_tl"] = tl.group(1) if tl else ""
        break
    else:
        recent_ctx = slot.get("recent_session_context") if isinstance(slot.get("recent_session_context"), dict) else {}
        if recent_ctx:
            newer_summary = _text(
                recent_ctx.get("latest_assistant_message")
                or recent_ctx.get("summary")
                or slot.get("visible_summary")
            )
            if newer_summary:
                slot["visible_summary"] = newer_summary
                slot["m1_progress"] = newer_summary
                slot["active_summary_source"] = "session"
    return slot


def _live_agent_records(binary: str, *, runner: CommandRunner = _run_command) -> list[dict[str, Any]]:
    try:
        result = runner([binary, "agent", "list"], timeout=5.0)
        return _records(_parse_json_output(result.stdout)) if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def _discover_and_enrich_global_m2_slots(
    state: dict[str, Any], *, agent_records: Iterable[dict[str, Any]] = (),
    runner: CommandRunner = _run_command, herdr_bin: str = "herdr",
    handoff_root: str | Path | None = None, protected_slots: Iterable[str] = (),
    visible_by_ref: dict[str, str] | None = None,
    allow_quiescent_settle: bool = False,
    settled_slot_ids_out: list[str] | None = None,
) -> dict[str, Any]:
    slots = state.setdefault("slots", [])
    records = list(agent_records)
    scan_default = runner is _run_command and bool(records)
    protected = set(protected_slots)
    visible_cache = dict(visible_by_ref or {})
    for live in records:
        name = _text(live.get("name") or live.get("herdr_target") or live.get("id"))
        pane = _text(live.get("pane_id") or live.get("paneId"))
        if not _looks_like_m2(live, "herdr") or _herdr_background(live):
            continue
        status = _text(live.get("agent_status") or live.get("status"), "unknown").lower()
        if status in HERDR_TERMINAL_STATUSES:
            continue
        slot = next((s for s in slots if (pane and s.get("pane_id") == pane) or (name and s.get("target") == name)), None)
        is_new = slot is None
        if slot is None:
            slot_id = name or pane
            if not slot_id:
                continue
            existing_ids = {s.get("slot_id") for s in slots}
            while slot_id in existing_ids:
                slot_id += "-live"
            slot = {"slot_id": slot_id, "run_label": name or pane, "run_seq": 1, "plan_id": "discovered", "wave_order": int(live.get("wave_order") or 1)}
        slot.update({
            "target": slot.get("target") if (slot.get("retained") and slot.get("target")) else (name or slot.get("target", "")),
            "pane_id": pane or slot.get("pane_id", ""),
            "target_repo": slot.get("target_repo") if (slot.get("retained") and slot.get("target_repo")) else (_repo_from(live) or slot.get("target_repo", "")),
            "topic_id": live.get("topic_id") or slot.get("topic_id", ""),
        })
        slot.update(_slot_metadata(live))
        slot["terminal_title"] = _text(live.get("terminal_title") or live.get("terminal_title_stripped") or live.get("title") or slot.get("terminal_title"))
        ref = name or pane
        if ref not in visible_cache:
            visible_cache[ref] = _text(live.get("visible") or live.get("visible_text")) or _read_herdr_visible(herdr_bin, ref, runner=runner)
        visible = visible_cache[ref]
        if visible:
            slot["visible_summary"] = _summarize_visible_lines(visible)
        _enrich_slot_metadata(slot, handoff_root=handoff_root, scan_default=scan_default)
        if slot.get("slot_id") not in protected:
            handoff_active = bool(slot.get("handoff_in_progress", False))
            if handoff_active:
                if slot.get("active_summary_source") == "session":
                    handoff_active = False
                else:
                    tl_name = _text(slot.get("goal_tl"))
                    if tl_name:
                        tl_rec = next(
                            (r for r in records if _text(r.get("name") or r.get("herdr_target") or r.get("id")) == tl_name),
                            None,
                        )
                        if tl_rec is not None:
                            tl_status = _text(tl_rec.get("agent_status") or tl_rec.get("status"), "idle").lower()
                            tl_vis = _text(tl_rec.get("visible") or tl_rec.get("visible_text"))
                            if tl_status not in {"working", "running", "blocked", "needs_input"} and not _has_active_visible_tail(tl_vis):
                                handoff_active = False
            active = _has_active_visible_tail(visible) or handoff_active
            if active or status in {"working", "running", "blocked", "needs_input"}:
                slot["raw_status"] = "working" if active and status not in {"blocked", "needs_input"} else status
                slot["status"] = slot["raw_status"]
                slot["needs_owner_attention"] = status in {"blocked", "needs_input"}
                slot["quiescent"] = False
                slot["seen_working"] = active or status in {"working", "running"}
            elif allow_quiescent_settle and status in {"idle", "done"} and not is_new:
                was_active = _text(slot.get("status")).lower() in {"working", "running", "blocked", "needs_input", "unknown"}
                slot["raw_status"] = status
                slot["status"] = status
                slot["needs_owner_attention"] = False
                slot["blocked_reason"] = ""
                slot["quiescent"] = True
                if was_active and settled_slot_ids_out is not None and _text(slot.get("slot_id")):
                    settled_slot_ids_out.append(_text(slot.get("slot_id")))
            elif is_new:
                continue
        if is_new:
            slots.append(slot)
    for slot in slots:
        _enrich_slot_metadata(slot, handoff_root=handoff_root, scan_default=scan_default)
    return state


def _compute_board_rollup(
    ledger: dict[str, Any],
    *,
    just_settled_slots: Iterable[str] = (),
) -> dict[str, Any]:
    slots = [dict(s) for s in (ledger.get("slots") or []) if isinstance(s, dict)]
    for slot in slots:
        slot.setdefault("wave_order", 1)
        dependencies = slot.get("depends_on") or []
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        slot["depends_on"] = list(dependencies)
        if not dependencies:
            hint = _text(slot.get("rag_context_hint"))
            for upstream in slots:
                if upstream is slot or int(upstream.get("wave_order") or 1) >= int(slot["wave_order"] or 1):
                    continue
                identity = _text(upstream.get("linear_issue") or upstream.get("run_label") or upstream.get("slot_id"))
                same_plan = slot.get("plan_id") and slot.get("plan_id") != "discovered" and upstream.get("plan_id") == slot.get("plan_id")
                if identity and (same_plan or identity in hint):
                    slot["depends_on"].append(identity)
        slot["upstream_waves"] = sorted({int(s.get("wave_order") or 1) for s in slots if any(_text(s.get(key)) in slot["depends_on"] for key in ("slot_id", "run_label", "linear_issue"))})
    settled_set = {s.casefold() for s in just_settled_slots if _text(s)}
    completed_slots = [s for s in slots if _text(s.get("status")).lower() in {"idle", "done"}]
    running_slots = [s for s in slots if _text(s.get("status")).lower() in {"running", "working"}]
    blocked_slots = [
        s for s in slots
        if bool(s.get("needs_owner_attention")) or _text(s.get("status")).lower() in {"blocked", "needs_input", "missing"}
    ]
    completed_labels = [_text(s.get("run_label") or s.get("slot_id")) for s in completed_slots]
    running_labels = [_text(s.get("run_label") or s.get("slot_id")) for s in running_slots]
    blocked_labels = [_text(s.get("run_label") or s.get("slot_id")) for s in blocked_slots]
    just_settled_labels = [
        _text(s.get("run_label") or s.get("slot_id"))
        for s in slots
        if _text(s.get("slot_id")).casefold() in settled_set or _text(s.get("run_label")).casefold() in settled_set
    ]
    in_flight = [s for s in slots if s in running_slots or s in blocked_slots or _text(s.get("status")) in {"unknown", "ready"}]
    detail_keys = ("slot_id", "run_label", "target", "pane_id", "topic_id", "target_repo", "wave_order", "depends_on", "upstream_waves", "linear_issue", "parent_linear_issue", "handoff_dir", "m1_progress", "m1_stage_summary", "raw_status", "status")
    details = [{key: slot.get(key, "") for key in detail_keys} for slot in in_flight]

    def node(slot: dict[str, Any]) -> str:
        name = _text(slot.get("topic_id") or slot.get("target_repo") or slot.get("target") or slot.get("run_label"))
        stages = _text(slot.get("m1_stage_summary"))
        return f"{_text(slot.get('linear_issue') or slot.get('run_label') or slot.get('slot_id'))} ({name}{': ' + stages if stages else ''})"

    waves = sorted({int(s.get("wave_order") or 1) for s in slots})
    chain = " -> ".join(f"Wave {wave}: " + " + ".join(node(s) for s in slots if int(s.get("wave_order") or 1) == wave) for wave in waves)

    def row(slot: dict[str, Any]) -> str:
        deps = ", ".join(str(dep) for dep in slot.get("depends_on", [])) or "无"
        upstream = ", ".join(f"Wave {w}" for w in slot.get("upstream_waves", []))
        summary = _text(slot.get("m1_progress") or slot.get("visible_summary"), "—").replace("\n", " / ")
        return (f"- `{slot.get('run_label', slot.get('slot_id', ''))}` · {slot.get('target', '')} · pane `{slot.get('pane_id', '')}` · "
                f"Wave {slot.get('wave_order', 1)} · 依赖 {upstream + ': ' if upstream else ''}{deps} · "
                f"{slot.get('linear_issue') or 'Linear 未绑定'} · status=`{slot.get('status', '')}` · {summary}")

    markdown = ["### 📊 全局 M2 统一汇总看板 (Unified M2 Rollup)",
                "#### 🔗 Wave 波次与上下游依赖链路（Wave & Dependency Topology）", chain or "无",
                "#### 1. 🔔 本次完成（Just Completed）"]
    markdown.extend([row(s) for s in completed_slots if _text(s.get("run_label")) in just_settled_labels] or ["无（状态巡检）"])
    markdown.extend(["#### 2. ✅ 累计已完成（Completed Rollup）", f"`completed_rollup_code`: `{''.join(completed_labels) or 'none'}` · {' + '.join(completed_labels) or 'none'}"])
    markdown.extend([row(s) for s in completed_slots] or ["无"])
    markdown.append("#### 3. ⏳ 仍在运行中（In-Flight Active M2s）")
    markdown.extend([row(s) for s in in_flight] or ["无"])
    backlog_items = [dict(b) for b in (ledger.get("backlog") or []) if isinstance(b, dict)] if "backlog" in ledger else None
    result_rollup: dict[str, Any] = {
        "unified_rollup_markdown": "\n".join(markdown),
        "in_flight_details": details,
        "wave_dependency_chain": chain,
        "just_settled_labels": just_settled_labels,
        "completed_labels": completed_labels,
        "completed_rollup_code": "".join(completed_labels),
        "completed_rollup_display": " + ".join(completed_labels) if completed_labels else "none",
        "running_labels": running_labels,
        "blocked_labels": blocked_labels,
        "all_board_settled": not in_flight,
        "completed_targets": completed_slots,
        "running_targets": running_slots,
        "blocked_targets": blocked_slots,
        "slots": slots,
    }
    if backlog_items is not None:
        result_rollup["backlog"] = backlog_items
    return result_rollup


def _resolve_local_hostname(local_hostname: str | None = None) -> str:
    explicit = _text(local_hostname or os.environ.get("M2_LOCAL_HOSTNAME"))
    if explicit:
        return explicit.split(".", 1)[0]
    try:
        host = socket.gethostname().split(".", 1)[0].strip()
    except OSError:
        host = ""
    return host or "localhost"


def _resolve_cross_device_cache_path(
    state_file: str | Path | None = None,
    *,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    apply_receipt: dict[str, Any] | None = None,
) -> Path:
    explicit_cache = _text(os.environ.get("M2_CROSS_DEVICE_CACHE_FILE"))
    if explicit_cache:
        return Path(explicit_cache).expanduser()
    state_path = _resolve_supervision_state_path(
        state_file,
        plan_file=plan_file,
        apply_receipt_file=apply_receipt_file,
        apply_receipt=apply_receipt,
        persist_by_default=True,
    )
    if state_path and state_path.parent not in {Path("/tmp"), Path("/private/tmp")}:
        return state_path.parent / "m2-cross-device-sync-cache.json"
    return Path("/tmp/m2-cross-device-sync-cache.json")


def _is_isolated_test_state_path(state_path: Path | None) -> bool:
    if state_path is None:
        return False
    resolved = state_path.expanduser().resolve(strict=False)
    return resolved not in {
        Path("/tmp/m2-supervision-state.json").resolve(strict=False),
        Path("/private/tmp/m2-supervision-state.json").resolve(strict=False),
    }


def _should_trigger_linear_sync(
    *,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    auto_trigger: bool = False,
    state_path: Path | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if no_sync_linear:
        return False, "explicit_no_sync_linear"
    if sync_linear or linear_runner is not None or hub_issue_payload is not None:
        return True, "explicit"
    if not auto_trigger:
        return False, "not_requested"
    if _text(os.environ.get("GTT_LINEAR_SYNC")) == "0":
        return False, "env_gtt_linear_sync_disabled"
    if _is_isolated_test_state_path(state_path) and _text(os.environ.get("GTT_LINEAR_SYNC")) != "1":
        return False, "isolated_test_state_path"
    return True, "auto_best_effort"


def _cst_now_iso() -> str:
    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).replace(microsecond=0).isoformat()


def _format_cst_heartbeat(iso_str: str) -> str:
    raw = _text(iso_str)
    if not raw:
        cst = timezone(timedelta(hours=8))
        return datetime.now(cst).strftime("%Y-%m-%d %H:%M")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        cst = timezone(timedelta(hours=8))
        return dt.astimezone(cst).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", raw)
        return f"{m.group(1)} {m.group(2)}" if m else raw[:16]


def _format_linear_issue_link(issue_id: str) -> str:
    clean = _text(issue_id).upper()
    if re.match(r"^TIA-\d+$", clean):
        return f"[{clean}](https://linear.app/tianpeili/issue/{clean})"
    return _text(issue_id) or "—"


_NON_WORK_EXACT_TOKENS = {
    "aidash",
    "aidash-aidata",
    "pew",
    "pew-telemetry",
    "user-skills",
    "skills-workflow",
    "skills-design",
    "skills-research-knowledge",
    "skills-retro-story",
    "skills-repo-ci",
    "skill-skill-contract",
    "voxpocket",
    "vitalstride",
    "helix",
    "davici",
    "nas",
    "nas-smart-home",
    "financial",
    "shared-design-system",
    "raven",
    "gateway-herdr-jev",
    "dsh-plugins",
    "learning-and-blog",
    "leepepe.github.io",
}
_WORK_SIGNAL_RE = re.compile(
    r"(?:\bbing-growth-sapphire-app\b|\bsapphire\b|\bsmartbanner\b|\bchina[-_ ]?bugs?\b"
    r"|\bchina\s+release\b|\bflightbook\b|\bwebiq\b|\bworkiq\b|\bteams-cli\b"
    r"|\binfo-teams\b|\binfo-email\b|\bemail-dsh\b|\bwork-recruitment\b"
    r"|\bintern\b|招聘|\bmy-pr-supervisor\b|\bpr-auto-review\b|\bdsat\b)",
    re.I,
)


def _normalize_hub_scope(value: Any) -> str:
    raw = _text(value).strip().casefold().replace("_", "-")
    if raw == "work":
        return "work"
    if raw in {"non-work", "nonwork", "personal"}:
        return "non-work"
    return ""


def _classify_hub_scope(
    item: Any = None,
    *,
    rich_line: str = "",
    domain_map: dict[str, Any] | None = None,
    hint_scope: str = "",
) -> str:
    explicit = _normalize_hub_scope(hint_scope)
    if explicit:
        return explicit
    if isinstance(item, dict):
        item_scope = _normalize_hub_scope(item.get("scope") or item.get("area"))
        if item_scope:
            return item_scope
    topics_by_id: dict[str, dict[str, Any]] = {}
    active_map = domain_map
    if active_map is None:
        try:
            active_map = load_domain_map()
        except RouterError:
            active_map = {}
    if isinstance(active_map, dict):
        topics_by_id = _topic_by_id(active_map)
    candidates: list[str] = []
    if isinstance(item, dict):
        for key in ("topic_id", "slot_id", "target_repo"):
            val = _text(item.get(key)).strip()
            if val:
                candidates.append(val)
    elif item is not None:
        val = _text(item).strip()
        if val:
            candidates.append(val)
    if rich_line:
        cells = [c.strip("` ").strip() for c in rich_line.strip().strip("|").split("|")]
        if len(cells) >= 2 and rich_line.lstrip().startswith("|"):
            candidates.append(cells[1])
    for cand in candidates:
        low = cand.casefold()
        topic = topics_by_id.get(low)
        if topic:
            return "work" if _text(topic.get("group")) == "work-business" else "non-work"
        if low in _NON_WORK_EXACT_TOKENS or low.startswith("new-topic-vox"):
            return "non-work"
        if _WORK_SIGNAL_RE.search(cand):
            return "work"
    probe_parts = [rich_line]
    if isinstance(item, dict):
        probe_parts.extend(_text(item.get(k)) for k in ("title", "summary"))
    elif item is not None:
        probe_parts.append(_text(item))
    if _WORK_SIGNAL_RE.search(" ".join(p for p in probe_parts if p)):
        return "work"
    return "non-work"


def parse_cross_device_hub_description(description: str) -> dict[str, Any]:
    text = str(description or "")
    state_obj: dict[str, Any] = {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}
    if M2_SYNC_STATE_START in text and M2_SYNC_STATE_END in text:
        between = text.split(M2_SYNC_STATE_START, 1)[1].split(M2_SYNC_STATE_END, 1)[0]
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", between, re.S)
        raw_json = code_match.group(1) if code_match else between.strip()
        try:
            loaded = json.loads(raw_json)
            if isinstance(loaded, dict):
                devices_raw = loaded.get("devices")
                state_obj = {
                    "schema": _text(loaded.get("schema"), M2_SYNC_SCHEMA),
                    "updated_at": _text(loaded.get("updated_at")),
                    "devices": {
                        str(k): dict(v)
                        for k, v in (devices_raw.items() if isinstance(devices_raw, dict) else [])
                        if isinstance(v, dict)
                    },
                }
        except (ValueError, TypeError):
            pass

    gtt_suffix = ""
    gtt_start = text.find("<!-- GTT-LINEAR-SYNC:START -->")
    if gtt_start >= 0:
        gtt_suffix = text[gtt_start:].strip()

    body_before_details = text.split("<details>", 1)[0] if "<details>" in text else (text[:gtt_start] if gtt_start >= 0 else text)
    pending_by_host: dict[str, list[str]] = {}
    in_flight_rows_by_host: dict[str, list[str]] = {}
    backlog_lines_by_host: dict[str, list[str]] = {}
    completed_rows_by_host: dict[str, list[str]] = {}
    line_scopes: dict[str, str] = {}

    def _extract_host_from_bullet(line: str) -> str:
        host_m = re.search(r"\(`([^`]+)`\)\s*$", line)
        if not host_m:
            host_m = re.search(r"\[Host:\s*([^\]]+)\]", line)
        return host_m.group(1).strip() if host_m else ""

    def _collect_table_rows(lines_iter: Iterable[str], target_map: dict[str, list[str]], scope_hint: str) -> None:
        for raw_line in lines_iter:
            line = raw_line.strip()
            if not line.startswith("|") or line.count("|") < 3:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0]
            if not first or all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            if "设备" in first or "Host" in first:
                continue
            host_name = first.strip("` ").strip()
            if host_name and host_name not in {"—", "-"}:
                if line not in target_map.setdefault(host_name, []):
                    target_map[host_name].append(line)
                if scope_hint:
                    line_scopes[line] = scope_hint

    current_part_scope = ""
    current_sub_kind = ""
    sub_buffer: list[str] = []

    def _flush_sub_buffer() -> None:
        nonlocal sub_buffer
        if not current_sub_kind or not sub_buffer:
            sub_buffer = []
            return
        if current_sub_kind == "pending":
            for raw_line in sub_buffer:
                line = raw_line.strip()
                if not line.startswith("-"):
                    continue
                host_key = _extract_host_from_bullet(line)
                if host_key:
                    if line not in pending_by_host.setdefault(host_key, []):
                        pending_by_host[host_key].append(line)
                    if current_part_scope:
                        line_scopes[line] = current_part_scope
        elif current_sub_kind == "backlog":
            for raw_line in sub_buffer:
                line = raw_line.strip()
                if not line.startswith("-"):
                    continue
                host_key = _extract_host_from_bullet(line)
                if host_key:
                    if line not in backlog_lines_by_host.setdefault(host_key, []):
                        backlog_lines_by_host[host_key].append(line)
                    if current_part_scope:
                        line_scopes[line] = current_part_scope
        elif current_sub_kind == "in_flight":
            _collect_table_rows(sub_buffer, in_flight_rows_by_host, current_part_scope)
        elif current_sub_kind == "completed":
            _collect_table_rows(sub_buffer, completed_rows_by_host, current_part_scope)
        sub_buffer = []

    for raw_line in body_before_details.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            _flush_sub_buffer()
            current_sub_kind = ""
            low_h2 = stripped.casefold()
            if "non-work" in low_h2 or "非工作" in stripped:
                current_part_scope = "non-work"
            elif re.search(r"\bwork\b", low_h2) or "工作业务" in stripped:
                current_part_scope = "work"
            continue
        if stripped.startswith("### "):
            _flush_sub_buffer()
            low_h3 = stripped.casefold()
            if "non-work" in low_h3 or "非工作" in stripped:
                current_part_scope = "non-work"
            elif re.search(r"\bwork\b", low_h3) or "工作业务" in stripped:
                current_part_scope = "work"
            if "🚨" in stripped:
                current_sub_kind = "pending"
            elif "⏳" in stripped:
                current_sub_kind = "in_flight"
            elif "🗂" in stripped or "backlog" in low_h3:
                current_sub_kind = "backlog"
            elif "✅" in stripped:
                current_sub_kind = "completed"
            else:
                current_sub_kind = ""
            continue
        if stripped == "---":
            _flush_sub_buffer()
            current_sub_kind = ""
            continue
        if current_sub_kind:
            sub_buffer.append(raw_line)
    _flush_sub_buffer()

    return {
        "state": state_obj,
        "pending_lines_by_host": pending_by_host,
        "in_flight_rows_by_host": in_flight_rows_by_host,
        "backlog_lines_by_host": backlog_lines_by_host,
        "completed_rows_by_host": completed_rows_by_host,
        "line_scopes": line_scopes,
        "gtt_suffix": gtt_suffix,
    }


def _build_local_device_shard(
    board_rollup: dict[str, Any],
    *,
    previous_shard: dict[str, Any] | None = None,
    now_iso: str = "",
) -> dict[str, Any]:
    timestamp = now_iso or _cst_now_iso()
    prev = previous_shard if isinstance(previous_shard, dict) else {}
    try:
        domain_map = load_domain_map()
    except RouterError:
        domain_map = {}
    slots = [dict(s) for s in (board_rollup.get("slots") or []) if isinstance(s, dict)]
    in_flight_items: list[dict[str, Any]] = []
    pending_decisions: list[str] = []
    recent_completed: list[Any] = []
    active_issues: set[str] = set()
    active_running_topics: set[str] = set()

    for slot in slots:
        status = _text(slot.get("status")).lower()
        retained = bool(slot.get("retained"))
        slot_id = _text(slot.get("slot_id") or slot.get("run_label"))
        topic_id = _text(slot.get("topic_id") or slot.get("target_repo") or slot_id)
        session_id = _text(slot.get("target") or slot.get("session_id") or slot_id)
        pane_id = _text(slot.get("pane_id"))
        issue = _text(slot.get("linear_issue"))
        summary = _text(slot.get("m1_progress") or slot.get("visible_summary") or slot.get("sub_prompt"), "—").replace("\n", " / ")[:280]
        scope = _classify_hub_scope(slot, domain_map=domain_map)
        if status in {"working", "running", "blocked", "needs_input", "ready", "unknown"} or retained:
            entry: dict[str, Any] = {
                "slot_id": slot_id,
                "topic_id": topic_id,
                "session_id": session_id,
                "pane_id": pane_id,
                "issue": issue,
                "summary": summary,
                "scope": scope,
            }
            if retained:
                entry["retained"] = True
            in_flight_items.append(entry)
            if issue:
                active_issues.add(issue)
            if status in {"working", "running"} and topic_id:
                active_running_topics.add(topic_id.casefold())
            if bool(slot.get("needs_owner_attention")) or status in {"blocked", "needs_input"}:
                decision_key = issue or slot_id
                if decision_key and decision_key not in pending_decisions:
                    pending_decisions.append(decision_key)
        elif status in {"idle", "done"}:
            comp_entry: dict[str, Any] = {
                "slot_id": slot_id,
                "topic_id": topic_id,
                "target_repo": _text(slot.get("target_repo") or topic_id),
                "issue": issue or slot_id,
                "summary": summary,
                "scope": scope,
            }
            recent_completed.append(comp_entry)

    # Preserve prior recent_completed items from this host that aren't currently back in-flight
    seen_completed_keys: set[str] = {
        _text(item.get("issue") if isinstance(item, dict) else item)
        for item in recent_completed
        if _text(item.get("issue") if isinstance(item, dict) else item)
    }
    for old_comp in (prev.get("recent_completed") or []):
        key = _text(old_comp.get("issue") if isinstance(old_comp, dict) else old_comp)
        if key and key not in seen_completed_keys and key not in active_issues:
            recent_completed.append(old_comp)
            seen_completed_keys.add(key)

    raw_backlog = board_rollup.get("backlog") if "backlog" in board_rollup else prev.get("backlog")
    backlog_items: list[dict[str, Any]] = []
    for raw_b in (raw_backlog or []):
        if isinstance(raw_b, dict):
            b_topic = _text(raw_b.get("topic_id") or raw_b.get("id"))
            if b_topic and b_topic.casefold() in active_running_topics:
                continue
            b_copy = dict(raw_b)
            b_copy["scope"] = _classify_hub_scope(b_copy, domain_map=domain_map)
            backlog_items.append(b_copy)

    shard_out: dict[str, Any] = {
        "updated_at": timestamp,
        "in_flight": in_flight_items,
        "pending_decisions": pending_decisions,
        "recent_completed": recent_completed[:12],
    }
    if backlog_items:
        shard_out["backlog"] = backlog_items
    return shard_out


def render_cross_device_hub_markdown(
    state: dict[str, Any],
    *,
    preserved_sections: dict[str, Any] | None = None,
    local_hostname: str = "",
) -> str:
    preserved = preserved_sections or {}
    pending_by_host: dict[str, list[str]] = dict(preserved.get("pending_lines_by_host") or {})
    in_flight_by_host: dict[str, list[str]] = dict(preserved.get("in_flight_rows_by_host") or {})
    backlog_by_host: dict[str, list[str]] = dict(preserved.get("backlog_lines_by_host") or {})
    completed_by_host: dict[str, list[str]] = dict(preserved.get("completed_rows_by_host") or {})
    line_scopes: dict[str, str] = dict(preserved.get("line_scopes") or {})
    gtt_suffix = _text(preserved.get("gtt_suffix"))
    try:
        domain_map = load_domain_map()
    except RouterError:
        domain_map = {}
    devices: dict[str, dict[str, Any]] = {
        str(k): dict(v)
        for k, v in ((state.get("devices") or {}).items() if isinstance(state.get("devices"), dict) else [])
        if isinstance(v, dict)
    }

    def _ordered_hosts() -> list[str]:
        keys = list(devices.keys())
        for extra_map in (pending_by_host, in_flight_by_host, backlog_by_host, completed_by_host):
            for k in extra_map:
                if k not in keys:
                    keys.append(k)
        return keys

    hosts = _ordered_hosts()
    pending_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    in_flight_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    backlog_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}
    completed_by_scope: dict[str, list[str]] = {"work": [], "non-work": []}

    def _append_unique(bucket: dict[str, list[str]], scope: str, line: str) -> None:
        key = "work" if scope == "work" else "non-work"
        if line not in bucket[key]:
            bucket[key].append(line)

    def _scope_for_host_issue(shard: dict[str, Any], issue_or_text: str, rich_line: str = "") -> str:
        hinted = line_scopes.get(rich_line, "")
        if hinted:
            return hinted
        needle = _text(issue_or_text)
        if needle:
            for coll_key in ("in_flight", "backlog", "recent_completed"):
                for entry in (shard.get(coll_key) or []):
                    if isinstance(entry, dict) and needle in {_text(entry.get("issue")), _text(entry.get("topic_id")), _text(entry.get("slot_id"))}:
                        return _classify_hub_scope(entry, rich_line=rich_line, domain_map=domain_map)
        return _classify_hub_scope(issue_or_text, rich_line=rich_line, domain_map=domain_map)

    # Pending decisions (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_pending = shard.get("pending_decisions") or []
        rich_lines = pending_by_host.get(host) or []
        all_pending_seen: list[str] = []
        if host != local_hostname and rich_lines and shard_pending:
            for line in rich_lines:
                sc = _scope_for_host_issue(shard, "", line)
                _append_unique(pending_by_scope, sc, line)
                all_pending_seen.append(line)
            for item in shard_pending:
                item_str = _text(item.get("issue") or item.get("summary") if isinstance(item, dict) else item)
                if item_str and not any(item_str in existing for existing in all_pending_seen):
                    line = f"- [ ] {_format_linear_issue_link(item_str)} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map) if isinstance(item, dict) else _scope_for_host_issue(shard, item_str, line)
                    _append_unique(pending_by_scope, sc, line)
                    all_pending_seen.append(line)
        else:
            for item in shard_pending:
                if isinstance(item, dict):
                    issue_ref = _format_linear_issue_link(_text(item.get("issue")))
                    desc = _text(item.get("summary") or item.get("title"))
                    line = f"- [ ] {issue_ref}{(' · ' + desc) if desc else ''} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map)
                else:
                    raw_item = _text(item)
                    matched_rich = next((rl for rl in rich_lines if raw_item and raw_item in rl), "")
                    if matched_rich:
                        line = matched_rich
                    elif re.match(r"^TIA-\d+$", raw_item, re.I):
                        line = f"- [ ] {_format_linear_issue_link(raw_item)} · 待 Owner 拍板 (`{host}`)"
                    else:
                        line = f"- [ ] {raw_item} (`{host}`)"
                    sc = _scope_for_host_issue(shard, raw_item, line)
                _append_unique(pending_by_scope, sc, line)

    # In-Flight & Retained by Device (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_in_flight = shard.get("in_flight") or []
        rich_rows = in_flight_by_host.get(host) or []
        if host != local_hostname and rich_rows and shard_in_flight:
            for r in rich_rows:
                cells = [c.strip("` ").strip() for c in r.strip("|").split("|")]
                row_topic = cells[1] if len(cells) > 1 else ""
                matched_entry = next(
                    (it for it in shard_in_flight if isinstance(it, dict) and _text(it.get("topic_id") or it.get("slot_id")) == row_topic),
                    None,
                )
                sc = _classify_hub_scope(matched_entry or row_topic, rich_line=r, domain_map=domain_map, hint_scope=line_scopes.get(r, ""))
                _append_unique(in_flight_by_scope, sc, r)
        else:
            hb = _format_cst_heartbeat(_text(shard.get("updated_at") or state.get("updated_at")))
            for item in shard_in_flight:
                if not isinstance(item, dict):
                    continue
                topic = _text(item.get("topic_id") or item.get("slot_id"), "—")
                session_id = _text(item.get("session_id") or item.get("target"), "—")
                pane_id = _text(item.get("pane_id"))
                session_cell = f"`{session_id}` (`{pane_id}`)" if pane_id else f"`{session_id}`"
                issue_cell = _format_linear_issue_link(_text(item.get("issue")))
                summary_cell = _text(item.get("summary"), "—").replace("|", "\\|").replace("\n", "&#10;")
                item_hb = _format_cst_heartbeat(_text(item.get("updated_at") or shard.get("updated_at") or state.get("updated_at"))) or hb
                row_str = f"| `{host}` | `{topic}` | {session_cell} | {issue_cell} | {summary_cell} | `{item_hb}` |"
                sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map)
                _append_unique(in_flight_by_scope, sc, row_str)

    # Deferred Backlog by Device (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_backlog = shard.get("backlog") or []
        rich_backlog = backlog_by_host.get(host) or []
        if host != local_hostname and rich_backlog and not shard_backlog:
            for line in rich_backlog:
                sc = _classify_hub_scope(None, rich_line=line, domain_map=domain_map, hint_scope=line_scopes.get(line, ""))
                _append_unique(backlog_by_scope, sc, line)
        else:
            for item in shard_backlog:
                if isinstance(item, dict):
                    b_topic = _text(item.get("topic_id") or item.get("id") or item.get("title"), "backlog")
                    b_summary = _text(item.get("summary") or item.get("intent") or item.get("title"), "暂缓执行")
                    b_issue = _text(item.get("issue"))
                    issue_part = f" · {_format_linear_issue_link(b_issue)}" if b_issue else ""
                    line = f"- ⏸️ `{b_topic}`{issue_part} · {b_summary} (`{host}`)"
                    sc = _classify_hub_scope(item, rich_line=line, domain_map=domain_map)
                else:
                    raw_b = _text(item)
                    line = f"- ⏸️ {raw_b} (`{host}`)"
                    sc = _classify_hub_scope(raw_b, rich_line=line, domain_map=domain_map)
                _append_unique(backlog_by_scope, sc, line)

    # Recent Completed Rollup (Work vs Non-Work)
    for host in hosts:
        shard = devices.get(host, {})
        shard_completed = shard.get("recent_completed") or []
        rich_rows = completed_by_host.get(host) or []
        seen_comp_rows: list[str] = []
        if host != local_hostname and rich_rows:
            for r in rich_rows:
                sc = _classify_hub_scope(None, rich_line=r, domain_map=domain_map, hint_scope=line_scopes.get(r, ""))
                _append_unique(completed_by_scope, sc, r)
                seen_comp_rows.append(r)
            for item in shard_completed:
                item_key = _text(item.get("issue") if isinstance(item, dict) else item)
                if item_key and not any(item_key in existing for existing in seen_comp_rows):
                    if isinstance(item, dict):
                        topic = _text(item.get("topic_id") or item.get("target_repo"), "—")
                        issue_cell = _format_linear_issue_link(item_key)
                        summary_cell = _text(item.get("summary"), "已完成").replace("|", "\\|")
                        row_str = f"| `{host}` | `{topic}` | {issue_cell} | {summary_cell} |"
                        sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map)
                    else:
                        row_str = f"| `{host}` | `—` | {_format_linear_issue_link(item_key)} | 已完成 |"
                        sc = _scope_for_host_issue(shard, item_key, row_str)
                    _append_unique(completed_by_scope, sc, row_str)
                    seen_comp_rows.append(row_str)
        else:
            for item in shard_completed:
                if isinstance(item, dict):
                    item_key = _text(item.get("issue") or item.get("slot_id"))
                    matched_rich = next((rr for rr in rich_rows if item_key and item_key in rr), "")
                    if matched_rich and item.get("summary") in {"", "—"}:
                        row_str = matched_rich
                    else:
                        topic = _text(item.get("topic_id") or item.get("target_repo") or item.get("slot_id"), "—")
                        issue_cell = _format_linear_issue_link(item_key) if re.match(r"^TIA-\d+$", item_key, re.I) else (f"`{item_key}`" if item_key else "—")
                        pr_ref = _text(item.get("pr"))
                        if pr_ref:
                            issue_cell = f"{issue_cell} / {pr_ref}"
                        summary_cell = _text(item.get("summary"), "已完成").replace("|", "\\|")
                        row_str = f"| `{host}` | `{topic}` | {issue_cell} | {summary_cell} |"
                    sc = _classify_hub_scope(item, rich_line=row_str, domain_map=domain_map, hint_scope=line_scopes.get(row_str, ""))
                else:
                    raw_val = _text(item)
                    matched_rich = next((rr for rr in rich_rows if raw_val and raw_val in rr), "")
                    row_str = matched_rich or f"| `{host}` | `—` | {_format_linear_issue_link(raw_val)} | 已完成 |"
                    sc = _scope_for_host_issue(shard, raw_val, row_str)
                _append_unique(completed_by_scope, sc, row_str)

    canonical_state = {
        "schema": M2_SYNC_SCHEMA,
        "updated_at": _text(state.get("updated_at")) or _cst_now_iso(),
        "devices": devices,
    }
    state_json_str = json.dumps(canonical_state, ensure_ascii=False, indent=2)

    work_pending = pending_by_scope["work"] or ["- 暂无 Work 阻塞或待拍板事项"]
    work_in_flight = in_flight_by_scope["work"] or ["| `—` | `—` | `—` | — | 当前无 Work 在途或常驻 M2 | `—` |"]
    work_backlog = backlog_by_scope["work"] or ["- 暂无 Work 暂缓 Backlog"]
    work_completed = completed_by_scope["work"] or ["| `—` | `—` | — | 暂无 Work 完成记录 |"]

    non_work_pending = pending_by_scope["non-work"] or ["- 暂无 Non-Work 阻塞或待拍板事项"]
    non_work_in_flight = in_flight_by_scope["non-work"] or ["| `—` | `—` | `—` | — | 当前无 Non-Work 在途 M2 | `—` |"]
    non_work_backlog = backlog_by_scope["non-work"] or ["- 暂无 Non-Work 暂缓 Backlog"]
    non_work_completed = completed_by_scope["non-work"] or ["| `—` | `—` | — | 暂无 Non-Work 完成记录 |"]

    lines = [
        M2_SYNC_HUB_MARKER,
        "",
        "## 🌐 跨设备 `/personal-m2-assistant` 监督与推进总账本",
        "",
        "> **用途**：作为 `leedeMac-Mini`、`MacBook-Pro` 与移动端 Linear 之间的唯一跨设备 M2 监督同步中枢，在同一个 Issue 内拆分为 **Part 1 · Work（工作业务）** 与 **Part 2 · Non-Work（非工作 / 个人与基建）** 两部分。任一设备执行 `--apply` / `--watch` / `--status` / `--gc` 时按设备分片（`devices.<hostname>`）自动对账更新；直接在本单评论指令即可通过 `linear-bot-bridge` 跨设备触发推进。",
        "",
        "---",
        "",
        "## 💼 Part 1 · Work（工作业务 M2）",
        "",
        "### 1.1 🚨 Work 待拍板 / 跨设备待选型事项",
        "",
        *work_pending,
        "",
        "### 1.2 ⏳ Work 各设备在途与常驻监督（In-Flight & Retained）",
        "",
        "| 设备 (`Host`) | M2 话题 (`Topic`) | 会话 / Pane | 关联单据 | 当前推进阶段 (`M1 Stage`) | 最近心跳 (`CST`) |",
        "| -- | -- | -- | -- | -- | -- |",
        *work_in_flight,
        "",
        "### 1.3 🗂️ Work 暂缓 Backlog（Deferred · 不自动派发）",
        "",
        *work_backlog,
        "",
        "### 1.4 ✅ Work 最近已完成（Recent Completed）",
        "",
        "| 设备 (`Host`) | 话题 / 仓库 | 单据 / PR | 一句话结果 |",
        "| -- | -- | -- | -- |",
        *work_completed,
        "",
        "---",
        "",
        "## 🛠️ Part 2 · Non-Work（非工作 / 个人与基建 M2）",
        "",
        "### 2.1 🚨 Non-Work 待拍板 / 跨设备待选型事项",
        "",
        *non_work_pending,
        "",
        "### 2.2 ⏳ Non-Work 各设备在途与继续推进（In-Flight & Continuing）",
        "",
        "| 设备 (`Host`) | M2 话题 (`Topic`) | 会话 / Pane | 关联单据 | 当前推进阶段 (`M1 Stage`) | 最近心跳 (`CST`) |",
        "| -- | -- | -- | -- | -- | -- |",
        *non_work_in_flight,
        "",
        "### 2.3 🗂️ Non-Work 暂缓 Backlog（Deferred · 不自动派发）",
        "",
        *non_work_backlog,
        "",
        "### 2.4 ✅ Non-Work 最近已完成（Recent Completed）",
        "",
        "| 设备 (`Host`) | 话题 / 仓库 | 单据 / PR | 一句话结果 |",
        "| -- | -- | -- | -- |",
        *non_work_completed,
        "",
        "---",
        "",
        "<details>",
        "<summary>🤖 Machine-Readable Cross-Device State (M2-SYNC-STATE v1)</summary>",
        "",
        M2_SYNC_STATE_START,
        "",
        "```json",
        state_json_str,
        "```",
        "",
        M2_SYNC_STATE_END,
        "</details>",
    ]
    if gtt_suffix:
        lines.extend(["", gtt_suffix])
    return "\n".join(lines).rstrip() + "\n"


def _substantive_hub_signature(description: str) -> tuple[dict[str, Any], str]:
    parsed = parse_cross_device_hub_description(description)
    state = copy.deepcopy(parsed.get("state") or {})
    state.pop("updated_at", None)
    devices = state.get("devices")
    if isinstance(devices, dict):
        for host_shard in devices.values():
            if isinstance(host_shard, dict):
                host_shard.pop("updated_at", None)
                for item in (host_shard.get("in_flight") or []):
                    if isinstance(item, dict):
                        item.pop("updated_at", None)
    body = str(description or "").split("<details>", 1)[0]
    body_norm = re.sub(r"\|\s*`\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}`\s*\|", "| `<CST>` |", body).strip()
    return state, body_norm


def _load_cross_device_cache(cache_path: Path | None) -> dict[str, Any]:
    if cache_path is None or not cache_path.is_file():
        return {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("devices"), dict):
            return loaded
    except (OSError, ValueError):
        pass
    return {"schema": M2_SYNC_SCHEMA, "updated_at": "", "devices": {}}


def _save_cross_device_cache(cache_path: Path | None, state: dict[str, Any]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + f".{os.getpid()}.tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(cache_path)
    except OSError:
        pass


def _extract_remote_devices_summary(
    cross_device_state: dict[str, Any] | None,
    *,
    local_hostname: str | None = None,
) -> dict[str, Any]:
    host = _resolve_local_hostname(local_hostname)
    devices = (cross_device_state or {}).get("devices") if isinstance(cross_device_state, dict) else {}
    if not isinstance(devices, dict):
        devices = {}
    remote_devices: dict[str, dict[str, Any]] = {}
    remote_in_flight: list[dict[str, Any]] = []
    remote_pending: list[dict[str, Any]] = []
    for other_host, shard in devices.items():
        if not isinstance(shard, dict) or str(other_host).casefold() == host.casefold():
            continue
        in_flight = [dict(x) for x in (shard.get("in_flight") or []) if isinstance(x, dict)]
        pending = list(shard.get("pending_decisions") or [])
        completed = list(shard.get("recent_completed") or [])
        if in_flight or pending or completed:
            remote_devices[str(other_host)] = {
                "updated_at": _text(shard.get("updated_at")),
                "in_flight": in_flight,
                "pending_decisions": pending,
                "recent_completed": completed,
            }
        for item in in_flight:
            remote_in_flight.append({"host": str(other_host), **item})
        if pending:
            remote_pending.append({"host": str(other_host), "decisions": pending})
    return {
        "local_hostname": host,
        "remote_devices": remote_devices,
        "cross_device_in_flight": remote_in_flight,
        "cross_device_pending_decisions": remote_pending,
    }


def _render_cross_device_rollup_lines(
    cross_device_summary: dict[str, Any],
) -> list[str]:
    remote_in_flight = cross_device_summary.get("cross_device_in_flight") or []
    remote_pending = cross_device_summary.get("cross_device_pending_decisions") or []
    if not remote_in_flight and not remote_pending:
        return []
    lines = ["#### 🌐 4. 跨设备在途与待拍板（Cross-Device M2 Sync Hub · `TIA-463`）"]
    for group in remote_pending:
        host = _text(group.get("host"))
        decisions = ", ".join(_text(d.get("issue") if isinstance(d, dict) else d) for d in (group.get("decisions") or []) if _text(d.get("issue") if isinstance(d, dict) else d))
        if decisions:
            lines.append(f"- 🚨 `[Host: {host}]` 待拍板 / 待选型: {decisions}")
    for item in remote_in_flight:
        host = _text(item.get("host"))
        topic = _text(item.get("topic_id") or item.get("slot_id"), "—")
        session_id = _text(item.get("session_id") or item.get("target"), "—")
        pane_id = _text(item.get("pane_id"))
        pane_str = f" (`{pane_id}`)" if pane_id else ""
        issue = _text(item.get("issue"), "Linear 未绑定")
        summary = _text(item.get("summary"), "—")
        lines.append(f"- ⏳ `[Host: {host}]` `{topic}` · `{session_id}`{pane_str} · {issue} · {summary}")
    return lines


def _attach_cross_device_to_rollup(
    board_rollup: dict[str, Any],
    cross_device_state: dict[str, Any] | None = None,
    *,
    local_hostname: str | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    state = cross_device_state if isinstance(cross_device_state, dict) else _load_cross_device_cache(cache_path)
    summary = _extract_remote_devices_summary(state, local_hostname=local_hostname)
    board_rollup["local_hostname"] = summary["local_hostname"]
    board_rollup["remote_devices"] = summary["remote_devices"]
    board_rollup["cross_device_in_flight"] = summary["cross_device_in_flight"]
    board_rollup["cross_device_pending_decisions"] = summary["cross_device_pending_decisions"]
    extra_lines = _render_cross_device_rollup_lines(summary)
    if extra_lines:
        base_md = _text(board_rollup.get("unified_rollup_markdown"))
        marker_header = "#### 🌐 4. 跨设备在途与待拍板"
        if marker_header in base_md:
            base_md = base_md.split(marker_header, 1)[0].rstrip()
        board_rollup["unified_rollup_markdown"] = base_md + "\n" + "\n".join(extra_lines)
    return board_rollup


def sync_cross_device_hub(
    board_rollup: dict[str, Any],
    *,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    cache_path: Path | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    now_iso: str = "",
) -> dict[str, Any]:
    issue_id = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
    host = _resolve_local_hostname(local_hostname)
    exec_runner = linear_runner or _run_command
    linear_script = _text(os.environ.get("M2_LINEAR_SYNC_SCRIPT"), str(LINEAR_SYNC_SCRIPT_PATH))
    remote_issue: dict[str, Any] = {}
    if isinstance(hub_issue_payload, dict):
        remote_issue = dict(hub_issue_payload.get("issue") if isinstance(hub_issue_payload.get("issue"), dict) else hub_issue_payload)
    else:
        try:
            get_res = exec_runner(
                [sys.executable, linear_script, "get-issue", "--id", issue_id],
                timeout=15.0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"ok": False, "updated": False, "hub_issue": issue_id, "local_hostname": host, "error": str(error)}
        parsed_get = _parse_json_output(getattr(get_res, "stdout", "")) if getattr(get_res, "returncode", 1) == 0 else None
        if isinstance(parsed_get, dict) and isinstance(parsed_get.get("issue"), dict):
            remote_issue = dict(parsed_get["issue"])
        else:
            # Fallback: search via list-issues for [M2-Sync]
            try:
                list_res = exec_runner(
                    [sys.executable, linear_script, "list-issues", "--team", "TIA", "--query", "M2-Sync", "--limit", "10"],
                    timeout=15.0,
                )
                parsed_list = _parse_json_output(getattr(list_res, "stdout", "")) if getattr(list_res, "returncode", 1) == 0 else None
                candidates = (parsed_list or {}).get("issues") if isinstance(parsed_list, dict) else []
                found_id = next(
                    (
                        _text(item.get("identifier"))
                        for item in (candidates or [])
                        if isinstance(item, dict) and "[m2-sync]" in _text(item.get("title")).casefold()
                    ),
                    "",
                )
                if found_id:
                    issue_id = found_id
                    retry_res = exec_runner(
                        [sys.executable, linear_script, "get-issue", "--id", issue_id],
                        timeout=15.0,
                    )
                    parsed_retry = _parse_json_output(getattr(retry_res, "stdout", "")) if getattr(retry_res, "returncode", 1) == 0 else None
                    if isinstance(parsed_retry, dict) and isinstance(parsed_retry.get("issue"), dict):
                        remote_issue = dict(parsed_retry["issue"])
            except (OSError, subprocess.SubprocessError):
                pass
        if not remote_issue:
            err_msg = _text(getattr(get_res, "stderr", "") or getattr(get_res, "stdout", ""), f"unable to fetch hub issue {issue_id}")
            return {"ok": False, "updated": False, "hub_issue": issue_id, "local_hostname": host, "error": err_msg}

    existing_description = str(remote_issue.get("description") or "")
    parsed_hub = parse_cross_device_hub_description(existing_description)
    merged_state = copy.deepcopy(parsed_hub["state"])
    devices = merged_state.setdefault("devices", {})
    timestamp = now_iso or _cst_now_iso()
    local_shard = _build_local_device_shard(
        board_rollup,
        previous_shard=devices.get(host),
        now_iso=timestamp,
    )
    devices[host] = local_shard
    merged_state["schema"] = M2_SYNC_SCHEMA
    merged_state["updated_at"] = timestamp

    rendered_description = render_cross_device_hub_markdown(
        merged_state,
        preserved_sections=parsed_hub,
        local_hostname=host,
    )
    _save_cross_device_cache(cache_path, merged_state)

    old_sig = _substantive_hub_signature(existing_description)
    new_sig = _substantive_hub_signature(rendered_description)
    if existing_description.strip() and old_sig == new_sig:
        return {
            "ok": True,
            "updated": False,
            "skipped_reason": "no_substantive_change",
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "description": existing_description,
        }

    if hub_issue_payload is not None and linear_runner is None:
        return {
            "ok": True,
            "updated": True,
            "simulated": True,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "description": rendered_description,
        }

    try:
        upd_res = exec_runner(
            [sys.executable, linear_script, "update-issue", "--id", _text(remote_issue.get("identifier"), issue_id), "--description", rendered_description],
            timeout=20.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "ok": False,
            "updated": False,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "error": str(error),
        }
    if getattr(upd_res, "returncode", 1) != 0:
        return {
            "ok": False,
            "updated": False,
            "hub_issue": _text(remote_issue.get("identifier"), issue_id),
            "local_hostname": host,
            "state": merged_state,
            "error": _text(getattr(upd_res, "stderr", "") or getattr(upd_res, "stdout", ""), "update-issue failed"),
        }
    return {
        "ok": True,
        "updated": True,
        "hub_issue": _text(remote_issue.get("identifier"), issue_id),
        "local_hostname": host,
        "state": merged_state,
        "description": rendered_description,
    }


def apply_plan(plan: dict[str, Any], *, approve: bool = False, register_new_domains: bool = False, cleanup_duplicates: bool = False, replace_stale_m2: bool = False, domain_map_file: str | Path | None = None, plan_file: str | Path | None = None, apply_receipt_file: str | Path | None = None, state_file: str | Path | None = None, reset_state: bool = False, handoff_root: str | Path | None = None, agent_records: list[dict[str, Any]] | None = None, transport: str = "auto", sync_linear: bool = False, no_sync_linear: bool = False, hub_issue: str | None = None, local_hostname: str | None = None, linear_runner: CommandRunner | None = None, hub_issue_payload: dict[str, Any] | None = None, caller_bot: str = "", no_supervision: bool = False, no_routine: bool = False, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
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
    auto_replace = any(t.get("m2_action") == "REPLACE_STALE_M2" for t in targets)
    cleanup_mode = "replace" if replace_stale_m2 else "replace_context" if auto_replace else "duplicates" if cleanup_duplicates else "none"
    cleanup_receipts = _cleanup_stale(plan, cleanup_mode, runner=runner, dry_run=is_stdout_only, dry_run_sessions=stdout_candidate_sessions)
    cleanup_state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True)
    pruned_cleanup_slots = _prune_closed_slots(cleanup_state_path, cleanup_receipts) if not is_stdout_only else []
    closed_sessions = {item.get("session_id") for item in cleanup_receipts if item.get("ok") and not item.get("skipped") and not item.get("simulated")}
    used_herdr_names: set[str] = set()
    created_workspaces: dict[str, str] = {}
    receipts: list[dict[str, Any]] = []
    dispatched_targets: list[dict[str, Any]] = []
    target_seq = 0
    for wave in sorted({int(item.get("wave_order", 1)) for item in targets}):
        for target in [item for item in targets if int(item.get("wave_order", 1)) == wave]:
            target_seq += 1
            dispatch_target = dict(target)
            initial_transport = _resolve_target_transport(dispatch_target, plan, transport)
            target_is_stdout = is_stdout_only or initial_transport == "stdout"
            if (replace_stale_m2 or target.get("m2_action") == "REPLACE_STALE_M2") and target.get("cleanup_candidates"):
                candidate_ids = {_text(c.get("session_id")) for c in target.get("cleanup_candidates", []) if isinstance(c, dict)}
                if not target_is_stdout and candidate_ids and not candidate_ids.issubset(closed_sessions):
                    failures = [item for item in cleanup_receipts if _text(item.get("session_id")) in candidate_ids and (item.get("skipped") or not item.get("ok"))]
                    raise RouterError(f"stale pane close failed; refusing replacement spawn: {failures}")
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
                receipt = _apply_herdr(dispatch_target, plan, prompt, runner=runner, index=target_seq, used_names=used_herdr_names, created_workspaces=created_workspaces, domain_map_file=domain_map_file)
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
            rec_entry: dict[str, Any] = {
                "wave_order": wave,
                "topic_id": _text(target.get("topic_id")),
                "target_repo": target.get("target_repo"),
                "pane_id": pane_id_val,
                "session_id": session_id_val,
                **receipt,
            }
            if _text(target.get("slot_id")):
                rec_entry["slot_id"] = _text(target.get("slot_id"))
            if _text(target.get("task_label")):
                rec_entry["task_label"] = _text(target.get("task_label"))
            receipts.append(rec_entry)
            dispatched_targets.append(dispatch_target)
    plan_id = _text(plan.get("plan_id"), "m2p-plan")
    safe_plan_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", plan_id).strip("-") or "m2p-plan"
    resolved_plan_file = _text(plan_file or plan.get("plan_file"), f"/tmp/m2-plan-{safe_plan_id}.json")
    resolved_receipt_path = Path(apply_receipt_file).expanduser() if apply_receipt_file else Path(f"/tmp/m2-apply-{safe_plan_id}.json")
    resolved_state_path = _resolve_supervision_state_path(
        state_file,
        plan_file=plan_file,
        apply_receipt_file=apply_receipt_file,
        safe_plan_id=safe_plan_id,
        persist_by_default=True,
    )
    assert resolved_state_path is not None
    binary = _text(os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    live_records = agent_records
    if live_records is None:
        live_records = _live_agent_records(binary, runner=runner) if "herdr" in resolved_initial_transports and not is_stdout_only else []

    def _register_dispatched_slots(state: dict[str, Any]) -> dict[str, Any]:
        slots = [dict(s) for s in (state.get("slots") or []) if isinstance(s, dict)]
        events = [dict(e) for e in (state.get("events") or []) if isinstance(e, dict)]
        used_slot_ids = {_text(s.get("slot_id")).casefold() for s in slots if _text(s.get("slot_id"))}
        for idx, (rec, tgt) in enumerate(zip(receipts, dispatched_targets)):
            match_idx = _match_existing_slot_index(slots, rec, tgt)
            if match_idx is not None:
                existing = dict(slots[match_idx])
                history = [dict(h) for h in (existing.get("history") or []) if isinstance(h, dict)]
                history.append({
                    "run_seq": int(existing.get("run_seq") or 1),
                    "run_label": _text(existing.get("run_label") or existing.get("slot_id")),
                    "plan_id": _text(existing.get("plan_id")),
                    "status": _text(existing.get("status")),
                    "visible_summary": _text(existing.get("visible_summary")),
                })
                run_seq = int(existing.get("run_seq") or 1) + 1
                slot_id = _text(existing.get("slot_id"))
                base_label = _text(existing.get("base_label") or slot_id)
                explicit_label = _text(tgt.get("task_label") or rec.get("task_label"))
                run_label = explicit_label if explicit_label else f"{base_label}{run_seq}"
                existing.update({
                    "slot_id": slot_id,
                    "base_label": base_label,
                    "run_seq": run_seq,
                    "run_label": run_label,
                    "plan_id": plan_id,
                    "wave_order": int(rec.get("wave_order") or 1),
                    "topic_id": _text(rec.get("topic_id") or existing.get("topic_id")),
                    "target_repo": _text(rec.get("target_repo") or existing.get("target_repo"), "unspecified"),
                    "target": _text(rec.get("target") or existing.get("target")),
                    "pane_id": _text(rec.get("pane_id") or existing.get("pane_id")),
                    "session_id": _text(rec.get("session_id") or existing.get("session_id")),
                    "status": "running",
                    "raw_status": "running",
                    "needs_owner_attention": False,
                    "blocked_reason": "",
                    "auto_unblocked": [],
                    "visible_summary": "",
                    "history": history,
                })
                slots[match_idx] = existing
                rec["slot_id"] = slot_id
                rec["run_seq"] = run_seq
                rec["run_label"] = run_label
            else:
                explicit_slot = _text(tgt.get("slot_id") or rec.get("slot_id"))
                explicit_label = _text(tgt.get("task_label") or rec.get("task_label"))
                raw_slot = (
                    explicit_slot
                    or explicit_label
                    or _text(rec.get("topic_id"))
                    or _text(rec.get("target"))
                    or _text(rec.get("target_repo"))
                    or f"m2-{idx + 1}"
                )
                slot_id = raw_slot
                suffix = 2
                while slot_id.casefold() in used_slot_ids:
                    slot_id = f"{raw_slot}-{suffix}"
                    suffix += 1
                used_slot_ids.add(slot_id.casefold())
                base_label = explicit_slot or explicit_label or slot_id
                run_label = explicit_label or base_label
                run_seq = 1
                slots.append({
                    "slot_id": slot_id,
                    "base_label": base_label,
                    "run_seq": run_seq,
                    "run_label": run_label,
                    "plan_id": plan_id,
                    "wave_order": int(rec.get("wave_order") or 1),
                    "topic_id": _text(rec.get("topic_id")),
                    "target_repo": _text(rec.get("target_repo"), "unspecified"),
                    "target": _text(rec.get("target")),
                    "pane_id": _text(rec.get("pane_id")),
                    "session_id": _text(rec.get("session_id")),
                    "status": "running",
                    "raw_status": "running",
                    "needs_owner_attention": False,
                    "blocked_reason": "",
                    "auto_unblocked": [],
                    "visible_summary": "",
                    "history": [],
                })
                rec["slot_id"] = slot_id
                rec["run_seq"] = run_seq
                rec["run_label"] = run_label
            slot = slots[_match_existing_slot_index(slots, rec, tgt)]
            for key in (*ROLLUP_METADATA_KEYS, "handoff_in_progress", "goal_tl"):
                slot.pop(key, None)
            slot.update(_slot_metadata(tgt))
        events.append({
            "seq": len(events) + 1,
            "type": "apply",
            "plan_id": plan_id,
            "dispatched_labels": [_text(r.get("run_label")) for r in receipts],
        })
        state = _discover_and_enrich_global_m2_slots(
            {"schema_version": 1, "slots": slots, "events": events}, agent_records=live_records,
            runner=runner, herdr_bin=binary, handoff_root=handoff_root,
        )
        state["slots"] = _compute_board_rollup(state)["slots"]
        return state

    board_state = _with_locked_supervision_state(resolved_state_path, _register_dispatched_slots, reset=reset_state)
    board_rollup = _compute_board_rollup(board_state)
    cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=True,
        state_path=resolved_state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    linear_sync_result: dict[str, Any]
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    else:
        linear_sync_result = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    _attach_cross_device_to_rollup(
        board_rollup,
        linear_sync_result.get("state") if isinstance(linear_sync_result.get("state"), dict) else None,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )

    handoff_watch_arg = f" --handoff-root {shlex.quote(str(handoff_root))}" if handoff_root else ""
    quota_protection = _resolve_quota_protection(
        plan=plan,
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=live_records,
        herdr_bin=binary,
        runner=runner,
        probe_herdr=not is_stdout_only,
    )
    grok_supervision_disabled = bool(quota_protection.get("active"))
    watchdog_tasks: list[dict[str, Any]] = []
    for rec in receipts:
        slot_id = _text(rec.get("slot_id"), "m2")
        run_seq = int(rec.get("run_seq") or 1)
        run_label = _text(rec.get("run_label"), slot_id)
        safe_slot = re.sub(r"[^a-zA-Z0-9_.-]+", "-", slot_id).strip("-") or "m2"
        if grok_supervision_disabled:
            rec["watchdog_cmd"] = None
            rec["watchdog_copilot_cli"] = None
            continue
        target_watch_cmd = (
            f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
            f"--watch --plan-file {shlex.quote(resolved_plan_file)} "
            f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
            f"--state-file {shlex.quote(str(resolved_state_path))} "
            f"--watch-target {shlex.quote(slot_id)} "
            f"--interval 15 --max-wait 2400 --json{handoff_watch_arg}"
        )
        target_cli = {
            "tool": "bash",
            "mode": "async",
            "detach": True,
            "shellId": f"watch-m2-{safe_plan_id}-{safe_slot}-r{run_seq}",
            "description": f"Watch M2 {run_label} ({slot_id}) until quiescent and update cumulative rollup",
            "command": target_watch_cmd,
        }
        rec["watchdog_cmd"] = target_watch_cmd
        rec["watchdog_copilot_cli"] = target_cli
        watchdog_tasks.append({
            "slot_id": slot_id,
            "run_seq": run_seq,
            "run_label": run_label,
            "topic_id": _text(rec.get("topic_id")),
            "target_repo": _text(rec.get("target_repo")),
            "target": _text(rec.get("target")),
            "pane_id": _text(rec.get("pane_id")),
            "session_id": _text(rec.get("session_id")),
            "watchdog_cmd": target_watch_cmd,
            "watchdog_copilot_cli": target_cli,
        })

    watch_cmd = None if grok_supervision_disabled else (
        f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
        f"--watch --plan-file {shlex.quote(resolved_plan_file)} "
        f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
        f"--state-file {shlex.quote(str(resolved_state_path))} "
        f"--interval 15 --max-wait 2400 --json{handoff_watch_arg}"
    )
    watch_cli = None if grok_supervision_disabled else {
        "tool": "bash",
        "mode": "async",
        "detach": True,
        "shellId": f"watch-m2-{plan_id}",
        "description": f"Watch dispatched target M2 sessions ({plan_id}) until quiescent",
        "command": watch_cmd,
    }
    payload: dict[str, Any] = {
        "ok": True,
        "plan_id": plan.get("plan_id"),
        "approved": True,
        "apply_receipt_path": str(resolved_receipt_path),
        "supervision_state_path": str(resolved_state_path),
        "registered_new_domains": registered,
        "cleanup_receipts": cleanup_receipts,
        "pruned_cleanup_slots": pruned_cleanup_slots,
        "receipts": receipts,
        "watchdog_tasks": watchdog_tasks,
        "unified_rollup_markdown": board_rollup["unified_rollup_markdown"],
        "in_flight_details": board_rollup["in_flight_details"],
        "wave_dependency_chain": board_rollup["wave_dependency_chain"],
        "local_hostname": board_rollup.get("local_hostname"),
        "remote_devices": board_rollup.get("remote_devices", {}),
        "cross_device_in_flight": board_rollup.get("cross_device_in_flight", []),
        "cross_device_pending_decisions": board_rollup.get("cross_device_pending_decisions", []),
        "linear_sync": linear_sync_result,
        "board_rollup": board_rollup,
        "watchdog_cmd": watch_cmd,
        "watchdog_copilot_cli": watch_cli,
    }
    if grok_supervision_disabled:
        payload["quota_protection_mode"] = GROK_QUOTA_PROTECTION_MODE
        payload["quota_protection"] = quota_protection
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


def _strip_tui_box_chrome(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in _text(text).splitlines():
        stripped = re.sub(r"^[\s│┃╭╮╰╯─━┌┐└┘├┤┬┴┼]+|[\s│┃╭╮╰╯─━┌┐└┘├┤┬┴┼]+$", "", raw_line)
        if stripped:
            cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines)


def _extract_dialog_paths(frame_text: str) -> list[str]:
    unboxed = _strip_tui_box_chrome(frame_text)
    single = _extract_single_dialog_path(unboxed)
    if single is not None:
        return [single]
    # If inline header pattern had multiple paths on one line, do not fall through
    for line in unboxed.splitlines():
        if re.search(r"(?:Allow\s+(?:path|directory)\s+access\s+to|access\s+to)\s+", line, re.I):
            return []
    paths: list[str] = []
    for raw_line in unboxed.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            continue
        if not (line.startswith("/") or line.startswith("~/") or line == "~"):
            continue
        if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", line):
            return []
        if any(ch in line for ch in ("*", "?", "[", "]", "$", "`", ";", "|", "&", "<", ">", '"', "'")):
            return []
        if line not in paths:
            paths.append(line)
    return paths


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
    r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|Read\s+access\s+covers\s+this\s+directory|"
    r"\bCurate\s+Memory\b|curate\s+this\s+memory|"
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

    raw_active_frame = _extract_active_bottom_frame(text)
    active_frame = _strip_tui_box_chrome(raw_active_frame)
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
        re.search(
            r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|Read\s+access\s+covers\s+this\s+directory|\baccess\s+to\b)",
            active_frame,
            re.I,
        )
    )
    is_folder_trust_prompt = bool(
        re.search(
            r"(?:Confirm\s+folder\s+trust|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|Trust\s+folder|Yes,\s*I\s+trust)",
            active_frame,
            re.I,
        )
    )
    is_curate_memory_prompt = bool(
        re.search(r"(?:Curate\s+Memory|curate\s+this\s+memory|\bstore_memory\b|\bvote_memory\b)", active_frame, re.I)
        and re.search(r"(?:\?|[>❯●◉▶]|\bAllow\b|\bConfirm\b|\bYes\b|1[.)])", active_frame, re.I)
    )

    # Also detect any unrecognized question lines in active_frame alongside a dialog header
    other_questions = [
        ln.strip()
        for ln in active_frame.splitlines()
        if ln.strip().endswith("?")
        and not re.search(
            r"(?:Allow\s+(?:path|directory)\s+access|Read\s+access\s+covers\s+this\s+directory|"
            r"Do\s+you\s+want\s+to\s+allow\s+this|Allow\s+Copilot\s+to\s+curate\s+this\s+memory|"
            r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|"
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
        dialog_paths = _extract_dialog_paths(raw_active_frame)
        if dialog_paths and all(_path_is_under_safe_roots(p, safe_roots) for p in dialog_paths):
            kind = "allow_path_access"
            has_tui_remember = (
                any(ch in raw_active_frame for ch in ("│", "╭", "╰"))
                and bool(re.search(r"(?m)^[>❯]\s*1[.)]\s*Yes\b", active_frame, re.I))
                and bool(re.search(r"(?m)^2[.)]\s*Yes,\s*and\s+remember\b", active_frame, re.I))
            )
            keys = ["down", "enter"] if has_tui_remember else ["enter"]
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


_VOLATILE_TUI_CHROME_RE = re.compile(
    r"(?:Session:\s*[\d.]+\s*AIC\s+used"
    r"|←\s*open\s+sidebar"
    r"|Interactive\s*·\s*Manual\s+Approval"
    r"|(?:Working|Thinking)\s*·\s*[\d.]+\s*[KMG]?i?B\b.*context\s*\(\d+%\))",
    re.I,
)


def _summarize_visible_lines(text: str, *, max_lines: int = 8) -> str:
    raw_lines = [re.sub(r"\s+", " ", line).strip(" ┃│╻╹▄▀") for line in _text(text).splitlines()]
    word_lines = [line for line in raw_lines if re.search(r"\w", line)]
    filtered = [line for line in word_lines if not _VOLATILE_TUI_CHROME_RE.search(line)]
    chosen = filtered if filtered else word_lines
    return "\n".join(chosen[-max_lines:])[-800:]


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
    watch_target: str = "",
    state_file: str | Path | None = None,
    handoff_root: str | Path | None = None,
    interval: float = 15.0,
    max_wait: float = 2400.0,
    max_polls: int | None = None,
    herdr_bin: str | None = None,
    dsh_state_url: str = "",
    dsh_token: str = "",
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    force_watch: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    runner: CommandRunner = _run_command,
    fetcher: Callable[..., Any] = _fetch_json,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll dispatched target M2s until quiescent, auto-unblocking safe Allow dialogs only with verified apply_receipt."""
    binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    if not force_watch:
        quota_protection = _resolve_quota_protection(
            plan=plan,
            apply_receipt=apply_receipt,
            caller_bot=caller_bot,
            no_supervision=no_supervision,
            no_routine=no_routine,
            agent_records=agent_records,
            herdr_bin=binary,
            runner=runner,
        )
        if bool(quota_protection.get("active")):
            return {
                "ok": True,
                "plan_id": _text(plan.get("plan_id")),
                "watch_target": _text(watch_target),
                "all_settled": True,
                "skipped": True,
                "polls": 0,
                "reason": "grok_bot_supervision_disabled",
                "quota_protection_mode": GROK_QUOTA_PROTECTION_MODE,
                "quota_protection": quota_protection,
                "targets": [],
                "summary_markdown": (
                    "### M2 Watch Skipped (`grok_bot_supervision_disabled`)\n"
                    "- **Quota Protection**: `grok_bot_no_supervision` "
                    "(Grok Bot quota protection active: background `--watch` polling and routines are disabled)."
                ),
            }
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

    all_monitored: list[dict[str, Any]] = []
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
            slot_id = _text(
                rec.get("slot_id")
                or plan_match.get("slot_id")
                or rec.get("task_label")
                or plan_match.get("task_label")
                or rec.get("topic_id")
                or plan_match.get("topic_id")
                or target_ref
                or rec.get("target_repo")
                or plan_match.get("target_repo"),
                f"m2-{idx + 1}",
            )
            run_seq = int(rec.get("run_seq") or plan_match.get("run_seq") or 1)
            run_label = _text(rec.get("run_label") or rec.get("task_label") or plan_match.get("task_label") or slot_id)
            all_monitored.append({
                "slot_id": slot_id,
                "run_seq": run_seq,
                "run_label": run_label,
                "wave_order": int(rec.get("wave_order") or plan_match.get("wave_order") or 1),
                "topic_id": _text(rec.get("topic_id") or plan_match.get("topic_id")),
                "target_repo": _text(rec.get("target_repo") or plan_match.get("target_repo"), "unspecified"),
                "target": target_ref,
                "pane_id": pane_id,
                "session_id": _text(rec.get("session_id") or target_ref),
                "transport": transport,
                "strict_receipt_identity": True,
                **_slot_metadata(plan_match),
                **_slot_metadata(rec),
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
            slot_id = _text(
                pt.get("slot_id")
                or pt.get("task_label")
                or pt.get("topic_id")
                or target_ref
                or pt.get("target_repo"),
                f"m2-{idx}",
            )
            run_seq = int(pt.get("run_seq") or 1)
            run_label = _text(pt.get("run_label") or pt.get("task_label") or slot_id)
            all_monitored.append({
                "slot_id": slot_id,
                "run_seq": run_seq,
                "run_label": run_label,
                "wave_order": int(pt.get("wave_order") or 1),
                "topic_id": _text(pt.get("topic_id")),
                "target_repo": _text(pt.get("target_repo"), "unspecified"),
                "target": target_ref or _text(pt.get("target_repo"), "unspecified"),
                "pane_id": _text(pt.get("pane_id")),
                "session_id": _text(pt.get("session_id") or target_ref),
                "transport": transport,
                "strict_receipt_identity": False,
                **_slot_metadata(pt),
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

    watch_filter = _text(watch_target)
    if watch_filter:
        key_cf = watch_filter.casefold()
        monitored = [
            item for item in all_monitored
            if key_cf in {
                _text(item.get("slot_id")).casefold(),
                _text(item.get("run_label")).casefold(),
                _text(item.get("topic_id")).casefold(),
                _text(item.get("target")).casefold(),
                _text(item.get("pane_id")).casefold(),
                _text(item.get("session_id")).casefold(),
                _text(item.get("target_repo")).casefold(),
            } - {""}
        ]
        if not monitored:
            raise RouterError(f"watch target not found in plan: {watch_filter}")
    else:
        monitored = list(all_monitored)

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

        visible_by_ref: dict[str, str] = {}
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
            visible_by_ref[item["target"] or item["pane_id"]] = visible
            if visible:
                item["visible_summary"] = _summarize_visible_lines(visible)
            item["terminal_title"] = _text(live.get("terminal_title") or live.get("terminal_title_stripped") or live.get("title") or item.get("terminal_title"))
            _enrich_slot_metadata(item, handoff_root=handoff_root, scan_default=runner is _run_command)
            tl_running = any(_text(rec.get("name")) == item.get("goal_tl") and _text(rec.get("agent_status") or rec.get("status")) in {"working", "running"} for rec in herdr_merged)
            if status not in {"blocked", "needs_input"} and (_has_active_visible_tail(visible) or (item.get("handoff_in_progress") and tl_running)):
                status = "working"

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
            **_slot_metadata(item),
            "raw_status": item["status"],
            "quiescent": item["status"] in {"idle", "done"},
            "seen_working": item["seen_working"],
            "slot_id": item["slot_id"],
            "run_seq": item["run_seq"],
            "run_label": item["run_label"],
            "wave_order": item["wave_order"],
            "topic_id": item["topic_id"],
            "target_repo": item["target_repo"],
            "target": item["target"],
            "pane_id": item["pane_id"],
            "session_id": item["session_id"],
            "status": item["status"],
            "auto_unblock_enabled": bool(item["auto_unblock_enabled"]),
            "needs_owner_attention": bool(item["needs_owner_attention"]),
            "blocked_reason": item["blocked_reason"],
            "auto_unblocked": list(item["auto_unblocked"]),
            "visible_summary": item["visible_summary"],
            "completion_seq": item["completion_seq"],
            "state_change_seq": item["state_change_seq"],
        })

    resolved_state_path = _resolve_supervision_state_path(state_file, apply_receipt=apply_receipt)

    def _update_settled_slots(state: dict[str, Any]) -> dict[str, Any]:
        slots = [dict(s) for s in (state.get("slots") or []) if isinstance(s, dict)]
        events = [dict(e) for e in (state.get("events") or []) if isinstance(e, dict)]
        if not slots:
            for item in all_monitored:
                slots.append({
                    "slot_id": item["slot_id"],
                    "base_label": item["slot_id"],
                    "run_seq": item["run_seq"],
                    "run_label": item["run_label"],
                    "plan_id": _text(plan.get("plan_id")),
                    "wave_order": item["wave_order"],
                    "topic_id": item["topic_id"],
                    "target_repo": item["target_repo"],
                    "target": item["target"],
                    "pane_id": item["pane_id"],
                    "session_id": item["session_id"],
                    "status": "running",
                    "raw_status": "running",
                    "needs_owner_attention": False,
                    "blocked_reason": "",
                    "auto_unblocked": [],
                    "visible_summary": "",
                    "history": [],
                })
        for slot in slots:
            source = next((item for item in all_monitored if item["slot_id"] == slot.get("slot_id") and int(item["run_seq"]) == int(slot.get("run_seq") or 1)), None)
            if source:
                slot.update(_slot_metadata(source))
        just_settled_slot_ids: list[str] = []
        for rep in target_reports:
            match_idx = _match_existing_slot_index(slots, rep, rep)
            if match_idx is not None:
                existing = dict(slots[match_idx])
                existing_run_seq = int(existing.get("run_seq") or 1)
                rep_run_seq = int(rep.get("run_seq") or 1)
                # Supersede guard: never let an older run overwrite a slot that has moved to a newer run_seq (e.g. A -> A2)
                if existing_run_seq > rep_run_seq and _text(existing.get("plan_id")) != _text(plan.get("plan_id")):
                    rep["superseded_by_run_seq"] = existing_run_seq
                    rep["superseded_by_label"] = _text(existing.get("run_label"))
                    continue
                rep["slot_id"] = _text(existing.get("slot_id") or rep["slot_id"])
                rep["run_seq"] = existing_run_seq
                rep["run_label"] = _text(existing.get("run_label") or rep["run_label"])
                existing.update({
                    **_slot_metadata(rep),
                    "quiescent": rep["quiescent"],
                    "seen_working": rep["seen_working"],
                    "target": _text(rep.get("target") or existing.get("target")),
                    "pane_id": _text(rep.get("pane_id") or existing.get("pane_id")),
                    "session_id": _text(rep.get("session_id") or existing.get("session_id")),
                    "status": rep["status"],
                    "raw_status": rep["status"],
                    "needs_owner_attention": bool(rep["needs_owner_attention"]),
                    "blocked_reason": rep["blocked_reason"],
                    "auto_unblocked": list(rep["auto_unblocked"]),
                    "visible_summary": rep["visible_summary"],
                    "completion_seq": rep["completion_seq"],
                    "state_change_seq": rep["state_change_seq"],
                })
                slots[match_idx] = existing
                if rep["status"] in {"idle", "done"}:
                    just_settled_slot_ids.append(rep["slot_id"])
            else:
                slots.append({
                    **_slot_metadata(rep),
                    "quiescent": rep["quiescent"],
                    "seen_working": rep["seen_working"],
                    "slot_id": rep["slot_id"],
                    "base_label": rep["slot_id"],
                    "run_seq": rep["run_seq"],
                    "run_label": rep["run_label"],
                    "plan_id": _text(plan.get("plan_id")),
                    "wave_order": rep["wave_order"],
                    "topic_id": rep["topic_id"],
                    "target_repo": rep["target_repo"],
                    "target": rep["target"],
                    "pane_id": rep["pane_id"],
                    "session_id": rep["session_id"],
                    "status": rep["status"],
                    "raw_status": rep["status"],
                    "needs_owner_attention": bool(rep["needs_owner_attention"]),
                    "blocked_reason": rep["blocked_reason"],
                    "auto_unblocked": list(rep["auto_unblocked"]),
                    "visible_summary": rep["visible_summary"],
                    "completion_seq": rep["completion_seq"],
                    "state_change_seq": rep["state_change_seq"],
                    "history": [],
                })
                if rep["status"] in {"idle", "done"}:
                    just_settled_slot_ids.append(rep["slot_id"])
        state = _discover_and_enrich_global_m2_slots(
            {"schema_version": 1, "slots": slots, "events": events}, agent_records=herdr_merged,
            runner=runner, herdr_bin=binary, handoff_root=handoff_root,
            protected_slots=[rep["slot_id"] for rep in target_reports if not rep.get("superseded_by_run_seq")],
            visible_by_ref=visible_by_ref,
        )
        snap = _compute_board_rollup(state, just_settled_slots=just_settled_slot_ids)
        slots = snap["slots"]
        events.append({
            "seq": len(events) + 1,
            "type": "watch_settled",
            "plan_id": _text(plan.get("plan_id")),
            "watch_target": watch_filter,
            "just_settled_labels": snap["just_settled_labels"],
            "completed_labels": snap["completed_labels"],
            "completed_rollup_code": snap["completed_rollup_code"],
            "running_labels": snap["running_labels"],
            "blocked_labels": snap["blocked_labels"],
        })
        return {"schema_version": 1, "slots": slots, "events": events}

    updated_state = _with_locked_supervision_state(resolved_state_path, _update_settled_slots)
    board_rollup = _compute_board_rollup(
        updated_state,
        just_settled_slots=[t["slot_id"] for t in target_reports if t["status"] in {"idle", "done"} and not t.get("superseded_by_run_seq")],
    )
    cache_path = _resolve_cross_device_cache_path(state_file, apply_receipt=apply_receipt)
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=True,
        state_path=resolved_state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
    else:
        linear_sync_result = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    _attach_cross_device_to_rollup(
        board_rollup,
        linear_sync_result.get("state") if isinstance(linear_sync_result.get("state"), dict) else None,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )

    any_missing = any(t["status"] == "missing" for t in target_reports)
    any_owner_attention = any(t["needs_owner_attention"] for t in target_reports)
    settled = (not any_missing) and all(
        t["status"] in {"idle", "done"} or (t["status"] in {"blocked", "needs_input"} and t["needs_owner_attention"])
        for t in target_reports
    )
    overall_ok = settled and not any_missing
    any_auto_unblock = any(t["auto_unblock_enabled"] for t in target_reports)
    header_suffix = f" · Target `{watch_filter}`" if watch_filter else ""
    summary_lines = [
        f"### M2 Watch Summary · `{_text(plan.get('plan_id'), 'm2p-unknown')}`{header_suffix}",
        f"- **All Settled**: `{settled}` · **Needs Owner Attention**: `{any_owner_attention}` · **Auto Unblock Enabled**: `{any_auto_unblock}` · **Polls**: `{polls}`",
        (
            f"- **Just Completed**: `{','.join(board_rollup['just_settled_labels']) or 'none'}` · "
            f"**Cumulative Completed Rollup**: `{board_rollup['completed_rollup_code'] or 'none'}` (`{board_rollup['completed_rollup_display']}`) · "
            f"**Still Running**: `{','.join(board_rollup['running_labels']) or 'none'}`"
        ),
    ]
    for t in target_reports:
        unblocked_kinds = ",".join(u["kind"] for u in t["auto_unblocked"]) or "none"
        attn = f" · **NEEDS OWNER ({t['blocked_reason']})**" if t["needs_owner_attention"] else ""
        summary_lines.append(
            f"- Wave {t['wave_order']} `{t['topic_id']}` (`{t['target_repo']}` → `{t['target']}`): "
            f"status=`{t['status']}` · label=`{t['run_label']}` · auto_unblocked=`{unblocked_kinds}`{attn}"
        )
        if t["visible_summary"]:
            first_line = t["visible_summary"].splitlines()[-1].strip()
            summary_lines.append(f"  - Latest: `{first_line[:160]}`")
    if board_rollup["completed_targets"]:
        summary_lines.append(f"#### Cumulative Completed M2s (`{board_rollup['completed_rollup_code']}`)")
        for cs in board_rollup["completed_targets"]:
            cs_summary = _text(cs.get("visible_summary")).splitlines()[-1].strip()[:120] if _text(cs.get("visible_summary")) else ""
            cs_tail = f" — `{cs_summary}`" if cs_summary else ""
            summary_lines.append(
                f"- `{_text(cs.get('run_label'))}` (`{_text(cs.get('topic_id') or cs.get('slot_id'))}` → `{_text(cs.get('target'))}`): status=`{_text(cs.get('status'))}`{cs_tail}"
            )
    summary_lines.extend(["", board_rollup["unified_rollup_markdown"]])
    return {
        "unified_rollup_markdown": board_rollup["unified_rollup_markdown"],
        "in_flight_details": board_rollup["in_flight_details"],
        "wave_dependency_chain": board_rollup["wave_dependency_chain"],
        "ok": overall_ok,
        "plan_id": _text(plan.get("plan_id")),
        "watch_target": watch_filter,
        "polls": polls,
        "auto_unblock_enabled": any_auto_unblock,
        "passive_only": not any_auto_unblock,
        "all_settled": settled,
        "needs_owner_attention": any_owner_attention,
        "just_settled_labels": board_rollup["just_settled_labels"],
        "completed_labels": board_rollup["completed_labels"],
        "completed_rollup_code": board_rollup["completed_rollup_code"],
        "completed_rollup_display": board_rollup["completed_rollup_display"],
        "running_labels": board_rollup["running_labels"],
        "blocked_labels": board_rollup["blocked_labels"],
        "all_board_settled": board_rollup["all_board_settled"],
        "local_hostname": board_rollup.get("local_hostname"),
        "remote_devices": board_rollup.get("remote_devices", {}),
        "cross_device_in_flight": board_rollup.get("cross_device_in_flight", []),
        "cross_device_pending_decisions": board_rollup.get("cross_device_pending_decisions", []),
        "linear_sync": linear_sync_result,
        "board_rollup": board_rollup,
        "targets": target_reports,
        "summary_markdown": "\n".join(summary_lines),
    }


def compute_board_fingerprint(state_or_path: dict[str, Any] | str | Path | None = None) -> str:
    """Compute a deterministic SHA-256 fingerprint of local supervision slot statuses and summaries."""
    if isinstance(state_or_path, dict):
        state = state_or_path
    else:
        state_path = _resolve_supervision_state_path(state_or_path, persist_by_default=True)
        state = {}
        if state_path and state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, ValueError):
                state = {}
    slots_payload: list[dict[str, Any]] = []
    for raw_slot in (state.get("slots") or []):
        if not isinstance(raw_slot, dict):
            continue
        summary = _text(raw_slot.get("m1_progress") or raw_slot.get("visible_summary") or raw_slot.get("sub_prompt"))
        slots_payload.append({
            "slot_id": _text(raw_slot.get("slot_id")),
            "run_label": _text(raw_slot.get("run_label")),
            "topic_id": _text(raw_slot.get("topic_id")),
            "target_repo": _text(raw_slot.get("target_repo")),
            "target": _text(raw_slot.get("target")),
            "pane_id": _text(raw_slot.get("pane_id")),
            "linear_issue": _text(raw_slot.get("linear_issue")),
            "status": _text(raw_slot.get("status")),
            "raw_status": _text(raw_slot.get("raw_status")),
            "retained": bool(raw_slot.get("retained")),
            "needs_owner_attention": bool(raw_slot.get("needs_owner_attention")),
            "blocked_reason": _text(raw_slot.get("blocked_reason")),
            "summary": summary,
            "active_summary_source": _text(raw_slot.get("active_summary_source")),
        })
    slots_payload.sort(key=lambda item: (item["slot_id"], item["pane_id"], item["target"]))
    backlog_payload = [
        {
            "topic_id": _text(b.get("topic_id") or b.get("id")),
            "issue": _text(b.get("issue")),
            "summary": _text(b.get("summary") or b.get("title")),
        }
        for b in (state.get("backlog") or [])
        if isinstance(b, dict)
    ]
    backlog_payload.sort(key=lambda item: (item["topic_id"], item["issue"], item["summary"]))
    canonical = {"slots": slots_payload, "backlog": backlog_payload}
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def install_m2_board_hook(
    *,
    hooks_dir: str | Path | None = None,
    hook_script_source: str | Path | None = None,
) -> dict[str, Any]:
    """Idempotently install m2-board.json and m2-board-hook.sh into ~/.copilot/hooks without touching sibling hooks."""
    target_dir = Path(hooks_dir or os.environ.get("COPILOT_HOOKS_DIR") or (Path.home() / ".copilot" / "hooks")).expanduser()
    source_script = Path(hook_script_source or DEFAULT_HOOK_SCRIPT_PATH).expanduser()
    if not source_script.is_file():
        raise RouterError(f"hook script source not found: {source_script}")
    target_dir.mkdir(parents=True, exist_ok=True)

    installed_script = target_dir / "m2-board-hook.sh"
    installed_json = target_dir / "m2-board.json"
    source_text = source_script.read_text(encoding="utf-8")

    updated_script = False
    existing_script_text = None
    if installed_script.is_file():
        try:
            existing_script_text = installed_script.read_text(encoding="utf-8")
        except OSError:
            existing_script_text = None
    if existing_script_text != source_text or not os.access(installed_script, os.X_OK):
        tmp_script = installed_script.with_suffix(f".{os.getpid()}.tmp")
        tmp_script.write_text(source_text, encoding="utf-8")
        tmp_script.chmod(0o755)
        tmp_script.replace(installed_script)
        installed_script.chmod(0o755)
        updated_script = True

    hook_cmd = f"sh '{installed_script}'"
    hook_config = {
        "version": 1,
        "hooks": {
            "agentStop": [
                {
                    "type": "command",
                    "bash": hook_cmd,
                    "timeoutSec": 5,
                }
            ],
            "sessionEnd": [
                {
                    "type": "command",
                    "bash": hook_cmd,
                    "timeoutSec": 5,
                }
            ],
        },
    }
    desired_json_text = json.dumps(hook_config, ensure_ascii=False, indent=2) + "\n"
    updated_json = False
    existing_json_text = None
    if installed_json.is_file():
        try:
            existing_json_text = installed_json.read_text(encoding="utf-8")
        except OSError:
            existing_json_text = None
    if existing_json_text != desired_json_text:
        tmp_json = installed_json.with_suffix(f".{os.getpid()}.tmp")
        tmp_json.write_text(desired_json_text, encoding="utf-8")
        tmp_json.replace(installed_json)
        updated_json = True

    preserved = sorted(
        p.name for p in target_dir.iterdir()
        if p.name not in {"m2-board.json", "m2-board-hook.sh"}
    )
    return {
        "ok": True,
        "hooks_dir": str(target_dir),
        "hook_json_path": str(installed_json),
        "hook_script_path": str(installed_script),
        "updated_json": updated_json,
        "updated_script": updated_script,
        "preserved_files": preserved,
    }


def supervision_status(
    *, state_file: str | Path | None = None, plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None, handoff_root: str | Path | None = None,
    agent_records: list[dict[str, Any]] | None = None, herdr_bin: str | None = None,
    runner: CommandRunner = _run_command, discover: bool = True,
    sync_linear: bool = False, no_sync_linear: bool = False,
    hub_issue: str | None = None, local_hostname: str | None = None,
    cross_device_state: dict[str, Any] | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True)
    binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    records = agent_records if agent_records is not None else (_live_agent_records(binary, runner=runner) if discover else [])
    settled_in_status: list[str] = []

    def refresh(state: dict[str, Any]) -> dict[str, Any]:
        if discover or agent_records is not None:
            _discover_and_enrich_global_m2_slots(
                state,
                agent_records=records,
                runner=runner,
                herdr_bin=binary,
                handoff_root=handoff_root,
                allow_quiescent_settle=True,
                settled_slot_ids_out=settled_in_status,
            )
        state["slots"] = _compute_board_rollup(state, just_settled_slots=settled_in_status)["slots"]
        return state

    state = _with_locked_supervision_state(state_path, refresh)
    board = _compute_board_rollup(state, just_settled_slots=settled_in_status)
    cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=False,
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    linear_sync_result: dict[str, Any] | None = None
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        if cross_device_state is None and isinstance(linear_sync_result.get("state"), dict):
            cross_device_state = linear_sync_result["state"]
    elif sync_linear or no_sync_linear:
        linear_sync_result = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    _attach_cross_device_to_rollup(
        board,
        cross_device_state,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )
    out: dict[str, Any] = {"ok": True, "supervision_state_path": str(state_path), **board, "board_rollup": board, "summary_markdown": board["unified_rollup_markdown"]}
    if linear_sync_result is not None:
        out["linear_sync"] = linear_sync_result
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and owner-gated dispatch for a unified M2 ingress")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--plan", action="store_true", help="build and print a dispatch preview")
    mode.add_argument("--apply", action="store_true", help="apply a previously saved plan")
    mode.add_argument("--watch", action="store_true", help="watch dispatched target M2 sessions until quiescent")
    mode.add_argument("--status", action="store_true", help="refresh the cross-round unified M2 rollup")
    mode.add_argument("--gc", action="store_true", help="preview or owner-approved safe global cleanup")
    mode.add_argument("--board-fingerprint", action="store_true", help="print SHA-256 fingerprint of local supervision state")
    mode.add_argument("--install-hook", action="store_true", help="idempotently install m2-board.json and m2-board-hook.sh into ~/.copilot/hooks")
    parser.add_argument("--hooks-dir", help="target hooks directory for --install-hook (default: ~/.copilot/hooks)")
    parser.add_argument("--sync-linear", action="store_true", help="bidirectional per-device shard sync with Linear cross-device M2 hub (TIA-463)")
    parser.add_argument("--no-sync-linear", action="store_true", help="explicitly disable automatic Linear cross-device hub sync")
    parser.add_argument("--hub-issue", default=os.environ.get("M2_SYNC_HUB_ISSUE", DEFAULT_M2_SYNC_HUB_ISSUE), help="Linear cross-device M2 sync hub issue identifier (default: TIA-463)")
    parser.add_argument("--local-hostname", default=os.environ.get("M2_LOCAL_HOSTNAME", ""), help="override local device hostname shard key")
    parser.add_argument("--handoff-root", help="Goal ledger root (default: M2_HANDOFF_ROOT or ~/Development/handoff)")
    parser.add_argument("--message")
    parser.add_argument("--message-file")
    parser.add_argument("--plan-file")
    parser.add_argument("--apply-receipt-file", help="optional apply receipt JSON file for --watch")
    parser.add_argument("--state-file", help="optional supervision state JSON file for per-M2 cumulative rollup")
    parser.add_argument("--reset-state", action="store_true", help="reset supervision state ledger before --apply")
    parser.add_argument("--watch-target", "--target-id", dest="watch_target", default="", help="watch a single M2 target/slot and update cumulative rollup immediately on completion")
    parser.add_argument("--preview-md-file", help="optional explicit markdown export path for --plan preview")
    parser.add_argument("--no-open-preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--orca-bin", help=argparse.SUPPRESS)
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
    parser.add_argument("--caller-bot", default=os.environ.get("M2_CALLER_BOT", ""), help="caller bot kind (e.g. grok) for quota-protection routing")
    parser.add_argument("--no-supervision", action="store_true", help="disable background --watch watchdog tasks for quota-sensitive callers")
    parser.add_argument("--no-routine", action="store_true", help="disable periodic routine polling for quota-sensitive callers")
    parser.add_argument("--force-watch", action="store_true", help="force --watch polling even when Grok Bot quota protection is active")
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
    parser = _parser()
    args = parser.parse_args(argv)
    if not (args.plan or args.apply or args.watch or args.status or args.gc or args.board_fingerprint or args.install_hook):
        if args.sync_linear:
            args.status = True
        else:
            parser.error("one of the arguments --plan --apply --watch --status --gc --sync-linear --board-fingerprint --install-hook is required")
    try:
        if args.board_fingerprint:
            fp = compute_board_fingerprint(args.state_file)
            output: Any = {"ok": True, "fingerprint": fp} if args.json else fp
        elif args.install_hook:
            output = install_m2_board_hook(hooks_dir=args.hooks_dir)
        elif args.plan:
            payload = _json_value(args.sessions_json) if args.sessions_json else None
            herdr_payload = _json_value(args.herdr_json) if args.herdr_json else payload
            herdr_pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            herdr_agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else None
            herdr_workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            dsh_payload = _json_value(args.dsh_state_json) if args.dsh_state_json else payload
            sessions, warnings = discover_sessions(herdr_payload=herdr_payload, herdr_pane_payload=herdr_pane_payload, herdr_agent_payload=herdr_agent_payload, workspace_payload=herdr_workspace_payload, dsh_payload=dsh_payload, herdr_bin=args.herdr_bin, dsh_state_url=args.dsh_state_url, dsh_token=args.dsh_token, include_herdr=not args.no_herdr, include_dsh=not args.no_dsh)
            raw_gc_records = _merge_herdr_records(_records(herdr_pane_payload), _records(herdr_agent_payload))
            if not raw_gc_records and herdr_payload is not None:
                raw_gc_records = _records(herdr_payload)
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
                state_file=args.state_file,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                herdr_bin=args.herdr_bin,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
                agent_records=raw_gc_records if raw_gc_records else ([] if args.no_herdr else None),
            )
            if raw_gc_records:
                plan["gc_candidates"] = discover_gc_candidates(raw_gc_records, handoff_root=args.handoff_root)
            if args.dsh_state_url:
                plan["discovery"]["dsh_state_url"] = args.dsh_state_url
            output: Any = plan if args.json else preview_text(plan)
        elif args.gc:
            agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else (_json_value(args.herdr_json) if args.herdr_json else None)
            pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            if agent_payload is None and pane_payload is None:
                records = _live_agent_records(_text(args.herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr"))
            else:
                records = _merge_herdr_records(_records(pane_payload), _records(agent_payload))
            workspace_rows = _workspace_inventory(workspace_payload) or []
            labels = {_text(w.get("workspace_id")): _text(w.get("label") or w.get("workspace_label")) for w in workspace_rows if _text(w.get("workspace_id"))}
            for rec in records:
                ws = _text(rec.get("workspace_id") or rec.get("workspaceId"))
                if ws and labels.get(ws):
                    rec["workspace_label"] = labels[ws]
            output = run_gc(
                records,
                approve=args.approve,
                state_file=args.state_file,
                handoff_root=args.handoff_root,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
            )
        elif args.status:
            result = supervision_status(
                state_file=args.state_file, plan_file=args.plan_file, apply_receipt_file=args.apply_receipt_file,
                handoff_root=args.handoff_root, herdr_bin=args.herdr_bin, discover=not args.no_herdr,
                agent_records=_records(_json_value(args.herdr_agent_json or args.herdr_json)) if (args.herdr_agent_json or args.herdr_json) else None,
                sync_linear=args.sync_linear, no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue, local_hostname=args.local_hostname or None,
            )
            output = result if args.json else result["unified_rollup_markdown"]
        elif args.apply:
            if not args.plan_file:
                raise RouterError("--apply requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            output = apply_plan(
                plan,
                approve=args.approve,
                register_new_domains=args.register_new_domains,
                cleanup_duplicates=args.cleanup_duplicates,
                replace_stale_m2=args.replace_stale_m2,
                domain_map_file=args.domain_map_file,
                plan_file=args.plan_file,
                apply_receipt_file=args.apply_receipt_file,
                state_file=args.state_file,
                reset_state=args.reset_state,
                handoff_root=args.handoff_root,
                agent_records=_records(_json_value(args.herdr_agent_json)) if args.herdr_agent_json else ([] if args.no_herdr else None),
                transport=args.transport,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
            )
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
                watch_target=args.watch_target,
                state_file=_resolve_supervision_state_path(args.state_file, plan_file=args.plan_file, apply_receipt_file=args.apply_receipt_file, apply_receipt=apply_receipt, persist_by_default=True),
                handoff_root=args.handoff_root,
                interval=args.interval,
                max_wait=args.max_wait,
                max_polls=args.max_polls,
                herdr_bin=args.herdr_bin,
                dsh_state_url=args.dsh_state_url,
                dsh_token=args.dsh_token,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
                force_watch=args.force_watch,
                agent_records=_records(_json_value(args.herdr_agent_json or args.herdr_json)) if (args.herdr_agent_json or args.herdr_json) else ([] if args.no_herdr else None),
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
