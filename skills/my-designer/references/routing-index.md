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
| JSON artifact facts (read-only, soft advisory) | `routing-index.md` | `verify_design_cases.py inspect-json <path>` or `--inspect-json <path>` |
| standalone geometry / PNG / traceback | `navigation-evidence-contract.md` | `design_evidence.py --self-check` / `--audit-geometry <json>` / `--check-whitespace <png_or_dir>` / `--verify-traceback <file>` |
| rendered pixel defect gate | `navigation-evidence-contract.md` | `verify_design_cases.py vision-audit --dir <screenshots> --platform <desktop\|mobile\|web> --out <receipt>` plus direct before/after PNG inspection |
| standalone Jev truth-table / stage | `eval-cases.json` | `design_jev.py --self-check` or `--stage <gallery\|spec\|traceback> --spec-file <file> --no-jev` |

Native evidence uses `PreviewHostKit`, `#Preview`, 120Hz and ImageRenderer; Web evidence uses Playwright. Freeze `design.md` and `spec_sha` before Writer work. Generic recurring findings qualify for `sink-rule` only when symbol-free.

Plan A and Plan C are complementary: route first, let L2 runtime checks self-disclose the exact L1 drawer on failure, then record the receipt. For Phase 1 Visual Traceback, PNG existence (`ls *.png`) or regex-only code scans can never certify a visual result: run `vision-audit` and directly inspect rendered before/after pixels.

`check-section-interactivity`/`--check-section-interactivity` and `check-whitespace-ratio`/`--check-whitespace-ratio` accept multiple positional targets mixed with repeatable/multi-argument `--path`/`--dir`. Every explicit target is checked: a missing path or directory with no relevant Swift/PNG files fails even when a later target passes. Interactivity also supports `--snippet` without paths (or with the historical `<snippet>` label). `lookup-defect`/`--lookup-defect` requires a non-empty keyword; usage errors exit 2 with bounded guidance here.

Platform-aware commands accept `--surface` as `--platform` (`ios -> mobile`, `macos -> desktop`). `route --platform <p> --surface <hint>` also retains UI surface hints. `audit-code --path <code> --spec-file <design.md>` validates both inputs and reports `spec_files` / `spec_validated`. Standalone Jev accepts `--gallery-dir` for gallery evidence, `--mock-jev-json` for offline replays, and only bounded loopback `--jev-url` endpoints for live checks.

Missing optional design-studio taxonomy/vaults use local routing defaults; missing canonical gallery renderers remain UNVALIDATED, never a fabricated PASS. `inspect-json` reports facts, not evidence certification. Repeated catalog imports and identical chooser-runtime checks are cached until their source mtime/runtime digest changes.
