## §0 Panorama & 4-Layer Product/Output Matrix

> 本手册把一次 Goal 从可验收的开发交付、证据驱动的双路复盘、规则与底座进化，推进到可复用的总结、抽取与公开叙事，再把反馈送回下一轮开发。四个层次描述产品与产出，三个主板块描述行动与回馈；两者不能互相替代。

配套资产：[`交互式全景飞轮图`](../assets/ai-native-flywheel.html) · [`跨机推进路线图`](./workflow-refine-roadmap.md)

### 0.1 三大板块闭环总图

```text
┌──────────────────────────────────────────────────────────────────────┐
│ §1 Build & Ship：Goal → Task → CLI/Herdr → 6DQ → 发布读回             │
│ 代码、测试、R10、Git/CI、版本与线上读回                               │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 交付事实与可观察指标
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ §2 Retro & Dual Feedback Loop：项目回路 A ＋工程回路 B                 │
│ Performance/Crash/体验发现；Workflow/PR/Infra 复盘；三道锁固化         │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 可执行 Task、底座升级、回归锁与素材
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ §3 Summarize, Extract & Narrate：抽取单 → 三种叙事 → 脱敏发布           │
│ Story Series、Tech Deep-Dive Blog、Deck；公开反馈与反例                 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 新约束、新选题、新验收标准
                                └──────────────────────► 回到 §1
```

**闭环规则**：

1. §1 只以可验收产出证明进展；终端状态、口头承诺和“看起来很忙”不是交付证据。
2. §2 只接受可定位的事实、时间线和反事实检验；每个结论必须转化为可执行 Task、底座升级或三锁之一。
3. §3 不把内部信息直接外传；先保留量化证据，再去标识化、抽象机制，最后按发布门禁读回。
4. 任一回路发现的缺口都通过 `Goal -> Task` 入口回流，不在叙事或复盘中另造未验收的工作单元。

### 0.2 四层产品与产出矩阵

| 层 | 产品／系统范围 | 主要输入 | 核心产出 | 验收读回 |
|---|---|---|---|---|
| **层 1：业务与产品应用层（Products & Apps）** | 原生与全栈应用：实时音频、健康运动、简报看板、财务管理、搜索、通知及核心业务场景 | 用户需求、设计 Token、客户端／服务端代码、真实用户旅程 | 功能切片、页面性能水位（PLT/FPS）、Crash-Free 率、关键交互成功率、用户体验指标 | 功能与 E2E 证据、PLT P50/P90/P99、FPS、Crash-Free、DSAT 趋势 |
| **层 2：共享工程与设计底座（Shared Foundations & Kits）** | `shared-design-system` Basalt Web 与 Native SwiftUI DesignKit；`shared-telemetry` Loki/Grafana、SDK 与 `aidata`；`shared-ci` 6DQ 门禁与发布引擎；`AppleUITesting` | 跨产品复用需求、契约、遥测事件、质量规则、组件与测试夹具 | 跨端一致性、可观测性数据流、自动化门禁、零漂移契约、可复用组件与测试能力 | 契约检查、遥测链路完整性、组件快照／UI 测试、6DQ 结果、版本与摘要一致 |
| **层 3：研发与协同工作流（Engineering Workflows `01–06` & `Goal -> Task`）** | 01 规划切片、02 CLI+Herdr 协同实现、03 PR 交付、04 自动发布、05 遥测分析、06 周期维护；两层任务合同 | Goal 定义、Task brief、依赖 DAG、R10 回执、Git／CI／发布证据 | 高吞吐 PR 交付链、准确 head、R10 证据回执、自愈流水线、可复盘的状态机 | `briefs/<task>.md`、R10、Git SHA、required checks、发布读回与状态对账 |
| **层 4：知识复利与叙事产出（Knowledge & Narrative Outputs）** | 《带一群 AI 干活》系列故事、项目 Performance／Crash 技术 Blog、专题分享 Deck、结构化复盘档案 | 项目证据、工程复盘、量化前后对比、架构图、脱敏映射、读者问题 | 可复用工程认知、架构影响力、公开叙事作品、下一轮选题与约束 | 事实回指内部证据、隐私脱敏检查、构建产物、静态资源哈希与线上页面读回 |

### 0.3 产品与产出如何互相映射

| 观察问题 | 先定位的产品层 | 需要消费的产出 | 回流入口 |
|---|---|---|---|
| 用户体验是否退化？ | 层 1，并核对层 2 遥测 | 性能／Crash／体验水位与版本区间 | 项目回路 A 的 Performance 或 Crash Task |
| 多产品是否出现同类缺陷？ | 层 2 | 组件、契约、SDK、设计 Token 的共性证据 | 共享底座升级 Task 与三锁 |
| 为什么交付链没有产生可用结果？ | 层 3 | brief、R10、事件、Git、PR、CI 的证据链 | 工程回路 B 的 Workflow／PR／Infra Task |
| 哪个机制值得传播？ | 层 4，同时回看层 1–3 | 四元素抽取单与量化 Delta | 三种叙事模板及新一轮门禁 |

---

## §1 Build & Ship — 从两层切片、CLI/Herdr 到 6DQ 发布读回（权重 1/3）

本板块的完成定义是：一个冻结的 Goal 被拆成可验收的 Task，Task 在隔离 Worktree 中完成实现与证据留存，经 6DQ 和发布读回后，准确 head、R10 与线上事实可以相互对账。它不以作者数量、终端输出或 PR 创建代替完成。

### 1.1 `Goal -> Task` 两层模型

#### Goal：可观察的最终结果

Goal 必须同时声明：

- 最终用户或系统可观察的结果、整体范围和明确不做项。
- 所有端到端验收命令，以及这些命令对应的 Task。
- Task 依赖 DAG、基线 SHA、公共接口与红线路径。
- 完成所需的 Git、CI、发布或线上读回证据。

Goal 状态为 `planning → in_progress → accepting → done`，也可以转为 `blocked` 或 `cancelled` 并记录原因。只有必需 Task 全部 `done`（或已备案取消），且端到端验收证据齐全，Goal 才能 `done`。

#### Task：一个垂直曳光弹切片、一个 PR

Task 是 Goal 下可独立交付的结果切片，遵循：

```text
1 Task = 1 Writer = 1 Worktree = 1 PR
```

每个 Task brief 至少记录：

```text
Background: 一句话背景
Owned Paths: 允许修改的路径
Interfaces: Consumes / Produces（精确文件、命令、契约或数据）
Dependencies: 前置 Task
Acceptance: 可复制命令与通过标准
Handoff: R10 回执路径、准确 head、未完成项
```

按真实接口与验收切片，而不是按文件数量或 UI 页面机械拆分。优先使用 Vertical Tracer-Bullet Slice；大范围机械演进使用：

```text
Expand → Migrate → Contract
```

四个拆分谓词依次判断，任一为“是”就拆成独立 Task：

1. 执行者不同：需要不同角色、机器或权限。
2. 验收方式不同：代码测试与线上读回不能由同一命令证明。
3. 可以并行：没有定义、数据、文件或契约依赖。
4. 中间结果交给别人消费：契约、脚本、设计或证据需要交接。

### 1.2 设计驱动开发与项目《设计契约》闭环（`Explorer -> Designer -> Writer -> CI Design Gate`）

