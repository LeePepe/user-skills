# Bot 与 CLI agent：分工与生命周期

本文件是本环境（Owner tianpei）的分工表与 CLI agent 生命周期规则；合同角色、权限与审查以 [SKILL.md](../SKILL.md) 为准，模型与启动参数只看 [runtime-profiles.yaml](runtime-profiles.yaml)，台账与事件见 [ledger.md](ledger.md)。

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
     - **只读探查（R11）**：按需派 1 个只读 `Explorer`（`gemini-3.8-flash`／`gemini-4-argon` 或 Codex 只读）极速摸底代码事实（附 `file:line`）；
     - **需求对齐与收敛（Grill / Spec）**：结合 `grill-me`／`to-spec`／`codebase-design` 每次聚焦 1–2 个关键决策点与 Owner 交互；
     - **视觉与交互设计（UI/UX Design）**：涉及界面时调用设计域（`艺术家`／`shared-design-system`／`Strata`／原型预览）形成 `design.md` 或原型；
     - **产出冻结候选**：将讨论确认的结论落盘为 `~/Development/handoff/<goal>/spec.md`（或目标仓 `specs/`），复杂架构可派异构 `planner_reviewer` 预审盲区。
   - **Phase 0 禁令**：在 Owner 给出明确执行信号前，**严禁创建业务代码 Worktree、严禁派发 Writer 修改业务代码**。
2. **切换开关（Freeze / GO）**
   - 当 Owner 明确拍板（如“按这个做／开始执行／做”），或初始请求本身就是范围清晰、无歧义的直接实现指令时，进入 **Phase 1（Autonomous TL Execution）**。
3. **Phase 1：自治执行期（Autonomous TL Execution —— Bot 入口 vs. CLI 入口无缝衔接）**
   - **从常驻 Bot 入口启动时**：常驻 Bot 保持自身会话轻量，建好 `~/Development/handoff/<goal>/ledger.md` 后，在 `herdr` 中拉起后台独立的 **`<goal>-goal-tl-r1` CLI 会话**，将 `spec.md` 交给 `TL` 自治跑完 `Task 拆分 → Writer 实现 → Reviewer 双轴审查 → PR Auto-merge 读回`；常驻 Bot 仅监听台账的 `NEED_GO`／`NEED_OWNER_DECISION`／`DONE` 事件向 Owner 汇报。
   - **从本地 CLI 入口启动时**：当前 CLI 主会话直接原地切换为 **`TL (Coordinator)`**（或者当 Owner 希望腾出当前终端时，由当前 CLI 在 `herdr` 拉起后台 `<goal>-goal-tl-r1`），按 `Lite`（单 Task）或 `Standard`（多 Task DAG）1-Hop 派发 `Writer` 与跨厂商 `Reviewer` Subagent，当前 CLI 主会话自身绝不直接下场写多文件业务代码或自审。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

常驻域主 Bot（`Joy`／`Atlas`／`flower` 等）与 `TL` 通过 [`scripts/goal-queue.py`](../scripts/goal-queue.py)（基于 `~/Development/handoff/<goal>/.queue.lock` 的 `fcntl.flock` 文件锁，原子同步 `queue.json` 与 `ledger.md`）协同处理批量输入与运行中热追加：

