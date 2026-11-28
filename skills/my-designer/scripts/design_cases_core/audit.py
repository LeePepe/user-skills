from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import shared
from .shared import *

for _name in dir(shared):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(shared, _name))

def command_lookup_defect(args: argparse.Namespace) -> int:
    path = Path(args.cases_file)
    if not path.is_file() and path == DEFAULT_CASES:
        path = ROOT / 'references' / 'defect-cases' / 'index.json'
    positional_q = ' '.join(getattr(args, 'positional_query', []) or []).strip()
    keyword = str(getattr(args, 'lookup_defect', None) or getattr(args, 'query', None) or positional_q or '').strip()
    if not keyword:
        _, guidance = _build_self_disclosing_guidance(['architecture', 'routing'])
        print(json.dumps({'ok': False, 'error': 'lookup-defect requires a keyword', 'guidance': guidance}, ensure_ascii=False))
        sys.stderr.write(
            "verify_design_cases.py lookup-defect: error: provide a non-empty keyword.\n"
            "Hint: lookup-defect <keyword> or --lookup-defect <keyword>.\n"
            "Read `skills/my-designer/references/routing-index.md` for accepted subcommand/--flag forms.\n"
        )
        return 2
    terms = [term.casefold() for term in re.findall(r'[\w-]+', keyword, re.UNICODE)] or [keyword.casefold()]
    matches: list[dict[str, Any]] = []
    for entry in _load_defect_entries(path):
        haystack = ' '.join(str(entry.get(field, '')) for field in ('id', 'title', 'symptom', 'fix_rule', 'platforms', 'dimensions', 'containers')).casefold()
        if keyword.casefold() in haystack or any(term in haystack for term in terms):
            matches.append(entry)
    print(json.dumps({'ok': True, 'query': keyword, 'count': len(matches), 'matches': matches}, ensure_ascii=False, indent=2))
    return 0


def _explicit_target_files(target: Path, pattern: str, *, recursive: bool = False) -> tuple[list[Path], list[str]]:
    if target.is_file():
        return [target], []
    if target.is_dir():
        matches = target.rglob(pattern) if recursive else target.glob(pattern)
        files = sorted(path for path in matches if path.is_file())
        return (files, []) if files else ([], [f'no {pattern} files found in target directory: {target}'])
    return [], [f'target path not found: {target}']


def command_check_section_interactivity(args: argparse.Namespace) -> int:
    raw_targets: list[str] = []
    path_values = getattr(args, 'interactivity_path', None)
    if isinstance(path_values, list):
        raw_targets.extend(str(value) for value in path_values if value and value != '__SNIPPET_ONLY__')
    elif path_values and path_values != '__SNIPPET_ONLY__':
        raw_targets.append(str(path_values))
    for extra in getattr(args, 'positional_paths', []) or []:
        if extra:
            raw_targets.append(str(extra))
    files: list[Path] = []
    errors: list[str] = []
    for raw in raw_targets:
        if raw == '<snippet>' and args.snippet is not None:
            continue  # Historical snippet label, not a filesystem target.
        found_files, target_errors = _explicit_target_files(Path(raw).expanduser(), '*.swift', recursive=True)
        files.extend(found_files)
        errors.extend(target_errors)
    target_label = ', '.join(raw_targets) if raw_targets else '<snippet>'
    sources = [('<snippet>', args.snippet)] if args.snippet is not None else []
    for file in files:
        try:
            sources.append((str(file), file.read_text(encoding='utf-8', errors='ignore')))
        except OSError as error:
            errors.append(f'{file}: {error}')
    all_views: list[dict[str, Any]] = []
    for name, source in sources:
        ok, views, found = _interactivity_result(source, name)
        all_views.extend([{'file': name, **view} for view in views])
        errors.extend(f'{name}: {error}' for error in found)
    if not sources:
        errors.append(f'no Swift source found at {target_label}')
    payload: dict[str, Any] = {
        'ok': not errors,
        'status': 'PASS' if not errors else 'REJECT_STATIC_PLACEHOLDER',
        'path': target_label,
        'errors': errors,
        'views': all_views,
    }
    if errors:
        drawers, guidance = _build_self_disclosing_guidance(
            ['interaction'],
            ['Fix: wire real @State/@Binding/@Bindable, DisclosureGroup, ClippedRevealLayout, or a formal SW* component instead of static color blocks.'],
        )
        payload['reference_drawers'] = drawers
        payload['guidance'] = guidance
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def _balanced_brace_end(text: str, open_brace_index: int) -> int:
    depth = 1
    idx = open_brace_index + 1
    while idx < len(text) and depth > 0:
        if text[idx] == '{':
            depth += 1
        elif text[idx] == '}':
            depth -= 1
        idx += 1
    return idx


