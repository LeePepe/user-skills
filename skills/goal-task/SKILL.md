---
name: goal-task
description: 进行任何开发、架构或规格规划、UI/UX 设计或界面重设计、数据/指标或代码库调研、实现、Bug 修复、代码审查或 PR 交付前必须加载；按单任务或多任务 Goal 通过 1-Hop 子代理编排 Explorer -> Designer -> Writer -> Reviewer -> pr_monitor。
---
# Goal -> Task 执行合同（`goal-task`）

> 唯一技能名是 `goal-task`。旧目录 `goal-target-task` 已移除，不再提供兼容安装路径或固定锚点。

本合同是 CLI 主会话与 subagent 的共同来源。
目标仓指南、固定版本的共享规则、hooks、身份及 required gates 继续有效；读取本合同不授予角色、委派或发布权限。
明确且范围清楚的实现请求不重复索取相同授权；事实问答和诊断不自动变成实施。

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
  1. **只读探查（R11）**：按需派 1 个只读 `Explorer`（`mai-code-2-flash`／`gemini-4-argon` 或 Codex 只读）极速摸底代码事实（附 `file:line`）；
  2. **需求对齐与收敛（Grill / Spec）**：结合 `grill-me`／`to-spec`／`codebase-design` 每次聚焦 1–2 个关键决策点与 Owner 交互；
  3. **视觉与交互设计（UI/UX Design）**：涉及界面时采用**探查与设计解耦（`Explorer -> Designer`）**：只读 `Explorer` 先扫描业务仓现状与项目《设计契约》（`docs/design-contract.md`），产出带 `file:line` 的事实回执；`Designer`（设计角色，`writes: handoff-only`）**不翻阅业务实现细节、不写任何业务或 Demo 代码**，仅依据 `Explorer` 回执、项目 `docs/design-contract.md` 与上游 `shared-design-system`（`ai/DESIGN-CONTRACT.md`）产出 `~/Development/handoff/<goal>/design.md`，其中必须包含 Token 语义映射、四态矩阵（`populated/empty/loading/error`）、双外观（`light/dark`）与组件边界。设计路由按机器分流（Owner 2026-10-28，Hotty → flower）：**Mac mini 常驻域**（`flower`／`Atlas`／`艺术家`／`ana`／`笔者`，以及 Mac mini 上的 `Hotty` 协调）的产品 UI/UX 设计 Goal 仍交给 `艺术家` 开设计 TL 并做设计总审；**MacBook／`Joy`** 不转交 `艺术家`，在本机 goal-task 内按与 CLI 相同的阶段跑 `Explorer` → `Designer` → `Writer` → CI Design Gate；**本地 CLI** 由本 Goal 会话内 Designer 角色执行，不转交 `艺术家`。模型档位与花名册不在此复制，详见 [references/bots-and-agents.md](references/bots-and-agents.md)。
  4. **产出冻结候选**：将讨论确认的结论落盘为 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`），复杂架构可派异构 `planner_reviewer` 预审盲区。
- **Phase 0 禁令**：在 Owner 给出明确执行信号前，**严禁创建业务代码 Worktree、严禁派发 Writer 修改业务代码**。

### 切换开关（Freeze / GO）

- 当 Owner 明确拍板（如“按这个做／开始执行／做”），或初始请求本身就是范围清晰、无歧义的直接实现指令时，立即锁死 `spec.md` 范围并进入 **Phase 1（Autonomous TL Execution）**。

### Phase 1：自治执行期（Autonomous TL Execution —— Bot 入口 vs. CLI 入口无缝衔接）

- **从常驻 Bot 入口启动时**：常驻 Bot 保持自身会话轻量，建好 `~/Development/handoff/<goal>/ledger.md` 后，在 `herdr` 中拉起后台独立的 **`<goal>-goal-tl-r1` CLI 会话**（若 Goal 涉及仓外合法路径，须在 `briefs/goal.md` 头声明 `extra_add_dirs`，并在启动父 TL 时一并注入 `--add-dir` 与 `HERDR_EXTRA_ADD_DIRS`，满足**父 ≥ 子白名单不变量**（Parent ≥ Child）），将 `spec.md` 交给 `TL` 自治跑完 `Task 拆分 → Writer 实现 → Reviewer 双轴审查 → PR Auto-merge 读回`；常驻 Bot 同时监听台账事件与 `herdr agent get <goal>-goal-tl-r1` 的 `agent_status`（遇计划内 `Allow path access` 自动 `send-keys enter` Remember 解卡，未授权路径记 `NEED_GO` 报 Owner）。
- **Bot ↔ TL 编排入口**：常驻域主 Bot 的开 TL、热追加、巡检解卡与 Goal 完结回收遵循 [`bot-tl-orchestration`](../bot-tl-orchestration/SKILL.md)；本合同继续负责 TL ↔ Subagent 的 Task-level 1-Hop 执行。`PR_MERGED` 后按两级清理规则回收任务 Worktree，`GOAL_DONE` 后由 TL 延迟自闭 pane，Bot 再核对并清理 Goal TL pane/workspace。
- **从本地 CLI 入口启动时**：当前 CLI 主会话直接原地切换为 **`TL (Coordinator)`**（或者当 Owner 希望腾出当前终端时，由当前 CLI 在 `herdr` 拉起后台 `<goal>-goal-tl-r1`），按 `Lite`（单 Task）或 `Standard`（多 Task DAG）1-Hop 派发 `Writer` 与跨厂商 `Reviewer` Subagent，当前 CLI 主会话自身绝不直接下场写多文件业务代码或自审。
- **设计驱动的 Phase 1 交接**：独立的 Demo/UI `Writer` 在专用 Worktree 中消费冻结的 `design.md`，实现可运行 Demo／代码并产出四态与双外观的多态渲染截图；随后由 `shared-ci` 的 `scripts/context/design_contract.py` 执行静态设计契约门禁，拦截裸 Hex／RGB 色值与缺漏章节，最后由跨厂商 `Reviewer` 按 V1–V5 双轴对账验收。设计路由按机器分流（Owner 2026-10-28）：Mac mini 常驻域仍交 `艺术家` 做设计 TL 与终审；MacBook／`Joy` 与本地 CLI 在本 Goal 内跑同一 `Explorer` → `Designer` → `Writer` → CI Design Gate，不转交 `艺术家`。详见 [references/bots-and-agents.md](references/bots-and-agents.md)。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

通过内置标准库队列脚本 [`scripts/goal-queue.py`](scripts/goal-queue.py)（基于 `~/Development/handoff/<goal>/.queue.lock` 的 `fcntl.flock` 排他锁，原子同步 `queue.json` 与 `ledger.md`），常驻域主 Bot（`Joy`／`Atlas`／`flower` 等）与 `TL` 统一支持以下两种任务收集与推进姿势：

1. **姿势 1（Batch Mode 一次性塞多个任务）**：
   - Owner 一次性向常驻 Bot 或 `TL` 下达多条任务时，调用 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json <json-string-or-file>`。
   - 队列按 `(repo, paths)` 冲突矩阵自动分拣（`auto_dependencies`）：不同 `repo` 或同 `repo` 但 `Owned Paths` 互不相交且无显式 `--depends-on` 的任务保持 `depends_on = []`，归入**并行波次（Parallel Wave）**；同 `repo` 且 `paths` 存在前缀／通配符重叠（或同仓未指定 `paths` 默认按 `*` 通配）的任务自动挂接在前序未完成任务之后，归入**串行链（Sequential Chain）**，并同步写入 `ledger.md`（`EVT=READY`）。
   - `TL` 循环调用 `skills/goal-task/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal> --mark-in-progress` 原子领取当前无冲突波次并派发 Herdr Subagents，每个 Task 收口时调用 `set-status --status done`，直至返回 `"drained": true`。