1. **姿势 1（Batch Mode 一次性塞多个任务）**：
   - Owner 一次性向 Bot 或 `TL` 提供多条任务时，调用 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> --batch-json <json-string-or-file>`。
   - 脚本自动按 `(repo, paths)` 冲突检测计算 `auto_dependencies`：跨仓或同仓路径不相交的任务进入**并行波次（Parallel Wave）**，同仓且路径重叠（含前缀／glob 重叠或空路径 `*` 通配）的任务自动串入前序未完成任务之后形成**串行链（Sequential Chain）**，并在 `ledger.md` 记录 `EVT=READY`。
   - `TL` 循环调用 `skills/goal-task/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal> --mark-in-progress` 派发每波无冲突任务，待任务完成调用 `set-status --status done`，直至 `drained: true`。
2. **姿势 3（Hot-Append Mode 运行中随时热追加）**：
   - 当 `TL` 已在运行中，Owner 向常驻 Bot（`Joy`／`Atlas`／`flower`）或 `TL` 发送新任务时，**Bot 绝不重复拉起第二个 TL**，而是直接运行 `skills/goal-task/scripts/goal-queue.py enqueue --handoff-dir ~/Development/handoff/<goal> ...`（获取 `.queue.lock`、对照在途任务自动计算并行／串行依赖边、在 `ledger.md` 记录 `EVT=HOT_APPEND`）。
   - 若 `<goal>-goal-tl-r1` 窗格处于 `idle`，Bot 执行 `herdr agent prompt <goal>-goal-tl-r1 "Hot-append task <id>; run goal-queue.py next-wave"` 唤醒 `TL`；`TL` 在任一波次收口退出前固定重跑一次 `goal-queue.py next-wave`，确保热追加任务被彻底排空（Drain to Zero）。

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
| 设计 | 艺术家（artist） | Mac mini | 设计域的定义方 | 不放行发布、合并 |
| retro + 反思 | ana | Mac mini | retro 与反思 | |
| 文章形成 | 笔者（writer） | Mac mini | 文章形成 | 与 CLI 层 Task 写者（`writer`）不是同一身份 |
| 产品开发 | Atlas | Mac mini | 产品应用（VitalStride、VoxPocket 等） | |
| 流程/方法（含基建） | flower | Mac mini | workflows 01–06、user-skills/GTT，以及 herdr、CLI、shared-ci、tooling | 具体盯梢由 stuck-PR bot + Mac mini 只读心跳负责；flower 负责每日摘要聊天与危险操作或 hold 例外需 Owner 决定时的路由；可执行的卡住 PR 走 supervise-and-fix 而非默认 Owner |
| 总控 | Hotty | Mac mini | 七域矩阵与跨域摘要；Owner 问时从各 Goal 台账汇总超时与卡住事件 | 不做具体 Goal 协调，不做 Task 写者 |
| MacBook | Joy | MacBook | MacBook 上 Goal 的协调者 | 不操作 Mac mini |

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

## CLI agent 角色与档位

| 角色 | 档位（见 profile） | 谁开 | 生命周期 |
|---|---|---|---|
| Team lead | strong | 协调者 bot | 整个 Goal |
| 裁决 | strong | TL / 协调者 | 一次裁决 |
| Planner | mid | TL | 一轮计划 |
| Planner 的 Reviewer、代码 Reviewer | mid，**与被审者不同家** | TL | 一次审查（每轮新会话） |
| 写者（`writer`） | worker | TL | 一个 Task（不递归派 subagent） |
| 升档写者（`writer_escalated`） | mid | TL | V3 第 2 轮修复升档或高复杂度／并发 Task（新鲜上下文，不递归派 subagent） |
| Explorer、机械活 | worker | 任何 bot / TL | 一次调研或一次活 |
| PR 监控者（`pr_monitor`） | worker（**与 `Explorer` 相同模型序列**） | TL | 一次 PR／波次 CI 与 auto-merge 状态监控（禁止 TL 主会话内联轮询） |
| 调研主 + 旁听 | research，旁听与主不同家，强档裁决 | 协调者 / TL | 一次调研 |

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
     --kind codex \
     --cwd "$TASK_WORKTREE" \
     --direction right \
     --prompt "First call create_goal bound to briefs/<task>.md; read briefs/<task>.md; implement only Owned Paths; write the full R10 receipt to <receipt-path>; then call update_goal(status=\"complete\"|\"blocked\")." \
     --wait --timeout 600000 \
     -- -m gpt-5.6-luna -s danger-full-access -a never
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
     --kind codex --pane "$NEW_PANE_ID" \
     -- -m gpt-5.6-luna -s danger-full-access -a never

   REVIEWER_NAME="merge-tk1a-reviewer-r1"
   herdr agent start "$REVIEWER_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model gemini-4-argon --reasoning-effort high --context long_context \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(git:*)' --allow-tool 'shell(gh:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(rg:*)' --allow-tool 'shell(shasum:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --no-ask-user

   PR_MONITOR_NAME="merge-tk1a-pr-monitor-r1"
   herdr agent start "$PR_MONITOR_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- --model gemini-3.8-flash \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
       --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --no-ask-user
   ```

   `herdr agent start --timeout` 的单位是毫秒（3000–300000）；`scripts/herdr-dispatch.sh` 会在分屏前自动将 `$TASK_WORKTREE`、`~/Development`、`~/Development/handoff`、`~/Development/worktrees`、`~/orca` 写入 `~/.copilot/config.json`（`trustedFolders`）与 `~/.codex/config.toml`（`trust_level = "trusted"`），为 `copilot` 自动注入 `--allow-tool`／`--add-dir` 白名单，并对新分屏 shell 未就绪导致的 `agent_pane_busy` 自动退避重试（最多 5 次）；不接受 bypass-permissions 提示，改用 profile 中的显式白名单参数。
