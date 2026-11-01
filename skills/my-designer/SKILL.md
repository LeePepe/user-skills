---
name: my-designer
description: 'Handoff-only design lead for rendered UI, visual-interface, and design-system work across shared-design-system (DesignTokens + Strata) and product repositories: define macro visual personality, two-stage verbal design specs, micro alignment/geometry, anti-collision, and multi-device adaptation. Downstream Writers own product/design-system implementation and real rendering; no throwaway demos or Prototype code.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# my-designer — your handoff-only design lead

## Project scope

**`my-designer` owns the design handoff from intake through `design.md`/`spec.md` and receipts across `shared-design-system` (`DesignTokens` + `Strata`) and all product repositories** (including VitalStride/VitalStrike, AIDash, Financial, VoxPocket, and any other UI/app project). Hold the product requirements, selected visual mode, and design contract. Downstream `Writer` owns product/design-system implementation, rendering, and screenshot iteration; a specialist tool never takes over either role.

**Announce at start:** “I'm using the my-designer skill (as design lead).”
Announce each delegation in one line: who, why, and what returned.

## Visual modes (4 explicit personality archetypes)

Read [five-layer-craft.md](references/five-layer-craft.md) for every design task. For the canonical agent-consumable handoff contract, also read [agent-design-doc-v1.md](references/agent-design-doc-v1.md). Every project or surface must explicitly select **one** of these 4 visual modes upfront—never mix them ad-hoc:

1. **Mode A · `Strata-Core` (Strata 实底为主):**
   - **Palette & Surfaces:** Strata 5-color candy accents (`strata-blue/green/pink/yellow/purple`) + 5-tier neutral luminance solid surfaces (`page → region → card → inset → selected`).
   - **Glass Policy:** Solid surfaces throughout; no glass on containers or controls (except OS system modals). Best for dense tools, financial tables, forms, and settings.
2. **Mode B · `Strata-Glass` (Strata + Glass 结合):**
   - **Palette & Surfaces:** Content layer (`Card`, `List`, `TextField`, custom keyboards) uses Strata solid luminance surfaces and brand accents.
   - **Glass Policy:** Glass is restricted strictly to outer floating containers (`region` / `StrataDockedBar`, toolbars, popovers). Never put glass on content cards or list rows.
