# Navigation Evidence Contract

The two-stage route uses `magpie/group/designer` then `magpie/group/coding`. Every design handoff declares platform, container, entry/exit, selection ownership, and recovery. Desktop favors `NavigationSplitView` and Inspector; mobile favors `NavigationStack` with platform-native actions; Web uses a semantic viewport and Playwright evidence.

| Evidence | Required |
|---|---|
| native preview | `PreviewHostKit`, Xcode `#Preview`, 120Hz |
| screenshots | ImageRenderer states or Playwright viewport captures |
| state | loading, empty, error, retry, success and cancellation |
| geometry | no clipping, overlap, or truncated numbers |
| handoff | `design.md`, `spec_sha`, and file:line obsolete tests |

A reviewer must be able to reproduce the route without inventing architecture.
