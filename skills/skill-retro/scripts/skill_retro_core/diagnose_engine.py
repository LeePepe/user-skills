from __future__ import annotations
from .common import *

"""Diagnose Engine implementation for the skill-retro engine."""

def _evaluate_session_progressive_and_preread(loaded_for_sk: Sequence[SessionObservation], sk_name: str) -> tuple[int, list[str], int]:
    """Compute (progressive_catch_count, wasted_preread_sids, unresolved_failure_count) for a skill across sessions.

    Establishes a unified chronological global timeline (`timeline_idx`) across all reads, edits, script calls,
    and bash commands in the session (sorting by `(ts, seq)` and deduplicating mirrored cross-source calls):
      - Wasted pre-read: reading >=3 distinct drawers cold-start before the first script call or edit on the
        unified timeline, OR reading drawer(s) after which zero downstream actions occur (`action_idx > last_read_idx`).
      - Progressive catch: `exit=1` on `script_rel` followed within 1-2 turns (`<=6` unified global timeline steps,
        `<=2` intervening calls of the same `script_rel`) by reading a drawer or editing code and passing `exit=0`
        on the SAME `script_rel`.
      - Unresolved failure count: count of distinct `(session_id, script_rel)` validators whose chronologically
        last failure is not followed by a subsequent `exit=0` on the same `script_rel`.
    """
    wasted_sids: list[str] = []
    prog_catches = 0
    unresolved_failures = 0
    for o in loaded_for_sk:
        raw_timeline: list[tuple[datetime, int, str, Any]] = []
        for r_sk, r_rel, r_ts, r_seq in o.ref_reads:
            raw_timeline.append((r_ts, r_seq, 'read', (r_sk, r_rel)))
        edit_ev_list = list(o.edit_events)
        if not edit_ev_list and o.edit_seqs:
            edit_ev_list = [(o.started_at, e_seq) for e_seq in o.edit_seqs]
        for e_ts, e_seq in edit_ev_list:
            raw_timeline.append((e_ts, e_seq, 'edit', None))
        all_call_seqs = {c.seq for c in o.script_calls}
        for c_ev in _deduplicate_session_script_calls(o):
            raw_timeline.append((c_ev.ts, c_ev.seq, 'script', c_ev))
        bash_ev_list = list(o.bash_events)
        if not bash_ev_list and o.bash_commands:
            bash_ev_list = [(cmd, ec, dur, o.started_at, b_seq) for cmd, ec, dur, b_seq in o.bash_commands]
        for _, _, _, b_ts, b_seq in bash_ev_list:
            if b_seq in all_call_seqs:
                continue
            raw_timeline.append((b_ts, b_seq, 'bash', None))
        raw_timeline.sort(key=lambda item: (item[0], item[1]))
        sk_reads_tl: list[tuple[str, int]] = []
        sk_edits_tl: list[int] = []
        all_actions_tl: list[int] = []
        sk_calls_tl: list[tuple[int, ScriptCallEvent]] = []
        for tl_idx, (_, _, kind, payload) in enumerate(raw_timeline, 1):
            if kind == 'read':
                r_sk, r_rel = payload
                if r_sk == sk_name:
                    sk_reads_tl.append((r_rel, tl_idx))
            elif kind == 'edit':
                sk_edits_tl.append(tl_idx)
                all_actions_tl.append(tl_idx)
            elif kind == 'script':
                c_obj: ScriptCallEvent = payload
                all_actions_tl.append(tl_idx)
                if c_obj.skill == sk_name:
                    sk_calls_tl.append((tl_idx, c_obj))
            elif kind == 'bash':
                all_actions_tl.append(tl_idx)
        first_call_or_edit_idx = min([idx for idx, _ in sk_calls_tl] + sk_edits_tl, default=None)
        cold_start_distinct_drawers = {r_rel for r_rel, r_idx in sk_reads_tl if first_call_or_edit_idx is None or r_idx < first_call_or_edit_idx}
        last_read_idx = max((r_idx for _, r_idx in sk_reads_tl), default=None)
        has_action_after_reads = any((a_idx > last_read_idx for a_idx in all_actions_tl)) if last_read_idx is not None else bool(all_actions_tl or o.edit_count > 0)
        if len(cold_start_distinct_drawers) >= 3 or (sk_reads_tl and (not has_action_after_reads)):
            wasted_sids.append(o.session_id)
        by_validator: dict[str, list[tuple[int, ScriptCallEvent]]] = {}
        for tl_idx, c_obj in sk_calls_tl:
            by_validator.setdefault(c_obj.script_rel, []).append((tl_idx, c_obj))
        for _, v_items in by_validator.items():
            failed_positions = [pos for pos, (_, c_obj) in enumerate(v_items) if c_obj.exit_code not in (None, 0, 2)]
            if not failed_positions:
                continue
            validator_recovered = False
            last_recovered_pos = -1
            for pos, (fail_tl_idx, call_ev) in enumerate(v_items):
                if call_ev.exit_code in (None, 0, 2):
                    continue
                if pos < last_recovered_pos:
                    continue
                later_same_script = [(pos + offset, pass_tl_idx, later) for offset, (pass_tl_idx, later) in enumerate(v_items[pos + 1:pos + 3], 1) if later.exit_code == 0 and 0 < pass_tl_idx - fail_tl_idx <= 6]
                for pass_pos, pass_tl_idx, _ in later_same_script:
                    intervening_read_or_edit = any((fail_tl_idx < r_idx < pass_tl_idx for _, r_idx in sk_reads_tl)) or any((fail_tl_idx < e_idx < pass_tl_idx for e_idx in sk_edits_tl))
                    if intervening_read_or_edit:
                        validator_recovered = True
                        last_recovered_pos = max(last_recovered_pos, pass_pos)
                        break
            if validator_recovered:
                prog_catches += 1
            last_fail_pos = max(failed_positions)
            has_pass_after_last_fail = any((c_obj.exit_code == 0 for _, c_obj in v_items[last_fail_pos + 1:]))
            if not has_pass_after_last_fail:
                unresolved_failures += 1
    return (prog_catches, wasted_sids, unresolved_failures)

