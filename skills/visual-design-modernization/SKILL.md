---
name: visual-design-modernization
description: Use when a user says an app's UI "looks bad / dated / not modern / misaligned / conflicts with Glass" and wants to improve visual quality (personality mode, spacing, typography, alignment, anti-collision, multi-device layout) — especially for SwiftUI/macOS/iOS and web. Uses a fast Two-Stage Verbal Spec -> Direct Product Implementation -> Real Screenshot Review loop (no throwaway code demos or isolated Prototype directories).
allowed-tools: Read, Write, Edit, Bash, Agent
---

# Visual Design Modernization (Direct-to-Product Fast Loop)

A repeatable method for taking a UI that feels "dated / misaligned / generic / conflicting" and making it cohesive, distinctive, and precise.

**Core shift:** Never build throwaway HTML mockups or isolated `Prototype/` code directories that drift from production tokens. Instead, use a **Two-Stage Verbal Spec** (Terminology Translation → Parameterized Rules) for alignment and direction, then implement **directly in the product or design-system code** and iterate against real rendered screenshots.

**Announce at start:** "I'm using the visual-design-modernization skill."

## When to use

- User says the UI "looks bad", "not modern", "dated", "单调/拥挤/没对齐/和 Glass 冲突", or wants visual polish and unification.
- Upgrading a screen, component library (`shared-design-system` / `Strata`), or product view to one of the 4 Visual Modes (`Strata-Core`, `Strata-Glass`, `Independent-Glass`, `Independent-Custom`).

## When NOT to use

- Pure non-visual backend or logic bugs → normal engineering.

## The Fast Product Loop (No Code Demo)

```
0. DIAGNOSE       inspect current screenshot/code → translate vague complaints into exact design terms
1. VERBAL SPEC    lock Visual Mode (A/B/C/D) + 6-Dimension parameters in concise text (north-star.md or brief)
2. IMPLEMENT      apply directly to real product / design-system components & tokens (no Prototype/ dir)
3. RENDER         capture real app/fixture screenshots (Light + Dark, applicable Device Macro-Tiers)
4. REVIEW         design-reviewer / apple-design-reviewer scores screenshots (/35 + P0/P1 alignment/collision checks)
5. ITERATE        fix P0s directly in product code, re-render until clean
```

Never judge aesthetics or alignment from reading code alone—always verify against rendered pixels in Step 3–4.

### Step 0 & 1 — Two-Stage Verbal Spec (Terminology → Parameters)

Do not write demo code to explore directions. Translate the diagnosis into a concise verbal specification covering the **6 Core Dimensions** (see [my-designer five-layer craft](../my-designer/references/five-layer-craft.md)):

1. **Visual Mode (pick 1):**
   - `Strata-Core` (Strata solid luminance tiers + candy accents)
   - `Strata-Glass` (Strata solid content + outer floating glass chrome)
   - `Independent-Glass` (Independent palette/atmosphere + native Apple Liquid Glass controls/nav)
   - `Independent-Custom` (Bespoke system, e.g., Basalt on web or editorial minimalist)
2. **Interaction, Hierarchy & Motion:**
   - Single primary focal point + 1 primary CTA per viewport.
   - All 4 states (`populated`, `loading` skeleton, `empty` with 1 CTA, `error` with inline retry).
   - Micro-feedback `120–150ms` (`scale(0.98)` press); spatial transitions with spring (`response: 0.32, dampingFraction: 0.82`); `Reduce Motion` crossfade fallback.
