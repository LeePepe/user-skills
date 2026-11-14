# Orthogonal X-Y-Z Interactive Layering & Anti-Flat-Tiling Framework (`L1` Reference)

Load this file on demand when designing `layout-navigation` (`LN`), `component` (`CP`), `chart-dataviz` (`DV`), or `motion-interaction` (`MI`) surfaces, or when resolving `DC-001` / `DC-002` flat-tiling defects.

Never design a screen by dumping every feature or candidate component onto a single vertical scroll plane. Every screen in `design.md` MUST pass the **3 Orthogonal Axes (`X-Y-Z`) of Interactive Layering** below, synthesizing the 5 canonical HCI/design laws into one executable contract.

## Direction-Lock & 10-Variant Exploration Gate (`B0 -> B2`)
- **Known direction**: collect a reference app/screenshot and anti-patterns, then run `scaffold-gallery --mode screen --direction VS-* --screen-content "domain content"` for ten orthogonal depth variants before locking coordinates; the selected archetype and real screen content are consumed by every variant.
- **Ambiguous direction**: run the same ten-variant gallery for 1–3 rounds, spanning VS archetypes, P0 hypotheses, and Axis-Y folding/navigation mechanisms. Every pair differs on at least two substantive axes; palette/radius swaps do not count.
- **Separate full-app track**: `scaffold-gallery --mode app-nav --platform mobile|desktop|macos|web` generates four bespoke, graph-driven app01..app04 schemes inside live iframe device bezels; `--category` keeps multiple rounds addressable. The Designer supplies four complete target-domain `--app-flows-json` schemes (see [navigation-evidence-contract.md](navigation-evidence-contract.md)); screen/context hints alone reject rather than invent journeys. Each scheme owns operative chrome, action-frequency priorities and push/pop/tab/sheet edges. The no-input fallback is only a starter skeleton, never a fixed template.
- Record the variant matrix, Owner selection, and feedback through `ask_user`; after selection freeze shared colors, spacing, font sizes, and native conventions before direct production implementation. If round three remains ambiguous, stop for Owner decision; never auto-lock.
- Product-repository implementation remains No Code Demo: never create `Prototype/` directories or throwaway product code. Handoff-only exploration may use the offline `scaffold-gallery` under `~/Development/handoff/<goal>/r<N>/`; it can also mount to `https://leepepe.github.io/demos/<goal>/`. Preserve B4 Visual Traceback and regression contracts. App/session preferences belong in `session_anti_patterns` and `style_overrides`, not universal laws.
- When B3 freezes the direction, or the Goal completes, immediately run `design_catalog.py cleanup-demo --goal <goal>` to burn both blog and handoff HTML rounds; receipts and design artifacts remain.

---

## 1. The 5 Industry Layering Laws & Their Synthesis

| # | Canonical Law / Framework | Origin / Authority | What Dimension It Governs | Failure Mode If Ignored |
|---|---|---|---|---|
| **1** | **Role × Frequency Priority Matrix** (`Display / Action / Display+Action` × `P0..P3`) | Owner's VitalStride Matrix + Emil Kowalski Frequency Gate + Hick's Law | **Information & Control Filtering (`What stays vs. hides`)** | Too many columns, inline buttons, or blocking modals on routine `100+/day` actions. |
| **2** | **Progressive Disclosure & Chunking Cap** (`<= 3` Visual Groups per Viewport) | Jakob Nielsen (NN/g) & George Miller (`3±1` working-memory chunks) | **Spatial Folding (`How sections fold in X/Y`)** | Single-plane vertical flat tiling (`>1000pt` scroll columns with 4–6 stacked cards/lists). |
| **3** | **Visual Information-Seeking Mantra** (`Overview -> Zoom & Filter -> Details-on-Demand`) | Ben Shneiderman & Edward Tufte (Data-Ink Ratio / Zero Redundancy) | **Cognitive Drill-Down & Data Uniqueness** | Rendering the same dataset twice (e.g., Donut legend + redundant `ProgressView` category bars) or dumping raw database fields inline. |
| **4** | **Two-Layer Z-Axis Material Hierarchy** (`Functional Glass Chrome` vs. `Solid Content`) | Apple HIG (iOS 26 / macOS 26 Liquid Glass) | **Z-Axis Elevation (`Floating Control vs. Scrolling Content`)** | Putting `.glassEffect` on content rows, or letting global filters scroll out of reach. |
| **5** | **Pace Layering** (`Fast / Medium / Slow` Change Cadence) | Stewart Brand / Linear & Flighty Cockpit Design | **Temporal Separation (`Live state vs. static config`)** | Mixing second-by-second live timers/waveforms with monthly account settings on the same visual tier. |

