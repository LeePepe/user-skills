# Aidata L1–L5 lineage

This drawer records the observed AIDash source contract. Physical names are authoritative; `L1_raw`, `L2_clean`, and `L3_model` are conceptual aliases.

| Layer | Physical evidence | Grain / contract |
|---|---|---|
| L1_collect / L1_raw | `L1_collect/raw/` dated redacted JSONL; adapter and raw writer | Per source record/file offset. Claude adapter strips tool-input and path details before persistence. |
| L2_normalize / L2_clean | `L2_normalize/clean/` source DBs and adapter clean tables | Source-local turn UUID with idempotent last-write-wins rebuild. |
| L3_merge / L3_model | `L3_merge/warehouse.db`, warehouse schema | `fact_turn` is one turn UUID; `tool_calls` is a JSON tool-name column. `fact_gtt_task_run` and `fact_gtt_event` have separate receipt/event grains. |
| L4_serve | `L4_serve/queries/retro/skill_roi_diagnostics.sql` | Derived ROI and review/escape diagnostics. Existing SQL does not prove per-file hits or per-script latency. |
| L5_apps | `L5_apps/digest/skills_roi_snapshot.py` | Canonical producer report plus diagnostics, metadata, coverage, window and freshness; atomic JSON replacement. |
| Consumer | AIDash `LiveDashboardTelemetryReader.swift` | Reads the Snapshot and treats missing/decode failure as unavailable, not zero. |

## Metric extension rules

- Preserve SSOT, null, coverage, window, sample and freshness semantics. Separate observed values from estimates.
- Prompt → L0 → `references/*.md` needs retained source/path evidence and a modeled file-hit fact; a sanitized `tool_calls` list is insufficient.
- Prompt → L0 → `scripts/*.py` needs an honest per-call fact for count, mean/P95 milliseconds and outcome; do not infer latency from aggregate ROI rows.
- `eval-cases.json` and `fact_gtt_event` can support interception/escape only when their event grain and coverage are explicit.
- `skills_roi_snapshot.py` consumes the canonical producer report and `skills/daily`; it is not a direct consumer of ROI SQL.
- Missing L1 collection is a red state, while retained but unmodeled records are an amber L2–L4 extension.

## Required gate order

1. Run `inspect_aidata_pipeline.py` against a real or hermetic root.
2. Lock human metric definitions and drill-down grain with the Owner.
3. Run `/my-designer` Stage 1 (`group/designer`, ten prompts), then Stage 2 (`group/coding`, persistent DOM gallery); freeze `design.md`.
4. Add and test source/SQL/producer/Snapshot contracts before SwiftUI Reader binding.

The inspector reads metadata, source text and fixture contents only; it never imports or executes discovered Python/SQL and never creates files beneath the supplied root.
