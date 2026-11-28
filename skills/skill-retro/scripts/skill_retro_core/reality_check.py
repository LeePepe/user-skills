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

def _ast_seq_strings(expr: ast.AST, const_map: Mapping[str, list[str]]) -> list[str]:
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        out: list[str] = []
        for elt in expr.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value.strip():
                out.append(elt.value.strip())
            elif (
                isinstance(elt, (ast.Tuple, ast.List))
                and elt.elts
                and isinstance(elt.elts[0], ast.Constant)
                and isinstance(elt.elts[0].value, str)
                and elt.elts[0].value.strip()
            ):
                out.append(elt.elts[0].value.strip())
        return out
    if isinstance(expr, ast.Dict):
        return [k.value.strip() for k in expr.keys if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value.strip()]
    if isinstance(expr, ast.Name) and expr.id in const_map:
        return list(const_map[expr.id])
    return []


def _extract_tree_cli_symbols(src: str, filename: str) -> tuple[bool, bool, set[str], set[str], bool]:
    tree = ast.parse(src, filename=filename)
    uses_argparse = 'argparse' in src
    uses_parse_known = 'parse_known_args' in src or 'REMAINDER' in src or ('sys.argv' in src and (not uses_argparse))
    flags: set[str] = set()
    subcommands: set[str] = set()
    has_subparsers = False
    const_seqs: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            vals = _ast_seq_strings(node.value, const_seqs)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if vals:
                        const_seqs[target.id] = vals
                    if 'subcommand' in target.id.lower() and vals:
                        subcommands.update(vals)
                        has_subparsers = True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and (node.value is not None):
            vals = _ast_seq_strings(node.value, const_seqs)
            if vals:
                const_seqs[node.target.id] = vals
            if 'subcommand' in node.target.id.lower() and vals:
                subcommands.update(vals)
                has_subparsers = True
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript):
            sl = node.left.slice
            is_idx0 = isinstance(sl, ast.Constant) and sl.value == 0
            base_id = node.left.value.id.lower() if isinstance(node.left.value, ast.Name) else ''
            if is_idx0 and base_id in {'argv', 'args', 'args_list', 'raw_argv', 'cli_args'}:
                for op, comp in zip(node.ops, node.comparators):
                    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str) and re.fullmatch('[a-z][a-z0-9_-]*', comp.value):
                        subcommands.add(comp.value)
                        has_subparsers = True
                    elif isinstance(op, ast.In):
                        vals = [v for v in _ast_seq_strings(comp, const_seqs) if re.fullmatch('[a-z][a-z0-9_-]*', v)]
                        if vals:
                            subcommands.update(vals)
                            has_subparsers = True

    def _inspect_expr(expr: ast.AST, loop_env: Mapping[str, list[str]]) -> None:
        nonlocal has_subparsers
        for subnode in ast.walk(expr):
            if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute):
                attr = subnode.func.attr
                if attr == 'add_argument':
                    is_pos = True
                    for arg in subnode.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            val = arg.value.strip()
                            if val.startswith('-'):
                                is_pos = False
                                flags.add(val.split('=')[0])
                    for kw in subnode.keywords:
                        if kw.arg == 'choices':
                            c_vals = _ast_seq_strings(kw.value, const_seqs)
                            if is_pos and c_vals:
                                subcommands.update(c_vals)
                                has_subparsers = True
                elif attr == 'add_parser':
                    has_subparsers = True
                    if subnode.args:
                        arg0 = subnode.args[0]
                        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                            subcommands.add(arg0.value.strip())
                        elif isinstance(arg0, ast.Name) and arg0.id in loop_env:
                            subcommands.update(loop_env[arg0.id])
                    for kw in subnode.keywords:
                        if kw.arg == 'aliases':
                            subcommands.update(_ast_seq_strings(kw.value, const_seqs))

    def _walk_stmts(stmts: Sequence[ast.stmt], loop_env: Mapping[str, list[str]]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.For):
                _inspect_expr(stmt.iter, loop_env)
                next_env = dict(loop_env)
                iter_vals = _ast_seq_strings(stmt.iter, const_seqs)
                if iter_vals:
                    if isinstance(stmt.target, ast.Name):
                        next_env[stmt.target.id] = iter_vals
                    elif isinstance(stmt.target, (ast.Tuple, ast.List)) and stmt.target.elts and isinstance(stmt.target.elts[0], ast.Name):
                        next_env[stmt.target.elts[0].id] = iter_vals
                _walk_stmts(stmt.body, next_env)
                _walk_stmts(stmt.orelse, loop_env)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in stmt.decorator_list:
                    _inspect_expr(dec, loop_env)
                _walk_stmts(stmt.body, loop_env)
            elif isinstance(stmt, (ast.If, ast.While)):
                _inspect_expr(stmt.test, loop_env)
                _walk_stmts(stmt.body, loop_env)
                _walk_stmts(stmt.orelse, loop_env)
            elif isinstance(stmt, ast.With):
                for item in stmt.items:
                    _inspect_expr(item.context_expr, loop_env)
                _walk_stmts(stmt.body, loop_env)
            elif isinstance(stmt, ast.Try):
                _walk_stmts(stmt.body, loop_env)
                for handler in stmt.handlers:
                    _walk_stmts(handler.body, loop_env)
                _walk_stmts(stmt.orelse, loop_env)
                _walk_stmts(stmt.finalbody, loop_env)
            else:
                _inspect_expr(stmt, loop_env)

    _walk_stmts(tree.body, {})
    return (uses_argparse, uses_parse_known, flags, subcommands, has_subparsers)


