---
name: team-lead
description: Goal/Task work must load this skill: coordinate Steering -> Writer -> Reviewer with handoff/<goal>/ledger.md, receipts/ R10, worktrees/, goal-supervisor.py, goal-queue.py, herdr-dispatch.sh, feedback_guard.py, and ci-monitor.py.
---
# Goal -> Task 执行合同（`team-lead`）

> 唯一技能名是 `team-lead`（旧目录 `goal-target-task` 已移除）。

## Role-Based Routing（按角色唯一必读抽屉 + 唯一必跑 CLI）

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: M1-TL | `references/ledger.md` | `goal-supervisor.py status --check` |
| Role: Designer | `references/bots-and-agents.md` | `herdr-dispatch.sh --role designer` |
| Role: Writer | `references/playbook.md` | `ci-monitor.py workflow` |
| Role: Reviewer | `references/playbook.md` | `feedback_guard.py pre-review-check` |

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| Goal state/lease | `references/ledger.md` | `goal-supervisor.py status --check` |
| implementation/review | `references/playbook.md` | `feedback_guard.py pre-review-check` |
| CI/release | `references/playbook.md` | `ci-monitor.py workflow` |

## Workflow Hub & Linear Integration

总流见 [`workflow-hub`](../workflow-hub/SKILL.md)，看板见 [`linear-board`](../linear-board/SKILL.md)，复盘见 [`retro-loop`](../retro-loop/SKILL.md)，叙事见 [`story-craft`](../story-craft/SKILL.md)，路线图见 [`workflow-refine-roadmap.md`](references/workflow-refine-roadmap.md)。`TIA` 为 `1 Goal = 1 Issue`；`ledger.md` 为 R8 权威源。

## 两层定义与单出口执行模型（`Goal -> Task`）

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal（`= 1 分支 / 最多 1 PR`） | 可观察结果、范围与不做项；共用 1 分支，最多提 **1 个最终 PR** | 外部前提及 Tasks DAG | 端到端验证通过；Tasks 全 `done`，完成 **1 次** 跨厂商审查 |
| Task（本地 `keep / drop`） | 垂直切片（或 `Expand -> Migrate -> Contract`），声明 `Owned Paths`、`Interfaces` 与验证 | 前置 Tasks | 本地验证通过，同分支连续 commit（并发子 Worktree 经 `keep` 合回、`drop` 丢弃） |

- **废除碎 PR 洪流**：多 Tasks 默认同分支连续 commit；仅真并发或实验开子 Worktree，验证后本地 `keep`（`git merge`）合回并销毁，禁为子任务单开 PR。
- **三档弹性执行模式**：**Inline Mode**（`≤3` 文件无长编译，独立 Worktree 内 `Steering` `<goal>-goal-tl-r1` 直改验证，禁在 `M2-Dispatcher` 或 `main` 直改；装 `/Applications/<App>.app` 首次须 `ask_user`）· **Single-Stream Mode**（单分支派 1 个 `Writer` `group/coding`，收口 1 次跨厂商审查提唯一 PR）· **Parallel Wave Mode**（无冲突并行子 Worktree 验证后本地 `keep` 合回销毁）。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

| 阶段 | 角色形态 | 触发信号 | 核心产出与边界 |
|---|---|---|---|
| **Phase 0：交互共创期** | **Co-Design 顾问态** | 有产品／交互／架构未决项 | 只读探查（R11）＋每次对齐 1–2 决策点＋UI 设计 → 落盘 `spec.md`；**未获 GO 不改代码** |
| **Phase 1：自治执行期** | **Steering 执行态** | Owner 拍板（GO）或直接指令 | 锁 `spec.md`（`Mandate`）→ `≤3` 文件直改或派 `Writer`，收口 1 次异构审查＋`ci-monitor.py` |

