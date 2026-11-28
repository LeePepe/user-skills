from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command, strip_herdr_origin_comments

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


def _resolve_slot_space_label(
    slot: dict[str, Any],
    domain_map: dict[str, Any] | None = None,
) -> str:
    """Return a stable human-facing Space label without Herdr/run identifiers."""
    for key in ("workspace_label", "space"):
        candidate = _text(slot.get(key))
        if candidate and candidate.casefold() != "m2":
            return candidate
    active_map = domain_map
    if active_map is None:
        try:
            active_map = load_domain_map()
        except RouterError:
            active_map = {}
    topics = _topic_by_id(active_map) if isinstance(active_map, dict) else {}
    candidates = [_text(slot.get(key)) for key in ("topic_id", "target_repo", "target", "slot_id")]
    for candidate in candidates:
        if not candidate:
            continue
        topic = topics.get(candidate.casefold())
        mapped = _text(topic.get("space")) if isinstance(topic, dict) else ""
        if mapped and mapped.casefold() != "m2":
            return mapped
        if topic and candidate:
            return candidate
    for candidate in candidates:
        if not candidate:
            continue
        clean = re.sub(r"^m2-", "", candidate, flags=re.I)
        clean = re.sub(r"-[0-9a-f]{6}-r\d+$", "", clean, flags=re.I)
        clean = re.sub(r"-r\d+$", "", clean, flags=re.I)
        clean = re.sub(r"(?:[-_]?r\d+|[-_]\d+)$", "", clean, flags=re.I)
        clean = clean.strip("-_ ")
        if clean and clean.casefold() != "m2":
            return clean
    return "M2"


def _summarize_concise_stage(value: dict[str, Any] | Any, *, max_len: int = 60) -> str:
    """Extract one clean, short stage sentence for human-facing Markdown."""
    slot = value if isinstance(value, dict) else {}
    raw_values = [
        slot.get("m1_progress"), slot.get("m1_stage_summary"), slot.get("summary"),
        slot.get("visible_summary"), slot.get("sub_prompt"), value if not isinstance(value, dict) else "",
    ]
    def clean_line(raw: Any) -> str:
        text = strip_herdr_origin_comments(raw)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = re.sub(r"(?:^|\n)\s*(?:EVT=[^\n]*|TL:\s*[^\n|]+|(?:\$\s*)?(?:Shell|PY Read|MD Edit)\b[^\n]*|\[\d+\]\s*User:.*?->\s*Assistant:.*?)(?=\n|$)", "", text, flags=re.I)
        text = re.sub(r"\bEVT=[A-Z_]+\b", "", text)
        text = re.sub(r"(?:^|\s)TL:\s*[^|\n]+(?:\s*\|\s*|$)", " ", text, flags=re.I)
        text = _VOLATILE_TUI_CHROME_RE.sub(" ", text)
        return re.sub(r"\s+", " ", text).strip(" -|·")

    task = re.search(r"([A-Za-z][\w.-]*)\s+\((in_progress|working|blocked|needs_input|ready|done)[^)]*\)", _text(slot.get("m1_progress")), re.I)
    if task and _text(slot.get("active_summary_source")) == "ledger":
        stage = clean_line(slot.get("m1_stage_summary"))
        candidate = f"{task.group(1)} ({task.group(2)})"
        if stage:
            candidate += f" · {stage}"
    else:
        candidate = ""
        for raw in raw_values:
            for line in clean_line(raw).splitlines() or [""]:
                line = clean_line(line)
                if line and not re.search(r"background\\s+/tasks|^(?:Working|Thinking|Running(?:\\s+tests)?|Waiting for)\\b", line, re.I):
                    candidate = line
                    break
            if candidate:
                break
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        candidate = "—"
    return candidate if len(candidate) <= max_len else candidate[: max_len - 1].rstrip() + "…"


