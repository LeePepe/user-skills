from __future__ import annotations
from .common import *

"""Reality Check implementation for the skill-retro engine."""

def _drawer_linked_in_scripts(pkg_path: Path, ref_rel: str) -> bool:
    scripts_dir = pkg_path / 'scripts'
    if not scripts_dir.is_dir():
        return False
    ref_name = Path(ref_rel).name
    for sp in scripts_dir.rglob('*'):
        if not sp.is_file() or sp.suffix not in {'.py', '.sh'}:
            continue
        with contextlib.suppress(OSError):
            txt = sp.read_text(encoding='utf-8', errors='ignore')
            if ref_rel in txt or ref_name in txt:
                return True
    return False

def _drawer_linked_in_l0(pkg_path: Path, ref_rel: str) -> bool:
    ref_name = Path(ref_rel).name
    skill_md = pkg_path / 'SKILL.md'
    if skill_md.is_file():
        with contextlib.suppress(OSError):
            txt = skill_md.read_text(encoding='utf-8', errors='ignore')
            if ref_rel in txt or ref_name in txt:
                return True
    return False

def _extract_script_ast_cli_contract(script_path: Path) -> dict[str, Any] | None:
    """Parse Python script AST to extract `argparse` flags and subcommands (`add_argument`, `add_parser`)."""
    if not _safe_is_file(script_path) or script_path.suffix.lower() != '.py':
        return None
    try:
        src = script_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(src, filename=str(script_path))
    except (OSError, SyntaxError):
        return None
    uses_argparse = 'argparse' in src
    uses_parse_known = 'parse_known_args' in src or 'REMAINDER' in src or ('sys.argv' in src and (not uses_argparse))
    flags: set[str] = set(_GENERIC_FLAG_PLACEHOLDERS)
    subcommands: set[str] = set()
    has_subparsers = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == 'add_argument':
                is_pos = True
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        val = arg.value.strip()
                        if val.startswith('-'):
                            is_pos = False
                            flags.add(val.split('=')[0])
                for kw in node.keywords:
                    if kw.arg == 'choices' and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                        c_vals = [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                        if is_pos and c_vals:
                            subcommands.update(c_vals)
                            has_subparsers = True
            elif attr == 'add_parser':
                has_subparsers = True
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    subcommands.add(node.args[0].value.strip())
                for kw in node.keywords:
                    if kw.arg == 'aliases' and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                subcommands.add(elt.value.strip())
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and 'subcommand' in target.id.lower():
                    if isinstance(node.value, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                        elts = node.value.keys if isinstance(node.value, ast.Dict) else node.value.elts
                        for elt in elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                subcommands.add(elt.value.strip())
                                has_subparsers = True
    return {'uses_argparse': uses_argparse, 'uses_parse_known': uses_parse_known, 'flags': flags, 'subcommands': subcommands, 'has_subparsers': has_subparsers}

def internal_reality_check(skill_dir: Path | str) -> dict[str, Any]:
    """S2 Internal Reality Check: verify SKILL.md and references/*.md against local scripts AST and reference files.

    Detects:
      - `internal_doc_cli_drift`: documented `skills/<sk>/scripts/<script>.py` missing on disk, or concrete
        `--flag` / subcommand documented in `SKILL.md` / `references/*.md` missing from the script's AST
        (when the script uses `argparse` without `parse_known_args`). Ignores generic placeholders like `[--flag]`
        and pass-through `--` arguments.
      - `internal_broken_refs`: local `references/<file>` links missing on disk, or malformed model strings
        such as `gemini:4- argon`.
    """
    pkg = Path(skill_dir)
    sk_name = pkg.name
    doc_files: list[Path] = []
    skill_md = pkg / 'SKILL.md'
    if _safe_is_file(skill_md):
        doc_files.append(skill_md)
    refs_dir = pkg / 'references'
    if _probe_readable_dir(refs_dir):
        for rp in _safe_glob(refs_dir, '*.md', recursive=True):
            if _safe_is_file(rp):
                doc_files.append(rp)
    ast_cache: dict[str, dict[str, Any] | None] = {}
    cli_drift: list[dict[str, Any]] = []
    broken_refs: list[dict[str, Any]] = []
    seen_drift: set[tuple[str, str, str, str]] = set()
    seen_broken: set[tuple[str, str, str]] = set()
    script_inv_re = re.compile(f'(?<![A-Za-z0-9_./-])(?:skills/{re.escape(sk_name)}/)?scripts/([A-Za-z0-9_./-]+\\.py)(?![A-Za-z0-9_.-])([^\\n`\\"|;]*)')
    local_ref_re = re.compile(f'(?:(?<![A-Za-z0-9_./-])|skills/{re.escape(sk_name)}/)(references/[A-Za-z0-9_./-]+\\.(?:md|json|yaml|yml))')
    for doc_p in doc_files:
        rel_doc = doc_p.relative_to(pkg).as_posix() if doc_p != skill_md else 'SKILL.md'
        try:
            text = doc_p.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for m_ref in local_ref_re.finditer(text):
            ref_rel = m_ref.group(1)
            if any((ch in ref_rel for ch in ('<', '>', '*', '{', '}'))):
                continue
            if not (pkg / ref_rel).exists():
                key = (rel_doc, 'missing_reference', ref_rel)
                if key not in seen_broken:
                    seen_broken.add(key)
                    broken_refs.append({'doc': rel_doc, 'kind': 'missing_reference', 'target': ref_rel, 'detail': f'{rel_doc} references missing local file `{ref_rel}`'})
        for m_mod in _MALFORMED_MODEL_ID_RE.finditer(text):
            bad_model = m_mod.group(0).strip()
            key = (rel_doc, 'malformed_model_id', bad_model)
            if key not in seen_broken:
                seen_broken.add(key)
                broken_refs.append({'doc': rel_doc, 'kind': 'malformed_model_id', 'target': bad_model, 'detail': f'{rel_doc} contains malformed model identifier `{bad_model}`'})
        for raw_line in text.splitlines():
            for m_sc in script_inv_re.finditer(raw_line):
                sc_sub = m_sc.group(1)
                tail = m_sc.group(2) or ''
                if sc_sub.startswith('test_') or sc_sub.endswith('_test.py'):
                    continue
                prefix_slice = raw_line[max(0, m_sc.start() - 36):m_sc.start()]
                if re.search('(?:skills/[A-Za-z0-9_-]+/|shared-ci/|\\$[A-Za-z0-9_]+/)$', prefix_slice) and (not prefix_slice.endswith(f'skills/{sk_name}/')):
                    continue
                sc_rel = f"scripts/{sc_sub.lstrip('/')}"
                sc_path = pkg / sc_rel
                if not sc_path.is_file():
                    d_key = (rel_doc, sc_rel, 'missing_script', sc_rel)
                    if d_key not in seen_drift:
                        seen_drift.add(d_key)
                        cli_drift.append({'doc': rel_doc, 'script': sc_rel, 'kind': 'missing_script', 'token': sc_rel, 'detail': f'{rel_doc} invokes missing script `skills/{sk_name}/{sc_rel}`'})
                    continue
                if sc_rel not in ast_cache:
                    ast_cache[sc_rel] = _extract_script_ast_cli_contract(sc_path)
                contract = ast_cache[sc_rel]
                if not contract or not contract['uses_argparse'] or contract['uses_parse_known']:
                    continue
                cleaned_tail = tail
                if ' -- ' in cleaned_tail:
                    cleaned_tail = cleaned_tail.split(' -- ', 1)[0]
                cleaned_tail = re.sub('\\[\\s*--(?:flag|flags|opt|options|arg|args)(?:\\s+[^\\]]*)?\\]', ' ', cleaned_tail)
                cleaned_tail_no_angle = re.sub('<[^>]*>', ' ', cleaned_tail)
                stripped_tail = cleaned_tail.strip()
                if stripped_tail and contract['has_subparsers'] and contract['subcommands']:
                    first_tok = stripped_tail.split()[0]
                    if re.fullmatch('[a-z][a-z0-9_-]*', first_tok) and (not first_tok.startswith('-')) and ('|' not in first_tok) and (first_tok not in {'python', 'python3', 'and', 'or', 'in', 'to', 'for', 'with', 'via'}):
                        if first_tok not in contract['subcommands']:
                            d_key = (rel_doc, sc_rel, 'unknown_subcommand', first_tok)
                            if d_key not in seen_drift:
                                seen_drift.add(d_key)
                                cli_drift.append({'doc': rel_doc, 'script': sc_rel, 'kind': 'unknown_subcommand', 'token': first_tok, 'detail': f'{rel_doc} documents subcommand `{first_tok}` not found in `{sc_rel}` AST'})
                for m_flag in re.finditer('(?<![A-Za-z0-9_-])(--[A-Za-z0-9][A-Za-z0-9_-]*)', cleaned_tail_no_angle):
                    flag_tok = m_flag.group(1)
                    if flag_tok in _GENERIC_FLAG_PLACEHOLDERS or flag_tok.endswith('-'):
                        continue
                    if flag_tok not in contract['flags']:
                        d_key = (rel_doc, sc_rel, 'unknown_flag', flag_tok)
                        if d_key not in seen_drift:
                            seen_drift.add(d_key)
                            cli_drift.append({'doc': rel_doc, 'script': sc_rel, 'kind': 'unknown_flag', 'token': flag_tok, 'detail': f'{rel_doc} documents flag `{flag_tok}` not found in `{sc_rel}` argparse AST'})
    return {'skill': sk_name, 'ok': len(cli_drift) == 0 and len(broken_refs) == 0, 'internal_doc_cli_drift_count': len(cli_drift), 'internal_broken_refs_count': len(broken_refs), 'internal_doc_cli_drift': cli_drift, 'internal_broken_refs': broken_refs}

def _is_eval_counter_case(item: Mapping[str, Any]) -> bool:
    """Return True if an eval case is a negative/counter case (`bad_snippet`, `forbidden_regex`, `negative_cmd`, `expect_exit != 0`, `should_trigger == False`)."""
    if any((str(item.get(k) or '').strip() for k in ('bad_snippet', 'forbidden_regex', 'negative_cmd'))):
        return True
    if item.get('expect_fail_exit') is not None:
        return True
    if item.get('expect_exit') not in (None, 0):
        return True
    if item.get('should_trigger') is False:
        return True
    tags = [str(t).strip().lower() for t in item.get('tags') or [] if str(t).strip()]
    return any((t in {'negative', 'counter', 'counter-case'} for t in tags))

def _harvest_cross_session_taste_and_eval(handoff_root: Path | None) -> dict[str, Any]:
    summary: dict[str, Any] = {'selection_receipt_count': 0, 'selected_variants_count': 0, 'starred_variants_count': 0, 'rejected_variants_count': 0, 'spectrum_2_1_1_compliant_goals': 0, 'low_diversity_goals': [], 'product_persona_notes': []}
    if not _probe_readable_dir(handoff_root):
        return summary
    notes_counter: dict[str, dict[str, Any]] = {}
    for goal_dir in _safe_iterdir(handoff_root):
        if not _safe_is_dir(goal_dir) or goal_dir.name.startswith('_'):
            continue
        receipts_dir = goal_dir / 'receipts'
        if not _probe_readable_dir(receipts_dir):
            continue
        for sel_file in _safe_iterdir(receipts_dir):
            if not _safe_is_file(sel_file) or not _SELECTION_FILE_RE.match(sel_file.name):
                continue
            raw_sel = _safe_read_text(sel_file)
            if not raw_sel:
                continue
            try:
                doc = json.loads(raw_sel)
            except json.JSONDecodeError:
                continue
            if not isinstance(doc, dict):
                continue
            summary['selection_receipt_count'] += 1
            if doc.get('selectedVariantID'):
                summary['selected_variants_count'] += 1
            starred = doc.get('starredVariantIDs')
            if isinstance(starred, list):
                summary['starred_variants_count'] += len(starred)
            rejected = doc.get('rejectedVariantIDs')
            if isinstance(rejected, list):
                summary['rejected_variants_count'] += len(rejected)
            variants = doc.get('variants')
            if isinstance(variants, dict) and len(variants) >= 4:
                summary['spectrum_2_1_1_compliant_goals'] += 1
                titles = {str((v or {}).get('title') or (v or {}).get('summary') or '').strip().lower() for v in variants.values() if isinstance(v, dict)}
                titles.discard('')
                if 0 < len(titles) < min(3, len(variants)):
                    summary['low_diversity_goals'].append(goal_dir.name)
            notes_map = doc.get('notesByVariant')
            if isinstance(notes_map, dict):
                for vid, note_raw in notes_map.items():
                    note_txt = ' '.join(str(note_raw or '').split()).strip()
                    if not note_txt:
                        continue
                    norm_key = note_txt.lower()
                    bucket = notes_counter.setdefault(norm_key, {'note': note_txt[:140], 'count': 0, 'goals': set(), 'variant_id': str(vid)})
                    bucket['count'] += 1
                    bucket['goals'].add(goal_dir.name)
    ranked_notes = sorted(notes_counter.values(), key=lambda x: (-x['count'], x['note']))[:5]
    summary['product_persona_notes'] = [{'note': item['note'], 'count': item['count'], 'goals': sorted(item['goals'])[:4]} for item in ranked_notes]
    summary['low_diversity_goals'] = sorted(set(summary['low_diversity_goals']))[:5]
    return summary

def _triage_proposal_scope(*, occurrence_sessions: int, total_sessions: int, is_objective_bug_or_friction: bool, domain_scoped: bool) -> dict[str, Any]:
    """Apply Dual-Flywheel Scope Triage (Fast Flywheel 3-question guard vs Slow Flywheel cohort)."""
    slow_flywheel_ready = total_sessions >= SLOW_FLYWHEEL_COHORT_MIN_SESSIONS
    if is_objective_bug_or_friction and occurrence_sessions >= 2:
        scope_verdict = 'global_invariant_or_cli_compat'
        flywheel = 'slow_flywheel' if slow_flywheel_ready else 'fast_flywheel_verified'
        should_emit = True
    elif domain_scoped and occurrence_sessions >= 2:
        scope_verdict = 'scenario_conditioned_branch'
        flywheel = 'slow_flywheel' if slow_flywheel_ready else 'fast_flywheel_scoped'
        should_emit = True
    elif is_objective_bug_or_friction and occurrence_sessions == 1:
        scope_verdict = 'global_cli_or_drawer_wiring'
        flywheel = 'fast_flywheel_verified'
        should_emit = True
    else:
        scope_verdict = 'observation_pool_hold'
        flywheel = 'hold_until_3_cases'
        should_emit = False
    return {'scope_verdict': scope_verdict, 'flywheel': flywheel, 'slow_flywheel_cohort_ready': slow_flywheel_ready, 'occurrence_sessions': occurrence_sessions, 'emit_proposal': should_emit}
