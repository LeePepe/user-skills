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
    target_issue: str | None = None,
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
    target_issue_writes: list[dict[str, Any]] = []
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board_rollup,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        target_issue_writes = write_watch_results_to_issues(
            plan,
            target_reports,
            state_path=resolved_state_path,
            hub_issue=hub_issue,
            target_issue=target_issue,
            linear_runner=linear_runner,
            linear_sync_result=linear_sync_result,
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
        "target_issue_writes": target_issue_writes,
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
