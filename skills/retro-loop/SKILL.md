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
  │      ├─ `retro/tf_release_deliveries.sql`  (个人 App TestFlight 交付与异常)
  │      ├─ `retro/app_crash_and_perf.sql`     (7d 新 Crash 签名、Crash-Free 率与 P95 退化 >20%)
  │      ├─ `retro/skill_roi_diagnostics.sql`  (S1–S4 与 S6–S10 Skill 效果/效率 ROI 诊断)
  │      └─ `retro/context_composition.sql` + `retro/tool_context_burn.sql` (S11 五维上下文切片与 S12 工具 >10k 膨胀告警)
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
     • 你在 `AIDash` 上对普通提案卡片点 **⭐ Star（批准）**：
       `event_bridge.py` 自动调用 `skills/team-lead/scripts/linear-sync.py idea --mode auto`
       在 **Linear Team `TIA`** 创建 Issue，并将 `proposals.jsonl` 标记为 `approved`；
     • `learning-topic://<topic-key>` 走独立学习审批边界：Approve 仅授权后台探索，Done 记录 Owner 结果；Star 不自动发布、归档或同步 `design-studio`。
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

1. **Skill / Config lock**：在 `user-skills` 或网关配置固化边界（如 `claude-magpie-diag` Lock 1：`magpie claude default` + `switch-only claude on` + `capCareful off`，让 Claude Code 保持 100% 原生直连 `opus`，消除中途跨协议切模型导致原生 `Agent`/`TeamCreate` 失灵）。
2. **Hook / Script lock**：在 `herdr-dispatch.sh`、`goal-queue.py`、`goal-supervisor.py` 或 Git hooks 中硬性拦截（如 `claude-magpie-diag` Lock 2：`herdr-dispatch.sh` 派发 `--kind claude` 前预检 `~/.config/magpie/quotas.json` `<80%` 与 OAuth 连通性；超限或 `401/403/429` 时在开窗前自动剥离 `--allowedTools` 并降级改派 `copilot --model magpie/group/steering`）。
3. **6DQ CI / Gateway Schema lock**：在 `shared-ci`、`raven` 协议层、`design_contract.py` 中阻断契约与协议漂移（如 `claude-magpie-diag` Lock 3：Local Raven `:7024` `sanitizeGeminiSchema` 在存在 `anyOf` 时强制剥离顶层 `out.type`，根治上游网关注入 `type: "string"` 触发的 Gemini `400 invalid_request_body`）。

## Agent 效率 5 大指标（`agent-efficiency`）与每日增量清理（`daily-cleanup`）

| 指标 ID | 指标名称 | 保底线（Floor） | Improve 方向（不限制编码内环探索，重点消灭审核往返与无效损耗） |
|---|---|---|---|
| **M1** | **审核返工往返率 (`M1_review_ping_pong`)** | R1 打回率 `<= 15%`，**`r3+` 多轮拉锯 `== 0`** | 定位高频被 Reviewer 打回的 Task，把审查发现的问题前移到提交 Review 前的内环 L1/G2 脚本 |
| **M2** | **文件反复擦写率 (`M2_edit_churn`)** | 单会话单文件 `edit <= 4 次` | 找出单会话被连改 `>=5 次` 的热点文件，补充局部契约或拆分深模块 |
| **M3** | **流转延迟、阻塞与网关错误 (`M3_flow_and_blockers`)** | `P90 TTFT <= 3s`，网关 `400/403` 错误率 `== 0` | 监控网关协议 `400`/`403`、重试率与 `TASK_BLOCKED` 弹窗阻塞 |
| **M4** | **Prompt Cache 命中率 (`M4_cache_efficiency`)** | 各逻辑组 `>= 75%` | 检查会话粘性（`stays`）与固定前缀稳定性 |
| **M5** | **主控与写者分流比 (`M5_steering_vs_coding_split`)** | `steering` Token 占比 `<= 35%` | 确保重度编码由 `group/coding` 承担，主控只做规划与裁决 |

### 观测与元数据合同

M1–M5 使用包含今天的七个 CST 自然日，`window_start` 含边界、`window_end` 不含边界；只统计真实时间戳，缺时间列的旧 schema 返回 unknown，不能把全历史贴上 7d。`source_as_of` 是对应样本最大观测时间，不是生成日期。S1/S3 是实际检查快照，使用 `snapshot` 窗口；技能行明确标注 30d 使用/会话口径与独立 R1 freshness。

保留 `S1_pin_drift`、`S2_gate_catch`、`S3_skill_health`、`S4_usage_and_r1`、`S6`–`S12` 既有键（不造 S5）。每对象/行/提案保留窗口、观测时间、样本量和覆盖状态；无样本的率/ROI 为 null。S11 分片、S12 工具 Token 与技能归因是 `estimated:true` 代理，不是实测；不推算“90% 压缩节省”。所有提案历史校验和幂等追加持有同一个 `proposals.jsonl.lock`，损坏历史显式失败。

## Return path & CLI 用法

已确认结论经 `AIDash` 展示与普通提案 Star 审批后回流 `linear-board` 的 TIA Idea 或 `team-lead` 的 Goal/Task；学习候选仍须遵守 `learning-topic://` 的独立探索与发布授权：

```bash
# 1. 计算 M1–M5 Agent 效率指标并将未达标项自动写入 proposals.jsonl 推送 AIDash
python3 skills/retro-loop/scripts/retro_story_engine.py agent-efficiency --auto-propose

# 2. 计算 S1–S4 与 S6–S10 Skill 效果与效率 ROI，将 Top 低 ROI Skill 写入 proposals.jsonl (ref="skill-roi://<skill>")
python3 skills/retro-loop/scripts/retro_story_engine.py skill-roi --auto-propose

# 3. 从 aidata warehouse.db 扫描 L4 复盘视图信号
python3 skills/retro-loop/scripts/retro_story_engine.py discover

# 4. 每日增量水位线清理（仅处理新完结的 Goal 日志与已合并 Worktree，不重复扫描已清理项）
python3 skills/team-lead/scripts/git-gc.py daily-cleanup
```

实现入口：[retro_story_engine.py](scripts/retro_story_engine.py)。
