# Orthogonal X-Y-Z Interactive Layering & Anti-Flat-Tiling Framework (`L1` Reference)

Load this file on demand when designing `layout-navigation` (`LN`), `component` (`CP`), `chart-dataviz` (`DV`), or `motion-interaction` (`MI`) surfaces, or when resolving `DC-001` / `DC-002` flat-tiling defects.

Never design a screen by dumping every feature or candidate component onto a single vertical scroll plane. Every screen in `design.md` MUST pass the **3 Orthogonal Axes (`X-Y-Z`) of Interactive Layering** below, synthesizing the 5 canonical HCI/design laws into one executable contract.

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

| Priority Tier | 1. `Display Only` (纯展示) | 2. `Action Only` (纯操作) | 3. `Display + Action` (展示+操作复合) |
|---|---|---|---|
| **`P0` · Primary Focal**<br>*(1 per viewport; `100+/day`)* | Single primary anchor metric or active object title (e.g., Active Exercise / Monthly Total Spend Hero) | Single primary commit CTA (`✓ Complete Set`, `Record`) | Primary direct-manipulation target (`Weight × Reps` cells, active interactive chart/table) |
| **`P1` · Inline Companion**<br>*(Non-blocking; `10s/day`)* | Non-blocking companion status bar (`⏱ Rest 01:42 · ♥ 136 BPM`, active filter summary pill) | High-frequency creation or lens toggle (`+ Add Exercise`, Segmented Lens `[Breakdown | Wallet]`) | **Merged Placeholder Hints & Direct Filter Tiles**: put `Previous / Suggested` values inside input placeholders (kill separate "Previous" columns!); make chart Bento tiles directly clickable to filter rows |
| **`P2` · Collapsed Context**<br>*(Folded by default)* | **Collapsed siblings**: completed/upcoming exercises folded to single-line headers; secondary account cards tucked inside `SWWallet` `ZStack` | Undo toast, `±15s` timer nudge, expand/collapse toggle | Secondary perspective folded behind Segmented Control or 3D `ZStack` card deck (tap card header to peek/filter) |
| **`P3` · On-Demand Drill-Down**<br>*(Rare / configuration)* | Audit metadata (`external_id`, `transfer_group_id`, full 40-category tail) hidden until requested | Low-frequency or destructive actions (reorder, AI swap, unit toggle, delete, settings) | Tucked into row/toolbar `⋯` menu (`>=44×44pt`), custom keypad function keys, or `L3` Receipt Detail `Sheet` / `Inspector` |

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

### Axis Z · Separate Functional Glass Chrome from Solid Content (`Apple Two-Layer Rule`)

- **Top Z-Layer (Floating Functional Chrome — `.glassEffect` + `GlassEffectContainer`)**:
  - Houses unified 1-row toolbar (`Period Menu` + `SWSearchBar` + compact `32pt` filter pills), mode switchers, and non-blocking companion bars (`.safeAreaInset`).
- **Bottom Z-Layer (Scrolling Content Canvas — Solid Semantic Luminance Tiers)**:
  - Houses `L0` canvas, `L1` grouped sections/lists, and `L2` focal cards. Never apply `.glassEffect` inside content rows, and never use heavy drop shadows where luminance tiers (`L0 < L1 < L2`) and 1px indented hairlines suffice.
- **Breathing Room & Zero-Guessing UX (`L1` Companion)**:
  - When tuning spacing ratios (`1:3:6`, `padding >= spacing`), luminance borders, `90/10` chromatic restraint, or novice-friendly microcopy (`说法 · 入口 · 反馈`), load [breathing-and-clarity.md](breathing-and-clarity.md).