2. **姿势 3（Hot-Append Mode 运行中随时热追加）**：
   - 当 `<goal>-goal-tl-r1` 已在运行中，Owner 向常驻域主 Bot（`Joy`／`Atlas`／`flower`）或直接在会话中追加新任务时，**Bot 绝不重复拉起第二个 TL**。
   - Bot 直接调用 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --task-id <id> --title <title> --repo <repo> --paths <paths> --brief briefs/<id>.md`；脚本在 `.queue.lock` 文件锁下安全写入 `queue.json`，自动计算与在途 `in_progress`／待执行任务的并行或串行依赖边，并在 `ledger.md` 追加 `EVT=HOT_APPEND`。
   - 若 `TL` 窗格当前处于 `idle` 或 `done`，Bot 通过
     `herdr agent prompt <goal>-goal-tl-r1 "Hot-append task <id>; run goal-queue.py next-wave"` 唤醒
     `TL`；发送后等待 2 秒，读取 `agent_status`，若仍为 `idle` 或 `done`，仅补发一次
     `herdr agent send-keys <goal>-goal-tl-r1 enter`，防止粘贴结束符吞掉回车而停在
     `❯` 输入框。`TL` 在每波任务收口退出前**必须**重跑一次 `goal-queue.py next-wave`，
     将运行期间热追加的任务自动接续清空（Drain to Zero）。

#### Tracked feedback manifest (schema 1)

For a feedback-tracked Goal, every enqueue must include `--feedback-manifest <json-string-or-file>`. The manifest is stored in `queue.json` and is validated again by `enqueue`, `next-wave`, and `set-status`; omission cannot fall back to legacy validation.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "feedback_id": "FB-001",
      "acceptance": [
        {"acceptance_id": "FB-001-A1", "text": "Synthetic acceptance criterion"}
      ]
    }
  ],
  "task_mappings": [
    {
      "task_id": "TK1",
      "feedback_ids": ["FB-001"],
      "acceptance_mapping": [
        {"feedback_id": "FB-001", "acceptance_ids": ["FB-001-A1"]}
      ],
      "common_root_cause_evidence": {
        "summary": "Required only when multiple feedback IDs share a Task",
        "references": ["test://synthetic/root-cause"]
      }
    }
  ]
}
```