def _ledger_is_completed(ledger_text: str) -> bool:
    events = list(re.finditer(r"EVT=([A-Z_]+)", ledger_text))
    if events:
        last_event = events[-1].group(1)
        if last_event in {"IN_PROGRESS", "ACTIVE", "READY", "BLOCKED", "CHANGES_REQUESTED"}:
            return False
        if last_event == "GOAL_DONE":
            return True
    status = re.search(r"^\s*[-*]?\s*status\s*:\s*(done|completed)\s*$", ledger_text, re.I | re.M)
    return bool(status)

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
                slot["visible_summary"] = strip_herdr_origin_comments(session_turn_summary)

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
    global_scan_candidates: set[Path] = set()
    if not explicit:
        candidates.extend(Path(match.group(0)).expanduser() / "ledger.md" for match in re.finditer(r"(?:~/|/)[^\s`\"'<>|()]*handoff/([^/\s`\"'<>|()]+)", text))
        if slot.get("handoff_dir"):
            candidates.append(Path(slot["handoff_dir"]) / "ledger.md")
        if (root_value or scan_default) and root.is_dir():
            global_scan_candidates = set(root.glob("*/ledger.md"))
            candidates.extend(sorted(global_scan_candidates))
    recent_ctx = slot.get("recent_session_context") if isinstance(slot.get("recent_session_context"), dict) else {}
    session_ts = _text(
        slot.get("last_session_activity_at")
        or recent_ctx.get("last_session_activity_at")
    )
    session_epoch = _parse_iso_epoch(session_ts)
    ranked = []
    for ledger_path in dict.fromkeys(candidates):
        try:
            ledger_text = ledger_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        linear = re.search(r"^\s*-?\s*Linear\s*[:：]\s*[^\n]*?\b(TIA-\d+)\b", ledger_text, re.M | re.I)
        tl = re.search(r"^\s*-?\s*TL\s*[:：]\s*([^\s/|]+)", ledger_text, re.M)
        full_goal_name = ledger_path.parent.name
        goal_names = [full_goal_name]
        if tl and "-goal-tl" in tl.group(1):
            goal_names.append(tl.group(1).split("-goal-tl", 1)[0])
        goal_pattern = r"(?<!\w)(?:" + "|".join(re.escape(name) for name in goal_names) + r")(?!\w)"
        context_text = " ".join(_text(slot.get(key)) for key in ("visible_summary", "sub_prompt", "terminal_title", "rag_context_hint"))
        exact_tl = bool(tl and re.search(r"(?<!\w)" + re.escape(tl.group(1)) + r"(?!\w)", text))
        exact_issue = bool(linear and re.search(r"(?<!\w)" + re.escape(linear.group(1)) + r"(?!\w)", text, re.I))
        strong_match = bool(re.search(goal_pattern, context_text) or exact_tl)
        ledger_ts, ledger_epoch = _extract_ledger_latest_timestamp(ledger_text, ledger_path)
        is_global_scan = ledger_path in global_scan_candidates
        stale_completed = bool(
            is_global_scan
            and _ledger_is_completed(ledger_text)
            and ledger_epoch is not None
            and (time.time() - ledger_epoch > 86400 or (session_epoch is not None and ledger_epoch < session_epoch))
        )
        if stale_completed and not (exact_tl or exact_issue):
            continue
        # Global ledger discovery must match a whole goal slug or exact TL, never a repo substring.
        if not explicit and not strong_match:
            continue
        score = 0
        for key, weight in (("visible_summary", 100), ("sub_prompt", 80), ("terminal_title", 40), ("rag_context_hint", 20)):
            context = _text(slot.get(key))
            if re.search(goal_pattern, context) or (tl and tl.group(1) in context):
                score += weight
        if exact_issue:
            score += 10
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
                slot["visible_summary"] = strip_herdr_origin_comments(newer_summary)
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
                slot["visible_summary"] = strip_herdr_origin_comments(newer_summary)
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
            "workspace_id": _text(live.get("workspace_id") or live.get("workspaceId") or slot.get("workspace_id")),
            "workspace_label": _text(live.get("workspace_label") or live.get("workspaceLabel") or live.get("space") or slot.get("workspace_label")),
            "space": _text(live.get("space") or slot.get("space")),
        })
        slot.update(_slot_metadata(live))
        slot["terminal_title"] = strip_herdr_origin_comments(_text(live.get("terminal_title") or live.get("terminal_title_stripped") or live.get("title") or slot.get("terminal_title")))
        ref = name or pane
        if ref not in visible_cache:
            visible_cache[ref] = _text(live.get("visible") or live.get("visible_text")) or _read_herdr_visible(herdr_bin, ref, runner=runner)
        visible = visible_cache[ref]
        if visible:
            slot["visible_summary"] = strip_herdr_origin_comments(_summarize_visible_lines(visible))
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
    if records and (scan_default or allow_quiescent_settle):
        live_refs = {
            _text(record.get(key))
            for record in records
            for key in ("name", "herdr_target", "id", "pane_id", "paneId", "target")
            if _text(record.get(key))
        }
        state["slots"] = [
            slot for slot in slots
            if (
                any(_text(slot.get(key)) in protected for key in ("slot_id", "pane_id", "target"))
                or slot.get("retained")
                or _text(slot.get("transport")).lower() in {"stdout", "dsh"}
                or any(_text(slot.get(key)) in live_refs for key in ("pane_id", "target", "run_label"))
            )
        ]
        slots = state["slots"]
    for slot in slots:
        _enrich_slot_metadata(slot, handoff_root=handoff_root, scan_default=scan_default)
    return state

