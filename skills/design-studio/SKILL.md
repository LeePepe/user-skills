---
name: design-studio
description: "Entry A of Dual-Entry Design Flow: ingest, classify, stockpile design vault inspirations, measured hex palettes (--measure-palette), 8D taxonomy-v2 cards, catalog interaction/motion patterns, Web galleries, and Remotion video. Not for design.md/spec.md, UI audits, Visual Traceback (my-designer), or knowledge-digest."
allowed-tools: Read, Write, Edit, Bash, Skill
---

# design-studio — Entry A

Entry A ingests and stockpiles inspiration, palettes, interaction patterns, defect cases, and Video/Offline assets. Product UI modernization, `design.md`/`spec.md`, and Visual Traceback belong to Entry B (`my-designer`); this entry never implements product code.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Read exactly one L1 drawer | Run exactly one L2 CLI |
|---|---|---|
| classify or query inspiration | `references/eval-cases.json` | `python3 skills/design-studio/scripts/design_catalog.py --query <keyword> --top 3` |
| video/offline export | `references/remotion-video.md` | `python3 skills/design-studio/scripts/design_catalog.py scaffold-gallery --platform web --goal <goal> --round 1` |
| toolchain matrix/render | `references/toolchain-20-matrix.md` | `python3 skills/design-studio/scripts/design_catalog.py render --check` |

## Role-Based Routing（按角色唯一必读抽屉 + 唯一必跑 CLI）

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `references/eval-cases.json` | `design_catalog.py --query <keyword> --top 3` |
| Writer | `references/toolchain-20-matrix.md` | `design_catalog.py render --check` |
| Reviewer | `references/eval-cases.json` | `skill_contract.py eval --skill design-studio --strict` |
| M1-TL | `references/eval-cases.json` | `design_catalog.py classify --text <text> --no-jev` |

## Workflow

1. Classify `desktop|mobile|web` across `CL,VS,CP,DV,GM,MI,LN,TC` with `scripts/data/taxonomy-v2.json`.
2. Ingest cards into `scripts/data/vault/`; update reusable patterns/tools in `scripts/data/catalog.json`. Query these dictionaries through the CLI; do not load raw JSON as an agent shortcut.
3. For native Apple previews use `PreviewHostKit`, Xcode `#Preview`, and 120Hz; Web HTML is only for Web platform work. The default gallery root is `leepepe.github.io/demos/<goal>`.
4. Register generic cases through `my-designer/scripts/verify_design_cases.py register-case` and evaluate this entry through `references/eval-cases.json`.
5. Load [`remotion-video.md`](references/remotion-video.md) only for `Video/Offline`; use [`toolchain-20-matrix.md`](references/toolchain-20-matrix.md) for matrix/render work.

```bash
python3 skills/design-studio/scripts/design_catalog.py classify --text "..." --no-jev
python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text "..." --image <shot.png> --measure-palette
python3 skills/design-studio/scripts/design_catalog.py --query "gesture" --top 3 --platform mobile
python3 skills/design-studio/scripts/design_catalog.py render --check
```
