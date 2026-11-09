---
name: my-designer
description: "Progressive-disclosure design lead for desktop, mobile, and web."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# my-designer — three-platform design lead

I'm using the my-designer skill (as design lead). This is a handoff-only capability. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code**, never a throwaway demo.

## Step 1 · Lock coordinates
Choose one equal first-class platform: `desktop`, `mobile`, or `web`; choose 1–4 dimensions: `color-palette`, `visual-style`, `component`, `chart-dataviz`, `glass-material`, `motion-interaction`, `layout-navigation`, `typography-copy`.

Shared L1 fundamentals apply to every platform: [interaction-layering.md](references/interaction-layering.md), [breathing-and-clarity.md](references/breathing-and-clarity.md), and [appearance-ledger.md](references/appearance-ledger.md).

## Step 2 · Route on demand
```bash
python3 skills/my-designer/scripts/verify_design_cases.py route --platform web --dimensions visual-style,motion-interaction
python3 skills/design-studio/scripts/design_catalog.py vault-search --platform web --dimension visual-style --query "intent" --top 3
```

Platform slices: `web` uses Basalt, CSS, Motion, Anime.js, Three.js, taste-skill, impeccable Web, and ui-ux-pro-max Web. `desktop|mobile` uses SwiftUI, Liquid Glass, AppTheme, and ShipSwift. Never mix platform firewalls.

## Step 3 · Author the handoff
Anchor ergonomics, state coverage, accessibility, responsive/container behavior, reduced motion, and the selected style recipe. Include an Obsolete Test Manifest when changing an existing screen. Use Plan A principles and Plan C cases from [defect-cases/index.json](references/defect-cases/index.json); register new cases with `register-case --id auto`.

## Step 4 · Deliver & Post-Implementation Visual Traceback (`实现后截图回溯`)
`my-designer` operates as a **Dual-Touchpoint Closed Loop**: **Touchpoint 1** is Phase 0 ideation (`design.md`/`spec.md`), and **Touchpoint 2** is **Post-Implementation Visual Traceback (`实现后截图回溯`)** during Phase 1 independent review:
- **Writer low-cost screenshot capture**: After passing `shared-ci/scripts/context/design_contract.py` (`--audit-code`, `--audit-l10n`, `--check-edge-clipping`, `--audit-geometry`), the `Writer` captures real rendered screenshots by platform (Apple: `XCTest` snapshot / `xcrun simctl io booted screenshot` / headless `ImageRenderer` 4 states × 2 appearances PNG; Web: Playwright headless screenshot) and records screenshot paths in the R10 receipt. **Writer is responsible ONLY for faithfully implementing declared constraints, never for guessing undeclared constraints.**
- **Independent Reviewer Visual Traceback (`[IMPL_BUG]` vs `[SPEC_GAP]`)**: The cross-vendor independent `Reviewer` loads `code-review` + `my-designer` to inspect both code diff and rendered screenshots from the R10 receipt, classifying every visual/interaction finding into:
  - **`[IMPL_BUG]` (Implementation Deviation)**: Writer violated an explicit clause in `design.md` or `design_contract.py` → route directly back to Writer to fix against existing `design.md`.
  - **`[SPEC_GAP]` (Phase 0 Spec Omission / Unforeseen Reality)**: Writer followed `design.md`, but rendered pixels expose crowding, CJK wrapping, column drift, or hierarchy conflict missed in Phase 0 → **NEVER blame Writer or ask Writer to guess undeclared rules**; route back to `Steering` to **amend `design.md` first (and re-stamp `spec_sha`)** with concrete parameters before dispatching Writer.
- **Anti-Explosion Promotion Gate**: Every `[SPEC_GAP]` updates the current Goal's `design.md` (100%). Promote to `my-designer` (`verify_design_cases.py register-case --id auto`) or `shared-ci` (`design_contract.py`) via `skill_contract.py sink-rule` **ONLY when it is cross-project generic, free of private symbols, and recurring on the same container**.

```bash
python3 skills/my-designer/scripts/verify_design_cases.py flow-map
python3 skills/my-designer/scripts/verify_design_cases.py register-case --id auto --title "..." --platforms desktop,mobile,web --symptom "..." --fix-rule "..."
```
