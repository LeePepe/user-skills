# iOS Dense Single Hand

## slot: ios-dense-single-hand

Use `Strata-Core` for high-density fitness entry. This slot supplies the
iOS-specific geometry that the global harness deliberately omits:

- Keep the centered row order
  `[index -> checkbox -> weight -> reps -> RPE]`; inputs and badges share a
  `44pt` control height and the index stays beside the completion control.
- Separate the outer `>=44×44pt` composite hit target from the inner
  `24–32pt` content-hugging numeric field. The whole visible pill must be
  interactive; do not give the inner field a fixed `44pt` side.
- Put `1.1`/`1.2` SubSetRow identity only in the leading index slot. Reuse
  the parent grid, keep AI suggestions in placeholders or keyboard actions,
  and avoid a full-row drop-set footer.
- Use a `44pt` single-line sticky header with `<=4pt` vertical padding and
  the same base plane as the list. Keep trailing header actions tight.
- Use a `64pt` CJK rail, five keypad columns, and one continuous solid
  keyboard dock with a single top hairline.
- Keep iOS text at `>=11pt`; primary numerics are `17pt` normal / `22pt`
  large, with coordinated `11–12pt` / `14–15pt` units. Use `4pt` row
  vertical padding and do not repeat full-width recommendation banners.
- Cover Normal/Large, Light/Dark, RTL, and the composite maximum-density
  fixture: long CJK header, Warm-up, RPE, inline hint, expanded drop-set,
  rail, bottom slots, and keyboard dock.
