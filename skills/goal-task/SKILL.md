---
name: goal-task
description: 进行任何开发、架构或规格规划、UI/UX 设计或界面重设计、数据/指标或代码库调研、实现、Bug 修复、代码审查或 PR 交付前必须加载；按单任务或多任务 Goal 通过 1-Hop 子代理编排 Explorer -> Designer -> Writer -> Reviewer -> pr_monitor。
---
# Goal -> Task 执行合同（`goal-task`）

> 唯一技能名是 `goal-task`。旧目录 `goal-target-task` 已移除，不再提供兼容安装路径或固定锚点。

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

## 两层定义与两档执行路由（`Goal -> Task`）

| 层 | 范围与层级 | 依赖 | 验收与完成 |
|---|---|---|---|
| Goal | 可观察的最终结果、整体范围和不做项；列明端到端验收命令及所属 `Task` 的依赖 DAG | 外部前提及 Tasks 之间的依赖 DAG | 每项整体验收对应端到端验证命令；必需 Tasks 全部 `done`，端到端验收有证据 |
| Task（`= 1 PR`） | 属于一个 Goal 的垂直曳光弹切片（或 `Expand -> Migrate -> Contract` 机械演进步骤）；遵循 **`1 Task = 1 Writer = 1 Worktree = 1 PR`**，显式声明 `Owned Paths`、`Interfaces: Consumes / Produces` 与验证命令 | 前置 Tasks | 指定输出齐全，指定验收命令全部通过，对应的单一目的 PR 完成独立审查（V1–V5）并合法合并 |

每个定义都显式记录范围、父级 Goal／代码层、依赖和验收命令；Task 的验收映射到所属 Goal。
按可验收结果、真实接口和依赖划分 Task（**Vertical Tracer-Bullet Slices**——每个切片端到端穿过所需的接口、实现与测试，能独立编译与验证；大范围机械重构采用 `Expand -> Migrate -> Contract` 分步演进），允许跨层，不按 layer 数、文件数或行数机械拆分。
**`1 Task = 1 Writer = 1 Worktree = 1 PR`**：Task 同时是分配、独立工作树隔离、PR 审查与合并的唯一原子单位；若某切片需要拆成多个 PR，必须在 Goal 的 DAG 中拆分为多个独立的 `Task`，不再存在“一个 Task 内拆多个 PR”。
依次判断四谓词：执行者不同、验收方式不同、可以并行、或中间结果要交给别人消费，任一成立即拆为独立 Task。
对原始反馈／缺陷输入，默认 **一个可独立验证的 bug 或 requested change 对应一个 Task／PR**；不得按页面、文件、layer、批次、集成便利或 CI 失败合并。只有多个原始反馈明确由同一变更解决、且任务简报携带结构化 common-root-cause evidence 时才可合并；机器只校验声明结构，不判断语义上的根因等价。
同一个 bug 所需的 storage、UI、文档和 regression test 属于同一垂直 Task；不同 bug 即使触碰同一 owned path 仍保持独立，并由队列串行化。
使用 issue 跟踪时，Goal 是父 issue，Task 是其子 issue，每个 Task 对应 1 个 PR。
*旧版兼容说明：既有台账或 issue 中遗留的 `Target` 条目，直接映射为单个 `Task`（或同一 `Goal` 下的一组 `Tasks`）。*

### 两档执行模式（Adaptive Scaling by Task Count）

协调者在启动前按 Task 数量与边界自适应分流为以下两档：

- **Lite / Single-Task Mode（`1 Goal = 1 Task = 1 PR`）**（同时满足：仅 1 个 Task、1 个 Writer、1 个 PR、改动 ≤5 个文件、不改公共接口／红线路径／CI 门禁、不新增依赖）：
  免除独立 `~/Development/handoff/<goal>/ledger.md` 台账与单独计划审查循环；定义方（TL）完成 R7 自检后直接派 1 个 Writer Subagent + 1 个异构（Cross-Vendor）Reviewer Subagent（V1–V5）；与在途 PRs 路径不重叠时不进 R9 串行队列；PR 描述兼作台账与 R10 回执（记录 Task、验收命令与结果、准确 head、审查结论），验收为仓库 CI＋该 PR 的一次双轴独立审查（V1–V5）；合法合并并读回后，Goal 即完成；R2 冻结、`EVT=RULING` 内联裁决与 V2／V3 照常。执行中任一条件不再满足，写者停止并回报，升级至 Standard / Multi-Task DAG Mode（补台账与独立计划审查）后再继续。
