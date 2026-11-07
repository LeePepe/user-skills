# Progressive Disclosure Routing Index (`L0 -> L1 -> L2`)

This file is the **Level 1 (L1) Router** for Apple-First UI/UX design. Never load the entire HIG directory or all vault files at once. Follow the 3-step progressive disclosure flow below to load only the **8–12 leaf files** needed for the current screen.

---

## Step 1: Lock Platform Topology (`desktop` vs `mobile` vs `web`)

| Platform | Status | Host Containers | Ergonomics & Navigation Floor (Plan A) | Distilled Base (`distilled_in_plan_a` — Skip Raw Read by Default) |
|---|---|---|---|---|
| **`desktop`** (macOS Native) | **Active Primary** | `NSWindow`, `NSPopover`, `MenuBarExtra`, `NavigationSplitView`, `Settings`, `Inspector` | Default target `28×28pt` (min `20×20pt`); default font `13pt` (min `10pt`); Menu Bar command parity, pointer hover, keyboard focus ring, `Esc`/`⌘` shortcuts; **never place a floating bottom action bar** | `designing-for-macos.md`, `accessibility.md`, `layout.md`, `color.md`, `typography.md` (already distilled into Plan A 5 Lenses) |
| **`mobile`** (iOS / iPadOS / iPhone Duo) | **Active Primary** | `NavigationStack`, `TabView`, `Sheet`, `Widget`, `LiveActivity`, `iPhoneDuoSplit` | Default target `44×44pt` (min `28×28pt`); default font `17pt` (min `11pt`); `TabView` for navigation only, `.toolbar` for actions, `Sheet` with grabber/detents, Size Classes instead of hardcoded screen widths | `designing-for-ios.md`, `designing-for-ipados.md`, `accessibility.md`, `layout.md`, `color.md`, `typography.md` (already distilled into Plan A 5 Lenses) |
| **`web`** (Web / Video) | **Backlog (Frozen)** | `WebViewport`, `RemotionComposition` | Frozen in `../design-studio/references/vault/web-backlog.json`; do not load during Apple design tasks unless `--platform web` is explicitly requested | N/A |

---

## Step 2: Disclose by Design Dimension (`8 Dimensions -> Native APIs + Leaf HIG`)

Query the inspiration vault for only the active platform and dimension:

```bash
python3 skills/design-studio/scripts/design_catalog.py vault-search \
  --platform desktop --dimension chart-dataviz --query "<intent>" --top 5
```

| Dimension ID | Prefix | Apple Native Primitives (No Custom Design System Wrapper Tax) | On-Demand HIG Leaf Files (`~/Development/Personal/collected-tools/repos/apple-design-skill/references/hig/`) |
|---|---|---|---|
| `color-palette` | `CL` | `.primary`, `.secondary`, `Color(uiColor: .systemBackground)`, `Color(nsColor: .windowBackgroundColor)`, Asset Catalog Accent, `.vibrant` | `color.md`, `dark-mode.md`, `branding.md`, `accessibility.md` |
| `visual-style` | `VS` | `.backgroundStyle`, `RoundedRectangle(cornerRadius:style: .continuous)`, `.symbolRenderingMode(.hierarchical)`, system elevation | `design-principles.md`, `branding.md`, `materials.md`, `dark-mode.md`, `icons.md`, `sf-symbols.md` |
| `component` | `CP` | `NavigationSplitView`, `TabView`, `List`, `Table`, `Form`, `.toolbar`, `.sheet`, `.popover`, `.searchable`, `ContentUnavailableView`, `LabeledContent` | `buttons.md`, `menus.md`, `the-menu-bar.md`, `toolbars.md`, `tab-bars.md`, `sidebars.md`, `split-views.md`, `sheets.md`, `popovers.md`, `lists-and-tables.md`, `text-fields.md`, `pickers.md`, `toggles.md`, `settings.md` |
| `chart-dataviz` | `DV` | Swift Charts (`import Charts`, `Chart`, `LineMark`, `AreaMark`, `BarMark`, `SectorMark`, `RuleMark`, `.chartXSelection`, `.chartAngleSelection`) | `charts.md`, `charting-data.md`, `gauges.md`, `progress-indicators.md` |
| `glass-material` | `GM` | `.glassEffect(.regular / .clear)`, `GlassEffectContainer`, `.buttonStyle(.glass)`, `.buttonStyle(.glassProminent)`, `NSGlassEffectView`, `UIGlassEffect`, `.ultraThinMaterial` | `liquid-glass.md`, `materials.md`, `color.md`, `toolbars.md`, `tab-bars.md`, `sidebars.md` |
| `motion-interaction` | `MI` | `Spring(response:dampingRatio:)`, `.matchedGeometryEffect`, `KeyframeAnimator`, `PhaseAnimator`, `.contentTransition(.numericText())`, `.symbolEffect`, `.sensoryFeedback` | `motion.md`, `playing-haptics.md`, `gestures.md`, `feedback.md`, `drag-and-drop.md`, `undo-and-redo.md` |
| `layout-navigation` | `LN` | `ViewThatFits`, `Grid`, `GridRow`, `.safeAreaInset`, `@Environment(\.horizontalSizeClass)`, `ArrangementView` | `layout.md`, `designing-for-ios.md`, `designing-for-ipados.md`, `designing-for-macos.md`, `designing-for-iphone-duo.md`, `windows.md`, `scroll-views.md` |
| `typography-copy` | `TC` | `.font(.system(...))`, `.monospacedDigit()`, `.DynamicTypeSize`, `ContentUnavailableView`, localized `.xcstrings` | `typography.md`, `writing.md`, `labels.md`, `loading.md`, `feedback.md`, `right-to-left.md` |

---

## Step 3: Baseline Gate (Plan A Default + Plan C On-Demand Defect Cases)

Run the progressive gate router and code auditor:

```bash
# 1. Disclose active Plan A lenses + any matching Plan C defect cases for this surface
python3 skills/my-designer/scripts/verify_design_cases.py route \
  --platform desktop --dimensions glass-material,chart-dataviz

# 2. Run Plan A Apple Native Code Audit (+ active Plan C checks) on Swift files
python3 skills/my-designer/scripts/verify_design_cases.py audit-code \
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
- **Default state**: `active_cases` in [defect-cases/index.json](defect-cases/index.json) is empty (`[]`), keeping daily design lightweight and pure Plan A.
- **When a real defect is discovered**: Register a scoped case so future tasks on the same platform/dimension automatically check for it:
  ```bash
  python3 skills/my-designer/scripts/verify_design_cases.py register-case \
    --id DC-001 \
    --title "macOS NSPopover Frame-Before-Padding Clipping" \
    --platforms desktop \
    --dimensions layout-navigation \
    --containers NSPopover \
    --symptom "Applying .frame(width:).padding() clips trailing content" \
    --fix-rule "Always apply .padding(P).frame(width: W)" \
    --forbidden-regex '\.frame\(\s*width\s*:[^)]+\)\s*\.padding\('
  ```
