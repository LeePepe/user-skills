# Bot 与 CLI agent：分工与生命周期

本文件是本环境（Owner tianpei）的分工表与 CLI agent 生命周期规则；合同角色、权限与审查以 [SKILL.md](../SKILL.md) 为准，模型与启动参数只看 [runtime-profiles.yaml](runtime-profiles.yaml)，台账与事件见 [ledger.md](ledger.md)。常驻域主 Bot 与 Goal-level TL 的开 TL、热追加、巡检解卡和完成回收另见 [`bot-tl-orchestration`](../../bot-tl-orchestration/SKILL.md)。

## 两层

| 层 | 是什么 | 谁开 | 活多久 |
|---|---|---|---|
| bot 层 | 有独立聊天的常驻 bot，各自固定一台机器 | Owner | 长期 |
| CLI agent 层 | bot 或 TL 在 herdr 里启动的 CLI 会话（TL、Planner、Reviewer、写者、Explorer） | 协调者 bot / TL | 用完即关 |

## Linear Idea capture and Goal binding

Linear `TIA` Idea capture, Goal binding, and status synchronization are
implemented by `linear-sync.py`; the local `ledger.md` remains authoritative.
See [`linear-board`](../../linear-board/SKILL.md) and [`ledger.md`](ledger.md)
for the command contract and event mapping.

## 双阶段与双入口衔接（Phase 0 共创设计 → Phase 1 自治执行）

Phase 0 只读探查、Owner 对齐与 `spec.md` 冻结候选不得创建业务
Worktree 或派 Writer；Owner 明确 GO 后进入 Phase 1。Phase 1 由常驻 Bot
通过 herdr 拉起 Goal TL，或由本地 CLI 原地担任 TL，按 Lite／Standard
和 1-Hop 合同派发 Writer 与跨厂商 Reviewer；机器、白名单和 UI/UX 设计挂载
分别以 [`bot-tl-orchestration`](../../bot-tl-orchestration/SKILL.md)、
[`runtime-profiles.yaml`](runtime-profiles.yaml) 与下方设计路由为准。

**Local CLI Steering rule:** the local CLI main session is the sole `Steering`
(TL) and never spawns a second `Steering` or standalone `Planner` subagent.
Only a resident Bot entry starts one `<goal>-goal-tl-r1` in Herdr; that Steering
then dispatches leaf `Writer`／`Reviewer` agents as needed, with optional read-only
`Explorer` support for noisy searches.

<a id="design-routing-machine-split"></a>
<a id="bot-与-cli-的设计路由-owner-2026-10-24"></a>

### Multi-Goal Lease Watchdog and Contract Telemetry

跨 Goal 监督使用 `goal-supervisor.py scan`，按角色 TTL 检测
`BLOCKED_AGENT`、`ZOMBIE_LEASE` 与 `ORPHAN_TL_WAIT`；`check-pins` 与
`telemetry --write` 负责 upstream drift 和合同遥测。等待子 Agent 只用
30–60s 有界轮询（例如 `herdr agent wait <name> --timeout 60000`），并以
`ledger.md`、agent 状态和 `receipts/` 对账。监督关联同时考虑
`-goal-tl-` 短名、台账身份和 Goal handoff cwd（例如 `good-des-*`／
`gd-e1-*` → `good-designer`）；`/Applications/Xcode.app` 必须在 allowlist。

**Blocked notify bridge**：主触发是 `pane.agent_status_changed` 且 `status=blocked`，
巡逻保底是 `goal-supervisor.py scan --write-alerts` 的 `BLOCKED_AGENT`；两者写入
`handoff/<goal_folder>/alerts/blocked-<agent>-<ts>.md`。`ledger.md` 的
`协调者 bot` MUST 在巡逻或入站信号时读取 alert、检查 visible prompt，并作
Tier A/A′ 人工／策略决定或 Tier B `NEED_GO`。**通知桥 ≠ 解卡**：桥只通知，
严禁 auto-Enter、自动提交按键或把 `blocked` 当作完成。
**GOAL_DONE 关窗与 blocked alerts 两条独立**。

