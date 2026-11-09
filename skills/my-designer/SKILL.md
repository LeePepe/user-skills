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

## Step 4 · Deliver
Keep design-time routing and case registration here. Implementation-time static, localization, screenshot, and geometry checks belong to `shared-ci/scripts/context/design_contract.py`, not this skill.

```bash
python3 skills/my-designer/scripts/verify_design_cases.py flow-map
python3 skills/my-designer/scripts/verify_design_cases.py register-case --id auto --title "..." --platforms desktop,mobile,web --symptom "..." --fix-rule "..."
```
