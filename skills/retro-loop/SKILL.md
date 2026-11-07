---
name: retro-loop
description: 结合 aidata 数仓（L1–L4）发现产品与工程异常，定位 H1–H5 根因与 3 Physical Locks，写入 proposals.jsonl 展示在 AIDash 上，并通过 Star/Done 事件回流 Linear TIA。
---
# Retro Loop（`aidata` 发现 ➔ 提出问题 ➔ `AIDash` 展示 ➔ `Linear` 回流）

`retro-loop` 是个人工作流的环 7a。它不写空洞的口头总结，而是直接对接本机 `~/Development/AIDash/aidata` 星型数仓与 `AIDash.app`，跑通 **「发现问题 ➔ 提出问题 ➔ 展示在 AIDash ➔ 拍板回流 Linear / 落物理锁」** 的完整闭环。

## 端到端数据流（End-to-End Flow）

```text
【1. 发现问题：aidata L1–L4 自动采集与异常检测】
  │  • L1–L3：采集 Goal 台账 (gtt_ledger)、回执 (gtt_receipt)、网关账单 (raven)、
  │           CI 运行 (ci_github)、Worktree 卫生与会话轨迹 → 合入 L3 warehouse.db
  │  • L4 具名复盘查询 (`aidata/L4_serve/queries/retro/*.sql` + `cost/` + `behavior/`)：
  │      ├─ `retro/gtt_scorecard.sql`          (Goal 阻塞、返工率、台账状态漂移)
  │      ├─ `retro/bot_tl_operations.sql`      (Steering/Writer 超时、解卡、配额异常)
  │      ├─ `retro/ci_health_and_noise.sql`    (CI 失败与空跑配置噪音)
  │      ├─ `retro/workspace_hygiene_reclaim.sql` (已合并未清理的残留 Worktree)
  │      └─ `retro/tf_release_deliveries.sql`  (个人 App TestFlight 交付与异常)
  ▼
【2. 提出问题：retro-loop 根因归因 + 写入 `proposals.jsonl`】
  │  • 按 Project Loop A（个人 App 体验/UI/构建）与 Engineering Loop B（H1–H5 工作流分岔点）归因；
  │  • 调用 `retro_story_engine.py propose` 将可执行改进提案写入：
  │    `~/Development/AIDash/aidata/L5_apps/digest/state/proposals.jsonl` (`status: "pending"`)
  ▼
【3. 展示在 AIDash：L5 `inbox.py` -> `aidash` CLI -> XPC -> `AIDash.app`】
  │  • `aidata/L5_apps/digest/inbox.py` 聚合 4 个桶：
  │      ├─ `待决策`：读取 `proposals.jsonl` 中的 `pending` 提案（`ref = "proposal://<id>"`）
  │      ├─ `卡顿`  ：`retro/gtt_scorecard` 阻塞 Goal、卡住 PR 与近期错误日志
  │      ├─ `发现`  ：CI 配置噪音、可回收 Worktree、高价模型琐碎输出浪费
  │      └─ `计划`  ：在途待办项
  │  • 经 `aidash` CLI XPC 推送到 macOS 菜单栏/主窗口与 iPhone 的「今日规划 / Needs a Person」卡片。
  ▼
【4. 拍板与回流：`AIDash` 交互 -> `event_bridge.py` -> `linear-board` / 3 Physical Locks】
     • 你在 `AIDash` 上对提案卡片点 **⭐ Star（批准）**：
       `event_bridge.py` 自动调用 `skills/team-lead/scripts/linear-sync.py idea --mode auto`
       在 **Linear Team `TIA`** 创建 Issue，并将 `proposals.jsonl` 标记为 `approved`；
     • 你在 `AIDash` 上点 **✓ Done（忽略/已处理）**：
       `event_bridge.py` 自动将提案标记为 `dismissed` 消警。
```

## Project Loop A & Engineering Loop B

- **Project Loop A（个人产品与 UI 质量）**：针对 `AIDash`、`VitalStride`、`VoxPocket`、`Financial` 的构建/运行异常、TestFlight 交付（`tf_release_deliveries`）与 UI 视觉缺陷，生成产品修复提案或通过 `verify_design_cases.py register-case` 沉淀进 `my-designer` Plan C 缺陷库。
- **Engineering Loop B（AI 团队工作流 H1–H5 诊断）**：
  - **H1** 定义或验收不可实现；
  - **H2** 依赖或基线漂移；
  - **H3** 调度或权限边界错误；
  - **H4** 实现或测试缺陷；
  - **H5** 门禁、发布或读回缺失。
  定位 Earliest Load-Bearing Fork（最早承重分岔点），通过 Counterfactual gate 验证后产出改进提案。

## 3 Physical Locks（三道物理锁）

同一类问题第二次出现时，必须落下至少一道物理锁（文字提醒不算锁）：

1. **Skill / Design Case lock**：在 `user-skills` 固化精简合同，或在 `my-designer` 注册 Plan C 缺陷规则。
2. **Hook / Script lock**：在 `herdr-dispatch.sh`、`goal-queue.py`、`goal-supervisor.py` 或 Git `pre-commit`/`pre-push` 中硬性拦截。
3. **6DQ CI lock**：在 `shared-ci`、`design_contract.py`、`contract_check.sh` 中阻断契约漂移。

## Return path & CLI 用法

已确认结论经 `AIDash` 展示与 Star 审批后回流 `linear-board` 的 TIA Idea 或 `team-lead` 的 Goal/Task：

```bash
# 1. 从 aidata warehouse.db 扫描当前复盘异常信号 (发现问题)
python3 skills/retro-loop/scripts/retro_story_engine.py discover

# 2. 提出改进提案写入 aidata proposals.jsonl，供 AIDash 展示 (提出问题)
python3 skills/retro-loop/scripts/retro_story_engine.py propose \
  --id "retro-20261107-01" \
  --title "修复 CI 空跑配置噪音" \
  --detail "H5 门禁噪音：停用未配置密钥的空跑 workflow" \
  --priority high
```

实现入口：[retro_story_engine.py](scripts/retro_story_engine.py)。
