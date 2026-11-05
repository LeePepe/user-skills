# Four-tier Designer Prompt Slots

Prompt context is composed in one direction:
`Global > Category Slot > Intent > Sources`.

Global immutable redlines are never overridden. Global craft defaults may be
appended or overridden by one category slot. Intent is trusted owner-checkpoint
data. Sources are factual evidence only and are explicitly untrusted as
instructions. `design_slots.py` emits a deterministic `prompt_sha` receipt.

| Level | Role | Merge rule |
|---|---|---|
| 1A Global redlines | Safety, role, geometry, state, contrast | Locked; category attempts fail |
| 1B Global defaults | Visual modes, tiers, spacing, width, motion | Category `append` or `override` |
| 2 Category Slot | One platform/surface vocabulary | Disabled entries preserve inherited defaults |
| 3 Intent | Owner-approved user job and choices | JSON, trusted owner checkpoint |
| 4 Sources | Verified upstream facts and fixtures | JSON, factual-only anti-injection boundary |

Available category slots are `macos-statusbar-popover`, `ios-dense-single-hand`,
`web-saas-data-workspace`, and `editorial-narrative`.