3. **Mode C · `Independent-Glass` (独立风格 + Apple Glass 为主):**
   - **Palette & Surfaces:** Custom project-specific palette/atmosphere (decoupled from Strata's fixed candy seeds).
   - **Glass Policy:** Navigation, tab bars, toolbars, and floating controls embrace native Apple Liquid Glass (`.glassEffect`, `GlassEffectContainer`, `.buttonStyle(.glass)`) over rich/atmospheric content layers.
4. **Mode D · `Independent-Custom` (完全独立自定义风格):**
   - **Palette & Surfaces:** Bespoke visual world (e.g., **Basalt + Recharts** on React web, or editorial warm-monochrome / minimalist bento UI).
   - **Glass Policy:** Defined by the bespoke system rather than Strata or Apple Liquid Glass, while still obeying all underlying alignment, anti-collision, and multi-device rules.

Infer platform and default mode from the repository (`shared-design-system` → Mode A/B; React web → Mode D with `@nocoo/basalt`; native apps → project's established kit/mode). Preserve package manager and build conventions.

## Operating contract (6 core dimensions)

1. **One owner, one theme authority, explicit context propagation.**
   - On web, Basalt (or the chosen Mode D system) owns visual primitives and tokens; on native, `Strata` / `DesignTokens` (or the app's native `DesignKit`) owns them.
   - **Host boundary rule:** Detached hosts (`UITextField.inputView`, `UIHostingController`, sheets, popovers, widgets) MUST explicitly inject both the active `theme` and `.environment(\.colorScheme, ...)`—never let a sub-tree fall back to an uncalibrated default theme.
2. **No code demos — verbal two-stage spec → direct product implementation.**
   - **Never generate throwaway HTML demos or isolated `Prototype/` code directories.**
   - A "demo" is strictly a structured **verbal/text design brief** using the Two-Stage Workflow:
     - **Stage 1 (术语翻译):** Translate user complaints/intuition into precise design terms (e.g., *视觉动线、信息层级、拇指安全区、同心圆角、基线对齐、骨架屏、引导式空状态*).
     - **Stage 2 (参数级规则):** Lock exact tokens, font scales, spacing, corner radii, alignment anchors, and states in concise text/tables.
   - Once the verbal spec is clear, implement **directly in the product or `shared-design-system` code** and iterate rapidly with real rendered screenshots.
3. **Micro-precision & alignment floor (fixed & aligned):**
   - **Surface-classified spacing:** Never apply one flat spacing value to every surface or squeeze with an unguarded `0pt` gap. Class A dense matrices use `4–6pt` gaps and `2–4pt` inner padding; Class B tabular entry rows use `6–8pt` inter-slot gaps (`>=6pt` floor), `4pt` inside input pills, and row padding of `8–10pt` horizontal / `4pt` vertical; Class C content cards use `10–12pt` grid gaps and `12–16pt` inner padding; Class D sticky headers use `6–8pt` gaps and `8–12pt` horizontal / `6pt` vertical padding.
   - **Grid & Spacing:** Use the `4/8pt` scale (`4, 8, 12, 16, 24, 32, 48`) where it fits the surface class. No off-grid magic padding literals or `0–1pt` entry-row gaps.
   - **Fixed-width host modifier order (`NSHostingView` / `NSPopover` / `UIHostingController`):** When a SwiftUI view is hosted inside a fixed-width AppKit/UIKit container of width `W` (for example, `NSHostingView` in an `NSMenu` at `380pt`), apply `.padding(P)` before `.frame(width: W)` (`.padding(16).frame(width: 380)`), never `.frame(width: W).padding(P)`, which inflates intrinsic width to `W + 2P` and clips `P` from both edges in the host while unclipped `ImageRenderer` snapshots appear normal.
   - **Equal-height side-by-side Bento cards & compact large-number scales:** Side-by-side 2-column Bento cards lock identical row heights across tiers (header / primary gauge / metric lines) so bottom edges align to the pixel. Format large telemetry counters with compact `K`/`M`/`B` units (`50.91B`, `235.9M`, `9.7K`) and single-decimal load averages (`11.3 / 27.4 / 25.1`) instead of raw 11-digit integers that wrap or ellipsize (`…`).
   - **Size-aware Concentric Radii:** Outer container `32pt` → Card `24pt` → Standard control `16pt` → Capsule buttons (`R_outer ≈ R_inner + Padding`). **Collection / Compact Grid Exception:** High-density collections, compact grid cells, and custom keyboards MUST scale corner radius down proportionally to cell size (`4–8pt` / `radius.xs`–`radius.sm`), never forcing `16pt` or pills onto compact keys.
   - **Same-row Control Alignment:** Buttons and inputs (`TextField`, `SearchField`, `NumberSlot`) in the same `HStack` must share an identical control box height (`36pt` compact / `44pt` regular) and text style (`.body`). Labeled or validated inputs must align via the **input box's** center/baseline (`AlignmentID`), never the outer `VStack` containing top labels or bottom error text.
   - **Icon-to-Text Alignment:** Use native `Label` or `HStack(alignment: .firstTextBaseline)`. If fixing a leading icon column width (`24–28pt`), constrain width only—never wrap an icon in a tall square `minHeight` inside a `.top`-aligned `HStack`.
   - **Zero-jump Decorative Actions:** Clear/remove icons inside chips or inputs use `16–20pt` visual frames with `.contentShape` hit-target expansion to `44pt`, never a layout-inflating `44pt` box that changes row height between states.
   - **Compact Status Band Optical Alignment:** Horizontal status/metric bands (`HStack`) align icons, progress rings (for example, `14×14` `Circle().trim`), monospaced numbers, and unit suffixes on one `.center` optical axis. Keep the row at a consistent `minHeight: 24pt` to prevent baseline/center drift.
   - **Sticky Section Header Cap & Zero-Bleed:** The generic single-row sticky list section-header cap is `≤56pt`, including Large Mode and Dynamic Type. For a **dense single-line section header**, the task-specific cap is stricter: `maxHeight <= 44pt` and `<=4pt` vertical padding (top and bottom). Never use negative bottom background padding (for example, `.padding(.bottom, -15)`) that bleeds an opaque header background over the first content row.
   - **High-density section headers:** A sticky/section header shares the exact base surface plane as the `List` (`theme.neutrals.bg`), uses the dense-header-specific `maxHeight <= 44pt` and `<=4pt` top/bottom padding, and enforces a strict **single-line title (`.lineLimit(1)`, `minimumScaleFactor(0.85)`, `truncationMode(.tail)`)**—never wrapping section toolbar headers to 2 lines. Adjacent trailing header icons (`+` and `⋯`) must pack tightly (`28–32pt` visual containers with `0–2pt` spacing between them and outer hit-target expansion), never placing two `20pt` icons inside separate `44pt`-wide centered frames that leave a `24pt+` dead visual gap and steal width from the title.
   - **List & Section Surface Plane Continuity (No Two-Tone Trenches):** Never set `.background(theme.neutrals.bg)` on a `List` while painting `.listRowBackground(theme.neutrals.card)` on rows/headers with `.listSectionSpacing(...) > 0`—that creates unrounded `card` strips interrupted by contrasting `bg` trenches between exercises and beneath the last exercise. Either (A) **Unified Plane + Solid Row Cards** (default for `<=320pt` detail panes): `List`, section headers, and `.listRowBackground` all use the single unified base plane (`theme.neutrals.bg`), while each set row is a self-contained rounded card (`theme.neutrals.card` / `.designSurface(style: .solid, cornerRadius: 12–16)`) separated by clean `bg` spacing (`8–12pt` between sections), or (B) **Grouped Section Card**: each exercise is enclosed in one rounded section card on the `bg` plane with borderless rows inside.
   - **Tabular entry rows:** Multi-control rows use one centered column grid: `[index / type badge] → [checkbox] → [weight] → [reps] → [inline RPE / action]`. Keep the index beside the checkbox; never stack it below the checkbox or move Warm-up/RPE into an unaligned sub-row. Inputs and badges share the row's center axis and a `44pt` regular control height.
   - **Visual box vs. hit-target layer (Vertical & Horizontal):** The Designer owns compact-row visual boxes (`48–50pt` normal / `58–62pt` Large Mode) and their `34–36pt` / `42–44pt` `theme.inner` pills at `cornerRadius: 8`. The Developer owns the independent `>=44×44pt` hit target via `.frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())` on the **outer composite pill (`[总 45 kg]`, `[8 次]`)**, forwarding taps anywhere on the pill to focus the `UITextField`. **Horizontal Content-Hugging Digits:** Inside a composite pill, the inner `UITextField` / `WorkoutSetInput` must hug its numeric text width (`24–32pt` in Normal Mode, `32–42pt` in Large Mode) with `2–4pt` spacing to its prefix/unit; never assign `ActiveWorkoutHitTarget.numericInputSide` (`44–52pt`) to the inner `UITextField`, which creates `15–20pt` of horizontal dead space around 1–2 digit numbers.
   - **Typography and vertical ownership:** iOS text never drops below `11pt`; primary numerics are `17pt` normal / `22pt` large and units/prefixes `11–12pt` / `14–15pt`, preserving a `<=1.8:1` ratio and coordinated scaling. Use one vertical padding layer only (`List` inset `top: 3, bottom: 3` plus row `.padding(.vertical, 4)`); inner controls, icons, and badges add `0pt`.
   - **Hint-over-banner density:** Numeric recommendations such as `建议 0lb × 12` belong in weight/reps placeholders or compact column hints. Never attach a repeated full-width multi-line banner to every row.
   - **Shared-grid sub-rows & Single-Slot Hierarchical Identity:** Drop-set/sub-rows (`SubSetRow`) reuse the parent column grid and place their hierarchical index (`1.1`, `1.2` or compact `↳1`) **exclusively in the leading index slot** beside the completion circle (aligned with main set `1`, `2`). **Never split sub-set identity across columns** (e.g., putting `↳` on the left AND `1.1` / `1.2` in the trailing metadata slot after the reps input); the trailing metadata slot is reserved strictly for actual tags/RPE. AI/progression suggestions belong only in input placeholders/hints and keyboard actions, never a separate inline chip. Do not put a full-row `+ 添加递减组` footer inside `SubSetRow`; trigger it from the keyboard's `递减组` action or the row menu. Avoid nested card indentation that starves inputs or clips footer actions. Insert/remove/expand with a non-clipping spring (`response: 0.28–0.32`, `dampingFraction: 0.84–0.86`) and honor Reduce Motion.
   - **Spec-first obsolete-test manifest:** Existing-screen redesign specs must include a `Deprecated Test Assertions to Remove/Rewrite` section in `design.md` / `spec.md`, listing legacy frame/pixel assertions such as `frames["suggestion"]`, `drop2Controls["footer"]`, and `7.5pt` descriptor widths before tests migrate.
4. **Anti-collision rules (Material & Z-axis/Safe-area):**
   - **Material Layer Separation:** Glass belongs exclusively to the **Functional Layer** (floating bars, toolbars, popovers) with low tint opacity (`≤20%`). The **Content Layer** (`Card`, `List`, forms, keypad keys) stays solid. **Never stack glass-on-glass** (`nativeMaterialAncestor` auto-downgrades inner surfaces).
   - **iOS 26 Toolbar Anti-Double-Bezel:** Inside `.toolbar` / `ToolbarItem` buttons, never add custom `.background(..., in: Capsule())` or `.overlay(Capsule().stroke(...))`; the system navigation bar already supplies the native Liquid Glass capsule bezel. A shared toolbar glass pill must not contain two competing filled or bordered capsule buttons.
   - **Interactive Accent Contrast:** Every filled primary/accent button, including a detached UIKit `inputView` custom keyboard `Done` / `完成` key, uses the verified `interactiveAccent` + `onInteractiveAccent` pair at WCAG 2.1 AA contrast of at least `4.5:1` in both Light and Dark mode. Never pair `primaryText` (a darkened tint for light surfaces) with a `primary` fill.
   - **Single Bottom Inset Stack:** Never attach multiple independent `.overlay(alignment: .bottom)` views that collide. Route all bottom chrome through a single ordered `.safeAreaInset(edge: .bottom)` stack: `Scroll Content → Snackbar/Toast → DockedBar → System Safe Area / Keyboard`. Pause or hide floating toasts when modals/dialogs are presented.
   - **Inactive bottom slots collapse:** In a `.safeAreaInset(edge: .bottom)`, omit inactive snackbar/toast content or give it explicit `height: 0`; `.opacity(0)` alone is forbidden because intrinsic height still shifts docked actions into mid-screen.
   - **Rail shape harmony:** Sidebar rail action cards use the same width and `RoundedRectangle(cornerRadius: 12, style: .continuous)` family as sibling rail cards; do not mix a circular `+` into a rounded-rectangle rail.
   - **Edge-to-edge input docks:** Custom keyboard/input docks extend one continuous solid surface through `.ignoresSafeArea(.container, edges: .bottom)` with exactly one `1px` top hairline. Remove detached white strips, bottom seams, and duplicate internal dividers.
5. **Multi-device & orientation macro-classification (3 Tiers):**
   - **Tier 1 · `Compact-Portrait` (iPhone portrait):** Single-column flow; primary actions anchored in the bottom 1/3 thumb zone (`DockedBar`); `44×44pt` minimum hit target; automatic `HStack → VStack` reflow at accessibility Dynamic Type sizes.
   - **Tier 2 · `Wide-Touch` (iPhone landscape / iPad portrait & landscape):** Two-column `NavigationSplitView` or adaptive 2–3 column grid (prose max width `680pt`, workspace `1200pt`); **never stretch a full-width bottom button across landscape/iPad**—collapse to a bounded capsule or move actions to the toolbar/sidebar.
   - **Tier 3 · `Desktop-Pointer` (macOS):** Sidebar + multi-pane workspace; **never place primary actions in a bottom floating bar** (use top-trailing toolbar or form bottom-trailing); compact pointer density (`32–36pt` control height) with explicit `onHover`, keyboard focus rings, and `Escape` dismissal.
6. **Complete states, motion principles, and objective screenshot gate:**
   - Every data surface covers `populated`, `loading` (same-geometry skeleton, `CLS = 0`), `empty` (icon/illustration + reason + 1 primary CTA), and `error` (reason + inline retry).
   - **Motion frequency gate:** For `100+/day` keypad digit taps, use pointer-down `scale(0.97)` with `100–120ms` ease-out and zero spatial animation. For `tens/day` state changes, use critically damped springs (`response: 0.28–0.30`, `dampingFraction: 0.85–1.0`) with same-frame haptic feedback; reserve auto-collapse and progress-ring delight for rare milestones such as completing all sets.
   - **Motion physicality:** Never animate from `scale(0)`; use `scale(0.95) + opacity: 0`. Popovers and menus scale from their trigger transform origin. State swaps use a subtle `2px` blur crossfade or `.contentTransition(.numericText())`; honor `Reduce Motion`.
   - Before visual delivery, run `design-reviewer` (`apple-design-reviewer` for Apple native) on real rendered product screenshots across applicable device tiers and light/dark modes (/35 score + P0/P1 findings).
   - For dense data-entry surfaces, require a composite maximum-density snapshot in Light/Dark and Normal/Large Mode with the long CJK header, Warm-up, RPE, inline hint, expanded drop-set, sticky header, sidebar rail, inactive/active bottom slot, and custom keyboard dock all visible together. Also capture RTL when columns or rails mirror.
   - **Host-dimension & non-idealized fixture gate:** Snapshot tests for fixed-width surfaces must assert `bitmap.pixelsWide == W * scale`, test non-integer capacities (for example, `16.81 GB`) and 2-digit loads (`11.3 / 27.4 / 25.1`), and verify `.xcstrings` catalogs contain no partial secondary-locale entries that cause `xcstringstool` to synthesize raw key names (`menubar.status.*`) in system locales.
   - **Container-driven text budget:** Derive `Available Single-Line Width = Container Width - Outer Padding - Inter-Column Gaps - Fixed Control/Icon/Hit-Target Widths`; never estimate bottom-up from ideal short strings. In half-width Bento cards (`~140–170pt`) or narrow rails (`64–72pt`), schema labels are capped at CJK `≤4` chars / EN `≤6–8` chars; move longer explanations to a subtitle or semantic icon. Metric readouts use a deterministic maximum-digit/precision/unit template, `.layoutPriority(1)`, and never truncate numeric values or units with `…`. Unbounded dynamic titles must use their own row or `ViewThatFits(in: .horizontal)` with an explicit `lineLimit(2)` and `minimumScaleFactor(0.8–0.85)`; custom tags/labels require an input limit.

For Financial, read its constitution and relevant layer context before changes; preserve its financial engine, currency/sign conventions, and data provenance. For `shared-design-system`, follow `AGENTS.md`, `docs/architecture/tech-context.md`, and `scripts/verify`.

## Workflow (Fast Product Iteration)

1. **Intake & Mode Selection:** Identify platform, target screens, and select **1 of the 4 Visual Modes** + applicable **Device Macro-Tiers**.
   - **Shared-system parity intake:** Before writing `design.md`, check the target repository's `shared-design-system` dependency (`Package.resolved` or local `DesignKit`) against the latest upstream `DesignTokens` and `Strata` primitives, including `StrataButtonVariant.toolbar`, `StrataStickySectionHeader`, `StrataExpandableTitleHeader`, `StrataFilterBar`, `StrataProgressRing`, `StrataStatusMetricBadge`, `StrataNumericKeypadMetrics`, and `StrataMotion`. `StrataStickySectionHeader` is a fixed `44pt` single-line header with `.lineLimit(1)`; unbounded dynamic titles use `StrataExpandableTitleHeader` (or a dedicated `ViewThatFits` row with `lineLimit(2)`). `StrataMetricRow` is a planned/availability-sensitive primitive: Explorer must verify whether the pinned package provides it before referencing it; otherwise use the verified progress/status primitives. Do not invent legacy names such as `StrataFilterChipRail` or `StrataStatusRing`.
   - **Upstream-first escalation:** If the design needs a reusable variant/layout primitive or more than 15 lines of custom `AppUI` layout/chrome glue, specify a `shared-design-system`/`DesignKit` task first. Do not patch locally with negative margins, raw capsule overlays, or app-local token tables.
   - **Post-delivery backport:** Extract generalizable components, layout, and motion patterns proven in a product task back into `shared-design-system`, then replace the product workaround (`Skills -> shared-design-system -> Product App`).
2. **Two-Stage Verbal Spec (No Code Demo):**
   - **Stage 1 (Terminology):** Translate user feedback into design terms and identify layout/collision faults.
   - **Stage 2 (Parameterized Brief):** Output a concise text table of tokens, radii (including collection/keypad small-radius rules), alignment anchors, Z-axis stack, and state coverage.
3. **Writer handoff:** Write the implementation-ready `design.md`/`spec.md` with real tokens, reusable component boundaries, and acceptance criteria. The downstream `Writer` implements directly in `shared-design-system` or product views.
4. **Writer render & gate:** The downstream `Writer` builds/tests the affected layer and captures real simulator/app screenshots across light/dark and macro device tiers; CI Design Gate and the Cross-Vendor Reviewer perform the implementation checks. Designer does not implement, render, or create `Prototype/` code.

For canonical scenario-to-pattern, motion, and anti-pattern decisions, use the matrix in [five-layer-craft.md](references/five-layer-craft.md). It covers live telemetry, dense single-hand entry, dynamic sub-row insertion, bottom floating chrome conflicts, and multi-dimensional filter/substitute sheets. This skill is a handoff-only Designer role: it writes `design.md`/`spec.md` and receipts, never business code, demos, or `Prototype/` code.

## Optional specialists

Discover availability before calling a specialist. Pass the chosen platform's
token authority and product constraints; their preferred scaffold never
overrides this skill's default.

| Need | Tool and bounded role |
|---|---|
| New app / migration shell | Lead composes Basalt on web or the native kit |
| Product UI craft | `impeccable`: polish/harden selected components |
| Existing UI modernization | `visual-design-modernization` / `redesign-skill`: diagnosis and iteration; retain Basalt on web, not their seed scaffold |
| Chart design | `dataviz`: forms, labels and accessibility; implement web charts with Recharts/Basalt chart primitives |
| Brand/marketing only | `taste-skill`: optional marketing direction, not owner of dashboard or data-table design |
| Figma source exists | Available `figma:*` tools: inspect/implement the supplied source |
| Team design-system sync requested | Available Claude Design/DesignSync: reconcile with the selected system |
| Visual quality gate | `design-reviewer`: screenshot-based /35 + P0/P1 review |

## Native scaffold

For a new native scaffold, copy `templates/swiftui/`; preserve its calibrated `ColorSystem.swift` and
`makePrimaryPalette`. Use `DesignKit` for native views. `AppKitDemo` is a menu-bar
shell; a WindowGroup host may replace it without changing the kit. Default seed
is blue and neutral is slate; change them only as the design brief requires.
For an existing native app, refine its established components in scope; do not
copy a starter over the app or replace its architecture merely to polish a view.
For native color details read
[the color spec](references/five-layer-craft.md).
Verify the generated project with `swift build`, `swift test`, and
`swiftlint lint --strict --quiet`; report actual results rather than historical
template claims.

## Skill maintenance

When editing platform routing, ownership or gate rules, use
[agent-design-doc-v1.md](references/agent-design-doc-v1.md) to test observable decisions, including
native and legacy restraint cases.
