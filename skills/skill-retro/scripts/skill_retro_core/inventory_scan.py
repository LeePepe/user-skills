from __future__ import annotations
from .common import *

"""Inventory Scan implementation for the skill-retro engine."""

def _cst_today_str() -> str:
    return datetime.now(_CST_TZ).date().isoformat()

def _normalize_priority(raw_priority: Any, default: str='high') -> str:
    norm = str(raw_priority or default).strip().lower()
    return _PRIORITY_MAP.get(norm, default)

def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        temporary = Path(handle.name)
    temporary.replace(path)

def _build_tool_context_burn_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        t_name = str(row.get('tool_name') or '').strip()
        bloated = int(row.get('bloated_call_count') or 0)
        if not t_name or bloated <= 0:
            continue
        p95_tok = int(row.get('p95_tokens') or 0)
        total_tok = int(row.get('total_tokens') or 0)
        share = float(row.get('share_pct') or 0.0)
        dom_skill = str(row.get('dominant_skill') or 'team-lead').strip()
        slug_tool = ''.join((ch if ch.isalnum() else '-' for ch in t_name.lower())).strip('-') or 'tool'
        candidates.append({'id': f'tool-burn-{dom_skill}-{slug_tool}', 'ref': f'skill-roi://{dom_skill}', 'title': f'[Context Burn · estimated] {t_name} ({dom_skill}) 产出 {bloated} 次 >10k tok 膨胀结果：建议加 --head-limit / view_range 或脚本下沉过滤', 'detail': f'tool_name={t_name} | dominant_skill={dom_skill} | bloated_calls_gt_10k={bloated} | p95_tokens={p95_tok} | total_tokens={total_tok} ({share:.1f}%) | remediation=Add --head-limit, view_range, or sink filtering into deterministic script', 'priority': 'high', **{key: row[key] for key in ('window_cst', 'window_start', 'window_end', 'source_as_of', 'sample_size', 'coverage_status', 'estimated', 'estimation_method') if key in row}})
    return candidates

def _is_valid_skill_script_rel_path(rel_path: str) -> bool:
    """Return True iff rel_path is a canonical executable skill script under scripts/."""
    cleaned = str(rel_path or '').strip().replace('\\', '/').lstrip('/')
    if not cleaned.startswith('scripts/'):
        return False
    parts = [p for p in cleaned.split('/') if p]
    if len(parts) < 2 or '..' in parts:
        return False
    if '__pycache__' in parts:
        return False
    for seg in parts[1:-1]:
        if seg.startswith('.') or seg.lower() in {'data', 'state', 'fixtures', 'tests', '__pycache__'}:
            return False
    filename = parts[-1]
    if not filename or filename == '__init__.py' or filename.startswith('.') or filename.startswith('test_') or filename.endswith('_test.py') or filename.endswith('.pyc'):
        return False
    return Path(filename).suffix.lower() in _VALID_SKILL_SCRIPT_SUFFIXES

def _extract_eval_case_title(item: Mapping[str, Any], *, cid: str, assertion_summary: str, skills_root: Path, dev_root: Path) -> str:
    """Extract a human-readable title for an EV-*/DC-* case, never degrading to bare cid."""
    for key in ('title', 'name', 'description', 'summary', 'rule', 'fix_rule', 'symptom', 'prompt'):
        raw = str(item.get(key) or '').strip()
        if raw and raw != cid:
            redacted = ' '.join(_redact_user_text(raw, skills_root=skills_root, dev_root=dev_root).split())
            if redacted and redacted != cid:
                return redacted[:180]
    fallback = assertion_summary if assertion_summary and assertion_summary != cid else 'Contract gate verification check'
    return f'{cid} — {fallback}'[:180]

def _redact_home_paths(text: str, *, skills_root: Path | None=None, dev_root: Path | None=None) -> str:
    """Redact absolute macOS and Linux user home directories into tilde paths."""
    return _redact_user_text(text, skills_root=skills_root, dev_root=dev_root)

def _redact_user_text(text: str, *, skills_root: Path | None=None, dev_root: Path | None=None) -> str:
    """Redact absolute home/temp roots into tilde-relative or symbolic paths."""
    cleaned = redact_text(str(text or ''))
    home_str = str(Path.home())
    if home_str:
        cleaned = cleaned.replace(home_str, '~')
    for candidate_root, replacement in ((skills_root, '~/Development/user-skills/skills'), (dev_root, '~/Development')):
        if candidate_root is None:
            continue
        for variant in {str(candidate_root), str(candidate_root.resolve())}:
            if variant and variant != '/':
                cleaned = cleaned.replace(variant, replacement)
    cleaned = _USER_HOME_PATH_RE.sub('~', cleaned)
    return cleaned

