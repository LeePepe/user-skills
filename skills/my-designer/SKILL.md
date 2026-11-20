---
name: my-designer
description: "Entry B of the Dual-Entry Design Flow: progressive-disclosure product design, UI modernization, and post-implementation Visual Traceback for desktop, mobile, and web."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# my-designer — Entry B

This is a handoff-only design capability. Author the implementation-ready `design.md`/`spec.md` handoff and receipts; the downstream `Writer` implements **directly in the product or `shared-design-system` code**, never a throwaway **No Code Demo**. Entry A (`design-studio`) owns external inspiration ingestion.

## Role-Based Routing（按角色唯一必读抽屉 + 唯一必跑 CLI）

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Designer | `references/routing-index.md` | `verify_design_cases.py route --platform <platform> --dimensions <d>` |
| Role: Writer | `references/navigation-evidence-contract.md` | `verify_design_cases.py audit-code --platform <platform> --path <path>` |
| Role: Reviewer | `references/eval-cases.json` | `verify_design_cases.py flow-map` |
| Role: M1-TL | `references/design-flow-architecture.md` | `skill_contract.py eval --skill my-designer --strict` |

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Read one L1 drawer | Run one L2 CLI |
|---|---|---|
| platform/dimension route | `references/routing-index.md` | `verify_design_cases.py route --platform <platform> --dimensions <d>` |
| implementation audit | `references/navigation-evidence-contract.md` | `verify_design_cases.py audit-code --platform <platform> --path <path>` |
| Visual Traceback/evaluation | `references/eval-cases.json` | `verify_design_cases.py flow-map` |
| shared appearance | `references/appearance-ledger.md` | `verify_design_cases.py flow-map` |
| modernization/clarity | `references/modernization-dimensions.md`, `references/breathing-and-clarity.md` | `verify_design_cases.py flow-map` |

## Phase 0 · Direction and freeze

1. Lock one platform (`desktop`, `mobile`, or `web`), 1–4 dimensions, one `VS-*` + `CL-*` route, and explicit anti-patterns. Follow `L0 -> L1 -> L2` via `routing-index.md`.
2. Native Apple exploration uses Local Native 120Hz SwiftUI Preview Host (`PreviewHostKit` + Xcode `#Preview`); Web uses Playwright. Preserve `r5-todo-swiftui-preview` as a native-only firewall. The two-stage pipeline uses `magpie/group/designer` then `magpie/group/coding`.
3. Run four independent candidates. After each round run `reset-preview-selection`; verify with `check-preview-selection`. Archives are `preview-selection-r<N>.json`; reset fields are `selectedVariantID`, `starredVariantIDs`, and `notesByVariant`. AT-011 uses `NavigationSplitView` on desktop.
4. Capture Negative DOM/View Assertions and an Obsolete Test Manifest. Clean ephemeral demos with `cleanup-demo --goal <goal>`.

## Phase 1 · Visual Traceback

The Reviewer compares declared constraints with screenshots and labels every finding `[IMPL_BUG]` or `[SPEC_GAP]`. Use ImageRenderer for Apple states, Playwright for Web, and update `spec_sha` before changing an omitted rule. Generic recurring cases enter the defect index through `sink-rule`.

## TIA-14 section gate

Reject static SwiftUI PreviewHost/Section color blocks; require `@State`, `@Binding`, `@Bindable`, `DisclosureGroup`, `ClippedRevealLayout`, or a formal `SW*` component:

```bash
python3 skills/my-designer/scripts/verify_design_cases.py --check-section-interactivity <path>
python3 skills/my-designer/scripts/verify_design_cases.py --lookup-defect <keyword>
```

## L2 commands

```bash
python3 skills/my-designer/scripts/verify_design_cases.py flow-map
python3 skills/my-designer/scripts/verify_design_cases.py audit-code --platform mobile --path <path>
python3 skills/my-designer/scripts/verify_design_cases.py check-section-interactivity --path <path> --snippet '<swift>'
```