def compute_skill_retro_diagnostics(*, inventory: Mapping[str, Mapping[str, Any]], skill_filter: str | None=None, emit_briefs_dir: Path | None=None, mode: str='full', aspects: Sequence[str] | str | None=None, coupled_with: Sequence[str] | str | None=None, relays: Sequence[Mapping[str, Any]] | None=None, warehouse_db: Path | None=None, copilot_db: Path | None=None, copilot_sessions_dir: Path | None=None, usage_jsonl: Path | None=None, telemetry_shards_dir: Path | None=None, trace_export_path: Path | None=None, handoff_root: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, days: int=30, shard_reader: Callable[[Path | None, int], list[dict[str, Any]]] | None=None, queue_fn: Callable[[list[dict[str, Any]], Path], list[dict[str, Any]]] | None=None, auto_propose: bool=False, proposals_path: Path | None=None, limit_sessions: int | None=DEFAULT_MAX_COPILOT_SESSIONS_SCAN) -> dict[str, Any]:
    """Run the 4-Layer / 6-Dimension Progressive Self-Evolution & Wide-Probe Multi-Angle Skill Diagnostic."""
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path or DEFAULT_PROPOSALS_PATH
    norm_mode = 'partial' if str(mode or 'full').strip().lower() == 'partial' else 'full'
    selected_aspects = _normalize_aspects(aspects, mode=norm_mode)
    if norm_mode == 'full':
        selected_aspects = list(CANONICAL_ASPECTS)
    norm_filter = _norm_skill(skill_filter) if skill_filter else ''
    if norm_filter:
        inventory = {k: v for k, v in inventory.items() if _norm_skill(k) == norm_filter}
    now_cst = datetime.now(CST)
    today_midnight = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_midnight - timedelta(days=max(1, days) - 1)
    window_end = today_midnight + timedelta(days=1)
    known_skills = set(inventory.keys())
    basename_to_targets: dict[str, list[tuple[str, str]]] = {}
    for sk_name, meta in inventory.items():
        for rel_sc in meta.get('script_entries', []):
            bname = Path(str(rel_sc)).name
            basename_to_targets.setdefault(bname, []).append((sk_name, str(rel_sc)))
    observations = _parse_copilot_sessions(copilot_sessions_dir, window_start=window_start, window_end=window_end, known_skills=known_skills, basename_to_targets=basename_to_targets, max_sessions=limit_sessions)
    shards: list[dict[str, Any]] = []
    if shard_reader and telemetry_shards_dir:
        with contextlib.suppress(OSError):
            shards = shard_reader(telemetry_shards_dir, days)
    _ingest_usage_jsonl_and_shards(observations, usage_jsonl=usage_jsonl, shard_records=shards, window_start=window_start, window_end=window_end, known_skills=known_skills, inventory=inventory)
    ingested_trace_spans = _ingest_trace_export(observations, trace_export_path=trace_export_path, window_start=window_start, window_end=window_end, known_skills=known_skills)
    wh_meta = _ingest_warehouse_and_copilot_db(observations, warehouse_db=warehouse_db, copilot_db=copilot_db, window_start=window_start, window_end=window_end, known_skills=known_skills, basename_to_targets=basename_to_targets)
    all_sessions = list(observations.values())
    for obs_item in all_sessions:
        _deduplicate_session_script_calls(obs_item)
    total_sessions = len(all_sessions)
    all_stamps = [obs.ended_at for obs in all_sessions]
    source_as_of = max(all_stamps).isoformat() if all_stamps else None
    sources_present = any((_safe_is_file(p) or _safe_is_dir(p) for p in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)))
    coverage_status = 'healthy' if total_sessions > 0 else 'unknown' if sources_present else 'missing'
    trigger_misses: dict[str, list[dict[str, Any]]] = {}
    domain_occurrences: dict[str, set[str]] = {sk: set() for sk in known_skills}
    for obs in all_sessions:
        for sk in obs.skills_loaded:
            domain_occurrences.setdefault(sk, set()).add(obs.session_id)
        for c in obs.script_calls:
            if c.skill in known_skills:
                domain_occurrences.setdefault(c.skill, set()).add(obs.session_id)
        cmd_texts = {cmd for cmd, _, _, _ in obs.bash_commands}
        for target_sk, matcher in _DOMAIN_HINTS:
            if target_sk not in known_skills:
                continue
            if matcher(obs.edited_files, cmd_texts):
                domain_occurrences.setdefault(target_sk, set()).add(obs.session_id)
                used_script = any((c.skill == target_sk for c in obs.script_calls))
                if target_sk not in obs.skills_loaded and (not used_script):
                    sample_files = sorted((_redact_command_sample(f, skills_root=s_root, dev_root=d_root) for f in list(obs.edited_files)[:3]))
                    trigger_misses.setdefault(target_sk, []).append({'session_id': obs.session_id[:12], 'evidence_files': sample_files})
    zero_action_loads: list[dict[str, Any]] = []
    for sk_name in sorted(known_skills):
        loaded_obs = [o for o in all_sessions if sk_name in o.skills_loaded]
        if not loaded_obs:
            continue
        empty_obs = [o for o in loaded_obs if not any((r_sk == sk_name for r_sk, _, _, _ in o.ref_reads)) and (not any((c.skill == sk_name for c in o.script_calls))) and (o.edit_count == 0) and (len(o.bash_commands) == 0)]
        rate = round(len(empty_obs) / len(loaded_obs), 4)
        if empty_obs and (len(empty_obs) >= 2 or rate >= 0.5):
            zero_action_loads.append({'skill': sk_name, 'loads': len(loaded_obs), 'zero_action_sessions': len(empty_obs), 'zero_action_rate': rate})
    trigger_miss_items = [{'skill': sk, 'missed_sessions': len(items), 'domain_sessions': len(domain_occurrences.get(sk, set())), 'miss_rate': round(len(items) / max(1, len(domain_occurrences.get(sk, set()))), 4), 'samples': items[:3]} for sk, items in sorted(trigger_misses.items(), key=lambda pair: (-len(pair[1]), pair[0]))]
    layer0 = {'trigger_misses': trigger_miss_items, 'zero_action_loads': sorted(zero_action_loads, key=lambda x: (-x['zero_action_sessions'], x['skill'])), 'healthy_r1_to_r2_convergences': wh_meta['r1_to_r2_converged_tasks'][:10], 'healthy_convergence_count': len(wh_meta['r1_to_r2_converged_tasks']), 'directional_drifts': wh_meta['directional_drift_tasks'][:10], 'directional_drift_count': len(wh_meta['directional_drift_tasks'])}
    hot_drawers: list[dict[str, Any]] = []
    on_demand_correction_drawers: list[dict[str, Any]] = []
    unwired_disconnected_drawers: list[dict[str, Any]] = []
    true_zero_hit_deadwood: list[dict[str, Any]] = []
    dormant_scenario_drawers: list[dict[str, Any]] = []
    progressive_catch_by_skill: dict[str, int] = {sk: 0 for sk in known_skills}
    wasted_preread_by_skill: dict[str, dict[str, Any]] = {}
    for sk_name, meta in sorted(inventory.items()):
        loaded_for_sk = [o for o in all_sessions if sk_name in o.skills_loaded or any((r_sk == sk_name for r_sk, _, _, _ in o.ref_reads)) or any((c.skill == sk_name for c in o.script_calls))]
        prog_catches, wasted_sids, unresolved_validator_failures = _evaluate_session_progressive_and_preread(loaded_for_sk, sk_name)
        progressive_catch_by_skill[sk_name] = prog_catches
        loaded_cnt = len(loaded_for_sk)
        wasted_rate = round(len(wasted_sids) / loaded_cnt, 4) if loaded_cnt > 0 else 0.0
        wasted_preread_by_skill[sk_name] = {'skill': sk_name, 'loaded_sessions': loaded_cnt, 'wasted_preread_sessions': len(wasted_sids), 'wasted_preread_rate': wasted_rate, 'progressive_catch_count': prog_catches, 'unresolved_validator_failures': unresolved_validator_failures}
        ref_entries = list(meta.get('reference_entries') or [])
        if not ref_entries:
            continue
        pkg_path = Path(str(meta.get('path') or s_root / sk_name))
        scen_sids = domain_occurrences.get(sk_name, set())
        scenario_count = len(scen_sids)
        failing_sids = {o.session_id for o in all_sessions if any((c.skill == sk_name and c.exit_code not in (None, 0, 2) for c in o.script_calls))}
        escape_counts = wh_meta['skill_escapes'].get(sk_name, {'impl_bug': 0, 'spec_gap': 0})
        total_domain_failures = unresolved_validator_failures + escape_counts.get('impl_bug', 0) + escape_counts.get('spec_gap', 0)
        for ref_info in ref_entries:
            rel_ref = str(ref_info.get('rel_path') or '')
            if not rel_ref.startswith('references/'):
                continue
            hit_sessions: set[str] = set()
            total_hits = 0
            post_failure_reads = 0
            for o in all_sessions:
                matching_reads = [(ts, seq) for r_sk, r_rel, ts, seq in o.ref_reads if r_sk == sk_name and r_rel == rel_ref]
                if not matching_reads:
                    continue
                hit_sessions.add(o.session_id)
                total_hits += len(matching_reads)
                first_fail_seq = min((c.seq for c in o.script_calls if c.skill == sk_name and c.exit_code not in (None, 0)), default=None)
                if first_fail_seq is not None and any((seq > first_fail_seq for _, seq in matching_reads)):
                    post_failure_reads += 1
            hit_sess_cnt = len(hit_sessions)
            hit_rate = round(hit_sess_cnt / scenario_count, 4) if scenario_count > 0 else 0.0
            script_linked = _drawer_linked_in_scripts(pkg_path, rel_ref)
            l0_linked = _drawer_linked_in_l0(pkg_path, rel_ref)
            base_row = {'skill': sk_name, 'drawer': rel_ref, 'hits': total_hits, 'hit_sessions': hit_sess_cnt, 'scenario_sessions': scenario_count, 'scenario_hit_rate': hit_rate, 'line_count': int(ref_info.get('line_count') or 0), 'tokens_est': int(ref_info.get('tokens_est') or 0), 'linked_in_scripts': script_linked, 'linked_in_l0': l0_linked}
            if scenario_count >= 3 and hit_rate >= 0.6 and (hit_sess_cnt >= 3):
                hot_drawers.append({**base_row, 'classification': 'hot_drawer', 'evolution_action': 'Sink repeated command/checklist lines into deterministic script or concise L0 table.'})
            elif post_failure_reads >= 1 or (hit_sess_cnt >= 1 and hit_rate < 0.6 and (failing_sids & hit_sessions or script_linked)):
                on_demand_correction_drawers.append({**base_row, 'post_failure_reads': post_failure_reads, 'classification': 'on_demand_correction_drawer', 'evolution_action': 'Healthy runtime progressive disclosure — preserve drawer and keep self-disclosing error pointer.'})
            elif total_domain_failures >= 1 and hit_sess_cnt == 0 and (not script_linked) and l0_linked:
                unwired_disconnected_drawers.append({**base_row, 'domain_failure_count': total_domain_failures, 'classification': 'unwired_disconnected_drawer', 'evolution_action': f'Domain/script failed {total_domain_failures}x without reading `{rel_ref}`; print self-disclosing pointer to `skills/{sk_name}/{rel_ref}` on script exit=1.'})
            elif scenario_count >= 5 and hit_sess_cnt == 0:
                true_zero_hit_deadwood.append({**base_row, 'classification': 'true_zero_hit_deadwood', 'evolution_action': f'Scenario occurred {scenario_count}x with 0 reads — candidate for archive/merge.'})
            elif hit_sess_cnt == 0:
                dormant_scenario_drawers.append({**base_row, 'classification': 'dormant_scenario_drawer', 'evolution_action': 'Low scenario occurrence; retain without premature deletion.'})
    layer1 = {'hot_drawers': sorted(hot_drawers, key=lambda r: (-r['hit_sessions'], r['skill'], r['drawer'])), 'on_demand_correction_drawers': sorted(on_demand_correction_drawers, key=lambda r: (-r.get('post_failure_reads', 0), -r['hit_sessions'], r['skill'])), 'unwired_disconnected_drawers': sorted(unwired_disconnected_drawers, key=lambda r: (-r['domain_failure_count'], -r['scenario_sessions'], r['skill'], r['drawer'])), 'true_zero_hit_deadwood': sorted(true_zero_hit_deadwood, key=lambda r: (-r['scenario_sessions'], -r['tokens_est'], r['skill'], r['drawer'])), 'dormant_scenario_drawers_count': len(dormant_scenario_drawers), 'progressive_catch_count': sum(progressive_catch_by_skill.values()), 'progressive_catch_by_skill': progressive_catch_by_skill, 'wasted_preread_by_skill': wasted_preread_by_skill}
    calls_by_script: dict[tuple[str, str], list[ScriptCallEvent]] = {}
    for sk_name, meta in inventory.items():
        for rel_sc in meta.get('script_entries', []):
            calls_by_script.setdefault((sk_name, str(rel_sc)), [])
    for o in all_sessions:
        for c in o.script_calls:
            calls_by_script.setdefault((c.skill, c.script_rel), []).append(c)
    cross_repo_gate_health: dict[str, Any] = {'available': False, 'repos_scanned': 0, 'healthy_repos_count': 0, 'drifted_repos_count': 0, 'average_gate_health_score': 100.0, 'total_findings': 0, 'rule_counts': {}, 'speedup_4_angles': {}, 'repos': [], 'actionable_proposals': []}
    if 'repo-kit' in inventory or norm_filter == 'repo-kit':
        cross_repo_gate_health = _compute_cross_repo_gate_health(skills_root=s_root, dev_root=d_root)
    true_catchers: list[dict[str, Any]] = []
    ceremonial_rubber_stamps: list[dict[str, Any]] = []
    rigid_roadblocks: list[dict[str, Any]] = []
    watermarked_resolved_roadblocks: list[dict[str, Any]] = []
    latency_sinks: list[dict[str, Any]] = []
    aci_output_bloat: list[dict[str, Any]] = []
    aci_output_bloat_by_skill: dict[str, int] = {sk: 0 for sk in known_skills}
    for (sk_name, sc_rel), sc_calls in sorted(calls_by_script.items()):
        if not sc_calls:
            continue
        ordered_calls = sorted(sc_calls, key=lambda c: (c.ts, c.seq))
        bloated_calls = [c for c in ordered_calls if c.aci_output_bloat or c.output_chars > ACI_OUTPUT_BLOAT_CHARS]
        if bloated_calls:
            aci_output_bloat_by_skill[sk_name] = aci_output_bloat_by_skill.get(sk_name, 0) + len(bloated_calls)
            aci_output_bloat.append({'skill': sk_name, 'script': sc_rel, 'bloat_call_count': len(bloated_calls), 'max_output_chars': max((c.output_chars for c in bloated_calls), default=ACI_OUTPUT_BLOAT_CHARS + 1), 'classification': 'aci_output_bloat', 'evolution_action': 'Add `--summary` / bounded output cap (`<=20KB`) so L2 CLI output does not overflow agent context.'})
        graded = [c for c in ordered_calls if c.exit_code is not None]
        exit0_cnt = sum((1 for c in graded if c.exit_code == 0))
        exit1_cnt = sum((1 for c in graded if c.exit_code not in (0, 2)))
        exit2_calls = [c for c in graded if c.exit_code == 2]
        sk_meta = inventory.get(sk_name) or {}
        current_skill_ver = str(sk_meta.get('skill_version') or '').strip() or None
        script_wm_map = sk_meta.get('script_watermarks') if isinstance(sk_meta.get('script_watermarks'), Mapping) else {}
        watermark_epoch: int | None = None
        if isinstance(script_wm_map, Mapping) and sc_rel in script_wm_map:
            watermark_epoch = int(script_wm_map[sc_rel])
        elif sk_meta.get('last_modified_epoch') is not None:
            watermark_epoch = int(sk_meta['last_modified_epoch'])
        pre_fix_exit2_calls: list[ScriptCallEvent] = []
        post_fix_exit2_calls: list[ScriptCallEvent] = []
        for c in exit2_calls:
            if _is_call_post_fix(c.ts, c.skill_version, current_skill_version=current_skill_ver, watermark_epoch=watermark_epoch):
                post_fix_exit2_calls.append(c)
            else:
                pre_fix_exit2_calls.append(c)
        by_sess: dict[str, list[ScriptCallEvent]] = {}
        for c in graded:
            by_sess.setdefault(c.session_id, []).append(c)
        fail_to_pass_sessions = 0
        exit2_arg_only_sessions = 0
        post_fix_exit2_sids = {c.session_id for c in post_fix_exit2_calls}
        for sid, s_calls in by_sess.items():
            saw_fail1 = False
            caught_in_sess = False
            saw_exit2 = False
            for c in s_calls:
                if c.exit_code not in (None, 0, 2):
                    saw_fail1 = True
                elif c.exit_code == 2:
                    saw_exit2 = True
                elif c.exit_code == 0 and saw_fail1:
                    caught_in_sess = True
            if caught_in_sess:
                fail_to_pass_sessions += 1
            if saw_exit2 and sid in post_fix_exit2_sids:
                obs_ref = observations.get(sid)
                if obs_ref is None or obs_ref.edit_count == 0 or any((x.exit_code == 0 for x in s_calls)):
                    exit2_arg_only_sessions += 1
        measured_durs = [float(c.duration_ms) for c in ordered_calls if c.measured and c.duration_ms is not None]
        p50_ms = _percentile(measured_durs, 50)
        max_ms = round(max(measured_durs), 2) if measured_durs else None
        escapes_for_skill = wh_meta['skill_escapes'].get(sk_name, {'impl_bug': 0, 'spec_gap': 0})
        total_skill_escapes = escapes_for_skill.get('impl_bug', 0) + escapes_for_skill.get('spec_gap', 0)
        if fail_to_pass_sessions > 0:
            true_catchers.append({'skill': sk_name, 'script': sc_rel, 'calls': len(ordered_calls), 'fail_to_pass_sessions': fail_to_pass_sessions, 'non_zero_catches': exit1_cnt, 'p50_ms': p50_ms, 'classification': 'true_catcher'})
        elif len(graded) >= 3 and exit0_cnt == len(graded):
            ceremonial_status = 'ceremonial_ leaky_gate' if total_skill_escapes > 0 else 'fast_baseline_guard' if (p50_ms or 0.0) <= 500.0 else 'ceremonial_rubber_stamp'
            if total_skill_escapes > 0 or (p50_ms or 0.0) > 500.0 or len(graded) >= 5:
                ceremonial_rubber_stamps.append({'skill': sk_name, 'script': sc_rel, 'calls': len(ordered_calls), 'exit_0_rate': 1.0, 'co_occurring_skill_escapes': total_skill_escapes, 'p50_ms': p50_ms, 'sub_status': ceremonial_status.strip(), 'classification': 'ceremonial_rubber_stamp', 'evolution_action': f'100% exit=0 across {len(graded)} runs while `{sk_name}` had {total_skill_escapes} review escapes — deepen assertions.' if total_skill_escapes > 0 else '100% exit=0 — keep silent if <0.5s baseline, or merge into batch gate.'})
        if exit2_calls:
            active_e2 = post_fix_exit2_calls
            samples: list[str] = []
            for c in active_e2 if active_e2 else pre_fix_exit2_calls:
                red = _redact_command_sample(c.command, skills_root=s_root, dev_root=d_root)
                if red and red not in samples:
                    samples.append(red)
                if len(samples) >= 3:
                    break
            wm_base = {'skill': sk_name, 'script': sc_rel, 'skill_version': current_skill_ver, 'last_modified_epoch': watermark_epoch, 'calls': len(ordered_calls), 'total_exit_2_count': len(exit2_calls), 'pre_fix': len(pre_fix_exit2_calls), 'post_fix': len(post_fix_exit2_calls), 'pre_fix_count': len(pre_fix_exit2_calls), 'post_fix_count': len(post_fix_exit2_calls), 'failing_command_samples': samples}
            if len(post_fix_exit2_calls) > 0:
                rigid_roadblocks.append({**wm_base, 'exit_2_count': len(post_fix_exit2_calls), 'exit_2_sessions': len(post_fix_exit2_sids), 'arg_retry_sessions': exit2_arg_only_sessions, 'watermark_status': 'active_or_regression' if len(pre_fix_exit2_calls) > 0 else 'active_post_fix', 'classification': 'rigid_roadblock', 'evolution_action': 'Add CLI flag aliases / positional path fallback and print 3-line usage hint on exit=2.'})
            else:
                watermarked_resolved_roadblocks.append({**wm_base, 'exit_2_count': 0, 'exit_2_sessions': 0, 'arg_retry_sessions': 0, 'watermark_status': 'resolved_in_current_version', 'classification': 'watermarked_resolved_roadblock', 'evolution_action': f"Watermarked out: {len(pre_fix_exit2_calls)} pre-fix exit=2 error(s) resolved in {current_skill_ver or 'current version'} (post_fix=0)."})
        is_latency_sink = max_ms is not None and max_ms >= LATENCY_SINK_MAX_MS or (p50_ms is not None and p50_ms >= LATENCY_SINK_P50_MS and (len(measured_durs) >= LATENCY_SINK_MIN_CALLS_FOR_P50))
        if is_latency_sink:
            speed_diag = _multi_angle_speed_diagnosis(sc_rel, ordered_calls)
            sink_entry: dict[str, Any] = {'skill': sk_name, 'script': sc_rel, 'calls': len(ordered_calls), 'measured_calls': len(measured_durs), 'classification': 'latency_sink', **speed_diag}
            if sk_name == 'repo-gate' and 'repo-kit' in inventory:
                sink_entry['owner_skill'] = 'repo-kit'
            latency_sinks.append(sink_entry)
    internal_reality_by_skill: dict[str, dict[str, Any]] = {}
    eval_quality_by_skill: dict[str, dict[str, Any]] = {}
    for sk_name, meta in sorted(inventory.items()):
        pkg_p = Path(str(meta.get('path') or s_root / sk_name))
        ir = meta.get('internal_reality') if isinstance(meta.get('internal_reality'), Mapping) else internal_reality_check(pkg_p, skills_root=s_root)
        internal_reality_by_skill[sk_name] = dict(ir)
        esc_map = wh_meta['skill_escapes'].get(sk_name, {'impl_bug': 0, 'spec_gap': 0})
        sk_escapes = int(esc_map.get('impl_bug') or 0) + int(esc_map.get('spec_gap') or 0)
        unres_v_fails = int((wasted_preread_by_skill.get(sk_name) or {}).get('unresolved_validator_failures') or 0)
        sk_dom_fails = unres_v_fails + sk_escapes
        ev_cnt = int(meta.get('eval_case_count') or 0)
        ev_counter_cnt = int(meta.get('eval_counter_case_count') or 0)
        ev_counter_ratio = float(meta.get('eval_counter_case_ratio') or (round(ev_counter_cnt / ev_cnt, 4) if ev_cnt > 0 else 0.0))
        ev_weekly_delta = int(meta.get('eval_case_weekly_delta') or 0)
        ev_escape_gap = bool(sk_dom_fails + sk_escapes >= 3 and ev_weekly_delta == 0)
        soft_adv_list = list(ir.get('soft_quality_advisories') or meta.get('soft_quality_advisories') or [])
        prose_adv_list = list(ir.get('prose_only_test_advisories') or meta.get('prose_only_test_advisories') or [])
        eval_quality_by_skill[sk_name] = {'skill': sk_name, 'eval_case_count': ev_cnt, 'eval_counter_case_count': ev_counter_cnt, 'eval_counter_case_ratio': ev_counter_ratio, 'eval_case_weekly_delta': ev_weekly_delta, 'domain_failures': sk_dom_fails, 'skill_escapes': sk_escapes, 'eval_escape_gap': ev_escape_gap, 'vacuous_good_snippet_count': int(ir.get('vacuous_good_snippet_count') or 0), 'prose_only_test_advisory_count': len(prose_adv_list), 'prose_only_test_advisories': prose_adv_list}
    soft_quality_by_skill: dict[str, dict[str, Any]] = {}
    soft_totals = {'soft_quality_advisories_count': 0, 'malformed_code_fence_count': 0, 'ast_namespace_pollution_count': 0, 'boilerplate_table_decay_count': 0, 'vacuous_good_snippet_count': 0, 'prose_only_test_advisory_count': 0}
    for sk_name, ir_item in sorted(internal_reality_by_skill.items()):
        s_advs = list(ir_item.get('soft_quality_advisories') or [])
        p_advs = list(ir_item.get('prose_only_test_advisories') or [])
        entry_counts = {
            'soft_quality_advisories_count': int(ir_item.get('soft_quality_advisories_count') if 'soft_quality_advisories_count' in ir_item else len(s_advs)),
            'malformed_code_fence_count': int(ir_item.get('malformed_code_fence_count') or 0),
            'ast_namespace_pollution_count': int(ir_item.get('ast_namespace_pollution_count') or 0),
            'boilerplate_table_decay_count': int(ir_item.get('boilerplate_table_decay_count') or 0),
            'vacuous_good_snippet_count': int(ir_item.get('vacuous_good_snippet_count') or 0),
            'prose_only_test_advisory_count': int(ir_item.get('prose_only_test_advisory_count') if 'prose_only_test_advisory_count' in ir_item else len(p_advs)),
        }
        for t_key, t_val in entry_counts.items():
            soft_totals[t_key] += t_val
        soft_quality_by_skill[sk_name] = {**entry_counts, 'soft_quality_advisories': s_advs, 'prose_only_test_advisories': p_advs}
    soft_quality_summary = {'tier': 'soft_advisory_exit_0', **soft_totals, 'by_skill': soft_quality_by_skill}
    layer2 = {'true_catchers': sorted(true_catchers, key=lambda r: (-r['fail_to_pass_sessions'], -r['calls'], r['script'])), 'ceremonial_rubber_stamps': sorted(ceremonial_rubber_stamps, key=lambda r: (-r['co_occurring_skill_escapes'], -r['calls'], r['script'])), 'rigid_roadblocks': sorted(rigid_roadblocks, key=lambda r: (-r['exit_2_count'], -r['calls'], r['script'])), 'watermarked_resolved_roadblocks': sorted(watermarked_resolved_roadblocks, key=lambda r: (-r['pre_fix'], -r['calls'], r['script'])), 'latency_sinks': sorted(latency_sinks, key=lambda r: (-(r['max_ms'] or 0.0), -(r['p50_ms'] or 0.0), r['script'])), 'aci_output_bloat': sorted(aci_output_bloat, key=lambda r: (-r['bloat_call_count'], r['script'])), 'aci_output_bloat_count': sum(aci_output_bloat_by_skill.values()), 'aci_output_bloat_by_skill': aci_output_bloat_by_skill, 'internal_reality_by_skill': internal_reality_by_skill, 'soft_quality_advisories': soft_quality_summary, 'cross_repo_gate_health': cross_repo_gate_health}
    wild_buckets: dict[str, dict[str, Any]] = {}
    for o in all_sessions:
        for cmd_str, _, _, _ in o.bash_commands:
            extracted = _extract_wild_manual_signature(cmd_str, skills_root=s_root, dev_root=d_root)
            if extracted is None:
                continue
            sig, redacted_example = extracted
            bucket = wild_buckets.setdefault(sig, {'pattern_signature': sig, 'count': 0, 'sessions': set(), 'skills_context': set(), 'examples': []})
            bucket['count'] += 1
            bucket['sessions'].add(o.session_id)
            bucket['skills_context'].update(o.skills_loaded)
            if redacted_example not in bucket['examples'] and len(bucket['examples']) < 3:
                bucket['examples'].append(redacted_example)
    wild_patterns: list[dict[str, Any]] = []
    for sig, b in sorted(wild_buckets.items(), key=lambda pair: (-pair[1]['count'], pair[0])):
        if b['count'] < WILD_PATTERN_MIN_COUNT:
            continue
        target_skill = norm_filter if norm_filter and norm_filter in b['skills_context'] else sorted(b['skills_context'])[0] if b['skills_context'] else norm_filter or 'retro-loop'
        if norm_filter and b['skills_context'] and (norm_filter not in b['skills_context']):
            continue
        wild_patterns.append({'pattern_signature': sig, 'occurrences': b['count'], 'session_count': len(b['sessions']), 'candidate_skill': target_skill, 'examples': b['examples'], 'crystallization_policy': {'data_only_helper': True, 'self_disclosing_error': True, 'gate_tier': 'soft_advisory_exit_0'}})
    taste_eval = _harvest_cross_session_taste_and_eval(handoff_root or d_root / 'handoff')
    slow_flywheel_ready = total_sessions >= SLOW_FLYWHEEL_COHORT_MIN_SESSIONS
    layer3 = {'wild_patterns': wild_patterns[:10], 'taste_and_eval_diversity': taste_eval, 'eval_quality_by_skill': eval_quality_by_skill, 'dual_flywheel_guard': {'observed_sessions': total_sessions, 'slow_flywheel_min_sessions': SLOW_FLYWHEEL_COHORT_MIN_SESSIONS, 'slow_flywheel_ready': slow_flywheel_ready, 'active_mode': 'slow_flywheel_batch_ready' if slow_flywheel_ready else 'fast_flywheel_scope_triaged', 'fast_flywheel_rules': ['objective_bug_or_cli_exit2 -> fix global script/alias with Dual-Case Replay', 'scenario_specific_need -> add scenario-conditioned branch (never mutate global default)', 'ambiguous_single_occurrence -> hold in observation_pool until >=3 occurrences']}}
    candidates: list[dict[str, Any]] = []
    observation_pool: list[dict[str, Any]] = []

    def _register_candidate(item: dict[str, Any], triage: dict[str, Any]) -> None:
        sk_cand = str(item.get('skill') or '').strip()
        sk_ver_cand = (inventory.get(sk_cand) or {}).get('skill_version') if sk_cand else None
        enriched = {**item, 'skill_version': sk_ver_cand, 'scope': 'personal', 'window_cst': f'{days}d', 'window_start': window_start.isoformat(), 'window_end': window_end.isoformat(), 'source_as_of': source_as_of, 'sample_size': total_sessions, 'coverage_status': coverage_status, 'scope_triage': triage}
        if triage['emit_proposal']:
            candidates.append(enriched)
        else:
            observation_pool.append(enriched)
    for rb in layer2['rigid_roadblocks'][:3]:
        sk = rb['skill']
        sc_name = Path(rb['script']).name
        sample_cmd = rb['failing_command_samples'][0] if rb['failing_command_samples'] else rb['script']
        triage = _triage_proposal_scope(occurrence_sessions=rb['exit_2_sessions'], total_sessions=total_sessions, is_objective_bug_or_friction=True, domain_scoped=False)
        _register_candidate({'id': f"skill-retro-l2-roadblock-{sk}-{sc_name.replace('.', '-')}", 'ref': f'retro://skill-retro/l2-roadblock/{sk}/{sc_name}', 'metric_id': f'skill-retro-l2-roadblock-{sk}', 'layer': 'L2', 'category': 'rigid_roadblock', 'skill': sk, 'priority': 'P1' if rb['exit_2_count'] >= 2 else 'P2', 'title': f"[Skill Retro L2] 柔化 `{sk}/{sc_name}` 参数路障（`exit=2` 触发 {rb['exit_2_count']} 次）", 'detail': f"layer=L2 | category=rigid_roadblock | skill={sk} | script={rb['script']} | exit_2_count={rb['exit_2_count']} | sessions={rb['exit_2_sessions']} | sample_cmd=`{sample_cmd}` | action={rb['evolution_action']}", 'action': f"Add compatible CLI flag aliases / positional defaults to `skills/{sk}/{rb['script']}` and add Dual-Case Replay test.", 'plain_reason': f"`{sk}` 的脚本 `{sc_name}` 发生 {rb['exit_2_count']} 次参数报错（exit=2），需补充兼容参数别名与报错自引导以消除空转重试"}, triage)
    seen_unwired_skills: set[str] = set()
    for uw in layer1['unwired_disconnected_drawers']:
        sk = uw['skill']
        if sk in seen_unwired_skills:
            continue
        seen_unwired_skills.add(sk)
        triage = _triage_proposal_scope(occurrence_sessions=uw['domain_failure_count'], total_sessions=total_sessions, is_objective_bug_or_friction=True, domain_scoped=True)
        _register_candidate({'id': f"skill-retro-l1-unwired-{sk}-{Path(uw['drawer']).stem}", 'ref': f"retro://skill-retro/l1-unwired/{sk}/{Path(uw['drawer']).name}", 'metric_id': f'skill-retro-l1-unwired-{sk}', 'layer': 'L1', 'category': 'unwired_disconnected_drawer', 'skill': sk, 'priority': 'P1' if uw['domain_failure_count'] >= 2 else 'P2', 'title': f"[Skill Retro L1] 打通 `{sk}` 断连纠偏抽屉 `{Path(uw['drawer']).name}`（报错 {uw['domain_failure_count']} 次但 0 读取）", 'detail': f"layer=L1 | category=unwired_disconnected_drawer | skill={sk} | drawer={uw['drawer']} | domain_failures={uw['domain_failure_count']} | hits=0 | scenario_sessions={uw['scenario_sessions']}", 'action': uw['evolution_action'], 'plain_reason': f"`{sk}` 在 {uw['domain_failure_count']} 次失败或漏检中均未触发 `{Path(uw['drawer']).name}` 读取，需在脚本报错输出中直链该参考抽屉"}, triage)
        if len(seen_unwired_skills) >= 2:
            break
    for ls in layer2['latency_sinks'][:2]:
        raw_sk = ls['skill']
        sk = 'repo-kit' if raw_sk == 'repo-gate' and 'repo-kit' in inventory else raw_sk
        sc_name = Path(ls['script']).name
        bottleneck = ls['primary_bottleneck']
        is_actionable_speed = bottleneck != 'external_wait' or ls['nested_rerun_calls'] >= 1
        triage = _triage_proposal_scope(occurrence_sessions=max(1, ls['measured_calls']), total_sessions=total_sessions, is_objective_bug_or_friction=is_actionable_speed, domain_scoped=True)
        if not is_actionable_speed:
            triage['emit_proposal'] = False
            triage['scope_verdict'] = 'external_wait_observed'
        max_sec = round((ls['max_ms'] or 0.0) / 1000.0, 1)
        p50_sec = round((ls['p50_ms'] or 0.0) / 1000.0, 1)
        _register_candidate({'id': f"skill-retro-l2-latency-{sk}-{sc_name.replace('.', '-')}", 'ref': f'retro://skill-retro/l2-latency/{sk}/{sc_name}', 'metric_id': f'skill-retro-l2-latency-{sk}', 'layer': 'L2', 'category': 'latency_sink', 'skill': sk, 'priority': 'P2', 'title': f'[Skill Retro L2] 多角度提速 `{sc_name}`（max={max_sec}s, P50={p50_sec}s, 主因={bottleneck}）', 'detail': f"layer=L2 | category=latency_sink | skill={sk} | script={ls['script']} | max_ms={ls['max_ms']} | p50_ms={ls['p50_ms']} | bottleneck={bottleneck} | nested_reruns={ls['nested_rerun_calls']} | angles={'; '.join(ls['attribution_angles'])}", 'action': 'Apply 4-angle speedup (path-scoped diff selection, content-hash watermark, parallel pool, or async tiering).', 'plain_reason': f'`{sc_name}` 峰值耗时 {max_sec}s（中位 {p50_sec}s，主因：{bottleneck}），建议按变更域裁剪与并发池做分层提速'}, triage)
    for cr_prop in (cross_repo_gate_health.get('actionable_proposals') or [])[:3]:
        if not isinstance(cr_prop, Mapping):
            continue
        affected_cnt = max(1, int(cr_prop.get('affected_repos_count') or 1))
        triage = _triage_proposal_scope(occurrence_sessions=max(2, affected_cnt), total_sessions=total_sessions, is_objective_bug_or_friction=True, domain_scoped=True)
        _register_candidate(dict(cr_prop), triage)
    for tm in layer0['trigger_misses'][:2]:
        sk = tm['skill']
        triage = _triage_proposal_scope(occurrence_sessions=tm['missed_sessions'], total_sessions=total_sessions, is_objective_bug_or_friction=False, domain_scoped=True)
        _register_candidate({'id': f'skill-retro-l0-trigger-miss-{sk}', 'ref': f'retro://skill-retro/l0-trigger-miss/{sk}', 'metric_id': f'skill-retro-l0-trigger-{sk}', 'layer': 'L0', 'category': 'trigger_miss', 'skill': sk, 'priority': 'P2', 'title': f"[Skill Retro L0] 收紧 `{sk}` 触发边界（{tm['missed_sessions']}/{tm['domain_sessions']} 次触碰领域文件未挂载技能）", 'detail': f"layer=L0 | category=trigger_miss | skill={sk} | missed_sessions={tm['missed_sessions']} | domain_sessions={tm['domain_sessions']} | miss_rate={tm['miss_rate']}", 'action': f'Refine `skills/{sk}/SKILL.md` frontmatter trigger description or pre-edit hook routing.', 'plain_reason': f"检测到 {tm['missed_sessions']} 次直接修改 `{sk}` 领域文件却未加载技能契约，需补齐触发词路由"}, triage)
    for dw in layer1['true_zero_hit_deadwood'][:2]:
        sk = dw['skill']
        triage = _triage_proposal_scope(occurrence_sessions=dw['scenario_sessions'], total_sessions=total_sessions, is_objective_bug_or_friction=False, domain_scoped=slow_flywheel_ready or dw['scenario_sessions'] >= 5)
        if not (slow_flywheel_ready or dw['scenario_sessions'] >= 5):
            triage['emit_proposal'] = False
        _register_candidate({'id': f"skill-retro-l1-deadwood-{sk}-{Path(dw['drawer']).stem}", 'ref': f"retro://skill-retro/l1-deadwood/{sk}/{Path(dw['drawer']).name}", 'metric_id': f'skill-retro-l1-deadwood-{sk}', 'layer': 'L1', 'category': 'true_zero_hit_deadwood', 'skill': sk, 'priority': 'P3', 'title': f"[Skill Retro L1] 清理或合并真僵尸抽屉 `{sk}/{dw['drawer']}`（场景发生 {dw['scenario_sessions']} 次仍 0 命中）", 'detail': f"layer=L1 | category=true_zero_hit_deadwood | skill={sk} | drawer={dw['drawer']} | scenario_sessions={dw['scenario_sessions']} | hits=0 | tokens_est={dw['tokens_est']}", 'action': dw['evolution_action'], 'plain_reason': f"`{sk}` 业务场景已发生 {dw['scenario_sessions']} 次但 `{Path(dw['drawer']).name}` 始终 0 读取，可归档或精简合并"}, triage)
    for cs in layer2['ceremonial_rubber_stamps']:
        if cs['co_occurring_skill_escapes'] <= 0:
            continue
        sk = cs['skill']
        sc_name = Path(cs['script']).name
        triage = _triage_proposal_scope(occurrence_sessions=cs['co_occurring_skill_escapes'] + 1, total_sessions=total_sessions, is_objective_bug_or_friction=True, domain_scoped=True)
        _register_candidate({'id': f"skill-retro-l2-rubberstamp-{sk}-{sc_name.replace('.', '-')}", 'ref': f'retro://skill-retro/l2-rubberstamp/{sk}/{sc_name}', 'metric_id': f'skill-retro-l2-rubberstamp-{sk}', 'layer': 'L2', 'category': 'ceremonial_rubber_stamp', 'skill': sk, 'priority': 'P2', 'title': f"[Skill Retro L2] 升级陪跑门禁 `{sk}/{sc_name}`（{cs['calls']} 次全绿但同技能漏检 {cs['co_occurring_skill_escapes']} 次）", 'detail': f"layer=L2 | category=ceremonial_rubber_stamp | skill={sk} | script={cs['script']} | calls={cs['calls']} | exit_0_rate=1.0 | co_occurring_escapes={cs['co_occurring_skill_escapes']}", 'action': cs['evolution_action'], 'plain_reason': f"`{sc_name}` 连续 {cs['calls']} 次运行全绿，但 `{sk}` 仍发生 {cs['co_occurring_skill_escapes']} 次审查打回，需加深校验断言"}, triage)
        break
    for wp in layer3['wild_patterns'][:2]:
        sk = wp['candidate_skill']
        sig_hash = hashlib.sha256(wp['pattern_signature'].encode('utf-8')).hexdigest()[:8]
        triage = _triage_proposal_scope(occurrence_sessions=max(2, wp['session_count']), total_sessions=total_sessions, is_objective_bug_or_friction=False, domain_scoped=True)
        _register_candidate({'id': f'skill-retro-l3-wild-{sk}-{sig_hash}', 'ref': f'retro://skill-retro/l3-wild/{sk}/{sig_hash}', 'metric_id': f'skill-retro-l3-wild-{sk}', 'layer': 'L3', 'category': 'wild_pattern_crystallization', 'skill': sk, 'priority': 'P3', 'title': f"[Skill Retro L3] 柔性沉淀高频手工探针 `{wp['pattern_signature'][:42]}`（重复 {wp['occurrences']} 次）", 'detail': f"layer=L3 | category=wild_pattern_crystallization | candidate_skill={sk} | occurrences={wp['occurrences']} | sessions={wp['session_count']} | pattern=`{wp['pattern_signature']}` | example=`{(wp['examples'][0] if wp['examples'] else '')}`", 'action': f'Crystallize read-only fact collector subcommand under `skills/{sk}/scripts/` (data-only, self-disclosing error, soft exit=0 advisory).', 'plain_reason': f"跨 {wp['session_count']} 个会话重复手敲 `{wp['pattern_signature'][:28]}` 共 {wp['occurrences']} 次，适合沉淀为只采数不代拍板的柔性脚本"}, triage)
    priority_rank = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
    if norm_filter:
        candidates = [c for c in candidates if c.get('skill') in {norm_filter, 'repo-gate'}]
        observation_pool = [c for c in observation_pool if c.get('skill') in {norm_filter, 'repo-gate'}]
    proposals = sorted(candidates, key=lambda item: (priority_rank.get(str(item.get('priority')), 9), str(item.get('id') or '')))[:6]
    skill_specific_proposals = [p for p in proposals if p.get('skill') == norm_filter] if norm_filter else proposals
    auto_proposed = queue_fn(proposals, prop_path) if auto_propose and queue_fn is not None else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0
    triad_summary = evaluate_skill_uptake(skills=[norm_filter] if norm_filter else sorted(inventory.keys()), triggered_skills=[norm_filter] if norm_filter else sorted({sk for o in all_sessions for sk in o.skills_loaded}), target_dir=s_root.parent if (s_root / 'skill-retro').is_dir() else s_root, scenario='skills_available_unmentioned')
    result_payload: dict[str, Any] = {'ok': True, 'schema': 'skill-retro-diagnostic/v1', 'query_id': 'retro/skill_progressive_retro', 'status': 'ACTIONABLE' if skill_specific_proposals else 'HEALTHY_NO_ISSUES', 'status_message': f'{len(skill_specific_proposals)} evidence-backed proposal(s) found' if skill_specific_proposals else 'healthy / 0 issues found (no changes needed)', 'skill_filter': norm_filter or None, 'mode': norm_mode, 'aspects': selected_aspects, 'generated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'), 'window_cst': f'{days}d', 'window_start': window_start.isoformat(), 'window_end': window_end.isoformat(), 'source_as_of': source_as_of, 'sample_size': total_sessions, 'coverage_status': coverage_status, 'sources_ingested': {'sessions_observed': total_sessions, 'trace_spans_ingested': ingested_trace_spans, 'skills_in_inventory': len(inventory)}, 'summary': {'trigger_miss_skills': len(layer0['trigger_misses']), 'zero_action_load_skills': len(layer0['zero_action_loads']), 'healthy_r1_r2_convergences': layer0['healthy_convergence_count'], 'directional_drifts': layer0['directional_drift_count'], 'hot_drawers_count': len(layer1['hot_drawers']), 'on_demand_drawers_count': len(layer1['on_demand_correction_drawers']), 'unwired_drawers_count': len(layer1['unwired_disconnected_drawers']), 'true_deadwood_drawers_count': len(layer1['true_zero_hit_deadwood']), 'true_catcher_scripts_count': len(layer2['true_catchers']), 'ceremonial_scripts_count': len(layer2['ceremonial_rubber_stamps']), 'rigid_roadblock_scripts_count': len(layer2['rigid_roadblocks']), 'latency_sink_scripts_count': len(layer2['latency_sinks']), 'cross_repo_gate_findings_count': int(cross_repo_gate_health.get('total_findings') or 0), 'wild_patterns_count': len(layer3['wild_patterns']), 'high_signal_proposals_count': len(proposals), 'observation_pool_count': len(observation_pool), 'slow_flywheel_ready': slow_flywheel_ready, **soft_totals}, 'soft_quality_advisories': soft_quality_summary, 'cross_repo_gate_health': cross_repo_gate_health, 'layer0_direction_and_triggers': layer0, 'layer1_drawer_metabolism': layer1, 'layer2_script_efficacy_and_speed': layer2, 'layer3_wild_patterns_and_eval': layer3, 'problem_discovery_dimensions': {'dim1_l0_direction_vs_convergence': layer0, 'dim2_l1_scenario_drawer_metabolism': layer1, 'dim3_l2_script_efficacy_and_4angle_speed': layer2, 'dim4_l3_wild_soft_crystallization': {'wild_patterns': layer3['wild_patterns'], 'principles': ['facts_only', 'self_disclosing_error_pointer', 'hard_lock_vs_soft_advisory']}, 'dim5_cross_session_creative_spectrum': taste_eval, 'dim6_three_tier_eval_and_flywheel_guard': {'eval_tiers': ['L1_baseline_eval', 'L2_diversity_intent_eval', 'L3_human_taste_eval'], 'dual_flywheel_guard': layer3['dual_flywheel_guard'], 'soft_quality_advisories': soft_quality_summary}, 'triad_trigger_uptake_outcome': triad_summary}, 'context_uptake_triad': triad_summary, 'proposals': proposals, 'observation_pool': observation_pool[:8], 'auto_proposed': auto_proposed, 'proposals_written': proposals_written, 'proposals_skipped': proposals_skipped, 'inventory_snapshot': {name: {'script_entries': list(meta.get('script_entries') or []), 'internal_doc_cli_drift': list(meta.get('internal_doc_cli_drift') or []), 'internal_broken_refs': list(meta.get('internal_broken_refs') or []), 'soft_quality_advisories': list(meta.get('soft_quality_advisories') or [])} for name, meta in inventory.items()}}
    if emit_briefs_dir is not None:
        result_payload['emitted_briefs'] = emit_skill_retro_briefs(result_payload, emit_briefs_dir, skill_filter=norm_filter or None, mode=norm_mode, aspects=selected_aspects, coupled_with=coupled_with, relays=relays)
    return result_payload