def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to path without leaving partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=f'.{path.name}.', delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

def _resolve_icloud_sync_dir(icloud_sync_dir: Path | None=None) -> Path | None:
    """Resolve AIDashSync target dir while keeping unittest/pytest runs hermetic."""
    if icloud_sync_dir is not None:
        return Path(icloud_sync_dir).expanduser()
    env_override = str(os.environ.get('AIDASH_ICLOUD_SYNC_DIR') or '').strip()
    if env_override:
        return Path(env_override).expanduser()
    in_test_harness = bool(os.environ.get('PYTEST_CURRENT_TEST')) or 'unittest' in sys.modules
    if in_test_harness:
        return None
    cloud_docs = Path.home() / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs'
    if not cloud_docs.is_dir():
        return None
    return cloud_docs / 'AIDashSync'

def mirror_skills_to_icloud(skill_details: Sequence[Mapping[str, Any]], *, icloud_sync_dir: Path | None=None) -> dict[str, Any]:
    """Mirror redacted SKILL.md and text references/* into <sync_dir>/skills-mirror/<slug>/."""
    target_sync_dir = _resolve_icloud_sync_dir(icloud_sync_dir)
    if target_sync_dir is None:
        return {'mirrored': False, 'reason': 'icloud_sync_disabled_or_unavailable', 'skills_written': 0, 'references_written': 0}
    if icloud_sync_dir is None and (not os.environ.get('AIDASH_ICLOUD_SYNC_DIR')):
        cloud_docs_parent = target_sync_dir.parent
        if not cloud_docs_parent.is_dir():
            return {'mirrored': False, 'reason': 'cloud_docs_missing', 'skills_written': 0, 'references_written': 0}
    mirror_root = target_sync_dir / 'skills-mirror'
    skills_written = 0
    refs_written = 0
    try:
        mirror_root.mkdir(parents=True, exist_ok=True)
        mirror_root_resolved = mirror_root.resolve()
        for item in skill_details:
            if not isinstance(item, Mapping):
                continue
            slug = str(item.get('skill') or '').strip()
            if not slug or not _SAFE_SKILL_SLUG_RE.match(slug) or slug in {'.', '..'}:
                continue
            skill_preview = item.get('doc_preview')
            if not isinstance(skill_preview, str) or not skill_preview:
                continue
            skill_bundle_dir = (mirror_root_resolved / slug).resolve()
            if skill_bundle_dir.parent != mirror_root_resolved:
                continue
            _atomic_write_text(skill_bundle_dir / 'SKILL.md', skill_preview)
            skills_written += 1
            refs = item.get('references')
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
                continue
            for ref in refs:
                if not isinstance(ref, Mapping):
                    continue
                ref_preview = ref.get('doc_preview')
                rel_path = str(ref.get('rel_path') or '').strip()
                if not isinstance(ref_preview, str) or not ref_preview or (not rel_path):
                    continue
                rel_parts = Path(rel_path).parts
                if not rel_parts or rel_parts[0] != 'references' or '..' in rel_parts:
                    continue
                dest = (skill_bundle_dir / rel_path).resolve()
                try:
                    dest.relative_to(skill_bundle_dir)
                except ValueError:
                    continue
                _atomic_write_text(dest, ref_preview)
                refs_written += 1
    except OSError as exc:
        return {'mirrored': False, 'reason': f'os_error:{type(exc).__name__}', 'sync_dir': _redact_home_paths(str(target_sync_dir)), 'skills_written': skills_written, 'references_written': refs_written}
    return {'mirrored': True, 'sync_dir': _redact_home_paths(str(target_sync_dir)), 'skills_written': skills_written, 'references_written': refs_written}

def _format_skill_dir(pkg: Path, *, repo_bound: bool, repo_name: str, skills_root: Path, dev_root: Path) -> str:
    """Return a privacy-safe tilde path for a discovered skill directory."""
    if not repo_bound:
        return f'~/Development/user-skills/skills/{pkg.name}'
    try:
        rel = pkg.relative_to(dev_root)
        return f'~/Development/{rel.as_posix()}'
    except ValueError:
        pass
    return _redact_user_text(str(pkg), skills_root=skills_root, dev_root=dev_root)

