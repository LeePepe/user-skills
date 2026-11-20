# Interaction Layering

Keep three layers explicit: Content (solid task material), Functional (bars and controls), and Feedback (state, motion, haptics). Liquid Glass belongs only to functional chrome (`DC-011`); content remains readable and state remains recoverable.

| Rule | Evidence |
|---|---|
| ownership | one control owns one transition; never nest `Button` inside a `Button` label or lock expandable rows to collapsed `minHeight` (`DC-012`) |
| expand stability | keep persistent status/priority pills identical across collapsed/expanded states, place expand-only rows below persistent headers, and wrap reveals in `.clipped()` (`DC-016`) |
| section gate | PreviewHost/Section views require real `@State`/`@Binding`/`@Bindable`, `DisclosureGroup`, `ClippedRevealLayout`, or `SW*` (`check-section-interactivity`) |
| state & motion | idle → intent → commit/cancel → settle; bounded transforms and reduced-motion fallback |
| touch & preview | >=44pt mobile, >=28pt + keyboard parity on desktop; `PreviewHostKit`, `#Preview`, 120Hz for native Apple |

The `magpie/group/designer` proposal is frozen before `magpie/group/coding` implementation.
