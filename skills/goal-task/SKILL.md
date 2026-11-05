---
name: goal-task
description: 进行开发、架构、规格规划、UI/UX 设计、调研、实现、Bug 修复、代码审查或 PR 交付前必须加载；按 Goal -> Task 通过 1-Hop 编排 Explorer -> Designer -> Writer -> Reviewer -> pr_monitor。
---
# Goal -> Task 执行合同（`goal-task`）

> 唯一技能名是 `goal-task`。旧目录 `goal-target-task` 已移除。

## Workflow Hub links

7 环中枢见 [`workflow-hub`](../workflow-hub/SKILL.md)；环 1 见
[`linear-board`](../linear-board/SKILL.md)，环 7a 见 [`retro-loop`](../retro-loop/SKILL.md)，
环 7b 见 [`story-craft`](../story-craft/SKILL.md)。本合同只负责环 4 的 Task 执行。

Linear Team `TIA` 的 Idea、Goal 绑定和 checklist 同步由
[`linear-board`](../linear-board/SKILL.md) 与 [`references/ledger.md`](references/ledger.md)
负责；`linear-sync.py` 失败不应阻塞本地交付，也不得把凭据写入仓库或 handoff。

## 两层定义与两档执行路由（`Goal -> Task`）

| 层 | 定义 | 完成 |
|---|---|---|
| Goal | 可观察结果、范围、不做项、验收命令与 Task DAG | 必需 Tasks done，端到端验收有证据 |
| Task | 一个垂直切片，显式 `Owned Paths`、接口、依赖与验收 | **1 Task = 1 Writer = 1 Worktree = 1 PR**，独立 V1–V5 审查 |

按真实接口和验收切片，不按文件数或页面机械拆分；大范围机械演进使用
`Expand -> Migrate -> Contract`。执行者不同、验收方式不同、可并行或需交接任一成立即拆 Task。
一个独立 bug 或 requested change 默认一个 Task；多个反馈只有 brief 带
`common_root_cause_evidence` 才可合并。

**Lite / Single-Task**：仅一个 Task、一个 PR、改动不超过 5 个文件、不改公共接口、
红线或依赖时，可用 PR 描述兼作台账。否则使用 Standard / Multi-Task DAG，任务简报放在
`~/Development/handoff/<goal>/briefs/<task>.md`，无依赖波次并行，有依赖按 DAG 顺序。

## 双阶段与双入口衔接

| 阶段 | 允许动作 | 禁止 |
|---|---|---|
| Phase 0 共创 | R11 只读探查、对齐决策、产出 `spec.md`/`design.md` 候选 | Owner GO 前创建业务 Worktree 或派 Writer |
| Phase 1 执行 | 冻结 spec，按 1-Hop 派 Writer 与异构 Reviewer，完成 PR 与 R10 | TL 亲自写多文件业务代码或自审 |

Phase 0 设计若涉及 UI，使用 **Explorer → Designer → Writer → CI Design Gate →
Cross-Vendor Reviewer**。`Designer` 是 `writes: handoff-only`；先核对
`shared-design-system`、`Package.resolved` 与 upstream primitives。`design.md` 必须声明
container-driven width budget、3-tier text 规则、状态矩阵、双外观和 scenario → component
& motion mapping；超过 `>15` 行 custom layout/chrome glue 且 upstream 缺失时，先拆
`shared-design-system` Task。细节归 [`my-designer`](../my-designer/SKILL.md) 与
[`my-designer/references/five-layer-craft.md`](../my-designer/references/five-layer-craft.md)。

CLI 与 Mac mini 常驻 Bot 均直接运行该固定流水线：
`Explorer → Designer → Writer → CI Design Gate → Cross-Vendor Reviewer`；
without requiring `艺术家` or any Grok Bot；
艺术家只可选 FYI，`非门禁`，不阻塞验收。TL 的 Bot 生命周期规则见
[`bot-tl-orchestration`](../bot-tl-orchestration/SKILL.md)。
设计者（Designer）首步加载 `skill("my-designer")`；派发使用
[`herdr-dispatch.sh`](scripts/herdr-dispatch.sh)，完成后由协调者 `herdr pane close`。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

[`scripts/goal-queue.py`](scripts/goal-queue.py) 在 `.queue.lock` 下原子更新队列和 ledger：

1. Batch Mode 用 `enqueue --batch-json`；按 `(repo, paths)` 冲突矩阵形成并行波次或串行链。
2. Hot-Append 不重复拉起 TL；用 `enqueue --task-id ... --paths ... --brief ...`，记录
   `EVT=HOT_APPEND`；TL 为 `idle` 或 `done` 时按合同唤醒并重跑 `next-wave` 直到 `drained: true`。
3. Tracked feedback manifest、supersession、acceptance evidence、Mandate 和
   `spec_sha` 校验以 [`references/ledger.md`](references/ledger.md) 为唯一事实源。

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

进入 `goal-task` 后会话保持 TL 合同。Bug 反馈最多执行 2–3 次只读探查；若需修改文件或
深入多文件分析，必须由 TL 派 Writer/Explorer。范围内追问可 steering；越界必须 handback，
不可先改范围外代码。