5. **文件指针派发、PR 状态监控委派（`pr_monitor`）与状态等待**：prompt 只携带 `briefs/<task>.md`、R10 回执路径与 Codex 原生 `create_goal`／`update_goal` 绑定指令，不粘贴长合同。单任务同步执行：

   ```sh
   set -euo pipefail
   test -n "$NEW_PANE_ID"
   herdr agent prompt "$WRITER_NAME" \
     "First call create_goal bound to briefs/<task>.md; read briefs/<task>.md; implement only Owned Paths; write the full R10 receipt to <receipt-path>; then call update_goal(status=\"complete\"|\"blocked\")." \
     --wait --timeout 600000
   ```

   `--wait` 会在约 5 秒内核验是否真正进入 `working` 或 `blocked`；若返回 `agent_prompt_stalled`，先排查 agent 状态、pane 与审批界面，禁止盲目重发。多个无依赖 Task 并行时，依次使用不带 `--wait` 的 `agent prompt`，再用 `herdr agent get <name>` 确认各自进入 `working`，随后分别执行 `herdr agent wait <name> --timeout 600000`。长任务可在完成边界通过 `herdr agent prompt <tl-name> "DONE <task>: receipt at <path>"` 反向唤醒 TL pane。
   **PR 状态监控必须派发 `pr_monitor` Subagent（严禁 TL 内联轮询）**：当 PR 已创建或进入 CI／auto-merge／发布流程时，TL **严禁**在主会话内跑 `for/while ... gh pr checks / sleep` 循环，必须通过 `scripts/herdr-dispatch.sh` 派发与 `Explorer` 同档位同模型的 `pr_monitor` Subagent（`gemini-3.8-flash` → `mai-code-2-flash` → `gpt-5.6-luna`）：

   ```sh
   set -euo pipefail
   skills/goal-task/scripts/herdr-dispatch.sh \
     --name "merge-tk1a-pr-monitor-r1" \
     --kind copilot \
     --cwd "$TASK_WORKTREE" \
     --direction down \
     --prompt "Monitor PR #<num> in <owner/repo> until required checks finish and PR merges (or fails/conflicts). If any check fails, capture 'gh run view <run-id> --log-failed | tail -n 80'. Write the full R10 receipt to <receipt-path> and return <15 lines with PR_MERGED | PR_CHECKS_GREEN | PR_CHECK_FAILED | PR_CONFLICT." \
     --wait --timeout 600000 \
     -- --model gemini-3.8-flash \
       --allow-tool write --allow-tool url \
       --allow-tool 'shell(gh:*)' --allow-tool 'shell(git:*)' --allow-tool 'shell(env:*)' \
       --allow-tool 'shell(python3:*)' --allow-tool 'shell(jq:*)' --allow-tool 'shell(sleep:*)' \
       --allow-tool 'shell(date:*)' --allow-tool 'shell(shasum:*)' --allow-tool 'shell(herdr:*)' \
       --deny-tool 'shell(git commit:*)' --deny-tool 'shell(git push:*)' --deny-tool 'shell(git reset:*)' \
       --add-dir ~/Development --add-dir ~/.copilot --add-dir ~/.agents --add-dir ~/.codex --add-dir ~/orca --no-ask-user
   ```

6. **输出读取、阻塞处理与 R10 对账**：`working` 状态禁止读取 alternate-screen 历史；运行中仅可探查：

   ```sh
   herdr agent read "$WRITER_NAME" --source visible --lines 40
   ```

   等 agent 进入 `idle`／`done` 后，才读取精简结论：

   ```sh
   herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
   ```

   **严禁把 `blocked` 当成完成退出**：任何状态检查脚本一旦发现 `agent_status == "blocked"`（或 `agent_blocked`），必须立即用 `herdr agent get <name>` 与 `herdr agent read <name> --source visible --lines 40` 检查审批／提问界面并解阻或重派，**严禁仅判断 `status != "working"` 就 `break` 退出循环并结束 TL 回合**。必要时用 `herdr agent send-keys <name> esc` 或 `ctrl+c` 处理，严禁盲目重发 prompt。Herdr 的 `idle`／`done` 只是终端就绪提示，不是完成证明；TL 必须以落盘的 R10 回执文件和 Git 实际 commit SHA／PR 状态对账为准。
7. **结束与清理**：TL 在下属还有 `working` 时不得结束；先收齐下属回执（R10），在台账记 `DONE`，再仅关闭本次创建的 pane：

   ```sh
   herdr pane close "$NEW_PANE_ID"
   ```

   worktree 不自动删：列清单（路径、分支、是否已合并）交 Owner 确认后再清理。不得碰 Owner 手开的会话与工作区、herdr `w1`（shared-design-system），也不得执行 `herdr server stop`。
