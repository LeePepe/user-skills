---
name: workflow-hub
description: 总工作流 7 环中枢：把 Linear Idea、Phase 0 共创、Goal/Task 开发、PR 交付、产品发布、双路复盘与叙事产出串成可验收闭环。
---
# Workflow Hub

`workflow-hub` 是总工作流中枢，不替代任何环的专项合同。它负责选择入口、
传递冻结的接口与证据，并把每一环的结果送到下一环；专项规则仍以各环 skill
为唯一事实源。

## 7-Ring lifecycle

1. **环 1 — `linear-board`**：把想法放入 TIA `Triage`/`Backlog`，维护
   `1 Goal = 1 Issue` 的全局状态看板、Task checklist、阻塞评论与
   `Promote Idea -> Goal`。
2. **环 2 — Phase 0 Co-Design**：用 `deep-research`、`domain-modeling`、
   `codebase-design`、`my-designer`、`visual-design-modernization`、
   `motion-studio` 与 `archify` 收敛事实、领域、架构和视觉决策，冻结
   `spec.md`/`design.md`。
3. **环 3 — `bot-tl-orchestration`**：Resident Bot（`Hotty`、`Joy`、
   `Atlas`、`flower`、`艺术家`、`ana`、`笔者`）与 Herdr Goal TL 之间只传
   Goal 生命周期、队列、解卡和收尾状态。
4. **环 4 — `goal-task`**：执行纯开发切片，遵守
   `1 Task = 1 Writer = 1 Worktree = 1 PR`、R1–R11 和 V1–V5。
5. **环 5 — PR Review/Merge**：用 `code-review`、`pr-auto-review`、
   `my-pr-supervisor`、`auto-review-merge` 与 `pr_monitor` 完成审查、Job-level
   Fail-Fast 盯梢和合法自动合并。
6. **环 6 — Product Delivery/Release**：按 Layer 1/2 产品与共享底座交付，
   使用 `sync_contract.py`、`design_contract.py`、`release_sync.py readback`、
   TestFlight 与 FlightBook 五面读回。
7. **环 7 — `retro-loop` + `story-craft`**：将项目回路 A、工程回路 B、三道
   物理锁和脱敏叙事产出回流环 1，形成下一轮 Idea 或 Goal。

## 4-layer product/output matrix

| 层 | 关注对象 | 代表输入 | 代表输出与读回 |
|---|---|---|---|
| 1. Products & Apps | `VitalStride`、`VoxPocket`、`WebIQSearch`、Sapphire | 用户旅程、设计契约、真实数据 | 功能、E2E、PLT/FPS、Crash-Free、DSAT |
| 2. Shared Foundations | `shared-design-system`、`shared-telemetry`、`shared-ci` | Token、遥测、质量契约 | 组件、契约、遥测链、6DQ、版本摘要 |
| 3. Engineering Workflows | `01–06`、Goal/Task、PR、发布 | brief、R10、Git、CI | 准确 head、Task 状态、门禁与发布读回 |
| 4. Knowledge & Narrative | 复盘、Story Series、Tech Blog、Deck | 证据、量化 Delta、脱敏映射 | 可迁移机制、公开作品、反馈回流 |

## Progressive 6DQ Discovery Ladder

6DQ 是帮助 Agent 在开发过程中由浅入深、渐进式发现问题的诊断阶梯
（**Progressive Discovery Ladder**），而不是在一开始就束缚 Agent 的前置一刀切
教条（**Not an upfront all-at-once constraint**）。

1. **Stage 1 — D1（动工轻检：环境与 Worktree 隔离）**：只做秒级分支、路径、
   依赖和隔离确认，让 Agent 放心探索编码。
2. **Stage 2 — L1（内环快反：单测与静态检查）**：完成最小代码切片后立刻跑局部
   单测、语法和静态检查，第一时间暴露函数级逻辑错误。
3. **Stage 3 — L2（边界探查：本地真实集成）**：L1 通过后逐步替换 mock，对接真实
   序列化、本地存储和接口边界，发现单测掩盖的契约偏差。
4. **Stage 4 — G2（交接扫描：安全与契约对齐）**：准备提 PR 时运行秘密扫描、
   权限审计、`design_contract.py` 与 `sync_contract.py --check`。
5. **Stage 5 — L3 + Release Readback（外环终验：端到端旅程与线上读回）**：合入与
   发布阶段验证关键用户旅程 E2E、`release_sync.py readback`、`/api/live`、
   `SHA256SUMS` 与 TestFlight 真实读回。

## Handoff rules

- Ring 之间只传文件指针、精确接口、准确 head、验证命令和 R10 receipt。
- 6DQ 阶梯按风险和交付阶段递进；早期失败先在最近的环内修复，不把所有后置
  检查伪装成开发前置硬门槛。
- 每次复盘和发布读回都必须能回指一个 Goal/Task 或新的 TIA Idea。

Canonical companion: [`seven-rings-and-progressive-6dq.md`](references/seven-rings-and-progressive-6dq.md)
