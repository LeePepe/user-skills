---
name: knowledge-digest
description: 面向日常学习、知识获取与好内容拆解沉淀的通用技能。当用户想快速搞懂一个知识/概念/原理、拆解一段优质科普/技术视频脚本或文章、或说「帮我讲懂 / 了解一下 / 拆解这篇内容 / 沉淀这个知识点 / knowledge digest」时使用。内置双通道自适应信息获取与「极简 TL;DR + FAQ + 立即可用清单」输出。
user-invocable: true
---

# Knowledge Digest

把主动求知和素材拆解合成一条高信噪比流水线：精准获取 → 反常识尺度与生活教具 → 个人知识库沉淀。不要以百科式定义开场。

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| card shape | `references/templates.md` | `digest_vault.py precheck-card --file <md>` |
| vault search | `references/templates.md` | `digest_vault.py search --query "<question>" --json` |
| topic discovery | `references/card-templates.json` | `digest_vault.py discover-topics --top 3 --no-materialize` |
| evaluation | `[eval-cases.json](references/eval-cases.json)` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `skill_contract.py eval --skill knowledge-digest --strict` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `references/templates.md` | `digest_vault.py precheck-card --file <md>` |
| Writer | `references/card-templates.json` | `digest_vault.py archive --slug <slug> --category <cat> --title <title> --file <md>` |
| Reviewer | `references/templates.md` | `skill_contract.py eval --skill knowledge-digest --strict` |
| M1-TL | `references/templates.md` | `digest_vault.py reconcile-state` |

## Two channels

| Channel | Trigger | Required capture |
|---|---|---|
| A · Topic/Question | concept, mechanism, or phenomenon | first principles, 1–3 scale numbers, pain-point/误区, 1–3 actions |
| B · Content/URL/Script | article, video, transcript, or script | knowledge card plus narrative/visual technique card and one background check |

## Output contract

1. TL;DR: an anti-intuitive conclusion plus one concrete scale.
2. FAQ: 3–5 pain points, each with a life-object analogy and one key number.
3. Action checklist: no more than three immediately usable actions.
4. For Channel B, add hook → concept → scale → visualization → misconception → action narrative notes.

Store concepts in `concepts/`, craft in `craft/`, raw material in `raw/`, and update `index.md` under `~/Development/Personal/knowledge-vault/`. `discover-topics` remains candidate-only; it reads `topic-ledger.json` and keeps `design-studio` ingestion isolated; Owner approval is required before archive, publish, or any design-data promotion. Keep design ingestion isolated and do not auto-sync it.

```bash
python3 skills/knowledge-digest/scripts/digest_vault.py search --query "<question>" --top-k 5 --json
python3 skills/knowledge-digest/scripts/digest_vault.py precheck-card --file <md> --require-index
python3 skills/knowledge-digest/scripts/digest_vault.py discover-topics --top 3 --no-materialize
python3 skills/knowledge-digest/scripts/digest_vault.py reconcile-state
```
