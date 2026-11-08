---
name: team-lead
description: 进行任何开发、架构或规格规划、UI/UX 设计或界面重设计、数据/指标或代码库调研、实现、Bug 修复、代码审查或 PR 交付前必须加载；按单任务或多任务 Goal 通过 Steering -> Writer -> Reviewer + ci-monitor.py 编排。
---
# Goal -> Task 执行合同（`team-lead`）

> 唯一技能名是 `team-lead`。旧目录 `goal-target-task` 已移除，不再提供兼容安装路径或固定锚点。

## Workflow Hub links

总工作流 7 环中枢见 [`workflow-hub`](../workflow-hub/SKILL.md)；
环 1 Idea 与状态看板见 [`linear-board`](../linear-board/SKILL.md)；
环 7a 双路复盘见 [`retro-loop`](../retro-loop/SKILL.md)；
环 7b 叙事产出见 [`story-craft`](../story-craft/SKILL.md)。本合同只负责环 4
开发切片执行，跨环交接使用精确接口、准确 head 和 R10 receipt。

## Linear workflow integration

Linear Team `TIA` maps one Goal to one Issue; Tasks remain its checklist.
Use `linear-sync.py` for Idea／Goal binding and `sync-ledger` for R8 state
projection. The local `ledger.md` remains authoritative; credentials stay in
the environment. See [`linear-board`](../linear-board/SKILL.md) and
[`ledger.md`](references/ledger.md) for the command contract.

本合同是 CLI 主会话与 subagent 的共同来源；读取它不授予额外角色、
委派或发布权限，事实问答和诊断不自动变成实施。

## 两层定义与单出口执行模型（`Goal -> Task`）

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal（`= 1 分支 / 最多 1 PR`） | 可观察的最终结果、整体范围和不做项；默认 **1 个 Goal 共用 1 个工作分支**，收口时最多提 **1 个最终 PR**（或无保护分支本地门禁通过直推） | 外部前提及 Tasks 之间的依赖 DAG | 每项整体验收对应端到端验证命令；必需 Tasks 全部 `done`，完成 **1 次** Goal 级跨厂商审查（或复用仓库 PR 异构门禁） |
| Task（本地 `keep / drop` 切片） | 属于一个 Goal 的垂直演进切片（或 `Expand -> Migrate -> Contract` 步骤），声明 `Owned Paths`、`Interfaces: Consumes / Produces` 与本地验证命令 | 前置 Tasks | 本地验证命令全部通过，在 Goal 主分支连续 commit（或并发临时 Worktree 经本地 `keep` 合回、`drop` 丢弃） |

- **废除 `1 Task = 1 Worktree = 1 PR` 与碎 PR 洪流**：遵循 `nocoo` 准则（*Do not use it to produce volume. Mass pull requests cost more than they give*），同一个 Goal 下的多个 Tasks 默认在**同一工作树/分支**上连续 commit 推进，避免每次重建 SwiftPM/Xcode 编译缓存与重复等待远端 CI。仅当**多 Writer 真并发写不重叠模块**或**实验探索（`evolve`）**时才按需开临时子 Worktree，验证通过后在本地 `keep`（`git merge`）合回 Goal 分支并立即销毁临时 Worktree，绝不为每个子任务单独开 GitHub PR。
- **原始反馈归并**：同一 Goal 或同一模块下的多个关联反馈/小缺陷可在同一 Goal 分支中连续修复并统一验收，无需按单条小反馈机械拆出多个独立 PR。
*旧版兼容说明：既有台账或 issue 中遗留的 `Target` 条目，直接映射为单个 `Task`（或同一 `Goal` 下的一组 `Tasks`）。*

### 三档弹性执行模式（Inline / Single-Stream / Parallel Wave）

`Steering`（TL 主会话）按改动体量与耗时自适应分流：

- **Inline Mode（`≤3` 个文件的轻量改动／配置／脚本／热修，无长时编译阻塞）**：
  免除独立 `ledger.md`、免建新 Worktree、免派 `Writer` 子代理；由 **`Steering` 在当前工作区直接修改并跑通本地验证命令**，完成后直接 commit（需要 PR 的仓库提 1 个 PR 并由 Layer-0 `ci-monitor.py` 跟进）。
