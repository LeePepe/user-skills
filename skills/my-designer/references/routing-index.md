# Routing Index: L0 -> L1 -> L2

Use the smallest drawer that answers the question. Plan A covers Apple HIG/native evidence; Plan C covers recurring defect cases and deterministic gates with self-disclosing runtime `guidance`.

| Trigger | L1 drawer | L2 command |
|---|---|---|
| route / Tier 1 invariants | `navigation-evidence-contract.md` | `verify_design_cases.py route --platform <p> --dimensions <d> [--persona <p>]` |
| interaction / glass / expand (`DC-011`/`DC-012`/`DC-016`) | `interaction-layering.md` | `verify_design_cases.py audit-code --platform <p> <path...>` |
| persona / `VS-*` & `CL-*` tokens | `appearance-ledger.md` | `verify_design_cases.py route --platform <p> --persona <compact\|default\|breathing>` |
| breathing / density / clutter (`DC-014`) | `breathing-and-clarity.md` | `verify_design_cases.py check-whitespace-ratio --persona <p> <png_or_dir>` |
| subtractive modernization (`DC-015`) | `modernization-dimensions.md` | `verify_design_cases.py audit-spec <design.md...>` |
| Visual Traceback `[IMPL_BUG]`/`[SPEC_GAP]` & `2+1+1` | `design-flow-architecture.md` | `verify_design_cases.py flow-map` |
| defect / eval gates | `eval-cases.json` | `verify_design_cases.py --lookup-defect <keyword>` |

Native evidence uses `PreviewHostKit`, `#Preview`, 120Hz and ImageRenderer; Web evidence uses Playwright. Freeze `design.md` and `spec_sha` before Writer work. Generic recurring findings qualify for `sink-rule` only when symbol-free.

Plan A and Plan C are complementary: route first, let L2 runtime checks self-disclose the exact L1 drawer on failure, then record the receipt.
