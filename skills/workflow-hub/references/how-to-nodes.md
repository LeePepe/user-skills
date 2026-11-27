# 可组合 How-To 节点图谱

L1 只描述节点独有 Internal；复用通过 Calls，完成后通过 PointsTo 交接。每个节点遵循 `Inbound → Internal → Calls → PointsTo → Trigger/Notify` 五元组；Composite 只编排，不抄子节点实现。

## §1 四层归位与 L4–L1 控制论金字塔

- **L4–L1 四层控制论金字塔（Cybernetic Pyramid，`howto_flow.py --pyramid`）**：**L4 人类掌舵**（Human Helmsman：Direction & Kill-Switch / 120Hz Taste / Oracle 裁判保护 / Evidence 终验，经 `I5`/`G1` 桥接）↔ **L3 熵减自演进**（Agent Helping Agent Improve：`P2, P3, P7, P8` + `agent-retro`/`skill-retro`/`retro-loop`/`skill-contract`/`repo-kit`）↔ **L2 协同编排**（Agent Guiding Agent：`I7, E6, E10, E11` + `personal-m2-assistant`/`m2-dispatcher`/`team-lead`/`code-review`）↔ **L1 专项执行**（Agent Doing the Work：其余标准业务节点 `E1–E5, E7–E9, E12, Q1–Q3, I1–I6, G1–G2, P1, P4–P6, P9, O1–O6`）。
- **四层仓库物理归位**：`L1` `~/Development/user-skills/skills`（workflow/研究/设计/审查/复盘/叙事）· `L2` `~/Development/Skills/skills`（repo/数仓/家居/工具）· `L3` `~/Development/job skills`（工作专项）· `L4` `<repo>/.claude/skills/`（仓内构建/资源/发版）。

## §2 标准节点目录

### Group A：工程质量（E1–E12、Q1–Q3）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| E1 Feature | Manual/G1 | 人/任务输入；定义边界、不做项与端到端集成验收 | E5, E6, E7, E4, E3, E2, E10, E12 | G2, I5 | TK-04/TK-05/TK-06 |
| E2 Bug List | Manual/QA | 批量缺陷清单、波次编排、devsync 与幂等可逆撤回 | E4a, I5, E3, E12 | G1, I5 | TK-02/TK-03 |
| E3 Single Bug | Manual/E1/E2/Q1 | 单点缺陷根因定位、fail-first 复现与最小闭环修复 | E4a, E4, E9, E10 | I5, E12 | TK-01/TK-06 |
| E4 Code Task | E1/E3/E10/P7/P8 | 独立 Worktree、Owned Paths、租约管理与 keep/drop | E9, I5 | E10, G1 | TK-16 |
| E4a Triage Bug | Manual/E2/E3/Q1 | Personal 五轨与业务六桶责任判定，筛除 BY DESIGN/配置项 | I3, E8 | E3, G1, I5 | TK-01/TK-02 |
| E5 Pre-Flight | Manual/E1/Q3/P5/P9 | Native/H5 边界、实例/hash、AASA/Adjust 与外部依赖探查 | I3, E8 | I5, E6 | TK-05 |
| E6 Design/Decompose | Manual/E1/P9 | 宿主层/Bridge Seam/复用视图拆解，冻结 spec_sha 与 Tasks | E5 | E1, E4 | TK-04 |
| E7 UI/Visual | Manual/E1/E11 | Apple 原生系统动态外观跟随、四态双外观截图与 Visual Traceback | E9 | E4, G1, I5 | 版本 pin/框选 |
| E8 Telemetry | Manual/Pulse | Kusto/Databricks 队列与基线核验，空字段不作差分依据 | — | Q1, G1, G2 | 证据快照基线 |
| E9 Verify 6DQ | Hook/E3/E4/E7/E10/E12 | D1→L1→L2→G2→L3 渐进验证阶梯与确定性门禁读回 | — | E10, E12, I5 | 仓库 verify 覆盖 |
| E10 PR | PR/Pulse/E1/E3/P7/P8 | 建 PR、precheck 盯梢、DAG 自愈与 CI/Worktree 回收 | E11, I3, E4, E9, I5 | E12, G2 | TK-10/TK-14 |
| E11 Review PR | Webhook/Pulse/E10/Q2 | V1–V5 双轴跨厂商代码审查，含 UI 视觉回溯归因 | E7 | E10, Q2 | 已成型 |
| E12 Release | Manual/CI/E1/E2/E3/E10 | 签名/公证/hash 产物构建、发版门禁与线上真实读回 | E9, E8, I5 | G2, Q1, Q3 | TK-10/TK-14 |
| Q1 DSAT/QA-DE | Pulse/Manual | DSAT/Crash 异动侦测、DE 派单与线上质量看板分流 | E8, E4a, Q2 | E3, E2, Q3, G1, G2 | TK-14 |
| Q2 Review DE Fix | DE PR/Q1 | QA 证据核对、DE 修复审查、集成回归与版本准入 | E11, E8 | E10, E3 | TK-14 |
| Q3 Postmortem | 事故/Q1/O2 | Git+遥测+工单三路取证、九类误判排查与物理锁沉淀 | E5, E8, I4 | E3, G1, P7, P8 | Action→物理锁 |

