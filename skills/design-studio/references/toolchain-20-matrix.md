# Design Studio Reference Catalog & Toolchain Matrix

This matrix is generated from `catalog.json`; use `design_catalog.py` to search,
extend, rate, and re-render the catalog.

## M1–M4 Expressiveness Decision Table

| Tier | Intent | Typical surfaces | Guardrail |
|---|---|---|---|
| M1 | Micro-feedback | Operate, Read | 100–150ms press, numeric transition, reduced-motion fallback |
| M2 | Spatial & gesture choreography | Operate, Read, Experience | Deterministic state machine, spring settle, 44pt hit targets |
| M3 | Narrative / 3D hero | Persuade, Experience | Tokenized timeline, progressive fallback, no dense-form usage |
| M4 | Code-to-video | Video/Offline, Persuade | Deterministic frame render and reproducible export |

## Web vs. Mobile-Native vs. Shared

| Boundary | Contract |
|---|---|
| Shared | Physical state machines, DesignTokens/StrataMotion timing, Lottie, Blender GLTF/USDZ, Remotion/FFmpeg promo outputs |
| Web | Motion, Anime.js, Three.js/Postprocessing/Three.quarks/Jolt/GPU Pathtracer, DEV-only Tweakpane |
| Mobile-Native | StrataMotion, matchedGeometryEffect, KeyframeAnimator, PhaseAnimator, Canvas, ShaderLibrary, SensoryFeedback, numericText |

## Extensible Toolchain

| ID | Tool | Category | Platforms | Surface modes | Tier | Automation |
|---|---|---|---|---|---|---|
| T-001 | Remotion | code-to-video | web, mobile, shared | Persuade, Video/Offline | M4 | direct-code |
| T-002 | @remotion/transitions | code-to-video | web, mobile, shared | Persuade, Video/Offline | M4 | direct-code |
| T-003 | Motion | core-ui-motion | web | Operate, Read, Persuade, Experience | M1 | direct-code |
| T-004 | Anime.js | core-ui-motion | web | Persuade, Experience | M2 | direct-code |
| T-005 | Lottie | core-ui-motion | web, mobile, shared | Read, Experience | M1 | direct-code |
| T-006 | Three.js | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | direct-code |
| T-007 | Postprocessing | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | direct-code |
| T-008 | Three.quarks | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | direct-code |
| T-009 | Jolt Physics | 3d-physics | web | Experience, Video/Offline | M3 | direct-code |
| T-010 | GPU Pathtracer | 3d-physics | web | Experience, Video/Offline | M3 | mcp-cli |
| T-011 | MCP for Blender | 3d-physics | web, mobile, shared | Experience, Video/Offline | M3 | mcp-cli |
| T-012 | Tweakpane | core-ui-motion | web | Persuade, Experience | M2 | dev-only |
| T-013 | Manim | code-to-video | web, mobile, shared | Video/Offline | M4 | direct-code |
| T-014 | CosyVoice | audio-beat | web, mobile, shared | Video/Offline | M4 | mcp-cli |
| T-015 | WhisperX | audio-beat | web, mobile, shared | Video/Offline | M4 | mcp-cli |
| T-016 | Librosa | audio-beat | web, mobile, shared | Video/Offline | M4 | direct-code |
| T-017 | @ffmpeg/ffmpeg | asset-export | web | Video/Offline | M4 | mcp-cli |
| T-018 | FFmpeg CLI | asset-export | web, mobile, shared | Video/Offline | M4 | mcp-cli |
| T-019 | OBS Studio | asset-export | web, mobile, shared | Video/Offline | M4 | human-gui-only |
| T-020 | LosslessCut | asset-export | web, mobile, shared | Video/Offline | M4 | human-gui-only |

## Interactive Pattern Catalog