`entries` and `task_mappings` must cover every queued Task and acceptance exactly once. Duplicate terminal ownership is resolved through the full `superseded_by` chain independent of manifest order; cycles, invalid links, and a chain with no active owner are rejected unless its terminal cancellation has explicit authorization. A multi-feedback mapping requires `common_root_cause_evidence`, which is structural evidence only and does not establish semantic common-root-cause truth. Existing queues without a manifest remain usable and report `feedback_validation=unverified_legacy`; a null or stripped manifest on a queue containing tracked task fields is rejected and must not downgrade to legacy.

Create a 1:1 replacement with `enqueue --replacement-of <Task ID>`, using the same original feedback ID(s) and every original acceptance ID/text. Later manifests may not delete, rewrite, or remap existing entries or another Task's acceptance. Automatic path-serialization edges are recorded as `auto_depends_on` and may unblock after cancellation; explicit functional dependencies remain blocked until the dependency or its completed replacement is delivered. Reopening a Task clears stale supersession, cancellation, and acceptance-evidence metadata.

For tracked terminal transitions use `set-status --status done|cancelled --evidence-json <json-string-or-file>`. Evidence is an object containing `acceptance`, with one item per mapped acceptance: `feedback_id`, `acceptance_id`, `status` (`passed`, `replaced`, or `cancelled`), and a non-empty `evidence` array. `done` requires every item to be `passed`. Supersession additionally requires `--superseded-by <task-id>`, `status=replaced`, matching `replacement_task_id` on every item, and a replacement manifest mapping the original acceptance; intentional cancellation instead requires `--authorization <reference>` and `status=cancelled`. These are local structure/evidence assertions only: the queue does not verify GitHub remotely, merge state, or semantic root-cause truth.

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

- **会话粘性（Sticky TL Rule）**：一旦当前会话已进入 `goal-task`（或存在活动的 `~/Development/handoff/<goal>/ledger.md`），**该会话后续每一轮用户追问或反馈都自动继承 `TL (Coordinator)` 合同约束——即使 Owner 没有再次输入 `/goal-task`**（例如口语化 Bug 反馈与追问：`"还是点不动呀"`、`"还没有找到原因吗"`、`"改一下配色"`）。
- **只读诊断上限（Read-Only Diagnostic Cap，≤3 次直接探查）**：收到用户 Bug 反馈或追问时，TL 最多执行 **2–3 次快速只读检查**以定位受影响的 Task 与文件路径；若排查需要深入的多文件／二进制逆向分析（`>3` 次工具调用）或**任何文件修改**（包括修改 `handoff/` 原型、规格说明 `spec.md`／`design.md` 或仓库业务代码），TL **必须**通过 `herdr` 派发 `Explorer` 或 `Writer` Subagent（`<goal>-<task>-writer-rN`）并记入 `ledger.md`，**严禁在 TL 主会话内直接内联调试或编辑文件**。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`~/Development/handoff/<goal>/ledger.md`、`briefs/<task>.md` 与 Git 实际状态始终是跨进程唯一事实源（Single Source of Truth）；各 CLI 的原生 Goal／Todo 工具作为**进程内持续推进引擎（In-Process Continuation Engine）**挂载并同步，防止 TL 或 Subagent 提前停机或无限空转：