- **`M2-Dispatcher` 边界**：M2 只改 `spec.md`、`design.md`、`ledger.md`、`plan.md`、`handoff/**`、`briefs/*.md`；代码经 `herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude`，由 `goal-supervisor.py watch` 读回。协同回收见 [`m2-dispatcher`](../m2-dispatcher/SKILL.md)。
- **唯一 `Steering` 铁律**：进入 `<goal>-goal-tl-r1` 后当前 `Steering`（TL）为唯一主控，**never spawns a second `Steering`** 或独立 `Planner`；仅直改 `≤3` 文件或派叶子 `Writer`／`Reviewer` 并跑 Layer 0 `ci-monitor.py`。
- **Phase 0 动作与分屏**：① 探查（R11，`>8` 文件派 `Explorer` 附 `file:line`；UI 核对 `shared-design-system` upstream primitives）；② 收敛（`codebase-design`）；③ UI 设计（加载 `my-designer` 写 `design.md`／`spec.md`，`writes: handoff-only`，Plan A+C）；④ 冻结 `spec.md` 与 `briefs/<task>.md`。Phase 1 分屏：`herdr pane split --pane "$HERDR_PANE_ID" --direction right|down --cwd "$TASK_WORKTREE" --no-focus`。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

`skills/team-lead/scripts/goal-queue.py` 以 `.queue.lock` 原子同步 queue 与 ledger，按 `(repo, paths)` 推导波次：Batch 走 `enqueue --batch-json` → `next-wave --mark-in-progress` → `set-status --status done` 直至 `drained: true`；已有 TL 时 Hot-Append 只 `enqueue` 记 `EVT=HOT_APPEND`，TL `idle`／`done` 时唤醒，收口前重跑 `next-wave`。Feedback 队列传 schema 1 manifest，详见 [`ledger.md`](references/ledger.md)。

### 会话粘性、上下文污染度阈值与原生 Goal 引擎挂载