| ID | Pattern | Category | Platforms | Tier | State machine |
|---|---|---|---|---|---|
| MP-001 | 卡片揭角 (Card Corner Peel) | gesture-physical | web, mobile, shared | M2 | `idle -> press -> drag -> partial_peel -> release -> spring_settle` |
| MP-002 | 滑动确认 (Slide-to-Confirm) | confirmation-safety | web, mobile, shared | M2 | `idle -> drag -> threshold -> commit/cancel -> spring_settle` |
| MP-003 | 拉绳开关 (Pull-Cord Light Switch) | gesture-physical | web, mobile, shared | M2 | `off_idle -> pull -> threshold -> on_commit -> return_swing -> on_idle -> pull -> off_commit -> settle` |
| MP-004 | 滑动工具栏 (Magnification Scrubbing Toolbar) | spatial-navigation | web, mobile, shared | M2 | `idle -> hover/drag -> focus_magnify -> neighbor_reflow -> release -> commit -> settle` |
| MP-005 | 消息气泡反馈 (Long-Press Bubble Reaction) | micro-feedback-social | web, mobile, shared | M2 | `idle -> long_press -> lifted_dimmed -> reaction_menu -> select -> arc_commit/cancel -> settle` |
| MP-006 | 文件拖入文件夹 (Spring-Loaded Folder Drop) | drag-drop-choreography | web, mobile, shared | M2 | `idle -> drag -> near_target -> folder_open -> hover -> commit/cancel -> settle` |
| IP-001 | 图表联动筛选 (Chart Cross-Filtering) | data-linkage | web, mobile, shared | M1 | `all_items -> select_category(X) -> filtered_list_and_kpi(X) -> tap_same_category(X) -> all_items` |
| IP-002 | 组合筛选与排序 (Combined Filter & Orthogonal Sort) | filter-sort-recovery | web, mobile, shared | M1 | `idle -> apply_filter(category, keyword) -> toggle_sort(asc|desc) -> reorder_subset_preserving_filters` |
| IP-003 | 列表联动详情 (In-Place Master-Detail Linkage) | master-detail-flow | web, mobile, shared | M1 | `list_ready -> select_row(id) -> detail_updated_in_place(id) [master_order_and_scroll_preserved]` |
| IP-004 | 无结果与局部恢复 (Scoped Empty State & Context-Preserving Recovery) | filter-sort-recovery | web, mobile, shared | M1 | `filtered(category) -> keyword_no_match -> contextual_empty_state -> clear_keyword_only -> restored(category)` |
| IP-005 | 状态联动汇总 (Live Status-to-Summary Aggregation) | data-linkage | web, mobile, shared | M1 | `item_status_change(old -> new) -> recompute_summary_counts_and_semantic_tokens -> UI_sync` |

### Pattern details

#### MP-001 · 卡片揭角 (Card Corner Peel)
- Prompt: 做一张可揭角的卡片，右下角按住后随拖动卷起，用折线裁切正面并露出背面，折片带柔和明暗。松手沿原路回落，短距离拖动只露一角，整张卡片保持稳定。
- Canonical: Drag the card's lower-right corner to peel a clipped front layer, reveal the back, then spring back along the inverse path unless committed.
- Web: Motion drag progress with CSS clip-path polygon and @nocoo/basalt tokens.
- Mobile: SwiftUI DragGesture with Canvas/path clipping, StrataMotion spring, and Reduce Motion crossfade.
- Guardrails: CLS = 0; tabular-nums where values appear; >=4.5:1 contrast; 44x44pt hit target; Reduce Motion fallback.

#### MP-002 · 滑动确认 (Slide-to-Confirm)
- Prompt: 做一个滑动确认按钮，圆钮与轨道填充跟随同一拖动进度。未到终点松手，阻尼回到起点且不执行；到终点松手才确认，箭头变成对勾，文字变为已完成。
- Canonical: Keep thumb and track fill on one normalized drag progress; cancel below the endpoint with damped return, commit only at endpoint, then swap arrow to checkmark and copy.
- Web: Motion drag controls thumb and track; CSS/SVG icon morph; Basalt semantic success tokens.
- Mobile: SwiftUI DragGesture with StrataMotion spring, contentTransition(.symbolEffect), SensoryFeedback, and numericText.
- Guardrails: No accidental commit; 44x44pt target; explicit threshold; >=4.5:1 contrast; Reduce Motion opacity/state fallback.