| CLI 运行时 | 原生引擎接口 | 挂载时机与绑定对象 | 状态映射（与 `ledger.md` R8 同步） | 完成与熔断释放守卫 |
|---|---|---|---|---|
| **Codex CLI**（`goals = true` / `thread_goals`） | `create_goal` / `update_goal` | Phase 1 启动（TL）绑定 `ledger.md`；Task 启动（Writer／Reviewer）首个动作调用 `create_goal(objective="...")` 绑定 `briefs/<task>.md` | `in_progress` → active goal；`done` → `update_goal(status="complete")`；`blocked` → `update_goal(status="blocked")` | **仅当**全部验收命令通过、PR 完成交接／合并且磁盘 R10 回执已落盘核实后，才可调用 `update_goal(status="complete")`；遇 `TASK_BLOCKED`／`NEED_GO`／`NEED_OWNER_DECISION`／V3 两轮失败立即调用 `update_goal(status="blocked")`，严禁留在活动态空转 |
| **Copilot CLI** | `sql`（`todos` + `todo_deps`） | Phase 1 启动时 TL 将 Goal 的 `Tasks` 及 DAG 依赖边镜像写入会话 SQLite `todos` 与 `todo_deps` | `planned`/`ready` → `pending`；`in_progress`/`in_review`/`changes_requested` → `in_progress`；`done` → `done`；`blocked`/`cancelled` → `blocked` | 随 `ledger.md` 同步更新 `todos.status`，未解除前置依赖或 R10 未落盘前不得标 `done` |
| **Claude Code** | `TodoWrite` / `TaskCreate` | Phase 1 启动及状态变更时 TL 将活动 `Tasks` 投影到 `TodoWrite` | `planned`/`ready` → `pending`；`in_progress`/`in_review` → `in_progress`；`done` → `completed` | 仅作实时进度可见性投影，`ledger.md` 保持唯一权威状态源 |

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
| PR 监控者（PR Monitor / `pr_monitor`） | 使用与 `Explorer` 相同的 `worker` 档位模型（`mai-code-2-flash` → `gpt-5.6-luna`）在独立 Herdr Subagent 窗格中监控 PR CI checks、auto-merge 与发布流水线状态，落盘 R10 监控回执并在绿灯合并或红灯失败时唤醒／回报 TL。Job 级 Fail-Fast：看 required job 的 `failure`／`timed_out`／`action_required`，不要只等 Workflow Run 顶层 `.conclusion`；Run 仍 `in_progress` 时用 `actions/jobs/<id>/logs`，不用 `gh run view --log-failed`。**严禁 TL 主会话或 Writer 内联 `gh run watch`／长 `sleep` 等待远端 CI** |
| 交付 | 推进 PR 合法合并与发布读回 |

### Designer 派遣与反内联守卫

`my-designer`、`impeccable`、`dataviz-principles`、`hig` 等设计技能属于 `Designer`／`Writer` Subagent 的 Required Skills。Owner 首次提出 UI/UX 设计或要求重新设计界面、调整布局、增加展示指标时，TL 只与 Owner 对齐 1–2 个高层决策点，**严禁在 TL 主会话内调用 `skill("my-designer")`、自任 Designer、直接读取组件库源码亲自写设计方案**；必须通过 `herdr-dispatch.sh` 派发 `Explorer` → `Designer`。

TL 编写的 `briefs/designer*.md` 只能包含目标范围、高层约束、探查回执路径、输出路径和 Required Skills；不得预写完整组件选型、布局尺寸表或视觉规格。Designer 必须在自己的子代理会话首步显式加载 Required Skills，并输出 `design.md`／`spec.md` 与 `receipts/des-rN.md`。等待任何 Subagent 时先检查 `agent_status` 和磁盘 R10 回执；`blocked` 必须按 Tier A/A′ 解卡或记 `NEED_GO`，**严禁将 `herdr agent wait` 与 `herdr pane close` 无条件串联，也严禁 TL 代跑探查脚本或代写 `receipts/explore-*.md`／`receipts/des-*.md`**。

### Writer / Designer 技术栈 Skill 路由表（Apple & DataViz Skills Routing）

TL 编写 `briefs/<task>.md` 或派发 `Writer`（`worker.writer`／`writer_escalated`）与 `Designer` 时，必须在简报中声明按任务技术栈匹配的 `Required Skills`；执行者启动时先加载这些 Skills，再开始实现或设计。