def _dc012_spans(masked: str, regex_pattern: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in re.finditer(regex_pattern, masked)]
    for btn in re.finditer(r'\bButton\b', masked):
        cursor = btn.end()
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == '(':
            depth = 1
            cursor += 1
            while cursor < len(masked) and depth > 0:
                depth += masked[cursor] == '('
                depth -= masked[cursor] == ')'
                cursor += 1
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor >= len(masked) or masked[cursor] != '{':
            continue
        first_end = _balanced_brace_end(masked, cursor)
        first_body = masked[cursor + 1:max(cursor + 1, first_end - 1)]
        label_Match = re.match(r'\s*label\s*:\s*\{', masked[first_end:])
        if label_Match:
            label_open = first_end + label_Match.end() - 1
            block_end = _balanced_brace_end(masked, label_open)
            label_body = masked[label_open + 1:max(label_open + 1, block_end - 1)]
        else:
            block_end = first_end
            label_body = first_body
        tail = masked[block_end:min(len(masked), block_end + 220)]
        has_nested_btn = bool(re.search(r'\bButton\s*(?:\(|\{)', label_body))
        has_locked_drawer = bool(
            re.search(r'\bif\s+(?:expanded|isExpanded)\b', label_body)
            and re.search(r'\.frame\s*\([^)]*minHeight\s*:', label_body + tail)
            and re.search(r'\.buttonStyle\s*\(\s*\.plain\s*\)', tail)
        )
        if has_nested_btn or has_locked_drawer:
            spans.append((btn.start(), min(len(masked), block_end + 60)))
    deduped: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if deduped and start < deduped[-1][1]:
            continue
        deduped.append((start, end))
    return deduped


def _audit_xcode_preview_single_viewport(masked: str, regex_pattern: str | None = None) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    host_re = re.compile(r'\bFourUpPreviewHostView\b|\bScrollView\s*\(\s*\.horizontal\b')
    for preview in re.finditer(r'#Preview\b', masked):
        cursor = preview.end()
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == '(':
            depth = 1
            cursor += 1
            while cursor < len(masked) and depth > 0:
                depth += masked[cursor] == '('
                depth -= masked[cursor] == ')'
                cursor += 1
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor >= len(masked) or masked[cursor] != '{':
            continue
        block_end = _balanced_brace_end(masked, cursor)
        body = masked[cursor + 1:max(cursor + 1, block_end - 1)]
        if host_re.search(body):
            spans.append((preview.start(), block_end))
    if not spans and regex_pattern:
        spans.extend((m.start(), m.end()) for m in re.finditer(regex_pattern, masked))
    return spans


def _collect_audit_paths(args: argparse.Namespace) -> list[Path]:
    raw_items: list[str] = []
    for attr in ('path', 'root_dir', 'ui_path'):
        val = getattr(args, attr, None)
        if isinstance(val, list):
            raw_items.extend(str(x) for x in val if x)
        elif val:
            raw_items.append(str(val))
    for extra in getattr(args, 'positional_paths', []) or []:
        if extra and str(extra) not in raw_items:
            raw_items.append(str(extra))
    # Support --root <repo> --ui-path <subpath> composition when both were passed
    root_dir = getattr(args, 'root_dir', None)
    ui_path = getattr(args, 'ui_path', None)
    if root_dir and ui_path and not Path(ui_path).is_absolute():
        combined = Path(root_dir) / ui_path
        if combined.exists():
            raw_items = [str(combined)] + [p for p in raw_items if p not in {str(root_dir), str(ui_path)}]
    resolved: list[Path] = []
    seen: set[str] = set()
    for item in raw_items:
        p = Path(item).expanduser()
        key = str(p)
        if key not in seen:
            seen.add(key)
            resolved.append(p)
    return resolved


