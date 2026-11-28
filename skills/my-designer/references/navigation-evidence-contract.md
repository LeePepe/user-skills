# Navigation Evidence Contract

The two-stage route uses `magpie/group/designer` then `magpie/group/coding`. Every design handoff declares platform, container, entry/exit, selection ownership, and recovery. Desktop favors `NavigationSplitView` and Inspector; mobile favors `NavigationStack` with platform-native actions; Web uses a semantic viewport and Playwright evidence.

| Evidence | Required |
|---|---|
| native preview | `PreviewHostKit`, Xcode `#Preview`, 120Hz, single-viewport per `#Preview("...")` tab (`DC-018`) |
| screenshots | ImageRenderer states or Playwright viewport captures |
| state | loading, empty, error, retry, success and cancellation |
| geometry | no clipping, overlap, or truncated numbers |
| handoff | `design.md`, `spec_sha`, and file:line obsolete tests |

## Single-Viewport Preview Choreography Rule (`DC-018`)

- Never pack a wide multi-column horizontal-scroll board (`FourUpPreviewHostView` or `ScrollView(.horizontal)` wrapping side-by-side device/variant columns) into a single Xcode `#Preview` viewport that requires horizontal panning.
- Decompose multi-variant exploration into **individual single-viewport `#Preview("...")` entries** (one variant per preview tab, or a vertical single-column stack with a top segmented variant switcher for atomic `Variant A/B` comparison).
- Validate deterministically via `verify_design_cases.py audit-code --platform <platform> <path...>` (`DC-018`: `xcode-preview-single-viewport-no-horizontal-host`).

A reviewer must be able to reproduce the route without inventing architecture.
