# Playbook Leaf · §1.5 CLI/Herdr 7-step SOP & Socket Bridge

本文件为 [`playbook.md`](./playbook.md) §1.5 的按需展开切片（`L1` leaf `<= 145` 行），完整命令模板见 [`dispatch-recipes.json`](./dispatch-recipes.json)。推荐优先调用 [`scripts/herdr-dispatch.sh`](../scripts/herdr-dispatch.sh) 自动完成 Step 2–4 的分屏、校验、启动与派发；禁止裸 `--current`，禁止把个人路径、账号或令牌写进仓库。

## Step 1–4 — 预检、分屏、具名启动与派发

命名格式 `<goal>-<task>-<role>-r<round>`（满足 `[a-z][a-z0-9_-]{0,31}`），按 [`runtime-profiles.yaml`](./runtime-profiles.yaml)（历史归档见 [`model-roles.md`](./model-roles.md)）显式传参，宽窗格用 `right`、窄/高窗格用 `down`（**严禁省略 `--direction`**），`--` 后严禁重复传二进制名：

```sh
set -euo pipefail
test "${HERDR_ENV:-}" = 1 && test -n "${HERDR_SOCKET_PATH:-}" && test -n "${HERDR_WORKSPACE_ID:-}" && test -n "${HERDR_TAB_ID:-}" && test -n "${HERDR_PANE_ID:-}"
SPLIT_JSON="$(herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$TASK_WORKTREE" --no-focus)"
NEW_PANE_ID="$(printf '%s' "$SPLIT_JSON" | jq -er '.result.pane.pane_id')" && test -n "$NEW_PANE_ID"
skills/team-lead/scripts/herdr-dispatch.sh --name "${GOAL_SLUG}-${TASK_ID}-writer-r1" --kind copilot --cwd "$TASK_WORKTREE" --direction right \
  --prompt "First call create_goal bound to $TASK_BRIEF; read $TASK_BRIEF; implement only Owned Paths; write R10 receipt to $R10_RECEIPT; then call update_goal(status=\"complete\"|\"blocked\")." \
  --wait --timeout 600000 -- --model gpt-5.6-luna
```

Reviewer 必须跨模型厂商；Writer 严禁 `gh run watch` 或 `sleep >= 30` 等待远端 CI；PR 开启后默认用 Layer-0 `ci-monitor.py`（失败诊断回退 `pr_monitor`）。订阅 `pane.agent_status_changed` 的 Socket 监听器见 [`dispatch-recipes.json`](./dispatch-recipes.json)（`socket_event_listener_node`）。`goal-supervisor.py notify-bridge` 仅在 `status == "blocked"` 时写入 `handoff/<goal_folder>/alerts/blocked-<agent>-<ts>.md`。**通知桥 ≠ 解卡**，**GOAL_DONE 关窗与 blocked alerts 两条独立**。

## Step 5 — 执行中裁决与失败升档

同 Goal 顺带缺口且不触四红线时记 `EVT=RULING`；第 1 轮 V5 `FAIL` 原 Writer 修复，第 2 轮升档 `writer_escalated`，仍 `FAIL` 转 `blocked`。

## Step 6 — 安全读取与 R10 对账

Working 读 `--source visible --lines 40`；`idle`/`done` 读 `--source recent-unwrapped --lines 80`。对账优先级：`磁盘 R10 回执 → Git SHA → CI/发布读回 → Herdr 状态`。

## Step 7 — 回收窗格、Worktree 与 Goal Workspace

```sh
herdr pane close "$NEW_PANE_ID"
git -C "$MAIN_REPO" worktree remove "$TASK_WORKTREE" && git -C "$MAIN_REPO" worktree prune && git -C "$MAIN_REPO" branch -D "$TASK_BRANCH"
```

`EVT=GOAL_DONE` 后拆为独立验收：**A. Pane/workspace 关闭（MUST）**（`herdr workspace close "$GOAL_WORKSPACE"` 或延迟自闭）；**B. Worktree 处置**（已 `PR_MERGED` 清理，未合并/`HOLD` 保留现场且不阻断 A）。
