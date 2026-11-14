# 可组合 How-To 节点图谱

> L1 reference：节点只描述自己独有的 Internal；复用通过 Calls，完成后通过 Points To 交接。

## §1 四层 Skills 与 Repos 归位

| 层 | 归位 | 典型职责 |
|---|---|---|
| L1 通用编排 | `~/Development/user-skills/skills` | workflow、调研、设计、审查、复盘、叙事 |
| L2 个人底座 | `~/Development/Skills/skills`（含顶层） | repo 治理、数仓、家居、工具 |
| L3 工作专项 | `~/Development/job skills` | MSFT/ADO/GHE/Kusto/M365 |
| L4 仓内专属 | `<repo>/.claude/skills/` | 单仓构建、资源、多语言、发版 |

| Repo 版图 | 归属示例 |
|---|---|
| Personal | `AIDash`/`aidata`、`VoxPocket`、`WebIQSearch`、`VitalStride`、`raven`、`agent-herdr`、`shared-ci`、`Nas` |
| Work | `bing-growth-sapphire-app`、`Sapphire`、`app-ios`、`OneAuth`、`FlightBook`、`Postmortem`、`ChatSDK-*` |

## §2 五元组与核心调用

**五元组**：`Inbound`（输入与上游）→ `Internal`（仅本节点逻辑）→ `Calls`（调用子节点并取回结果）→ `PointsTo`（完成后下游）→ `Trigger/Notify`（Manual/Hook/Pulse 与 P0/P1/P2）。Primitive 不再拆 How-To；Composite 只编排，不抄子节点实现。

- **E2 Fix Bug List** —`Calls 1:N`→ **E4a Triage Bug**（写码前筛出 `BY DESIGN`/`REMOTE CONFIG`，交 I5 P0 批拍）—`Calls 1:N`→ **E3 Fix Single Bug**（仅确认可修项）。
- **E1 Build Feature** —`Calls`→ E5 Pre-Flight / E6 四层复用拆解 / 条件 E7 UI / `1:N` E4 编码切片 / 条件 E3 或 E2（QA/联调 Bug）/ E10 PR / E12 发布。
- **P1 AIDash** 五条入边：P2 `aidata`、P3 `retro-loop`、E4/`goal-supervisor` 阻塞拍板、E10 PR 状态、I1/I2 Teams/Email 摘要；出边：`Star/Done/Promote/拍板`→ G1 / P7 / P8 / P6 / Waiting Node（并回调 E1/E2/E4/I6）。
- `Calls` 是子流程返回父节点；`PointsTo` 是结果交接，不等于父子包含。缺口必须标在节点或边上。

## §3 标准节点目录（30 个节点族；范围 ID 展开）

列：`节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链`

### Group A：工程质量（E1–E12、Q1–Q3）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| E1 Feature | Manual/G1 | 人/任务；边界、不做项、集成/E2E | E5,E6,条件E7,1:N E4,条件E3/E2,E10→E12 | G2,I5 | E6复用门；QA回流E3 |
| E2 Bug List | Manual/QA | 清单、波次、devsync、可逆撤回 | 1:N E4a→I5拍板→1:N E3；条件E12 | G1/I5 | 预筛与幂等revert |
| E3 Single Bug | Manual/告警/E1/E2/Q1 | 根因、fail-first、最小修复 | E4a,E4,E9；条件E10 | I5/E12或回父节点 | 双轨词典、外部转交 |
| E4 Code Task | E1/E3/E10/P7/P8 | 独立 worktree、Owned Paths、租约、keep/drop | E9；越界条件I5 | R10回父节点 | Tier解卡与租约回读 |
| E4a Triage Bug | Manual/E2/E3/Q1 | Personal五轨；Sapphire六桶；责任判定 | 条件I3、E8 | E3或G1/I5；返结构化 verdict | Personal规则卡 |
| E5 Pre-Flight | Manual/E1/Q3/P5/P9 | Native/H5、实例/hash、AASA/Adjust、外部依赖 | 条件I3/E8 | I5阻塞或返冻结证据 | 依赖门禁卡 |
| E6 Design/Decompose | Manual/E1/P9 | 宿主层、Bridge Seam、复用视图、挂载层；Goal→Tasks | 条件E5 | spec_sha、briefs→E1 | 四层检查模板 |
| E7 UI/Visual | Manual/E1/E11 | Figma、四态双外观、截图、Visual Traceback | E9 | SPEC_GAP→spec/E4；IMPL_BUG→E4 | 版本 pin、截图框选 |
| E8 Telemetry | Manual/Pulse/多节点 | Kusto/Databricks cohort、空字段不作 differential | — | 指标返调用方；异动→Q1/G1 | 证据快照与基线 |
| E9 Verify 6DQ | Hook/E3/E4/E7/E10/E12 | D1→L1→L2→G2→L3 渐进验证 | — | 结构化 pass/fail | 仓库 verify 覆盖 |
| E10 PR | PR/Pulse/E1/E3/P7/P8 | 建 PR、precheck、DAG 自愈、CI/worktree 回收 | E11、条件I3、E4+E9、I5 | E12或G2 | Pulse迁移、评论分桶 |
| E11 Review PR | Webhook/Pulse/E10/Q2 | 双轴跨厂商审查；UI加视觉回溯 | 条件E7 | PASS/CONCERNS/FAIL→E10/Q2 | 已成型 |
| E12 Release | Manual/CI/E1/E2/E3/E10 | 双轨构建、签名/hash、线上读回 | E9、条件E8、I5 | G2；退化→Q1/Q3 | 发布回执/观察窗统一 |
| Q1 DSAT/QA-DE | Pulse/Manual | DSAT/Crash 异动、DE派单、Dashboard | E8、1:N E4a、条件Q2 | E3/E2；Q3；G1/G2 | DE→PR→Guardrail闭环 |
| Q2 Review DE Fix | DE PR/Q1 | QA证据→分析→实现→PR→集成→版本审核 | E11、条件E8 | 通过→E10；驳回→DE/E3 | — |
| Q3 Postmortem | 事故/Q1/O2 | Git+遥测+ADO三路取证、九类误判、双报告 | E5,E8,I4 | E3；G1→P7/P8 | Action→物理锁 |