---

## 2. Executable 3-Step (`X-Y-Z`) Layering Protocol for `design.md`

### Axis X · Filter by `Interaction Role × Frequency Priority (P0..P3)` (Who Stays vs. Who Folds)

Before drawing layout boxes, classify every domain element in a `3 × 4` table:

| Priority Tier | 1. `Display Only` (纯展示) | 2. `Action Only` (纯操作 · `Spatial Action Priority: Bottom > Top > Secondary Menu`) | 3. `Display + Action` (展示+操作复合) |
|---|---|---|---|
| **`P0` · Primary Focal**<br>*(1 per viewport; `100+/day`)* | Single primary anchor metric or active object title (e.g., Active Exercise / Monthly Total Spend Hero) | **`Bottom` (Thumb Golden Zone)**: Single primary commit CTA (`✓ Complete Set`, `Record`) anchored in `.safeAreaInset(edge: .bottom)`, primary card footer, or separated `Tab(role: .prominent)` (`.topBarPinnedTrailing` on desktop) | Primary direct-manipulation target (`Weight × Reps` cells, active interactive chart/table) |
| **`P1` · Inline Companion**<br>*(Non-blocking; `10s/day`)* | Non-blocking companion status bar (`⏱ Rest 01:42 · ♥ 136 BPM`, active filter summary pill) | **`Bottom` Creation vs. `Top` Lens**: High-frequency creation (`+ Add Exercise`) in `Bottom` thumb zone; session/global lens switchers in `Top` `.toolbar` (`.visibilityPriority(.high)`, `ToolbarSpacer` — never cram creation next to session finish in top bar) | **Merged Placeholder Hints & Direct Filter Tiles**: put `Previous / Suggested` values inside input placeholders (kill separate "Previous" columns!); make chart Bento tiles directly clickable to filter rows |
| **`P2` · Collapsed Context**<br>*(Folded by default)* | **Collapsed siblings**: completed/upcoming exercises folded to single-line headers; secondary account cards tucked inside `SWWallet` `ZStack` | **`Top` Session / Inline Nudge**: Session finish (`Finish`), undo toast, `±15s` timer nudge, expand/collapse toggle, `.toolbarMinimizeBehavior(.onScrollDown, for: .navigationBar)` | Secondary perspective folded behind Segmented Control or 3D `ZStack` card deck (tap card header to peek/filter) |
| **`P3` · On-Demand Drill-Down**<br>*(Rare / configuration)* | Audit metadata (`external_id`, `transfer_group_id`, full 40-category tail) hidden until requested | **`Secondary Menu` (`⋯` / Sheet / Swipe)**: Reorder, replace, AI substitute, unit toggle, delete, or discard auto-collapsed into `ToolbarOverflowMenu` (`⋯`) or `Sheet` | Tucked into row/toolbar `ToolbarOverflowMenu` (`>=44×44pt`), custom keypad function keys, or `L3` Receipt Detail `Sheet` / `Inspector` |

### Axis Y · Fold Space via Chunking Cap & Shneiderman Mantra (`<= 2–3` Groups per Pane)

1. **Hard Cap of `2–3` Visual Container Groups per Viewport / Split-View Column (`aspect_ratio <= 2.6`)**:
   - Pattern for Dashboards / Ledgers (`Shneiderman Mantra`):
     - **Group 1 (`Overview`)**: Compact Hero Stage Card (primary metric + inline proportion bar).
     - **Group 2 (`Zoom & Filter`)**: Interactive Secondary Stage — **pick ONE active lens** or toggle via a Segmented Control (`[ 支出结构 Donut+Bento | 账户卡包 SWWallet ]`), or use a collapsed 3D `ZStack` pocket (`SWWallet` `~220pt` height).
     - **Group 3 (`Details-on-Demand`)**: Grouped timeline/list rows; clicking a row opens `L3` `Sheet`/`Inspector`.