### UI 设计与技术栈 Skill 挂载（Steering / Writer 直接加载，无独立设计子代理）

UI/UX design is handled by `Steering` during Phase 0 co-design and by
`Writer` during Phase 1 implementation. Steering loads `my-designer`
(`L0 -> L1 -> L2`) directly when authoring `design.md`/`spec.md`; Writer
implements directly in product or `shared-design-system` code. The CI Design
Gate (`design_contract.py`) and Cross-Vendor `Reviewer` verify the result,
including `sips -g pixelWidth`, zero numeric truncation (`…`), the
`followed the brief verbatim` rule, `Package.resolved`, and `shared-design-system`
upstream primitives.

产品 UI/UX 设计仍以 `shared-design-system`（`ai/DESIGN-CONTRACT.md`）、项目
`docs/design-contract.md` 与既有 `Strata`／原型预览为共享设计资产的单一事实源。
`Steering` 直接核对 `Package.resolved` 与 upstream primitives；需要 >15 行
custom layout/chrome glue 且 upstream 缺失时，先拆上游 Task，不得在 `AppUI`
先行修补。

- **Spec-First Requirement Change Rule（规格先行）**：已有代码与测试的仓库一旦修改产品需求或细化 UI/UX，`spec.md`／`design.md`（及仓库 `specs/` 或 `docs/design-contract.md`）就是唯一上游权威；unit／snapshot tests 是派生物，不得锁定 obsolete UI 或行为。`Steering`（或按需 `Explorer`）映射当前实现和冲突 legacy test assertions，产出带精确 `file:line` 的 **Obsolete Test Manifest**，并记录 **Deprecated Test Clauses to Remove/Rewrite**；随后 `Code Writer` 先迁移获授权的 deprecated assertions，再改代码，`Code/UI Reviewer` 保留非 deprecated regression tests。
- **Reviewer V2 UI gate**：Reviewer 必须以 worst-case density fixtures 核验 `sips -g pixelWidth` == `W * scale`，并检查 zero numeric truncation（`…`）、无 unexpected wrapping、Bento 卡片等高、无 duplicate unit placeholders（`lb lb`）及无 raw localization keys。任一项是 V2 P1 `FAIL`；不得以“followed the brief verbatim”降级为 `CONCERNS`。
- **Anti-Gaming Visual Harmony Gate**：Writer 不得用仅通过 literal width／ellipsis checks 的 workaround 破坏 visual harmony；具体字号、间距、组件几何与 hit-target 规则由 `my-designer` Category Slot 维护，Reviewer 仍按 V2 P1 判定真实截断、溢出与可访问性回归。
- **Reviewer V2 UI gate**：Reviewer 必须以 worst-case density fixtures 核验 `sips -g pixelWidth` == `W * scale`，并检查 zero numeric truncation（`…`）、无 unexpected wrapping、Bento 卡片等高、无 duplicate unit placeholders（`lb lb`）及无 raw localization keys。任一项是 V2 P1 `FAIL`；不得以“followed the brief verbatim”降级为 `CONCERNS`，若 brief formula 导致 overflow/truncation，仍 `FAIL` 并报告 brief definition defect。
- **Anti-Gaming Visual Harmony Gate**：Writer 不得用仅通过 literal width／
  ellipsis checks 的 workaround 破坏 visual harmony；具体字号、间距、组件
  几何与 hit-target 规则由 `my-designer` Category Slot 维护，Reviewer 仍按
  V2 P1 判定真实截断、溢出与可访问性回归。
- **Mac mini 常驻域**：`flower`／`Atlas`／`ana`／`笔者`，以及在 Mac mini 上做协调的 `Hotty`，均按 Steering → Writer → CI Design Gate → Reviewer 处理产品 UI/UX 设计 Goal；`Hotty` 不做具体 Goal 协调的边界不变。
- **MacBook／`Joy`（本机设计流水线）**：`Joy` 在 MacBook 上、于 goal-task 内运行同一 Steering → Writer → CI Design Gate → Reviewer 流程，也不操作 Mac mini。
- **CLI 入口**：本地 CLI／TL 在本 Goal 内主动运行同一 Steering → Writer → CI Design Gate → Reviewer 流程。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