### Group B：信息与治理（I1–I6、G1–G2）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| I1 Teams | Manual/Pulse | teams-cli、群/频道、watermark、深链 | I3,I4 | I5→I6；G1/G2 | 认证、watermark、一键回复 |
| I2 Email | Manual/Pulse | workiq只读、watermark、最小元数据 | I3,I4 | I5/G1；证据→G2；告警→E10 | 采集与证据收件箱 |
| I3 Classify | 被I1/I2/E2/E4a/E5/E10/P1 | Jev正交标签：内容/动作/紧急度/置信度；Star 样本经 `sync-jev-stars` 回流 | — | 标签返调用方 | 多标签与人工回写；TK-07 闭环 |
| I4 Summarize | 被I1/I2/E5/Q3/G2/P1/P5 | 事实、影响、责任人、截止、引用、脱敏 | — | 有出处摘要 | 跨渠道Schema |
| I5 Notify | 被几乎所有节点 | P0拍板/P1回执/P2日报，去重并落唯一落点 | — | User decision→Waiting Node | event_id与回调桥 |
| I6 Reply Teams | I1草稿+I5确认 | 幂等发送、回读消息深链 | — | I5已回复 | 待实装 |
| I7 Unified M2 Ingress | Manual/CLI/dsh；P0预览/P1回执 | `personal-m2-assistant` 发现 Herdr/dsh M2；Jev I3 拆 `topic_id/target_repo/sub_prompt/wave_order/rag_context_hint`；Owner Gate 后按 wave 交付既有或新建 M2 | I3、I5、m2-dispatcher | `1:N` M2 Dispatcher；新建目标回执坐标 | dsh 私有 session 禁读；无 Owner 批准不得派发 |
| G1 Task | Manual/I1/I2/Q1/Q3/G2/P1 | Linear/ADO去重、1 Goal=1 Issue、Task/R10 | — | GO→E1/E2/E3/P7/P8 | 跨源幂等键 |
| G2 FlightBook | 周期/里程碑/I2/E10/E12/E8 | 五面同步、证据池、真实读回 | E8,I4,I5 | P1回执；缺件P0 | 邮件/PR/发布自动投递 |

