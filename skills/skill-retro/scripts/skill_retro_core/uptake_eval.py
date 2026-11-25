from __future__ import annotations
from .common import *

"""Uptake Eval implementation for the skill-retro engine."""

def _strip_source_comments(text: str, suffix: str='') -> str:
    """Strip C/JS/Swift block & line comments or Python/shell `#` comments before lexical regex scans."""
    ext = suffix.lower()
    if ext in {'.md', '.json', '.html', '.htm'}:
        return text
    cleaned = re.sub('/\\*[\\s\\S]*?\\*/', '', text)
    if ext in {'.py', '.sh', '.yaml', '.yml', '.toml'}:
        cleaned = re.sub('(?m)^\\s*#.*$', '', cleaned)
    else:
        cleaned = re.sub('(?m)//.*$', '', cleaned)
    return cleaned

def load_uptake_registry(uptake_map_path: Path | None=None) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """Load decoupled `skill_map` (`skill_id -> [check_id, ...]`) and atomic `checks` from `uptake_map.json`."""
    path = uptake_map_path or DEFAULT_UPTAKE_MAP_PATH
    if not _safe_is_file(path):
        return ({}, {})
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ({}, {})
    if not isinstance(raw, Mapping):
        return ({}, {})
    raw_skill_map = raw.get('skill_map') if isinstance(raw.get('skill_map'), Mapping) else raw
    skill_map: dict[str, list[str]] = {}
    if isinstance(raw_skill_map, Mapping):
        for k, v in raw_skill_map.items():
            if str(k).startswith('_') or str(k) in {'schema', 'version', 'updated', 'checks', 'skill_map'}:
                continue
            if isinstance(v, list):
                skill_map[_norm_skill(str(k))] = [str(item) for item in v if str(item).strip()]
    checks_by_id: dict[str, dict[str, Any]] = {}
    raw_checks = raw.get('checks')
    if isinstance(raw_checks, list):
        for entry in raw_checks:
            if isinstance(entry, Mapping) and entry.get('id'):
                cid = str(entry['id']).strip()
                checks_by_id[cid] = dict(entry)
    return (skill_map, checks_by_id)

def _discover_target_files(root: Path, globs: Sequence[str] | None) -> list[tuple[str, Path]]:
    """Return sorted `(rel_posix_path, abs_path)` matching any glob in `globs` under `root`."""
    if not _probe_readable_dir(root):
        return []
    patterns = [str(g).strip() for g in globs or ['**/*'] if str(g).strip()]
    if not patterns:
        patterns = ['**/*']
    matched: dict[str, Path] = {}
    for pattern in patterns:
        direct_candidate = root / pattern
        if '*' not in pattern and '?' not in pattern and ('[' not in pattern):
            if _safe_is_file(direct_candidate) or _safe_is_dir(direct_candidate):
                rel = direct_candidate.relative_to(root).as_posix()
                matched[rel] = direct_candidate
            continue
        for cand in _safe_glob(root, pattern, recursive='**' in pattern):
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                continue
            if any((part in _UPTAKE_SKIP_DIRS for part in Path(rel).parts)):
                continue
            matched[rel] = cand
    if not matched and (not all(('*' in p or '?' in p or '[' in p for p in patterns))):
        for cand in _safe_glob(root, '*', recursive=True):
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                continue
            if any((part in _UPTAKE_SKIP_DIRS for part in Path(rel).parts)):
                continue
            if any((fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) for pat in patterns)):
                matched[rel] = cand
    return sorted(matched.items(), key=lambda item: item[0])