常驻域主 Bot 与 `TL` 通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py)
协同处理批量输入与运行中热追加；队列以 `.queue.lock` 原子更新
`queue.json`／`ledger.md`，按 `(repo, paths)` 冲突矩阵自动形成并行或串行波次。

1. **Batch Mode**：使用 `enqueue --batch-json`，再由 TL 循环
   `next-wave --mark-in-progress`，任务收口用 `set-status --status done`，
   直到返回 `drained: true`。
2. **Hot-Append Mode**：已有 TL 时绝不重复开 TL；使用 `enqueue` 记录
   `EVT=HOT_APPEND`。TL 为 `idle`／`done` 时，prompt 唤醒后若仍未工作，
   仅补一次 `herdr agent send-keys "$GOAL-goal-tl-r1" enter`，并在波次收口
   前重跑 `next-wave`。

### 两级完成与清理边界

- **1-Hop Communication & Lifecycle Ownership（单层沟通与管辖）**：独立 Goal TL
   存在时，Bot 只沟通、唤醒、解卡并在 `GOAL_DONE` 时回收 TL pane/workspace；
   TL 独占下属 Subagent 的派发、更新与回收。Bot 或本地 CLI 直接担任 TL 时，
   由其直接管辖下属。
- `pr_monitor` 必须从主仓或 `~/Development/handoff/<goal>` 启动，不能占用 `$TASK_WORKTREE`；如果曾在任务 Worktree 中运行，先关闭其 pane，再执行 Worktree 清理。
- **Task / `PR_MERGED`**：按 **`writer_stays_and_cleans_own_worktree`（谁建谁埋）**，Writer 在开 PR、写 R10 回执后保持 `idle`，等待 Cross-Vendor Reviewer `V5 PASS`；通过后由 Writer（或 fallback `pr_monitor` Worker）从主仓 cwd 运行 Layer 0 `ci-monitor.py pr` 与 `git-gc.py task-clean`，执行 `git worktree remove "$TASK_WORKTREE"`、`git worktree prune` 与 `git branch -D "$TASK_BRANCH"`，再关闭自己的任务 pane。Steering TL 只规划、派发、仲裁（`EVT=RULING`）并读回执，不内联执行 CI 监控或清理。未合并、HOLD 或 dirty Worktree 保留并报告 Owner。
- **Goal / `GOAL_DONE`**：ledger 记录 `EVT=GOAL_DONE`（全部 Task `done` 且 `drained: true`）后，关窗与 Worktree 处置分开验收。**A（MUST）**：MUST 关闭该 Goal 的 TL pane，并清空其 herdr workspace（例如 `herdr workspace close "$GOAL_WORKSPACE"`）。TL SHOULD 延迟自闭 pane；若未发生，协调 Bot MUST 回退关闭，即使 Worktree 已由 Owner Freeze 保留。不触碰 `w1`、Owner 会话或活动 Goal。**B（与 A 正交）**：Owner Freeze 可以保留 Worktree；未合并、HOLD 或 dirty Worktree 保留并报告 Owner。保留 Worktree MUST NOT 阻断 A。

### 会话内追问与 Bug 反馈守卫（Sticky TL & Context-Noise Threshold）

进入 `goal-task` 或存在活动 ledger 后，会话追问继续继承 TL 合同。
已知模块或中小改动（`≤8` 个文件）由 Steering 直接读取、诊断根因并写
`spec.md`／`briefs/<task>.md`，无需派发 `Explorer`；
跨仓大海捞针、全量盘点 `Obsolete Test Manifest` 或长日志分析超过 8 个文件时，
通过 `herdr-dispatch.sh` 按需派发只读 `Explorer`，接收 `<15` 行摘要与 R10 receipt。
UI/UX 设计由 Steering 直接加载 `skill("my-designer")`；Phase 1 产品代码仍由隔离
worktree 的 Writer 修改，Steering 不内联改产品代码。

