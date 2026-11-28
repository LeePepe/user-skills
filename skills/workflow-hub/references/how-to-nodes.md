# 可组合 How-To 节点图谱

L1 只描述节点独有 Internal；复用通过 Calls，完成后通过 PointsTo 交接。每个节点遵循 `Inbound → Internal → Calls → PointsTo → Trigger/Notify` 五元组；Composite 只编排，不抄子节点实现。

## §1 四层归位

| Layer | Scope |
|---|---|
| L1 | `~/Development/user-skills/skills`: workflow、研究、设计、审查、复盘、叙事 |
| L2 | `~/Development/Skills/skills`: repo、数仓、家居、工具 |
| L3 | `~/Development/job skills`: 工作专项 |
| L4 | `<repo>/.claude/skills/`: 仓内构建、资源、多语言、发版 |

## §2 标准节点目录

### Group A：工程质量（E1–E12、Q1–Q3）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| E1 Build Feature | Manual/G1 | 需求→边界、不做项、集成/E2E | E5,E6,条件E7,1:N E4,条件E3/E2,E10,E12 | G2,I5 | TK-04/05/06 |
| E2 Bug List | Manual/QA | 清单→波次、devsync、可逆撤回 | 1:N E4a→I5→E3 | G1,I5 | TK-02/03 |
| E3 Single Bug | Manual/告警 | 缺陷→根因、fail-first、最小修复 | E4a,E4,E9,条件E10 | 父节点/I5/E12 | 外部责任转交 |
| E4 Code Task | E1/E3/P7/P8 | brief→worktree、Owned Paths、租约、keep/drop | E9,越界I5 | R10返父节点 | TK-16 |
| E4a Triage Bug | E2/E3/Q1 | 缺陷→Personal五轨/Sapphire六桶责任判定 | 条件I3,E8 | verdict→E3/G1/I5 | TK-01 |
| E5 Pre-Flight | E1/Q3/P9 | 依赖→Native/H5、实例/hash、AASA/Adjust取证 | 条件I3,E8 | 冻结证据/阻塞I5 | TK-05 |
| E6 Design/Decompose | E1/P9 | 宿主→Bridge Seam、四层复用、Goal切片 | 条件E5 | spec_sha/briefs→E1 | TK-04 |
| E7 UI/Visual | Manual/E1/E11 | Figma→四态双外观、截图、Visual Traceback | E9 | SPEC_GAP→spec；IMPL_BUG→E4 | 版本pin/框选 |
| E8 Telemetry | Manual/Pulse | cohort→Kusto/Databricks；空字段不作差异 | — | 指标返父；异动→Q1/G1 | 快照/基线 |
| E9 Verify 6DQ | Hook/E4/E7/E10/E12 | 变更→D1→L1→L2→G2→L3渐进验证 | — | pass/fail返调用方 | verify覆盖 |
| E10 PR | PR/Pulse | 分支→PR、precheck、DAG自愈、CI/回收 | E11,条件I3,E4,E9,I5 | E12/G2 | 评论分桶/Pulse |
| E11 Review PR | Webhook/E10/Q2 | diff→双轴跨厂商审查与视觉回溯 | 条件E7 | PASS/CONCERNS/FAIL→E10/Q2 | 已成型 |
| E12 Release | Manual/CI | 候选包→双轨构建、签名/hash、线上读回 | E9,条件E8,I5 | G2；退化→Q1/Q3 | 观察窗/回执 |
| Q1 DSAT/QA-DE | Pulse/Manual | DSAT/Crash异动→DE派单、Dashboard | E8,1:N E4a,条件Q2 | E3/E2/Q3/G1/G2 | TK-14 |
| Q2 Review DE Fix | DE PR/Q1 | QA证据→分析/实现/集成/版本核对 | E11,条件E8 | 通过E10；驳回DE/E3 | — |
| Q3 Postmortem | 事故/Q1/O2 | Git/遥测/ADO→九类误判、双报告 | E5,E8,I4 | E3；G1→P7/P8 | Action→物理锁 |
### Group B：信息与治理（I1–I7、G1–G2）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| I1 Teams | Manual/Pulse | teams-cli→动态未读/watched、0600水位、freshness/深链 | I3,I4 | I5→I6；G1/G2 | TK-08 |
| I2 Email | Manual/Pulse | workiq→只读watermark、最小邮件元数据 | I3,I4 | I5/G1/G2；告警E10 | TK-09 |
| I3 Classify | I1/I2/E4a/E10 | Jev→内容/动作/紧急度/置信度正交标签 | — | 标签返父；Star→sync-jev-stars | TK-07 |
| I4 Summarize | I1/I2/Q3/G2 | 多源事实→影响/责任人/截止/引用、脱敏 | — | 有出处摘要返父 | 跨渠道Schema |
| I5 Notify | 各节点事件 | event_id→P0拍板/P1回执/P2日报、唯一落点 | — | User decision→Waiting Node | TK-12 |
| I6 Reply Teams | I1草稿+I5批准 | approvalBinding/tail→plan/apply/verify；uncertain禁重发 | — | 已回复深链→I5 | 仅Owner/self meeting验收 |
| I7 Unified M2 Ingress | Manual/CLI/dsh | 自然语言→topic/repo/sub_prompt/wave；Owner Gate | I3,I5,m2-dispatcher | 1:N M2及坐标回执 | 禁读私有session/未批派发 |
| G1 Task | I1/I2/Q1/P1 | Linear/ADO→去重、1 Goal=1 Issue、Task/R10 | — | GO→E1/E2/E3/P7/P8 | 跨源幂等键 |
| G2 FlightBook | 周期/I2/E10/E12 | 邮件/PR/发布证据→五面同步、真实读回 | E8,I4,I5 | P1回执/缺件P0 | TK-10 |
### Group C：个人、底座、云与家庭（P1–P9、O1–O6）

