from __future__ import annotations
from .common import *

"""Triage Jev implementation for the skill-retro engine."""

def probe_skills(*, skill: str | None=None, aspect: str | None=None, fast: bool=True, summary: bool=False, warehouse_db: Path | None=None, copilot_db: Path | None=None, copilot_sessions_dir: Path | None=None, usage_jsonl: Path | None=None, trace_export_path: Path | None=None, handoff_root: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, telemetry_shards_dir: Path | None=None, days: int=30) -> dict[str, Any]:
    """Stage 1: Sub-second wide or single-skill probe (`probe [--skill <name>] [--aspect <dim>] --fast`)."""
    t0 = time.monotonic()
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    norm_sk = _norm_skill(skill) if skill else None
    entry_mode = 'direct_single_skill' if norm_sk else 'global_sweep'
    aspect_filter = _normalize_aspects(aspect, mode='partial' if aspect else 'full') if aspect else list(CANONICAL_ASPECTS)
    full_inv = _discover_skill_inventory(s_root, d_root, skill_filter=skill)
    known_all = set(full_inv.keys())
    leases = discover_active_skill_leases(dev_root=d_root, handoff_root=handoff_root, known_skills=known_all)
    if norm_sk:
        inv = {k: v for k, v in full_inv.items() if _norm_skill(k) == norm_sk}
    else:
        inv = full_inv
    explicit_source_override = any((val is not None for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)))
    has_small_custom_sources = explicit_source_override
    skills_signals: list[dict[str, Any]] = []
    cross_repo_gate_health: dict[str, Any] | None = None
    if norm_sk == 'repo-kit' or (norm_sk is None and 'repo-kit' in inv):
        cross_repo_gate_health = _compute_cross_repo_gate_health(skills_root=s_root, dev_root=d_root)
    if has_small_custom_sources or not fast:
        diag = compute_skill_retro(warehouse_db=warehouse_db, copilot_db=copilot_db, copilot_sessions_dir=copilot_sessions_dir, usage_jsonl=usage_jsonl, trace_export_path=trace_export_path, handoff_root=handoff_root, skills_root=s_root, dev_root=d_root, telemetry_shards_dir=telemetry_shards_dir, skill=norm_sk, days=days, mirror_roi_snapshot=False)
        if diag.get('cross_repo_gate_health'):
            cross_repo_gate_health = diag['cross_repo_gate_health']
        l0 = diag.get('layer0_direction_and_triggers') or {}
        l1 = diag.get('layer1_drawer_metabolism') or {}
        l2 = diag.get('layer2_script_efficacy_and_speed') or {}
        l3 = diag.get('layer3_wild_patterns_and_eval') or {}
        taste = l3.get('taste_and_eval_diversity') or {}
        prog_by_sk = l1.get('progressive_catch_by_skill') or {}
        wasted_by_sk = l1.get('wasted_preread_by_skill') or {}
        bloat_by_sk = l2.get('aci_output_bloat_by_skill') or {}
        ir_by_sk = l2.get('internal_reality_by_skill') or {}
        ev_qual_by_sk = l3.get('eval_quality_by_skill') or {}
        fast_wh = _fast_telemetry_signals(inv=inv, warehouse_db=warehouse_db, usage_jsonl=usage_jsonl, days=days)
        for sk_name, meta in sorted(inv.items()):
            t_wh = fast_wh.get(sk_name) or {}
            trig_misses = sum((int(x.get('missed_sessions') or 0) for x in l0.get('trigger_misses') or [] if x.get('skill') == sk_name))
            empty_loads = sum((int(x.get('zero_action_sessions') or 0) for x in l0.get('zero_action_loads') or [] if x.get('skill') == sk_name))
            drifts = sum((1 for x in l0.get('directional_drifts') or [] if sk_name in (x.get('skills') or [])))
            r1_r2_conv = sum((1 for x in l0.get('healthy_r1_to_r2_convergences') or [] if sk_name in (x.get('skills') or [])))
            unwired = sum((1 for x in l1.get('unwired_disconnected_drawers') or [] if x.get('skill') == sk_name))
            hot_cnt = sum((1 for x in l1.get('hot_drawers') or [] if x.get('skill') == sk_name))
            deadwood = sum((1 for x in l1.get('true_zero_hit_deadwood') or [] if x.get('skill') == sk_name))
            prog_catches = int(prog_by_sk.get(sk_name) or 0)
            wasted_info = wasted_by_sk.get(sk_name) or {}
            wasted_rate = float(wasted_info.get('wasted_preread_rate') or 0.0)
            wasted_sessions = int(wasted_info.get('wasted_preread_sessions') or 0)
            exit2_cnt = sum((int(x.get('exit_2_count') or 0) for x in l2.get('rigid_roadblocks') or [] if x.get('skill') == sk_name))
            handoff_drop_cnt = sum((1 for x in l2.get('handoff_findings') or [] if x.get('upstream_skill') == sk_name and x.get('code') == 'HANDOFF_INSTRUCTION_DROP'))
            router_trap_cnt = sum((1 for x in l2.get('handoff_findings') or [] if x.get('upstream_skill') == sk_name and x.get('code') == 'M2_ROUTER_PARAM_TRAP'))
            exit2_pre_cnt = sum((int(x.get('pre_fix') or 0) for x in (l2.get('rigid_roadblocks') or []) + (l2.get('watermarked_resolved_roadblocks') or []) if x.get('skill') == sk_name))
            rubber_items = [x for x in l2.get('ceremonial_rubber_stamps') or [] if x.get('skill') == sk_name and int(x.get('co_occurring_skill_escapes') or 0) > 0]
            rubber_cnt = len(rubber_items)
            rubber_escapes = max([int(x.get('co_occurring_skill_escapes') or 0) for x in rubber_items], default=int(t_wh.get('rubber_stamp_escapes') or 0))
            slow_cnt = sum((1 for x in l2.get('latency_sinks') or [] if (x.get('skill') == sk_name or (sk_name == 'repo-kit' and x.get('skill') == 'repo-gate')) and x.get('primary_bottleneck') != 'external_wait'))
            bloat_cnt = int(bloat_by_sk.get(sk_name) or 0)
            ir_info = ir_by_sk.get(sk_name) or meta.get('internal_reality') or {}
            cli_drift_list = list(ir_info.get('internal_doc_cli_drift') or [])
            broken_refs_list = list(ir_info.get('internal_broken_refs') or [])
            cli_drift_cnt = len(cli_drift_list)
            broken_refs_cnt = len(broken_refs_list)
            soft_advs = list(ir_info.get('soft_quality_advisories') or meta.get('soft_quality_advisories') or [])
            prose_advs = list(ir_info.get('prose_only_test_advisories') or meta.get('prose_only_test_advisories') or [])
            soft_counts = {
                'soft_quality_advisories_count': int(ir_info.get('soft_quality_advisories_count') if 'soft_quality_advisories_count' in ir_info else len(soft_advs)),
                'malformed_code_fence_count': int(ir_info.get('malformed_code_fence_count') or meta.get('malformed_code_fence_count') or 0),
                'ast_namespace_pollution_count': int(ir_info.get('ast_namespace_pollution_count') or meta.get('ast_namespace_pollution_count') or 0),
                'boilerplate_table_decay_count': int(ir_info.get('boilerplate_table_decay_count') or meta.get('boilerplate_table_decay_count') or 0),
                'vacuous_good_snippet_count': int(ir_info.get('vacuous_good_snippet_count') or meta.get('vacuous_good_snippet_count') or 0),
                'prose_only_test_advisory_count': int(ir_info.get('prose_only_test_advisory_count') if 'prose_only_test_advisory_count' in ir_info else len(prose_advs)),
                'soft_quality_advisories': soft_advs[:5] if summary else soft_advs,
                'prose_only_test_advisories': prose_advs[:3] if summary else prose_advs,
            }
            ev_q = ev_qual_by_sk.get(sk_name) or {}
            ev_counter_cnt = int(ev_q.get('eval_counter_case_count') if 'eval_counter_case_count' in ev_q else meta.get('eval_counter_case_count') or 0)
            ev_counter_ratio = float(ev_q.get('eval_counter_case_ratio') if 'eval_counter_case_ratio' in ev_q else meta.get('eval_counter_case_ratio') or 0.0)
            ev_escape_gap = bool(ev_q.get('eval_escape_gap', False))
            sk_escapes = int(ev_q.get('skill_escapes') or t_wh.get('skill_escapes') or 0)
            dom_fails = int(ev_q.get('domain_failures') or t_wh.get('domain_failures') or 0)
            cr_findings_cnt = int((cross_repo_gate_health or {}).get('total_findings') or 0) if sk_name == 'repo-kit' else 0
            wild_cnt = sum((int(x.get('occurrences') or 0) for x in l3.get('wild_patterns') or [] if x.get('candidate_skill') == sk_name))
            creative_flag = sk_name in {'my-designer', 'design-studio'} and bool(taste.get('low_diversity_goals'))
            eval_cases_cnt = int(meta.get('eval_case_count') or 0)
            triggered_aspects: list[str] = []
            if (trig_misses > 0 or empty_loads > 0 or drifts > 0 or (broken_refs_cnt > 0) or (not meta.get('budget_ok', True))) and 'l0' in aspect_filter:
                triggered_aspects.append('l0')
            if (unwired > 0 or hot_cnt > 0 or deadwood > 0 or (wasted_sessions > 0)) and 'l1' in aspect_filter:
                triggered_aspects.append('l1')
            if (exit2_cnt > 0 or handoff_drop_cnt > 0 or router_trap_cnt > 0 or rubber_cnt > 0 or slow_cnt > 0 or (cr_findings_cnt > 0) or (cli_drift_cnt > 0) or (bloat_cnt > 0)) and 'l2' in aspect_filter:
                triggered_aspects.append('l2')
            if wild_cnt > 0 and 'l3' in aspect_filter:
                triggered_aspects.append('l3')
            if creative_flag and 'creative' in aspect_filter:
                triggered_aspects.append('creative')
            if (len(triggered_aspects) >= 2 or ev_escape_gap) and 'eval' in aspect_filter:
                triggered_aspects.append('eval')
            priority_score = exit2_cnt * 3.0 + handoff_drop_cnt * 2.5 + router_trap_cnt * 2.5 + unwired * 2.5 + trig_misses * 2.0 + empty_loads * 1.2 + drifts * 2.0 + wasted_sessions * 1.5 + rubber_cnt * 1.8 + slow_cnt * 1.5 + cli_drift_cnt * 2.5 + broken_refs_cnt * 2.0 + bloat_cnt * 1.8 + (2.0 if ev_escape_gap else 0.0) + (min(6.0, cr_findings_cnt * 0.5) if cr_findings_cnt > 0 else 0.0) + (1.5 if wild_cnt >= WILD_PATTERN_MIN_COUNT else 0.0) + (1.2 if creative_flag else 0.0) + (1.0 if not meta.get('budget_ok', True) else 0.0)
            sig_dict: dict[str, Any] = {'l0_trigger_misses': trig_misses, 'l0_zero_action_loads': empty_loads, 'l0_directional_drifts': drifts, 'healthy_r1_to_r2_convergences': r1_r2_conv, 'progressive_catch_count': prog_catches, 'wasted_preread_sessions': wasted_sessions, 'wasted_preread_rate': wasted_rate, 'l1_unwired_drawers': unwired, 'l1_hot_drawers': hot_cnt, 'l1_true_deadwood': deadwood, 'l2_exit2_roadblocks': exit2_cnt, 'l2_exit2_pre_fix': exit2_pre_cnt, 'l2_exit2_post_fix': exit2_cnt, 'l2_handoff_instruction_drop': handoff_drop_cnt, 'l2_m2_router_param_trap': router_trap_cnt, 'l2_leaky_rubber_stamps': rubber_cnt, 'rubber_stamp_escapes': rubber_escapes, 'l2_latency_sinks': slow_cnt, 'p50_ms': t_wh.get('p50_ms'), 'p90_ms': t_wh.get('p90_ms'), 'aci_output_bloat_count': bloat_cnt, 'internal_doc_cli_drift_count': cli_drift_cnt, 'internal_broken_refs_count': broken_refs_cnt, 'internal_doc_cli_drift': cli_drift_list[:3] if summary else cli_drift_list, 'internal_broken_refs': broken_refs_list[:3] if summary else broken_refs_list, 'l3_wild_occurrences': wild_cnt, 'creative_selection_signal': bool(creative_flag), 'domain_failures': dom_fails, 'skill_escapes': sk_escapes, 'eval_counter_case_count': ev_counter_cnt, 'eval_counter_case_ratio': ev_counter_ratio, 'eval_escape_gap': ev_escape_gap, **soft_counts}
            if sk_name == 'repo-kit':
                sig_dict['cross_repo_gate_findings'] = cr_findings_cnt
            skills_signals.append({'skill': sk_name, 'skill_version': meta.get('skill_version'), 'subtree_sha': meta.get('subtree_sha'), 'plugin_version': meta.get('plugin_version', '1.0.0'), 'last_modified_epoch': meta.get('last_modified_epoch'), 'scope': meta.get('scope', 'global'), 'l0_lines': meta.get('l0_lines', 0), 'max_l1_lines': meta.get('max_l1_lines', 0), 'budget_ok': bool(meta.get('budget_ok', True)), 'eval_case_count': eval_cases_cnt, 'eval_counter_case_count': ev_counter_cnt, 'eval_counter_case_ratio': ev_counter_ratio, 'eval_escape_gap': ev_escape_gap, 'soft_quality_advisories_count': soft_counts['soft_quality_advisories_count'], 'leased': sk_name in leases['leased_skills'], 'lease_reasons': leases['lease_reasons'].get(sk_name, []), 'signals': sig_dict, 'triggered_aspects': triggered_aspects, 'priority_score': round(priority_score, 2)})
    else:
        fast_telem = _fast_telemetry_signals(inv=inv, warehouse_db=warehouse_db, usage_jsonl=usage_jsonl, copilot_sessions_dir=copilot_sessions_dir, days=days)
        for sk_name, meta in sorted(inv.items()):
            pkg_p = Path(str(meta.get('path') or s_root / sk_name))
            t_sig = fast_telem.get(sk_name) or {}
            ref_hits_set = t_sig.get('ref_hit_drawers') or set()
            unwired_zero_hit = 0
            for ref_e in meta.get('reference_entries') or []:
                rel_r = str(ref_e.get('rel_path') or '')
                if rel_r.startswith('references/') and _drawer_linked_in_l0(pkg_p, rel_r) and (not _drawer_linked_in_scripts(pkg_p, rel_r)):
                    if rel_r not in ref_hits_set:
                        unwired_zero_hit += 1
            trig_misses = int(t_sig.get('l0_trigger_misses') or 0)
            empty_loads = int(t_sig.get('l0_zero_action_loads') or 0)
            drifts = int(t_sig.get('l0_directional_drifts') or 0)
            r1_r2_conv = int(t_sig.get('r1_to_r2_converged_count') or 0)
            prog_catches = int(t_sig.get('progressive_catch_count') or 0)
            wasted_sessions = int(t_sig.get('wasted_preread_sessions') or 0)
            wasted_rate = float(t_sig.get('wasted_preread_rate') or 0.0)
            bloat_cnt = int(t_sig.get('aci_output_bloat_count') or 0)
            exit2_cnt = int(t_sig.get('l2_exit2_roadblocks') or 0)
            domain_fails = int(t_sig.get('domain_failures') or 0)
            sk_escapes = int(t_sig.get('skill_escapes') or 0)
            rubber_cnt = int(t_sig.get('l2_leaky_rubber_stamps') or 0)
            rubber_escapes = int(t_sig.get('rubber_stamp_escapes') or 0)
            slow_cnt = int(t_sig.get('l2_latency_sinks') or 0)
            p1_bonus = float(t_sig.get('p1_proposal_bonus') or 0.0)
            unwired_cnt = max(int(t_sig.get('l1_unwired_drawers') or 0), unwired_zero_hit if domain_fails > 0 and meta.get('scripts') else 0)
            ir_info = meta.get('internal_reality') or internal_reality_check(pkg_p, skills_root=s_root)
            cli_drift_list = list(ir_info.get('internal_doc_cli_drift') or [])
            broken_refs_list = list(ir_info.get('internal_broken_refs') or [])
            cli_drift_cnt = len(cli_drift_list)
            broken_refs_cnt = len(broken_refs_list)
            soft_advs = list(ir_info.get('soft_quality_advisories') or meta.get('soft_quality_advisories') or [])
            prose_advs = list(ir_info.get('prose_only_test_advisories') or meta.get('prose_only_test_advisories') or [])
            soft_counts = {
                'soft_quality_advisories_count': int(ir_info.get('soft_quality_advisories_count') if 'soft_quality_advisories_count' in ir_info else len(soft_advs)),
                'malformed_code_fence_count': int(ir_info.get('malformed_code_fence_count') or meta.get('malformed_code_fence_count') or 0),
                'ast_namespace_pollution_count': int(ir_info.get('ast_namespace_pollution_count') or meta.get('ast_namespace_pollution_count') or 0),
                'boilerplate_table_decay_count': int(ir_info.get('boilerplate_table_decay_count') or meta.get('boilerplate_table_decay_count') or 0),
                'vacuous_good_snippet_count': int(ir_info.get('vacuous_good_snippet_count') or meta.get('vacuous_good_snippet_count') or 0),
                'prose_only_test_advisory_count': int(ir_info.get('prose_only_test_advisory_count') if 'prose_only_test_advisory_count' in ir_info else len(prose_advs)),
                'soft_quality_advisories': soft_advs[:5] if summary else soft_advs,
                'prose_only_test_advisories': prose_advs[:3] if summary else prose_advs,
            }
            cr_findings_cnt = int((cross_repo_gate_health or {}).get('total_findings') or 0) if sk_name == 'repo-kit' else 0
            budget_ok = bool(meta.get('budget_ok', True))
            eval_cases_cnt = int(meta.get('eval_case_count') or 0)
            ev_counter_cnt = int(meta.get('eval_counter_case_count') or 0)
            ev_counter_ratio = float(meta.get('eval_counter_case_ratio') or 0.0)
            ev_weekly_delta = int(meta.get('eval_case_weekly_delta') or 0)
            ev_escape_gap = bool(domain_fails + sk_escapes >= 3 and ev_weekly_delta == 0)
            creative_flag = False
            triggered_aspects = []
            if (trig_misses > 0 or empty_loads > 0 or drifts > 0 or (broken_refs_cnt > 0) or (not budget_ok)) and 'l0' in aspect_filter:
                triggered_aspects.append('l0')
            if (unwired_cnt > 0 and meta.get('scripts') or wasted_sessions > 0) and 'l1' in aspect_filter:
                triggered_aspects.append('l1')
            if (exit2_cnt > 0 or rubber_cnt > 0 or slow_cnt > 0 or (cli_drift_cnt > 0) or (bloat_cnt > 0) or (cr_findings_cnt > 0) or (meta.get('scripts') and unwired_cnt > 0)) and 'l2' in aspect_filter:
                triggered_aspects.append('l2')
            if creative_flag and 'creative' in aspect_filter:
                triggered_aspects.append('creative')
            if (eval_cases_cnt == 0 or ev_escape_gap or len(triggered_aspects) >= 2) and 'eval' in aspect_filter:
                triggered_aspects.append('eval')
            score = p1_bonus + exit2_cnt * 3.0 + (unwired_cnt * 2.5 if domain_fails > 0 or p1_bonus > 0 else 0.0) + domain_fails * 1.5 + drifts * 2.0 + wasted_sessions * 1.5 + rubber_cnt * 1.8 + slow_cnt * 1.5 + cli_drift_cnt * 2.5 + broken_refs_cnt * 2.0 + bloat_cnt * 1.8 + (2.0 if ev_escape_gap else 0.0) + trig_misses * 2.0 + empty_loads * 1.2 + (min(6.0, cr_findings_cnt * 0.5) if cr_findings_cnt > 0 else 0.0) + (2.5 if not budget_ok else 0.0) + (1.0 if eval_cases_cnt == 0 else 0.0)
            sig_dict = {'l0_trigger_misses': trig_misses, 'l0_zero_action_loads': empty_loads, 'l0_directional_drifts': drifts, 'healthy_r1_to_r2_convergences': r1_r2_conv, 'progressive_catch_count': prog_catches, 'wasted_preread_sessions': wasted_sessions, 'wasted_preread_rate': wasted_rate, 'l1_unwired_drawers': unwired_cnt if meta.get('scripts') else 0, 'l1_hot_drawers': int(t_sig.get('l1_hot_drawers') or 0), 'l1_true_deadwood': int(t_sig.get('l1_true_deadwood') or 0), 'l2_exit2_roadblocks': exit2_cnt, 'l2_exit2_pre_fix': int(t_sig.get('l2_exit2_pre_fix') or 0), 'l2_exit2_post_fix': int(t_sig.get('l2_exit2_post_fix') or exit2_cnt), 'l2_leaky_rubber_stamps': rubber_cnt, 'rubber_stamp_escapes': rubber_escapes, 'l2_latency_sinks': slow_cnt, 'p50_ms': t_sig.get('p50_ms'), 'p90_ms': t_sig.get('p90_ms'), 'aci_output_bloat_count': bloat_cnt, 'internal_doc_cli_drift_count': cli_drift_cnt, 'internal_broken_refs_count': broken_refs_cnt, 'internal_doc_cli_drift': cli_drift_list[:3] if summary else cli_drift_list, 'internal_broken_refs': broken_refs_list[:3] if summary else broken_refs_list, 'l3_wild_occurrences': int(t_sig.get('l3_wild_occurrences') or 0), 'creative_selection_signal': creative_flag, 'domain_failures': domain_fails, 'skill_escapes': sk_escapes, 'eval_counter_case_count': ev_counter_cnt, 'eval_counter_case_ratio': ev_counter_ratio, 'eval_escape_gap': ev_escape_gap, **soft_counts}
            if sk_name == 'repo-kit':
                sig_dict['cross_repo_gate_findings'] = cr_findings_cnt
            skills_signals.append({'skill': sk_name, 'skill_version': meta.get('skill_version'), 'subtree_sha': meta.get('subtree_sha'), 'plugin_version': meta.get('plugin_version', '1.0.0'), 'last_modified_epoch': meta.get('last_modified_epoch'), 'scope': meta.get('scope', 'global'), 'l0_lines': meta.get('l0_lines', 0), 'max_l1_lines': meta.get('max_l1_lines', 0), 'budget_ok': budget_ok, 'eval_case_count': eval_cases_cnt, 'eval_counter_case_count': ev_counter_cnt, 'eval_counter_case_ratio': ev_counter_ratio, 'eval_escape_gap': ev_escape_gap, 'soft_quality_advisories_count': soft_counts['soft_quality_advisories_count'], 'leased': sk_name in leases['leased_skills'], 'lease_reasons': leases['lease_reasons'].get(sk_name, []), 'signals': sig_dict, 'triggered_aspects': triggered_aspects, 'priority_score': round(score, 2)})
    skills_signals.sort(key=lambda item: (-float(item['priority_score']), item['skill']))
    elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
    probe_out: dict[str, Any] = {'ok': True, 'schema': 'skill-retro-probe/v1', 'stage': 'stage1_probe', 'entry_mode': entry_mode, 'skill_filter': norm_sk, 'aspect_filter': aspect_filter, 'fast': bool(fast), 'duration_ms': elapsed_ms, 'skills_probed': len(skills_signals), 'active_leases': leases, 'probe_role_tier': {'role': 'explorer', 'compatible_roles': ['explorer', 'mechanical'], 'tier': 'worker', 'models': ['mai-code-2-flash', 'gpt-5.6-luna']}, 'skills': skills_signals}
    if cross_repo_gate_health is not None:
        probe_out['cross_repo_gate_health'] = cross_repo_gate_health
    return probe_out

