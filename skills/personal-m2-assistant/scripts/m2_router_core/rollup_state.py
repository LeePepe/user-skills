from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

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