def command_audit_code(args: argparse.Namespace) -> int:
    plat = getattr(args, 'platform', None) or getattr(args, 'top_platform', None) or 'desktop'
    args.platform = _normalize_platform(plat)
    if args.platform not in {'desktop', 'mobile', 'web'}:
        print(json.dumps({'ok': True, 'platform': args.platform, 'findings': [], 'native_only': False}, indent=2))
        return 0
    target_roots = _collect_audit_paths(args)
    code_errors = [f'code path not found: {root}' for root in target_roots if not args.snippet and not root.exists()]
    if not target_roots and not getattr(args, 'snippet', None):
        drawers, guidance = _build_self_disclosing_guidance(['navigation'])
        sys.stderr.write(
            "verify_design_cases.py audit-code: error: provide at least one <path> or --path/--dir <dir_or_file> (or --snippet).\n"
            + "\n".join(guidance[:1]) + "\n"
            + "Read `skills/my-designer/references/routing-index.md` for accepted subcommand/--flag forms.\n"
        )
        return 2
    allowed_suffixes = {'.swift', '.html', '.htm', '.tsx', '.jsx'} if args.platform == 'web' else {'.swift'}
    if getattr(args, 'snippet', None):
        files = [target_roots[0] if target_roots else Path('<snippet>.swift')]
    else:
        files = []
        seen_files: set[Path] = set()
        for root in target_roots:
            candidates = [root] if root.is_file() else (
                sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in allowed_suffixes)
                if root.is_dir() else []
            )
            for cand in candidates:
                if cand not in seen_files:
                    seen_files.add(cand)
                    files.append(cand)
    cases = _load_json(Path(args.cases_file)).get('active_cases', [])
    findings = []
    drawer_keys: list[str] = []
    for path in files:
        if not args.snippet and path.suffix.lower() not in allowed_suffixes:
            continue
        text = args.snippet if args.snippet else path.read_text(encoding='utf-8', errors='ignore')
        masked = _mask_swift(text) if (args.snippet or path.suffix.lower() == '.swift') else text
        lines = text.splitlines()
        for case in cases:
            if args.platform not in case.get('platforms', []) or not case.get('forbidden_regex'):
                continue
            target_text = text if case.get('match_raw_strings') or case.get('id') in {'DC-006', 'DC-007', 'DC-008', 'DC-009', 'DC-010', 'DC-014'} else masked
            try:
                if case.get('id') == 'DC-011':
                    spans = []
                    for call in re.finditer(r'\.glassEffect\s*\(', masked):
                        depth, end = 1, call.end()
                        while end < len(masked) and depth:
                            depth += masked[end] == '('
                            depth -= masked[end] == ')'
                            end += 1
                        args_text = masked[call.end():max(call.end(), end - 1)]
                        tail = masked[end:]
                        if re.search(r'\.prominent\b', args_text):
                            spans.append((call.start(), end))
                        elif re.match(r'\s*\.\s*(?:padding|frame)\s*\(', tail):
                            spans.append((call.start(), end))
                elif case.get('id') == 'DC-012':
                    spans = _dc012_spans(masked, case['forbidden_regex'])
                elif case.get('id') == 'DC-016':
                    spans = [
                        (m.start(), m.end())
                        for m in re.finditer(case['forbidden_regex'], target_text)
                        if not ('.move' in m.group(0) and _has_enclosing_clipped_reveal_container(target_text, m.start(), m.end()))
                    ]
                elif case.get('id') == 'DC-018':
                    spans = _audit_xcode_preview_single_viewport(masked, case.get('forbidden_regex'))
                else:
                    spans = [(m.start(), m.end()) for m in re.finditer(case['forbidden_regex'], target_text)]
            except re.error as error:
                print(json.dumps({'ok': False, 'error': f"{case.get('id')}: invalid forbidden_regex: {error}"}))
                return 2
            for start, end in spans:
                line = target_text.count('\n', 0, start) + 1
                end_line = target_text.count('\n', 0, max(start, end - 1)) + 1
                if case.get('id') == 'DC-020' and 'design-contract-allow' in '\n'.join(lines[line - 1:end_line]):
                    continue
                c_drawers = _drawer_keys_for_case(case['id'], case.get('dimensions'))
                drawer_keys.extend(c_drawers)
                ref_path = DRAWER_GUIDANCE[c_drawers[0]][0]
                entry_finding: dict[str, Any] = {
                    'case_id': case['id'],
                    'title': case['title'],
                    'file': str(path),
                    'line': line,
                    'match': text[start:end].replace('\n', ' ')[:240],
                    'reference_drawer': ref_path,
                }
                if case.get('fix_rule'):
                    entry_finding['fix_rule'] = case['fix_rule']
                findings.append(entry_finding)
        for match in UI_INSTRUCTIONAL_CLUTTER_RE.finditer(text):
            line = text.count('\n', 0, match.start()) + 1
            line_text = lines[line - 1] if line <= len(lines) else ""
            if "design-contract-allow" in line_text:
                continue
            if not any(f.get('line') == line and f.get('case_id') == 'DC-014' for f in findings):
                drawer_keys.append('breathing')
                findings.append({
                    'case_id': 'UI_INSTRUCTIONAL_CLUTTER',
                    'title': 'Self-Proving Instructional Micro-Copy Printed on UI Surface',
                    'file': str(path),
                    'line': line,
                    'match': match.group(0).replace('\n', ' ')[:240],
                    'reference_drawer': DRAWER_GUIDANCE['breathing'][0],
                })
        for rule_id, rule_title, rule_re in (
            ('UI_EXPAND_STATE_MUTATION', 'Persistent Pill Label or Size Mutation Between Collapsed and Expanded States', UI_EXPAND_STATE_MUTATION_RE),
            ('UI_HEADER_INTERLEAVED_INSERTION', 'Interleaved Conditional Insertion Between Persistent Header Rows', UI_HEADER_INTERLEAVED_INSERTION_RE),
            ('UI_UNCLIPPED_PANEL_OVERLAP', 'Unclipped Inline Panel .move(edge: .top) Overlap', UI_UNCLIPPED_PANEL_OVERLAP_RE),
        ):
            for match in rule_re.finditer(masked):
                if rule_id == 'UI_UNCLIPPED_PANEL_OVERLAP' and _has_enclosing_clipped_reveal_container(masked, match.start(), match.end()):
                    continue
                line = masked.count('\n', 0, match.start()) + 1
                end_line = masked.count('\n', 0, max(match.start(), match.end() - 1)) + 1
                spanned = '\n'.join(lines[line - 1:end_line])
                if 'design-contract-allow' in spanned:
                    continue
                drawer_keys.append('interaction')
                findings.append({
                    'case_id': rule_id,
                    'title': rule_title,
                    'file': str(path),
                    'line': line,
                    'match': text[match.start():match.end()].replace('\n', ' ')[:240],
                    'reference_drawer': DRAWER_GUIDANCE['interaction'][0],
                })
        for match in UI_CUSTOM_THEME_PLUMBING_RE.finditer(masked):
            line = masked.count('\n', 0, match.start()) + 1
            end_line = masked.count('\n', 0, max(match.start(), match.end() - 1)) + 1
            if 'design-contract-allow' in '\n'.join(lines[line - 1:end_line]):
                continue
            drawer_keys.append('appearance')
            findings.append({
                'case_id': 'UI_CUSTOM_THEME_PLUMBING',
                'title': 'Custom Theme Engine or Manual .designTheme / Theme(isDark:) View-Tree Plumbing',
                'file': str(path),
                'line': line,
                'match': text[match.start():match.end()].replace('\n', ' ')[:240],
                'reference_drawer': DRAWER_GUIDANCE['appearance'][0],
                'fix_rule': 'Track native OS light/dark appearance automatically via dynamic semantic tokens; remove .designTheme(...) and .environment(\\.theme, Theme(isDark: ...)) plumbing.',
            })
        if args.snippet or path.suffix.lower() == '.swift':
            semiotic_findings = _audit_visual_semiotics_swift_gates(text, masked, lines, str(path))
            for sf in semiotic_findings:
                drawer_keys.extend(_drawer_keys_for_case(sf['case_id']))
                findings.append(sf)
    spec_errors: list[dict[str, Any]] = []
    raw_spec_files = getattr(args, 'spec_files_list', None) or ([args.spec_file] if getattr(args, 'spec_file', None) else [])
    for sf in raw_spec_files:
        spec_path = Path(sf).expanduser()
        if not spec_path.is_file():
            spec_errors.append({'rule': 'SUBTRACTIVE_SPEC_MISSING', 'file': str(spec_path), 'line': 0, 'message': f'spec file not found: {spec_path}'})
        else:
            spec_errors.extend(audit_subtractive_spec_text(spec_path.read_text(encoding='utf-8', errors='ignore'), str(spec_path)))
    if spec_errors:
        drawer_keys.append('modernization')
    visual_errors: list[str] = []
    geometry_errors: list[str] = []
    if getattr(args, 'check_whitespace_ratio', None):
        _, max_dead, _, _ = _resolve_persona_whitespace(getattr(args, 'persona', None), getattr(args, 'max_dead_space', None))
        ws_errs, _ = EVIDENCE.check_whitespace_ratio(args.check_whitespace_ratio, max_dead_space_ratio=max_dead)
        visual_errors.extend(ws_errs)
        if ws_errs:
            drawer_keys.append('breathing')
    if getattr(args, 'audit_geometry', None):
        geom_errs, _ = EVIDENCE.audit_geometry(args.audit_geometry, platform=args.platform, collision_threshold_pt2=float(getattr(args, 'collision_threshold', 2.0)))
        geometry_errors.extend(geom_errs)
        if geom_errs:
            drawer_keys.append('navigation')
    ok = not code_errors and not findings and not spec_errors and not visual_errors and not geometry_errors
    result: dict[str, Any] = {'ok': ok, 'platform': args.platform, 'native_only': args.platform != 'web', 'files': len(files), 'findings': findings}
    if code_errors:
        result['code_errors'] = code_errors
    if raw_spec_files:
        result['spec_files'] = [str(Path(sf).expanduser()) for sf in raw_spec_files]
        result['spec_validated'] = not spec_errors
    if spec_errors:
        result['spec_errors'] = spec_errors
    if visual_errors:
        result['visual_errors'] = visual_errors
    if geometry_errors:
        result['geometry_errors'] = geometry_errors
    if not ok:
        drawers, guidance = _build_self_disclosing_guidance(drawer_keys or ['navigation'])
        result['reference_drawers'] = drawers
        result['guidance'] = guidance
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def command_check_whitespace_ratio(args: argparse.Namespace) -> int:
    targets: list[str] = []
    for val in (getattr(args, 'path', None), *(getattr(args, 'positional_paths', []) or [])):
        if isinstance(val, list):
            targets.extend(str(v) for v in val if v and str(v) not in targets)
        elif val and str(val) not in targets:
            targets.append(str(val))
    if not targets:
        _, guidance = _build_self_disclosing_guidance(['breathing'])
        sys.stderr.write(
            "verify_design_cases.py check-whitespace-ratio: error: provide <png_or_dir> or --path/--dir <png_or_dir>.\n"
            + "\n".join(guidance[:1]) + "\n"
            + "Read `skills/my-designer/references/routing-index.md` for accepted subcommand/--flag forms.\n"
        )
        return 2
    persona_name, max_dead, min_breath, profile = _resolve_persona_whitespace(
        getattr(args, 'persona', None),
        getattr(args, 'max_dead_space', None),
    )
    errors: list[str] = []
    metrics: list[dict[str, Any]] = []
    advisories: list[str] = []
    for target in targets:
        files, target_errors = _explicit_target_files(Path(target).expanduser(), '*.png')
        errors.extend(target_errors)
        for file in files:
            t_errors, t_metrics = EVIDENCE.check_whitespace_ratio(file, max_dead_space_ratio=max_dead)
            errors.extend(t_errors)
            metrics.extend(t_metrics)
    if min_breath > 0.0:
        for m in metrics:
            ratio = float(m.get('dead_space_ratio', 0.0))
            if ratio < min_breath:
                advisories.append(
                    f"{Path(str(m.get('file', 'image'))).name}: SOFT_ADVISORY_CRAMPED_BREATHING "
                    f"(whitespace ratio {ratio:.1%} < {min_breath:.0%} floor for --persona breathing)"
                )
    payload: dict[str, Any] = {
        'ok': not errors,
        'platform': args.platform,
        'persona': persona_name,
        'persona_scope': profile['scope'],
        'max_dead_space_ratio': max_dead,
        'errors': errors,
        'metrics': metrics,
    }
    if advisories:
        payload['advisories'] = advisories
    if errors or advisories:
        extra = [
            f"Scope rule: Tier 2 persona `{persona_name}` ({profile['scope']}) applies context-scoped threshold {max_dead:.0%}; never overwrite global default (40%) from a single scene."
        ]
        drawers, guidance = _build_self_disclosing_guidance(['breathing', 'appearance'], extra)
        payload['reference_drawers'] = drawers
        payload['guidance'] = guidance
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def command_audit_geometry(args: argparse.Namespace) -> int:
    plat_raw = getattr(args, 'platform', None) or getattr(args, 'top_platform', None) or 'desktop'
    platform = _normalize_platform(plat_raw)
    targets: list[str] = []
    for val in (getattr(args, 'path', None), *(getattr(args, 'positional_paths', []) or [])):
        if isinstance(val, list):
            targets.extend(str(v) for v in val if v and str(v) not in targets)
        elif val and str(val) not in targets:
            targets.append(str(val))
    if not targets:
        _, guidance = _build_self_disclosing_guidance(['navigation'])
        sys.stderr.write(
            "verify_design_cases.py audit-geometry: error: provide <geometry.json> or --path <geometry.json>.\n"
            + "\n".join(guidance) + "\n"
        )
        return 2
    errors: list[str] = []
    details: list[dict[str, Any]] = []
    for target in targets:
        g_errs, g_details = EVIDENCE.audit_geometry(Path(target).expanduser(), platform=platform, collision_threshold_pt2=float(args.collision_threshold))
        errors.extend(g_errs)
        details.extend(g_details)
    errors = list(dict.fromkeys(errors))
    payload: dict[str, Any] = {
        'ok': not errors,
        'platform': platform,
        'collision_threshold_pt2': float(args.collision_threshold),
        'errors': errors,
        'details': details,
        'geometry_details': details,
    }
    if errors:
        drawers, guidance = _build_self_disclosing_guidance(['navigation', 'interaction'])
        payload['reference_drawers'] = drawers
        payload['guidance'] = guidance
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def _extract_markdown_block(content: str, heading_pattern: re.Pattern[str]) -> tuple[int, str] | None:
    match = heading_pattern.search(content)
    if match is None:
        return None
    line_number = content.count("\n", 0, match.start()) + 1
    heading_text = match.group(0).lstrip()
    tail = content[match.end() :]
    if heading_text.startswith("#"):
        level = len(heading_text) - len(heading_text.lstrip("#"))
        stop = re.search(rf"(?m)^#{{1,{max(1, level)}}}\s+\S", tail)
    else:
        stop = re.search(
            r"(?m)^(?:#{1,6}\s+\S|[-*]\s+\*\*(?!Negative|Obsolete|减法禁用|负向断言)[^*\n]+\*\*)",
            tail,
        )
    block = tail[: stop.start()] if stop is not None else tail
    return line_number, block.strip()