def _extract_script_ast_cli_contract(script_path: Path) -> dict[str, Any] | None:
    """Parse Python script AST (and delegated `*_core/*.py` modules) to extract `argparse` flags & subcommands."""
    if not _safe_is_file(script_path) or script_path.suffix.lower() != '.py':
        return None
    try:
        src = script_path.read_text(encoding='utf-8', errors='ignore')
        uses_argparse, uses_parse_known, flags, subcommands, has_subparsers = _extract_tree_cli_symbols(src, str(script_path))
    except (OSError, SyntaxError):
        return None
    flags.update(_GENERIC_FLAG_PLACEHOLDERS)
    if not uses_argparse:
        with contextlib.suppress(SyntaxError):
            facade_tree = ast.parse(src, filename=str(script_path))
            core_dirs: set[Path] = set()
            for node in ast.walk(facade_tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split('.')[0]
                        if top.endswith('_core') and _safe_is_dir(script_path.parent / top):
                            core_dirs.add(script_path.parent / top)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split('.')[0]
                    if top.endswith('_core') and _safe_is_dir(script_path.parent / top):
                        core_dirs.add(script_path.parent / top)
            for core_dir in sorted(core_dirs):
                for core_py in _safe_glob(core_dir, '*.py'):
                    if not _safe_is_file(core_py):
                        continue
                    with contextlib.suppress(OSError, SyntaxError):
                        c_src = core_py.read_text(encoding='utf-8', errors='ignore')
                        c_arg, c_pk, c_flags, c_subs, c_has_sub = _extract_tree_cli_symbols(c_src, str(core_py))
                        if c_arg or c_flags or c_subs:
                            uses_argparse = uses_argparse or c_arg
                            if c_arg and c_pk:
                                uses_parse_known = True
                            flags.update(c_flags)
                            subcommands.update(c_subs)
                            has_subparsers = has_subparsers or c_has_sub
    return {'uses_argparse': uses_argparse, 'uses_parse_known': uses_parse_known, 'flags': flags, 'subcommands': subcommands, 'has_subparsers': has_subparsers}


def _extract_python_string_literals(py_path: Path) -> list[str]:
    """Extract string literals and normalized f-strings (`<expr>` placeholders) from a Python file AST."""
    if not _safe_is_file(py_path):
        return []
    try:
        src = py_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(src, filename=str(py_path))
    except (OSError, SyntaxError):
        return []
    joined_part_ids: set[int] = set()
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for part in node.values:
                joined_part_ids.add(id(part))
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                else:
                    parts.append('<expr>')
            literals.append(''.join(parts))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and (id(node) not in joined_part_ids):
            literals.append(node.value)
    return literals


def internal_reality_check(skill_dir: Path | str, *, skills_root: Path | str | None=None) -> dict[str, Any]:
    """S2 Internal Reality Check: verify `.md` docs AND `scripts/**/*.py` string literals against local & cross-skill scripts AST.

    Detects:
      - `internal_doc_cli_drift`: documented/invoked `skills/<any_skill>/scripts/<script>.py` (or local `scripts/<script>.py`)
        missing on disk, or concrete `--flag` / subcommand missing from the target script's AST (when the script uses
        `argparse` without `parse_known_args`), scanning both `SKILL.md` / `references/*.md` AND `.py` string literals.
      - `internal_broken_refs`: local `references/<file>` links missing on disk, or malformed model strings
        such as `gemini:4- argon`.
    """
    pkg = Path(skill_dir)
    sk_name = pkg.name
    eff_skills_root = Path(skills_root) if skills_root is not None else pkg.parent
    doc_files: list[Path] = []
    skill_md = pkg / 'SKILL.md'
    if _safe_is_file(skill_md):
        doc_files.append(skill_md)
    refs_dir = pkg / 'references'
    if _probe_readable_dir(refs_dir):
        for rp in _safe_glob(refs_dir, '*.md', recursive=True):
            if _safe_is_file(rp):
                doc_files.append(rp)

    scan_sources: list[tuple[str, bool, list[str]]] = []
    ast_cache: dict[str, dict[str, Any] | None] = {}
    cli_drift: list[dict[str, Any]] = []
    broken_refs: list[dict[str, Any]] = []
    seen_drift: set[tuple[str, str, str, str]] = set()
    seen_broken: set[tuple[str, str, str]] = set()
    script_inv_re = re.compile(
        r'(?:(?<![A-Za-z0-9_./-])|(?:user-skills/))'
        r'(?P<skill_prefix>skills/(?P<target_skill>[A-Za-z0-9_-]+)/)?'
        r'scripts/(?P<script>[A-Za-z0-9_./-]+\.py)(?![A-Za-z0-9_.-])'
        r'(?P<tail>[^\n`"|;]*)'
    )
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
        scan_sources.append((rel_doc, False, text.splitlines()))

    scripts_dir = pkg / 'scripts'
    if _probe_readable_dir(scripts_dir):
        for py_p in _safe_glob(scripts_dir, '*.py', recursive=True):
            if py_p.is_symlink() or not _safe_is_file(py_p) or '__pycache__' in py_p.parts:
                continue
            if py_p.name.startswith('test_') or py_p.name.endswith('_test.py'):
                continue
            rel_py = py_p.relative_to(pkg).as_posix()
            py_lines: list[str] = []
            for lit in _extract_python_string_literals(py_p):
                py_lines.extend(lit.splitlines())
            if py_lines:
                scan_sources.append((rel_py, True, py_lines))

    for rel_src, is_py_source, source_lines in scan_sources:
        for raw_line in source_lines:
            for m_sc in script_inv_re.finditer(raw_line):
                skill_prefix = m_sc.group('skill_prefix')
                target_sk = m_sc.group('target_skill') or sk_name
                sc_sub = m_sc.group('script')
                tail = m_sc.group('tail') or ''
                if any((ch in target_sk or ch in sc_sub for ch in ('<', '>', '*', '{', '}'))):
                    continue
                if sc_sub.startswith('test_') or sc_sub.endswith('_test.py') or sc_sub.endswith('__init__.py'):
                    continue
                prefix_slice = raw_line[max(0, m_sc.start() - 36):m_sc.start()]
                if re.search(r'(?:job\s+|shared-ci/|\$[A-Za-z0-9_]+/)$', prefix_slice):
                    continue
                if not skill_prefix and re.search(r'(?:skills/[A-Za-z0-9_-]+/|[A-Za-z0-9_-]+/)$', prefix_slice):
                    continue
                if is_py_source and (not skill_prefix) and (not tail.strip()):
                    continue
                target_pkg = pkg if target_sk == sk_name else eff_skills_root / target_sk
                if target_sk != sk_name and (not _safe_is_dir(target_pkg)):
                    continue
                sc_rel = f"scripts/{sc_sub.lstrip('/')}"
                display_script = sc_rel if target_sk == sk_name else f'skills/{target_sk}/{sc_rel}'
                sc_path = target_pkg / sc_rel
                if not _safe_is_file(sc_path):
                    d_key = (rel_src, display_script, 'missing_script', display_script)
                    if d_key not in seen_drift:
                        seen_drift.add(d_key)
                        cli_drift.append({'doc': rel_src, 'script': display_script, 'target_skill': target_sk, 'kind': 'missing_script', 'token': display_script, 'detail': f'{rel_src} invokes missing script `skills/{target_sk}/{sc_rel}`'})
                    continue
                cache_key = str(sc_path.resolve())
                if cache_key not in ast_cache:
                    ast_cache[cache_key] = _extract_script_ast_cli_contract(sc_path)
                contract = ast_cache[cache_key]
                if not contract or not contract['uses_argparse'] or contract['uses_parse_known']:
                    continue
                cleaned_tail = tail.split(' -- ', 1)[0] if ' -- ' in tail else tail
                cleaned_tail = re.sub('\\[\\s*--(?:flag|flags|opt|options|arg|args)(?:\\s+[^\\]]*)?\\]', ' ', cleaned_tail)
                cleaned_tail_no_angle = re.sub('<[^>]*>', ' ', cleaned_tail)
                stripped_tail = cleaned_tail.strip()
                if stripped_tail and contract['has_subparsers'] and contract['subcommands']:
                    first_tok = stripped_tail.split()[0]
                    if re.fullmatch('[a-z][a-z0-9_-]*', first_tok) and (not first_tok.startswith('-')) and ('|' not in first_tok) and (first_tok not in {'python', 'python3', 'and', 'or', 'in', 'to', 'for', 'with', 'via', 'is', 'as', 'on', 'of', 'by', 'from'}):
                        if first_tok not in contract['subcommands']:
                            d_key = (rel_src, display_script, 'unknown_subcommand', first_tok)
                            if d_key not in seen_drift:
                                seen_drift.add(d_key)
                                cli_drift.append({'doc': rel_src, 'script': display_script, 'target_skill': target_sk, 'kind': 'unknown_subcommand', 'token': first_tok, 'detail': f'{rel_src} invokes subcommand `{first_tok}` not found in `{display_script}` AST'})
                for m_flag in re.finditer('(?<![A-Za-z0-9_-])(--[A-Za-z0-9][A-Za-z0-9_-]*)', cleaned_tail_no_angle):
                    flag_tok = m_flag.group(1)
                    if flag_tok in _GENERIC_FLAG_PLACEHOLDERS or flag_tok.endswith('-'):
                        continue
                    if flag_tok not in contract['flags']:
                        d_key = (rel_src, display_script, 'unknown_flag', flag_tok)
                        if d_key not in seen_drift:
                            seen_drift.add(d_key)
                            cli_drift.append({'doc': rel_src, 'script': display_script, 'target_skill': target_sk, 'kind': 'unknown_flag', 'token': flag_tok, 'detail': f'{rel_src} invokes flag `{flag_tok}` not found in `{display_script}` argparse AST'})
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
