---
name: howto
description: 梳理、细化、复盘、串联或新增个人与工作 How-To 流程的统一入口（如 how to fix bug / bug list、check teams、check/classify email、build feature、make a skill、怎么用 Jev / Falcon / System One 写题与排障，或针对具体任务做节点拆解与断链诊断）。复用 workflow-hub 底层节点图谱，保持 M2 全局五元组视角并将细节调查交由 M1 落实。
---

# How-To 统一入口

## 1. 定位与底层事实源复用

`/howto` 是新 Session 启动任何“怎么做某事 / 细化流程 / 复盘任务”的唯一交互入口。

底层 30 个标准节点（`E1–E12, Q1–Q3, I1–I6, G1–G2, P1–P9, O1–O6`）、四层 Skills/Repos 归位表与 `TK-01..TK-16` 断链清册，直接复用 [`../workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md)。Jev / Falcon (`POST /v1/systemone`) 写题范式、Source Key 角色映射与排障抽屉见 [`references/jev-systemone-guide.md`](references/jev-systemone-guide.md)。不在本文件复制节点表；长文案例底稿按需读取 `~/Development/handoff/howto-atlas/spec.md`。

## 2. 新 Session 启动用法（3 种标准姿势）

- **姿势 A：细化具体任务 / 补断链**：`/howto 细化：<任务描述，如：做 email 每日总结并展示在 dsh 或 cindy 的 session 中>`
- **姿势 B：查询 / 执行既有 How-To**：`/howto <场景，如：fix bug list / build feature / check teams / how to use jev>`
- **姿势 C：复盘项目 / 新增 How-To 节点**：`/howto 复盘：<项目或 handoff>` 或 `/howto 新增：<新场景>`

## 3. M2 全局拆解 → M1 细节落实四步铁律

1. **查图谱搭积木**：先读 [`../workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md)，定位复用节点与对应 `TK-xx` 断链。
2. **M2 只列全局五元组，不翻代码**：向 Owner 输出 `Inbound（来源） → Internal（本节点独有逻辑） → Calls（子节点） → PointsTo（去向） → Trigger & Notify（Manual / Hook / Pulse + P0/P1/P2）`，并标出 `✅ 已具备` 与 `🔴 待 M1 调查/补齐`。
3. **细节调查与实现派给右侧 M1**：Owner 拍板后，加载 [`m2-dispatcher`](../m2-dispatcher/SKILL.md) 与 [`team-lead`](../team-lead/SKILL.md)，通过 `herdr-dispatch.sh --pane "$HERDR_PANE_ID" --direction right` 拉起 M1；M1 在独立 Worktree 查清技术细节、写 `~/Development/handoff/<goal>/spec.md` 并实现。
4. **闭环回写图谱或独立建 Skill**：组合串联且 `Internal` 薄时，更新 [`../workflow-hub/references/how-to-nodes.md`](../workflow-hub/references/how-to-nodes.md) 消除断链或追加配方；含厚领域知识、专用 CLI/API 或确定性门禁时，调用 [`skill-contract`](../skill-contract/SKILL.md) 按四层归位创建独立 Skill，并下沉脚本。

## 4. 典型拆解示范：Email 每日总结 → dsh/Cindy session

`Inbound`（每日 Pulse / 手动）→ `I2 Process Email`（`workiq` 只读 watermark）→ `Calls`：`I3 Jev 分桶` + `I4 总结` → `PointsTo`：`I5 Notify User`（`dsh` / `Cindy` session adapter）。

`✅ workiq + jev_client` 已就绪；`🔴 TK-09 水位采集脚本` 与 `🔴 dsh/Cindy session 投递机制` 交 M1 调查。确认后按四步 SOP 回写图谱或独立下沉实现，不在 M2 直接翻代码。

## 5. How to Use Jev & Falcon（`I3 Classify` 毫秒级判断标准姿势）

凡节点需在 `0-Token 确定性脚本`（Layer 0）与 `唤醒重型大模型/CLI`（Layer 2）之间插入 **70–500ms 快思考判断（Layer 1）**，统一走 [`references/jev-systemone-guide.md`](references/jev-systemone-guide.md)：

1. **标准链路与 Source Key 鉴权**：
   - `Script -> Falcon (127.0.0.1:19823/v1/systemone, Bearer <FALCON_SOURCE_KEY>) -> Local Raven (127.0.0.1:7024/v1/systemone)`。
   - 本机各技能/角色的 Source Key 存于 `~/.config/falcon/source-keys.json`（权限 `0600`）；脚本统一调用 [`../team-lead/scripts/jev_client.py`](../team-lead/scripts/jev_client.py) 的 `jev_decide()`，禁止自写无鉴权裸请求或绕过 `:19823`。
2. **核心范式（“Judgments from Jev, decisions in code”）**：
   - **四题型**：`choice`（互斥枚举 + `criteria` 字典）、`noul`（`0~1` 真值概率，默认 `>0.70` Yes / `<0.30` No / 灰区升维）、`score`（量表重排）、`box`（UI 截图视觉框选）。
   - **写题五铁律**：① 一题只问一件事（禁 `and`/`or`）；② 同一份 `state` 多题打包单次请求并发问完（Speculative Fan-Out）；③ 只问客观可观察判断，决策逻辑写在代码真值表（Truth Table）；④ `choice` 必配伴生 `noul` 处理都不匹配的 Null Case；⑤ 在 Falcon UI 用 `⌘Return` 审阅并点 **Star** 将边界样本沉淀为永久回归集。