| 节点 | 触发 | Inbound & Internal | Calls | PointsTo | 🔴断链 |
|---|---|---|---|---|---|
| P1 AIDash Hub | Pulse/UI Hook | P2/P3/E4/E10/I1/I2→装job、渲染卡、捕获点击 | P2,P3,I5 | Promote→G1/P7/P8；Star→I3/P6；拍板返父 | TK-11/13 |
| P2 aidata | Pulse/P1 | CLI会话→token/缓存/耗时、L1–L5脱敏星型表 | — | P1/P3快照 | Pulse挂载 |
| P3 retro-loop | P1/Manual | 异常证据→H1–H5根因、物理锁、proposals | 条件I3,I4 | P1；高优P7/P8 | 状态去重 |
| P4 Save Resource | Manual | 工具/repo链接→save-tool去重、收藏索引；零执行 | — | 条件P5 | 已成型 |
| P5 Digest Knowledge | Manual | 检索/拆解→TL;DR/FAQ/清单、知识归档 | 条件E5,I4 | P6 | — |
| P6 Public Story | Manual/P1/P5 | 验收素材→Provenance/Delta/Pivot/Takeaway、脱敏 | I4,条件E9 | I5发布回执 | 敏感词回归 |
| P7 Make Skill | Manual/P1/Q3 | 能力缺口→四层归位/init/预算/sink-rule、四端同步 | 条件E4,E9,E10 | 能力加固；漂移I5 | TK-15/description-eval |
| P8 Standardize Repo | Manual | 仓合同→init/adopt/split、AGENTS/verify/shared-ci | E4,E9,E10 | 仓库门禁能力 | pin/ruleset |
| P9 Layered Warehouse | Manual | ORM/DDL/管线→ODS/DWD/DIM/DWS/ADS、SCD/血缘 | E5,E6 | G1→E1 | — |
| O1 Azure Cost | Manual/Pulse | 云账单→成本归因与预算偏差 | E8 | G1→E4 | 按需取证 |
| O2 Azure Observe | Manual/Pulse | 云信号→服务健康与故障证据 | E8 | G1→E4；故障Q3 | 观测基线 |
| O3 Azure RBAC | Manual | 权限请求→角色/作用域/最小授权核对 | 条件I5 | G1→E4 | 审批证据 |
| O4 Protected Feeds | Manual | 包依赖→受保护源认证与可达性 | 条件E5 | G1→E4 | 包源读回 |
| O5 NAS | Manual | NAS操作→SSH强门禁/备份、可恢复性 | 条件I5 | NAS验收回执 | SSH不可达即停 |
| O6 Home Assistant | Manual | 家居动作→HA/HomeKit控制权边界 | O5,条件I5 | 家居状态读回 | 单控制器归属 |

