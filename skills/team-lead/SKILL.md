---
name: team-lead
description: 进行任何开发、架构或规格规划、UI/UX 设计或界面重设计、数据/指标或代码库调研、实现、Bug 修复、代码审查或 PR 交付前必须加载；按单任务或多任务 Goal 通过 Steering -> Writer -> Reviewer + ci-monitor.py 编排。
---
# Goal -> Task 执行合同（`team-lead`）

> 唯一技能名是 `team-lead`。旧目录 `goal-target-task` 已移除，不再提供兼容安装路径或固定锚点。

## Workflow Hub & Linear Integration

总工作流中枢见 [`workflow-hub`](../workflow-hub/SKILL.md)，Idea 与状态看板见 [`linear-board`](../linear-board/SKILL.md)，双路复盘见 [`retro-loop`](../retro-loop/SKILL.md)，叙事产出见 [`story-craft`](../story-craft/SKILL.md)，推进路线图见 [`workflow-refine-roadmap.md`](references/workflow-refine-roadmap.md)。Linear Team `TIA` 映射 `1 Goal = 1 Issue`（Checklist 为 Tasks），由 `skills/team-lead/scripts/linear-sync.py` 同步 R8 状态，本地 [`ledger.md`](references/ledger.md) 为唯一权威源。读取本合同不授予额外委派或发布权限，事实问答与诊断不自动变成实施。

## 两层定义与单出口执行模型（`Goal -> Task`）

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal（`= 1 分支 / 最多 1 PR`） | 可观察的最终结果、整体范围与不做项；默认 **1 个 Goal 共用 1 个工作分支**，收口最多提 **1 个最终 PR** | 外部前提及 Tasks 依赖 DAG | 端到端验证通过；必需 Tasks 全部 `done`，完成 **1 次** Goal 级跨厂商审查（或复用仓库 PR 异构门禁） |
| Task（本地 `keep / drop` 切片） | 垂直演进切片（或 `Expand -> Migrate -> Contract`），声明 `Owned Paths`、`Interfaces: Consumes / Produces` 与本地验证命令 | 前置 Tasks | 本地验证全部通过，在 Goal 主分支连续 commit（并发临时 Worktree 经本地 `keep` 合回、`drop` 丢弃） |

- **废除碎 PR 洪流**：同 Goal 下多个 Tasks 默认在同一工作树/分支连续 commit；仅多 Writer 真并发或实验探索时才开临时子 Worktree，验证后本地 `keep`（`git merge`）合回 Goal 分支并立即销毁临时 Worktree，绝不为每个子任务单开 PR。关联反馈可在同分支连续修复统一验收；历史 `Target` 条目直接映射为 `Task`。
- **三档弹性执行模式**：
  - **Inline Mode（`≤3` 文件轻量改动，无长时编译阻塞）**：免派 `Writer`；由已进入后台 Herdr Space 与独立 Worktree（`~/Development/worktrees/<task>`）的 `Steering`（`<goal>-goal-tl-r1`）直接修改并跑通本地验证（严禁在 `M2-Dispatcher` 或主 checkout `main` 直改业务代码；macOS 构建安装 `/Applications/<App>.app` 首次须 `ask_user` 确认）。
  - **Single-Stream Mode（常规单流 Goal，多文件或 `xcodebuild` 耗时编译）**：共用 1 个分支/工作树，派 1 个 `Writer`（`group/coding`）后台完成编码与测试，收口执行 **1 次** 跨厂商 `Reviewer` 审查并交付唯一 PR。
  - **Parallel Wave Mode（跨仓或多模块真并发 Goal）**：在 `ledger.md` 与 `briefs/<task>.md` 定义无冲突波次；并行 `Writer` 在临时子 Worktree 开发验证后本地 `keep` 合回主分支并销毁子 Worktree。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