- **Single-Stream Mode（常规单流 Goal，多文件或耗时编译如 `xcodebuild`）**：
  整个 Goal 共用 1 个分支/工作树；`Steering` 派发 1 个 `Writer`（`group/coding`）在后台完成编码与本地测试，避免阻塞主会话输入框；所有切片完成后执行 **1 次** 跨厂商 `Reviewer` 审查（若目标仓 PR CI 已有 `codex-review-target` 等异构 AI 门禁则直接复用）并交付唯一 PR。
- **Parallel Wave Mode（跨仓或多模块真并发 Goal）**：
  `Steering` 在 `~/Development/handoff/<goal>/ledger.md` 与 `briefs/<task>.md` 定义无冲突波次；并行 `Writer` 在临时子 Worktree 中开发并通过本地测试后，本地 `keep` 合回 Goal 主分支并销毁子 Worktree，最终按 Goal 统一端到端验收与交付。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

**核心区分原则**：不按“你是常驻 Bot 还是本地 CLI”死板切分讨论与执行，而是按 **所处阶段（Phase 0 vs. Phase 1）** 切换角色与动作边界。

| 阶段 | 角色形态 | 触发信号 | 核心产出与边界 |
|---|---|---|---|
| **Phase 0：交互共创期**（Interactive Co-Design） | **Co-Design 顾问态**（`Steering` 主会话） | Owner 提出探讨性问题（“怎么设计比较好”、“先讨论方案”、“先出设计”），或需求存在明显的产品／交互／架构未决项 | 只读探查（R11）＋每次聚焦 1–2 个关键决策点与 Owner 对齐＋UI/UX 设计 → 落盘 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`）；**未获 GO 前不改业务代码** |
| **Phase 1：自治执行期**（Autonomous Execution） | **Steering 执行/协调态** | Owner 明确拍板（“按这个做／开始执行／做”），或初始请求本身即为范围清晰、无歧义的直接实现指令 | 锁定 `spec.md`（`Mandate`）→ `≤3` 文件轻量改动由 `Steering` 直接内联改完验证；多文件/耗时编译派 `Writer` 在同 Goal 分支连续推进，收口时做 **1 次** 异构 `Reviewer` 审查（或复用仓库 CI 异构门禁）＋Layer-0 `ci-monitor.py` 读回 |

**三类入口派生独立 `Steering` 与单层 `Steering` 规则**：
- **M2 两径简单 Spec 确认与拍板后硬切换门禁（普通启动的 CLI、`Grok Bot`、`Cindy` → 启动独立 `Steering` M1）**：显式加载的领域 Skill（如 `/my-designer`、`/design-studio`）只负责领域/设计内容与审计，与编排层正交。Phase 0 确认基本任务时，常规需求由前台 M2 直接快速只读探查并用 `ask_user` 与 Owner 拍板落盘 `spec.md`；复杂需求由 M2 先派后台 `<goal>-goal-tl-r1`（M1）梳理草案与 `NEED_GO` 再由 M2 与 Owner 沟通。**一旦 `ask_user` 拍板完成（或无需确认），凡改动 `>3` 文件或含耗时编译/安装（如 `xcodebuild`、`install-fixed-build.sh`），前台 M2 严禁对业务仓文件调 `edit`/`create`**（即便 Prompt 含提交/安装指令或仓库 `AGENTS.md` 写有“单层一个 agent 直接做”），必须调用 `skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind <claude|codex|copilot> --cwd <repo_or_dev_root> --prompt ...` 在后台 Herdr Space 启动绑定 `Magpie (:3425)` `group/steering` 的独立 `Steering`，并在前台挂载 `goal-supervisor.py watch` 看门狗，绝不阻塞当前交互窗口。
- **Goal 内唯一 `Steering` 铁律**：一旦进入 `<goal>-goal-tl-r1`（或本地 CLI 内联执行轻量改动），当前 `Steering`（TL）主会话是该 Goal 唯一的 `Steering`（TL），**never spawns a second `Steering`** 或独立 `Planner`；仅由协调入口为每个 Goal 启动一个 `<goal>-goal-tl-r1`，再由该 `Steering` 派发叶子 `Writer`（`--role writer` → `group/coding`）／`Reviewer`（`--role code_reviewer` → `group/reviewing`），并运行 Layer-0 `ci-monitor.py`。

### Phase 0：交互共创期（Interactive Co-Design —— 讨论 / 方案 / 设计）

- **触发条件**：当 Owner 提出探讨性问题或需求存在未决项时，当前对话窗口处于 **Co-Design 顾问态**。
- **允许动作**：
  1. **只读探查（R11）**：Steering 直接读取有界代码；仅在跨仓或高噪声搜索超过
     8 个文件时按需派只读 `Explorer`（附 `file:line`）摸底代码事实。涉及 UI 时核对
     `shared-design-system`／`DesignKit` parity（`Package.resolved`）及 upstream primitives；
  2. **需求对齐与收敛（Grill / Spec）**：Steering 结合 `grill-me`／`to-spec`／
     `codebase-design`／`domain-modeling` 聚焦关键决策点与 Owner 交互；
  3. **视觉与交互设计（UI/UX Design）**：Steering 按需直接加载
     `skill("my-designer")`、`hig` 或 `impeccable`，产出 `design.md`／`spec.md`
     （`writes: handoff-only`），遵循 `my-designer`（Plan A + Plan C）与 `shared-design-system` upstream 规范；
  4. **产出冻结候选**：将结论落盘为 `~/Development/handoff/<goal>/spec.md`；
     多任务或派发 `Writer` 时按需写 `briefs/<task>.md`。
- **Phase 0 禁令**：在 Owner 给出明确执行信号前，不修改业务代码。

### 切换开关（Freeze / GO）

- 当 Owner 明确拍板（如“按这个做／开始执行／做”），或初始请求本身就是范围清晰、无歧义的直接实现指令时，立即锁定 `spec.md`（`Mandate`）并进入 **Phase 1**。

### Phase 1：自治执行期（Autonomous Execution —— Inline 与 Worker 弹性分流）

- **Inline 直改 vs. 后台 Worker**：`≤3` 个文件的轻量改动/配置/脚本热修，由 `Steering` 在当前工作树直接修改并运行验证命令；多文件、跨模块或耗时构建（如 `xcodebuild`）则通过 `herdr-dispatch.sh` 派发后台 `Writer`，绝不阻塞用户当前交互 CLI Space。
- **Space 与分屏规范**：派发后台 Subagent 时复用同一 Space 分屏（`herdr pane split --direction right|down`）或创建 Space（`herdr workspace create`／`herdr worktree create`，`--cwd` 传目标 Git 仓根或共享根 `~/Development`／`/Volumes/SSD/Development/handoff`）。
- Bot ↔ TL 协同与 `PR_MERGED`／`GOAL_DONE` 回收遵循 [`coordinator`](../coordinator/SKILL.md)；保留 Worktree 不阻断关窗。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

`skills/team-lead/scripts/goal-queue.py` 以 `.queue.lock` 原子更新 queue 与
ledger，并按 `(repo, paths)` 冲突矩阵形成并行／串行波次。Batch 使用
`enqueue --batch-json` → `next-wave --mark-in-progress` → `set-status --status done`，
直到 `drained: true`。已有 `<goal>-goal-tl-r1` 时 Hot-Append 只 `enqueue`、记录
`EVT=HOT_APPEND`；TL 为 `idle`／`done` 时 prompt 唤醒，仍未工作只补一次
`herdr agent send-keys <goal>-goal-tl-r1 enter`，并在波次收口重跑 `next-wave`。

#### Tracked feedback manifest (schema 1)

Feedback-tracked queues require the manifest, exact `feedback_id` → acceptance
mapping, and terminal evidence; supersession preserves every original acceptance.
The canonical schema, replacement/cancellation rules, and `set-status` evidence
contract live in [`ledger.md`](references/ledger.md).

### 会话内追问与上下文污染度弹性分流（Sticky TL & Context-Noise Threshold）

- **会话粘性（Sticky TL Rule）**：一旦当前会话已进入 `team-lead`（或存在活动的 `~/Development/handoff/<goal>/ledger.md`），后续用户追问自动继承合同约束。
- **上下文污染度弹性分流（Context-Noise Threshold）**：已知模块或中小改动（`≤8` 个文件）由 `Steering` 直接读取、诊断并写 `spec.md`／`briefs/<task>.md`，无需派发 `Explorer`；跨仓搜索、全量 `Obsolete Test Manifest` 或长日志超过 8 个文件时，才经 `herdr-dispatch.sh` 按需派发只读 `Explorer`，接收 `<15` 行摘要与 R10 receipt。`≤3` 个文件的轻量代码修正可由 `Steering` 直接完成并验证；大范围或耗时构建交 `Writer` 执行。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`ledger.md`、`briefs/<task>.md` 与 Git 是跨进程唯一事实源；Codex
`create_goal`／`update_goal`、Copilot `sql` 的 `todos`／`todo_deps` 与
Claude `TodoWrite` 仅作进程内投影。验收与 R10 完成后才标 complete；
`TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮失败立即标 blocked。
详见 [`ledger.md`](references/ledger.md)。