#### MP-003 · 拉绳开关 (Pull-Cord Light Switch)
- Prompt: 做一个拉绳灯光开关，绳头跟随竖向拖动，超过触发距离时切换灯光与玻璃暖光，每次拉动切换一次。松手收回绳子，摆幅逐渐衰减，再拉一次可以关闭。
- Canonical: Track vertical cord drag; cross the trigger distance once to toggle light and warm glass glow, then return with damped swing and allow the next pull to turn it off.
- Web: Motion drag transform with Anime.js damped return and tokenized warm-glow CSS.
- Mobile: SwiftUI DragGesture plus KeyframeAnimator/StrataMotion, ShaderLibrary glow, and SensoryFeedback.
- Guardrails: One toggle per pull; bounded drag; no glare-only status; 44x44pt target; Reduce Motion removes swing.

#### MP-004 · 滑动工具栏 (Magnification Scrubbing Toolbar)
- Prompt: 做一条可滑选的工具栏，当前图标随手指靠近连续放大，相邻图标平滑让位，位置由实时宽度累加计算。显示当前名称，松手提交选择，画布同步切换工具效果。
- Canonical: Scrub across a toolbar whose focused icon magnifies continuously and neighbors make room from live accumulated widths; show the name and commit the selected tool on release.
- Web: Motion layout animation with measured widths, CSS transform scale, and tokenized focus ring.
- Mobile: SwiftUI DragGesture with matchedGeometryEffect, measured layout, and StrataMotion spring.
- Guardrails: Live width accumulation, no overlap, single-line label, 44x44pt targets, Reduce Motion uses discrete focus.

#### MP-005 · 消息气泡反馈 (Long-Press Bubble Reaction)
- Prompt: 长按消息后拾起当前气泡，周围消息降低亮度，三枚表情错峰展开。选中并松手后，同一表情沿弧线缩到气泡角标，菜单收回。徽标随气泡归位，再点可取消。
- Canonical: Long-press and lift the message bubble, dim neighbors, stagger three reactions, then arc the selected reaction into the bubble badge on release; tap again cancels.
- Web: Motion shared layout with Anime.js stagger and SVG arc path; Basalt reaction tokens.
- Mobile: SwiftUI long press, matchedGeometryEffect, KeyframeAnimator arc, and SensoryFeedback.
- Guardrails: Preserve message context; 44x44pt reactions; tabular-nums for counts; Reduce Motion uses fade/scale.

#### MP-006 · 文件拖入文件夹 (Spring-Loaded Folder Drop)
- Prompt: 拖动文件靠近文件夹时，夹口张开并显示落点；悬停阶段保持原数量；松手命中后文件缩进前后盖之间，数量加一，再合上夹口；未命中则回到原位置。
- Canonical: When a dragged file nears a folder, open the folder and show the drop target without changing count; on hit, tuck the file between covers, increment count, and close, otherwise return.
- Web: Motion pointer drag with Anime.js folder lid choreography, hit testing, and numeric transition.
- Mobile: SwiftUI DragGesture/drop delegate, matchedGeometryEffect tuck, numericText count, and SensoryFeedback.
- Guardrails: Visible target and hit parity; count changes only on commit; 44x44pt targets; Reduce Motion crossfade.

#### IP-001 · 图表联动筛选 (Chart Cross-Filtering)
- Prompt: 点图表里的分类，下方列表和统计数字跟着变，再点一下全部恢复。
- Canonical: Click a chart category/segment to highlight it and cross-filter the list and summary metrics below; click the same category again to reset to all items.
- Web: Derive list and KPIs from a shared activeCategory state; highlight active Recharts/Basalt segment, dim unselected segments, and toggle off on repeat click.
- Mobile: SwiftUI Charts selection binding filtering the shared query state with .contentTransition(.numericText()) on summary readouts and tap-to-deselect.
- Guardrails: tabular-nums / monospacedDigit for changing metrics; preserve active search keyword and sort order; >=4.5:1 active/inactive contrast.

#### IP-002 · 组合筛选与排序 (Combined Filter & Orthogonal Sort)
- Prompt: 选好类别搜关键词，还能按数量升降序排，而且排序不会清空之前的筛选条件。
- Canonical: Compose category filter, keyword search, and ascending/descending sort orthogonally; toggling sort order reorders the filtered subset without clearing active filters.
- Web: Pure derived selector: filter(items, category, keyword) then sort(field, dir); keep filter and sort controls in independent state slices.
- Mobile: SwiftUIsearchable + category filter tokens + Menu/Picker sort descriptor operating on the filtered collection without resetting filter state.
- Guardrails: Sorting never clears filter/search state; provide clear active indicators and reset options.