2. **Three Mandatory Folding Mechanisms (Pick at least 1 when modules `>= 3`)**:
   - **Mechanism A · Accordion Single-Active (`VitalStride` Tiled Mode)**: Expand ONLY the currently active item (`selectedExerciseID`); fold all completed/upcoming items into 1-line summary headers.
   - **Mechanism B · Segmented Lens Switcher (`Operate ⇄ Focus` / `Breakdown ⇄ Accounts`)**: Share the same stage footprint between mutually exclusive analytical lenses instead of stacking them vertically.
   - **Mechanism C · 3D `ZStack` Pocket Stack (`ShipSwift SWWallet`)**: Stack multi-card collections along the Z-axis (`ZStack` + `zIndex` + `offset(y:)` + `scaleEffect`, `~220pt` collapsed height) that fan out only when revealed.
3. **Tufte Zero-Redundancy Rule (`DC-002`)**:
   - Never render the same dataset twice in one viewport (e.g., `SWDonutChart` with a 6-row legend + an 8-row `ProgressView` category bar list below it). Fuse the chart and legend into a single **Donut + 2-Column Interactive Bento Grid** where tapping a tile filters the list.
4. **Compact Width Anti-Overflow & Shared Table Column Geometry (`DC-003` / `DC-005`)**:
   - Wrap dynamic horizontal `HStack { ForEach(...) }` rails in `ScrollView(.horizontal, showsIndicators: false)` scoped to the active parent item; cap complex interactive cards at `<= 2` per compact horizontal row (`2 + 1` vertical split for 3 modules like `L + R + Reps`); apply `.containerRelativeFrame(.horizontal)` on vertical `ScrollView` content roots; and enforce 100% shared column geometry between multi-column table Headers and data Rows (identical fixed widths, identical `maxWidth: .infinity` flex columns, matching per-column alignment, and identical outer `HStack` spacing/padding, or `Grid`/`GridRow` — never pair a `maxWidth: .infinity` header with fixed-width-only rows).

### Axis Z · Separate Functional Glass Chrome from Solid Content (`Apple Two-Layer & Optical Sampling Rule`)

- **Top Z-Layer (Floating Functional Chrome — `.glassEffect` + `GlassEffectContainer` + `Bottom > Top > Secondary Menu` Action Hierarchy)**:
  - Houses unified 1-row `Top` `.toolbar` (session/global lens switchers, `Period Menu`, `SWSearchBar`, compact `32pt` filter pills) and `Bottom` `.safeAreaInset(edge: .bottom)` thumb-zone bars (`P0/P1` core commits & creation on mobile; `P2/P3` low-frequency/destructive actions tucked into `⋯` `ToolbarOverflowMenu` or `Sheet`).
  - **WWDC25/26 Optical Lensing Contract (`GM-003`)**: Liquid Glass refracts via optical lensing, not simple blur—**glass cannot sample glass**. Group adjacent glass controls inside a single `GlassEffectContainer` with `@Namespace` + `.glassEffectID` for fluid morphing; use `Glass.regular` by default and restrict `Glass.clear` to surfaces satisfying all **3 preconditions** (media-rich backdrop + dimming scrim + bold bright foreground); on macOS 27 add `.interactive()` to custom glass controls for click bounce and observe `@Environment(\.appearsActive)` to dim accents when the window is inactive.
- **Bottom Z-Layer (Scrolling Content Canvas — Solid Semantic Luminance Tiers & 120Hz Prefetch)**:
  - Houses `L0` canvas, `L1` grouped sections/lists, and `L2` focal cards. Never apply `.glassEffect` inside content rows, and keep `LazyVStack`/`LazyHStack` `ForEach` closures strictly `1:1` (filter in `@Query(#Predicate)`, never a top-level `if` inside `ForEach` per `DC-004`).
- **Breathing Room & Zero-Guessing UX (`L1` Companion)**:
  - When tuning spacing ratios (`1:3:6`, `padding >= spacing`), luminance borders, `90/10` chromatic restraint, or novice-friendly microcopy (`说法 · 入口 · 反馈`), load [breathing-and-clarity.md](breathing-and-clarity.md).