设计类 Task 必须把“发现事实、形成设计、实现验证、门禁验收”拆成可交接的接口，避免设计角色因翻阅实现细节而越界，也避免 Writer 用临时色值或未定义状态绕过契约。

#### 三层设计契约架构

| 层 | 权威来源 | 约束与产出 |
|---|---|---|
| **上游全局词典** | `shared-design-system/ai/DESIGN-CONTRACT.md` | 定义跨项目可复用的 Design Token、语义映射、组件边界与全局设计语言。 |
| **项目身份与状态白名单** | 项目内 `docs/design-contract.md` | 声明项目身份、允许消费的 Token／组件，以及 `populated/empty/loading/error` 四态和 `light/dark` 双外观的页面契约。 |
| **静态质量门禁** | `shared-ci/scripts/context/design_contract.py` | 检查项目契约的必需章节，拦截实现中的裸 Hex／RGB 色值及其他契约缺口；失败即阻塞交付。 |

#### 四角色解耦流水线

1. **`Explorer`**：只读扫描业务仓现状与 `docs/design-contract.md`，以 `file:line` 事实回执交接，不写业务代码。
2. **`Designer`**：只消费 Explorer 回执、项目契约和上游 `ai/DESIGN-CONTRACT.md`，以 `writes: handoff-only` 产出 `handoff/<goal>/design.md`；不得翻阅业务实现细节或写 Demo／实现代码。设计交付至少包含 Token 语义映射、四态矩阵、双外观与组件边界。
3. **`Writer`**：在独立 Worktree 消费冻结的 `design.md`，实现可运行 Demo／代码，并提供四态、双外观的多态渲染截图和对应验证证据。
4. **`CI + Reviewer`**：`design_contract.py` 先拦截裸色值与缺漏章节，再由跨厂商 Reviewer 按 V1–V5 双轴核对设计契约、实现范围、截图和命令证据。

### 1.3 两档自适应分流

| 模式 | 适用条件 | 定义与交接 | 完成门槛 |
|---|---|---|---|
| **Lite / Single-Task Mode**（`1 Goal = 1 Task = 1 PR`） | 仅 1 Task、1 个 Writer、1 个 PR、改动不超过 5 个文件；不改公共接口、红线路径、CI 门禁；不新增依赖 | R7 自检后使用一个 PR 描述承载台账与 R10 回执；brief 可内联 | Task 验收、仓库 CI、一次 V1–V5 双轴独立审查、合法合并并读回 |
| **Standard / Multi-Task DAG Mode**（`1 Goal = N Tasks = N PRs`） | 多文件、跨层、多 PR、跨仓、不同验收或存在并行切片 | 用 `ledger.md` 定义 DAG；每个 Task 使用 `briefs/<task>.md` 文件指针，声明 `Interfaces: Consumes / Produces` | 每个 Task 单一 PR 完成 V1–V5；全部 Task `done` 后执行 Goal 端到端验收 |

Lite 执行中若触碰任一升级条件，立即停止扩大范围，转 Standard，补齐台账、brief 与依赖审查。无论哪一档，Task 定义交出即冻结；范围变化须走 `EVT=RULING` 或 R2/R4/R5 变更记录。

### 1.4 冻结、状态与证据

Task 状态：

```text
planned → ready → in_progress → in_review → done
                         ↑             │
                         └── changes_requested
```

只有前置 Task 全部 `done` 且 R7、R9 通过，Task 才能 `ready`。V5 `FAIL` 才能进入 `changes_requested`；任意状态可转 `blocked`／`cancelled`，必须说明原因。R10 回执是状态的持久证据，Herdr 的 `idle`／`done` 只是提示。

冻结前逐项核对：

- `Owned Paths` 不重叠，或已明确串行合并队列。
- 依赖 DAG 无环，Task 的验收映射到 Goal。
- 文件、脚本、API、符号和命令真实存在且可运行。
- 记录基分支 SHA、受影响文件 revision、契约 SHA。
- 计划不含可编译实现代码，且不改 Goal 验收。

运行中发现同一目标内的辅助文件遗漏或非公共接口微调时，写者回报 `NEEDS_RULING`。协调者只有在不改变 Goal 验收、不触碰安全／隐私／数据丢失、不修改 CI 门禁／规则集、不做破坏性操作时，才能追加：

```text
EVT=RULING | decision=<...> | why=<...> | blast_radius=<...>
```

改变验收、增删 Task、改变 Task 接口或触碰四条红线时，必须按 R2/R4/R5 重规划。第 1 轮审查失败由原写者修复；修复后复审仍失败，改派 `writer_escalated`，第二轮后仍失败则 `blocked`，不通过换名、换 SHA 或换 PR 重置计数。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`~/Development/handoff/<goal>/ledger.md`、`briefs/<task>.md` 与 Git 实际状态始终是跨进程唯一事实源（Single Source of Truth）；各 CLI 的原生 Goal／Todo 工具作为**进程内持续推进引擎（In-Process Continuation Engine）**挂载并同步，防止 TL 或 Subagent 提前停机或无限空转：

- **Codex CLI（`goals = true` / `thread_goals`）**：
  - Phase 1 启动时（TL）或 Task 启动时（Writer／Reviewer），首个动作调用 `create_goal(objective="...")`，绑定到 Goal `ledger.md` 或 Task `briefs/<task>.md`。
  - **仅当**全部验收命令通过、PR 完成交接／合并且磁盘 R10 回执已落盘核实后，才允许调用 `update_goal(status="complete")`。
  - 遇到 `TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮修复仍失败时，立即调用 `update_goal(status="blocked")`，**严禁留在活动态空转**。
- **Copilot CLI（`sql` `todos` + `todo_deps`）**：
  - TL 在 Phase 1 启动时将 Goal 的 `Tasks` 及依赖 DAG 边镜像写入会话 SQLite 的 `todos` 与 `todo_deps` 表，随 `ledger.md` 同步更新状态（`pending -> in_progress -> done | blocked`）。
- **Claude Code（`TodoWrite` / `TaskCreate`）**：
  - TL 将活动 `Tasks` 投影到 `TodoWrite` 以提供实时进度可见性，同时保持 `ledger.md` 为唯一权威状态源。

### 会话内追问与 Bug 反馈守卫（Sticky TL & 3-Probe Diagnostic Cap）

- **会话粘性（Sticky TL Rule）**：一旦当前会话已进入 `goal-task`（或存在活动的 `~/Development/handoff/<goal>/ledger.md`），**该会话后续每一轮用户追问或反馈都自动继承 `TL (Coordinator)` 合同约束——即使 Owner 没有再次输入 `/goal-task`**（例如口语化 Bug 反馈与追问：`"还是点不动呀"`、`"还没有找到原因吗"`、`"改一下配色"`）。
- **只读诊断上限（Read-Only Diagnostic Cap，≤3 次直接探查）**：收到用户 Bug 反馈或追问时，TL 最多执行 **2–3 次快速只读检查**以定位受影响的 Task 与文件路径；若排查需要深入的多文件／二进制逆向分析（`>3` 次工具调用）或**任何文件修改**（包括修改 `handoff/` 原型、规格说明 `spec.md`／`design.md` 或仓库业务代码），TL **必须**通过 `herdr` 派发 `Explorer` 或 `Writer` Subagent（`<goal>-<task>-writer-rN`）并记入 `ledger.md`，**严禁在 TL 主会话内直接内联调试或编辑文件**。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py) 在 `~/Development/handoff/<goal>/.queue.lock` 排他文件锁下同步维护 `queue.json` 与 `ledger.md`，统一支撑两种任务入队与波次调度模式：

1. **姿势 1（Batch Mode 一次性批量入队）**：
   - Owner 一次性向 Bot 或 `TL` 给出多个任务时，执行 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json <json-string-or-file>`。
   - 脚本对比既有非终态任务的 `(repo, paths)` 自动推导 `auto_dependencies`：不同 `repo` 或路径互不重叠的任务进入**并行波次（Parallel Wave）**，同 `repo` 且路径重叠（前缀／glob／单文件重叠或空路径 `*` 通配）的任务自动串入前序任务之后形成**串行链（Sequential Chain）**，同步 `ledger.md`（`EVT=READY`）。
   - `TL` 循环调用 `skills/goal-task/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal> --mark-in-progress` 领取无冲突波次并派发 Herdr Subagents，收口后调用 `set-status --status done`，直至 `drained: true`。