### Group C：个人、底座、云与家庭（P1–P9、O1–O6）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| P1 AIDash Hub | 04:00/23:59 Pulse+UI Hook | 收 P2/P3/E4/E10/I1/I2；装job、渲染卡、捕获点击 | P2,P3,I5 | Promote→G1/P7/P8；Star→I3/P6；拍板→E1/E2/E4/I6 | 入边Schema、plist迁移 |
| P2 aidata | Pulse/P1 | CLI会话/token/缓存/耗时，L1–L5脱敏星型表 | — | P1/P3 | Pulse挂载 |
| P3 retro-loop | P1/Manual | H1–H5根因、物理锁、proposals | 条件I3/I4 | P1；高优→P7/P8 | 状态去重 |
| P4 Save Resource | Manual | 工具/repo链接去重，写收藏与索引 | — | 条件P5 | 已成型 |
| P5 Digest Knowledge | Manual | 检索或拆解；TL;DR/FAQ/清单/叙事归档 | 条件E5,I4 | P6 | — |
| P6 Public Story | Manual/P1/P5 | Provenance、Delta、Pivot、Takeaway，脱敏产出 | I4、条件E9 | I5 | 敏感词回归 |
| P7 Make Skill | Manual/P1/Q3 | 四层归位、init、预算、sink-rule、audit、`description-eval`、S1/S2/S3 零冗余共享、四端同步 | 条件E4/E9/E10 | 能力加固；漂移→I5 | 自动漂移修复；描述≤320字符 |
| P8 Standardize Repo | Manual | init/adopt/split、AGENTS、verify、shared-ci、CODEOWNERS | E4,E9,E10 | E9/E10能力 | 版本 pin/ruleset |
| P9 Layered Warehouse | Manual | ORM/DDL/派生管线→ODS/DWD/DIM/DWS/ADS、SCD/血缘 | E5,E6 | G1→E1 | — |
| O1–O4 Azure/Feeds | Manual/Pulse | 成本、观测、RBAC、受保护包源 | O1/O2→E8 | G1→E4；故障→Q3 | 按需调用 |
| O5–O6 NAS/HA | Manual | O5 SSH强门禁/备份；O6控制权边界 | O6→O5，条件I5 | 独立闭环 | — |

## §4 断链修复清册（TK-01..TK-16）

| ID | Broken Node/Edge | Trigger/Notify | Owned Paths | Acceptance Gate |
|---|---|---|---|---|
| TK-01 | E4a Personal轨→E3 | Manual/E2/E3/Q1；P0/P1 | user-skills、bug-triage | 五轨+Sapphire六桶统一JSON |
| TK-02 | E2→E4a→I5→E3 | QA导入；争议P0 | bug-triage、goal-queue、清单 | 37条级先筛、未拍板不写码 |
| TK-03 | E2 manifest↔devsync revert | 波次；冲突P0/成功P1 | devsync handoff、team-lead scripts | 一命令幂等drop/revert并更新表 |
| TK-04 | E1→E6→E4 | Feature；未拆解P0 | codebase-design、team-lead | 四层复用表+Owned Paths/Interfaces |
| TK-05 | E1→E5→I5 | Phase 0；缺件P0 | Notification/SmartBanner handoff | 实例/hash/AASA/Adjust闭合才派E4 |
| TK-06 | E1→E3/E2回流 | QA/联调；阻塞P0 | team-lead、goal-queue | 热追加修复后回E1验收 |
| TK-07 | I3正交标签/回写 | 被多节点；低置信P0 | Jev client、handoff、`sync-jev-stars` | tags/actions/urgency/confidence+Star样本闭环 |
| TK-08 | I1→I3→I4→I5→I6 | Pulse/Manual；确认后P1 | teams-cli adapter | watermark→草稿→确认发送→深链 |
| TK-09 | I2→I3→I4 | workiq Pulse；Actionable P0 | workiq、handoff | 只读state.json增量，无发信路径 |
| TK-10 | I2/E10/E12→G2 | Hook/周五Pulse | flightbook skill/handoff | 幂等 evidence inbox→五面读回 |
| TK-11 | P1 Pulse→E10/P2/I1/I2 | launchd；失败P0/P1 | AIDash installer | job registry/plist安装状态读回 |
| TK-12 | I5→Waiting Node | 所有节点；P0/P1/P2 | notify/wake/AIDash | event_id跨通道一次，点击可解封 |
| TK-13 | P1→G1/P7/P6/I3 | UI Hook；成功P1 | AIDash、retro/linear/story | Promote/Star幂等回流 |
| TK-14 | Q1→Q2→E10→E12→E8 | DE/Pulse；退化P0 | DSAT/QA-DE/Postmortem | 观察窗验证DSAT下降且无回归 |
| TK-15 | P7/P8→E9/四端 | Hook/漂移Pulse | skill-contract、drift、repo-kit | S1 相对 Markdown link、S2 相对 git symlink、S3 ≤25L `_CANONICAL_PATH` wrapper；`description-eval` 与 verify/shared-ci 对齐 |
| TK-16 | E4网关/租约/回收 | Herdr Hook/scan | m2/team-lead scripts | 回退、ZOMBIE告警、GOAL_DONE回收读回 |

## §5 新增 How-To 与独立 Skill 判定