### CLI 原生 Goal 引擎挂载与状态同步（Native Goal Continuation Binding）

`ledger.md`、`briefs/<task>.md` 与 Git 是跨进程唯一事实源；Codex
`create_goal`／`update_goal`、Copilot `sql` 的 `todos`／`todo_deps`、
以及 Claude `TodoWrite` 仅作进程内投影。验收与 R10 完成后才标 complete；
`TASK_BLOCKED`、`NEED_GO`、`NEED_OWNER_DECISION` 或 V3 两轮失败立即标 blocked。

## bot 分工（唯一一处）

Owner 2026-10-23 的七域分工：每个域一个主 bot。本表是花名册的唯一来源。

| 域 | 主 bot | 机器 | 范围 | 不做 |
|---|---|---|---|---|
| retro + 反思 | ana | Mac mini | retro 与反思 | |
| 文章形成 | 笔者（writer） | Mac mini | 文章形成 | 与 CLI 层 Task 写者（`writer`）不是同一身份 |
| 产品开发 | Atlas | Mac mini | 产品应用（VitalStride、VoxPocket 等） | |
| 流程/方法（含基建） | flower | Mac mini | workflows 01–06、user-skills/GTT，以及 herdr、CLI、shared-ci、tooling | 具体盯梢由 stuck-PR bot + Mac mini 只读心跳负责；flower 负责每日摘要聊天与危险操作或 hold 例外需 Owner 决定时的路由；可执行的卡住 PR 走 supervise-and-fix 而非默认 Owner |
| 总控 | Hotty | Mac mini | 七域矩阵与跨域摘要；Owner 问时从各 Goal 台账汇总超时与卡住事件 | 不做具体 Goal 协调，不做 Task 写者 |
| MacBook | Joy | MacBook | MacBook 上 Goal 的协调者；本机产品 UI/UX 设计在 goal-task 内跑设计流水线 | 不操作 Mac mini |

- fonder（Mac mini）只作为 flower 域下的可选帮手，不占域主。
- pigger（Mac mini）在七域之外：Owner 的个人咨询顾问，可按 GTT 做自己的咨询类工作；不接触 Owner 的这些项目（仓库、台账、handoff 中的 Goal）。
- 每个 bot 只操作自己的机器；跨机器只通过 handoff 交接（[ledger.md](ledger.md) 的 inputs 规则）。
- Owner（用户）是危险操作及明确 hold 例外的唯一放行人；普通 PR 合并与发布（04/tag/TestFlight）不需要 Owner GO——普通 PR 在准确 head 的 required checks 通过并完成独立审查后，按仓库既定 auto-merge 流程合并，再读回合并结果。bot 转述不产生新的放行授权；已有明确 Owner 授权须在台账记录原话或来源。

## 每个 Goal 的责任线

每个 Goal 在台账文件头写明：**Owner、协调者 bot、TL（herdr 名 + 模型）、机器、合同 SHA**。信号（事件）只按这条线走：

```
写者/Reviewer/Explorer → TL → 协调者 bot → Owner
```

- 协调者 bot 每个 Goal 只有一个：开 TL、收 TL 事件、需要 Owner 决定时去问。
- TL 按 **`Goal -> Task` 两层模型（`1 Task = 1 Writer = 1 Worktree = 1 PR`）** 划分垂直切片并写 `briefs/<task>.md`（Lite 档单 Task 免独立台账），按 **1-Hop Flat Tree** 在 herdr 里统一开 Explorer、Planner、Reviewer、写者（含 `writer_escalated`）；不兼任写者或独立验收（SKILL.md「角色绑定与交接」）。写者与 Reviewer 默认不再递归派发下级 Subagent（`You Do Not Dispatch Subagents`）。
- Hotty 只读台账做跨域摘要，不在具体 Goal 的协调线上；线上没人接的事件由 Owner 问 Hotty 时查出。