## 合同角色（`Steering / Writer / Reviewer + Layer-0 脚本`）

| 合同角色 | 职责 |
|---|---|
| Steering / 主控（TL / `group/steering`） | 探查规划、按需加载 `my-designer` 等技能写 `spec.md`／`design.md`、`≤3` 文件轻量直改、多任务依赖调度与内联裁决（`EVT=RULING`） |
| 探查者（Explorer，可选） | 仅在 `>8` 文件高噪声跨仓搜索时按需派发，只读核实事实（附 `file:line`）并写 handoff 回执（R11） |
| Task 写者（Writer / `writer_escalated`） | 负责多文件或耗时编译任务的实现、本地验证与 commit（同 Goal 默认共用同一分支连续推进；并发子 Worktree 验证后本地 `keep` 合回并退出关窗）；严禁 `gh run watch` 或 `sleep >= 30` 等待远端 CI；首轮使用 `worker.writer`（`group/coding`），`writer_escalated` 仅在 V3 第 2 轮修复时启用 |
| 独立验收（Reviewer） | 在 Goal 收口（或最终 PR）时执行 **1 次** V1–V5 双轴跨厂商独立审查（若目标仓 PR CI 已运行 `codex-review-target` 异构审查则直接复用，不重复派发） |
| CI / 发布监控（`ci-monitor.py` / `pr_monitor`） | 默认在后台运行 `python3 skills/team-lead/scripts/ci-monitor.py workflow|pr`（Layer 0、0 Token）监控发布流水线、PR CI 与 auto-merge；仅失败需 LLM 诊断时回退 `worker.pr_monitor`。Job 级 Fail-Fast 看 `failure`／`timed_out`／`action_required`，Run 仍 `in_progress` 时用 `actions/jobs/<id>/logs`，不用 `gh run view --log-failed` |