3. **Micro-Precision & Alignment (Fixed & Aligned):**
   - Strict `4/8pt` spacing scale (`4, 8, 12, 16, 24, 32, 48`).
   - **Size-aware concentric radii:** Outer `32pt` → Card `24pt` → Control `16pt` → Capsule; **dense collections / custom keyboards / compact grid cells scale down to `4–8pt` (`radius.xs` / `radius.sm`)**.
   - **Same-row control alignment:** Buttons and single-line inputs share identical box height (`36pt` / `44pt`), `.body` font, and align on the input box's center/baseline (`AlignmentID`), ignoring outer top labels or bottom error messages.
   - **Icon + text alignment:** Use `Label` or `.firstTextBaseline`; fixed-width icon columns constrain width only, never adding a tall square `minHeight` inside a `.top`-aligned `HStack`.
   - **Zero-jump embedded icons:** Chip/SearchField clear/remove buttons use `16–20pt` visual frames + hit-target expansion, never inflating row height to `44pt`.
   - **High-density section headers:** Dense single-line sticky/section headers share the content surface, require `maxHeight <= 44pt` and `<=4pt` vertical padding (top and bottom), and keep a single-line title budget. This dense-header limit is distinct from the generic sticky-header cap of `≤56pt`. Long CJK titles must not compete with wide text-capsule CTAs; use compact icon chrome or a subtle footer CTA.
   - **Tabular data-entry rows:** Keep `[index / type badge] → [checkbox] → [weight] → [reps] → [inline RPE / action]` on one centered horizontal axis. The index stays beside the checkbox, and Warm-up/RPE badges never fall into a second unaligned row.
   - **Hint-over-banner:** Put recommendations such as `建议 0lb × 12` inside input placeholders or compact column hints, never in repeated full-width banners.
   - **Drop-set grid and motion:** Sub-rows reuse the parent grid with `↳`/`D` in the leading index column; do not nest cards or starve inputs. Use a non-clipping spring (`response: 0.28–0.32`, `dampingFraction: 0.84–0.86`) for insertion/removal/expansion, with Reduce Motion fallback.
4. **Engineering Reuse:**
   - Bind every color, radius, and spacing value to `DesignTokens` / `StyleTokens` / `Strata` (or Basalt on web). If a token or constitution rule blocks the approved spec, update the token/contract and its tests in the same PR.
5. **Anti-Collision (Glass & Z-Axis):**
   - Glass only on Functional Layer (bars/floating controls, tint opacity `≤20%`); Content Layer (`Card`, `List`, forms, keypads) stays `.solid`. Never stack glass-on-glass.
   - Single `.safeAreaInset(edge: .bottom)` stack (`Content → Snackbar → DockedBar → Safe Area/Keyboard`)—never overlapping `.overlay(alignment: .bottom)` views.
   - Explicitly inject `theme` and `colorScheme` into detached hosts (`UITextField.inputView` custom keyboards, `UIHostingController`).
   - **Zero-height inactive slots:** In the single bottom inset stack, omit an inactive snackbar/toast slot or set its height to `0`; hidden opacity must not retain intrinsic layout height.
   - **Sidebar and keyboard continuity:** Rail `+` actions match sibling rounded-rectangle cards (`cornerRadius: 12`, continuous style). Custom keyboard docks extend through the bottom safe area with one `1px` top hairline and no white strip or duplicate divider.
6. **Multi-Device Macro-Tiers:**
   - **Tier 1 `Compact-Portrait` (iPhone portrait):** Single column, bottom 1/3 thumb-zone primary bar, `44pt` hit targets.
   - **Tier 2 `Wide-Touch` (iPhone landscape / iPad):** Split view / adaptive 2–3 col grid, bounded bottom actions (never full-width across landscape).
   - **Tier 3 `Desktop-Pointer` (macOS):** Sidebar + multi-pane, compact `32–36pt` controls, top-trailing/bottom-right actions (no bottom floating bar), hover + focus rings.

### Step 2 — Implement Directly in Product / Design System

Edit the real component or product view directly. Keep changes surgical and token-backed so every iteration is immediately shippable.

### Step 3 & 4 — Render & Objective Review