- **Standard / Multi-Task DAG Mode（`1 Goal = N Tasks = N PRs`）**（多文件特性、跨层改动或多 PR／跨仓工作）：
  Planner／TL 在 `~/Development/handoff/<goal>/ledger.md` 定义 `Goal` 及其 `Task` 依赖 DAG，并为每个 Task 写入磁盘文件指针简报 `~/Development/handoff/<goal>/briefs/<task>.md`（显式声明 `Interfaces: Consumes / Produces` 精确签名、`Owned Paths` 与验收命令）。
  无依赖的 `Tasks` 并行执行（各自在独立 worktree 由专属 Writer 负责），有依赖的 `Tasks` 按 DAG 拓扑顺序推进；每个 `Task` 在其唯一 PR 上完成一次独立审查（V1–V5）并合法合并；当所有必需 `Tasks` 均为 `done` 后，执行 Goal 端到端验收以关闭 Goal。

需要计划的工作先形成可审定义，按目标仓规则完成独立计划审查和执行确认；有效受审基线直接复用，不先实施再补审。

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

**核心区分原则**：不按“你是常驻 Bot 还是本地 CLI”死板切分讨论与执行，而是按 **所处阶段（Phase 0 vs. Phase 1）** 切换角色与动作边界。

| 阶段 | 角色形态 | 触发信号 | 核心产出与边界 |
|---|---|---|---|
| **Phase 0：交互共创期**（Interactive Co-Design） | **Co-Design 顾问态**（常驻域主 Bot 或本地 CLI 主会话均可担任） | Owner 提出探讨性问题（“怎么设计比较好”、“先讨论方案”、“先出设计”），或需求存在明显的产品／交互／架构未决项 | 只读探查（R11）＋每次聚焦 1–2 个关键决策点与 Owner 对齐＋UI/UX 设计或原型 → 落盘 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`）；**严禁建业务代码 Worktree、严禁派 Writer 改业务代码** |
| **Phase 1：自治执行期**（Autonomous TL Execution） | **TL 协调态**（Bot 拉起后台 `<goal>-goal-tl-r1` 或本地 CLI 原地转任 TL） | Owner 明确拍板（“按这个做／开始执行／做”），或初始请求本身即为范围清晰、无歧义的直接实现指令 | 冻结 `spec.md` → 按 `Lite`（单 Task）或 `Standard`（多 Task DAG）1-Hop 派发 `Writer` 与异构 `Reviewer`，跑通实现、双轴审查与 PR Auto-merge 读回；**TL 绝不亲自下场写多文件业务代码或自审** |

### Phase 0：交互共创期（Interactive Co-Design —— 讨论 / 方案 / 设计）

- **触发条件**：当 Owner 提出探讨性问题（“怎么设计比较好”、“先讨论方案”、“先出设计”），或需求存在明显的产品／交互／架构未决项时，当前对话窗口（无论是常驻域主 Bot 如 `Joy`／`Atlas`／`艺术家`／`flower`，还是 Owner 直接打开的本地 CLI 会话）处于 **Co-Design 顾问态**。
- **允许动作**：
  1. **只读探查（R11）**：按需派 1 个只读 `Explorer`（`gpt-5.6-luna`／`mai-code-2-flash` 或 `gemini-4-argon`／Codex 只读）极速摸底代码事实（附 `file:line`）。涉及 UI 时，Explorer 还必须核对目标仓库的 `shared-design-system`／`DesignKit` 版本 parity（`Package.resolved`／本地 package state），并盘点已有 upstream primitives，避免 Designer／Writer 在 `AppUI` 重造组件；
  2. **需求对齐与收敛（Grill / Spec）**：结合 `grill-me`／`to-spec`／`codebase-design` 每次聚焦 1–2 个关键决策点与 Owner 交互；
  3. **视觉与交互设计（UI/UX Design）**：采用 **Explorer → Designer**
     解耦；Designer 仅写 `design.md`／`spec.md`（`writes: handoff-only`），
     声明 Token、四态、双外观、container-driven width budget、3-tier text
     rules、zero numeric truncation、scenario → component & motion mapping，
     并复用 `shared-design-system` 的 upstream primitives。超过 `>15` 行
     custom layout/chrome glue 且 upstream 缺失时，先拆上游 Task；不得在
     `AppUI` 打补丁。固定流水线为 **Explorer → Designer → Writer → CI Design
     Gate → Cross-Vendor Reviewer**，不要求 `艺术家` 或任何 Grok Bot，`非门禁`；
     详规见 [references/bots-and-agents.md](references/bots-and-agents.md)。
  4. **产出冻结候选**：将讨论确认的结论落盘为 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`），复杂架构可派异构 `planner_reviewer` 预审盲区。