def _parse_jev_triage_entry(raw_entry: Any, *, default_aspects: list[str], default_score: float) -> tuple[str | None, list[str] | None, float | None]:
    if isinstance(raw_entry, str):
        choice = raw_entry.strip().lower()
        if choice in {'skip', 'partial', 'full'}:
            return (choice, list(CANONICAL_ASPECTS) if choice == 'full' else default_aspects, 0.85 if choice != 'skip' else 0.1)
    if not isinstance(raw_entry, Mapping):
        return (None, None, None)
    inner = raw_entry.get('answers', raw_entry) if isinstance(raw_entry.get('answers'), Mapping) else raw_entry
    choice_val = inner.get('route_mode', inner.get('mode', inner.get('choice')))
    if isinstance(choice_val, Mapping):
        choice_val = choice_val.get('choice') or choice_val.get('mode')
    choice_str = str(choice_val or '').strip().lower() if choice_val is not None else ''
    noul_val = inner.get('noul', inner.get('score', inner.get('confidence')))
    if isinstance(noul_val, Mapping):
        noul_val = noul_val.get('noul', noul_val.get('score', noul_val.get('confidence')))
    noul_num: float | None = None
    if isinstance(noul_val, (int, float)) and (not isinstance(noul_val, bool)):
        noul_num = float(noul_val)
    raw_asp = inner.get('aspects')
    asp_list = _normalize_aspects(raw_asp, mode=choice_str or 'partial') if raw_asp else None
    if choice_str in {'skip', 'partial', 'full'}:
        if noul_num is not None and noul_num < 0.3:
            return ('skip', [], noul_num)
        if noul_num is not None and 0.3 <= noul_num <= 0.7 and (choice_str != 'skip'):
            return (None, asp_list, noul_num)
        return (choice_str, asp_list, noul_num if noul_num is not None else 0.85 if choice_str != 'skip' else 0.1)
    if noul_num is not None:
        if noul_num > 0.7:
            mode_out = 'full' if len(default_aspects) >= 3 else 'partial'
            return (mode_out, asp_list or default_aspects, noul_num)
        if noul_num < 0.3:
            return ('skip', [], noul_num)
    return (None, asp_list, noul_num)

