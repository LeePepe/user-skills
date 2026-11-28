from __future__ import annotations
from .common import *

"""Retro Engine implementation for the skill-retro engine."""

def compute_skill_roi(warehouse_db: Path | None=None, copilot_db: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, magpie_usage: Path | None=None, dsh_sessions: Path | None=None, telemetry_shards_dir: Path | None=None, *, auto_propose: bool=False, proposals_path: Path | None=None, icloud_sync_dir: Path | None=None, mirror_icloud_roi: bool | None=None, icloud_roi_path: Path | None=None) -> dict[str, Any]:
    """Alias for compute_skill_roi_diagnostics()."""
    report = compute_skill_roi_diagnostics(warehouse_db=warehouse_db, copilot_db=copilot_db, skills_root=skills_root, dev_root=dev_root, magpie_usage=magpie_usage, dsh_sessions=dsh_sessions, telemetry_shards_dir=telemetry_shards_dir, auto_propose=auto_propose, proposals_path=proposals_path, icloud_sync_dir=icloud_sync_dir, mirror_icloud_roi=mirror_icloud_roi, icloud_roi_path=icloud_roi_path)
    report.setdefault('generated_at', datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'))
    return report

def compute_skill_retro(warehouse_db: Path | None=None, copilot_db: Path | None=None, copilot_sessions_dir: Path | None=None, usage_jsonl: Path | None=None, trace_export_path: Path | None=None, handoff_root: Path | None=None, skills_root: Path | None=None, dev_root: Path | None=None, telemetry_shards_dir: Path | None=None, *, skill: str | None=None, emit_briefs_dir: Path | None=None, mode: str='full', aspects: Sequence[str] | str | None=None, coupled_with: Sequence[str] | str | None=None, relays: Sequence[Mapping[str, Any]] | None=None, days: int=30, auto_propose: bool=False, proposals_path: Path | None=None, icloud_sync_dir: Path | None=None, icloud_roi_path: Path | None=None, local_roi_path: Path | None=None, mirror_roi_snapshot: bool | None=None, isolated_fixtures: bool=False, limit_sessions: int | None=DEFAULT_MAX_COPILOT_SESSIONS_SCAN) -> dict[str, Any]:
    """Compute the 4-Layer / 6-Dimension Progressive Skill Evolution & Wide-Probe Multi-Angle Diagnostic."""
    explicit_source_override = bool(isolated_fixtures) or any((val is not None for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)))
    wh = warehouse_db if warehouse_db is not None else None if explicit_source_override or (limit_sessions is not None and DEFAULT_WAREHOUSE_DB == DEFAULT_AIDATA_DIR / 'L3_merge' / 'warehouse.db') else DEFAULT_WAREHOUSE_DB
    cp = copilot_db if copilot_db is not None else None if explicit_source_override or (limit_sessions is not None and DEFAULT_COPILOT_DB == Path.home() / '.copilot' / 'session-store.db') else DEFAULT_COPILOT_DB
    sess_dir = copilot_sessions_dir if copilot_sessions_dir is not None else None if explicit_source_override else DEFAULT_COPILOT_SESSIONS_DIR
    u_jsonl = usage_jsonl if usage_jsonl is not None else None if explicit_source_override or (limit_sessions is not None and DEFAULT_SKILL_TELEMETRY_USAGE_JSONL == DEFAULT_USAGE_JSONL) else DEFAULT_SKILL_TELEMETRY_USAGE_JSONL
    h_root = handoff_root if handoff_root is not None else None if explicit_source_override else DEFAULT_HANDOFF_ROOT
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    resolved_sync = _resolve_icloud_sync_dir(icloud_sync_dir) if icloud_sync_dir is not None else None
    shards_dir = telemetry_shards_dir if telemetry_shards_dir is not None else resolved_sync / 'telemetry-shards' if resolved_sync is not None else None
    inventory = _discover_skill_inventory(s_root, d_root, skill_filter=skill)
    report = compute_skill_retro_diagnostics(inventory=inventory, skill_filter=skill, emit_briefs_dir=emit_briefs_dir, mode=mode, aspects=aspects, coupled_with=coupled_with, relays=relays, warehouse_db=wh, copilot_db=cp, copilot_sessions_dir=sess_dir, usage_jsonl=u_jsonl, telemetry_shards_dir=shards_dir, trace_export_path=trace_export_path, handoff_root=h_root, skills_root=s_root, dev_root=d_root, days=days, shard_reader=_read_telemetry_shards, queue_fn=_queue_recommendations, auto_propose=auto_propose, proposals_path=prop_path, limit_sessions=limit_sessions)
    should_mirror = mirror_roi_snapshot if mirror_roi_snapshot is not None else icloud_roi_path is not None or local_roi_path is not None or (not explicit_source_override and not skill)
    if should_mirror:
        eff_icloud_roi = icloud_roi_path or (resolved_sync / 'jobs' / 'aidata-digest' / 'skills-roi.json' if resolved_sync is not None else None)
        eff_local_roi = local_roi_path or (DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH if not explicit_source_override else None)
        _merge_skill_retro_into_roi_snapshot(report, icloud_roi_path=eff_icloud_roi, local_snapshot_path=eff_local_roi)
    return report

