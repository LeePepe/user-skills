---
name: my-designer
description: "Entry B of the Dual-Entry Design Flow: progressive-disclosure product design, UI modernization, and post-implementation Visual Traceback for desktop, mobile, and web."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# my-designer — Entry B (`B0..B4`)

This is a handoff-only design capability. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code**, never a throwaway **No Code Demo**. For external inspiration and palette ingestion, use `design-studio` (Entry A). Reference drawers: [eval-cases.json](references/eval-cases.json) and [remotion-video.md](references/remotion-video.md) (video mode only).

## Phase 0 · Direction, variants, and freeze

1. Lock one first-class platform (`desktop`, `mobile`, or `web`), 1–4 dimensions, one `VS-*` + `CL-*` route, and explicit anti-patterns.
2. Route through `references/routing-index.md`, taxonomy, vault, and toolchain. Native Apple (`desktop`/`mobile`) exploration must use Local Native 120Hz SwiftUI Preview Host (`PreviewHostKit` + Xcode `#Preview`); Web HTML/CSS and MP4 galleries are forbidden (`r5-todo-swiftui-preview`). The two-stage demo pipeline uses `magpie/group/designer` then `magpie/group/coding`.
3. Run four independent candidates before freezing. Use `design_catalog.py reset-preview-selection --receipts-dir <dir> [--round N]` after each round, then verify with `check-preview-selection --file <path>`. Archives are `preview-selection-r<N>.json`; reset fields are `selectedVariantID`, `starredVariantIDs`, and `notesByVariant`.
4. Split navigation for `AT-011`: macOS uses `NavigationSplitView`/Commander toolbar and Inspector; iOS uses a single-column `NavigationStack` and platform-native bottom actions. Never put a mobile bottom capsule on macOS.
5. Subtractive requirements need `Negative DOM/View Assertions` and a concrete `Obsolete Test Manifest (file:line)`. In-place unfold stays in one container; do not substitute a stacked duplicate card. Clean ephemeral demos after freeze with `cleanup-demo --goal <goal>`.

## Phase 1 · Visual Traceback

The Reviewer compares declared design constraints with real screenshots and classifies every finding as `[IMPL_BUG]` (Writer deviation) or `[SPEC_GAP]` (amend the spec first). Check subtractive exclusivity, zero instructional micro-copy, CJK wrapping, clipping, geometry, and platform divergence. Capture Web screenshots with Playwright and Apple states with ImageRenderer. Promote a `[SPEC_GAP]` to a shared defect case only when it is generic, symbol-free, and recurring; update `spec_sha` and use `sink-rule` for eligible shared gates.

```bash
python3 skills/my-designer/scripts/verify_design_cases.py flow-map
python3 skills/my-designer/scripts/verify_design_cases.py register-case --id auto --title "..." --platforms desktop,mobile,web --symptom "..." --fix-rule "..."
```

## Loop 5 · Taste Evolution

The shared `taste_evolution_loop.py` reads archived and active `preview-selection*.json` receipts plus Visual Traceback findings, then emits deterministic Owner gate proposals. It never edits catalogs or gates. Run `harvest`, `cluster`, `propose-gates`, or `loop-check` with `--handoff-root`; use `--mock-jev-json` for offline decisions. The runner executes on the Mac node, not a NAS filesystem.