def _compute_board_rollup(
    ledger: dict[str, Any],
    *,
    just_settled_slots: Iterable[str] = (),
) -> dict[str, Any]:
    slots = [dict(s) for s in (ledger.get("slots") or []) if isinstance(s, dict)]
    settled_set = {s.casefold() for s in just_settled_slots if _text(s)}
    live_spaces = ledger.get("spaces") if isinstance(ledger.get("spaces"), list) else ledger.get("workspaces")
    if isinstance(live_spaces, list) and live_spaces:
        live_workspace_ids = {
            _text(space.get(key))
            for space in live_spaces
            if isinstance(space, dict)
            for key in ("workspace_id", "workspaceId", "id", "label", "workspace_label")
            if _text(space.get(key))
        }
        filtered_slots = []
        for slot in slots:
            transport = _text(slot.get("transport")).lower()
            identity = {_text(slot.get(key)) for key in ("slot_id", "run_label", "pane_id") if _text(slot.get(key))}
            if identity & settled_set or transport in {"stdout", "dsh"} or slot.get("retained"):
                filtered_slots.append(slot)
                continue
            workspace_id = _text(slot.get("workspace_id"))
            pane_workspace = _text(slot.get("pane_id")).split(":", 1)[0]
            if not workspace_id and not pane_workspace:
                filtered_slots.append(slot)
                continue
            if (workspace_id or pane_workspace) in live_workspace_ids:
                filtered_slots.append(slot)
        slots = filtered_slots
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
        deps = ", ".join(_text(dep) for dep in slot.get("depends_on", []) if _text(dep))
        upstream = ", ".join(f"Wave {wave}" for wave in slot.get("upstream_waves", []))
        dependency = f" · 依赖 {upstream}: {deps}" if upstream and deps else ""
        return f"{_text(slot.get('linear_issue') or slot.get('run_label') or slot.get('slot_id'))} ({name}{': ' + stages if stages else ''}){dependency}"

    waves = sorted({int(s.get("wave_order") or 1) for s in slots})
    has_dependencies = len(waves) > 1 and any(s.get("depends_on") for s in slots)
    chain = " -> ".join(f"Wave {wave}: " + " + ".join(node(s) for s in slots if int(s.get("wave_order") or 1) == wave) for wave in waves)
    markdown_chain = re.sub(r"\bTIA-\d+\b", lambda match: _format_linear_issue_link(match.group(0)), chain)

    domain_map = {}
    try:
        domain_map = load_domain_map()
    except RouterError:
        pass

    def issue_cell(slot: dict[str, Any]) -> str:
        issue = _text(slot.get("linear_issue"))
        return _format_linear_issue_link(issue) if issue else "—"

    def stage_cell(slot: dict[str, Any]) -> str:
        summary = _summarize_concise_stage(slot)
        status = _text(slot.get("status"), "unknown").lower()
        if bool(slot.get("needs_owner_attention")) or status in {"blocked", "needs_input", "missing"}:
            reason = _text(slot.get("blocked_reason"), "owner_decision_required")
            summary = f"🚨 NEEDS OWNER ({reason}) · {summary}"
        return summary

    markdown = ["### 📊 全局 M2 统一汇总看板 (Unified M2 Rollup)"]
    if has_dependencies:
        markdown.extend([f"🔗 Wave 依赖：{markdown_chain}"])
    markdown.append("#### ⏳ 进行中 & 🚨 待处理")
    if in_flight:
        markdown.extend(["| Space | 关联单据 | 当前阶段 |", "|---|---|---|"])
        markdown.extend(
            f"| `{_resolve_slot_space_label(slot, domain_map)}` | {issue_cell(slot)} | {stage_cell(slot)} |"
            for slot in in_flight
        )
    else:
        markdown.append("无")
    markdown.append("#### ✅ 空闲待命")
    completed_lines: list[str] = []
    seen_spaces: set[str] = set()
    for slot in completed_slots:
        space = _resolve_slot_space_label(slot, domain_map)
        if space.casefold() in seen_spaces:
            continue
        seen_spaces.add(space.casefold())
        label = _text(slot.get("run_label") or slot.get("slot_id"))
        settled_marker = "🔔 " if label in just_settled_labels or _text(slot.get("slot_id")) in just_settled_labels else ""
        completed_lines.append(f"- {settled_marker}**{space}**（{issue_cell(slot)}）：{_summarize_concise_stage(slot)}")
    markdown.extend(completed_lines or ["无"])
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