def _run_single_uptake_check(check: Mapping[str, Any], target_dir: Path) -> dict[str, Any]:
    """Run one atomic check against `target_dir` and return a 4-state CheckResult dict."""
    cid = str(check.get('id') or '')
    category = str(check.get('category') or 'lexical')
    kind = str(check.get('kind') or 'text_any')
    severity = str(check.get('severity') or 'hard')
    remediation_drawer = str(check.get('remediation_drawer') or '')
    description = str(check.get('description') or '')
    file_globs_raw = check.get('file_glob')
    file_globs = [str(x) for x in file_globs_raw] if isinstance(file_globs_raw, list) else [str(file_globs_raw)] if file_globs_raw else ['**/*']

    def _make_res(status: str, passed: bool | None, evidence: str) -> dict[str, Any]:
        return {'id': cid, 'category': category, 'kind': kind, 'severity': severity, 'fatal': severity == 'hard', 'status': status, 'passed': passed, 'evidence': evidence, 'remediation_drawer': remediation_drawer, 'description': description}
    if not _probe_readable_dir(target_dir):
        return _make_res(UPTAKE_STATUS_UNAVAILABLE, None, f'target_dir unavailable: {target_dir}')
    if kind == 'path_exists':
        targets = check.get('target') or file_globs
        pat_list = [str(x) for x in (targets if isinstance(targets, list) else [targets])]
        hits = _discover_target_files(target_dir, pat_list)
        if hits:
            return _make_res(UPTAKE_STATUS_PASSED, True, f'found {hits[0][0]}')
        return _make_res(UPTAKE_STATUS_FAILED, False, f'no path matched any of {pat_list}')
    if kind == 'path_absent':
        targets = check.get('target') or file_globs
        pat_list = [str(x) for x in (targets if isinstance(targets, list) else [targets])]
        hits = _discover_target_files(target_dir, pat_list)
        if hits:
            return _make_res(UPTAKE_STATUS_FAILED, False, f'forbidden path exists: {hits[0][0]}')
        eng_globs = check.get('engagement_glob')
        if isinstance(eng_globs, list) and eng_globs:
            if not _discover_target_files(target_dir, [str(x) for x in eng_globs]):
                return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, 'engagement_glob precondition did not match any files')
        return _make_res(UPTAKE_STATUS_PASSED, True, f'no path matched forbidden {pat_list}')
    candidate_files = [(rel, p) for rel, p in _discover_target_files(target_dir, file_globs) if _safe_is_file(p)]
    if kind == 'python_ast':
        if not candidate_files:
            return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, f'no Python files matched {file_globs}')
        ast_rule = str(check.get('ast_rule') or 'has_main_or_argparse')
        parse_errors: list[str] = []
        matched_ast_files: list[str] = []
        py_candidates = [(rel, fpath) for rel, fpath in candidate_files if fpath.suffix.lower() == '.py']
        non_test_py = [(rel, fpath) for rel, fpath in py_candidates if not fpath.name.startswith('test_') and (not fpath.name.endswith('_test.py'))]
        ordered_py = (non_test_py if non_test_py else py_candidates)[:6]
        for rel, fpath in ordered_py:
            try:
                src = fpath.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError) as exc:
                parse_errors.append(f'{rel}: {exc}')
                continue
            try:
                tree = ast.parse(src, filename=rel)
            except SyntaxError as exc:
                return _make_res(UPTAKE_STATUS_FAILED, False, f'{rel}: SyntaxError line {exc.lineno}: {exc.msg}')
            if ast_rule == 'has_main_or_argparse':
                has_main = '_CANONICAL_PATH' in src or any((isinstance(node, ast.FunctionDef) and node.name in {'main', 'parser', 'cli'} or (isinstance(node, ast.Import) and any((alias.name == 'argparse' for alias in node.names))) or (isinstance(node, ast.ImportFrom) and node.module == 'argparse') for node in ast.walk(tree)))
                if has_main:
                    matched_ast_files.append(rel)
            else:
                matched_ast_files.append(rel)
        if parse_errors:
            return _make_res(UPTAKE_STATUS_UNAVAILABLE, None, f'could not read candidate file(s): {parse_errors[0]}')
        if matched_ast_files:
            return _make_res(UPTAKE_STATUS_PASSED, True, f'{matched_ast_files[0]}: AST verified ({ast_rule})')
        return _make_res(UPTAKE_STATUS_FAILED, False, f'no Python file satisfied AST rule {ast_rule!r}')
    file_texts: list[tuple[str, str, str]] = []
    for rel, fpath in candidate_files:
        try:
            raw_txt = fpath.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        file_texts.append((rel, raw_txt, _strip_source_comments(raw_txt, fpath.suffix)))

    def _engaged() -> bool:
        eng_any = check.get('engagement_any')
        if isinstance(eng_any, list) and eng_any:
            for pat_str in eng_any:
                rx = re.compile(str(pat_str), re.MULTILINE)
                if any((rx.search(stripped) for _, _, stripped in file_texts)):
                    return True
            return False
        eng_globs = check.get('engagement_glob')
        if isinstance(eng_globs, list) and eng_globs:
            return bool(_discover_target_files(target_dir, [str(x) for x in eng_globs]))
        return bool(file_texts)
    if kind == 'text_absent':
        raw_pat = check.get('must_not_match') or check.get('target') or ''
        pat_list = [str(x) for x in (raw_pat if isinstance(raw_pat, list) else [raw_pat]) if str(x)]
        for pat_str in pat_list:
            rx = re.compile(pat_str, re.MULTILINE)
            for rel, _, stripped in file_texts:
                if rx.search(stripped):
                    return _make_res(UPTAKE_STATUS_FAILED, False, f'{rel}: contains forbidden pattern {pat_str!r}')
        if not _engaged():
            return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, f'no engagement precondition matched for negative check {cid!r}')
        return _make_res(UPTAKE_STATUS_PASSED, True, f'checked {len(file_texts)} file(s); forbidden pattern absent')
    if not _engaged():
        return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, f'no target domain files engaged for {cid!r}')
    raw_pat = check.get('must_match') or check.get('target') or ''
    pat_list = [str(x) for x in (raw_pat if isinstance(raw_pat, list) else [raw_pat]) if str(x)]
    for pat_str in pat_list:
        rx = re.compile(pat_str, re.MULTILINE)
        for rel, raw_txt, stripped in file_texts:
            corpus = raw_txt if rel.endswith('SKILL.md') else stripped
            if rx.search(corpus):
                return _make_res(UPTAKE_STATUS_PASSED, True, f'{rel}: matches {pat_str!r}')
    return _make_res(UPTAKE_STATUS_FAILED, False, f'no file matched any of {pat_list!r}')

def score_trigger_quality(expected_skills: Sequence[str], triggered_skills: Sequence[str]) -> dict[str, Any]:
    """Compute Trigger Recall, Precision, and Exact Match (`expo/eval-harness` `trigger.ts` parity)."""
    expected: list[str] = []
    for s in expected_skills:
        ns = _norm_skill(s)
        if ns and ns not in expected:
            expected.append(ns)
    triggered: list[str] = []
    for s in triggered_skills:
        ns = _norm_skill(s)
        if ns and ns not in triggered:
            triggered.append(ns)
    expected_set = set(expected)
    triggered_set = set(triggered)
    matched = [s for s in expected if s in triggered_set]
    extra = [s for s in triggered if s not in expected_set]
    missing = [s for s in expected if s not in triggered_set]
    recall = 1.0 if not expected else round(len(matched) / len(expected), 4)
    precision = (1.0 if not expected else 0.0) if not triggered else round(len(matched) / len(triggered), 4)
    return {'expected_skills': expected, 'triggered_skills': triggered, 'matched_skills': matched, 'extra_skills': extra, 'missing_skills': missing, 'recall': recall, 'precision': precision, 'exact_match': expected_set == triggered_set, 'any_skill_triggered': len(triggered) > 0}

