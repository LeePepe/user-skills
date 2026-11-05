---
name: my-designer
description: 'Handoff-only design lead for rendered UI, visual-interface, and design-system work across shared-design-system (DesignTokens + Strata) and product repositories: define macro visual personality, two-stage verbal design specs, micro alignment/geometry, anti-collision, and multi-device adaptation. Downstream Writers own product/design-system implementation and real rendering; no throwaway demos or Prototype code.'
allowed-tools: Read, Write, Edit, Bash, Agent, Skill
---

# my-designer — thin handoff harness

This handoff-only skill owns the design handoff, not production implementation. Select one
visual mode, compose the applicable category slot, and write an
author the implementation-ready `design.md`/`spec.md` handoff and receipts.
Downstream
Writers implement in the product or design system, render real screenshots,
and iterate against the frozen handoff. Never create throwaway demos or
`Prototype/` code.

**Announce at start:** “I'm using the my-designer skill (as design lead).”

## Visual modes

Select exactly one mode for every surface:

1. **Mode A · `Strata-Core`** — Strata accents and solid neutral surfaces;
   best for dense tools, forms, tables, and settings.
2. **Mode B · `Strata-Glass`** — Strata solid content with glass restricted
   to outer floating containers, toolbars, and popovers.
3. **Mode C · `Independent-Glass`** — project-specific content with native
   Apple Liquid Glass for navigation, toolbars, and floating controls.
4. **Mode D · `Independent-Custom`** — a bespoke visual system such as Basalt
   + Recharts or an editorial world, with the same geometry and state floors.

Infer the platform and established token authority; do not mix modes ad hoc.
For shared-design-system, read [five-layer-craft.md](references/five-layer-craft.md)
and [agent-design-doc-v1.md](references/agent-design-doc-v1.md) when the task
requires their detailed contract.

## Level 1A — eight cross-platform hard floors

These are immutable and category slots cannot override them:

1. **Handoff-only role:** Designer writes verbal handoffs; Writer owns
   production code and rendering.
2. **Semantic tokens:** Use the active theme authority; never add raw colors.
3. **Four states and two appearances:** Cover populated, loading, empty, and
   error in Light and Dark.
4. **Contrast:** Interactive foreground/background pairs meet WCAG 2.1 AA,
   at least `4.5:1`.
5. **Platform ergonomics:** Respect platform typography and minimum
   `44×44pt` touch targets; never truncate numeric values or units with `…`.
6. **Host geometry:** Apply padding before fixed frame sizing, e.g.
   `.padding(16).frame(width: 380)`.
7. **Anti-collision stack:** Keep content surfaces solid, separate functional
   material from content, and route bottom chrome through one ordered inset
   stack.
8. **Upstream-first escalation:** Escalate reusable primitives or more than
   `>15` lines of local layout/chrome instead of patching a product view.

## Level 1B — general grid, corner, tier, and motion rules

- Use the `4/8pt` scale (`4, 8, 12, 16, 24, 32, 48`) and derive text width
  from container width minus padding, gaps, and fixed controls. Avoid
  off-grid magic values and zero/one-point entry gaps.
- Use size-aware concentric radii: outer `32pt`, card `24pt`, control
  `16pt`; compact collections and keyboards scale down to `4–8pt`.
- Classify the surface as one of three macro tiers: **Tier 1
  `Compact-Portrait`** (single column, thumb-zone actions), **Tier 2
  `Wide-Touch`** (adaptive two/three-column workspace, bounded actions), or
  **Tier 3 `Desktop-Pointer`** (sidebar/multi-pane, `32–36pt` controls,
  hover, keyboard focus, and Escape dismissal).
- Every surface has populated/loading/empty/error geometry; loading preserves
  geometry (`CLS = 0`) and error includes an inline retry.
- Route motion by frequency: frequent taps use pointer-down `scale(0.97)` with
  `100–120ms` ease-out and no spatial travel; ordinary state changes use
  critically damped springs (`response 0.28–0.30`, `dampingFraction
  0.85–1.0`). Never animate from `scale(0)` and honor Reduce Motion.
- Keep numeric transitions readable, use `ViewThatFits` or a dedicated row
  for unbounded titles, and preserve a strict single-line header where the
  surface is dense (`lineLimit(1)`). `StrataStickySectionHeader` is a fixed
  `44pt` row; `StrataExpandableTitleHeader` handles expandable titles.
  Verify availability before using planned primitives such as `StrataMetricRow`.

## Workflow and entry points

1. Intake the platform, surface, one visual mode, macro tier, token authority,
   states, and screenshot acceptance criteria.
2. Translate the request into design terminology, then lock parameter-level
   tokens, spacing, radii, alignment anchors, z-stack, states, and motion in
   the handoff. Existing-screen changes include an **Obsolete Test Manifest**
   and **Deprecated Test Clauses to Remove/Rewrite** section.
3. Compose `Global > Category Slot > Intent > Sources`. Category slots live
   in [`references/slots/`](references/slots/); use the platform slot rather
   than copying its rules into this harness.

Use the deterministic prompt composer:

```text
python3 skills/my-designer/scripts/design_slots.py list-categories
python3 skills/my-designer/scripts/design_slots.py compose \
  --category ios-dense-single-hand --intent-json '{}' --sources-json '{}'
```

For inspiration and interaction/motion/tool lookup, use the design-studio
catalog entry point (read-only reference):

```text
python3 skills/design-studio/scripts/design_catalog.py search --query IP-
python3 skills/design-studio/scripts/design_catalog.py recommend --surface-mode operate
```

The catalog is inspiration, not authority: preserve the floors above and
record chosen triggers, linkage, recovery, and motion in `design.md`. For
native projects, verify the pinned `Package.resolved`/`DesignKit` before
referencing upstream primitives; do not invent unavailable names. Before
visual delivery, run the applicable platform design reviewer on real
screenshots across device tiers and appearances.

the downstream `Writer` implements **directly in the product or `shared-design-system` code**;
this skill only authors the handoff.

## Native restraint

For a new native scaffold, use the repository's existing `DesignKit`,
`ColorSystem`, and package/build conventions. For an existing app, refine its
established components; do not replace architecture or seed a local token
table. For Financial, read its constitution and preserve financial engine,
currency/sign conventions, and data provenance.