## §4 Broken-edge repair清册（TK-01..TK-16）

| ID | Edge | Trigger/Notify | Owned Paths | Acceptance Gate |
|---|---|---|---|---|
| TK-01 | E4a Personal轨→E3 | Manual/E2/Q1；P0/P1 | bug-triage | 五轨+Sapphire六桶统一JSON |
| TK-02 | E2→E4a→I5→E3 | QA导入；争议P0 | bug-triage/goal-queue | 先筛后批拍，未批准不写码 |
| TK-03 | E2 manifest↔devsync revert | 波次；冲突P0/成功P1 | devsync handoff/team-lead | 幂等drop/revert并更新清单 |
| TK-04 | E1→E6→E4 | Feature；未拆解P0 | codebase-design/team-lead | 四层复用表+Owned Paths/Interfaces |
| TK-05 | E1→E5→I5 | Phase 0；缺件P0 | 依赖handoff | 实例/hash/AASA/Adjust闭合才派E4 |
| TK-06 | E1→E3/E2回流 | QA/联调；阻塞P0 | team-lead/goal-queue | 热追加修复后回E1验收 |
| TK-07 | I3标签/人工回写 | 低置信P0 | Jev client/sync-jev-stars | tags/actions/urgency/confidence+Star回归 |
| TK-08 | I1→I3→I4→I5→I6 | Pulse/Manual；批准后P1 | packages/dsh-teams | 0600水位→5×7×8→grounded draft→approvalBinding/tail→安全发送/深链读回 |
| TK-09 | I2→I3→I4 | workiq Pulse；Actionable P0 | workiq/handoff | 只读state.json增量，无发信路径 |
| TK-10 | I2/E10/E12→G2 | Hook/周五Pulse | flightbook handoff | 幂等evidence inbox→五面读回 |
| TK-11 | P1 Pulse→E10/P2/I1/I2 | launchd；失败P0/P1 | AIDash installer | job registry/plist安装状态读回 |
| TK-12 | I5→Waiting Node | 各节点；P0/P1/P2 | notify/wake/AIDash | event_id跨通道一次、点击解封 |
| TK-13 | P1→G1/P7/P6/I3 | UI Hook；成功P1 | AIDash/retro/linear/story | Promote/Star幂等回流，禁止自动发布 |
| TK-14 | Q1→Q2→E10→E12→E8 | DE/Pulse；退化P0 | DSAT/QA-DE/Postmortem | 观察窗DSAT下降且无回归 |
| TK-15 | P7/P8→E9/四端 | Hook/漂移Pulse | skill-contract/repo-kit | S1/S2/S3共享；description-eval与verify/shared-ci对齐 |
| TK-16 | E4网关/租约/回收 | Herdr Hook/scan | m2-dispatcher/team-lead | 网关回退、ZOMBIE告警、GOAL_DONE回收读回 |

Required guardrail rows: `S1` relative Markdown links, `S2` relative git symlinks, `S3` ≤25L `_CANONICAL_PATH` wrappers.

## §5 Routing and sink rules

Thin Internal stays in this graph; thick domain knowledge, CLI/API, or deterministic gates become a skill with `scripts/` gates. Use `skill_contract.py audit --all --strict`, `sync-jev-stars`, `description-eval`, and `sink-rule`. L0 routes to L1 drawers, then L2 commands; no automatic cross-vault sync or Star publication.

## §6 High-value recipes

- `P9/P2 → P1 → E7 → E4`: aidata snapshot → AIDash card → UI evidence → Goal task.
- `I1 → I3 → I4 → I5 → I6` (`TK-08`): Teams ingest → classify → summarize → Owner notify → grounded reply; no blind resend.
- `G1/E2/E10/I3/I5/P7`: Root Tracker deduplicates Goal/Task/PR evidence; Linear is the only writable tracker.
- `aidash-metrics` handles 厚领域流程; `howto` remains the thin workflow entry.

Validation: `python3 -m unittest scripts/test_m2_router.py`, `python3 skills/howto/scripts/howto_guard.py check-nodes`, and `./scripts/verify --all`.
