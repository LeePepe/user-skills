---
name: bot-tl-orchestration
description: 常驻域主 Bot 编排 Herdr Team Lead (TL) 的共用生命周期合同：开 TL、热追加、巡检解卡与 Goal/Task 完结回收。
---
# Bot ↔ TL 编排合同

本入口只负责 Bot ↔ Goal-level TL；Task-level 1-Hop 由
[`goal-task`](../goal-task/SKILL.md) 负责。唯一事实源：
[`runtime-profiles.yaml`](../goal-task/references/runtime-profiles.yaml)、
[`bots-and-agents.md`](../goal-task/references/bots-and-agents.md)、
[`ledger.md`](../goal-task/references/ledger.md)、
[`goal-queue.py`](../goal-task/scripts/goal-queue.py)、
[`herdr-dispatch.sh`](../goal-task/scripts/herdr-dispatch.sh)。

## 1. 开 TL、热追加与设计路由

先读取 `<goal>-goal-tl-r1`；`working`、`idle` 或 `done` 都不得重复开 TL。运行中追加
用 `goal-queue.py enqueue`，记录 `EVT=HOT_APPEND`，必要时唤醒现有 TL。

产品 UI/UX 统一运行 `Explorer → Designer → Writer → CI Design Gate → Cross-Vendor Reviewer`。
Mac mini、MacBook/`Joy` 与本地 CLI 均直接走该流水线，without requiring `艺术家` or any
Grok Bot；Mac mini 常驻 Bot 仅可选择性 FYI `艺术家` 做协调／终审，`非门禁`，不阻塞。
详细设计路由见 [`bots-and-agents.md`](../goal-task/references/bots-and-agents.md)。

启动父 TL 时 Parent ≥ Child Allowlist，基线包含 `~/Development`、`~/.copilot`、
`~/.agents`、`~/.codex`、`~/orca`、`/Volumes/SSD`、`/tmp`、`/private/tmp`、
`/var/folders`、`/private/var/folders`；额外路径须在 Goal brief 声明。

## 2. 监控、通知与解卡

`goal-supervisor.py scan` 按 Explorer/pr_monitor 10m、Designer/Reviewer 15m、Writer
30m、TL 40m 检测 `BLOCKED_AGENT`、`ZOMBIE_LEASE`、`ORPHAN_TL_WAIT`；等待使用
`30–60s` slices。`good-des` 等短名按 ledger、TL、cwd 绑定，不按前缀猜测。

Blocked bridge 写 `blocked-<agent>-<ts>.md`。协调者先读 visible prompt 再作 Tier A/A′
决定；**通知桥 ≠ 解卡**，不得 auto-Enter、不得把 `blocked` 当完成。
**GOAL_DONE 关窗与 blocked alerts 两条独立**。

Tier A/A′ 才可执行：
```sh
herdr agent read "$AGENT" --source visible --lines 40
herdr agent send-keys "$AGENT" enter
```
未声明路径、凭据或破坏性命令记 `NEED_GO`/`NEED_OWNER_DECISION`，交 `Owner`。

## 3. 两级完成与回收

每次只沟通一层。Bot 不创建、prompt 或关闭 TL 下属 Agent；TL 负责其 Subagent。
`pr_monitor` 从主仓或 `~/Development/handoff/<goal>` 启动，做 Job 级 Fail-Fast，不使用
`gh run watch`。确认 `PR_MERGED` 与 R10 后，关闭 pane，执行
`git worktree remove "$TASK_WORKTREE"`、`git worktree prune`；未合并／暂停（HOLD）或
dirty Worktree 必须保留并报告 `Owner`。`GOAL_DONE` 时关闭 Goal TL pane/workspace；窗格
关闭与 Worktree 处置互不替代。

## 4. 固定操作顺序

1. 读取 ledger，核对 Owner、TL、合同 SHA 与 Goal 状态。
2. 读取 `herdr agent get`，逐项对账 `working`、`idle`、`done`、`blocked`。
3. 判断 live TL 后才 `enqueue` 或开 TL；不得先开 pane。
4. UI/UX 直接在本 Goal 内跑固定流水线，细则以 `bots-and-agents.md` 为准。
5. 入队保留 `task_id`、`repo`、`paths`、`brief`、依赖和真实时间戳。
6. 解卡、hold、完成和异常写入 `ledger.md`。

唤醒模板必须保留状态守卫：
```sh
TL_STATUS="$(herdr agent get "$GOAL-goal-tl-r1" --json | jq -r '.agent_status')"
if [ "$TL_STATUS" = "idle" ] || [ "$TL_STATUS" = "done" ]; then
  herdr agent prompt "$GOAL-goal-tl-r1" "Hot-append task $TASK_ID; run goal-queue.py next-wave"
  herdr agent send-keys "$GOAL-goal-tl-r1" enter
fi
```

## 5. PR DAG supervision

Bot 只做有限快照，持续 CI/auto-merge 交 `pr_monitor`。按
`S1 CONFLICT`、`S2 UNRESOLVED_COMMENTS`、`S3 CI_FAILURE`、`S4 PR_MERGED_ADVANCE_DAG`
建立稳定修复 brief；同一信号最多两轮，依赖未读回 `MERGED` 时 hold，不猜测成功。
所有修复仍遵循 `goal-task` 的 Owned Paths、异构 Reviewer、V5 和 R10。

## 6. 典型 dispatch 形态

```sh
herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$TASK_WORKTREE" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$GOAL_HANDOFF" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$GOAL_HANDOFF" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus
herdr agent start "$WRITER_NAME" --kind copilot --pane "$NEW_PANE_ID" -- --model gpt-5.6-luna
```

推荐通过 `herdr-dispatch.sh` 统一校验 pane、模型、allowlist 与 bounded wait。不要在
`--` 后重复传 `codex`/`copilot` 二进制名。脚本与模型参数唯一来源见上述 references。
