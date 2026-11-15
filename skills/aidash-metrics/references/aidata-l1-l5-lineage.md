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
- Inspector evidence is bounded to canonical layer/schema/adapter artifacts, excluding hidden/vendor/cache trees and escaping symlinks. L2 `clean/*.db` and an existing L3 `warehouse.db` use URI `mode=ro&immutable=1` and checkpointed table/column metadata only (no WAL/SHM creation); binary files are never decoded as text. With no generated DB/raw, adapter collection projections + clean DDL and `schema/warehouse.sql` support source-only `--check-contract` (not metric readiness). Missing/unreadable required collector, schema, SQL or Snapshot sources fail closed.
- L1 samples at most 128 records / 2 MB per shard and extracts JSON key shapes, not field-word matches in values. Budget-truncated records are never parsed; `limit_reached_shards` makes sampling limits explicit, and absent samples cannot grant readiness. Session/agent sources are scoped separately from git/repo/hygiene/Linear shards; eval additionally permits GTT/event sources. Bare `file_path` or `escape` in unrelated records cannot prove reference hits or eval effects. Reference collection needs explicit `reference_path`; scripts need both `script_path` and `duration_ms`; eval needs `eval_case`, `interception` and `escape` in one retained record/projection.
- Baseline ROI requires skill attribution (`skill_names` **or** `attributionSkill`/`attribution_skill`) plus independent input/output usage dimensions. `skills_roi`/`skill_roi` report names are not required raw aliases. Adapter AST keys reachable from `collect` and clean DDL are declared evidence (uncalled/normalize-only helpers are not raw projections); SQLite table/column groups are observed schema metadata. SQL identifiers exclude comments/literals; Snapshot AST keys recognize canonical `skills_daily`, `coverage_status` and `source_as_of`, not arbitrary source words or inferred detailed report sections.
- Per-layer coverage reports `observed`, `declared`, `partial` or `missing` with source pointers and conjunctive dimensions of alternative aliases. `ready` requires a complete observed L1 shape and complete downstream contracts. Declared L1 without observed retention is conservative amber when a source sample exists; empty/no eligible L1 is red. Bounded absence is not proof of global absence; rerun after approved collection/backfill. These are source/contract classifications, not population freshness, measured values or proof that detailed user-requested metrics already exist.

## Required gate order

1. Run `inspect_aidata_pipeline.py` against a real or hermetic root.
2. Lock human metric definitions and drill-down grain with the Owner.
3. Run `/my-designer` Stage 1 (`group/designer`, ten prompts), then Stage 2 (`group/coding`, persistent DOM gallery); freeze `design.md`.
4. Add and test source/SQL/producer/Snapshot contracts before SwiftUI Reader binding.

The inspector reads metadata, source text and fixture contents only; it never imports or executes discovered Python/SQL and never creates files beneath the supplied root.