def triage_skills(*, probe_report: Mapping[str, Any] | None=None, skill: str | None=None, aspect: str | None=None, pick_one: bool=False, pick_top: int | None=None, use_jev: bool=True, mock_jev_json: Any=None, emit_briefs_dir: Path | None=None, coupled_with: Sequence[str] | str | None=None, relays: Sequence[Mapping[str, Any]] | None=None, summary: bool=False, warehouse_db: Path | None=None, copilot_db: Path | None=None, copilot_sessions_dir: Path | None=None, usage_jsonl: Path | None=None, trace_export_path: Path | None=None, handoff_root: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, telemetry_shards_dir: Path | None=None, days: int=30) -> dict[str, Any]:
    """Stage 2: Cheap Worker + Jev fast-thinking triage & Autonomous Daily Batch (--pick-top 3 default) or Pick-1."""
    norm_sk = _norm_skill(skill) if skill else None
    probe = dict(probe_report) if probe_report is not None else probe_skills(skill=norm_sk, aspect=aspect, fast=True, summary=summary, warehouse_db=warehouse_db, copilot_db=copilot_db, copilot_sessions_dir=copilot_sessions_dir, usage_jsonl=usage_jsonl, trace_export_path=trace_export_path, handoff_root=handoff_root, skills_root=skills_root, dev_root=dev_root, telemetry_shards_dir=telemetry_shards_dir, days=days)
    parsed_mock: Any = None
    if mock_jev_json is not None:
        if isinstance(mock_jev_json, str):
            p_cand = Path(mock_jev_json).expanduser()
            parsed_mock = json.loads(p_cand.read_text(encoding='utf-8') if p_cand.is_file() else mock_jev_json)
        else:
            parsed_mock = mock_jev_json
    jev_mod = _load_jev_client() if use_jev or parsed_mock is not None else None
    decisions: list[dict[str, Any]] = []
    excluded_by_lease: list[dict[str, Any]] = []
    if norm_sk:
        effective_top_n = 1
    elif pick_one:
        effective_top_n = 1
    elif pick_top is not None:
        effective_top_n = max(1, int(pick_top))
    else:
        effective_top_n = 3
    live_jev_budget = max(6, effective_top_n * 2) if parsed_mock is None else 999
    live_jev_calls = 0
    all_probe_skills = list(probe.get('skills') or [])
    prefetched_live_jev: dict[str, Any] = {}
    if use_jev and parsed_mock is None and (jev_mod is not None):
        live_candidates: list[dict[str, Any]] = []
        for cand_item in all_probe_skills:
            if float(cand_item.get('priority_score') or 0.0) <= 0.0 or bool(cand_item.get('leased')):
                continue
            live_candidates.append(cand_item)
            if len(live_candidates) >= live_jev_budget:
                break
        if live_candidates:
            questions = {'route_mode': {'type': 'choice', 'instructions': 'Classify skill retro inspection scope from lightweight probe signals.', 'criteria': {'skip': 'Healthy or low-sample noise; keep skill asleep.', 'partial': 'Localized friction in 1-2 dimensions (e.g. L1/L2 script or drawer); run targeted partial check.', 'full': 'Multi-layer drift, creative homogenization, or >=3 dimensions triggered; run full 6-dimension check.'}}, 'noul': {'type': 'noul', 'instructions': 'Confidence that this skill warrants immediate Stage 3 retro evolution.'}}

            def _call_live_jev(c_item: dict[str, Any]) -> tuple[str, Any]:
                c_sk = str(c_item.get('skill') or '')
                try:
                    c_res = jev_mod.jev_decide({'skill': c_sk, 'signals': c_item.get('signals'), 'triggered_aspects': list(c_item.get('triggered_aspects') or []), 'priority_score': float(c_item.get('priority_score') or 0.0)}, questions, agent_role='retro-loop', timeout_sec=0.35, falcon_only=True)
                except TypeError:
                    c_res = jev_mod.jev_decide({'skill': c_sk, 'signals': c_item.get('signals'), 'triggered_aspects': list(c_item.get('triggered_aspects') or []), 'priority_score': float(c_item.get('priority_score') or 0.0)}, questions, agent_role='retro-loop', timeout_sec=0.35)
                return (c_sk, c_res)
            with ThreadPoolExecutor(max_workers=min(6, len(live_candidates))) as pool:
                futs = {pool.submit(_call_live_jev, c_item): str(c_item.get('skill') or '') for c_item in live_candidates}
                t_deadline = time.monotonic() + 0.4
                for fut, c_sk in futs.items():
                    rem = max(0.01, t_deadline - time.monotonic())
                    with contextlib.suppress(Exception):
                        _, c_val = fut.result(timeout=rem)
                        prefetched_live_jev[c_sk] = c_val
    for idx, item in enumerate(all_probe_skills, 1):
        sk_name = str(item.get('skill') or '')
        leased = bool(item.get('leased'))
        lease_reasons = list(item.get('lease_reasons') or [])
        trig = list(item.get('triggered_aspects') or [])
        score = float(item.get('priority_score') or 0.0)
        if score <= 0.0 or not trig:
            det_mode = 'skip'
            det_aspects = []
            det_noul = 0.12
        elif norm_sk and sk_name == norm_sk:
            det_mode = 'partial' if aspect and len(trig) < 6 else 'full' if len(trig) >= 3 else 'partial'
            det_aspects = trig if not aspect else _normalize_aspects(aspect, mode=det_mode)
            det_noul = max(0.8, min(0.99, 0.72 + score * 0.05))
        elif len(trig) >= 3 or ('creative' in trig and len(trig) >= 2):
            det_mode = 'full'
            det_aspects = list(CANONICAL_ASPECTS)
            det_noul = min(0.98, round(0.72 + score * 0.04, 3))
        else:
            det_mode = 'partial'
            det_aspects = trig
            det_noul = min(0.95, round(0.71 + score * 0.04, 3))
        decision_source = 'deterministic_fallback'
        final_mode = det_mode
        final_aspects = det_aspects
        final_noul = det_noul
        jev_raw: Any = None
        if isinstance(parsed_mock, Mapping):
            if sk_name in parsed_mock:
                jev_raw = parsed_mock[sk_name]
            elif str(idx) in parsed_mock:
                jev_raw = parsed_mock[str(idx)]
            elif 'route_mode' in parsed_mock or 'choice' in parsed_mock or 'answers' in parsed_mock:
                jev_raw = parsed_mock
        elif isinstance(parsed_mock, list) and idx - 1 < len(parsed_mock):
            jev_raw = parsed_mock[idx - 1]
        elif use_jev and jev_mod is not None and (score > 0) and (not leased) and (live_jev_calls < live_jev_budget):
            live_jev_calls += 1
            jev_raw = prefetched_live_jev.get(sk_name)
        if jev_raw is not None:
            j_mode, j_aspects, j_noul = _parse_jev_triage_entry(jev_raw, default_aspects=det_aspects or ['l2'], default_score=det_noul)
            sig_map = item.get('signals') if isinstance(item.get('signals'), Mapping) else {}
            has_concrete_measured_telemetry = bool(int(sig_map.get('l2_exit2_roadblocks') or 0) >= 3 or (int(sig_map.get('l1_unwired_drawers') or 0) >= 1 and int(sig_map.get('domain_failures') or 0) >= 1) or int(sig_map.get('l0_trigger_misses') or 0) >= 5 or (score >= 20.0))
            if j_mode == 'skip' and parsed_mock is None and has_concrete_measured_telemetry and (det_mode in {'partial', 'full'}):
                final_mode = det_mode
                final_aspects = det_aspects
                final_noul = max(det_noul, float(j_noul or 0.0))
                decision_source = 'jev_decide:telemetry_floor_preserved'
            elif j_mode is not None:
                final_mode = j_mode
                final_aspects = list(CANONICAL_ASPECTS) if j_mode == 'full' else [] if j_mode == 'skip' else j_aspects or det_aspects or ['l2']
                final_noul = j_noul if j_noul is not None else det_noul
                decision_source = 'jev_mock' if parsed_mock is not None else 'jev_decide'
        if leased and (not norm_sk):
            excluded_by_lease.append({'skill': sk_name, 'lease_reasons': lease_reasons, 'would_have_been': final_mode})
            final_mode = 'skip'
            final_aspects = []
            decision_source = f'{decision_source}:excluded_active_lease'
        decisions.append({'skill': sk_name, 'decision': final_mode, 'mode': final_mode, 'aspects': final_aspects, 'noul': round(float(final_noul), 3), 'priority_score': score, 'decision_source': decision_source, 'leased': leased, 'lease_reasons': lease_reasons, 'owned_paths': [f'skills/{sk_name}/**', f"scripts/test_{sk_name.replace('-', '_')}_*.py"] if final_mode in {'partial', 'full'} else []})
    actionable = [d for d in decisions if d['decision'] in {'partial', 'full'}]
    actionable.sort(key=lambda d: (-float(d['priority_score']), -float(d['noul']), d['skill']))
    if norm_sk:
        effective_top_n = 1
    elif pick_one:
        effective_top_n = 1
    elif pick_top is not None:
        effective_top_n = max(1, int(pick_top))
    else:
        effective_top_n = 3
    selected_targets = actionable[:effective_top_n]
    picked_skill_entry = selected_targets[0] if selected_targets else None
    today_cst = _cst_today_str()
    triage_status = 'ACTIONABLE' if selected_targets else 'HEALTHY_NO_ISSUES'
    triage_status_msg = f'Selected {len(selected_targets)}/{effective_top_n} skill(s) for parallel M1 TL deep reasoning' if selected_targets else 'healthy / 0 issues found (no changes needed)'
    handoff_root_for_plan = emit_briefs_dir or handoff_root or DEFAULT_HANDOFF_ROOT
    preliminary_plan_items = [{'skill': t['skill'], 'mode': t['mode'], 'aspects': t['aspects'], 'owned_paths': t['owned_paths']} for t in selected_targets]
    m2_dispatch_plan = build_m2_dispatch_plan(preliminary_plan_items, handoff_root=handoff_root_for_plan, dev_root=dev_root)
    m1_parallel_dispatches = [{'skill': d['skill'], 'goal_id': d['goal_id'], 'm1_tl_agent': d['m1_tl_agent_name'], 'm1_tl_agent_name': d['m1_tl_agent_name'], 'handoff_spec': d['spec_md'], 'spec_md': d['spec_md'], 'isolated_worktree': d['worktree_dir'], 'worktree_dir': d['worktree_dir'], 'mode': d['mode'], 'aspects': d['aspects'], 'owned_paths': d['owned_paths'], 'dispatch_cmd': d['herdr_dispatch_cmd'], 'herdr_dispatch_cmd': d['herdr_dispatch_cmd'], 'rollup_outcomes': ['FIXED_AND_MERGED', 'HEALTHY_NO_ISSUES', 'CROSS_SKILL_RELAY']} for d in m2_dispatch_plan['parallel_m1_dispatches']]
    result: dict[str, Any] = {'ok': True, 'schema': 'skill-retro-triage/v1', 'stage': 'stage2_triage', 'status': triage_status, 'status_message': triage_status_msg, 'orchestration_topology': '1_M2 -> K_PARALLEL_M1_TLS (K=0..3, 1 M1 per skill) -> M2_ROLLUP', 'm1_tls_to_spawn': len(selected_targets), 'writers_to_spawn': len(selected_targets), 'autonomous_daily_execution': {'owner_approval_gate_required': False, 'daily_batch_default_pick_top': 3, 'pick_top_is_upper_cap_not_quota': True, 'one_skill_equals_one_independent_m1_tl': True, 'zero_issue_policy': 'HEALTHY_NO_ISSUES (spawn 0 M1 TLs / 0 Writers; never invent or force cosmetic edits)', 'm1_healthy_early_exit_policy': "If an M1 TL's deep reasoning finds its assigned skill is healthy/benign, M1 TL writes HEALTHY_NO_ISSUES receipt and returns to M2 with 0 Writers/Reviewers spawned", 'pipeline': '1 M2 -> 1 Cheap Probe Worker -> K (0..3) Parallel M1 TLs (deep reasoning per skill) -> Rollup to Single M2 -> Daily Summary Issue', 'daily_summary_issue_title': f'[Skill Retro {today_cst}] Autonomous Evolution Summary ({len(selected_targets)} skill(s))'}, 'm1_parallel_dispatches': m1_parallel_dispatches, 'worker_role_slot': {'role': 'explorer', 'compatible_roles': ['explorer', 'mechanical'], 'tier': 'worker', 'steering_tokens_burned': 0, 'dispatch_cmd': 'skills/team-lead/scripts/herdr-dispatch.sh --role explorer --name skill-retro-probe-r1'}, 'pick_one': bool(effective_top_n == 1), 'pick_top': effective_top_n, 'picked_skill': picked_skill_entry['skill'] if picked_skill_entry else None, 'picked_mode': picked_skill_entry['mode'] if picked_skill_entry else 'skip', 'picked_aspects': picked_skill_entry['aspects'] if picked_skill_entry else [], 'selected_skills': selected_targets, 'excluded_by_active_lease': excluded_by_lease, 'anti_collision_locks': {'lock1_strict_disjoint_owned_paths': True, 'lock2_active_lease_exclusion': True, 'lock3_wave1_disjoint_wave2_relays': True}, 'm2_dispatch_plan': m2_dispatch_plan, 'wave_plan': {'wave_1_disjoint_hosts': [{'skill': t['skill'], 'goal_id': f"skill-retro-{t['skill']}", 'm1_tl_agent_name': f"skill-retro-{t['skill']}-goal-tl-r1", 'mode': t['mode'], 'aspects': t['aspects'], 'owned_paths': t['owned_paths']} for t in selected_targets], 'wave_2_cross_skill_relays': list(relays or [])}, 'decisions': decisions[:5] if summary else decisions}
    if emit_briefs_dir is not None and selected_targets:
        stage3_runs: list[dict[str, Any]] = []
        combined_brief_records: list[dict[str, Any]] = []
        combined_specs: list[dict[str, Any]] = []
        combined_relays: list[dict[str, Any]] = []
        combined_w1: list[dict[str, Any]] = []
        combined_w2: list[dict[str, Any]] = []
        for target_item in selected_targets:
            diag = compute_skill_retro(warehouse_db=warehouse_db, copilot_db=copilot_db, copilot_sessions_dir=copilot_sessions_dir, usage_jsonl=usage_jsonl, trace_export_path=trace_export_path, handoff_root=handoff_root, skills_root=skills_root, dev_root=dev_root, telemetry_shards_dir=telemetry_shards_dir, skill=target_item['skill'], emit_briefs_dir=emit_briefs_dir, mode=target_item['mode'], aspects=target_item['aspects'], coupled_with=coupled_with, relays=relays, days=days, mirror_roi_snapshot=False)
            eb = diag.get('emitted_briefs') or {}
            stage3_runs.append(eb)
            combined_brief_records.extend(eb.get('briefs') or [])
            combined_specs.extend(eb.get('m1_goal_specs') or [])
            combined_relays.extend(eb.get('cross_skill_relays') or [])
            combined_w1.extend((eb.get('wave_plan') or {}).get('wave_1_disjoint_hosts') or [])
            combined_w2.extend((eb.get('wave_plan') or {}).get('wave_2_cross_skill_relays') or [])
        updated_m2_plan = build_m2_dispatch_plan(combined_brief_records, handoff_root=emit_briefs_dir, dev_root=dev_root)
        result['m2_dispatch_plan'] = updated_m2_plan
        result['emitted_briefs'] = stage3_runs[0] if len(stage3_runs) == 1 else {'briefs_dir': str(emit_briefs_dir / 'briefs' if emit_briefs_dir.name != 'briefs' else emit_briefs_dir), 'handoff_root': str(emit_briefs_dir.parent if emit_briefs_dir.name == 'briefs' else emit_briefs_dir), 'status': 'ACTIONABLE' if combined_brief_records else 'HEALTHY_NO_ISSUES', 'orchestration_topology': '1_M2 -> K_PARALLEL_M1_TLS (K=0..3) -> M2_ROLLUP', 'm1_goals_to_dispatch': len(combined_brief_records), 'm1_tls_to_spawn': len(combined_brief_records), 'writers_spawned': len(combined_brief_records), 'role_slots_reused': ['M1-TL', 'writer', 'reviewer'] if combined_brief_records else [], 'new_agent_roles_created': 0, 'count': len(combined_brief_records), 'briefs': combined_brief_records, 'm1_goal_specs': combined_specs, 'm2_dispatch_plan': updated_m2_plan, 'cross_skill_relays': combined_relays, 'wave_plan': {'wave_1_disjoint_hosts': combined_w1, 'wave_2_cross_skill_relays': combined_w2}}
    return result