### 技术栈 Skill 路由表（Apple & DataViz Skills Routing）

技术栈到 Required Skills 的唯一事实源是
[`bots-and-agents.md`](references/bots-and-agents.md)；模型与启动参数只看
[`runtime-profiles.yaml`](references/runtime-profiles.yaml)。

## 定义、冻结与缺口

- **R1–R5 `Mandate (spec_sha)` 与极简两态裁决**：
  - **常态直通（`EVT=RULING`）**：只要不推翻与 Owner 对齐的北极星目标（Goal 验收）且不触碰 4 条硬红线（① 改变 Goal 核心目标、② 安全／隐私／数据丢失、③ 修改 CI 门禁／规则集、④ 破坏性操作），执行中的顺带文件增补、Task 切分调整或接口微调由 `Steering` 直接记一条 `EVT=RULING` 放行，无需多角色联合审批、预授权额度计数或修订次数锁死。
  - **触线升级（`needs a person`）**：仅当需要改变 Goal 验收目标或触碰上述 4 条硬红线时，暂停受影响项并向 Owner 发起 `EVT=NEED_GO`／`NEED_OWNER_DECISION`。
  - 反馈跟踪队列若发生 supersession，须保留原始 `feedback_id` 的 acceptance 覆盖映射。
- **R6 1-Hop 扁平调度与磁盘文件指针交接（File-Pointer Handoff）**：
  **1-Hop Flat Tree**：由 `Steering`（TL）统一派发下属 Subagent（`Writer`、`Reviewer`、可选 `Explorer`）；下属默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`）。
  **File-Pointer Handoff（防 Context Rot）**：派发 Subagent 时将简报写入 `~/Development/handoff/<goal>/briefs/<task>.md`（背景、`Owned Paths`、`Interfaces`、验收命令），prompt 仅传文件指针路径；Subagent 将完整日志写入 R10 回执，向主会话只返回 `<15` 行精简结论。
- **R7 执行前一致性**：确认 `Owned Paths`、接口与验证命令实际存在且可运行。
- **R8 状态机与租约**：
  - **Task 状态**：`planned → ready → in_progress → done`（或 Goal 收口时 `in_review → changes_requested → in_progress`），任意状态可转 `blocked`／`cancelled`。
  - **Goal 状态**：`planning → in_progress → accepting → done`（或 `blocked`／`cancelled`）。
  - **租约**：超时后 `Steering` 按 git 实际状态＋R10 回执对账，确认原写者已停止后接手或改派。
- **R9 本地 `keep / drop` 收口与单出口交付**：
  同 Goal 下的串行 Tasks 直接在 Goal 分支连续 commit；并发子 Worktree 验证通过后在本地 `keep`（merge）回 Goal 分支并清理子 Worktree（失败实验分支 `drop`）。整个 Goal 完成后统一提 1 个 PR（或无保护分支本地门禁通过后直推）。
- **R10 回执与证据对账**：Subagent 执行与 Goal 收口审查落盘 R10 回执（记录 commit SHA／head、验证命令与结果、实际模型）；`Steering` 按回执＋git 实际状态对账。
- **R11 按需只读探查**：跨仓或 `>8` 文件高噪声搜索可派只读 `Explorer`（只写 handoff 回执，不改仓库文件），附 `file:line` 回报事实。

## 角色绑定与交接

**1-Hop Communication & Lifecycle Ownership（单层沟通与管辖）**：每次只沟通一层
Agent。常驻 Bot 拉起独立 Goal TL 时，Bot 只沟通、唤醒、解卡并在 `GOAL_DONE` 时关闭
该 TL 的 Goal pane/workspace（保留 Worktree 不阻断关窗）；绝不越级创建、prompt 或关闭 TL 下属 Subagent。TL 全权
负责下属 Subagent 的派发、更新与关窗回收。

**任意时点接手**：新的 `Steering` 接手同一 Goal 时，先按 R10 回执、台账与 git 实际状态对账，从当前状态继续推进，不重复已完成的工作。

## 限定审查

- **Spec-First Requirement Change Rule**：已有代码／测试的需求或 UI/UX 变更以
  `spec.md`／`design.md`（及 `specs/`、`docs/design-contract.md`）为唯一上游；
  `Steering`（或按需 `Explorer`）识别冲突的 legacy test assertions，产出带
  `file:line` 的 **Obsolete Test Manifest**，并在 `spec.md`／`design.md` 记录
  **Deprecated Test Clauses to Remove/Rewrite**；`Code Writer` 先迁移获授权旧断言再实现，
  `Code/UI Reviewer` 保留非 deprecated regression tests。
- **V1 双轴（Goal 收口单次审查）**：中间子任务以本地确定性编译/测试通过为准；Goal 收口交付时执行 **1 次** 独立审查（若目标仓 PR CI 已配置 `codex-review-target` 等异构 AI 审查门禁则直接复用，不重复派发 Reviewer 子代理）。Axis 1 核对范围与规格，Axis 2 核对测试真实性、质量与安全红线。
- **V2 按引入来源阻塞**：本 diff 引入的问题、验收失败、安全／隐私／数据丢失问题阻塞；既有无关问题仅记 backlog。UI 任务须通过目标仓 `design_contract.py` 与 `my-designer` 门禁（含 `sips -g pixelWidth`、zero numeric truncation `…`、无 raw localization keys，且不得因 “followed the brief verbatim” 降级 V2 P1 `FAIL`）。
- **V3 复审与升档**：第 1 轮 FAIL 由原写者修复；仍 FAIL 时在第 2 轮启用 `writer_escalated`（`gpt-6.1-sol`）；两轮仍失败转 `blocked` 交 `Steering`／Owner 裁决。
- **V4 CI 范围**：目标仓库 CI 门禁照常有效；修改门禁须获 Owner 批准。
- **V5 结论**：`PASS` 通过，`CONCERNS` 记非阻塞 backlog，`WAIVED` 记批准与原因，`FAIL` 触发修复。

## CLI 用法

- 并发 Goal 用 `skills/team-lead/scripts/goal-supervisor.py scan`、
  `status --check`、`check-pins` 与 `telemetry --handoff-dir <goal> --write`；
  等待子 Agent 使用 30–60s 有界 slices，并检查 `ZOMBIE_LEASE`、
  `BLOCKED_AGENT` 与 R10 receipts。关联 `good-des-*`／`gd-e1-*`，基线祖先为
  `/Volumes/SSD`；完成回收可执行 `herdr pane close "$HERDR_PANE_ID"`。
- 模型配额水位与轻重意图路由统一由 `herdr-dispatch.sh` 与本机网关 `Magpie (:3425)` 的 `group/steering`、
  `group/reviewing`、`group/coding` 协同调度：`--kind claude` 走 `direct_quota_gated`（额度 `<80%` 且 OAuth 正常时原生 `opus` 直连，保住原生 Subagent/Teammate 且防跨协议 Schema 400；超限或 `401/403/429` 时开窗前自动改派 `copilot --model magpie/group/steering`），网关不可达时按 `runtime-profiles.yaml` 回退。
- 通过 `skills/team-lead/scripts/herdr-dispatch.sh` 按 profile 显式分屏、
  allowlist、`--direction right|down` 与 Parent ≥ Child 白名单派发；
  不使用裸 `--current`，不在 Writer／TL 内联等待远端 CI。
  手写分屏示例：`herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus`。
- Layer 0 默认使用 `python3 skills/team-lead/scripts/ci-monitor.py workflow` 或
  `python3 skills/team-lead/scripts/ci-monitor.py pr`，直接异步启动，不为纯状态轮询启动 LLM
  Subagent；仅失败诊断或特殊复杂场景才回退 `pr_monitor`。脚本按 Job 级 Fail-Fast；Run 仍
  `in_progress` 时使用 `actions/jobs/<failed_job_id>/logs`，不要使用
  `gh run view --log-failed`。
- `Writer` 完成本地验证与交付后即退出关窗（无需挂起空等）；当临时子 Worktree 已本地 `keep` 合回或最终 PR 已 `PR_MERGED` 后，通过 `python3 skills/team-lead/scripts/git-gc.py task-clean`（或 `git worktree remove`）清理已合并 Worktree；`GOAL_DONE` 时关闭 Goal pane/workspace，未合并／暂停（HOLD）现场保留并报告 `Owner`。
- 详细启动、Tier A/A′ 解卡、Reply/Resolve、队列和生命周期规则分别见
  [`bots-and-agents.md`](references/bots-and-agents.md)、
  [`coordinator`](../coordinator/SKILL.md)、
  [`playbook.md`](references/playbook.md) 与 [`ledger.md`](references/ledger.md)。