def audit_subtractive_spec_text(content: str, source: str = "<spec>") -> list[dict[str, Any]]:
    subtractive_match = SUBTRACTIVE_MANDATE_RE.search(content)
    if subtractive_match is None:
        return []
    mandate_line = content.count("\n", 0, subtractive_match.start()) + 1
    findings: list[dict[str, Any]] = []
    neg_block = _extract_markdown_block(content, NEGATIVE_ASSERTION_HEADING_RE)
    if neg_block is None or not NEGATIVE_ASSERTION_EVIDENCE_RE.search(neg_block[1]):
        findings.append({
            "rule": "SUBTRACTIVE_MISSING_NEGATIVE_ASSERTIONS",
            "file": source,
            "line": neg_block[0] if neg_block is not None else mandate_line,
            "message": "subtractive mandate requires a non-empty 'Negative DOM/View Assertions' section with explicit count == 0 / !exists assertions",
        })
    obs_block = _extract_markdown_block(content, OBSOLETE_TEST_HEADING_RE)
    if obs_block is None:
        findings.append({
            "rule": "SUBTRACTIVE_MISSING_OBSOLETE_TEST_MANIFEST",
            "file": source,
            "line": mandate_line,
            "message": "subtractive mandate requires an 'Obsolete Test Manifest' section with concrete test file:line references",
        })
    else:
        obs_line, obs_body = obs_block
        cleaned_body = re.sub(r"^[-*0-9.)\s:：]+", "", obs_body).strip()
        has_concrete_ref = bool(CONCRETE_TEST_REF_RE.search(obs_body))
        is_vague = bool(VAGUE_OBSOLETE_TEST_RE.search(cleaned_body)) and not has_concrete_ref
        if not cleaned_body or is_vague or not has_concrete_ref:
            findings.append({
                "rule": "SUBTRACTIVE_MISSING_OBSOLETE_TEST_MANIFEST",
                "file": source,
                "line": obs_line,
                "message": "Obsolete Test Manifest must cite concrete test file/file:line references (vague placeholders like '无破坏性旧单测删除' are forbidden)",
            })
    return findings



