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
    r"(?:修改|编辑|切换|收起|聚焦|置顶|追补|直补|录入|补充)"
    r"|(?:点击|点按|长按|按住|双击)(?![^\n\"'<>]{0,12}?(?:卡片|磁贴|模块|任意卡片|展开详情|查看详情|查看分项|查看更多))[^\n\"'<>]{0,20}?(?:即可|直接|原地|可|继续)[^\n\"'<>]{0,8}?展开"
    r"|操作\s*/\s*展示一体"
    r"|执行步骤树"
    r"|点击任务置顶聚焦"
    r"|标题与属性展示处直接点按修改"
    r"|点按文字原地修改"
    r"|右下麦克风语音直补"
    r"|按住右下麦克风继续追补"
    r"|点按任意条目切换至顶部聚焦"
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
UI_PRIMARY_NAV_CTA_VERB_RE = re.compile(
    r"(?:开始导航|立即出发|开始骑行|\bStart\s+Navigation\b)",
    re.IGNORECASE,
)
UI_PROMINENT_CTA_STYLE_RE = re.compile(
    r"(?:\.buttonStyle\s*\(\s*\.(?:borderedProminent|glassProminent)\b"
    r"|\bmaxWidth\s*:\s*\.infinity\b"
    r"|\.(?:background|overlay)\s*\([^\n{}]*\b(?:Capsule|RoundedRectangle)\b[^\n{}]*\.fill\s*\()",
    re.IGNORECASE,
)
UI_GENERIC_BUTTON_LABEL_RE = re.compile(
    r"\bButton\b(?:\s*\(\s*\"([^\n\"]{2,40})\"|\s*\([^\n{}]*?\)\s*\{\s*(?:Label|Text)\s*\(\s*\"([^\n\"]{2,40})\")"
)
UI_PROMINENT_CTA_HINT_RE = UI_PROMINENT_CTA_STYLE_RE
UI_FOREGROUND_SERIES_BY_RE = re.compile(
    r"\.foregroundStyle\s*\(\s*by\s*:\s*\.value\s*\([^)]+\)\s*\)"
)
UI_CHART_SERIES_LITERAL_RE = re.compile(
    r"\.foregroundStyle\s*\(\s*by\s*:\s*\.value\s*\(\s*\"[^\"]+\"\s*,\s*\"([^\"]+)\"\s*\)\s*\)"
)
UI_CHART_Y_METRIC_LABEL_RE = re.compile(
    r"\by\s*:\s*\.value\s*\(\s*\"([^\"]+)\""
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


_ECHO_L1_CONTAINER_RE = re.compile(
    r"(?:Hero|Header|Banner|Summary|Verdict|Score|Overview|Headline|TopBar)",
    re.IGNORECASE,
)
_ECHO_L2_CONTAINER_RE = re.compile(
    r"(?:\b(?:LazyVGrid|LazyHGrid|Grid|GridRow|Section|List|ForEach|DisclosureGroup)\b"
    r"|(?:Bento|Tile|Card|Cell|Metric|Detail|Row|Drawer|Panel|Module))",
    re.IGNORECASE,
)
_ECHO_L1_FONT_RE = re.compile(
    r"\.font\s*\(\s*(?:\.system\s*\(\s*size\s*:\s*(?:2[8-9]|[3-9]\d)\b|\.(?:largeTitle|title)\b)"
)


def _balanced_delim_end_local(text: str, open_index: int, open_ch: str, close_ch: str) -> int:
    depth = 1
    idx = open_index + 1
    while idx < len(text) and depth > 0:
        if text[idx] == open_ch:
            depth += 1
        elif text[idx] == close_ch:
            depth -= 1
        idx += 1
    return idx


def _balanced_brace_end_local(text: str, open_brace_index: int) -> int:
    return _balanced_delim_end_local(text, open_brace_index, "{", "}")


def _find_ternary_expr_end(masked_text: str, colon_pos: int) -> int:
    b = p = k = 0
    idx = colon_pos + 1
    n = len(masked_text)
    while idx < n:
        ch = masked_text[idx]
        if ch == "{":
            b += 1
        elif ch == "}":
            if b == 0:
                return idx
            b -= 1
        elif ch == "(":
            p += 1
        elif ch == ")":
            if p == 0:
                return idx
            p -= 1
        elif ch == "[":
            k += 1
        elif ch == "]":
            if k == 0:
                return idx
            k -= 1
        elif b == 0 and p == 0 and k == 0 and ch in ",;\n":
            return idx
        idx += 1
    return n


def _find_branch_arm_spans(masked_text: str) -> list[tuple[int, int, int]]:
    """Return (chain_id, arm_start, arm_end) for mutually exclusive if/else, switch, and ternary arms."""
    arms: list[tuple[int, int, int]] = []
    chain_id = 0
    cursor = 0
    if_re = re.compile(r"\bif\b")
    while True:
        match = if_re.search(masked_text, cursor)
        if match is None:
            break
        prefix = masked_text[max(0, match.start() - 12) : match.start()]
        if re.search(r"\belse\s+$", prefix):
            cursor = match.end()
            continue
        brace_open = masked_text.find("{", match.end())
        if brace_open < 0:
            break
        brace_close = _balanced_brace_end_local(masked_text, brace_open)
        chain_arms = [(brace_open, brace_close)]
        tail_pos = brace_close
        while True:
            else_m = re.match(r"\s*else\b", masked_text[tail_pos:])
            if not else_m:
                break
            next_search = tail_pos + else_m.end()
            next_brace = masked_text.find("{", next_search)
            if next_brace < 0:
                break
            next_close = _balanced_brace_end_local(masked_text, next_brace)
            chain_arms.append((next_brace, next_close))
            tail_pos = next_close
        if len(chain_arms) >= 2:
            chain_id += 1
            for a_start, a_end in chain_arms:
                arms.append((chain_id, a_start, a_end))
        cursor = match.end()

    case_re = re.compile(r"(?:case\b|default\s*:)")
    for sw_m in re.finditer(r"\bswitch\b[^{]*\{", masked_text):
        sw_open = masked_text.rfind("{", sw_m.start(), sw_m.end())
        if sw_open < 0:
            continue
        sw_close = _balanced_brace_end_local(masked_text, sw_open)
        case_positions: list[int] = []
        depth = 0
        idx = sw_open
        while idx < sw_close:
            ch = masked_text[idx]
            if ch == "{":
                depth += 1
                idx += 1
                continue
            if ch == "}":
                depth -= 1
                idx += 1
                continue
            if depth == 1 and (idx == 0 or not (masked_text[idx - 1].isalnum() or masked_text[idx - 1] == "_")):
                cm = case_re.match(masked_text, idx)
                if cm is not None:
                    case_positions.append(idx)
                    idx = cm.end()
                    continue
            idx += 1
        if len(case_positions) >= 2:
            chain_id += 1
            for idx_c, c_start in enumerate(case_positions):
                c_end = case_positions[idx_c + 1] if idx_c + 1 < len(case_positions) else max(c_start, sw_close - 1)
                arms.append((chain_id, c_start, c_end))

    q_stack: list[tuple[int, int, int, int]] = []
    b = p = k = 0
    idx = 0
    n = len(masked_text)
    while idx < n:
        ch = masked_text[idx]
        if ch == "{":
            b += 1
        elif ch == "}":
            q_stack = [item for item in q_stack if item[:3] != (b, p, k)]
            b = max(0, b - 1)
        elif ch == "(":
            p += 1
        elif ch == ")":
            q_stack = [item for item in q_stack if item[:3] != (b, p, k)]
            p = max(0, p - 1)
        elif ch == "[":
            k += 1
        elif ch == "]":
            q_stack = [item for item in q_stack if item[:3] != (b, p, k)]
            k = max(0, k - 1)
        elif ch == "?":
            prev_ch = masked_text[idx - 1] if idx > 0 else " "
            next_ch = masked_text[idx + 1] if idx + 1 < n else " "
            prev_ident = idx > 0 and (prev_ch.isalnum() or prev_ch in "_)]>")
            if next_ch not in {"?", "."} and prev_ch != "?" and not (prev_ident and not next_ch.isspace()):
                q_stack.append((b, p, k, idx))
        elif ch == ":" and q_stack and q_stack[-1][:3] == (b, p, k):
            _, _, _, q_pos = q_stack.pop()
            expr_end = _find_ternary_expr_end(masked_text, idx)
            chain_id += 1
            arms.append((chain_id, q_pos + 1, idx))
            arms.append((chain_id, idx + 1, expr_end))
        idx += 1
    return arms


def _are_mutually_exclusive_positions(
    pos_a: int,
    pos_b: int,
    arms: list[tuple[int, int, int]],
) -> bool:
    for cid, a_start, a_end in arms:
        if a_start <= pos_a < a_end and not (a_start <= pos_b < a_end):
            for other_cid, b_start, b_end in arms:
                if other_cid == cid and (b_start, b_end) != (a_start, a_end) and b_start <= pos_b < b_end:
                    return True
    return False


def _build_brace_blocks(masked_text: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    stack: list[tuple[int, str]] = []
    for idx, ch in enumerate(masked_text):
        if ch == "{":
            h_start = max(0, idx - 160)
            for back in range(idx - 1, h_start - 1, -1):
                if masked_text[back] in "{};":
                    h_start = back + 1
                    break
            stack.append((idx, masked_text[h_start:idx]))
        elif ch == "}" and stack:
            open_idx, head = stack.pop()
            blocks.append((open_idx, idx + 1, head))
    blocks.sort(key=lambda item: (item[0], -item[1]))
    return blocks


def _classify_echo_layer(
    pos: int,
    literal_val: str,
    masked_text: str,
    blocks: list[tuple[int, int, str]],
) -> tuple[str, int, int | None]:
    enclosing = [
        (o, c, h)
        for o, c, h in blocks
        if o <= pos < c and not re.search(r"\bstruct\s+\w+\s*:", h)
    ]
    leaf_open = enclosing[-1][0] if enclosing else 0
    local_mod = masked_text[pos : min(len(masked_text), pos + len(literal_val) + 120)]
    role: str | None = None
    role_block: int | None = None
    for o, _, h in reversed(enclosing):
        if _ECHO_L2_CONTAINER_RE.search(h):
            role, role_block = "L2", o
            break
        if _ECHO_L1_CONTAINER_RE.search(h):
            role, role_block = "L1", o
            break
    if role is None:
        if _ECHO_L1_FONT_RE.search(local_mod) or re.match(r"^\s*\d{2,3}\s*[·•|]", literal_val):
            role = "L1"
        elif re.search(r"^(?:环境|身体|负荷|睡眠|风速|气温|心率|功率|恢复|详情|分项)\s*[:：·•]", literal_val):
            role = "L2"
        else:
            role = "NEUTRAL"
    return role, leaf_open, role_block


def _are_distinct_structural_layers(
    layer_a: tuple[str, int, int | None],
    layer_b: tuple[str, int, int | None],
) -> bool:
    role_a, leaf_a, rblk_a = layer_a
    role_b, leaf_b, rblk_b = layer_b
    if {role_a, role_b} == {"L1", "L2"}:
        return True
    if leaf_a != leaf_b and ("L1" in {role_a, role_b} or "L2" in {role_a, role_b}):
        if role_a == role_b and rblk_a is not None and rblk_a == rblk_b:
            return False
        return True
    return False


def _max_coexisting_indices(
    positions: list[int],
    arms: list[tuple[int, int, int]],
) -> list[int]:
    n = len(positions)
    best: list[int] = []
    for seed in range(n):
        clique = [seed]
        for cand in range(seed + 1, n):
            if all(not _are_mutually_exclusive_positions(positions[m], positions[cand], arms) for m in clique):
                clique.append(cand)
        if len(clique) > len(best):
            best = clique
    return best


def _extract_swift_button_calls(source_text: str, masked_text: str) -> list[dict[str, Any]]:
    """Extract SwiftUI Button calls with balanced arguments/closures and strictly attached postfix modifiers."""
    calls: list[dict[str, Any]] = []
    n = len(masked_text)
    for bm in re.finditer(r"(?<!\.)\bButton\b", masked_text):
        start = bm.start()
        cursor = bm.end()
        while cursor < n and masked_text[cursor].isspace():
            cursor += 1
        arg_slice = ""
        if cursor < n and masked_text[cursor] == "(":
            arg_end = _balanced_delim_end_local(masked_text, cursor, "(", ")")
            arg_slice = source_text[cursor + 1 : max(cursor + 1, arg_end - 1)]
            cursor = arg_end
        while cursor < n and masked_text[cursor].isspace():
            cursor += 1
        first_closure_slice = ""
        if cursor < n and masked_text[cursor] == "{":
            first_end = _balanced_delim_end_local(masked_text, cursor, "{", "}")
            first_closure_slice = source_text[cursor + 1 : max(cursor + 1, first_end - 1)]
            cursor = first_end
        elif not arg_slice:
            continue
        label_closure_slice = ""
        lm = re.match(r"\s*label\s*:\s*\{", masked_text[cursor:])
        if lm:
            l_open = cursor + lm.end() - 1
            l_end = _balanced_delim_end_local(masked_text, l_open, "{", "}")
            label_closure_slice = source_text[l_open + 1 : max(l_open + 1, l_end - 1)]
            cursor = l_end
        body_end = cursor
        while cursor < n:
            mm = re.match(r"\s*\.\s*([A-Za-z_]\w*)", masked_text[cursor:])
            if not mm:
                break
            cursor += mm.end()
            ws = cursor
            while ws < n and masked_text[ws] in " \t":
                ws += 1
            if ws < n and masked_text[ws] == "(":
                cursor = _balanced_delim_end_local(masked_text, ws, "(", ")")
                ws2 = cursor
                while ws2 < n and masked_text[ws2] in " \t":
                    ws2 += 1
                if ws2 < n and masked_text[ws2] == "{":
                    cursor = _balanced_delim_end_local(masked_text, ws2, "{", "}")
            elif ws < n and masked_text[ws] == "{":
                cursor = _balanced_delim_end_local(masked_text, ws, "{", "}")
        label_literal = ""
        m_arg_str = re.match(
            r"\s*(?:LocalizedStringKey\s*\(\s*)?\"([^\n\"]{1,60})\"",
            arg_slice,
        )
        if m_arg_str:
            label_literal = m_arg_str.group(1).strip()
        else:
            target_label_body = (
                label_closure_slice
                if label_closure_slice
                else (first_closure_slice if re.search(r"\baction\s*:", arg_slice) else "")
            )
            if target_label_body:
                m_lbl = re.search(r"\b(?:Text|Label)\s*\(\s*\"([^\n\"]{1,60})\"", target_label_body)
                if m_lbl:
                    label_literal = m_lbl.group(1).strip()
        modifiers_source = source_text[body_end:cursor]
        is_prominent = bool(
            UI_PROMINENT_CTA_STYLE_RE.search(modifiers_source)
            or (label_closure_slice and UI_PROMINENT_CTA_STYLE_RE.search(label_closure_slice))
            or (first_closure_slice and re.search(r"\baction\s*:", arg_slice) and UI_PROMINENT_CTA_STYLE_RE.search(first_closure_slice))
            or (label_literal and UI_PRIMARY_NAV_CTA_VERB_RE.search(label_literal))
        )
        calls.append(
            {
                "start": start,
                "end": cursor,
                "label": label_literal,
                "is_prominent": is_prominent,
                "match_preview": source_text[start:cursor].replace("\n", " ")[:240],
            }
        )
    return calls


def _audit_cross_layer_metric_echo(
    no_prev_text: str,
    full_text: str,
    is_waived,
    path: str,
    ref_interaction: str,
    view_scopes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    comment_free_full = _strip_swift_comments_preserve_strings(no_prev_text)
    string_re = re.compile(r'"([^"\n\\]*(?:\\.[^"\n\\]*)*)"')
    non_ui_prefix_re = re.compile(
        r"(?:\b(?:accessibilityIdentifier|print|debugPrint|NSLog|Logger|fatalError|precondition|assert)\s*\(\s*"
        r"|\b(?:systemName|named|font|identifier|id)\s*:\s*)$"
    )
    scopes = view_scopes or [{"name": "<screen>", "start": 0, "end": len(no_prev_text), "masked": comment_free_full}]

    for scope in scopes:
        s_start = int(scope.get("start", 0))
        s_end = int(scope.get("end", len(no_prev_text)))
        s_comment_free = comment_free_full[s_start:s_end]
        s_masked = str(scope.get("masked", s_comment_free))
        branch_arms = _find_branch_arm_spans(s_masked)
        brace_blocks = _build_brace_blocks(s_masked)
        token_occurrences: dict[str, list[tuple[int, str]]] = {}

        for sm in string_re.finditer(s_comment_free):
            abs_start = s_start + sm.start()
            abs_end = s_start + sm.end()
            if is_waived(abs_start, abs_end):
                continue
            prefix = s_comment_free[max(0, sm.start() - 48) : sm.start()]
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
            for pm in SPLIT_METRIC_PAIR_RE.finditer(s_comment_free):
                abs_start = s_start + pm.start()
                abs_end = s_start + pm.end()
                if is_waived(abs_start, abs_end):
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

        echoed_tokens: list[tuple[str, list[tuple[int, str]]]] = []
        for token, occs in token_occurrences.items():
            if len(occs) < 2:
                continue
            positions = [pos for pos, _ in occs]
            coexisting_idxs = _max_coexisting_indices(positions, branch_arms)
            if len(coexisting_idxs) >= 3:
                echoed_tokens.append((token, [occs[i] for i in coexisting_idxs]))
                continue
            layers = [_classify_echo_layer(pos, lit, s_masked, brace_blocks) for pos, lit in occs]
            cross_layer_pair: list[tuple[int, str]] | None = None
            for idx_a in range(len(occs)):
                for idx_b in range(idx_a + 1, len(occs)):
                    if _are_mutually_exclusive_positions(positions[idx_a], positions[idx_b], branch_arms):
                        continue
                    if _are_distinct_structural_layers(layers[idx_a], layers[idx_b]):
                        cross_layer_pair = [occs[idx_a], occs[idx_b]]
                        break
                if cross_layer_pair is not None:
                    break
            if cross_layer_pair is not None:
                echoed_tokens.append((token, cross_layer_pair))

        if echoed_tokens:
            first_token, first_occs = echoed_tokens[0]
            second_pos, second_lit = first_occs[1]
            line = full_text.count("\n", 0, s_start + second_pos) + 1
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

    return []


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
