from __future__ import annotations

from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})

def _turn_number(event: dict[str, Any], fallback: int) -> int:
    value = _field(event, "turn", "turnIndex", "turn_index", "turnNumber")
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _duration_seconds(seq: list[dict[str, Any]]) -> float:
    times = [_parse_time(e.get("timestamp") or _field(e, "timestamp", "createdAt")) for e in seq]
    valid = [x for x in times if x]
    return (max(valid) - min(valid)).total_seconds() if len(valid) > 1 else 0.0


def _event_stamp(event: Mapping[str, Any]) -> datetime | None:
    for key in ("timestamp", "ts", "started_at", "startedAt", "completed_at", "completedAt"):
        value = event.get(key) if isinstance(event, dict) else None
        parsed = _parse_time(value)
        if parsed:
            return parsed
    for key in ("timestamp", "ts", "started_at", "startedAt", "completed_at", "completedAt"):
        parsed = _parse_time(_field(dict(event), key))
        if parsed:
            return parsed
    return None


def _queue_tasks(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read explicit queue tasks, with a small ledger fallback for old handoffs."""
    path = record.get("path")
    queue_path = Path(path) / "queue.json" if path else None
    payload: Any = None
    if queue_path and queue_path.is_file() and _external_path_safe(queue_path):
        try:
            payload = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
    raw_tasks = payload.get("tasks", payload.get("queue", payload)) if isinstance(payload, dict) else payload
    if isinstance(raw_tasks, dict):
        raw_tasks = list(raw_tasks.values())
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    tasks: list[dict[str, Any]] = []
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        deps = item.get("depends_on", item.get("dependsOn", item.get("dependencies", [])))
        paths = item.get("paths", item.get("owned_paths", item.get("ownedPaths", item.get("path", []))))
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            paths = []
        tasks.append({"depends_on": deps, "paths": [str(x) for x in paths if str(x).strip()], "raw": item})
    if len(tasks) >= 2:
        return tasks
    ledger = str(record.get("ledger", ""))
    blocks = re.split(r"(?m)(?=^\s*(?:[-*]\s*)?(?:task|item)\b)", ledger)
    for block in blocks:
        dep = re.search(r"depends[_ ]on\s*:\s*(\[\s*\]|null|none)", block, re.I)
        path_match = re.search(r"(?:owned[_ ]paths?|paths?|path)\s*:\s*([^\n]+)", block, re.I)
        if dep and path_match:
            raw = {key: value for key, value in re.findall(
                r"(started_at|startedAt|completed_at|completedAt|timestamp)\s*:\s*([^\n]+)", block, re.I
            )}
            paths = [part.strip(" `[]'\"") for part in path_match.group(1).split(",") if part.strip(" `[]'\"")]
            tasks.append({"depends_on": [], "paths": paths, "raw": raw})
    return tasks


def _serial_independent_tasks(record: Mapping[str, Any], seq: list[dict[str, Any]],
                              tasks: list[dict[str, Any]] | None = None,
                              event_blobs: list[tuple[dict[str, Any], str]] | None = None) -> int:
    tasks = [task for task in (tasks if tasks is not None else _queue_tasks(record)) if not task["depends_on"] and task["paths"]]
    if len(tasks) < 2:
        return 0
    spans: list[tuple[datetime, datetime, set[str]]] = []
    needs_events = any(not any(_parse_time(task["raw"].get(key)) for key in ("timestamp", "started_at", "startedAt", "completed_at", "completedAt")) for task in tasks)
    if needs_events and event_blobs is None:
        event_blobs = [(event, _event_blob(event)) for event in seq]
    for task in tasks:
        raw = task["raw"]
        stamps = [_parse_time(raw.get(key)) for key in ("timestamp", "started_at", "startedAt", "completed_at", "completedAt")]
        stamps = [stamp for stamp in stamps if stamp]
        path_tokens = {path.lower() for path in task["paths"]}
        path_tokens.update(Path(path).name.lower() for path in task["paths"])
        if not stamps and event_blobs is not None:
            matched = [event for event, blob in event_blobs if any(token and token in blob for token in path_tokens)]
            stamps.extend(stamp for stamp in (_event_stamp(event) for event in matched) if stamp)
        if not stamps:
            return 0
        spans.append((min(stamps), max(stamps), path_tokens))
    spans.sort(key=lambda span: span[0])
    chain = 1
    best = 1
    for previous, current in zip(spans, spans[1:]):
        disjoint = previous[2].isdisjoint(current[2])
        if disjoint and previous[1] < current[0]:
            chain += 1
            best = max(best, chain)
        else:
            chain = 1
    return best if best >= 2 else 0


def _reasoning_text(event: dict[str, Any]) -> str:
    value = _field(event, "reasoningText", "reasoning_text", "reasoning")
    if isinstance(value, str):
        return value
    trace = _field(event, "copilot_trace", "copilotTrace", "trace")
    if isinstance(trace, dict):
        for key in ("reasoningText", "reasoning_text", "reasoning", "text"):
            if isinstance(trace.get(key), str):
                return trace[key]
    return ""


def _output_tokens(event: dict[str, Any]) -> int | None:
    value = _field(event, "outputTokens", "output_tokens", "usageOutputTokens")
    if value is None:
        usage = _field(event, "usage")
        if isinstance(usage, dict):
            value = usage.get("outputTokens", usage.get("output_tokens"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _mechanical_tool_sequence(associated: list[dict[str, Any]]) -> bool:
    if not associated:
        return False
    mechanical = re.compile(r"(?:read_bash(?:\s*\(|\b)|\b(?:ls|find|sleep)\b|git\s+status\b|herdr\s+(?:pane\s+read|agent\s+list)|gh\s+pr\s+view)", re.I)
    for tool in associated:
        name = _tool_name(tool)
        command = _command(tool)
        if name in {"view", "read", "cat", "edit", "create", "apply_patch", "write", "patch"}:
            return False
        if name not in {"read_bash", "bash", "shell", "terminal"} and not mechanical.search(command):
            return False
        if name in {"bash", "shell", "terminal"} and not mechanical.search(command):
            return False
    return True


def _is_hzn5_mechanical_turn(event: dict[str, Any], associated: list[dict[str, Any]]) -> tuple[bool, int, int | None]:
    reasoning = _reasoning_text(event)
    tokens = _output_tokens(event)
    shallow = len(reasoning) < 250 and (tokens is None or tokens < 120)
    if not shallow:
        return False, len(reasoning), tokens
    if associated:
        return _mechanical_tool_sequence(associated), len(reasoning), tokens
    # Preserve the legacy fixture contract while requiring a status/read-like turn.
    content = _text(_field(event, "content", "assistantContent", "message"))
    legacy_low = (tokens is None and len(content) < 300) or (tokens is not None and tokens < 80)
    return bool(legacy_low and re.search(r"\b(?:read|status|list|poll|check)\b", content, re.I)), len(reasoning), tokens


def _horizon_profile(events: list[dict[str, Any]], handoffs: list[dict[str, Any]], *, min_turns: int = 40,
                     min_duration_min: float = 15.0) -> list[dict[str, Any]]:
    """Profile successful and at-risk long sessions with deterministic heuristics."""
    rows: list[dict[str, Any]] = []
    handoff_tasks = [(record, _queue_tasks(record)) for record in handoffs]
    for sid, seq in _session_groups(events).items():
        turns = max([_turn_number(e, i + 1) for i, e in enumerate(seq)] or [0])
        duration = _duration_seconds(seq)
        if turns < min_turns and duration < min_duration_min * 60:
            continue
        tools = _tool_events(seq)
        assistant = [(i, e) for i, e in enumerate(seq) if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response"}]
        event_texts: list[str] | None = None
        # HZN1 counts heavy unreferenced outputs, while retaining byte-turns as evidence.
        heavy_hits = 0
        carry_byte_turns = 0
        for event_index, event in enumerate(seq):
            if event not in tools:
                continue
            size = len(_result_content(event).encode("utf-8"))
            event_turn = _turn_number(event, event_index + 1)
            remaining = max(0, turns - event_turn)
            if size < 8192 or remaining < 10 or size * remaining < 250_000:
                continue
            if event_texts is None:
                event_texts = [_text(item).lower() for item in seq]
            call_id = _tool_call_id(event).lower()
            raw_path = str(_arguments(event).get("path") or _field(event, "path") or "")
            basename = Path(raw_path).name.lower() if raw_path else ""
            referenced_later = any(
                (call_id and call_id in later) or (basename and basename in later)
                for later in event_texts[event_index + 1:]
            )
            if referenced_later:
                continue
            heavy_hits += 1
            carry_byte_turns += size * remaining
        if heavy_hits:
            rows.append(_observation("HZN1_context_carry_tax", "horizon", heavy_hits,
                                     [f"{sid}: carry_byte_turns={carry_byte_turns}; heavy_outputs={heavy_hits}; offload to Explorer or --summary"], skill="agent-retro"))
        # HZN2 wakeup lag and explicit queue/ledger parallelism evidence.
        lags = []
        for index, event in enumerate(seq):
            if _event_type(event) not in {"subagent.completed", "evt=done", "receipt.written"} and "EVT=DONE" not in _text(event):
                continue
            stamp = _event_stamp(event)
            nxt = next((e for e in seq[index + 1:] if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response", "user.message"}), None)
            next_stamp = _event_stamp(nxt) if nxt else None
            if stamp and next_stamp and (next_stamp - stamp).total_seconds() > 180:
                lags.append(int((next_stamp - stamp).total_seconds()))
        event_blobs = None
        serial = 0
        for record, tasks in handoff_tasks:
            if len(tasks) < 2:
                continue
            serial = max(serial, _serial_independent_tasks(record, seq, tasks, event_blobs))
            if event_blobs is None and any(not any(_parse_time(task["raw"].get(key)) for key in ("timestamp", "started_at", "startedAt", "completed_at", "completedAt")) for task in tasks):
                event_blobs = [(event, _event_blob(event)) for event in seq]
        if lags or serial >= 2:
            rows.append(_observation("HZN2_wakeup_lag_and_missed_parallelism", "horizon", len(lags) + serial,
                                     [f"{sid}: wakeup_lag_sec={max(lags, default=0)}, missed_parallel_tasks={serial}"], skill="m2-dispatcher"))
        # HZN3: edit churn and expensive verification before the end.
        paths = Counter(str(_arguments(e).get("path") or _field(e, "path") or "") for e in tools if _tool_name(e) in {"edit", "create", "apply_patch", "write", "patch"})
        churn = max(paths.values(), default=0)
        heavy = 0
        for event in tools:
            command = _command(event).lower()
            raw_duration = _field(event, "durationMs", "duration_ms", "elapsedMs")
            try:
                seconds = float(raw_duration or 0) / 1000
            except (TypeError, ValueError):
                seconds = 0
            if seconds >= 15 and ("scripts/verify" in command or "xcodebuild" in command):
                heavy += 1
        if churn >= 4 or heavy >= 3:
            rows.append(_observation("HZN3_rework_detour_and_premature_full_verify", "horizon", churn + heavy,
                                     [f"{sid}: edit_churn={churn}, premature_full_verify={heavy}"], skill="agent-retro"))
        # HZN4 compares late failures and empty turns with no tool calls to early turns.
        early = [e for e in seq if _turn_number(e, 0) <= 25]
        late = [e for e in seq if _turn_number(e, 0) > 40]
        def failure_rate(items: list[dict[str, Any]]) -> float:
            calls = [e for e in items if _event_type(e).startswith("tool.") or _tool_name(e)]
            return sum(_exit_code(e) not in (None, 0) for e in calls) / max(1, len(calls))
        def empty_rate(items: list[dict[str, Any]]) -> float:
            turns_here = [e for e in items if _event_type(e) in {"assistant.message", "assistant.turn", "assistant.response"}]
            empty = 0
            for event in turns_here:
                calls = _field(event, "toolCalls", "tool_calls", "tools")
                has_calls = isinstance(calls, (list, dict)) and bool(calls)
                empty += int(not _text(_field(event, "content", "assistantContent")) and not has_calls)
            return empty / max(1, len(turns_here))
        early_rate = failure_rate(early) + empty_rate(early)
        late_rate = failure_rate(late) + empty_rate(late)
        if late and late_rate >= max(0.01, early_rate * 2):
            inflection = min((_turn_number(e, 0) for e in late), default=41)
            rows.append(_observation("HZN4_context_fatigue_inflection", "horizon", 1,
                                     [f"{sid}: early_rate={early_rate:.2f}, late_rate={late_rate:.2f}, inflection_turn={inflection}"], skill="team-lead"))
        # HZN5 only counts shallow steering turns doing repetitive mechanical work.
        span = best = 0
        shallow_evidence: list[str] = []
        for event_index, event in assistant:
            model = str(_field(event, "selectedModel", "model", "usageModel") or "").lower()
            next_assistant = next((idx for idx, _ in assistant if idx > event_index), len(seq))
            associated = seq[event_index + 1:next_assistant]
            read_only = not any(_tool_name(item) in {"edit", "create", "apply_patch", "write", "patch"} or re.search(r"\b(?:rm|mv|cp|git\s+(?:commit|push|reset))\b", _command(item), re.I) for item in associated)
            mechanical, reasoning_chars, output_tokens = _is_hzn5_mechanical_turn(event, associated)
            if "group/steering" in model and read_only and mechanical:
                span += 1
                best = max(best, span)
                shallow_evidence.append(f"turn={_turn_number(event, event_index)} reasoning_chars={reasoning_chars} output_tokens={output_tokens}")
            else:
                span = 0
        if best >= 5:
            rows.append(_observation("HZN5_steering_overkill_mechanical_span", "horizon", best,
                                     [f"{sid}: steering mechanical span={best}; " + "; ".join(shallow_evidence[:2]) + "; route mechanical or crystallize L0"], skill="team-lead"))
    return rows