def _usage_records(path: Path, *, telemetry_shards_dir: Path | None=None) -> list[dict[str, Any]]:
    start, end = _window_bounds()
    records: list[dict[str, Any]] = []
    seen_route_sigs: set[tuple[str, str, int, int]] = set()
    seen_fps: set[str] = set()
    if _is_safe_local_file(path):
        raw_usage = _safe_read_text(path)
        for number, line in enumerate(raw_usage.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'invalid usage JSON at {path}:{number}') from exc
            if not isinstance(rec, dict):
                raise ValueError(f'invalid usage record at {path}:{number}')
            stamp = next((_observed_at(rec[key]) for key in ('t', 'ts', 'timestamp', 'created_at', 'observed_at') if rec.get(key)), None)
            if stamp and start <= stamp < end:
                rec['_observed_at'] = stamp
                fp = _event_fingerprint(rec)
                seen_fps.add(fp)
                route_sig = (str(rec.get('route_id') or ''), str(rec.get('req') or rec.get('model') or ''), int(rec.get('in') or 0), int(rec.get('out') or 0))
                if route_sig[0]:
                    seen_route_sigs.add(route_sig)
                records.append(rec)
    for s_rec in _read_telemetry_shards(telemetry_shards_dir, days=7):
        kind = str(s_rec.get('kind') or '')
        if kind and kind not in {'gateway_usage', 'usage'}:
            continue
        fp = str(s_rec.get('event_id') or _event_fingerprint(s_rec))
        if fp in seen_fps:
            continue
        route_sig = (str(s_rec.get('route_id') or ''), str(s_rec.get('req') or s_rec.get('model') or ''), int(s_rec.get('in') or 0), int(s_rec.get('out') or 0))
        if route_sig[0] and route_sig in seen_route_sigs:
            continue
        seen_fps.add(fp)
        if route_sig[0]:
            seen_route_sigs.add(route_sig)
        records.append(s_rec)
    return records