2. **姿势 3（Hot-Append Mode 运行中随时热追加）**：
   - 当 `TL` 正在执行中，Owner 向常驻域主 Bot（`Joy`／`Atlas`／`flower`）或 `TL` 追加新任务时，**Bot 绝不启动第二个重复 TL**，而是直接调用 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> ...`（通过 `.queue.lock` 安全写入 `queue.json`、计算与在途 `in_progress` 任务的并行／串行依赖并在 `ledger.md` 记 `EVT=HOT_APPEND`）。
   - 若 `<goal>-goal-tl-r1` 窗格处于 `idle`，Bot 用 `herdr agent prompt <goal>-goal-tl-r1 "Hot-append task <id>; run goal-queue.py next-wave"` 唤醒 `TL`；同时 `TL` 在任一波次退出前总是重跑一次 `goal-queue.py next-wave`，直到队列 `drained: true`。

### 1.5 CLI/Herdr 7-step SOP

以下是可复制的七步标准命令流（所有多步 Herdr 脚本段均开启 `set -euo pipefail` 并强制执行 `test -n "$NEW_PANE_ID"`；推荐直接调用内置 fail-closed 辅助脚本 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh) 一键完成 Step 2–4 的分屏、校验、启动与派发）。变量必须由当前运行时填充；禁止裸 `--current`，禁止把真实个人路径、账号或令牌写进仓库。Socket 事件只负责唤醒下一步，不能代替 R10、Git、CI 或发布读回。

#### Step 1 — 环境与显式 ID 预检

```sh
set -euo pipefail
test "${HERDR_ENV:-}" = 1 &&
  test -n "${HERDR_SOCKET_PATH:-}" &&
  test -n "${HERDR_WORKSPACE_ID:-}" &&
  test -n "${HERDR_TAB_ID:-}" &&
  test -n "${HERDR_PANE_ID:-}"

herdr pane list --workspace "$HERDR_WORKSPACE_ID"
```

核对输出中的 `pane_id`、`tab_id`、`workspace_id` 与环境变量完全一致；不一致即停止，不猜 ID。

#### Step 2 — 布局感知分屏并绑定 Worktree（或调用 `scripts/herdr-dispatch.sh`）

```sh
set -euo pipefail
herdr pane layout --pane "$HERDR_PANE_ID"

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

宽窗格用 `right`，窄或高窗格用 `down`（**严禁省略 `--direction`**）；只使用返回 JSON 的新 `pane_id` 并通过 `test -n "$NEW_PANE_ID"` 校验非空，不复用旧窗格。

#### Step 3 — 具名启动并使用运行时映射

命名格式为 `<goal>-<task>-<role>-r<round>`，满足 `[a-z][a-z0-9_-]{0,31}`。`herdr --kind` 必须采用 `runtime-profiles.yaml` 的 `common.herdr_kind_map`，并显式指定模型；`--` 之后**仅传 `[AGENT_ARG]...`**，**严禁在 `--` 后重复传 `codex`／`copilot` 二进制名**：

```sh
set -euo pipefail
test -n "$NEW_PANE_ID"

WRITER_NAME="${GOAL_SLUG}-${TASK_ID}-writer-r1"
herdr agent start "$WRITER_NAME" \
  --kind copilot \
  --pane "$NEW_PANE_ID" \
  -- --model gpt-5.6-luna \
    --allow-tool write --allow-tool shell --allow-tool url \
    --deny-tool 'shell(rm -rf /:*)' --deny-tool 'shell(herdr server stop:*)' \
    --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

REVIEWER_NAME="${GOAL_SLUG}-${TASK_ID}-reviewer-r1"
herdr agent start "$REVIEWER_NAME" \
  --kind copilot \
  --pane "$NEW_PANE_ID" \
  -- --model gemini-4-argon --reasoning-effort high --context long_context \
    --allow-tool write --allow-tool url \
    --allow-tool 'shell(git:*)' --allow-tool 'shell(gh:*)' \
    --allow-tool 'shell(python3:*)' --allow-tool 'shell(shasum:*)' \
    --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
    --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

PR_MONITOR_NAME="${GOAL_SLUG}-${TASK_ID}-pr-monitor-r1"
herdr agent start "$PR_MONITOR_NAME" \
  --kind copilot \
  --pane "$NEW_PANE_ID" \
  -- --model mai-code-2-flash \
    --allow-tool write --allow-tool url \
    --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
    --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
    --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
    --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
    --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user
```

Reviewer 必须与被审者使用不同模型厂商；不使用 `--yolo`、`--force`、`--allow-all-*` 或 profile 禁止的参数，统一使用 `runtime-profiles.yaml`（`common.copilot_allowlist`）中的显式 `--allow-tool`／`--add-dir` 白名单。`readonly_receipt_flags` 不再包含与内置工具重复的 `shell(env:*)`／`shell(jq:*)`／`shell(rg:*)`，为 `BASELINE_ADD_DIRS` 与 `HERDR_EXTRA_ADD_DIRS` 留出 macOS 1024B PTY 余量；`/tmp` 与 `/private/tmp`、`/var/folders` 与 `/private/var/folders` 两对字面量仍保留。Writer 不递归派发下级执行者，完成本地验证、`git push` 与 R10 后立即返回，**严禁** `gh run watch` 或 `sleep >= 30` 等待远端 CI。**TL 严禁在主会话内直接轮询 PR 状态**；PR 开启后由 `pr_monitor`（与 `Explorer` 同模型）做 Job 级 Fail-Fast，Run 未结束时用单 Job 日志 API，不用 `gh run view --log-failed`。

#### Step 4 — 文件指针派发与 Socket 事件监听

推荐直接通过 `scripts/herdr-dispatch.sh` 一次完成 Step 2–4：

```sh
set -euo pipefail
skills/goal-task/scripts/herdr-dispatch.sh \
  --name "$WRITER_NAME" \
  --kind copilot \
  --cwd "$TASK_WORKTREE" \
  --direction right \
  --prompt "First call create_goal bound to $TASK_BRIEF; read $TASK_BRIEF; implement only Owned Paths; write the full R10 receipt to $R10_RECEIPT; then call update_goal(status=\"complete\"|\"blocked\")." \
  --wait --timeout 600000 \
  -- --model gpt-5.6-luna
```

