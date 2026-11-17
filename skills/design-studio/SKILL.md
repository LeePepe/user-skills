---
name: design-studio
description: "Entry A of the Dual-Entry Design Flow: ingest, classify, and stockpile design inspirations, measured palettes, interaction/motion patterns, and video assets."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# design-studio — Entry A: Ingest, Stockpile & Video (`A0..A4`)

`design-studio` is **Entry A (`Ingest & Stockpile`)** of the Dual-Entry Design Flow (see [design-flow-architecture.md](references/design-flow-architecture.md)). Use it to classify and stockpile external design inspirations, reference apps, measured screenshot palettes, WWDC/HIG notes, interaction/motion patterns (`IP-*/MP-*`), reusable defect cases (`DC-*`), and `Video/Offline` Remotion pipelines.

For product UI design, existing screen modernization (`No Code Demo`), `design.md`/`spec.md` authoring, and post-implementation screenshot verification (`Visual Traceback`), use **Entry B (`my-designer`)** (see [../my-designer/SKILL.md](../my-designer/SKILL.md)). App/screen scaffold inputs and gate receipts follow [navigation-evidence-contract.md](../my-designer/references/navigation-evidence-contract.md).

`scaffold-gallery --platform mobile|desktop|macos|web --count 4` creates four live iframe screen candidates by default (`--count 10` is explicit backward compatibility) or four app-nav rounds under `~/Development/leepepe.github.io/demos/<goal>` when mounted; **for iOS / macOS native SwiftUI projects (`--platform mobile|desktop`), `v01..v04` variant exploration and motion/interaction previews MUST exclusively use the Local Native 120Hz SwiftUI Preview Host (`PreviewHostKit` + Xcode `#Preview`), strictly forbidding Web HTML/CSS simulations or low-fps MP4 video galleries (`r5-todo-swiftui-preview` style) on `leepepe.github.io/demos/` (Web HTML Choosers are reserved for Web frontend `--platform web` or static card/graphic projects)**; `--category` and `--category-title` aggregate a multi-category portal with device bezels, filters, theme, persisted chooser controls, breadcrumbs, first-category isolation, and live SELECT/STAR/NOTES telemetry. Mount and cleanup also refresh the active `/demos/index.html` hub; its chooser gate rejects non-canonical hiding or incomplete summaries. Every card exposes one escaped, distinct Chinese `variant-summary` from real metadata between its header and bezel; pre-wrapped summaries are normalized to plain text. Controls use `<category>/<id>`; portal and hub summaries/telemetry share the active non-reference inventory, with NOTES inside each category block. After B3 or Goal completion, run `cleanup-demo --goal <goal>`; it removes all round/category demos while preserving receipts.

The **Two-Stage Demo Pipeline** keeps ideation with Designer (`--role designer` → `magpie/group/designer`, Copilot `gpt-6.1-sol` + `grok-4.7`): FOUR high-craft screen or FOUR app-nav briefs by default (`--count 10` remains explicit legacy breadth). Python batch-scripted clone shells are forbidden; deterministic rendering may iterate independently defined configurations. Coding (`--role writer` → `magpie/group/coding`) implements approved HTML/CSS/JS and selected production code. `--prompts-dir` binds exact `prompts/<id>-prompt.md` files through manifest `prompt_file`; scaffold starters are not approval or a prose compiler. Sub-pages forbid caustic repeating stripes/rulers/debug bars and inner gallery controls; outer choosers own theme/platform/viewport/selection.

## Entry A · Ingest & Stockpile Workflow (`A0..A4`)
1. **A1 · Classify & Learn**: Score `desktop|mobile|web` × 8 dimensions (`CL,VS,CP,DV,GM,MI,LN,TC`) against [taxonomy-v2.json](references/taxonomy-v2.json), decomposing compound prompts into atomic cards and learning new trigger keywords.
2. **A2 · Vault, Palette & Pattern Ingest**: Store atomic cards into [vault/](references/vault), combine curated `--text`/`--file` with `--image` OCR, extract screenshot hex palettes via `--measure-palette` (`--archetype-ref VS-*`), persist `image_assets`, `source_urls`, `html_recipe_ref`, and add state-machine patterns (`idle -> intent -> commit/cancel -> settle`) to [catalog.json](references/catalog.json) and re-render [toolchain-20-matrix.md](references/toolchain-20-matrix.md). URL-only input creates a useful source card without network fetch.
3. **A3 · Distill Shared `L1` Laws (`One-In, One-Out`)**: Keep shared `L1` references ([routing-index.md](references/routing-index.md), [interaction-layering.md](references/interaction-layering.md), [appearance-ledger.md](references/appearance-ledger.md), [breathing-and-clarity.md](references/breathing-and-clarity.md), [modernization-dimensions.md](references/modernization-dimensions.md)) within `<=130` lines.
4. **A4 · Sink Generic Defect Cases & Usage Telemetry**: Register cross-project recurring cases into [defect-cases/index.json](references/defect-cases/index.json) and [eval-cases.json](references/eval-cases.json) via `register-case --id auto` / `skill_contract.py add-case --id auto`, and track pattern ratings via `record-use`.
5. **Video Mode (`M4`)**: Disclose [remotion-video.md](references/remotion-video.md) only when `--surface-mode video`; never load it for product UI.

```bash
python3 skills/design-studio/scripts/design_catalog.py classify --text "..." --learn
python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text "..." --image <shot.png> --measure-palette --archetype-ref VS-005 --url https://example.com/design
python3 skills/design-studio/scripts/design_catalog.py ingest --text "..."
python3 skills/design-studio/scripts/verify_design_cases.py register-case --id auto --title "..." --platforms desktop,mobile,web --symptom "..." --fix-rule "..."
python3 skills/design-studio/scripts/design_catalog.py record-use --id MP-002 --context "checkout" --rating liked
```