def evaluate_skill_uptake(*, skills: Sequence[str] | str | None=None, triggered_skills: Sequence[str] | str | None=None, target_dir: Path | None=None, scenario: str='skills_available_unmentioned', uptake_map_path: Path | None=None, baseline_evaluator_pct: float | None=None, skill_evaluator_pct: float | None=None, build_success: bool | None=None) -> dict[str, Any]:
    """Evaluate decoupled `uptake_map.json` checks and compute the `Trigger -> Context Uptake -> Outcome Delta` triad."""
    skill_map, checks_by_id = load_uptake_registry(uptake_map_path)
    norm_scenario = str(scenario or 'skills_available_unmentioned').strip()
    if norm_scenario not in VALID_SKILL_SCENARIOS:
        norm_scenario = 'skills_available_unmentioned'
    raw_expected: list[str] = []
    if isinstance(skills, str):
        raw_expected = [_norm_skill(x) for x in re.split('[,;\\s]+', skills) if _norm_skill(x)]
    elif isinstance(skills, Sequence):
        for item in skills:
            for x in re.split('[,;\\s]+', str(item)):
                if _norm_skill(x):
                    raw_expected.append(_norm_skill(x))
    else:
        raw_expected = sorted(skill_map.keys())
    expected_skills = [] if norm_scenario == 'skills_unavailable' else list(dict.fromkeys(raw_expected))
    if triggered_skills is None:
        trig_list = list(expected_skills)
    elif isinstance(triggered_skills, str):
        trig_list = [_norm_skill(x) for x in re.split('[,;\\s]+', triggered_skills) if _norm_skill(x)]
    else:
        trig_list = [_norm_skill(str(x)) for x in triggered_skills if _norm_skill(str(x))]
    eff_target_dir = (target_dir or DEFAULT_SKILLS_ROOT.parent).expanduser().resolve()
    warnings: list[str] = []
    pooled_check_ids: list[str] = []
    for sk in expected_skills:
        cids = skill_map.get(sk)
        if cids is None:
            warnings.append(f"no uptake checks mapped for skill '{sk}'")
            continue
        for cid in cids:
            if cid not in checks_by_id:
                warnings.append(f"uptake_map references unknown check id '{cid}'")
                continue
            if cid not in pooled_check_ids:
                pooled_check_ids.append(cid)
    results_by_id: dict[str, dict[str, Any]] = {}
    for cid in pooled_check_ids:
        results_by_id[cid] = _run_single_uptake_check(checks_by_id[cid], eff_target_dir)
    trig_quality = score_trigger_quality(expected_skills, trig_list)
    triggered_set = set(trig_quality['triggered_skills'])
    skills_out: dict[str, dict[str, Any]] = {}
    for sk in expected_skills:
        is_trig = sk in triggered_set
        cids = skill_map.get(sk)
        if cids is None:
            skills_out[sk] = {'expected': True, 'triggered': is_trig, 'trigger_status': 'observed' if is_trig else 'not_observed', 'uptake_status': 'unsupported', 'passed': None, 'total': None, 'uptake_rate': None, 'hard_failures': 0, 'soft_advisories': 0, 'checks': []}
            continue
        sk_checks = [results_by_id[cid] for cid in cids if cid in results_by_id]
        scored = [c for c in sk_checks if c['status'] in UPTAKE_SCORED_STATUSES]
        passed_cnt = sum((1 for c in scored if c['passed'] is True))
        hard_fails = sum((1 for c in scored if c['passed'] is False and c.get('severity') == 'hard'))
        soft_fails = sum((1 for c in scored if c['passed'] is False and c.get('severity') != 'hard'))
        has_unavail = any((c['status'] == UPTAKE_STATUS_UNAVAILABLE for c in sk_checks))
        uptake_status = UPTAKE_STATUS_UNAVAILABLE if has_unavail else 'measured' if scored else UPTAKE_STATUS_NOT_APPLICABLE if sk_checks else 'measured'
        skills_out[sk] = {'expected': True, 'triggered': is_trig, 'trigger_status': 'observed' if is_trig else 'not_observed', 'uptake_status': uptake_status, 'passed': passed_cnt, 'total': len(scored), 'uptake_rate': None if not scored else round(passed_cnt / len(scored), 4), 'hard_failures': hard_fails, 'soft_advisories': soft_fails, 'checks': sk_checks}
    all_pooled_results = [results_by_id[cid] for cid in pooled_check_ids]
    scored_pooled = [c for c in all_pooled_results if c['status'] in UPTAKE_SCORED_STATUSES]
    static_passed = sum((1 for c in scored_pooled if c['passed'] is True))
    static_total = len(scored_pooled)
    relevant_triggered = len(trig_quality['matched_skills']) > 0
    if relevant_triggered:
        context_uptake = {'passed': static_passed, 'total': static_total, 'uptake_rate': None if static_total == 0 else round(static_passed / static_total, 4), 'skipped_reason': None}
    else:
        context_uptake = {'passed': 0, 'total': static_total, 'uptake_rate': None, 'skipped_reason': 'relevant skill did not trigger' if expected_skills else 'skills_unavailable baseline scenario'}
    outcome_delta_val = round(float(skill_evaluator_pct) - float(baseline_evaluator_pct), 4) if skill_evaluator_pct is not None and baseline_evaluator_pct is not None else None
    hard_failed_checks = [c for c in scored_pooled if c['passed'] is False and c.get('severity') == 'hard']
    return {'ok': len(hard_failed_checks) == 0, 'schema': 'skill-uptake-eval/v1', 'scenario': norm_scenario, 'target_dir': str(eff_target_dir), 'expected_skills': expected_skills, 'warnings': warnings, 'score': {'trigger_quality': trig_quality, 'context_uptake': context_uptake, 'outcome_delta': {'baseline_evaluator_pct': baseline_evaluator_pct, 'skill_evaluator_pct': skill_evaluator_pct, 'outcome_delta': outcome_delta_val, 'build_success': build_success}}, 'skills': skills_out, 'static_checks': all_pooled_results}