#### IP-003 · 列表联动详情 (In-Place Master-Detail Linkage)
- Prompt: 点左侧记录，右侧详情直接更新，不用跳页面，列表顺序也不变。
- Canonical: Select a record in the master list to update the detail pane in-place without page navigation, preserving the master list's sort order, filters, and scroll position.
- Web: Two-column master-detail layout with selectedId state; render detail panel in-place and keep master list key/scroll stable.
- Mobile: NavigationSplitView on iPad/macOS or in-place detail card/sheet on compact screens, preserving list scroll and ordering.
- Guardrails: CLS = 0 on selection; master list order and scroll offset remain unchanged; clear selected row state.

#### IP-004 · 无结果与局部恢复 (Scoped Empty State & Context-Preserving Recovery)
- Prompt: 搜不到时显示空状态，清空关键词后数据恢复，其他筛选条件还得保留。
- Canonical: Show a contextual empty state when keyword search yields zero matches; clearing the keyword restores matching data while keeping other active filters intact.
- Web: Empty state names the active query/filter and offers a 'Clear keyword' action that resets only searchQuery while preserving category and sort state.
- Mobile: ContentUnavailableView.search(text:) or custom Strata empty state whose reset action clears search text without wiping selected category pills.
- Guardrails: Empty state explains why and provides a 1-click partial recovery CTA; non-keyword filters remain preserved.

#### IP-005 · 状态联动汇总 (Live Status-to-Summary Aggregation)
- Prompt: 卡片状态一变，下方的分类数量和颜色也得跟着同步更新。
- Canonical: When an item's status changes, synchronously recompute summary category counts, ratios, and semantic status colors from the same single source of truth.
- Web: Derive summary badges, counts, and semantic color tokens directly from the items array rather than separate static counters.
- Mobile: Computed properties on @Observable / SwiftData model collections driving summary badges and semantic tokens with .numericText() transition.
- Guardrails: Single source of truth for item status and summary counters; semantic tokens calibrated for both Light and Dark mode.

## Usage & Preference Ledger

| ID | Usage count | Rating | Last used |
|---|---:|---|---|
| T-001 | 0 | unrated | — |
| T-002 | 0 | unrated | — |
| T-003 | 0 | unrated | — |
| T-004 | 0 | unrated | — |
| T-005 | 0 | unrated | — |
| T-006 | 0 | unrated | — |
| T-007 | 0 | unrated | — |
| T-008 | 0 | unrated | — |
| T-009 | 0 | unrated | — |
| T-010 | 0 | unrated | — |
| T-011 | 0 | unrated | — |
| T-012 | 0 | unrated | — |
| T-013 | 0 | unrated | — |
| T-014 | 0 | unrated | — |
| T-015 | 0 | unrated | — |
| T-016 | 0 | unrated | — |
| T-017 | 0 | unrated | — |
| T-018 | 0 | unrated | — |
| T-019 | 0 | unrated | — |
| T-020 | 0 | unrated | — |
| MP-001 | 0 | unrated | — |
| MP-002 | 0 | unrated | — |
| MP-003 | 0 | unrated | — |
| MP-004 | 0 | unrated | — |
| MP-005 | 0 | unrated | — |
| MP-006 | 0 | unrated | — |
| IP-001 | 1 | liked | 2026-11-07 |
| IP-002 | 2 | liked | 2026-11-07 |
| IP-003 | 2 | liked | 2026-11-07 |
| IP-004 | 2 | liked | 2026-11-07 |
| IP-005 | 2 | liked | 2026-11-07 |

## Code / CLI Recipes

```bash
python3 skills/design-studio/scripts/design_catalog.py search --query "滑动确认"
python3 skills/design-studio/scripts/design_catalog.py recommend --platform web --surface-mode Operate
python3 skills/design-studio/scripts/design_catalog.py record-use --id MP-002 --context "checkout" --rating liked
```
