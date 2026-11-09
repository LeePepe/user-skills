---
name: visual-design-modernization
description: "Modernize real product UI across desktop, mobile, and web with a verbal spec and rendered review."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# Visual Design Modernization

I'm using the visual-design-modernization skill. Use for dated, misaligned, generic, or Glass-conflicting UI. Work directly in the product or design system; never create a `Prototype/` directory (No Code Demo).

## Fast loop
1. Diagnose screenshot/code into precise terms.
2. Lock platform (`desktop|mobile|web`), Visual Mode, Style Archetype, and six dimensions.
3. Implement token-backed changes in the real product.
4. Render Light/Dark and applicable macro tiers.
5. Review pixels objectively, fix P0s, and repeat.

Shared fundamentals apply to all platforms. Web uses Basalt/CSS/Motion/Anime.js/Three.js; desktop/mobile use SwiftUI/Liquid Glass/AppTheme/ShipSwift. Use container-driven responsive layout, semantic colors, accessible hit targets, reduced motion, and one source of truth.

Detailed dimensions, anti-collision rules, macro tiers, and portable checklists are in [references/modernization-dimensions.md](references/modernization-dimensions.md). Implementation-time audits (`--audit-code`, `--audit-l10n`, `--check-edge-clipping`, `--audit-geometry`) run through `shared-ci/scripts/context/design_contract.py`.

## Deliverables
Produce a concise verbal spec, real screenshots, objective review findings, and a focused production migration.
