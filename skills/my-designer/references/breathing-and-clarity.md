# Visual Air, Breathing Room & Zero-Guessing UX (`L1` Craft Reference)

Load this file on demand when designing `visual-style` (`VS`), `color-palette` (`CL`), `layout-navigation` (`LN`), or `typography-copy` (`TC`) surfaces, or when diagnosing "crowded / suffocating / dated / confusing" screens.

"Breathing room" (呼吸感) is **never** achieved by blindly making every spacing larger. It comes from **tight internal cohesion + generous structural whitespace + restrained visual noise + zero-guessing microcopy**.

---

## 1. The 5 Quantifiable Levers of Breathing Room (呼吸感五抓手)

| # | Lever | Quantifiable Engineering Rule (`AppTheme`) | Anti-Pattern (Why Screens Feel Suffocating) |
|---|---|---|---|
| **1** | **Spacing Multipliers (内紧外松 · Gestalt Proximity)** | Enforce a **`1 : 3 : 6` three-tier ratio**:<br>• Intra-atom (`icon↔text`, `title↔subtitle`): `4–6pt` (`Space.xs`)<br>• Card padding: `16–20pt` (`12–16pt` on compact macOS popovers) — **Container `padding` MUST always be `>=` internal row `spacing`**<br>• Inter-section gap: `24–32pt` (`Space.xxl` / `Space.section`) | Using uniform `12pt` spacing everywhere so atoms float apart while sections bleed into each other; or setting card `padding(10)` smaller than internal `spacing: 16`. |
| **2** | **Chunking Cap (`<= 2–3` Groups · See `interaction-layering.md`)** | Cap any single viewport or split-view column at **`2–3` visual container groups** (`aspect_ratio <= 2.6`). Fold 3rd+ modules into a Segmented Lens, Accordion single-active row, or `SWWallet` 3D `ZStack` (`~220pt`). | Stacking 4–6 cards vertically in a `>1000pt` scroll plane (`DC-001`) or rendering the same dataset twice (`DC-002`). |
| **3** | **Borderless Luminance Tiers (以底代框 · `L0 -> L1 -> L2`)** | Separate regions using **background luminance steps (`2%–4%` delta)** instead of heavy `1px` box borders or dark drop shadows:<br>• `L0` Window/Canvas background<br>• `L1` Grouped card surface (borderless or `0.5pt` `white.opacity(0.07)` top specular rim)<br>• `L2` Active/Focal surface<br>• List dividers: either zero dividers or leading-indented `0.5pt` hairlines (`opacity 0.06–0.10`). | Drawing `1px` solid borders around every card, metric cell, and badge ("wireframe cage"), or stacking full-width dark dividers on every row. |
| **4** | **Typographic Scale & Contrast Retreat (字重拉开 · 次要退后)** | • **At most `3–4` font sizes per viewport** (e.g., `32pt` Hero numeral, `15pt` Headline, `13pt` Body, `11pt` Caption).<br>• **Contrast Retreat**: reserve `.primary` (`100%`) strictly for core values and titles; push units, timestamps, and hints to `.secondary` / `.tertiary`.<br>• **Line Width & Leading**: cap body copy at `55–70` chars (`28–35` CJK chars) with `4–6pt` line spacing. | Making every label `.primary` at `13–14pt` so all text shouts at equal volume. |
| **5** | **90/10 Chromatic Restraint & Z-Axis Glass Air (色彩克制与景深)** | • **90% Neutral Canvas + 10% Brand Accent**: reserve high-saturation brand color (`CL-001..008`) for the single `P0` primary action or active selection.<br>• **Status Pills**: use `color.opacity(0.12–0.18)` fill + full-saturation label (`Capsule`), never solid neon blocks.<br>• **Z-Axis Air**: apply `.glassEffect(.regular)` strictly to floating toolbars/companion bars so scrolling content softly diffuses beneath. | Painting every category card or badge in full-saturation rainbow blocks, or applying `.glassEffect` to content rows. |

