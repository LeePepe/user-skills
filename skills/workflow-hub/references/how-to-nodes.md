# 可组合 How-To 节点图谱

L1 只描述节点独有 Internal；复用通过 Calls，完成后通过 PointsTo 交接。每个节点遵循 `Inbound → Internal → Calls → PointsTo → Trigger/Notify` 五元组；Composite 只编排，不抄子节点实现。

## §1 四层归位

| Layer | Scope |
|---|---|
| L1 | `~/Development/user-skills/skills`: workflow、研究、设计、审查、复盘、叙事 |
| L2 | `~/Development/Skills/skills`: repo、数仓、家居、工具 |
| L3 | `~/Development/job skills`: 工作专项 |
| L4 | `<repo>/.claude/skills/`: 仓内构建、资源、多语言、发版 |

## §2 标准节点目录

### Group A：canonical nodes

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| E1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E3 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E4 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E4a | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E5 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E6 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E7 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E8 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E9 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E10 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E11 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| E12 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| Q1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| Q2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| Q3 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
### Group B：canonical nodes

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| I1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I3 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I4 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I5 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I6 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| I7 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| G1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| G2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
### Group C：canonical nodes

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| P1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P3 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P4 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P5 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P6 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P7 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P8 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| P9 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O1 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O2 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O3 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O4 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O5 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |
| O6 | Manual/Pulse | node-owned contract | child evidence | parent receipt | repair gate |

## §4 Broken-edge repair清册（TK-01..TK-16）

| ID | Edge | Trigger/Notify | Owned Paths | Acceptance Gate |
|---|---|---|---|---|
| TK-01 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-02 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-03 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-04 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-05 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-06 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-07 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-08 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-09 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-10 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-11 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-12 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-13 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-14 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-15 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |
| TK-16 | restore declared edge | Owner/Pulse | owning skill | deterministic pass/fail |

Required guardrail rows: `S1` relative Markdown links, `S2` relative git symlinks, `S3` ≤25L `_CANONICAL_PATH` wrappers.

## §5 Routing and sink rules

Thin Internal stays in this graph; thick domain knowledge, CLI/API, or deterministic gates become a skill with `scripts/` gates. Use `skill_contract.py audit --all --strict`, `sync-jev-stars`, `description-eval`, and `sink-rule`. L0 routes to L1 drawers, then L2 commands; no automatic cross-vault sync or Star publication.

## §6 High-value recipes

- `P9/P2 → P1 → E7 → E4`: aidata snapshot → AIDash card → UI evidence → Goal task.
- `I1 → I3 → I4 → I5 → I6` (`TK-08`): Teams ingest → classify → summarize → Owner notify → grounded reply; no blind resend.
- `G1/E2/E10/I3/I5/P7`: Root Tracker deduplicates Goal/Task/PR evidence; Linear is the only writable tracker.
- `aidash-metrics` handles 厚领域流程; `howto` remains the thin workflow entry.

Validation: `python3 -m unittest scripts/test_m2_router.py`, `python3 skills/howto/scripts/howto_guard.py check-nodes`, and `./scripts/verify --all`.
