# Bot 与 CLI agent：分工与生命周期

本文件是本环境（Owner tianpei）的分工表与 CLI agent 生命周期规则；合同角色、权限与审查以 [SKILL.md](../SKILL.md) 为准，模型与启动参数只看 [runtime-profiles.yaml](runtime-profiles.yaml)，台账与事件见 [ledger.md](ledger.md)。常驻域主 Bot 与 Goal-level TL 的开 TL、热追加、巡检解卡和完成回收另见 [`bot-tl-orchestration`](../../bot-tl-orchestration/SKILL.md)。

## 两层

| 层 | 是什么 | 谁开 | 活多久 |
|---|---|---|---|
| bot 层 | 有独立聊天的常驻 bot，各自固定一台机器 | Owner | 长期 |
| CLI agent 层 | bot 或 TL 在 herdr 里启动的 CLI 会话（TL、Planner、Reviewer、写者、Explorer） | 协调者 bot / TL | 用完即关 |

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

不按“你是常驻 Bot 还是本地 CLI”死板切分讨论与执行，而是按 **所处阶段（Phase 0 vs. Phase 1）** 切换角色：

1. **Phase 0：交互共创期（Interactive Co-Design —— 讨论 / 方案 / 设计）**
   - **触发条件**：当 Owner 提出探讨性问题（“怎么设计比较好”、“先讨论方案”、“先出设计”），或需求存在明显的产品／交互／架构未决项时，当前对话窗口（无论是常驻域主 Bot 如 `Joy`／`Atlas`／`艺术家`／`flower`，还是 Owner 直接打开的本地 CLI 会话）处于 **Co-Design 顾问态**。
   - **允许动作**：
     - **只读探查（R11）**：按需派 1 个只读 `Explorer`（`mai-code-2-flash`／`gemini-4-argon` 或 Codex 只读）极速摸底代码事实（附 `file:line`）；
     - **需求对齐与收敛（Grill / Spec）**：结合 `grill-me`／`to-spec`／`codebase-design` 每次聚焦 1–2 个关键决策点与 Owner 交互；
     - **视觉与交互设计（UI/UX Design）**：涉及界面时按下方 [设计路由（按机器分流）](#design-routing-machine-split) 决定谁跑流水线。Mac mini 常驻域把产品 UI/UX 设计 Goal 交给 `艺术家` 做设计 TL 与终审；MacBook／`Joy` 与本地 CLI 在本机跑 `Explorer` → `Designer` → `Writer` → CI Design Gate。共享设计资产仍只引用 `shared-design-system`／`Strata`／原型预览，不另复制模型表或设计词典；
     - **产出冻结候选**：将讨论确认的结论落盘为 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`），复杂架构可派异构 `planner_reviewer` 预审盲区。
   - **Phase 0 禁令**：在 Owner 给出明确执行信号前，**严禁创建业务代码 Worktree、严禁派发 Writer 修改业务代码**。
2. **切换开关（Freeze / GO）**
   - 当 Owner 明确拍板（如“按这个做／开始执行／做”），或初始请求本身就是范围清晰、无歧义的直接实现指令时，进入 **Phase 1（Autonomous TL Execution）**。
3. **Phase 1：自治执行期（Autonomous TL Execution —— Bot 入口 vs. CLI 入口无缝衔接）**
   - **从常驻 Bot 入口启动时**：常驻 Bot 保持自身会话轻量，建好 `~/Development/handoff/<goal>/ledger.md` 后，在 `herdr` 中拉起后台独立的 **`<goal>-goal-tl-r1` CLI 会话**（若 Goal 涉及 `~/Development` 与 `~/orca` 之外的合法路径如 `~/actions-runner-*`、`~/Library/LaunchAgents`、`~/Library/Logs`、`~/.local/*`，必须在 `briefs/goal.md` 头声明 Goal 级 `extra_add_dirs`，并在启动父 TL 时一并传入 `--add-dir`、导出冒号分隔的 `HERDR_EXTRA_ADD_DIRS`，满足**父 TL 白名单 ≥ 子 Subagent 白名单不变量**（Parent ≥ Child）），将 `spec.md` 交给 `TL` 自治跑完 `Task 拆分 → Writer 实现 → Reviewer 双轴审查 → PR Auto-merge 读回`；常驻 Bot 除监听台账的 `NEED_GO`／`NEED_OWNER_DECISION`／`DONE` 事件外，须周期巡检 `herdr agent get <goal>-goal-tl-r1` 的 `agent_status`（防止 TL 卡在 CLI 权限弹窗无法写台账）。
   - **从本地 CLI 入口启动时**：当前 CLI 主会话直接原地切换为 **`TL (Coordinator)`**（或者当 Owner 希望腾出当前终端时，由当前 CLI 在 `herdr` 拉起后台 `<goal>-goal-tl-r1`），按 `Lite`（单 Task）或 `Standard`（多 Task DAG）1-Hop 派发 `Writer` 与跨厂商 `Reviewer` Subagent，当前 CLI 主会话自身绝不直接下场写多文件业务代码或自审。

<a id="design-routing-machine-split"></a>
<a id="bot-与-cli-的设计路由-owner-2026-10-24"></a>

### 设计路由（按机器分流；Owner 2026-10-28，Hotty → flower）

产品 UI/UX 设计 Goal 的阶段与 CLI／`goal-task` 设计流相同。模型档位只以 [runtime-profiles.yaml](runtime-profiles.yaml) 为唯一来源；七域花名册只以本节「bot 分工」为唯一来源。本路由只决定谁开设计 TL、谁做设计总审，不复制模型表。

- **流水线固定顺序**：`Explorer`（只读事实，附 `file:line`）→ `Designer`（`writes: handoff-only`，只写 `~/Development/handoff/<goal>/design.md`）→ `Writer`（Demo／UI + 四态 `populated/empty/loading/error` 与双外观 `light/dark` 截图）→ CI Design Gate（`shared-ci` 的 `scripts/context/design_contract.py`）→ 跨厂商 `Reviewer`。`shared-design-system`（`ai/DESIGN-CONTRACT.md`）、项目 `docs/design-contract.md` 与既有 `Strata`／原型预览仍是共享设计资产的单一事实源，各机器只引用、不另存一份。
- **Mac mini 常驻域（Bot → `艺术家` 终审）**：`flower`／`Atlas`／`艺术家`／`ana`／`笔者`，以及在 Mac mini 上做协调的 `Hotty`，其产品 UI/UX 设计 Goal 仍走既有 **Bot → `艺术家`** 路径，由 **`艺术家`** 开设计 TL 并做设计总审。`艺术家` 在 `herdr` 拉起该设计 Goal 的 TL（`<goal>-goal-tl-r1`），派发上述流水线，并依据 `design.md`、项目契约、四态／双外观截图与 CI 结果执行设计总审门禁。`艺术家` 仍只派发与审证据，不亲自写 `design.md` 或实现代码。这些域主 Bot 不在本域 Goal TL 内跑该设计流水线。`Hotty` 不做具体 Goal 协调的边界不变。
- **MacBook／`Joy`（本机设计流水线）**：设计不再经 `艺术家`。`Joy` 在 MacBook 上、于 goal-task 内本地跑完同一流水线：`Explorer` → `Designer` → `Writer` → CI Design Gate（随后仍是跨厂商 `Reviewer`，与 CLI／`goal-task` 设计流同阶段）。`Joy` 不把设计 Goal 转交 Mac mini 上的 `艺术家`，也不操作 Mac mini。
- **CLI 入口**：本地 CLI／TL（不经常驻域主 Bot）在本 Goal 内主动跑完该流水线，不转交 `艺术家`。阶段与 MacBook／`Joy` 的本机流水线相同。
- Owner 2026-10-24「常驻 Bot 的设计需求一律交 `艺术家`」由本决定按机器拆开：Mac mini 常驻域保持 Bot → `艺术家` 终审；MacBook／`Joy` 改为本机设计流水线。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

常驻域主 Bot（`Joy`／`Atlas`／`flower` 等）与 `TL` 通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py)（基于 `~/Development/handoff/<goal>/.queue.lock` 的 `fcntl.flock` 文件锁，原子同步 `queue.json` 与 `ledger.md`）协同处理批量输入与运行中热追加：

1. **姿势 1（Batch Mode 一次性塞多个任务）**：
   - Owner 一次性向 Bot 或 `TL` 提供多条任务时，调用 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json <json-string-or-file>`。
   - 脚本自动按 `(repo, paths)` 冲突检测计算 `auto_dependencies`：跨仓或同仓路径不相交的任务进入**并行波次（Parallel Wave）**，同仓且路径重叠（含前缀／glob 重叠或空路径 `*` 通配）的任务自动串入前序未完成任务之后形成**串行链（Sequential Chain）**，并在 `ledger.md` 记录 `EVT=READY`。
   - `TL` 循环调用 `skills/goal-task/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal> --mark-in-progress` 派发每波无冲突任务，待任务完成调用 `set-status --status done`，直至 `drained: true`。
2. **姿势 3（Hot-Append Mode 运行中随时热追加）**：
   - 当 `TL` 已在运行中，Owner 向常驻 Bot（`Joy`／`Atlas`／`flower`）或 `TL` 发送新任务时，**Bot 绝不重复拉起第二个 TL**，而是直接运行 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> ...`（获取 `.queue.lock`、对照在途任务自动计算并行／串行依赖边、在 `ledger.md` 记录 `EVT=HOT_APPEND`）。
   - 若 `<goal>-goal-tl-r1` 窗格处于 `idle` 或 `done`，Bot 执行
     `herdr agent prompt <goal>-goal-tl-r1 "Hot-append task <id>; run goal-queue.py next-wave"` 唤醒
     `TL`；发送后等待 2 秒，读取 `agent_status`，若仍为 `idle` 或 `done`，仅补发一次
     `herdr agent send-keys <goal>-goal-tl-r1 enter`，防止粘贴结束符吞掉回车而停在
     `❯` 输入框。`TL` 在任一波次收口退出前固定重跑一次 `goal-queue.py next-wave`，
     确保热追加任务被彻底排空（Drain to Zero）。

### 两级完成与清理边界

- **1-Hop Communication & Lifecycle Ownership（单层沟通与管辖）**：每次只沟通一层
  Agent。独立 Goal TL 存在时，常驻 Bot 只沟通、唤醒、解卡并在 `GOAL_DONE` 时回收
  Goal TL pane/workspace；不得越级创建、prompt 或关闭 TL 下属的 Explorer／Designer／
  Writer／Reviewer／`pr_monitor`。TL 独占这些 Subagent 的派发、更新、Task／阶段完结
  关窗与回收。Bot 或本地 CLI 直接担任 TL 时，则由其直接管辖下属 Subagent。
- `pr_monitor` 必须从主仓或 `~/Development/handoff/<goal>` 启动，不能占用 `$TASK_WORKTREE`；如果曾在任务 Worktree 中运行，先关闭其 pane，再执行 Worktree 清理。
- **Task / `PR_MERGED`**：TL 或清理 Subagent 收到准确合并读回与 R10 回执后，关闭任务 pane，清理 `__pycache__`，执行 `git worktree remove "$TASK_WORKTREE"`、`git worktree prune` 与 `git branch -D "$TASK_BRANCH"`。未合并、HOLD 或 dirty Worktree 保留并报告 Owner。
- **Goal / `GOAL_DONE`**：仅当所有 Task `done`、`drained: true` 且 ledger 记录 `EVT=GOAL_DONE` 时收口；TL 可延迟自闭 pane，Bot 再确认没有 `working`／`blocked` Agent 或合并 Worktree，必要时只关闭该 Goal 的 TL pane/workspace，不触碰 `w1`、Owner 会话或活动 Goal。

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

- **会话粘性（Sticky TL Rule）**：一旦当前会话已进入 `goal-task`（或存在活动的 `~/Development/handoff/<goal>/ledger.md`），**该会话后续每一轮用户追问或反馈都自动继承 `TL (Coordinator)` 合同约束——即使 Owner 没有再次输入 `/goal-task`**（例如口语化 Bug 反馈与追问：`"还是点不动呀"`、`"还没有找到原因吗"`、`"改一下配色"`）。
- **只读诊断上限（Read-Only Diagnostic Cap，≤3 次直接探查）**：收到用户 Bug 反馈或追问时，TL 最多执行 **2–3 次快速只读检查**以定位受影响的 Task 与文件路径；若排查需要深入的多文件／二进制逆向分析（`>3` 次工具调用）或**任何文件修改**（包括修改 `handoff/` 原型、规格说明 `spec.md`／`design.md` 或仓库业务代码），TL **必须**通过 `herdr` 派发 `Explorer` 或 `Writer` Subagent（`<goal>-<task>-writer-rN`）并记入 `ledger.md`，**严禁在 TL 主会话内直接内联调试或编辑文件**。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`~/Development/handoff/<goal>/ledger.md`、`briefs/<task>.md` 与 Git 实际状态始终是跨进程唯一事实源（Single Source of Truth）；各 CLI 的原生 Goal／Todo 工具作为**进程内持续推进引擎（In-Process Continuation Engine）**挂载并同步，防止 TL 或 Subagent 提前停机或无限空转：

- **Codex CLI（`goals = true` / `thread_goals`）**：
  - Phase 1 启动时（TL）或 Task 启动时（Writer／Reviewer），首个动作调用 `create_goal(objective="...")`，将目标绑定到 Goal `ledger.md` 或 Task `briefs/<task>.md`。
  - **仅当**全部验收命令通过、PR 完成交接／合并且磁盘 R10 回执已落盘核实后，才允许调用 `update_goal(status="complete")`。
  - 遇到 `TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮修复仍失败时，立即调用 `update_goal(status="blocked")`，**严禁留在活动态空转**。
- **Copilot CLI（`sql` `todos` + `todo_deps`）**：
  - TL 在 Phase 1 启动时将 Goal 的 `Tasks` 及依赖 DAG 边镜像写入会话 SQLite 的 `todos` 与 `todo_deps` 表，随 `ledger.md` 同步更新状态（`pending -> in_progress -> done | blocked`）。
- **Claude Code（`TodoWrite` / `TaskCreate`）**：
  - TL 将活动 `Tasks` 投影到 `TodoWrite` 以提供实时进度可见性，同时保持 `ledger.md` 为唯一权威状态源。

## bot 分工（唯一一处）

Owner 2026-10-23 的七域分工：每个域一个主 bot。本表是花名册的唯一来源。

| 域 | 主 bot | 机器 | 范围 | 不做 |
|---|---|---|---|---|
| 设计 | 艺术家（artist） | Mac mini | 设计域的定义方；Mac mini 常驻域产品 UI/UX 设计 Goal 的设计 TL 与终审 | 不放行发布、合并；不亲自写 `design.md` 或实现代码 |
| retro + 反思 | ana | Mac mini | retro 与反思 | |
| 文章形成 | 笔者（writer） | Mac mini | 文章形成 | 与 CLI 层 Task 写者（`writer`）不是同一身份 |
| 产品开发 | Atlas | Mac mini | 产品应用（VitalStride、VoxPocket 等） | |
| 流程/方法（含基建） | flower | Mac mini | workflows 01–06、user-skills/GTT，以及 herdr、CLI、shared-ci、tooling | 具体盯梢由 stuck-PR bot + Mac mini 只读心跳负责；flower 负责每日摘要聊天与危险操作或 hold 例外需 Owner 决定时的路由；可执行的卡住 PR 走 supervise-and-fix 而非默认 Owner |
| 总控 | Hotty | Mac mini | 七域矩阵与跨域摘要；Owner 问时从各 Goal 台账汇总超时与卡住事件 | 不做具体 Goal 协调，不做 Task 写者 |
| MacBook | Joy | MacBook | MacBook 上 Goal 的协调者；本机产品 UI/UX 设计在 goal-task 内跑设计流水线 | 不操作 Mac mini；设计不转交 `艺术家` |

- fonder（Mac mini）只作为 flower 域下的可选帮手，不占域主。
- pigger（Mac mini）在七域之外：Owner 的个人咨询顾问，可按 GTT 做自己的咨询类工作；不接触 Owner 的这些项目（仓库、台账、handoff 中的 Goal）。
- 每个 bot 只操作自己的机器；跨机器只通过 handoff 交接（[ledger.md](ledger.md) 的 inputs 规则）。
- Owner（用户）是危险操作及明确 hold 例外的唯一放行人；普通 PR 合并与发布（04/tag/TestFlight）不需要 Owner GO——普通 PR 在准确 head 的 required checks 通过并完成独立审查后，按仓库既定 auto-merge 流程合并，再读回合并结果。bot 转述不产生新的放行授权；已有明确 Owner 授权须在台账记录原话或来源。
- 设计需求按机器分流：Mac mini 常驻域走 Bot → `艺术家` 终审，MacBook／`Joy` 与 CLI 走本机设计流水线。细则见上方 [设计路由（按机器分流；Owner 2026-10-28）](#design-routing-machine-split)。

## 每个 Goal 的责任线

每个 Goal 在台账文件头写明：**Owner、协调者 bot、TL（herdr 名 + 模型）、机器、合同 SHA**。信号（事件）只按这条线走：

```
写者/Reviewer/Explorer → TL → 协调者 bot → Owner
```

- 协调者 bot 每个 Goal 只有一个：开 TL、收 TL 事件、需要 Owner 决定时去问。
- TL 按 **`Goal -> Task` 两层模型（`1 Task = 1 Writer = 1 Worktree = 1 PR`）** 划分垂直切片并写 `briefs/<task>.md`（Lite 档单 Task 免独立台账），按 **1-Hop Flat Tree** 在 herdr 里统一开 Explorer、Planner、Reviewer、写者（含 `writer_escalated`）；不兼任写者或独立验收（SKILL.md「角色绑定与交接」）。写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`）。
- Hotty 只读台账做跨域摘要，不在具体 Goal 的协调线上；线上没人接的事件由 Owner 问 Hotty 时查出。

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

## CLI agent 角色与档位

| 角色 | 档位（见 profile） | 谁开 | 生命周期 |
|---|---|---|---|
| Team lead | strong | 协调者 bot | 整个 Goal |
| 裁决 | strong | TL / 协调者 | 一次裁决 |
| Planner | mid | TL | 一轮计划 |
| Planner 的 Reviewer、代码 Reviewer | mid，**与被审者不同家** | TL | 一次审查（每轮新会话） |
| 写者（`writer`） | worker | TL | 一个 Task（不递归派 subagent） |
| 升档写者（`writer_escalated`） | mid | TL | **仅限 V3 第 2 轮审查失败升档**（严禁在首轮 `r1`、Planner 简报或常规修改中直接派发 `writer_escalated`／`gpt-6.1-sol`；新鲜上下文，不递归派 subagent） |
| Explorer、机械活 | worker | 任何 bot / TL | 一次调研或一次活 |
| PR 监控者（`pr_monitor`） | worker（**与 `Explorer` 相同模型序列**） | TL | 一次 PR／波次 CI 与 auto-merge 状态监控（Job 级 Fail-Fast；禁止 TL 与 Writer 内联 `gh run watch`／长 `sleep`） |
| 调研主 + 旁听 | research，旁听与主不同家，强档裁决 | 协调者 / TL | 一次调研 |

## 诊断类 Goal 的 TL（仅此类；Owner 2026-10-26，Joy → Hotty → flower）

**只适用于**以「为什么慢／效果如何／用了哪个 skill」为目的的诊断 Goal：调查 CLI、`goal-task` 或 TL 过慢、有效性，或 skill 选用回顾。不替换日常实现类 Goal 的 TL，也不改会话内 Bug 追问所继承的在途 TL。

| 机器 | 诊断 Goal 的 TL |
|---|---|
| MacBook | `copilot` + `gemini-4-argon`（与该机日常 strong `team_lead` 相同） |
| Mac mini | 同一类诊断对齐 gemini-4 档：`copilot` + `gemini-4-argon` |

Mac mini 上此类诊断**不得**用 strong 档（`claude` + `opus`）硬扛调研，也**不**走下一节 Claude 周配额预检。日常实现 TL 不变：MacBook 仍是 `copilot`／`gemini-4-argon`；Mac mini strong 仍是 `claude`／`opus`，周剩余 `weekly_remaining_pct` &lt; 20%（或测不到）才改 `copilot`／`gemini-4-argon`。默认档位仍以 [runtime-profiles.yaml](runtime-profiles.yaml) 为准；本表不改那些默认项。

## Mac mini strong：Claude 周配额预检（team_lead / arbitration）

**范围**：仅 `machines.mac-mini.strong` 的 `team_lead` 与 `arbitration`（首项为 `claude` + `opus`，备份为 `copilot` + `gemini-4-argon`）。Joy / MacBook 的 strong 档不使用 Claude，**不适用**本规则。

**Owner 规则**：在 herdr 拉起 `<goal>-goal-tl-r1`（或 strong 档 `arbitration`）**之前**，若 Claude 订阅的 **周剩余用量 `weekly_remaining_pct` &lt; 20%**，协调者必须**直接**以备份档启动（`copilot` + `gemini-4-argon`），**不得**先起 Claude 等撞墙后再切换。合同字段见 [runtime-profiles.yaml](runtime-profiles.yaml) 中 `machines.mac-mini.strong.quota_precheck`（`threshold_lt: 20`、`action: use_next`、`on_measure_failure: use_next`）。

**选择算法（herdr / bot 实现）**：

1. 读取当前机器与角色的 profile 列表（`team_lead` 或 `arbitration`）。
2. 若 `quota_precheck` 存在且 `list[0]` 匹配 `primary_match`（`cli: claude`），则测量 `weekly_remaining_pct`（见下节 Measurement）。
3. 若 `weekly_remaining_pct < 20` **或**测量失败（未知），选用 `list[1]`（备份）作为**初始** `--kind` / `--` 参数；否则选用 `list[0]`。
4. 在 `ledger.md` 头或 TL 启动回执中记一行：`TL_PROFILE=… | quota_precheck=pass|skip_low_quota|skip_unknown`。

### Measurement（如何读取 `weekly_remaining_pct`）

合同指标 **`weekly_remaining_pct`** = 100 − **`rate_limits.seven_day.used_percentage`**（Claude Code 7 日窗口**已用**百分比，0–100）。与交互式 `/usage` 中 “Current week (all models)” 的 **used** 一致。

| 方式 | 用法 | 解析 |
|---|---|---|
| **缓存（推荐，预启动）** | Mac mini 上由 Claude Code **statusline** 脚本在每次 API 响应后写入 `~/.config/claude/gtt-weekly-remaining-pct.json`（见 [statusline 文档](https://code.claude.com/docs/en/statusline) 的 `rate_limits.seven_day`） | 若 `observed_at` 距今 ≤ `cache_max_age_seconds`（900），读 `weekly_remaining_pct`；否则视为过期，走下一行 |
| **Statusline stdin JSON** | 从 statusline 收到的 JSON 取 `rate_limits.seven_day.used_percentage` | `weekly_remaining_pct=$((100 - USED))`（`USED` 为空则测量失败） |
| **交互式核对** | 已有 Claude Code 会话中执行 `/usage` | 周窗口 **used** ≥ 80% 等价于 `weekly_remaining_pct` &lt; 20% |

**失败模式（fail-closed）**：无法得到可信的 `weekly_remaining_pct`（字段缺失、缓存过期、非 Pro/Max 订阅无 `rate_limits`、命令非零退出等）→ 按 `on_measure_failure: use_next` **直接起备份** `copilot` + `gemini-4-argon`，避免意外撞周限额。不得在未测量且周余量可能不足时默认冒险使用 `claude`/`opus`。

**Statusline 解析示例**（协调者脚本可内联；stdout 一行整数 0–100 即 `weekly_remaining_pct`）：

```sh
set -euo pipefail
USED="$(jq -r '.rate_limits.seven_day.used_percentage // empty' <<<"$CLAUDE_STATUSLINE_JSON")"
if [ -z "$USED" ]; then exit 1; fi
echo $((100 - "${USED%.*}"))
```

## 生命周期（herdr）

以下命令均在已进入 Herdr 的协调者 pane 中执行；变量只表示当前 Goal 的运行时值，不得写入个人路径、令牌或密钥。

1. **全部进 herdr（优先使用 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh)）**：所有 CLI agent（含 Reviewer、Explorer、一次性 `codex exec`）都在 herdr 中启动，有可检索的名字；不留 herdr 外的临时进程。优先直接调用 fail-closed 辅助脚本 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh) 一站式完成分屏、`NEW_PANE_ID` 校验、启动与 prompt 派发：

   ```sh
   set -euo pipefail
   skills/goal-task/scripts/herdr-dispatch.sh \
     --name "merge-tk1a-writer-r1" \
     --kind copilot \
     --cwd "$TASK_WORKTREE" \
     --direction right \
     --prompt "Read briefs/<task>.md; implement only Owned Paths; run verification commands; write the full R10 receipt to <receipt-path> and return <15 lines." \
     --wait --timeout 600000 \
     -- --model gpt-5.6-luna
   ```

2. **环境与显式 ID 预检**：先确认当前会话及所有控制器 ID，禁用裸 `--current`：

   ```sh
   set -euo pipefail
   test "${HERDR_ENV:-}" = 1 &&
     test -n "${HERDR_SOCKET_PATH:-}" &&
     test -n "${HERDR_WORKSPACE_ID:-}" &&
     test -n "${HERDR_TAB_ID:-}" &&
     test -n "${HERDR_PANE_ID:-}"
   herdr pane list --workspace "$HERDR_WORKSPACE_ID"
   ```

   从 `pane list` 输出核实当前控制器的精确 `pane_id` 与 `HERDR_TAB_ID`、`HERDR_WORKSPACE_ID` 匹配后，才允许继续。多 tab／workspace 下对 `split`、`resize` 等操作始终传显式 pane ID，不依赖裸 `--current`。
3. **命名与分屏绑定**：命名遵循 `<goal>-<task>-<role>-r<round>`，例如 `merge-tk1a-writer-r1`、`merge-tk1a-reviewer-r2`；Goal 级角色用 `<goal>-goal-tl-r1`。先看当前布局，再按宽窄选择方向（**严禁省略 `--direction right|down`**）：

   ```sh
   set -euo pipefail
   herdr pane layout --pane "$HERDR_PANE_ID"
   # 宽窗格用 right；窄／高窗格用 down
   SPLIT_JSON="$(
     herdr pane split \
       --pane "$HERDR_PANE_ID" \
       --direction right \
       --cwd "$TASK_WORKTREE" \
       --no-focus
   )"
   NEW_PANE_ID="$(printf '%s' "$SPLIT_JSON" | jq -er '.result.pane.pane_id')"
   test -n "$NEW_PANE_ID"
   ```

   若布局判断为窄／高窗格，将 `--direction right` 改为 `--direction down`。`NEW_PANE_ID` 必须来自返回 JSON 的 `.result.pane.pane_id` 且通过 `test -n "$NEW_PANE_ID"` 校验非空（若为空必须立即中止，严禁继续执行 `agent start`／`agent prompt`），不可猜测或复用其他 pane。
4. **按 profile 启动具名 Subagent**：显式带 profile 中的模型及沙箱／只读参数；`--kind` 使用 `runtime-profiles.yaml` 的 `common.herdr_kind_map`（特别是 `cursor-agent` 必须传 `--kind cursor`，不能把 binary 名传给 Herdr）。`--` 之后**只传 `[AGENT_ARG]...`**，**严禁在 `--` 后重复传 `codex`／`copilot` 等二进制文件名**。名字需满足 `[a-z][a-z0-9_-]{0,31}`。例如：

   ```sh
   set -euo pipefail
   test -n "$NEW_PANE_ID"

   WRITER_NAME="merge-tk1a-writer-r1"
   herdr agent start "$WRITER_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model gpt-5.6-luna \
       --allow-tool write --allow-tool shell --allow-tool url \
       --deny-tool 'shell(rm -rf /:*)' --deny-tool 'shell(herdr server stop:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

   REVIEWER_NAME="merge-tk1a-reviewer-r1"
   herdr agent start "$REVIEWER_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model gemini-4-argon --reasoning-effort high --context long_context \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(git:*)' --allow-tool 'shell(gh:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(shasum:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

   PR_MONITOR_NAME="merge-tk1a-pr-monitor-r1"
   herdr agent start "$PR_MONITOR_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model mai-code-2-flash \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
       --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user
   ```

   `herdr agent start --timeout` 的单位是毫秒（3000–300000）；`scripts/herdr-dispatch.sh` 在 `herdr pane split --direction` 之前校验 `--name` 匹配 `^[a-z][a-z0-9_-]{0,31}$`（≤32 字符），并把 `$TASK_WORKTREE`、`~/Development`、`~/Development/handoff`、`~/Development/worktrees`、`~/orca`、`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders` 以及 `HERDR_EXTRA_ADD_DIRS`（冒号分隔，对应 `briefs/goal.md` 的 Goal 级 `extra_add_dirs`）写入 `~/.copilot/config.json`（`trustedFolders`）与 `~/.codex/config.toml`（`trust_level = "trusted"`；同一 `[projects."…"]` 只保留一块，并清掉文件里已有的相邻重复 trust 块）。对 `copilot`，`--add-dir` 改为叠加去重：无论调用方是否已传 `--add-dir`，始终补齐 `$CWD`、`~/Development`、`~/.copilot`、`~/.agents`、`~/.codex`、`~/orca`、`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders` 与 `HERDR_EXTRA_ADD_DIRS`，父 TL 白名单必须覆盖子 Subagent（Parent ≥ Child）。并对新分屏 shell 未就绪导致的 `agent_pane_busy` 自动退避重试（最多 5 次）；不接受 bypass-permissions 提示，改用 profile 中的显式白名单参数。
5. **文件指针派发、PR 状态监控委派（`pr_monitor`）与状态等待**：prompt 只携带 `briefs/<task>.md`、R10 回执路径与 Codex 原生 `create_goal`／`update_goal` 绑定指令，不粘贴长合同。单任务同步执行：

   ```sh
   set -euo pipefail
   test -n "$NEW_PANE_ID"
   herdr agent prompt "$WRITER_NAME" \
     "First call create_goal bound to briefs/<task>.md; read briefs/<task>.md; implement only Owned Paths; write the full R10 receipt to <receipt-path>; then call update_goal(status=\"complete\"|\"blocked\")." \
     --wait --timeout 600000
   ```

   `herdr-dispatch.sh` 在 `agent start` 成功后轮询 `interactive_ready`（最多约 10s，间隔 0.5s）再 `agent prompt`。若返回 `agent_prompt_stalled`（`--wait` 约 5s 初态检测的假阳性）或发送后约 2s 仍为 `idle`，脚本自动发送一次 Enter（`herdr agent send-keys <name> enter`）提交输入框；指定了 `--wait` 时改走 `herdr agent wait`，不要把这次 5s stall 当成派发失败，也不要盲目重发整段 prompt。手写命令流同样只补一次 Enter 再 `agent wait`。多个无依赖 Task 并行时，依次使用不带 `--wait` 的 `agent prompt`，再用 `herdr agent get <name>` 确认各自进入 `working`，随后分别执行 `herdr agent wait <name> --timeout 600000`。长任务可在完成边界通过 `herdr agent prompt <tl-name> "DONE <task>: receipt at <path>"` 反向唤醒 TL pane。
   **PR 状态监控必须派发 `pr_monitor` Subagent（严禁 TL 与 Writer 内联轮询）**：当 PR 已创建或进入 CI／auto-merge／发布流程时，TL **严禁**在主会话内跑 `for/while ... gh pr checks / sleep` 循环，Writer **严禁**在会话内执行 `gh run watch` 或 `sleep >= 30` 等待远端 CI（本地验证、`git push` 与 R10 回执落盘后立即返回）。必须通过 `scripts/herdr-dispatch.sh` 派发与 `Explorer` 同档位同模型的 `pr_monitor` Subagent（`mai-code-2-flash` → `gpt-5.6-luna`）。`pr_monitor` 做 Job 级 Fail-Fast：轮询 `gh pr checks <pr> --json name,state,bucket,link` 或 `gh api repos/<owner>/<repo>/actions/runs/<run_id>/jobs`，**不得**只看 Workflow Run 顶层 `.conclusion`（同 Run 仍有 Job 在排队或运行时它恒为 `null`）。任一 required job 为 `failure`／`timed_out`／`action_required` 即停止轮询并回报。Run 仍 `in_progress` 时 `gh run view <run-id> --log-failed` 会报 `run is still in progress`，必须改用单 Job 日志：`gh api repos/<owner>/<repo>/actions/jobs/<failed_job_id>/logs`。

   ```sh
   set -euo pipefail
   skills/goal-task/scripts/herdr-dispatch.sh \
     --name "merge-tk1a-pr-monitor-r1" \
     --kind copilot \
     --cwd "$GOAL_HANDOFF" \
     --direction down \
     --prompt "Monitor PR #<num> in <owner/repo> until the PR merges or a required job fails. Poll job conclusions (gh pr checks or actions/runs/<run_id>/jobs). Do not wait on the workflow run .conclusion while sibling jobs are still queued. On failure, timed_out, or action_required, fetch that job with gh api repos/<owner>/<repo>/actions/jobs/<failed_job_id>/logs (gh run view --log-failed errors while the run is in_progress). Write the full R10 receipt to <receipt-path> and return <15 lines with PR_MERGED | PR_CHECKS_GREEN | PR_CHECK_FAILED | PR_CONFLICT." \
     --wait --timeout 600000 \
     -- --model mai-code-2-flash \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
       --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user
   ```

6. **输出读取、阻塞处理与 R10 对账**：`working` 状态禁止读取 alternate-screen 历史；运行中仅可探查：

   ```sh
   herdr agent read "$WRITER_NAME" --source visible --lines 40
   ```

   等 agent 进入 `idle`／`done` 后，才读取精简结论：

   ```sh
   herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
   ```

   **严禁把 `blocked` 当成完成退出（含 Bot 盯 TL 与 TL 盯 Subagent）**：任何状态检查或巡检一旦发现 `agent_status == "blocked"`（或 `agent_blocked`），必须立即用 `herdr agent get <name>` 与 `herdr agent read <name> --source visible --lines 40` 检查审批／提问界面，**严禁仅判断 `status != "working"` 就 `break` 退出循环**。若弹窗为 `Allow path access` 且所列路径均属于临时目录（`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders`）、基线白名单或当前 Goal/Task 简报已声明的 `extra_add_dirs` 目标路径，立即发送 `herdr agent send-keys <name> enter`（默认高亮项 `Yes, and remember these paths for this session`）解卡并确认转回 `working`；若涉及未声明的仓外／敏感路径或高危操作，严禁盲按 Enter，记 `EVT=NEED_GO` 上报 Owner 裁决。Herdr 的 `idle`／`done` 只是终端就绪提示，不是完成证明；TL 必须以落盘的 R10 回执文件和 Git 实际 commit SHA／PR 状态对账为准。

   **Tier A′（`CMD_CONFIRM`）**：可见窗格出现 Copilot／CLI「Do you want to run this command?」或等价的命令确认时，必须先用 `herdr agent read <name> --source visible` 核对命令文本与当前高亮项；仅当命令属于白名单——`gh`、非破坏性 `git`（不含 `commit`／`push`／`merge`／`rebase`／`reset`／`checkout` 及其它改写历史或工作区的子命令）、`python3`、`jq`、`herdr`（不含 `herdr server stop`），以及 Allow-path／herdr 操作已经允许的同类安全操作（例如 `shasum`、`date`、`sleep`、`env`）——才可按键解卡并记 `EVT=UNBLOCK`。当前高亮已是安全的运行项时发送 `herdr agent send-keys <name> enter`；界面若提供 don't-ask-again／remember，且记住范围仍限于这条已核对的白名单命令，用 Down+Enter（`herdr agent send-keys <name> down`，随后 `herdr agent send-keys <name> enter`，或本仓已建立的等效按键序列）选中该安全记住项。敏感、破坏性或未声明的命令保持 hold，记 `EVT=NEED_GO` 或 `NEED_OWNER_DECISION`，严禁盲按 Enter，不得把 blocked 当作完成。**`--no-ask-user` 不批准 `CMD_CONFIRM` 或 Allow-path**（即便启动参数带有该旗标，也不抑制这两类提示），不得靠它解卡；MacBook `team_lead` 故意不带 `--no-ask-user`（Owner 2026-10-26 对可选 B 暂不），不改 `runtime-profiles.yaml` 或 `herdr-dispatch.sh`，也不开启 `--yolo`。
7. **结束与清理**：TL 在下属还有 `working` 时不得结束；先收齐下属回执（R10），在台账记 `DONE`，再关闭本次 Task 的 Writer、Reviewer 与 `pr_monitor` panes：

   ```sh
   herdr pane close "$NEW_PANE_ID"
   ```

   **已合并（`PR_MERGED`）的 Task 自动清理**：当 Task PR 已读回确认 `MERGED`、R10 回执已落盘且绑定该 worktree 的 Writer、Reviewer 与 `pr_monitor` panes 已关闭后，先清理 `__pycache__`、临时日志等临时产物，再由 TL 在主仓执行：

   ```sh
   # Equivalent cleanup command from the main-repository checkout:
   # git worktree remove "$TASK_WORKTREE"
   git -C "$MAIN_REPO" worktree remove "$TASK_WORKTREE"
   git -C "$MAIN_REPO" worktree prune
   git -C "$MAIN_REPO" branch -D "$TASK_BRANCH"
   git push origin --delete "$TASK_BRANCH" 2>/dev/null || true
   ```

   **Goal 自动清理**：仅当协调 Bot 已确认 `GOAL_DONE`、全部 R10 回执齐全、Task worktrees 已清理且该 Goal 没有 `working` 或 `blocked` agent 时，由协调 Bot（不是 TL 自关）关闭该 Goal 的 workspace 或 TL pane，例如 `herdr workspace close "$GOAL_WORKSPACE"`。严禁触碰共享 `w1`、Owner 手开的 workspace／pane 或其他在途 Goal。

   并在主仓库 checkout 执行 `git pull --ff-only` 同步基分支。**未合并／暂停（HOLD）／含未提交业务改动的 worktree 不自动删**：保留现场，列清单（路径、分支、状态）交 Owner 确认后再清理。不得执行 `herdr server stop`。
