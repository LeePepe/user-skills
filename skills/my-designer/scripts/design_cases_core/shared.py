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
