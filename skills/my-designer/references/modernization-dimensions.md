# Modernization Dimensions

Modernization is subtractive as well as additive. The deliverable is product code, never a **No Code Demo**.

| Dimension | Question |
|---|---|
| hierarchy | Is the first task visible without explanation? |
| navigation | Is the return path native and stable? |
| state | Are loading, empty, error, retry, and success explicit? |
| density | Does spacing protect the P0 and numeric content? |
| platform | Does desktop differ from mobile where conventions require it? |
| accessibility | Do contrast, Dynamic Type, keyboard, and Reduce Motion pass? |

## Replace Meta-Labels with Visual Semiotics (Show, Don't Tell)

Subtractive modernization removes instructional prose and meta-labels by encoding state, baselines, thresholds, and hierarchy visually (see `references/breathing-and-clarity.md`):

| Legacy Meta-Text / Clutter (`DC-014` / Subtractive Target) | Canonical Visual Semiotic Replacement | Methodology Anchor |
|---|---|---|
| `"只读"` / `"100%自动同步"` | Flat muted surface + domain source glyph (`❤️` / `⌚️`), zero interactive track/stepper | **Don Norman** perceived affordances & **Jacques Bertin** shape/luminance variables |
| `"默认值 39 km/h"` / `"恢复默认"` | Recessed track with Elevated Thumb + baseline tick dot (`•`) + offset-only `↺` reset glyph | **Don Norman** signifiers |
| `"一票否决"` / `"超过安全阈值"` | 3-layer Bullet Graph (~16–20pt): Qualitative Range Bands + Primary Measure (`•`) + Comparative Marker Ticks (`\|`) | **Stephen Few** bullet graph & **Jacques Bertin** position/hue variables |
| Hollow numeric tile (small number + >50% blank cell) | Dominant `.monospacedDigit()` reading (~50% cell height) + edge-hugged Sparkline / zone strip | **Edward Tufte** data-ink ratio & word-sized sparklines |
| `"点击卡片查看详情"` + crowded multi-row inline alerts | Single summary badge cluster + subtle `↗` signifier expanding on tap into spatial detail sheet | **Ben Shneiderman** overview-first ➔ details-on-demand |

Write concrete Negative DOM/View Assertions and an Obsolete Test Manifest in `design.md`.

For pre-implementation `audit-spec --strict-spec-gaps`, scope the test decision to a `Deprecated Test Clauses` / `Obsolete Test Manifest` inline clause (one physical line) or heading body (ending before the next Markdown heading, at any level). Cite a concrete test artifact (`LegacyTests.swift:42`, `legacy_spec.ts`), identifier (`test_removeLegacy`, `testLegacyNavigation`, or a backticked assertion), or an explicit scoped N/A/none/greenfield decision. An empty section, unrelated `Analytics: N/A`, or vague `tests TBD` is not evidence. These gap checks are advisory without `--strict-spec-gaps`; existing subtractive hard rules still require concrete obsolete-test references.
