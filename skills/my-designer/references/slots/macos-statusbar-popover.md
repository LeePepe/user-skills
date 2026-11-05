# macOS Status Bar Popover

## slot: macos-statusbar-popover

Use `Strata-Core` for the fixed `380pt` status-bar popover. This slot carries
the macOS-specific fixed-width rules removed from the global harness:

- Apply content padding before the host frame:
  `.padding(16).frame(width: 380)`. Never frame first and then pad.
- Use a two-up Bento grid with equal-height header, primary-gauge, and metric
  rows so the card bottoms align. Keep card content solid; reserve material
  for functional floating chrome.
- Format telemetry compactly with `K/M/B` units and one-decimal load
  averages; never allow long raw numerics to wrap or ellipsize.
- Use compact `32–36pt` pointer controls, `28×28pt` visual icon targets with
  separate keyboard/focus affordances, and place primary actions top-trailing.
- Cover populated, loading, empty, and error states at the real `380pt`
  host dimension in Light and Dark, including non-integer capacity fixtures.
