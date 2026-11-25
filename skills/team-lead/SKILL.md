---
name: team-lead
description: Goal/Task work must load this skill: coordinate Steering -> Writer -> Reviewer with handoff/<goal>/ledger.md, receipts/ R10, worktrees/, goal-supervisor.py, goal-queue.py, herdr-dispatch.sh, feedback_guard.py, and ci-monitor.py.
---
# Goal -> Task 执行合同（`team-lead`）

> 唯一技能名是 `team-lead`。旧目录 `goal-target-task` 已移除，不再提供兼容安装路径或固定锚点。

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

总工作流见 [`workflow-hub`](../workflow-hub/SKILL.md)，看板见 [`linear-board`](../linear-board/SKILL.md)，复盘见 [`retro-loop`](../retro-loop/SKILL.md)，叙事见 [`story-craft`](../story-craft/SKILL.md)，路线图见 [`workflow-refine-roadmap.md`](references/workflow-refine-roadmap.md)。`TIA` 为 `1 Goal = 1 Issue`；`ledger.md` 是 R8 权威源。

## 两层定义与单出口执行模型（`Goal -> Task`）

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal（`= 1 分支 / 最多 1 PR`） | 可观察的最终结果、整体范围与不做项；默认 **1 个 Goal 共用 1 个工作分支**，收口最多提 **1 个最终 PR** | 外部前提及 Tasks 依赖 DAG | 端到端验证通过；必需 Tasks 全部 `done`，完成 **1 次** Goal 级跨厂商审查（或复用仓库 PR 异构门禁） |
| Task（本地 `keep / drop` 切片） | 垂直演进切片（或 `Expand -> Migrate -> Contract`），声明 `Owned Paths`、`Interfaces: Consumes / Produces` 与本地验证命令 | 前置 Tasks | 本地验证全部通过，在 Goal 主分支连续 commit（并发临时 Worktree 经本地 `keep` 合回、`drop` 丢弃） |

- **废除碎 PR 洪流**：同 Goal 多 Tasks 默认在同一工作树/分支连续 commit；仅多 Writer 真并发或实验探索时才开临时子 Worktree，验证后本地 `keep`（`git merge`）合回 Goal 分支并立即销毁临时 Worktree，绝不为子任务单开 PR。关联反馈同分支连续修复统一验收；历史 `Target` 映射为 `Task`。
- **三档弹性执行模式**：
  - **Inline Mode（`≤3` 文件轻量改动，无长时编译阻塞）**：免派 `Writer`；由后台 Herdr Space 独立 Worktree（`~/Development/worktrees/<task>`）中的 `Steering`（`<goal>-goal-tl-r1`）直改并本地验证（禁在 `M2-Dispatcher` 或主 checkout `main` 直改代码；安装 `/Applications/<App>.app` 首次须 `ask_user` 确认）。
  - **Single-Stream Mode（常规单流 Goal，多文件或 `xcodebuild` 耗时编译）**：共用 1 分支/工作树，派 1 个 `Writer`（`group/coding`）后台编码测试，收口执行 **1 次** 跨厂商 `Reviewer` 审查并交付唯一 PR。
  - **Parallel Wave Mode（跨仓或多模块真并发 Goal）**：在 `ledger.md` 与 `briefs/<task>.md` 定义无冲突波次；并行 `Writer` 在临时子 Worktree 开发验证后本地 `keep` 合回主分支并销毁子 Worktree。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

| 阶段 | 角色形态 | 触发信号 | 核心产出与边界 |
|---|---|---|---|
| **Phase 0：交互共创期** | **Co-Design 顾问态** | 探讨性问题或存在产品／交互／架构未决项 | 只读探查（R11）＋每次聚焦 1–2 个决策点对齐＋UI/UX 设计 → 落盘 `~/Development/handoff/<goal>/spec.md`；**未获 GO 前不改业务代码** |
| **Phase 1：自治执行期** | **Steering 执行/协调态** | Owner 明确拍板（GO）或范围清晰直接指令 | 锁定 `spec.md`（`Mandate`）→ `≤3` 文件直改或派 `Writer` 同分支推进，收口 **1 次** 异构审查＋Layer 0 `ci-monitor.py` 读回 |