SPEC_GAP_GUIDANCE = [
    "Read `skills/my-designer/references/navigation-evidence-contract.md` for platform-diverged navigation and state evidence.",
    "Read `skills/my-designer/references/modernization-dimensions.md` for Deprecated Test Clauses and subtractive handoff evidence.",
]


def _spec_gap_warning(
    code: str,
    category: str,
    source: str,
    message: str,
    guidance: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "tag": "[SPEC_GAP]",
        "file": source,
        "message": message,
        "guidance": list(guidance if guidance is not None else SPEC_GAP_GUIDANCE),
    }


def _has_deprecated_test_evidence(content: str) -> bool:
    clause_re = re.compile(
        r"^[ \t]*(?P<heading>#{1,6}[ \t]+)?(?:[-*][ \t]+)?"
        r"(?:Deprecated[ \t]+Test[ \t]+Clauses|Obsolete[ \t]+Test[ \t]+Manifest|Obsolete[ \t]+Tests?)\b"
        r"[ \t]*(?:[:：-][ \t]*)?(?P<inline>[^\r\n]*)\r?$",
        re.IGNORECASE | re.MULTILINE,
    )
    for clause in clause_re.finditer(content):
        body = clause.group('inline')
        if clause.group('heading'):
            tail = content[clause.end():]
            next_heading = re.search(r'(?m)^[ \t]*#{1,6}[ \t]+\S', tail)
            body += '\n' + (tail[:next_heading.start()] if next_heading else tail)
        if re.search(r'(?i)\b(?:N/?A|none|greenfield)\b|\bno (?:obsolete|deprecated) tests\b', body):
            return True
        if re.search(
            r'[A-Za-z0-9_./-]+\.(?:swift|py|tsx?|jsx?|mjs)(?::\d+)?\b'
            r'|[A-Za-z0-9_./-]+:\d+\b'
            r'|\btest_[A-Za-z0-9_]+\b|\btest[A-Z][A-Za-z0-9_]*\b'
            r'|`[A-Za-z_][A-Za-z0-9_.:()\[\]-]*`',
            body,
        ):
            return True
    return False


