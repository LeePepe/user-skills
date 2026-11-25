from __future__ import annotations
from .common import *

"""Roi Diagnostics implementation for the skill-retro engine."""

def compute_skill_roi_diagnostics(warehouse_db: Path | None=None, copilot_db: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, magpie_usage: Path | None=None, dsh_sessions: Path | None=None, telemetry_shards_dir: Path | None=None, *, auto_propose: bool=False, proposals_path: Path | None=None, icloud_sync_dir: Path | None=None, mirror_icloud_roi: bool | None=None, icloud_roi_path: Path | None=None) -> dict[str, Any]:
    """Compute S1-S4, S6-S10 Skill ROI diagnostics, and S11-S12 Context & Tool Burn."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    cp = copilot_db or DEFAULT_COPILOT_DB
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    resolved_sync = _resolve_icloud_sync_dir(icloud_sync_dir)
    shards_dir = telemetry_shards_dir if telemetry_shards_dir is not None else resolved_sync / 'telemetry-shards' if resolved_sync is not None and (icloud_sync_dir is not None or (warehouse_db is None and copilot_db is None)) else None
    effective_roi_path = icloud_roi_path or (resolved_sync / 'jobs' / 'aidata-digest' / 'skills-roi.json' if resolved_sync is not None else None)
    should_mirror_roi = mirror_icloud_roi if mirror_icloud_roi is not None else icloud_roi_path is not None or icloud_sync_dir is not None or (resolved_sync is not None and warehouse_db is None and (copilot_db is None))
    inventory = _discover_skill_inventory(s_root, d_root)
    known_skills = set(inventory.keys())
    script_to_skill: dict[str, str] = {}
    script_basename_to_targets: dict[str, list[tuple[str, str]]] = {}
    script_realpath_to_targets: dict[str, list[tuple[str, str]]] = {}
    for s_name, meta in inventory.items():
        for sc in meta.get('scripts', []):
            script_to_skill[sc] = s_name
        for rel_sc in meta.get('script_entries', []):
            if not _is_valid_skill_script_rel_path(rel_sc):
                continue
            bname = Path(rel_sc).name
            script_basename_to_targets.setdefault(bname, []).append((s_name, rel_sc))
        for rel_sc, resolved_p in (meta.get('script_realpaths') or {}).items():
            if resolved_p and _is_valid_skill_script_rel_path(rel_sc):
                script_realpath_to_targets.setdefault(resolved_p, []).append((s_name, rel_sc))
    basename_regex = re.compile('(?<![A-Za-z0-9_./-])(' + '|'.join((re.escape(bn) for bn in sorted(script_basename_to_targets.keys(), key=len, reverse=True))) + ')(?![A-Za-z0-9_.-])') if script_basename_to_targets else None
    wall_clock_date = _cst_today_str()
    historical_dates: list[datetime] = []
    if _safe_is_file(wh):
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(wh) as conn:
            if conn is not None:
                for table in ('fact_turn', 'fact_gtt_task_run', 'skill_day', 'hermes_skill_day'):
                    columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
                    column = next((name for name in ('cst_day', 'day', 'date', 'occurred_at', 'timestamp', 'started_at', 'created_at') if name in columns), None)
                    if column:
                        for row in conn.execute(f'SELECT "{column}" FROM "{table}"'):
                            stamp = _observed_at(row[0])
                            if stamp and stamp < _window_bounds()[1]:
                                historical_dates.append(stamp)
    anchor_as_of = max(historical_dates).date().isoformat() if historical_dates else None
    staleness_days = (date.fromisoformat(wall_clock_date) - date.fromisoformat(anchor_as_of)).days if anchor_as_of else None
    window_anchor = {'anchor_date': anchor_as_of, 'source_as_of': anchor_as_of, 'wall_clock_date': wall_clock_date, 'staleness_days': staleness_days, 'coverage_status': 'missing' if staleness_days is None else 'fresh' if staleness_days <= 2 else 'stale', 'window_days_7d': 7, 'window_days_30d': 30, 'basis': 'historical_source_watermark; not the live metric window'}
    stats: dict[str, dict[str, Any]] = {s_name: {'skill': s_name, 'loads_7d': 0, 'loads_30d': 0, 'script_execs': 0, 'script_failures': 0, 'script_touch_sessions': 0, 'script_durations_ms': [], 'has_scripts': bool(meta.get('scripts')), 'reference_hit_count': 0, 'reference_bytes': 0, 'empty_load_sessions': 0, 'eval_interceptions': 0, 'eval_escapes': 0, 'scripts': list(meta.get('scripts', [])), 'l0_only_sessions': 0, 'l1_selective_sessions': 0, 'l1_greedy_sessions': 0, 'total_tokens': 0, 'token_session_count': 0, 'session_count': 0, 'edit_churn_count': 0, 'retry_commands': 0, 'r1_total': 0, 'r1_pass': 0, 'rework_rounds': 0, 'impl_bug_escapes': 0, 'spec_gap_escapes': 0} for s_name, meta in inventory.items()}

    def _ensure_skill(name: str) -> dict[str, Any]:
        norm = 'team-lead' if name == 'goal-target-task' else name
        if norm not in stats:
            stats[norm] = {'skill': norm, 'loads_7d': 0, 'loads_30d': 0, 'script_execs': 0, 'script_failures': 0, 'script_touch_sessions': 0, 'script_durations_ms': [], 'has_scripts': False, 'reference_hit_count': 0, 'reference_bytes': 0, 'empty_load_sessions': 0, 'eval_interceptions': 0, 'eval_escapes': 0, 'scripts': [], 'l0_only_sessions': 0, 'l1_selective_sessions': 0, 'l1_greedy_sessions': 0, 'total_tokens': 0, 'token_session_count': 0, 'session_count': 0, 'edit_churn_count': 0, 'retry_commands': 0, 'r1_total': 0, 'r1_pass': 0, 'rework_rounds': 0, 'impl_bug_escapes': 0, 'spec_gap_escapes': 0}
        return stats[norm]
    populations: dict[str, dict[str, list[datetime]]] = {}
    observed: list[datetime] = []

    def observe(skill: str, kind: str, stamp: datetime) -> dict[str, Any]:
        entry = _ensure_skill(skill)
        populations.setdefault(entry['skill'], {}).setdefault(kind, []).append(stamp)
        return entry
    s2_gate = {'total_runs_7d': 0, 'true_positive_catches_7d': 0, 'config_or_rerun_noise_7d': 0, 'classified_sample_size': 0, 'gate_catch_rate': None, 'noise_rate': None, **_freshness([], wh.is_file())}
    eval_total = 0
    eval_interceptions = 0
    eval_escapes = 0
    eval_by_case: dict[str, dict[str, int]] = {}
    per_skill_eval_cases: dict[str, dict[str, dict[str, int]]] = {}
    per_skill_refs: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_scripts: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_channels: dict[str, dict[str, int]] = {}
    per_skill_cache: dict[str, dict[str, int]] = {}
    session_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    turn_skill_links: dict[str, set[str]] = {}
    start7, _ = _window_bounds()
    start30, end30 = _window_bounds(30)
    shard_records_30d = _read_telemetry_shards(shards_dir, days=30)
    shards_present = bool(shards_dir and _safe_glob(shards_dir, '*-skill-usage.jsonl'))
    warehouse_present = wh.is_file() or cp.is_file() or shards_present
    l2_channels = _read_l2_channel_counts(wh, start30, end30)
    seen_turn_sigs_30d: set[tuple[str, str, str]] = set()
    seen_ref_sigs_30d: set[tuple[str, str, str]] = set()
    seen_script_sigs_30d: set[tuple[str, str, str]] = set()

    def _norm_skill(name: str) -> str:
        return 'team-lead' if name in {'goal-target-task', 'goal-task'} else name

    def _bump_channel(skill: str, channel: str, delta: int=1) -> None:
        sk = _norm_skill(skill)
        bucket = per_skill_channels.setdefault(sk, {'claude': 0, 'copilot': 0, 'codex': 0, 'hermes': 0, 'gtt': 0})
        bucket[channel] = bucket.get(channel, 0) + max(0, int(delta))

    def session_for(source: str, sid: str, skill: str, stamp: datetime, *, reference_source: bool=False) -> dict[str, Any]:
        skill = _norm_skill(skill)
        key = (source, sid or f'row-{len(session_records)}', skill)
        record = session_records.setdefault(key, {'source': source, 'sid': sid, 'skill': skill, 'stamp': stamp, 'loaded': True, 'refs': {}, 'reference_source': reference_source, 'read_proxy': 0, 'scripts': [], 'edits': 0, 'tokens': 0, 'token_seen': False})
        record['stamp'] = max(record['stamp'], stamp)
        record['reference_source'] = record['reference_source'] or reference_source
        return record

    def add_reference(source: str, sid: str, skill: str, path: str, stamp: datetime, byte_len: Any=0) -> None:
        norm_sk = _norm_skill(skill)
        record = session_for(source, sid, norm_sk, stamp, reference_source=True)
        ref_path = str(path or '').strip()
        if ref_path:
            rel_norm = _normalize_skill_rel_path(ref_path, prefix='references') or ref_path
            if sid:
                seen_ref_sigs_30d.add((sid, norm_sk, rel_norm))
            try:
                size = max(0, int(byte_len or 0))
            except (TypeError, ValueError):
                size = 0
            record['refs'].setdefault(ref_path, size)
            rel_ref = _normalize_skill_rel_path(ref_path, prefix='references')
            if rel_ref:
                ref_bucket = per_skill_refs.setdefault(norm_sk, {}).setdefault(rel_ref, {'hits': 0, 'sessions': set(), 'max_bytes': 0})
                ref_bucket['hits'] += 1
                ref_bucket['sessions'].add(sid or f'{source}:{stamp.isoformat()}')
                ref_bucket['max_bytes'] = max(ref_bucket['max_bytes'], size)
        observed.append(stamp)
        observe(norm_sk, 'usage', stamp)

    def add_script(source: str, sid: str, skill: str, path: str, stamp: datetime, exit_code: Any=0, duration_ms: Any=None) -> None:
        norm_sk = _norm_skill(skill)
        raw_path_str = str(path or '').strip()
        is_legacy_proxy = raw_path_str == 'legacy:bash'
        rel_script = '' if is_legacy_proxy else _normalize_skill_rel_path(raw_path_str, prefix='scripts')
        if not is_legacy_proxy and (not _is_valid_skill_script_rel_path(rel_script)):
            return
        has_exit = exit_code not in (None, '')
        try:
            code = int(exit_code) if has_exit else 0
        except (TypeError, ValueError):
            code = 0
            has_exit = False
        has_dur = duration_ms not in (None, '')
        try:
            duration = max(0.0, float(duration_ms)) if has_dur else 0.0
        except (TypeError, ValueError):
            duration = 0.0
            has_dur = False

        def _record_single_script(target_sk: str, target_rel: str, *, is_proxy: bool=False) -> None:
            record = session_for(source, sid, target_sk, stamp, reference_source=source == 'copilot')
            sig_path = 'legacy:bash' if is_proxy else target_rel
            call = (sig_path, stamp.isoformat(), code, duration)
            if sid and sig_path:
                seen_script_sigs_30d.add((sid, target_sk, sig_path))
            if call not in record['scripts']:
                record['scripts'].append(call)
                if not is_proxy and target_rel:
                    sc_bucket = per_skill_scripts.setdefault(target_sk, {}).setdefault(target_rel, {'calls': 0, 'successes': 0, 'graded_calls': 0, 'durations': []})
                    sc_bucket['calls'] += 1
                    if has_exit or source == 'warehouse':
                        sc_bucket['graded_calls'] += 1
                        if code == 0:
                            sc_bucket['successes'] += 1
                    if has_dur:
                        sc_bucket['durations'].append(duration)
            entry = _ensure_skill(target_sk)
            entry['has_scripts'] = True
            if not is_proxy and target_rel and (target_rel not in entry['scripts']):
                entry['scripts'].append(target_rel)
            observed.append(stamp)
            observe(target_sk, 'usage', stamp)
        if is_legacy_proxy:
            _record_single_script(norm_sk, '', is_proxy=True)
            return
        targets: list[tuple[str, str]] = []
        inv_entry = inventory.get(norm_sk)
        if inv_entry and rel_script in set(inv_entry.get('script_entries', [])):
            targets.append((norm_sk, rel_script))
            rp = (inv_entry.get('script_realpaths') or {}).get(rel_script)
            if rp:
                for sym_sk, sym_rel in script_realpath_to_targets.get(rp, []):
                    if (sym_sk, sym_rel) not in targets:
                        targets.append((sym_sk, sym_rel))
        else:
            bname = Path(rel_script).name
            by_name = script_basename_to_targets.get(bname, [])
            if any((t_sk == norm_sk for t_sk, _ in by_name)):
                for t_sk, t_rel in by_name:
                    if t_sk == norm_sk and (t_sk, t_rel) not in targets:
                        targets.append((t_sk, t_rel))
            elif len(by_name) == 1 and norm_sk not in known_skills:
                targets.append(by_name[0])
            else:
                targets.append((norm_sk, rel_script))
        for t_sk, t_rel in targets:
            _record_single_script(t_sk, t_rel)

    def _attribute_basename_scripts_in_text(source: str, sid: str, text: str, active_skills: set[str], stamp: datetime, exit_code: Any=None, duration_ms: Any=None) -> set[str]:
        """Match bare script basenames (e.g. goal-queue.py) when full skills/<sk>/scripts/ prefix is omitted."""
        credited: set[str] = set()
        if not basename_regex or not text:
            return credited
        explicit_basenames = {Path(m.group('script')).name for m in _SCRIPT_PATH_RE.finditer(text)}
        for m in basename_regex.finditer(text):
            bname = m.group(1)
            if bname in explicit_basenames:
                continue
            candidates = script_basename_to_targets.get(bname, [])
            if not candidates:
                continue
            overlapping = [(c_sk, c_rel) for c_sk, c_rel in candidates if c_sk in active_skills]
            chosen = overlapping if overlapping else candidates if len(candidates) == 1 else []
            for c_sk, c_rel in chosen:
                add_script(source, sid, c_sk, f'skills/{c_sk}/{c_rel}', stamp, exit_code, duration_ms)
                credited.add(c_sk)
        return credited
    goal_skills_cache: dict[str, set[str]] = {}

    def _handoff_skills_for_goal(goal: str) -> set[str]:
        if not goal:
            return set()
        if goal in goal_skills_cache:
            return goal_skills_cache[goal]
        handoff = d_root / 'handoff' / goal
        text_parts: list[str] = []
        if _probe_readable_dir(handoff):
            spec_p = handoff / 'spec.md'
            if _safe_is_file(spec_p):
                try:
                    text_parts.append(spec_p.read_text(encoding='utf-8', errors='ignore'))
                except OSError:
                    pass
            briefs_dir = handoff / 'briefs'
            if _probe_readable_dir(briefs_dir):
                for path in _safe_glob(briefs_dir, '*.md'):
                    if _safe_is_file(path):
                        try:
                            text_parts.append(path.read_text(encoding='utf-8', errors='ignore'))
                        except OSError:
                            pass
        extracted = _extract_skill_names('\n'.join(text_parts), known_skills) if text_parts else set()
        goal_skills_cache[goal] = extracted
        return extracted

    def task_skills(row: dict[str, Any], *, keys_to_skills: dict[str, set[str]]) -> set[str]:
        raw_explicit = {str(item).strip() for item in _json_list(row.get('skills_used')) if str(item).strip()}
        if not raw_explicit and row.get('skills_used'):
            raw_explicit = _extract_skill_names(row.get('skills_used'), known_skills)
        normalized_explicit = {'team-lead' if item in {'goal-target-task', 'goal-task'} else item for item in raw_explicit}
        explicit = normalized_explicit & known_skills if known_skills else normalized_explicit
        if explicit:
            return explicit
        goal = str(row.get('goal_id') or '')
        inferred = set(_handoff_skills_for_goal(goal))
        for key in (row.get('goal_id'), row.get('git_branch'), row.get('repo')):
            if key:
                inferred.update(keys_to_skills.get(str(key).strip().lower(), set()))
        if inferred:
            return inferred
        goal_lower = goal.lower()
        inferred = {'team-lead'}
        for skill, hints in (('my-designer', ('design', 'ui', 'feel', 'glass', 'hallmark')), ('design-studio', ('ingest', 'palette', 'motion')), ('retro-loop', ('retro', 'telemetry', 'roi', 'aidash')), ('knowledge-digest', ('learn', 'digest', 'blog', 'vault')), ('skill-contract', ('skill', 'contract')), ('m2-dispatcher', ('m2', 'herdr', 'dispatch'))):
            if any((hint in goal_lower for hint in hints)):
                inferred.add(skill)
        return inferred
    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f'file:{wh}?mode=ro', uri=True)) as conn:
            ci_rows = _timed_rows(conn, 'fact_ci_job_run')
            catches = sum((row.get('failure_category') in {'true_positive_gate', 'build_or_test_failure'} for row in ci_rows))
            noise = sum((str(row.get('failure_category') or '').startswith('config_drift_') or row.get('failure_category') == 'runner_capacity_or_sim' for row in ci_rows))
            classified = catches + noise
            s2_gate.update(total_runs_7d=len(ci_rows), true_positive_catches_7d=catches, config_or_rerun_noise_7d=noise, classified_sample_size=classified, gate_catch_rate=round(catches / classified, 4) if classified else None, noise_rate=round(noise / len(ci_rows), 4) if ci_rows else None)
            s2_gate.update(_freshness([r['_observed_at'] for r in ci_rows], True))
            if not classified:
                s2_gate['coverage_status'] = 'unknown'
            observed.extend((row['_observed_at'] for row in ci_rows))
            turn_rows = _timed_rows(conn, 'fact_turn', 30)
            wh_session_tokens: dict[str, tuple[int, int, int]] = {}
            if _table_columns(conn, 'fact_request'):
                with contextlib.suppress(sqlite3.Error):
                    req_start_ms = int(start30.timestamp() * 1000)
                    req_end_ms = int(end30.timestamp() * 1000)
                    for r_sid, r_in, r_out, r_cr in conn.execute("SELECT session_uuid, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cache_read), 0) FROM fact_request WHERE session_uuid IS NOT NULL AND session_uuid != '' AND ts >= ? AND ts < ? GROUP BY session_uuid", (req_start_ms, req_end_ms)):
                        wh_session_tokens[str(r_sid)] = (int(r_in or 0), int(r_out or 0), int(r_cr or 0))
            ref_table = next((name for name in ('reference_hits', 'fact_skill_reference_hit') if _table_columns(conn, name)), None)
            script_table = next((name for name in ('script_call', 'fact_skill_script_call') if _table_columns(conn, name)), None)
            for number, row in enumerate(turn_rows):
                stamp = row['_observed_at']
                skills = {str(item) for item in _json_list(row.get('skill_names')) if str(item).strip()}
                if row.get('attribution_skill'):
                    skills.add(str(row['attribution_skill']))
                sid = str(row.get('session_id') or row.get('turn_uuid') or f'turn-{number}')
                keys = [row.get(key) for key in ('goal_id', 'git_branch', 'repo')]
                for key in keys:
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
                tools = _json_list(row.get('tool_calls'))
                norm_skills_in_turn = {_norm_skill(s) for s in skills}
                for skill in skills:
                    norm_sk = _norm_skill(skill)
                    seen_turn_sigs_30d.add((sid, str(row.get('turn_uuid') or f'turn-{number}'), norm_sk))
                    _bump_channel(norm_sk, 'claude')
                    cache_bucket = per_skill_cache.setdefault(norm_sk, {'in': 0, 'cache_read': 0})
                    cache_bucket['in'] += int(row.get('input_tokens') or 0)
                    cache_bucket['cache_read'] += int(row.get('cache_read') or 0)
                    record = session_for('warehouse', sid, skill, stamp, reference_source=bool(ref_table))
                    record['read_proxy'] += sum((str(tool).lower() in {'read', 'view', 'read_file'} for tool in tools))
                    record['edits'] += sum((str(tool).lower() in {'edit', 'write', 'multiedit', 'apply_patch', 'create'} for tool in tools))
                    record['tokens'] += sum((int(row.get(key) or 0) for key in ('input_tokens', 'output_tokens', 'cache_read')))
                    record['token_seen'] = record['token_seen'] or any((row.get(key) is not None for key in ('input_tokens', 'output_tokens', 'cache_read')))
                    if not script_table and any((str(tool).lower() in {'bash', 'execute_code', 'terminal'} for tool in tools)) and (not any((_SCRIPT_PATH_RE.search(str(tool)) for tool in tools))) and _ensure_skill(skill)['has_scripts']:
                        add_script('warehouse', sid, skill, 'legacy:bash', stamp)
                    observe(skill, 'usage', stamp)
                for tool in tools:
                    text = str(tool.get('command') or tool.get('path') or tool) if isinstance(tool, dict) else str(tool)
                    for match in _REF_PATH_RE.finditer(text):
                        add_reference('warehouse', sid, match.group('skill'), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp)
                    for match in _SCRIPT_PATH_RE.finditer(text):
                        add_script('warehouse', sid, match.group('skill'), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp)
                    _attribute_basename_scripts_in_text('warehouse', sid, text, norm_skills_in_turn, stamp)
                if skills:
                    observed.append(stamp)
            for (src_key, sid_key, sk_key), rec_val in session_records.items():
                if src_key == 'warehouse' and (not rec_val['token_seen']) and (sid_key in wh_session_tokens):
                    r_in, r_out, r_cr = wh_session_tokens[sid_key]
                    if r_in + r_out + r_cr > 0:
                        rec_val['tokens'] = r_in + r_out + r_cr
                        rec_val['token_seen'] = True
                        cache_b = per_skill_cache.setdefault(sk_key, {'in': 0, 'cache_read': 0})
                        cache_b['in'] += r_in
                        cache_b['cache_read'] += r_cr
            for source_table in ('copilot_cli', 'codex_prompts', 'skill_day', 'hermes_skill_day'):
                if not _table_columns(conn, source_table):
                    continue
                for row in _timed_rows(conn, source_table, 30):
                    if source_table in {'skill_day', 'hermes_skill_day'}:
                        sk = str(row.get('skill') or '').strip()
                        cnt = int(row.get('calls') or row.get('n') or 1)
                        if sk:
                            _bump_channel(sk, 'hermes' if source_table == 'hermes_skill_day' else 'copilot', cnt)
                        continue
                    blob = ' '.join((str(row.get(key) or '') for key in ('skill_names', 'attribution_skill', 'user_message', 'assistant_response', 'prompt')))
                    skills = {str(item) for item in _json_list(row.get('skill_names')) if str(item).strip()}
                    skills.update(_extract_skill_names(blob, known_skills))
                    if row.get('attribution_skill'):
                        skills.add(str(row['attribution_skill']))
                    ch_name = 'codex' if source_table == 'codex_prompts' else 'copilot'
                    for sk in skills:
                        _bump_channel(sk, ch_name)
                    for key in (row.get('goal_id'), row.get('git_branch'), row.get('repo')):
                        if key:
                            turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
            if ref_table:
                for row in _timed_rows(conn, ref_table, 30):
                    skill = str(row.get('skill') or '').strip()
                    path = str(row.get('reference_path') or row.get('ref_path') or '').strip()
                    if skill and path:
                        src_col = str(row.get('source') or '').lower()
                        _bump_channel(skill, 'codex' if 'codex' in src_col else 'copilot')
                        add_reference('warehouse', str(row.get('session_id') or row.get('turn_uuid') or ''), skill, path, row['_observed_at'], row.get('byte_len'))
            if script_table:
                for row in _timed_rows(conn, script_table, 30):
                    skill = str(row.get('skill') or '').strip()
                    path = str(row.get('script_path') or row.get('path') or '').strip()
                    if skill and path:
                        src_col = str(row.get('source') or '').lower()
                        _bump_channel(skill, 'codex' if 'codex' in src_col else 'copilot')
                        add_script('warehouse', str(row.get('session_id') or row.get('turn_uuid') or ''), skill, path, row['_observed_at'], row.get('exit_code'), row.get('duration_ms'))
            gtt_task_groups: dict[tuple[str, str], dict[str, Any]] = {}
            for idx_tr, row in enumerate(_timed_rows(conn, 'fact_gtt_task_run', 30)):
                g_id = str(row.get('goal_id') or '')
                t_id = str(row.get('task_id') or f'task-{idx_tr}')
                role = str(row.get('role_name') or '').strip().lower()
                stamp = row['_observed_at']
                row_skills = task_skills(row, keys_to_skills=turn_skill_links)
                for key in (row.get('goal_id'), row.get('git_branch'), row.get('repo')):
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(row_skills)
                grp = gtt_task_groups.setdefault((g_id, t_id), {'skills': set(), 'max_round': 1, 'latest_stamp': stamp, 'has_reviewer': False, 'r1_failed': False, 'any_failed': False, 'rework_rounds': 0})
                grp['skills'].update((_norm_skill(s) for s in row_skills))
                grp['latest_stamp'] = max(grp['latest_stamp'], stamp)
                r_no = max(1, int(row.get('round_no') or 1))
                grp['max_round'] = max(grp['max_round'], r_no)
                verdict_up = str(row.get('v5_verdict') or '').strip().upper()
                status_low = str(row.get('status') or '').strip().lower()
                is_reviewer_role = role in {'reviewer', 'code_reviewer', 'planner_reviewer'}
                if is_reviewer_role:
                    grp['has_reviewer'] = True
                    if r_no == 1:
                        if verdict_up not in {'PASS', 'CONCERNS', 'WAIVED'} or status_low == 'failed':
                            grp['r1_failed'] = True
                    elif r_no >= 2:
                        grp['rework_rounds'] += 1
                else:
                    if status_low == 'failed' or verdict_up == 'FAIL':
                        grp['any_failed'] = True
                    if r_no >= 2:
                        grp['rework_rounds'] = max(grp['rework_rounds'], r_no - 1)
                for skill in row_skills:
                    _bump_channel(skill, 'gtt')
            for grp in gtt_task_groups.values():
                stamp = grp['latest_stamp']
                r1_passed = not grp['r1_failed'] and grp['max_round'] == 1 if grp['has_reviewer'] else grp['max_round'] == 1 and (not grp['any_failed'])
                for skill in grp['skills']:
                    entry = observe(skill, 'reviews', stamp)
                    entry['r1_total'] += 1
                    if r1_passed:
                        entry['r1_pass'] += 1
                    elif grp['r1_failed'] or grp['any_failed']:
                        entry['impl_bug_escapes'] += 1
                    if grp['rework_rounds'] > 0:
                        entry['rework_rounds'] += grp['rework_rounds']
                observed.append(stamp)
            for row in _timed_rows(conn, 'fact_gtt_event', 30):
                event = row.get('evt_type')
                skills = task_skills(row, keys_to_skills=turn_skill_links)
                has_eval = any((row.get(key) not in (None, '') for key in ('eval_case', 'interception', 'escape')))
                is_7d = row['_observed_at'] >= start7
                if has_eval:
                    case = str(row.get('eval_case') or 'unclassified')
                    int_flag = int(_flag(row.get('interception')))
                    esc_flag = int(_flag(row.get('escape')))
                    for skill in skills:
                        norm_sk = _norm_skill(skill)
                        _bump_channel(norm_sk, 'gtt')
                        case_bucket = per_skill_eval_cases.setdefault(norm_sk, {}).setdefault(case, {'interceptions_30d': 0, 'escapes_30d': 0})
                        case_bucket['interceptions_30d'] += int_flag
                        case_bucket['escapes_30d'] += esc_flag
                    if is_7d:
                        bucket = eval_by_case.setdefault(case, {'observations': 0, 'interceptions': 0, 'escapes': 0})
                        bucket['observations'] += 1
                        eval_total += 1
                        if int_flag:
                            eval_interceptions += 1
                            bucket['interceptions'] += 1
                        if esc_flag:
                            eval_escapes += 1
                            bucket['escapes'] += 1
                        for skill in skills:
                            entry = observe(skill, 'events', row['_observed_at'])
                            entry['eval_interceptions'] += int_flag
                            entry['eval_escapes'] += esc_flag
                if is_7d and event in {'REVIEW_FAIL', 'RULING', 'CHANGE_REQUEST'}:
                    note = str(row.get('note_preview') or '').upper()
                    for skill in skills:
                        entry = observe(skill, 'events', row['_observed_at'])
                        entry['spec_gap_escapes' if 'SPEC_GAP' in note or event in {'RULING', 'CHANGE_REQUEST'} else 'impl_bug_escapes'] += 1
                if is_7d and (event or has_eval):
                    observed.append(row['_observed_at'])
    if cp.is_file():
        with contextlib.closing(sqlite3.connect(f'file:{cp}?mode=ro', uri=True)) as conn:
            files = _timed_rows(conn, 'session_files', 30)
            turns = _timed_rows(conn, 'turns', 30)
            cp_session_usage: dict[str, tuple[int, int, int, int]] = {}
            if _table_columns(conn, 'assistant_usage_events'):
                for u_row in _timed_rows(conn, 'assistant_usage_events', 30):
                    u_sid = str(u_row.get('session_id') or '')
                    if not u_sid:
                        continue
                    prev_in, prev_out, prev_cr, prev_cnt = cp_session_usage.get(u_sid, (0, 0, 0, 0))
                    cp_session_usage[u_sid] = (prev_in + int(u_row.get('input_tokens') or 0), prev_out + int(u_row.get('output_tokens') or 0), prev_cr + int(u_row.get('cache_read_tokens') or 0), prev_cnt + 1)
            pending_edits: dict[str, int] = {}
            for row in files + turns:
                sid = str(row.get('session_id') or '')
                stamp = row['_observed_at']
                text = str(row.get('file_path') or '') if 'file_path' in row else f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                matched = _extract_skill_names(text, known_skills)
                ref_matches = list(_REF_PATH_RE.finditer(text))
                script_matches = list(_SCRIPT_PATH_RE.finditer(text))
                matched.update((match.group('skill') for match in ref_matches))
                matched.update((match.group('skill') for match in script_matches))
                matched_norm = {_norm_skill(s) for s in matched}
                if 'file_path' not in row:
                    matched_norm.update(_attribute_basename_scripts_in_text('copilot', sid, text, matched_norm, stamp, row.get('exit_code'), row.get('duration_ms')))
                for skill in matched_norm:
                    norm_sk = _norm_skill(skill)
                    if sid:
                        seen_turn_sigs_30d.add((sid, f"turn-{row.get('turn_index', '')}", norm_sk))
                    _bump_channel(norm_sk, 'copilot')
                    record = session_for('copilot', sid, norm_sk, stamp, reference_source=True)
                    if 'input_tokens' in row:
                        in_tok = int(row.get('input_tokens') or 0)
                        cr_tok = int(row.get('cache_read') or 0)
                        cache_bucket = per_skill_cache.setdefault(norm_sk, {'in': 0, 'cache_read': 0})
                        cache_bucket['in'] += in_tok
                        cache_bucket['cache_read'] += cr_tok
                        record['tokens'] += sum((int(row.get(key) or 0) for key in ('input_tokens', 'output_tokens', 'cache_read')))
                        record['token_seen'] = True
                    observe(norm_sk, 'usage', stamp)
                for match in ref_matches:
                    add_reference('copilot', sid, match.group('skill'), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp, row.get('byte_len'))
                for match in script_matches:
                    add_script('copilot', sid, match.group('skill'), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp, row.get('exit_code'), row.get('duration_ms'))
                tool_name = str(row.get('tool_name') or '').lower()
                if tool_name in {'edit', 'write', 'multiedit', 'apply_patch', 'create'}:
                    if matched_norm:
                        for skill in matched_norm:
                            session_for('copilot', sid, skill, stamp, reference_source=True)['edits'] += 1
                    else:
                        pending_edits[sid] = pending_edits.get(sid, 0) + 1
                observed.append(stamp)
            for (source, sid, skill), record in session_records.items():
                if source == 'copilot' and pending_edits.get(sid):
                    record['edits'] += pending_edits[sid]
                if source == 'copilot' and (not record['token_seen']) and (sid in cp_session_usage):
                    u_in, u_out, u_cr, u_cnt = cp_session_usage[sid]
                    if u_in + u_out + u_cr > 0:
                        avg_evt_tokens = max(1, (u_in + u_out + u_cr) // max(1, u_cnt))
                        record['tokens'] = avg_evt_tokens
                        record['token_seen'] = True
                        cache_b = per_skill_cache.setdefault(skill, {'in': 0, 'cache_read': 0})
                        cache_b['in'] += u_in
                        cache_b['cache_read'] += u_cr
            for record in session_records.values():
                if record['source'] == 'copilot' and record['edits'] >= 4:
                    observe(record['skill'], 'churn', record['stamp'])['edit_churn_count'] += record['edits']
    for s_row in shard_records_30d:
        kind = str(s_row.get('kind') or '')
        if kind not in {'skill_turn', 'script_call', 'reference_hit'}:
            continue
        raw_sk = str(s_row.get('skill') or '').strip()
        if not raw_sk:
            continue
        norm_sk = _norm_skill(raw_sk)
        sid = str(s_row.get('session_id') or '')
        tid = str(s_row.get('turn_id') or s_row.get('call_uid') or s_row.get('hit_uid') or '')
        host_tag = str(s_row.get('host') or 'shard').strip()
        shard_src = f'shard:{host_tag}'
        stamp = s_row['_observed_at']
        ch = str(s_row.get('channel') or 'copilot').strip().lower()
        if ch not in {'claude', 'copilot', 'codex', 'hermes', 'gtt'}:
            ch = 'copilot'
        if kind == 'skill_turn':
            if (sid or tid) and (sid, tid, norm_sk) in seen_turn_sigs_30d:
                continue
            if sid or tid:
                seen_turn_sigs_30d.add((sid, tid, norm_sk))
            _bump_channel(norm_sk, ch)
            in_tok = int(s_row.get('input_tokens') or s_row.get('in') or 0)
            out_tok = int(s_row.get('output_tokens') or s_row.get('out') or 0)
            cr_tok = int(s_row.get('cache_read') or 0)
            cache_bucket = per_skill_cache.setdefault(norm_sk, {'in': 0, 'cache_read': 0})
            cache_bucket['in'] += in_tok
            cache_bucket['cache_read'] += cr_tok
            record = session_for(shard_src, sid or tid, norm_sk, stamp, reference_source=True)
            tools = _json_list(s_row.get('tool_calls'))
            record['read_proxy'] += sum((str(t).lower() in {'read', 'view', 'read_file'} for t in tools))
            record['edits'] += sum((str(t).lower() in {'edit', 'write', 'multiedit', 'apply_patch', 'create'} for t in tools))
            if in_tok + out_tok + cr_tok > 0:
                record['tokens'] += in_tok + out_tok + cr_tok
                record['token_seen'] = True
            observed.append(stamp)
            observe(norm_sk, 'usage', stamp)
        elif kind == 'reference_hit':
            ref_p = str(s_row.get('reference_path') or '').strip()
            rel_norm = _normalize_skill_rel_path(ref_p, prefix='references') or ref_p
            if sid and (sid, norm_sk, rel_norm) in seen_ref_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_reference(shard_src, sid or tid, norm_sk, ref_p, stamp, s_row.get('byte_len'))
        elif kind == 'script_call':
            sc_p = str(s_row.get('script_path') or '').strip()
            rel_sc = _normalize_skill_rel_path(sc_p, prefix='scripts') or sc_p
            if sid and (sid, norm_sk, rel_sc) in seen_script_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_script(shard_src, sid or tid, norm_sk, sc_p, stamp, s_row.get('exit_code'), s_row.get('duration_ms'))
    for record in session_records.values():
        if not record['loaded']:
            continue
        skill = record['skill']
        entry = _ensure_skill(skill)
        entry['session_count'] += 1
        entry['loads_30d'] += 1
        entry['loads_7d'] += record['stamp'] >= start7
        entry['total_tokens'] += record['tokens']
        entry['token_session_count'] += int(record['token_seen'])
        refs = record['refs']
        ref_count = len(refs) if record['reference_source'] else record['read_proxy']
        entry['reference_hit_count'] += len(refs)
        entry['reference_bytes'] += sum(refs.values())
        entry['l0_only_sessions' if ref_count == 0 else 'l1_selective_sessions' if ref_count <= 2 else 'l1_greedy_sessions'] += 1
        if record['scripts']:
            entry['script_touch_sessions'] += 1
            entry['script_execs'] += len(record['scripts'])
            entry['script_failures'] += sum((call[2] != 0 for call in record['scripts']))
            entry['script_durations_ms'].extend((call[3] for call in record['scripts']))
        if not refs and (not record['scripts']) and (record['edits'] == 0):
            entry['empty_load_sessions'] += 1
        if record['edits'] >= 4:
            entry['edit_churn_count'] += record['edits']
    s2_gate['eval_effect'] = {'interceptions_7d': eval_interceptions, 'escapes_7d': eval_escapes, 'interception_rate': round(eval_interceptions / (eval_interceptions + eval_escapes), 4) if eval_interceptions + eval_escapes else None, 'by_eval_case': eval_by_case}
    source_meta = _freshness(observed, warehouse_present, 30)
    source_as_of = source_meta['source_as_of']
    start7, end7 = _window_bounds()
    root_stamps = [stamp for stamp in observed if start7 <= stamp < end7]
    root_meta = _freshness(root_stamps, warehouse_present)
    s1_pins = _compute_s1_pin_drift(d_root)
    budget_passed = sum((1 for m in inventory.values() if m['budget_ok']))
    top_l0 = sorted(inventory.values(), key=lambda m: (-m['l0_lines'], m['skill']))[:3]
    top_l1 = sorted(inventory.values(), key=lambda m: (-m['max_l1_lines'], m['skill']))[:3]
    total_eval_cases = sum((m['eval_case_count'] for m in inventory.values()))
    weekly_eval_delta = sum((m['eval_case_weekly_delta'] for m in inventory.values()))
    s3_health = {'total_skills': len(inventory), 'audit_pass_count': budget_passed, 'audit_pass_rate': round(budget_passed / len(inventory), 4) if inventory else None, 'top3_l0_watermarks': [{'skill': m['skill'], 'l0_lines': m['l0_lines'], 'l0_cap': m['l0_cap']} for m in top_l0], 'top3_l1_watermarks': [{'skill': m['skill'], 'max_l1_lines': m['max_l1_lines'], 'l1_cap': m['l1_cap']} for m in top_l1], 'eval_case_total': total_eval_cases, 'eval_case_weekly_delta': weekly_eval_delta}
    s11_context, s12_tools, burn_proposals = _compute_s11_s12_context_and_tools(wh, magpie_usage=magpie_usage, copilot_db=cp, dsh_sessions=dsh_sessions, telemetry_shards_dir=shards_dir)
    for l2_sk, l2_map in l2_channels.items():
        if sum((int(v or 0) for v in l2_map.values())) > 0:
            _ensure_skill(l2_sk)
    skill_rows: list[dict[str, Any]] = []
    idle_skills_30d: list[str] = []
    for s_name, st in sorted(stats.items()):
        l2_ch_map = l2_channels.get(s_name, {})
        l2_sum_30d = sum((int(v or 0) for v in l2_ch_map.values()))
        loads = max(st['loads_30d'], st['loads_7d'], st['session_count'], l2_sum_30d)
        if loads == 0 and s_name in known_skills:
            idle_skills_30d.append(s_name)
        meta_inv = inventory.get(s_name, {})
        l0_lines = int(meta_inv.get('l0_lines') or 0)
        max_l1_lines = int(meta_inv.get('max_l1_lines') or 0)
        fm_tok_est = int(meta_inv.get('frontmatter_tokens_est') or 1)
        body_tok_est = int(meta_inv.get('body_tokens_est') or max(1, l0_lines * 12))
        r1_tot = st['r1_total']
        if r1_tot > 0:
            r1_pass_rate: float | None = round(st['r1_pass'] / r1_tot, 4)
        elif loads > 0:
            clean_loads = max(0, loads - st['impl_bug_escapes'] - st['spec_gap_escapes'] - min(loads, st['script_failures']) - (1 if st['edit_churn_count'] >= 4 else 0))
            r1_pass_rate = round(max(0.05, clean_loads / loads), 4)
        else:
            r1_pass_rate = None
        obs_script_calls_30d = sum((int(sc_info.get('calls') or 0) for sc_path, sc_info in per_skill_scripts.get(s_name, {}).items() if _is_valid_skill_script_rel_path(sc_path)))
        script_execs = max(st['script_execs'], obs_script_calls_30d)
        has_valid_scripts = bool(meta_inv.get('script_entries')) or bool(st['has_scripts'])
        if has_valid_scripts and loads > 0:
            practiced_sessions = min(loads, max(st['script_touch_sessions'], min(loads, script_execs)))
            script_ratio: float | None = round(practiced_sessions / loads, 4)
            read_only_unpracticed_rate: float | None = round(max(0.0, 1.0 - script_ratio), 4)
        elif not has_valid_scripts and loads > 0:
            script_ratio = None
            read_only_unpracticed_rate = 0.0
        else:
            script_ratio = None
            read_only_unpracticed_rate = None
        empty_load_rate = round(min(1.0, st['empty_load_sessions'] / loads), 4) if loads > 0 else None
        script_durations = [float(value) for value in st['script_durations_ms']]
        script_avg_ms = round(sum(script_durations) / len(script_durations), 2) if script_durations else None
        script_p95_ms = _p95(script_durations)
        f_tot = max(1, st['l0_only_sessions'] + st['l1_selective_sessions'] + st['l1_greedy_sessions'])
        l0_rate = round(st['l0_only_sessions'] / f_tot, 4) if loads > 0 else None
        l1_sel_rate = round(st['l1_selective_sessions'] / f_tot, 4) if loads > 0 else None
        l1_greedy_rate = round(st['l1_greedy_sessions'] / f_tot, 4) if loads > 0 else None
        if st['token_session_count'] > 0 and st['total_tokens'] > 0:
            avg_tokens: int | None = max(1, int(st['total_tokens'] / st['token_session_count']))
        elif loads > 0:
            avg_hit_ref_tokens = int(st['reference_bytes'] / max(1, st['reference_hit_count']) // 4) if st['reference_hit_count'] > 0 and st['reference_bytes'] > 0 else 0
            script_proxy_tokens = 320 if script_execs > 0 else 0
            avg_tokens = max(1, fm_tok_est + body_tok_est + avg_hit_ref_tokens + script_proxy_tokens + 1200)
        else:
            avg_tokens = None
        cache_b = per_skill_cache.get(s_name, {})
        tot_in_sk = int(cache_b.get('in') or 0)
        tot_cr_sk = int(cache_b.get('cache_read') or 0)
        sk_kv_cache_pct = round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1) if tot_in_sk + tot_cr_sk > 0 else s11_context.get('kv_cache_hit_pct')
        cache_bonus = float(sk_kv_cache_pct) / 100.0 * 0.25 if sk_kv_cache_pct is not None else 0.0
        script_bonus = min(0.2, 0.05 * min(4, script_execs)) if has_valid_scripts and script_execs > 0 else 0.0
        norm_tokens = min(2.0, max(0.2, avg_tokens / 10000.0)) if avg_tokens is not None else None
        norm_churn = min(2.0, st['edit_churn_count'] * 0.15 + st['retry_commands'] * 0.1)
        uncalled_penalty = (read_only_unpracticed_rate or 0.0) * 0.6 if has_valid_scripts and loads > 0 else 0.0
        escape_penalty = st['impl_bug_escapes'] * 0.2 + st['spec_gap_escapes'] * 0.15
        denom = max(0.25, 0.5 + norm_tokens + norm_churn + uncalled_penalty + escape_penalty) if norm_tokens is not None else None
        roi_score = round((r1_pass_rate + cache_bonus + script_bonus) / denom, 4) if loads > 0 and denom is not None and (r1_pass_rate is not None) else round(r1_pass_rate / denom, 4) if denom is not None and r1_pass_rate is not None else None
        rework_rounds = int(st.get('rework_rounds') or 0)
        repair_denom = max(1, r1_tot, loads)
        repair_numer = st['impl_bug_escapes'] + st['spec_gap_escapes'] + rework_rounds + (1 if st['edit_churn_count'] >= 4 else 0)
        post_invocation_repair_loop_rate = round(min(1.0, repair_numer / repair_denom), 4) if loads > 0 or r1_tot > 0 or st['edit_churn_count'] > 0 else None
        diagnoses: list[str] = []
        if has_valid_scripts and loads > 0 and ((read_only_unpracticed_rate or 0.0) >= 0.5):
            diagnoses.append(f'光读不练率 {(read_only_unpracticed_rate or 0.0) * 100:.0f}%（脚本未充分触发）')
        if st['impl_bug_escapes'] > 0:
            diagnoses.append(f"[IMPL_BUG] 漏网 {st['impl_bug_escapes']} 次（建议 Valve 1 下沉脚本检查）")
        if st['spec_gap_escapes'] > 0:
            diagnoses.append(f"[SPEC_GAP] 盲区 {st['spec_gap_escapes']} 次（建议 add-case --id auto 补齐抽屉）")
        if st['edit_churn_count'] >= 4:
            diagnoses.append(f"挂载后文件反复擦写 {st['edit_churn_count']} 次")
        if l1_greedy_rate is not None and l1_greedy_rate > 0.25:
            diagnoses.append(f'L1 全量暴读违规率 {l1_greedy_rate * 100:.0f}%')
        if (post_invocation_repair_loop_rate or 0) > 0.25 and (not diagnoses):
            diagnoses.append(f'调用后返工率 {post_invocation_repair_loop_rate * 100:.0f}% 超阈值')
        if (post_invocation_repair_loop_rate or 0) > 0.25 or st['impl_bug_escapes'] > 0 or st['spec_gap_escapes'] > 0 or (st['edit_churn_count'] >= 4):
            diagnostic_status = 'high_repair_loop'
        elif loads == 0 and observed and (l0_lines > 120 or max_l1_lines > 140 or (not meta_inv.get('budget_ok', True))):
            diagnostic_status = 'zero_trigger_bloat'
            diagnoses.append(f'30d 零触发且 L0/L1 体积偏大 (L0={l0_lines}, max_L1={max_l1_lines})')
        elif loads == 0 and s_name in known_skills:
            diagnostic_status = 'dormant'
        elif diagnoses:
            diagnostic_status = 'warn'
        else:
            diagnostic_status = 'healthy' if loads > 0 else 'unknown'
        kinds = populations.get(s_name, {})
        stamps = [stamp for values in kinds.values() for stamp in values]
        row_meta = _freshness(stamps, warehouse_present, 30)
        skill_rows.append({**row_meta, 'estimated': True, 'estimation_method': 'session/tool and goal-name skill attribution proxies', 'r1_freshness': _freshness(kinds.get('reviews', []), wh.is_file()), 'skill': s_name, 'loads_7d': st['loads_7d'], 'loads_30d': loads, 'r1_pass_rate': r1_pass_rate, 'r1_total': r1_tot, 'rework_rounds': rework_rounds, 'post_invocation_repair_loop_rate': post_invocation_repair_loop_rate, 'has_scripts': has_valid_scripts, 'script_execs': script_execs, 'script_failures': st['script_failures'], 'script_success_rate': round(max(0, script_execs - st['script_failures']) / script_execs, 4) if script_execs else None, 'script_touch_sessions': st['script_touch_sessions'], 'script_touch_rate': script_ratio, 'unpracticed_rate': read_only_unpracticed_rate, 'script_avg_ms': script_avg_ms, 'script_p95_ms': script_p95_ms, 'reference_hit_count': st['reference_hit_count'], 'reference_bytes': st['reference_bytes'], 'empty_load_sessions': st['empty_load_sessions'], 'empty_load_rate': empty_load_rate, 'eval_interceptions': st['eval_interceptions'], 'eval_escapes': st['eval_escapes'], 'l0_only_rate': l0_rate, 'l1_selective_rate': l1_sel_rate, 'l1_greedy_violation_rate': l1_greedy_rate, 'l0_only_sessions': st['l0_only_sessions'], 'l1_selective_sessions': st['l1_selective_sessions'], 'l1_greedy_sessions': st['l1_greedy_sessions'], 'avg_context_tokens': avg_tokens, 'edit_churn_count': st['edit_churn_count'], 'retry_commands': st['retry_commands'], 'impl_bug_escapes': st['impl_bug_escapes'], 'spec_gap_escapes': st['spec_gap_escapes'], 'roi_score': roi_score, 'diagnosis': '；'.join(diagnoses) if diagnoses else '健康达标' if loads > 0 else 'unknown / 观测不足', 'diagnostic_status': diagnostic_status, 'needs_optimization': bool(diagnoses) and (loads > 0 or r1_tot > 0 or st['edit_churn_count'] > 0 or (diagnostic_status == 'zero_trigger_bloat'))})
    active_rows = [r for r in skill_rows if r['loads_30d'] > 0 or r['r1_total'] > 0 or r['edit_churn_count'] > 0]
    actionable_rows = [r for r in skill_rows if r['needs_optimization']]
    low_roi_candidates = sorted(actionable_rows, key=lambda r: (r['roi_score'] is None, r['roi_score'] if r['roi_score'] is not None else float('inf'), -(r['post_invocation_repair_loop_rate'] or 0), -r['impl_bug_escapes'], -(r['unpracticed_rate'] or 0.0)))[:3]
    recommendations: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for cand in low_roi_candidates:
        sk = cand['skill']
        roi_label = f"{cand['roi_score']:.2f}" if cand['roi_score'] is not None else 'unknown'
        r1_label = f"{cand['r1_pass_rate'] * 100:.1f}%" if cand['r1_pass_rate'] is not None else 'unknown'
        rec_item = {'id': f'skill-roi-{sk}', 'ref': f'retro://skill-roi/{sk}', 'legacy_ref': f'skill-roi://{sk}', 'legacy_refs': [f'skill-roi://{sk}'], 'skill': sk, 'metric_id': f'skill-roi-{sk}', 'diagnostic_status': cand['diagnostic_status'], 'post_invocation_repair_loop_rate': cand['post_invocation_repair_loop_rate'], 'action': f'Run skill_contract.py add-case --skill {sk} --id auto and sink repeated repair checks into scripts/' if cand['diagnostic_status'] == 'high_repair_loop' else f'Audit {sk} L0/L1 budget and wire deterministic script triggers', 'scope': 'personal', 'title': f"[Skill ROI] 优化 {sk}（ROI={cand['roi_score']:.2f}，R1通过={cand['r1_pass_rate'] * 100:.0f}%）：{cand['diagnosis'][:36]}" if cand['roi_score'] is not None and cand['r1_pass_rate'] is not None else f"[Skill ROI] 补齐 {sk} 的观测覆盖：{cand['diagnosis'][:36]}", 'detail': f"skill={sk} | roi_score={roi_label} | r1_pass_rate={r1_label} | unpracticed_rate={_percent_label(cand['unpracticed_rate'])} | edit_churn={cand['edit_churn_count']} | impl_bug={cand['impl_bug_escapes']} | spec_gap={cand['spec_gap_escapes']} | diagnosis={cand['diagnosis']}", 'priority': 'P1', **{key: cand[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method')}}
        recommendations.append(rec_item)
        proposals.append({**rec_item, 'ref': rec_item['legacy_ref'], 'priority': 'high'})
    for bp in burn_proposals[:2]:
        bp_enriched = dict(bp)
        bp_enriched.setdefault('scope', 'personal')
        bp_enriched.setdefault('action', 'Add --head-limit / view_range or sink filtering into deterministic script')
        proposals.append(bp_enriched)
        if not any((r['id'] == bp_enriched['id'] for r in recommendations)):
            recommendations.append(bp_enriched)
    total_sample_size = max(len(root_stamps), int(s11_context['sample_size'] or 0))
    cov_status = 'healthy' if total_sample_size > 0 else 'unknown' if warehouse_present else 'missing'
    observed_as_of = [value for value in (root_meta['source_as_of'], s11_context['source_as_of']) if value]
    source_as_of = max(observed_as_of) if observed_as_of else None
    inventory_paths = [path for meta in inventory.values() for path in [Path(meta['path']) / 'SKILL.md', *(Path(meta['path']) / 'references').rglob('*.md'), *(Path(meta['path']) / 'references').rglob('*.json')]]
    s3_health.update(_snapshot_freshness(inventory_paths, len(inventory)))
    for key in ('top3_l0_watermarks', 'top3_l1_watermarks'):
        for row in s3_health[key]:
            row.update(_snapshot_freshness([Path(inventory[row['skill']]['path']) / 'SKILL.md'], 1))
    for p_item in proposals:
        p_item.setdefault('window_cst', '7d')
        p_item.setdefault('source_as_of', source_as_of)
        p_item.setdefault('sample_size', total_sample_size)
        p_item.setdefault('coverage_status', cov_status)
        p_item.setdefault('window_start', source_meta['window_start'])
        p_item.setdefault('window_end', source_meta['window_end'])
    skill_details = _collect_skill_details(inventory=inventory, skill_rows=skill_rows, per_skill_channels=per_skill_channels, l2_channels=l2_channels, per_skill_cache=per_skill_cache, s11_context=s11_context, per_skill_refs=per_skill_refs, per_skill_scripts=per_skill_scripts, per_skill_eval_cases=per_skill_eval_cases, warehouse_present=warehouse_present)
    try:
        mirror_skills_to_icloud(skill_details, icloud_sync_dir=icloud_sync_dir)
    except Exception:
        pass
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    auto_proposed = _queue_recommendations(proposals, prop_path) if auto_propose else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0
    report_payload = {'ok': True, 'generated_at': generated_at, 'query_id': 'retro/skill_roi_diagnostics', 'wall_clock_date': wall_clock_date, 'staleness_days': staleness_days, 'window_anchor': window_anchor, 'window_cst': '7d', 'window_start': root_meta['window_start'] or s11_context['window_start'], 'window_end': root_meta['window_end'] or s11_context['window_end'], 'source_as_of': source_as_of, 'sample_size': total_sample_size, 'coverage_status': cov_status, 'skill_details': skill_details, 'S1_pin_drift': s1_pins, 'S2_gate_catch': s2_gate, 'S3_skill_health': s3_health, 'S4_usage_and_r1': {**source_meta, 'active_skills_count': len(active_rows), 'idle_skills_30d': idle_skills_30d, 'empty_load_sessions': sum((int(r.get('empty_load_sessions') or 0) for r in active_rows)), 'empty_load_rate': round(sum((int(r.get('empty_load_sessions') or 0) for r in active_rows)) / max(1, sum((int(r.get('loads_30d') or 0) for r in active_rows))), 4) if active_rows else None, 'top_used_skills': sorted(skill_rows, key=lambda r: (-r['loads_30d'], r['skill']))[:8]}, 'S6_load_vs_script': [{**{key: r[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method')}, **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r['skill'], {}).get(kind, [])], warehouse_present, 30), 'skill': r['skill'], 'loads_30d': r['loads_30d'], 'script_execs': r['script_execs'], 'script_touch_sessions': r['script_touch_sessions'], 'script_touch_rate': r['script_touch_rate'], 'unpracticed_rate': r['unpracticed_rate'], 'script_success_rate': r['script_success_rate'], 'script_avg_ms': r['script_avg_ms'], 'script_p95_ms': r['script_p95_ms']} for r in active_rows if r['has_scripts']], 'S7_disclosure_funnel': [{**{key: r[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method')}, **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r['skill'], {}).get(kind, [])], warehouse_present, 30), 'skill': r['skill'], 'l0_only_rate': r['l0_only_rate'], 'l1_selective_rate': r['l1_selective_rate'], 'l1_greedy_violation_rate': r['l1_greedy_violation_rate'], 'l0_only_sessions': r['l0_only_sessions'], 'l1_selective_sessions': r['l1_selective_sessions'], 'l1_greedy_sessions': r['l1_greedy_sessions'], 'reference_hit_count': r['reference_hit_count'], 'reference_bytes': r['reference_bytes'], 'avg_context_tokens': r['avg_context_tokens']} for r in active_rows], 'S8_post_skill_churn': [{**{key: r[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method')}, **_freshness([stamp for kind in ('churn',) for stamp in populations.get(r['skill'], {}).get(kind, [])], warehouse_present, 30), 'skill': r['skill'], 'edit_churn_count': r['edit_churn_count'], 'retry_commands': r['retry_commands']} for r in active_rows if r['edit_churn_count'] > 0 or r['retry_commands'] > 0], 'S9_escape_attribution': [{**{key: r[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method')}, **_freshness([stamp for kind in ('reviews', 'events') for stamp in populations.get(r['skill'], {}).get(kind, [])], warehouse_present, 7), 'skill': r['skill'], 'impl_bug_escapes': r['impl_bug_escapes'], 'spec_gap_escapes': r['spec_gap_escapes'], 'eval_interceptions': r['eval_interceptions'], 'eval_escapes': r['eval_escapes']} for r in active_rows if r['impl_bug_escapes'] > 0 or r['spec_gap_escapes'] > 0 or r['eval_interceptions'] > 0 or (r['eval_escapes'] > 0)], 'S10_skill_roi_ranking': sorted(active_rows, key=lambda r: (r['roi_score'] is None, r['roi_score'] if r['roi_score'] is not None else float('inf'), r['skill'])), 'S11_context_composition': s11_context, 'S12_tool_context_burn': s12_tools, 'recommendations': recommendations, 'low_roi_proposals': proposals, 'auto_proposed': auto_proposed, 'proposals_written': proposals_written, 'proposals_skipped': proposals_skipped}
    if should_mirror_roi:
        _mirror_skills_roi_snapshot_to_icloud(report_payload, icloud_roi_path=effective_roi_path)
    return report_payload