- **Phase 0 禁令**：在 Owner 给出明确执行信号前，**严禁创建业务代码 Worktree、严禁派发 Writer 修改业务代码**。

### 切换开关（Freeze / GO）

- 当 Owner 明确拍板（如“按这个做／开始执行／做”），或初始请求本身就是范围清晰、无歧义的直接实现指令时，立即锁死 `spec.md` 范围并进入 **Phase 1（Autonomous TL Execution）**。

### Phase 1：自治执行期（Autonomous TL Execution —— Bot 入口 vs. CLI 入口无缝衔接）

- 常驻 Bot 通过 `herdr` 拉起 `<goal>-goal-tl-r1`，把冻结 `spec.md` 交给 TL；
  `extra_add_dirs` 与 Parent ≥ Child allowlist 必须随 brief 注入。
- **Space 优先**：绝不占用／阻塞用户当前交互 CLI Space，保持可交互；后台 TL／Subagent
  可复用同一 Space 分屏，或创建 1 个／多个 Space。分屏用 `git worktree add` +
  `herdr pane split --direction right|down`（经 `herdr-dispatch.sh`）；创建用
  `herdr workspace create`／`herdr worktree create`，其 `--cwd`（`identity_cwd`）须传目标
  Git 仓根／worktree，或共享根 `~/Development`／`/Volumes/SSD/Development/handoff`，
  严禁传每次不同的 `/Volumes/SSD/Development/handoff/<goal>` 子目录，以统一归组。
- 本地 CLI 可原地担任 TL；两种入口都按 Lite／Standard 1-Hop 派发 Writer 与
  跨厂商 Reviewer，主会话不兼任 Task Writer 或独立验收。
- Bot ↔ TL 的开 TL、热追加、巡检、`PR_MERGED`／`GOAL_DONE` 回收遵循
  [`bot-tl-orchestration`](../bot-tl-orchestration/SKILL.md)；保留 Worktree 不阻断关窗。
- UI Task 固定走 `Explorer → Designer → Writer → CI Design Gate → Cross-Vendor Reviewer`，
  不要求 `艺术家` 或任何 Grok Bot，`非门禁`；详规见 `bots-and-agents.md`。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

`skills/goal-task/scripts/goal-queue.py` 以 `.queue.lock` 原子更新 queue 与
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

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

