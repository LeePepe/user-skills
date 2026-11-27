# Design Flow Architecture

This entry is handoff-only: `design-studio` Entry A ingests; `my-designer` Entry B authors `design.md`/`spec.md`; the downstream Writer implements product code. The Reviewer performs Visual Traceback and labels `[IMPL_BUG]` versus `[SPEC_GAP]`.

| Tier / Eval | Contract |
|---|---|
| Tier 1 Invariants | alignment, grid rhythm, 1 focal P0, zero truncation/overlap, macOS `NavigationSplitView`/TopBar vs iOS `NavigationStack`/BottomDock |
| Tier 2 Persona | context-scoped visual budget (`compact` cockpit vs `default` vs `breathing` companion); never overwrite global defaults from one scene |
| Tier 3 `2+1+1` | `v01..v02` deepen persona, `v03` cross-pollinates `design-studio`, `v04` wildcard topology |
| Atomic Dual-Variant + Composite Reuse | group UI components by category; each atomic component provides `>=2` distinct interactive variants (`Variant A/B`), and composite screens (`v01..v04` in `FourUpPreviewHostView`) directly reuse those atomic variants without duplicating view code |
| PreviewHost `ENABLE_DEBUG_DYLIB=YES` (`DC-017`) | when a SwiftPM package defines an executable `*PreviewHost` alongside `#Preview`, generate a native `.xcodeproj` (`project.yml` + `xcodegen`) with `ENABLE_DEBUG_DYLIB = YES` on both the executable host and library target (plus shared schemes) and open `.xcodeproj` in Xcode so `#Preview` never fails with `ENABLE_DEBUG_DYLIB` |
| L1 / L2 / L3 Eval | L1 deterministic `verify_design_cases.py` · L2 `jev-gate --stage gallery` differentiation · L3 `preview-selection.json` -> `taste_evolution_loop.py` |

| Phase | Contract |
|---|---|
| Phase 0 | platform, dimensions, Tier 2 persona + VS/CL route, categorized atomic `Variant A/B` + `2+1+1` `FourUpPreviewHostView` composite reuse, `.xcodeproj` `ENABLE_DEBUG_DYLIB = YES`, anti-patterns, `spec_sha` |
| Phase 1 | real screenshots, geometry, clipping, CJK and state evidence |
| close | receipt, obsolete-test manifest, cleanup and reroute |

## Categorized Atomic Dual-Variant & PreviewHost Dylib Contract

- **Categorized Atomic Dual-Variant + Composite Reuse**: Group UI components by category; each atomic component provides `>=2` distinct interactive variants (`Variant A/B`), and composite screens (`v01..v04` in `FourUpPreviewHostView`) directly reuse those atomic variants without duplicating view code.
- **Xcode Preview Host `ENABLE_DEBUG_DYLIB=YES` Rule (`DC-017` / `preview-host-enable-debug-dylib`)**: When a SwiftPM package defines an executable `*PreviewHost` alongside `#Preview`, generate a native `.xcodeproj` (`project.yml` + `xcodegen`) with `ENABLE_DEBUG_DYLIB = YES` on both the executable host and library target (plus shared schemes) and open `.xcodeproj` in Xcode so `#Preview` never fails with `ENABLE_DEBUG_DYLIB`.

Native Apple work uses `magpie/group/designer`, `magpie/group/coding`, `PreviewHostKit`, Xcode `#Preview`, and 120Hz. Web evidence uses Playwright; Apple evidence uses ImageRenderer. Never ship a No Code Demo.
