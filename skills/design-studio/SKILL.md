---
name: design-studio
description: "Design and implement equal desktop, mobile, and web interactions, motion, and rendered assets."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# design-studio

Use after a `my-designer` handoff. Select one equal platform (`desktop`, `mobile`, `web`), one surface mode (`Operate`, `Read`, `Persuade`, `Experience`, or `Video/Offline`), and a Style Archetype + Brand Palette. Token authorities are AppTheme/SwiftUI on Apple platforms and Basalt/CSS on web.

## Shared contract
Use the shared state machine `idle -> intent -> commit/cancel -> settle`, preserve one source of truth, cover populated/loading/empty/error states, and provide reduced-motion fallbacks. Shared L1 fundamentals live in `../my-designer/references/{interaction-layering,breathing-and-clarity,appearance-ledger}.md`.

## Platform routing
- **web:** Basalt, CSS, Motion, Anime.js, Three.js, taste-skill, impeccable Web, ui-ux-pro-max Web.
- **desktop/mobile:** SwiftUI, Liquid Glass, AppTheme, ShipSwift, native spring/gesture primitives.
- **video:** disclose [references/remotion-video.md](references/remotion-video.md) only when `--surface-mode video`; do not load it for product UI.

```bash
python3 skills/design-studio/scripts/design_catalog.py vault-search --platform web --dimension motion-interaction --query "intent" --top 3
python3 skills/design-studio/scripts/design_catalog.py recommend --platform web --surface-mode Experience
```

## Delivery
Implement directly in the real product/design system; keep dev controls out of production, preserve token authority, and validate rendered output. Implementation-time code, localization, edge, and geometry gates are owned by `shared-ci/scripts/context/design_contract.py`.