| 阶段 | 角色形态 | 触发信号 | 核心产出与边界 |
|---|---|---|---|
| **Phase 0：交互共创期** | **Co-Design 顾问态** | 探讨性问题或存在产品／交互／架构未决项 | 只读探查（R11）＋每次聚焦 1–2 个决策点对齐＋UI/UX 设计 → 落盘 `~/Development/handoff/<goal>/spec.md`；**未获 GO 前不改业务代码** |
| **Phase 1：自治执行期** | **Steering 执行/协调态** | Owner 明确拍板（GO）或范围清晰直接指令 | 锁定 `spec.md`（`Mandate`）→ `≤3` 文件直改或派 `Writer` 同分支推进，收口 **1 次** 异构审查＋Layer 0 `ci-monitor.py` 读回 |

- **`M2-Dispatcher` 只写简单 Spec 边界**：外层 M2 入口会话（交互式入口／常驻入口）作为 `M2-Dispatcher` **只允许创建/修改简单 Spec 与交接文件（`spec.md`、`design.md`、`ledger.md`、`plan.md`、`handoff/**`、`specs/*.md`、`briefs/*.md`），严禁修改任何业务代码、测试或配置**。凡涉及代码修改，必须调用 `skills/team-lead/scripts/herdr-dispatch.sh`（`--new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude`）在后台拉起绑定 `Magpie (:3425)` `group/steering` 的独立 `Steering`，在独立 Worktree 完成修改与提交。同步 shell 宿主环境启动后须同步运行 `skills/team-lead/scripts/goal-supervisor.py`（`watch --interval 15 --max-wait 600 --json`）盯完解卡与完结。
- **Goal 内唯一 `Steering` 铁律**：一旦进入 `<goal>-goal-tl-r1`，当前 `Steering`（TL）是该 Goal 唯一主控，**never spawns a second `Steering`** 或独立 `Planner`；仅内联完成 `≤3` 文件改动或派发叶子 `Writer`／`Reviewer` 并运行 Layer 0 `ci-monitor.py`。
- **Phase 0 允许动作**：① **只读探查（R11）**（直接读有界代码，`>8` 文件高噪声跨仓搜索才派 `Explorer` 附 `file:line`；UI 核对 `shared-design-system`／`DesignKit` `Package.resolved` 及 upstream primitives）；② **需求收敛**（`codebase-design`，含领域建模 `CONTEXT.md` 与 ADR）；③ **UI/UX 设计**（直接加载 `my-designer`，产出 `design.md`／`spec.md`，`writes: handoff-only`，遵循 Plan A + Plan C 与 `shared-design-system` upstream 规范）；④ **产出冻结候选**（落盘 `spec.md` 与 `briefs/<task>.md`）。
- **Phase 1 Worktree 与分屏**：一切代码修改在独立 Worktree 完成；分屏使用 `herdr pane split --pane "$HERDR_PANE_ID" --direction right|down --cwd "$TASK_WORKTREE" --no-focus`。`M2-Dispatcher` 协同与 `PR_MERGED`／`GOAL_DONE` 回收见 [`m2-dispatcher`](../m2-dispatcher/SKILL.md)。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

`skills/team-lead/scripts/goal-queue.py` 以 `.queue.lock` 原子更新 queue 与 ledger，按 `(repo, paths)` 形成并行／串行波次：Batch 走 `enqueue --batch-json` → `next-wave --mark-in-progress` → `set-status --status done` 直至 `drained: true`；已有 `<goal>-goal-tl-r1` 时 Hot-Append 只 `enqueue` 记 `EVT=HOT_APPEND`，TL 为 `idle`／`done` 时唤醒并补一次 `herdr agent send-keys <goal>-goal-tl-r1 enter`，收口前重跑 `next-wave`。Feedback-tracked 队列须提供 schema 1 manifest 与 `feedback_id` → acceptance 映射，supersession 保留全部原始 acceptance，详见 [`ledger.md`](references/ledger.md)。

### 会话粘性、上下文污染度阈值与原生 Goal 引擎挂载

