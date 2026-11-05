# Bot 与 CLI agent：分工与生命周期

本文件是角色、机器、模型与生命周期的共享参考；合同由
[`SKILL.md`](../SKILL.md) 规定，模型与启动参数只看
[`runtime-profiles.yaml`](runtime-profiles.yaml)，事件与 R10 只看
[`ledger.md`](ledger.md)。Goal-level Bot/TL 回收另见
[`bot-tl-orchestration`](../../bot-tl-orchestration/SKILL.md)。

## 两层与阶段

| 层 | 谁开 | 生命周期 |
|---|---|---|
| bot | Owner | 长期 |
| CLI agent | Bot 或 TL | Goal/Task 完成即关 |

Phase 0 只读探查、对齐与 handoff；Phase 1 冻结 `spec.md` 后按
`Explorer → Designer → Writer → CI Design Gate → Cross-Vendor Reviewer` 执行。UI
设计不要求 `艺术家` 或任何 Grok Bot；without requiring `艺术家` or any Grok Bot。
Mac mini Bot 可选择性 FYI，`非门禁`。
设计者（Designer）首步加载 `skill("my-designer")`；完成后协调者可执行
`herdr pane close`，不得把 `blocked` 当完成。

<a id="design-routing-machine-split"></a>

## 设计路由与 Spec-First

`Designer` 是 `writes: handoff-only`，不写业务代码或 Demo。UI Goal 先核对
`shared-design-system`、`Package.resolved` 和 upstream primitives，再写
`design.md` 的状态矩阵、双外观、width budget、3-tier text 与
scenario → component & motion mapping。超过 `>15` 行的 reusable layout/chrome glue
先拆 upstream Task；细则归 [`five-layer-craft.md`](../../my-designer/references/five-layer-craft.md)。

**Spec-First Requirement Change Rule**：`spec.md`/`design.md`/`docs/design-contract.md`
是唯一上游权威。`Explorer` 输出 **Obsolete Test Manifest**；Designer/Planner 提交
**Deprecated Test Clauses to Remove/Rewrite**；`Planner Reviewer` 核验后，`Code Writer`
先迁移获授权旧断言，`Code/UI Reviewer` 保留非 deprecated regression tests。

## 监督与阻塞

`goal-supervisor.py` 按 Explorer/pr_monitor 10m、Designer/Reviewer 15m、Writer 30m、
TL 40m 检测 `ZOMBIE_LEASE`、`BLOCKED_AGENT`、`ORPHAN_TL_WAIT`；每次等待使用
`30–60s` bounded slices。`/Volumes/SSD` 是 allowlist 父级基线，`good-des` 短名按
ledger/TL/cwd 绑定。

Blocked bridge 写 `blocked-<agent>-<ts>.md`。先读 visible prompt 再决定；**通知桥 ≠ 解卡**，
不得 auto-Enter、不得把 `blocked` 当完成。**GOAL_DONE 关窗与 blocked alerts 两条独立**。
未声明路径、凭据或破坏性动作记 `NEED_GO`，交 `Owner`。

## 设计与 Writer skill 路由

| 技术栈 | Required Skills |
|---|---|
| SwiftUI 图表 | `guide-swiftui-charts` + `dataviz` + `my-designer` |
| SwiftUI 布局/材质 | `swiftui` + `hig` + `ios-liquid-glass` + `impeccable` |
| UI 现代化 | `visual-design-modernization` + `apple-design-reviewer` |
| Swift 并发 | `guide-swift-concurrency` + `swift-concurrency` |
| SwiftData | `guide-swiftdata` + `swiftdata` |
| macOS SwiftPM | `guide-macos-spm-packaging` + `guide-swift-testing` + `swift-testing` + `xcode-cli` |

角色分工：`Designer` 只写 handoff；Writer 是 `worker.writer`，不递归派发；
Reviewer 与 Writer 不同家；`pr_monitor` 的顺序为 `mai-code-2-flash` → `gpt-5.6-luna`。
首轮 Writer 不得直接使用 `writer_escalated`。

### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）

`goal-queue.py enqueue` 按 `(repo, paths)` 自动形成并行波次或串行链；Hot-Append 不重复
拉起 TL，记录 `EVT=HOT_APPEND`，`next-wave` 直到 `drained: true`。反馈 manifest、
Mandate、spec/brief SHA、supersession 和 acceptance evidence 详见 [`ledger.md`](ledger.md)。

## 生命周期与 PR 监控

所有 CLI agent 经 `herdr-dispatch.sh` 启动，显式 `--direction right|down`，Parent ≥ Child
allowlist；`--` 后不能重复传 `codex`/`copilot`。以下示例必须使用真实 pane ID：

```sh
herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$TASK_WORKTREE" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$GOAL_HANDOFF" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction right --cwd "$GOAL_HANDOFF" --no-focus
herdr pane split --pane "$HERDR_PANE_ID" --direction down --cwd "$TASK_WORKTREE" --no-focus
```

Writer 完成本地验证、`git push` 和 R10 后立即返回，严禁 `gh run watch` 或长 sleep。
PR 开启后交 `pr_monitor` 做 Job 级 Fail-Fast；Run 未结束时使用单 Job logs，不用
`gh run view --log-failed`。确认 `PR_MERGED`、R10 后才执行
`git worktree remove "$TASK_WORKTREE"`；未合并／暂停（HOLD）或 dirty 现场保留并报告
`Owner`。`GOAL_DONE` 关闭 TL pane/workspace，Worktree 处置独立。

## 设计物理门禁索引

全局审查保留 `sips -g pixelWidth`、zero numeric truncation、`…`、`V2 P1` 与
`followed the brief verbatim`；`Anti-Gaming Visual Harmony Gate`、字号与间距细则只在
[`five-layer-craft.md`](../../my-designer/references/five-layer-craft.md)，避免把品类
规则复制到编排合同。