| 任务技术栈 | Required Skills |
|---|---|
| SwiftUI 图表与数据可视化 | `guide-swiftui-charts`（`Prisma-Labs-Dev/apple-skills`） + `dataviz`／`dataviz-principles` + `my-designer` |
| SwiftUI 界面、布局与材质（iOS 26／macOS 26） | `swiftui` + `hig` + `ios-liquid-glass` + `guide-swiftui-ui-patterns` + `impeccable` |
| UI 视觉现代化与客观评审 | `visual-design-modernization` + `apple-design-reviewer` |
| Swift 6 严格并发（`@MainActor`／`Sendable`／Actor） | `guide-swift-concurrency` + `swift-concurrency` |
| SwiftData 持久化与模型 | `guide-swiftdata` + `swiftdata` |
| HealthKit／运动健康（`VitalStride`） | `healthkit` |
| macOS SwiftPM 打包、测试与构建 | `guide-macos-spm-packaging` + `guide-swift-testing` + `swift-testing` + `xcode-cli` |

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
该 TL 的 Goal pane/workspace；绝不越级创建、prompt 或关闭 TL 下属 Subagent。TL 全权
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

- **V1 双轴限定审查单位（`1 Task = 1 PR` 单次审查）**：每个 PR（即 1 个 Task）仅执行一次独立跨厂商双轴审查：
  - **Axis 1: Spec & Scope Compliance（规格与范围合规）**：核对实际 diff 是否完整实现 `briefs/<task>.md`（或冻结定义）声明的验收项与 `Interfaces: Consumes / Produces`、是否漏项、是否符合该 PR 声明的单一目的、是否在所属 Task 的 `Owned Paths` 授权范围内（含已记录的 `EVT=RULING`）、是否越界。
  - **Axis 2: Code Quality & Redlines（代码质量与红线）**：核对测试真实性与有效性、代码质量与可维护性、该 Task 自身的验证证据，以及安全／隐私／数据丢失与仓库红线。
  不设“多 PR 累计 Task 审查”或“Target 中间层汇总审查”；当所有必需 `Tasks` 全部 `done` 后，仅剩 Goal 级端到端验收检查。
  计划审查核对相同字段与映射及 R7 全部一致性项；首轮覆盖当前 Goal 下 Task DAG 的完整候选和对应验收。
  reviewer 直接读准确 revision 的定义／简报、diff 和命令证据，不以执行者勾选代替确认；按双轴记录验收项、位置、证据及结论。
  V1 必须对照原始 feedback manifest、Task brief 与实际 delivery；宽泛或错误的 brief 不能为 bundling 提供合法性。Reviewer 可核对结构化覆盖与证据存在性，但不把机器声明当作 common-root-cause 语义裁定或 GitHub merge 事实。
  内容 reviewer 不重复执行或裁定 build/test/lint/hooks/CI；执行者和 CI 提供命令结果，缺必要证据不能算通过。
- **V2 按引入来源判定阻塞**：按问题是否由本次 diff 引入区分，不按文件路径区分；本次 diff 引入的任何问题（不论影响哪个文件）、范围内验收失败、红线违规，以及任何安全／隐私／数据丢失问题都阻塞；除上述阻塞项外，本次 diff 未引入的原有问题仅记 backlog，不阻塞，协调者可按 R4 提议新 Task；修改声明范围外的文件且未获 `EVT=RULING` 或未按 R2 备案获批的范围扩张均阻塞。
- **V3 复审与失败自动升档（Failure-Tier Escalation）**：复审核对上一轮的问题和本次修复 diff；修复引入的问题按 V2 阻塞，未改动代码不重新扫描。
  **升档破局规则**：首轮（`r1`）Task Writer 必须使用 `worker.writer`（`copilot` `gpt-5.6-luna` → `mai-code-2-flash`），**严禁在 `r1` 首轮或 Planner 简报中直接指定 `writer_escalated`（`codex` `gpt-6.1-sol`）**；第 1 轮 FAIL 由原 Writer 修复；若第 1 轮修复后复审仍 FAIL（即进入第 2 轮修复），协调者才允许触发 **Failure-Tier Escalation**——改派高一档模型（`writer_escalated`，即 `gpt-6.1-sol`，见 [references/runtime-profiles.yaml](references/runtime-profiles.yaml)）带新鲜上下文（原简报、前序 diff、两轮 review findings 与验证日志路径）接手第 2 轮修复；两轮修复后复审仍不过才将受影响 Task 置为 `blocked` 并退回定义方重规划，不开第三轮修复；换 PR、SHA、agent、run 或任务名不重置历史；Goal 端到端整体验收另计两轮。
  复审携带原 findings、前后 revision、repair diff 和证据，逐项标记已修复／未修复并列出修复引入的回归；其余发现仍按 V2 判定阻塞或记入 backlog。
  新 SHA 需要该 SHA 的证据，但不扩大复审范围；定义改动仅审 R2／R4／R5 的 delta。
