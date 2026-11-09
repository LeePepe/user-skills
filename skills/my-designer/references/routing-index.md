# Progressive Disclosure Routing Index (`L0 -> L1 -> L2`)

This file is the **Level 1 (L1) Router** for Apple-First UI/UX design. Never load the entire HIG directory or all vault files at once. Follow the 3-step progressive disclosure flow below to load only the **8–12 leaf files** needed for the current screen.

---

## Step 1: Lock Platform Topology (`desktop` vs `mobile` vs `web`)

| Platform | Status | Host Containers | Ergonomics & Navigation Floor (Plan A) | Distilled Base (`distilled_in_plan_a` — Skip Raw Read by Default) |
|---|---|---|---|---|
| **`desktop`** (macOS Native) | **Active Primary** | `NSWindow`, `NSPopover`, `MenuBarExtra`, `NavigationSplitView`, `Settings`, `Inspector` | Default target `28×28pt` (min `20×20pt`); default font `13pt` (min `10pt`); Menu Bar command parity, pointer hover, keyboard focus ring, `Esc`/`⌘` shortcuts; **never place a floating bottom action bar** | `designing-for-macos.md`, `accessibility.md`, `layout.md`, `color.md`, `typography.md` (already distilled into Plan A 5 Lenses) |
| **`mobile`** (iOS / iPadOS / iPhone Duo) | **Active Primary** | `NavigationStack`, `TabView`, `Sheet`, `Widget`, `LiveActivity`, `iPhoneDuoSplit` | Default target `44×44pt` (min `28×28pt`); default font `17pt` (min `11pt`); `TabView` for navigation only, `.toolbar` for actions, `Sheet` with grabber/detents, Size Classes instead of hardcoded screen widths | `designing-for-ios.md`, `designing-for-ipados.md`, `accessibility.md`, `layout.md`, `color.md`, `typography.md` (already distilled into Plan A 5 Lenses) |
| **`web`** (Web / Video) | **Active** | `WebViewport`, `RemotionComposition` | Active in `../design-studio/references/vault/web.json` when `--platform web` is selected | N/A |

---

## Step 2: Disclose by Design Dimension (`8 Dimensions -> Native APIs + Leaf HIG`)

Query the inspiration vault for only the active platform and dimension:

```bash
python3 skills/design-studio/scripts/design_catalog.py vault-search \
  --platform desktop --dimension chart-dataviz --query "<intent>" --top 3
```

| Dimension ID | Prefix | Apple Native Primitives + Per-App `AppTheme` & `ShipSwift` Recipes | On-Demand HIG & External Skill Refs |
|---|---|---|---|
| `color-palette` | `CL` | `.primary`, `.secondary`, adaptive Asset Catalog colors, per-app `AppTheme` brand seed (`CL-001..008`, `CL-005` hue-arc rule), `awesome-ios-design-md` 200-app palettes, [breathing-and-clarity.md](breathing-and-clarity.md) | `color.md`, `dark-mode.md`, `branding.md`, `accessibility.md` |
| `visual-style` | `VS` | `ConcentricRectangle`, `.rect(corner: .containerConcentric)`, `NSView.cornerConfiguration`, `.backgroundStyle`, `.symbolRenderingMode(.hierarchical)`, [appearance-ledger.md](appearance-ledger.md) (`VS-001..008`), [breathing-and-clarity.md](breathing-and-clarity.md) | `design-principles.md`, `branding.md`, `materials.md`, `dark-mode.md`, `icons.md`, `sf-symbols.md`, `native-slop.md` |
| `component` | `CP` | Native `NavigationSplitView`, `TabView`, `Tab(role: .prominent)`, `List`, `Table`, `.toolbar`, `ToolbarSpacer`, `ToolbarOverflowMenu`, `.sheet`, `ContentUnavailableView` + `ShipSwift` `SWComponent/` (`SWWallet`, `SWKPICard`), [interaction-layering.md](interaction-layering.md) | `buttons.md`, `menus.md`, `the-menu-bar.md`, `toolbars.md`, `tab-bars.md`, `sidebars.md`, `split-views.md`, `sheets.md`, `popovers.md`, `lists-and-tables.md` |
| `chart-dataviz` | `DV` | Swift Charts (`Chart`, `LineMark`, `AreaMark`, `BarMark`, `SectorMark`, `.chartXSelection`) + `ShipSwift` `SWChart/` (`SWActivityHeatmap`, `SWRadarChart`, `SWRingChart`, `SWNetworkGraph`), [interaction-layering.md](interaction-layering.md) | `charts.md`, `charting-data.md`, `gauges.md`, `progress-indicators.md` |
| `glass-material` | `GM` | `.glassEffect(.regular / .clear)`, `GlassEffectContainer`, `.glassEffectID`, `.buttonStyle(.glassProminent)`, `@Environment(\.appearsActive)`, `.backgroundExtensionEffect()`, `.scrollEdgeEffectStyle(.hard, for:)` + `ShipSwift` `SWMetal/` & `genjutsu` (`GM-003`) | `liquid-glass.md`, `materials.md`, `color.md`, `toolbars.md`, `tab-bars.md`, `sidebars.md` |
| `motion-interaction` | `MI` | `Spring(response:dampingRatio:)`, `.matchedGeometryEffect`, `KeyframeAnimator`, `.onScrollTargetVisibilityChange`, `.swipeActionsContainer()`, `.reorderContainer`, `.dragContainer`, `.dropConfiguration` (`MI-008`, `MP-008/009`), [interaction-layering.md](interaction-layering.md) | `motion.md`, `playing-haptics.md`, `gestures.md`, `feedback.md`, `drag-and-drop.md`, `undo-and-redo.md` |
| `layout-navigation` | `LN` | `ViewThatFits`, `Grid`, `.rect(corner: .containerConcentric)`, `.visibilityPriority(.high)`, `.topBarPinnedTrailing`, `.toolbarMinimizeBehavior(.onScrollDown, for: .navigationBar)`, `@Environment(\.horizontalSizeClass)`, `.alignmentGuide` pin-flip (`LN-002`, `IP-008`), [interaction-layering.md](interaction-layering.md) | `layout.md`, `designing-for-ios.md`, `designing-for-ipados.md`, `designing-for-macos.md`, `designing-for-iphone-duo.md`, `windows.md`, `scroll-views.md` |
| `typography-copy` | `TC` | `AppTextStyle` (`font + lineSpacing + tracking + .monospacedDigit()`), `.bold()`, CJK typography rules, localized `.xcstrings`, [breathing-and-clarity.md](breathing-and-clarity.md) | `typography.md`, `writing.md`, `labels.md`, `loading.md`, `feedback.md`, `right-to-left.md` |