若已按 Step 2–3 手动启动，则单任务同步派发：

```sh
set -euo pipefail
test -n "$NEW_PANE_ID"
herdr agent prompt "$WRITER_NAME" \
  "First call create_goal bound to $TASK_BRIEF; read $TASK_BRIEF; implement only Owned Paths; write the full R10 receipt to $R10_RECEIPT; then call update_goal(status=\"complete\"|\"blocked\")." \
  --wait --timeout 600000
```

多个无依赖 Task 先派发，再等待：

```sh
set -euo pipefail
test -n "$NEW_PANE_ID"
herdr agent prompt "$TASK_A_NAME" "First call create_goal bound to $TASK_A_BRIEF; read $TASK_A_BRIEF; write $TASK_A_RECEIPT; then call update_goal(status=\"complete\"|\"blocked\")."
herdr agent prompt "$TASK_B_NAME" "First call create_goal bound to $TASK_B_BRIEF; read $TASK_B_BRIEF; write $TASK_B_RECEIPT; then call update_goal(status=\"complete\"|\"blocked\")."
herdr agent wait "$TASK_A_NAME" --timeout 600000
herdr agent wait "$TASK_B_NAME" --timeout 600000
```

需要状态通知时，用 Node.js 通过 `events.subscribe` 订阅 `pane.agent_status_changed`，不要固定间隔轮询：

```js
import net from "node:net";

const socketPath = process.env.HERDR_SOCKET_PATH;
const paneId = process.env.HERDR_PANE_ID;
if (!socketPath || !paneId) {
  throw new Error("HERDR_SOCKET_PATH and HERDR_PANE_ID are required");
}

const request = {
  jsonrpc: "2.0",
  id: 1,
  method: "events.subscribe",
  params: {
    subscriptions: [{ type: "pane.agent_status_changed", pane_id: paneId }],
  },
};

const socket = net.createConnection(socketPath);
let buffer = "";
socket.on("connect", () => socket.write(`${JSON.stringify(request)}\n`));
socket.on("data", (chunk) => {
  buffer += chunk.toString();
  const lines = buffer.split("\n");
  buffer = lines.pop() ?? "";
  for (const line of lines.filter(Boolean)) {
    try {
      const event = JSON.parse(line);
      const status = event?.params?.status ?? event?.result?.status;
      if (status) console.log(`[herdr] status=${status}`);
    } catch {
      console.error("[herdr] ignored malformed event");
    }
  }
});
socket.on("error", (error) => {
  console.error("[herdr] socket error:", error.message);
  process.exitCode = 1;
});
```

#### Step 5 — 执行中裁决与失败升档

`NEEDS_RULING` 只处理同一 Goal 内的顺带缺口；协调者先判定四条红线，再追加 `EVT=RULING`。越出边界则冻结当前范围，提交结构化 `CHANGE_REQUEST`，不自行补范围外实现。

```text
第 1 轮 V5 FAIL → 原 Writer 修复
修复后复审仍 FAIL → writer_escalated 接手第二轮
第二轮后仍 FAIL → Task blocked，退回定义方重规划
```

升档交接必须包括原 brief、前序 diff、两轮 findings、验证日志路径和最新准确 head。

#### Step 6 — 安全读取、阻塞处理与 R10 对账

Working 时只读可见输出：

```sh
herdr agent read "$WRITER_NAME" --source visible --lines 40
```

进入 `idle`／`done` 后读取稳定输出：

```sh
herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
```

`blocked` 时先获取状态再读可见输出；只在确认需要终止当前输入时发送 `esc` 或 `ctrl+c`。固定判断顺序：

```text
磁盘 R10 回执 → Git 实际 SHA／工作树 → CI／发布读回 → Herdr 状态
```

#### Step 7 — 回收窗格、Worktree 与 Goal Workspace

收齐 R10 回执、确认执行者不再 `working` 后，关闭本轮 Task 的 Writer、Reviewer 与 `pr_monitor` panes：

```sh
herdr pane close "$NEW_PANE_ID"
```

Task PR 只有在 `PR_MERGED` 已读回、R10 已落盘且相关 panes 已关闭后才进入自动清理：先删除 `__pycache__`、临时日志等临时产物，再在主仓执行：

```sh
git -C "$MAIN_REPO" worktree remove "$TASK_WORKTREE"
git -C "$MAIN_REPO" worktree prune
git -C "$MAIN_REPO" branch -D "$TASK_BRANCH"
git push origin --delete "$TASK_BRANCH" 2>/dev/null || true
```

删除前仍须确认分支已推送、PR 已合法合并、`git status --short` 为空且没有未保存回执。未合并、`HOLD` 或含未提交业务改动的 worktree 保留现场，列清单交 Owner 确认。

Goal 只有在协调 Bot 已确认 `GOAL_DONE`、全部 R10 回执齐全、Task worktrees 已清理且该 Goal 无 `working`／`blocked` agent 时才收尾；由协调 Bot（不是 TL 自关）执行 `herdr workspace close "$GOAL_WORKSPACE"`，或关闭该 Goal 的 TL pane。严禁触碰共享 `w1`、Owner 手开的 workspace／pane 或其他在途 Goal，不停止共享服务。

### 1.6 6DQ 质量门禁与自动化发布读回

6DQ 是组合门槛，不是五个可选建议：

| 维度 | 检查目标 | 最低证据 | 失败处置 |
|---|---|---|---|
| **L1** | 单测、边界、覆盖率、静态检查 | 测试、覆盖率、lint/typecheck 输出 | 补失败路径与真实断言，拒绝伪测试 |
| **L2** | 本地真实边界集成 | 序列化、存储、网络等最小真实集成 | 减少 mock，固定可重复夹具 |
| **L3** | 端到端用户旅程 | 从入口到结果的一条关键 E2E | 记录环境、版本与旅程证据 |
| **G2** | 安全与契约审计 | 输入、权限、输出、兼容性、秘密扫描 | 阻塞合并，修复后全量复验 |
| **D1** | Worktree 与环境隔离 | 分支、路径、依赖、环境变量、清理记录 | 停止写入，对账并恢复隔离 |

`L1` 通过不能推断 `L3` 通过；`G2` 或 `D1` 失败时，即使功能测试通过也不得交付。

多仓契约同步：

```sh
python3 scripts/context/sync_contract.py --check
python3 scripts/context/sync_contract.py --sync
python3 scripts/context/sync_contract.py --check
```

只有确认 drift 属于当前 Goal 才运行 `--sync`；回执记录同步前后摘要、生成文件和第二次 `--check` 结果。

跨语言版本与发布读回：

```sh
python3 scripts/quality/release_sync.py version-sync
python3 scripts/quality/release_sync.py readback
```

`readback` 必须同时确认：

- `/api/live` 的版本、构建 SHA 与期望值一致。
- `SHA256SUMS` 的摘要与下载产物一致。
- Release tag、资产、摘要与准确 head 一致。
- TestFlight 构建版本、处理状态与目标 commit 可追溯。

任一不一致都标为 `blocked`，记录首次失败命令、漂移层次和最小修复范围，不以页面“看起来正常”结案。

