from .common import *
from .data_loaders import *

def build_goal_trace(
    goal_dir: Path,
    wh_maps: dict[str, Any],
    linked_sessions: list[ParsedSessionActivity] | None = None,
    skills_root: Path | None = None,
) -> TraceRecord | None:
    """Reconstruct a Goal trace strictly from real recorded evidence (no fabricated spans/metrics)."""
    ledger_path = goal_dir / "ledger.md"
    if not ledger_path.is_file():
        return None
    parsed = _parse_ledger_markdown(ledger_path)
    goal_id = parsed["goal_id"]
    linked_sessions = linked_sessions or []

    spec_path = goal_dir / "spec.md"
    spec_excerpt = ""
    if spec_path.is_file():
        try:
            spec_excerpt = spec_path.read_text(encoding="utf-8", errors="replace")[:500].strip()
        except OSError:
            spec_excerpt = ""

    wh_goal = wh_maps["goals"].get(goal_id, {})
    wh_events = wh_maps["goal_events"].get(goal_id, [])
    wh_runs = wh_maps["goal_runs"].get(goal_id, [])

    fallback_ns = int(ledger_path.stat().st_mtime * 1_000_000_000)
    evt_timestamps = [parse_iso_to_ns(e.get("ts"), fallback_ns) for e in parsed["events"] if e.get("ts")]
    if wh_goal.get("created_at"):
        evt_timestamps.append(parse_iso_to_ns(wh_goal["created_at"], fallback_ns))
    if wh_goal.get("last_event_at"):
        evt_timestamps.append(parse_iso_to_ns(wh_goal["last_event_at"], fallback_ns))
    for sess in linked_sessions:
        evt_timestamps.append(sess.start_ns)
        evt_timestamps.append(sess.end_ns)

    start_ns = min(evt_timestamps) if evt_timestamps else fallback_ns
    end_ns = max(evt_timestamps) if evt_timestamps else fallback_ns
    start_ns, end_ns = _clamp_span_window(start_ns, end_ns, min_duration_ns=1_000_000)

    evt_types = {e.get("EVT", "") for e in parsed["events"]} | {e.get("evt_type", "") for e in wh_events}
    if "GOAL_DONE" in evt_types or "PR_MERGED" in evt_types or wh_goal.get("derived_actual_status") == "done":
        status = "done"
    elif "TASK_BLOCKED" in evt_types or wh_goal.get("derived_actual_status") == "blocked":
        status = "blocked"
    elif parsed["tasks"] and all(t["status"].lower() == "done" for t in parsed["tasks"]):
        status = "done"
    else:
        status = str(wh_goal.get("derived_actual_status") or "in_progress")

    trace_id = deterministic_trace_id(f"goal:{goal_id}")
    root_span_id = deterministic_span_id(f"goal:{goal_id}:root")

    # Read ONLY receipts (never briefs!) for executed work evidence when session logs are absent
    receipt_files = sorted((goal_dir / "receipts").glob("*.md")) if (goal_dir / "receipts").is_dir() else []
    receipt_texts: list[tuple[Path, str]] = []
    for rf in receipt_files:
        try:
            receipt_texts.append((rf, rf.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue

    duration_ms = round((end_ns - start_ns) / 1_000_000, 2)
    spans: list[SpanRecord] = []

    # 1. Stage / Role Spans from real warehouse runs, receipts, or ledger events
    m1_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M1-TL")
    tl_model = str(wh_goal.get("tl_model") or (linked_sessions[0].selected_model if linked_sessions else "unknown"))
    spans.append(
        SpanRecord(
            span_id=m1_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name=f"Stage / Role: M1 TL ({wh_goal.get('tl_agent') or f'{goal_id}-tl'})",
            start_ns=start_ns,
            end_ns=end_ns,
            status_ok=None if status == "in_progress" else (status != "blocked"),
            attributes={
                "stage.role": "M1 TL",
                "stage.chain": "M2 -> M1 TL",
                "stage.round": "r1",
                "stage.model": tl_model,
                "stage.verdict": status.upper(),
                "evidence.source": "ledger.md",
            },
            input_payload={"tasks": [t["task_id"] for t in parsed["tasks"]]},
            output_payload={"status": status},
        )
    )

    if wh_goal.get("coordinator_bot") or "coordinator" in parsed["owner_line"].lower() or "协调者" in parsed["owner_line"]:
        m2_span_id = deterministic_span_id(f"goal:{goal_id}:stage:M2")
        spans.append(
            SpanRecord(
                span_id=m2_span_id,
                parent_span_id=root_span_id,
                category="stage_role",
                name=f"Stage / Role: M2 ({wh_goal.get('coordinator_bot') or 'coordinator'})",
                start_ns=start_ns,
                end_ns=start_ns + 1_000_000,
                status_ok=True,
                attributes={
                    "stage.role": "M2",
                    "stage.chain": "M3 -> M2",
                    "stage.round": "r1",
                    "evidence.source": "ledger.md:header",
                    **_point_event_timing(),
                },
                input_payload={"mandate": parsed["mandate"][:240]},
                output_payload={"coordinator": wh_goal.get("coordinator_bot") or "recorded", **_point_event_timing()},
            )
        )

    primary_worker_span_id = m1_span_id
    if wh_runs:
        for idx, run in enumerate(wh_runs):
            r_role = _normalize_role(str(run.get("role_name") or "Writer"))
            r_round = f"r{run.get('round_no') or 1}"
            r_model = str(run.get("model_id") or "unknown")
            r_verdict = str(run.get("v5_verdict") or run.get("status") or "UNKNOWN")
            r_task = str(run.get("task_id") or "task")
            r_uid = str(run.get("receipt_uid") or f"{idx}:{r_task}:{r_role}:{r_round}")
            w_span_id = deterministic_span_id(f"goal:{goal_id}:wh_run:{r_uid}")
            w_start = parse_iso_to_ns(run.get("started_at") or run.get("completed_at"), end_ns)
            run_measured = bool(run.get("started_at") and run.get("completed_at"))
            w_end = parse_iso_to_ns(run["completed_at"], w_start) if run_measured else w_start + 1_000_000
            w_start, w_end = _clamp_span_window(w_start, w_end)
            run_timing = ({"timing.measured": True, "timing.provenance": "measured"}
                          if run_measured else _point_event_timing())
            if idx == 0:
                primary_worker_span_id = w_span_id
            v_up = r_verdict.upper()
            v_ok: bool | None = False if "FAIL" in v_up else (True if v_up in ("PASS", "DONE", "COMPLETED") else None)
            spans.append(
                SpanRecord(
                    span_id=w_span_id,
                    parent_span_id=m1_span_id,
                    category="stage_role",
                    name=f"Stage / Role: {r_role} ({r_task} {r_round})",
                    start_ns=w_start,
                    end_ns=w_end,
                    status_ok=v_ok,
                    attributes={
                        "stage.role": r_role,
                        "stage.task_id": r_task,
                        "stage.round": r_round,
                        "stage.model": r_model[:80],
                        "stage.verdict": r_verdict[:40],
                        "evidence.source": f"warehouse.fact_gtt_task_run:{r_uid}",
                        **run_timing,
                    },
                    input_payload={"task_id": r_task, "role": r_role, "round": r_round},
                    output_payload={"verdict": r_verdict[:80], **run_timing},
                )
            )
    else:
        for idx, (rf, r_text) in enumerate(receipt_texts):
            r_role = _normalize_role(rf.stem)
            m_rnd = re.search(r"r(\d+)", rf.stem, re.I)
            r_round = f"r{m_rnd.group(1)}" if m_rnd else "r1"
            m_verd = _VERDICT_LINE_RE.search(r_text)
            r_verdict = m_verd.group(1).upper() if m_verd else "RECORDED"
            v_ok = False if r_verdict == "FAIL" else (True if r_verdict == "PASS" else None)
            rf_ns = int(rf.stat().st_mtime * 1_000_000_000)
            w_start, w_end = rf_ns, rf_ns + 1_000_000
            w_span_id = deterministic_span_id(f"goal:{goal_id}:receipt_role:{rf.name}")
            if idx == 0:
                primary_worker_span_id = w_span_id
            spans.append(
                SpanRecord(
                    span_id=w_span_id,
                    parent_span_id=m1_span_id,
                    category="stage_role",
                    name=f"Stage / Role: {r_role} ({rf.stem})",
                    start_ns=w_start,
                    end_ns=w_end,
                    status_ok=v_ok,
                    attributes={
                        "stage.role": r_role,
                        "stage.round": r_round,
                        "stage.verdict": r_verdict,
                        "evidence.source": f"receipts/{rf.name}:mtime",
                        **_point_event_timing(),
                    },
                    input_payload={"receipt": rf.name},
                    output_payload={"verdict": r_verdict, **_point_event_timing()},
                )
            )

    # 2–5. Evidence-backed Child Spans from linked real sessions AND explicit receipt evidence
    seen_skills: set[str] = set()
    first_skill_span_id: str | None = None
    seen_refs: set[tuple[str, str]] = set()
    script_count = 0
    llm_count = 0
    total_in_tokens = 0
    total_out_tokens = 0
    total_cache_tokens = 0

    for sess in linked_sessions:
        session_span_start = len(spans)
        sess_stage_id = deterministic_span_id(f"goal:{goal_id}:session_stage:{sess.session_id}")
        spans.append(
            SpanRecord(
                span_id=sess_stage_id,
                parent_span_id=m1_span_id,
                category="stage_role",
                name=f"Stage / Role: Session Worker ({sess.session_id[:8]})",
                start_ns=sess.start_ns,
                end_ns=sess.end_ns,
                status_ok=True,
                attributes={
                    "stage.role": "SessionWorker",
                    "stage.model": sess.selected_model,
                    "session.id": sess.session_id,
                    "evidence.source": f"session-state/{sess.session_id}/events.jsonl",
                },
                input_payload={"session_id": sess.session_id, "prompt": sess.first_prompt[:200]},
                output_payload={"llm_generations": len(sess.llm_calls)},
            )
        )

        for sk_name, sk_ts, src_id in sess.skill_invocations:
            if sk_name in seen_skills:
                continue
            seen_skills.add(sk_name)
            sk_span_id = deterministic_span_id(f"goal:{goal_id}:skill:{sess.session_id}:{sk_name}")
            if first_skill_span_id is None:
                first_skill_span_id = sk_span_id
            spans.append(
                SpanRecord(
                    span_id=sk_span_id,
                    parent_span_id=sess_stage_id,
                    category="skill_load",
                    name=f"Skill Load: {sk_name}",
                    start_ns=sk_ts,
                    end_ns=sk_ts + 1_000_000,
                    status_ok=True,
                    attributes={
                        "skill.name": sk_name,
                        "session.id": sess.session_id,
                        "evidence.source": src_id,
                    },
                    input_payload={"skill": sk_name},
                    output_payload={"loaded": True},
                )
            )

        for rp, b_len, r_ts, src_id in sess.ref_reads:
            dedup_key = (sess.session_id, rp)
            if dedup_key in seen_refs:
                continue
            seen_refs.add(dedup_key)
            f_bytes, line_cnt = _file_stats(rp, skills_root=skills_root)
            actual_bytes = b_len if b_len is not None else f_bytes
            attrs: dict[str, str | int | float | bool] = {
                "reference.path": rp,
                "reference.skill": _infer_skill_from_path(rp),
                "session.id": sess.session_id,
                "evidence.source": src_id,
            }
            out_pay: dict[str, Any] = {"path": rp}
            if actual_bytes is not None:
                attrs["reference.byte_len"] = actual_bytes
                attrs["reference.estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
                out_pay["byte_len"] = actual_bytes
                out_pay["estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
            if line_cnt is not None:
                attrs["reference.line_count"] = line_cnt
                out_pay["lines"] = line_cnt
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:ref:{sess.session_id}:{src_id}:{rp}"),
                    parent_span_id=sess_stage_id,
                    category="l1_reference_read",
                    name=f"L1 Reference Read: {Path(rp).name}",
                    start_ns=r_ts,
                    end_ns=r_ts + 1_000_000,
                    status_ok=True,
                    attributes=attrs,
                    input_payload={"path": rp},
                    output_payload=out_pay,
                )
            )

        for idx_sc, (sp_p, cmd_args, sc_ts, dur_ms, ec, src_id) in enumerate(sess.script_execs):
            script_count += 1
            sc_ok: bool | None = None if ec is None else (ec == 0)
            sc_attrs: dict[str, str | int | float | bool] = {
                "script.path": sp_p,
                "script.skill": _infer_skill_from_path(sp_p),
                "script.args": cmd_args,
                "script.duration_ms": dur_ms,
                "session.id": sess.session_id,
                "evidence.source": src_id,
            }
            sc_out: dict[str, Any] = {"duration_ms": dur_ms}
            if ec is not None:
                sc_attrs["script.exit_code"] = ec
                sc_out["exit_code"] = ec
            else:
                sc_attrs["script.exit_status"] = "unknown"
                sc_out["exit_code"] = None
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:script:{sess.session_id}:{idx_sc}:{src_id}"),
                    parent_span_id=sess_stage_id,
                    category="l2_script_exec",
                    name=f"L2 Script Exec: {Path(sp_p).name}",
                    start_ns=sc_ts,
                    end_ns=sc_ts + max(1, dur_ms) * 1_000_000,
                    status_ok=sc_ok,
                    attributes=sc_attrs,
                    input_payload={"script": sp_p, "command": cmd_args},
                    output_payload=sc_out,
                )
            )

        for idx_llm, (m_name, l_ts, dur_ms, in_t, out_t, ca_t, src_id) in enumerate(sess.llm_calls):
            llm_count += 1
            total_in_tokens += in_t
            total_out_tokens += out_t
            total_cache_tokens += ca_t
            spans.append(
                SpanRecord(
                    span_id=deterministic_span_id(f"goal:{goal_id}:llm:{sess.session_id}:{idx_llm}:{src_id}"),
                    parent_span_id=sess_stage_id,
                    category="llm_generation",
                    name=f"LLM Generation: {m_name}",
                    start_ns=l_ts,
                    end_ns=l_ts + max(1, dur_ms) * 1_000_000,
                    status_ok=True,
                    attributes={
                        "llm.model_name": m_name,
                        "llm.input_tokens": in_t,
                        "llm.output_tokens": out_t,
                        "llm.cache_read": ca_t,
                        "session.id": sess.session_id,
                        "evidence.source": src_id,
                    },
                    input_payload={"session_id": sess.session_id, "turn_index": idx_llm},
                    output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t},
                    model=m_name,
                    input_tokens=in_t,
                    output_tokens=out_t,
                    cache_read=ca_t,
                )
            )

        _apply_activity_evidence(spans[session_span_start:], sess)

    # Supplement ONLY from structured receipt token headers (never prose mentions).
    if not linked_sessions and receipt_texts:
        for rf, r_text in receipt_texts:
            rf_ns = int(rf.stat().st_mtime * 1_000_000_000)
            token_header = _RECEIPT_TOKENS_RE.search(r_text)
            if token_header is None:
                continue
            token_fields = token_header.group(1)
            m_in = _TOKEN_NUM_RE.search(token_fields)
            m_out = _OUT_TOKEN_RE.search(token_fields)
            m_ca = _CACHE_TOKEN_RE.search(token_fields)
            if m_in or m_out:
                in_t = int(m_in.group(1)) if m_in else 0
                out_t = int(m_out.group(1)) if m_out else 0
                ca_t = int(m_ca.group(1)) if m_ca else 0
                llm_count += 1
                total_in_tokens += in_t
                total_out_tokens += out_t
                total_cache_tokens += ca_t
                spans.append(
                    SpanRecord(
                        span_id=deterministic_span_id(f"goal:{goal_id}:rcpt_llm:{rf.name}"),
                        parent_span_id=primary_worker_span_id,
                        category="llm_generation",
                        name=f"LLM Generation: {tl_model}",
                        start_ns=rf_ns,
                        end_ns=rf_ns + 1_000_000,
                        status_ok=True,
                        attributes={
                            "llm.model_name": tl_model,
                            "llm.input_tokens": in_t,
                            "llm.output_tokens": out_t,
                            "llm.cache_read": ca_t,
                            "evidence.source": f"receipts/{rf.name}:Tokens",
                            **_point_event_timing(),
                        },
                        input_payload={"receipt": rf.name},
                        output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t,
                                        **_point_event_timing()},
                        model=tl_model,
                        input_tokens=in_t,
                        output_tokens=out_t,
                        cache_read=ca_t,
                    )
                )

    present_categories = sorted({sp.category for sp in spans if sp.category != "root"})
    missing_categories = [c for c in SPAN_CATEGORIES if c not in present_categories]

    root_input = {
        "goal_id": goal_id,
        "linear_issue": parsed["linear_id"] or "N/A",
        "repo": parsed["repo"],
        "mandate": parsed["mandate"][:400],
        "spec_summary": spec_excerpt[:300] if spec_excerpt else parsed["mandate"][:300],
    }
    root_output = {
        "status": status,
        "duration_ms": duration_ms,
        "task_count": len(parsed["tasks"]),
        "event_count": max(len(parsed["events"]), len(wh_events)),
        "receipt_count": len(receipt_files),
        "linked_sessions": len(linked_sessions),
        "llm_generations": llm_count,
        "total_input_tokens": total_in_tokens,
        "total_output_tokens": total_out_tokens,
        "total_cache_read": total_cache_tokens,
        "present_categories": present_categories,
        "missing_categories": missing_categories,
    }

    root_span = SpanRecord(
        span_id=root_span_id,
        parent_span_id=None,
        category="root",
        name=f"Goal: {goal_id}",
        start_ns=start_ns,
        end_ns=end_ns,
        status_ok=None if status == "in_progress" else (status != "blocked"),
        attributes={
            "goal.id": goal_id,
            "goal.linear_issue": parsed["linear_id"] or "none",
            "goal.repo": parsed["repo"],
            "goal.status": status,
            "goal.duration_ms": duration_ms,
            "goal.linked_sessions": len(linked_sessions),
            "goal.complete_5_categories": len(missing_categories) == 0,
        },
        input_payload=root_input,
        output_payload=root_output,
    )
    spans.insert(0, root_span)

    tags = ["backfill", "goal", f"status:{status}"]
    if parsed["linear_id"]:
        tags.append(parsed["linear_id"])
    if not missing_categories:
        tags.append("complete-5-cat")

    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root_span_id,
        trace_kind="goal",
        entity_id=goal_id,
        name=f"Goal: {goal_id}",
        start_ns=start_ns,
        end_ns=end_ns,
        status=status,
        input_payload=root_input,
        output_payload=root_output,
        tags=tags,
        metadata={
            "goal_id": goal_id,
            "linear_issue": parsed["linear_id"],
            "repo": parsed["repo"],
            "duration_ms": duration_ms,
            "linked_session_ids": [s.session_id for s in linked_sessions],
            "present_categories": present_categories,
            "missing_categories": missing_categories,
            "span_count": len(spans),
        },
        spans=spans,
    )