---

## Step 3: Baseline Gate (Plan A Default + Plan C On-Demand Defect Cases)

Run the progressive gate router and code auditor:

```bash
# 1. Disclose active Plan A lenses + any matching Plan C defect cases for this surface
python3 skills/my-designer/scripts/verify_design_cases.py route \
  --platform desktop --dimensions glass-material,chart-dataviz

# 2. Run Plan A Apple Native Code Audit (+ active Plan C checks) on Swift files
python3 shared-ci/scripts/context/design_contract.py --audit-code \
  --platform desktop --path <SwiftUI-File-Or-Dir>
```

### Plan A — Five Apple Native Lenses (Always Active)
1. **Lens 1 · Accessibility (Critical)**: Dynamic Type scaling, `>=4.5:1` text contrast (`>=3:1` large/bold), `44×44pt` mobile / `28×28pt` desktop hit targets, VoiceOver labels on icon-only controls, Reduce Motion & Reduce Transparency fallbacks.
2. **Lens 2 · Platform Conventions (High)**: Native `TabView`/`NavigationSplitView`/`.toolbar`/`MenuBarExtra`; no bottom bars on macOS; no custom hand-drawn tab/navigation bars; iPhone Duo & iPad adapt via Size Classes rather than fixed device widths.
3. **Lens 3 · Visual & Liquid Glass Craft (High)**:
   - **Two-Layer Rule**: Liquid Glass (`glassEffect`, `UIGlassEffect`, `NSGlassEffectView`) belongs strictly to the **Functional Layer** (floating bars, toolbars, popovers, transient controls). Never apply Liquid Glass to the **Content Layer** (cards, list rows, tables, backgrounds).
   - **Color & Stacking Restraint**: Group adjacent glass shapes in `GlassEffectContainer`; at most 1–2 `.glassProminent` tinted controls per view; never paint opaque backgrounds behind system toolbars/tab bars.
4. **Lens 4 · Interaction & State Completeness (Medium)**: Immediate loading feedback, inline error recovery, `ContentUnavailableView` for empty/no-result states, confirmation on destructive actions, undo for common actions.
5. **Lens 5 · Typography & Copy (Medium)**: Platform title/sentence capitalization consistency, `.monospacedDigit()` for changing numbers, clear action verbs ("Save Changes", not "OK").

### Plan C — Defect-Driven Case Evolution (`defect-cases/index.json`)
- **Active Plan C Registry**: [defect-cases/index.json](defect-cases/index.json) enforces verified interaction-layering, anti-flat-tiling, compact anti-overflow, 120Hz scroll prefetching, and shared table column geometry guards (`DC-001` Single-Plane Flat Tiling & `SWWallet` Flattening, `DC-002` Duplicate Same-Source Data Representation, `DC-003` Unbounded Horizontal `HStack/ForEach` Overflow & Multi-Card Row Cramming on Compact Width, `DC-004` `LazyStack` Leaf Conditional Subview Leak, `DC-005` Table Header-to-Cell Column Geometry Drift).
- **When a new defect is discovered**: Register a scoped case (`--id auto` assigns the next sequential `DC-00N`) so future tasks automatically check for it:
  ```bash
  python3 skills/my-designer/scripts/verify_design_cases.py register-case \
    --id auto \
    --title "macOS NSPopover Frame-Before-Padding Clipping" \
    --platforms desktop \
    --dimensions layout-navigation \
    --containers NSPopover \
    --symptom "Applying .frame(width:).padding() clips trailing content" \
    --fix-rule "Always apply .padding(P).frame(width: W)" \
    --forbidden-regex '\.frame\(\s*width\s*:[^)]+\)\s*\.padding\('
  ```
