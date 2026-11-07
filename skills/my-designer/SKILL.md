---
name: my-designer
description: 'Apple-First progressive-disclosure design lead (`L0 -> L1 -> L2`) for macOS (`desktop`) and iOS/iPadOS (`mobile`) apps. Uses pure Apple Native components (SwiftUI, Swift Charts, SF Symbols, Liquid Glass), routes on-demand HIG & vault inspirations, enforces Plan A (Apple HIG 5 Lenses + code audit) by default, and evolves Plan C defect cases on demand. Downstream Writers implement directly in product code; no throwaway demos or Prototype code.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# my-designer — Progressive Disclosure Apple Design Lead (`L0`)

This `handoff-only` skill owns the design specification and review routing, not production code implementation. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code** without throwaway demos or `Prototype/` directories.

**Announce at start:** “I'm using the my-designer skill (as design lead).”

---

## 4-Step Progressive Disclosure Workflow (`L0 -> L1 -> L2`)

Never load all HIG documents or the entire inspiration vault upfront. Disclose context in three strict layers:

### Step 1 · Lock Platform & Dimensions (`L0`)
1. **Platform Topology:** Select `desktop` (macOS `NSWindow`, `NSPopover`, `MenuBarExtra`, `NavigationSplitView`) or `mobile` (iOS/iPadOS/iPhone Duo `NavigationStack`, `TabView`, `Sheet`, `Widget`, `LiveActivity`). `web` remains frozen in backlog unless `--platform web` is explicitly requested.
2. **Design Dimensions:** Pick the 1–4 active dimensions for the surface:
   - `color-palette` (`CL`) · `visual-style` (`VS`) · `component` (`CP`) · `chart-dataviz` (`DV`)
   - `glass-material` (`GM`) · `motion-interaction` (`MI`) · `layout-navigation` (`LN`) · `typography-copy` (`TC`)

### Step 2 · On-Demand Routing & Platform Firewall (`L1` -> [routing-index.md](references/routing-index.md))
Run the L1 router and vault search. `verify_design_cases.py route` enforces a strict **Native vs. Web Platform Firewall** over the 4 external git-pullable repos in `~/Development/Personal/collected-tools/repos/` (`apple-design-skill`, `impeccable`, `taste-skill`, `ui-ux-pro-max-skill`):
- **When `--platform desktop` or `mobile` (Apple Native):**
  - **Skip raw foundation HIG** (`distilled_in_plan_a` is already in Plan A 5 Lenses);
  - **Allowed external refs:** `apple-design-skill/references/hig/<on_demand_external_refs>`, `impeccable` **native-only subset** (`reference/{ios.md,audit.native.md,adapt.native.md,craft-floor.md,operate.md}`), and `ui-ux-pro-max` `--stack swiftui` only;
  - **Strictly blocked (Zero Web Pollution):** never load `taste-skill` (100% Web HTML/CSS/Shader/Chrome hook), `ui-ux-pro-max` Web `--design-system`/Tailwind/Shadcn/GSAP, or `impeccable` Web `detect`/`live`/`audit.md`.
- **When `--platform web` (Web / Landing Page):**
  - Routes to `taste-skill/skills/taste-skill/SKILL.md`, `impeccable` Web (`SKILL.md` + `detect`), and `ui-ux-pro-max` (`--design-system` / `ui-styling`).

```bash
# 1. Route active platform + dimensions -> Plan A lenses, Plan C defect cases & platform-isolated external refs
python3 skills/my-designer/scripts/verify_design_cases.py route \
  --platform desktop --dimensions glass-material,chart-dataviz

# 2. Retrieve matching inspirations from desktop-macos.json or mobile-ios.json
python3 skills/design-studio/scripts/design_catalog.py vault-search \
  --platform desktop --dimension chart-dataviz --query "<intent>" --top 5
```

### Step 3 · Author Apple-First Native Handoff (`design.md` / `spec.md`)
- **Pure Apple Native Priority (No Mandatory Design System Wrapper):**
  - Default to native SwiftUI controls (`NavigationSplitView`, `TabView`, `List`, `Table`, `Form`, `.toolbar`, `.sheet`, `.searchable`, `ContentUnavailableView`, `LabeledContent`), **Swift Charts** (`Chart`, `LineMark`, `BarMark`, `AreaMark`, `SectorMark`, `.chartXSelection`), **SF Symbols**, system semantic colors, and native **Liquid Glass** (`.glassEffect(.regular / .clear)`, `GlassEffectContainer`, `.buttonStyle(.glassProminent)`).
  - Do not force custom design-system wrappers on standalone Apple apps; add lightweight project-local modifiers only when native components cannot express a recurring domain pattern.
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
- **Plan C ([defect-cases/index.json](references/defect-cases/index.json)):** Starts empty (`active_cases: []`) to keep daily design lightweight. When a real visual or layout defect is discovered, register an atomic case via `verify_design_cases.py register-case` so future tasks on the same platform/container automatically disclose and check it.