### 技术栈 Skill 路由表（Apple & DataViz Skills Routing）

TL 编写 `briefs/<task>.md` 或派发 `Writer`（`worker.writer`／`writer_escalated`）时，必须在简报中声明按任务技术栈匹配的 `Required Skills`；执行者启动时先加载这些 Skills，再开始实现。

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
| PR 监控者（`pr_monitor`） | 默认由 Layer 0 `ci-monitor.py`（0 Token）处理；失败诊断或特殊复杂场景才用 worker（**`pr_monitor` 顺序为 `mai-code-2-flash` → `gpt-5.6-luna`**） | TL | 一次 PR／波次 CI 与 auto-merge 状态监控（Job 级 Fail-Fast；禁止 TL 与 Writer 内联 `gh run watch`／长 `sleep`） |
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
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /Volumes/SSD --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

   REVIEWER_NAME="merge-tk1a-reviewer-r1"
   herdr agent start "$REVIEWER_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model gemini-4-argon --reasoning-effort high --context long_context \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(git:*)' --allow-tool 'shell(gh:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(shasum:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /Volumes/SSD --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user

   PR_MONITOR_NAME="merge-tk1a-pr-monitor-r1"
   herdr agent start "$PR_MONITOR_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model mai-code-2-flash \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
       --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /Volumes/SSD --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user
   ```

   `herdr agent start --timeout` 的单位是毫秒（3000–300000）；`scripts/herdr-dispatch.sh` 在 `herdr pane split --direction` 之前校验 `--name` 匹配 `^[a-z][a-z0-9_-]{0,31}$`（≤32 字符），并把 `$TASK_WORKTREE`、`~/Development`、`~/Development/handoff`、`~/Development/artifacts`、`~/Development/workflow-evidence`、`~/Development/worktrees`、`~/orca`、`/Volumes/SSD`、`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders` 以及 `HERDR_EXTRA_ADD_DIRS`（冒号分隔，对应 `briefs/goal.md` 的 Goal 级 `extra_add_dirs`）写入 `~/.copilot/config.json`（`trustedFolders`）与 `~/.codex/config.toml`（`trust_level = "trusted"`；同一 `[projects."…"]` 只保留一块，并清掉文件里已有的相邻重复 trust 块）。对 `copilot`，`--add-dir` 改为叠加去重：无论调用方是否已传 `--add-dir`，始终补齐 `$CWD`、`~/Development`、`~/.copilot`、`~/.agents`、`~/.codex`、`~/orca`、`/Volumes/SSD`、`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders` 与 `HERDR_EXTRA_ADD_DIRS`，父 TL 白名单必须覆盖子 Subagent（Parent ≥ Child）。`/Volumes/SSD` 是基线祖先：Goal 放在 `/Volumes/SSD/.../handoff/<goal>` 时，只把该 Goal 目录写入 `trustedFolders` 盖不住 handoff 兄弟目录的 realpath，会话 `--add-dir` 必须包含 `/Volumes/SSD`（handoff 父目录 realpath 若不在 `/Volumes/SSD`、`~/Development` 或 `~/orca` 下，由 `herdr-dispatch.sh` 另行补上），父 TL 不得窄于子 Subagent。并对新分屏 shell 未就绪导致的 `agent_pane_busy` 自动退避重试（最多 5 次）；不接受 bypass-permissions 提示，改用 profile 中的显式白名单参数。
5. **文件指针派发、PR 状态监控委派（`pr_monitor`）与状态等待**：prompt 只携带 `briefs/<task>.md`、R10 回执路径与 Codex 原生 `create_goal`／`update_goal` 绑定指令，不粘贴长合同。单任务同步执行：

   ```sh
   set -euo pipefail
   test -n "$NEW_PANE_ID"
   herdr agent prompt "$WRITER_NAME" \
     "First call create_goal bound to briefs/<task>.md; read briefs/<task>.md; implement only Owned Paths; write the full R10 receipt to <receipt-path>; then call update_goal(status=\"complete\"|\"blocked\")." \
     --wait --timeout 600000
   ```

   `herdr-dispatch.sh` 在 `agent start` 成功后轮询 `interactive_ready`（最多约 10s，间隔 0.5s）再 `agent prompt`。若返回 `agent_prompt_stalled`（`--wait` 约 5s 初态检测的假阳性）或发送后约 2s 仍为 `idle`，脚本自动发送一次 Enter（`herdr agent send-keys <name> enter`）提交输入框；指定了 `--wait` 时改走 `herdr agent wait`，不要把这次 5s stall 当成派发失败，也不要盲目重发整段 prompt。手写命令流同样只补一次 Enter 再 `agent wait`。多个无依赖 Task 并行时，依次使用不带 `--wait` 的 `agent prompt`，再用 `herdr agent get <name>` 确认各自进入 `working`，随后分别执行 `herdr agent wait <name> --timeout 600000`。长任务可在完成边界通过 `herdr agent prompt <tl-name> "DONE <task>: receipt at <path>"` 反向唤醒 TL pane。
   **PR 状态监控默认走 Layer 0（0 Token）**：当 PR 已创建或进入 CI／auto-merge／发布流程时，TL 直接后台异步启动 `python3 skills/goal-task/scripts/ci-monitor.py workflow|pr ...`，不为纯状态轮询启动 LLM Subagent；仅流水线失败需 LLM 诊断或特殊复杂场景才通过 `scripts/herdr-dispatch.sh` 回退派发 `worker.pr_monitor`。Writer **严禁**在会话内执行 `gh run watch` 或 `sleep >= 30` 等待远端 CI（本地验证、`git push` 与 R10 回执落盘后立即返回）。脚本与回退 `pr_monitor` 都做 Job 级 Fail-Fast：不得只看 Workflow Run 顶层 `.conclusion`（同 Run 仍有 Job 在排队或运行时它恒为 `null`）。任一 required job 为 `failure`／`timed_out`／`action_required` 即停止并回报；Run 仍 `in_progress` 时 `gh run view <run-id> --log-failed` 会报 `run is still in progress`，必须改用单 Job 日志：`gh api repos/<owner>/<repo>/actions/jobs/<failed_job_id>/logs`。

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
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --add-dir /Volumes/SSD --add-dir /tmp --add-dir /private/tmp --add-dir /var/folders --add-dir /private/var/folders --no-ask-user
   ```