---

## §2 Retro & Dual Feedback Loop — 项目 Telemetry/Crash 与工程 Workflow/PR/Infra（权重 1/3）

复盘与反思进化属于同一个交付板块：先用证据解释发生了什么，再把解释变成下一次可执行的开发入口。两条回路平行运行，均必须返回 §1 的 Goal／Task；不能把“写了复盘”或“提出建议”当作闭环完成。

### 2.1 双路总览与共同停止线

```text
                    §1 交付事实／版本／R10／线上读回
                               │
             ┌─────────────────┴─────────────────┐
             ▼                                   ▼
  回路 A：项目与产品反馈                  回路 B：工程体系反馈
  Telemetry → Performance Task             Workflow/PR/Infra → 规则与底座 Task
  Crash → postmortem → Fix Task             证据链 → 三道物理防回归锁
  体验审计 → 产品打磨 Task                  回流 Goal → 下一次交付
             └─────────────────┬─────────────────┘
                               ▼
                新 Task、共享底座升级、门禁与素材
```

共同触发条件：

- Goal 结项前后出现重复重试、性能回退、Crash 新高、DSAT 聚类突增或体验投诉。
- 交付链出现多轮空转、0 产品提交、stuck-PR、V3 两轮打回、契约或版本漂移。
- 周期巡检发现设计 Token、组件、文档、发布或遥测的系统性偏差。

共同停止线：

1. 先冻结证据边界：版本、准确 head、时间窗口、环境、相关 Task。
2. 先描述可观察事实，再写假设；任何“模型偷懒”“某人疏忽”等意图判断都不得直接作为根因。
3. 证据不足时输出 `unknown`、待查命令和责任面，不用叙事填空。
4. 结论必须选择一个落点：项目修复 Task、Workflow／PR Task、共享底座 Task、CI／Hook 锁或明确 backlog。

### 2.2 回路 A：Project / Product Feedback Loop

#### A1. Telemetry 自动发现 Performance 与体验退化

数据链路：

```text
客户端／服务端埋点
      → shared-telemetry（SDK、Loki/Grafana）
      → L0 聚合与 aidata 查询
      → 基线比较与版本区间定位
      → Performance／体验优化 Task
```

自动检测至少覆盖：

| 信号 | 对比方法 | 需要保留的事实 |
|---|---|---|
| 页面加载耗时（PLT P50/P90/P99） | 当前版本与稳定基线、分端与分场景比较 | 版本、设备／网络分层、样本量、时间窗口 |
| 首屏渲染与关键交互成功率 | 入口到结果的阶段耗时与失败比例 | 阶段边界、失败事件、用户旅程 |
| FPS、卡顿、资源使用 | 关键页面和长任务分位数 | 设备层级、线程／内存／网络上下文 |
| 用户 DSAT 聚类突增 | 同类反馈与版本、市场、设备的时间序列 | 原始分类、增幅、聚类样本和反例 |

发现后必须生成一个带基线与目标阈值的 Performance Task，至少包含：

- 退化版本区间、可疑模块和证据查询。
- 可重复的基准测试、数据夹具、环境与预期阈值。
- 优化前后同一指标的比较方式。
- 上线后遥测读回时间窗、成功标准和回滚／阻塞条件。

不得只写“优化性能”；没有基线、阈值或读回命令的条目不能进入 `ready`。

#### A2. Crash 与线上异常自动诊断和修复

诊断链路：

```text
Crash 堆栈符号化与聚簇
      → 新增 Top 崩溃与版本区间
      → postmortem 根因考古
      → 必现／边界回归测试（RED）
      → 最小手术式修复（GREEN）
      → 审查、合并、发布
      → Crash-Free 与 Top 崩溃读回
```

`postmortem` 必须逐项完成：

1. 符号化调用栈与首次／最近出现版本。
2. Git 近期变更和受影响状态、并发、生命周期条件比对。
3. 最小复现路径、边界输入和竞态窗口还原。
4. H1–H5 竞争假设检验，明确排除的伪根因。
5. 修复前基线、目标阈值、发布版本和读回时间窗。

Crash 修复 Task 的 Writer 先写必现与边界回归单测，完成 RED 后实施最小 GREEN 修复；不得用改测试断言、屏蔽错误或无证据重试伪装修复。发布后读回 Crash-Free、Top 崩溃排名与新增回归；未清零或出现新回归则保持 `blocked`，回到诊断而非直接结案。

#### A3. 周期产品与设计一致性巡检

周期比较设计 Token、Figma 规范、Basalt Web、Native SwiftUI DesignKit、实际页面和 `AppleUITesting` 结果，检查：

- 视觉、间距、颜色、排版、交互状态是否偏移。
- 废弃开关、未消费组件、文档与真实行为是否不一致。
- 关键旅程的无障碍、错误态、加载态和空态是否覆盖。
- 组件或 Token 的修复是否应该落在共享底座而非单个产品。

每个发现必须带截图／快照或可复现路径、期望与实际差异、影响层级和验收方式，然后生成产品打磨 Task 或共享底座 Task。

### 2.3 回路 B：Workflow / PR / Infra Feedback Loop

#### B1. Workflow 与规划／调度复盘：五步证据链

出现“多轮空转／0 产品提交／规划坍缩”时，按以下顺序诊断，禁止先写故事结论：

**第一步：锁定证据边界。** 固定 `ledger.md`、Task brief、R10、JSONL／事件、Git SHA、PR、CI 和时间窗口；为每份文件记录摘要或 SHA-256，确保复盘期间证据不漂移。

**第二步：还原时间线并找 Earliest Load-Bearing Fork。** 把 Goal 定义、首次派发、首次阻塞、首次范围变化、首次失败验收、审查退回和最终停止排序；找出第一个仍承载后续失败的分岔点，而不是最后一个可见错误。

**第三步：用 H1–H5 可反驳假设检验。**

| 假设 | 可观察预测 | 反证材料 | 判定 |
|---|---|---|---|
| H1：定义或验收不可实现 | 首次执行即无法运行或缺少真实接口 | R7 命令、文件、基线证据 | 支持／排除／未知 |
| H2：依赖或基线漂移 | 上游变更后引用、契约或命令失效 | R9 revision、Git diff、CI 日志 | 支持／排除／未知 |
| H3：调度或权限边界错误 | Task 被派给不匹配环境，状态无证据闭环 | pane、Worktree、R10、D1 证据 | 支持／排除／未知 |
| H4：实现／测试缺陷 | 代码有提交但验收失败或审查反复指出同类问题 | diff、测试输出、V1 findings | 支持／排除／未知 |
| H5：门禁／发布读回缺失 | 本地绿但 CI、契约、版本或线上不一致 | 6DQ、发布读回、准确 head | 支持／排除／未知 |

**第四步：反事实门槛核对。** 对每个候选根因问：如果移除该因素，最早分岔点是否仍会出现？如果不会，是否已有更早证据支持它？如果会，记录它只是放大器而不是根因。

**第五步：转化为规则补丁。** 将结论落为 `goal-target-task` 的拆分、调度、R10、R9 或验收规则；写明触发条件、自动检查、失败输出、责任路径和回滚方式。

复盘输出必须包含：