- **`M2-Dispatcher` 只写简单 Spec 边界**：M2 只改 `spec.md`、`design.md`、`ledger.md`、`plan.md`、`handoff/**`、`briefs/*.md`，严禁改代码、测试、配置；代码须经 `herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude`。独立 Worktree 完成后由 `goal-supervisor.py watch --interval 15 --max-wait 600 --json` 读回。
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
| Designer（`designer` / `group/designer`） | 仅 UI Demo 探索阶段独立产出 10 个原型 Prompt（`v01..v10` 或 `app01..app04`）；不接管 Steering 的 Spec/Design ownership，也不实现生产代码 |
| 探查者（Explorer，可选） | 仅在 `>8` 文件高噪声跨仓搜索时派发，只读核实事实（附 `file:line`）并写 handoff R10 回执（R11） |
| Task 写者（Writer / `writer_escalated`） | 多文件或耗时编译任务实现、本地验证与 commit（同分支连续推进或子 Worktree 本地 `keep` 合回后退出关窗）；遵守 **5s Foreground-to-Background Rule**（耗时 `>5s` 的编译/全量测试如 `xcodebuild` 必走后台/异步或短时间片，严禁阻塞 Worker 实时接收 `steer`/停止信号，严禁 `gh run watch` 或 `sleep >= 30`）；超 `Owned Paths` 追问执行 `goal-queue.py set-status --status handback --reason "..."` 退回 TL；首轮 `group/coding` 按 Designer Prompt 实现 HTML 原型/生产代码，V3 第 2 轮启用 `writer_escalated` |
| 独立验收（Reviewer） | Goal 收口执行 **1 次** V1–V5 双轴跨厂商独立审查（若目标仓 PR CI 已运行 `codex-review-target` 则直接复用） |
| CI / 发布监控（`ci-monitor.py` / `pr_monitor`） | 默认后台运行 `skills/team-lead/scripts/ci-monitor.py`（Layer 0、0 Token），不为纯状态轮询启动 LLM；仅失败需 LLM 诊断时回退 `worker.pr_monitor`。Job 级 Fail-Fast 看 `failure`／`timed_out`／`action_required`，Run `in_progress` 时读 `actions/jobs/<id>/logs` |

参考索引：[`bots-and-agents.md`](references/bots-and-agents.md)、[`ledger.md`](references/ledger.md)、[`playbook.md`](references/playbook.md)、[`playbook-herdr-sop.md`](references/playbook-herdr-sop.md)、[`playbook-narrative-extract.md`](references/playbook-narrative-extract.md)、[`playbook-retro-loop.md`](references/playbook-retro-loop.md)、[`workflow-refine-roadmap.md`](references/workflow-refine-roadmap.md)、[`runtime-profiles.yaml`](references/runtime-profiles.yaml)（Script-Only Data Drawer — query via L2 CLI, never full-file view）、[`operator-policy.json`](references/operator-policy.json)、[`dispatch-recipes.json`](references/dispatch-recipes.json)、[`eval-cases.json`](references/eval-cases.json)。

## 定义、冻结与缺口

- **R1–R5 `Mandate (spec_sha)` 与两态裁决**：不推翻北极星目标且不触碰 4 条硬红线（① 改 Goal 核心目标、② 安全／隐私／数据丢失、③ 改 CI 门禁／规则集、④ 破坏性操作）的顺带文件增补或接口微调，由 `Steering` 直接记 `EVT=RULING` 放行；触线则暂停并发起 `EVT=NEED_GO`／`NEED_OWNER_DECISION`。
- **R6 1-Hop 扁平调度与文件指针交接**：`Steering` 统一派发 `Writer`、`Reviewer`、`Explorer`，下属不再递归派发（`You Do Not Dispatch Subagents`）；简报写 `~/Development/handoff/<goal>/briefs/<task>.md`，prompt 仅传路径，Subagent 写 R10 回执并回 `<15` 行结论。
- **R7 执行前一致性**：确认 `Owned Paths`、接口与验证命令真实可运行。
- **R8 状态机与租约**：Task `planned → ready → in_progress → done`（或 `in_review → changes_requested → in_progress`，可转 `blocked`／`cancelled`）；Goal `planning → in_progress → accepting → done`。租约超时按 git 状态＋R10 回执对账后接手。
- **R9 本地 `keep / drop` 与单出口交付**：串行 Tasks 同分支连续 commit；并发子 Worktree 验证后本地 `keep` 合回并清理（失败分支 `drop`），整个 Goal 统一提 1 个 PR。
- **R10 回执对账、Pre-Review 防呆门禁 & R11 只读探查**：R10 记录 commit SHA、验证结果与实际模型；派 `Reviewer` 前须跑 `python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree <wt> --brief <brief.md> [--receipt <r10.md>]`（拦截越界文件、个人路径／密钥泄漏、未提交改动与残缺 R10，评测集见 [`eval-cases.json`](references/eval-cases.json)（Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view））；`>8` 文件高噪声搜索派只读 `Explorer` 附 `file:line` 回报。
- **角色绑定与交接**：每次只沟通一层 Agent；Bot 只管 Goal TL 唤醒、解卡与 `GOAL_DONE` 关窗（保留 Worktree 不阻断关窗），TL 独占下属派发与回收；新 `Steering` 接手先按 R10、台账与 git 状态对账。