6. **输出读取、阻塞处理与 R10 对账**：`working` 状态禁止读取 alternate-screen 历史；运行中仅可探查：

   ```sh
   herdr agent read "$WRITER_NAME" --source visible --lines 40
   ```

   等 agent 进入 `idle`／`done` 后，才读取精简结论：

   ```sh
   herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
   ```

   **严禁把 `blocked` 当成完成退出（含 Bot 盯 TL 与 TL 盯 Subagent）**：任何状态检查或巡检一旦发现 `agent_status == "blocked"`（或 `agent_blocked`），必须立即用 `herdr agent get <name>` 与 `herdr agent read <name> --source visible --lines 40` 检查审批／提问界面，**严禁仅判断 `status != "working"` 就 `break` 退出循环**。若弹窗为 `Allow path access` 且所列路径均属于临时目录（`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders`）、基线白名单（含 `/Volumes/SSD` 及其下 handoff 兄弟目录的 realpath）或当前 Goal/Task 简报已声明的 `extra_add_dirs` 目标路径，立即发送 `herdr agent send-keys <name> enter`（默认高亮项 `Yes, and remember these paths for this session`）解卡并确认转回 `working`；若涉及未声明的仓外／敏感路径或高危操作，严禁盲按 Enter，记 `EVT=NEED_GO` 上报 Owner 裁决。Herdr 的 `idle`／`done` 只是终端就绪提示，不是完成证明；TL 必须以落盘的 R10 回执文件和 Git 实际 commit SHA／PR 状态对账为准。

   **Tier A′（`CMD_CONFIRM`）**：可见窗格出现 Copilot／CLI「Do you want to run this command?」或等价的命令确认时，必须先用 `herdr agent read <name> --source visible` 核对命令文本与当前高亮项；仅当命令属于白名单——`gh`、非破坏性 `git`（不含 `commit`／`push`／`merge`／`rebase`／`reset`／`checkout` 及其它改写历史或工作区的子命令）、`python3`、`jq`、`herdr`（不含 `herdr server stop`），以及 Allow-path／herdr 操作已经允许的同类安全操作（例如 `shasum`、`date`、`sleep`、`env`）——才可按键解卡并记 `EVT=UNBLOCK`。当前高亮已是安全的运行项时发送 `herdr agent send-keys <name> enter`；界面若提供 don't-ask-again／remember，且记住范围仍限于这条已核对的白名单命令，用 Down+Enter（`herdr agent send-keys <name> down`，随后 `herdr agent send-keys <name> enter`，或本仓已建立的等效按键序列）选中该安全记住项。敏感、破坏性或未声明的命令保持 hold，记 `EVT=NEED_GO` 或 `NEED_OWNER_DECISION`，严禁盲按 Enter，不得把 blocked 当作完成。**`--no-ask-user` 不批准 `CMD_CONFIRM` 或 Allow-path**（即便启动参数带有该旗标，也不抑制这两类提示），不得靠它解卡；MacBook `team_lead` 故意不带 `--no-ask-user`（Owner 2026-10-26 对可选 B 暂不），不改 `runtime-profiles.yaml` 或 `herdr-dispatch.sh`，也不开启 `--yolo`。