```text
Evidence boundary
Observable timeline
Earliest Load-Bearing Fork
H1–H5 verdict
Counterfactual gate
Responsibility and stop boundary
Executable Task or lock
```

#### B2. PR 生命周期与代码审查复盘

按 PR 生命周期分类阻塞，而不是把所有“卡住”归为执行慢：

| 阻塞类 | 证据 | 复盘问题 | 回馈开发 |
|---|---|---|---|
| 定义不完整 | brief、R7、验收失败 | 是否缺真实接口、命令、依赖或停止线 | 补齐 brief／拆分 Task |
| 实现未收敛 | diff、测试、R10 | 是否违反最小切片、是否缺 RED→GREEN | 测试模板、代码路径或 Task 边界 |
| 审查反复打回 | V1/V5 findings、修复 diff | 哪类缺陷跨 PR 重复出现 | 双轴清单、静态检查或自动修复路由 |
| CI／契约漂移 | 6DQ、`sync_contract.py` | 本地与门禁为何不一致 | 共享门禁、契约生成或基线校验 |
| 发布／读回失败 | `release_sync.py`、线上响应 | 版本、摘要、资产、TestFlight 哪层失真 | 发布前阻断与读回 Task |

高频问题要进入 `supervise-and-repair` 路由或等价的自动修复机制，但自动修复必须留下 diff、测试、准确 head 和 R10，不能以重新运行命令替代根因修复。

#### B3. 基建、CI 门禁与共享库复盘

所有工程结论映射到三道物理防回归锁：

| 锁 | 作用位置 | 适合固化的结论 | 典型载体 |
|---|---|---|---|
| **锁 1：认知与工作流层** | `user-skills`／`agent-skills` | 任务拆分、状态机、证据格式、停止线、复盘法 | Goal/Task 合同、brief 模板、R10 模板、双轴清单 |
| **锁 2：本地执行层** | `PreToolUse` Worktree 守卫、Herdr 回调器 | 路径隔离、显式 ID、事件唤醒、危险操作拦截 | Worktree／pane 检查、Socket 订阅、状态对账 |
| **锁 3：跨仓 CI 与组件层** | `shared-ci` 6DQ、`sync_contract.py`、`release_sync.py`、共享设计与遥测库 | 契约、版本、质量、发布、组件和遥测不可回归 | CI 门禁、生成器、共享组件、线上读回 |

物理锁的选择原则：

1. 只靠记忆能再次发生，就至少落锁 1。
2. 本地操作顺序错误能再次发生，就落锁 2。
3. 多仓、多人或发布环境仍能发生，就落锁 3。
4. 同一故障两次出现，至少增加一条不同层级的锁；只增加文字提醒不算完成。

### 2.4 回路输出与回流契约

每次 Retro 交付一份结构化记录，至少含：

- 触发信号、时间窗、版本、准确 head 和证据摘要。
- 回路 A 或 B、Earliest Load-Bearing Fork、H1–H5 结论和未知项。
- 责任面与停止边界，避免把无关历史问题拉入本 Goal。
- 一个或多个 `Goal -> Task`、共享底座升级或物理锁；每项都有 `Owned Paths`、Interfaces 和验收。
- 线上读回或门禁读回的时间与结果。
- 可供 §3 使用的四元素素材候选，但不得把脱敏前内部数据直接发布。

回流后，新增 Task 与原 Goal 的依赖、验收映射和基线必须可追溯；如果改变 Goal 验收，回到定义方和负责人审批，不在 Retro 文档中隐式扩权。

### 2.5 双路复盘执行模板与质量门

#### 项目回路 A 记录模板

```markdown
# Project Feedback Record

Trigger:
Product / version / environment:
Telemetry query and evidence window:
Baseline and target:
Observed regression:
Candidate module:
Reproduction or diagnostic command:
Performance / Crash / UX Task:
Acceptance before release:
Acceptance after release:
Rollback or blocked condition:
```

填写时遵循以下顺序：

1. 先复制原始指标和查询条件，再写摘要，避免摘要改变事实。
2. 固定分层维度：版本、设备、网络、市场、旅程和样本量。
3. 同时记录正向对照组与反例；只有异常样本不能证明全局退化。
4. 将可疑模块写成待验证范围，不把它写成已确认根因。
5. 将上线后读回命令写进 Task 验收，而不是留给发布后口头跟进。

Performance Task 必须能回答“改善多少、在什么条件下改善、何时判定没有改善”。Crash Task 必须能回答“如何必现、如何证明修复、如何证明没有换成另一种崩溃”。体验巡检 Task 必须能回答“期望状态、实际状态、共享底座还是产品层负责”。

#### 工程回路 B 记录模板

```markdown
# Engineering Feedback Record

Trigger:
Goal / Task / PR / release:
Evidence boundary and hashes:
Observable timeline:
Earliest Load-Bearing Fork:
H1:
H2:
H3:
H4:
H5:
Counterfactual gate:
Responsibility and stop boundary:
Lock 1 / Lock 2 / Lock 3:
Executable Task or backlog:
Verification command:
Owner decision required:
```

工程回路的质量门：

- 没有证据边界，不开始根因归因。
- 没有 Earliest Load-Bearing Fork，不宣称“根因已找到”。
- 没有反事实检验，不把相关性写成因果性。
- 没有可执行 Task 或物理锁，不得标记复盘完成。
- 没有验证命令，不得把规则补丁移入 `ready`。

#### 复盘结果分级

| 结果 | 含义 | 下一步 |
|---|---|---|
| `confirmed` | 证据和反事实均支持，范围清楚 | 生成 Task／锁，进入 §1 验收 |
| `probable` | 证据支持但仍缺关键反事实 | 保留未知项，生成诊断 Task，不直接修复 |
| `unknown` | 证据不足或互相冲突 | 扩充证据边界，维持原问题 `blocked` |
| `not-reproducible` | 当前环境无法重现 | 固定环境、扩大样本、保留线上监控，不关闭问题 |
| `rejected` | 假设被证据排除 | 记录排除原因，避免下轮重复猜测 |

#### 复盘后的回流检查

```text
是否生成了明确的 Goal/Task？
  ├─ 否：保留为诊断素材或 backlog，不宣称闭环
  └─ 是：是否写明 Owned Paths、Interfaces、Dependencies、Acceptance？
          ├─ 否：回到定义与 R7
          └─ 是：是否新增至少一条物理防回归锁？
                  ├─ 否：说明为何已有锁足够，并附证据
                  └─ 是：进入 §1 的 ready 检查
```

---

## §3 Summarize, Extract & Narrate — 从四元素抽取单到故事、Blog、Deck 与发布（权重 1/3）

本板块把 §1 的交付事实和 §2 的双路结论压缩成可复用知识，但不牺牲证据。每篇作品只有一个核心母题；同一素材可按不同受众进入三种模板，但量化事实、隐私边界和发布读回必须共享同一来源。

### 3.1 四元素《Story & Tech Extraction Sheet》

每次从项目攻关（Performance／Crash／架构重构）或工程演进（Workflow／PR／基建）中抽取一张素材单。素材单先保存在受控内部位置，发布稿只消费脱敏后的副本。

