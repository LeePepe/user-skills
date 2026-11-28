from __future__ import annotations

import re
from typing import Any

SCROLL_EXPAND_PROMPT_TEXT_RE = re.compile(
    r"(?:"
    r"(?:[⌄∨↓↑]\s*)?(?:下拉|上滑|向上滑动|向下滑动|滑动)[^\n\"'<>]{0,16}?(?:查看|展开|浏览|更多|详情|分项|指标)"
    r"|(?:点击|点按|轻触)[^\n\"'<>]{0,8}?(?:卡片|磁贴|模块|任意卡片)[^\n\"'<>]{0,6}?(?:展开|查看)(?:详情|分项|指标|更多|全部)?"
    r"|(?:点击|点按|轻触)(?:展开|查看)(?:详情|分项指标|卡片详情|更多详情)"
    r"|\b(?:scroll\s+down|swipe\s+up|pull\s+down)\s+to\s+(?:view|see|explore|expand)\s+(?:more|details|metrics|cards)\b"
    r"|\btap\s+(?:any\s+)?(?:card|tile)\s+to\s+(?:expand|view)\s+details\b"
    r")",
    re.IGNORECASE,
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
    rf"|{SCROLL_EXPAND_PROMPT_TEXT_RE.pattern}"
    r")",
    re.IGNORECASE,
)

UI_INSTRUCTIONAL_CLUTTER_RE = re.compile(
    rf"(?:\b(?:Text|Label)\s*\(\s*\"[^\n\"]*{INSTRUCTIONAL_MICRO_COPY_TEXT_RE.pattern}[^\n\"]*\""
    rf"|>[^<>\n]*{INSTRUCTIONAL_MICRO_COPY_TEXT_RE.pattern}[^<>\n]*<)",
    re.IGNORECASE,
)

UI_SCROLL_PROMPT_CLUTTER_RE = re.compile(
    rf"(?:\b(?:Text|Label)\s*\(\s*\"[^\n\"]*{SCROLL_EXPAND_PROMPT_TEXT_RE.pattern}[^\n\"]*\""
    rf"|>[^<>\n]*{SCROLL_EXPAND_PROMPT_TEXT_RE.pattern}[^<>\n]*<)",
    re.IGNORECASE,
)

SPEC_SCROLL_PROMPT_PROPOSAL_RE = re.compile(
    r"(?:"
    r"(?:[⌄∨↓↑]\s*)?(?:下拉|上滑|向上滑动|向下滑动|滑动)[^\n\"'<>。；]{0,16}?(?:查看|展开|浏览)(?:分项|指标|详情|更多|卡片|内容)"
    r"|(?:点击|点按|轻触)\s*(?:卡片|磁贴|模块|任意卡片)\s*(?:展开|查看)(?:详情|分项|指标|更多)"
    r"|(?:点击|点按|轻触)(?:展开|查看)(?:详情|分项指标|卡片详情|更多详情)"
    r"|\b(?:render|show|display|place|add|include|print)\b[^\n.;]{0,50}?\b(?:scroll\s+down|swipe\s+up|pull\s+down|tap\s+(?:any\s+)?(?:card|tile))\s+to\s+(?:view|see|expand)\b"
    r")",
    re.IGNORECASE,
)

SPEC_SCROLL_PROMPT_NEGATION_RE = re.compile(
    r"(?:\b(?:no|zero|without|never|forbid(?:den|s)?|prohibit(?:ed|s)?|ban(?:ned|s)?|avoid|remove|strip|drop|delete|eliminate|do\s+not|don't|must\s+not|anti-pattern|counter-case|bad|wrong|instead\s+of)\b"
    r"|严禁|禁止|不写|不显示|不提供|不设|不加|不放|不出现|移除|去掉|砍掉|删除|零|无需|避免|反例|错误|不得"
    r"|count\s*==\s*0|exists\s*==\s*false|!\s*\w|\bUI_SCROLL_PROMPT_CLUTTER\b|\bUI_INSTRUCTIONAL_CLUTTER\b|\bDC-014\b|\bSPEC_GAP_INSTRUCTIONAL_SCROLL_PROMPT\b)",
    re.IGNORECASE,
)