7. **结束与清理**：TL 在下属还有 `working` 时不得结束；先收齐下属回执（R10），在台账记 `DONE`，再关闭本次 Task 的 Writer、Reviewer 与 `pr_monitor` panes：

   ```sh
   herdr pane close "$NEW_PANE_ID"
   ```

   **已合并（`PR_MERGED`）的 Task 自动清理**：当 Task PR 已读回确认 `MERGED`、R10 回执已落盘且 Cross-Vendor Reviewer 已记录 `V5 PASS` 后，Writer（或 fallback `pr_monitor` Worker）先清理 `__pycache__`、临时日志等临时产物，再从主仓执行：

   ```sh
   # Equivalent cleanup command from the main-repository checkout:
   # git worktree remove "$TASK_WORKTREE"
   git -C "$MAIN_REPO" worktree remove "$TASK_WORKTREE"
   git -C "$MAIN_REPO" worktree prune
   git -C "$MAIN_REPO" branch -D "$TASK_BRANCH"
   git push origin --delete "$TASK_BRANCH" 2>/dev/null || true
   ```

   **Goal 窗格关闭（验收 A，与 Worktree 正交）**：协调 Bot 读到 `EVT=GOAL_DONE` 后，MUST 关闭该 Goal 的 workspace 或 TL pane，例如 `herdr workspace close "$GOAL_WORKSPACE"`。TL SHOULD 延迟自闭 pane：

   ```sh
   nohup sh -c 'sleep 2 && herdr pane close "$HERDR_PANE_ID"' >/dev/null 2>&1 &
   ```

   若延迟自闭没有发生，协调 Bot MUST 回退关窗，即使该 Goal 的 Worktree 仍被 Owner Freeze 保留。严禁触碰共享 `w1`、Owner 手开的 workspace／pane 或其他在途 Goal。

   在主仓库 checkout 执行 `git pull --ff-only` 同步基分支。**Worktree 处置（验收 B）**：未合并／暂停（HOLD）／含未提交业务改动的 worktree 不自动删；Owner Freeze 可保留现场，列清单（路径、分支、状态）交 Owner 确认后再清理。保留 Worktree MUST NOT 阻断上面的 pane／workspace 关闭。不得执行 `herdr server stop`。
