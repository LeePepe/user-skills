# Design Flow Architecture

This entry is handoff-only: `design-studio` Entry A ingests; `my-designer` Entry B authors `design.md`/`spec.md`; the downstream Writer implements product code. The Reviewer performs Visual Traceback and labels `[IMPL_BUG]` versus `[SPEC_GAP]`.

## Design-vs-Worker Skill Boundary (Designer-vs-Worker)

- **Designer Role (`my-designer`)**: Owns pure UX/design specifications, categorized atomic dual-variants (`Variant A/B`), and composite screen reuse topology (`v01..v04`) in `design.md`/`spec.md`.
- **Worker Implementation Boundary**: `my-designer` MUST NEVER contain compiler flags, build settings, Swift package/Xcode project manifests, or source-code implementation knowledge — all code/build execution belongs strictly to the downstream Worker skills (`xcode-cli`, `ios-concurrency-swiftui`, `ios-architecture-testing`, `team-lead`).

| Tier / Eval | Contract |
|---|---|
| Tier 1 Invariants | alignment, grid rhythm, 1 focal P0, zero truncation/overlap, macOS `NavigationSplitView`/TopBar vs iOS `NavigationStack`/BottomDock |
| Tier 2 Persona | context-scoped visual budget (`compact` cockpit vs `default` vs `breathing` companion); never overwrite global defaults from one scene |
| Tier 3 `2+1+1` | `v01..v02` deepen persona, `v03` cross-pollinates `design-studio`, `v04` wildcard topology |
| Atomic Dual-Variant + Composite Reuse | group UI components by category in `design.md`/`spec.md`; each atomic component specifies `>=2` distinct interactive variants (`Variant A/B`), and composite screen variants (`v01..v04`) are composed directly from those atomic variants in the design spec |
| L1 / L2 / L3 Eval | L1 deterministic `verify_design_cases.py` · L2 `jev-gate --stage gallery` differentiation · L3 `preview-selection.json` -> `taste_evolution_loop.py` |

| Phase | Contract |
|---|---|
| Phase 0 | platform, dimensions, Tier 2 persona + VS/CL route, categorized atomic `Variant A/B` floor + `2+1+1` (`v01..v04`) composite screen reuse, anti-patterns, `spec_sha` |
| Phase 1 | real screenshots, geometry, clipping, CJK and state evidence |
| close | receipt, obsolete-test manifest, cleanup and reroute |

## Categorized Atomic Dual-Variant Floor & Composite Screen Reuse

- **Categorized Atomic Dual-Variant Floor (`Variant A/B`)**: Group UI components by category in `design.md`/`spec.md`; each atomic component specifies `>=2` distinct interactive variants (`Variant A/B`).
- **Composite Screen Reuse (`v01..v04`)**: Compose full-screen variants (`v01..v04`) directly from those categorized atomic variants in the design spec rather than redefining ad-hoc screen-only components.

Native Apple work uses `magpie/group/designer`, `magpie/group/coding`, `PreviewHostKit`, Xcode `#Preview`, and 120Hz. Web evidence uses Playwright; Apple evidence uses ImageRenderer. Never ship a No Code Demo.
