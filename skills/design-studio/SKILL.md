---
name: design-studio
description: "Entry A of the Dual-Entry Design Flow: ingest, classify, and stockpile design inspirations, measured palettes, interaction/motion patterns, and video assets."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# design-studio — Entry A: Ingest, Stockpile & Video (`A0..A4`)

`design-studio` is **Entry A (`Ingest & Stockpile`)** of the Dual-Entry Design Flow (see [design-flow-architecture.md](references/design-flow-architecture.md)). Use it to classify and stockpile external design inspirations, reference apps, measured screenshot palettes, WWDC/HIG notes, interaction/motion patterns (`IP-*/MP-*`), reusable defect cases (`DC-*`), and `Video/Offline` Remotion pipelines.

For product UI design, existing screen modernization (`No Code Demo`), `design.md`/`spec.md` authoring, and post-implementation screenshot verification (`Visual Traceback`), use **Entry B (`my-designer`)** (see [../my-designer/SKILL.md](../my-designer/SKILL.md)). App/screen scaffold inputs and gate receipts follow [navigation-evidence-contract.md](../my-designer/references/navigation-evidence-contract.md).

`scaffold-gallery` mounts ephemeral screen/app-nav rounds to `~/Development/leepepe.github.io/demos/<goal>/` with a latest-round redirect. After B3 freezes the direction, or when the Goal completes, immediately run `design_catalog.py cleanup-demo --goal <goal>`; it removes blog and handoff demo rounds while preserving receipts.

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
