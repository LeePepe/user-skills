---
name: my-designer
description: 'Apple-First progressive-disclosure design lead (`L0 -> L1 -> L2`) for macOS (`desktop`) and iOS/iPadOS (`mobile`) apps. Uses pure Apple Native components (SwiftUI, Swift Charts, SF Symbols, Liquid Glass), routes on-demand HIG & vault inspirations, enforces Plan A (Apple HIG 5 Lenses + code audit) by default, and evolves Plan C defect cases on demand. Downstream Writers implement directly in product code; no throwaway demos or Prototype code.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# my-designer — Progressive Disclosure Apple Design Lead (`L0`)

This `handoff-only` skill owns the design specification and review routing, not production code implementation. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code** without throwaway demos or `Prototype/` directories. (`my-designer` is a pure design capability node: it never edits product source files itself and remains agnostic of underlying agent orchestration.)

**Announce at start:** “I'm using the my-designer skill (as design lead).”

> **Dual-Entry Architecture & Anti-Explosion Contract ([design-flow-architecture.md](references/design-flow-architecture.md)):**
> All knowledge ingestion (`Entry A: A0..A4`) and design delivery (`Entry B: B0..B4`) share the 4-Tier Asset Pool (`Tier 0..3`) governed by 4 Anti-Explosion Valves (Zero-Token Code Sinking, 3D Coordinate Slicing, `--top 3` & `<=130`-line `L1` Caps, One-In/One-Out Compaction). Run `python3 skills/my-designer/scripts/verify_design_cases.py flow-map` to inspect live asset counts and budget health.

---

## 4-Step Progressive Disclosure Workflow (`L0 -> L1 -> L2`)

Never load all HIG documents or the entire inspiration vault upfront. Disclose context in three strict layers:

### Step 1 · Lock Platform & Dimensions (`L0`)
1. **Platform Topology:** Select `desktop` (macOS `NSWindow`, `NSPopover`, `MenuBarExtra`, `NavigationSplitView`) or `mobile` (iOS/iPadOS/iPhone Duo `NavigationStack`, `TabView`, `Sheet`, `Widget`, `LiveActivity`). `web` remains frozen in backlog unless `--platform web` is explicitly requested.
2. **Design Dimensions:** Pick the 1–4 active dimensions for the surface:
   - `color-palette` (`CL`) · `visual-style` (`VS`) · `component` (`CP`) · `chart-dataviz` (`DV`)
   - `glass-material` (`GM`) · `motion-interaction` (`MI`) · `layout-navigation` (`LN`) · `typography-copy` (`TC`)

### Step 2 · On-Demand Routing & Platform Firewall (`L1` -> [routing-index.md](references/routing-index.md), [appearance-ledger.md](references/appearance-ledger.md), [interaction-layering.md](references/interaction-layering.md))
Run the L1 router and vault search. `verify_design_cases.py route` enforces a strict **Native vs. Web Platform Firewall** over the 14 external git-pullable repos in `~/Development/Personal/collected-tools/repos/` and discloses [interaction-layering.md](references/interaction-layering.md) on demand when `LN / CP / DV / MI` dimensions are active:
- **When `--platform desktop` or `mobile` (Apple Native + Distinct Style Recipes):**
  - **Skip raw foundation HIG** (`distilled_in_plan_a` is already in Plan A 5 Lenses);
  - **Allowed native & style sources:** `apple-design-skill`, `twostraws-swiftui-skill`, `avdlee-swiftui-skill`, `dimillian-skills`, `bama` (macOS 4pt grid & pixel measurement), `awesome-ios-design-md` (200 real iOS app `DESIGN-swiftui.md` style recipes), `shipswift` (80+ self-contained SwiftUI components, charts, and 28 Metal shaders), `genjutsu` (`swiftui-graphics` Metal shaders & desktop/mobile principles), `claude-code-apple-skills` (`game-feel` & `AppTextStyle`), `emilkowalski-skills` (fluid spring physics & frequency gates), `expo-skills` (`native-slop.md` 20 Tells), `impeccable` native-only subset, and `ui-ux-pro-max --stack swiftui`;
  - **Strictly blocked (Zero Web Pollution):** never load `taste-skill` (100% Web HTML/CSS/Shader/Chrome hook), `ui-ux-pro-max` Web `--design-system`/Tailwind/Shadcn/GSAP, or `impeccable` Web `detect`/`live`/`audit.md`.