---

## 2. Zero-Guessing UX Law (`说法 · 入口 · 反馈` — Novice-Friendly Guardrails)

> *"一个功能好不好用，不看它有多强，就看用户要猜几次。"* (Vault `TC-001` / `LN-001` / `MI-007` · Catalog `IP-006` / `IP-007` / `MP-007`)

| Dimension | Rule | Bad (Forces User to Guess) | Good (Zero-Guessing Apple Native) |
|---|---|---|---|
| **1. 说法 (`TC-001` / `IP-006`)**<br>*Natural-Language Intent* | Never expose backend schema or system jargon as UI labels. Translate every control label into a plain-language user intent. | `"循环规则 (Recurrence Rule)"`, `"Cron 表达式"`, `"同步锚点偏移"` | `"每周一提醒我"`, `"每天早上 9:00 自动备份"` |
| **2. 入口 (`LN-001` / `IP-007`)**<br>*In-Context Locus + Folded Advanced* | Place `P0/P1` options directly where the task happens; fold low-frequency "advanced settings" by default so novices never hunt across screens. | Scattering basic task options into a global Settings window, or dumping 12 advanced toggles inline above the primary button. | Inline `Menu` / `Picker` right on the active task card + collapsed `DisclosureGroup("高级选项")` or `P3` `⋯` Menu/Sheet. |
| **3. 反馈 (`MI-007` / `MP-007`)**<br>*Deterministic Consequence Receipt* | Never leave `Save / Commit` silent. Immediately confirm with the concrete **next consequence** plus tactile/numeric feedback. | Tapping `"保存"` closes or stays static with zero change, or shows a generic `"操作成功"`. | Inline status / toast receipt: `"已保存 · 下次提醒周一上午 9:00"` + `.contentTransition(.numericText())` / `.symbolEffect(.bounce)` + `SensoryFeedback.success`. |

---

## 3. Web Quiet Editorial & Anti-Box-in-Box Baseline (Web 安静排版四铁律)

| # | Iron Law | Web Engineering Contract (`WebViewport`) | Anti-Pattern (`DC-006..008`) |
|---|---|---|---|
| **1** | **Zero Box-in-Box Hero & Tag Walls (`以底代框`)** | Page intro (`section.page-intro`) sits directly on `L0` canvas (`--strata-surface-page`); list items live in a single `L1` `.card` with `1px` hairlines (`<=3` tags/item, `<=8–10` hot tags in aside). | Stacking a bordered Hero box around inner bordered sub-boxes + 30+ tag pills on the first fold. |
| **2** | **Zero Nested Prose Cages & Heading Emojis (`原生正文层级`)** | Prose (`.prose`, `17px/1.8`, `680px`) uses `blockquote` (`4px` accent left border) for `TL;DR` and native `h2`/`h3` for Q&A/checklists — zero nested `.card` per Q&A, zero decorative heading Emojis. | Wrapping every Q&A or checklist in its own bordered card and prefixing headings with `📌`/`❓`/`✅`/`🎬`/`🏷️` (`DC-007`). |
| **3** | **CJK Table Wrap Contract (`表格折行防撑爆`)** | Allow natural wrapping on `.prose td` (`white-space: normal; min-width: 96px`) inside `overflow-x: auto`; restrict `white-space: nowrap` + `tabular-nums` strictly to `.num`, `time`, or short code columns. | Applying `white-space: nowrap` to all `.prose th, .prose td`, overflowing `680px` cards and clipping `390px` screens (`DC-008`). |
| **4** | **True Viewport Geometry Gate (`Playwright 390×844`)** | Verify every static `.html` starts with `<!doctype html>` on line 1 (`DC-006`), render mobile `390×844` and desktop `1280×900` via Playwright `setViewportSize`, and assert `scrollWidth <= clientWidth`. | Using desktop Chrome CLI `--window-size=390,844` (clamped by macOS ~500px minimum window width) or leaking raw `---` YAML frontmatter. |