def _estimate_frontmatter_and_body_tokens(skill_md: Path) -> tuple[int, int, int]:
    """Return (line_count, frontmatter_tokens_est, body_tokens_est) for SKILL.md."""
    try:
        raw_bytes = skill_md.read_bytes()
        text = raw_bytes.decode('utf-8', errors='ignore')
    except OSError:
        return (0, 1, 1)
    lines = text.splitlines()
    body_tokens = max(1, len(raw_bytes) // 4)
    fm_text = text
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3:
            fm_lines = [ln.strip() for ln in parts[1].splitlines() if ln.strip() and (not ln.strip().startswith('#'))]
            fm_text = '\n'.join(fm_lines) if fm_lines else parts[1].strip()
    frontmatter_tokens = max(1, len(fm_text.encode('utf-8')) // 4)
    return (len(lines), frontmatter_tokens, body_tokens)

def _summarize_eval_case(item: dict[str, Any], *, skills_root: Path, dev_root: Path) -> tuple[str, str]:
    """Classify an EV-*/DC-* case into ('regex'|'cli'|'contract', <=140 char summary)."""
    cmd = str(item.get('cmd') or '').strip()
    forbidden_regex = str(item.get('forbidden_regex') or '').strip()
    bad_snippet = str(item.get('bad_snippet') or '').strip()
    must_contain = item.get('must_contain')
    expect_stdout = item.get('expect_stdout_contains')
    fix_rule = str(item.get('fix_rule') or '').strip()
    if cmd:
        kind = 'cli'
        summary_raw = cmd
        if isinstance(expect_stdout, list) and expect_stdout:
            summary_raw = f'{cmd} -> contains {expect_stdout[0]}'
    elif forbidden_regex or bad_snippet:
        kind = 'regex'
        summary_raw = forbidden_regex or f'forbid: {bad_snippet}'
    else:
        kind = 'contract'
        target_file = str(item.get('file') or '').strip()
        if isinstance(must_contain, list) and must_contain:
            joined = ', '.join((str(x) for x in must_contain[:3]))
            summary_raw = f'{target_file}: must_contain [{joined}]' if target_file else f'must_contain [{joined}]'
        else:
            summary_raw = fix_rule or str(item.get('symptom') or item.get('title') or 'contract check')
    one_line = ' '.join(_redact_user_text(summary_raw, skills_root=skills_root, dev_root=dev_root).split())
    if len(one_line) > 140:
        one_line = one_line[:137].rstrip() + '...'
    return (kind, one_line or 'contract assertion')

def _discover_skill_inventory(skills_root: Path | None=None, dev_root: Path | None=None, skill_filter: str | None=None) -> dict[str, dict[str, Any]]:
    """Scan global user-skills and repo-bound skills for scripts, L0/L1 lines, and EV/DC cases."""
    root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    search_dirs: list[tuple[Path, bool, str]] = [(root, False, 'user-skills')]
    for fallback_cat in ('repo', 'meta'):
        fallback_dir = d_root / 'Skills' / 'skills' / fallback_cat
        if _probe_readable_dir(fallback_dir):
            search_dirs.append((fallback_dir, False, 'user-skills'))
    repo_names = ['AIDash', 'Financial', 'VitalStride', 'VoxPocket']
    if _probe_readable_dir(d_root):
        for child in _safe_iterdir(d_root):
            if not _safe_is_dir(child) or child.name in {'user-skills', 'worktrees', 'handoff'} or child.name.startswith('.'):
                continue
            if child.name not in repo_names and (_safe_is_dir(child / '.agents' / 'skills') or _safe_is_dir(child / '.claude' / 'skills')):
                repo_names.append(child.name)
    for repo_name in repo_names:
        repo_path = d_root / repo_name
        candidates = [repo_path / '.agents' / 'skills', repo_path / '.claude' / 'skills']
        if repo_name in {'AIDash', 'Financial', 'VitalStride', 'VoxPocket'}:
            candidates.append(repo_path / 'skills')
        for rel in candidates:
            if _probe_readable_dir(rel):
                search_dirs.append((rel, True, repo_name))
    inventory: dict[str, dict[str, Any]] = {}
    for base_dir, repo_bound, repo_name in search_dirs:
        if not _probe_readable_dir(base_dir):
            continue
        for pkg in _safe_iterdir(base_dir):
            skill_md = pkg / 'SKILL.md'
            if not _safe_is_dir(pkg) or not _safe_is_file(skill_md):
                continue
            skill_name = pkg.name
            if skill_filter and _norm_skill(skill_name) != _norm_skill(skill_filter):
                continue
            if repo_bound and skill_name.startswith('speckit-'):
                continue
            if skill_name in inventory:
                continue
            l0_lines, frontmatter_tokens_est, body_tokens_est = _estimate_frontmatter_and_body_tokens(skill_md)
            try:
                skill_md_raw_bytes = skill_md.read_bytes()
                skill_md_raw_text = skill_md_raw_bytes.decode('utf-8', errors='ignore')
            except OSError:
                skill_md_raw_bytes = b''
                skill_md_raw_text = ''
            skill_md_bytes_len = len(skill_md_raw_bytes)
            skill_doc_preview = _redact_home_paths(skill_md_raw_text, skills_root=root, dev_root=d_root)[:_SKILL_MD_PREVIEW_MAX_CHARS]
            l0_cap = 80 if skill_name in {'my-designer', 'design-studio'} else 180
            refs_dir = pkg / 'references'
            ref_files: list[str] = []
            ref_entries: list[dict[str, Any]] = []
            total_refs_bytes = 0
            max_l1_lines = 0
            eval_cases = 0
            eval_counter_cases = 0
            recent_eval_cases = 0
            parsed_eval_cases: list[dict[str, Any]] = []
            seen_case_ids: set[str] = set()
            cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
            if refs_dir.is_dir():
                for ref_p in sorted(refs_dir.rglob('*')):
                    if not ref_p.is_file() or ref_p.name.startswith('.') or '__pycache__' in ref_p.parts:
                        continue
                    rel_ref = ref_p.relative_to(refs_dir).as_posix()
                    try:
                        ref_bytes = ref_p.read_bytes()
                        ref_text = ref_bytes.decode('utf-8', errors='ignore')
                        ref_line_count = len(ref_text.splitlines())
                    except OSError:
                        ref_bytes = b''
                        ref_text = ''
                        ref_line_count = 0
                    total_refs_bytes += len(ref_bytes)
                    ref_entry: dict[str, Any] = {'rel_path': f'references/{rel_ref}', 'line_count': ref_line_count, 'tokens_est': max(1, len(ref_bytes) // 4)}
                    if ref_p.suffix.lower() in _DOC_PREVIEW_REF_SUFFIXES and ref_text:
                        ref_entry['doc_preview'] = _redact_home_paths(ref_text, skills_root=root, dev_root=d_root)[:_REF_DOC_PREVIEW_MAX_CHARS]
                    ref_entries.append(ref_entry)
                    if ref_p.suffix == '.md':
                        ref_files.append(rel_ref)
                        max_l1_lines = max(max_l1_lines, ref_line_count)
                    elif ref_p.suffix == '.json' and (rel_ref.endswith('eval-cases.json') or rel_ref.endswith('defect-cases/index.json')):
                        try:
                            doc = json.loads(ref_bytes.decode('utf-8', errors='ignore'))
                            for status_key, case_status in (('active_cases', 'active'), ('retired_cases', 'retired')):
                                for item in doc.get(status_key, []):
                                    if not isinstance(item, dict):
                                        continue
                                    cid = str(item.get('id') or '').strip() or f'CASE-{len(parsed_eval_cases) + 1:03d}'
                                    if case_status == 'active':
                                        eval_cases += 1
                                        if _is_eval_counter_case(item):
                                            eval_counter_cases += 1
                                        reg = str(item.get('registered_at') or doc.get('updated') or '')
                                        if reg >= cutoff_7d:
                                            recent_eval_cases += 1
                                    if cid in seen_case_ids:
                                        continue
                                    seen_case_ids.add(cid)
                                    kind, assertion_summary = _summarize_eval_case(item, skills_root=root, dev_root=d_root)
                                    parsed_eval_cases.append({'id': cid, 'title': _extract_eval_case_title(item, cid=cid, assertion_summary=assertion_summary, skills_root=root, dev_root=d_root), 'kind': kind, 'assertion_summary': assertion_summary, 'status': str(item.get('status') or case_status), 'is_counter_case': _is_eval_counter_case(item)})
                        except (OSError, json.JSONDecodeError):
                            pass
            data_drawer = pkg / 'scripts' / 'data' / 'defect-cases' / 'index.json'
            if data_drawer.is_file():
                try:
                    data_bytes = data_drawer.read_bytes()
                    ref_entries.append({'rel_path': 'scripts/data/defect-cases/index.json', 'line_count': len(data_bytes.decode('utf-8', errors='ignore').splitlines()), 'tokens_est': max(1, len(data_bytes) // 4)})
                    data_doc = json.loads(data_bytes.decode('utf-8', errors='ignore'))
                    for item in data_doc.get('active_cases', []):
                        if isinstance(item, dict):
                            eval_cases += 1
                            if _is_eval_counter_case(item):
                                eval_counter_cases += 1
                            reg = str(item.get('registered_at') or data_doc.get('updated') or '')
                            if reg >= cutoff_7d:
                                recent_eval_cases += 1
                except (OSError, json.JSONDecodeError):
                    pass
            scripts_dir = pkg / 'scripts'
            scripts: list[str] = []
            script_entries: list[str] = []
            script_realpaths: dict[str, str] = {}
            script_watermarks: dict[str, int] = {}
            total_scripts_bytes = 0
            if scripts_dir.is_dir():
                for sp in sorted(scripts_dir.rglob('*')):
                    if not (sp.is_file() or sp.is_symlink()):
                        continue
                    rel_sc = f'scripts/{sp.relative_to(scripts_dir).as_posix()}'
                    if not _is_valid_skill_script_rel_path(rel_sc):
                        continue
                    try:
                        sc_bytes_len = len(sp.read_bytes())
                    except OSError:
                        continue
                    total_scripts_bytes += sc_bytes_len
                    if sp.parent == scripts_dir:
                        scripts.append(sp.name)
                    script_entries.append(rel_sc)
                    with contextlib.suppress(OSError):
                        script_realpaths[rel_sc] = str(sp.resolve())
                    sc_epoch = _git_last_modified_epoch(sp, fallback_mtime=False)
                    if sc_epoch is not None:
                        script_watermarks[rel_sc] = sc_epoch
            ver_info = _compute_pkg_version_fingerprint(pkg)
            reality_info = internal_reality_check(pkg)
            eval_counter_ratio = round(eval_counter_cases / eval_cases, 4) if eval_cases > 0 else 0.0
            refs_tokens_est = sum((int(r.get('tokens_est') or 0) for r in ref_entries))
            bundle_kb = round((skill_md_bytes_len + total_refs_bytes + total_scripts_bytes) / 1024.0, 1)
            refs_kb = round(total_refs_bytes / 1024.0, 1)
            budget_ok = l0_lines <= l0_cap and max_l1_lines <= 160
            inventory[skill_name] = {'skill': skill_name, 'path': str(pkg), 'skill_dir': _format_skill_dir(pkg, repo_bound=repo_bound, repo_name=repo_name, skills_root=root, dev_root=d_root), 'scope': 'repo_bound' if repo_bound else 'global', 'repo': repo_name if repo_bound else 'user-skills', 'repo_bound': repo_bound, 'skill_version': ver_info['skill_version'], 'commit_count': ver_info['commit_count'], 'subtree_sha': ver_info['subtree_sha'], 'plugin_version': ver_info['plugin_version'], 'last_modified_epoch': ver_info['last_modified_epoch'], 'l0_lines': l0_lines, 'frontmatter_tokens_est': frontmatter_tokens_est, 'body_tokens_est': body_tokens_est, 'bundle_kb': bundle_kb, 'refs_kb': refs_kb, 'refs_tokens_est': refs_tokens_est, 'l0_cap': l0_cap, 'max_l1_lines': max_l1_lines, 'l1_cap': 160, 'budget_ok': budget_ok, 'doc_preview': skill_doc_preview, 'reference_files': ref_files, 'reference_entries': ref_entries, 'scripts': scripts, 'script_entries': script_entries, 'script_realpaths': script_realpaths, 'script_watermarks': script_watermarks, 'eval_cases': parsed_eval_cases, 'eval_case_count': eval_cases, 'eval_counter_case_count': eval_counter_cases, 'eval_counter_case_ratio': eval_counter_ratio, 'eval_case_weekly_delta': recent_eval_cases, 'internal_reality': reality_info, 'internal_doc_cli_drift_count': reality_info['internal_doc_cli_drift_count'], 'internal_broken_refs_count': reality_info['internal_broken_refs_count'], 'internal_doc_cli_drift': reality_info['internal_doc_cli_drift'], 'internal_broken_refs': reality_info['internal_broken_refs']}
    return inventory

def _git_commits_behind(repo_path: Path, old_sha: str, new_sha: str) -> tuple[int, int]:
    """Return (commits_behind, days_behind) between old_sha and new_sha in repo_path."""
    if not repo_path.is_dir() or not old_sha or (not new_sha) or new_sha.startswith(old_sha) or old_sha.startswith(new_sha):
        return (0, 0)
    try:
        cnt_proc = subprocess.run(['git', '--no-optional-locks', 'rev-list', '--count', f'{old_sha}..{new_sha}'], cwd=repo_path, capture_output=True, text=True, timeout=5, check=False)
        commits = int(cnt_proc.stdout.strip()) if cnt_proc.returncode == 0 and cnt_proc.stdout.strip().isdigit() else 1
        ts_old = subprocess.run(['git', '--no-optional-locks', 'show', '-s', '--format=%ct', old_sha], cwd=repo_path, capture_output=True, text=True, timeout=5, check=False)
        ts_new = subprocess.run(['git', '--no-optional-locks', 'show', '-s', '--format=%ct', new_sha], cwd=repo_path, capture_output=True, text=True, timeout=5, check=False)
        if ts_old.returncode == 0 and ts_new.returncode == 0:
            sec_diff = max(0, int(ts_new.stdout.strip() or 0) - int(ts_old.stdout.strip() or 0))
            return (commits, sec_diff // 86400)
        return (commits, 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return (1, 0)

def _compute_s1_pin_drift(dev_root: Path) -> dict[str, Any]:
    """S1: Shared-Repos downstream pin drift across shared-ci, shared-design-system, shared-telemetry."""
    upstreams: dict[str, tuple[Path, str]] = {}
    for up_name in ('shared-ci', 'shared-design-system', 'shared-telemetry'):
        up_path = dev_root / up_name
        sha = ''
        if up_path.is_dir():
            try:
                proc = subprocess.run(['git', '--no-optional-locks', 'rev-parse', 'HEAD'], cwd=up_path, capture_output=True, text=True, timeout=5, check=False)
                if proc.returncode == 0:
                    sha = proc.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                sha = ''
        if sha:
            upstreams[up_name] = (up_path, sha)
    consumers: list[dict[str, Any]] = []
    for repo_name in ('AIDash', 'VitalStride', 'VoxPocket'):
        r_path = dev_root / repo_name
        if not r_path.is_dir():
            continue
        pins: dict[str, str] = {}
        pin_sources: dict[str, Path] = {}
        shared_ci_file = r_path / '.shared-ci'
        if shared_ci_file.is_file():
            try:
                txt = shared_ci_file.read_text(encoding='utf-8', errors='ignore').strip()
                m = re.search('\\b([0-9a-f]{7,40})\\b', txt)
                if m:
                    pins['shared-ci'] = m.group(1)
                    pin_sources['shared-ci'] = shared_ci_file
            except OSError:
                pass
        wf_dir = r_path / '.github' / 'workflows'
        if 'shared-ci' not in pins and wf_dir.is_dir():
            for wf in sorted(wf_dir.glob('*.yml')):
                try:
                    wtxt = wf.read_text(encoding='utf-8', errors='ignore')
                except OSError:
                    continue
                m = re.search('LeePepe/shared-ci/[^@\\s]+@([0-9a-f]{7,40})', wtxt)
                if m:
                    pins['shared-ci'] = m.group(1)
                    pin_sources['shared-ci'] = wf
                    break
        for pr_rel in (r_path / f'{repo_name}.xcodeproj' / 'project.xcworkspace' / 'xcshareddata' / 'swiftpm' / 'Package.resolved', r_path / repo_name / f'{repo_name}.xcodeproj' / 'project.xcworkspace' / 'xcshareddata' / 'swiftpm' / 'Package.resolved', r_path / 'Package.resolved'):
            if not pr_rel.is_file():
                continue
            try:
                pdoc = json.loads(pr_rel.read_text(encoding='utf-8'))
                for pin_entry in pdoc.get('pins', []):
                    ident = str(pin_entry.get('identity') or '').lower()
                    rev = str((pin_entry.get('state') or {}).get('revision') or '')
                    if ident in {'shared-design-system', 'shared-telemetry'} and rev:
                        pins[ident] = rev
                        pin_sources[ident] = pr_rel
            except (OSError, json.JSONDecodeError):
                continue
        drift_items: list[dict[str, Any]] = []
        for dep, (up_path, head_sha) in upstreams.items():
            pinned = pins.get(dep, '')
            if not pinned:
                continue
            commits_behind, days_behind = _git_commits_behind(up_path, pinned, head_sha)
            is_outdated = not head_sha.startswith(pinned) and (not pinned.startswith(head_sha)) and (commits_behind > 0)
            drift_items.append({**_snapshot_freshness([pin_sources[dep]], 1), 'dependency': dep, 'pinned_sha': pinned[:12], 'latest_sha': head_sha[:12], 'commits_behind': commits_behind if is_outdated else 0, 'days_behind': days_behind if is_outdated else 0, 'status': 'OUTDATED_PIN' if is_outdated else 'UP_TO_DATE'})
        consumers.append({**_snapshot_freshness([pin_sources[d['dependency']] for d in drift_items], len(drift_items)), 'repo': repo_name, 'pins': pins, 'drift': drift_items, 'outdated_count': sum((1 for d in drift_items if d['status'] == 'OUTDATED_PIN'))})
    total_outdated = sum((c['outdated_count'] for c in consumers))
    stamps = [_observed_at(row['source_as_of']) for row in consumers if row['source_as_of']]
    metadata = _freshness(stamps, bool(consumers))
    metadata.update(window_cst='snapshot', window_start=min(stamps).isoformat() if stamps else None, window_end=max(stamps).isoformat() if stamps else None)
    return {**metadata, 'upstreams': {k: v[1][:12] for k, v in upstreams.items()}, 'consumers': consumers, 'outdated_pin_count': total_outdated, 'all_up_to_date': total_outdated == 0 if any((c['drift'] for c in consumers)) else None, 'sample_size': sum((len(c['drift']) for c in consumers))}

def _compute_s11_s12_context_and_tools(warehouse_db: Path, magpie_usage: Path | None=None, copilot_db: Path | None=None, dsh_sessions: Path | None=None, telemetry_shards_dir: Path | None=None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Token allocation and per-tool burn are proxies, never measured savings."""
    mp, cp = (magpie_usage or DEFAULT_MAGPIE_USAGE, copilot_db or DEFAULT_COPILOT_DB)
    slice_tokens = dict.fromkeys(('System & AGENTS.md 指令链', 'Skills 目录与 SKILL.md', 'Tools Schema 定义', 'Tool Results 工具返回', 'User/Assistant 对话与思考链'), 0)
    total_in, total_cache_read = (0, 0)
    context_stamps, cache_stamps = ([], [])
    tool_calls: dict[str, list[tuple[int, str, datetime]]] = {}
    rows = []
    if warehouse_db.is_file():
        with contextlib.closing(sqlite3.connect(f'file:{warehouse_db}?mode=ro', uri=True)) as conn:
            rows = _timed_rows(conn, 'fact_turn')
    seen_turn_sigs: set[tuple[str, str, str]] = set()
    for row in rows:
        sid_s = str(row.get('session_id') or '')
        tid_s = str(row.get('turn_uuid') or '')
        sk_list = _json_list(row.get('skill_names'))
        sk_s = str(row.get('attribution_skill') or (sk_list[0] if sk_list else 'team-lead'))
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, sk_s))
        in_t, out_t, cache = (int(row.get(key) or 0) for key in ('input_tokens', 'output_tokens', 'cache_read'))
        total_in += in_t
        total_cache_read += cache
        stamp = row['_observed_at']
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = json.loads(row.get('tool_calls') or '[]')
        skills = json.loads(row.get('skill_names') or '[]')
        skill = str(row.get('attribution_skill') or (skills[0] if skills else 'team-lead'))
        has_skill, has_tool = (bool(row.get('attribution_skill') or skills), bool(tools))
        shares = (0.16 if has_skill and has_tool else 0.2 if has_tool else 0.28, 0.18 if has_skill else 0.04, 0.14 if has_tool else 0.18, 0.36 if has_tool else 0.1, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens['User/Assistant 对话与思考链'] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {'bash', 'execute_code', 'terminal', 'run_command'}:
                name = 'bash'
            elif name in {'read', 'view', 'read_file'}:
                name = 'read/view'
            elif name in {'edit', 'apply_patch', 'write', 'create'}:
                name = 'edit/write'
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    shard_rows = _read_telemetry_shards(telemetry_shards_dir, days=7)
    for s_row in shard_rows:
        if str(s_row.get('kind') or '') != 'skill_turn':
            continue
        sid_s = str(s_row.get('session_id') or '')
        tid_s = str(s_row.get('turn_id') or '')
        skill = str(s_row.get('skill') or 'team-lead')
        if (sid_s or tid_s) and (sid_s, tid_s, skill) in seen_turn_sigs:
            continue
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, skill))
        in_t = int(s_row.get('input_tokens') or s_row.get('in') or 0)
        out_t = int(s_row.get('output_tokens') or s_row.get('out') or 0)
        cache = int(s_row.get('cache_read') or 0)
        total_in += in_t
        total_cache_read += cache
        stamp = s_row['_observed_at']
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = _json_list(s_row.get('tool_calls'))
        has_tool = bool(tools)
        shares = (0.16 if has_tool else 0.28, 0.18, 0.14 if has_tool else 0.18, 0.36 if has_tool else 0.1, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens['User/Assistant 对话与思考链'] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {'bash', 'execute_code', 'terminal', 'run_command'}:
                name = 'bash'
            elif name in {'read', 'view', 'read_file'}:
                name = 'read/view'
            elif name in {'edit', 'apply_patch', 'write', 'create'}:
                name = 'edit/write'
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    if not cache_stamps:
        usage = _usage_records(mp, telemetry_shards_dir=telemetry_shards_dir)
        for row in usage:
            in_t, cache = (int(row.get('in') or 0), int(row.get('cache_read') or 0))
            if in_t + cache > 0:
                total_in += in_t
                total_cache_read += cache
                cache_stamps.append(row['_observed_at'])
    if not cache_stamps and cp.is_file():
        with contextlib.closing(sqlite3.connect(f'file:{cp}?mode=ro', uri=True)) as conn:
            for row in _timed_rows(conn, 'assistant_usage_events'):
                in_t, cache = (int(row.get('input_tokens') or 0), int(row.get('cache_read_tokens') or 0))
                if in_t + cache > 0:
                    total_in += in_t
                    total_cache_read += cache
                    cache_stamps.append(row['_observed_at'])
    shards_present = bool(telemetry_shards_dir and _safe_glob(telemetry_shards_dir, '*-skill-usage.jsonl'))
    present = warehouse_db.is_file() or mp.is_file() or cp.is_file() or shards_present
    context_meta = _freshness(context_stamps, present)
    cache_meta = _freshness(cache_stamps, present)
    total_ctx = sum(slice_tokens.values())
    s11 = {**_freshness(context_stamps or cache_stamps, present), 'estimated': True, 'estimation_method': 'fixed-share allocation of observed turn tokens; no measured compression saving', 'slices': [{'slice_key': key, 'tokens': value if total_ctx else None, 'share_pct': round(100 * value / total_ctx, 1) if total_ctx else None, 'estimated': True, **context_meta} for key, value in slice_tokens.items()], 'total_context_tokens': total_ctx or None, 'kv_cache_hit_pct': round(100 * total_cache_read / (total_in + total_cache_read), 1) if total_in + total_cache_read else None, 'kv_cache_freshness': cache_meta, 'compressed_saved_tokens': None}
    grand_total = sum((tokens for calls in tool_calls.values() for tokens, _, _ in calls))
    s12 = []
    for tool, calls in tool_calls.items():
        tokens = sorted((item[0] for item in calls))
        skill_counts: dict[str, int] = {}
        for tok, skill, _ in calls:
            skill_counts[skill] = skill_counts.get(skill, 0) + tok
        s12.append({'tool_name': tool, 'call_count': len(calls), 'total_tokens': sum(tokens), 'p95_tokens': tokens[max(0, (len(tokens) * 95 + 99) // 100 - 1)], 'bloated_call_count': sum((tok > 10000 for tok in tokens)), 'share_pct': round(100 * sum(tokens) / grand_total, 1) if grand_total else None, 'dominant_skill': max(skill_counts, key=skill_counts.get), 'estimated': True, 'estimation_method': 'turn context * 0.36 / tool count, not measured tool result tokens', **_freshness([item[2] for item in calls])})
    s12.sort(key=lambda row: (-row['total_tokens'], row['tool_name']))
    return (s11, s12, _build_tool_context_burn_candidates(s12))

def _percent_label(value: float | None) -> str:
    return f'{value * 100:.1f}%' if value is not None else 'unknown'

def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}

def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []

def _extract_skill_names(value: Any, known_skills: set[str]) -> set[str]:
    text = str(value or '')
    found: set[str] = set()
    for match in _SKILL_NAME_RE.finditer(text):
        name = match.group('ctx') or match.group('path') or match.group('call')
        if name:
            found.add('team-lead' if name == 'goal-target-task' else name)
    for pattern in (_REF_PATH_RE, _SCRIPT_PATH_RE):
        found.update((match.group('skill') for match in pattern.finditer(text)))
    for name in known_skills:
        if re.search(f'(?<![A-Za-z0-9_-])/{re.escape(name)}(?![A-Za-z0-9_-])', text):
            found.add(name)
        if re.search(f'(?<![A-Za-z0-9_-])skills/{re.escape(name)}(?:/|\\b)', text):
            found.add(name)
    return {'team-lead' if name == 'goal-target-task' else name for name in found}

def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1))
    return ordered[index]

def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y', 'on', 'pass', 'caught'}

def _normalize_skill_rel_path(raw_path: str, *, prefix: str) -> str:
    """Normalize a warehouse reference_path or script_path to 'references/...' or 'scripts/...'."""
    cleaned = str(raw_path or '').strip().replace('\\', '/')
    if not cleaned or cleaned.startswith('legacy:'):
        return ''
    marker = f'/{prefix}/'
    idx = cleaned.find(marker)
    if idx != -1:
        return f"{prefix}/{cleaned[idx + len(marker):].lstrip('/')}"
    if cleaned.startswith(f'{prefix}/'):
        return cleaned
    return f"{prefix}/{cleaned.lstrip('/')}"
