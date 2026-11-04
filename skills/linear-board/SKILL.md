---
name: linear-board
description: 管理 Linear Team TIA 的 Idea Inbox 与 Global Goal Status Board，把 Idea 提升为 1 Goal = 1 Issue 并同步 Task checklist、阻塞证据和状态。
---
# Linear Board

`linear-board` 是 workflow-hub 的环 1。它管理想法与 Goal 的外部可见状态，
而 `ledger.md` 仍是本地执行事实源。

## Idea Inbox

- 先把未成熟想法写入 Linear Team `TIA` 的 `Triage`；若不可用则写入
  `Backlog`。
- Team `TIA` 的 Linear Projects 固定为 `Agent Workflow`、`Bing` 和
  `Personal project`。使用 `scripts/linear-sync.py idea --title ...`
  `--description ... --project "Agent Workflow"` 可显式指定；未指定时脚本会
  按 ledger、Goal 文本和仓库关键词自动路由。
- 所有 workflow、goal-task、skills、Linear board 和编排相关的 Idea/Goal 都归入
  `Agent Workflow`，Bing/Sapphire/FlightBook 等产品工作归入 `Bing`，个人应用归入
  `Personal project`。不在仓库或私有记忆中保存 API key。
- Idea 只记录问题、背景、证据指针和下一步，不伪装成已批准 Task。

## Global Goal Status Board

- 遵守 **`1 Goal = 1 Issue`**：一个 Goal 绑定一个 TIA Issue。
- Issue description 维护 Markdown Task checklist；每个 Task 仍遵守
  `1 Task = 1 Writer = 1 Worktree = 1 PR`。
- `Todo`、`In Progress`、`In Review`、`Done`、`Canceled` 映射 Goal 状态。
- 阻塞必须通过 blocker label/comment 写明准确 head、命令、证据和 Owner decision，
  不用终端输出或“正在处理”代替证据。

## Promote Idea -> Goal

执行 `ensure-goal --handoff-dir <dir> --issue TIA-xx` 将已有 Idea 提升为
Active Goal；不传 `--issue` 时按 `auto_create` 策略创建 Goal Issue。随后
`sync-ledger` 将 `ledger.md` 的 R8 状态、Task checklist 和去重后的里程碑评论
同步回 Linear。

Canonical implementation: [`scripts/linear-sync.py`](scripts/linear-sync.py)。它是
`goal-task` canonical script 的共享入口；真正的实现仍在
[`../goal-task/scripts/linear-sync.py`](../goal-task/scripts/linear-sync.py)。
