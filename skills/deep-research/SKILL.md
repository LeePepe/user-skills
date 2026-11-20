---
name: deep-research
description: "Use for deep, multi-source research with citations and adversarial verification. Break questions into subproblems, cross-check sources, grade confidence, and synthesize disagreements. Not for quick factual lookups."
user-invocable: true
---

# Deep Research

A provider-agnostic pipeline for precise, cited, adversarially checked research. Discovery and verification are separate roles; every claim is traceable to a source.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| source selection | `references/source-tiers.json` | `jev_research_helper.py route --topic-type <type> --depth <depth>` |
| citation verification | `references/verification-protocol.md` | `jev_research_helper.py flow-map` |
| report synthesis | `references/report-template.md` | `jev_research_helper.py check-citations --input <file>` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `references/channel-playbooks.md` | `jev_research_helper.py route --topic-type general --depth quick` |
| Writer | `references/verification-protocol.md` | `jev_research_helper.py check-citations --input <file>` |
| Reviewer | `references/cross-model-verification.md` | `jev_research_helper.py flow-map` |
| M1-TL | `references/report-template.md` | `skill_contract.py eval --skill deep-research --strict` |

## Pipeline

1. Clarify missing budget, geography, use, comparison baseline, or time range; ask 2–3 questions when needed.
2. Decompose into 2–3 focused questions for quick, 4–6 for standard, or 6–8 for deep, with web/GitHub/news/academic channels.
3. Fan out findings with URL, date, source type, claim, and provisional confidence. Keep child output self-contained and bounded.
4. Independently try to falsify each finding. Use `CONFIRMED`, `REFUTED`, or `UNCERTAIN` verdicts.
5. Cross-check channel agreement, label `HIGH`, `MEDIUM`, `LOW`, or `CONTESTED`, and synthesize disagreements without inventing them.
6. Report executive summary, confidence table, evidence gaps, limitations, and complete citations.

R09 uses `0.65 * p_direct + 0.35 * (s_tier / 3.0)`; R06 quote failures are hard stops. Without independent verification, confidence is capped at MEDIUM. If no child CLI is available, run serially and state the downgrade.

References: `channel-playbooks.md`, `verification-protocol.md`, `cross-model-verification.md`, `report-template.md`, and `source-tiers.json` are disclosed only when their trigger matches.
