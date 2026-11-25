from __future__ import annotations
from .common import *

"""Brief Emitter implementation for the skill-retro engine."""

def _multi_angle_speed_diagnosis(script_rel: str, calls: Sequence[ScriptCallEvent]) -> dict[str, Any]:
    """Attribute script latency across external_wait, serial_queuing, nested_reruns, heavy_full_scan."""
    measured_durs = [float(c.duration_ms) for c in calls if c.measured and c.duration_ms is not None]
    p50_ms = _percentile(measured_durs, 50)
    p95_ms = _percentile(measured_durs, 95)
    max_ms = round(max(measured_durs), 2) if measured_durs else None
    per_session_counts: dict[str, int] = {}
    for c in calls:
        per_session_counts[c.session_id] = per_session_counts.get(c.session_id, 0) + 1
    repeated_sessions = {sid: cnt for sid, cnt in per_session_counts.items() if cnt >= 2}
    nested_rerun_calls = sum((cnt - 1 for cnt in repeated_sessions.values()))
    bname = Path(script_rel).name
    cmd_corpus = ' '.join((c.command for c in calls))
    is_external = any((hint in script_rel or hint in bname for hint in _EXTERNAL_WAIT_SCRIPT_HINTS))
    is_serial_suite = any((hint in script_rel or hint in cmd_corpus for hint in _SERIAL_RUNNER_HINTS))
    angles: list[str] = []
    primary_cause = 'heavy_full_scan'
    if is_external:
        primary_cause = 'external_wait'
        angles.append('external_wait: dominated by remote CI/API/daemon polling rather than local CPU')
    if is_serial_suite:
        if primary_cause == 'heavy_full_scan':
            primary_cause = 'serial_queuing'
        angles.append('serial_queuing: sequential test/audit targets queued in a single process')
    if nested_rerun_calls >= 2:
        if primary_cause == 'heavy_full_scan':
            primary_cause = 'nested_reruns'
        angles.append(f'nested_reruns: {nested_rerun_calls} repeated invocations across {len(repeated_sessions)} session(s)')
    if not angles:
        angles.append('heavy_full_scan: full-history or unindexed scan without incremental watermark')
    speedup_plan = {'angle_a_path_scoped': 'Scope execution to `git diff --name-only` affected skills/tests on inner-loop edits.', 'angle_b_watermark_cache': 'Persist content-hash / session watermark so unchanged artifacts skip re-scanning.', 'angle_c_parallel_dag': 'Run independent unittest / static audit targets via concurrent worker pool.', 'angle_d_tiered_fast_fail': 'Run <0.5s CLI/contract checks in Tier 0 and move >30s suites/CI to async background.'}
    return {'primary_bottleneck': primary_cause, 'p50_ms': p50_ms, 'p95_ms': p95_ms, 'max_ms': max_ms, 'nested_rerun_calls': nested_rerun_calls, 'repeated_session_count': len(repeated_sessions), 'attribution_angles': angles, 'four_angle_speedup_options': speedup_plan}

def _normalize_aspects(raw_aspects: Sequence[str] | str | None, *, mode: str='full') -> list[str]:
    """Normalize aspect list into canonical subset of ('l0', 'l1', 'l2', 'l3', 'creative', 'eval')."""
    if (mode or 'full').strip().lower() == 'full' and (not raw_aspects):
        return list(CANONICAL_ASPECTS)
    tokens: list[str] = []
    if isinstance(raw_aspects, str):
        tokens = [t.strip().lower() for t in re.split('[,;\\s]+', raw_aspects) if t.strip()]
    elif isinstance(raw_aspects, Sequence):
        for item in raw_aspects:
            tokens.extend((t.strip().lower() for t in re.split('[,;\\s]+', str(item)) if t.strip()))
    if not tokens or 'all' in tokens:
        return list(CANONICAL_ASPECTS)
    resolved: list[str] = []
    for tok in tokens:
        mapped = _ASPECT_ALIASES.get(tok, tok)
        if mapped in CANONICAL_ASPECTS and mapped not in resolved:
            resolved.append(mapped)
    return resolved or list(CANONICAL_ASPECTS)

def discover_active_skill_leases(*, dev_root: Path | None=None, handoff_root: Path | None=None, known_skills: set[str] | None=None) -> dict[str, Any]:
    """Scan active worktrees, in_progress handoff ledgers, and pending cross-skill relays for Lock 2 exclusion."""
    d_root = dev_root or DEFAULT_DEV_ROOT
    h_root = handoff_root if handoff_root is not None else d_root / 'handoff'
    worktrees_dir = d_root / 'worktrees'
    leased: dict[str, list[str]] = {}
    pending_relay_targets: dict[str, list[str]] = {}

    def _mark(sk: str, reason: str) -> None:
        norm = _norm_skill(sk)
        if not norm or (known_skills and norm not in known_skills):
            return
        bucket = leased.setdefault(norm, [])
        if reason not in bucket:
            bucket.append(reason)
    if _probe_readable_dir(worktrees_dir):
        with contextlib.suppress(OSError):
            for child in _safe_iterdir(worktrees_dir):
                if not _safe_is_dir(child) or child.name.startswith('.'):
                    continue
                wt_name = child.name.lower()
                for sk in sorted(known_skills or set()):
                    if re.search(f'(?<![a-z0-9]){re.escape(sk.lower())}(?![a-z0-9])', wt_name):
                        _mark(sk, f'active_worktree:{child.name}')
    if _probe_readable_dir(h_root):
        with contextlib.suppress(OSError):
            for goal_dir in _safe_iterdir(h_root):
                if not _safe_is_dir(goal_dir) or goal_dir.name.startswith('_'):
                    continue
                ledger_p = goal_dir / 'ledger.md'
                if _safe_is_file(ledger_p):
                    l_txt = _safe_read_text(ledger_p)
                    if 'in_progress' in l_txt and 'EVT=GOAL_DONE' not in l_txt:
                        for sk in sorted(known_skills or set()):
                            if f'skills/{sk}' in l_txt or f'skill-retro-{sk}' in goal_dir.name:
                                _mark(sk, f'in_progress_ledger:{goal_dir.name}')
                briefs_d = goal_dir / 'briefs'
                if _probe_readable_dir(briefs_d):
                    with contextlib.suppress(OSError):
                        for relay_f in _safe_glob(briefs_d, 'skill-retro-relay-*.json'):
                            with contextlib.suppress(OSError, json.JSONDecodeError):
                                raw_relay = _safe_read_text(relay_f)
                                if not raw_relay:
                                    continue
                                rdoc = json.loads(raw_relay)
                                if isinstance(rdoc, dict) and str(rdoc.get('status') or 'pending') == 'pending':
                                    to_sk = _norm_skill(rdoc.get('to_skill') or '')
                                    if to_sk:
                                        pending_relay_targets.setdefault(to_sk, []).append(str(relay_f))
                                        _mark(to_sk, f'pending_relay_target:{goal_dir.name}')
    return {'leased_skills': sorted(leased.keys()), 'lease_reasons': leased, 'pending_relay_targets': pending_relay_targets}

