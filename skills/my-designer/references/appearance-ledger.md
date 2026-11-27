# Appearance Ledger

Use one platform-native appearance route and one context-scoped **Tier 2 Product Persona** (`compact`, `default`, or `breathing`) per frozen design. The Cross-Domain Archetype Remix Matrix is a compact index, not a second catalog.

## OS-Native Dynamic Appearance Rule (Zero Custom Theme Engine)

- **Automatic System Tracking**: Light and dark appearance MUST track native OS appearance automatically across every window, sheet, popover, and viewport through dynamic semantic tokens (`VS-*` / `CL-*`).
- **Forbidden Custom Theme Plumbing**: Custom in-app theme engines, app-private light/dark theme switchers or toggles, appearance-override state machines, and manual per-root or per-sheet theme injection modifiers are strictly forbidden in `design.md`/`spec.md` and product surfaces.
- **Verification**: Validate handoff specs with `verify_design_cases.py audit-spec --strict-spec-gaps` and audit view surfaces with `verify_design_cases.py audit-code` (`DC-020` / `UI_CUSTOM_THEME_PLUMBING`). Low-level token unit fixtures may waive a single assertion line only when explicitly marked in test scope.

| ID | Archetype | Default Persona Fit | Use |
|---|---|---|---|
| VS-005 | quiet glass | `breathing` / `default` | restrained material and legible hierarchy |
| VS-006 | editorial dark | `breathing` | high-contrast narrative surface |
| VS-007 | warm utility | `default` | task-first friendly density |
| VS-008 | luminous data | `compact` | measured high-density chart/cockpit emphasis |
| CL-006 | soft neutral | `breathing` / `default` | semantic background palette |
| CL-007 | dusk accent | `default` | focused action contrast |
| CL-008 | signal spectrum | `compact` | status-safe data colors |

Record appearance, Tier 2 persona scope, contrast, OS-native light/dark behavior, Dynamic Type, Reduce Motion, and the selected ID in the receipt. When a Tier 3 wildcard (`v04`) wins Owner selection in `preview-selection.json`, absorb its weighting into that product's persona—never mutate global defaults across unrelated apps, and never replace semantic tokens with arbitrary hex values or custom theme engines.
