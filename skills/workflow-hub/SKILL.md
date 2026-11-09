---
name: workflow-hub
description: 总工作流开放能力图谱：把 Linear Idea、Phase 0 共创、Goal/Task 开发、PR 交付、产品发布、双路复盘与叙事产出连接成可验收闭环。
---
# Workflow Hub

`workflow-hub` 是总工作流中枢，不替代任何能力节点的专项合同。它负责选择入口、
传递冻结的接口与证据，并把节点结果送给下一个需要它的节点；专项规则仍以各 skill
为唯一事实源。

## 如何按需让 CLI / Agent 加载总 Workflow（On-Demand Invocation）

`workflow-hub` 只在修改或审查总 Workflow 时按需加载；正常编码期间不会产生
idle context overhead。Copilot CLI / Claude Code 使用 `/workflow-hub`，或输入
“加载 workflow-hub ...”（`skill("workflow-hub")`）；Codex CLI 使用
`$workflow-hub`，或输入 “use workflow-hub skill ...”。

各 CLI 的 skill 目录保持同步：`~/.agents/skills`、`~/.copilot/skills`、
`~/.codex/skills`、`~/.claude/skills`。

## 开放式能力节点与按需直连图谱（Open Capability Graph）

图谱不规定固定数量或串行顺序；当前挂载的能力节点包括：

- **Idea & Goal State** — `linear-board`：TIA `Triage`/`Backlog`、`1 Goal = 1 Issue`、
  Task checklist 与 `Promote Idea -> Goal`。
- **Co-Design** — `deep-research`、`domain-modeling`、`codebase-design`、
  `my-designer`、`visual-design-modernization`、`design-studio`：收敛事实、领域、
  架构与视觉决策，冻结 `spec.md`/`design.md`。
- **Autonomous Build, Review & Merge** — `m2-dispatcher`、`team-lead`、
  `code-review` 与 `ci-monitor.py`（`pr_monitor`）：完成 R1–R11、V1–V5、审查、Job-level Fail-Fast 与合法合并。
- **Product Delivery & Release** — 产品／共享底座交付及真实发布读回。
- **Learning & Narrative** — `retro-loop` 与 `story-craft`：把项目／工程证据和脱敏
  叙事回流为下一轮 Idea 或 Goal。

节点可以按证据和状态任意直连，例如 `linear-board -> domain-modeling -> team-lead`、
`team-lead -> code-review`，或
`team-lead -> retro-loop -> linear-board`；不要求经过未相关的节点。所有直连共享四类
标准合同：冻结决策使用 `spec.md`/`design.md`，执行状态使用 `ledger.md`，交接证据使用
`receipts/*-rN.md`，Goal／Task 绑定使用 Linear `TIA-xx`。

## 自治脉搏：Cron / launchd 定时调度（Precheck-First, Wake-on-Actionable）

### 核心原则（零 Token 确定性预检 → 按需唤醒）

1. **Step 1 — 定时触发与时间窗守卫（Cron / `launchd`）**：通过
   `~/Library/LaunchAgents/*.plist`（例如 `StartInterval=300`，门控工作日 `09:00–22:00 Asia/Shanghai`）启动定时预检脚本。
2. **Step 2 — 确定性预检（Deterministic Precheck，0 Token）**：先运行纯
   Python / Shell 预检（如 `precheck.py` 或 `goal-supervisor.py scan`）。若
   `pending == 0`（无待审新 SHA、CI 失败/冲突或阻塞 Agent），直接 `exit 0`，
   **不唤醒任何 LLM / Bot**。
3. **Step 3 — 精准唤醒与环路派发（Targeted Wake）**：仅当 `pending > 0` 时，
   通过 `wake-pr-sentry-webhook.sh` 或 `herdr agent prompt` 唤醒对应常驻 Bot /
   Sticky TL，进入对应工作流节点。

### 定时回路与能力图谱映射

| 定时回路 | 驱动节点 | 触发与动作 |
|---|---|---|
| PR 自动审查与盯梢 | 环 5 | `local.pr-auto-review` 与 `local.my-pr-supervisor-precheck` 扫描 GHE / GitHub / ADO PR；未审 head SHA、CI 失败、评论或冲突时唤醒审查或 Sticky TL。 |
| 多 Goal 租约与阻塞巡检 | 环 3 & 环 4 | `goal-supervisor.py scan --write-alerts` 扫描活动 `ledger.md` 与 Herdr 窗格；发现 `BLOCKED_AGENT` / `ZOMBIE_LEASE` / `ORPHAN_TL_WAIT` 时写告警并唤醒协调者。 |
| 遥测/反馈巡检与任务回流 | 环 7 → 环 1 | 定时拉取性能、Crash、DSAT 与周期维护信号；退化时通过 `linear-board`（`linear-sync.py`）写入 `TIA` 看板，触发下一轮 Goal。 |

## 4-layer product/output matrix

| 层 | 关注对象 | 代表输入 | 代表输出与读回 |
|---|---|---|---|
| 1. Products & Apps | `VitalStride`、`VoxPocket`、`WebIQSearch`、Sapphire | 用户旅程、设计契约、真实数据 | 功能、E2E、PLT/FPS、Crash-Free、DSAT |
| 2. Shared Foundations | `shared-design-system`、`shared-telemetry`、`shared-ci` | Token、遥测、质量契约 | 组件、契约、遥测链、6DQ、版本摘要 |
| 3. Engineering Workflows | `m2-dispatcher`、`team-lead`、PR、发布 | brief、R10、Git、CI | 准确 head、Task 状态、门禁与发布读回 |
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

Canonical companions:
- [`seven-rings-and-progressive-6dq.md`](references/seven-rings-and-progressive-6dq.md)
- [`his-fm-nocoo-study-and-roadmap.md`](references/his-fm-nocoo-study-and-roadmap.md)（顶层 `workflow-hub` 架构演进总纲、his.fm & `nocoo`/Multica 全量资料对标，以及按 **Sub-Workflow 0–5** 拆分的接口适配规范与专项会话启动 Prompt）
- [skills-drift-check.py](scripts/skills-drift-check.py)
- [otter-workspace.template.json](references/otter-workspace.template.json)