def emit_cross_skill_relay(*, from_skill: str, to_skill: str, requirement: str, emit_dir: Path, evidence_summary: str='', wave: int=2) -> dict[str, Any]:
    """Emit a Wave 2 Cross-Skill Relay Brief (Host -> Guest) so Host never edits Guest out-of-bounds."""
    host = _norm_skill(from_skill)
    guest = _norm_skill(to_skill)
    if not host or not guest:
        raise ValueError('Both --from (host skill) and --to (guest skill) are required for emit-relay.')
    if host == guest:
        raise ValueError('Host and Guest skill cannot be identical for a cross-skill relay.')
    briefs_dir = emit_dir / 'briefs' if emit_dir.name != 'briefs' else emit_dir
    briefs_dir.mkdir(parents=True, exist_ok=True)
    host_slug = re.sub('[^A-Za-z0-9_-]+', '-', host).strip('-')
    guest_slug = re.sub('[^A-Za-z0-9_-]+', '-', guest).strip('-')
    relay_id = f'relay-{host_slug}-to-{guest_slug}'
    md_path = briefs_dir / f'skill-retro-relay-{host_slug}-to-{guest_slug}-wave{wave}.md'
    json_path = briefs_dir / f'skill-retro-relay-{host_slug}-to-{guest_slug}-wave{wave}.json'
    req_clean = ' '.join(str(requirement or '').strip().split()) or f'Align `{guest}` interface consumed by `{host}`.'
    md_lines = [f'# Cross-Skill Relay Brief (Wave {wave}): `{host}` (Host) -> `{guest}` (Guest)', '', f'- **Relay ID**: `{relay_id}`', f'- **Wave**: `{wave}` (scheduled AFTER Wave 1 Host `{host}` merges — parallel anti-collision Lock 3)', f'- **Host Skill (Origin)**: `{host}` (strictly forbidden from editing `skills/{guest}/**` in Wave 1)', f'- **Guest Skill (Target Owner)**: `{guest}`', '- **Role Slot**: `writer` -> `reviewer` (`M1 TL -> Writer -> Reviewer`)', '- **Owned Paths**:', f'  - `skills/{guest}/**`', f"  - `scripts/test_{guest.replace('-', '_')}_*.py`", '- **Forbidden Shared Paths (Anti-Collision Lock 1)**:', '  - `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, and `skills/' + host + '/**`', '- **Interfaces**:', f'  - **Consumes**: Relay requirement from `{host}`: {req_clean}', f'  - **Produces**: Backward-compatible contract/CLI update in `skills/{guest}/**` + Positive & Counter-Case Replay', '', '## Relay Requirement & Evidence', f'- **Requirement**: {req_clean}']
    if evidence_summary:
        md_lines.append(f'- **Observed Evidence**: {evidence_summary}')
    md_lines.extend(['', '## Verification Commands', '```bash', f'python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {md_path}', f'python3 skills/skill-contract/scripts/skill_contract.py audit --skill {guest} --strict', f'python3 skills/skill-contract/scripts/skill_contract.py eval --skill {guest} --strict', './scripts/verify', '```'])
    md_path.write_text('\n'.join(md_lines).rstrip() + '\n', encoding='utf-8')
    payload = {'ok': True, 'schema': 'skill-retro-relay/v1', 'relay_id': relay_id, 'wave': wave, 'status': 'pending', 'from_skill': host, 'to_skill': guest, 'requirement': req_clean, 'evidence_summary': evidence_summary, 'owned_paths': [f'skills/{guest}/**', f"scripts/test_{guest.replace('-', '_')}_*.py"], 'relay_brief_md': str(md_path), 'relay_brief_json': str(json_path)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload

def _resolve_per_skill_goal_handoff_dir(emit_dir: Path, safe_slug: str) -> Path:
    """Resolve the isolated M1 Goal handoff directory (`<root>/skill-retro-<slug>`) for a single skill."""
    base = emit_dir.parent if emit_dir.name == 'briefs' else emit_dir
    goal_name = f'skill-retro-{safe_slug}'
    if base.name == goal_name:
        return base
    return base / goal_name

def build_m2_dispatch_plan(selected_briefs: Sequence[Mapping[str, Any]], *, handoff_root: Path, dev_root: Path | None=None) -> dict[str, Any]:
    """Build the 1-M2 -> K (0..3) Independent M1 TL parallel dispatch plan and final M2 rollup command."""
    resolved_dev_root = (dev_root or DEFAULT_DEV_ROOT).expanduser()
    resolved_handoff_root = handoff_root.parent if handoff_root.name == 'briefs' else handoff_root
    parallel_m1_tls: list[dict[str, Any]] = []
    seen_skills: set[str] = set()
    for item in selected_briefs:
        if not isinstance(item, Mapping):
            continue
        sk = _norm_skill(str(item.get('skill') or ''))
        if not sk or sk in seen_skills:
            continue
        seen_skills.add(sk)
        safe_slug = re.sub('[^A-Za-z0-9_-]+', '-', sk).strip('-') or 'skill'
        goal_id = str(item.get('goal_id') or f'skill-retro-{safe_slug}')
        tl_agent_name = str(item.get('m1_tl_agent_name') or f'skill-retro-{safe_slug}-goal-tl-r1')
        goal_handoff_dir = Path(str(item.get('goal_handoff_dir') or _resolve_per_skill_goal_handoff_dir(resolved_handoff_root, safe_slug)))
        spec_md = Path(str(item.get('spec_md') or goal_handoff_dir / 'spec.md'))
        ledger_md = Path(str(item.get('ledger_md') or goal_handoff_dir / 'ledger.md'))
        receipt_json = Path(str(item.get('m1_receipt_json') or goal_handoff_dir / 'receipts' / 'm1-tl-receipt.json'))
        worktree_dir = Path(str(item.get('worktree_dir') or resolved_dev_root / 'worktrees' / goal_id))
        mode_val = str(item.get('mode') or 'full')
        aspects_list = list(item.get('aspects') or CANONICAL_ASPECTS)
        aspects_csv = ','.join((str(a) for a in aspects_list))
        owned_paths = list(item.get('owned_paths') or [f'skills/{sk}/**', f"scripts/test_{sk.replace('-', '_')}_*.py"])
        dispatch_cmd = f'skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead --name {tl_agent_name} --kind claude'
        m1_boot_prompt = f'Load /team-lead and /skill-retro. You are the independent M1 TL ({tl_agent_name}) for single-skill Goal {goal_id} (1 Skill = 1 Independent M1 TL). Read {spec_md} and {ledger_md}. Create/use isolated worktree {worktree_dir}. Perform deep reasoning (`--mode {mode_val} --aspects {aspects_csv}`) for `{sk}`: if the signal is benign/healthy after deep investigation, write `status=HEALTHY_NO_ISSUES` to {receipt_json} and return to M2 with ZERO Writers/Reviewers spawned; if a real issue is confirmed, run Writer -> Reviewer strictly within {owned_paths}, pass Positive + Counter-Case Replay + `./scripts/verify`, merge PR, and write `status=FIXED_AND_MERGED` (or `CROSS_SKILL_RELAY`) to {receipt_json}.'
        parallel_m1_tls.append({'skill': sk, 'goal_id': goal_id, 'm1_tl_agent_name': tl_agent_name, 'role': 'team_lead', 'kind': 'claude', 'mode': mode_val, 'aspects': aspects_list, 'goal_handoff_dir': str(goal_handoff_dir), 'spec_md': str(spec_md), 'ledger_md': str(ledger_md), 'm1_receipt_json': str(receipt_json), 'worktree_dir': str(worktree_dir), 'owned_paths': owned_paths, 'herdr_dispatch_cmd': dispatch_cmd, 'm1_boot_prompt': m1_boot_prompt})
    k_count = len(parallel_m1_tls)
    skills_flag = f" --skills {','.join((x['skill'] for x in parallel_m1_tls))}" if parallel_m1_tls else ''
    rollup_cmd = f'python3 skills/skill-retro/scripts/skill_retro.py rollup --handoff-root {resolved_handoff_root}{skills_flag} --json'
    return {'schema': 'skill-retro-m2-dispatch-plan/v1', 'topology': '1_m2 -> 1_cheap_probe_worker -> 0..3_parallel_m1_tls -> 1_m2_rollup', 'deep_reasoning_owner': 'M1-TL (1 independent M1 TL per skill)', 'm2_coordinator_count': 1, 'probe_worker_count': 1, 'm1_tls_to_spawn': k_count, 'max_parallel_m1_tls': 3, 'honest_zero_to_three_cap': True, 'm1_healthy_early_exit_rule': "If an M1 TL's deep reasoning finds its assigned skill is benign/healthy, that M1 TL directly writes a HEALTHY_NO_ISSUES receipt and returns to M2 without spawning any Writer or Reviewer.", 'parallel_m1_dispatches': parallel_m1_tls, 'herdr_dispatch_commands': [x['herdr_dispatch_cmd'] for x in parallel_m1_tls], 'm2_rollup_cmd': rollup_cmd}

def emit_skill_retro_briefs(report: Mapping[str, Any], emit_dir: Path, *, skill_filter: str | None=None, mode: str='full', aspects: Sequence[str] | str | None=None, coupled_with: Sequence[str] | str | None=None, relays: Sequence[Mapping[str, Any]] | None=None) -> dict[str, Any]:
    """Generate per-skill isolated M1 Goal handoff dirs (`<root>/skill-retro-<skill>/spec.md`) + Writer & Reviewer briefs."""
    briefs_dir = emit_dir / 'briefs' if emit_dir.name != 'briefs' else emit_dir
    briefs_dir.mkdir(parents=True, exist_ok=True)
    handoff_root_base = emit_dir.parent if emit_dir.name == 'briefs' else emit_dir
    norm_mode = 'partial' if str(mode or 'full').strip().lower() == 'partial' else 'full'
    selected_aspects = _normalize_aspects(aspects, mode=norm_mode)
    if norm_mode == 'full':
        selected_aspects = list(CANONICAL_ASPECTS)
    coupled_skills: list[str] = []
    if isinstance(coupled_with, str):
        coupled_skills = [_norm_skill(s) for s in re.split('[,;\\s]+', coupled_with) if _norm_skill(s)]
    elif isinstance(coupled_with, Sequence):
        for item in coupled_with:
            for s in re.split('[,;\\s]+', str(item)):
                if _norm_skill(s):
                    coupled_skills.append(_norm_skill(s))
    norm_filter = _norm_skill(skill_filter) if skill_filter else ''
    l0 = report.get('layer0_direction_and_triggers') or {}
    l1 = report.get('layer1_drawer_metabolism') or {}
    l2 = report.get('layer2_script_efficacy_and_speed') or {}
    l3 = report.get('layer3_wild_patterns_and_eval') or {}
    cross_repo_health = report.get('cross_repo_gate_health') or l2.get('cross_repo_gate_health') or {}
    proposals = list(report.get('proposals') or [])
    obs_pool = list(report.get('observation_pool') or [])
    target_skills: set[str] = set()
    if norm_filter:
        target_skills.add(norm_filter)
    else:
        for p in proposals:
            sk = str(p.get('skill') or '').strip()
            if sk and sk != 'repo-gate':
                target_skills.add(sk)
        if not target_skills:
            for item in (l2.get('rigid_roadblocks') or []) + (l1.get('unwired_disconnected_drawers') or []):
                sk = str(item.get('skill') or '').strip()
                if sk and sk != 'repo-gate':
                    target_skills.add(sk)
    taste_eval = l3.get('taste_and_eval_diversity') or {}
    emitted: list[dict[str, Any]] = []
    emitted_relays: list[dict[str, Any]] = []
    wave1_tasks: list[dict[str, Any]] = []
    wave2_tasks: list[dict[str, Any]] = []
    for sk in sorted(target_skills):
        safe_slug = re.sub('[^A-Za-z0-9_-]+', '-', sk).strip('-') or 'skill'
        sk_triggers = [x for x in l0.get('trigger_misses') or [] if x.get('skill') == sk]
        sk_empty = [x for x in l0.get('zero_action_loads') or [] if x.get('skill') == sk]
        sk_drifts = [x for x in l0.get('directional_drifts') or [] if sk in (x.get('skills') or [])]
        sk_converged = [x for x in l0.get('healthy_r1_to_r2_convergences') or [] if sk in (x.get('skills') or [])]
        sk_hot = [x for x in l1.get('hot_drawers') or [] if x.get('skill') == sk]
        sk_ondemand = [x for x in l1.get('on_demand_correction_drawers') or [] if x.get('skill') == sk]
        sk_unwired = [x for x in l1.get('unwired_disconnected_drawers') or [] if x.get('skill') == sk]
        sk_deadwood = [x for x in l1.get('true_zero_hit_deadwood') or [] if x.get('skill') == sk]
        sk_catchers = [x for x in l2.get('true_catchers') or [] if x.get('skill') == sk]
        sk_ceremonial = [x for x in l2.get('ceremonial_rubber_stamps') or [] if x.get('skill') == sk]
        sk_roadblocks = [x for x in l2.get('rigid_roadblocks') or [] if x.get('skill') == sk]
        sk_sinks = [x for x in l2.get('latency_sinks') or [] if x.get('skill') in {sk, 'repo-gate'} or x.get('owner_skill') == sk]
        sk_wild = [x for x in l3.get('wild_patterns') or [] if x.get('candidate_skill') == sk]
        sk_proposals = [x for x in proposals if x.get('skill') == sk]
        sk_held = [x for x in obs_pool if x.get('skill') == sk]
        sk_cross_repo_findings = int(cross_repo_health.get('total_findings') or 0) if sk == 'repo-kit' else 0
        own_actionable_sinks = [x for x in sk_sinks if (x.get('skill') == sk or (sk == 'repo-kit' and x.get('skill') == 'repo-gate')) and (x.get('primary_bottleneck') != 'external_wait' or int(x.get('nested_rerun_calls') or 0) >= 1)]
        has_actionable_evidence = bool(sk_proposals or sk_triggers or sk_empty or sk_drifts or sk_unwired or sk_hot or sk_deadwood or sk_roadblocks or [x for x in sk_ceremonial if int(x.get('co_occurring_skill_escapes') or 0) > 0] or own_actionable_sinks or (sk_cross_repo_findings > 0) or sk_wild or relays)
        if not has_actionable_evidence:
            continue
        inventory_meta = (report.get('inventory_snapshot') or {}).get(sk, {})
        exact_script_paths = [f'skills/{sk}/{rel}' for rel in inventory_meta.get('script_entries') or []]
        exact_drift_items = list(inventory_meta.get('internal_doc_cli_drift') or [])
        exact_drift_items.extend(inventory_meta.get('internal_broken_refs') or [])
        evidence_samples: list[str] = []
        for roadblock in sk_roadblocks:
            for sample in roadblock.get('failing_command_samples') or []:
                evidence_samples.append(f'exit=2 | {_redact_user_text(str(sample))}')
        for call in sk_held:
            code = call.get('exit_code')
            command = call.get('command') or call.get('script') or call.get('path')
            if code == 1:
                evidence_samples.append(f'exit=1 | {_redact_user_text(str(command))}')
            if call.get('aci_output_bloat') or int(call.get('output_chars') or 0) > ACI_OUTPUT_BLOAT_CHARS:
                evidence_samples.append(f'>20KB bloat | {_redact_user_text(str(command))}')
        if not evidence_samples:
            evidence_samples = [f'exit=2 | python3 skills/{sk}/scripts/<script>.py --help --unexpected-flag', f'exit=1 | python3 skills/{sk}/scripts/<script>.py <observed-input>', f'>20KB bloat | python3 skills/{sk}/scripts/<script>.py --summary']
        evidence_samples = list(dict.fromkeys(evidence_samples))[:10]
        owned_paths = [f'skills/{sk}/**', f"scripts/test_{sk.replace('-', '_')}_*.py", 'scripts/test_skill_retro.py']
        coupled_for_sk = [g for g in coupled_skills if g != sk]
        for guest_sk in coupled_for_sk:
            owned_paths.append(f'skills/{guest_sk}/**')
            owned_paths.append(f"scripts/test_{guest_sk.replace('-', '_')}_*.py")
        m1_path = briefs_dir / f'skill-retro-{safe_slug}-m1-tl.md'
        writer_path = briefs_dir / f'skill-retro-{safe_slug}-writer.md'
        reviewer_path = briefs_dir / f'skill-retro-{safe_slug}-reviewer.md'
        aspects_csv = ','.join(selected_aspects)
        goal_id = f'skill-retro-{safe_slug}'
        m1_tl_agent_name = f'skill-retro-{safe_slug}-goal-tl-r1'
        goal_handoff_dir = _resolve_per_skill_goal_handoff_dir(handoff_root_base, safe_slug)
        goal_briefs_dir = goal_handoff_dir / 'briefs'
        goal_receipts_dir = goal_handoff_dir / 'receipts'
        goal_briefs_dir.mkdir(parents=True, exist_ok=True)
        goal_receipts_dir.mkdir(parents=True, exist_ok=True)
        spec_path = goal_handoff_dir / 'spec.md'
        ledger_path = goal_handoff_dir / 'ledger.md'
        m1_receipt_path = goal_receipts_dir / 'm1-tl-receipt.json'
        worktree_dir = DEFAULT_DEV_ROOT / 'worktrees' / goal_id
        herdr_dispatch_cmd = f'skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead --name {m1_tl_agent_name} --kind claude'
        m1_lines = [f'# M1-TL Brief & Goal Spec (`{goal_id}`): `{sk}` — Stage 3 Independent M1 Deep Reasoning (`mode={norm_mode}`, `aspects={aspects_csv}`)', '', f'- **Goal ID**: `{goal_id}` (`1 Skill = 1 Independent M1 TL`)', f'- **M1 TL Agent**: `{m1_tl_agent_name}` (dispatched in parallel by the single M2 Coordinator)', f'- **Isolated Worktree**: `{worktree_dir}`', '- **Role Slot**: `M1-TL` (`team-lead` Steering -> `Writer` -> `Reviewer` only if issue confirmed; zero new agent role types)', f'- **Host Skill**: `{sk}`' + (f" (Locked Coupled Pair: `{', '.join(coupled_for_sk)}`)" if coupled_for_sk else ' (Strict Single-Skill Isolation)'), f'- **Check Mode & Aspects**: `--mode {norm_mode} --aspects {aspects_csv}`', '- **M1 Deep-Reasoning Early-Exit Rule (`HEALTHY_NO_ISSUES`)**:', f'  - M1 TL performs the deep reasoning & investigation for `{sk}` first.', f"  - If M1 TL's deep reasoning finds that the flagged signal is benign/healthy (e.g. normal `R1->R2` convergence), **M1 TL directly writes a `HEALTHY_NO_ISSUES` receipt to `{m1_receipt_path}` and returns to M2 WITHOUT spawning a Writer or Reviewer (`writers_spawned=0`)**.", '- **Anti-Collision Locks**:', f"  - **Lock 1 (Disjoint Owned Paths)**: `{', '.join(owned_paths)}` (NEVER edit `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, or uncoupled guest skills)", '  - **Lock 2 (Active Lease Guard)**: Verified not leased by another active worktree/ledger.', '  - **Lock 3 (Host/Guest Wave Relay)**: Any uncoupled Guest Skill requirement is emitted as a Wave 2 Relay Brief (`emit-relay`).', '- **Dispatch & Rollup Sequence**:', f'  1. Deep-reason `{sk}` in `{worktree_dir}` (`python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill {sk} --mode {norm_mode} --aspects {aspects_csv} --json`).', f'  2. If benign/healthy -> write `{m1_receipt_path}` (`status=HEALTHY_NO_ISSUES`, `writers_spawned=0`) and report back to the single M2.', f'  3. If real issue confirmed -> dispatch `Writer` with `{writer_path.name}`, run `python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {writer_path}`, dispatch `Reviewer` with `{reviewer_path.name}`, merge PR, and write `{m1_receipt_path}` (`status=FIXED_AND_MERGED` or `CROSS_SKILL_RELAY`).', '', '## Self-Contained Evidence (no skill-retro source reverse-engineering required)', '', '### Top 10 redacted failing command samples', *[f'- {sample}' for sample in evidence_samples], '', '### Exact SKILL.md <-> argparse drift items', *[f'- {_redact_user_text(str(item))}' for item in exact_drift_items or ['none observed in the bounded window']], '', '### Exact script paths', *[f'- `{path}`' for path in exact_script_paths or [f'skills/{sk}/scripts/<no-script-discovered>']], '', '## Execution Guardrail', '', '- M1 TL MUST NOT read `skills/skill-retro/scripts/**` to reverse-engineer scoring.', '- M1 TL MUST NOT inline pre-read full target script bodies before dispatching Writer.', '- Dispatch Writer via `herdr-dispatch.sh --role writer` (never inherit `group/steering`; pass an explicit coding model).', '- Expand absolute paths in every handoff and command; never use volatile `/tmp` relative paths.']
        m1_md = '\n'.join(m1_lines).rstrip() + '\n'
        m1_path.write_text(m1_md, encoding='utf-8')
        spec_path.write_text(m1_md, encoding='utf-8')
        if not ledger_path.is_file():
            ledger_lines = [f'# Goal Ledger: `{goal_id}`', '', f'- Mandate: Independent M1 TL deep reasoning & scoped evolution for `{sk}` (`mode={norm_mode}`, `aspects={aspects_csv}`)', '- Owner: M2-Dispatcher', f'- TL: {m1_tl_agent_name}', '- Repo: LeePepe/user-skills', '', '## Tasks', '', '| ID | Wave | Title | Assignee | Status | PR |', '|---|---|---|---|---|---|', f'| T1 | 1 | Deep-reason `{sk}` and either record HEALTHY_NO_ISSUES or run Writer -> Reviewer + merge PR | {m1_tl_agent_name} | pending | - |', '', '## Events', f'- {_cst_today_str()}T04:20:00+08:00 | EVT=GOAL_CREATED | goal={goal_id} | skill={sk} | from=m2-dispatcher | to={m1_tl_agent_name}']
            ledger_path.write_text('\n'.join(ledger_lines).rstrip() + '\n', encoding='utf-8')
        w_lines: list[str] = [f'# Writer Brief: `{sk}` — 6-Dimension Progressive Skill Self-Evolution (`mode={norm_mode}`, `aspects={aspects_csv}`)', '', '- **Role Slot**: `writer` (standard `M1 TL -> Writer -> Reviewer` pipeline; zero new agent role types)', f'- **Target Skill**: `{sk}` (strict per-skill isolation — do NOT edit sibling skills, shared root docs, or scheduler runners)', f'- **Check Mode & Aspects**: `mode={norm_mode}` · `aspects={aspects_csv}`', '- **Background**: Evolve this skill contract, drawers, and deterministic scripts from observed runtime telemetry using runtime progressive disclosure.', '- **Owned Paths**:']
        for op in owned_paths:
            w_lines.append(f'  - `{op}`')
        w_lines.extend(['- **Forbidden Shared / Guest Paths (Anti-Collision Lock 1 & Host/Guest Protocol)**:', '  - Never edit `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, or any uncoupled `skills/<guest>/**`.', f'  - If a Guest Skill contract change is required, run `python3 skills/skill-retro/scripts/skill_retro.py emit-relay --from {sk} --to <guest> --requirement "..." --emit-briefs {emit_dir}` for Wave 2.', '- **Interfaces**:', f'  - **Consumes**: `skills/{sk}/SKILL.md`, `skills/{sk}/references/*`, `skills/{sk}/scripts/*`, `python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill {sk} --mode {norm_mode} --aspects {aspects_csv} --json`', '  - **Produces**: Softened CLI compatibility aliases, self-disclosing error guidance (`<=3 lines + references/*.md` pointer), and Positive + Counter-Case Replay tests', '', f'## Observed Telemetry Evidence (`mode={norm_mode}`, `aspects={aspects_csv}`)', ''])
        if 'l0' in selected_aspects:
            w_lines.append('### Dim 1 (`l0`) — L0 Direction vs Healthy `R1->R2` Progressive Convergence')
            w_lines.append(f'- **Healthy `R1->R2` Convergences (Preserve, do NOT bloat L0)**: `{len(sk_converged)}` task(s) | **True Directional Drifts (`r3+` / `CHANGE_REQUEST`)**: `{len(sk_drifts)}` task(s)')
            for tm in sk_triggers:
                w_lines.append(f"- **L0 Trigger Miss**: missed `{tm['missed_sessions']}/{tm['domain_sessions']}` domain sessions (`miss_rate={tm['miss_rate']}`)")
            for ze in sk_empty:
                w_lines.append(f"- **L0 Zero-Action Load**: `{ze['zero_action_sessions']}/{ze['loads']}` loads had 0 actions")
            w_lines.append('')
        if 'l1' in selected_aspects:
            w_lines.append('### Dim 2 (`l1`) — Scenario-Aware Reference Drawer Metabolism (`hits` vs `scenario_sessions`)')
            if sk_unwired or sk_hot or sk_ondemand or sk_deadwood:
                for uw in sk_unwired:
                    w_lines.append(f"- **Unwired Disconnected Drawer**: `{uw['drawer']}` — `hits={uw['hits']}` vs `scenario_sessions={uw['scenario_sessions']}`, `domain_failures={uw['domain_failure_count']}` (wire self-disclosing pointer on script `exit=1`)")
                for hd in sk_hot:
                    w_lines.append(f"- **Hot Drawer**: `{hd['drawer']}` — `hit_sessions={hd['hit_sessions']}/{hd['scenario_sessions']}` (`{hd['scenario_hit_rate'] * 100:.0f}%`); sink repeated checklist lines into script")
                for od in sk_ondemand:
                    w_lines.append(f"- **On-Demand Correction Drawer (Healthy — Preserve)**: `{od['drawer']}` — `hit_sessions={od['hit_sessions']}/{od['scenario_sessions']}`")
                for dw in sk_deadwood:
                    w_lines.append(f"- **True Zero-Hit Deadwood**: `{dw['drawer']}` — `0` hits across `{dw['scenario_sessions']}` scenario sessions")
            else:
                w_lines.append('- No drawer anomalies recorded in current window (preserve low-frequency scenario docs with `scenario_sessions < 5`).')
            w_lines.append('')
        if 'l2' in selected_aspects:
            w_lines.append('### Dim 3 (`l2`) — Script Efficacy & 4-Angle Slow Script Deep-Dive')
            for rb in sk_roadblocks:
                w_lines.append(f"- **Rigid CLI Roadblock (`exit_code = 2`)**: `{rb['script']}` — `exit=2` count `{rb['exit_2_count']}` across `{rb['exit_2_sessions']}` session(s) (arg-only retry sessions: `{rb['arg_retry_sessions']}`)")
                for sample in rb.get('failing_command_samples') or []:
                    w_lines.append(f'  - Failing command sample: `{sample}`')
            for tc in sk_catchers:
                w_lines.append(f"- **True Catcher (Keep)**: `{tc['script']}` (`fail->pass` sessions=`{tc['fail_to_pass_sessions']}`, calls=`{tc['calls']}`)")
            for cs in sk_ceremonial:
                w_lines.append(f"- **Ceremonial Rubber-Stamp**: `{cs['script']}` (`100% exit=0` over `{cs['calls']}` runs, co-occurring skill escapes=`{cs['co_occurring_skill_escapes']}`)")
            for ls in sk_sinks:
                w_lines.append(f"- **Latency Sink (`>180s` / `P50>=30s`)**: `{ls['script']}` — `max_ms={ls['max_ms']}`, `p50_ms={ls['p50_ms']}`, `primary_bottleneck={ls['primary_bottleneck']}`, `nested_reruns={ls['nested_rerun_calls']}` ({'; '.join(ls.get('attribution_angles') or [])})")
                opts = ls.get('four_angle_speedup_options') or {}
                if opts:
                    w_lines.append(f"  - **4-Angle Speedup Options**: (a) `{opts.get('angle_a_path_scoped')}` | (b) `{opts.get('angle_b_watermark_cache')}` | (c) `{opts.get('angle_c_parallel_dag')}` | (d) `{opts.get('angle_d_tiered_fast_fail')}`")
            if sk == 'repo-kit' and cross_repo_health.get('available'):
                w_lines.append(f"- **Cross-Repo Gate Health (`repo_gate_auditor.py`)**: `repos_scanned={cross_repo_health.get('repos_scanned', 0)}`, `drifted_repos={cross_repo_health.get('drifted_repos_count', 0)}`, `avg_score={cross_repo_health.get('average_gate_health_score', 100.0)}`, `total_findings={cross_repo_health.get('total_findings', 0)}`")
            w_lines.append('')
        if 'l3' in selected_aspects:
            w_lines.append('### Dim 4 (`l3`) — Wild Pattern Soft Crystallization (Facts-Only, Self-Disclosing, Hard vs Soft Gate)')
            if sk_wild:
                for wp in sk_wild:
                    w_lines.append(f"- **Wild Manual Pattern (`>={WILD_PATTERN_MIN_COUNT}x`)**: `{wp['pattern_signature']}` (`{wp['occurrences']}` occurrences across `{wp['session_count']}` sessions; crystallize as facts-only `soft_advisory_exit_0` with self-disclosing error pointer)")
            else:
                w_lines.append('- No wild manual command pattern >=5x for this skill in the current window.')
            w_lines.append('')
        if 'creative' in selected_aspects:
            w_lines.append('### Dim 5 (`creative`) — Cross-Session Creative Spectrum (`Invariants + Product Persona + 2+1+1 Quota`)')
            w_lines.append(f"- **Preview Selections Observed**: `receipts={taste_eval.get('selection_receipt_count', 0)}`, `2+1+1_compliant_goals={taste_eval.get('spectrum_2_1_1_compliant_goals', 0)}`, `low_diversity_goals={taste_eval.get('low_diversity_goals', [])}`")
            w_lines.append('- **3-Tier Spectrum Contract**: Tier 1 Universal Invariants (alignment/hierarchy/platform firewall) + Tier 2 Product Persona Intent Weighting (primary visual anchor vs muted secondary budget) + Tier 3 Controlled Exploration (`2+1+1` quota: 2 persona-deepening + 1 cross-domain borrow + 1 bold wildcard).')
            w_lines.append('')
        if 'eval' in selected_aspects:
            flywheel_guard = l3.get('dual_flywheel_guard') or {}
            w_lines.append('### Dim 6 (`eval`) — 3-Tier Eval (`L1 Baseline / L2 Diversity / L3 Taste`) & Fast/Slow Flywheel Guardrails')
            w_lines.append(f"- **Flywheel Cohort Status**: `active_mode={flywheel_guard.get('active_mode', 'fast_flywheel_scope_triaged')}` (`observed_sessions={flywheel_guard.get('observed_sessions', 0)}`, `slow_flywheel_ready={flywheel_guard.get('slow_flywheel_ready', False)}`, `observation_pool_held={len(sk_held)}`)")
            w_lines.append('- **3-Tier Eval Audit**: Verify L1 Baseline deterministic assertions (`eval-cases.json`), L2 Variant Diversity & Intent Weighting, and L3 Human Taste (`jev-star` / `preview-selection.json`) backflow.')
            w_lines.append('')
        flywheel_guard = l3.get('dual_flywheel_guard') or {}
        w_lines.extend(['## Mandatory Execution Guardrails (Scope Triage + Self-Disclosing Errors + Dual-Case Replay)', '', f"1. **Scope Triage (Active Flywheel: `{flywheel_guard.get('active_mode', 'fast_flywheel_scope_triaged')}`)**:", '   - **Objective Bug / `exit=2` CLI Friction**: Fix global parser compatibility aliases & positional defaults.', '   - **Scenario-Specific Requirement**: Encode as a **scenario-conditioned branch** — NEVER mutate global defaults across unrelated scenarios!', f'   - **Observation Pool Hold (`{len(sk_held)}` held items)**: Do NOT over-fit ambiguous single-session anecdotes into L0.', '2. **Self-Disclosing Error Guidance**:', f'   - Silent when passing; when failing (`exit=1` or `exit=2`), print `<=3` concrete remediation lines plus the exact `skills/{sk}/references/<drawer>.md` path.', '   - Wild manual patterns (`Layer 3`) crystallize ONLY as data-collection helpers (`soft_advisory_exit_0`), never subjective aesthetic hard-locks.', '3. **Positive + Counter-Case Replay Verification**:', '   - Add a **Positive Replay Case** proving the historical failing command / defect is now caught or accepted.', '   - Add a **Counter-Case Replay** proving standard/neighboring scenarios and existing baseline behavior are NOT broken or over-constrained.', '', '## Verification Commands', '', '```bash', f'python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict', f'python3 skills/skill-contract/scripts/skill_contract.py eval --skill {sk} --strict', './scripts/verify', '```'])
        w_lines.extend(['', '## Self-Contained Evidence (redacted; do not inspect skill-retro implementation)', '', '### Top 10 redacted failing command samples', *[f'- {sample}' for sample in evidence_samples], '', '### Exact SKILL.md <-> argparse drift items', *[f'- {_redact_user_text(str(item))}' for item in exact_drift_items or ['none observed in the bounded window']], '', '### Exact `skills/<sk>/scripts/*.py` paths', *[f'- `{path}`' for path in exact_script_paths or [f'skills/{sk}/scripts/<no-script-discovered>']], '', '## Execution Guardrail', '', '- M1 TL MUST NOT read `skills/skill-retro/scripts/**` to reverse-engineer scoring.', '- M1 TL MUST NOT inline pre-read full target script bodies before dispatching Writer.', '- Dispatch Writer via `herdr-dispatch.sh --role writer` (never inherit `group/steering`; pass an explicit coding model).', '- Use fully expanded absolute paths in handoffs; never use volatile `/tmp` relative paths.'])
        writer_md = '\n'.join(w_lines).rstrip() + '\n'
        writer_path.write_text(writer_md, encoding='utf-8')
        if goal_briefs_dir.resolve() != briefs_dir.resolve():
            (goal_briefs_dir / m1_path.name).write_text(m1_md, encoding='utf-8')
            (goal_briefs_dir / writer_path.name).write_text(writer_md, encoding='utf-8')
        r_lines: list[str] = [f'# Reviewer Brief: `{sk}` — Progressive Evolution & Anti-Rigidity Gate (`mode={norm_mode}`, `aspects={aspects_csv}`)', '', '- **Role Slot**: `reviewer` (standard `M1 TL -> Writer -> Reviewer` cross-vendor V1–V5 review; zero new agent role types)', f'- **Skills to Load**: `code-review`, `skill-contract` (target skill: `{sk}`)', f'- **Check Mode & Aspects**: `mode={norm_mode}` · `aspects={aspects_csv}`', f'- **Companion Writer Brief**: `{writer_path.name}`', f"- **Targeted Proposals**: `{len(sk_proposals)}` actionable (`{', '.join((str(p.get('id')) for p in sk_proposals)) or 'baseline-audit'}`)", '', '## V1–V5 Review Checklist (Anti-Overcorrection, Host/Guest Isolation & Replay Proof)', '', '1. **Per-Skill Scope Isolation & Anti-Collision Lock 1 (`Axis 1`)**:', f"   - Confirm diff stays strictly within `{', '.join(owned_paths)}`; reject any edit to uncoupled Guest Skills, shared root files (`README.md`, `AGENTS.md`, `marketplace.json`), or scheduler/cron files (`feedback_guard.py pre-review-check`).", '   - Confirm `SKILL.md` and `references/*.md` pass `skill_contract.py audit --skill ' + sk + ' --strict` (L0/L1 line & byte caps, zero private identifiers).', '2. **Reject Global-Default Over-Correction (`Scope Triage Gate`)**:', '   - **FAIL (`[SPEC_GAP]` / `[IMPL_BUG]`)** if a scenario-specific preference was promoted into a global unconditional default instead of a scenario-conditioned branch.', '   - **FAIL** if low-occurrence scenario drawers (`scenario_sessions < 5`) were prematurely deleted as deadwood.', '3. **Reject Rigid Hardcoding & Opaque Errors (`Anti-Rigidity Gate`)**:', '   - **FAIL** if newly crystallized Layer 3 helpers hard-lock subjective choices (`exit=1`) instead of factual collection / soft advisory (`exit=0`).', f'   - Verify script error paths emit concise self-disclosing guidance pointing to `skills/{sk}/references/*.md`.', '4. **Verify Positive + Counter-Case Replay & Creative Spectrum (`Axis 2`)**:', '   - Verify the test suite executes BOTH a **Positive Case** (historical `exit=2` / escape fixed) AND a **Counter-Case** (neighboring scenario preserved without false-positive regression).', '   - For creative/design skills, verify `2+1+1` exploration quota and Product Persona Intent Weighting are preserved.', '', '## Mandatory Pre-Review & Audit Commands', '', '```bash', f'python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {writer_path}', f'python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict', f'python3 skills/skill-contract/scripts/skill_contract.py eval --skill {sk} --strict', '```']
        reviewer_md = '\n'.join(r_lines).rstrip() + '\n'
        reviewer_path.write_text(reviewer_md, encoding='utf-8')
        if goal_briefs_dir.resolve() != briefs_dir.resolve():
            (goal_briefs_dir / reviewer_path.name).write_text(reviewer_md, encoding='utf-8')
        brief_record = {'skill': sk, 'goal_id': goal_id, 'm1_tl_agent': m1_tl_agent_name, 'm1_tl_agent_name': m1_tl_agent_name, 'goal_handoff_dir': str(goal_handoff_dir), 'goal_spec_path': str(spec_path), 'goal_ledger_path': str(ledger_path), 'spec_md': str(spec_path), 'ledger_md': str(ledger_path), 'm1_receipt_json': str(m1_receipt_path), 'isolated_worktree': str(worktree_dir), 'worktree_dir': str(worktree_dir), 'm1_dispatch_cmd': herdr_dispatch_cmd, 'herdr_dispatch_cmd': herdr_dispatch_cmd, 'm1_outcomes_allowed': ['FIXED_AND_MERGED', 'HEALTHY_NO_ISSUES', 'CROSS_SKILL_RELAY'], 'wave': 1, 'mode': norm_mode, 'aspects': selected_aspects, 'coupled_with': coupled_for_sk, 'owned_paths': owned_paths, 'role_pipeline': ['M1-TL', 'writer', 'reviewer'], 'm1_tl_brief': str(m1_path), 'writer_brief': str(writer_path), 'reviewer_brief': str(reviewer_path), 'm1_tl_prompt': m1_md, 'writer_prompt': writer_md, 'reviewer_prompt': reviewer_md, 'proposal_ids': [p.get('id') for p in sk_proposals]}
        emitted.append(brief_record)
        wave1_tasks.append({'skill': sk, 'goal_id': goal_id, 'm1_tl_agent': m1_tl_agent_name, 'm1_tl_agent_name': m1_tl_agent_name, 'goal_handoff_dir': str(goal_handoff_dir), 'goal_spec_path': str(spec_path), 'spec_md': str(spec_path), 'isolated_worktree': str(worktree_dir), 'worktree_dir': str(worktree_dir), 'herdr_dispatch_cmd': herdr_dispatch_cmd, 'wave': 1, 'mode': norm_mode, 'aspects': selected_aspects, 'owned_paths': owned_paths})
    if relays:
        for rel_spec in relays:
            if not isinstance(rel_spec, Mapping):
                continue
            from_sk = _norm_skill(str(rel_spec.get('from_skill') or norm_filter or (sorted(target_skills)[0] if target_skills else '')))
            to_sk = _norm_skill(str(rel_spec.get('to_skill') or ''))
            req_txt = str(rel_spec.get('requirement') or '')
            if from_sk and to_sk and (from_sk != to_sk):
                rel_out = emit_cross_skill_relay(from_skill=from_sk, to_skill=to_sk, requirement=req_txt, emit_dir=emit_dir, evidence_summary=str(rel_spec.get('evidence_summary') or ''), wave=int(rel_spec.get('wave') or 2))
                emitted_relays.append(rel_out)
                wave2_tasks.append({'relay_id': rel_out['relay_id'], 'from_skill': from_sk, 'to_skill': to_sk, 'wave': rel_out['wave'], 'owned_paths': rel_out['owned_paths']})
    overall_status = 'ACTIONABLE' if emitted or emitted_relays else 'HEALTHY_NO_ISSUES'
    m2_dispatch_plan = build_m2_dispatch_plan(emitted, handoff_root=handoff_root_base)
    return {'briefs_dir': str(briefs_dir), 'handoff_root': str(handoff_root_base), 'status': overall_status, 'status_message': f'Emitted {len(emitted)} per-skill M1 Goal spec(s) & brief(s)' if overall_status == 'ACTIONABLE' else 'healthy / 0 issues found (no changes needed)', 'orchestration_topology': '1_M2 -> K_PARALLEL_M1_TLS (K=0..3) -> M2_ROLLUP', 'm1_goals_to_dispatch': len(emitted), 'm1_tls_to_spawn': len(emitted), 'writers_spawned': len(emitted), 'mode': norm_mode, 'aspects': selected_aspects, 'role_slots_reused': ['M1-TL', 'writer', 'reviewer'] if emitted else [], 'new_agent_roles_created': 0, 'count': len(emitted), 'briefs': emitted, 'm1_goal_specs': [{'skill': b['skill'], 'goal_id': b['goal_id'], 'm1_tl_agent': b['m1_tl_agent'], 'm1_tl_agent_name': b['m1_tl_agent_name'], 'goal_handoff_dir': b['goal_handoff_dir'], 'goal_spec_path': b['goal_spec_path'], 'goal_ledger_path': b['goal_ledger_path'], 'spec_md': b['spec_md'], 'ledger_md': b['ledger_md'], 'm1_receipt_json': b['m1_receipt_json'], 'isolated_worktree': b['isolated_worktree'], 'worktree_dir': b['worktree_dir'], 'm1_dispatch_cmd': b['m1_dispatch_cmd'], 'herdr_dispatch_cmd': b['herdr_dispatch_cmd'], 'm1_outcomes_allowed': b['m1_outcomes_allowed']} for b in emitted], 'm2_dispatch_plan': m2_dispatch_plan, 'cross_skill_relays': emitted_relays, 'wave_plan': {'wave_1_disjoint_hosts': wave1_tasks, 'wave_2_cross_skill_relays': wave2_tasks}}

def _deduplicate_session_script_calls(obs: SessionObservation) -> list[ScriptCallEvent]:
    """Chronologically sort and deduplicate adjacent cross-source mirrored calls per `(skill, script_rel)` without crossing an intervening pass, preserving rich completion/duration/version evidence."""
    if not obs.script_calls:
        return []
    dedup_by_validator: dict[tuple[str, str], list[ScriptCallEvent]] = {}
    retained: list[ScriptCallEvent] = []
    for c_ev in sorted(obs.script_calls, key=lambda c: (c.ts, c.seq)):
        v_key = (c_ev.skill, c_ev.script_rel)
        v_hist = dedup_by_validator.setdefault(v_key, [])
        if v_hist:
            last_v = v_hist[-1]
            if last_v.origin_source != c_ev.origin_source and last_v.exit_code == c_ev.exit_code and (abs((c_ev.ts - last_v.ts).total_seconds()) < 1.0):
                last_v.output_chars = max(int(last_v.output_chars or 0), int(c_ev.output_chars or 0))
                last_v.aci_output_bloat = bool(last_v.aci_output_bloat or c_ev.aci_output_bloat)
                if not last_v.measured and c_ev.measured and (c_ev.duration_ms is not None):
                    last_v.measured = True
                    last_v.duration_ms = c_ev.duration_ms
                if not last_v.skill_version and c_ev.skill_version:
                    last_v.skill_version = c_ev.skill_version
                if not last_v.subtree_sha and c_ev.subtree_sha:
                    last_v.subtree_sha = c_ev.subtree_sha
                if last_v.command == last_v.script_rel and c_ev.command and (c_ev.command != c_ev.script_rel):
                    last_v.command = c_ev.command
                continue
        v_hist.append(c_ev)
        retained.append(c_ev)
    obs.script_calls = retained
    return retained
