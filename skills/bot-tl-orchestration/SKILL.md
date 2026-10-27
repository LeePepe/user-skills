---
name: bot-tl-orchestration
description: 常驻域主 Bot 编排 Herdr Team Lead (TL) 的共用生命周期合同：开 TL、热追加、巡检解卡与 Goal/Task 完结回收。
---
# Bot ↔ TL 编排合同

本入口供常驻域主 Bot（`Joy`、`Atlas`、`flower`、`艺术家`、`ana`、`笔者`、`Hotty`）使用。
它只负责 Bot 与 Goal-level TL 的生命周期；TL 与 Subagent 的 Task-level 1-Hop 执行仍由
[`goal-task`](../goal-task/SKILL.md) 负责。以下共享资产是唯一事实源：

- [`runtime-profiles.yaml`](../goal-task/references/runtime-profiles.yaml)：模型、机器与白名单档位。
- [`bots-and-agents.md`](../goal-task/references/bots-and-agents.md)：角色、启动、监控与回收规则。
- [`ledger.md`](../goal-task/references/ledger.md)：Goal 事件与 R10 证据格式。
- [`goal-queue.py`](../goal-task/scripts/goal-queue.py)：批量入队、热追加、波次与 drain。
- [`herdr-dispatch.sh`](../goal-task/scripts/herdr-dispatch.sh)：分屏、启动、解卡与等待的 fail-closed 辅助脚本。

## 三层分离

1. **Layer A — Shared Flow Contract**：本入口规定 `Bot ↔ TL` 的 Goal 生命周期；
   [`goal-task`](../goal-task/SKILL.md) 规定 `TL ↔ Subagent` 的 Task-level 1-Hop 执行。
   两者共享上述 `references/*` 与 `scripts/*`，不复制模型表或生命周期命令。
2. **Layer B — Private Bot Identity & Memory**：Bot 的 profile、记忆与日常例程留在私有
   记忆中；不得把七域花名册或模型表作为私有记忆中的唯一真源。
3. **Layer C — Product Auto-review Gate**：客户端安全门禁在仓库外。若 Auto-review 阻止
   Bot 的 `herdr` 调用，fail-closed：向 Owner 报告所需的确切规则，不得退回 Bot 会话内
   编辑业务代码。

## 1. 开 TL、热追加与路由

- 先检查 Goal 是否已有 `<goal>-goal-tl-r1`，状态为 `working` 或 `idle` 时绝不再开第二个
  TL；改用 [`goal-queue.py`](../goal-task/scripts/goal-queue.py) `enqueue`，记录
  `EVT=HOT_APPEND`，若 TL 为 `idle` 再用 `herdr agent prompt <goal>-goal-tl-r1` 唤醒。
- 非设计域 Bot 遇 UI/UX 需求必须转给 `艺术家`，不要在本域开设计 Writer。
- 按 [`runtime-profiles.yaml`](../goal-task/references/runtime-profiles.yaml) 选择模型；
  Mac mini strong Claude 启动前检查 `weekly_remaining_pct`，小于 20% 或未知时使用
  `copilot` + `gemini-4-argon`。诊断 Goal 固定使用该组合。
- 启动父 TL 时满足 Parent ≥ Child Allowlist：传入基线
  `~/Development`、`~/.copilot`、`~/.agents`、`~/.codex`、`~/orca`、`/tmp`、
  `/private/tmp`、`/var/folders`、`/private/var/folders`，并合并 Goal 声明的
  `extra_add_dirs`；不要让子 Agent 获得父 TL 未获准的路径。

## 2. 监控与解卡

- 双源对账：同时读取 [`ledger.md`](../goal-task/references/ledger.md) 与
  `herdr agent get <tl-name>` 的 `agent_status`。Bot 事件使用真实系统时钟与 `id=B<N>`
  或 `id=J<N>`，不得撞用 TL 的 `id=E<N>`。
- `agent_status == "blocked"` 时先执行 `herdr agent read <name> --source visible --lines 40`。
  Tier A（临时路径、基线路径或已声明 `extra_add_dirs` 的权限提示）才可
  `herdr agent send-keys <name> enter` 并记录 `EVT=UNBLOCK`。