- **V4 CI 范围**：目标仓库的 CI 门禁照常有效；修改门禁本身是独立 Task 且触碰硬红线，须负责人（Owner）批准。
- **V5 审查结论**：PASS＝通过；CONCERNS＝仅有非阻塞项，记 backlog，不返工；WAIVED＝阻塞项豁免至少须协调者＋提出该项的独立 reviewer 同意，涉及验收项、红线或安全／隐私／数据丢失的豁免还须负责人批准，记录原因及批准人，仓库 required gates 仍须通过；豁免不能替代 R2／R4 的分级批准，未获批的范围扩张只能按 R2 补批，不可 WAIVED；FAIL＝存在未获豁免的 V2 阻塞项，仅此结论触发修复，仍受 V3／R9 重规划约束；每次结论按 R10 落盘。

复用已有、覆盖同一职责的独立审查，不堆叠重复全量审查；实现者现有完整 code-review 自检保留。
实现者回报 Task 输出及限制，独立验收按 V1 确认每个 Task 的 PR；PR URL／准确 head 的实现交接不等于合法合并。
Goal 的端到端、发布、实机或真实消费验收分别取证，不能从 Task 勾选、CI 绿或 PR merged 推断完成。

## CLI 用法

全景开发、复盘、反思与故事集飞轮见 [references/playbook.md](references/playbook.md)。

主会话（TL）担任协调者：审定计划、分配与协调；定义方可由独立 Planner agent 担任，主会话也可兼任；用户是负责人。
本合同不绑定具体模型；角色档位、模型、CLI 与启动参数只以 [references/runtime-profiles.yaml](references/runtime-profiles.yaml) 为源头，bot 分工与 CLI agent 生命周期见 [references/bots-and-agents.md](references/bots-and-agents.md)，台账与标准事件见 [references/ledger.md](references/ledger.md)。**Mac mini** 上 strong 档 `team_lead`／`arbitration` 在拉起 TL 前须做 Claude 周配额预检（周剩余 &lt; 20% 或读不到用量 → 直接 `copilot`/`gemini-4-argon`，见 `runtime-profiles.yaml` 与 bots-and-agents.md）。**诊断类 Goal**（查 CLI／`goal-task`／TL 为何慢、效果、用了哪个 skill；Owner 2026-10-26）的 TL 在 MacBook 与 Mac mini 均为 `copilot`/`gemini-4-argon`，不用 strong 档硬扛调研；不改变日常实现 TL。详见 bots-and-agents.md。
硬规则：Reviewer（含 Planner 的 Reviewer）与被审者使用不同家模型；启动显式指定模型，不依赖 CLI 默认值；降级只在同档内换可用模型并记入回执；不用 profile 列出的禁用参数（如 `--yolo`、`--allow-all-*`），Copilot CLI 角色统一使用 `runtime-profiles.yaml`（`common.copilot_allowlist`）中的显式 `--allow-tool`／`--deny-tool` 工具白名单与 `--add-dir` 目录白名单；Codex 沙箱按 profile 使用 `danger-full-access`（`-s danger-full-access`），不视为禁用项。
所有 CLI agent（含 Reviewer、Explorer、PR Monitor）经 herdr 启动并按 profile 命名，TL 在下属仍 working 时不得结束；每个 Goal 在台账头写明 Owner、协调者 bot、TL 与机器，事件只沿这条线传递；危险操作及明确 hold 例外只由 Owner 放行；普通 PR 合并与发布（04/tag/TestFlight）不需要 Owner GO，按仓库既定 auto-merge 条件合并（见 references/bots-and-agents.md）。
在 `HERDR_ENV=1` 且显式 ID 预检通过后，优先直接调用内置 fail-closed 辅助脚本 [`scripts/herdr-dispatch.sh`](scripts/herdr-dispatch.sh)（自动预注册 `~/.copilot/config.json` `trustedFolders` 与 `~/.codex/config.toml` 项目信任、自动注入 Copilot `--allow-tool`／`--add-dir` 白名单、对 `agent_pane_busy` 自动退避重试、`agent start` 后等待 `interactive_ready`（最多约 10s）再 prompt、遇 `agent_prompt_stalled` 或发送后仍 `idle` 自动 Enter 一次、`--wait` 的 5s stall 假阳性回退 `herdr agent wait`、必传 `--direction right|down`、开启 `set -euo pipefail` 并在 `NEW_PANE_ID` 为空时立即中止、自动剥离 `--` 后误传的重复二进制名）；若手写命令流，必须开启 `set -euo pipefail`，使用 `herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$TASK_WORKTREE" --no-focus`（窄／高窗格改用 `--direction down`，**严禁漏传 `--direction`**）创建绑定 worktree 的窗格，提取 `NEW_PANE_ID` 后立即执行 `test -n "$NEW_PANE_ID"` 校验非空，再按 `common.herdr_kind_map` 用 `herdr agent start <name> --kind <kind> --pane "$NEW_PANE_ID" -- <profile_flags>` 启动跨厂商 Subagent（`--` 后仅传模型与受限参数：默认首轮 `r1` Writer 使用 `--kind copilot -- --model gpt-5.6-luna --allow-tool write --allow-tool shell --allow-tool url --deny-tool 'shell(rm -rf /:*)' --deny-tool 'shell(herdr server stop:*)' --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user`，Codex Subagent 如 `planner`、`code_reviewer` 或 V3 第 2 轮 `writer_escalated` 使用 `--kind codex -- -m gpt-6.1-sol -c model_context_window=1000000 -s danger-full-access -a never`，**严禁在 `--` 后重复传 `codex`／`copilot` 二进制名**）；用 `herdr agent prompt <name> "Read briefs/<task>.md; implement only Owned Paths; run verification commands; write the full R10 receipt to <receipt-path> and return <15 lines." --wait` 派发（若为 Codex Subagent，须在启动时调用 `create_goal` 绑定 `briefs/<task>.md` 并在 R10 回执落盘后调用 `update_goal(status="complete"|"blocked")`），完成后用 `herdr agent read <name> --source recent-unwrapped` 读取。禁止裸 `--current`，禁止在 `working` 时读 alternate-screen 历史，**严禁在状态等待脚本中将 `blocked` 当作“非 `working` 已完成”直接退出并结束 TL 回合**；以 R10 回执和 Git 实际状态为唯一真值。一旦会话进入 `goal-task` 或存在活动 `ledger.md`，后续每轮用户追问或 Bug 反馈自动继承 TL 约束（Sticky TL），TL 只读探查上限 ≤3 次，超限或需改任何文件（含 `handoff/` 原型／spec／业务代码）必须经 `herdr` 派发 `Explorer` 或 `Writer`。完整预检、命名、等待、阻塞处理与清理模板见 [references/bots-and-agents.md](references/bots-and-agents.md)。
加载本合同后按可信调用来源和显式 assignment 绑定角色，复用已有决定与有效受审基线。

