# Design Flow Architecture

This entry is handoff-only: `design-studio` Entry A ingests; `my-designer` Entry B authors `design.md`/`spec.md`; the downstream Writer implements product code. The Reviewer performs Visual Traceback and labels `[IMPL_BUG]` versus `[SPEC_GAP]`.

| Tier / Eval | Contract |
|---|---|
| Tier 1 Invariants | alignment, grid rhythm, 1 focal P0, zero truncation/overlap, macOS `NavigationSplitView`/TopBar vs iOS `NavigationStack`/BottomDock |
| Tier 2 Persona | context-scoped visual budget (`compact` cockpit vs `default` vs `breathing` companion); never overwrite global defaults from one scene |
| Tier 3 `2+1+1` | `v01..v02` deepen persona, `v03` cross-pollinates `design-studio`, `v04` wildcard topology |
| L1 / L2 / L3 Eval | L1 deterministic `verify_design_cases.py` · L2 `jev-gate --stage gallery` differentiation · L3 `preview-selection.json` -> `taste_evolution_loop.py` |

| Phase | Contract |
|---|---|
| Phase 0 | platform, dimensions, Tier 2 persona + VS/CL route, `2+1+1` candidates, anti-patterns, `spec_sha` |
| Phase 1 | real screenshots, geometry, clipping, CJK and state evidence |
| close | receipt, obsolete-test manifest, cleanup and reroute |

Native Apple work uses `magpie/group/designer`, `magpie/group/coding`, `PreviewHostKit`, Xcode `#Preview`, and 120Hz. Web evidence uses Playwright; Apple evidence uses ImageRenderer. Never ship a No Code Demo.
