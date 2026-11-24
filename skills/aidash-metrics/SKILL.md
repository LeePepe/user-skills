---
name: aidash-metrics
description: "凡涉及新增、修改、诊断或重设计 AIDash 的 AI 指标、Skills 效能/ROI、Token/Context 消耗、工具调用或遥测看板卡片时必须加载；强制先探查与扩展 aidata (L1_raw -> L2_clean -> L3_model -> L4_serve SQL -> L5_apps Snapshot)，严禁跳过 aidata 直接在 UI 层硬编码或堆砌无意义的学术编号。"
---

# aidash-metrics

Use this skill for any AIDash AI-metric or telemetry-card change. It is an evidence-first contract, not a UI implementation recipe.

## When-to-Read & CLI Routing

| Need | Read / run |
|---|---|
| Physical lineage, grains, coverage | [`references/aidata-l1-l5-lineage.md`](references/aidata-l1-l5-lineage.md) |
| Deterministic discovery | `python3 skills/aidash-metrics/scripts/inspect_aidata_pipeline.py --aidata-root <root> --metrics <csv> --json` |
| Compact discovery aliases | `--root`, `--dir`, and `--aidata-dir` alias `--aidata-root`; default/`--summary` bounds oversized JSON, while `--full` restores raw lists |
| Metric acceptance cases | [`references/eval-cases.json`](references/eval-cases.json) |
| Layered model design | `/layered-data-warehouse` and its `SKILL.md` |
| Product/UI flow | UI handoff contract (consume `design.md`, no skill hop) |

## Four mandatory gates

1. **Aidata discovery (zero token).** Run the inspector before proposing a card. It checks physical L1–L5 evidence and reports `ready`, `needs_l2_l4_extension`, or `needs_l1_collection`. Unknown metrics and unreadable roots fail closed.
2. **P0 Owner metric gate.** Align human-readable definitions, null/coverage/window/freshness semantics, and branch or drill-down grain. Never turn missing collection into zero or expose internal academic IDs.
3. **Two-stage design gate.** `/my-designer` Stage 1 uses `group/designer` for ten prompts; Stage 2 uses `group/coding` for a persistent DOM gallery. Owner freezes the selected design in `design.md` before production UI work.
4. **Data-before-UI gate.** Implement and test aidata source, SQL/producer, and Snapshot contracts before Reader/SwiftUI binding. Keep observed values separate from estimates and preserve SSOT/privacy/redaction.

## Metric evidence rules

Treat `fact_turn.tool_calls` as a JSON tool-name column, not a per-call table. Keep `fact_gtt_task_run` and `fact_gtt_event` at their distinct grains. The ROI SQL is an extension surface; the canonical producer and Snapshot remain the source of truth. If L1 strips tool inputs or paths, classify the metric as collection work rather than inventing evidence.

No UI gallery, AIDash pipeline, private Session-log, credential, or live-database change belongs in this skill task. Use hermetic fixtures for regressions and keep all source reads read-only.

## Inspector output contract

The inspector is read-only and fail-closed. Invalid flags, missing roots, and unknown metrics exit 2 with no more than three stderr lines, including a pointer to `references/aidata-l1-l5-lineage.md`. JSON summaries stay below 15,000 bytes when oversized by truncating representative layer, sampling, and evidence lists while retaining `l1_total_count` and `l1_total_files`. Small fixture payloads are emitted unchanged; `--full` restores unlimited lists.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/aidata-l1-l5-lineage.md` | `python3 skills/aidash-metrics/scripts/inspect_aidata_pipeline.py --help` |
| verification | `references/aidata-l1-l5-lineage.md` | `skill_contract.py eval --skill aidash-metrics --strict` |
