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

Repeated issues require one concrete lock, not a reminder: Skill/Config lock, Hook/Script lock, or `6DQ CI / Gateway Schema lock`. Preserve S1–S4 and S6–S12 keys; missing samples remain `null`/`unknown`, and estimated S11/S12 proxies are never presented as measured savings. Standalone 3-stage Skill Progressive Self-Evolution (`probe -> triage -> emit-briefs`) lives in [`skill-retro`](../skill-retro/SKILL.md).

## Deterministic CLI

```bash
python3 skills/retro-loop/scripts/retro_story_engine.py discover
python3 skills/retro-loop/scripts/retro_story_engine.py agent-efficiency --auto-propose
python3 skills/retro-loop/scripts/retro_story_engine.py skill-roi --auto-propose
python3 skills/retro-loop/scripts/trace_backfill_exporter.py --target both --limit-goals 35 --limit-sessions 55 --json
```

Trace backfill uses recorded execution telemetry, injected skill contexts, and structured receipt `- Tokens:` headers—not path mentions or recommendations. Copilot tool start/completion pairs provide measured durations; point events use a 1 ms display width with `timing.measured=false` and `timing.provenance=point_event_unmeasured` in attributes/output. Generations use all token-bearing rows from the first available source (Copilot usage → usage JSONL → warehouse). Missing process exits stay unknown even when tool transport fails. Five-category presence is coverage, not proof of measured timing or complete history.

`proposals.jsonl.lock` serializes idempotent writes. `learning-topic://` remains a separate Owner-approved exploration path; Star never auto-publishes or syncs design data. Implementations live in `retro_story_engine.py`, `multica_skills_autopilot.py`, and `trace_backfill_exporter.py` (with `skill-retro` delegated to `skills/skill-retro/scripts/skill_retro.py`).

## Verification drawer

[`eval-cases.json`](references/eval-cases.json) is the Script-Only Regression Drawer: run it via `skill_contract.py eval --skill retro-loop --strict`; do not view the full JSON.
