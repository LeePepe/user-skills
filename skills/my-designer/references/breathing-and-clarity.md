# Breathing and Clarity

Treat whitespace as a task affordance: reserve a clear focal region, protect labels from controls, and keep one primary action per viewport. Validate compact and expanded states, CJK wrapping, Dynamic Type, and reduced motion.

## Context-Scoped Density Triage (Tier 2 Persona Guard)

| Persona (`--persona`) | Max Bottom Dead Space | Breathing Floor | Scope & Intent Weighting |
|---|---|---|---|
| `compact` (cockpit/dense) | `<= 25%` | `0%` | High-density telemetry/ledger cockpit; tight chrome, dense tables/charts. **Never overwrite global defaults.** |
| `default` (balanced) | `<= 40%` | `0%` | Global baseline task workspace; 1 focal P0, zero dead viewport tail. |
| `breathing` (companion/calm) | `<= 55%` | `>= 12%` (soft advisory) | Calm companion/editorial surface; generous margin protecting P0 clarity without overcrowding. |

| Check | Evidence |
|---|---|
| hierarchy | one focal task and one P0 |
| density | context-scoped `--persona` budget; no accidental dead rail or crowded toolbar |
| copy | concrete verbs; zero instructional micro-copy (`DC-014` / `UI_INSTRUCTIONAL_CLUTTER`) |
| recovery | empty/error states retain a return path |

## Visual Semiotics & Data-Ink Methodology (Bertin · Tufte · Few · Norman · Shneiderman)

Encode state, mutability, baselines, thresholds, and trends through preattentive visual channels instead of explanatory prose labels:

1. **Jacques Bertin's *Retinal Variables* (1967) & Colin Ware's *Preattentive Processing* (2004)**:
   - The visual cortex decodes **7 retinal variables** in 100–200ms before serial text reading:
     - `Position`: metric placement along a shared scale/tick (highest quantitative precision, Cleveland & McGill).
     - `Value / Luminance`: surface depth and opacity encoding hierarchy and read-only/synced vs elevated interactive state.
     - `Hue`: qualitative category or continuous safety spectrum (emerald comfort ➔ sky degradation ➔ amber warning ➔ crimson limit).
     - `Size / Length`: magnitude and proportional weight.
     - `Shape / Glyph`: domain source or entity identity (e.g., `❤️` physiological source, `🧭` wind vector, `🎙️` voice modality).
     - `Orientation`: vector direction and rate-of-change trend (`↗` climb, wind angle).
     - `Texture / Dash`: certainty and commit state (solid line = committed route, dashed line = streaming/draft proposal).
   - **Design Rule**: Any state, source, trend, or certainty that maps to these 7 retinal variables must be encoded visually rather than degraded into explanatory prose labels.
2. **Edward Tufte's *Data-Ink Ratio, Sparklines & Small Multiples* (1983/2006)**:
   - **Maximize Data-Ink Ratio (`Data-Ink Ratio = Data Pixels / Total Card Pixels`)**: strip non-data meta-ink and chartjunk (explanatory rule labels like `"只读"`, `"默认值"`, `"100%自动同步"`, `"点击查看详情"`, internal spec IDs like `"A1 ·"` / `"[24/24格]"`, duplicate unit strings, and rainbow primary numerals). Keep primary large numerals (`>= 24pt`) in high-contrast monochrome `.primary`, reserving chromatic hue for status strips or threshold badges (`UI_COCKPIT_RAINBOW_NUMERAL_NOISE`).
   - **Word-Sized Sparklines, Synchronized Small Multiples & Cell Spatial Fill**: in compact grid cells (`compact` persona `1×1` / `2×1` / `2×2` tiles), avoid hollow cards with a single small number over blank canvas; pair a dominant typographic reading (~50% cell height with `.monospacedDigit()`) with an edge-hugged ambient Sparkline or zone strip. Never overlay `>= 3` multi-unit curves (e.g. elevation + speed + heart rate) in a `< 80pt` box (`UI_MINI_SPAGHETTI_CHART_COLLAPSE`); split them into **Synchronized Small Multiples** (independent single-series micro-sparkline rows sharing the time axis).
3. **Stephen Few's *Bullet Graph Specification* (*Information Dashboard Design*, 2006)**:
   - For metrics combining live reading, default baseline, warning threshold, and hard limit, use a compact **3-layer Bullet Graph (~16–20pt height)** instead of prose rules (`"一票否决"`, `"默认值 39 km/h"`):
     - **Layer 1 · Qualitative Range Bands**: continuous chromatic/luminance bands (Comfort ➔ Degradation ➔ Warning Threshold ➔ Hard Limit).
     - **Layer 2 · Primary Measure**: high-contrast bar or live cursor dot (`•`) marking the current measured value.
     - **Layer 3 · Comparative Marker Ticks (`|`)**: crisp vertical tick marks for default baseline, warning threshold, and hard stop limit.
4. **Don Norman's *Perceived Affordances & Signifiers* (*The Design of Everyday Things*, 2013)**:
   - Controls communicate mutability through physical/visual form rather than instructional labels:
     - **Flat Muted Surface + Source Glyph (`❤️` / `⌚️`)** = system-synced read-only telemetry (no stepper/track).
     - **Recessed Track + Elevated Thumb / Segmented Pill / Stepper** = user-mutable parameter.
     - **Baseline Tick Dot (`•`) + Contextual Reset Glyph (`↺`)** = `•` marks the default position on the track; `↺` materializes only when offset from baseline.
5. **Ben Shneiderman's *Visual Information-Seeking Mantra* (IEEE 1996)**:
   - **"Overview first, zoom and filter, then details-on-demand"**:
     - **Overview (Small Multiples / Bento Summary Tiles)**: primary screen tiles preserve high signal-to-noise (1 primary metric + 1 micro-spectrum/sparkline + subtle `↗` expansion signifier; collapse multi-alert items into a single summary badge cluster).
     - **Details-on-Demand (Spatial Drill-Down)**: tapping a summary tile expands spatially into a full-screen detail view or sheet to reveal multi-axis charts, sub-factor breakdowns, and threshold comparison tables.

Capture the persona scope in `design.md`, then let the Writer implement it in product code.
