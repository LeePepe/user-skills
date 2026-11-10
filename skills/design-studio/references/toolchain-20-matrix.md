# Design Studio Reference Catalog & Toolchain Matrix

Generated from `catalog.json`; full per-pattern prompts, Web/Mobile implementations, and guardrails live in `catalog.json` (query on demand via `design_catalog.py search|recommend`).

## M1–M4 Expressiveness Decision Table

| Tier | Intent | Typical surfaces | Guardrail |
|---|---|---|---|
| M1 | Micro-feedback | Operate, Read | 100–150ms press, numeric transition, reduced-motion fallback |
| M2 | Spatial & gesture choreography | Operate, Read, Experience | Deterministic state machine, spring settle, 44pt hit targets |
| M3 | Narrative / 3D hero | Persuade, Experience | Tokenized timeline, progressive fallback, no dense-form usage |
| M4 | Code-to-video | Video/Offline, Persuade | Deterministic frame render and reproducible export |

## Extensible Toolchain (`T-*`)

| ID | Tool | Category | Platforms | Surface modes | Tier | Rating (Uses) |
|---|---|---|---|---|---|---|
| T-001 | Remotion | code-to-video | web, mobile, shared | Persuade, Video/Offline | M4 | unrated (0) |
| T-002 | @remotion/transitions | code-to-video | web, mobile, shared | Persuade, Video/Offline | M4 | unrated (0) |
| T-003 | Motion | core-ui-motion | web | Operate, Read, Persuade, Experience | M1 | unrated (0) |
| T-004 | Anime.js | core-ui-motion | web | Persuade, Experience | M2 | unrated (0) |
| T-005 | Lottie | core-ui-motion | web, mobile, shared | Read, Experience | M1 | unrated (0) |
| T-006 | Three.js | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | unrated (0) |
| T-007 | Postprocessing | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | unrated (0) |
| T-008 | Three.quarks | 3d-physics | web | Persuade, Experience, Video/Offline | M3 | unrated (0) |
| T-009 | Jolt Physics | 3d-physics | web | Experience, Video/Offline | M3 | unrated (0) |
| T-010 | GPU Pathtracer | 3d-physics | web | Experience, Video/Offline | M3 | unrated (0) |
| T-011 | MCP for Blender | 3d-physics | web, mobile, shared | Experience, Video/Offline | M3 | unrated (0) |
| T-012 | Tweakpane | core-ui-motion | web | Persuade, Experience | M2 | unrated (0) |
| T-013 | Manim | code-to-video | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-014 | CosyVoice | audio-beat | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-015 | WhisperX | audio-beat | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-016 | Librosa | audio-beat | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-017 | @ffmpeg/ffmpeg | asset-export | web | Video/Offline | M4 | unrated (0) |
| T-018 | FFmpeg CLI | asset-export | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-019 | OBS Studio | asset-export | web, mobile, shared | Video/Offline | M4 | unrated (0) |
| T-020 | LosslessCut | asset-export | web, mobile, shared | Video/Offline | M4 | unrated (0) |

## Interactive Pattern Catalog (`IP-*` / `MP-*`)