UI_SPEC_ID_DEBUG_LEAK_RE = re.compile(
    r"\b(?:Text|Label)\s*\(\s*\"[^\n\"]*(?:"
    r"\b[A-D]\d{1,2}(?:\s*[左中右上下底顶]?卡)?\s*[·:：]"
    r"|已选原子\s*[:：]?"
    r"|模式\s*[A-D]\s*[·:：]"
    r"|\[\s*\d+\s*/\s*\d+\s*格\s*\]"
    r"|\bPreset\s*\d+\b"
    r")[^\n\"]*\""
)
UI_REPEATED_DETAIL_LABEL_RE = re.compile(
    r"\b(?:Text|Label)\s*\(\s*\"[^\n\"]*查看详情[^\n\"]*\""
)
UI_MAP_COMPONENT_CALL_RE = re.compile(
    r"(?<!func\s)(?<!struct\s)\b(?:Map|MKMapView|\w*(?:RouteMap|MiniMap|MapCanvas|MapPreview|MapCard)\w*)\s*\("
)
UI_PRIMARY_NAV_CTA_RE = re.compile(
    r"\b(?:Button|Text|Label)\s*\([^\n{}]*\"[^\n\"]*(开始导航|立即出发|开始骑行|Start\s+Navigation)[^\n\"]*\""
)
UI_SATURATED_COLOR_RE = re.compile(
    r"\.\s*(green|blue|orange|red|cyan|purple|yellow|mint|pink|indigo|teal)\b"
)
UI_LARGE_FONT_RE = re.compile(
    r"\.font\s*\(\s*(?:\.system\s*\(\s*size\s*:\s*(?:2[4-9]|[3-9]\d)\b|\.(?:largeTitle|title|title2)\b)"
)

PHYSICAL_TELEMETRY_TOKEN_RE = re.compile(
    r"(?:"
    r"(?<!\d)-?\d+(?:\.\d+)?\s*(?:°[CF]|℃)"
    r"|(?<!\d)\d+(?:\.\d+)?\s*(?:km/h|mph|m/s)\b"
    r"|(?<!\d)\d+(?:\.\d+)?\s*km\b(?!/h)"
    r"|(?<!\d)\d+\.\d+\s*(?:h|hr|hrs|小时)\b"
    r"|(?:睡眠|时长|用时)\s*\d+(?:\.\d+)?\s*(?:h|hr|hrs|小时)\b"
    r"|(?:已?恢复|湿度|降水|血氧|电量)\s*\d+(?:\.\d+)?\s*%"
    r"|\d+(?:\.\d+)?\s*%\s*(?:恢复|湿度|降水|血氧|电量)"
    r")",
    re.IGNORECASE,
)

SPLIT_METRIC_PAIR_RE = re.compile(
    r"\"(-?\d+(?:\.\d+)?)\"[\s\S]{0,140}?\"(°[CF]|℃|km/h|mph|m/s|km|h|hr|hrs|小时)\""
    r"|\"(?:已?恢复|湿度|降水|血氧|电量)\"[\s\S]{0,140}?\"(\d+(?:\.\d+)?\s*%)\""
)

UI_CROSS_LAYER_METRIC_ECHO_FIX_RULE = (
    "Enforce Three-Layer Orthogonal Information Allocation (L1 Hero Verdict -> L2 Metric Tiles -> L3 Expanded Detail): "
    "keep L1 Hero restricted to composite score + qualitative verdict + top-level dimension weights; "
    "assign concrete telemetry readings (e.g. 19°C, 14km/h, 35.2km, 7.6h) exclusively to L2 Bento tiles with zero cross-layer repetition."
)

UI_SCROLL_PROMPT_CLUTTER_FIX_RULE = (
    "Apply Don Norman's Spatial Signifiers & Card-Peek Choreography: remove instructional scroll/expand prompts "
    "('下拉查看...', '向上滑动查看...', '点击卡片展开详情'); size L1 Hero to ~55%–60% of the viewport so the first row of L2 Bento tiles "
    "naturally peeks into the bottom of the initial viewport."
)


def _normalize_telemetry_token(raw_token: str) -> str:
    compact = re.sub(r"\s+", "", raw_token).replace("℃", "°C")
    num_match = re.search(r"-?\d+(?:\.\d+)?", compact)
    if not num_match:
        return compact.lower()
    num = num_match.group(0)
    lower = compact.lower()
    if lower.endswith("°c"):
        return f"{num}°C"
    if lower.endswith("°f"):
        return f"{num}°F"
    if lower.endswith("km/h"):
        return f"{num}km/h"
    if lower.endswith("mph"):
        return f"{num}mph"
    if lower.endswith("m/s"):
        return f"{num}m/s"
    if lower.endswith("km"):
        return f"{num}km"
    if lower.endswith(("h", "hr", "hrs", "小时")):
        return f"{num}h"
    for label in ("恢复", "湿度", "降水", "血氧", "电量"):
        if label in compact and "%" in compact:
            return f"{label}{num}%"
    return compact


