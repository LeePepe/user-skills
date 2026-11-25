from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

from .constants_and_paths import EVENTS_TAIL_MAX_BYTES

from .constants_and_paths import RECENT_SESSION_MAX_TURNS

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
    if session.get("_from_issue_cache") and not raw_pane_text:
        return None
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