def build_session_trace_from_activity(
    activity: ParsedSessionActivity,
    skills_root: Path | None = None,
) -> TraceRecord:
    """Build a complete Session Prompt trace exporting ALL recorded events without truncation."""
    session_id = activity.session_id
    s_ns, e_ns = _clamp_span_window(activity.start_ns, activity.end_ns, min_duration_ns=1_000_000)

    trace_id = deterministic_trace_id(f"session:{session_id}")
    root_span_id = deterministic_span_id(f"session:{session_id}:root")
    stage_span_id = deterministic_span_id(f"session:{session_id}:stage:agent")

    prompt_summary = activity.first_prompt[:320] if activity.first_prompt else f"Session {session_id}"
    inferred_role = "M1 TL"
    lower_prompt = prompt_summary.lower()
    if "reviewer" in lower_prompt or "code-review" in lower_prompt:
        inferred_role = "Reviewer"
    elif "writer" in lower_prompt or "implement" in lower_prompt:
        inferred_role = "Writer"
    elif "m2-dispatcher" in lower_prompt:
        inferred_role = "M2"

    spans: list[SpanRecord] = [
        SpanRecord(
            span_id=stage_span_id,
            parent_span_id=root_span_id,
            category="stage_role",
            name=f"Stage / Role: {inferred_role} ({activity.selected_model})",
            start_ns=s_ns,
            end_ns=e_ns,
            status_ok=True,
            attributes={
                "stage.role": inferred_role,
                "stage.round": "r1",
                "stage.model": activity.selected_model,
                "stage.verdict": "COMPLETED",
                "evidence.source": activity.source_path,
            },
            input_payload={"prompt": prompt_summary[:200]},
            output_payload={"llm_calls": len(activity.llm_calls)},
        )
    ]

    seen_skills: set[tuple[str, int]] = set()
    for idx, (sk_name, sk_ts, src_id) in enumerate(activity.skill_invocations):
        key = (sk_name, sk_ts)
        if key in seen_skills:
            continue
        seen_skills.add(key)
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:skill:{idx}:{src_id}:{sk_name}"),
                parent_span_id=stage_span_id,
                category="skill_load",
                name=f"Skill Load: {sk_name}",
                start_ns=sk_ts,
                end_ns=sk_ts + 1_000_000,
                status_ok=True,
                attributes={"skill.name": sk_name, "evidence.source": src_id},
                input_payload={"skill": sk_name},
                output_payload={"loaded": True},
            )
        )

    seen_refs: set[tuple[str, int]] = set()
    for idx, (rp, b_len, r_ts, src_id) in enumerate(activity.ref_reads):
        r_key = (rp, r_ts)
        if r_key in seen_refs:
            continue
        seen_refs.add(r_key)
        f_bytes, line_cnt = _file_stats(rp, skills_root=skills_root)
        actual_bytes = b_len if b_len is not None else f_bytes
        ref_attrs: dict[str, str | int | float | bool] = {
            "reference.path": rp,
            "reference.skill": _infer_skill_from_path(rp),
            "evidence.source": src_id,
        }
        out_pay: dict[str, Any] = {"path": rp}
        if actual_bytes is not None:
            ref_attrs["reference.byte_len"] = actual_bytes
            ref_attrs["reference.estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
            out_pay["byte_len"] = actual_bytes
            out_pay["estimated_tokens"] = _estimate_tokens_from_bytes(actual_bytes)
        if line_cnt is not None:
            ref_attrs["reference.line_count"] = line_cnt
            out_pay["lines"] = line_cnt
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:ref:{idx}:{src_id}:{rp}"),
                parent_span_id=stage_span_id,
                category="l1_reference_read",
                name=f"L1 Reference Read: {Path(rp).name}",
                start_ns=r_ts,
                end_ns=r_ts + 1_000_000,
                status_ok=True,
                attributes=ref_attrs,
                input_payload={"path": rp},
                output_payload=out_pay,
            )
        )

    for idx, (sp_p, cmd_args, sc_ts, dur_ms, ec, src_id) in enumerate(activity.script_execs):
        sc_ok: bool | None = None if ec is None else (ec == 0)
        sc_attrs: dict[str, str | int | float | bool] = {
            "script.path": sp_p,
            "script.skill": _infer_skill_from_path(sp_p),
            "script.args": cmd_args,
            "script.duration_ms": dur_ms,
            "evidence.source": src_id,
        }
        sc_out: dict[str, Any] = {"duration_ms": dur_ms}
        if ec is not None:
            sc_attrs["script.exit_code"] = ec
            sc_out["exit_code"] = ec
        else:
            sc_attrs["script.exit_status"] = "unknown"
            sc_out["exit_code"] = None
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:script:{idx}:{src_id}:{sp_p}"),
                parent_span_id=stage_span_id,
                category="l2_script_exec",
                name=f"L2 Script Exec: {Path(sp_p).name}",
                start_ns=sc_ts,
                end_ns=sc_ts + max(1, dur_ms) * 1_000_000,
                status_ok=sc_ok,
                attributes=sc_attrs,
                input_payload={"script": sp_p, "command": cmd_args},
                output_payload=sc_out,
            )
        )

    # Export EVERY recorded LLM generation (zero per-trace truncation) so root totals reconcile 100%
    exported_in = 0
    exported_out = 0
    exported_cache = 0
    for idx, (m_name, l_ts, dur_ms, in_t, out_t, ca_t, src_id) in enumerate(activity.llm_calls):
        exported_in += in_t
        exported_out += out_t
        exported_cache += ca_t
        spans.append(
            SpanRecord(
                span_id=deterministic_span_id(f"session:{session_id}:llm:{idx}:{src_id}:{l_ts}"),
                parent_span_id=stage_span_id,
                category="llm_generation",
                name=f"LLM Generation: {m_name}",
                start_ns=l_ts,
                end_ns=l_ts + max(1, dur_ms) * 1_000_000,
                status_ok=True,
                attributes={
                    "llm.model_name": m_name,
                    "llm.input_tokens": in_t,
                    "llm.output_tokens": out_t,
                    "llm.cache_read": ca_t,
                    "evidence.source": src_id,
                },
                input_payload={"turn_index": idx, "session_id": session_id},
                output_payload={"input_tokens": in_t, "output_tokens": out_t, "cache_read": ca_t},
                model=m_name,
                input_tokens=in_t,
                output_tokens=out_t,
                cache_read=ca_t,
            )
        )

    _apply_activity_evidence(spans, activity)

    duration_ms = round((e_ns - s_ns) / 1_000_000, 2)
    present_categories = sorted({sp.category for sp in spans})
    missing_categories = [c for c in SPAN_CATEGORIES if c not in present_categories]

    root_input = {
        "session_id": session_id,
        "cwd": activity.cwd or "unknown",
        "prompt": prompt_summary,
        "selected_model": activity.selected_model,
    }
    root_output = {
        "status": "completed",
        "duration_ms": duration_ms,
        "llm_generations": len(activity.llm_calls),
        "total_input_tokens": exported_in,
        "total_output_tokens": exported_out,
        "total_cache_read": exported_cache,
        "present_categories": present_categories,
        "missing_categories": missing_categories,
    }

    root_span = SpanRecord(
        span_id=root_span_id,
        parent_span_id=None,
        category="root",
        name=f"User Prompt Session: {session_id[:8]}",
        start_ns=s_ns,
        end_ns=e_ns,
        status_ok=True,
        attributes={
            "session.id": session_id,
            "session.cwd": activity.cwd or "unknown",
            "session.model": activity.selected_model,
            "session.duration_ms": duration_ms,
            "session.complete_5_categories": len(missing_categories) == 0,
        },
        input_payload=root_input,
        output_payload=root_output,
    )
    spans.insert(0, root_span)

    tags = ["backfill", "session", f"role:{inferred_role}"]
    if not missing_categories:
        tags.append("complete-5-cat")

    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root_span_id,
        trace_kind="session",
        entity_id=session_id,
        name=f"Session Prompt: {session_id[:8]}",
        start_ns=s_ns,
        end_ns=e_ns,
        status="completed",
        input_payload=root_input,
        output_payload=root_output,
        tags=tags,
        metadata={
            "session_id": session_id,
            "cwd": activity.cwd,
            "model": activity.selected_model,
            "duration_ms": duration_ms,
            "present_categories": present_categories,
            "missing_categories": missing_categories,
            "span_count": len(spans),
        },
        spans=spans,
    )
