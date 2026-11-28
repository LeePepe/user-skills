from __future__ import annotations

import argparse
import datetime as dt
import json
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

from skill_runtime import compact_json_dumps, redact_payload, safe_read_text
from skill_runtime.fs_guard import _SF_DATALESS as RUNTIME_SF_DATALESS

SF_DATALESS = RUNTIME_SF_DATALESS

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
ROOT = _SCRIPT_DIR.parent
_BASE_ROOT = ROOT
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
        "Keep Content/Functional/Feedback layers separate, enforce Three-Layer Orthogonal Information Allocation (L1 Hero Verdict -> L2 Metric Tiles -> L3 Expanded Detail) with 55%–60% Hero + L2 Card Peek (zero instructional scroll prompts or cross-layer metric echoing), isolate outer Button triggers, keep persistent pills stable, and wrap reveals in clipped layouts.",
    ),
    "breathing": (
        "skills/my-designer/references/breathing-and-clarity.md",
        "Reserve one focal P0 per viewport, replace instructional/meta labels with Visual Semiotics (Bertin retinal variables, Tufte data-ink & sparklines, Few bullet-graph comparative ticks '|', Norman signifiers, Shneiderman overview-first drill-down), and scope compact vs breathing whitespace via --persona without mutating global defaults.",
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
        "Track native OS light/dark appearance automatically via semantic VS-*/CL-* and Tier 2 Product Persona tokens; forbid custom in-app theme switchers and manual .designTheme / Theme(isDark:) plumbing.",
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
    if cid in {"DC-020", "UI_CUSTOM_THEME_PLUMBING", "SPEC_GAP_CUSTOM_THEME_TOGGLE"}:
        keys.append("appearance")
    if cid in {"UI_CROSS_LAYER_METRIC_ECHO", "UI_SCROLL_PROMPT_CLUTTER", "SPEC_GAP_INSTRUCTIONAL_SCROLL_PROMPT"}:
        keys.extend(["interaction", "breathing"])
    if cid in {"DC-004", "DC-010", "DC-011", "DC-012", "DC-016", "UI_EXPAND_STATE_MUTATION", "UI_HEADER_INTERLEAVED_INSERTION", "UI_UNCLIPPED_PANEL_OVERLAP"} and "interaction" not in keys:
        keys.append("interaction")
    if cid in {"DC-001", "DC-002", "DC-007", "DC-009", "DC-014", "DC-021", "UI_INSTRUCTIONAL_CLUTTER", "UI_SPEC_ID_DEBUG_LEAK", "UI_DUPLICATE_MAP_OR_PRIMARY_CTA", "UI_COCKPIT_RAINBOW_NUMERAL_NOISE", "UI_MINI_SPAGHETTI_CHART_COLLAPSE"} and "breathing" not in keys:
        keys.append("breathing")
    if cid in {"DC-003", "DC-005", "DC-008", "DC-013", "DC-018"}:
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


def _read_json(path: Path | str) -> Any:
    try:
        target = Path(path)
        text = safe_read_text(target)
        if text is None:
            text = target.read_text(encoding="utf-8")
        return json.loads(text)
    except (OSError, ValueError):
        return None


def _load_json(path: Path) -> dict[str, Any]:
    target = Path(path)
    text = safe_read_text(target)
    if text is None:
        text = target.read_text(encoding="utf-8")
    return json.loads(text)

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
    safe_data = redact_payload(data)
    path.write_text(json.dumps(safe_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _mask_swift(text: str) -> str:
    """Hide comments and string literals while preserving line/column offsets."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/)')
    return pattern.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group()), text)


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
    for key in ('active_cases', 'archived_templates', 'archived_cases'):
        values = doc.get(key, [])
        if isinstance(values, list):
            entries.extend(item for item in values if isinstance(item, dict))
    return entries


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
UI_CUSTOM_THEME_PLUMBING_RE = re.compile(
    r"(?:\.\s*designTheme\s*\("
    r"|\.\s*environment\s*\(\s*\\\.\s*theme\s*,\s*Theme\s*\([^)]*\bisDark\s*:)"
)
SPEC_CUSTOM_THEME_TOGGLE_PROPOSAL_RE = re.compile(
    r"(?:"
    r"\b(?:add|include|provide|expose|build|implement|offer|render|place|support|wire|ship)\b[^\n.;]{0,70}?\b(?:custom\s+|in-app\s+|app-private\s+|manual\s+)?(?:light\s*/\s*dark|dark\s*/\s*light|dark-mode|light-mode|theme|appearance)\s+(?:toggle|switcher|selector|picker|mode\s+switch)\b"
    r"|\b(?:custom|in-app|app-private)\s+(?:light\s*/\s*dark\s+|dark\s*/\s*light\s+)?theme\s+(?:engine|state\s+machine|toggle|switcher|selector|picker)\b"
    r"|(?:新增|提供|内置|放置|增加|支持|实现)[^\n。；]{0,24}?(?:应用内|自定义|手动)?(?:深浅色|明暗|暗黑模式|主题)(?:切换(?:器|按钮|开关)?|开关|选择器)"
    r")",
    re.IGNORECASE,
)
SPEC_CUSTOM_THEME_NEGATION_RE = re.compile(
    r"(?:\b(?:no|zero|without|never|forbid(?:den|s)?|prohibit(?:ed|s)?|ban(?:ned|s)?|avoid|remove|strip|drop|delete|eliminate|do\s+not|don't|must\s+not)\b"
    r"|严禁|禁止|不提供|不设|移除|去掉|砍掉|删除|零自定义|无需)",
    re.IGNORECASE,
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

from .semiotics_gates import (
    INSTRUCTIONAL_MICRO_COPY_TEXT_RE,
    PHYSICAL_TELEMETRY_TOKEN_RE,
    SCROLL_EXPAND_PROMPT_TEXT_RE,
    SPEC_GAP_GUIDANCE,
    SPEC_SCROLL_PROMPT_NEGATION_RE,
    SPEC_SCROLL_PROMPT_PROPOSAL_RE,
    UI_CHART_SERIES_LITERAL_RE,
    UI_CHART_Y_METRIC_LABEL_RE,
    UI_CROSS_LAYER_METRIC_ECHO_FIX_RULE,
    UI_FOREGROUND_SERIES_BY_RE,
    UI_GENERIC_BUTTON_LABEL_RE,
    UI_INSTRUCTIONAL_CLUTTER_RE,
    UI_LARGE_FONT_RE,
    UI_MAP_COMPONENT_CALL_RE,
    UI_PRIMARY_NAV_CTA_RE,
    UI_PROMINENT_CTA_HINT_RE,
    UI_REPEATED_DETAIL_LABEL_RE,
    UI_SATURATED_COLOR_RE,
    UI_SCROLL_PROMPT_CLUTTER_FIX_RULE,
    UI_SCROLL_PROMPT_CLUTTER_RE,
    UI_SPEC_ID_DEBUG_LEAK_RE,
    _audit_cross_layer_metric_echo,
    _detect_scroll_prompt_spec_gaps,
    _extract_swift_button_calls,
    _has_deprecated_test_evidence,
    _spec_gap_warning,
    _strip_swift_comments_preserve_strings,
)


def _strip_preview_blocks(text: str, masked: str) -> tuple[str, str]:
    spans: list[tuple[int, int]] = []
    for preview in re.finditer(r"#Preview\b", masked):
        cursor = preview.end()
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == "(":
            depth = 1
            cursor += 1
            while cursor < len(masked) and depth > 0:
                depth += masked[cursor] == "("
                depth -= masked[cursor] == ")"
                cursor += 1
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == "{":
            end = _balanced_brace_end(masked, cursor)
            spans.append((preview.start(), end))
    if not spans:
        return text, masked
    t_chars = list(text)
    m_chars = list(masked)
    for s, e in spans:
        for i in range(s, min(e, len(t_chars))):
            if t_chars[i] != "\n":
                t_chars[i] = " "
                m_chars[i] = " "
    return "".join(t_chars), "".join(m_chars)


def _audit_visual_semiotics_swift_gates(text: str, masked: str, lines: list[str], path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    ref_breathing = DRAWER_GUIDANCE["breathing"][0]
    ref_interaction = DRAWER_GUIDANCE["interaction"][0]

    def _is_waived(start_idx: int, end_idx: int) -> bool:
        s_line = text.count("\n", 0, start_idx) + 1
        e_line = text.count("\n", 0, max(start_idx, end_idx - 1)) + 1
        return "design-contract-allow" in "\n".join(lines[max(0, s_line - 1):e_line])

    comment_free = _strip_swift_comments_preserve_strings(text)

    # 1. Bug 2: Spec ID & Debug Chrome Leak (UI_SPEC_ID_DEBUG_LEAK)
    for match in UI_SPEC_ID_DEBUG_LEAK_RE.finditer(comment_free):
        if _is_waived(match.start(), match.end()):
            continue
        line = comment_free.count("\n", 0, match.start()) + 1
        findings.append({
            "case_id": "UI_SPEC_ID_DEBUG_LEAK",
            "title": "Internal Spec ID or Debug Chrome Rendered on User-Facing UI Surface",
            "file": path,
            "line": line,
            "match": match.group(0).replace("\n", " ")[:240],
            "reference_drawer": ref_breathing,
            "fix_rule": "Apply Edward Tufte's Data-Ink Ratio & Don Norman's Signifiers: strip internal spec IDs ('A1 ·', '已选原子', '模式A ·', '[24/24格]', 'Preset 1') from user-facing Text/Label views.",
        })
    detail_matches = [m for m in UI_REPEATED_DETAIL_LABEL_RE.finditer(comment_free) if not _is_waived(m.start(), m.end())]
    if len(detail_matches) >= 3:
        m3 = detail_matches[2]
        line = text.count("\n", 0, m3.start()) + 1
        findings.append({
            "case_id": "UI_SPEC_ID_DEBUG_LEAK",
            "title": "Repeated '查看详情' Text Pills Clutter Cards Instead of Subtle Signifier",
            "file": path,
            "line": line,
            "match": f"{len(detail_matches)}x {m3.group(0).replace(chr(10), ' ')[:180]}",
            "reference_drawer": ref_breathing,
            "fix_rule": "Apply Don Norman's Signifiers & Ben Shneiderman's Mantra: replace repeated '查看详情' text pills (>=3 per file) with a single subtle trailing chevron/↗ signifier.",
        })

    # 2. Bug 3: Duplicate Map & Primary CTA Clutter (UI_DUPLICATE_MAP_OR_PRIMARY_CTA)
    no_prev_text, no_prev_masked = _strip_preview_blocks(comment_free, masked)
    view_candidates = [
        c for c in _swiftui_view_candidates(no_prev_text)
        if not re.search(r"(?:Preview|Switcher|Catalog|Showcase)", c["name"], re.I)
    ]
    scopes = view_candidates if view_candidates else [{"name": "<screen>", "start": 0, "end": len(no_prev_text), "source": no_prev_text, "masked": no_prev_masked}]
    reported_dup_map_or_cta = False
    for scope in scopes:
        s_offset = scope["start"]
        s_text = scope["source"]
        s_masked = scope["masked"]
        map_calls = [m for m in UI_MAP_COMPONENT_CALL_RE.finditer(s_masked) if not _is_waived(s_offset + m.start(), s_offset + m.end())]
        if len(map_calls) >= 2:
            m2 = map_calls[1]
            line = text.count("\n", 0, s_offset + m2.start()) + 1
            findings.append({
                "case_id": "UI_DUPLICATE_MAP_OR_PRIMARY_CTA",
                "title": "Multiple Map Canvases Stacked in Single View Hierarchy",
                "file": path,
                "line": line,
                "match": f"{len(map_calls)} map components in {scope['name']}: {s_text[m2.start():min(len(s_text), m2.end() + 40)].replace(chr(10), ' ')}",
                "reference_drawer": ref_breathing,
                "fix_rule": "Apply Ben Shneiderman's Overview-First Mantra & Tier 1 Single Focal P0: mount at most 1 Map canvas per screen viewport.",
            })
            reported_dup_map_or_cta = True
            break
        cta_calls = [m for m in UI_PRIMARY_NAV_CTA_RE.finditer(s_text) if not _is_waived(s_offset + m.start(), s_offset + m.end())]
        if len(cta_calls) >= 2:
            m2 = cta_calls[1]
            line = text.count("\n", 0, s_offset + m2.start()) + 1
            findings.append({
                "case_id": "UI_DUPLICATE_MAP_OR_PRIMARY_CTA",
                "title": "Duplicate Full-Width Primary Navigation CTA Buttons in Single View",
                "file": path,
                "line": line,
                "match": s_text[m2.start():m2.end()].replace("\n", " ")[:240],
                "reference_drawer": ref_breathing,
                "fix_rule": "Reserve exactly 1 primary P0 navigation CTA ('开始导航' / '立即出发') per viewport; demote secondary card actions.",
            })
            reported_dup_map_or_cta = True
            break
        generic_cta_by_label: dict[str, list[dict[str, Any]]] = {}
        for btn_call in _extract_swift_button_calls(s_text, s_masked):
            if _is_waived(s_offset + btn_call["start"], s_offset + btn_call["end"]):
                continue
            label_text = btn_call["label"]
            if label_text and btn_call["is_prominent"]:
                generic_cta_by_label.setdefault(label_text, []).append(btn_call)
        dup_generic = next((matches for matches in generic_cta_by_label.values() if len(matches) >= 2), None)
        if dup_generic is not None:
            b2 = dup_generic[1]
            line = text.count("\n", 0, s_offset + b2["start"]) + 1
            findings.append({
                "case_id": "UI_DUPLICATE_MAP_OR_PRIMARY_CTA",
                "title": "Duplicate Full-Width or Prominent Primary CTA Buttons in Single View",
                "file": path,
                "line": line,
                "match": b2["match_preview"],
                "reference_drawer": ref_breathing,
                "fix_rule": "Reserve at most 1 primary P0 CTA button per viewport; demote secondary card actions to quiet inline or secondary controls.",
            })
            reported_dup_map_or_cta = True
            break
    if not reported_dup_map_or_cta:
        all_ctas = [m for m in UI_PRIMARY_NAV_CTA_RE.finditer(no_prev_text) if not _is_waived(m.start(), m.end())]
        if len(all_ctas) >= 2 and not re.search(r"\bswitch\s+\w*(?:variant|tab|mode)\b", no_prev_masked, re.I):
            m2 = all_ctas[1]
            line = text.count("\n", 0, m2.start()) + 1
            findings.append({
                "case_id": "UI_DUPLICATE_MAP_OR_PRIMARY_CTA",
                "title": "Duplicate Primary Navigation CTA Buttons Across Composite Screen",
                "file": path,
                "line": line,
                "match": no_prev_text[m2.start():m2.end()].replace("\n", " ")[:240],
                "reference_drawer": ref_breathing,
                "fix_rule": "Reserve exactly 1 primary P0 navigation CTA ('开始导航' / '立即出发') per screen; remove redundant bottom/inline primary CTAs.",
            })

    # 3. Bug 4: Cockpit Rainbow Numeral Noise (UI_COCKPIT_RAINBOW_NUMERAL_NOISE)
    large_font_matches = list(UI_LARGE_FONT_RE.finditer(no_prev_masked))
    if large_font_matches:
        rainbow_colors: set[str] = set()
        first_color_match = None
        for lf in large_font_matches:
            win_start = max(0, lf.start() - 180)
            win_end = min(len(no_prev_masked), lf.end() + 220)
            if _is_waived(win_start, win_end):
                continue
            for cm in re.finditer(r"\.(?:foregroundStyle|foregroundColor)\s*\(\s*\.(green|blue|orange|red|cyan|purple|yellow|mint|pink|indigo|teal)\b", no_prev_masked[win_start:win_end]):
                rainbow_colors.add(cm.group(1))
                if first_color_match is None:
                    first_color_match = (win_start + cm.start(), cm.group(0))
        if len(rainbow_colors) < 4 and re.search(r"(?:Cockpit|Telemetry|Computer|WidgetGrid|DashboardGrid|BentoGrid|MetricTile|MetricCard)", no_prev_text, re.I):
            for cm in re.finditer(r"(?:\b(?:color|accent|tint|hue)\s*:\s*|\.(?:foregroundStyle|foregroundColor)\s*\(\s*)\.(green|blue|orange|red|cyan|purple|yellow|mint|pink|indigo|teal)\b", no_prev_masked):
                if not _is_waived(cm.start(), cm.end()):
                    rainbow_colors.add(cm.group(1))
                    if first_color_match is None:
                        first_color_match = (cm.start(), cm.group(0))
        if len(rainbow_colors) >= 4 and first_color_match is not None:
            line = text.count("\n", 0, first_color_match[0]) + 1
            findings.append({
                "case_id": "UI_COCKPIT_RAINBOW_NUMERAL_NOISE",
                "title": "Rainbow-Colored Large Primary Numerals Destroy Single Focal P0 Hierarchy",
                "file": path,
                "line": line,
                "match": f"rainbow numeral colors ({', '.join(sorted(rainbow_colors))}) around {first_color_match[1]}",
                "reference_drawer": ref_breathing,
                "fix_rule": "Apply Jacques Bertin's Retinal Variables & Edward Tufte's Data-Ink Ratio: keep primary large numerals (>=24pt) in monochrome .primary, reserving chromatic hue strictly for bottom Bullet Graph zone strips or threshold breach badges.",
            })

    # 4. Bug 5: Mini Spaghetti Chart Collapse (UI_MINI_SPAGHETTI_CHART_COLLAPSE)
    for frame_match in re.finditer(r"\.frame\s*\([^)]*\b(?:height|maxHeight)\s*:\s*([1-7]\d)\b[^)]*\)", no_prev_masked):
        if _is_waived(frame_match.start(), frame_match.end()):
            continue
        win_start = max(0, frame_match.start() - 550)
        win_end = min(len(no_prev_masked), frame_match.end() + 350)
        window_masked = no_prev_masked[win_start:win_end]
        window_text = no_prev_text[win_start:win_end]
        if not re.search(r"\b(?:Chart|Canvas|LineMark|Path)\b", window_masked):
            continue
        line_marks = len(re.findall(r"\bLineMark\s*\(", window_masked))
        strokes = len(re.findall(r"\.stroke\s*\(", window_masked))
        fg_by_calls = len(UI_FOREGROUND_SERIES_BY_RE.findall(window_masked))
        distinct_series_literals = {m.group(1).strip() for m in UI_CHART_SERIES_LITERAL_RE.finditer(window_text)}
        distinct_y_labels = {m.group(1).strip() for m in UI_CHART_Y_METRIC_LABEL_RE.finditer(window_text)}
        multi_series_tokens = sum(
            1 for tok in ("海拔", "速度", "心率", "功率", "踏频", "elevation", "speed", "heartRate", "power", "cadence")
            if re.search(rf"\b{tok}\b" if tok.isascii() else tok, window_text, re.I)
        )
        if (
            line_marks >= 3
            or strokes >= 3
            or fg_by_calls >= 3
            or len(distinct_series_literals) >= 3
            or len(distinct_y_labels) >= 3
            or (line_marks >= 1 and multi_series_tokens >= 3)
        ):
            line = text.count("\n", 0, frame_match.start()) + 1
            findings.append({
                "case_id": "UI_MINI_SPAGHETTI_CHART_COLLAPSE",
                "title": "3+ Multi-Unit Line Series Overlaid in Compact (<80pt) Chart Box (Spaghetti Collapse)",
                "file": path,
                "line": line,
                "match": no_prev_text[frame_match.start():frame_match.end()].replace("\n", " ")[:240],
                "reference_drawer": ref_breathing,
                "fix_rule": "Apply Edward Tufte's Synchronized Small Multiples: never overlay >=3 multi-unit curves (e.g. elevation + speed + heart rate) in a <80pt box; split into stacked single-series micro-sparkline rows.",
            })
            break

    # 5. Bug 6: Cross-Layer Telemetry Metric Duplication Across L1 Hero and L2 Tiles (UI_CROSS_LAYER_METRIC_ECHO)
    findings.extend(
        _audit_cross_layer_metric_echo(no_prev_text, text, _is_waived, path, ref_interaction, view_scopes=scopes)
    )

    return findings
