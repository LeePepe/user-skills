from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

from .apply_engine import _post_json

def apply_plan(plan: dict[str, Any], *, approve: bool = False, register_new_domains: bool = False, cleanup_duplicates: bool = False, replace_stale_m2: bool = False, domain_map_file: str | Path | None = None, plan_file: str | Path | None = None, apply_receipt_file: str | Path | None = None, state_file: str | Path | None = None, reset_state: bool = False, handoff_root: str | Path | None = None, agent_records: list[dict[str, Any]] | None = None, transport: str = "auto", sync_linear: bool = False, no_sync_linear: bool = False, hub_issue: str | None = None, local_hostname: str | None = None, linear_runner: CommandRunner | None = None, hub_issue_payload: dict[str, Any] | None = None, caller_bot: str = "", no_supervision: bool = False, no_routine: bool = False, spawn_background_sync: bool = False, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
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
    explicit_sync_requested = bool(sync_linear or linear_runner is not None or hub_issue_payload is not None)
    live_records = agent_records if agent_records is not None else (
        _live_agent_records(binary, runner=runner)
        if explicit_sync_requested and "herdr" in resolved_initial_transports and not is_stdout_only
        else []
    )

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
    host = _resolve_local_hostname(local_hostname)
    existing_cache = _load_cross_device_cache(cache_path)
    existing_devices = existing_cache.setdefault("devices", {})
    now_ts = _cst_now_iso()
    existing_devices[host] = _build_local_device_shard(
        board_rollup,
        previous_shard=existing_devices.get(host),
        now_iso=now_ts,
    )
    existing_cache["schema"] = M2_SYNC_SCHEMA
    existing_cache["updated_at"] = now_ts
    _save_cross_device_cache(cache_path, existing_cache)

    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=explicit_sync_requested,
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
        linear_sync_result = {
            "ok": True if not no_sync_linear else False,
            "updated": False,
            "deferred_to_background_task": not no_sync_linear and not is_stdout_only,
            "skipped_reason": sync_reason if no_sync_linear else "deferred_to_post_apply_background_task",
        }
    _attach_cross_device_to_rollup(
        board_rollup,
        linear_sync_result.get("state") if isinstance(linear_sync_result.get("state"), dict) else existing_cache,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )

    handoff_watch_arg = f" --handoff-root {shlex.quote(str(handoff_root))}" if handoff_root else ""
    effective_hub_issue = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
    host_watch_arg = f" --local-hostname {shlex.quote(str(local_hostname))}" if local_hostname else ""
    quota_protection = _resolve_quota_protection(
        plan=plan,
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=live_records if live_records else None,
        herdr_bin=binary,
        runner=runner,
        probe_herdr=False,
    )
    grok_supervision_disabled = bool(quota_protection.get("active"))
    post_apply_sync_cmd = None if (is_stdout_only or no_sync_linear) else (
        f"python3 ~/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py "
        f"--sync-herdr-to-issue --plan-file {shlex.quote(resolved_plan_file)} "
        f"--apply-receipt-file {shlex.quote(str(resolved_receipt_path))} "
        f"--state-file {shlex.quote(str(resolved_state_path))} "
        f"--hub-issue {shlex.quote(effective_hub_issue)}{host_watch_arg} --json{handoff_watch_arg}"
    )
    post_apply_sync_task = None if post_apply_sync_cmd is None else {
        "tool": "bash",
        "mode": "async",
        "detach": True,
        "shellId": f"sync-m2-issue-{safe_plan_id}",
        "description": f"Scan live Herdr spaces/agents in background and write back to Issue {effective_hub_issue}",
        "command": post_apply_sync_cmd,
    }
    background_sync_pid: int | None = None
    if (
        post_apply_sync_cmd is not None
        and (spawn_background_sync or _text(os.environ.get("M2_SPAWN_POST_APPLY_SYNC")) == "1")
        and runner is _run_command
    ):
        try:
            bg_args = [
                sys.executable,
                str(ROUTER_FILE.resolve()),
                "--sync-herdr-to-issue",
                "--plan-file", str(resolved_plan_file),
                "--apply-receipt-file", str(resolved_receipt_path),
                "--state-file", str(resolved_state_path),
                "--hub-issue", effective_hub_issue,
                "--json",
            ]
            if local_hostname:
                bg_args.extend(["--local-hostname", str(local_hostname)])
            if handoff_root:
                bg_args.extend(["--handoff-root", str(handoff_root)])
            proc = subprocess.Popen(
                bg_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            background_sync_pid = proc.pid
        except OSError:
            background_sync_pid = None
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
        "post_apply_sync_cmd": post_apply_sync_cmd,
        "post_apply_sync_task": post_apply_sync_task,
        **({"post_apply_sync_pid": background_sync_pid} if background_sync_pid is not None else {}),
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
