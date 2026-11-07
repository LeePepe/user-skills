---
name: retro-loop
description: 双路复盘与 3 Physical Locks：以项目 Telemetry/Crash 和工程 Workflow/PR/Infra 证据定位最早承重分岔点，并回流 Goal/Task。
---
# Retro Loop

`retro-loop` 是 workflow-hub 的环 7a，抽取自 `team-lead` playbook §2。它不把
复盘文字当作完成，而是把可反驳的事实转成 Task、共享底座升级或物理锁。

## Project Loop A

1. **Telemetry / Performance**：比较 PLT P50/P90/P99、FPS、首屏、交互成功率、
   DSAT、版本、设备、市场、网络和样本窗口，输出带基线、阈值与读回时间窗的
   Performance Task。
2. **Crash clustering**：符号化并聚簇堆栈，固定首次/最近版本和最小复现，经过
   `postmortem`、RED regression、GREEN 修复、发布和 Crash-Free 读回。
3. **Product/UX audit**：对照 Token、组件、四态、双外观和真实旅程，生成产品层
   或共享底座 Task。

## Engineering Loop B

按 H1–H5 诊断 Workflow/PR/Infra：

- H1 定义或验收不可实现；
- H2 依赖或基线漂移；
- H3 调度或权限边界错误；
- H4 实现或测试缺陷；
- H5 门禁、发布或读回缺失。

每次记录 Evidence boundary、Observable timeline、Earliest Load-Bearing Fork、
H1–H5 verdict、Counterfactual gate、Responsibility/stop boundary，以及一个
可执行的 Goal/Task 或明确 backlog。

## 3 Physical Locks

1. **Skill lock**：在 `user-skills`/`agent-skills` 固化拆分、状态、R10、停止线
   和复盘模板。
2. **Hook lock**：用 PreToolUse、Worktree 守卫、Herdr ID 与事件订阅阻止本地
   路径和危险操作错误。
3. **6DQ CI lock**：用 `shared-ci`、`sync_contract.py`、`design_contract.py`、
   `release_sync.py` 和共享组件阻止跨仓契约、版本和发布漂移。

同一故障第二次出现时，至少增加一条不同层级的锁；文字提醒不算物理锁。

## Return path

已确认结论回流 `linear-board` 的 TIA Idea 或 `team-lead` 的 Goal/Task；证据不足
时保留 `unknown`，不得静默修改冻结合同。

实现入口：[retro_story_engine.py](scripts/retro_story_engine.py)。
