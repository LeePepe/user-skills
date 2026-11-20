# Routing Index: L0 -> L1 -> L2

Use the smallest drawer that answers the question. Plan A covers Apple HIG/native evidence; Plan C covers recurring defect cases and deterministic gates.

| Trigger | L1 drawer | L2 command |
|---|---|---|
| route/platform | `navigation-evidence-contract.md` | `verify_design_cases.py route --platform <p> --dimensions <d>` |
| interaction/material | `interaction-layering.md` | `verify_design_cases.py audit-code --platform <p> --path <path>` |
| Visual Traceback `[IMPL_BUG]`/`[SPEC_GAP]` | `design-flow-architecture.md` | `verify_design_cases.py flow-map` |
| defect/gate | `eval-cases.json` | `verify_design_cases.py --lookup-defect <keyword>` |

Native evidence uses `PreviewHostKit`, `#Preview`, 120Hz and ImageRenderer; Web evidence uses Playwright. Freeze `design.md` and `spec_sha` before Writer work. Generic recurring findings qualify for `sink-rule` only when symbol-free.

Plan A and Plan C are complementary: route first, disclose one drawer, run one CLI, then record the receipt.