规划前或规划中按需（R11）派只读 Explorer（快速／长上下文只读模型即可），回报落盘后由定义方写入计划。

每个 Task 由主会话（TL）按 1-Hop 扁平调度派一个写者 subagent（`1 Task = 1 Writer = 1 Worktree = 1 PR`）；写者通过读取 `~/Development/handoff/<goal>/briefs/<task>.md`（含 1 句话背景、`Owned Paths`、`Interfaces: Consumes / Produces`、验收命令）执行，完整日志写入回执文件，向主会话只返回 `<15` 行精简结论。

同一 Goal 下无依赖的多个 Task 可并行，每个 Task 由主会话派一个写者 subagent，`Owned Paths` 不重叠（R7）并各用独立 worktree；有依赖的 Tasks 按 DAG 顺序推进，主会话只协调。
写者遇到非红线顺带缺口时回报 `NEEDS_RULING`，由主会话在台账记录 `EVT=RULING` 内联放行；各写者实现、验证、commit、push、开 PR，并回报准确 head 与回执路径。

独立验收：每个 Task 的 PR 由主会话另派一个未接触写者上下文且与写者不同家（cross-vendor）的 Reviewer agent，只读简报／定义、diff 和命令证据，按 V1 双轴（Axis 1: Spec & Scope Compliance + Axis 2: Code Quality & Redlines）单次审查。
第 1 轮 FAIL 由原写者修复；若第 1 轮修复后复审仍 FAIL，主会话按 V3 触发 Failure-Tier Escalation，改派 `writer_escalated`（高一档模型，带新鲜上下文）执行第 2 轮修复。
所有必需 Task 完成后，仅执行 Goal 端到端验收以关闭 Goal，无 Target 中间层二次审查。
审查按 V1–V5 限定范围，写者自检不替代独立验收。
主会话收集并转述写者证据与验收结论，在执行台账记录对应 agent 和会话 ID。

