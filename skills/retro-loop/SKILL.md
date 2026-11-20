---
name: retro-loop
description: 结合 aidata 数仓（L1–L4）发现产品与工程异常，定位 H1–H5 根因与 3 Physical Locks，写入 proposals.jsonl 展示在 AIDash 上，并通过 Star/Done 事件回流 Linear TIA。
---
# Retro Loop（aidata 发现 → AIDash 展示 → Linear 回流）

`retro-loop` reads local `aidata` L1–L4 signals, writes actionable proposals to L5 `proposals.jsonl`, displays them in AIDash, and returns approved Star/Done events to Linear TIA. It does not replace Owner decisions.

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| anomaly/ROI query | `SKILL.md` | `retro_story_engine.py discover` |
| skill progressive retro | `SKILL.md` | `retro_story_engine.py skill-retro --auto-propose --json` |
| proposal lifecycle | `SKILL.md` | `retro_story_engine.py agent-efficiency --auto-propose` |
| trace backfill / observability | `SKILL.md` | `trace_backfill_exporter.py --target both --dry-run` |
| verification | `SKILL.md` | `skill_contract.py eval --skill retro-loop --strict` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Designer | `SKILL.md` | `retro_story_engine.py discover` |
| Writer | `SKILL.md` | `retro_story_engine.py agent-efficiency --auto-propose` |
| Reviewer | `SKILL.md` | `skill_contract.py eval --skill retro-loop --strict` |
| M1-TL | `SKILL.md` | `retro_story_engine.py skill-roi --auto-propose` |

## Data flow

1. L1–L3 collect Goal ledgers, receipts, gateway bills, CI, worktree hygiene, and session traces into the warehouse.
2. L4 queries cover Goal scorecards, bot/TL operations, CI noise, workspace reclaim, release delivery, crash/performance, skill ROI, context composition, and tool-context burn.
3. `Project Loop A` covers product/UI/build and TestFlight signals; `Engineering Loop B` locates H1–H5: impossible acceptance, drift, dispatch/permission, implementation/test, or missing gate/readback.
4. Emit a complete proposal with source window, observation time, sample coverage, root cause, and physical lock. AIDash renders four buckets; `linear-board` receives only approved Star events.

## 3 Physical Locks

Repeated issues require one concrete lock, not a reminder: Skill/Config lock, Hook/Script lock, or `6DQ CI / Gateway Schema lock`. Preserve S1–S4 and S6–S12 keys; missing samples remain `null`/`unknown`, and estimated S11/S12 proxies are never presented as measured savings.

## 4-Layer Progressive Skill Retro (`skill-retro`)

`retro_story_engine.py skill-retro [--skill <name>] [--emit-briefs <handoff_dir>] [--auto-propose] [--json]` evaluates skills across `warehouse.db`, `~/.copilot/session-state/*/events.jsonl`, `~/.local/share/skill-telemetry/usage.jsonl`, and exported traces:
1. **Layer 0 (Direction & Triggers)**: Separates trigger misses (domain edits without loading the skill) and directional drifts from healthy `R1 -> R2` convergence, plus zero-action loads.
2. **Layer 1 (Context-Aware Drawer Metabolism)**: Cross-checks `references/*` hits against scenario occurrence to distinguish **Hot Drawers**, **On-Demand Correction Drawers**, **Unwired Disconnected Drawers** (domain/script failed without reading the drawer), and **True Zero-Hit Deadwood** (never deleting low-frequency scenario docs).
3. **Layer 2 (Script Efficacy & Multi-Angle Speed)**: Classifies `scripts/*.py|.sh` into **True Catchers (`fail -> pass`)**, **Ceremonial Rubber-Stamps (`100% exit=0` with escapes)**, **Rigid Roadblocks (`exit=2` CLI argument friction with failing samples)**, and **Latency Sinks (`>180s` or `P50 >= 30s` attributed across `external_wait`, `serial_queuing`, `nested_reruns`, and `heavy_full_scan` + 4 speedup axes)**.
4. **Layer 3 (Wild Crystallization & Dual Flywheel)**: Detects repeated manual shell probes (`>=5x`) for soft advisory crystallization (`exit=0`, self-disclosing error), harvests `preview-selection` `2+1+1` taste diversity, and enforces **Fast Flywheel Scope Triage** vs **Slow Flywheel (`>=50` sessions)** cohort gating before queuing to `proposals.jsonl` / `skills-roi.json`.
5. **Role-Slot Reuse (`--emit-briefs`)**: Reuses standard `M1 TL -> Writer -> Reviewer` slots (zero new agent roles) by emitting per-skill isolated Writer & Reviewer briefs enforcing Scope Triage, self-disclosing errors, and Positive + Counter-Case Replay.

## Deterministic CLI

```bash
python3 skills/retro-loop/scripts/retro_story_engine.py discover
python3 skills/retro-loop/scripts/retro_story_engine.py agent-efficiency --auto-propose
python3 skills/retro-loop/scripts/retro_story_engine.py skill-roi --auto-propose
python3 skills/retro-loop/scripts/retro_story_engine.py skill-retro --skill my-designer --emit-briefs ~/Development/handoff/<goal> --json
python3 skills/retro-loop/scripts/retro_story_engine.py skill-retro --auto-propose --json
python3 skills/retro-loop/scripts/trace_backfill_exporter.py --target both --limit-goals 35 --limit-sessions 55 --json
```

Trace backfill uses recorded execution telemetry, injected skill contexts, and structured receipt `- Tokens:` headers—not path mentions or recommendations. Copilot tool start/completion pairs provide measured durations; point events use a 1 ms display width with `timing.measured=false` and `timing.provenance=point_event_unmeasured` in attributes/output. Generations use all token-bearing rows from the first available source (Copilot usage → usage JSONL → warehouse). Missing process exits stay unknown even when tool transport fails. Five-category presence is coverage, not proof of measured timing or complete history.

`proposals.jsonl.lock` serializes idempotent writes. `learning-topic://` remains a separate Owner-approved exploration path; Star never auto-publishes or syncs design data. Implementations live in `retro_story_engine.py`, `skill_retro_diagnostic.py`, `multica_skills_autopilot.py`, and `trace_backfill_exporter.py`.