- **When `--platform web` (Web / Landing Page):**
  - Routes to `taste-skill/skills/taste-skill/SKILL.md`, `impeccable` Web (`SKILL.md` + `detect`), and `ui-ux-pro-max` (`--design-system` / `ui-styling`).

```bash
# 1. Route active platform + dimensions -> Plan A lenses, Plan C defect cases & platform-isolated external refs
python3 skills/my-designer/scripts/verify_design_cases.py route \
  --platform desktop --dimensions glass-material,chart-dataviz

# 2. Retrieve matching style archetypes, palettes, and ShipSwift components from desktop-macos.json or mobile-ios.json
python3 skills/design-studio/scripts/design_catalog.py vault-search \
  --platform desktop --dimension visual-style --query "<intent>" --top 3
```

### Step 3 · Author Apple-First + Bespoke Style Handoff (`design.md` / `spec.md`)
- **Apple Principle + Liquid Glass Foundation, Distinct Per-App Personality ([appearance-ledger.md](references/appearance-ledger.md)):**
  - Anchor ergonomics and navigation on native SwiftUI (`NavigationSplitView`, `TabView`, `List`, `Table`, `.toolbar`, `.sheet`, `ConcentricRectangle`, `ContentUnavailableView`), **Swift Charts**, **SF Symbols**, and native **Liquid Glass** (`.glassEffect(.regular / .clear)`, `GlassEffectContainer`, `.buttonStyle(.glassProminent)`).
  - **Distinct Per-App Style Recipe (`AppTheme.swift` + `ShipSwift` Source Components):** Never force a monolithic cross-repo UI wrapper package onto every app. Instead, pick a non-colliding **Style Archetype (`VS-001..008`, including Cross-Domain Archetype Remixes) and Brand Palette (`CL-001..008`)** from [appearance-ledger.md](references/appearance-ledger.md) (or `awesome-ios-design-md`), generate a single-file `<80`-line `AppTheme.swift` (`Space`, `Radius`, semantic colors, `AppTextStyle`, `AppPressableButtonStyle`) inside the app, and copy self-contained bespoke components/Metal shaders from `ShipSwift/SWPackage/` (`SWMetal`, `SWAnimation`, `SWChart`, `SWComponent`) restyled with `AppTheme`.
  - **Orthogonal X-Y-Z Interactive Layering (`DC-001` & `DC-002` -> [interaction-layering.md](references/interaction-layering.md)):**
    Disclose [interaction-layering.md](references/interaction-layering.md) on demand for `LN / CP / DV / MI` surfaces to enforce: (1) **Axis X (`Role × P0..P3` Matrix + Pace Layering)** — filter `Display / Action / Display+Action` into `P0` focal, `P1` inline hint/companion bar, `P2` folded context, `P3` `⋯` menu/Sheet; (2) **Axis Y (`Nielsen <=3 Chunks` + `Shneiderman Overview -> Zoom/Filter -> Details` + `Tufte Zero Redundancy`)** — fold 3rd+ modules via Accordion single-active, Segmented Lens, or `SWWallet` 3D `ZStack` (`~220pt`), and never duplicate Donut legends with `ProgressView` bars; (3) **Axis Z (`Apple Two-Layer Glass`)**.
- **Spec-First Test Alignment:** When modifying an existing screen, include an **Obsolete Test Manifest** and **Deprecated Test Clauses to Remove/Rewrite** section so legacy UI assertions do not block approved design changes.

### Step 4 · Baseline Gate: Plan A Default + Plan C Defect-Case Evolution (`L2`)
- **Plan A (Always Active):** Audit against Apple's 5 HIG Lenses (Accessibility, Platform Conventions, Liquid Glass Two-Layer Craft, Interaction & States, Typography & Copy), run the static native code audit, and run the portable Localization (`xcstrings`) audit:
  ```bash
  python3 skills/my-designer/scripts/verify_design_cases.py audit-code \
    --platform desktop --path <SwiftUI-File-Or-Dir>

  # Portable Cross-Project Localization Gate (locale completeness, format parity, source key coverage & Info.plist CFBundleLocalizations)
  python3 skills/my-designer/scripts/verify_design_cases.py audit-l10n \
    --path <App-Or-Package-Dir> --locales en,zh-Hans
  ```
- **Plan C ([defect-cases/index.json](references/defect-cases/index.json)):** Enforces verified spatial and interaction-layering guards (`DC-001` Single-Plane Flat Tiling & Flattened `SWWallet`, `DC-002` Duplicate Same-Source Data Representation). When a new defect is discovered, register an atomic case via `verify_design_cases.py register-case` so future tasks on the same platform/container automatically disclose and check it.