### CLI 原生 Goal 引擎挂载与状态同步

`ledger.md`、`briefs/<task>.md` 与 Git 是唯一事实源。Codex 用 `create_goal`/`update_goal`，
Copilot 用 `sql` 的 `todos`/`todo_deps`，Claude 用 TodoWrite 投影状态；只有验收、PR 交接和
R10 齐全才可 complete，`blocked`/`NEED_GO` 不得留在活动态。

## 合同角色与交接

TL 统一派发所有 Subagent；Writer、Reviewer 默认 **You Do Not Dispatch Subagents**。
Writer 实现、验证、commit、push、开 PR 并写完整 R10；Reviewer 与 Writer 使用不同家模型。
交接只传 brief 文件指针，包含背景、Owned Paths、Consumes / Produces、依赖、验收、
R10 路径和准确 head。Task 状态为 `planned → ready → in_progress → in_review → done`；
审查 FAIL 可回 `changes_requested → in_progress`，任何状态可 `blocked`/`cancelled`。

## 定义、冻结与缺口

R1 定义权归 Planner/TL；R2 交出即冻结；R3 缺口先作不触碰硬红线的 `EVT=RULING`，否则
重规划；R4/R5 变更由定义方按授权分级批准。四条硬红线是改变 Goal 验收、安全／隐私／
数据丢失、修改 CI 门禁、破坏性操作。R6 为 1-Hop；R7 核对 Owned Paths、接口、依赖和命令；
R8 维护状态与租约；R9 处理漂移和串行合并；R10 保留双 ID receipt；R11 只读探查。

`R10` 回执必须记录 Task/run/agent、合同与 spec SHA、模型、机器、commit SHA、验证命令、
审查结论和未完成项。任何未授权路径、范围扩张或验收缺口都应停止并回报，不以口头状态替代证据。

## 限定审查

### Spec-First Requirement Change Rule

已有代码或测试的产品需求/UI 变化以 `spec.md`、`design.md` 或 `docs/design-contract.md`
为唯一权威。`Explorer` 产出带 file:line 的 **Obsolete Test Manifest**；Designer/Planner
附 **Deprecated Test Clauses to Remove/Rewrite**；`Planner Reviewer` 核验后由 `Code Writer`
先迁移获授权的旧断言，再实现，`Code/UI Reviewer` 保留所有非 deprecated regression tests。

### V1–V5

- **V1** 双轴核对 Spec & Scope Compliance 与 Code Quality & Redlines，包含 Owned Paths、
  Interfaces、测试真实性、安全／隐私／数据丢失。
- **V2** 按引入来源阻塞；范围外改动、红线、安全问题和本次 diff 引入的失败均 FAIL。
  UI 物理门禁包括快照 `sips -g pixelWidth` 等于规格宽度乘缩放倍数、zero numeric
  truncation（禁止 `…`）、无 raw localization key、无意外折行、容器点击热区对等。不得以
  “followed the brief verbatim” 降级，均为 `V2 P1`。
- **Anti-Gaming Visual Harmony Gate** 禁止用 `.system(size: <11)` 或
  `minimumScaleFactor < 0.85` 逃避截断；详细品类规则仅见 designer reference。
- **V3** 首轮由原 Writer 修复；第二轮失败才升档，连续两轮仍 FAIL 则 blocked/重规划。
- **V4** CI 门禁照常；修改门禁须另行授权。
- **V5** 结论为 PASS、CONCERNS、WAIVED 或 FAIL；没有授权的范围扩张不能 WAIVED。

## CLI 用法与生命周期

统一优先使用 [`scripts/herdr-dispatch.sh`](scripts/herdr-dispatch.sh)，它负责显式
`herdr pane split --direction right|down`、白名单、Parent ≥ Child、派发和 bounded wait。
模型与工具只看 [`references/runtime-profiles.yaml`](references/runtime-profiles.yaml)；
事件和 R10 只看 [`references/ledger.md`](references/ledger.md) 与
[`references/bots-and-agents.md`](references/bots-and-agents.md)。

多 Goal 用 `goal-supervisor.py scan` / `check-pins` / `telemetry --write`；按
`30–60s` bounded slices 检查 `ZOMBIE_LEASE`、`BLOCKED_AGENT` 和 `ORPHAN_TL_WAIT`。
`/Volumes/SSD` 是父级 allowlist 基线；`good-des` 等短名必须按 ledger、cwd 和 TL 关系绑定。
通知桥只写 `blocked-<agent>-<ts>.md`，**通知桥 ≠ 解卡**；`GOAL_DONE 关窗与 blocked alerts 两条独立`。

Writer 本地验证、`git push`、R10 后立即返回，严禁 `gh run watch` 或长 sleep；CI 与合并由
`pr_monitor` 按 job 级 Fail-Fast 处理。确认 `PR_MERGED`、R10 和 `drained: true` 后才可
`git worktree remove`；未合并／暂停（HOLD）或 dirty 现场保留并报告 `Owner`。`GOAL_DONE`
后由协调者关闭 TL pane/workspace，保留 Worktree 不阻塞关窗。