def _fast_telemetry_signals(*, inv: Mapping[str, Mapping[str, Any]], warehouse_db: Path | None=None, usage_jsonl: Path | None=None, copilot_sessions_dir: Path | None=None, proposals_path: Path | None=None, roi_snapshot_path: Path | None=None, days: int=30) -> dict[str, dict[str, Any]]:
    """Collect sub-second lightweight telemetry signals from warehouse.db, recent events.jsonl, usage.jsonl, proposals.jsonl, and skills-roi.json."""
    out: dict[str, dict[str, Any]] = {sk: {'l0_trigger_misses': 0, 'l0_zero_action_loads': 0, 'l0_directional_drifts': 0, 'r1_to_r2_converged_count': 0, 'progressive_catch_count': 0, 'wasted_preread_sessions': 0, 'wasted_preread_rate': 0.0, 'aci_output_bloat_count': 0, 'l1_unwired_drawers': 0, 'l1_hot_drawers': 0, 'l1_true_deadwood': 0, 'l2_exit2_roadblocks': 0, 'l2_exit2_pre_fix': 0, 'l2_exit2_post_fix': 0, 'l2_leaky_rubber_stamps': 0, 'l2_latency_sinks': 0, 'l3_wild_occurrences': 0, 'domain_failures': 0, 'skill_escapes': 0, 'rubber_stamp_escapes': 0, 'p50_ms': None, 'p90_ms': None, 'p1_proposal_bonus': 0.0, 'ref_hit_drawers': set(), '_script_durations': [], '_script_exit_codes': {}} for sk in inv}
    now_cst = datetime.now(CST)
    window_start = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(1, days) - 1)
    window_end = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def _resolve_script_watermark(sk: str, raw_script_path: str | None) -> tuple[str | None, int | None]:
        meta = inv.get(sk) or {}
        cur_ver = str(meta.get('skill_version') or '').strip() or None
        wm_map = meta.get('script_watermarks') if isinstance(meta.get('script_watermarks'), Mapping) else {}
        sc_rel = ''
        if raw_script_path:
            m_sc = _SKILL_SCRIPT_RE.search(str(raw_script_path))
            sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else str(raw_script_path) if str(raw_script_path).startswith('scripts/') else f'scripts/{Path(str(raw_script_path)).name}'
        if isinstance(wm_map, Mapping) and sc_rel in wm_map:
            return (cur_ver, int(wm_map[sc_rel]))
        if meta.get('last_modified_epoch') is not None:
            return (cur_ver, int(meta['last_modified_epoch']))
        return (cur_ver, None)
    post_validator_events: dict[tuple[str, str, str], list[tuple[float, int, int, str]]] = {}
    wh_row_seq = 0
    wh = warehouse_db if warehouse_db is not None else DEFAULT_WAREHOUSE_DB
    if _safe_is_file(wh) and wh is not None:
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(wh) as conn:
            if conn is not None:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                tables = set()
            for sc_tbl in ('script_call', 'fact_skill_script_call'):
                if sc_tbl not in tables:
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{sc_tbl}")')}
                ts_col = next((c for c in ('ts', 'timestamp', 'observed_at', 'cst_day') if c in cols), None)
                p_col = next((c for c in ('script_path', 'path') if c in cols), None)
                ver_col = 'skill_version' if 'skill_version' in cols else None
                dur_col = 'duration_ms' if 'duration_ms' in cols else None
                sid_col = next((c for c in ('session_id', 'turn_uuid') if c in cols), None)
                wh_pre2: dict[str, int] = {}
                wh_post2: dict[str, int] = {}
                sel_cols = ['skill', 'exit_code']
                for opt_c in (ts_col, p_col, ver_col, dur_col, sid_col):
                    if opt_c:
                        sel_cols.append(f'"{opt_c}"')
                for row_vals in conn.execute(f'''SELECT {', '.join(sel_cols)} FROM "{sc_tbl}"'''):
                    wh_row_seq += 1
                    row_map = dict(zip([c.strip('"') for c in sel_cols], row_vals))
                    sk = _norm_skill(str(row_map.get('skill') or ''))
                    if sk not in out:
                        continue
                    ts_val = _parse_ts(row_map.get(ts_col)) if ts_col else None
                    if ts_val is not None and (not window_start <= ts_val < window_end):
                        continue
                    raw_p = str(row_map.get(p_col) or 'scripts/unknown.py') if p_col else 'scripts/unknown.py'
                    m_sc_p = _SKILL_SCRIPT_RE.search(raw_p)
                    sc_rel_key = f"scripts/{m_sc_p.group('script').lstrip('/')}" if m_sc_p else raw_p if raw_p.startswith('scripts/') else f'scripts/{Path(raw_p).name}'
                    if dur_col and row_map.get(dur_col) not in (None, ''):
                        with contextlib.suppress(TypeError, ValueError):
                            d_val = float(row_map[dur_col])
                            if d_val >= 0:
                                out[sk]['_script_durations'].append(d_val)
                    raw_ec = row_map.get('exit_code')
                    if raw_ec in (None, ''):
                        continue
                    ec = int(raw_ec)
                    out[sk]['_script_exit_codes'].setdefault(sc_rel_key, []).append(ec)
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, raw_p if p_col else None)
                    is_post = _is_call_post_fix(ts_val, str(row_map.get(ver_col) or '').strip() or None if ver_col else None, current_skill_version=cur_ver, watermark_epoch=wm_epoch)
                    sid_val = str(row_map.get(sid_col) or 'wh-default') if sid_col else 'wh-default'
                    if is_post and ec != 2:
                        ts_num = ts_val.timestamp() if ts_val is not None else float(wh_row_seq)
                        post_validator_events.setdefault((sk, sid_val, sc_rel_key), []).append((ts_num, wh_row_seq, ec, 'wh'))
                    if ec == 2:
                        if is_post:
                            wh_post2[sk] = wh_post2.get(sk, 0) + 1
                        else:
                            wh_pre2[sk] = wh_pre2.get(sk, 0) + 1
                for sk, cnt in wh_post2.items():
                    out[sk]['l2_exit2_roadblocks'] = max(out[sk]['l2_exit2_roadblocks'], cnt)
                    out[sk]['l2_exit2_post_fix'] = max(out[sk]['l2_exit2_post_fix'], cnt)
                for sk, cnt in wh_pre2.items():
                    out[sk]['l2_exit2_pre_fix'] = max(out[sk]['l2_exit2_pre_fix'], cnt)
            for ref_tbl in ('reference_hits', 'fact_skill_reference_hit'):
                if ref_tbl not in tables:
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{ref_tbl}")')}
                p_col = 'reference_path' if 'reference_path' in cols else 'ref_path' if 'ref_path' in cols else None
                if not p_col:
                    continue
                for raw_sk, raw_p in conn.execute(f'SELECT DISTINCT skill, "{p_col}" FROM "{ref_tbl}"'):
                    sk = _norm_skill(str(raw_sk or ''))
                    if sk not in out or not raw_p:
                        continue
                    norm_pair = _normalize_ref_rel(str(raw_p))
                    rel_ref = norm_pair[1] if norm_pair else str(raw_p) if str(raw_p).startswith('references/') else f'references/{Path(str(raw_p)).name}'
                    out[sk]['ref_hit_drawers'].add(rel_ref)
            converged_goal_task_skills: set[tuple[str, str, str]] = set()
            converged_goal_skills: set[tuple[str, str]] = set()
            if 'fact_gtt_task_run' in tables:
                t_cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_task_run")')]
                grouped_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for vals in conn.execute('SELECT * FROM "fact_gtt_task_run"'):
                    r_map = dict(zip(t_cols, vals))
                    ts_r = _parse_ts(r_map.get('ended_at') or r_map.get('started_at') or r_map.get('cst_day'))
                    if ts_r is not None and (not window_start <= ts_r < window_end):
                        continue
                    g_id = str(r_map.get('goal_id') or '')
                    t_id = str(r_map.get('task_id') or '')
                    if g_id and t_id:
                        grouped_runs.setdefault((g_id, t_id), []).append(r_map)
                for (g_id, t_id), runs in grouped_runs.items():
                    max_r = max((int(r.get('round_no') or 1) for r in runs))
                    passed_later = any((str(r.get('v5_verdict') or '').upper() in {'PASS', 'CONCERNS', 'WAIVED'} and int(r.get('round_no') or 1) >= 2 for r in runs))
                    still_failed_r3 = max_r >= 3
                    sk_set: set[str] = set()
                    for r in runs:
                        raw_su = r.get('skills_used')
                        if isinstance(raw_su, str) and raw_su.strip().startswith('['):
                            with contextlib.suppress(json.JSONDecodeError):
                                sk_set.update((_norm_skill(x) for x in json.loads(raw_su) if str(x).strip()))
                        elif raw_su:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(str(raw_su)):
                                tok = m_sk.group('ctx') or m_sk.group('path') or m_sk.group('call')
                                if tok:
                                    sk_set.add(_norm_skill(tok))
                    for sk in sk_set:
                        if sk not in out:
                            continue
                        if max_r == 2 and passed_later:
                            converged_goal_task_skills.add((g_id, t_id, sk))
                            if g_id:
                                converged_goal_skills.add((g_id, sk))
                            out[sk]['r1_to_r2_converged_count'] += 1
                        elif still_failed_r3:
                            out[sk]['l0_directional_drifts'] += 1
                            out[sk]['skill_escapes'] += 1
                            out[sk]['domain_failures'] += 1
            if 'fact_gtt_event' in tables:
                e_cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_event")')]
                for vals in conn.execute('SELECT * FROM "fact_gtt_event"'):
                    r_map = dict(zip(e_cols, vals))
                    ts_e = _parse_ts(r_map.get('ts') or r_map.get('observed_at') or r_map.get('cst_day'))
                    if ts_e is not None and (not window_start <= ts_e < window_end):
                        continue
                    evt = str(r_map.get('evt_type') or '').upper()
                    note = str(r_map.get('note_preview') or r_map.get('note') or '')
                    g_id = str(r_map.get('goal_id') or '')
                    t_id = str(r_map.get('task_id') or '')
                    sk_hits = {_norm_skill(m.group('ctx') or m.group('path') or m.group('call') or '') for m in _SKILL_CONTEXT_RE.finditer(note)}
                    sk_hits.discard('')
                    if not sk_hits and 'design' in g_id.lower():
                        sk_hits.add('my-designer')
                    for sk in sk_hits:
                        if sk not in out:
                            continue
                        if evt in {'CHANGE_REQUEST', 'NEED_OWNER_DECISION'}:
                            out[sk]['l0_directional_drifts'] += 1
                            out[sk]['skill_escapes'] += 1
                            out[sk]['domain_failures'] += 1
                        elif evt == 'REVIEW_FAIL':
                            if (g_id, t_id, sk) in converged_goal_task_skills or (not t_id and (g_id, sk) in converged_goal_skills):
                                continue
                            out[sk]['skill_escapes'] += 1
                            out[sk]['domain_failures'] += 1
    u_path = usage_jsonl if usage_jsonl is not None else DEFAULT_SKILL_TELEMETRY_USAGE_JSONL
    if _is_safe_local_file(u_path) and u_path is not None:
        u_pre_exit2: dict[str, int] = {}
        u_post_exit2: dict[str, int] = {}
        raw_u = _safe_read_text(u_path)
        for idx_u, line in enumerate(raw_u.splitlines(), 1):
            if '"script_call"' not in line and '"reference_hit"' not in line:
                continue
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                rec = json.loads(line)
                if not isinstance(rec, Mapping):
                    continue
                ts = _parse_ts(rec.get('ts') or rec.get('timestamp') or rec.get('observed_at'))
                if ts is not None and (not window_start <= ts < window_end):
                    continue
                sk = _norm_skill(str(rec.get('skill') or ''))
                if sk not in out:
                    continue
                kind = str(rec.get('kind') or '')
                if kind == 'script_call':
                    ec_raw = rec.get('exit_code')
                    if ec_raw not in (None, ''):
                        ec = int(ec_raw)
                        raw_sc = str(rec.get('script_path') or 'scripts/unknown.py')
                        m_sc_u = _SKILL_SCRIPT_RE.search(raw_sc)
                        sc_rel_u = f"scripts/{m_sc_u.group('script').lstrip('/')}" if m_sc_u else raw_sc if raw_sc.startswith('scripts/') else f'scripts/{Path(raw_sc).name}'
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, raw_sc)
                        is_post = _is_call_post_fix(ts, str(rec.get('skill_version') or '').strip() or None, current_skill_version=cur_ver, watermark_epoch=wm_epoch)
                        sid_u = str(rec.get('session_id') or 'usage-default').strip() or 'usage-default'
                        if is_post and ec != 2:
                            ts_num_u = ts.timestamp() if ts is not None else float(1000000 + idx_u)
                            post_validator_events.setdefault((sk, sid_u, sc_rel_u), []).append((ts_num_u, 1000000 + idx_u, ec, 'usage'))
                        if ec == 2:
                            if is_post:
                                u_post_exit2[sk] = u_post_exit2.get(sk, 0) + 1
                            else:
                                u_pre_exit2[sk] = u_pre_exit2.get(sk, 0) + 1
                elif kind == 'reference_hit':
                    rp = str(rec.get('reference_path') or '')
                    if rp:
                        norm_pair = _normalize_ref_rel(rp)
                        rel_ref = norm_pair[1] if norm_pair else rp if rp.startswith('references/') else f'references/{Path(rp).name}'
                        out[sk]['ref_hit_drawers'].add(rel_ref)
        for sk, cnt in u_post_exit2.items():
            out[sk]['l2_exit2_roadblocks'] = max(out[sk]['l2_exit2_roadblocks'], cnt)
            out[sk]['l2_exit2_post_fix'] = max(out[sk]['l2_exit2_post_fix'], cnt)
        for sk, cnt in u_pre_exit2.items():
            out[sk]['l2_exit2_pre_fix'] = max(out[sk]['l2_exit2_pre_fix'], cnt)
    roi_path = roi_snapshot_path if roi_snapshot_path is not None else DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH
    if _is_safe_local_file(roi_path) and roi_path is not None:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            raw_roi = _safe_read_text(roi_path)
            roi_doc = json.loads(raw_roi) if raw_roi else {}
            sr_doc = (roi_doc.get('skill_retro') if isinstance(roi_doc, Mapping) else None) or {}
            if isinstance(sr_doc, Mapping):
                snap_ts = _parse_ts(sr_doc.get('generated_at') or roi_doc.get('source_as_of'))
                l0 = sr_doc.get('layer0_direction_and_triggers') or {}
                l1 = sr_doc.get('layer1_drawer_metabolism') or {}
                l2 = sr_doc.get('layer2_script_efficacy_and_speed') or {}
                for tm in l0.get('trigger_misses') or []:
                    sk = _norm_skill(str(tm.get('skill') or ''))
                    if sk in out:
                        out[sk]['l0_trigger_misses'] = max(out[sk]['l0_trigger_misses'], int(tm.get('missed_sessions') or 0))
                for ze in l0.get('zero_action_loads') or []:
                    sk = _norm_skill(str(ze.get('skill') or ''))
                    if sk in out:
                        out[sk]['l0_zero_action_loads'] = max(out[sk]['l0_zero_action_loads'], int(ze.get('zero_action_sessions') or 0))
                for uw in l1.get('unwired_disconnected_drawers') or []:
                    sk = _norm_skill(str(uw.get('skill') or ''))
                    if sk in out:
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, None)
                        if _is_call_post_fix(snap_ts, None, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                            out[sk]['l1_unwired_drawers'] += 1
                            out[sk]['domain_failures'] = max(out[sk]['domain_failures'], int(uw.get('domain_failure_count') or 0))
                for rb in l2.get('rigid_roadblocks') or []:
                    sk = _norm_skill(str(rb.get('skill') or ''))
                    if sk in out:
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, str(rb.get('script') or ''))
                        if _is_call_post_fix(snap_ts, str(rb.get('skill_version') or '').strip() or None, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                            cnt_val = int(rb.get('post_fix') if rb.get('post_fix') is not None else rb.get('exit_2_count') or 0)
                            out[sk]['l2_exit2_roadblocks'] = max(out[sk]['l2_exit2_roadblocks'], cnt_val)
                            out[sk]['l2_exit2_post_fix'] = max(out[sk]['l2_exit2_post_fix'], cnt_val)
                        else:
                            out[sk]['l2_exit2_pre_fix'] = max(out[sk]['l2_exit2_pre_fix'], int(rb.get('exit_2_count') or 0))
                for idx_p, prop in enumerate(sr_doc.get('proposals') or []):
                    if not isinstance(prop, Mapping):
                        continue
                    sk = _norm_skill(str(prop.get('skill') or ''))
                    if sk not in out:
                        continue
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, None)
                    if not _is_call_post_fix(_parse_ts(prop.get('ts') or prop.get('source_as_of')) or snap_ts, str(prop.get('skill_version') or '').strip() or None, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                        continue
                    prio = str(prop.get('priority') or '').upper()
                    if prio in {'P0', 'P1', 'HIGH'}:
                        out[sk]['p1_proposal_bonus'] = max(out[sk]['p1_proposal_bonus'], round(8000.0 - idx_p * 1200.0, 2))
    p_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    if _is_safe_local_file(p_path) and p_path is not None:
        with contextlib.suppress(OSError):
            pending_idx = 0
            for line in _safe_read_text(p_path).splitlines():
                if not line.strip():
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(line)
                    if not isinstance(rec, Mapping):
                        continue
                    if str(rec.get('status') or 'pending').lower() != 'pending':
                        continue
                    if not str(rec.get('id') or '').startswith('skill-retro-'):
                        continue
                    sk = _norm_skill(str(rec.get('skill') or ''))
                    if sk not in out:
                        continue
                    cat = str(rec.get('category') or '')
                    detail = str(rec.get('detail') or '')
                    m_sc_detail = re.search('script=([^\\s|]+)', detail)
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, m_sc_detail.group(1) if m_sc_detail else None)
                    rec_ts = _parse_ts(rec.get('ts') or rec.get('source_as_of'))
                    if not _is_call_post_fix(rec_ts, str(rec.get('skill_version') or '').strip() or None, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                        if cat == 'rigid_roadblock':
                            m_e2_pre = re.search('exit_2_count=(\\d+)', detail)
                            if m_e2_pre:
                                out[sk]['l2_exit2_pre_fix'] = max(out[sk]['l2_exit2_pre_fix'], int(m_e2_pre.group(1)))
                        continue
                    if cat == 'unwired_disconnected_drawer':
                        out[sk]['l1_unwired_drawers'] = max(out[sk]['l1_unwired_drawers'], 1)
                        m_df = re.search('domain_failures=(\\d+)', detail)
                        if m_df:
                            out[sk]['domain_failures'] = max(out[sk]['domain_failures'], int(m_df.group(1)))
                    elif cat == 'rigid_roadblock':
                        m_e2 = re.search('exit_2_count=(\\d+)', detail)
                        if m_e2:
                            out[sk]['l2_exit2_roadblocks'] = max(out[sk]['l2_exit2_roadblocks'], int(m_e2.group(1)))
                            out[sk]['l2_exit2_post_fix'] = max(out[sk]['l2_exit2_post_fix'], int(m_e2.group(1)))
                    elif cat == 'trigger_miss':
                        m_tm = re.search('missed_sessions=(\\d+)', detail)
                        if m_tm:
                            out[sk]['l0_trigger_misses'] = max(out[sk]['l0_trigger_misses'], int(m_tm.group(1)))
                    prio = str(rec.get('priority') or '').upper()
                    if prio in {'P0', 'P1', 'HIGH'}:
                        out[sk]['p1_proposal_bonus'] = max(out[sk]['p1_proposal_bonus'], round(8000.0 - pending_idx * 1200.0, 2))
                        pending_idx += 1
    sampled_obs: dict[str, SessionObservation] = {}
    sess_dir = copilot_sessions_dir if copilot_sessions_dir is not None else DEFAULT_COPILOT_SESSIONS_DIR
    if _probe_readable_dir(sess_dir) and sess_dir is not None:
        basename_to_targets: dict[str, list[tuple[str, str]]] = {}
        for sk_n, meta_n in inv.items():
            for rel_sc in meta_n.get('script_entries', []):
                basename_to_targets.setdefault(Path(str(rel_sc)).name, []).append((sk_n, str(rel_sc)))
        sampled_obs = _parse_copilot_sessions(sess_dir, window_start=window_start, window_end=window_end, known_skills=set(inv.keys()), basename_to_targets=basename_to_targets, max_sessions=12)
    non_sampled_validator_events: dict[tuple[str, str, str], list[tuple[float, int, int, str]]] = {}
    for (sk, sid_v, sc_rel_v), ev_items in post_validator_events.items():
        if sid_v in sampled_obs:
            obs_s = sampled_obs[sid_v]
            obs_s.skills_loaded.add(sk)
            for ts_num, _, ec_v, ev_src in sorted(ev_items, key=lambda item: (item[0], item[1])):
                ev_dt = datetime.fromtimestamp(ts_num, tz=timezone.utc).astimezone(CST)
                src_label = 'warehouse' if ev_src == 'wh' else 'usage_jsonl'
                obs_s.script_calls.append(ScriptCallEvent(session_id=sid_v, skill=sk, script_rel=sc_rel_v, command=sc_rel_v, ts=ev_dt, exit_code=ec_v, duration_ms=None, measured=False, seq=obs_s.next_seq(), origin_source=src_label))
        else:
            non_sampled_validator_events[sk, sid_v, sc_rel_v] = ev_items
    if sampled_obs:
        all_sampled = list(sampled_obs.values())
        for obs_s in all_sampled:
            _deduplicate_session_script_calls(obs_s)
        for sk_n in inv:
            loaded_for_sk = [o for o in all_sampled if sk_n in o.skills_loaded or any((r_sk == sk_n for r_sk, _, _, _ in o.ref_reads)) or any((c.skill == sk_n for c in o.script_calls))]
            prog_catches, wasted_sids, unres_fails = _evaluate_session_progressive_and_preread(loaded_for_sk, sk_n)
            if prog_catches > 0:
                out[sk_n]['progressive_catch_count'] = max(int(out[sk_n].get('progressive_catch_count') or 0), prog_catches)
            if wasted_sids:
                out[sk_n]['wasted_preread_sessions'] = len(wasted_sids)
                out[sk_n]['wasted_preread_rate'] = round(len(wasted_sids) / max(1, len(loaded_for_sk)), 4)
            if unres_fails > 0:
                out[sk_n]['domain_failures'] += unres_fails
            for o in loaded_for_sk:
                for r_sk, r_rel, _, _ in o.ref_reads:
                    if r_sk == sk_n:
                        out[sk_n]['ref_hit_drawers'].add(r_rel)
                for c in o.script_calls:
                    if c.skill != sk_n:
                        continue
                    if c.aci_output_bloat or c.output_chars > ACI_OUTPUT_BLOAT_CHARS:
                        out[sk_n]['aci_output_bloat_count'] += 1
                    if c.measured and c.duration_ms is not None:
                        out[sk_n]['_script_durations'].append(float(c.duration_ms))
                    if c.exit_code is not None:
                        out[sk_n]['_script_exit_codes'].setdefault(c.script_rel, []).append(int(c.exit_code))
                        if c.exit_code == 2:
                            cur_ver, wm_epoch = _resolve_script_watermark(sk_n, c.script_rel)
                            if _is_call_post_fix(c.ts, c.skill_version, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                                out[sk_n]['l2_exit2_roadblocks'] = max(out[sk_n]['l2_exit2_roadblocks'], 1)
                                out[sk_n]['l2_exit2_post_fix'] = max(out[sk_n]['l2_exit2_post_fix'], 1)
    for (sk, _, _), ev_items in non_sampled_validator_events.items():
        ordered_evs = sorted(ev_items, key=lambda item: (item[0], item[1]))
        dedup_evs: list[tuple[float, int, str]] = []
        for ts_num, _, ec_v, ev_src in ordered_evs:
            if dedup_evs and dedup_evs[-1][2] != ev_src and (dedup_evs[-1][1] == ec_v) and (abs(ts_num - dedup_evs[-1][0]) < 1.0):
                continue
            dedup_evs.append((ts_num, ec_v, ev_src))
        dedup_ecs = [ec_v for _, ec_v, _ in dedup_evs]
        fail_indices = [i for i, ec_v in enumerate(dedup_ecs) if ec_v != 0]
        if not fail_indices:
            continue
        recovered_any = any((any((ec_later == 0 for ec_later in dedup_ecs[f_idx + 1:])) for f_idx in fail_indices))
        last_fail_idx = max(fail_indices)
        terminal_unresolved = not any((ec_later == 0 for ec_later in dedup_ecs[last_fail_idx + 1:]))
        if recovered_any:
            out[sk]['progressive_catch_count'] += 1
        if terminal_unresolved:
            out[sk]['domain_failures'] += 1
    for sk, s_data in out.items():
        durs = s_data.pop('_script_durations', [])
        sc_exits = s_data.pop('_script_exit_codes', {})
        if durs:
            s_data['p50_ms'] = _percentile(durs, 50)
            s_data['p90_ms'] = _percentile(durs, 90)
            if max(durs) >= LATENCY_SINK_MAX_MS or ((s_data['p50_ms'] or 0.0) >= LATENCY_SINK_P50_MS and len(durs) >= LATENCY_SINK_MIN_CALLS_FOR_P50):
                s_data['l2_latency_sinks'] = max(int(s_data.get('l2_latency_sinks') or 0), 1)
        sk_escapes = int(s_data.get('skill_escapes') or 0)
        leaky_cnt = 0
        for _, ec_list in sc_exits.items():
            if len(ec_list) >= 3 and all((c == 0 for c in ec_list)) and (sk_escapes > 0):
                leaky_cnt += 1
        if leaky_cnt > 0:
            s_data['l2_leaky_rubber_stamps'] = max(int(s_data.get('l2_leaky_rubber_stamps') or 0), leaky_cnt)
            s_data['rubber_stamp_escapes'] = sk_escapes
    return out
