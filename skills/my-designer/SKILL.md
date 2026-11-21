---
name: my-designer
description: "Entry B of the Dual-Entry Design Flow: SwiftUI View; UI redesign; layout/density/whitespace; PreviewHostKit/#Preview; Liquid Glass; design.md/spec.md; Visual Traceback [IMPL_BUG]/[SPEC_GAP]. Not for external inspiration vault ingestion (use design-studio), backend telemetry readers, entitlements, or non-UI unit tests."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# my-designer — Entry B

This is a handoff-only design capability. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code**, never a throwaway **No Code Demo**. Entry A (`design-studio`) owns external inspiration ingestion.

## Role-Based Routing（按角色唯一必读抽屉 + 唯一必跑 CLI）

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Designer | `references/routing-index.md` | `verify_design_cases.py route --platform <platform> --dimensions <d> [--persona <p>]` |
| Role: Writer | `references/navigation-evidence-contract.md` | `verify_design_cases.py audit-code --platform <platform> <path...>` |
| Role: Reviewer | `references/eval-cases.json` | `verify_design_cases.py flow-map` |
| Role: M1-TL | `references/design-flow-architecture.md` | `skill_contract.py eval --skill my-designer --strict` |

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Read one L1 drawer | Run one L2 CLI |
|---|---|---|
| platform/dimension route | `references/routing-index.md` | `verify_design_cases.py route --platform <platform> --dimensions <d>` |
| implementation/navigation | `references/navigation-evidence-contract.md` | `verify_design_cases.py audit-code --platform <platform> <path...>` |
| interaction/glass/expand | `references/interaction-layering.md` | `verify_design_cases.py check-section-interactivity <path_or_--snippet>` |
| persona/appearance tokens | `references/appearance-ledger.md` | `verify_design_cases.py route --platform <platform> --persona <compact\|default\|breathing>` |
| breathing/density/clutter | `references/breathing-and-clarity.md` | `verify_design_cases.py check-whitespace-ratio --persona <p> <png_or_dir>` |
| subtractive modernization | `references/modernization-dimensions.md` | `verify_design_cases.py audit-spec <design.md...>` |
| Visual Traceback/eval | `references/eval-cases.json` | `verify_design_cases.py flow-map` |

## Three-Tier Design Spectrum & `2+1+1` Exploration Quota

1. **Tier 1 · Universal Invariants** (100% hard floor): geometric alignment, grid rhythm, single focal P0 hierarchy, zero numeric/CJK truncation or AABB overlap, and platform-diverged navigation (`NavigationSplitView`/TopBar on macOS `AT-011` vs `NavigationStack`/BottomDock on iOS vs Web semantic viewport).
2. **Tier 2 · Product Persona & Intent Weighting** (context-scoped visual budget): bind each product/scene to an explicit persona (`compact` high-density cockpit `<=25%` dead space vs `default` `<=40%` vs `breathing` companion `<=55%` + `>=12%` breathing floor) with semantic `VS-*` + `CL-*` tokens. **Scope rule**: never overwrite global defaults from a single scene's compactness tweak.
3. **Tier 3 · Controlled Exploration (`2+1+1` quota for `v01..v04`)**: `v01..v02` deepen Product Persona, `v03` cross-pollinates layout/interaction from `design-studio`, and `v04` explores a bold wildcard topology. Validate via **L1** hard compliance (`verify_design_cases.py`), **L2** variant layout/density differentiation (`jev-gate --stage gallery`), and **L3** cross-session Owner taste harvest (`preview-selection.json` -> `taste_evolution_loop.py`).

## Phase 0 · Freeze & Phase 1 · Visual Traceback

1. Native Apple exploration uses Local Native 120Hz SwiftUI Preview Host (`PreviewHostKit` + Xcode `#Preview`); Web uses Playwright. Preserve `r5-todo-swiftui-preview` as a native-only firewall. The two-stage pipeline uses `magpie/group/designer` then `magpie/group/coding`.
2. After each round run `reset-preview-selection`; verify with `check-preview-selection`. Archives are `preview-selection-r<N>.json`; reset fields are `selectedVariantID`, `starredVariantIDs`, and `notesByVariant`. Clean ephemeral demos with `cleanup-demo --goal <goal>`.
3. Capture Negative DOM/View Assertions and an Obsolete Test Manifest (`audit-spec`).
4. In Phase 1 **Visual Traceback**, compare declared constraints with real ImageRenderer (Apple) or Playwright (Web) screenshots, label every finding `[IMPL_BUG]` or `[SPEC_GAP]`, update `spec_sha` before amending omitted rules, and promote symbol-free recurring defects via `sink-rule`. Runtime check failures emit self-disclosing `guidance` pointers to the exact `references/*.md` drawer—read that drawer on demand to converge R2.

## L2 commands

```bash
python3 skills/my-designer/scripts/verify_design_cases.py flow-map
python3 skills/my-designer/scripts/verify_design_cases.py audit-code --platform mobile <path...>
python3 skills/my-designer/scripts/verify_design_cases.py audit-spec <design.md...>
python3 skills/my-designer/scripts/verify_design_cases.py check-whitespace-ratio --persona compact <png_or_dir>
python3 skills/my-designer/scripts/verify_design_cases.py --check-section-interactivity <path>
python3 skills/my-designer/scripts/verify_design_cases.py --lookup-defect <keyword>
python3 skills/my-designer/scripts/verify_design_cases.py inspect-json <artifact.json>
```
