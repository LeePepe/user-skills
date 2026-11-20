# Design Flow Architecture

This entry is handoff-only: `design-studio` Entry A ingests; `my-designer` Entry B authors `design.md`/`spec.md`; the downstream Writer implements product code. The Reviewer performs Visual Traceback and labels `[IMPL_BUG]` versus `[SPEC_GAP]`.

| Phase | Contract |
|---|---|
| Phase 0 | platform, dimensions, VS/CL route, anti-patterns, `spec_sha` |
| Phase 1 | real screenshots, geometry, clipping, CJK and state evidence |
| close | receipt, obsolete-test manifest, cleanup and reroute |

Native Apple work uses `magpie/group/designer`, `magpie/group/coding`, `PreviewHostKit`, Xcode `#Preview`, and 120Hz. Web evidence uses Playwright; Apple evidence uses ImageRenderer. Never ship a No Code Demo.
