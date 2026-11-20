---
name: issue-brief
description: "Format, render, and verify Multica and Linear issue bodies using the Dual-Zone Issue Format Contract (<=45 concrete human lines with TL;DR, rich numbers/tables, and anti-boilerplate checkable items above a collapsible AI context <details> block and <!-- multica-cursor:... --> watermark). Not for codebase PR reviews."
---

# issue-brief

Deterministic Dual-Zone formatting and verification contract for all Multica and Linear issue bodies. Keeps Owner triage rich, concrete, and instant while giving downstream AI agents lossless execution context.

## When-to-Read & CLI Routing

| Need | Read / run |
|---|---|
| Dual-Zone schema, concrete table & anti-boilerplate rules | [`references/dual-zone-spec.md`](references/dual-zone-spec.md) |
| Render structured JSON to compliant Dual-Zone Markdown | `python3 skills/issue-brief/scripts/verify_issue_format.py render --input <payload.json>` |
| Verify Markdown file or stdin (`0` pass, `2` violation) | `python3 skills/issue-brief/scripts/verify_issue_format.py verify <issue.md> --json` |
| Regression & routing cases | [`references/eval-cases.json`](references/eval-cases.json) |
| Upstream producers | `multica_loop_runner.py`, `multica_skills_autopilot.py`, `digest_vault.py`, `daily-brief.py` |

## `/howto` P7 Global 5-Tuple

- **Inbound**: Any producer writing Multica or Linear issue descriptions (`multica_loop_runner.py`, `multica_skills_autopilot.py`, `digest_vault.py`, `daily-brief.py`, or manual/automated issue drafting).
- **Internal**:
  - **Upper Zone — Human Zone (`上半区 · 给人看`)**: `<= 45` non-empty visible lines before `<details>`. Encourages concrete numbers, percentages, currency amounts, fund codes (`017811`), issue/PR IDs (`TIA-15`, `#196`), file/module names, and compact Markdown tables! Must contain `**一句话结论**：...` (or `**核心结论**：...`) plus distinct checkable `- [ ] 1. <具体对象+动作> — <具体指标/原因>` items. Rejects repetitive/boilerplate checklist items (`duplicate_boilerplate_items`), internal codes (`S1..S12`, `M1..M5`, `EV-001`, `DC-001`, `H1..H5`, `R10`, `V1..V5`), raw commit SHAs/UUIDs, and multi-line fenced code blocks or CLI invocations.
  - **Lower Zone — AI Context Zone (`下半区 · 给 AI 看`)**: Closed `<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>...</details>` holding `topic_key`, telemetry JSON payloads, reproduction & remediation CLI commands, and verification gates, paired with `<!-- multica-cursor:... -->` watermarks.
- **Calls**: None (Layer-0 leaf deterministic formatter & validator, 0 LLM tokens).
- **PointsTo**: Multica issues (`TIA-*`), Linear `TIA` issues, and CI gates (`skill_contract.py eval --strict`, `./scripts/verify`).
- **Trigger & Notify**: Invoked automatically before issue creation/update; violations exit `2` with structured JSON findings.

## Mandatory Gates

1. **Render via canonical helper**: Import `render_dual_zone_issue()` from `skills/issue-brief/scripts/verify_issue_format.py` instead of hand-concatenating ad-hoc Markdown headers.
2. **Pre-flight verify (`exit 0` vs `exit 2`)**: Call `verify_dual_zone_issue()` before `multica issue create/update` or Linear `create_issue/update_issue`. Never publish an unverified Dual-Zone body.
3. **HTML comment cursor exemption**: Watermarks (`<!-- multica-cursor:... -->`, `<!-- multica-skills-autopilot:... -->`, `<!-- GTT-LINEAR-DEDUP:... -->`) are invisible to readers and excluded from the 45-line Human Zone count.
4. **Anti-boilerplate enforcement**: Every `- [ ]` item must specify a unique concrete target and reason; duplicate actions or copy-pasted generic rationales trigger `duplicate_boilerplate_items` (`exit 2`).
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/dual-zone-spec.md` | `python3 skills/issue-brief/scripts/verify_issue_format.py --help` |
| verification | `references/dual-zone-spec.md` | `skill_contract.py eval --skill issue-brief --strict` |