- **会话粘性（Sticky TL Rule）**：一旦当前会话已进入 `goal-task`（或存在活动的 `~/Development/handoff/<goal>/ledger.md`），**该会话后续每一轮用户追问或反馈都自动继承 `TL (Coordinator)` 合同约束——即使 Owner 没有再次输入 `/goal-task`**（例如口语化 Bug 反馈与追问：`"还是点不动呀"`、`"还没有找到原因吗"`、`"改一下配色"`）。
- **只读诊断上限（Read-Only Diagnostic Cap，≤3 次直接探查）**：收到用户 Bug 反馈或追问时，TL 最多执行 **2–3 次快速只读检查**以定位受影响的 Task 与文件路径；若排查需要深入的多文件／二进制逆向分析（`>3` 次工具调用）或**任何文件修改**（包括修改 `handoff/` 原型、规格说明 `spec.md`／`design.md` 或仓库业务代码），TL **必须**通过 `herdr` 派发 `Explorer` 或 `Writer` Subagent（`<goal>-<task>-writer-rN`）并记入 `ledger.md`，**严禁在 TL 主会话内直接内联调试或编辑文件**。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`ledger.md`、`briefs/<task>.md` 与 Git 是跨进程唯一事实源；Codex
`create_goal`／`update_goal`、Copilot `sql` 的 `todos`／`todo_deps` 与
Claude `TodoWrite` 仅作进程内投影。验收与 R10 完成后才标 complete；
`TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮失败立即标 blocked。
详见 [`ledger.md`](references/ledger.md)。

## 合同角色

以下为常见角色绑定；定义权仍按 R1，含获宽范围 assignment 的原实现者。

| 合同角色 | 职责 |
|---|---|
| Goal/Task 定义方（Planner / TL） | 定义 Goal、不做项、Task DAG 与各 Task 验收命令；评估 Goal／Task 变更，按 R2／R4／R5 分级报批并编制 revision |
| 协调者（TL） | 审定计划、分配、依赖调度、内联裁决（`EVT=RULING`）与路由决定；不兼任 Task 写者或独立验收 |
| 探查者（Explorer） | 只读核实代码库、API／符号、文件、命令与文档是否实际存在，附 file:line 证据回报事实（R11）；不改代码、只把报告写到 handoff，不提方案、不补定义 |
| 设计者（Designer） | 只写 handoff 的 `design.md`／`spec.md` 与 R10 回执；由 TL 通过 `herdr-dispatch.sh` 派发，在自己的子代理会话首步加载 `my-designer`／`impeccable`／`dataviz-principles`／`hig` 等 Required Skills，不写业务或 Demo 代码 |
| Task 写者（Writer / `writer_escalated`） | 每个 Task 唯一写者（1 Task = 1 Writer = 1 Worktree = 1 PR）：实现、验证、commit、push、开 PR；本地验证、`git push` 与 R10 回执落盘后立即结束，远端 CI 交 `pr_monitor`（严禁 `gh run watch` 或 `sleep >= 30` 等待远端 CI）；首轮（`r1`）必须使用 `worker.writer`（`copilot` `gpt-5.6-luna` → `mai-code-2-flash`），**严禁在 `r1` 首轮或 Planner 简报中直接指定 `writer_escalated`（`codex` `gpt-6.1-sol`）**，`writer_escalated` 仅在 V3 第 1 轮修复后复审仍 FAIL（进入第 2 轮修复）时启用 |
| 独立验收（Reviewer） | 按 V1–V5 对每个 Task 的 PR 执行双轴限定审查，并在全部 Task 完成后核验 Goal 端到端验收证据 |
| PR 监控者（PR Monitor / `pr_monitor`） | 使用 `worker.pr_monitor` 的模型顺序（`mai-code-2-flash` → `gpt-5.6-luna`）在独立 Herdr Subagent 窗格中监控 PR CI checks、auto-merge 与发布流水线状态，落盘 R10 监控回执并在绿灯合并或红灯失败时唤醒／回报 TL。Job 级 Fail-Fast：看 required job 的 `failure`／`timed_out`／`action_required`，不要只等 Workflow Run 顶层 `.conclusion`；Run 仍 `in_progress` 时用 `actions/jobs/<id>/logs`，不用 `gh run view --log-failed`。**严禁 TL 主会话或 Writer 内联 `gh run watch`／长 `sleep` 等待远端 CI** |
| 交付 | 推进 PR 合法合并与发布读回 |

### Designer 派遣与反内联守卫

`Designer`／`Writer` 的 Required Skills 与设计路由以
`bots-and-agents.md` 为准。TL 不在主会话调用 `skill("my-designer")`、自任
Designer 或读取组件源码写方案；必须经 `herdr-dispatch.sh` 派发 Explorer →
Designer，并由 Designer 产出 `design.md`／`spec.md` 与 R10 receipt。
CLI pipeline remains `Explorer → Designer → Writer → CI Design Gate → Cross-Vendor Reviewer`
without requiring `艺术家` or any Grok Bot；该路由为 `非门禁` FYI，不改变验收。

### Writer / Designer 技术栈 Skill 路由表（Apple & DataViz Skills Routing）

技术栈到 Required Skills 的唯一事实源是
[`bots-and-agents.md`](references/bots-and-agents.md)；模型与启动参数只看
[`runtime-profiles.yaml`](references/runtime-profiles.yaml)。

## 定义、冻结与缺口

- **R1 定义权**：拿到定义 assignment 的 Planner 或协调者（TL，含获宽范围 assignment 的原实现者）可以编制 `Goal` 与 `Tasks` 两层定义；不按工作流划分定义权，编制 revision 不等于决定变更，变更权限按 R2／R4／R5。
  保留已有决定和有效受审基线，只补真正未定的内容；Task-only 执行者不能补定义或扩权。
- **R2 交出即冻结与内联裁决（Rulings, Not Stalls）**：Task 简报与 Goal 定义一经交给执行方即冻结；执行方严格按获分配的 Task 做，不擅自增加文件、行为或内容。
  **协调者内联裁决（`EVT=RULING`）**：当写者在实现中发现同一 Task 目标内的顺带缺口（如漏列 1–2 个辅助／适配文件、非公共接口微调）且**不触碰 4 条硬红线**（① 改变 Goal 验收、② 安全／隐私／数据丢失、③ 修改 CI 门禁／规则集、④ 破坏性操作）时，写者无需暂停等待重规划循环，回报 `NEEDS_RULING`，由协调者（TL）当场在台账记一条 `EVT=RULING | decision=... | why=... | blast_radius=...` 即可放行继续。
  **超出内联裁决边界的变更**：触碰上述 4 条硬红线，或超出顺带缺口边界（如增删 Task、改变 Tasks 间接口）的范围变更须落盘结构化记录（改什么、原因、证据、对 Tasks 及验收的影响、规模等级）并走重规划审批：在负责人预授权额度内（不改变 Goal 验收、Task 增删合计不超过 1 个、不触碰 4 条硬红线）由协调者（TL）＋独立 Reviewer 批准并报负责人备案（记录授权来源，同一 Goal 累计计数）；超额度或改变 Goal 验收／范围的变更由负责人（Owner）批准（R4）。
  等待批准只阻塞受影响 Tasks，其余 ready Tasks 继续派发；待负责人决定的事项按批汇总提交。
  按等级获批后，定义方编制 revision；独立审查只看定义改动部分；未获 `RULING` 或未备案的范围扩张按 V2 阻塞。
  进度、PR/head、验证证据及下一动作记入执行台账，不修改冻结定义或另设审批。
  原始反馈 ID 与 acceptance mapping 是冻结契约；不得因 supersession、CI failure、重试或任务取消而静默删除、降级或改写 acceptance。显式授权 cancellation 与成功 delivery 是不同结果，未完成 acceptance 不能以 `done` 关闭。
- **R3 缺口分流（Rulings vs. 重规划）**：发现缺口时先按 R2 判断是否属于可由协调者内联裁决（`EVT=RULING`）的顺带缺口；若属于且不触碰 4 条硬红线，由 TL 记 `EVT=RULING` 当场放行，不暂停重规划。
  超出 `RULING` 边界或破坏性缺口不扩大当前 Task，走 R2／R4／R5 重规划：若上游编译、CI 或依赖坏了，在 Goal DAG 中插入前置 `Task`，当前 Task 等前置 Task 完成交付（`done`）后继续；若验收覆盖不全或需拆分新切片，按 R4／R5 把缺项拆入新 `Task`，当前 Task 仅按修订后的范围交付；与当前 Goal 无关的既有问题记入 backlog，由定义方／协调者判断是否属于本 Goal；改变 Goal 回负责人。
  对超出 `RULING` 边界的缺口，执行方按 R2 提交结构化变更记录，附失败命令、受影响验收及建议，不自行补范围外适配／测试、删验收或宣称原范围已完成。
- **R4 Task DAG 变更权**：Task DAG 的增删改、Task 边界调整与接口修订由定义方／协调者评估并按 R2 分级批准（预授权额度内由 TL＋Reviewer 批准并备案，超额度或改变 Goal 由 Owner 批准）；定义方据此编制 revision；Task 变更不能改变 Goal 验收，Goal 变更须负责人批准；执行方只能附证据提议。
- **R5 验收重映射**：验收项或接口契约在 Tasks 之间移动／拆分时，由定义方／协调者评估，按 R2／R4 分级批准；定义方据此重映射 Goal 与各 Task 的验收命令并出新 revision，独立审查只看改动部分。
  台账保留迁出／迁入 Task、原因及未交付状态；迁移不等于 Goal 验收完成。
  supersession 必须保留原始 feedback ID 和每条 acceptance 的 replacement linkage；replacement Task 未覆盖全部原 acceptance 时不得关闭原 Task，CI／build 失败不得成为取消 scope 的替代理由。
- **R6 1-Hop 扁平调度与磁盘文件指针交接（File-Pointer Handoff）**：
  **1-Hop Flat Tree 优先**：由顶层协调者（TL）统一派发所有 Subagent（Explorer、Planner、Writer、Reviewer）；Task 写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`），从根源避免 Claude Code 单层 subagent 限制、Codex 只读沙箱嵌套被拒以及 Worker 私自派 Reviewer 重复自审的问题。若特殊场景需对同一 Task 拆分执行分片，由 TL 并行派发执行分片 subagent（使用不重叠路径或独立 worktree），仍由该 Task 唯一写者整合、commit、push、开 PR 并对验收负责；subagent 检查计入自检，不算独立审查。
  **File-Pointer Handoff（防 Context Rot）**：交接不再把冗长的合同条文或历史对话粘贴进 prompt，而是由协调者将任务简报写入 `~/Development/handoff/<goal>/briefs/<task>.md`（含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces` 精确签名、允许操作与验收命令），prompt 仅传该文件指针路径；Subagent 读文件执行，将完整验证日志写入 R10 回执文件，向主会话只返回 `<15` 行精简结论（状态、PR/head、关键命令结果、回执路径）。
- **R7 冻结前一致性**：Goal／Task 计划冻结前逐项核对：每个 Task 的验收、`Owned Paths`、`Interfaces: Consumes / Produces`、前置依赖及验证命令齐全；引用的命令、API、符号、文件已实际核实存在，不凭假设；可用对应当前基分支 SHA 的 R11 探查证据满足；冻结前清单须列出 `Owned Paths`、引用的脚本（含其调用的嵌套检查器）与验收命令，并附其存在且可运行的证据。
  对反馈跟踪 Goal 还必须逐项核对 `原始 feedback ID → 独立 Bug／request → Task／PR → 每条 acceptance` 的精确覆盖与唯一归属；多个 feedback ID 仅在有证据证明同一根因且由同一变更解决时可合并。缺失映射、重复归属、不当归组或证据不足均阻止冻结。
  Tasks 之间命令不冲突；可并行（无依赖顺序）的 Tasks 的 `Owned Paths` 互不重叠；Task 依赖 DAG 无环；定义方输出不得含可编译的实现代码；任一失败即阻止冻结。
  首轮独立计划审查须一次覆盖全部一致性项并集中给出 findings，不分轮追加本轮应发现的问题；按 R2 出新 revision 后复核受影响项。
- **R8 单套清晰状态机**：
  - **Task 状态机（`1 Task = 1 PR`）**：`planned → ready → in_progress → in_review → done`，或 `in_review → changes_requested → in_progress`（仅 V5 的 FAIL 触发修复）；因每个 Task 对应唯一 PR，不存在“多 PR 部分合并回 `in_progress`”的中间态。任意状态可转 `blocked`／`cancelled` 并记原因；解除阻塞后 `blocked → ready`，仍须满足就绪条件；只有全部前置 Task 为 `done` 且 R7 通过才可 `ready`，只派发 `ready` 的 Task。
  - **Goal 状态机**：`planning → in_progress → accepting → done`，可转 `blocked`／`cancelled` 并记原因；当所有必需 Tasks 全部 `done`（或 `cancelled` 已备案）后进入 `accepting`，端到端验收证据齐全后转 `done`。
  - **租约**：`in_progress`／`changes_requested` 的写者按协调者设定的周期更新心跳（回执或台账）；超时后协调者按 git 实际状态＋R10 回执对账，确认原写者及其 R6 subagent 均已停止，记录交接（现场、head、未完成项）后改派新写者，同一时间仍只有一个写者。
- **R9 漂移与合并队列**：规划记录基分支 SHA 及 owned／依赖文件的 revision；每次派发或恢复前核对当前基分支 SHA 和这些文件；同一 Goal DAG 内上游 Tasks 按计划合并带来的变更不算漂移，只更新记录基线。
  其他漂移先暂停派发受影响 Tasks，按 R11 由 Explorer 复核受影响引用；事实未变则更新基线后继续，事实已变才退回定义方按 R2 重规划。
  同一问题两轮审查仍失败（V3）、经复核的事实漂移、验收不可实现或依赖被取消，均将受影响 Tasks 置为 `blocked` 并退回定义方重规划；修订基线获审通过前不再派发受影响 Task。
  同一 Goal 的 Task DAG 冻结后已修订 2 次仍需再修订时，不再自行重规划，交负责人决定缩小、拆分或取消。
  `Owned Paths` 重叠或有依赖关系的 PRs 由协调者排入串行合并队列，每次仅处理一个；写者轮到时按目标仓规则更新到最新基分支（rebase 或 merge），重跑 Task 验证，待准确 head 通过审查及仓库门禁后合法合并；失败不合并。
  与在途 PRs 路径不相交且无依赖的 PR 可直接走仓库原生 merge queue／自动合并，仍须准确 head 通过 Task 验证、审查及仓库门禁。
- **R10 双 ID 回执**：每个 Task 完成／交回、每次审查及每次 R11 探查均须落盘回执，以 Task ID＋run／agent ID 唯一定位；多轮审查保留各轮文件，记录状态、审查结论、commit SHA／head、验证命令与结果、合同 SHA、实际模型（降级注明原模型）、机器及大致 token；探查回执以 Goal／计划 ID 定位，记录基分支 SHA 及逐项事实与证据。
  协调者按回执＋git 实际状态对账，不以终端／pane 输出或 agent 自述代替证据；回执放目标仓允许或协调者指定的位置，台账链接对应文件。
- **R11 只读探查**：定义方在规划前或规划中可派 Explorer，交接列明待核实问题清单（API／符号、文件、命令、文档及版本）和基分支 SHA；Explorer 不改代码：不 edit 仓库文件、不 commit／push／开 PR，不运行会改变状态的命令；唯一允许的写入是把回执／报告写到 handoff 目录。
  逐项回报：存在／不存在／与假设不符，附 file:line、命令及输出或文档 URL／版本；不提实现方案、不补定义、不扩大问题清单，新发现的疑点回报定义方。
  定义方只引用已核实的事实；基分支 SHA 变化后按 R9 复核受影响项。探查不算独立审查，计划审查仍按 V1 核对。

## 角色绑定与交接

**1-Hop Communication & Lifecycle Ownership（单层沟通与管辖）**：每次只沟通一层
Agent。常驻 Bot 拉起独立 Goal TL 时，Bot 只沟通、唤醒、解卡并在 `GOAL_DONE` 时关闭
该 TL 的 Goal pane/workspace（保留 Worktree 不阻断关窗）；绝不越级创建、prompt 或关闭 TL 下属 Subagent。TL 全权
负责下属 Subagent 的派发、更新与 Task／阶段完结关窗回收；Bot 不得将子 pane 留给自己
清理。Bot 或本地 CLI 直接作为 TL 时，则由其直接管辖下属 Subagent。

角色来自可信调用来源和显式 assignment；独立 CLI 会话、可用工具或父级 Goal 上下文都不授予主 agent 权限。
协调者分配、监督、路由决定和汇报证据；原执行者实现、验证、修复，独立 reviewer 验收，不自签自己的产物。
协调者及 CLI 主会话不兼任 Task 写者或独立验收；只定义或审定 Planner 的定义、分配、监督、路由决定和转述证据。
实现、验证和验收结论由对应 agent 提供；协调者不推翻验收结论，有分歧按 V3 进入复审或交负责人。
采用软行为隔离，保持工具可用；helper 权限不包含同级或下级调度权（1-Hop 扁平调度，写者与 reviewer 默认不派发 subagent）；未知执行结果先对账，不派第二写者。

交接通过 `~/Development/handoff/<goal>/briefs/<task>.md` 磁盘文件指针（Lite 档可在任务描述中内联）携带本合同与目标仓指南、角色／来源／父级 Goal 绑定、1 句话背景、准确受审 revision 和授权来源、
Goal／Task、`Owned Paths`、`Interfaces: Consumes / Produces`、允许操作、唯一写者、验收命令、独立验收责任及预期回报（`<15` 行精简摘要＋R10 回执路径）；交给 subagent 的 Task 同样适用，不假定 skill 自动继承。
交接与台账记录合同路径及 SHA、定义 revision/hash，并分别标明审查基准和执行时实际加载的版本。
缺失或冲突沿原调用者回报，暂停依赖该缺口的动作；执行者不可用时保留现场，按 R8 租约超时对账后改派新写者，协调者不自行接管写入。

**任意时点接手**：所有执行入口（CLI 会话或其他平台的团队）适用同一合同与合同角色，平台只决定由谁担任角色；新的协调者或执行团队可在任意时点接手同一 Goal：尚在规划 Goal／Task DAG，或已有 Tasks（含在途或需修改的 Task）。
接手先按 R10 回执、台账与 git 实际状态对账，确认 Goal 与各 Task 的 R8 状态、合同 SHA 与定义 revision 并记录交接；从当前状态继续，不重新规划已冻结的定义。
先看 Goal：为 `planning` 时，定义方按 R1／R7 编制或补齐，按本合同完成计划审查（Lite 档按其规则以 R7 自检代替单独计划审查）后进入 `in_progress`；已冻结的不重新定义。
Goal 为 `blocked`、`cancelled` 或 `done` 时，其下 Task 一律不派发、不恢复，先按 R8 处理 Goal；Goal 为 `blocked` 时，其下 `in_progress`／`changes_requested` 的 Task 由协调者通知写者停止，确认原写者及其 R6 subagent 已停止后记录交接（现场、head、未完成项），Task 转 `blocked`。
Goal 处于 `in_progress` 时，逐个按 Task 自身状态继续：
- `planned`：满足 R7、全部前置 Task 为 `done` 并通过 R9 基线核对后转 `ready`；
- `ready`：复用有效受审基线，分配写者；
- `in_progress`／`changes_requested`：按 R8 租约对账，确认原写者及其 R6 subagent 已停止后改派，同一时间仅一个写者；
- `in_review`：交独立验收，不重做实现；
- `blocked`、`cancelled`、`done`：保持原状态，按 R8 处理，不因接手恢复、重开或重做。

Goal 处于 `accepting` 时交独立验收核对端到端整体验收；已冻结但需改动的，按 R2／R4／R5 落结构化变更记录并分级批准，由定义方出 revision，独立审查只看改动部分。
接手不重置 V3 两轮计数与 R9 冻结后修订计数；满足 Lite 档的小改动同样按本合同执行，不因入口不同而免去定义、冻结或 PR 独立审查。

## 限定审查

- **Spec-First Requirement Change Rule**：已有代码／测试的需求或 UI/UX 变更以
  `spec.md`／`design.md`（及 `specs/`、`docs/design-contract.md`）为唯一上游；
  `Explorer` 产出带 `file:line` 的 **Obsolete Test Manifest**，
  `Designer`／`Planner` 产出 **Deprecated Test Clauses to Remove/Rewrite**，
  `Planner Reviewer` 冻结后由 `Code Writer` 先迁移获授权旧断言，再实现，
  `Code/UI Reviewer` 保留非 deprecated regression tests。
- **V1 双轴**：每个 `Task = 1 PR` 单次独立审查；Axis 1 核对 brief／Interfaces／Owned
  Paths／范围，Axis 2 核对测试真实性、质量、验证证据与安全红线。
- **V2 按引入来源阻塞**：本 diff 引入的问题、验收失败、红线／安全／隐私／数据
  丢失问题和未经 Ruling 的越界修改均阻塞；既有且未由本 diff 引入的问题仅记 backlog。
  UI 任务必须核验 `sips -g pixelWidth == W * scale`、zero numeric truncation（`…`）、
  raw localization keys 与真实容器几何；不得因 “followed the brief verbatim” 降级，
  均属 V2 P1 `FAIL`。**Container-Filling Hit-Target Parity** 要求可点击容器至少
  `>=44×44pt`，且 `abs(effectiveHitFrame - visibleFrame) <= 1pt`。具体品类像素规则
  归 `my-designer`，不复制到本合同。
- **V3 复审与升档**：第 1 轮 FAIL 由原 `worker.writer` 修复；第 1 轮修复后复审
  仍 FAIL 才能在第 2 轮启用 `writer_escalated`（`gpt-6.1-sol`）带新鲜上下文。
  两轮仍失败即 `blocked` 并重规划，不开第三轮；新 SHA 仍须有该 SHA 的证据。
- **V4 CI 范围**：目标仓库 CI 门禁照常有效；修改门禁须另行获 Owner 批准。
- **V5 结论**：`PASS` 通过，`CONCERNS` 仅记非阻塞 backlog，`WAIVED` 需记录批准与
  原因，`FAIL` 仅在未豁免 V2 阻塞项存在时触发修复；每次结论落盘 R10。

复用同职责的既有审查，不堆叠重复全量审查；Goal 级端到端验收另行取证。

## CLI 用法

- 并发 Goal 用 `skills/goal-task/scripts/goal-supervisor.py scan`、
  `status --check`、`check-pins` 与 `telemetry --handoff-dir <goal> --write`；
  等待子 Agent 使用 30–60s 有界 slices，并检查 `ZOMBIE_LEASE`、
  `BLOCKED_AGENT` 与 R10 receipts。关联 `good-des-*`／`gd-e1-*`，基线祖先为
  `/Volumes/SSD`；完成回收可执行 `herdr pane close "$HERDR_PANE_ID"`。
- Mac mini strong 的 `team_lead`／`arbitration` 先做 Claude 周配额预检；
  周余量 `< 20%` 或未知时直接用 `copilot`／`gemini-4-argon`。诊断类
  Goal 在 MacBook 与 Mac mini 固定使用该组合。
- 通过 `skills/goal-task/scripts/herdr-dispatch.sh` 按 profile 显式分屏、
  allowlist、`--direction right|down` 与 Parent ≥ Child 白名单派发；
  不使用裸 `--current`，不在 Writer／TL 内联等待远端 CI。
  手写分屏示例：`herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus`。
- `pr_monitor` 按 `worker.pr_monitor` 做 Job 级 Fail-Fast；Run 仍
  `in_progress` 时使用 `actions/jobs/<failed_job_id>/logs`，不要使用
  `gh run view --log-failed`。
- 只有真实 `PR_MERGED`、R10 回执与 required checks 齐全后清理已合并
  Worktree（`git worktree remove`）；`GOAL_DONE` 时关闭 Goal pane/workspace，
  未合并／暂停（HOLD）现场保留并报告 `Owner`。
- 详细启动、Tier A/A′ 解卡、Reply/Resolve、队列和生命周期规则分别见
  [`bots-and-agents.md`](references/bots-and-agents.md)、
  [`bot-tl-orchestration`](../bot-tl-orchestration/SKILL.md)、
  [`playbook.md`](references/playbook.md) 与 [`ledger.md`](references/ledger.md)。
