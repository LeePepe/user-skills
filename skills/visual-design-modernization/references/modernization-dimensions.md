# Modernization dimensions

Use this reference after the platform and verbal spec are locked.

## Six dimensions
- Visual Mode + non-colliding Style Archetype from `appearance-ledger.md`.
- Interaction hierarchy: one focal point, one primary CTA, and populated/loading/empty/error states.
- Micro-precision: 4/8pt spacing, size-aware concentric radii, token-backed controls, baseline-aligned rows, no numeric ellipsis.
- Engineering reuse: DesignTokens/StyleTokens/Strata or Basalt; update the contract and tests together when needed.
- Anti-collision: glass only on functional chrome, solid content layer, one bottom inset stack, zero-height inactive slots.
- Macro tiers: compact touch (44pt targets), wide touch/adaptive grid, desktop pointer (28pt minimum); use container/size-class signals, never fixed device idioms.

## Review checklist
Check max content width, responsive KPI/data-viz grouping, three type levels, semantic status pills, restrained accents, and hue-aware neutral surfaces. Render Light/Dark plus dense and empty fixtures; include RTL where applicable. Reject custom capsule chrome nested in system glass, sub-AA filled buttons, oversized sticky headers, and negative-padding occlusion.

For web, verify CSS container behavior, keyboard focus, reduced motion, and pointer/touch parity. For desktop/mobile, verify SwiftUI/Liquid Glass layering, Dynamic Type, and native navigation.
