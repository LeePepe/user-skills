from .common import *
from .data_loaders import *
from .trace_builders import *

def build_session_trace_from_copilot(
    events_jsonl: Path,
    wh_maps: dict[str, Any],
    usage_by_session: dict[str, dict[str, list[dict[str, Any]]]],
    skills_root: Path | None = None,
) -> TraceRecord | None:
    activity = parse_copilot_session_activity(
        events_jsonl,
        wh_maps,
        usage_by_session,
        skills_root=skills_root,
    )
    if activity is None:
        return None
    return build_session_trace_from_activity(activity, skills_root=skills_root)


def _activity_category_count(act: ParsedSessionActivity) -> int:
    count = 1  # stage_role always present
    if act.skill_invocations:
        count += 1
    if act.ref_reads:
        count += 1
    if act.script_execs:
        count += 1
    if act.llm_calls:
        count += 1
    return count


def collect_historical_traces(
    *,
    handoff_root: Path = DEFAULT_HANDOFF_ROOT,
    copilot_sessions_dir: Path = DEFAULT_COPILOT_SESSIONS_DIR,
    usage_jsonl: Path = DEFAULT_USAGE_JSONL,
    warehouse_db: Path = DEFAULT_WAREHOUSE_DB,
    limit_goals: int = 35,
    limit_sessions: int = 55,
    max_sessions_per_goal: int = 6,
    skills_root: Path | None = None,
) -> tuple[list[TraceRecord], list[TraceRecord]]:
    wh_maps = _load_warehouse_maps(warehouse_db)
    usage_by_session = _load_usage_jsonl(usage_jsonl)

    # Index Copilot sessions for both Session export AND real Goal-to-Session attribution
    parsed_activities: list[ParsedSessionActivity] = []
    goal_to_activities: dict[str, list[ParsedSessionActivity]] = {}

    if copilot_sessions_dir.is_dir():
        rich_session_ids = (
            set(usage_by_session.keys())
            | set(wh_maps["refs_by_session"].keys())
            | set(wh_maps["scripts_by_session"].keys())
        )
        ev_files = [p for p in copilot_sessions_dir.glob("*/events.jsonl") if p.stat().st_size >= 512]
        by_mtime = sorted(ev_files, key=lambda p: p.stat().st_mtime, reverse=True)
        by_rich = sorted(
            ev_files,
            key=lambda p: (1 if p.parent.name in rich_session_ids else 0, p.stat().st_mtime),
            reverse=True,
        )
        scan_cap = max(600, limit_sessions * 8, limit_goals * 12)
        half_cap = scan_cap // 2
        candidates_to_scan = list(dict.fromkeys(by_mtime[:half_cap] + by_rich[:scan_cap]))[:scan_cap]
        for ev_path in candidates_to_scan:
            act = parse_copilot_session_activity(
                ev_path,
                wh_maps,
                usage_by_session,
                skills_root=skills_root,
            )
            if act is None:
                continue
            parsed_activities.append(act)
            for g_name in act.referenced_goals:
                goal_to_activities.setdefault(g_name, []).append(act)

    # 1. Build Goal Traces (prioritizing complete 5-category evidence + named spec Goals)
    goal_traces: list[TraceRecord] = []
    if handoff_root.is_dir() and limit_goals > 0:
        all_goal_dirs = [
            p.parent for p in handoff_root.glob("*/ledger.md") if not p.parent.name.startswith("_")
        ]
        priority_set = set(PRIORITY_GOALS)

        def _goal_rank(gdir: Path) -> tuple[int, int, float]:
            g_acts = [a for a in goal_to_activities.get(gdir.name, []) if len(a.referenced_goals) <= 8]
            has_all_5 = (
                any(a.skill_invocations for a in g_acts)
                and any(a.ref_reads for a in g_acts)
                and any(a.script_execs for a in g_acts)
                and any(a.llm_calls for a in g_acts)
            )
            is_named = 1 if (gdir.name in priority_set or gdir.name.startswith("vs-ui-")) else 0
            return (1 if has_all_5 else 0, is_named, (gdir / "ledger.md").stat().st_mtime)

        all_goal_dirs.sort(key=_goal_rank, reverse=True)
        for gdir in all_goal_dirs:
            if len(goal_traces) >= limit_goals:
                break
            linked = sorted(
                [a for a in goal_to_activities.get(gdir.name, []) if len(a.referenced_goals) <= 8],
                key=lambda a: (len(a.referenced_goals) == 1, _activity_category_count(a), a.end_ns),
                reverse=True,
            )[:max_sessions_per_goal]
            tr = build_goal_trace(gdir, wh_maps, linked_sessions=linked, skills_root=skills_root)
            if tr is not None:
                goal_traces.append(tr)

    # 2. Build Session Traces (prioritizing complete 5-category real sessions, deduplicated)
    session_traces: list[TraceRecord] = []
    seen_sids: set[str] = set()
    if limit_sessions > 0:
        ordered_acts = sorted(
            parsed_activities,
            key=lambda a: (_activity_category_count(a) == 5, _activity_category_count(a), a.end_ns),
            reverse=True,
        )
        for act in ordered_acts:
            if len(session_traces) >= limit_sessions:
                break
            if act.session_id in seen_sids:
                continue
            seen_sids.add(act.session_id)
            session_traces.append(build_session_trace_from_activity(act, skills_root=skills_root))

    # 3. Fallback session reconstruction from usage.jsonl / warehouse.db (strictly deduplicated!)
    if len(session_traces) < limit_sessions:
        ordered_fallback_sids: list[str] = list(
            dict.fromkeys(
                list(usage_by_session.keys())
                + list(wh_maps["turns_by_session"].keys())
                + list(wh_maps["refs_by_session"].keys())
                + list(wh_maps["scripts_by_session"].keys())
            )
        )
        for sid in ordered_fallback_sids:
            if len(session_traces) >= limit_sessions:
                break
            if not sid or sid in seen_sids:
                continue
            u_b = usage_by_session.get(sid, {})
            turns = wh_maps["turns_by_session"].get(sid, [])
            refs = u_b.get("reference_hit", []) + wh_maps["refs_by_session"].get(sid, [])
            scripts = u_b.get("script_call", []) + wh_maps["scripts_by_session"].get(sid, [])
            skill_turns = u_b.get("skill_turn", [])
            if not (turns or refs or scripts or skill_turns):
                continue

            seen_sids.add(sid)
            ts_candidates = [
                parse_iso_to_ns(r.get("ts"))
                for r in (turns + refs + scripts + skill_turns)
                if r.get("ts")
            ]
            s_ns = min(ts_candidates) if ts_candidates else int(time.time() * 1_000_000_000)
            e_ns = max(ts_candidates) if ts_candidates else s_ns + 1_000_000
            s_ns, e_ns = _clamp_span_window(s_ns, e_ns, min_duration_ns=1_000_000)

            fb_skills: list[tuple[str, int, str]] = []
            for idx, st in enumerate(skill_turns):
                sk = str(st.get("skill") or "").strip()
                if sk:
                    fb_skills.append(
                        (
                            sk,
                            parse_iso_to_ns(st.get("ts"), s_ns),
                            str(st.get("event_id") or f"usage:skill_turn:{idx}"),
                        )
                    )

            fb_refs: list[tuple[str, int | None, int, str]] = []
            for idx, rf in enumerate(refs):
                rp = str(rf.get("reference_path") or "").strip()
                if rp:
                    raw_b = rf.get("byte_len")
                    b_len = int(raw_b) if raw_b is not None and int(raw_b) > 0 else None
                    fb_refs.append(
                        (
                            rp,
                            b_len,
                            parse_iso_to_ns(rf.get("ts"), s_ns),
                            str(rf.get("hit_uid") or rf.get("event_id") or f"ref:{idx}"),
                        )
                    )

            fb_durations: dict[str, int] = {}
            fb_scripts: list[tuple[str, str, int, int, int | None, str]] = []
            for idx, sc in enumerate(scripts):
                sp_p = str(sc.get("script_path") or "").strip()
                if sp_p:
                    dur = max(1, int(float(sc.get("duration_ms") or 1)))
                    raw_ec = sc.get("exit_code")
                    ec = int(raw_ec) if raw_ec is not None else None
                    source_id = str(sc.get("call_uid") or sc.get("event_id") or f"script:{idx}")
                    if sc.get("duration_ms") is not None:
                        fb_durations[source_id] = dur
                    fb_scripts.append((sp_p, sp_p, parse_iso_to_ns(sc.get("ts"), s_ns), dur, ec, source_id))

            fb_llms: list[tuple[str, int, int, int, int, int, str]] = []
            token_skill_turns = [row for row in skill_turns if any(
                int(row.get(key) or 0) > 0 for key in ("input_tokens", "output_tokens", "cache_read")
            )]
            source_llm_rows = token_skill_turns if token_skill_turns else turns
            for idx, tn in enumerate(source_llm_rows):
                in_t = int(tn.get("input_tokens") or 0)
                out_t = int(tn.get("output_tokens") or 0)
                ca_t = int(tn.get("cache_read") or 0)
                if in_t > 0 or out_t > 0 or ca_t > 0:
                    m_name = str(tn.get("model") or "unknown")
                    fb_llms.append(
                        (
                            m_name,
                            parse_iso_to_ns(tn.get("ts"), s_ns),
                            1,
                            in_t,
                            out_t,
                            ca_t,
                            str(tn.get("turn_uuid") or tn.get("event_id") or f"llm:{idx}"),
                        )
                    )

            fb_act = ParsedSessionActivity(
                session_id=sid,
                source_path="telemetry_fallback",
                selected_model=fb_llms[0][0] if fb_llms else "unknown",
                cwd="telemetry_fallback",
                first_prompt="",
                start_ns=s_ns,
                end_ns=e_ns,
                referenced_goals=set(),
                skill_invocations=fb_skills,
                ref_reads=fb_refs,
                script_execs=fb_scripts,
                llm_calls=fb_llms,
                measured_durations_ms=fb_durations,
            )
            session_traces.append(build_session_trace_from_activity(fb_act, skills_root=skills_root))

    return goal_traces, session_traces