1. 注册五元组：`Inbound / Internal / Calls / PointsTo / Trigger&Notify`；先写输入、专有逻辑、复用边、输出和通知，不孤立建节点。
2. **组合配方**：Internal 薄、只串现有节点→追加本抽屉一行/ recipe；不新建 Skill。
3. **独立 Skill**：有厚领域知识、外部 CLI/API、可复用确定性门禁→按四层归位，运行 `skill_contract.py init`，把 regex/AST/水位/Schema/像素/几何检查下沉 `scripts/*.py` 或 shared-ci；共享只允许 S1 相对 Markdown link、S2 相对 git symlink、S3 ≤25L `_CANONICAL_PATH` wrapper，且 `description <= 320`，再跑 `sink-rule`、`audit --all --strict`、`description-eval` 与四端同步。
4. 入口 L0 只保留路由；完整节点表放本 L1；机器可读状态放 L2。新节点必须附 `Owned Paths`、验证命令、缺口与 P0/P1/P2 落点。
5. **四大素材库挂载**：仅当内容高频累积、跨会话 Top-3 可复用且具图文/公开沉淀价值时，按 `/vault/`（Design & Motion、Collected Tools）、`/learn/`（Personal Knowledge）、`/thoughts/`（Retro & Story）挂载；不得自动跨库同步、Star 自动发布或执行收藏工具。

### I7 Unified M2 Ingress 组合配方

- **Inbound**：CLI/Herdr 外部入口或 dsh Omni Hub 的一条自然语言输入，加上公开的活跃 M2 发现表。
- **Internal**：`personal-m2-assistant/scripts/m2-router.py --plan` 调 I3 Jev Classify，再由 Magpie 语义拆解，生成包含 `topic_id`、`EXISTING_M2`/`NEW_M2`、完整 `sub_prompt`、软 `wave_order` 与可选 `rag_context_hint` 的计划；展示 P0 预览，Owner 以 `--approve` 放行后 `--apply` 调 Herdr `agent prompt`、`herdr-dispatch.sh` 或 dsh Session Sink。
- **Calls**：I3（分类）、I5（预览/回执）、目标 `m2-dispatcher`（各仓自己的 Spec/Goal 生命周期）。
- **PointsTo**：`1:N` 目标 M2；新建目标返回 session/agent/workspace/pane 坐标，后续由目标 M2 按原合同处理。
- **红线**：计划阶段不能创建或投递；不读取 dsh 私有 session 存储；wave 不伪装成事务顺序；Cindy 适配器复用同一 schema，不另造路由语义。
- **验证**：`python3 -m unittest scripts/test_m2_router.py`、`skill_contract.py audit --strict`、`./scripts/verify`。
- **共享/评测**：P7 通过 `description-eval`；Jev Star 通过 `sync-jev-stars` 回写 `references/eval-cases.json`。

### G1/E2/E10/I3/I5/P7 Project Root Tracker 组合配方

- **Inbound**：外部 PR/ADO Bug、本地 handoff 清单、devsync 集成分支、Worktree、`my-pr-supervisor` PoP/S1-S2-S3 状态及 `pr_eval_extractor` 评测沉淀项。
- **Internal**：按 Root Manifest 的 repo/branch/handoff/integration/keyword 锚点确定性归根；未命中时调用 I3 Jev，离线使用可解释的 keyword scorer 并标记 `NEW_ROOT_CANDIDATE`。在 Root → Workstream → Leaf 树中生成 Bugs、PR Supervision、P0 Blocking Decisions、Repo & Skill Sinks。
- **Calls**：G1 只读/幂等 upsert Linear TIA Root Parent 与 child tracking；E2 导入 Bug/devsync；E10 读取 PR supervision；I3 分类；I5 生成 Owner 拍板/PoP 提醒；P7 接收评分 `>=0.70` 的三类 repo/skill sink。
- **PointsTo**：本地 `state/root-tree-snapshot.json` → 单文件 HTML war-room；Owner 通过链接或复制命令自行完成 PoP/Merge、`record-feedback`、Sticky TL 自愈或 `devsync revert`。
- **Trigger/Notify**：Manual/Pulse；P0 用 `P0_BLOCKING_DECISION`，P1 用 `WAIT_POP_OR_MERGE`，S1→S2→S3 串行自愈；Linear 是唯一可写追踪 DB，严禁向 FlightBook、ADO、GitHub/GHE PR 反向写入。
- **Owned Paths**：`handoff/project-root-tracker/**` 与本 recipe；**验证**：`python3 -m unittest`、`python3 skills/skill-contract/scripts/skill_contract.py audit --strict`。
- **Teams timeline extension**：I1 Teams 写入 `state/teams-china-release-timeline.json`（版本、Code Freeze/QA/Release、DRI、上下文与深链）；Root Tracker 先以 `--preview` 展示候选 `targetDate`/description 与 `TIA-365` L0，I5 Owner 确认后才由 G1 更新 China Release Project Milestone 和 `## Release Timeline & Teams Context`。未确认数据只进入本地 snapshot/dashboard，不进入 Linear。