- **Sticky TL & Context-Noise Threshold**：进入 `team-lead` 或存在活动 `ledger.md` 后追问自动继承合同。已知模块或 `≤8` 个文件由 `Steering` 直接读取诊断；跨仓搜索、全量 `Obsolete Test Manifest` 或长日志 `>8` 个文件时才经 `herdr-dispatch.sh` 派只读 `Explorer`（返回 `<15` 行摘要与 R10 receipt）。`≤3` 文件轻量修正由 `Steering` 直改，大范围或耗时构建交 `Writer`。
- **Native Goal Continuation Binding**：`ledger.md`、`briefs/<task>.md` 与 Git 为唯一事实源；Codex `create_goal`／`update_goal`、Copilot `sql` `todos`／`todo_deps`、Claude `TodoWrite` 仅作进程内投影。验收与 R10 完成后标 complete；遇 `TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮失败立即标 `blocked`。

## 合同角色（`Steering / Writer / Reviewer + Layer-0 脚本`）

| 合同角色 | 职责 |
|---|---|
| Steering / 主控（TL / `group/steering`） | 探查规划、加载 `my-designer` 写 `spec.md`／`design.md`、`≤3` 文件直改、依赖调度与内联裁决（`EVT=RULING`） |
| 探查者（Explorer，可选） | 仅在 `>8` 文件高噪声跨仓搜索时派发，只读核实事实（附 `file:line`）并写 handoff R10 回执（R11） |
| Task 写者（Writer / `writer_escalated`） | 多文件或耗时编译任务实现、本地验证与 commit（同分支连续推进或子 Worktree 本地 `keep` 合回后退出关窗）；严禁 `gh run watch` 或 `sleep >= 30`；首轮 `group/coding`，V3 第 2 轮启用 `writer_escalated` |
| 独立验收（Reviewer） | Goal 收口执行 **1 次** V1–V5 双轴跨厂商独立审查（若目标仓 PR CI 已运行 `codex-review-target` 则直接复用） |
| CI / 发布监控（`ci-monitor.py` / `pr_monitor`） | 默认后台运行 `skills/team-lead/scripts/ci-monitor.py`（Layer 0、0 Token），不为纯状态轮询启动 LLM；仅失败需 LLM 诊断时回退 `worker.pr_monitor`。Job 级 Fail-Fast 看 `failure`／`timed_out`／`action_required`，Run `in_progress` 时读 `actions/jobs/<id>/logs` |

技术栈到 Required Skills 的路由见 [`bots-and-agents.md`](references/bots-and-agents.md)；模型与启动参数见 [`runtime-profiles.yaml`](references/runtime-profiles.yaml)（历史归档见 [`model-roles.md`](references/model-roles.md)）。

## 定义、冻结与缺口

- **R1–R5 `Mandate (spec_sha)` 与两态裁决**：不推翻北极星目标且不触碰 4 条硬红线（① 改 Goal 核心目标、② 安全／隐私／数据丢失、③ 改 CI 门禁／规则集、④ 破坏性操作）的顺带文件增补或接口微调，由 `Steering` 直接记 `EVT=RULING` 放行；触线则暂停并发起 `EVT=NEED_GO`／`NEED_OWNER_DECISION`。
- **R6 1-Hop 扁平调度与文件指针交接**：`Steering` 统一派发 `Writer`、`Reviewer`、`Explorer`，下属不再递归派发（`You Do Not Dispatch Subagents`）；简报写 `~/Development/handoff/<goal>/briefs/<task>.md`，prompt 仅传路径，Subagent 写 R10 回执并回 `<15` 行结论。
- **R7 执行前一致性**：确认 `Owned Paths`、接口与验证命令真实可运行。
- **R8 状态机与租约**：Task `planned → ready → in_progress → done`（或 `in_review → changes_requested → in_progress`，可转 `blocked`／`cancelled`）；Goal `planning → in_progress → accepting → done`。租约超时按 git 状态＋R10 回执对账后接手。
- **R9 本地 `keep / drop` 与单出口交付**：串行 Tasks 同分支连续 commit；并发子 Worktree 验证后本地 `keep` 合回并清理（失败分支 `drop`），整个 Goal 统一提 1 个 PR。
- **R10 回执对账 & R11 只读探查**：R10 记录 commit SHA、验证结果与实际模型；`>8` 文件高噪声搜索派只读 `Explorer` 附 `file:line` 回报。
- **角色绑定与交接**：每次只沟通一层 Agent；Bot 只管 Goal TL 唤醒、解卡与 `GOAL_DONE` 关窗（保留 Worktree 不阻断关窗），TL 独占下属派发与回收；新 `Steering` 接手先按 R10、台账与 git 状态对账。

## 限定审查

- **Spec-First Requirement Change Rule**：需求或 UI/UX 变更以 `spec.md`／`design.md` 为唯一上游；`Steering`（或 `Explorer`）识别冲突旧断言，产出带 `file:line` 的 **Obsolete Test Manifest** 并在 `spec.md`／`design.md` 记录 **Deprecated Test Clauses to Remove/Rewrite**；`Code Writer` 先迁移旧断言再实现，`Code/UI Reviewer` 保留非 deprecated regression tests。
- **V1 双轴（Goal 收口单次审查）**：Axis 1 核对范围与规格，Axis 2 核对测试真实性、质量与安全红线（可复用仓库 `codex-review-target`）。
- **V2 按引入来源阻塞**：本 diff 引入的问题、验收失败、安全／隐私／数据丢失阻塞。UI 任务须过 `design_contract.py` 与 `my-designer` 门禁（含 `sips -g pixelWidth`、zero numeric truncation `…`、无 raw localization keys，且不得因 “followed the brief verbatim” 降级 V2 P1 `FAIL`）。独立 `Reviewer` 加载 `code-review` + `my-designer` 对代码与 R10 截图做 **Visual Traceback**，判责为 `[IMPL_BUG]`（直退 Writer 修复）或 `[SPEC_GAP]`（先由 `Steering` 修订 `design.md` 重签 `spec_sha` 再派 Writer；仅跨项目通用、零私有符号且同类容器必踩时经 `skill_contract.py sink-rule` 入库）。
- **V3–V5 复审、CI 与结论**：第 1 轮 FAIL 交原写者（或先补 `spec_sha`），第 2 轮启用 `writer_escalated`，两轮仍失败转 `blocked` 交 `Steering`／Owner 裁决；修改 CI 门禁须获 Owner 批准；结论分 `PASS`、`CONCERNS`、`WAIVED`、`FAIL`。

## CLI 用法与 UI Inner Loop

- **并发监督与网关路由**：用 `skills/team-lead/scripts/goal-supervisor.py`（`scan`、`status --check`、`check-pins`、`telemetry --handoff-dir <goal> --write`）监控，子 Agent 等待用 `30–60s` 有界 slices，检查 `ZOMBIE_LEASE`、`BLOCKED_AGENT` 与 R10 receipts（关联 `good-des-*`／`gd-e1-*`，基线祖先 `/Volumes/SSD`；回收执行 `herdr pane close "$HERDR_PANE_ID"`）。`skills/team-lead/scripts/herdr-dispatch.sh` 协同 `Magpie (:3425)` 调度（`--kind claude` 走 `direct_quota_gated`，超限自动改派 `copilot --model magpie/group/steering`），分屏必传 `--direction right|down`（如 `herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus`）。
- **Layer 0 监控与 Worktree 清理**：异步运行 `python3 skills/team-lead/scripts/ci-monitor.py workflow|pr`（不为纯状态轮询启动 LLM，仅失败诊断回退 `pr_monitor`）。`Writer` 干完即退；子 Worktree `keep` 合回或 PR 已 `PR_MERGED` 后用 `skills/team-lead/scripts/git-gc.py`（或 `git worktree remove`）清理；`GOAL_DONE` 关窗，未合并／暂停（HOLD）现场保留并报告 `Owner`。详见 [`bots-and-agents.md`](references/bots-and-agents.md)、[`m2-dispatcher`](../m2-dispatcher/SKILL.md)、[`playbook.md`](references/playbook.md) 与 [`ledger.md`](references/ledger.md)。
- **UI implementation inner loop**：调用 `python3 shared-ci/scripts/context/design_contract.py --audit-code --platform <desktop|mobile|web>`、`--audit-l10n`、`--check-edge-clipping`、`--audit-geometry`（禁走 `xcode-cli`），通过后导出真实截图（Apple: `XCTest` / `xcrun simctl io booted screenshot` / `ImageRenderer` 4 states × 2 appearances PNG; Web: Playwright headless screenshot）写入 R10 receipt，由 `Reviewer` 执行 **Visual Traceback** 判分 `[IMPL_BUG]` vs `[SPEC_GAP]`。
