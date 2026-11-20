# Breathing and Clarity

Treat whitespace as a task affordance: reserve a clear focal region, protect labels from controls, and keep one primary action per viewport. Validate compact and expanded states, CJK wrapping, Dynamic Type, and reduced motion.

## Context-Scoped Density Triage (Tier 2 Persona Guard)

| Persona (`--persona`) | Max Bottom Dead Space | Breathing Floor | Scope & Intent Weighting |
|---|---|---|---|
| `compact` (cockpit/dense) | `<= 25%` | `0%` | High-density telemetry/ledger cockpit; tight chrome, dense tables/charts. **Never overwrite global defaults.** |
| `default` (balanced) | `<= 40%` | `0%` | Global baseline task workspace; 1 focal P0, zero dead viewport tail. |
| `breathing` (companion/calm) | `<= 55%` | `>= 12%` (soft advisory) | Calm companion/editorial surface; generous margin protecting P0 clarity without overcrowding. |

| Check | Evidence |
|---|---|
| hierarchy | one focal task and one P0 |
| density | context-scoped `--persona` budget; no accidental dead rail or crowded toolbar |
| copy | concrete verbs; zero instructional micro-copy (`DC-014` / `UI_INSTRUCTIONAL_CLUTTER`) |
| recovery | empty/error states retain a return path |

Capture the persona scope in `design.md`, then let the Writer implement it in product code.