def detect_spec_gap_warnings(content: str, source: str = "<spec>") -> list[dict[str, Any]]:
    """Return soft pre-implementation handoff warnings without changing legacy hard rules."""
    warnings: list[dict[str, Any]] = []
    lower = content.casefold()
    has_split = bool(re.search(r"\bNavigationSplitView\b", content))
    has_stack = bool(re.search(r"\bNavigationStack\b", content))
    desktop_native = has_split or bool(re.search(r"\b(?:TopBar|Inspector)\b", content))
    mobile_native = has_stack or bool(re.search(r"\b(?:TabView|BottomDock)\b", content))
    web_declared = bool(re.search(r"(?i)(?:platform\s*[:：]\s*web|\bPlaywright\b|\bsemantic\s+viewport\b)", content))
    desktop_declared = bool(re.search(r"(?i)(?:platforms?\s*[:：][^\n]*\b(?:macos|desktop)\b|\bmacos\b|\bdesktop\b)", content))
    mobile_declared = bool(re.search(r"(?i)(?:platforms?\s*[:：][^\n]*\b(?:ios|mobile|iphone|ipad)\b|\bios\b|\bmobile\b|\biphone\b|\bipad\b)", content))
    scoped_na = bool(re.search(
        r"(?im)^\s*(?:[-*]\s*)?(?:Navigation|Platform\s+Navigation\s+Divergence|Container)\s*[:：-]\s*N/?A\b",
        content,
    ))
    if not scoped_na:
        if web_declared and not (desktop_declared or mobile_declared):
            navigation_ok = bool(re.search(r"(?i)semantic\s+viewport|playwright|web\s+(?:container|navigation)", content))
            required = "semantic viewport/Web container"
        elif desktop_declared and not mobile_declared:
            navigation_ok = desktop_native
            required = "NavigationSplitView, TopBar, or Inspector"
        elif mobile_declared and not desktop_declared:
            navigation_ok = mobile_native
            required = "NavigationStack, TabView, or BottomDock"
        else:
            navigation_ok = desktop_native and mobile_native
            required = "desktop NavigationSplitView/TopBar/Inspector and mobile NavigationStack/TabView/BottomDock"
        if not navigation_ok:
            warnings.append(_spec_gap_warning(
                "SPEC_GAP_PLATFORM_NAVIGATION_DIVERGENCE",
                "platform_navigation",
                source,
                f"design handoff must declare {required}, or an explicit scoped platform-navigation/container N/A clause",
            ))

    missing_states = [state for state in ("empty", "loading", "error") if not re.search(rf"\b{state}\b", content, re.IGNORECASE)]
    if missing_states:
        warnings.append(_spec_gap_warning(
            "SPEC_GAP_STATE_COVERAGE", "state_matrix", source,
            "state matrix is missing: " + ", ".join(missing_states) + " (include recovery/retry evidence where applicable)",
        ))

    if not _has_deprecated_test_evidence(content):
        warnings.append(_spec_gap_warning(
            "SPEC_GAP_DEPRECATED_TEST_CLAUSES", "deprecated_tests", source,
            "handoff must include Deprecated Test Clauses, an Obsolete Test Manifest, or a scoped N/A clause",
        ))

    for raw_line in content.splitlines():
        if "design-contract-allow" in raw_line:
            continue
        match = SPEC_CUSTOM_THEME_TOGGLE_PROPOSAL_RE.search(raw_line)
        if match and not SPEC_CUSTOM_THEME_NEGATION_RE.search(raw_line[: match.start() + 16]):
            _, app_guidance = _build_self_disclosing_guidance(["appearance"])
            warnings.append(_spec_gap_warning(
                "SPEC_GAP_CUSTOM_THEME_TOGGLE",
                "system_appearance",
                source,
                "handoff proposes a custom in-app light/dark theme switcher or custom theme engine; appearance must track native OS appearance automatically",
                guidance=app_guidance + list(SPEC_GAP_GUIDANCE[:1]),
            ))
            break
    return warnings


