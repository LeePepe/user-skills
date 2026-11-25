from __future__ import annotations
from .common import *

"""Receipts implementation for the skill-retro engine."""

def record_m1_retro_receipt(*, skill: str, status: str, handoff_root: Path | None=None, summary: str='', pr_url: str | None=None, pr_number: int | None=None, commit_sha: str | None=None, merge_sha: str | None=None, writers_spawned: int | None=None, reviewers_spawned: int | None=None, relay_to: str | None=None, relay_requirement: str='', mode: str='full', aspects: Sequence[str] | str | None=None, observation_notes: Sequence[str] | None=None) -> dict[str, Any]:
    """Write a canonical M1 TL deep-reasoning receipt (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) for M2 Rollup."""
    norm_sk = _norm_skill(skill)
    if not norm_sk:
        raise ValueError('--skill is required to record an M1 retro receipt.')
    norm_status = str(status or '').strip().upper()
    canonical_statuses = {'FIXED_AND_MERGED', 'HEALTHY_NO_ISSUES', 'CROSS_SKILL_RELAY'}
    if norm_status not in canonical_statuses:
        raise ValueError(f'Invalid M1 receipt status {status!r}; must be one of {sorted(canonical_statuses)}.')
    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    safe_slug = re.sub('[^A-Za-z0-9_-]+', '-', norm_sk).strip('-') or 'skill'
    goal_dir = _resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug)
    receipts_dir = goal_dir / 'receipts'
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / 'm1-tl-receipt.json'
    norm_mode = 'partial' if str(mode or 'full').strip().lower() == 'partial' else 'full'
    aspect_list = _normalize_aspects(aspects, mode=norm_mode)
    if norm_status == 'HEALTHY_NO_ISSUES':
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f'M1 TL deep reasoning confirmed `{norm_sk}` signal is healthy/benign (0 Writers/Reviewers spawned).'
    elif norm_status == 'CROSS_SKILL_RELAY':
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL emitted Wave 2 Cross-Skill Relay from `{norm_sk}` to `{_norm_skill(relay_to or '')}`."
    else:
        eff_writers = 1 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 1 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f'M1 TL fixed `{norm_sk}` via Writer -> Reviewer, passed Positive + Counter-Case Replay, and merged PR.'
    eff_sha = commit_sha or merge_sha
    payload: dict[str, Any] = {'ok': True, 'schema': 'skill-retro-m1-receipt/v1', 'skill': norm_sk, 'goal_id': f'skill-retro-{safe_slug}', 'm1_tl_agent_name': f'skill-retro-{safe_slug}-goal-tl-r1', 'status': norm_status, 'mode': norm_mode, 'aspects': aspect_list, 'writers_spawned': eff_writers, 'reviewers_spawned': eff_reviewers, 'pr_url': pr_url, 'pr_number': pr_number, 'commit_sha': eff_sha, 'merge_sha': merge_sha or eff_sha, 'relay_to': _norm_skill(relay_to) if relay_to else None, 'relay_requirement': relay_requirement, 'summary': summary.strip() or default_summary, 'observation_notes': list(observation_notes or []), 'recorded_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'), 'receipt_path': str(receipt_path)}
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload

def rollup_m1_retro_receipts(*, handoff_root: Path | None=None, skills: Sequence[str] | str | None=None, observation_pool: Sequence[Mapping[str, Any]] | None=None, target_date: str | None=None, output_path: Path | None=None, repo_root: Path | None=None, verify_grounding: bool=False) -> dict[str, Any]:
    """Aggregate K (0..3) independent M1 TL receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) back to the single M2."""
    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    date_str = str(target_date or _cst_today_str()).strip()
    filter_skills: list[str] = []
    if isinstance(skills, str):
        filter_skills = [_norm_skill(s) for s in re.split('[,;\\s]+', skills) if _norm_skill(s)]
    elif isinstance(skills, Sequence):
        for item in skills:
            for s in re.split('[,;\\s]+', str(item)):
                if _norm_skill(s):
                    filter_skills.append(_norm_skill(s))
    candidate_dirs: list[Path] = []
    if filter_skills:
        for sk in filter_skills:
            safe_slug = re.sub('[^A-Za-z0-9_-]+', '-', sk).strip('-') or 'skill'
            candidate_dirs.append(_resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug))
    elif _probe_readable_dir(resolved_root):
        if resolved_root.name.startswith('skill-retro-') and _safe_is_file(resolved_root / 'spec.md'):
            candidate_dirs.append(resolved_root)
        else:
            for child in _safe_glob(resolved_root, 'skill-retro-*'):
                if _safe_is_dir(child) and child.name not in {'skill-retro-m2-3m1-rollup'}:
                    candidate_dirs.append(child)
    m1_receipts: list[dict[str, Any]] = []
    relays_collected: list[dict[str, Any]] = []
    for gdir in candidate_dirs:
        if not _probe_readable_dir(gdir):
            continue
        sk_guess = gdir.name[len('skill-retro-'):] if gdir.name.startswith('skill-retro-') else gdir.name
        r_file = gdir / 'receipts' / 'm1-tl-receipt.json'
        loaded_receipt: dict[str, Any] | None = None
        if _safe_is_file(r_file):
            try:
                raw_r = json.loads(r_file.read_text(encoding='utf-8'))
                if isinstance(raw_r, dict):
                    loaded_receipt = dict(raw_r)
            except (OSError, ValueError, TypeError):
                loaded_receipt = None
        if loaded_receipt is None:
            ledger_file = gdir / 'ledger.md'
            try:
                ledger_txt = ledger_file.read_text(encoding='utf-8') if _safe_is_file(ledger_file) else ''
            except OSError:
                ledger_txt = ''
            relay_files = _safe_glob(gdir / 'briefs', 'skill-retro-relay-*.json')
            if 'HEALTHY_NO_ISSUES' in ledger_txt:
                loaded_receipt = {'skill': sk_guess, 'goal_id': gdir.name, 'm1_tl_agent_name': f'{gdir.name}-goal-tl-r1', 'status': 'HEALTHY_NO_ISSUES', 'writers_spawned': 0, 'reviewers_spawned': 0, 'summary': f'M1 TL deep reasoning found `{sk_guess}` healthy (`0` Writers spawned).'}
            elif 'PR_MERGED' in ledger_txt or 'GOAL_DONE' in ledger_txt or 'FIXED_AND_MERGED' in ledger_txt:
                pr_match = re.search('(https://github\\.com/\\S+/pull/\\d+|#\\d+)', ledger_txt)
                sha_match = re.search('\\b([0-9a-f]{7,40})\\b', ledger_txt)
                loaded_receipt = {'skill': sk_guess, 'goal_id': gdir.name, 'm1_tl_agent_name': f'{gdir.name}-goal-tl-r1', 'status': 'FIXED_AND_MERGED', 'writers_spawned': 1, 'reviewers_spawned': 1, 'pr_url': pr_match.group(1) if pr_match else None, 'commit_sha': sha_match.group(1) if sha_match else None, 'summary': f'M1 TL completed Writer -> Reviewer fix and merged PR for `{sk_guess}`.'}
            elif relay_files:
                with contextlib.suppress(OSError, ValueError, TypeError):
                    rel_data = json.loads(relay_files[0].read_text(encoding='utf-8'))
                    loaded_receipt = {'skill': sk_guess, 'goal_id': gdir.name, 'm1_tl_agent_name': f'{gdir.name}-goal-tl-r1', 'status': 'CROSS_SKILL_RELAY', 'writers_spawned': 0, 'reviewers_spawned': 0, 'relay_to': rel_data.get('to_skill'), 'relay_requirement': rel_data.get('requirement'), 'summary': f"M1 TL scheduled Wave 2 Cross-Skill Relay `{sk_guess}` -> `{rel_data.get('to_skill')}`."}
        if loaded_receipt is not None:
            is_grounded, g_issues = _verify_m1_receipt_grounding(loaded_receipt, repo_root=repo_root, verify_grounding=verify_grounding)
            loaded_receipt['receipt_grounded'] = is_grounded
            loaded_receipt['grounding_issues'] = g_issues
            loaded_receipt['grounding_errors'] = g_issues
            if loaded_receipt.get('status') == 'FIXED_AND_MERGED' and (not is_grounded):
                loaded_receipt['claimed_status'] = 'FIXED_AND_MERGED'
                loaded_receipt['effective_status'] = 'UNGROUNDED_RECEIPT'
            else:
                loaded_receipt['effective_status'] = loaded_receipt.get('status')
            m1_receipts.append(loaded_receipt)
        for rf in _safe_glob(gdir / 'briefs', 'skill-retro-relay-*.json'):
            try:
                relays_collected.append(json.loads(rf.read_text(encoding='utf-8')))
            except (OSError, ValueError, TypeError):
                pass
    for rf in _safe_glob(resolved_root / 'briefs', 'skill-retro-relay-*.json'):
        try:
            rel_obj = json.loads(rf.read_text(encoding='utf-8'))
            if not any((r.get('relay_id') == rel_obj.get('relay_id') for r in relays_collected)):
                relays_collected.append(rel_obj)
        except (OSError, ValueError, TypeError):
            pass
    fixed_items = [r for r in m1_receipts if r.get('status') == 'FIXED_AND_MERGED' and r.get('receipt_grounded', True)]
    ungrounded_items = [r for r in m1_receipts if r.get('status') == 'FIXED_AND_MERGED' and (not r.get('receipt_grounded', True))]
    healthy_items = [r for r in m1_receipts if r.get('status') == 'HEALTHY_NO_ISSUES']
    relay_items = [r for r in m1_receipts if r.get('status') == 'CROSS_SKILL_RELAY']
    overall_status = 'FIXED_AND_MERGED' if fixed_items else 'UNGROUNDED_RECEIPT' if ungrounded_items else 'CROSS_SKILL_RELAY' if relay_items or relays_collected else 'HEALTHY_NO_ISSUES'
    all_grounding_errors = [f"{r.get('skill')}:{err}" for r in m1_receipts for err in r.get('grounding_issues') or []]
    md_lines = [f'## `[Skill Retro {date_str}]` `1 M2 -> K ({len(m1_receipts)}) M1 TLs -> Single M2 Rollup` Summary', '', '- **Topology**: `1 M2 Coordinator -> 1 Cheap Probe Worker -> K (0..3) Independent M1 TLs -> Single M2 Rollup`', f'- **M1 TLs Spawned (`K`)**: `{len(m1_receipts)}` (max cap `3`, never a forced quota)', f'- **Rollup Breakdown**: `FIXED_AND_MERGED={len(fixed_items)}` · `HEALTHY_NO_ISSUES={len(healthy_items)}` · `CROSS_SKILL_RELAY={len(relay_items) or len(relays_collected)}` · `UNGROUNDED={len(ungrounded_items)}`', '', '| Skill | Independent M1 TL | Outcome | Grounded | Writers Spawned | PR / Relay / Evidence |', '|---|---|---|---|---|---|']
    if m1_receipts:
        for rec in m1_receipts:
            ev_cell = str(rec.get('pr_url') or (f"relay->{rec.get('relay_to')}" if rec.get('relay_to') else rec.get('summary') or '—')).replace('|', '\\|')
            grounded_cell = 'yes' if rec.get('receipt_grounded', True) else f"NO ({','.join(rec.get('grounding_issues') or [])})"
            md_lines.append(f"| `{rec.get('skill')}` | `{rec.get('m1_tl_agent_name')}` | `{rec.get('effective_status') or rec.get('status')}` | `{grounded_cell}` | `{rec.get('writers_spawned', 0)}` | {ev_cell} |")
    else:
        md_lines.append('| — | `0 M1 TLs spawned` | `HEALTHY_NO_ISSUES` | `yes` | `0` | Probe Worker found 0 issues; no M1 TL or Writer spawned |')
    obs_list = list(observation_pool or [])
    if obs_list:
        md_lines.extend(['', '### Observation Pool (Held Below Threshold)', ''])
        for ob in obs_list[:6]:
            if isinstance(ob, Mapping):
                md_lines.append(f"- `{ob.get('skill', 'unknown')}` (`{ob.get('category', 'signal')}`): {ob.get('title', '')}")
    summary_md = '\n'.join(md_lines).rstrip() + '\n'
    rollup_payload: dict[str, Any] = {'ok': True, 'schema': 'skill-retro-m2-rollup/v1', 'date': date_str, 'topology': '1_m2 -> 1_cheap_probe_worker -> 0..3_parallel_m1_tls -> 1_m2_rollup', 'status': overall_status, 'receipt_grounded': len(ungrounded_items) == 0, 'grounding_issues': all_grounding_errors, 'grounding_errors': all_grounding_errors, 'handoff_root': str(resolved_root), 'm1_tls_spawned': len(m1_receipts), 'writers_spawned_total': sum((int(r.get('writers_spawned') or 0) for r in m1_receipts)), 'counts': {'fixed_and_merged': len(fixed_items), 'ungrounded_receipts': len(ungrounded_items), 'healthy_no_issues': len(healthy_items), 'cross_skill_relay': max(len(relay_items), len(relays_collected)), 'observation_pool': len(obs_list)}, 'm1_receipts': m1_receipts, 'cross_skill_relays': relays_collected, 'observation_pool': obs_list, 'daily_summary_issue_title': f'[Skill Retro {date_str}] Autonomous Evolution Summary ({len(fixed_items)} fixed, {len(healthy_items)} healthy)', 'markdown_summary': summary_md}
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(rollup_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return rollup_payload