| ID | Pattern | Category | Platforms | Tier | State machine | Rating (Uses) |
|---|---|---|---|---|---|---|
| MP-001 | 卡片揭角 (Card Corner Peel) | gesture-physical | web, mobile, shared | M2 | `idle -> press -> drag -> partial_peel -> release -> spring_settle` | unrated (0) |
| MP-002 | 滑动确认 (Slide-to-Confirm) | confirmation-safety | web, mobile, shared | M2 | `idle -> drag -> threshold -> commit/cancel -> spring_settle` | unrated (0) |
| MP-003 | 拉绳开关 (Pull-Cord Light Switch) | gesture-physical | web, mobile, shared | M2 | `off_idle -> pull -> threshold -> on_commit -> return_swing -> on_idle -> pull -> off_commit -> settle` | unrated (0) |
| MP-004 | 滑动工具栏 (Magnification Scrubbing Toolbar) | spatial-navigation | web, mobile, shared | M2 | `idle -> hover/drag -> focus_magnify -> neighbor_reflow -> release -> commit -> settle` | unrated (0) |
| MP-005 | 消息气泡反馈 (Long-Press Bubble Reaction) | micro-feedback-social | web, mobile, shared | M2 | `idle -> long_press -> lifted_dimmed -> reaction_menu -> select -> arc_commit/cancel -> settle` | unrated (0) |
| MP-006 | 文件拖入文件夹 (Spring-Loaded Folder Drop) | drag-drop-choreography | web, mobile, shared | M2 | `idle -> drag -> near_target -> folder_open -> hover -> commit/cancel -> settle` | unrated (0) |
| IP-001 | 图表联动筛选 (Chart Cross-Filtering) | data-linkage | web, mobile, shared | M1 | `all_items -> select_category(X) -> filtered_list_and_kpi(X) -> tap_same_category(X) -> all_items` | liked (1) |
| IP-002 | 组合筛选与排序 (Combined Filter & Orthogonal Sort) | filter-sort-recovery | web, mobile, shared | M1 | `idle -> apply_filter(category, keyword) -> toggle_sort(asc|desc) -> reorder_subset_preserving_filters` | liked (2) |
| IP-003 | 列表联动详情 (In-Place Master-Detail Linkage) | master-detail-flow | web, mobile, shared | M1 | `list_ready -> select_row(id) -> detail_updated_in_place(id) [master_order_and_scroll_preserved]` | liked (2) |
| IP-004 | 无结果与局部恢复 (Scoped Empty State & Context-Preserving Recovery) | filter-sort-recovery | web, mobile, shared | M1 | `filtered(category) -> keyword_no_match -> contextual_empty_state -> clear_keyword_only -> restored(category)` | liked (2) |
| IP-005 | 状态联动汇总 (Live Status-to-Summary Aggregation) | data-linkage | web, mobile, shared | M1 | `item_status_change(old -> new) -> recompute_summary_counts_and_semantic_tokens -> UI_sync` | liked (2) |
| IP-006 | **说法**：别用系统术语。比如写“循环规则”，用户根本看不懂；换成大白话“每周一提醒我”，一下就明白了。 | layout-craft | web, mobile, shared | M1 | `ready -> container_or_content_change -> measure -> reflow -> stable` | unrated (0) |
| IP-007 | **入口**：别让新用户满屏找。把选项直接放在任务发生的地方，高级设置先收起来。 | layout-craft | web, mobile, shared | M1 | `ready -> container_or_content_change -> measure -> reflow -> stable` | unrated (0) |
| MP-007 | **反馈**：操作完得给个准信。点保存后页面啥变化没有，新手根本不知道成没成功；直接告诉他“下次提醒周一上午9点”才行。 | micro-feedback-social | web, mobile, shared | M2 | `idle -> press -> feedback_preview -> commit/cancel -> acknowledged -> idle` | unrated (0) |
| MP-008 | Domain Warping 流体背景与时间水管 (Domain Warping Fluid Shader & Time Pipe — WWDC26-322) | 3d-physics | web, mobile, shared | M3 | `idle_or_paused -> timeline_tick(dt) -> accumulate_elapsed -> metal_two_pass_domain_warp(uv, elapsed) -> render_frame` | unrated (0) |
| IP-008 | 图钉孔位反转零测高外挂角标 (AlignmentGuide Pin-Flip Edge Attachment — WWDC26-322) | layout-craft | web, mobile, shared | M1 | `ready -> dynamic_type_or_content_resize -> alignment_guide_pin_flip -> zero_cls_edge_anchor` | unrated (0) |
| MP-009 | 任意容器成摞拖拽与规则验牌回弹 (Multi-Item Card Stack Drag & Drop Validation — WWDC26-269/271) | drag-drop-choreography | web, mobile, shared | M2 | `idle -> grab_sub_pile(.stack_formation) -> hover_target -> drop_configuration_validate(.move | .forbidden) -> commit_insert_or_spring_snap_back` | unrated (0) |

## CLI Recipes (`Top-3` On-Demand Retrieval)

```bash
python3 skills/design-studio/scripts/design_catalog.py search --query "滑动确认"
python3 skills/design-studio/scripts/design_catalog.py recommend --platform web --surface-mode Operate
python3 skills/design-studio/scripts/design_catalog.py record-use --id MP-002 --context "checkout" --rating liked
```
