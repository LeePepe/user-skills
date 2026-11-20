# Interaction Layering

Keep three layers explicit: Content (solid task material), Functional (bars and controls), and Feedback (state, motion, haptics). Liquid Glass belongs only to functional chrome; content remains readable and state remains recoverable.

| Rule | Evidence |
|---|---|
| ownership | one control owns one transition |
| state | idle → intent → commit/cancel → settle |
| motion | bounded transforms and reduced-motion fallback |
| touch | >=44pt mobile, keyboard alternative on desktop |
| preview | `PreviewHostKit`, `#Preview`, 120Hz for native Apple |

The `magpie/group/designer` proposal is frozen before `magpie/group/coding` implementation.
