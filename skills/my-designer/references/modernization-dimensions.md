# Modernization Dimensions & Micro-Precision (`L1` Shared Reference)

Use this reference after locking platform (`desktop|mobile|web`) and surface coordinates in `my-designer` (Entry B), covering both brand-new UI and existing UI modernization (**No Code Demo** — work directly in product or `shared-design-system` code; never create a `Prototype/` directory).

## 1. Six Dimensions of UI Modernization

| Dimension | Contract & Guardrails |
|---|---|
| **1. Visual Mode & Style Archetype** | Select one Visual Mode and a non-colliding Style Archetype (`VS-001..VS-008`) + Brand Palette (`CL-001..CL-008`) from [appearance-ledger.md](appearance-ledger.md). Never mix warm mineral paper with neon dark glass on the same canvas. |
| **2. Interaction Hierarchy & Motion (`M1–M4`)** | Enforce one primary focal point and one primary CTA per surface, deterministic state machine (`idle -> intent -> commit/cancel -> settle`), and 100% coverage of populated/loading/empty/error states with reduced-motion fallbacks. |
| **3. Micro-Precision & Geometry** | Strict `4/8pt` spacing rhythm, size-aware concentric radii (`.rect(corner: .containerConcentric)` or `innerRadius = max(4, outerRadius - padding)`), `.alignmentGuide` pin-flip for anchored badges, baseline-aligned rows, and `.monospacedDigit()` / `tabular-nums` with zero numeric truncation (`…`). |
| **4. Engineering Token Reuse** | Reuse `AppTheme` / `DesignTokens` / `StyleTokens` on Apple platforms and `Basalt` / CSS variables on Web; update contract and tests together when changing tokens. |
| **5. Anti-Collision & Single Bottom Inset** | Restrict Liquid Glass to the floating functional layer (`Z=10..20`), keep the content layer (`Z=0`) solid/standard material, consolidate bottom chrome into a single `.safeAreaInset(edge: .bottom)` stack (`<=2` layers), and collapse inactive slots to `0pt`. |
| **6. OS 27 Macro-Tiers (`horizontalSizeClass`)** | Drive layout from container width and `@Environment(\.horizontalSizeClass)` (or CSS `@container`), never fixed device idioms (`UIScreen.main` or `userInterfaceIdiom == .phone`): compact touch (`>=44x44pt` targets), wide touch / iPhone Duo adaptive split grid, and desktop pointer (`>=28x28pt` minimum). |

## 2. Objective Modernization & Visual Traceback Checklist

- **Typography & Copy**: Keep at most 3 type scale levels per card/section; use semantic status pills (never color alone); ensure localized strings (`en`, `zh-Hans`) never clip or wrap single words awkwardly.
- **Surface & Contrast**: Verify Light and Dark appearances plus dense and empty fixtures (include RTL where applicable). Reject custom capsule chrome nested inside system glass, sub-AA (`<4.5:1`) filled buttons, oversized sticky headers (`>64pt`), and negative-padding occlusion.
- **Platform Verification**:
  - **Web**: Verify CSS `@container` behavior, keyboard focus rings, `prefers-reduced-motion`, and pointer/touch parity via Playwright headless screenshots.
  - **Desktop / Mobile**: Verify SwiftUI / Liquid Glass layering, Dynamic Type scaling, and native navigation via `XCTest` / `simctl` / `ImageRenderer` 4 states × 2 appearances PNGs.