交付与 PR 状态监控委派（`pr_monitor` Subagent）：**严禁 TL 主会话或 Task Writer 直接内联运行 `gh pr checks`／`gh pr view`／`gh run watch`／长 `sleep` 循环轮询等待远端 CI 或合并**（Writer 完成本地验证与 `git push` 后必须立即落盘 R10 回执返回）。`pr_monitor` 的 cwd 只能是主仓、`$GOAL_HANDOFF` 或 `/tmp`，**禁止使用 `$TASK_WORKTREE`**。当 PR 开启、进入 CI／auto-merge 队列或触发发布流水线后，TL 必须通过 `scripts/herdr-dispatch.sh` 派发与 `Explorer` 同档位同模型序列（`worker.pr_monitor`：`mai-code-2-flash` → `gpt-5.6-luna`）的独立 `<goal>-<task>-pr-monitor-r<round>`（或整波 `<goal>-wave<N>-pr-monitor-r1`）Subagent 负责监控：推进或核验 auto-merge、持续盯梢 required checks 直至真实读回 PR 已合并（`PR_MERGED`），或**一旦任一 required job 结论为 `failure`/`timed_out`/`action_required`（Job 级 Fail-Fast，严禁死等整轮 Workflow Run 顶层 `.conclusion` 完结）**／出现冲突，立即用单 Job 日志接口（`gh api repos/<owner>/<repo>/actions/jobs/<failed_job_id>/logs | tail -n 120`，因整轮 Run 未结束时 `gh run view --log-failed` 会报错拒绝）抓取失败日志落盘 `~/Development/handoff/<goal>/receipts/<task>-pr-monitor-r<round>.md` 并回报 `PR_CHECK_FAILED`／`PR_CONFLICT` 交由 TL 派写者修复。Task PR 确认 `PR_MERGED`、R10 回执已落盘且其 Subagent 窗格已关闭后，TL 自动清理该已合并 Task 的 worktree 与分支（`git worktree remove` 加分支清理）；未合并／暂停（HOLD）的 worktree 仍须 Owner 确认。
补充收尾约束：Task PR 确认 `PR_MERGED`、R10 回执已落盘且其 Writer、Reviewer 与 `pr_monitor` panes 已关闭后，才可用 `git worktree remove` 清理 worktree；未合并／暂停（HOLD）／含未提交业务改动的 worktree 保留交 Owner。Goal 完成后仅由协调 Bot 在 `GOAL_DONE`、R10 齐全、worktrees 清理且无 `working`／`blocked` agent 时关闭 Goal workspace／TL pane，**严禁触碰共享 `w1`、Owner 手开或其他在途 Goal**。
主会话汇总台账，Goal 级端到端验收另行取证。
台账事件时间必须取自写入时刻的真实系统时钟（`date -Iseconds` 或 `date '+%Y-%m-%dT%H:%M:%S%z'`），严禁估算或回填时间戳；TL 事件使用 `id=E<N>`，协调者 Bot 旁路巡检／解卡事件使用 `id=B<N>` 或 `id=J<N>` 以防撞号。

1-Hop 扁平委派与沙箱约束（2026-10-19 / 2026-10-23）：Claude Code 的 subagent 不能再委派；Codex CLI 0.163 在 read-only sandbox 下第二层被拒（Operation not permitted）。
R6 默认采用 1-Hop Flat Tree：所有 Subagent 由主会话（TL）直接派发，Task 写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`）；若特殊场景需拆分同一 Task 的执行分片，由主会话并行派发、该 Task 唯一写者整合；工具能力不扩大合同授权。

台账为每个 Goal 一本只追加的 `~/Development/handoff/<goal>/ledger.md`（格式与事件见 [references/ledger.md](references/ledger.md)），goal.md 与看板只是派生视图；Lite 档仍以 PR 描述为台账；台账不改冻结定义；仅补进度或验证回执时，优先使用目标仓库已有、获准且不会重复触发 CI 的载体（台账或获准的评论），不为补回执编辑会触发 CI 的 PR 描述。
实际行为、范围、测试说明和 PR 模板仍按仓库规则更新；回执保留准确 head、角色归属、结论和未完成项，评论仍须原有发布授权。
状态列遵循 R8；验收证据另行区分内容批准、执行证据待齐、自动化验收通过、PR 已交接、合法合并并链接 R10 回执；done 须符合 Task 完成定义，发布与实机验收另行取证：

| Task | 依赖 | 写者 | 状态 | PR/head | 验收证据／回执 | 下一步 |
|---|---|---|---|---|---|---|
| 待分配 Task | — | 待分配写者 | planned | — | — | 通过 R7 并满足依赖后转 ready，再分配交接 |