Render real screenshots (via simulator XCTest fixtures, app run, or `ImageRenderer` per `references/swiftui-preview-snapshot.md`) in Light + Dark and applicable Macro Tiers. Invoke `design-reviewer` / `apple-design-reviewer` to score against the 7-dimension rubric (`references/design-reviewer-rubric.md`, /35) plus alignment and collision checks. Fix all P0s before finishing.

For dense workout/table surfaces, the rendered gate must include a maximum-density composite (long CJK sticky header, Warm-up, RPE, inline recommendation hint, expanded drop-set, footer CTA, rail action, inactive/active bottom slot, keyboard dock) in Light/Dark and Normal/Large Mode, plus RTL where mirroring applies. Empty default fixtures alone do not satisfy the gate.

**P0 review gates:** Reject the delivery if any of these are present:

- iOS 26 `.toolbar` / `ToolbarItem` buttons nest custom capsule backgrounds or capsule strokes inside the system Liquid Glass bezel, or place two competing filled/bordered capsule buttons in one shared toolbar glass pill.
- A filled primary/accent button, including a detached UIKit custom keyboard `Done` / `完成` key, uses a foreground/background pair below WCAG 2.1 AA `4.5:1` in either Light or Dark mode.
- A generic sticky single-row section header exceeds the `56pt` cap, a dense single-line section header exceeds `44pt` or uses more than `4pt` vertical padding, or negative bottom padding lets its opaque background bleed over and occlude the first content row.

## What "modern" concretely means (portable checklist)

The reviewer enforces these; they're the recurring wins:
- **Max content width** (~1100–1280pt) centered — kills "stretched sparse".
- **Independent KPI cards** in a responsive grid — kills "numbers floating in one bar".
- **Data-viz next to numbers** — sparkline (area+line), ring gauge for ratios. A dashboard with only ↑↓ arrows reads as a text report.
- **Elevation** — either soft shadow *or* pew-style flat luminance tiers (L0 bg < L1 card < L2 inner); pick one, be consistent.
- **≥3 type levels** on one screen (display / body / meta) + rounded design for numbers.
- **Status as colored pills**, not grey text (`color.opacity(0.12–0.20)` fill + full-saturation text, `Capsule`).
- **Restrained color** — semantic system colors, no hex hardcode; ≤1 accent anchor per card.
- **Hued-but-restrained greys** (Radix slate) or **pure neutral** (Tailwind neutral); avoid heavy single-hue greys that read "cheap".

## Color: a swappable brand system

Give the app one `seed` color and derive the whole primary token set (primary / hover / active / subtle / muted / border / text / onPrimary / ring) with `makePrimaryPalette(seed:isDark:)`. Swapping the seed re-themes the app; **semantic colors (success/warning/danger) stay fixed** so "green=good/red=bad" never breaks. Full implementation + palettes in `references/color-system.md`.

Two modes coexist on macOS: follow the system accent color, or pin a brand seed — both feed the same derivation pipeline.

## Deliverables this skill produces

- A concise verbal design specification for the real product surface.
- Real product screenshots in light/dark and applicable macro tiers.
- Objective reviewer scores and the focused production implementation.
- (On approval) a production migration PR; never an isolated `Prototype/` directory.

## Anti-patterns

- ❌ Judging aesthetics from code without rendering.
- ❌ Editing production, constitution-locked code before the user approves the look.
- ❌ Asking a design-inexperienced user "does this look good?" without giving them rendered options and an objective score to anchor on.
- ❌ Adding shadows/tints/type-levels that the design constitution forbids without amending it → breaks the compliance test wall.
- ❌ Hardcoding hex where a semantic/system color exists.

## References

- `references/north-star-template.md` — fill-in visual-target rules.
- `references/color-system.md` — `makePrimaryPalette` + Radix slate / Tailwind neutral / Apple system color values.
- `references/swiftui-preview-snapshot.md` — ImageRenderer snapshot recipe + gotchas.
- `references/design-reviewer-rubric.md` — the 7-dimension scoring rubric (shared with the global `design-reviewer` agent).