```markdown
# Story & Tech Extraction Sheet

## 0. Provenance
- Source Goal / Task:
- Evidence window:
- Accurate head / release:
- Internal evidence pointers:
- Audience and candidate format:

## 1. The Hook Scene
- Exact date/time window and concrete scene:
- What was visible, audible, or failing:
- One detail that makes the problem reproducible:

## 2. Quantitative Delta
| Metric | Before | After | Baseline / target | Evidence |
|---|---:|---:|---:|---|
| PLT / Crash-Free / coverage / rounds / LOC | | | | |

## 3. The Counter-Intuitive Pivot
- Initial belief (A):
- Evidence that challenged A:
- Root mechanism found (B):
- H1–H5 or equivalent rejected alternatives:

## 4. The Takeaway Mechanism
- Architecture diagram, code/config fragment, or one-line rule:
- Why it generalizes:
- Where it does not apply:
- Next executable Task or physical lock:

## 5. Desensitization
- Internal identifiers replaced:
- Public terms retained:
- Evidence that must remain private:
- Reviewer and publish checks:
```

四个必填要素的质量标准：

1. **The Hook Scene：具象时间锚点与切入现场。** 用某年某月某日或明确时间窗的真实细节切入，例如一条 PLT 曲线翻倍、一次连续失败的构建或一次没有产出的巡检；不以“我们遇到挑战”开头。
2. **Quantitative Delta：硬核量化证据包。** 给出耗时、Crash 率、覆盖率、代码行数、审查轮次或吞吐的前后对比、基线和样本；不能用“明显”“大幅”等模糊形容词替代。
3. **The Counter-Intuitive Pivot：反直觉认知转折。** 清楚写出原本以为的 A、证据链如何排除 A、真凶或机制为何是 B；不把事后知识伪装成当时直觉。
4. **The Takeaway Mechanism：可复用机制结晶。** 提供一张架构图、一段最小代码／配置或一条极简规则，并说明适用边界；故事没有机制不能作为工程素材结案。

### 3.2 三种 Narrative Templates

#### 模板一：Story Series — 工程故事集连载

适用于有鲜明现场、曲折排查和人物可感知节奏的系列文章。可采用五段式：

1. **具体时刻异常切入。** 读者先看到一个数字、错误、空窗或反常状态。
2. **旧习惯／直觉碰壁。** 展示最合理但错误或不足的第一反应，保留限制与代价。
3. **翻开日志／回执破案。** 用证据、时间线、分岔点和被排除的假设推进，而不是用旁白宣布答案。
4. **换上极简新机制。** 只展示解决核心问题的 Task、规则、锁或架构变化。
5. **真实跑通与读回余味。** 给出量化 Delta、限制、尚未解决的边界和下一轮反馈，不用口号收尾。

连载规则：

- 每篇一个现场和一个机制，不把整个组织或所有工具塞进一篇。
- 系列前后篇共享术语与脱敏映射，数字必须可追溯。
- 只写实际发生的阻塞、修复和读回；未知项要保留为未知。
- 篇末给读者一条可复制的检查表或最小实验。

#### 模板二：Tech Deep-Dive Blog — Performance／Crash／架构深潜

适用于需要让工程读者复现、理解并迁移机制的长文：

1. **线上现象与指标水位。** 定义用户影响、版本范围、基线、目标阈值和样本。
2. **H1–H5 排查时间线与被排除的假象。** 逐项列证据、实验、结果与停止理由。
3. **底层原理与根因解剖。** 解释调用、状态、并发、渲染、数据流或契约为何导致现象。
4. **修复方案与压测／遥测读回对比。** 展示 RED→GREEN、关键 diff 概念、基准方法和线上结果。
5. **防回归护栏。** 给出三锁映射、6DQ／CI／Hook、报警阈值和下一次失败时的入口。

技术 Blog 的验收：

- 每个结论有内部证据指针，公开稿删除内部标识但不删除可验证的逻辑。
- 数字明确单位、分位数、样本量、时间窗和设备／环境。
- 代码或配置只保留足以理解机制的最小片段，避免泄露私有实现。
- 读者可以按文章复现诊断或验证方法，而不是只能接受结论。

#### 模板三：Sharing / Deck — 体系化专题分享

适用于跨项目、跨团队或管理与工程混合受众。推荐四幕结构：

1. **痛点矩阵。** 用用户影响、交付影响、质量影响和维护成本呈现问题，不从工具清单开始。
2. **架构演进代际图。** 对比旧流程、过渡阶段和当前机制，标出每一代新增的证据与锁。
3. **关键设计取舍。** 解释为何采用两层 Goal/Task、事件订阅、6DQ、三锁或某个共享底座，说明放弃的选项。
4. **实测收益。** 以吞吐、交付轮次、PLT、Crash-Free、覆盖率、漂移次数或读回成功率收尾，并说明适用边界。

Deck 验收：

- 每页有一个结论和一个可读证据，不用密集文字隐藏无结论的图表。
- 架构图与实际流程一致，箭头标明输入、产出与回流。
- 收益区分相关性与因果性；无法归因的指标标为观察结果。
- 讲者备注保留风险、限制和 Q&A 可能追问的证据入口。

### 3.3 去口号化、脱敏与发布流水线

#### 叙事打磨五原则

1. **具象代替抽象。** 用现场、命令、曲线、回执和版本替代“高效”“智能”“无缝”。
2. **展示过程而非自夸结论。** 写出假设、失败、排除和证据如何改变行动。
3. **保留真实曲折与限制。** 说明哪些没解决、哪些环境不适用、哪些数字仍需观察。
4. **克制收尾。** 以读回事实、机制边界或下一步实验结束，不用口号升华。
5. **一文一核心母题。** 其他发现进入选题池或链接，不让一篇文章承担全部体系。

#### 隐私脱敏硬红线

| 信息类别 | 内部稿可保留 | 公开稿处理 |
|---|---|---|
| 个人姓名、账号、机器名 | 仅在受控证据索引中保留 | 映射为“作者”“协调者”“Reviewer”“执行环境”等角色 |
| 公司、组织、产品和项目代号 | 仅在发布审批范围内保留 | 使用经过批准的泛化名称或公开名称 |
| 私有平台、内部域名、绝对路径、内部 ID | 用于事实定位 | 删除、泛化或替换为公开等价接口 |
| API Key、令牌、Cookie、密钥、客户数据 | 不进入正文；单独受控保存 | 永不发布；发现即阻塞并旋转 |
| 公开技术栈、通用协议、标准命令 | 可保留 | 保留公开语义，移除内部参数和上下文 |
| 指标与日志片段 | 保留可验证的结构与必要数字 | 去除用户标识、时间精度和可反推的敏感维度 |

脱敏不是改写事实。任何数字变化、时间泛化、图表裁剪或术语替换都要在内部稿记录理由，并确认不会制造错误因果。

#### Desensitization & Publish Pipeline

```text
内部证据与 Extraction Sheet
        ↓
选定母题与 Narrative Template
        ↓
内部初稿：事实回指、量化表、机制图
        ↓
脱敏映射：身份／项目／路径／密钥／用户数据
        ↓
双人内容与隐私检查
        ↓
Markdown 构建与链接／图表检查
        ↓
ai-workflow/lab/_build.py 双栏阅读器渲染
        ↓
Archify 配图交付、静态资源哈希校验
        ↓
线上页面、版本、摘要与回滚入口读回
        ↓
记录发布证据，向下一轮选题池与 Task 规则回流
```