def _verify_m1_receipt_grounding(receipt: Mapping[str, Any], *, repo_root: Path | None=None, verify_grounding: bool=False) -> tuple[bool, list[str]]:
    """Verify M1 receipt-to-trace faithfulness (`receipt_grounded`, `grounding_issues` / `grounding_errors`)."""
    status = str(receipt.get('status') or '').strip().upper()
    issues: list[str] = []
    if status != 'FIXED_AND_MERGED':
        return (True, issues)
    sha_val = str(receipt.get('merge_sha') or receipt.get('commit_sha') or '').strip()
    pr_val = str(receipt.get('pr_url') or receipt.get('pr') or (f"#{receipt['pr_number']}" if receipt.get('pr_number') else '')).strip()
    if not sha_val:
        issues.append('missing_commit_or_merge_sha')
    elif not _VALID_GIT_SHA_RE.fullmatch(sha_val):
        issues.append(f'invalid_sha_format:{sha_val}')
    if not pr_val:
        issues.append('missing_pr_reference')
    elif not _VALID_PR_REF_RE.fullmatch(pr_val):
        issues.append(f'invalid_pr_reference:{pr_val}')
    eff_repo: Path | None = repo_root
    if eff_repo is None and verify_grounding:
        default_repo, _ = _find_git_root_for_path(DEFAULT_SKILLS_ROOT)
        eff_repo = default_repo or DEFAULT_SKILLS_ROOT.parent
    if eff_repo is not None or verify_grounding:
        if eff_repo is None or not _safe_is_dir(eff_repo):
            issues.append(f'invalid_or_missing_repo_root:{eff_repo}')
        elif sha_val and _VALID_GIT_SHA_RE.fullmatch(sha_val):
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            try:
                proc = subprocess.run(['git', '--no-optional-locks', 'cat-file', '-t', sha_val], cwd=eff_repo, env=clean_env, capture_output=True, text=True, timeout=2.5, check=False)
                obj_type = proc.stdout.strip()
                if proc.returncode != 0 or obj_type not in {'commit', 'tag'}:
                    issues.append(f'commit_not_found_in_git:{sha_val}')
            except (OSError, subprocess.SubprocessError):
                issues.append(f'git_verification_error:{sha_val}')
    return (len(issues) == 0, issues)
