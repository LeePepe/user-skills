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
| 调研主 + 旁听 | research，旁听与主不同家，强档裁决 | 协调者 / TL | 一次调研 |

## 生命周期（herdr）

以下命令均在已进入 Herdr 的协调者 pane 中执行；变量只表示当前 Goal 的运行时值，不得写入个人路径、令牌或密钥。

1. **全部进 herdr**：所有 CLI agent（含 Reviewer、Explorer、一次性 `codex exec`）都在 herdr 中启动，有可检索的名字；不留 herdr 外的临时进程。
2. **环境与显式 ID 预检**：先确认当前会话及所有控制器 ID，禁用裸 `--current`：

   ```sh
   test "${HERDR_ENV:-}" = 1 &&
     test -n "${HERDR_SOCKET_PATH:-}" &&
     test -n "${HERDR_WORKSPACE_ID:-}" &&
     test -n "${HERDR_TAB_ID:-}" &&
     test -n "${HERDR_PANE_ID:-}"
   herdr pane list --workspace "$HERDR_WORKSPACE_ID"
   ```

   从 `pane list` 输出核实当前控制器的精确 `pane_id` 与 `HERDR_TAB_ID`、`HERDR_WORKSPACE_ID` 匹配后，才允许继续。多 tab／workspace 下对 `split`、`resize` 等操作始终传显式 pane ID，不依赖裸 `--current`。
3. **命名与分屏绑定**：命名遵循 `<goal>-<task>-<role>-r<round>`，例如 `merge-tk1a-writer-r1`、`merge-tk1a-reviewer-r2`；Goal 级角色用 `<goal>-goal-tl-r1`。先看当前布局，再按宽窄选择方向：

   ```sh
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
   ```

   若布局判断为窄／高窗格，将 `--direction right` 改为 `--direction down`。`NEW_PANE_ID` 必须来自返回 JSON 的 `.result.pane.pane_id`，不可猜测或复用其他 pane。
4. **按 profile 启动具名 Subagent**：显式带 profile 中的模型及沙箱／只读参数；`--kind` 使用 `runtime-profiles.yaml` 的 `common.herdr_kind_map`（特别是 `cursor-agent` 必须传 `--kind cursor`，不能把 binary 名传给 Herdr）。名字需满足 `[a-z][a-z0-9_-]{0,31}`。例如：

   ```sh
   WRITER_NAME="merge-tk1a-writer-r1"
   herdr agent start "$WRITER_NAME" \
     --kind codex --pane "$NEW_PANE_ID" \
     -- codex -m gpt-5.6-luna -s danger-full-access -a never

   REVIEWER_NAME="merge-tk1a-reviewer-r1"
   herdr agent start "$REVIEWER_NAME" \
     --kind copilot --pane "$NEW_PANE_ID" \
     -- copilot --model gemini-4-argon \
       --deny-tool write --deny-tool shell --no-ask-user
   ```

   `herdr agent start --timeout` 的单位是毫秒（3000–300000）；首次进入目录时处理 folder-trust；不接受 bypass-permissions 提示，改用 profile 中的受限参数。
5. **文件指针派发与状态等待**：prompt 只携带 `briefs/<task>.md` 与 R10 回执路径等短指令，不粘贴长合同。单任务同步执行：

   ```sh
   herdr agent prompt "$WRITER_NAME" \
     "Read briefs/<task>.md; implement only Owned Paths; write the full R10 receipt to <receipt-path>." \
     --wait --timeout 600000
   ```

   `--wait` 会在约 5 秒内核验是否真正进入 `working` 或 `blocked`；若返回 `agent_prompt_stalled`，先排查 agent 状态、pane 与审批界面，禁止盲目重发。多个无依赖 Task 并行时，依次使用不带 `--wait` 的 `agent prompt`，再用 `herdr agent get <name>` 确认各自进入 `working`，随后分别执行 `herdr agent wait <name> --timeout 600000`。长任务可在完成边界通过 `herdr agent prompt <tl-name> "DONE <task>: receipt at <path>"` 反向唤醒 TL pane。
6. **输出读取、阻塞处理与 R10 对账**：`working` 状态禁止读取 alternate-screen 历史；运行中仅可探查：

   ```sh
   herdr agent read "$WRITER_NAME" --source visible --lines 40
   ```

   等 agent 进入 `idle`／`done` 后，才读取精简结论：

   ```sh
   herdr agent read "$WRITER_NAME" --source recent-unwrapped --lines 80
   ```

   若等待返回 `blocked` 或 `agent_blocked`，先用 `herdr agent get <name>` 与 `herdr agent read <name> --source visible --lines 40` 检查审批／提问界面；必要时用 `herdr agent send-keys <name> esc` 或 `ctrl+c` 处理，严禁盲目重发 prompt。Herdr 的 `idle`／`done` 只是终端就绪提示，不是完成证明；TL 必须以落盘的 R10 回执文件和 Git 实际 commit SHA／PR 状态对账为准。
7. **结束与清理**：TL 在下属还有 `working` 时不得结束；先收齐下属回执（R10），在台账记 `DONE`，再仅关闭本次创建的 pane：

   ```sh
   herdr pane close "$NEW_PANE_ID"
   ```

   worktree 不自动删：列清单（路径、分支、是否已合并）交 Owner 确认后再清理。不得碰 Owner 手开的会话与工作区、herdr `w1`（shared-design-system），也不得执行 `herdr server stop`。