def command_audit_spec(args: argparse.Namespace) -> int:
    raw_specs: list[str] = []
    for val in (
        getattr(args, 'spec_files_list', None),
        getattr(args, 'spec_file', None),
        getattr(args, 'positional_specs', None),
    ):
        if isinstance(val, list):
            raw_specs.extend(str(v) for v in val if v and str(v) not in raw_specs)
        elif val and str(val) not in raw_specs:
            raw_specs.append(str(val))
    if not raw_specs:
        _, guidance = _build_self_disclosing_guidance(['modernization'])
        sys.stderr.write(
            "verify_design_cases.py audit-spec: error: provide <spec.md> or --spec-file/--file/--path/--dir <spec.md>.\n"
            + "\n".join(guidance[:1]) + "\n"
            + "Read `skills/my-designer/references/routing-index.md` for accepted subcommand/--flag forms.\n"
        )
        return 2
    errors: list[str] = []
    findings: list[dict[str, Any]] = []
    spec_gap_warnings: list[dict[str, Any]] = []
    resolved_files: list[str] = []
    for raw in raw_specs:
        spec_path = Path(raw).expanduser()
        resolved_files.append(str(spec_path))
        if not spec_path.is_file():
            errors.append(f"spec file not found: {spec_path}")
            continue
        text = spec_path.read_text(encoding="utf-8", errors="ignore")
        findings.extend(audit_subtractive_spec_text(text, str(spec_path)))
        spec_gap_warnings.extend(detect_spec_gap_warnings(text, str(spec_path)))
    strict_gaps = bool(getattr(args, "strict_spec_gaps", False))
    ok = not errors and not findings and (not strict_gaps or not spec_gap_warnings)
    payload: dict[str, Any] = {
        "ok": ok,
        "spec_file": resolved_files[0],
        "findings": findings,
        "spec_gap_warnings": spec_gap_warnings,
    }
    if len(resolved_files) > 1:
        payload["spec_files"] = resolved_files
    if errors:
        payload["errors"] = errors
    if findings or spec_gap_warnings or errors:
        has_theme_gap = any(w.get("code") == "SPEC_GAP_CUSTOM_THEME_TOGGLE" for w in spec_gap_warnings)
        ref_drawers = [
            "skills/my-designer/references/modernization-dimensions.md",
            "skills/my-designer/references/navigation-evidence-contract.md",
        ]
        guidance_lines = list(SPEC_GAP_GUIDANCE)
        if has_theme_gap:
            ref_drawers.insert(0, DRAWER_GUIDANCE["appearance"][0])
            _, app_guidance = _build_self_disclosing_guidance(["appearance"])
            guidance_lines = app_guidance + guidance_lines
        payload["reference_drawers"] = ref_drawers
        payload["guidance"] = guidance_lines
    if strict_gaps:
        payload["strict_spec_gaps"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1