- **Sticky TL & Context-Noise Threshold**：进入 `team-lead` 或有活动 `ledger.md` 后追问继承合同。`≤8` 文件由 `Steering` 直读；跨仓、全量 `Obsolete Test Manifest` 或长日志 `>8` 文件经 `herdr-dispatch.sh` 派只读 `Explorer`（回 `<15` 行摘要与 R10）。`≤3` 文件直改，大范围交 `Writer`。
- **Native Goal Binding**：`ledger.md`、`briefs/<task>.md` 与 Git 为唯一事实源；`create_goal`、`todos`／`todo_deps`、`TodoWrite` 仅作进程内投影。验收与 R10 后标 complete；遇 `TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮败即标 `blocked`。

## 合同角色（`Steering / Writer / Reviewer + Layer-0 脚本`）

| 合同角色 | 职责 |
|---|---|
| Steering / 主控（TL / `group/steering`） | 探查规划、加载 `my-designer` 写 `spec.md`／`design.md`、`≤3` 文件直改、调度与裁决（`EVT=RULING`） |
| Designer（`designer` / `group/designer`） | 仅 UI Demo 探索期产出 10 个原型 Prompt（`v01..v10`／`app01..app04`）；不接管 Spec，不写生产代码 |
| 探查者（Explorer，可选） | `>8` 文件高噪声搜索时只读核实事实（附 `file:line`）并写 R10 回执（R11） |
| Task 写者（Writer / `writer_escalated`） | 多文件/耗时实现、验证与 commit（同分支或子 Worktree `keep` 合回退出）；守 **5s Foreground-to-Background Rule**（`>5s` 走后台/异步，禁 `gh run watch` 或 `sleep >= 30`）；超界 `set-status --status handback`；首轮 `group/coding`，V3 第 2 轮 `writer_escalated` |
| 独立验收（Reviewer） | Goal 收口执行 **1 次** V1–V5 双轴跨厂商审查（若 PR CI 已跑 `codex-review-target` 则复用） |
| CI / 发布监控（`ci-monitor.py` / `pr_monitor`） | 默认后台跑 `skills/team-lead/scripts/ci-monitor.py`（Layer 0、0 Token），不为纯状态轮询启动 LLM；仅失败诊断回退 `pr_monitor` |

参考索引：[`bots-and-agents.md`](references/bots-and-agents.md)、[`ledger.md`](references/ledger.md)、[`playbook.md`](references/playbook.md)、[`playbook-herdr-sop.md`](references/playbook-herdr-sop.md)、[`playbook-narrative-extract.md`](references/playbook-narrative-extract.md)、[`playbook-retro-loop.md`](references/playbook-retro-loop.md)、[`workflow-refine-roadmap.md`](references/workflow-refine-roadmap.md)、[`runtime-profiles.yaml`](references/runtime-profiles.yaml)（Script-Only Data Drawer — query via L2 CLI, never full-file view）、[`operator-policy.json`](references/operator-policy.json)、[`dispatch-recipes.json`](references/dispatch-recipes.json)、[`eval-cases.json`](references/eval-cases.json)。

## 定义、冻结与缺口

- **R1–R5 `Mandate (spec_sha)` 与两态裁决**：不触 4 红线（① 改核心目标、② 安全／隐私／数据丢失、③ 改 CI 门禁、④ 破坏性操作）的顺带增补记 `EVT=RULING` 放行；触线暂停并发 `EVT=NEED_GO`／`NEED_OWNER_DECISION`。
- **R6 1-Hop 扁平调度**：`Steering` 统一派发，下属禁递归派发；简报写 `briefs/<task>.md`，prompt 仅传路径，下属写 R10 回 `<15` 行结论。
- **R7 一致性 & R8 状态租约**：确认 `Owned Paths`、接口与验证可跑；Task `planned → ready → in_progress → done`（或 `in_review → changes_requested`、`blocked`／`cancelled`），Goal `planning → in_progress → accepting → done`，超时按 git＋R10 对账接手。
- **R9 本地 `keep / drop` 单出口**：串行同分支 commit；并发子 Worktree 验证后 `keep` 合回清理（败则 `drop`），全 Goal 提 1 个 PR。
- **R10 回执、Pre-Review 门禁 & R11 探查**：R10 记 SHA、验证与模型；派审前跑 `skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree <wt> --brief <brief.md> [--receipt <r10.md>]`（评测集 [`eval-cases.json`](references/eval-cases.json)，经 `skill_contract.py eval` 跑，禁直接查看）；`>8` 文件派只读 `Explorer`。每次只沟通一层 Agent；Bot 管唤醒解卡与 `GOAL_DONE` 关窗。

## 限定审查

- **Spec-First Requirement Change Rule**：需求或 UI 变更以 `spec.md`／`design.md` 为上游；`Steering`／`Explorer` 产 **Obsolete Test Manifest** 并记 **Deprecated Test Clauses to Remove/Rewrite**；`Code Writer` 先迁断言再实现，`Code/UI Reviewer` 护非废弃回归测试。
- **V1 双轴 & V2 来源阻塞**：Axis 1 核范围规格，Axis 2 核测试真实性、质量与安全红线；本 diff 问题、验收失败、安全红线阻塞。UI 须过 `design_contract.py` 与 `my-designer` 门禁（含 `sips -g pixelWidth`、zero numeric truncation `…`、无 raw keys，禁以 “followed the brief verbatim” 降级 V2 P1 `FAIL`）。`Reviewer` 做 **Visual Traceback** 判 `[IMPL_BUG]`（退 Writer）或 `[SPEC_GAP]`（`Steering` 修订 `design.md` 重签 `spec_sha`；通用规则经 `skill_contract.py sink-rule` 入库）。
- **V3–V5 复审、CI 与结论**：第 1 轮 FAIL 原写者修，第 2 轮用 `writer_escalated`，仍败转 `blocked`；改 CI 门禁须 Owner 批准；结论 `PASS`、`CONCERNS`、`WAIVED`、`FAIL`。

## CLI 用法与 UI Inner Loop

- **并发监督与网关路由**：用 `skills/team-lead/scripts/goal-supervisor.py`（`scan`、`status --check`、`check-pins`、`telemetry`）以 `30–60s` slices 监控 `ZOMBIE_LEASE`、`BLOCKED_AGENT` 与 R10（关联 `good-des-*`，基线 `/Volumes/SSD`；回收 `herdr pane close "$HERDR_PANE_ID"`）。`skills/team-lead/scripts/herdr-dispatch.sh` 协同 `Magpie`，分屏必传 `--direction right|down`（`herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus`）。
- **Layer 0 监控与清理**：异步跑 `ci-monitor.py workflow|pr`（0 Token，失败回退 `pr_monitor`）。`PR_MERGED` 后 `git worktree remove`；`GOAL_DONE` 关窗，未合并／暂停（HOLD）现场保留并报 `Owner`。
- **UI inner loop**：跑 `design_contract.py --audit-code`、`--audit-l10n`、`--check-edge-clipping`、`--audit-geometry`，导出截图（`ImageRenderer` / `simctl` PNG 或 Playwright）写 R10，由 `Reviewer` 做 **Visual Traceback**（`[IMPL_BUG]` vs `[SPEC_GAP]`）。
