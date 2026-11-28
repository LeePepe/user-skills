# Interaction Layering

Keep three layers explicit: Content (solid task material), Functional (bars and controls), and Feedback (state, motion, haptics). Liquid Glass belongs only to functional chrome (`DC-011`); content remains readable and state remains recoverable.

| Rule | Evidence |
|---|---|
| ownership | one control owns one transition; never nest `Button` inside a `Button` label or lock expandable rows to collapsed `minHeight` (`DC-012`) |
| expand stability | keep persistent status/priority pills identical across collapsed/expanded states, place expand-only rows below persistent headers, and wrap reveals in `.clipped()` (`DC-016`) |
| orthogonal layers | enforce `L1 Hero Verdict ➔ L2 Metric Tiles ➔ L3 Expanded Detail` with zero cross-layer metric echoing (`UI_CROSS_LAYER_METRIC_ECHO`) and `55%–60%` L1 Hero + L2 Card Peek (`UI_SCROLL_PROMPT_CLUTTER`) |
| section gate | PreviewHost/Section views require real `@State`/`@Binding`/`@Bindable`, `DisclosureGroup`, `ClippedRevealLayout`, or `SW*` (`check-section-interactivity`) |
| state & motion | idle → intent → commit/cancel → settle; bounded transforms and reduced-motion fallback |
| touch & preview | >=44pt mobile, >=28pt + keyboard parity on desktop; `PreviewHostKit`, `#Preview`, 120Hz for native Apple |

## Three-Layer Orthogonal Information Allocation Case (L1 Hero Verdict ➔ L2 Metric Tiles ➔ L3 Expanded Detail)

| Layer | Orthogonal Information Scope (Positive Contract) | Anti-Pattern (`UI_CROSS_LAYER_METRIC_ECHO` / `UI_SCROLL_PROMPT_CLUTTER`) |
|---|---|---|
| **Layer 1 · Top Hero Stage (`55%–60%` viewport height)** | **Qualitative Verdict & Composite Score Only**: single composite score (`86`), core qualitative conclusion (`极佳适宜 · 适合耐力拉练`), and top-level dimension score weights (`环境 88 · 身体 84`). | Echoing concrete telemetry readings (`19°C`, `东北风 14km/h`, `昨骑 35.2km`, `恢复 88%`) across top status pills, summary prose, and 3-up sub-strips before repeating them in L2 tiles. |
| **Inter-Layer Spatial Boundary (`Card Peek`)** | **Norman Spatial Signifier / Card Peek (Above-the-Fold Bleed)**: size L1 Hero to ~`55%–60%` of the viewport height so the first row of L2 Bento tiles naturally bleeds/peeks into the bottom of the initial viewport with **zero instructional copy**. | Printing instructional scroll/expand banners (`"⌄ 下拉查看分项指标与详情"`, `"向上滑动查看更多"`, `"点击卡片展开详情"`) between Layer 1 and Layer 2 (`SPEC_GAP_INSTRUCTIONAL_SCROLL_PROMPT`). |
| **Layer 2 · Below-the-Fold Bento Tiles** | **Domain Key Metrics**: each compact tile claims 1 orthogonal domain's primary measured values (e.g. Environment tile owns `19°C · 14km/h`; Body tile owns `7.6h · 恢复 88%`), with zero duplication against Layer 1. | Repeating L1 summary text or duplicating the same physical reading (`19°C`, `14km/h`, `35.2km`, `7.6h`) across multiple sibling cards. |
| **Layer 3 · Morph Expanded Detail Card** | **Time-Series & Thresholds**: tapping an L2 tile expands in-place or full-screen to reveal 24h hourly curves, Bullet Graph threshold bands, and historical comparisons. | Stuffing 24h multi-axis curves or prose threshold explanations directly into L1 Hero or collapsed L2 summary tiles. |

1. **Anti-Pattern A · Cross-Layer Redundancy / Echoing (`UI_CROSS_LAYER_METRIC_ECHO`)**:
   - Top Hero stage repeats the exact physical telemetry readings (`19°C`, `东北风 14km/h`, `昨骑 35.2km`, `恢复 88%`) 2–3 times (in a top status pill, a summary sentence, and a 3-up sub-strip) and then renders the same metrics again in the L2 Bento tiles right below.
2. **Orthogonal Layer Allocation Contract**:
   - **Layer 1 · Top Hero Stage (Qualitative Verdict & Composite Score Only)**: carries ONLY the single composite score (`86`), core qualitative conclusion (`极佳适宜 · 适合耐力拉练`), and top-level dimension score weights (`环境 88 · 身体 84`). Never spoil or echo concrete physical readings (temperature, wind speed, ride distance, recovery percentage, sleep hours) that belong to L2 tiles.
   - **Layer 2 · Below-the-Fold Bento Tiles (Domain Key Metrics)**: each compact tile claims 1 orthogonal domain's primary measured values (e.g. Environment tile owns `19°C · 14km/h`; Body tile owns `7.6h · 恢复 88%`), with zero duplication against Layer 1.
   - **Layer 3 · Morph Expanded Detail Card (Time-Series & Thresholds)**: tapping an L2 tile expands in-place or full-screen to reveal 24h hourly curves, Bullet Graph threshold bands, and historical comparisons.
3. **Anti-Pattern B · Instructional Scroll / Expand Prompt Clutter (`UI_INSTRUCTIONAL_CLUTTER` / `UI_SCROLL_PROMPT_CLUTTER`)**:
   - Never print `"⌄ 下拉查看分项指标与详情"`, `"向上滑动查看更多"`, or `"点击卡片展开详情"` between Layer 1 and Layer 2.
   - **Norman Spatial Signifier / Card Peek (Above-the-Fold Bleed)**: size Layer 1 to ~`55%–60%` of the viewport height so the first row of Layer 2 Bento tiles naturally bleeds/peeks into the bottom of the initial viewport (`Card Peek`), signaling vertical scrollability through spatial geometry with **zero instructional copy**.

The `magpie/group/designer` proposal is frozen before `magpie/group/coding` implementation.