## 限定审查

- **Spec-First Requirement Change Rule**：需求或 UI/UX 变更以 `spec.md`／`design.md` 为唯一上游；`Steering`（或 `Explorer`）识别冲突旧断言，产出带 `file:line` 的 **Obsolete Test Manifest** 并在 `spec.md`／`design.md` 记录 **Deprecated Test Clauses to Remove/Rewrite**；`Code Writer` 先迁移旧断言再实现，`Code/UI Reviewer` 保留非 deprecated regression tests。
- **V1 双轴（Goal 收口单次审查）**：Axis 1 核对范围与规格，Axis 2 核对测试真实性、质量与安全红线（可复用仓库 `codex-review-target`）。
- **V2 按引入来源阻塞**：本 diff 引入的问题、验收失败、安全／隐私／数据丢失阻塞。UI 任务须过 `design_contract.py` 与 `my-designer` 门禁（含 `sips -g pixelWidth`、zero numeric truncation `…`、无 raw localization keys，且不得因 “followed the brief verbatim” 降级 V2 P1 `FAIL`）。独立 `Reviewer` 加载 `code-review` + `my-designer` 对代码与 R10 截图做 **Visual Traceback**，判责为 `[IMPL_BUG]`（直退 Writer 修复）或 `[SPEC_GAP]`（先由 `Steering` 修订 `design.md` 重签 `spec_sha` 再派 Writer；仅跨项目通用、零私有符号且同类容器必踩时经 `skill_contract.py sink-rule` 入库）。
- **V3–V5 复审、CI 与结论**：第 1 轮 FAIL 交原写者（或先补 `spec_sha`），第 2 轮启用 `writer_escalated`，两轮仍失败转 `blocked` 交 `Steering`／Owner 裁决；修改 CI 门禁须获 Owner 批准；结论分 `PASS`、`CONCERNS`、`WAIVED`、`FAIL`。

## CLI 用法与 UI Inner Loop

- **并发监督与网关路由**：用 `skills/team-lead/scripts/goal-supervisor.py`（`scan`、`status --check`、`check-pins`、`telemetry --handoff-dir <goal> --write`）监控，子 Agent 等待用 `30–60s` 有界 slices，检查 `ZOMBIE_LEASE`、`BLOCKED_AGENT` 与 R10 receipts（关联 `good-des-*`／`gd-e1-*`，基线祖先 `/Volumes/SSD`；回收执行 `herdr pane close "$HERDR_PANE_ID"`）。`skills/team-lead/scripts/herdr-dispatch.sh` 协同 `Magpie (:3425)` 调度（`--kind claude` 走 `direct_quota_gated`，超限自动改派 `copilot --model magpie/group/steering`），分屏必传 `--direction right|down`（如 `herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus`）。
- **Layer 0 监控与 Worktree 清理**：异步跑 `ci-monitor.py workflow|pr`（0 Token，仅失败回退 `pr_monitor`）。`Writer` 完成后，`keep` 合回；`PR_MERGED` 后用 `git worktree remove` 清理；`GOAL_DONE` 关窗，未合并／暂停（HOLD）现场保留并报 `Owner`。跨端同步由 `AIDashSync` 负责；详见 `bots-and-agents.md`、`playbook.md`、`ledger.md`。
- **UI implementation inner loop**：调用 `python3 shared-ci/scripts/context/design_contract.py --audit-code --platform <desktop|mobile|web>`、`--audit-l10n`、`--check-edge-clipping`、`--audit-geometry`（禁走 `xcode-cli`），通过后导出真实截图（Apple: `XCTest` / `xcrun simctl io booted screenshot` / `ImageRenderer` 4 states × 2 appearances PNG; Web: Playwright headless screenshot）写入 R10 receipt，由 `Reviewer` 执行 **Visual Traceback** 判分 `[IMPL_BUG]` vs `[SPEC_GAP]`。
