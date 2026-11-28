---
name: howto
description: 梳理、细化、复盘、串联或新增个人与工作 How-To 流程的统一入口（如 how to fix bug / bug list、check teams、check/classify email、build feature、make a skill、给 AIDash 新增 AI metrics 并扩展 aidata L1–L5、用 Jev / Falcon / System One 写题与排障，或任务节点拆解与断链诊断）。复用 workflow-hub 图谱，保持 M2 全局五元组并将细节调查交由 M1。
---

# How-To 统一入口

## 1. 定位与 L4–L1 四层控制论金字塔（Harness Engineering）

`/howto` 是新 Session 启动任何“怎么做某事 / 细化流程 / 复盘任务”的唯一交互入口。**加载 `/howto` 后必须首先运行 [`scripts/howto_flow.py`](scripts/howto_flow.py)**（无参数默认 `--graph --summary`；也可用 `--lookup "<场景>" --json`、`--graph`/`--pyramid`、`--list-nodes --json`、`--summary`、`--neighbors <NodeID>` 或 `--scaffold-tuple "<任务>" --json`），严禁只读文档不跑脚本。30+ 标准节点（`E1–E12, Q1–Q3, I1–I7, G1–G2, P1–P9, O1–O6`）与 `TK-01..TK-16` 复用 [`../workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md)；Jev 写题见 [`references/jev-systemone-guide.md`](references/jev-systemone-guide.md)。

| 金字塔层 | 定位与职责边界 | 核心节点 / 技能 |
|---|---|---|
| **L4 人类掌舵**（Human Helmsman） | ① 定方向与砍伪需求（Kill-Switch）· ② 120Hz 原生审美（Taste）· ③ 保护确定性裁判与 Owned Paths（Oracle）· ④ 看截图/指标/R10 回执验收，不逐行读 Diff | `I5` 拍板、`G1` Goal 门禁 |
| **L3 熵减自演进**（Agent Helping Agent Improve） | 把人类拍过的 Taste 与踩过的坑经 6 层首错归因与 3 把物理锁永久下沉为 CLI 护栏 | `P2` `aidata` → `P3` `retro-loop`/`agent-retro`/`skill-retro` → `P7` `skill-contract`/`P8` `repo-kit` |
| **L2 协同编排**（Agent Guiding Agent） | M3 认知分流 → M2 冻结 `spec.md`/盯 PR DAG → M1 波次调度、独立 Worktree + 跨厂商 Reviewer 独立裁判 + V3 熔断 | `I7` `personal-m2-assistant`、`m2-dispatcher`、`team-lead`（`E6`/`E10`/`E11`）、`code-review` |
| **L1 专项执行**（Agent Doing the Work） | 研发交付、信息看板与个人/家居底座专项任务闭环 | `E1–E5, E7–E9, E12, Q1–Q3, I1–I6, G1–G2, P1, P4–P6, P9, O1–O6` |

### 1.5 L0 → L1/L2 按需下钻路由表（When-to-Read & CLI Routing）

| 触发 | L1 精确抽屉 | L2 CLI / 动作 |
|---|---|---|
| **默认首步（必跑）**：金字塔全局图谱、节点与五元组 | [`workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md) | `python3 skills/howto/scripts/howto_flow.py --lookup "<query>" --json` / `--pyramid` / `--neighbors <NodeID>` / `--scaffold-tuple "<task>" --json` |
| 校验五元组与 TK-01..16 断链 | [`workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md) | `python3 skills/howto/scripts/howto_guard.py validate-recipe --file <md>` |
| Apple 原生动态系统外观（禁自建 `.designTheme` / 平行主题） | [`../my-designer/references/appearance-ledger.md`](../my-designer/references/appearance-ledger.md) | `python3 skills/howto/scripts/howto_flow.py --lookup "system theme" --json` + `howto_guard.py validate-recipe --file <md>` |
| AIDash 新增/扩展 AI 指标（aidata L1–L5） | [`../aidash-metrics/SKILL.md`](../aidash-metrics/SKILL.md) | 加载独立 `aidash-metrics` Skill；按其 inspector → Owner → designer → data-before-UI gates 路由；实现另派 M1 |
| 接入 Layer-1 Jev 快思考 / 排障 | [`references/jev-systemone-guide.md`](references/jev-systemone-guide.md) | `python3 skills/team-lead/scripts/jev_client.py --state '<json>' --questions '<json>'` |
| 新建/审计/评测 Skill 或回流 Falcon Star | [`skill-contract/SKILL.md`](../skill-contract/SKILL.md) | `.../skill_contract.py audit|eval|description-eval|sync-jev-stars --skill <name>` |
| 派发单仓 M1 Worktree | [`m2-dispatcher/SKILL.md`](../m2-dispatcher/SKILL.md) | `herdr-dispatch.sh --pane "$HERDR_PANE_ID" --direction right` |
| 跨仓多 M2 编排 | [`personal-m2-assistant/SKILL.md`](../personal-m2-assistant/SKILL.md) | `m2-router.py --plan`，Owner Gate 后 `--apply` |
| 回归入口与触发边界 | [eval-cases.json](references/eval-cases.json) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill howto --strict` |

## 2. 新 Session 启动用法（3 种标准姿势）

- **姿势 A（细化/补断链）**：`/howto 细化：<任务描述，如：做 email 每日总结并投递 session>` · **姿势 B（查/跑既有流程）**：`/howto <fix bug list / build feature / check teams / how to use jev>` · **姿势 C（复盘/新增）**：`/howto 复盘：<handoff>` 或 `/howto 新增：<场景>`

## 3. M2 全局拆解 → M1 细节落实四步铁律

1. **首步必跑 `howto_flow.py` 查图谱搭积木**：先运行 `python3 skills/howto/scripts/howto_flow.py --lookup "<场景>" --json`（或 `--pyramid`/`--list-nodes --json`/`--scaffold-tuple`）解析 [`how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md)，定位复用节点、技能与 `TK-xx` 断链。英文按词边界、节点按完整 ID 匹配；零命中返回 `unresolved: true`、`matched_scenario: null` 与空 `matched_nodes`；空目录 fail closed。
2. **M2 只列全局五元组，不翻代码**：输出 `Inbound → Internal → Calls → PointsTo → Trigger & Notify`（标 `✅ 已具备` / `🔴 待 M1 调查`），用 [`scripts/howto_guard.py`](scripts/howto_guard.py)（`validate-recipe` / `check-nodes` / `audit-5tuple`）零 Token 校验合法节点与红线。
3. **细节调查与实现派给右侧 M1**：拍板后加载 [`m2-dispatcher`](../m2-dispatcher/SKILL.md) 与 [`team-lead`](../team-lead/SKILL.md)，经 `herdr-dispatch.sh --pane "$HERDR_PANE_ID" --direction right` 在独立 Worktree 写 `~/Development/handoff/<goal>/spec.md` 并实现。
4. **闭环回写图谱或独立建 Skill**：`Internal` 薄则更新 [`how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md)；含厚领域知识、专用 CLI/API 或确定性门禁则调 [`skill-contract`](../skill-contract/SKILL.md) 建独立 Skill 并下沉脚本。

## 4. 典型拆解示范

- **Email 每日总结 → dsh/Cindy session**：`Inbound`（Pulse/Manual）→ `I2`（`workiq` 只读 watermark）→ `Calls: I3 + I4` → `PointsTo: I5`（session adapter）；`✅ workiq + jev_client` 就绪，`🔴 TK-09 水位` 与 `🔴 投递` 交 M1 调查。

### 4.5 P9/P2 → P1 → E7 → E4 AIDash AI 指标新增与扩展配方

- **Inbound / Internal**：新增/重构指标或 `P3` 提案；aidata L1–L5 血缘、inspector 与门禁由厚领域流程 [`../aidash-metrics/SKILL.md`](../aidash-metrics/SKILL.md) 承载，本处仅做编排交接。
- **Calls / PointsTo / Trigger & Notify**：加载 `aidash-metrics` → `I5` 拍板 → `E7` → `E4` + `E9`（实现另派 M1）→ `P1` 快照卡片与 `G1` 读回；Manual/`P3` 触发，口径 P0，合入 P1。

### 4.6 E7 → P7 → E4 → E9 Apple OS-Native Dynamic System Appearance（Zero Custom Theme Engine）配方

- **Inbound / Internal**：Apple UI 亮/暗适配交 `my-designer` + `skill-contract`；必须 100% 绑定 Apple OS 原生动态系统外观（`NSAppearance` / `UITraitCollection` / `@Environment(\.colorScheme)` + 语义动态 Token），严禁自建 `.designTheme`、应用内主题切换器或 Web-CSS 平行主题状态机（`howto_guard.py` 拦截）。
- **Calls / PointsTo / Trigger & Notify**：`E7` → `P7` → `E4` → `E9` 双外观核验 → `G1`/`I5` 验收；Manual 触发，边界 P0，门禁/合入 P1。

## 5. 三层省 Token 漏斗准入准则（Layer 0 → Layer 1 → Layer 2）

- **Layer 0 Script**：节点规则可由 regex/AST/schema/watermark 完整表达时使用；目标 `<10ms`、`$0`、可复现，先做确定性筛选与校验。
- **Layer 1 Jev**：需要 `70–500ms` 的封闭世界事实判断时使用；通过 Falcon `:19823`，题型为 `choice+noul`、`noul`、`score` 或 `box`，Python truth table 落决定；网关超时 `1.5s` 时走确定性 fallback。
- **Layer 2 Heavy LLM**：开放式生成、跨资料综合或 Layer 1 `0.30~0.70` 灰区才唤醒；预算约 `5–180s`，不得用它替代可执行规则。

## 6. 四大标准素材库挂载判定矩阵与隔离红线

只有同时满足 **高频累积性 × 跨会话 Top-3 检索复用 × 图文/公开沉淀价值** 才挂载素材库；否则留在当前 Goal/handoff。下表为 `leepepe.github.io` 的候选消费路由，只有脱敏并获发布批准后才投递，登记不代表已上线。

| Canonical Vault | 唯一写入技能 / 本地事实源 | Blog 挂载 | 适合内容 |
|---|---|---|---|
| Design & Motion Vault | `design-studio` / 该技能的 `scripts/data/vault/` | `/vault/` | 设计灵感、动效、测量卡 |
| Collected Tools Vault | `save-tool` / `~/Development/Personal/collected-tools/` | `/vault/` | 工具、Repo、使用入口 |
| Personal Knowledge Vault | `knowledge-digest` / `~/Development/Personal/knowledge-vault/` | `/learn/` | 知识卡、文章/视频拆解 |
| Retro & Story Vault | `retro-loop` → `story-craft` / Goal 的已验收叙事 handoff | `/thoughts/` | 复盘、技术叙事、公开素材 |

红线：`knowledge-vault` 永不自动同步 `design-studio`；AIDash 的 `Star` 永不自动发布；`save-tool` 只收藏与索引，**零执行**。

## 7. How to Use Jev & Falcon（`I3 Classify` 毫秒级判断标准姿势）

凡节点需在 `0-Token 确定性脚本`（Layer 0）与 `唤醒重型大模型/CLI`（Layer 2）之间插入 **70–500ms 快思考判断（Layer 1）**，统一走 [`references/jev-systemone-guide.md`](references/jev-systemone-guide.md)：

1. **标准链路与 Source Key 鉴权**：
   - `Script -> Falcon (127.0.0.1:19823/v1/systemone, Bearer <FALCON_SOURCE_KEY>) -> Local Raven (127.0.0.1:7024/v1/systemone)`。
   - 本机各技能/角色的 Source Key 存于 `~/.config/falcon/source-keys.json`（权限 `0600`）；脚本统一调用 [`../team-lead/scripts/jev_client.py`](../team-lead/scripts/jev_client.py) 的 `jev_decide()`，禁止自写无鉴权裸请求或绕过 `:19823`。
2. **核心范式（“Judgments from Jev, decisions in code”）**：
   - **四题型**：`choice`（互斥枚举 + `criteria` 字典）、`noul`（`0~1` 真值概率，默认 `>0.70` Yes / `<0.30` No / 灰区升维）、`score`（量表重排）、`box`（UI 截图视觉框选）。
   - **写题五铁律**：① 一题只问一件事（禁 `and`/`or`）；② 同一份 `state` 多题打包单次请求并发问完（Speculative Fan-Out）；③ 只问客观可观察判断，决策逻辑写在代码真值表（Truth Table）；④ `choice` 必配伴生 `noul` 处理都不匹配的 Null Case；⑤ 在 Falcon UI 用 `⌘Return` 审阅并点 **Star** 将边界样本沉淀为永久回归集。
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/jev-systemone-guide.md` | `python3 skills/howto/scripts/howto_flow.py --help` |
| verification | `references/eval-cases.json` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `skill_contract.py eval --skill howto --strict` |