发布前清单：

- [ ] 文章只有一个核心母题，并引用一张完整 Extraction Sheet。
- [ ] Hook、Quantitative Delta、Counter-Intuitive Pivot、Takeaway Mechanism 四项齐全。
- [ ] 所有事实可回指 Goal／Task、R10、Git、CI、Telemetry 或发布读回。
- [ ] 已移除个人、账号、机器、内部域名、绝对路径、密钥和客户数据。
- [ ] 代码、图表、架构图与公开上下文不泄露内部实现或可反推信息。
- [ ] `ai-workflow/lab/_build.py` 构建通过，链接、图片、字体和静态资源完整。
- [ ] 线上页面、静态资源哈希、版本与发布摘要已读回并记录。
- [ ] 读者能复用机制，也能看到不适用边界与未完成项。

### 3.4 跨项目与工程选题池矩阵

| 母题 | 项目证据入口 | 工程证据入口 | 最合适体裁 | 预期回流 |
|---|---|---|---|---|
| 性能退化如何被发现与修复 | PLT、FPS、首屏、交互成功率、版本区间 | Telemetry SDK、查询、基线与阈值 | Tech Deep-Dive 或 Deck | Performance Task、报警与性能门禁 |
| Crash 如何从堆栈走到清零 | 聚簇、符号化、Crash-Free、复现与发布读回 | postmortem、RED→GREEN、审查与三锁 | Story Series 或 Tech Deep-Dive | Crash Fix Task、回归测试、版本读回 |
| 为什么交付链空转 | 0 产品提交、重复重试、未完成 Goal | brief、R10、事件、Git、V3、调度证据 | Story Series 或 Deck | 拆分／调度／R10 规则 Task |
| stuck-PR 如何收敛 | 交付时间、审查轮次、测试漏检 | V1/V5、自动修复路由、准确 head | Tech Deep-Dive | 双轴清单、监督与修复锁 |
| 共享底座如何避免漂移 | 跨端视觉、契约、版本、遥测一致性 | 6DQ、`sync_contract.py`、组件与 CI | Deck 或 Tech Deep-Dive | 共享库、契约、CI Task |
| 发布为何必须读回 | 用户可见版本、资源摘要、TestFlight 状态 | `release_sync.py`、`/api/live`、`SHA256SUMS` | Deck 或 Story Series | 发布门禁与回滚 Task |

选题池不是新的任务队列。只有经过 §2 证据确认并按 §1 定义了验收的条目，才可变成 Goal／Task；其余保留为素材候选，定期去重、脱敏和复核。

### 3.5 一页式闭环验收卡

```text
§0：产品层、工程层、工作流层、知识层的映射是否清楚？
§1：Goal/Task 是否冻结？是否符合 1 Task = 1 Writer = 1 Worktree = 1 PR？
§1：CLI/Herdr 七步证据、6DQ、契约同步和发布读回是否齐全？
§2：是否明确回路 A 或 B？是否找到了最早承重分岔点？
§2：H1–H5、反事实门槛、Task 回流和三道物理锁是否记录？
§3：四元素 Extraction Sheet 是否完整？
§3：是否选定 Story Series、Tech Deep-Dive 或 Deck 模板？
§3：是否完成脱敏、构建、哈希与线上读回？
```

当且仅当这张卡的每一项都有证据或明确 `blocked` 原因，闭环才可进入下一轮；任何缺项都回到对应板块，不用叙事质量掩盖交付或复盘缺口。

### 3.6 作品验收、版本化与反馈回流

#### 从证据到稿件的四次收敛

| 收敛阶段 | 必须回答 | 禁止事项 | 产物 |
|---|---|---|---|
| **事实稿** | 发生了什么？何时发生？由什么证据证明？ | 用形容词替代数字；把猜测写成事实 | 带证据指针的内部稿 |
| **机制稿** | 哪个机制导致现象？为何可迁移？边界是什么？ | 只展示成功结果；删除被排除的假设 | 架构图、最小代码／配置、适用边界 |
| **叙事稿** | 读者从哪里进入？转折在哪里？如何复用？ | 一个段落塞多个母题；用工具名取代问题 | 选定模板的公开草稿 |
| **发布稿** | 是否脱敏、可构建、可读回、可回滚？ | 未经检查直接发布；把构建绿当作线上成功 | 带发布证据的成稿 |

每次向下一个阶段移动，都要保留上一阶段的版本号、审查人、变更摘要和回退指针。删掉事实、指标或限制时必须说明原因；不能因为文章变短而丢失关键证据。

#### 内容与技术双轴审查

| 轴 | 检查项 | 失败时 |
|---|---|---|
| **内容轴** | Hook 是否具体；转折是否真实；母题是否唯一；结尾是否克制 | 退回稿件重写，不进入构建 |
| **技术轴** | 指标单位、样本、基线、代码片段、图表、链接和机制是否一致 | 退回证据源或技术段落，不用编辑措辞掩盖 |
| **隐私轴** | 个人、账号、机器、内部域名、路径、密钥、客户数据是否全部处理 | 立即 `blocked`；移除并检查衍生资源 |
| **发布轴** | 构建、哈希、页面、版本、摘要、回滚入口是否可读回 | 不发布；记录最小修复与重新读回命令 |

技术轴不能替代隐私轴，内容轴也不能豁免发布轴。任何阻塞项修复后，只重审受影响部分，但必须重新执行完整的隐私扫描和构建读回。

#### 版本与证据命名

内部稿和公开稿使用可追溯但不含敏感信息的版本标识：

```text
<topic>-<format>-<revision>
```

版本记录至少包含：

- 来源 Goal／Task 的非敏感标识和准确 head 摘要。
- Extraction Sheet revision、证据时间窗和脱敏映射 revision。
- 内容轴、技术轴、隐私轴、发布轴的结论。
- 构建命令、产物摘要、线上页面读回时间和回滚入口。

不要把内部绝对路径、账号、访问令牌或客户标识塞入文件名、图片元数据、静态资源名称、HTML 注释或发布日志。

#### 公开反馈回流

发布后的评论、错误报告、读者复现和指标异常分成三类：

1. **事实纠错**：回到来源证据，修订公开稿和 Extraction Sheet，保留变更记录。
2. **机制追问**：补充边界实验、图表或 FAQ；若暴露新工程问题，创建诊断 Task。
3. **可迁移改进**：验证是否适用于其他产品或工作流；经 §2 证据确认后，生成共享底座、门禁或规则 Task。

公开反馈不直接改变冻结的 Goal／Task。它先进入 §2 的证据边界，再按 H1–H5 或项目 Telemetry／Crash 回路确认；只有完成 §1 的定义、接口和验收，才进入交付队列。

#### 发布完成定义

```text
Extraction Sheet 完整
  → 事实、机制、叙事、发布四次收敛完成
  → 内容／技术／隐私／发布四轴通过
  → 构建产物与静态资源摘要固定
  → 线上页面、版本与哈希读回一致
  → 记录回滚入口与公开反馈路由
  → 将新的可执行机制送回 §1 或 §2
```

没有线上读回，只能标记“已构建”；没有脱敏证据，只能标记“内部稿”；没有机制边界，只能标记“故事候选”；没有回流入口，不能标记为完整闭环。