### Group B：信息与治理（I1–I7、G1–G2）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| I1 Teams | Manual/Pulse | teams-cli 未读+关注群采集、0600 水位与降级深链 | I3, I4 | I5, I6, G1, G2 | TK-08/TK-11 |
| I2 Email | Manual/Pulse | workiq 只读增量水位采集与最小必要元数据抽取 | I3, I4 | I5, G1, G2, E10 | TK-09/TK-10/TK-11 |
| I3 Classify | I1/I2/E2/E4a/E5/E10/P1 | Falcon/Jev 毫秒级正交分桶判断，Star 样本回流评测集 | — | I4, I5, E4a | TK-07/TK-13 |
| I4 Summarize | I1/I2/E5/Q3/G2/P1/P5 | 提炼事实、影响、责任人、截止期与带出处脱敏摘要 | — | I5, G2, P6 | TK-08/TK-09 |
| I5 Notify | 全局节点回调 | P0 拍板 / P1 回执 / P2 日报去重投递并绑定唯一落点 | — | G1, E1, E2, E4, I6 | TK-12 |
| I6 Reply Teams | I1草稿+I5确认 | approvalBinding 与 tail 无碰撞校验，plan→apply→verify 回读 | — | I5 | TK-08（已落地） |
| I7 Unified M2 Ingress | Manual/CLI/dsh | `personal-m2-assistant` 跨会话拆解话题，Owner Gate 后按 wave 派发 | I3, I5 | G1, E1, E2 | 禁读私有存储 |
| G1 Task | Manual/I1/I2/Q1/Q3/P1 | Linear/ADO 幂等去重、`1 Goal = 1 Issue` 与 R10 台账绑定 | — | E1, E2, E3, P7, P8 | 跨源幂等键 |
| G2 FlightBook | 周期/I2/E10/E12/E8 | 五面状态同步、里程碑证据池聚合与真实读回校验 | E8, I4, I5 | P1 | TK-10 |

### Group C：个人、底座、云与家庭（P1–P9、O1–O6）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| P1 AIDash Hub | Pulse+UI Hook | 汇聚 P2/P3/E4/E10/I1/I2 快照卡片并响应点击交互动作 | P2, P3, I5 | G1, P7, P8, P6, I3, E1, E2, E4, I6 | TK-11/TK-13 |
| P2 aidata | Pulse/P1 | CLI 会话/Token/缓存/耗时采集，构建 L1–L5 脱敏星型数仓 | — | P1, P3 | TK-11 |
| P3 retro-loop | P1/Manual | H1–H5 根因诊断、三道物理锁提炼与 proposals 生成 | I3, I4 | P1, P7, P8 | TK-13 |
| P4 Save Resource | Manual | 工具/Repo 链接去重归档与索引登记（save-tool 零执行） | — | P5 | 已成型 |
| P5 Digest Knowledge | Manual | 双通道知识检索拆解，沉淀极简 TL;DR/FAQ/行动清单 | E5, I4 | P6 | — |
| P6 Public Story | Manual/P1/P5 | 四元素（Provenance/Delta/Pivot/Takeaway）脱敏叙事产出 | I4, E9 | I5 | TK-13 |
| P7 Make Skill | Manual/P1/Q3 | 四层归位、预算门禁、sink-rule、audit/eval 与 S1/S2/S3 共享 | E4, E9, E10 | I5, E9 | TK-15 |
| P8 Standardize Repo | Manual/P3/Q3 | repo-kit init/adopt/split、AGENTS、verify 与 shared-ci 对齐 | E4, E9, E10 | E9, E10 | TK-15 |
| P9 Layered Warehouse | Manual | 真实模型与管线分层建模（ODS/DWD/DIM/DWS/ADS）及血缘门禁 | E5, E6 | G1, P2, E1 | — |
| O1 Cloud Cost | Manual/Pulse | 云资源账单巡检、异常消耗归因与配额告警 | E8 | G1, E4 | 按需调用 |
| O2 Cloud Observability | Manual/Pulse | 云端服务健康度、延迟/错误率监控与故障升级 | E8 | G1, Q3 | 按需调用 |
| O3 Cloud RBAC | Manual/Pulse | 最小权限审计、密钥轮换与访问控制边界核验 | — | G1, E4 | 按需调用 |
| O4 Protected Feeds | Manual/Pulse | 受保护私有包源鉴权、缓存健康与依赖拉取排障 | — | G1, E4 | 按需调用 |
| O5 NAS Ops | Manual | 绿联 NAS SSH 强门禁、容器编排与冷热备份核验 | I5 | I5 | — |
| O6 Smart Home | Manual | Home Assistant/Node-RED/HomeKit/米家控制权边界协同 | O5, I5 | I5 | — |