def _strip_swift_comments_preserve_strings(text: str) -> str:
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|(//[^\n]*|/\*[\s\S]*?\*/)')
    return pattern.sub(
        lambda m: m.group(1) if m.group(1) is not None else "".join("\n" if c == "\n" else " " for c in m.group(2)),
        text,
    )


def _audit_cross_layer_metric_echo(
    no_prev_text: str,
    full_text: str,
    is_waived,
    path: str,
    ref_interaction: str,
) -> list[dict[str, Any]]:
    comment_free = _strip_swift_comments_preserve_strings(no_prev_text)
    string_re = re.compile(r'"([^"\n\\]*(?:\\.[^"\n\\]*)*)"')
    non_ui_prefix_re = re.compile(
        r"(?:\b(?:accessibilityIdentifier|print|debugPrint|NSLog|Logger|fatalError|precondition|assert)\s*\(\s*"
        r"|\b(?:systemName|named|font|identifier|id)\s*:\s*)$"
    )
    token_occurrences: dict[str, list[tuple[int, str]]] = {}

    for sm in string_re.finditer(comment_free):
        if is_waived(sm.start(), sm.end()):
            continue
        prefix = comment_free[max(0, sm.start() - 48) : sm.start()]
        if non_ui_prefix_re.search(prefix):
            continue
        literal_val = sm.group(1)
        seen_in_literal: set[str] = set()
        for tm in PHYSICAL_TELEMETRY_TOKEN_RE.finditer(literal_val):
            norm = _normalize_telemetry_token(tm.group(0))
            if norm not in seen_in_literal:
                seen_in_literal.add(norm)
                token_occurrences.setdefault(norm, []).append((sm.start(), literal_val))

    if token_occurrences:
        for pm in SPLIT_METRIC_PAIR_RE.finditer(comment_free):
            if is_waived(pm.start(), pm.end()):
                continue
            if pm.group(1) and pm.group(2):
                norm = _normalize_telemetry_token(f"{pm.group(1)}{pm.group(2)}")
            else:
                label_m = re.search(r"恢复|湿度|降水|血氧|电量", pm.group(0))
                norm = _normalize_telemetry_token(f"{label_m.group(0) if label_m else '恢复'}{pm.group(3)}")
            if norm in token_occurrences:
                existing_positions = [pos for pos, _ in token_occurrences[norm]]
                if all(abs(pm.start() - pos) > 8 for pos in existing_positions):
                    token_occurrences[norm].append((pm.start(), pm.group(0).replace("\n", " ")[:80]))

    echoed_tokens = [
        (token, occs)
        for token, occs in token_occurrences.items()
        if len(occs) >= 2
    ]
    if not echoed_tokens:
        return []

    first_token, first_occs = echoed_tokens[0]
    second_pos, second_lit = first_occs[1]
    line = full_text.count("\n", 0, second_pos) + 1
    summary_tokens = ", ".join(f"{tok} ({len(occs)}x)" for tok, occs in echoed_tokens[:4])
    return [
        {
            "case_id": "UI_CROSS_LAYER_METRIC_ECHO",
            "title": "Cross-Layer Telemetry Metric Duplication Across L1 Hero and L2 Tiles",
            "file": path,
            "line": line,
            "match": f"repeated telemetry metrics [{summary_tokens}] in {second_lit!r}"[:240],
            "reference_drawer": ref_interaction,
            "fix_rule": UI_CROSS_LAYER_METRIC_ECHO_FIX_RULE,
        }
    ]


def _detect_scroll_prompt_spec_gaps(
    content: str,
    source: str,
    build_guidance_fn,
    spec_gap_warning_fn,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        if "design-contract-allow" in raw_line:
            continue
        match = SPEC_SCROLL_PROMPT_PROPOSAL_RE.search(raw_line)
        if match and not SPEC_SCROLL_PROMPT_NEGATION_RE.search(raw_line):
            _, scroll_guidance = build_guidance_fn(["interaction", "breathing"])
            warnings.append(
                spec_gap_warning_fn(
                    "SPEC_GAP_INSTRUCTIONAL_SCROLL_PROMPT",
                    "spatial_signifiers",
                    source,
                    "handoff proposes instructional scroll/expand prompt copy ('下拉查看...' / '向上滑动查看...' / '点击卡片展开...'); size L1 Hero to 55%–60% of the viewport and rely on L2 Card Peek spatial signifiers instead",
                    guidance=scroll_guidance,
                )
            )
            break
    return warnings


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
        body = clause.group("inline")
        if clause.group("heading"):
            tail = content[clause.end() :]
            next_heading = re.search(r"(?m)^[ \t]*#{1,6}[ \t]+\S", tail)
            body += "\n" + (tail[: next_heading.start()] if next_heading else tail)
        if re.search(r"(?i)\b(?:N/?A|none|greenfield)\b|\bno (?:obsolete|deprecated) tests\b", body):
            return True
        if re.search(
            r"[A-Za-z0-9_./-]+\.(?:swift|py|tsx?|jsx?|mjs)(?::\d+)?\b"
            r"|[A-Za-z0-9_./-]+:\d+\b"
            r"|\btest_[A-Za-z0-9_]+\b|\btest[A-Z][A-Za-z0-9_]*\b"
            r"|`[A-Za-z_][A-Za-z0-9_.:()\[\]-]*`",
            body,
        ):
            return True
    return False
