# Five-Layer Designer Craft

This contract turns visual design quality into five reviewable layers. Each layer
is a design decision and an implementation gate, not a decorative checklist.

## L1 — Perceptual physics

- Treat dark mode as a calibrated surface system, not an inversion.
- Preserve `L0 canvas < L1 card < L2 inset` and use a `9%–16%` top highlight
  where a dark surface needs separation.
- Calibrate semantic colors independently in light and dark appearances.
- Pair `interactiveAccent` with `onInteractiveAccent` at WCAG 2.2 AA
  (`>=4.5:1`); use `tabular-nums` or `.monospacedDigit()` for changing values.
- Keep typography platform-aware: iOS body floors at `11pt`, macOS at `10pt`,
  Web Operate text at `>=14px`, and Web mobile `<input>` text at `>=16px`.

## L2 — Platform ergonomics

- Share semantic tokens and component anatomy across platforms, but do not
  transplant platform shells, gestures, or action topology.
- Minimum targets are iOS `44x44pt`, Android `48x48dp`, macOS `28x28pt`, and
  Web `24x24px` for WCAG 2.2 AA.
- Keep native gesture boundaries intact. Glass belongs to floating functional
  chrome; content cards, lists, forms, and keypads remain solid. Never stack
  glass-on-glass or add a second bezel inside system toolbar chrome.

## L3 — State completeness

- Every data-bearing surface defines `populated`, `empty`, `loading`, and
  `error`, in both `light` and `dark` appearances.
- Loading preserves populated geometry (`CLS = 0`); empty states explain why
  and expose one primary CTA aligned with the product's business charter
  (for example, a read-only app guides users to the CLI/help path rather than
  offering a generic write/create CTA); errors explain the cause and offer
  inline recovery.
- Include stale-with-error and permission-denied behavior where the product
  can encounter them: preserve the last known data with a visible stale/error
  state and retry path. Never replace a failed response with fake zero data.

## L4 — Deep modules

- Put complexity behind a narrow semantic interface. Components should consume
  business data and high-level variants while hiding alignment, contrast,
  reduced-motion, and fixed-height behavior.
- A local composition seam may contain at most `15` lines of layout/chrome glue.
  A reusable variant or larger seam is an upstream task, not app-local polish.
- Separate the visual box from the hit-target layer: explicit
  `frame(minWidth: 44, minHeight: 44)` plus `.contentShape(Rectangle())` may
  expand interaction without inflating the visual row. Use explicit alignment
  guides such as `.strataControlCenter` and `.strataInputBaseline` so labels,
  controls, and validation copy share stable axes.
- Header distinction is contractual: `StrataStickySectionHeader` is fixed at
  `44pt`, single-line, and `.lineLimit(1)`; unbounded dynamic titles belong in
  `StrataExpandableTitleHeader` or a dedicated `ViewThatFits` row that reflows
  from one to two lines with `lineLimit(2)`. `StrataMetricRow` is availability-
  sensitive and must be confirmed by Explorer before use.

## L5 — Anti-AI-slop review

Score rendered output on a seven-dimension `/35` rubric and apply hard fails
for contrast below `4.5:1`, numeric ellipsis, duplicate units, double chrome,
unequal sibling card heights, or localization-key leakage. A screenshot review
does not replace keyboard-focus, VoiceOver, or runtime performance checks.

## Required handoff

The Designer writes handoff documents only. The Designer does not write
business code, demos, or `Prototype/` code. Use
`references/agent-design-doc-v1.md` for the YAML schema, deterministic layout,
width arithmetic, state matrix, and Writer/Reviewer gates.