@contextlib.contextmanager
def _proposal_lock(path: Path):
    sidecar = path.with_name(path.name + '.lock')
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open('a+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def _proposal_history(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'invalid proposal history JSON at {path}:{number}') from exc
            if not isinstance(rec, dict) or any((not str(rec.get(k) or '').strip() for k in ('id', 'ts', 'agent', 'title'))):
                raise ValueError(f'incomplete proposal history record at {path}:{number}')
            if _observed_at(rec['ts']) is None:
                raise ValueError(f'invalid proposal timestamp at {path}:{number}')
            records[str(rec['id'])] = rec
    return records

def _queue_recommendations(candidates: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    queued = []
    for candidate in candidates:
        result = route_retro_candidate(candidate, proposals_path=path, deduplicate_pending=True)
        if result['decision'] != 'already_pending':
            queued.append(result)
    return queued

def route_retro_candidate(candidate: dict[str, Any], mode: str='observe', starred: bool=False, proposals_path: Path | None=None, linear_runner: Callable[..., Any] | None=None, deduplicate_pending: bool=False) -> dict[str, Any]:
    cid = str(candidate.get('id') or '').strip()
    if not cid:
        raise ValueError('candidate id is required')
    title = str(candidate.get('title') or cid).strip()
    detail = str(candidate.get('detail') or title).strip()
    ref = str(candidate.get('ref') or f'retro://{cid}').strip()
    scope = str(candidate.get('scope') or 'personal').strip() or 'personal'
    norm_prio = _normalize_priority(candidate.get('priority'), default='high')
    proposal = {'id': cid, 'ts': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'), 'agent': str(candidate.get('agent') or 'retro-loop').strip() or 'retro-loop', 'title': title, 'detail': detail, 'priority': norm_prio, 'status': 'pending', 'ref': ref, 'scope': scope}
    for extra_key in ('layer', 'category', 'skill', 'skill_version', 'plain_reason', 'scope_triage', 'metric_id', 'action', 'legacy_ids', 'legacy_refs', 'legacy_ref', 'diagnostic_status', 'post_invocation_repair_loop_rate', 'app_name', 'event_kind', 'signature', 'stack_top_symbol', 'recommended_layer', 'verify_cmd', 'window_cst', 'window_start', 'window_end', 'estimated', 'estimation_method', 'source_as_of', 'sample_size', 'coverage_status', 'scope'):
        if extra_key in candidate:
            proposal[extra_key] = candidate[extra_key]
    proposal.setdefault('scope', 'personal')
    if any((not str(proposal[key] or '').strip() for key in ('id', 'ts', 'agent', 'title'))):
        raise ValueError('proposal requires nonempty id/ts/agent/title')
    if mode == 'observe' and (not starred):
        if proposals_path is None:
            raise ValueError('proposals_path is required for observe mode')
        with _proposal_lock(proposals_path):
            history = _proposal_history(proposals_path)
            ids = {proposal['id'], *candidate.get('legacy_ids', [])}
            refs = {proposal['ref'], *candidate.get('legacy_refs', []), candidate.get('legacy_ref', '')}
            refs.discard('')
            prior = next((row for row in history.values() if (row['id'] in ids or row.get('ref') in refs or ids.intersection(row.get('legacy_ids', [])) or refs.intersection(row.get('legacy_refs', []))) and row.get('status') in {'pending', 'approved', 'dismissed', 'transferred'}), None)
            if deduplicate_pending and prior:
                return {'decision': 'already_pending', 'promoted': False, 'proposal': prior}
            with proposals_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(proposal, ensure_ascii=False) + '\n')
        return {'decision': 'observe (queued_to_aidash)', 'promoted': False, 'proposal': proposal}
    if linear_runner is None:
        raise ValueError('linear_runner is required for promotion')
    linear_runner(proposal)
    proposal['status'] = 'approved'
    return {'decision': 'promoted_to_linear', 'promoted': True, 'proposal': proposal}

def _load_jev_client() -> Any | None:
    path = Path(__file__).resolve().parents[2] / 'team-lead' / 'scripts' / 'jev_client.py'
    if not path.is_file():
        return None
    with contextlib.suppress(Exception):
        spec = importlib.util.spec_from_file_location('skill_retro_jev_client', path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None

def _load_repo_gate_auditor(skills_root: Path | None=None, dev_root: Path | None=None) -> Any | None:
    candidates: list[Path] = []
    if skills_root is not None:
        candidates.append(skills_root / 'repo-kit' / 'scripts' / 'repo_gate_auditor.py')
    candidates.append(Path(__file__).resolve().parents[2] / 'repo-kit' / 'scripts' / 'repo_gate_auditor.py')
    if dev_root is not None:
        candidates.append(dev_root / 'Skills' / 'skills' / 'repo' / 'repo-kit' / 'scripts' / 'repo_gate_auditor.py')
    for cand in candidates:
        if _safe_is_file(cand):
            with contextlib.suppress(Exception):
                spec = importlib.util.spec_from_file_location('skill_retro_repo_gate_auditor', cand)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
    return None

def _compute_cross_repo_gate_health(*, skills_root: Path | None=None, dev_root: Path | None=None) -> dict[str, Any]:
    mod = _load_repo_gate_auditor(skills_root=skills_root, dev_root=dev_root)
    eff_dev_root = dev_root or DEFAULT_DEV_ROOT
    if mod is None or not hasattr(mod, 'scan_repositories'):
        return {'available': False, 'repos_scanned': 0, 'healthy_repos_count': 0, 'drifted_repos_count': 0, 'average_gate_health_score': 100.0, 'total_findings': 0, 'rule_counts': {}, 'speedup_4_angles': {}, 'repos': [], 'actionable_proposals': []}
    with contextlib.suppress(Exception):
        scan_out = mod.scan_repositories(dev_root=eff_dev_root, flow_map=True)
        if isinstance(scan_out, Mapping):
            return {'available': True, **dict(scan_out)}
    return {'available': False, 'repos_scanned': 0, 'healthy_repos_count': 0, 'drifted_repos_count': 0, 'average_gate_health_score': 100.0, 'total_findings': 0, 'rule_counts': {}, 'speedup_4_angles': {}, 'repos': [], 'actionable_proposals': []}