## §4 Broken-edge repair清册（TK-01..TK-16）

| ID | Edge | Trigger/Notify | Owned Paths | Acceptance Gate |
|---|---|---|---|---|
| TK-01 | E4a Personal轨→E3 | Manual/E2/E3/Q1；P0/P1 | user-skills、bug-triage | 五轨+业务六桶统一分流 JSON |
| TK-02 | E2→E4a→I5→E3 | QA导入；争议P0 | bug-triage、goal-queue | 批量清单先筛除再排波次，未拍板不写码 |
| TK-03 | E2 manifest↔devsync revert | 波次；冲突P0/成功P1 | devsync handoff、team-lead | 单命令幂等 drop/revert 并同步清单状态 |
| TK-04 | E1→E6→E4 | Feature；未拆解P0 | codebase-design、team-lead | 四层复用检查表 + Owned Paths/Interfaces |
| TK-05 | E1→E5→I5 | Phase 0；缺件P0 | Pre-Flight handoff | 实例/hash/AASA/Adjust 闭合后才派发 E4 |
| TK-06 | E1→E3/E2回流 | QA/联调；阻塞P0 | team-lead、goal-queue | 热追加缺陷修复后回流 E1 端到端验收 |
| TK-07 | I3正交标签/回写 | 被多节点；低置信P0 | jev_client、`sync-jev-stars` | 正交标签+置信度输出，Star 样本回流评测集 |
| TK-08 | I1→I3→I4→I5→I6 | Pulse/Manual；确认后P1 | `packages/dsh-teams/**` | 0600 水位→Jev 分桶→草稿确认→防碰撞发送与深链回读 |
| TK-09 | I2→I3→I4 | workiq Pulse；P0/P1 | workiq、linear-board | 只读 state.json 增量水位，严禁发信副作用 |
| TK-10 | I2/E10/E12→G2 | Hook/周五Pulse | flightbook handoff | 幂等 evidence inbox 投递与五面真实读回 |
| TK-11 | P1 Pulse→E10/P2/I1/I2 | launchd；失败P0/P1 | AIDash installer | job registry/plist 安装状态与心跳读回 |
| TK-12 | I5→Waiting Node | 全局节点；P0/P1/P2 | notify/wake/AIDash | event_id 跨通道幂等去重，点击拍板解封等待节点 |
| TK-13 | P1→G1/P7/P6/I3 | UI Hook；成功P1 | AIDash、retro/linear/story | Star/Done/Promote 动作幂等回流下游节点 |
| TK-14 | Q1→Q2→E10→E12→E8 | DE/Pulse；退化P0 | DSAT/QA-DE/Postmortem | 发布观察窗验证线上指标恢复且零回归 |
| TK-15 | P7/P8→E9/四端 | Hook/漂移Pulse | skill-contract、repo-kit | S1/S2/S3 零冗余共享、`description-eval` 与 verify 对齐 |
| TK-16 | E4网关/租约/回收 | Herdr Hook/scan | m2-dispatcher、team-lead | 网关回退、ZOMBIE 租约告警与 GOAL_DONE 自动回收 |

Required guardrail rows: `S1` relative Markdown links, `S2` relative git symlinks, `S3` ≤25L `_CANONICAL_PATH` wrappers.

## §5 Routing and sink rules

Thin Internal stays in this graph; thick domain knowledge, CLI/API, or deterministic gates become a skill with `scripts/` gates. Use `skill_contract.py audit --all --strict`, `sync-jev-stars`, `description-eval`, and `sink-rule`. L0 routes to L1 drawers, then L2 commands; no automatic cross-vault sync or Star publication.

## §6 High-value recipes

- `P9/P2 → P1 → E7 → E4`: aidata snapshot → AIDash card → UI evidence → Goal task.
- `E7 → P7 → E4 → E9` (**Apple OS-Native Dynamic System Appearance / Zero Custom Theme Engine**): Inbound UI light/dark request → Internal binds strictly to Apple OS system appearance (`NSAppearance` / `UITraitCollection` / `@Environment(\.colorScheme)` + semantic dynamic tokens; never build custom `.designTheme`, in-app theme switchers, or Web-CSS-style parallel theme state machines) → Calls `my-designer`, `skill-contract`, M1 `E4`/`E9` dual-appearance check → PointsTo `G1`/`I5` receipt → Trigger/Notify Manual + P0 spec / P1 PR gate (`howto_guard.py validate-recipe`).
- `I1 → I3 → I4 → I5 → I6` (`TK-08`): Teams ingest → classify → summarize → Owner notify → grounded reply; no blind resend.
- `G1/E2/E10/I3/I5/P7`: Root Tracker deduplicates Goal/Task/PR evidence; Linear is the only writable tracker.
- `aidash-metrics` handles 厚领域流程; `howto` remains the thin workflow entry.

Validation: `python3 -m unittest scripts/test_m2_router.py`, `python3 skills/howto/scripts/howto_guard.py check-nodes`, and `./scripts/verify --all`.