- Tier B（未声明外部路径、凭据或破坏性操作）保持 hold，记录 `EVT=NEED_GO` 或
  `NEED_OWNER_DECISION`，把确切请求交给 Owner；不得把 blocked 当作完成。

## 3. 两级完成与回收

- **Task / `PR_MERGED`**：由 TL 或清理 Subagent 启动 `pr_monitor`，其 cwd 使用主仓或
  `~/Development/handoff/<goal>`，不要占用 `$TASK_WORKTREE`。确认 `PR_MERGED` 与 R10
  回执后，关闭任务窗格，清理 `__pycache__`，执行
  `git worktree remove "$TASK_WORKTREE"`、`git worktree prune`、`git branch -D
  "$TASK_BRANCH"`；未合并、hold 或 dirty 的现场必须保留并报告 Owner。
- **Goal / `GOAL_DONE`**：只有全部 Task `done`、队列 `drained: true` 且 ledger 已记录
  `EVT=GOAL_DONE` 才可结束。TL 可延迟自闭 pane：
  `nohup sh -c 'sleep 2 && herdr pane close "$HERDR_PANE_ID"' >/dev/null 2>&1 &`。
  Bot 随后确认没有 `working`/`blocked` Agent 或已合并 Worktree；若 TL 仍开着，只关闭该
  Goal 的 TL pane/workspace，绝不触碰 `w1`、Owner 会话或其他活动 Goal，然后停止监督例程。

## 4. 交接边界

Bot 只负责开 TL、入队、路由、巡检、解卡与 Goal 完结回收；Task 的实现、验证、R10
回执与独立审查遵循 [`goal-task`](../goal-task/SKILL.md)。所有异常沿
`Writer/Reviewer → TL → 协调者 Bot → Owner` 回报，真实 Git、ledger、R10 和 Herdr 状态
必须相互对账。

## 5. 固定操作顺序

每次收到 Goal、追加反馈或恢复监督时，Bot 按以下顺序执行：

1. 读取 Goal ledger，确认 `Owner`、协调者、TL 名称、合同版本和当前 Goal 状态。
2. 读取 `herdr agent get`，把 `working`、`idle`、`blocked` 与 ledger 事件逐项对账。
3. 需要新工作时先判断是否已有 live TL，再选择 `enqueue` 或开 TL；不得先开 pane 后补判断。
4. 需要跨域设计时停止本域实现路由，向 `艺术家` 交接设计输入和验收边界。
5. 每次入队或热追加都保留 `task_id`、`repo`、`paths`、`brief` 与依赖结果。
6. 每次解卡、hold、完成或异常都写入 [`ledger.md`](../goal-task/references/ledger.md)，时间戳取真实系统时钟。
7. 只有从磁盘回执、Git 状态和 Agent 状态都得到同一结论，才发送下一步提示。

热追加的最小命令形态如下；若路径重叠，队列负责串行化，不得借“方便整合”把任务合并：

```sh
skills/goal-task/scripts/goal-queue.py enqueue \
  --handoff-dir ~/Development/handoff/<goal> \
  --task-id <id> --title <title> --repo <repo> \
  --paths <paths> --brief briefs/<id>.md
```

完成时必须同时看到 `PR_MERGED`、R10 回执、`drained: true`（Goal 级）和
`EVT=GOAL_DONE`（ledger 级）；任一缺失都只能保持 `in_progress`、`blocked` 或 hold。

## 6. Fail-closed 原则

- 不用 Agent 的 `idle`、终端输出或口头确认替代 ledger、Git、R10 和 CI 证据。
- 不因 Auto-review、权限弹窗、未声明路径或凭据请求而修改安全门禁或业务代码。
- 不关闭未合并、dirty、hold 或仍被其他进程使用的 Worktree；把阻塞原因与所需 Owner 决策原样上报。
- 不在 Bot 私有记忆中复制共享模型表、七域花名册或脚本规则；共享资产变化只改其唯一事实源。
