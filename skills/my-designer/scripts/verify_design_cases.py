#!/usr/bin/env python3
"""Progressive disclosure router, Plan A Apple HIG code auditor, and Plan C defect-case registry."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
def _load_gate_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVIDENCE = _load_gate_module('design_evidence')
JEV = _load_gate_module('design_jev')
DEFAULT_CASES = ROOT / "scripts" / "data" / "defect-cases" / "index.json"
DEFAULT_TAXONOMY = ROOT.parent / "design-studio" / "scripts" / "data" / "taxonomy-v2.json"
EXT_REPOS_ROOT = "~/Development/Personal/collected-tools/repos"

EXTERNAL_SKILL_SOURCES: dict[str, dict[str, str]] = {
    "web": {
        "impeccable": f"{EXT_REPOS_ROOT}/impeccable/skill/reference",
        "taste_skill": f"{EXT_REPOS_ROOT}/taste-skill/skills/taste-skill",
        "emilkowalski": f"{EXT_REPOS_ROOT}/emilkowalski-skills",
        "ui_ux_pro_max": f"{EXT_REPOS_ROOT}/ui-ux-pro-max-skill/src/ui-ux-pro-max",
        "awesome_web_design_md": f"{EXT_REPOS_ROOT}/awesome-web-design-md/design-md",
        "vercel_agent_skills": f"{EXT_REPOS_ROOT}/vercel-agent-skills/skills",
        "awesome_cjk_design_md": f"{EXT_REPOS_ROOT}/awesome-cjk-design-md",
        "vibecoded_design_tells": f"{EXT_REPOS_ROOT}/vibecoded-design-tells/skill",
        "hallmark": f"{EXT_REPOS_ROOT}/hallmark/skills/hallmark/references",
    },
}

WEB_DIMENSION_SLICES: dict[str, dict[str, list[str]]] = {
    "hallmark": {
        "color-palette": ["themes/index.json", "dark-mode.md"],
        "visual-style": ["craft.md", "gates.md", "texture.md"],
        "component": ["patterns.md", "interaction-and-states.md"],
        "chart-dataviz": ["data-viz.md"],
        "glass-material": ["texture.md", "dark-mode.md"],
        "motion-interaction": ["microinteractions.md", "scroll-choreography.md"],
        "layout-navigation": ["layout.md", "anatomy.md", "macrostructures.md", "responsive.md"],
        "typography-copy": ["fonts.md", "craft.md", "gates.md"],
    },
}

ALWAYS_LOAD_HIG = {
    "desktop": ["designing-for-macos.md", "accessibility.md", "layout.md", "color.md", "typography.md"],
    "mobile": ["designing-for-ios.md", "designing-for-ipados.md", "accessibility.md", "layout.md", "color.md", "typography.md"],
    "web": [],
}

PLAN_A_LENSES = [
    {
        "id": "lens-1-accessibility",
        "severity": "Critical",
        "summary": "Dynamic Type (>=11pt mobile / >=10pt desktop), >=4.5:1 contrast, >=44x44pt mobile / >=28x28pt desktop targets, Reduce Motion & Reduce Transparency.",
    },
    {
        "id": "lens-2-platform-conventions",
        "severity": "High",
        "summary": "Native TabView/NavigationSplitView/.toolbar/MenuBarExtra; no bottom bars on macOS; Size Classes over fixed device widths on iOS/iPadOS/iPhone Duo.",
    },
    {
        "id": "lens-3-liquid-glass-and-craft",
        "severity": "High",
        "summary": "Liquid Glass restricted to Functional Layer (bars/floating controls); Content Layer stays solid/standard material; max 1-2 .glassProminent actions.",
    },
    {
        "id": "lens-4-interaction-and-states",
        "severity": "Medium",
        "summary": "Non-blocking loading, ContentUnavailableView empty states, inline error recovery, confirmation for destructive actions.",
    },
    {
        "id": "lens-5-typography-and-copy",
        "severity": "Medium",
        "summary": "Platform capitalization, .monospacedDigit() + .contentTransition(.numericText()) for live numbers, clear verb labels.",
    },
]

PERSONA_WHITESPACE_PROFILES: dict[str, dict[str, Any]] = {
    "compact": {
        "max_dead_space_ratio": 0.25,
        "advisory_min_breathing_ratio": 0.0,
        "scope": "context_scoped_compact_cockpit",
        "intent_weighting": "High-density data/telemetry cockpit: tight chrome, dense tabular/chart focus, minimal tail blankness (<=25%). Never overwrite global defaults.",
    },
    "default": {
        "max_dead_space_ratio": 0.40,
        "advisory_min_breathing_ratio": 0.0,
        "scope": "global_default_baseline",
        "intent_weighting": "Balanced task workspace: 1 focal P0 per viewport, <=40% bottom contiguous dead space.",
    },
    "breathing": {
        "max_dead_space_ratio": 0.55,
        "advisory_min_breathing_ratio": 0.12,
        "scope": "context_scoped_breathing_companion",
        "intent_weighting": "Calm companion/editorial surface: generous whitespace (>=12% advisory breathing floor, <=55% cap) protecting focal P0 clarity.",
    },
}

DRAWER_GUIDANCE: dict[str, tuple[str, str]] = {
    "routing": (
        "skills/my-designer/references/routing-index.md",
        "Choose the smallest drawer and use --platform/--surface (ios -> mobile, macos -> desktop).",
    ),
    "eval": (
        "skills/my-designer/references/eval-cases.json",
        "Replay the positive and counter-cases before changing a gate or remedy route.",
    ),
    "interaction": (
        "skills/my-designer/references/interaction-layering.md",
        "Keep Content/Functional/Feedback layers separate: isolate outer Button triggers from expanded actions, keep persistent pills stable across expand states, and wrap reveals in clipped layouts.",
    ),
    "breathing": (
        "skills/my-designer/references/breathing-and-clarity.md",
        "Reserve one focal P0 per viewport, strip self-proving instructional micro-copy, and scope compact vs breathing whitespace via --persona without mutating global defaults.",
    ),
    "navigation": (
        "skills/my-designer/references/navigation-evidence-contract.md",
        "Enforce platform-diverged navigation (macOS NavigationSplitView/TopBar vs iOS NavigationStack/BottomDock), >=44pt/28pt targets, and zero leaf AABB overlap or numeric ellipsis.",
    ),
    "modernization": (
        "skills/my-designer/references/modernization-dimensions.md",
        "Subtractive mandates ('只保留/去掉/砍掉') require explicit Negative DOM/View Assertions (count == 0 / !exists) and concrete file:line Obsolete Test Manifest entries.",
    ),
    "appearance": (
        "skills/my-designer/references/appearance-ledger.md",
        "Lock one semantic VS-*/CL-* appearance route, contrast floor, light/dark parity, and context-scoped Tier 2 Product Persona tokens rather than ad-hoc hex overrides.",
    ),
    "architecture": (
        "skills/my-designer/references/design-flow-architecture.md",
        "Follow the 3-tier spectrum + 2+1+1 variant quota (v01..v02 deepen persona, v03 cross-pollinates design-studio, v04 wildcard) and structured visual-traceback/v1 evidence.",
    ),
}


def _drawer_keys_for_case(case_id: str, dimensions: list[str] | None = None) -> list[str]:
    cid = (case_id or "").upper()
    dims = set(dimensions or [])
    keys: list[str] = []
    if cid in {"DC-004", "DC-010", "DC-011", "DC-012", "DC-016", "UI_EXPAND_STATE_MUTATION", "UI_HEADER_INTERLEAVED_INSERTION", "UI_UNCLIPPED_PANEL_OVERLAP"}:
        keys.append("interaction")
    if cid in {"DC-001", "DC-002", "DC-007", "DC-009", "DC-014", "UI_INSTRUCTIONAL_CLUTTER"}:
        keys.append("breathing")
    if cid in {"DC-003", "DC-005", "DC-008", "DC-013"}:
        keys.append("navigation")
    if cid == "DC-015" or cid.startswith("SUBTRACTIVE_"):
        keys.append("modernization")
    if not keys:
        if dims & {"motion-interaction", "glass-material"}:
            keys.append("interaction")
        if dims & {"typography-copy", "visual-style"}:
            keys.append("breathing")
        if dims & {"color-palette"}:
            keys.append("appearance")
        if dims & {"layout-navigation", "component"}:
            keys.append("navigation")
    return keys or ["navigation"]


def _build_self_disclosing_guidance(drawer_keys: list[str], extra_lines: list[str] | None = None) -> tuple[list[str], list[str]]:
    seen_keys: list[str] = []
    for key in drawer_keys:
        if key in DRAWER_GUIDANCE and key not in seen_keys:
            seen_keys.append(key)
    drawers: list[str] = []
    lines: list[str] = []
    for key in seen_keys[:3]:
        ref_path, hint = DRAWER_GUIDANCE[key]
        drawers.append(ref_path)
        lines.append(f"Read `{ref_path}` — {hint}")
    for line in extra_lines or []:
        if line and line not in lines:
            lines.append(line)
    return drawers, lines[:3]


def _resolve_persona_whitespace(persona: str | None, explicit_max_dead_space: float | None) -> tuple[str, float, float, dict[str, Any]]:
    norm = (persona or "default").strip().lower()
    alias_map = {
        "cockpit": "compact",
        "dense": "compact",
        "content-dense": "compact",
        "high-density": "compact",
        "standard": "default",
        "balanced": "default",
        "companion": "breathing",
        "calm": "breathing",
        "editorial": "breathing",
    }
    norm = alias_map.get(norm, norm)
    profile = PERSONA_WHITESPACE_PROFILES.get(norm, PERSONA_WHITESPACE_PROFILES["default"])
    max_dead = float(explicit_max_dead_space) if explicit_max_dead_space is not None else float(profile["max_dead_space_ratio"])
    min_breath = float(profile["advisory_min_breathing_ratio"])
    return (norm if norm in PERSONA_WHITESPACE_PROFILES else "default"), max_dead, min_breath, profile


class SelfDisclosingArgumentParser(argparse.ArgumentParser):
    """Emit actionable 2-3 line CLI usage + L1 reference hints on exit_code = 2."""

    def error(self, message: str) -> None:
        hints = [
            f"{self.prog}: error: {message}",
            "Hint: common patterns -> `audit-code [--platform desktop|mobile|web] <path...>` | `audit-spec <design.md...>` | `check-whitespace-ratio [--persona compact|default|breathing] <png_or_dir...>`.",
            "Read `skills/my-designer/references/routing-index.md` and replay `skills/my-designer/references/eval-cases.json`.",
        ]
        sys.stderr.write("\n".join(hints) + "\n")
        self.exit(2)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

_FALLBACK_TAXONOMY = {
    "dimensions": [
        {"id": "layout-navigation", "hig_routes": ["layout.md"], "apple_native_primitives": ["NavigationStack", "NavigationSplitView"]},
        {"id": "component", "hig_routes": ["accessibility.md"], "apple_native_primitives": ["Button", "Toggle"]},
        {"id": "glass-material", "hig_routes": ["materials.md"], "apple_native_primitives": ["glassEffect"]},
        *[{"id": dim, "hig_routes": [], "apple_native_primitives": []} for dim in
          ("visual-style", "color-palette", "chart-dataviz", "motion-interaction", "typography-copy")],
    ],
    "jev_verification_gates": {
        "mobile": {"q_mobile_native_evidence": "Verify native NavigationStack/TabView, >=44pt targets, solid content and functional-only glass."},
        "desktop": {"q_desktop_native_evidence": "Verify split/inspector navigation, >=28pt targets, keyboard parity and inactive-window dimming."},
        "web": {"q_web_viewport_evidence": "Verify semantic viewport, 60–72ch prose, natural CJK wrapping and functional optical refraction."},
    },
}


def _load_taxonomy(path=None):
    target = Path(path or DEFAULT_TAXONOMY)
    return _load_json(target) if target.is_file() else _FALLBACK_TAXONOMY


TAXONOMY = _load_taxonomy()


def _normalize_platform(value):
    return {"ios": "mobile", "macos": "desktop"}.get(value, value)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _mask_swift(text: str) -> str:
    """Hide comments and string literals while preserving line/column offsets."""
    pattern = re.compile(r'("(?:\\\\.|[^"\\\\])*"|//[^\\n]*|/\\*[\\s\\S]*?\\*/)')
    return pattern.sub(lambda m: ''.join('\\n' if c == '\\n' else ' ' for c in m.group()), text)


def _swiftui_view_candidates(text: str) -> list[dict[str, Any]]:
    """Return masked source spans for SwiftUI View declarations."""
    masked = _mask_swift(text)
    candidates: list[dict[str, Any]] = []
    header = re.compile(r'\bstruct\s+([A-Za-z_]\w*)\s*:[^{\n]*\bView\b[^\{]*\{')
    for match in header.finditer(masked):
        start = match.start()
        open_brace = masked.find('{', match.start(), match.end())
        end = _balanced_brace_end(masked, open_brace)
        candidates.append({
            'name': match.group(1),
            'start': start,
            'end': end,
            'source': text[start:end],
            'masked': masked[start:end],
            'line': text.count('\n', 0, start) + 1,
        })
    if candidates:
        return candidates
    return [{'name': '<snippet>', 'start': 0, 'end': len(text), 'source': text, 'masked': masked, 'line': 1}]


def _interactivity_result(text: str, path: str) -> tuple[bool, list[dict[str, Any]], list[str]]:
    primitive = re.compile(r'@(?:State|Binding|Bindable)\b|\b(?:DisclosureGroup|ClippedRevealLayout|SW[A-Z]\w*)\b')
    static_only = re.compile(r'^(?:[\s{}()\[\].,;:+\-]|(?:RoundedRectangle|Rectangle|Circle|Capsule|Color|fill|Text|Spacer|VStack|HStack|ZStack|body|some|View|var|let|return|frame|foregroundStyle|background|padding|overlay|cornerRadius|font|foregroundColor|opacity|lineLimit|resizable|scaledToFit|contentShape|self|\d+|\w+))*$', re.MULTILINE)
    views: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate in _swiftui_view_candidates(text):
        masked = candidate['masked']
        tokens = sorted(set(re.findall(r'@(?:State|Binding|Bindable)\b|\b(?:DisclosureGroup|ClippedRevealLayout|SW[A-Z]\w*)\b', masked)))
        has_primitive = bool(primitive.search(masked))
        has_static_shape = bool(re.search(r'\b(?:RoundedRectangle|Rectangle|Circle|Capsule|Color)\b|\.fill\s*\(', masked))
        is_static = has_static_shape and not has_primitive and bool(static_only.match(masked.replace('\n', ' ')))
        valid = has_primitive and not is_static
        view = {'name': candidate['name'], 'line': candidate['line'], 'ok': valid, 'interactive_tokens': tokens, 'static_placeholder': is_static}
        views.append(view)
        if not has_primitive:
            errors.append(f"{candidate['name']}: missing interactive state, disclosure, ClippedRevealLayout, or SW* component")
        elif is_static:
            errors.append(f"{candidate['name']}: static color-block placeholder")
    return not errors, views, errors


def _load_defect_entries(path: Path) -> list[dict[str, Any]]:
    doc = _load_json(path)
    entries: list[dict[str, Any]] = []
    for key in ('active_cases', 'archived_templates'):
        values = doc.get(key, [])
        if isinstance(values, list):
            entries.extend(item for item in values if isinstance(item, dict))
    return entries


def command_lookup_defect(args: argparse.Namespace) -> int:
    path = Path(args.cases_file)
    if not path.is_file() and path == DEFAULT_CASES:
        path = ROOT / 'references' / 'defect-cases' / 'index.json'
    positional_q = ' '.join(getattr(args, 'positional_query', []) or []).strip()
    keyword = str(getattr(args, 'lookup_defect', None) or getattr(args, 'query', None) or positional_q or '').strip()
    if not keyword:
        _, guidance = _build_self_disclosing_guidance(['architecture'])
        print(json.dumps({'ok': False, 'error': 'lookup-defect requires a keyword', 'guidance': guidance}, ensure_ascii=False))
        return 2
    terms = [term.casefold() for term in re.findall(r'[\w-]+', keyword, re.UNICODE)] or [keyword.casefold()]
    matches: list[dict[str, Any]] = []
    for entry in _load_defect_entries(path):
        haystack = ' '.join(str(entry.get(field, '')) for field in ('id', 'title', 'symptom', 'fix_rule', 'platforms', 'dimensions', 'containers')).casefold()
        if keyword.casefold() in haystack or any(term in haystack for term in terms):
            matches.append(entry)
    print(json.dumps({'ok': True, 'query': keyword, 'count': len(matches), 'matches': matches}, ensure_ascii=False, indent=2))
    return 0


def command_check_section_interactivity(args: argparse.Namespace) -> int:
    raw_targets: list[str] = []
    if getattr(args, 'interactivity_path', None) and args.interactivity_path != '__SNIPPET_ONLY__':
        raw_targets.append(args.interactivity_path)
    for extra in getattr(args, 'positional_paths', []) or []:
        if extra and extra not in raw_targets:
            raw_targets.append(extra)
    files: list[Path] = []
    for raw in raw_targets:
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(sorted(candidate.rglob('*.swift')))
    target_label = ', '.join(raw_targets) if raw_targets else '<snippet>'
    if args.snippet is not None:
        files = []
        sources = [('<snippet>', args.snippet)]
    else:
        sources = []
        for file in files:
            try:
                sources.append((str(file), file.read_text(encoding='utf-8', errors='ignore')))
            except OSError as error:
                drawers, guidance = _build_self_disclosing_guidance(['interaction'])
                print(json.dumps({
                    'ok': False,
                    'status': 'REJECT_STATIC_PLACEHOLDER',
                    'path': target_label,
                    'errors': [str(error)],
                    'views': [],
                    'reference_drawers': drawers,
                    'guidance': guidance,
                }, ensure_ascii=False))
                return 1
    all_views: list[dict[str, Any]] = []
    errors: list[str] = []
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
            "verify_design_cases.py audit-code: error: provide at least one <path> or --path <dir_or_file> (or --snippet).\n"
            + "\n".join(guidance) + "\n"
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
                else:
                    spans = [(m.start(), m.end()) for m in re.finditer(case['forbidden_regex'], target_text)]
            except re.error as error:
                print(json.dumps({'ok': False, 'error': f"{case.get('id')}: invalid forbidden_regex: {error}"}))
                return 2
            for start, end in spans:
                line = target_text.count('\n', 0, start) + 1
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
        lines = text.splitlines()
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
            + "\n".join(guidance) + "\n"
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
        t_errors, t_metrics = EVIDENCE.check_whitespace_ratio(Path(target).expanduser(), max_dead_space_ratio=max_dead)
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
    payload: dict[str, Any] = {'ok': not errors, 'platform': platform, 'collision_threshold_pt2': float(args.collision_threshold), 'errors': errors, 'details': details}
    if errors:
        drawers, guidance = _build_self_disclosing_guidance(['navigation', 'interaction'])
        payload['reference_drawers'] = drawers
        payload['guidance'] = guidance
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def match_active_cases(cases_doc: dict[str, Any], platform: str, dimensions: list[str], container: str | None = None) -> list[dict[str, Any]]:
    matched = []
    dim_set = set(dimensions)
    container_set = set(_csv(container))
    for case in cases_doc.get("active_cases", []):
        plats = case.get("platforms", [])
        if plats and platform not in plats:
            continue
        case_dims = set(case.get("dimensions", []))
        if dim_set and case_dims and not (dim_set & case_dims):
            continue
        containers = set(case.get("containers", []))
        if container_set and containers and not (container_set & containers):
            continue
        matched.append(case)
    return matched


def command_route(args: argparse.Namespace) -> int:
    plat = getattr(args, 'platform', None) or getattr(args, 'top_platform', None) or 'desktop'
    args.platform = _normalize_platform(plat)
    cases_doc = _load_json(Path(args.cases_file))
    taxonomy = _load_taxonomy(args.taxonomy)
    dims = _csv(args.dimensions)
    persona_raw = getattr(args, 'persona', None) or getattr(args, 'archetype', None)
    persona_name, max_dead, min_breath, persona_profile = _resolve_persona_whitespace(persona_raw, None)
    distilled_base = list(ALWAYS_LOAD_HIG.get(args.platform, []))
    hig_files = list(distilled_base)
    on_demand_external: list[str] = []
    native_primitives: list[str] = []

    for dim in taxonomy.get("dimensions", []):
        if not dims or dim["id"] in dims:
            for h in dim.get("hig_routes", []):
                if h not in hig_files:
                    hig_files.append(h)
                if h not in distilled_base and h not in on_demand_external:
                    on_demand_external.append(h)
            for p in dim.get("apple_native_primitives", []):
                if p not in native_primitives:
                    native_primitives.append(p)

    active_plan_c = match_active_cases(cases_doc, args.platform, dims, args.container)
    layering_dims = {"layout-navigation", "component", "chart-dataviz", "motion-interaction"}
    breathing_dims = {"visual-style", "color-palette", "layout-navigation", "typography-copy"}
    disclose_layering = (not dims) or bool(set(dims) & layering_dims) or bool(active_plan_c)
    disclose_breathing = (not dims) or bool(set(dims) & breathing_dims)
    dim_set = set(dims)
    ext_root = "~/Development/Personal/collected-tools/repos"
    if args.platform in ("desktop", "mobile"):
        shipswift_pkgs = {
            "animation": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWAnimation",
            "metal_shaders": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWAnimation/SWMetal",
            "charts": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWChart",
            "components": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWComponent",
            "modules": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWModule",
        }
        focused_shipswift: dict[str, str] = {}
        if not dim_set or (dim_set & {"motion-interaction", "glass-material", "visual-style"}):
            focused_shipswift["animation"] = shipswift_pkgs["animation"]
        if not dim_set or (dim_set & {"glass-material", "visual-style", "motion-interaction"}):
            focused_shipswift["metal_shaders"] = shipswift_pkgs["metal_shaders"]
        if not dim_set or ("chart-dataviz" in dim_set):
            focused_shipswift["charts"] = shipswift_pkgs["charts"]
        if not dim_set or (dim_set & {"component", "layout-navigation", "typography-copy"}):
            focused_shipswift["components"] = shipswift_pkgs["components"]
        if not dim_set or ("layout-navigation" in dim_set):
            focused_shipswift["modules"] = shipswift_pkgs["modules"]
        if not focused_shipswift:
            focused_shipswift["components"] = shipswift_pkgs["components"]

        avdlee_all_files = [
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/liquid-glass.md",
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/charts.md",
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/animation-advanced.md",
            f"{ext_root}/avdlee-swiftui-skill/.agents/skills/update-swiftui-apis/references/scan-manifest.md",
            *(
                [
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/macos-window-styling.md",
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/macos-scenes.md",
                ]
                if args.platform == "desktop"
                else [
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/iphone-duo.md",
                ]
            ),
        ]
        avdlee_focused: list[str] = []
        if not dim_set or (dim_set & {"glass-material", "visual-style", "color-palette"}):
            avdlee_focused.append(avdlee_all_files[0])
        if not dim_set or ("chart-dataviz" in dim_set):
            avdlee_focused.append(avdlee_all_files[1])
        if not dim_set or ("motion-interaction" in dim_set):
            avdlee_focused.append(avdlee_all_files[2])
        if not dim_set or (dim_set & {"layout-navigation", "component"}):
            avdlee_focused.extend(avdlee_all_files[3:])
        if not avdlee_focused:
            avdlee_focused = list(avdlee_all_files[:1])

        genjutsu_all_files = [
            f"{ext_root}/genjutsu/skills/_jutsu/swiftui-graphics/SKILL.md",
            f"{ext_root}/genjutsu/skills/_jutsu/swiftui-motion/SKILL.md",
            f"{ext_root}/genjutsu/skills/_jutsu/motion-principles/SKILL.md",
            *(
                [f"{ext_root}/genjutsu/skills/_jutsu/desktop-principles/SKILL.md"]
                if args.platform == "desktop"
                else [f"{ext_root}/genjutsu/skills/_jutsu/mobile-principles/SKILL.md"]
            ),
        ]
        genjutsu_focused: list[str] = []
        if not dim_set or (dim_set & {"glass-material", "visual-style", "color-palette"}):
            genjutsu_focused.append(genjutsu_all_files[0])
        if not dim_set or ("motion-interaction" in dim_set):
            genjutsu_focused.extend(genjutsu_all_files[1:3])
        if not dim_set or (dim_set & {"layout-navigation", "component"}):
            genjutsu_focused.append(genjutsu_all_files[3])
        if not genjutsu_focused:
            genjutsu_focused = [genjutsu_all_files[3]]

        allowed_native = {
            "apple-design-skill": {
                "hig_dir": f"{ext_root}/apple-design-skill/references/hig",
                "code_audit": f"{ext_root}/apple-design-skill/references/code-audit.md",
                "on_demand_files": on_demand_external[:8],
            },
            "twostraws-swiftui-skill": {
                "files": [
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/design.md",
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/resizability.md",
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/views.md",
                ],
            },
            "avdlee-swiftui-skill": {
                "files": avdlee_all_files,
                "focused_files": avdlee_focused,
            },
            "dimillian-skills": {
                "files": [
                    f"{ext_root}/dimillian-skills/swiftui-liquid-glass/references/liquid-glass.md",
                    f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/scroll-reveal.md",
                    *(
                        [
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/menu-bar.md",
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/macos-settings.md",
                        ]
                        if args.platform == "desktop"
                        else [
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/sheets.md",
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/navigationstack.md",
                        ]
                    ),
                ],
            },
            "emilkowalski-skills_native": {
                "files": [
                    f"{ext_root}/emilkowalski-skills/skills/apple-design/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/mobile-native/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/remove-animations/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/break-ui/SKILL.md",
                ],
            },
            "expo-skills_anti_slop_only": {
                "files": [
                    f"{ext_root}/expo-skills/plugins/expo/skills/expo-design-system/references/native-slop.md",
                    f"{ext_root}/expo-skills/.claude/skills/expo-skill-eval/references/design-rubric.md",
                    f"{ext_root}/expo-skills/plugins/expo/skills/expo-web-to-native/references/false-friends.md",
                ],
                "forbidden_expo_features": ["expo-router", "eas-*", "react-native JS components"],
            },
            "shipswift": {
                "catalog": f"{ext_root}/shipswift/skills/catalog.md",
                "packages": shipswift_pkgs,
                "focused_packages": focused_shipswift,
            },
            "awesome-ios-design-md": {
                "root": f"{ext_root}/awesome-ios-design-md/design-md",
                "categories": [
                    "finance", "fitness", "music", "productivity", "social",
                    "travel", "messaging", "video", "food", "dating", "misc",
                ],
                "note": "200 real iOS apps with DESIGN-swiftui.md brand palettes, typography scales, and ButtonStyle springs.",
            },
            "genjutsu_native_only": {
                "files": genjutsu_all_files,
                "focused_files": genjutsu_focused,
                "choreography_tiers": {"cast": "single micro-interaction", "paint": "single screen or AppTheme", "bunshin": "multi-screen with two owner gates and shoot.mjs --self-test"},
                "forbidden_web_compose": ["css-native", "framer-motion", "gsap", "threejs-r3f", "compose-*"],
            },
            "claude-code-apple-skills": {
                "files": [
                    f"{ext_root}/claude-code-apple-skills/skills/design/game-feel/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/design-system/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/liquid-glass/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/animation-patterns/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/typography/SKILL.md",
                ],
            },
            "impeccable_native_only": {
                "ref_dir": f"{ext_root}/impeccable/.agents/skills/impeccable/reference",
                "files": [
                    "ios.md",
                    "audit.native.md",
                    "adapt.native.md",
                    "craft-floor.md",
                    "operate.md",
                ],
                "forbidden_web_features": ["impeccable detect", "impeccable live", "audit.md", "adapt.md"],
            },
            "ui-ux-pro-max_swiftui_only": {
                "allowed_cmd": f"python3 {ext_root}/ui-ux-pro-max-skill/.claude/skills/ui-ux-pro-max/scripts/search.py '<query>' --stack swiftui",
                "forbidden_domains": ["gsap", "google-fonts", "landing", "shadcn", "html-tailwind", "--design-system"],
            },
        }
        if args.platform == "desktop":
            allowed_native["bama_macos_only"] = {
                "files": [
                    f"{ext_root}/bama/skills/design/mac-design/SKILL.md",
                    f"{ext_root}/bama/skills/design/mac-design/design-rules.md",
                    f"{ext_root}/bama/skills/design/mac-design/toolbars.md",
                    f"{ext_root}/bama/skills/design/layout-grid/SKILL.md",
                    f"{ext_root}/bama/skills/design/design-from-app/measuring.md",
                ],
            }
        external_skill_routing = {
            "allowed_native": allowed_native,
            "allowed_native_sources": allowed_native,
            "blocked_web_only_sources": [
                "taste-skill",
                "ui_ux_pro_max_web",
                "impeccable_web",
                "expo_web",
                "apple_liquid_glass_web",
                *( ["bama_macos_only"] if args.platform == "mobile" else []),
            ],
        }
    else:
        has_tc = not dim_set or ("typography-copy" in dim_set or "TC" in dim_set)
        has_ln = not dim_set or ("layout-navigation" in dim_set or "LN" in dim_set)
        has_cl = not dim_set or ("color-palette" in dim_set or "CL" in dim_set)
        has_vs = not dim_set or ("visual-style" in dim_set or "VS" in dim_set)
        has_cp = not dim_set or ("component" in dim_set or "CP" in dim_set)
        has_dv = not dim_set or ("chart-dataviz" in dim_set or "DV" in dim_set)
        has_mi = not dim_set or ("motion-interaction" in dim_set or "MI" in dim_set)
        has_gm = not dim_set or ("glass-material" in dim_set or "GM" in dim_set)

        impeccable_root = f"{ext_root}/impeccable/skill/reference"
        impeccable_focused: list[str] = []
        if has_tc:
            impeccable_focused.extend([f"{impeccable_root}/typeset.md", f"{impeccable_root}/mode-read.md", f"{impeccable_root}/clarify.md"])
        if has_ln:
            impeccable_focused.extend([f"{impeccable_root}/layout.md", f"{impeccable_root}/region-map.md", f"{impeccable_root}/adapt.md"])
        if has_cl:
            impeccable_focused.extend([f"{impeccable_root}/colorize.md", f"{impeccable_root}/quieter.md", f"{impeccable_root}/bolder.md"])
        if has_vs or has_gm:
            impeccable_focused.extend([f"{impeccable_root}/craft-floor.md", f"{impeccable_root}/polish.md", f"{impeccable_root}/mode-read.md", f"{impeccable_root}/mode-persuade.md"])
        if has_cp:
            impeccable_focused.extend([f"{impeccable_root}/mode-operate.md", f"{impeccable_root}/harden.md"])
        if has_dv:
            impeccable_focused.append(f"{impeccable_root}/visualize.md")
        if has_mi:
            impeccable_focused.extend([f"{impeccable_root}/animate.md", f"{impeccable_root}/delight.md"])
        impeccable_focused = list(dict.fromkeys(impeccable_focused)) or [f"{impeccable_root}/craft-floor.md"]

        taste_root = f"{ext_root}/taste-skill/skills/taste-skill"
        taste_focused: list[str] = []
        if has_ln:
            taste_focused.extend([f"{taste_root}/references/layout-v4.md", f"{taste_root}/references/sections.md"])
        if has_cp:
            taste_focused.extend([f"{taste_root}/references/cards.md", f"{taste_root}/references/icons.md"])
        if has_vs or has_gm:
            taste_focused.extend([f"{taste_root}/references/backgrounds.md", f"{taste_root}/references/effects-usage.md"])
        if has_mi:
            taste_focused.extend([f"{taste_root}/references/motion.md", f"{taste_root}/references/moments.md"])
        if has_dv:
            taste_focused.append(f"{taste_root}/references/diagrams.md")
        if has_tc:
            taste_focused.extend([f"{taste_root}/references/copy.md", f"{taste_root}/references/copy-voice.md", f"{taste_root}/library/type.json"])
        if has_cl:
            taste_focused.append(f"{taste_root}/library/palettes.json")
        taste_focused = list(dict.fromkeys(taste_focused)) or [f"{taste_root}/references/layout-v4.md"]

        emil_root = f"{ext_root}/emilkowalski-skills"
        emil_focused: list[str] = []
        if has_mi:
            emil_focused.extend([
                f"{emil_root}/skills/animate/SKILL.md",
                f"{emil_root}/skills/animate/RECIPES.md",
                f"{emil_root}/skills/choreograph/SKILL.md",
                f"{emil_root}/skills/animation-performance/SKILL.md",
                f"{emil_root}/performance-cheatsheet.md",
            ])
        if has_cp or has_vs or has_ln or has_tc:
            emil_focused.extend([
                f"{emil_root}/ui-cheatsheet.md",
                f"{emil_root}/skills/emil-design-eng/SKILL.md",
                f"{emil_root}/skills/ask-sonner/SKILL.md",
            ])
        emil_focused = list(dict.fromkeys(emil_focused)) or [f"{emil_root}/ui-cheatsheet.md"]

        pro_max_root = f"{ext_root}/ui-ux-pro-max-skill/src/ui-ux-pro-max"
        pro_max_focused: list[str] = []
        if has_vs:
            pro_max_focused.append(f"{pro_max_root}/data/styles.csv")
        if has_cl:
            pro_max_focused.append(f"{pro_max_root}/data/colors.csv")
        if has_tc:
            pro_max_focused.append(f"{pro_max_root}/data/typography.csv")
        if has_dv:
            pro_max_focused.append(f"{pro_max_root}/data/charts.csv")
        if has_ln or has_cp:
            pro_max_focused.extend([
                f"{pro_max_root}/data/web-interface.csv",
                f"{pro_max_root}/data/ux-guidelines.csv",
                f"{pro_max_root}/data/stacks/html-tailwind.csv",
                f"{pro_max_root}/data/stacks/react.csv",
                f"{pro_max_root}/data/stacks/astro.csv",
            ])
        pro_max_focused = list(dict.fromkeys(pro_max_focused)) or [f"{pro_max_root}/data/web-interface.csv"]

        vercel_root = f"{ext_root}/vercel-agent-skills/skills"
        vercel_focused: list[str] = []
        if has_ln or has_cp or has_vs:
            vercel_focused.append(f"{vercel_root}/web-design-guidelines/SKILL.md")
        if has_tc:
            vercel_focused.append(f"{vercel_root}/writing-guidelines/SKILL.md")
        if has_mi:
            vercel_focused.append(f"{vercel_root}/react-view-transitions/SKILL.md")
        if has_cp:
            vercel_focused.extend([
                f"{vercel_root}/composition-patterns/SKILL.md",
                f"{vercel_root}/react-best-practices/SKILL.md",
            ])
        vercel_focused = list(dict.fromkeys(vercel_focused)) or [f"{vercel_root}/web-design-guidelines/SKILL.md"]

        hallmark_root = EXTERNAL_SKILL_SOURCES["web"]["hallmark"]
        hallmark_slices = WEB_DIMENSION_SLICES["hallmark"]
        hallmark_rel: list[str] = []
        for flag, dim_key in (
            (has_cl, "color-palette"),
            (has_vs, "visual-style"),
            (has_cp, "component"),
            (has_dv, "chart-dataviz"),
            (has_gm, "glass-material"),
            (has_mi, "motion-interaction"),
            (has_ln, "layout-navigation"),
            (has_tc, "typography-copy"),
        ):
            if flag:
                hallmark_rel.extend(hallmark_slices.get(dim_key, []))
        hallmark_focused = [
            f"{hallmark_root}/{rel}"
            for rel in (list(dict.fromkeys(hallmark_rel)) or ["craft.md", "gates.md"])
        ]

        web_design_md_root = f"{ext_root}/awesome-web-design-md/design-md"
        allowed_web = {
            "apple_liquid_glass_web": {
                "root": "~/Development/handoff/apple-liquid-glass-html-recipe",
                "focused_files": [
                    "~/Development/handoff/apple-liquid-glass-html-recipe/liquid-glass-recipe.css",
                    "~/Development/handoff/apple-liquid-glass-html-recipe/liquid-glass-engine.js",
                    "~/Development/handoff/apple-liquid-glass-html-recipe/standalone-snippet.html",
                ],
                "note": "Web HTML demo capability only; never included in native routes.",
            },
            "impeccable": {
                "root": impeccable_root,
                "focused_files": impeccable_focused,
            },
            "taste_skill": {
                "root": taste_root,
                "focused_files": taste_focused,
            },
            "emilkowalski": {
                "root": emil_root,
                "focused_files": emil_focused,
            },
            "ui_ux_pro_max": {
                "root": pro_max_root,
                "focused_files": pro_max_focused,
            },
            "awesome_web_design_md": {
                "root": web_design_md_root,
                "focused_files": [
                    f"{web_design_md_root}/linear/DESIGN.md",
                    f"{web_design_md_root}/stripe/DESIGN.md",
                    f"{web_design_md_root}/vercel/DESIGN.md",
                    f"{web_design_md_root}/notion/DESIGN.md",
                    f"{web_design_md_root}/claude/DESIGN.md",
                    f"{web_design_md_root}/mintlify/DESIGN.md",
                    f"{web_design_md_root}/raycast/DESIGN.md",
                    f"{web_design_md_root}/resend/DESIGN.md",
                ],
                "note": "84 real brand Web DESIGN.md systems.",
            },
            "vercel_agent_skills": {
                "root": vercel_root,
                "focused_files": vercel_focused,
            },
            "awesome_cjk_design_md": {
                "root": f"{ext_root}/awesome-cjk-design-md",
                "focused_files": [
                    f"{ext_root}/awesome-cjk-design-md/README.md",
                    f"{ext_root}/awesome-cjk-design-md/design-md",
                ] if (has_tc or has_ln or has_vs) else [f"{ext_root}/awesome-cjk-design-md/README.md"],
            },
            "vibecoded_design_tells": {
                "root": f"{ext_root}/vibecoded-design-tells/skill",
                "focused_files": [
                    f"{ext_root}/vibecoded-design-tells/skill/SKILL.md",
                    f"{ext_root}/vibecoded-design-tells/skill/references",
                ],
            },
            "hallmark": {
                "root": hallmark_root,
                "focused_files": hallmark_focused,
                "scripts": {
                    "theme_deal": f"{ext_root}/hallmark/skills/hallmark/scripts/theme.mjs",
                    "sloplint": f"{ext_root}/hallmark/skills/hallmark/scripts/sloplint.mjs",
                },
                "note": "50 material themes (theme.mjs deal: 50 pick 3), job-driven anatomy, and multi-viewport real-browser sloplint.mjs --render.",
            },
        }
        external_skill_routing = {
            "allowed_web": allowed_web,
            "allowed_web_sources": allowed_web,
            "blocked_native_only_sources": [
                "apple-design-skill / twostraws / avdlee / dimillian / bama / shipswift SwiftUI native APIs",
                "impeccable ios.md / audit.native.md",
            ],
        }

    payload = {
        "ok": True,
        "platform": args.platform,
        "dimensions": dims,
        "container": args.container,
        "persona": persona_name,
        "baseline_mode": cases_doc.get("baseline_mode", "plan-a-apple-hig"),
        "three_tier_spectrum": {
            "tier_1_universal_invariants": {
                "alignment_grid_hierarchy_no_truncation": True,
                "platform_navigation": "macOS NavigationSplitView/TopBar vs iOS NavigationStack/BottomDock vs Web semantic viewport",
            },
            "tier_2_product_persona": {
                "persona": persona_name,
                "scope": persona_profile["scope"],
                "max_dead_space_ratio": max_dead,
                "advisory_min_breathing_ratio": min_breath,
                "intent_weighting": persona_profile["intent_weighting"],
                "scope_isolation_rule": "Context-scoped visual budget only; never overwrite global defaults (0.40) from a single compact scene.",
            },
            "tier_3_exploration_quota": {
                "formula": "2+1+1",
                "variants": {
                    "v01_v02": "2 deepen Product Persona & Intent Weighting",
                    "v03": "1 cross-pollinates layout/interaction from design-studio",
                    "v04": "1 wildcard structural exploration",
                },
                "eval_layers": {
                    "L1": "Deterministic invariants & defect-case compliance (verify_design_cases.py)",
                    "L2": "Variant spatial/density differentiation & focal P0 intent weighting",
                    "L3": "Cross-session Owner selection/star taste feedback (preview-selection.json -> taste_evolution_loop.py)",
                },
            },
        },
        "external_skill_sources": external_skill_routing,
        "progressive_disclosure": {
            "distilled_in_plan_a": distilled_base,
            "distilled_note": "Already distilled in Plan A 5 Lenses; skip reading raw files by default.",
            "on_demand_external_refs": on_demand_external[:8],
            "external_hig_root": f"{ext_root}/apple-design-skill/references/hig",
            "external_skill_routing": external_skill_routing,
            "external_skill_sources": external_skill_routing,
            "external_repos_sync_cmd": (
                f"for r in apple-design-skill avdlee-swiftui-skill awesome-cjk-design-md "
                f"awesome-ios-design-md awesome-web-design-md bama claude-code-apple-skills "
                f"dimillian-skills emilkowalski-skills expo-skills genjutsu hallmark impeccable "
                f"shipswift taste-skill twostraws-swiftui-skill ui-ux-pro-max-skill "
                f"vercel-agent-skills vibecoded-design-tells; do "
                f"git -C {ext_root}/$r pull --ff-only; done"
            ),
            "appearance_ledger": "skills/my-designer/references/appearance-ledger.md",
            "interaction_layering_ref": (
                "skills/my-designer/references/interaction-layering.md" if disclose_layering else None
            ),
            "breathing_and_clarity_ref": (
                "skills/my-designer/references/breathing-and-clarity.md" if disclose_breathing else None
            ),
            "hig_files_to_load": hig_files[:12],
            "apple_native_primitives": native_primitives[:12],
            "context_budget": {
                "vault_search_top_k": 3,
                "l1_doc_max_lines": 130,
                "active_plan_c_cap": 15,
            },
        },
        "plan_a_lenses": PLAN_A_LENSES,
        "plan_c_active_cases": active_plan_c,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _next_defect_case_id(cases_doc: dict[str, Any]) -> str:
    numbers = [int(match.group(1)) for c in cases_doc.get("active_cases", [])
               if (match := re.fullmatch(r"DC-(\d+)", str(c.get("id", ""))))]
    return f"DC-{(max(numbers) if numbers else 0) + 1:03d}"


def command_register_case(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases_file)
    cases_doc = _load_json(cases_path)
    active = cases_doc.setdefault("active_cases", [])
    raw_id = (args.id or "").strip()
    if getattr(args, "auto_id", False) or not raw_id or raw_id.lower() == "auto":
        case_id = _next_defect_case_id(cases_doc)
    else:
        case_id = raw_id
    if any(c.get("id") == case_id for c in active):
        raise ValueError(f"defect case already exists: {case_id}")
    entry = {
        "id": case_id,
        "title": args.title,
        "platforms": _csv(args.platforms) or ["desktop", "mobile"],
        "dimensions": _csv(args.dimensions),
        "containers": _csv(args.containers),
        "symptom": args.symptom,
        "fix_rule": args.fix_rule,
        "forbidden_regex": args.forbidden_regex,
        "registered_at": dt.date.today().isoformat(),
    }
    active.append(entry)
    cases_doc["updated"] = dt.date.today().isoformat()
    _save_json(cases_path, cases_doc)
    print(json.dumps({"ok": True, "registered": entry, "active_count": len(active)}, ensure_ascii=False, indent=2))
    return 0


def command_flow_map(args: argparse.Namespace) -> int:
    cases_doc = _load_json(Path(args.cases_file))
    taxonomy = _load_taxonomy(args.taxonomy)
    ds_refs = ROOT.parent / "design-studio" / "scripts" / "data"
    desktop_vault = _read_json(ds_refs / "vault" / "desktop-macos.json") or {}
    mobile_vault = _read_json(ds_refs / "vault" / "mobile-ios.json") or {}
    web_vault = _read_json(ds_refs / "vault" / "web.json") or {}
    catalog = _read_json(ds_refs / "catalog.json") or {}

    l1_md_files = sorted(p for p in (ROOT / "references").glob("*.md") if not p.is_symlink())
    l1_line_counts = {
        f"skills/my-designer/references/{p.name}": len(p.read_text(encoding="utf-8").splitlines())
        for p in l1_md_files
    }
    max_l1_lines = 130
    active_cases_count = len(cases_doc.get("active_cases", []))
    all_within_budget = all(cnt <= max_l1_lines for cnt in l1_line_counts.values()) and active_cases_count <= 15

    payload = {
        "ok": all_within_budget,
        "architecture_doc": "skills/my-designer/references/design-flow-architecture.md",
        "reference_drawers": [path for path, _ in DRAWER_GUIDANCE.values()],
        "routing_index": DRAWER_GUIDANCE['routing'][0],
        "eval_cases": DRAWER_GUIDANCE['eval'][0],
        "entry_a_ingest": {
            "A0_raw_digest": "skills/knowledge-digest/SKILL.md -> ~/Development/Personal/knowledge-vault/",
            "A1_classify_learn": "python3 skills/design-studio/scripts/design_catalog.py classify --text '<raw>' --learn",
            "A2_vault_and_catalog_ingest": [
                "python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text '<raw>'",
                "python3 skills/design-studio/scripts/design_catalog.py ingest --text '<pattern>'",
            ],
            "A3_tier2_distillation": "Update <=130-line L1 tables in skills/my-designer/references/*.md (One-In, One-Out)",
            "A4_tier3_code_sinking": "Register a generic case with verify_design_cases.py register-case --id auto",
        },
        "shared_tiers": {
            "tier_0_raw_archives": [
                "~/Development/Personal/knowledge-vault/",
                "~/Development/Personal/collected-tools/repos/",
            ],
            "tier_1_indexed_vault": {
                "taxonomy_dimensions": len(taxonomy.get("dimensions", [])),
                "desktop_macos_cards": len(desktop_vault.get("inspirations", [])),
                "mobile_ios_cards": len(mobile_vault.get("inspirations", [])),
                "web_cards": len(web_vault.get("inspirations", [])),
                "catalog_patterns": len(catalog.get("patterns", [])),
                "catalog_tools": len(catalog.get("tools", [])),
            },
            "tier_2_l1_docs_line_counts": l1_line_counts,
            "tier_3_executable_gates": {
                "design_time_rules": "shared-ci/scripts/context/design_contract.py",
                "plan_c_active_cases": active_cases_count,
                "plan_c_case_ids": [c["id"] for c in cases_doc.get("active_cases", [])],
            },
        },
        "entry_b_design": {
            "B0_lock_coordinates": "--platform (desktop|mobile|web) --dimensions <1..4> [--container <host>]",
            "B1_drawer_retrieval": "python3 skills/design-studio/scripts/design_catalog.py vault-search --platform <p> --dimension <d> --query '<q>' --top 3",
            "B2_sliced_route": "python3 skills/my-designer/scripts/verify_design_cases.py route --platform <p> --dimensions <d>",
            "B3_direct_impl": "Author design.md/spec.md (AppTheme.swift + X-Y-Z layering) -> direct product implementation (Zero Demo)",
            "B4_static_and_visual_audit": [
                "python3 shared-ci/scripts/context/design_contract.py --audit-code --platform <p> <path>",
                "python3 shared-ci/scripts/context/design_contract.py --audit-l10n --locales en,zh-Hans <path>",
            ],
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_code_sinking": "shared-ci/scripts/context/design_contract.py",
            "valve_2_3d_coordinate_slicing": True,
            "valve_3_top_k_and_line_caps": {
                "vault_search_top_k": 3,
                "l1_doc_max_lines": max_l1_lines,
                "active_plan_c_cap": 15,
            },
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": all_within_budget,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all_within_budget else 1


INSTRUCTIONAL_MICRO_COPY_TEXT_RE = re.compile(
    r"(?:"
    r"(?:点击|点按|长按|按住|双击)[^\n\"'<>]{0,20}?"
    r"(?:即可|直接|原地|可|继续)?[^\n\"'<>]{0,8}?"
    r"(?:修改|编辑|切换|展开|收起|聚焦|置顶|追补|直补|录入|补充)"
    r"|操作\s*/\s*展示一体"
    r"|执行步骤树"
    r"|点击任务置顶聚焦"
    r"|标题与属性展示处直接点按修改"
    r"|点按文字原地修改"
    r"|右下麦克风语音直补"
    r"|按住右下麦克风继续追补"
    r"|点按任意条目切换至顶部聚焦"
    r")"
)
UI_INSTRUCTIONAL_CLUTTER_RE = re.compile(
    rf"(?:\b(?:Text|Label)\s*\(\s*\"[^\n\"]*{INSTRUCTIONAL_MICRO_COPY_TEXT_RE.pattern}[^\n\"]*\""
    rf"|>[^<>\n]*{INSTRUCTIONAL_MICRO_COPY_TEXT_RE.pattern}[^<>\n]*<)"
)
UI_EXPAND_STATE_MUTATION_RE = re.compile(
    r"(?:"
    r"\bshowLabel\s*:\s*[^\n,)]*\b(?:isExpanded|expanded)\b"
    r"|\bif\s+!\s*(?:isExpanded|expanded)\b[\s\S]{0,450}?\bshowLabel\s*:\s*false\b[\s\S]{0,450}?\belse\b[\s\S]{0,450}?\bshowLabel\s*:\s*true\b"
    r"|\bif\s+(?:isExpanded|expanded)\b[\s\S]{0,450}?\bshowLabel\s*:\s*true\b[\s\S]{0,450}?\belse\b[\s\S]{0,450}?\bshowLabel\s*:\s*false\b"
    r"|\b(?:minHeight|maxHeight|height)\s*:\s*[^\n,)]*\b(?:isExpanded|expanded)\s*\?"
    r"|\bif\s+(?:isExpanded|expanded)\s*\{\s*Text\s*\(\s*statusTitle\b"
    r")"
)
UI_HEADER_INTERLEAVED_INSERTION_RE = re.compile(
    r"\b\w*TitleHeaderRow\b(?:\s*\n\s*|\s*;\s*|\s+)if\s+(?:isExpanded|expanded)\b[\s\S]{0,260}?(?:\s*\n\s*|\s*;\s*|\s+)\b\w*(?:Metadata|Status|Priority)\w*Row\b"
)
UI_UNCLIPPED_PANEL_OVERLAP_RE = re.compile(
    r"\.transition\s*\([^\n;{}]*\.move\s*\(\s*edge\s*:\s*\.top\s*\)"
)


def _has_enclosing_clipped_reveal_container(content: str, match_start: int, match_end: int) -> bool:
    open_paren = content.find("(", match_start)
    cursor = match_end
    if open_paren != -1 and open_paren < match_end:
        depth = 1
        idx = open_paren + 1
        while idx < len(content) and depth > 0:
            if content[idx] == "(":
                depth += 1
            elif content[idx] == ")":
                depth -= 1
            idx += 1
        cursor = idx
    suffix = content[cursor : min(len(content), cursor + 260)]
    if re.match(r"^\s*\.(?:clipped|clipShape)\s*\(", suffix):
        return True
    prefix = content[max(0, match_start - 400) : match_start]
    for layout_match in re.finditer(r"\b\w*(?:ExpandReveal|Reveal)Layout\b[^{]*\{", prefix):
        brace_open = prefix.rfind("{", layout_match.start(), layout_match.end())
        if brace_open < 0:
            continue
        abs_open = max(0, match_start - 400) + brace_open
        depth = 1
        idx = abs_open + 1
        while idx < len(content) and depth > 0:
            if content[idx] == "{":
                depth += 1
            elif content[idx] == "}":
                depth -= 1
            idx += 1
        if idx >= cursor and re.match(r"^\s*\.(?:clipped|clipShape)\s*\(", content[idx : idx + 80]):
            return True
    return False
SUBTRACTIVE_MANDATE_RE = re.compile(
    r"(?:只保留|仅保留|去掉|砍掉|删除|移除|严禁出现|\bONLY\s+keep\b)",
    re.IGNORECASE,
)
NEGATIVE_ASSERTION_HEADING_RE = re.compile(
    r"^#{1,6}\s*[^\n]*(?:Negative\s+(?:DOM/View\s+)?Assertions?|减法禁用(?:断言|清单)|负向断言)[^\n]*$"
    r"|^\s*[-*]\s*\*\*(?:Negative\s+(?:DOM/View\s+)?Assertions?|减法禁用(?:断言|清单)|负向断言)\*\*\s*[:：]?",
    re.IGNORECASE | re.MULTILINE,
)
NEGATIVE_ASSERTION_EVIDENCE_RE = re.compile(
    r"(?:\bcount\s*==\s*0\b|!\s*\w[\w.]*\.exists\b|\bexists\s*==\s*false\b|\bXCTAssertFalse\b|\btoHaveCount\(\s*0\s*\))",
    re.IGNORECASE,
)
OBSOLETE_TEST_HEADING_RE = re.compile(
    r"^#{1,6}\s*[^\n]*Obsolete\s+Test\s+Manifest[^\n]*$"
    r"|^\s*[-*]\s*\*\*Obsolete\s+Test\s+Manifest\*\*\s*[:：]?",
    re.IGNORECASE | re.MULTILINE,
)
VAGUE_OBSOLETE_TEST_RE = re.compile(
    r"(?:无破坏性旧单测删除|无旧单测删除|无需删除旧测试|无冲突旧单测|^\s*none\s*$|^\s*n/a\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
CONCRETE_TEST_REF_RE = re.compile(
    r"(?:[A-Za-z0-9_./-]+(?:Tests?|Spec)\.[A-Za-z0-9]+(?::\d+)?"
    r"|\btest_[A-Za-z0-9_]+\.py(?::\d+)?"
    r"|[A-Za-z0-9_./-]+\.(?:swift|py|ts|tsx|js|jsx|mjs):\d+)"
)


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


def command_audit_spec(args: argparse.Namespace) -> int:
    raw_specs: list[str] = []
    for val in (getattr(args, 'spec_files_list', None), getattr(args, 'spec_file', None), getattr(args, 'positional_specs', None)):
        if isinstance(val, list):
            raw_specs.extend(str(v) for v in val if v and str(v) not in raw_specs)
        elif val and str(val) not in raw_specs:
            raw_specs.append(str(val))
    if not raw_specs:
        _, guidance = _build_self_disclosing_guidance(['modernization'])
        sys.stderr.write(
            "verify_design_cases.py audit-spec: error: provide <spec.md> or --spec-file/--file <spec.md>.\n"
            + "\n".join(guidance) + "\n"
        )
        return 2
    errors: list[str] = []
    findings: list[dict[str, Any]] = []
    resolved_files: list[str] = []
    for raw in raw_specs:
        spec_path = Path(raw).expanduser()
        resolved_files.append(str(spec_path))
        if not spec_path.is_file():
            errors.append(f"spec file not found: {spec_path}")
            continue
        text = spec_path.read_text(encoding="utf-8", errors="ignore")
        findings.extend(audit_subtractive_spec_text(text, str(spec_path)))
    ok = not errors and not findings
    payload: dict[str, Any] = {
        "ok": ok,
        "spec_file": resolved_files[0],
        "findings": findings,
    }
    if len(resolved_files) > 1:
        payload["spec_files"] = resolved_files
    if errors:
        payload["errors"] = errors
    if not ok:
        drawers, guidance = _build_self_disclosing_guidance(['modernization', 'navigation'])
        payload["reference_drawers"] = drawers
        payload["guidance"] = guidance
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1



def _jev_response(args, payload, platform):
    return JEV.response(args, payload, platform, TAXONOMY)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _platform_errors(platform, text):
    return EVIDENCE.platform_errors(platform, text)


def _gallery_errors(root, manifest):
    return EVIDENCE.gallery_errors(root, manifest)


def command_jev_gate(args):
    plat = getattr(args, 'platform', None) or getattr(args, 'top_platform', None) or 'desktop'
    args.platform = _normalize_platform(plat)
    errors, evidence = [], []
    if args.stage == 'gallery':
        if not args.gallery_dir:
            errors.append('gallery stage requires --gallery-dir')
        else:
            root = Path(args.gallery_dir)
            manifest = _read_json(root / 'manifest.json')
            if not isinstance(manifest, dict):
                errors.append('missing or malformed manifest.json')
            else:
                if manifest.get('platform') != args.platform:
                    errors.append('manifest platform does not match gate platform')
                found, evidence = _gallery_errors(root, manifest)
                errors.extend(found)
    else:
        try:
            if not args.spec_file:
                raise ValueError('spec/traceback stage requires --spec-file')
            text = Path(args.spec_file).read_text(encoding='utf-8')
            if args.stage == 'spec':
                for token in ('Axis X', 'Axis Y', 'Axis Z'):
                    if token not in text:
                        errors.append(f'missing {token}')
                if not re.search(r'(?i)(single\s+P0|one\s+P0|唯一\s*P0|P0[^\n]{0,40}(?:primary|per viewport|唯一))', text):
                    errors.append('missing single P0 focal evidence')
                for sub_err in audit_subtractive_spec_text(text, str(args.spec_file)):
                    errors.append(f"{sub_err['rule']}: {sub_err['message']}")
                errors.extend(_platform_errors(args.platform, text))
                evidence = [{'spec_text': text, 'platform': args.platform}]
            else:
                found, evidence = EVIDENCE.traceback_errors(text, base_dir=Path(args.spec_file).resolve().parent, platform=args.platform)
                errors.extend(found)
        except (OSError, ValueError) as error:
            errors.append(str(error))
    try:
        taxonomy = _load_taxonomy(args.taxonomy)
        if not isinstance(taxonomy, dict):
            raise ValueError('taxonomy root must be an object')
    except (OSError, ValueError) as error:
        errors.append(f'invalid taxonomy: {error}')
        taxonomy = {}
    payload = {'platform': args.platform, 'stage': args.stage, 'evidence': evidence, 'deterministic_errors': errors}
    jev = JEV.response(args, payload, args.platform, taxonomy)
    ok = not errors and jev['status'] == 'PASS'
    result: dict[str, Any] = {'ok': ok, 'platform': args.platform, 'stage': args.stage,
              'status': 'PASS' if ok else 'REJECT_REVISE', 'errors': errors, 'evidence': evidence, 'jev': jev}
    if not ok:
        stage_drawers = ['architecture', 'appearance'] if args.stage == 'gallery' else (
            ['navigation', 'breathing', 'modernization'] if args.stage == 'spec' else ['architecture', 'navigation']
        )
        drawers, guidance = _build_self_disclosing_guidance(['eval', *stage_drawers])
        result['reference_drawers'] = drawers
        result['guidance'] = guidance
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def command_inspect_json(args):
    path = Path(args.inspect_path).expanduser()
    try:
        data = _load_json(path)
    except (OSError, ValueError) as error:
        drawers, guidance = _build_self_disclosing_guidance(['routing', 'eval'])
        print(json.dumps({'ok': False, 'mode': 'soft_advisory', 'error': f'invalid JSON artifact: {error}',
                          'reference_drawers': drawers, 'guidance': guidance}, ensure_ascii=False))
        sys.stderr.write('\n'.join(guidance) + '\n')
        return 1
    kind = 'generic-json'
    if isinstance(data, dict):
        summary = {'keys': sorted(data), 'key_count': len(data),
                   'counts': {k: len(v) for k, v in data.items() if isinstance(v, (dict, list))}}
        if path.name.startswith('preview-selection') or 'selectedVariantID' in data:
            kind = 'preview-selection'
            summary.update({k: data.get(k) for k in ('selectedVariantID', 'starredVariantIDs', 'notesByVariant')})
        elif data.get('schema') == 'visual-traceback/v1':
            kind = 'visual-traceback/v1'
            summary.update({'findings': data.get('findings', []), 'rendered_evidence': data.get('rendered_evidence', [])})
        elif data.get('schema') == 'skill-eval-cases/v1' or 'active_cases' in data:
            kind = data.get('schema') or 'defect-cases/index'
            summary['active_cases'] = data.get('active_cases', [])
    else:
        summary = {'type': type(data).__name__, 'count': len(data) if isinstance(data, list) else 1}
    print(json.dumps({'ok': True, 'mode': 'soft_advisory', 'artifact_kind': kind, 'summary': summary}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = SelfDisclosingArgumentParser(add_help=False)
    common.add_argument("--cases-file", default=str(DEFAULT_CASES))
    common.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    common.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    parser = SelfDisclosingArgumentParser(description=__doc__, parents=[common])
    parser.add_argument("--check-whitespace-ratio", dest="top_whitespace_path", default=None, metavar="PNG_OR_DIR")
    parser.add_argument("--max-dead-space", "--max-ratio", dest="top_max_dead_space", type=float, default=None)
    parser.add_argument("--persona", dest="top_persona", default=None, help="Tier 2 Product Persona scope (compact|default|breathing)")
    parser.add_argument("--audit-geometry", dest="top_geometry_path", default=None, metavar="GEOMETRY_JSON")
    parser.add_argument("--check-section-interactivity", dest="top_interactivity_path", nargs="?", const="__SNIPPET_ONLY__", default=None, metavar="SWIFT_OR_DIR")
    parser.add_argument("--audit-code", dest="top_audit_code", nargs="+", action="extend", default=None, metavar="PATH")
    parser.add_argument("--audit-spec", dest="top_audit_spec", nargs="+", action="extend", default=None, metavar="SPEC_MD")
    parser.add_argument("--snippet", dest="top_snippet", default=None)
    parser.add_argument("--lookup-defect", dest="top_lookup_defect", default=None, metavar="KEYWORD")
    parser.add_argument("--inspect-json", dest="top_inspect_json", default=None, metavar="JSON_PATH")
    parser.add_argument("--collision-threshold", type=float, default=2.0)
    parser.add_argument("--platform", "--surface", dest="top_platform", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("flow-map", parents=[common])
    inspect = sub.add_parser("inspect-json", parents=[common], help="Read-only JSON fact collector; never certifies evidence")
    inspect.add_argument("inspect_path", metavar="JSON_PATH")

    route = sub.add_parser("route", parents=[common])
    route.add_argument("--platform", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    route.add_argument("--dimensions", "--dimension", default="")
    route.add_argument("--container", "--containers", default=None)
    route.add_argument("--persona", default=None, help="Tier 2 Product Persona scope (compact|default|breathing)")
    route.add_argument("--archetype", default=None, help="Alias for --persona / style archetype hint")
    route.add_argument("--surface", default=None, help="Platform alias; with --platform, may be a UI surface hint")
    route.add_argument("--path", default=None, help="Optional target source path hint")

    audit = sub.add_parser(
        "audit-code",
        aliases=["audit-swift", "check-code", "audit"],
        parents=[common],
        help="Scan Swift/Web source against active defect cases and instructional clutter",
    )
    audit.add_argument("positional_paths", nargs="*", default=[], metavar="PATH")
    audit.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    audit.add_argument("--path", "--paths", "--files", dest="path", nargs="+", action="extend", default=None)
    audit.add_argument("--root", dest="root_dir", default=None)
    audit.add_argument("--ui-path", dest="ui_path", default=None)
    audit.add_argument("--snippet", default=None)
    audit.add_argument("--spec-file", "--spec", dest="spec_files_list", action="append", default=None, metavar="SPEC_MD")
    audit.add_argument("--check-whitespace-ratio", default=None, metavar="PNG_OR_DIR")
    audit.add_argument("--max-dead-space", "--max-ratio", dest="max_dead_space", type=float, default=None)
    audit.add_argument("--persona", default=None)
    audit.add_argument("--audit-geometry", default=None, metavar="GEOMETRY_JSON")
    audit.add_argument("--collision-threshold", type=float, default=2.0)

    spec_audit = sub.add_parser("audit-spec", parents=[common], help="Audit design.md/spec.md for subtractive Negative DOM/View Assertions and concrete Obsolete Test Manifest")
    spec_audit.add_argument("positional_specs", nargs="*", default=[], metavar="SPEC_MD")
    spec_audit.add_argument("--spec-file", "--spec", "--file", "--path", dest="spec_files_list", action="append", default=None, metavar="SPEC_MD")

    ws = sub.add_parser("check-whitespace-ratio", parents=[common], help="Reject full-viewport screenshots with excessive bottom contiguous dead space")
    ws.add_argument("positional_paths", nargs="*", default=[], metavar="PNG_OR_DIR")
    ws.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    ws.add_argument("--path", "--dir", "--file", "--image", "--png-or-dir", "--check-whitespace-ratio", dest="path", action="append", default=None, metavar="PNG_OR_DIR")
    ws.add_argument("--max-dead-space", "--max-ratio", dest="max_dead_space", type=float, default=None)
    ws.add_argument("--persona", default=None, help="Context-scoped Tier 2 persona: compact (0.25), default (0.40), breathing (0.55 + >=0.12 advisory)")

    geom = sub.add_parser("audit-geometry", parents=[common], help="Audit subtree leaf AABB collisions (>2pt^2 -> GEOM_TEXT_COLLISION) and layout bounds")
    geom.add_argument("positional_paths", nargs="*", default=[], metavar="GEOMETRY_JSON")
    geom.add_argument("--path", "--file", "--audit-geometry", dest="path", action="append", default=None, metavar="GEOMETRY_JSON")
    geom.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    geom.add_argument("--collision-threshold", type=float, default=2.0)

    interactivity = sub.add_parser("check-section-interactivity", parents=[common], help="Reject static SwiftUI section/preview placeholders")
    interactivity.add_argument("positional_paths", nargs="*", default=[], metavar="SWIFT_OR_DIR")
    interactivity.add_argument("--path", "--file", dest="interactivity_path", default=None, metavar="SWIFT_OR_DIR")
    interactivity.add_argument("--snippet", default=None)

    lookup = sub.add_parser("lookup-defect", parents=[common], help="Query the deterministic defect-case index")
    lookup.add_argument("positional_query", nargs="*", default=[], metavar="KEYWORD")
    lookup.add_argument("--query", "--keyword", dest="query", default=None)
    lookup.set_defaults(lookup_defect=None)

    gate = sub.add_parser("jev-gate", parents=[common], help="Fail-closed structural and Jev truth-table gate")
    gate.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    gate.add_argument("--stage", choices=["gallery", "spec", "traceback"], required=True)
    gate.add_argument("--gallery-dir", "--dir", dest="gallery_dir", default=None)
    gate.add_argument("--spec-file", "--spec", "--file", dest="spec_file", default=None)
    gate.add_argument("--no-jev", action="store_true")
    gate.add_argument("--mock-jev-json")
    gate.add_argument("--jev-url", default="http://127.0.0.1:19823/v1/systemone")
    gate.add_argument("--jev-timeout", type=float, default=1.5)

    reg = sub.add_parser("register-case", parents=[common])
    reg.add_argument("--id", default="auto", help="Case ID (e.g. DC-005) or 'auto' to assign the next sequential DC-00N")
    reg.add_argument("--auto-id", action="store_true", help="Automatically assign the next sequential DC-00N ID")
    reg.add_argument("--title", required=True)
    reg.add_argument("--platforms", default="desktop,mobile")
    reg.add_argument("--dimensions", default="")
    reg.add_argument("--containers", default="")
    reg.add_argument("--symptom", required=True)
    reg.add_argument("--fix-rule", required=True)
    reg.add_argument("--forbidden-regex", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    platform = getattr(args, 'platform', None) or args.top_platform
    if args.command == 'route' and args.surface and not platform:
        if args.surface not in {'ios', 'macos', 'mobile', 'desktop', 'web'}:
            parser.error('--surface UI hints require --platform; otherwise use ios|macos|mobile|desktop|web')
        platform = args.surface
    args.platform = _normalize_platform(platform or 'desktop')
    if args.command == 'inspect-json' or args.top_inspect_json:
        if args.top_inspect_json:
            args.inspect_path = args.top_inspect_json
        return command_inspect_json(args)
    if args.command == "flow-map":
        return command_flow_map(args)
    if args.command == "route":
        return command_route(args)
    if args.command in {"audit-code", "audit-swift", "check-code", "audit"}:
        if args.snippet is None and getattr(args, "top_snippet", None) is not None:
            args.snippet = args.top_snippet
        if args.max_dead_space is None and getattr(args, "top_max_dead_space", None) is not None:
            args.max_dead_space = args.top_max_dead_space
        if args.persona is None and getattr(args, "top_persona", None) is not None:
            args.persona = args.top_persona
        return command_audit_code(args)
    if args.command == "audit-spec":
        return command_audit_spec(args)
    if args.command == "check-whitespace-ratio":
        if args.max_dead_space is None and getattr(args, "top_max_dead_space", None) is not None:
            args.max_dead_space = args.top_max_dead_space
        if args.persona is None and getattr(args, "top_persona", None) is not None:
            args.persona = args.top_persona
        return command_check_whitespace_ratio(args)
    if args.command == "audit-geometry":
        return command_audit_geometry(args)
    if args.command == "check-section-interactivity":
        if args.snippet is None and getattr(args, "top_snippet", None) is not None:
            args.snippet = args.top_snippet
        return command_check_section_interactivity(args)
    if args.command == "lookup-defect":
        return command_lookup_defect(args)
    if args.command == "register-case":
        return command_register_case(args)
    if args.command == "jev-gate":
        return command_jev_gate(args)
    if args.top_audit_code:
        args.path = args.top_audit_code
        args.positional_paths = []
        args.root_dir = None
        args.ui_path = None
        args.snippet = args.top_snippet
        args.spec_files_list = args.top_audit_spec
        args.check_whitespace_ratio = args.top_whitespace_path
        args.max_dead_space = args.top_max_dead_space
        args.persona = args.top_persona
        args.audit_geometry = args.top_geometry_path
        return command_audit_code(args)
    if args.top_audit_spec:
        args.spec_files_list = args.top_audit_spec
        args.spec_file = None
        args.positional_specs = []
        return command_audit_spec(args)
    if args.top_interactivity_path is not None:
        args.interactivity_path = None if args.top_interactivity_path == "__SNIPPET_ONLY__" else args.top_interactivity_path
        args.positional_paths = []
        args.snippet = args.top_snippet
        return command_check_section_interactivity(args)
    if args.top_lookup_defect:
        args.lookup_defect = args.top_lookup_defect
        args.query = None
        args.positional_query = []
        return command_lookup_defect(args)
    if args.top_whitespace_path or args.top_geometry_path:
        ws_errors, ws_metrics, ws_advisories = ([], [], [])
        geom_errors, geom_details = ([], [])
        drawer_keys: list[str] = []
        persona_name, max_dead, min_breath, profile = _resolve_persona_whitespace(args.top_persona, args.top_max_dead_space)
        if args.top_whitespace_path:
            ws_errors, ws_metrics = EVIDENCE.check_whitespace_ratio(args.top_whitespace_path, max_dead_space_ratio=max_dead)
            if min_breath > 0.0:
                for m in ws_metrics:
                    ratio = float(m.get('dead_space_ratio', 0.0))
                    if ratio < min_breath:
                        ws_advisories.append(
                            f"{Path(str(m.get('file', 'image'))).name}: SOFT_ADVISORY_CRAMPED_BREATHING "
                            f"(whitespace ratio {ratio:.1%} < {min_breath:.0%} floor for --persona breathing)"
                        )
            if ws_errors or ws_advisories:
                drawer_keys.extend(['breathing', 'appearance'])
        if args.top_geometry_path:
            geom_errors, geom_details = EVIDENCE.audit_geometry(args.top_geometry_path, platform=args.platform, collision_threshold_pt2=float(args.collision_threshold))
            if geom_errors:
                drawer_keys.extend(['navigation', 'interaction'])
        errors = [*ws_errors, *geom_errors]
        out_payload: dict[str, Any] = {
            'ok': not errors,
            'platform': args.platform,
            'persona': persona_name,
            'max_dead_space_ratio': max_dead,
            'errors': errors,
            'whitespace_metrics': ws_metrics,
            'geometry_details': geom_details,
        }
        if ws_advisories:
            out_payload['advisories'] = ws_advisories
        if errors or ws_advisories:
            drawers, guidance = _build_self_disclosing_guidance(drawer_keys or ['breathing'])
            out_payload['reference_drawers'] = drawers
            out_payload['guidance'] = guidance
        print(json.dumps(out_payload, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    parser.error("a subcommand or --audit-code / --audit-spec / --check-whitespace-ratio / --audit-geometry / --check-section-interactivity / --lookup-defect is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
